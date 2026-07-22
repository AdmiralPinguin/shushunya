"""Run lifecycle engine: execution preflight, orchestration, the research
loop, interrupted-run recovery, and background execution start. This is
the controller Warmaster runs above the governors; the HTTP gateway wires
these into endpoints."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .actions import action_for_mode, run_preflight_actions
from .gateway_util import resolve_run_child_path, valid_task_id, validate_service_host
from .http_executor import execute_run as execute_http_run, preflight_workers as preflight_http_workers
from .ledger import TaskLedger
from .local_executor import WORKER_COMMANDS, execute_run as execute_local_run, input_artifact_errors, ordered_dispatch_paths
from .mission_control import (
    link_run_to_mission,
    mission_dir_for,
    mission_id_for,
    open_mission,
    record_warmaster_acceptance,
    task_id_for_message,
)
from .native_runs import (
    NATIVE_CODE_ADAPTER,
    NATIVE_RESEARCH_ADAPTER,
    NativeRunAdapter,
    native_adapter_for_contract,
    native_adapter_for_execution,
    native_adapter_for_run,
    native_adapter_for_route,
)
from .run_package import load_json_file, load_json_object, load_ledger_dict, run_oversight, sandbox_artifact_file_status
from .run_state import list_runs, orchestration_state, run_progress, run_snapshot, run_summary
from .run_validation import revision_plan_summary, run_oversight_summary, validate_oversight_against_run, validate_revision_plan
from .runtime_state import ACTIVE_RUNS, ACTIVE_RUNS_LOCK, REPO_ROOT
from .skitarii_bridge import run_via_skitarii
from .task_prepare import prepare_task, preflight_task
from .views import executable_client_action, orchestration_view_fields, recovery_candidate_display


NATIVE_EXECUTION_DESCRIPTOR = NATIVE_CODE_ADAPTER.execution

_ORCHESTRATE_LOCKS_GUARD = threading.Lock()
_ORCHESTRATE_LOCKS: dict[str, tuple[threading.RLock, int]] = {}

MAX_STANDARD_EXECUTION_TIMEOUT_SEC = 43_200
MAX_RESEARCH_WARBAND_TIMEOUT_SEC = 604_800
_TASK_MEMORY_IDENTITY_FIELDS = (
    "task_memory_id",
    "root_task_id",
    "run_task_id",
    "parent_task_id",
)


class TaskMemoryParentConflict(ValueError):
    """A child attempt names ancestry that is not durably provable."""


class ExistingMissionReplayError(ValueError):
    """An existing run cannot be replayed as the requested commander intake."""

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def _task_memory_ref(
    task_id: str,
    *,
    task_memory_id: str = "",
    root_task_id: str = "",
    parent_task_id: str = "",
) -> dict[str, Any]:
    """Return the stable goal-memory identity for an immutable run attempt."""
    memory_id = str(task_memory_id or root_task_id or task_id).strip()
    root_id = str(root_task_id or memory_id or task_id).strip()
    parent_id = str(parent_task_id or "").strip()
    for field, value in (
        ("task_id", task_id),
        ("task_memory_id", memory_id),
        ("root_task_id", root_id),
    ):
        if not valid_task_id(value):
            raise ValueError(f"{field} is not a valid task identity")
    if parent_id and not valid_task_id(parent_id):
        raise ValueError("parent_task_id is not a valid task identity")
    if task_id != root_id and not parent_id:
        raise ValueError(
            "non-root task attempt requires parent_task_id provenance"
        )
    if parent_id and parent_id == task_id:
        raise ValueError("parent_task_id must differ from the child task_id")
    if task_id == root_id and parent_id:
        raise ValueError("root task attempt cannot have a parent_task_id")
    return {
        "schema_version": 1,
        "task_memory_id": memory_id,
        "root_task_id": root_id,
        "run_task_id": task_id,
        "parent_task_id": parent_id,
    }


def _load_persisted_task_memory_ref(
    path: Path,
    expected_task_id: str,
) -> dict[str, Any]:
    """Load one canonical immutable run-to-page binding without normalization."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("task-memory reference is missing or is not a regular file")
    raw, error = load_json_object(path, "task memory reference")
    if error:
        raise ValueError(error)
    expected_fields = {"schema_version", *_TASK_MEMORY_IDENTITY_FIELDS}
    if set(raw) != expected_fields or raw.get("schema_version") != 1:
        raise ValueError("task-memory reference is not the canonical schema")
    if str(raw.get("run_task_id") or "") != expected_task_id:
        raise ValueError("task-memory reference names a different run_task_id")
    ref = _task_memory_ref(
        expected_task_id,
        task_memory_id=str(raw.get("task_memory_id") or ""),
        root_task_id=str(raw.get("root_task_id") or ""),
        parent_task_id=str(raw.get("parent_task_id") or ""),
    )
    if any(
        str(raw.get(field) or "") != str(ref.get(field) or "")
        for field in _TASK_MEMORY_IDENTITY_FIELDS
    ):
        raise ValueError("task-memory reference is not canonical")
    return ref


def _verify_parent_task_memory(
    run_root: Path,
    ref: dict[str, Any],
) -> dict[str, Any] | None:
    """Prove a child attempt belongs to the same durable goal as its parent.

    A syntactically valid ``parent_task_id`` is not provenance: without this
    check any caller could attach a new run to an unrelated task page.  The
    parent's immutable ``task_memory.json`` is the authority for ancestry.
    """
    first_parent_task_id = str(ref.get("parent_task_id") or "").strip()
    if not first_parent_task_id:
        return None
    run_root = run_root.resolve()
    expected_memory_id = str(ref.get("task_memory_id") or "")
    expected_root_id = str(ref.get("root_task_id") or "")
    current_task_id = first_parent_task_id
    seen = {str(ref.get("run_task_id") or "")}
    first_parent_ref: dict[str, Any] | None = None
    while current_task_id:
        if current_task_id in seen:
            raise TaskMemoryParentConflict("task-memory ancestry contains a cycle")
        seen.add(current_task_id)
        if len(seen) > 128:
            raise TaskMemoryParentConflict("task-memory ancestry exceeds 128 attempts")
        parent_dir = run_root / current_task_id
        if parent_dir.is_symlink() or not parent_dir.is_dir():
            raise TaskMemoryParentConflict(
                f"parent task {current_task_id!r} does not exist in this run root"
            )
        parent_path = parent_dir / "task_memory.json"
        if parent_path.is_symlink() or not parent_path.is_file():
            raise TaskMemoryParentConflict(
                f"parent task {current_task_id!r} has no immutable task_memory.json"
            )
        try:
            parent_ref = _load_persisted_task_memory_ref(
                parent_path,
                current_task_id,
            )
        except ValueError as exc:
            raise TaskMemoryParentConflict(
                f"parent task-memory reference is invalid: {exc}"
            ) from exc
        if first_parent_ref is None:
            first_parent_ref = parent_ref
        if str(parent_ref.get("task_memory_id") or "") != expected_memory_id:
            raise TaskMemoryParentConflict(
                "parent and child disagree on immutable task_memory_id"
            )
        if str(parent_ref.get("root_task_id") or "") != expected_root_id:
            raise TaskMemoryParentConflict(
                "parent and child disagree on immutable root_task_id"
            )
        next_parent = str(parent_ref.get("parent_task_id") or "").strip()
        if current_task_id == expected_root_id:
            if next_parent:
                raise TaskMemoryParentConflict("root task has unexpected ancestry")
            return first_parent_ref
        if not next_parent:
            raise TaskMemoryParentConflict(
                f"ancestry stopped at {current_task_id!r} before root {expected_root_id!r}"
            )
        current_task_id = next_parent
    raise TaskMemoryParentConflict("task-memory ancestry has no root")


