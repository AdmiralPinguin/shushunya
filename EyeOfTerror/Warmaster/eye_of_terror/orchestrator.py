"""Run lifecycle engine: execution preflight, orchestration, the research
loop, interrupted-run recovery, and background execution start. This is
the controller Warmaster runs above the governors; the HTTP gateway wires
these into endpoints."""
from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .actions import run_preflight_actions
from .gateway_util import resolve_run_child_path, valid_task_id, validate_service_host
from .http_executor import execute_run as execute_http_run, preflight_workers as preflight_http_workers
from .ledger import TaskLedger
from .local_executor import WORKER_COMMANDS, execute_run as execute_local_run, input_artifact_errors, ordered_dispatch_paths
from .mission_control import (
    link_run_to_mission,
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

MAX_STANDARD_EXECUTION_TIMEOUT_SEC = 7_200
MAX_RESEARCH_WARBAND_TIMEOUT_SEC = 604_800


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
    suffix = 2
    while (run_dir.parent / fresh_task_id).exists():
        suffix_text = f"-native-{suffix}"
        fresh_task_id = f"{stem[:127 - len(suffix_text)].rstrip('.-_')}{suffix_text}"
        suffix += 1
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
            "reuse_existing": False,
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
    suffix = 2
    while (run_dir.parent / fresh_task_id).exists():
        suffix_text = f"-native-{suffix}"
        fresh_task_id = f"{stem[:127 - len(suffix_text)].rstrip('.-_')}{suffix_text}"
        suffix += 1
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
            "reuse_existing": False,
        },
        "reason": (
            "native terminal evidence is immutable; create a fresh "
            f"{adapter.governor} mission"
        ),
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
        action = _native_reprepare_action(
            run_dir, raw_contract, NATIVE_RESEARCH_ADAPTER,
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
        action = _reprepare_action(run_dir, raw_contract)
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
            terminal_action = _native_reprepare_action(run_dir, contract, adapter)
            if adapter is NATIVE_CODE_ADAPTER:
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
    existing_run = run_root / task_id if task_id else None
    if existing_run is not None and existing_run.is_dir():
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
                "trace": [{
                    "stage": "existing_run",
                    "ok": bool(started.get("ok")),
                    "task_id": task_id,
                    "next_action": started.get("next_action", {}),
                }],
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
            "trace": [{"stage": "existing_run", "ok": True, "task_id": task_id}],
            "orchestration": state,
            "decision": decision,
            "display": state.get("display", {}),
            "display_events": state.get("display_events", []),
            "next_action": next_action,
            "client_action": state.get("client_action", {}),
        }
    warmaster_root = Path(__file__).resolve().parents[1]
    mission = open_mission(warmaster_root, message, task_id, source_channel="main_chat")
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
        current_status = str(run_summary(run_dir).get("status") or "")
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
            result = run_via_skitarii(run_dir, run_dir.name, timeout_sec=timeout_sec)
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
                    run_dir, run_dir.name, timeout_sec=timeout_sec,
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
) -> dict[str, Any]:
    if run_mode not in {"local", "http"}:
        raise ValueError("run_mode must be local or http")
    host = validate_service_host(host)
    run_dir = run_root / task_id
    if not run_dir.exists():
        return {"ok": False, "phase": "missing_run", "task_id": task_id, "error": "run not found"}
    timeout_sec = _execution_timeout_for_run(run_dir, timeout_sec)
    backend_route = execution_backend_route(run_dir)
    if not backend_route.get("ok"):
        return backend_route
    summary = run_summary(run_dir)
    actions = summary.get("actions") if isinstance(summary.get("actions"), dict) else {}
    next_action = actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {}
    execution_mode = "full"
    step_ids: list[str] | None = None
    native_backend = native_adapter_for_route(backend_route) is not None
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
    if native_backend and native_status in {"created", "assigned"}:
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