def _persist_task_memory_ref(run_dir: Path, ref: dict[str, Any]) -> dict[str, Any]:
    """Atomically bind a run to one durable task page without rewriting lineage."""
    path = run_dir / "task_memory.json"
    if path.exists():
        current = _load_persisted_task_memory_ref(path, run_dir.name)
        if any(
            str(current.get(key) or "") != str(ref.get(key) or "")
            for key in _TASK_MEMORY_IDENTITY_FIELDS
        ):
            raise ValueError("run already belongs to a different task-memory lineage")
        return current
    _verify_existing_run_task_memory_provenance(run_dir, ref)
    raw = json.dumps(ref, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    temporary.write_text(raw, encoding="utf-8")
    os.replace(temporary, path)
    try:
        ledger = TaskLedger.load(run_dir / "task_ledger.json")
        ledger.record_event(
            "task_memory_bound",
            {
                "task_memory_id": ref["task_memory_id"],
                "root_task_id": ref["root_task_id"],
                "parent_task_id": ref["parent_task_id"],
            },
        )
    except Exception:
        # The immutable reference itself is already durable. Ledger event repair
        # is observational and must not make a valid run unusable.
        pass
    return ref


def _verify_existing_run_task_memory_provenance(
    run_dir: Path, ref: dict[str, Any],
) -> None:
    """Refuse to bind a prepared run to lineage not proven by its durable inputs.

    Ceraxia can finish writing a native package just before the gateway process
    dies.  On retry ``task_memory.json`` is consequently absent even though the
    package already exists.  The mission protocol (full lineage) and Ceraxia's
    captured task-memory context (goal identity) are the durable witnesses for
    that crash window; a retry must not be allowed to invent a different owner.
    """
    mission_paths: list[Path] = []
    mission_ref_path = run_dir / "mission_ref.json"
    mission_ref: dict[str, Any] = {}
    if mission_ref_path.exists():
        mission_ref, mission_ref_error = load_json_object(
            mission_ref_path, "mission reference",
        )
        if mission_ref_error:
            raise ValueError(mission_ref_error)
    raw_mission_dir = str(mission_ref.get("mission_dir") or "").strip()
    if raw_mission_dir:
        mission_paths.append(Path(raw_mission_dir) / "mission.json")

    contract_path = run_dir / "contract.json"
    contract: dict[str, Any] = {}
    if contract_path.exists():
        contract, contract_error = load_json_object(contract_path, "contract")
        if contract_error:
            raise ValueError(contract_error)
    mission_id = str(contract.get("mission_id") or "").strip()
    if mission_id:
        mission_paths.append(
            Path(__file__).resolve().parents[1] / "missions" / mission_id / "mission.json"
        )

    seen: set[Path] = set()
    for mission_path in mission_paths:
        resolved = mission_path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        mission, error = load_json_object(resolved, "mission")
        if error:
            raise ValueError(error)
        mission_memory = (
            mission.get("task_memory")
            if isinstance(mission.get("task_memory"), dict)
            else {}
        )
        if not mission_memory:
            continue
        if any(
            str(mission_memory.get(key) or "") != str(ref.get(key) or "")
            for key in _TASK_MEMORY_IDENTITY_FIELDS
        ):
            raise ValueError("prepared run mission belongs to a different task-memory lineage")
        return

    context_path = run_dir / "task_memory_context.json"
    context: dict[str, Any] = {}
    if context_path.exists():
        context, context_error = load_json_object(
            context_path, "task memory context",
        )
        if context_error:
            raise ValueError(context_error)
    context_memory_id = str(context.get("task_memory_id") or "").strip()
    if context_memory_id:
        if context_memory_id != str(ref.get("task_memory_id") or ""):
            raise ValueError("prepared run context belongs to a different task memory")
        # The service captures only the goal page id.  It is complete proof for
        # a root run, while recovery ancestry still requires the mission record.
        if (
            str(ref.get("run_task_id") or "")
            == str(ref.get("root_task_id") or "")
            and not str(ref.get("parent_task_id") or "")
        ):
            return

    run_task_id = str(ref.get("run_task_id") or "")
    default_identity = (
        str(ref.get("task_memory_id") or "") == run_task_id
        and str(ref.get("root_task_id") or "") == run_task_id
        and not str(ref.get("parent_task_id") or "")
    )
    if not default_identity:
        raise ValueError("prepared run has no durable proof of the requested task-memory lineage")


def _recovery_task_memory_fields(run_dir: Path) -> dict[str, Any]:
    """Carry one goal page across immutable recovery attempts."""
    path = run_dir / "task_memory.json"
    current = _load_persisted_task_memory_ref(path, run_dir.name)
    task_memory_id = str(current.get("task_memory_id") or "").strip()
    root_task_id = str(current.get("root_task_id") or "").strip()
    if not valid_task_id(task_memory_id) or not valid_task_id(root_task_id):
        raise ValueError("run has an invalid task-memory lineage")
    return {
        "task_memory_id": task_memory_id,
        "root_task_id": root_task_id,
        "parent_task_id": run_dir.name,
        "continuation_of": run_dir.name,
    }


def _existing_mission_request(
    warmaster_root: Path,
    *,
    task_id: str,
    message: str,
    ref: dict[str, Any],
) -> dict[str, Any]:
    """Prove an existing run belongs to this exact immutable intake."""
    mission_id = mission_id_for(task_id, message)
    mission_dir = mission_dir_for(warmaster_root, mission_id)
    if not (mission_dir / "mission.json").is_file():
        raise ExistingMissionReplayError(
            "existing run has no durable commander intake; create a fresh immutable attempt",
            error_code="existing_mission_invalid",
        )
    replay = open_mission(
        warmaster_root,
        message,
        task_id,
        source_channel="main_chat",
        task_memory=ref,
    )
    if not replay.get("ok"):
        raise ExistingMissionReplayError(
            str(replay.get("error") or "existing mission request mismatch"),
            error_code=str(replay.get("error_code") or "existing_mission_invalid"),
        )
    mission = replay.get("mission") if isinstance(replay.get("mission"), dict) else {}
    return {**mission, "mission_id": mission_id}


def _ensure_task_memory_page(
    ref: dict[str, Any], message: str, mission: dict[str, Any],
) -> dict[str, Any]:
    """Initialise the required goal wiki before a governor reads it."""
    base_url = os.environ.get(
        "WARMMASTER_ARCHIVE_URL",
        os.environ.get(
            "CERAXIA_ARCHIVE_URL",
            os.environ.get("SHUSHUNYA_CORE_ARCHIVE_URL", "http://127.0.0.1:8090"),
        ),
    ).rstrip("/")
    memory_id = str(ref.get("task_memory_id") or "")
    headers = {"Accept": "application/json"}
    archive_key = os.environ.get("ARCHIVE_API_KEY", "").strip()
    if archive_key:
        if any(char in archive_key for char in "\r\n"):
            return {
                "stage": "task_memory_init",
                "ok": False,
                "retryable": False,
                "error_code": "task_memory_auth_invalid",
                "warning": "Archive API key is invalid; task page was not initialised",
            }
        headers["Authorization"] = f"Bearer {archive_key}"

    def request_json(
        method: str, path: str, payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        request_headers = dict(headers)
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{base_url}{path}", data=data, headers=request_headers, method=method,
        )
        with urllib.request.urlopen(request, timeout=3.0) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError("Archive task-page response exceeds 1000000 bytes")
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("Archive task-page response is not an object")
        return decoded

    try:
        current = request_json(
            "GET", f"/archive/task-page?task_memory_id={quote(memory_id, safe='')}",
        )
        snapshot = current.get("snapshot") if isinstance(current.get("snapshot"), dict) else {}
        exists = bool(current.get("task_memory_id"))
        requested_root_task_id = str(ref.get("root_task_id") or "").strip()
        stored_root_task_id = str(
            current.get("root_task_id") or snapshot.get("root_task_id") or ""
        ).strip()
        if exists and stored_root_task_id != requested_root_task_id:
            raise ValueError(
                "Archive task page belongs to a different root_task_id"
            )
        root_task_id = stored_root_task_id if exists else requested_root_task_id
        goal_verbatim = str(
            snapshot.get("goal_verbatim") if exists else message
        ).strip()
        aliases = [str(ref.get("run_task_id") or "").strip()]
        mission_id = str(mission.get("mission_id") or "").strip()
        if mission_id:
            aliases.append(mission_id)
        initialised = request_json(
            "POST",
            "/archive/task-page/init",
            {
                "action": "init",
                "task_id": str(ref.get("run_task_id") or ""),
                "task_memory_id": memory_id,
                "root_task_id": root_task_id,
                "goal_verbatim": goal_verbatim,
                "aliases": aliases,
                "actor": "WarmasterGateway",
            },
        )
        if initialised.get("ok") is not True:
            raise ValueError(str(initialised.get("error") or "Archive rejected task-page init"))
        return {
            "stage": "task_memory_init",
            "ok": True,
            "retryable": False,
            "task_memory_id": str(initialised.get("task_memory_id") or memory_id),
            "root_task_id": str(initialised.get("root_task_id") or root_task_id),
            "revision": int(initialised.get("revision") or 0),
            "existing": exists,
        }
    except urllib.error.HTTPError as exc:
        retryable = exc.code >= 500 or exc.code in {408, 425, 429}
        return {
            "stage": "task_memory_init",
            "ok": False,
            "retryable": retryable,
            "error_code": (
                "task_memory_unavailable" if retryable else "task_memory_rejected"
            ),
            "task_memory_id": memory_id,
            "warning": f"Archive task page request failed with HTTP {exc.code}",
        }
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        return {
            "stage": "task_memory_init",
            "ok": False,
            "retryable": True,
            "error_code": "task_memory_unavailable",
            "task_memory_id": memory_id,
            "warning": f"Archive task page is temporarily unavailable: {exc}",
        }
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {
            "stage": "task_memory_init",
            "ok": False,
            "retryable": True,
            "error_code": "task_memory_invalid_response",
            "task_memory_id": memory_id,
            "warning": f"Archive task page returned an invalid response: {exc}",
        }
    except ValueError as exc:
        return {
            "stage": "task_memory_init",
            "ok": False,
            "retryable": False,
            "error_code": "task_memory_identity_conflict",
            "task_memory_id": memory_id,
            "warning": f"Archive task page identity was rejected: {exc}",
        }


def task_memory_start_guard(run_dir: Path) -> dict[str, Any]:
    """Repair/verify the task page at the final execution boundary."""
    ref_path = run_dir / "task_memory.json"
    if not ref_path.is_file():
        return {
            "stage": "task_memory_start_guard",
            "ok": False,
            "retryable": False,
            "error_code": "task_memory_reference_missing",
            "warning": "run has no durable task_memory.json; reprepare it before execution",
        }
    try:
        ref = _load_persisted_task_memory_ref(
            ref_path,
            run_dir.name,
        )
        _verify_parent_task_memory(run_dir.parent, ref)
    except TaskMemoryParentConflict as exc:
        return {
            "stage": "task_memory_start_guard",
            "ok": False,
            "retryable": False,
            "error_code": "task_memory_parent_conflict",
            "warning": str(exc),
        }
    except ValueError as exc:
        return {
            "stage": "task_memory_start_guard",
            "ok": False,
            "retryable": False,
            "error_code": "task_memory_reference_invalid",
            "warning": str(exc),
        }
    contract, contract_error = load_json_object(run_dir / "contract.json", "contract")
    mission_id = str(contract.get("mission_id") or "").strip() if not contract_error else ""
    if not mission_id:
        return {
            "stage": "task_memory_start_guard",
            "ok": False,
            "retryable": False,
            "error_code": "task_memory_mission_missing",
            "warning": contract_error or "run contract has no mission_id",
        }
    warmaster_root = Path(__file__).resolve().parents[1]
    mission_dir = mission_dir_for(warmaster_root, mission_id)
    mission, mission_error = load_json_object(mission_dir / "mission.json", "mission")
    intake, intake_error = load_json_object(
        mission_dir / "mission_intake.json", "mission intake",
    )
    if mission_error or intake_error or not mission or not intake:
        return {
            "stage": "task_memory_start_guard",
            "ok": False,
            "retryable": False,
            "error_code": "task_memory_mission_missing",
            "warning": mission_error or intake_error or "mission provenance is missing",
        }
    guarded = _ensure_task_memory_page(
        ref,
        str(intake.get("user_request") or ""),
        {**mission, "mission_id": mission_id},
    )
    return {**guarded, "stage": "task_memory_start_guard"}


def ensure_task_memory_for_intake(
    *,
    run_root: Path,
    task_id: str,
    message: str,
    mission_id: str,
    task_memory_id: str = "",
    root_task_id: str = "",
    parent_task_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Public preflight gate used before any governor receives the task."""
    ref = _task_memory_ref(
        task_id,
        task_memory_id=task_memory_id,
        root_task_id=root_task_id,
        parent_task_id=parent_task_id,
    )
    _verify_parent_task_memory(run_root, ref)
    result = _ensure_task_memory_page(
        ref,
        message,
        {"mission_id": mission_id},
    )
    return result, ref


def _execution_timeout_for_run(run_dir: Path, requested: int) -> int:
    """Clamp execution time without starving the deliberately slow research backend."""
    adapter = native_adapter_for_run(run_dir, declared=True)
    limit = (
        MAX_RESEARCH_WARBAND_TIMEOUT_SEC
        if adapter is not None and adapter.backend == "ResearchWarband"
        else MAX_STANDARD_EXECUTION_TIMEOUT_SEC
    )
    return max(1, min(int(requested), limit))


@contextmanager
def _orchestrate_task_reservation(run_root: Path, task_key: str):
    """Serialize check/create/link for one task id inside the gateway process."""
    key = f"{run_root.resolve()}\0{task_key}"
    with _ORCHESTRATE_LOCKS_GUARD:
        lock, users = _ORCHESTRATE_LOCKS.get(key, (threading.RLock(), 0))
        _ORCHESTRATE_LOCKS[key] = (lock, users + 1)
    try:
        with lock:
            yield
    finally:
        with _ORCHESTRATE_LOCKS_GUARD:
            current = _ORCHESTRATE_LOCKS.get(key)
            if current is not None and current[0] is lock:
                remaining = current[1] - 1
                if remaining <= 0:
                    _ORCHESTRATE_LOCKS.pop(key, None)
                else:
                    _ORCHESTRATE_LOCKS[key] = (lock, remaining)


def _skitarii_backend_health(timeout_sec: int) -> dict[str, Any]:
    """Attest the exact Skitarii instance before native execution.

    Ceraxia and the executor must use the same deep readiness definition.  A
    shallow HTTP 200 is not authority to start code execution: the VM/process
    boundary, hidden-verifier policy, model roster, instance identity, and
    source SHA all have to match the source mounted by this checkout.
    """
    from .inner_circle.ceraxia_service import skitarii_backend_health

    attestation = skitarii_backend_health(max(3, min(timeout_sec, 15)))
    health = attestation.get("health") if isinstance(attestation.get("health"), dict) else {}
    return {
        "ok": attestation.get("healthy") is True,
        "backend": "SkitariiWarband",
        "service": str(attestation.get("endpoint") or ""),
        "status": str(attestation.get("status") or "unavailable"),
        "health": health,
        "identity": health.get("identity") if isinstance(health.get("identity"), dict) else {},
        "error": str(attestation.get("error") or "")[:300],
    }


def _native_backend_health(
    adapter: NativeRunAdapter, timeout_sec: int,
) -> dict[str, Any]:
    """Run the backend-specific attestation selected by a native adapter."""
    if adapter is NATIVE_CODE_ADAPTER:
        return _skitarii_backend_health(timeout_sec)
    if adapter.backend == "ResearchWarband":
        try:
            from .research_warband_bridge import research_warband_backend_health
        except ImportError as exc:
            return {
                "ok": False,
                "backend": adapter.backend,
                "service": f"http://127.0.0.1:{adapter.service_port}",
                "status": "bridge_unavailable",
                "health": {},
                "identity": {},
                "error": f"ResearchWarband bridge is unavailable: {exc}",
            }
        health = research_warband_backend_health(max(3, min(timeout_sec, 15)))
        if not isinstance(health, dict):
            return {
                "ok": False,
                "backend": adapter.backend,
                "service": f"http://127.0.0.1:{adapter.service_port}",
                "status": "invalid_health",
                "health": {},
                "identity": {},
                "error": "ResearchWarband health adapter returned a non-object",
            }
        return health
    return {
        "ok": False,
        "backend": adapter.backend,
        "service": f"http://127.0.0.1:{adapter.service_port}",
        "status": "unsupported_native_backend",
        "health": {},
        "identity": {},
        "error": f"no health adapter is registered for {adapter.backend}",
    }


def _reprepare_action(run_dir: Path, contract: dict[str, Any]) -> dict[str, Any]:
    stem = run_dir.name[:119].rstrip(".-_") or "ceraxia-code-run"
    fresh_task_id = f"{stem}-native"
    lineage = _recovery_task_memory_fields(run_dir)
    return {
        "kind": "legacy_ceraxia_reprepare_required",
        "method": "POST",
        "endpoint": "POST /orchestrate_run",
        "body": {
            "message": str(contract.get("goal") or ""),
            "task_id": fresh_task_id,
            "governor_transport": "http",
            "run_mode": "http",
            "auto_start": True,
            "reuse_existing": True,
            **lineage,
        },
        "reason": (
            "this legacy Ceraxia package has no native execution descriptor; "
            "create a fresh run through the live Ceraxia service"
        ),
    }


def _native_reprepare_action(
    run_dir: Path, contract: dict[str, Any], adapter: NativeRunAdapter,
) -> dict[str, Any]:
    if adapter is NATIVE_CODE_ADAPTER:
        return _reprepare_action(run_dir, contract)
    stem = run_dir.name[:117].rstrip(".-_") or "iskandar-research-run"
    fresh_task_id = f"{stem}-native"
    lineage = _recovery_task_memory_fields(run_dir)
    return {
        "kind": f"reprepare_{adapter.name}_run",
        "method": "POST",
        "endpoint": "POST /orchestrate_run",
        "body": {
            "message": str(contract.get("goal") or ""),
            "task_id": fresh_task_id,
            "governor_transport": "http",
            "run_mode": "http",
            "auto_start": True,
            "reuse_existing": True,
            **lineage,
        },
        "reason": (
            "native terminal evidence is immutable; create a fresh "
            f"{adapter.governor} mission"
        ),
    }


def _task_memory_lineage_repair_action(reason: str) -> dict[str, Any]:
    return {
        "kind": "task_memory_lineage_repair_required",
        "reason": reason,
    }


def _route_failure(
    run_dir: Path,
    *,
    phase: str,
    error: str,
    error_code: str,
    next_action: dict[str, Any],
    validation_errors: list[str] | None = None,
) -> dict[str, Any]:
    task_id = run_dir.name
    payload = {
        "ok": False,
        "phase": phase,
        "status": phase,
        "task_id": task_id,
        "run_dir": str(run_dir),
        "error": error,
        "error_code": error_code,
        "actions": {"next_action": next_action},
        "next_action": next_action,
        "client_action": executable_client_action(task_id, next_action),
    }
    if validation_errors:
        payload["native_validation_errors"] = validation_errors
    return payload


def execution_backend_route(run_dir: Path) -> dict[str, Any]:
    """Resolve exactly one execution backend from the persisted run contract.

    Native runs opt in through an adapter-owned ``contract['execution']``.
    Governor names are consulted only to quarantine old Ceraxia six-worker
    packages; they never opt a new run into a native backend.
    """
    raw_contract, contract_error = load_json_object(run_dir / "contract.json", "contract")
    if contract_error:
        # Generic package validation owns missing/corrupt contracts, preserving its
        # existing diagnostics instead of changing non-code behaviour here.
        return {
            "ok": True,
            "native": False,
            "kind": "generic_pipeline",
            "backend": "legacy_pipeline",
            "execution": {},
            "contract_error": contract_error,
        }

    legacy_iskandar = (
        str(raw_contract.get("assigned_governor") or "") == "IskandarKhayon"
        and str(raw_contract.get("kind") or "").lower() == "research"
        and native_adapter_for_execution(
            raw_contract.get("execution"), declared=True
        ) is None
    )
    if legacy_iskandar:
        try:
            action = _native_reprepare_action(
                run_dir, raw_contract, NATIVE_RESEARCH_ADAPTER,
            )
        except ValueError as exc:
            repair = _task_memory_lineage_repair_action(
                (
                    "the removed legacy run has no canonical task-memory binding; "
                    "reconcile its immutable page/root provenance before creating a child"
                )
            )
            return _route_failure(
                run_dir,
                phase="legacy_iskandar_run_removed",
                error=f"legacy Iskandar run cannot be migrated safely: {exc}",
                error_code="task_memory_reference_missing",
                next_action=repair,
            )
        action["reason"] = (
            "the old Iskandar worker-plan executor was removed; create a fresh "
            "native ResearchWarband mission"
        )
        return _route_failure(
            run_dir,
            phase="legacy_iskandar_run_removed",
            error="legacy Iskandar research packages cannot be executed",
            error_code="legacy_iskandar_run_removed",
            next_action=action,
        )

    adapter = native_adapter_for_contract(raw_contract, declared=True)
    if adapter is not None:
        validation_errors: list[str] = []
        try:
            adapter.is_run(run_dir)
        except Exception as exc:  # noqa: BLE001 - malformed native packages fail closed.
            validation_errors.append(str(exc))
        try:
            validation_errors.extend(adapter.validate(run_dir))
        except Exception as exc:  # noqa: BLE001 - validation is an executor trust boundary.
            validation_errors.append(str(exc))
        validated_contract: dict[str, Any] = {}
        if not validation_errors:
            try:
                loaded_native = adapter.load(run_dir)
                load_errors = (
                    loaded_native.get("errors")
                    if isinstance(loaded_native.get("errors"), list)
                    else []
                )
                validation_errors.extend(str(item) for item in load_errors if str(item))
                validated_contract = (
                    loaded_native.get("contract")
                    if isinstance(loaded_native.get("contract"), dict)
                    else loaded_native
                )
            except Exception as exc:  # noqa: BLE001 - loading must fail closed too.
                validation_errors.append(str(exc))
        execution = (
            validated_contract.get("execution")
            if isinstance(validated_contract.get("execution"), dict)
            else raw_contract.get("execution")
        )
        if execution != adapter.execution:
            validation_errors.append(
                f"contract.execution is not the native {adapter.backend} descriptor"
            )
        if validation_errors:
            action = {
                "kind": f"inspect_{adapter.route_kind}",
                "method": "GET",
                "endpoint": "GET /runs/{task_id}/package",
                "body": {},
                "reason": (
                    f"native {adapter.contract_kind} contract or "
                    f"{adapter.governor} directive is invalid"
                ),
            }
            return _route_failure(
                run_dir,
                phase=adapter.invalid_error_code,
                error=f"{adapter.route_kind.replace('_', ' ')} validation failed",
                error_code=adapter.invalid_error_code,
                next_action=action,
                validation_errors=validation_errors,
            )
        return {
            "ok": True,
            "native": True,
            "kind": adapter.route_kind,
            "backend": adapter.backend,
            "execution": dict(execution),
            "adapter": adapter.to_dict(),
            "native_validation_errors": [],
        }

    if (
        str(raw_contract.get("assigned_governor") or "") == "Ceraxia"
        and str(raw_contract.get("kind") or "").lower() == "code"
    ):
        try:
            action = _reprepare_action(run_dir, raw_contract)
        except ValueError as exc:
            return _route_failure(
                run_dir,
                phase="legacy_ceraxia_reprepare_required",
                error=f"legacy Ceraxia run cannot be migrated safely: {exc}",
                error_code="task_memory_reference_missing",
                next_action=_task_memory_lineage_repair_action(
                    "reconcile the legacy run's immutable task page before creating a child"
                ),
            )
        return _route_failure(
            run_dir,
            phase="legacy_ceraxia_reprepare_required",
            error="legacy Ceraxia code packages cannot be executed",
            error_code="legacy_ceraxia_reprepare_required",
            next_action=action,
        )

    return {
        "ok": True,
        "native": False,
        "kind": "generic_pipeline",
        "backend": "legacy_pipeline",
        "execution": {},
    }


def _native_mission_ref_errors(run_dir: Path) -> list[str]:
    """Validate the durable protocol link required before native execution."""
    errors: list[str] = []
    ref_path = run_dir / "mission_ref.json"
    if ref_path.is_symlink() or not ref_path.is_file():
        return ["mission_ref.json is missing or not a regular file"]
    mission_ref, ref_error = load_json_object(ref_path, "mission_ref")
    if ref_error:
        return [ref_error]

    contract, contract_error = load_json_object(run_dir / "contract.json", "contract")
    if contract_error:
        return [contract_error]
    expected_mission_id = str(contract.get("mission_id") or "").strip()
    linked_mission_id = str(mission_ref.get("mission_id") or "").strip()
    if not expected_mission_id or linked_mission_id != expected_mission_id:
        errors.append("mission_ref mission_id does not match the native contract")

    raw_mission_dir = str(mission_ref.get("mission_dir") or "").strip()
    mission_dir = Path(raw_mission_dir) if raw_mission_dir else None
    if mission_dir is None or mission_dir.is_symlink() or not mission_dir.is_dir():
        errors.append("mission_ref mission_dir does not exist as a real directory")
        return errors

    mission_path = mission_dir / "mission.json"
    if mission_path.is_symlink() or not mission_path.is_file():
        errors.append("linked mission.json is missing or not a regular file")
        return errors
    mission, mission_error = load_json_object(mission_path, "mission")
    if mission_error:
        errors.append(mission_error)
    elif str(mission.get("mission_id") or "").strip() != expected_mission_id:
        errors.append("linked mission.json mission_id does not match the native contract")
    return errors


def _native_preflight(
    run_dir: Path,
    route: dict[str, Any],
    *,
    mode: str,
    timeout_sec: int,
    force: bool = False,
) -> dict[str, Any]:
    adapter = native_adapter_for_route(route)
    if adapter is None:
        raise ValueError("native preflight requires an adapter-backed route")
    health = _native_backend_health(adapter, timeout_sec)
    mission_ref_errors = _native_mission_ref_errors(run_dir)
    preflight = {
        "ok": (
            bool(route.get("ok"))
            and bool(health.get("ok"))
            and not mission_ref_errors
        ),
        "task_id": run_dir.name,
        "mode": mode,
        "run_dir": str(run_dir),
        "backend_route": route,
        "execution": route.get("execution", {}),
        "native_validation_errors": route.get("native_validation_errors", []),
        "backend_health": health,
        "mission_ref_errors": mission_ref_errors,
        "step_ids": [adapter.step_id],
        "steps": [{"step_id": adapter.step_id, "backend": adapter.backend}],
        # Native packages never enter dispatch, artifact-dependency, local-command,
        # oversight, or per-worker preflight.
        "dispatch_errors": [],
        "oversight_errors": [],
        "oversight_summary": {},
        "input_failures": [],
        "missing_local_commands": [],
        "worker_preflight_failures": [],
    }
    summary = run_summary(run_dir)
    run_status = str(summary.get("status") or "")
    preflight["run_status"] = run_status
    run_actions = summary.get("actions") if isinstance(summary.get("actions"), dict) else {}
    run_next_action = (
        run_actions.get("next_action")
        if isinstance(run_actions.get("next_action"), dict)
        else {}
    )
    preflight["run_next_action"] = run_next_action
    # Native terminal evidence is immutable.  A second attempt must be a fresh
    # governor mission (or the terminal result's own backend action), never an
    # in-place forced rewrite of the old ledger and protocol trail.
    del force
    immutable_terminal = run_status in {"blocked", "completed", "failed", "cancelled", "corrupt"}
    force_required = bool(run_actions.get("force_required_for_rerun")) and not immutable_terminal
    can_start_run = not mission_ref_errors and bool(health.get("ok")) and (
        bool(run_actions.get("can_start"))
        or bool(run_actions.get("can_resume"))
        or bool(run_actions.get("can_start_revision"))
        or bool(run_actions.get("can_execute_revision"))
    ) and not immutable_terminal
    if can_start_run:
        revision_runnable = bool(
            run_actions.get("can_start_revision")
            or run_actions.get("can_execute_revision")
        )
        if revision_runnable and run_next_action:
            # A native revision is an attempt inside the same durable mission.
            # Preserve the backend-issued token and revision endpoint instead of
            # laundering it into a generic start action.
            start_action = action_for_mode(run_next_action, mode)
        else:
            start_action = {
                "kind": f"start_{adapter.route_kind}",
                "method": "POST",
                "endpoint": (
                    "POST /runs/{task_id}/start_local"
                    if mode == "local"
                    else "POST /runs/{task_id}/start_http"
                ),
                "body": {},
                "reason": (
                    f"native contract, {adapter.governor} directive, mission link, "
                    f"and {adapter.backend} health passed"
                ),
            }
        preflight["actions"] = {
            "can_start_run": True,
            "can_start_revision": revision_runnable,
            "can_inspect_package": True,
            "force_required_for_rerun": force_required,
            "terminal_run_immutable": immutable_terminal,
            "next_action": start_action,
        }
    elif mission_ref_errors:
        inspect_action = {
            "kind": "inspect_mission_link",
            "method": "GET",
            "endpoint": "GET /runs/{task_id}/package",
            "body": {},
            "reason": "native execution requires a durable matching mission_ref",
        }
        preflight["actions"] = {
            "can_start_run": False,
            "can_inspect_package": True,
            "force_required_for_rerun": force_required,
            "terminal_run_immutable": immutable_terminal,
            "next_action": inspect_action,
        }
    elif not health.get("ok"):
        retry_action = {
            "kind": "retry_native_preflight",
            "method": "POST",
            "endpoint": "POST /runs/{task_id}/preflight_http",
            "body": {},
            "reason": f"the declared {adapter.backend} backend is unavailable",
        }
        preflight["actions"] = {
            "can_start_run": False,
            "can_inspect_package": True,
            "force_required_for_rerun": force_required,
            "terminal_run_immutable": immutable_terminal,
            "next_action": retry_action,
        }
    elif immutable_terminal:
        terminal_action = run_next_action
        if not terminal_action:
            contract, _ = load_json_object(run_dir / "contract.json", "contract")
            try:
                terminal_action = _native_reprepare_action(run_dir, contract, adapter)
            except ValueError as exc:
                terminal_action = _task_memory_lineage_repair_action(
                    f"terminal run task-memory provenance must be reconciled first: {exc}"
                )
            if adapter is NATIVE_CODE_ADAPTER:
                if terminal_action.get("kind") != "task_memory_lineage_repair_required":
                    terminal_action["kind"] = "reprepare_ceraxia_run"
                    terminal_action["reason"] = (
                        "native terminal evidence is immutable; create a fresh Ceraxia mission"
                    )
        preflight["actions"] = {
            "can_start_run": False,
            "can_inspect_package": True,
            "force_required_for_rerun": False,
            "terminal_run_immutable": True,
            "next_action": terminal_action,
        }
    else:
        preflight["actions"] = {
            "can_start_run": False,
            "can_inspect_package": True,
            "force_required_for_rerun": force_required,
            "terminal_run_immutable": immutable_terminal,
            "next_action": run_next_action,
        }
    return preflight


def run_execution_preflight(
    run_dir: Path,
    mode: str,
    workspace_root: Path | None = None,
    host: str = "127.0.0.1",
    timeout_sec: int = 10,
    step_ids: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    if mode not in {"local", "http"}:
        raise ValueError("mode must be local or http")
    host = validate_service_host(host)
    backend_route = execution_backend_route(run_dir)
    if not backend_route.get("ok"):
        return {
            **backend_route,
            "mode": mode,
            "host": host if mode == "http" else "",
            "workspace_root": str(workspace_root) if workspace_root is not None else "",
            "step_ids": [],
            "steps": [],
            "dispatch_errors": [],
            "oversight_errors": [],
            "oversight_summary": {},
            "input_failures": [],
            "missing_local_commands": [],
            "worker_preflight_failures": [],
            "backend_route": backend_route,
        }
    if native_adapter_for_route(backend_route) is not None:
        return _native_preflight(
            run_dir,
            backend_route,
            mode=mode,
            timeout_sec=timeout_sec,
            force=force,
        )

    status = load_json_file(run_dir / "status.json")
    planned_steps = status.get("steps") if isinstance(status.get("steps"), list) else []
    selected = set(step_ids or [])
    order = [
        str(step.get("step_id") or "")
        for step in planned_steps
        if isinstance(step, dict) and step.get("step_id") and (not selected or str(step.get("step_id") or "") in selected)
    ]
    selected_order = {step_id: index for index, step_id in enumerate(order)}
    producer_by_artifact: dict[str, str] = {}
    for step in planned_steps:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("step_id") or "")
        expected = step.get("expected_artifacts") if isinstance(step.get("expected_artifacts"), list) else []
        for artifact in expected:
            if isinstance(artifact, str):
                producer_by_artifact[artifact] = step_id

    dispatch_errors: list[dict[str, Any]] = []
    input_failures: list[dict[str, Any]] = []
    step_checks: list[dict[str, Any]] = []
    missing_local_commands: list[dict[str, Any]] = []
    oversight_errors: list[str] = []
    oversight_payload = run_oversight(run_dir)
    if not oversight_payload.get("ok"):
        oversight_errors.append(str(oversight_payload.get("error") or "oversight unavailable"))
    else:
        oversight = oversight_payload.get("oversight") if isinstance(oversight_payload.get("oversight"), dict) else {}
        oversight_errors.extend(validate_oversight_against_run(run_dir, oversight, status))
    for dispatch_path in ordered_dispatch_paths(run_dir, step_ids=step_ids):
        try:
            packet = load_json_file(dispatch_path)
        except Exception as exc:  # noqa: BLE001 - preflight reports all unreadable dispatch packets.
            dispatch_errors.append({"dispatch": str(dispatch_path), "error": str(exc)})
            continue
        step_id = str(packet.get("step_id") or dispatch_path.stem)
        worker = str(packet.get("worker") or "")
        request = packet.get("request") if isinstance(packet.get("request"), dict) else packet
        input_artifacts = request.get("input_artifacts") if isinstance(request.get("input_artifacts"), list) else []
        input_status: list[dict[str, Any]] = []
        for artifact in input_artifacts:
            artifact_text = str(artifact)
            producer = producer_by_artifact.get(artifact_text, "")
            produced_by_selected = producer in selected_order and selected_order[producer] < selected_order.get(step_id, -1)
            status_item: dict[str, Any] = {
                "path": artifact_text,
                "producer_step_id": producer,
                "produced_by_selected_step": produced_by_selected,
            }
            if produced_by_selected:
                status_item["exists"] = None
                status_item["source"] = "selected_dependency"
            elif workspace_root is not None:
                errors = input_artifact_errors({"input_artifacts": [artifact]}, workspace_root)
                status_item.update(sandbox_artifact_file_status(str(workspace_root), artifact_text))
                if errors:
                    status_item["errors"] = errors
                    input_failures.append({"step_id": step_id, "worker": worker, "path": artifact_text, "errors": errors})
            else:
                status_item["exists"] = None
                status_item["source"] = "workspace_unknown"
            input_status.append(status_item)
        if mode == "local" and worker not in WORKER_COMMANDS:
            missing_local_commands.append({"step_id": step_id, "worker": worker, "error": "no local command registered"})
        step_checks.append(
            {
                "step_id": step_id,
                "worker": worker,
                "dispatch": str(dispatch_path),
                "input_artifacts": input_artifacts,
                "input_artifact_status": input_status,
            }
        )
    if mode == "http":
        worker_failures = preflight_http_workers(run_dir, host, timeout_sec, step_ids=step_ids)
    else:
        worker_failures = []
    preflight = {
        "ok": not dispatch_errors and not input_failures and not missing_local_commands and not worker_failures and not oversight_errors,
        "task_id": run_dir.name,
        "mode": mode,
        "run_dir": str(run_dir),
        "host": host if mode == "http" else "",
        "workspace_root": str(workspace_root) if workspace_root is not None else "",
        "step_ids": order,
        "steps": step_checks,
        "dispatch_errors": dispatch_errors,
        "oversight_errors": oversight_errors,
        "oversight_summary": run_oversight_summary(run_dir) if not oversight_errors else {},
        "input_failures": input_failures,
        "missing_local_commands": missing_local_commands,
        "worker_preflight_failures": worker_failures,
    }
    summary = run_summary(run_dir)
    preflight["run_status"] = str(summary.get("status") or "")
    preflight["run_next_action"] = summary.get("actions", {}).get("next_action", {}) if isinstance(summary.get("actions"), dict) else {}
    preflight["actions"] = run_preflight_actions(
        preflight,
        summary.get("actions") if isinstance(summary.get("actions"), dict) else {},
    )
    return preflight


def planned_step_ids_from_run(run_dir: Path) -> list[str]:
    status = load_json_file(run_dir / "status.json")
    steps = status.get("steps") if isinstance(status.get("steps"), list) else []
    return [
        str(step.get("step_id") or "")
        for step in steps
        if isinstance(step, dict) and str(step.get("step_id") or "")
    ]


def validate_requested_step_ids(run_dir: Path, requested: list[str], allowed: list[str] | None = None) -> None:
    available = planned_step_ids_from_run(run_dir)
    unknown = [step_id for step_id in requested if step_id not in available]
    if unknown:
        raise ValueError(f"step_ids reference unknown run steps: {unknown}")
    if allowed is not None:
        blocked = [step_id for step_id in requested if step_id not in allowed]
        if blocked:
            raise ValueError(f"step_ids are not valid for this execution mode: {blocked}")


def record_run_preflight_event(run_dir: Path, preflight: dict[str, Any]) -> None:
    ledger_path = run_dir / "task_ledger.json"
    if not ledger_path.exists():
        return
    payload = {
        "mode": str(preflight.get("mode") or ""),
        "ok": bool(preflight.get("ok")),
        "step_ids": preflight.get("step_ids") if isinstance(preflight.get("step_ids"), list) else [],
        "dispatch_errors": len(preflight.get("dispatch_errors") if isinstance(preflight.get("dispatch_errors"), list) else []),
        "oversight_errors": len(preflight.get("oversight_errors") if isinstance(preflight.get("oversight_errors"), list) else []),
        "input_failures": len(preflight.get("input_failures") if isinstance(preflight.get("input_failures"), list) else []),
        "missing_local_commands": len(preflight.get("missing_local_commands") if isinstance(preflight.get("missing_local_commands"), list) else []),
        "worker_preflight_failures": len(preflight.get("worker_preflight_failures") if isinstance(preflight.get("worker_preflight_failures"), list) else []),
        "backend": str(
            (preflight.get("backend_route") or {}).get("backend")
            if isinstance(preflight.get("backend_route"), dict)
            else ""
        ),
        "backend_health_ok": bool(
            (preflight.get("backend_health") or {}).get("ok")
            if isinstance(preflight.get("backend_health"), dict)
            else False
        ),
    }
    TaskLedger.load(ledger_path).record_event("run_preflight_recorded", payload)


def orchestrate_prepare_task(
    message: str,
    task_id: str | None,
    run_root: Path,
    governor_transport: str = "local",
    governor_host: str = "127.0.0.1",
    run_mode: str = "http",
    host: str = "127.0.0.1",
    timeout_sec: int = 30,
    include_brigade_health: bool = False,
    forced_governor: str | None = None,
    commander_order: dict[str, Any] | None = None,
    require_commander_order: bool = True,
    mission: dict[str, Any] | None = None,
    task_memory_id: str = "",
) -> dict[str, Any]:
    if run_mode not in {"local", "http"}:
        raise ValueError("run_mode must be local or http")
    trace: list[dict[str, Any]] = []
    task_preflight = preflight_task(
        message,
        task_id,
        run_root,
        governor_transport=governor_transport,
        governor_host=governor_host,
        include_brigade_health=include_brigade_health,
        forced_governor=forced_governor,
        commander_order=commander_order,
        require_commander_order=require_commander_order,
    )
    task_preflight_actions = task_preflight.get("actions") if isinstance(task_preflight.get("actions"), dict) else {}
    trace.append({"stage": "task_preflight", "ok": bool(task_preflight.get("ok")), "next_action": task_preflight_actions.get("next_action", {})})
    if not task_preflight.get("ok"):
        next_action = task_preflight_actions.get("next_action", {}) if isinstance(task_preflight_actions.get("next_action"), dict) else {}
        return {
            "ok": False,
            "phase": "task_preflight",
            "trace": trace,
            "task_preflight": task_preflight,
            "actions": task_preflight_actions,
            "next_action": next_action,
            "client_action": executable_client_action(str(task_preflight.get("task_id") or task_id or ""), next_action),
        }
    task = prepare_task(
        message,
        task_id,
        run_root,
        governor_transport=governor_transport,
        governor_host=governor_host,
        forced_governor=forced_governor,
        commander_order=commander_order,
        require_commander_order=require_commander_order,
        task_memory_id=task_memory_id,
    )
    task_actions = task.get("actions") if isinstance(task.get("actions"), dict) else {}
    trace.append({"stage": "task", "ok": bool(task.get("ok")), "task_id": str(task.get("task_id") or ""), "next_action": task_actions.get("next_action", {})})
    if not task.get("ok"):
        next_action = task_actions.get("next_action", {}) if isinstance(task_actions.get("next_action"), dict) else {}
        return {
            "ok": False,
            "phase": "task",
            "trace": trace,
            "task_preflight": task_preflight,
            "task": task,
            "actions": task_actions,
            "next_action": next_action,
            "client_action": executable_client_action(str(task.get("task_id") or task_id or ""), next_action),
        }
    run_dir = Path(str(task.get("run_dir") or ""))
    if not run_dir.exists():
        next_action = {"kind": "inspect_existing_run", "method": "GET", "endpoint": "GET /runs/{task_id}/summary", "body": {}, "reason": "run directory is missing after task creation"}
        return {
            "ok": False,
            "phase": "task",
            "trace": trace,
            "task_preflight": task_preflight,
            "task": task,
            "error": "task did not create a run directory",
            "next_action": next_action,
            "client_action": executable_client_action(str(task.get("task_id") or task_id or ""), next_action),
        }
    mission_linked = False
    if mission is not None:
        try:
            link_run_to_mission(run_dir, mission)
            mission_linked = True
        except Exception as exc:  # noqa: BLE001 - no native preflight may run against an unlinked mission.
            next_action = {
                "kind": "inspect_commander_intake",
                "method": "GET",
                "endpoint": "GET /runs/{task_id}/package",
                "body": {},
                "reason": "run preflight was not attempted because its mission link could not be persisted",
            }
            return {
                "ok": False,
                "phase": "mission_link_failed",
                "error_code": "mission_link_failed",
                "error": str(exc),
                "task_id": str(task.get("task_id") or task_id or ""),
                "run_dir": str(run_dir),
                "trace": trace,
                "task_preflight": task_preflight,
                "task": task,
                "next_action": next_action,
                "client_action": executable_client_action(
                    str(task.get("task_id") or task_id or ""), next_action,
                ),
            }
    preflight_workspace = resolve_run_child_path(run_dir, "", "work") if run_mode == "local" else None
    run_preflight = run_execution_preflight(
        run_dir,
        mode=run_mode,
        workspace_root=preflight_workspace,
        host=host,
        timeout_sec=timeout_sec,
    )
    record_run_preflight_event(run_dir, run_preflight)
    run_preflight_actions = run_preflight.get("actions") if isinstance(run_preflight.get("actions"), dict) else {}
    trace.append(
        {
            "stage": "run_preflight",
            "ok": bool(run_preflight.get("ok")),
            "task_id": str(run_preflight.get("task_id") or task.get("task_id") or ""),
            "next_action": run_preflight_actions.get("next_action", {}),
        }
    )
    next_action = run_preflight_actions.get("next_action", {}) if isinstance(run_preflight_actions.get("next_action"), dict) else {}
    prepared_task_id = str(task.get("task_id") or run_preflight.get("task_id") or "")
    return {
        "ok": bool(run_preflight.get("ok")),
        "phase": "ready_to_start" if run_preflight.get("ok") else "run_preflight",
        "task_id": prepared_task_id,
        "run_dir": str(run_dir),
        "run_mode": run_mode,
        "trace": trace,
        "task_preflight": task_preflight,
        "task": task,
        "mission_linked": mission_linked,
        "run_preflight": run_preflight,
        "actions": run_preflight_actions,
        "next_action": next_action,
        "client_action": executable_client_action(prepared_task_id, next_action),
    }


def _orchestrate_run_task_locked(
    message: str,
    task_id: str | None,
    run_root: Path,
    governor_transport: str = "local",
    governor_host: str = "127.0.0.1",
    run_mode: str = "http",
    host: str = "127.0.0.1",
    timeout_sec: int = 1800,
    include_brigade_health: bool = False,
    auto_start: bool = True,
    force: bool = False,
    reuse_existing: bool = True,
    task_memory_id: str = "",
    root_task_id: str = "",
    parent_task_id: str = "",
) -> dict[str, Any]:
    requested_timeout_sec = max(
        1, min(int(timeout_sec), MAX_RESEARCH_WARBAND_TIMEOUT_SEC)
    )
    prepare_timeout_sec = min(
        requested_timeout_sec, MAX_STANDARD_EXECUTION_TIMEOUT_SEC
    )
    if task_id is not None and not valid_task_id(task_id):
        return {
            "ok": False,
            "phase": "task_preflight",
            "task_id": task_id,
            "error": "invalid task_id",
            "error_code": "invalid_task_id",
            "next_action": {},
            "client_action": {},
        }
    warmaster_root = Path(__file__).resolve().parents[1]
    existing_run = run_root / task_id if task_id else None
    if existing_run is not None and existing_run.is_dir():
        try:
            requested_memory_ref = _task_memory_ref(
                task_id,
                task_memory_id=task_memory_id,
                root_task_id=root_task_id,
                parent_task_id=parent_task_id,
            )
            _verify_parent_task_memory(run_root, requested_memory_ref)
            existing_mission = _existing_mission_request(
                warmaster_root,
                task_id=task_id,
                message=message,
                ref=requested_memory_ref,
            )
            memory_ref = _persist_task_memory_ref(
                existing_run,
                requested_memory_ref,
            )
        except (OSError, ValueError) as exc:
            mission_replay_error = isinstance(exc, ExistingMissionReplayError)
            return {
                "ok": False,
                "phase": "task_preflight",
                "task_id": task_id,
                "error": (
                    f"commander intake cannot reuse this immutable task_id: {exc}"
                    if mission_replay_error
                    else f"task-memory lineage conflict: {exc}"
                ),
                "error_code": (
                    exc.error_code
                    if mission_replay_error
                    else (
                        "task_memory_parent_conflict"
                        if isinstance(exc, TaskMemoryParentConflict)
                        else "task_memory_identity_conflict"
                    )
                ),
                "next_action": {},
                "client_action": {},
            }
        task_memory_init = _ensure_task_memory_page(
            memory_ref, message, existing_mission,
        )
        if not task_memory_init.get("ok"):
            return {
                "ok": False,
                "retryable": bool(task_memory_init.get("retryable")),
                "phase": "task_memory_retry",
                "task_id": task_id,
                "run_dir": str(existing_run),
                "error": str(
                    task_memory_init.get("warning")
                    or "Archive task page is temporarily unavailable"
                ),
                "error_code": str(
                    task_memory_init.get("error_code") or "task_memory_unavailable"
                ),
                "trace": [task_memory_init],
                "next_action": {},
                "client_action": {},
            }
        # A task id owns one immutable mission protocol.  Never call
        # open_mission() or relink a durable run that already exists: doing so
        # would overwrite terminal evidence before start authorization runs.
        state = orchestration_state(existing_run, event_limit=5, events_after=0)
        decision = state.get("decision") if isinstance(state.get("decision"), dict) else {}
        next_action = (
            state.get("next_action")
            if isinstance(state.get("next_action"), dict)
            else {}
        )
        if not reuse_existing:
            return {
                "ok": False,
                "phase": "task_preflight",
                "task_id": task_id,
                "run_dir": str(existing_run),
                "error": "task_id already exists; use a fresh task_id",
                "error_code": "task_exists",
                "reused_existing": False,
                "orchestration": state,
                "decision": decision,
                "display": state.get("display", {}),
                "display_events": state.get("display_events", []),
                "next_action": next_action,
                "client_action": state.get("client_action", {}),
            }
        should_start = auto_start and (
            bool(decision.get("can_start"))
            or bool(decision.get("can_resume"))
            or bool(decision.get("can_execute_revision"))
            or force
        )
        if should_start:
            started = orchestrate_start_run(
                run_root,
                task_id,
                run_mode=run_mode,
                host=host,
                timeout_sec=_execution_timeout_for_run(
                    existing_run, requested_timeout_sec
                ),
                force=force,
            )
            state = orchestration_state(existing_run, event_limit=5, events_after=0)
            return {
                "ok": bool(started.get("ok")),
                "phase": "started" if started.get("ok") else "existing_run",
                "task_id": task_id,
                "run_dir": str(existing_run),
                "run_mode": run_mode,
                "reused_existing": True,
                "trace": [
                    task_memory_init,
                    {
                        "stage": "existing_run",
                        "ok": bool(started.get("ok")),
                        "task_id": task_id,
                        "next_action": started.get("next_action", {}),
                    },
                ],
                "start": started,
                "orchestration": state,
                "decision": state.get("decision", {}),
                "display": state.get("display", {}),
                "display_events": state.get("display_events", []),
                "next_action": (
                    started.get("next_action")
                    if isinstance(started.get("next_action"), dict)
                    else state.get("next_action", {})
                ),
                "client_action": state.get("client_action", {}),
            }
        return {
            "ok": True,
            "phase": "existing_run",
            "task_id": task_id,
            "run_dir": str(existing_run),
            "run_mode": run_mode,
            "reused_existing": True,
            "trace": [
                task_memory_init,
                {"stage": "existing_run", "ok": True, "task_id": task_id},
            ],
            "orchestration": state,
            "decision": decision,
            "display": state.get("display", {}),
            "display_events": state.get("display_events", []),
            "next_action": next_action,
            "client_action": state.get("client_action", {}),
        }
    try:
        requested_memory_ref = _task_memory_ref(
            str(task_id or task_id_for_message(message)),
            task_memory_id=task_memory_id,
            root_task_id=root_task_id,
            parent_task_id=parent_task_id,
        )
        _verify_parent_task_memory(run_root, requested_memory_ref)
    except TaskMemoryParentConflict as exc:
        return {
            "ok": False,
            "phase": "task_preflight",
            "task_id": task_id or "",
            "error": str(exc),
            "error_code": "task_memory_parent_conflict",
            "next_action": {},
            "client_action": {},
        }
    except ValueError as exc:
        return {
            "ok": False,
            "phase": "task_preflight",
            "task_id": task_id or "",
            "error": str(exc),
            "error_code": "invalid_task_memory_identity",
            "next_action": {},
            "client_action": {},
        }
    mission = open_mission(
        warmaster_root,
        message,
        task_id,
        source_channel="main_chat",
        task_memory=requested_memory_ref,
    )
    if not mission.get("ok"):
        return {
            "ok": False,
            "phase": "commander_intake",
            "task_id": task_id or "",
            "mission_id": str(mission.get("mission_id") or ""),
            "mission": mission,
            "error": str(mission.get("error") or "Warmaster commander intake failed"),
            "error_code": str(mission.get("error_code") or "commander_intake_failed"),
            "next_action": {
                "kind": "inspect_commander_intake",
                "method": "GET",
                "endpoint": "GET /missions/{mission_id}",
                "body": {},
                "reason": "Warmaster could not form a commander order",
            },
        }
    command = mission.get("commander_order") if isinstance(mission.get("commander_order"), dict) else {}
    governor_message = str(mission.get("governor_task") or message)
    task_memory_init = _ensure_task_memory_page(
        requested_memory_ref, message, mission,
    )
    if not task_memory_init.get("ok"):
        return {
            "ok": False,
            "retryable": bool(task_memory_init.get("retryable")),
            "phase": "task_memory_retry",
            "task_id": task_id or "",
            "mission_id": str(mission.get("mission_id") or ""),
            "error": str(
                task_memory_init.get("warning")
                or "Archive task page is temporarily unavailable"
            ),
            "error_code": str(
                task_memory_init.get("error_code") or "task_memory_unavailable"
            ),
            "trace": [
                {
                    "stage": "commander_intake",
                    "ok": True,
                    "mission_id": str(mission.get("mission_id") or ""),
                    "assigned_governor": str(
                        (mission.get("commander_order") or {}).get("to") or ""
                    ),
                },
                task_memory_init,
            ],
            "next_action": {},
            "client_action": {},
        }
    prepared = orchestrate_prepare_task(
        governor_message,
        task_id,
        run_root,
        governor_transport=governor_transport,
        governor_host=governor_host,
        run_mode=run_mode,
        host=host,
        timeout_sec=min(prepare_timeout_sec, 300),
        include_brigade_health=include_brigade_health,
        forced_governor=str(command.get("to") or "") or None,
        commander_order=command,
        require_commander_order=True,
        mission=mission,
        task_memory_id=requested_memory_ref["task_memory_id"],
    )
    trace = list(prepared.get("trace") if isinstance(prepared.get("trace"), list) else [])
    trace.insert(
        0,
        {
            "stage": "commander_intake",
            "ok": True,
            "mission_id": str(mission.get("mission_id") or ""),
            "assigned_governor": str((mission.get("commander_order") or {}).get("to") or ""),
        },
    )
    trace.insert(1, task_memory_init)
    run_task_id = str(prepared.get("task_id") or task_id or "")
    if not prepared.get("ok"):
        task_preflight = prepared.get("task_preflight") if isinstance(prepared.get("task_preflight"), dict) else {}
        if reuse_existing and task_preflight.get("error_code") == "task_exists" and task_id:
            run_dir = run_root / task_id
            state = orchestration_state(run_dir, event_limit=5, events_after=0) if run_dir.exists() else {}
            decision = state.get("decision") if isinstance(state.get("decision"), dict) else {}
            should_start = auto_start and (
                bool(decision.get("can_start"))
                or bool(decision.get("can_resume"))
                or bool(decision.get("can_execute_revision"))
                or force
            )
            if should_start:
                started = orchestrate_start_run(
                    run_root,
                    task_id,
                    run_mode=run_mode,
                    host=host,
                    timeout_sec=_execution_timeout_for_run(
                        run_dir, requested_timeout_sec
                    ),
                    force=force,
                )
                trace.append(
                    {
                        "stage": "orchestrate_start",
                        "ok": bool(started.get("ok")),
                        "task_id": task_id,
                        "next_action": started.get("next_action") if isinstance(started.get("next_action"), dict) else {},
                    }
                )
                if started.get("error_code") == "legacy_ceraxia_reprepare_required":
                    return {
                        **started,
                        "run_mode": run_mode,
                        "reused_existing": True,
                        "trace": trace,
                        "prepare": prepared,
                        "start": started,
                    }
                state = orchestration_state(run_dir, event_limit=5, events_after=0) if run_dir.exists() else {}
                return {
                    "ok": bool(started.get("ok")),
                    "phase": "started" if started.get("ok") else "existing_run",
                    "task_id": task_id,
                    "run_mode": run_mode,
                    "reused_existing": True,
                    "trace": trace,
                    "prepare": prepared,
                    "start": started,
                    "orchestration": state,
                    "decision": state.get("decision", {}) if isinstance(state, dict) else {},
                    "display": state.get("display", {}) if isinstance(state, dict) else {},
                    "display_events": state.get("display_events", []) if isinstance(state, dict) else [],
                    "next_action": started.get("next_action") if isinstance(started.get("next_action"), dict) else state.get("next_action", {}),
                    "client_action": state.get("client_action", {}) if isinstance(state, dict) else {},
                }
            return {
                "ok": True,
                "phase": "existing_run",
                "task_id": task_id,
                "run_mode": run_mode,
                "reused_existing": True,
                "trace": trace,
                "prepare": prepared,
                "orchestration": state,
                "decision": state.get("decision", {}) if isinstance(state, dict) else {},
                "display": state.get("display", {}) if isinstance(state, dict) else {},
                "display_events": state.get("display_events", []) if isinstance(state, dict) else [],
                "next_action": state.get("next_action", {}) if isinstance(state, dict) else {},
                "client_action": state.get("client_action", {}) if isinstance(state, dict) else {},
            }
        return {
            "ok": False,
            "phase": str(prepared.get("phase") or "prepare_failed"),
            "task_id": run_task_id,
            "trace": trace,
            "prepare": prepared,
            "next_action": prepared.get("next_action") if isinstance(prepared.get("next_action"), dict) else {},
            "client_action": prepared.get("client_action") if isinstance(prepared.get("client_action"), dict) else {},
        }
    run_dir = run_root / run_task_id
    try:
        if not run_task_id:
            raise ValueError("prepared run did not return a task_id")
        memory_ref = _persist_task_memory_ref(
            run_dir,
            _task_memory_ref(
                run_task_id,
                task_memory_id=requested_memory_ref["task_memory_id"],
                root_task_id=requested_memory_ref["root_task_id"],
                parent_task_id=requested_memory_ref["parent_task_id"],
            ),
        )
        if prepared.get("mission_linked") is not True:
            link_run_to_mission(run_dir, mission)
    except Exception as exc:  # noqa: BLE001 - execution must not race an unlinked mission.
        return {
            "ok": False,
            "phase": "mission_link_failed",
            "error_code": "mission_link_failed",
            "error": str(exc),
            "task_id": run_task_id,
            "trace": trace,
            "prepare": prepared,
            "next_action": {
                "kind": "inspect_commander_intake",
                "method": "GET",
                "endpoint": "GET /runs/{task_id}/package",
                "body": {},
                "reason": "run was not started because its mission link could not be persisted",
            },
        }
    if not auto_start:
        state = orchestration_state(run_root / run_task_id, event_limit=5, events_after=0)
        return {
            "ok": True,
            "phase": "ready_to_start",
            "task_id": run_task_id,
            "run_dir": str(run_root / run_task_id),
            "mission_id": str(mission.get("mission_id") or ""),
            "mission": {
                "mission_id": str(mission.get("mission_id") or ""),
                "assigned_governor": str((mission.get("commander_order") or {}).get("to") or ""),
                "mission_dir": str(mission.get("mission_dir") or ""),
            },
            "trace": trace,
            "prepare": prepared,
            "next_action": prepared.get("next_action") if isinstance(prepared.get("next_action"), dict) else {},
            "orchestration": state,
            "decision": state.get("decision", {}),
            "display": state.get("display", {}),
            "display_events": state.get("display_events", []),
            "client_action": prepared.get("client_action") if isinstance(prepared.get("client_action"), dict) else state.get("client_action", {}),
        }
    started = orchestrate_start_run(
        run_root,
        run_task_id,
        run_mode=run_mode,
        host=host,
        timeout_sec=_execution_timeout_for_run(run_dir, requested_timeout_sec),
        force=force,
    )
    trace.append(
        {
            "stage": "orchestrate_start",
            "ok": bool(started.get("ok")),
            "task_id": run_task_id,
            "next_action": started.get("next_action") if isinstance(started.get("next_action"), dict) else {},
        }
    )
    if started.get("error_code") == "legacy_ceraxia_reprepare_required":
        return {
            **started,
            "mission_id": str(mission.get("mission_id") or ""),
            "run_mode": run_mode,
            "trace": trace,
            "prepare": prepared,
            "start": started,
        }
    state = orchestration_state(run_dir, event_limit=5, events_after=0) if run_dir.exists() else {}
    return {
        "ok": bool(started.get("ok")),
        "phase": "started" if started.get("ok") else "start_failed",
        "task_id": run_task_id,
        "task_memory": memory_ref,
        "mission_id": str(mission.get("mission_id") or ""),
        "mission": {
            "mission_id": str(mission.get("mission_id") or ""),
            "assigned_governor": str((mission.get("commander_order") or {}).get("to") or ""),
            "mission_dir": str(mission.get("mission_dir") or ""),
        },
        "run_mode": run_mode,
        "trace": trace,
        "prepare": prepared,
        "start": started,
        "orchestration": state,
        "decision": state.get("decision", {}) if isinstance(state, dict) else {},
        "display": state.get("display", {}) if isinstance(state, dict) else {},
        "display_events": state.get("display_events", []) if isinstance(state, dict) else [],
        "next_action": started.get("next_action") if isinstance(started.get("next_action"), dict) else {},
        "client_action": state.get("client_action", {}) if isinstance(state, dict) else {},
    }


def orchestrate_run_task(
    message: str,
    task_id: str | None,
    run_root: Path,
    governor_transport: str = "local",
    governor_host: str = "127.0.0.1",
    run_mode: str = "http",
    host: str = "127.0.0.1",
    timeout_sec: int = 1800,
    include_brigade_health: bool = False,
    auto_start: bool = True,
    force: bool = False,
    reuse_existing: bool = True,
    task_memory_id: str = "",
    root_task_id: str = "",
    parent_task_id: str = "",
) -> dict[str, Any]:
    resolved_task_id = task_id or task_id_for_message(message)
    reservation_key = mission_id_for(resolved_task_id, message)
    with _orchestrate_task_reservation(run_root, reservation_key):
        return _orchestrate_run_task_locked(
            message,
            resolved_task_id,
            run_root,
            governor_transport=governor_transport,
            governor_host=governor_host,
            run_mode=run_mode,
            host=host,
            timeout_sec=timeout_sec,
            include_brigade_health=include_brigade_health,
            auto_start=auto_start,
            force=force,
            reuse_existing=reuse_existing,
            task_memory_id=task_memory_id,
            root_task_id=root_task_id,
            parent_task_id=parent_task_id,
        )


def revision_step_ids_from_run(run_dir: Path) -> list[str]:
    ledger_path = run_dir / "task_ledger.json"
    ledger, ledger_error = load_ledger_dict(ledger_path)
    if ledger_error:
        raise ValueError(f"ledger unavailable for revision execution: {ledger_error}")
    result = ledger.get("result", {}) if isinstance(ledger.get("result"), dict) else {}
    revision_plan = result.get("revision_plan") if isinstance(result.get("revision_plan"), dict) else {}
    if not revision_plan.get("required"):
        raise ValueError("run does not have a required revision_plan")
    revision_errors = validate_revision_plan(run_dir, revision_plan)
    if revision_errors:
        raise ValueError(f"revision_plan is invalid: {revision_errors}")
    raw_steps = revision_plan.get("steps", [])
    if not isinstance(raw_steps, list):
        raise ValueError("revision_plan.steps must be a list")
    requested: list[str] = []
    for item in raw_steps:
        if isinstance(item, dict):
            step_id = str(item.get("step_id") or "").strip()
            if step_id and step_id not in requested:
                requested.append(step_id)
    oversight_payload = run_oversight(run_dir)
    oversight = oversight_payload.get("oversight") if isinstance(oversight_payload.get("oversight"), dict) else {}
    revision_policy = oversight.get("revision_policy") if isinstance(oversight.get("revision_policy"), dict) else {}
    policy_final_steps = revision_policy.get("final_steps") if isinstance(revision_policy.get("final_steps"), list) else []
    final_steps = [str(step_id) for step_id in policy_final_steps if isinstance(step_id, str) and step_id] or ["critic_review", "finalize"]
    for final_step in final_steps:
        if final_step not in requested:
            requested.append(final_step)
    available = {
        path.stem
        for path in ordered_dispatch_paths(run_dir)
    }
    missing = [step_id for step_id in requested if step_id not in available]
    if missing:
        raise ValueError(f"revision_plan references unknown dispatch steps: {missing}")
    return requested


def revision_plan_fingerprint(summary: dict[str, Any]) -> str:
    revision_summary = summary.get("revision_plan_summary") if isinstance(summary.get("revision_plan_summary"), dict) else {}
    revision_plan = summary.get("revision_plan") if isinstance(summary.get("revision_plan"), dict) else {}
    payload = {
        "status": str(summary.get("status") or ""),
        "required": bool(revision_summary.get("required") or revision_plan.get("required")),
        "valid": bool(revision_summary.get("valid")),
        "step_ids": revision_summary.get("step_ids") if isinstance(revision_summary.get("step_ids"), list) else [],
        "workers": revision_summary.get("workers") if isinstance(revision_summary.get("workers"), list) else [],
        "errors": revision_summary.get("errors") if isinstance(revision_summary.get("errors"), list) else [],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def record_research_loop_event(run_dir: Path, event_type: str, payload: dict[str, Any]) -> None:
    ledger_path = run_dir / "task_ledger.json"
    if not ledger_path.exists():
        return
    try:
        TaskLedger.load(ledger_path).record_event(event_type, payload)
    except Exception:
        pass


def execute_routed_run(
    run_dir: Path,
    *,
    run_mode: str,
    host: str,
    timeout_sec: int,
    workspace_root: Path | None = None,
    step_ids: list[str] | None = None,
    execution_mode: str = "full",
) -> dict[str, Any]:
    """The sole backend switch for run execution.

    Public start, resume, revision, recovery, and research-loop paths all call
    this function. Raw executors remain implementation details for generic runs.
    """
    if run_mode not in {"local", "http"}:
        raise ValueError("run_mode must be local or http")
    route = execution_backend_route(run_dir)
    if not route.get("ok"):
        return route
    task_memory_guard = task_memory_start_guard(run_dir)
    if not task_memory_guard.get("ok"):
        return {
            "ok": False,
            "retryable": bool(task_memory_guard.get("retryable")),
            "status": "retry_wait" if task_memory_guard.get("retryable") else "failed",
            "phase": "task_memory_retry",
            "error_code": str(
                task_memory_guard.get("error_code") or "task_memory_unavailable"
            ),
            "error": str(
                task_memory_guard.get("warning")
                or "task memory is not ready for execution"
            ),
            "task_memory": task_memory_guard,
            "next_action": {},
            "client_action": {},
        }
    native_adapter = native_adapter_for_route(route)
    if native_adapter is not None:
        mission_ref_errors = _native_mission_ref_errors(run_dir)
        if mission_ref_errors:
            action = {
                "kind": "inspect_mission_link",
                "method": "GET",
                "endpoint": "GET /runs/{task_id}/package",
                "body": {},
                "reason": "native execution requires a durable matching mission_ref",
            }
            failure = _route_failure(
                run_dir,
                phase="native_mission_link_invalid",
                error="; ".join(mission_ref_errors),
                error_code="native_mission_link_invalid",
                next_action=action,
                validation_errors=mission_ref_errors,
            )
            failure["backend_route"] = route
            failure["mission_ref_errors"] = mission_ref_errors
            return failure
        current_summary = run_summary(run_dir)
        current_status = str(current_summary.get("status") or "")
        current_actions = (
            current_summary.get("actions")
            if isinstance(current_summary.get("actions"), dict)
            else {}
        )
        revision_available = bool(
            current_actions.get("can_start_revision")
            or current_actions.get("can_execute_revision")
        )
        current_next_action = (
            current_actions.get("next_action")
            if isinstance(current_actions.get("next_action"), dict)
            else {}
        )
        if execution_mode != "revision" and (
            current_status in {"revision", "needs_revision"} or revision_available
        ):
            failure = _route_failure(
                run_dir,
                phase="native_revision_mode_required",
                error=(
                    "native mission requires a revision attempt; execute the "
                    "published revision action instead of starting a new run"
                ),
                error_code="native_revision_mode_required",
                next_action=current_next_action,
            )
            failure["backend_route"] = route
            return failure
        if execution_mode == "revision" and not revision_available:
            failure = _route_failure(
                run_dir,
                phase="native_revision_not_available",
                error="the current native mission state does not offer a revision attempt",
                error_code="native_revision_not_available",
                next_action=current_next_action,
            )
            failure["backend_route"] = route
            return failure
        if current_status in {"blocked", "completed", "failed", "cancelled", "corrupt"}:
            action = _native_reprepare_action(
                run_dir,
                load_json_object(run_dir / "contract.json", "contract")[0],
                native_adapter,
            )
            if native_adapter is NATIVE_CODE_ADAPTER:
                action["kind"] = "reprepare_ceraxia_run"
                action["reason"] = (
                    "native terminal evidence is immutable; create a fresh Ceraxia mission"
                )
            failure = _route_failure(
                run_dir,
                phase="native_terminal_immutable",
                error=f"native run is already terminal: {current_status}",
                error_code="native_terminal_immutable",
                next_action=action,
            )
            failure["backend_route"] = route
            return failure
        health = _native_backend_health(native_adapter, timeout_sec)
        if not health.get("ok"):
            action = {
                "kind": "retry_native_preflight",
                "method": "POST",
                "endpoint": "POST /runs/{task_id}/preflight_http",
                "body": {},
                "reason": f"the declared {native_adapter.backend} backend is unavailable",
            }
            failure = _route_failure(
                run_dir,
                phase="native_backend_unavailable",
                error=str(
                    health.get("error")
                    or f"{native_adapter.backend} backend is unavailable"
                ),
                error_code="native_backend_unavailable",
                next_action=action,
            )
            failure["backend_route"] = route
            failure["backend_health"] = health
            transient_research_failure = (
                native_adapter.backend == "ResearchWarband"
            )
            if transient_research_failure:
                # Preflight may pass and the service may disappear in the small
                # window before execution.  No remote mission has been created
                # at this point, so a transient 7201 outage must leave the
                # durable native package startable instead of burning its
                # immutable mission identity as a terminal failure.
                failure["retryable"] = True
            ledger_path = run_dir / "task_ledger.json"
            if ledger_path.exists():
                try:
                    ledger = TaskLedger.load(ledger_path)
                    event = dict(health)
                    if transient_research_failure:
                        event["retryable"] = True
                    ledger.record_event("native_backend_preflight_failed", event)
                    if not transient_research_failure:
                        # Preserve the established Skitarii failure semantics;
                        # the research adapter alone has the fresh immutable
                        # mission retry requirement introduced at cutover.
                        ledger.set_result(failure)
                        ledger.set_status("failed")
                except Exception:  # noqa: BLE001 - failure payload remains authoritative.
                    pass
            return failure
        if native_adapter is NATIVE_CODE_ADAPTER:
            result = run_via_skitarii(
                run_dir,
                run_dir.name,
                timeout_sec=timeout_sec,
                execution_mode=execution_mode,
            )
        elif native_adapter.backend == "ResearchWarband":
            try:
                from .research_warband_bridge import run_via_research_warband
            except ImportError as exc:
                result = {
                    "ok": False,
                    "status": "failed",
                    "error": f"ResearchWarband bridge is unavailable: {exc}",
                    "error_code": "native_backend_unavailable",
                }
            else:
                result = run_via_research_warband(
                    run_dir,
                    run_dir.name,
                    timeout_sec=timeout_sec,
                    execution_mode=execution_mode,
                )
        else:
            result = {
                "ok": False,
                "status": "failed",
                "error": f"no execution bridge is registered for {native_adapter.backend}",
                "error_code": "native_backend_unavailable",
            }
        routed = dict(result) if isinstance(result, dict) else {
            "ok": False,
            "status": "failed",
            "error": f"{native_adapter.backend} backend returned a non-object result",
        }
        routed["backend_route"] = route
        routed["requested_execution_mode"] = execution_mode
        return routed
    if run_mode == "local":
        local_workspace = workspace_root or resolve_run_child_path(run_dir, "", "work")
        return execute_local_run(
            REPO_ROOT,
            run_dir,
            local_workspace,
            timeout_sec=timeout_sec,
            step_ids=step_ids,
            execution_mode=execution_mode,
        )
    return execute_http_run(
        run_dir,
        host=host,
        timeout_sec=timeout_sec,
        workspace_root=workspace_root,
        step_ids=step_ids,
        execution_mode=execution_mode,
    )


def execute_run_cycle(
    run_dir: Path,
    run_mode: str,
    host: str,
    timeout_sec: int,
    operation: str,
) -> dict[str, Any]:
    workspace_root = resolve_run_child_path(run_dir, "", "work")
    backend_route = execution_backend_route(run_dir)
    if not backend_route.get("ok"):
        return backend_route
    if native_adapter_for_route(backend_route) is not None:
        return execute_routed_run(
            run_dir,
            run_mode=run_mode,
            host=host,
            timeout_sec=timeout_sec,
            execution_mode=operation if operation in {"revision", "resume"} else "full",
        )
    step_ids: list[str] | None = None
    execution_mode = "full"
    if operation == "revision":
        step_ids = revision_step_ids_from_run(run_dir)
        execution_mode = "revision"
    elif operation == "resume":
        step_ids = resume_step_ids_from_run(run_dir)
        execution_mode = "resume"
    return execute_routed_run(
        run_dir,
        run_mode=run_mode,
        host=host,
        timeout_sec=timeout_sec,
        workspace_root=workspace_root if run_mode == "local" else None,
        step_ids=step_ids,
        execution_mode=execution_mode,
    )


def research_loop_run(
    run_root: Path,
    task_id: str,
    run_mode: str = "local",
    host: str = "127.0.0.1",
    timeout_sec: int = 1800,
    max_revision_cycles: int = 3,
    allow_resume: bool = True,
    claim_active: bool = True,
) -> dict[str, Any]:
    if run_mode not in {"local", "http"}:
        raise ValueError("run_mode must be local or http")
    host = validate_service_host(host)
    run_dir = run_root / task_id
    timeout_sec = _execution_timeout_for_run(run_dir, timeout_sec)
    max_revision_cycles = max(0, min(int(max_revision_cycles), 8))
    if not run_dir.exists():
        return {"ok": False, "phase": "missing_run", "task_id": task_id, "error": "run not found"}
    if claim_active:
        with ACTIVE_RUNS_LOCK:
            if task_id in ACTIVE_RUNS:
                return {
                    "ok": False,
                    "phase": "already_active",
                    "task_id": task_id,
                    "error": "run already active",
                    "snapshot": run_snapshot(run_dir, event_limit=5, events_after=0),
                }
            ACTIVE_RUNS.add(task_id)
    cycles: list[dict[str, Any]] = []
    seen_revision_fingerprints: set[str] = set()
    revision_cycles = 0
    stop_reason = "unknown"
    try:
        backend_route = execution_backend_route(run_dir)
        if not backend_route.get("ok"):
            return backend_route
        if native_adapter_for_route(backend_route) is not None:
            return execute_routed_run(
                run_dir,
                run_mode=run_mode,
                host=host,
                timeout_sec=timeout_sec,
                execution_mode="full",
            )
        record_research_loop_event(
            run_dir,
            "research_loop_started",
            {
                "mode": f"research_loop_{run_mode}",
                "max_revision_cycles": max_revision_cycles,
                "allow_resume": allow_resume,
            },
        )
        while True:
            summary = run_summary(run_dir)
            actions = summary.get("actions") if isinstance(summary.get("actions"), dict) else {}
            status = str(summary.get("status") or "")
            progress = summary.get("progress") if isinstance(summary.get("progress"), dict) else {}
            pending_step_ids = [str(step_id) for step_id in progress.get("pending_step_ids", []) if step_id]
            cycle: dict[str, Any] = {
                "index": len(cycles),
                "status": status,
                "next_action": actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {},
            }
            if actions.get("can_start") or status in {"created", "assigned"}:
                operation = "full"
            elif allow_resume and actions.get("can_resume") and pending_step_ids:
                operation = "resume"
            elif allow_resume and actions.get("can_resume") and not pending_step_ids:
                planned_steps = int(progress.get("planned_steps") or 0)
                completed_steps = int(progress.get("completed_steps") or 0)
                failed_steps = int(progress.get("failed_steps") or 0)
                if planned_steps > 0 and completed_steps >= planned_steps and failed_steps == 0:
                    TaskLedger.load(run_dir / "task_ledger.json").set_status("completed")
                    record_research_loop_event(
                        run_dir,
                        "research_loop_completed_empty_resume",
                        {"planned_steps": planned_steps, "completed_steps": completed_steps},
                    )
                    cycle["stop_reason"] = "normalized_completed"
                    cycle["resume_skipped"] = "no_pending_steps"
                    cycles.append(cycle)
                    continue
                stop_reason = "needs_attention"
                cycle["stop_reason"] = stop_reason
                cycle["resume_skipped"] = "no_pending_steps"
                cycles.append(cycle)
                break
            elif actions.get("can_execute_revision"):
                if revision_cycles >= max_revision_cycles:
                    stop_reason = "revision_cycle_limit"
                    cycle["stop_reason"] = stop_reason
                    cycles.append(cycle)
                    break
                fingerprint = revision_plan_fingerprint(summary)
                if fingerprint in seen_revision_fingerprints:
                    stop_reason = "repeated_revision_plan"
                    cycle["stop_reason"] = stop_reason
                    cycles.append(cycle)
                    break
                seen_revision_fingerprints.add(fingerprint)
                revision_cycles += 1
                operation = "revision"
                cycle["revision_cycle"] = revision_cycles
                cycle["revision_fingerprint"] = fingerprint
            elif status == "completed":
                acceptance = record_warmaster_acceptance(run_dir)
                cycle["warmaster_acceptance"] = {
                    "ok": bool(acceptance.get("ok")),
                    "accepted": bool(acceptance.get("accepted")),
                    "blocked": bool(acceptance.get("blocked")),
                    "revision_required": bool(acceptance.get("revision_required")),
                    "skipped": bool(acceptance.get("skipped")),
                    "already_recorded": bool(acceptance.get("already_recorded")),
                }
                if acceptance.get("revision_required"):
                    cycle["stop_reason"] = "warmaster_revision_ordered"
                    cycles.append(cycle)
                    continue
                if acceptance.get("blocked"):
                    stop_reason = "warmaster_acceptance_blocked"
                    cycle["stop_reason"] = stop_reason
                    cycles.append(cycle)
                    break
                if acceptance.get("accepted") or acceptance.get("skipped"):
                    stop_reason = "completed"
                    cycle["stop_reason"] = stop_reason
                    cycles.append(cycle)
                    break
                stop_reason = "warmaster_acceptance_failed"
                cycle["stop_reason"] = stop_reason
                cycles.append(cycle)
                break
            elif bool(summary.get("revision_plan_summary", {}).get("required")) and not actions.get("can_execute_revision"):
                stop_reason = "invalid_revision"
                cycle["stop_reason"] = stop_reason
                cycles.append(cycle)
                break
            else:
                stop_reason = "needs_attention"
                cycle["stop_reason"] = stop_reason
                cycles.append(cycle)
                break
            cycle["operation"] = operation
            record_research_loop_event(
                run_dir,
                "research_loop_cycle_started",
                {"cycle": len(cycles), "operation": operation, "revision_cycle": revision_cycles},
            )
            execution = execute_run_cycle(run_dir, run_mode, host, timeout_sec, operation)
            cycle["execution_ok"] = bool(execution.get("ok"))
            cycle["execution_status"] = str(execution.get("status") or "")
            cycle["execution_mode"] = str(execution.get("mode") or operation)
            if isinstance(execution.get("step_ids"), list):
                cycle["step_ids"] = execution.get("step_ids")
            cycles.append(cycle)
            record_research_loop_event(
                run_dir,
                "research_loop_cycle_finished",
                {
                    "cycle": cycle["index"],
                    "operation": operation,
                    "ok": bool(execution.get("ok")),
                    "status": str(execution.get("status") or ""),
                    "step_ids": cycle.get("step_ids", []),
                },
            )
            if execution.get("ok"):
                # A cycle that executed cleanly with nothing left to revise IS the
                # finished mission. Mark it completed so the next iteration accepts
                # and stops — otherwise the run looks startable again and a fresh
                # full cycle re-invokes the model, which can wreck an already-good
                # result (a passing finalize turned into needs_revision).
                post_ok_summary = run_summary(run_dir)
                post_ok_revision = post_ok_summary.get("revision_plan_summary") if isinstance(post_ok_summary.get("revision_plan_summary"), dict) else {}
                if not post_ok_revision.get("required") and str(post_ok_summary.get("status") or "") != "completed":
                    try:
                        TaskLedger.load(run_dir / "task_ledger.json").set_status("completed")
                        record_research_loop_event(run_dir, "research_loop_cycle_succeeded", {"cycle": cycle["index"], "operation": operation})
                    except Exception:  # noqa: BLE001
                        pass
            if not execution.get("ok"):
                post_execution_summary = run_summary(run_dir)
                post_revision_summary = post_execution_summary.get("revision_plan_summary") if isinstance(post_execution_summary.get("revision_plan_summary"), dict) else {}
                worker_steps = execution.get("steps") if isinstance(execution.get("steps"), list) else []
                worker_steps_ok = bool(worker_steps) and all(isinstance(item, dict) and item.get("ok") for item in worker_steps)
                if worker_steps_ok and post_revision_summary.get("required") and post_revision_summary.get("valid"):
                    cycle["managed_blocker"] = True
                    cycle["revision_step_ids"] = post_revision_summary.get("step_ids", [])
                    continue
                stop_reason = "execution_failed"
                break
        stable_blocker_reasons = {"repeated_revision_plan", "revision_cycle_limit"}
        if stop_reason in stable_blocker_reasons:
            try:
                ledger = TaskLedger.load(run_dir / "task_ledger.json")
                existing_result = ledger.data.get("result") if isinstance(ledger.data.get("result"), dict) else {}
                blocked_result = dict(existing_result)
                blocked_result.update(
                    {
                        "ok": False,
                        "status": "blocked",
                        "summary": f"Research loop stopped on stable blocker: {stop_reason}.",
                        "research_loop_blocked": True,
                        "research_loop_stop_reason": stop_reason,
                        "research_loop_revision_cycles": revision_cycles,
                    }
                )
                ledger.set_result(blocked_result)
                ledger.set_status("blocked")
            except Exception:
                pass
        final_summary = run_summary(run_dir)
        final_view = orchestration_view_fields(final_summary, task_id=task_id)
        ok = stop_reason == "completed" or (
            str(final_summary.get("status") or "") == "completed"
            and not bool(final_summary.get("revision_plan_summary", {}).get("required"))
        )
        record_research_loop_event(
            run_dir,
            "research_loop_finished",
            {
                "ok": ok,
                "stop_reason": stop_reason,
                "cycles": len(cycles),
                "revision_cycles": revision_cycles,
                "final_status": str(final_summary.get("status") or ""),
            },
        )
        return {
            "ok": ok,
            "phase": "completed" if ok else stop_reason,
            "task_id": task_id,
            "run_mode": run_mode,
            "stop_reason": stop_reason,
            "cycles": cycles,
            "revision_cycles": revision_cycles,
            "max_revision_cycles": max_revision_cycles,
            "run_summary": final_summary,
            "decision": final_view.get("decision", {}),
            "display": final_view.get("display", {}),
            "next_action": final_view.get("next_action", {}),
            "client_action": final_view.get("client_action", {}),
        }
    finally:
        if claim_active:
            with ACTIVE_RUNS_LOCK:
                ACTIVE_RUNS.discard(task_id)


def resume_step_ids_from_run(run_dir: Path) -> list[str]:
    status, status_error = load_json_object(run_dir / "status.json", "status")
    if status_error:
        raise ValueError(f"status unavailable for resume execution: {status_error}")
    ledger, ledger_error = load_ledger_dict(run_dir / "task_ledger.json")
    if ledger_error:
        raise ValueError(f"ledger unavailable for resume execution: {ledger_error}")
    progress = run_progress(status, ledger)
    pending = [str(step_id) for step_id in progress.get("pending_step_ids", []) if step_id]
    if not pending:
        raise ValueError("run has no pending steps to resume")
    return pending


def recover_stale_runs(run_root: Path) -> list[dict[str, Any]]:
    recovered: list[dict[str, Any]] = []
    if not run_root.exists():
        return recovered
    with ACTIVE_RUNS_LOCK:
        active = set(ACTIVE_RUNS)
    for run_dir in run_root.iterdir():
        if not run_dir.is_dir() or run_dir.name.startswith("_") or run_dir.name in active:
            continue
        ledger_path = run_dir / "task_ledger.json"
        if not ledger_path.exists():
            continue
        try:
            ledger = TaskLedger.load(ledger_path)
        except Exception:  # noqa: BLE001 - corrupt runs are reported by run_summary and must not block recovery.
            recovered.append(run_summary(run_dir))
            continue
        if ledger.data.get("status") in {"running", "cancelling"}:
            ledger.set_status("interrupted")
            ledger.record_event("recovered_stale_run", {"reason": "gateway process has no active worker thread"})
            recovered.append(run_summary(run_dir))
    return recovered


def prepare_run_root(run_root: Path, recover_stale_on_start: bool = True) -> list[dict[str, Any]]:
    run_root.mkdir(parents=True, exist_ok=True)
    if not recover_stale_on_start:
        return []
    return recover_stale_runs(run_root)


def recovery_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for run in runs:
        actions = run.get("actions") if isinstance(run.get("actions"), dict) else {}
        if run.get("status") != "interrupted":
            continue
        run_dir = Path(str(run.get("run_dir") or ""))
        backend_route = execution_backend_route(run_dir)
        native_adapter = native_adapter_for_route(backend_route)
        native_backend = native_adapter is not None
        if backend_route.get("ok") and not native_backend and not actions.get("can_resume"):
            continue
        next_action = (
            backend_route.get("next_action")
            if not backend_route.get("ok") and isinstance(backend_route.get("next_action"), dict)
            else actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {}
        )
        resume_ready = False
        resume_errors: list[str] = []
        pending_step_ids = run.get("progress", {}).get("pending_step_ids", []) if isinstance(run.get("progress"), dict) else []
        if native_backend and backend_route.get("ok"):
            pending_step_ids = [native_adapter.step_id]
            resume_ready = True
            next_action = {
                "kind": f"resume_{native_adapter.route_kind}",
                "method": "POST",
                "endpoint": "POST /runs/{task_id}/start_resume_http",
                "body": {},
                "reason": f"resume the atomic {native_adapter.backend} mission",
            }
        elif not backend_route.get("ok"):
            resume_errors.append(str(backend_route.get("error") or backend_route.get("error_code") or "run cannot resume"))
        else:
            try:
                pending_step_ids = resume_step_ids_from_run(run_dir)
                resume_ready = True
            except Exception as exc:  # noqa: BLE001 - recovery listing should diagnose malformed run packages.
                resume_errors.append(str(exc))
        task_id = str(run.get("task_id") or "")
        candidates.append(
            {
                "task_id": task_id,
                "status": str(run.get("status") or ""),
                "updated_at": str(run.get("updated_at") or ""),
                "pending_step_ids": pending_step_ids,
                "resume_ready": resume_ready,
                "resume_errors": resume_errors,
                "backend_route": backend_route,
                "next_action": next_action,
                "client_action": executable_client_action(task_id, next_action),
                "display": recovery_candidate_display(run, resume_ready, resume_errors, pending_step_ids),
            }
        )
    startable = [candidate for candidate in candidates if candidate.get("resume_ready")]
    blocked = [candidate for candidate in candidates if not candidate.get("resume_ready")]
    return {
        "recoverable": len(candidates),
        "startable": len(startable),
        "blocked": len(blocked),
        "task_ids": [candidate["task_id"] for candidate in candidates if candidate.get("task_id")],
        "candidates": candidates,
    }


def start_background(task_id: str, target: Any) -> bool:
    with ACTIVE_RUNS_LOCK:
        if task_id in ACTIVE_RUNS:
            return False
        ACTIVE_RUNS.add(task_id)

    def wrapped() -> None:
        try:
            target()
        finally:
            with ACTIVE_RUNS_LOCK:
                ACTIVE_RUNS.discard(task_id)

    threading.Thread(target=wrapped, daemon=True).start()
    return True


def execute_with_ledger_failure_guard(run_dir: Path, target: Any) -> Any:
    try:
        return target()
    except Exception as exc:  # noqa: BLE001 - background failures must not leave runs stuck as running.
        ledger_path = run_dir / "task_ledger.json"
        if ledger_path.exists():
            try:
                ledger = TaskLedger.load(ledger_path)
                ledger.record_event("background_execution_failed", {"error": str(exc), "type": type(exc).__name__})
                ledger.set_result({"ok": False, "status": "failed", "summary": str(exc), "error": str(exc)})
                ledger.set_status("failed")
            except Exception:
                pass
        raise


def orchestrate_start_run(
    run_root: Path,
    task_id: str,
    run_mode: str = "http",
    host: str = "127.0.0.1",
    timeout_sec: int = 1800,
    force: bool = False,
    revision_token: str = "",
) -> dict[str, Any]:
    if run_mode not in {"local", "http"}:
        raise ValueError("run_mode must be local or http")
    host = validate_service_host(host)
    run_dir = run_root / task_id
    if not run_dir.exists():
        return {"ok": False, "phase": "missing_run", "task_id": task_id, "error": "run not found"}
    backend_route = execution_backend_route(run_dir)
    if not backend_route.get("ok"):
        return backend_route
    task_memory_guard = task_memory_start_guard(run_dir)
    if not task_memory_guard.get("ok"):
        return {
            "ok": False,
            "retryable": bool(task_memory_guard.get("retryable")),
            "phase": "task_memory_retry",
            "task_id": task_id,
            "error_code": str(
                task_memory_guard.get("error_code") or "task_memory_unavailable"
            ),
            "error": str(
                task_memory_guard.get("warning")
                or "task memory is not ready for execution"
            ),
            "task_memory": task_memory_guard,
            "next_action": {},
            "client_action": {},
        }
    timeout_sec = _execution_timeout_for_run(run_dir, timeout_sec)
    summary = run_summary(run_dir)
    actions = summary.get("actions") if isinstance(summary.get("actions"), dict) else {}
    next_action = actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {}
    execution_mode = "full"
    step_ids: list[str] | None = None
    native_backend = native_adapter_for_route(backend_route) is not None
    native_revision_requested = native_backend and (
        str(summary.get("status") or "") in {"revision", "needs_revision"}
        or bool(actions.get("can_start_revision"))
    )
    if native_revision_requested:
        action_body = (
            next_action.get("body")
            if isinstance(next_action.get("body"), dict)
            else {}
        )
        expected_token = str(action_body.get("revision_token") or "")
        provided_token = str(revision_token or "")
        if (
            not expected_token
            or not provided_token
            or not secrets.compare_digest(provided_token, expected_token)
        ):
            return {
                "ok": False,
                "phase": "native_revision_token_required",
                "error_code": "stale_or_missing_revision_token",
                "error": (
                    "native revision was not started: generic orchestration cannot "
                    "bypass the current mission's revision token"
                ),
                "remediation": (
                    "execute the current run summary's published revision action unchanged"
                ),
                "task_id": task_id,
                "next_action": next_action,
                "client_action": executable_client_action(task_id, next_action),
                "snapshot": run_snapshot(run_dir, event_limit=5, events_after=0),
            }
    if native_backend:
        start_preflight = _native_preflight(
            run_dir,
            backend_route,
            mode=run_mode,
            timeout_sec=timeout_sec,
            force=force,
        )
        start_actions = (
            start_preflight.get("actions")
            if isinstance(start_preflight.get("actions"), dict)
            else {}
        )
        if not start_preflight.get("ok") or start_actions.get("can_start_run") is not True:
            blocked_action = (
                start_actions.get("next_action")
                if isinstance(start_actions.get("next_action"), dict)
                else {}
            )
            return {
                "ok": False,
                "phase": "native_preflight",
                "error_code": "native_preflight_failed",
                "error": "native run is not startable under its current durable state",
                "task_id": task_id,
                "backend_route": backend_route,
                "run_preflight": start_preflight,
                "next_action": blocked_action,
                "client_action": executable_client_action(task_id, blocked_action),
                "snapshot": run_snapshot(run_dir, event_limit=5, events_after=0),
            }
    ledger_data, ledger_error = load_ledger_dict(run_dir / "task_ledger.json")
    native_status = str(ledger_data.get("status") or "") if not ledger_error else ""
    if native_backend and (
        native_status in {"revision", "needs_revision"}
        or actions.get("can_start_revision")
    ):
        operation = "revision"
        execution_mode = "revision"
    elif native_backend and native_status in {"created", "assigned"}:
        operation = "start"
    elif native_backend and native_status == "interrupted":
        operation = "resume"
        execution_mode = "resume"
    elif actions.get("can_start"):
        operation = "start"
    elif actions.get("can_resume"):
        operation = "resume"
        execution_mode = "resume"
        if native_adapter_for_route(backend_route) is None:
            step_ids = resume_step_ids_from_run(run_dir)
    elif actions.get("can_start_revision"):
        operation = "revision"
        execution_mode = "revision"
        if native_adapter_for_route(backend_route) is None:
            step_ids = revision_step_ids_from_run(run_dir)
    elif not native_backend and force and actions.get("force_required_for_rerun"):
        operation = "force_rerun"
    else:
        return {
            "ok": False,
            "phase": "not_startable",
            "task_id": task_id,
            "summary": summary,
            "next_action": next_action,
            "client_action": executable_client_action(task_id, next_action),
            "snapshot": run_snapshot(run_dir, event_limit=5, events_after=0),
        }

    workspace_root = resolve_run_child_path(run_dir, "", "work")
    executor = lambda: execute_with_ledger_failure_guard(
        run_dir,
        lambda: execute_routed_run(
            run_dir,
            run_mode=run_mode,
            host=host,
            timeout_sec=timeout_sec,
            workspace_root=workspace_root if run_mode == "local" else None,
            step_ids=step_ids,
            execution_mode=execution_mode,
        ),
    )
    ledger_path = run_dir / "task_ledger.json"
    if ledger_path.exists():
        ledger = TaskLedger.load(ledger_path)
        if operation == "resume":
            ledger.record_event("resume_execution_requested", {"mode": f"orchestrate_start_{run_mode}", "step_ids": step_ids or []})
        event_payload: dict[str, Any] = {
            "mode": f"orchestrate_start_{run_mode}",
            "operation": operation,
            "backend": str(backend_route.get("backend") or ""),
        }
        if step_ids:
            event_payload["step_ids"] = step_ids
        if force:
            event_payload["force"] = True
        ledger.record_event("background_start_requested", event_payload)
    if not start_background(task_id, executor):
        return {
            "ok": False,
            "phase": "already_active",
            "task_id": task_id,
            "error": "run already active",
            "snapshot": run_snapshot(run_dir, event_limit=5, events_after=0),
        }
    poll_action = {"kind": "poll", "method": "GET", "endpoint": "GET /runs/{task_id}/snapshot", "body": {"events_after": 0}, "reason": "run started in background"}
    return {
        "ok": True,
        "phase": "started",
        "task_id": task_id,
        "run_mode": run_mode,
        "operation": operation,
        "backend_route": backend_route,
        "step_ids": step_ids or [],
        "next_action": poll_action,
        "client_action": executable_client_action(task_id, poll_action),
        "snapshot": run_snapshot(run_dir, event_limit=5, events_after=0),
    }


def start_recoverable_runs(run_root: Path, mode: str, host: str = "127.0.0.1", timeout_sec: int = 1800) -> dict[str, Any]:
    if mode not in {"local", "http"}:
        raise ValueError("mode must be local or http")
    host = validate_service_host(host)
    timeout_sec = max(1, min(int(timeout_sec), MAX_RESEARCH_WARBAND_TIMEOUT_SEC))
    all_runs = list_runs(run_root)
    candidates = recovery_summary(all_runs).get("candidates", [])
    results: list[dict[str, Any]] = []
    started_count = 0
    poll_action = {"kind": "poll", "method": "GET", "endpoint": "GET /runs/{task_id}/snapshot", "body": {"events_after": 0}, "reason": "run started in background"}
    inspect_action = {"kind": "inspect_package", "method": "GET", "endpoint": "GET /runs/{task_id}/package", "body": {}, "reason": "run could not be resumed automatically"}
    for candidate in candidates:
        task_id = str(candidate.get("task_id") or "")
        if not task_id:
            continue
        run_dir = run_root / task_id
        run_timeout_sec = _execution_timeout_for_run(run_dir, timeout_sec)
        ledger_path = run_dir / "task_ledger.json"
        try:
            backend_route = execution_backend_route(run_dir)
            if not backend_route.get("ok"):
                results.append(backend_route)
                continue
            if native_adapter_for_route(backend_route) is not None:
                started = orchestrate_start_run(
                    run_root,
                    task_id,
                    run_mode=mode,
                    host=host,
                    timeout_sec=run_timeout_sec,
                )
                if started.get("ok"):
                    started_count += 1
                results.append(
                    {
                        **started,
                        "status": "started" if started.get("ok") else "skipped",
                    }
                )
                continue
            step_ids = (
                resume_step_ids_from_run(run_dir)
            )
            if ledger_path.exists():
                ledger = TaskLedger.load(ledger_path)
                ledger.record_event("resume_execution_requested", {"mode": f"bulk_start_resume_{mode}"})
                ledger.record_event(
                    "background_start_requested",
                    {
                        "mode": f"bulk_start_resume_{mode}",
                        "step_ids": step_ids,
                        "backend": str(backend_route.get("backend") or ""),
                    },
                )
            workspace_root = resolve_run_child_path(run_dir, "", "work")
            executor = lambda run_dir=run_dir, workspace_root=workspace_root, step_ids=step_ids: execute_with_ledger_failure_guard(
                run_dir,
                lambda: execute_routed_run(
                    run_dir,
                    run_mode=mode,
                    host=host,
                    timeout_sec=run_timeout_sec,
                    workspace_root=workspace_root if mode == "local" else None,
                    step_ids=step_ids or None,
                    execution_mode="resume",
                ),
            )
            if not start_background(task_id, executor):
                already_active_action = {"kind": "poll", "method": "GET", "endpoint": "GET /runs/{task_id}/snapshot", "body": {"events_after": 0}, "reason": "run is already active"}
                results.append(
                    {
                        "task_id": task_id,
                        "ok": False,
                        "status": "already_active",
                        "next_action": already_active_action,
                        "client_action": executable_client_action(task_id, already_active_action),
                    }
                )
                continue
            started_count += 1
            results.append(
                {
                    "task_id": task_id,
                    "ok": True,
                    "status": "started",
                    "backend_route": backend_route,
                    "step_ids": step_ids,
                    "next_action": poll_action,
                    "client_action": executable_client_action(task_id, poll_action),
                }
            )
        except Exception as exc:  # noqa: BLE001 - one malformed recoverable run must not block the queue.
            results.append(
                {
                    "task_id": task_id,
                    "ok": False,
                    "status": "skipped",
                    "error": str(exc),
                    "next_action": inspect_action,
                    "client_action": executable_client_action(task_id, inspect_action),
                }
            )
    return {
        "ok": True,
        "mode": mode,
        "started": started_count,
        "total_candidates": len(candidates),
        "results": results,
    }


def cancel_http_worker_tasks(run_dir: Path, host: str = "127.0.0.1", timeout_sec: float = 1.0) -> list[dict[str, Any]]:
    host = validate_service_host(host)
    dispatch_dir = run_dir / "dispatch"
    if not dispatch_dir.exists():
        return []
    results: list[dict[str, Any]] = []
    for dispatch_path in sorted(dispatch_dir.glob("*.json")):
        try:
            packet = json.loads(dispatch_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            results.append({"dispatch": str(dispatch_path), "ok": False, "error": str(exc)})
            continue
        if not isinstance(packet, dict):
            results.append({"dispatch": str(dispatch_path), "ok": False, "error": "dispatch packet is not an object"})
            continue
        request_payload = packet.get("request") if isinstance(packet.get("request"), dict) else {}
        task_id = str(request_payload.get("task_id") or packet.get("task_id") or "")
        worker = str(packet.get("worker") or "")
        port = int(packet.get("port") or 0)
        if not task_id or not port:
            results.append({"worker": worker, "port": port, "task_id": task_id, "ok": False, "error": "missing task_id or port"})
            continue
        url = f"http://{host}:{port}/tasks/{quote(task_id, safe='')}/cancel"
        try:
            data = json.dumps({}).encode("utf-8")
            request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                payload = json.loads(response.read().decode("utf-8"))
            results.append({"worker": worker, "port": port, "task_id": task_id, "ok": bool(isinstance(payload, dict) and payload.get("ok")), "response": payload})
        except Exception as exc:  # noqa: BLE001 - cancellation fan-out is best-effort.
            results.append({"worker": worker, "port": port, "task_id": task_id, "ok": False, "error": str(exc)})
    return results
