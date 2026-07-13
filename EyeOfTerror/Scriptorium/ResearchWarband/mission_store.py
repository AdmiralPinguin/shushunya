"""Crash-visible asynchronous lifecycle storage for ResearchWarband.

The lifecycle store is intentionally independent from Skitarii.  A mission owns
an immutable request payload, an append-only logical event history, every exact
pipeline result produced by its attempts, and a small atomic metadata record.
The metadata file is committed last.  A torn event/result write is therefore
detected during recovery and represented as ``blocked`` instead of being
mistaken for completed work.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import threading
import time
from typing import Any
import uuid


SCHEMA_VERSION = 2
MAX_CLARIFICATION_TURNS = 16
MAX_CLARIFICATION_FIELD_BYTES = 8_000
MAX_CLARIFICATION_TOTAL_BYTES = 16_000
MISSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
WORK_STATUSES = frozenset({"queued", "running", "cancelling"})
ACTIVE_STATUSES = frozenset({*WORK_STATUSES, "needs_user"})
TERMINAL_STATUSES = frozenset(
    {"done", "needs_revision", "blocked", "failed", "cancelled"}
)
ALL_STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES
OUTCOME_STATUS = {
    "accepted": "done",
    "accepted_with_uncertainty": "done",
    "clarify": "needs_user",
    "needs_revision": "needs_revision",
    "blocked": "blocked",
    "failed": "failed",
}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _json_bytes(value: Any, *, canonical: bool = False) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=canonical,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _json_object(value: Any, context: str) -> tuple[dict[str, Any], bytes]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{context} must be a JSON object")
    try:
        raw = _json_bytes(dict(value))
        restored = json.loads(raw.decode("utf-8"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{context} must contain finite JSON data") from exc
    if not isinstance(restored, dict):
        raise TypeError(f"{context} must be a JSON object")
    return restored, raw


def request_sha256(payload: Mapping[str, Any]) -> str:
    """Return the stable idempotency hash for the complete request object."""
    restored, _raw = _json_object(payload, "mission payload")
    return hashlib.sha256(_json_bytes(restored, canonical=True)).hexdigest()


def valid_mission_id(value: Any) -> bool:
    return type(value) is str and bool(MISSION_ID_RE.fullmatch(value)) and value not in {".", ".."}


def _error_text(error: BaseException | str, limit: int = 4096) -> str:
    text = str(error)
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= limit:
        return text
    return raw[: max(0, limit - 3)].decode("utf-8", errors="ignore") + "..."


class MissionStoreError(RuntimeError):
    pass


class MissionExistsError(MissionStoreError):
    pass


class MissionConflictError(MissionStoreError):
    pass


class MissionCapacityError(MissionStoreError):
    pass


class MissionPersistenceError(MissionStoreError):
    pass


class PayloadTooLargeError(MissionStoreError, ValueError):
    pass


class ResultTooLargeError(MissionStoreError, ValueError):
    pass


class EventLimitError(MissionStoreError, ValueError):
    pass


class UnsafeStoreError(MissionStoreError, ValueError):
    pass


try:
    from .process_supervisor import (
        AttemptContext,
        MAX_REVISION_TURNS,
        ProcessSupervisor,
        RunnerCleanupError,
        RunnerReadinessError,
        RunnerSpec,
        attest_runner,
        read_runtime_readiness,
        verify_linux_cgroup_delegation,
        verify_runtime_readiness,
        validate_revision_turns,
    )
    from .deployment_guard import DeploymentGuard, DeploymentIntegrityError
except ImportError:
    from process_supervisor import (  # type: ignore[no-redef]
        AttemptContext,
        MAX_REVISION_TURNS,
        ProcessSupervisor,
        RunnerCleanupError,
        RunnerReadinessError,
        RunnerSpec,
        attest_runner,
        read_runtime_readiness,
        verify_linux_cgroup_delegation,
        verify_runtime_readiness,
        validate_revision_turns,
    )
    from deployment_guard import (  # type: ignore[no-redef]
        DeploymentGuard,
        DeploymentIntegrityError,
    )


def _revision_turn_for_result(
    attempt: int,
    result: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Extract one safe internal retry instruction from an exact attempt result."""

    audit = result.get("pipeline_audit")
    source = audit if isinstance(audit, Mapping) else result
    findings = source.get("review_findings")
    reason = result.get("reason")
    if type(findings) is not list or not findings or type(reason) is not str:
        return None
    try:
        turn = validate_revision_turns(
            (
                {
                    "attempt": attempt,
                    "result_sha256": hashlib.sha256(
                        _json_bytes(dict(result))
                    ).hexdigest(),
                    "reason": _error_text(reason.strip(), 4096),
                    "findings": findings,
                },
            )
        )[0]
    except (TypeError, ValueError, UnicodeError):
        return None
    if not any(item["retryable"] for item in turn["findings"]):
        return None
    return turn


def _terminal_failure_result(
    result: Mapping[str, Any],
    reason: str,
    **metadata: Any,
) -> dict[str, Any]:
    """Convert a runner result into one coherent terminal failure contract.

    Production results carry a second, evaluator-facing status envelope.  A
    lifecycle-only conversion would otherwise leave that envelope claiming
    ``needs_revision`` while the mission store reports ``failed``.  Preserve
    immutable identities and evidence, but never publish an unaccepted draft
    as a terminal answer.
    """

    failed = {**dict(result), **metadata, "outcome": "failed", "reason": reason}
    if "answer" in failed:
        failed["answer"] = ""
    external = failed.get("external_evaluator_result")
    if isinstance(external, Mapping):
        failed["external_evaluator_result"] = {
            **dict(external),
            "status": "failed",
            "accepted": False,
            "final_text": "",
            "question": "",
        }
    return failed


def _pipeline_contract_failure_result(
    result: Mapping[str, Any],
    *,
    code: str,
    what_failed: str,
    evidence: str,
    expected: str,
    remediation: str,
) -> dict[str, Any]:
    """Turn an invalid runner result into an explained, clean terminal failure."""

    failed = _terminal_failure_result(result, what_failed, runner_contract_error=True)
    audit = failed.get("pipeline_audit")
    preserved_audit = dict(audit) if isinstance(audit, Mapping) else {}
    diagnostics = preserved_audit.get("diagnostics")
    safe_diagnostics = (
        [str(item)[:2_000] for item in diagnostics if str(item).strip()]
        if isinstance(diagnostics, list)
        else []
    )
    failed["pipeline_audit"] = {
        **preserved_audit,
        "review_findings": [{
            "code": code,
            "entity_kind": "runner_contract",
            "entity_id": "isolated-research-runner",
            "what_failed": what_failed,
            "evidence": evidence,
            "expected": expected,
            "remediation": remediation,
            "revision_owner": "infrastructure",
            "retryable": True,
        }],
        "diagnostics": [*safe_diagnostics, evidence][:20],
    }
    return failed


class Mission:
    """One in-memory view of an exactly persisted mission."""

    def __init__(
        self,
        mission_id: str,
        payload: dict[str, Any],
        request_hash: str,
        payload_bytes: bytes | None = None,
    ) -> None:
        self.id = mission_id
        self.payload = payload
        self.payload_bytes = bytes(
            payload_bytes if payload_bytes is not None else _json_bytes(payload, canonical=True)
        )
        self.request_sha256 = request_hash
        self.status = "queued"
        self.created = time.time()
        self.updated = self.created
        self.attempt = 0
        self.question: str | None = None
        self.clarification_turns: list[dict[str, str]] = []
        self.revision_turns: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.result: dict[str, Any] | None = None
        self.result_refs: list[dict[str, Any]] = []
        self.storage_error: str | None = None
        self.inflight = False
        self.cleanup_complete = True
        self.adopted_count = 0
        self.cancelled = threading.Event()
        self._thread: threading.Thread | None = None
        self._worker_token: str | None = None
        self._resume_disabled = False
        self._adopted_in_process = False
        self._lock = threading.RLock()

    def snapshot(self, *, event_limit: int = 0) -> dict[str, Any]:
        with self._lock:
            events = self.events[-event_limit:] if event_limit else list(self.events)
            return {
                "id": self.id,
                "request_sha256": self.request_sha256,
                "status": self.status,
                "attempt": self.attempt,
                "question": self.question,
                "answer_count": len(self.clarification_turns),
                "clarification_turn_count": len(self.clarification_turns),
                "revision_turn_count": len(self.revision_turns),
                "result": self.result,
                "inflight": self.inflight,
                "cleanup_complete": self.cleanup_complete,
                "storage_error": self.storage_error,
                "created": self.created,
                "updated": self.updated,
                "events": events,
            }

    def events_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.events)


Runner = RunnerSpec | str | Callable[[dict[str, Any], AttemptContext], Any]


class MissionStore:
    """Bounded durable store plus cooperative background-worker lifecycle."""

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        max_active: int | None = None,
        max_missions: int | None = None,
        max_store_bytes: int | None = None,
        max_payload_bytes: int | None = None,
        max_result_bytes: int | None = None,
        max_events_bytes: int | None = None,
        max_event_bytes: int | None = None,
        max_state_bytes: int | None = None,
        max_attempts: int | None = None,
        max_auto_revisions: int | None = None,
        max_question_bytes: int | None = None,
        max_answer_bytes: int | None = None,
        max_terminal: int | None = None,
        terminal_ttl_seconds: float | None = None,
        attempt_timeout_seconds: float | None = None,
        cancel_grace_seconds: float | None = None,
        terminate_grace_seconds: float | None = None,
    ) -> None:
        default_root = Path(__file__).resolve().parent / "runtime" / "missions"
        self.root = Path(root or os.environ.get("RESEARCH_WARBAND_MISSION_ROOT", default_root))
        self.max_active = max_active or _env_int("RESEARCH_WARBAND_MAX_ACTIVE", 4)
        self.max_missions = max_missions or _env_int("RESEARCH_WARBAND_MAX_MISSIONS", 256)
        self.max_store_bytes = max_store_bytes or _env_int(
            "RESEARCH_WARBAND_STORE_MAX_BYTES", 4_000_000_000, 4096
        )
        self.max_payload_bytes = max_payload_bytes or _env_int(
            "RESEARCH_WARBAND_PAYLOAD_MAX_BYTES", 2_000_000, 1024
        )
        self.max_result_bytes = max_result_bytes or _env_int(
            "RESEARCH_WARBAND_RESULT_MAX_BYTES", 64_000_000, 1024
        )
        self.max_events_bytes = max_events_bytes or _env_int(
            "RESEARCH_WARBAND_EVENTS_MAX_BYTES", 4_000_000, 1024
        )
        self.max_event_bytes = max_event_bytes or _env_int(
            "RESEARCH_WARBAND_EVENT_MAX_BYTES", 256_000, 128
        )
        self.max_state_bytes = max_state_bytes or _env_int(
            "RESEARCH_WARBAND_STATE_MAX_BYTES", 1_000_000, 4096
        )
        self.max_attempts = max_attempts or _env_int(
            "RESEARCH_WARBAND_MAX_ATTEMPTS", 16
        )
        self.max_auto_revisions = min(
            (
                max_auto_revisions
                if max_auto_revisions is not None
                else _env_int("RESEARCH_WARBAND_MAX_AUTO_REVISIONS", 2, 0)
            ),
            MAX_REVISION_TURNS,
        )
        self.max_question_bytes = min(
            max_question_bytes or _env_int(
                "RESEARCH_WARBAND_QUESTION_MAX_BYTES", MAX_CLARIFICATION_FIELD_BYTES, 1
            ),
            MAX_CLARIFICATION_FIELD_BYTES,
        )
        self.max_answer_bytes = min(
            max_answer_bytes or _env_int(
                "RESEARCH_WARBAND_MAX_ANSWER_BYTES", MAX_CLARIFICATION_FIELD_BYTES, 1
            ),
            MAX_CLARIFICATION_FIELD_BYTES,
        )
        self.max_terminal = (
            max_terminal
            if max_terminal is not None
            else _env_int("RESEARCH_WARBAND_TERMINAL_MAX_COUNT", 128, 0)
        )
        self.terminal_ttl_seconds = (
            terminal_ttl_seconds
            if terminal_ttl_seconds is not None
            else _env_float(
                "RESEARCH_WARBAND_TERMINAL_TTL_SECONDS", 7 * 24 * 3600
            )
        )
        self.attempt_timeout_seconds = (
            float(attempt_timeout_seconds)
            if attempt_timeout_seconds is not None
            else _env_float("RESEARCH_WARBAND_ATTEMPT_TIMEOUT_SECONDS", 6 * 3600, 1.0)
        )
        self.cancel_grace_seconds = (
            float(cancel_grace_seconds)
            if cancel_grace_seconds is not None
            else _env_float("RESEARCH_WARBAND_CANCEL_GRACE_SECONDS", 5.0, 0.0)
        )
        self.terminate_grace_seconds = (
            float(terminate_grace_seconds)
            if terminate_grace_seconds is not None
            else _env_float("RESEARCH_WARBAND_TERMINATE_GRACE_SECONDS", 5.0, 0.0)
        )
        if self.attempt_timeout_seconds <= 0:
            raise ValueError("attempt_timeout_seconds must be positive")
        if self.max_auto_revisions < 0:
            raise ValueError("max_auto_revisions must not be negative")
        if self.cancel_grace_seconds < 0 or self.terminate_grace_seconds < 0:
            raise ValueError("runner grace periods must not be negative")
        self.instance_id = uuid.uuid4().hex
        self.missions: dict[str, Mission] = {}
        self.recovery_errors: list[str] = []
        self.recovered_count = 0
        self._lock = threading.RLock()
        self._runner: RunnerSpec | None = None
        self._readiness_spec: RunnerSpec | None = None
        self._require_readiness_attestation = False
        self._require_linux_cgroup = False
        self._deployment_guard: DeploymentGuard | None = None
        self._worker_registry: dict[str, threading.Thread] = {}
        self._lease_handle: Any | None = None
        self._ensure_root()
        self.root = self.root.resolve()
        self.store_id = self._load_or_create_store_id()
        self._rehydrate()

    # -- secure filesystem primitives -------------------------------------------------

    @staticmethod
    def _is_regular(path: Path) -> bool:
        try:
            mode = path.lstat().st_mode
        except OSError:
            return False
        return stat.S_ISREG(mode) and not stat.S_ISLNK(mode)

    @staticmethod
    def _is_directory(path: Path) -> bool:
        try:
            mode = path.lstat().st_mode
        except OSError:
            return False
        return stat.S_ISDIR(mode) and not stat.S_ISLNK(mode)

    def _ensure_root(self) -> Path:
        if self.root.is_symlink():
            raise UnsafeStoreError("mission store root must not be a symlink")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self._is_directory(self.root):
            raise UnsafeStoreError("mission store root must be a real directory")
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        return self.root.resolve()

    def _mission_dir(self, mission_id: str, *, existing: bool = False) -> Path:
        if not valid_mission_id(mission_id):
            raise ValueError("invalid mission_id")
        root = self._ensure_root()
        raw = self.root / mission_id
        if raw.is_symlink():
            raise UnsafeStoreError("mission directory must not be a symlink")
        resolved = raw.resolve(strict=existing)
        if resolved.parent != root:
            raise UnsafeStoreError("mission path escapes store root")
        if existing and not self._is_directory(raw):
            raise UnsafeStoreError("mission path is not a real directory")
        return raw

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _atomic_write(self, target: Path, raw: bytes) -> None:
        if target.exists() or target.is_symlink():
            if not self._is_regular(target):
                raise UnsafeStoreError(f"unsafe persistence target: {target.name}")
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            try:
                target.chmod(0o600)
            except OSError:
                pass
            self._fsync_dir(target.parent)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _read_bounded(self, path: Path, limit: int) -> bytes:
        if path.is_symlink():
            raise UnsafeStoreError(f"{path.name} must not be a symlink")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise UnsafeStoreError(f"{path.name} cannot be safely opened: {exc}") from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise UnsafeStoreError(f"{path.name} is not a real regular file")
            if info.st_size > limit:
                raise MissionPersistenceError(f"{path.name} exceeds {limit} bytes")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                raw = handle.read(limit + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(raw) > limit:
            raise MissionPersistenceError(f"{path.name} exceeds {limit} bytes")
        return raw

    def acquire_service_lease(self) -> None:
        """Hold an OS-released singleton lease for startup adoption safety."""
        with self._lock:
            if self._lease_handle is not None:
                return
            path = self.root / ".service.lock"
            if path.is_symlink():
                raise UnsafeStoreError("service lease must not be a symlink")
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                os.close(descriptor)
                raise UnsafeStoreError("service lease must be a regular file")
            handle = os.fdopen(descriptor, "r+b", buffering=0)
            try:
                if os.name == "nt":
                    import msvcrt

                    if path.stat().st_size < 1:
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (OSError, ImportError) as exc:
                handle.close()
                raise MissionStoreError(
                    "another ResearchWarband service owns the mission store"
                ) from exc
            self._lease_handle = handle

    def release_service_lease(self) -> bool:
        """Release only when no in-process mission could still mutate the store."""
        with self._lock:
            if self._lease_handle is None:
                return True
            if self.active_worker_count() != 0:
                return False
            handle = self._lease_handle
            self._lease_handle = None
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
            return True

    def _tree_bytes(self) -> int:
        total = 0
        stack = [self.root]
        while stack:
            directory = stack.pop()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        info = entry.stat(follow_symlinks=False)
                        total += int(info.st_size)
                        if total > self.max_store_bytes:
                            return total
                        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
                            stack.append(Path(entry.path))
            except OSError:
                continue
        return total

    def _load_or_create_store_id(self) -> str:
        path = self.root / ".store.json"
        if path.exists() or path.is_symlink():
            try:
                value = json.loads(self._read_bounded(path, 4096))
                store_id = value.get("store_id") if isinstance(value, dict) else None
                if type(store_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", store_id):
                    raise ValueError("invalid store id")
                return store_id
            except Exception as exc:
                raise UnsafeStoreError(f"unsafe store identity: {exc}") from exc
        store_id = uuid.uuid4().hex
        self._atomic_write(path, _json_bytes({"schema_version": 1, "store_id": store_id}))
        return store_id

    # -- serialization ----------------------------------------------------------------

    def _event_bytes(self, events: list[dict[str, Any]]) -> bytes:
        for event in events:
            encoded = _json_bytes(event)
            if len(encoded) > self.max_event_bytes:
                raise EventLimitError(f"event exceeds {self.max_event_bytes} bytes")
        raw = _json_bytes(events)
        if len(raw) > self.max_events_bytes:
            raise EventLimitError(f"event history exceeds {self.max_events_bytes} bytes")
        return raw

    def _state(self, mission: Mission, event_raw: bytes) -> dict[str, Any]:
        payload_raw = mission.payload_bytes
        result_paths = [str(item["path"]) for item in mission.result_refs]
        state = {
            "schema_version": SCHEMA_VERSION,
            "id": mission.id,
            "request_sha256": mission.request_sha256,
            "payload_ref": {
                "path": "payload.json",
                "size_bytes": len(payload_raw),
                "sha256": hashlib.sha256(payload_raw).hexdigest(),
            },
            "events_ref": {
                "path": "events.json",
                "count": len(mission.events),
                "size_bytes": len(event_raw),
                "sha256": hashlib.sha256(event_raw).hexdigest(),
            },
            "result_refs": mission.result_refs,
            "current_result": result_paths[-1] if result_paths else None,
            "status": mission.status,
            "created": mission.created,
            "updated": mission.updated,
            "attempt": mission.attempt,
            "question": mission.question,
            "clarification_turns": [dict(turn) for turn in mission.clarification_turns],
            "revision_turns": [dict(turn) for turn in mission.revision_turns],
            "storage_error": mission.storage_error,
            "inflight": mission.inflight,
            "cleanup_complete": mission.cleanup_complete,
            "adopted_count": mission.adopted_count,
        }
        raw = _json_bytes(state)
        if len(raw) > self.max_state_bytes:
            raise MissionPersistenceError(f"mission metadata exceeds {self.max_state_bytes} bytes")
        return state

    def _persist(self, mission: Mission, *, new_result: dict[str, Any] | None = None) -> None:
        directory = self._mission_dir(mission.id, existing=True)
        with mission._lock:
            appended_ref: dict[str, Any] | None = None
            if new_result is not None:
                result, result_raw = _json_object(new_result, "pipeline result")
                if len(result_raw) > self.max_result_bytes:
                    raise ResultTooLargeError(
                        f"pipeline result exceeds {self.max_result_bytes} bytes"
                    )
                name = f"result-{mission.attempt:06d}.json"
                if any(item.get("path") == name for item in mission.result_refs):
                    raise MissionPersistenceError("attempt result path is already committed")
                self._atomic_write(directory / name, result_raw)
                appended_ref = {
                    "path": name,
                    "size_bytes": len(result_raw),
                    "sha256": hashlib.sha256(result_raw).hexdigest(),
                }
                mission.result_refs.append(appended_ref)
                mission.result = result
            try:
                event_raw = self._event_bytes(mission.events)
                state = self._state(mission, event_raw)
                state_raw = _json_bytes(state)
                projected = self._tree_bytes() + len(event_raw) + len(state_raw)
                if projected > self.max_store_bytes:
                    raise MissionCapacityError("mission store byte capacity is exhausted")
                # Journal first, metadata last. Recovery detects an uncommitted journal.
                self._atomic_write(directory / "events.json", event_raw)
                self._atomic_write(directory / "mission.json", state_raw)
            except Exception:
                if appended_ref is not None:
                    # Keep the orphan on disk: recovery will fail closed instead of
                    # re-running a pipeline whose terminal write may have happened.
                    mission.result_refs.pop()
                    mission.result = None
                raise

    def _append_event(self, mission: Mission, event_type: str, data: Mapping[str, Any] | None = None) -> None:
        event = {
            "seq": len(mission.events) + 1,
            "at": time.time(),
            "type": str(event_type),
            **dict(data or {}),
        }
        # Validate before exposing the event in memory.
        if len(_json_bytes(event)) > self.max_event_bytes:
            raise EventLimitError(f"event exceeds {self.max_event_bytes} bytes")
        mission.events.append(event)
        mission.updated = time.time()

    def _transition(
        self,
        mission: Mission,
        status: str,
        event_type: str,
        data: Mapping[str, Any] | None = None,
        *,
        new_result: dict[str, Any] | None = None,
    ) -> None:
        if status not in ALL_STATUSES:
            raise ValueError(f"unsupported mission status: {status}")
        with mission._lock:
            previous = (
                mission.status,
                mission.updated,
                mission.question,
                mission.storage_error,
                len(mission.events),
                mission.result,
                list(mission.result_refs),
                list(mission.revision_turns),
            )
            mission.status = status
            self._append_event(mission, event_type, {"status": status, **dict(data or {})})
            try:
                self._persist(mission, new_result=new_result)
            except Exception:
                (
                    mission.status,
                    mission.updated,
                    mission.question,
                    mission.storage_error,
                    event_count,
                    mission.result,
                    result_refs,
                    revision_turns,
                ) = previous
                mission.events = mission.events[:event_count]
                mission.result_refs = result_refs
                mission.revision_turns = revision_turns
                raise

    @staticmethod
    def _mark_storage_failure(mission: Mission, error: BaseException | str) -> None:
        with mission._lock:
            mission.status = "blocked"
            mission.storage_error = _error_text(
                f"mission lifecycle persistence failed: {type(error).__name__}: {error}"
                if isinstance(error, BaseException)
                else error
            )
            mission.inflight = False
            mission.cleanup_complete = False
            mission.question = None
            mission._resume_disabled = True
            mission.updated = time.time()

    # -- creation and lookup -----------------------------------------------------------

    def _work_count_locked(self, *, exclude: Mission | None = None) -> int:
        return sum(
            1
            for mission in self.missions.values()
            if mission is not exclude and mission.status in WORK_STATUSES
        )

    def _remove_mission_locked(self, mission: Mission) -> bool:
        with mission._lock:
            if (
                mission.status not in TERMINAL_STATUSES
                or mission.inflight
                or not mission.cleanup_complete
                or (mission._thread is not None and mission._thread.is_alive())
                or self.missions.get(mission.id) is not mission
            ):
                return False
            try:
                directory = self.root / mission.id
                if directory.is_symlink():
                    directory.unlink()
                elif self._is_directory(directory):
                    shutil.rmtree(directory)
                else:
                    return False
                self._fsync_dir(self.root)
            except OSError:
                return False
            self.missions.pop(mission.id, None)
            return True

    def _prune_locked(
        self,
        *,
        now: float | None = None,
        required_slots: int = 0,
        required_bytes: int = 0,
    ) -> list[str]:
        current = time.time() if now is None else float(now)
        eligible = [
            mission
            for mission in self.missions.values()
            if mission.status in TERMINAL_STATUSES
            and not mission.inflight
            and mission.cleanup_complete
            and (mission._thread is None or not mission._thread.is_alive())
        ]
        eligible.sort(key=lambda item: (item.updated, item.id))
        newest_to_keep = max(0, int(self.max_terminal))
        count_excess = max(0, len(eligible) - newest_to_keep)
        candidates: list[Mission] = []
        for index, mission in enumerate(eligible):
            expired = current - mission.updated > max(0.0, self.terminal_ttl_seconds)
            if expired or index < count_excess:
                candidates.append(mission)
        removed: list[str] = []
        for mission in candidates:
            if self._remove_mission_locked(mission):
                removed.append(mission.id)
        for mission in eligible:
            count_full = len(self.missions) + required_slots > self.max_missions
            bytes_full = self._tree_bytes() + required_bytes > self.max_store_bytes
            if not count_full and not bytes_full:
                break
            if mission.id in removed:
                continue
            if self._remove_mission_locked(mission):
                removed.append(mission.id)
        return removed

    def prune(self, now: float | None = None) -> list[str]:
        with self._lock:
            return self._prune_locked(now=now)

    def create_or_get(
        self, mission_id: str, payload: Mapping[str, Any]
    ) -> tuple[Mission, bool]:
        if not valid_mission_id(mission_id):
            raise ValueError("invalid mission_id")
        exact_payload, _input_raw = _json_object(payload, "mission payload")
        payload_raw = _json_bytes(exact_payload, canonical=True)
        if len(payload_raw) > self.max_payload_bytes:
            raise PayloadTooLargeError(
                f"mission payload exceeds {self.max_payload_bytes} bytes"
            )
        request_hash = hashlib.sha256(payload_raw).hexdigest()
        with self._lock:
            existing = self.missions.get(mission_id)
            if existing is not None:
                if existing.request_sha256 == request_hash:
                    return existing, False
                raise MissionConflictError(
                    "mission id is already bound to a different request_sha256"
                )
            self._prune_locked(
                required_slots=1,
                required_bytes=len(payload_raw) + 8192,
            )
            if len(self.missions) >= self.max_missions:
                raise MissionCapacityError("mission store count capacity is exhausted")
            if self._work_count_locked() >= self.max_active:
                raise MissionCapacityError("mission active capacity is exhausted")
            if self._tree_bytes() + len(payload_raw) + 8192 > self.max_store_bytes:
                raise MissionCapacityError("mission store byte capacity is exhausted")
            directory = self._mission_dir(mission_id)
            if directory.exists() or directory.is_symlink():
                raise MissionExistsError("mission id is already reserved on disk")
            try:
                directory.mkdir(mode=0o700, parents=False, exist_ok=False)
                self._fsync_dir(self.root)
                mission = Mission(mission_id, exact_payload, request_hash, payload_raw)
                self._atomic_write(directory / "payload.json", payload_raw)
                self._append_event(
                    mission,
                    "created",
                    {"status": "queued", "request_sha256": request_hash},
                )
                self._persist(mission)
            except Exception:
                shutil.rmtree(directory, ignore_errors=True)
                self._fsync_dir(self.root)
                raise
            self.missions[mission_id] = mission
            return mission, True

    def get(self, mission_id: str) -> Mission | None:
        if not valid_mission_id(mission_id):
            return None
        with self._lock:
            return self.missions.get(mission_id)

    # -- execution ---------------------------------------------------------------------

    @staticmethod
    def _result_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            result = dict(value)
        else:
            serializer = getattr(value, "to_dict", None)
            if not callable(serializer):
                raise TypeError("pipeline runner must return a JSON object or to_dict() result")
            result = serializer()
        exact, _raw = _json_object(result, "pipeline result")
        return exact

    def bind_runner(self, runner: Runner) -> None:
        self._runner = attest_runner(runner)

    def bind_deployment_guard(self, guard: DeploymentGuard) -> None:
        if not isinstance(guard, DeploymentGuard):
            raise TypeError("guard must be a DeploymentGuard")
        guard.verify()
        self._deployment_guard = guard

    def bind_readiness_probe(
        self,
        probe: RunnerSpec | None,
        *,
        require_attestation: bool = False,
        require_linux_cgroup: bool = False,
    ) -> None:
        self._readiness_spec = attest_runner(probe) if probe is not None else None
        self._require_readiness_attestation = bool(require_attestation)
        self._require_linux_cgroup = bool(require_linux_cgroup)
        if self._require_readiness_attestation and self._readiness_spec is None:
            raise RunnerReadinessError("attested readiness probe is required")

    def require_deployment_ready(self) -> None:
        guard = self._deployment_guard
        if guard is not None:
            guard.verify()
        verify_runtime_readiness(
            self._readiness_spec,
            require_attestation=self._require_readiness_attestation,
        )

    def _block_deployment_integrity_locked(
        self, mission: Mission, error: BaseException
    ) -> None:
        mission.inflight = False
        mission.cleanup_complete = True
        mission.storage_error = _error_text(
            f"deployment integrity check failed: {error}"
        )
        try:
            self._transition(
                mission,
                "blocked",
                "deployment_integrity_failed",
                {"error": mission.storage_error},
            )
        except Exception as exc:
            self._mark_storage_failure(mission, exc)
            raise MissionPersistenceError(str(mission.storage_error)) from exc

    def _run_supervised(self, mission: Mission, spec: RunnerSpec) -> dict[str, Any]:
        requested = json.loads(mission.payload_bytes).get("max_wall_sec")
        hard_timeout = self.attempt_timeout_seconds
        if (
            type(requested) in {int, float}
            and not isinstance(requested, bool)
            and requested > 0
        ):
            hard_timeout = min(hard_timeout, float(requested))
        supervisor = ProcessSupervisor(
            max_result_bytes=self.max_result_bytes,
            cancel_grace_seconds=self.cancel_grace_seconds,
            terminate_grace_seconds=self.terminate_grace_seconds,
        )
        return supervisor.run(
            spec=spec,
            payload_bytes=mission.payload_bytes,
            mission_id=mission.id,
            attempt=mission.attempt,
            clarification_turns=tuple(
                dict(turn) for turn in mission.clarification_turns
            ),
            revision_turns=tuple(
                dict(turn) for turn in mission.revision_turns
            ),
            cancelled=mission.cancelled,
            hard_timeout_seconds=hard_timeout,
            deployment_manifest=(
                self._deployment_guard.manifest
                if self._deployment_guard is not None
                else None
            ),
            readiness_spec=self._readiness_spec,
            require_readiness_attestation=self._require_readiness_attestation,
            require_linux_cgroup=self._require_linux_cgroup,
        )

    def launch(self, mission: Mission, runner: Runner | None = None, *, adopted: bool = False) -> bool:
        selected = attest_runner(runner) if runner is not None else self._runner
        if not isinstance(selected, RunnerSpec):
            raise RuntimeError("ResearchWarband runner is not configured")
        with self._lock, mission._lock:
            if self.missions.get(mission.id) is not mission:
                return False
            if mission._thread is not None and mission._thread.is_alive():
                return False
            if mission.status not in {"queued", "running"}:
                return False
            try:
                self.require_deployment_ready()
            except (DeploymentIntegrityError, RunnerReadinessError) as exc:
                self._block_deployment_integrity_locked(mission, exc)
                return False
            if mission.attempt >= self.max_attempts:
                self._transition(mission, "failed", "attempt_limit")
                return False
            if len(self._worker_registry) >= self.max_active:
                return False
            if adopted:
                if mission._adopted_in_process:
                    return False
                mission._adopted_in_process = True
                mission.adopted_count += 1
                mission.status = "queued"
                self._append_event(
                    mission,
                    "adopted",
                    {"status": "queued", "instance_id": self.instance_id},
                )
            mission.attempt += 1
            mission.inflight = True
            mission.cleanup_complete = False
            mission.cancelled.clear()
            self._append_event(
                mission,
                "scheduled",
                {"status": "queued", "attempt": mission.attempt},
            )
            try:
                self._persist(mission)
            except Exception as exc:
                self._mark_storage_failure(mission, exc)
                raise MissionPersistenceError(str(mission.storage_error)) from exc

            worker_token = uuid.uuid4().hex
            worker = threading.Thread(
                target=self._worker_main,
                args=(mission, selected, worker_token),
                daemon=True,
                name=f"research-mission-{mission.id}",
            )
            mission._thread = worker
            mission._worker_token = worker_token
            self._worker_registry[worker_token] = worker
            try:
                worker.start()
            except Exception as exc:
                self._worker_registry.pop(worker_token, None)
                mission._thread = None
                mission._worker_token = None
                self._mark_storage_failure(mission, exc)
                raise MissionPersistenceError(str(mission.storage_error)) from exc
            return True

    def _worker_main(self, mission: Mission, runner: RunnerSpec, worker_token: str) -> None:
        try:
            with mission._lock:
                if mission.cancelled.is_set():
                    self._transition(mission, "cancelled", "cancelled_before_start")
                    return
                self._transition(
                    mission,
                    "running",
                    "started",
                    {"attempt": mission.attempt, "instance_id": self.instance_id},
                )
            result = self._run_supervised(mission, runner)
            self.require_deployment_ready()
            outcome = result.get("outcome")
            mapped = OUTCOME_STATUS.get(outcome) if type(outcome) is str else None
            outcome_event = (
                outcome
                if type(outcome) is str and len(outcome.encode("utf-8")) <= 256
                else f"<{type(outcome).__name__}>"
            )
            with mission._lock:
                # The runner has returned and no further pipeline side effect is
                # pending. Commit the result and quiescent lifecycle together so
                # GET can never observe done with an active/unclean worker.
                mission.inflight = False
                mission.cleanup_complete = True
                if mission.cancelled.is_set():
                    self._transition(mission, "cancelled", "cancelled_after_runner")
                elif mapped is None:
                    mission.storage_error = _error_text(
                        f"pipeline returned unsupported outcome: {outcome!r}"
                    )
                    failed_result = _pipeline_contract_failure_result(
                        result,
                        code="invalid_pipeline_outcome",
                        what_failed="The isolated runner returned an unsupported terminal outcome.",
                        evidence=mission.storage_error,
                        expected=(
                            "The runner returns accepted, accepted_with_uncertainty, "
                            "clarify, needs_revision, blocked, or failed."
                        ),
                        remediation=(
                            "Repair the runner outcome mapping, then resume this persisted "
                            "mission without discarding its attempt history."
                        ),
                    )
                    self._transition(
                        mission,
                        "failed",
                        "invalid_pipeline_outcome",
                        {"outcome": outcome_event},
                        new_result=failed_result,
                    )
                elif mapped == "needs_user":
                    reason = result.get("reason")
                    if type(reason) is not str or not reason.strip():
                        mission.storage_error = "clarify outcome omitted a usable question"
                        failed_result = _pipeline_contract_failure_result(
                            result,
                            code="invalid_clarification",
                            what_failed="The runner requested clarification without a usable question.",
                            evidence=mission.storage_error,
                            expected="A clarify result contains one non-empty, bounded user question.",
                            remediation=(
                                "Repair clarification serialization in the runner, then resume "
                                "this persisted mission."
                            ),
                        )
                        self._transition(
                            mission,
                            "failed",
                            "invalid_clarification",
                            new_result=failed_result,
                        )
                    elif len(reason.strip().encode("utf-8")) > self.max_question_bytes:
                        mission.storage_error = (
                            f"clarification question exceeds {self.max_question_bytes} bytes"
                        )
                        failed_result = _pipeline_contract_failure_result(
                            result,
                            code="oversized_clarification",
                            what_failed="The runner produced a clarification question above the byte limit.",
                            evidence=mission.storage_error,
                            expected=(
                                f"A clarification question is at most {self.max_question_bytes} UTF-8 bytes."
                            ),
                            remediation=(
                                "Make the runner ask one concise question within the contract "
                                "limit, then resume this persisted mission."
                            ),
                        )
                        self._transition(
                            mission,
                            "failed",
                            "invalid_clarification",
                            new_result=failed_result,
                        )
                    elif len(mission.clarification_turns) >= MAX_CLARIFICATION_TURNS:
                        mission.storage_error = "clarification turn limit is exhausted"
                        failed_result = _pipeline_contract_failure_result(
                            result,
                            code="clarification_limit_exhausted",
                            what_failed="The runner exhausted the bounded clarification dialogue.",
                            evidence=mission.storage_error,
                            expected=(
                                "The runner resolves the mission or reports an explained failure "
                                "within the clarification-turn limit."
                            ),
                            remediation=(
                                "Repair the repeated-question strategy before resuming the mission."
                            ),
                        )
                        self._transition(
                            mission,
                            "failed",
                            "clarification_limit",
                            new_result=failed_result,
                        )
                    else:
                        mission.question = reason.strip()
                        self._transition(
                            mission,
                            "needs_user",
                            "needs_user",
                            {"question": mission.question},
                            new_result=result,
                        )
                elif mapped == "needs_revision":
                    turn = _revision_turn_for_result(mission.attempt, result)
                    if turn is None:
                        failed_result = _terminal_failure_result(
                            result,
                            "ResearchWarband returned needs_revision without a valid "
                            "retryable diagnostic; automatic correction cannot safely continue.",
                            revision_protocol_error=True,
                        )
                        self._transition(
                            mission,
                            "failed",
                            "invalid_revision_result",
                            {"attempt": mission.attempt},
                            new_result=failed_result,
                        )
                    elif (
                        mission.attempt >= self.max_attempts
                        or len(mission.revision_turns) >= self.max_auto_revisions
                    ):
                        failed_result = _terminal_failure_result(
                            result,
                            (
                                "ResearchWarband exhausted its bounded automatic "
                                f"correction budget after {mission.attempt} attempt(s). "
                                + str(result.get("reason") or "")
                            ).strip(),
                            revision_exhausted=True,
                            auto_revision_limit=self.max_auto_revisions,
                        )
                        self._transition(
                            mission,
                            "failed",
                            "revision_attempts_exhausted",
                            {
                                "attempt": mission.attempt,
                                "finding_count": len(turn["findings"]),
                            },
                            new_result=failed_result,
                        )
                    else:
                        previous_revisions = list(mission.revision_turns)
                        mission.revision_turns.append(turn)
                        mission.question = None
                        try:
                            self._transition(
                                mission,
                                "queued",
                                "revision_auto_queued",
                                {
                                    "attempt": mission.attempt,
                                    "next_attempt": mission.attempt + 1,
                                    "finding_count": len(turn["findings"]),
                                    "result_sha256": turn["result_sha256"],
                                },
                                new_result=result,
                            )
                        except Exception:
                            mission.revision_turns = previous_revisions
                            raise
                else:
                    mission.question = None
                    self._transition(
                        mission,
                        mapped,
                        "pipeline_result",
                        {"outcome": outcome_event},
                        new_result=result,
                    )
        except RunnerCleanupError as exc:
            with mission._lock:
                mission.inflight = False
                mission.cleanup_complete = False
                mission._resume_disabled = True
                mission.storage_error = _error_text(f"RunnerCleanupError: {exc}")
                try:
                    self._transition(
                        mission,
                        "blocked",
                        "runner_cleanup_unproven",
                        {"error": mission.storage_error},
                    )
                except Exception as persist_exc:
                    self._mark_storage_failure(mission, persist_exc)
        except (DeploymentIntegrityError, RunnerReadinessError) as exc:
            with mission._lock:
                mission.inflight = False
                mission.cleanup_complete = True
                self._block_deployment_integrity_locked(mission, exc)
        except Exception as exc:  # fail closed; never synthesize a successful result
            with mission._lock:
                mission.inflight = False
                mission.cleanup_complete = True
                if mission.cancelled.is_set():
                    try:
                        self._transition(mission, "cancelled", "cancelled_on_error")
                    except Exception as persist_exc:
                        self._mark_storage_failure(mission, persist_exc)
                else:
                    mission.storage_error = _error_text(f"{type(exc).__name__}: {exc}")
                    failure_result = {
                        "outcome": "failed",
                        "reason": (
                            "The isolated ResearchWarband runner failed safely. "
                            "Resume condition: repair the reported runtime defect, then "
                            "resume this same persisted mission."
                        ),
                        "runner_error": mission.storage_error,
                        "pipeline_audit": {
                            "review_findings": [{
                                "code": "research_runner_failure",
                                "entity_kind": "research_runtime",
                                "entity_id": "isolated-runner",
                                "what_failed": "The isolated research runner did not complete its attempt.",
                                "evidence": mission.storage_error,
                                "expected": "The attested runner returns one complete finite result and exits cleanly.",
                                "remediation": "Repair the runner/model/runtime defect and resume the same mission; do not discard its attempt history.",
                                "revision_owner": "infrastructure",
                                "retryable": True,
                            }],
                            "diagnostics": [mission.storage_error],
                        },
                    }
                    try:
                        self._transition(
                            mission,
                            "failed",
                            "runner_error",
                            {"error": mission.storage_error},
                            new_result=failure_result,
                        )
                    except Exception as persist_exc:
                        self._mark_storage_failure(mission, persist_exc)
        finally:
            # Deregistration and queue scheduling share the lease lock. Once this
            # block releases, the exiting worker performs no further store action.
            with self._lock, mission._lock:
                mission.inflight = False
                if not mission._resume_disabled:
                    mission.cleanup_complete = True
                if mission.status == "cancelling":
                    mission.status = "cancelled"
                    try:
                        self._append_event(mission, "cancelled", {"status": "cancelled"})
                    except Exception:
                        pass
                try:
                    self._persist(mission)
                except Exception as exc:
                    self._mark_storage_failure(mission, exc)
                mission._thread = None
                mission._worker_token = None
                self._worker_registry.pop(worker_token, None)
                if (
                    mission.status == "queued"
                    and not mission._resume_disabled
                    and len(self._worker_registry) < self.max_active
                ):
                    # An explicitly supplied attested runner is mission-scoped and
                    # may not also be installed as the store default. Preserve that
                    # exact identity across an automatic correction attempt.
                    self.launch(mission, runner)
                self._launch_waiting_locked()

    def _launch_waiting_locked(self) -> None:
        """Fill slots while holding the same lock used by lease release."""
        selected = self._runner
        if not isinstance(selected, RunnerSpec):
            return
        candidates = [
            mission
            for mission in self.missions.values()
            if mission.status == "queued" and mission._thread is None
        ]
        for candidate in candidates:
            if len(self._worker_registry) >= self.max_active:
                break
            self.launch(candidate, selected)

    def _launch_waiting(self) -> None:
        with self._lock:
            self._launch_waiting_locked()

    def adopt_pending(self, runner: Runner | None = None) -> list[str]:
        selected = attest_runner(runner) if runner is not None else self._runner
        if not isinstance(selected, RunnerSpec):
            raise RuntimeError("ResearchWarband runner is not configured")
        adopted: list[str] = []
        with self._lock:
            candidates = list(self.missions.values())
        for mission in candidates:
            with mission._lock:
                if mission.status == "cancelling":
                    try:
                        mission.cancelled.set()
                        mission.inflight = False
                        mission.cleanup_complete = True
                        mission.question = None
                        self._transition(mission, "cancelled", "cancel_adopted_after_restart")
                    except Exception as exc:
                        self._mark_storage_failure(mission, exc)
                    continue
                eligible = mission.status in {"queued", "running"} and not mission._resume_disabled
            if eligible and self.launch(mission, selected, adopted=True):
                adopted.append(mission.id)
        return adopted

    def cancel(self, mission_id: str, *, expected: Mission | None = None) -> bool:
        with self._lock:
            mission = self.missions.get(mission_id)
            if mission is None or (expected is not None and mission is not expected):
                return False
            with mission._lock:
                if mission.status in TERMINAL_STATUSES:
                    return False
                mission.cancelled.set()
                thread_alive = mission._thread is not None and mission._thread.is_alive()
                try:
                    if thread_alive:
                        self._transition(mission, "cancelling", "cancel_requested")
                    else:
                        mission.question = None
                        self._transition(mission, "cancelled", "cancelled")
                    return True
                except Exception as exc:
                    self._mark_storage_failure(mission, exc)
                    raise MissionPersistenceError(str(mission.storage_error)) from exc

    def provide_answer(
        self,
        mission_id: str,
        answer: str,
        runner: Runner | None = None,
        *,
        expected: Mission | None = None,
    ) -> bool:
        if type(answer) is not str or not answer.strip():
            raise ValueError("answer must be a non-empty string")
        answer = answer.strip()
        answer_size = len(answer.encode("utf-8"))
        if answer_size > self.max_answer_bytes:
            raise PayloadTooLargeError(
                f"answer exceeds {self.max_answer_bytes} bytes"
            )
        self.require_deployment_ready()
        selected = attest_runner(runner) if runner is not None else self._runner
        if not isinstance(selected, RunnerSpec):
            raise RuntimeError("ResearchWarband runner is not configured")
        with self._lock:
            mission = self.missions.get(mission_id)
            if mission is None or (expected is not None and mission is not expected):
                return False
            with mission._lock:
                if mission.status != "needs_user" or mission._resume_disabled:
                    return False
                if type(mission.question) is not str or not mission.question.strip():
                    return False
                if len(mission.clarification_turns) >= MAX_CLARIFICATION_TURNS:
                    raise MissionCapacityError("clarification turn limit is exhausted")
                question = mission.question
                question_size = len(question.encode("utf-8"))
                aggregate = sum(
                    len(turn["question"].encode("utf-8"))
                    + len(turn["answer"].encode("utf-8"))
                    for turn in mission.clarification_turns
                )
                if aggregate + question_size + answer_size > MAX_CLARIFICATION_TOTAL_BYTES:
                    raise PayloadTooLargeError("clarification turns exceed aggregate byte limit")
                if self._work_count_locked(exclude=mission) >= self.max_active:
                    raise MissionCapacityError("mission active capacity is exhausted")
                previous_turns = [dict(turn) for turn in mission.clarification_turns]
                previous_question = mission.question
                mission.clarification_turns.append(
                    {"question": question, "answer": answer}
                )
                mission.question = None
                mission.cancelled = threading.Event()
                try:
                    self._transition(
                        mission,
                        "queued",
                        "answer_received",
                        {
                            "turn_index": len(mission.clarification_turns) - 1,
                            "question_sha256": hashlib.sha256(
                                question.encode("utf-8")
                            ).hexdigest(),
                        },
                    )
                except Exception as exc:
                    mission.clarification_turns = previous_turns
                    mission.question = previous_question
                    self._mark_storage_failure(mission, exc)
                    raise MissionPersistenceError(str(mission.storage_error)) from exc
        return self.launch(mission, selected)

    def resume(
        self,
        mission_id: str,
        runner: Runner | None = None,
        *,
        expected: Mission | None = None,
    ) -> bool:
        self.require_deployment_ready()
        selected = attest_runner(runner) if runner is not None else self._runner
        if not isinstance(selected, RunnerSpec):
            raise RuntimeError("ResearchWarband runner is not configured")
        with self._lock:
            mission = self.missions.get(mission_id)
            if mission is None or (expected is not None and mission is not expected):
                return False
            with mission._lock:
                if (
                    mission.status not in {
                        "needs_revision", "blocked", "failed", "cancelled"
                    }
                    or mission._resume_disabled
                    or not mission.cleanup_complete
                    or mission.attempt >= self.max_attempts
                ):
                    return False
                if self._work_count_locked(exclude=mission) >= self.max_active:
                    raise MissionCapacityError("mission active capacity is exhausted")
                previous_revisions = list(mission.revision_turns)
                if mission.status == "needs_revision":
                    turn = _revision_turn_for_result(
                        mission.attempt, mission.result or {}
                    )
                    if turn is None:
                        return False
                    if not any(
                        existing["attempt"] == turn["attempt"]
                        for existing in mission.revision_turns
                    ):
                        mission.revision_turns.append(turn)
                mission.cancelled = threading.Event()
                mission.question = None
                mission.storage_error = None
                try:
                    self._transition(mission, "queued", "resume_requested")
                except Exception as exc:
                    mission.revision_turns = previous_revisions
                    self._mark_storage_failure(mission, exc)
                    raise MissionPersistenceError(str(mission.storage_error)) from exc
        return self.launch(mission, selected)

    def active_worker_count(self) -> int:
        with self._lock:
            return len(self._worker_registry)

    def wait_for_idle(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            with self._lock:
                threads = [
                    mission._thread
                    for mission in self.missions.values()
                    if mission._thread is not None
                ]
            if not threads:
                return True
            for worker in threads:
                worker.join(timeout=min(0.05, max(0.0, deadline - time.monotonic())))
        return self.active_worker_count() == 0

    # -- recovery ----------------------------------------------------------------------

    @staticmethod
    def _placeholder(mission_id: str, reason: str) -> Mission:
        mission = Mission(mission_id, {}, hashlib.sha256(b"{}").hexdigest())
        mission.status = "blocked"
        mission.storage_error = _error_text(reason)
        mission.cleanup_complete = False
        mission._resume_disabled = True
        mission.events = [
            {
                "seq": 1,
                "at": time.time(),
                "type": "storage_blocked",
                "status": "blocked",
                "error": mission.storage_error,
            }
        ]
        return mission

    def _verify_ref(self, directory: Path, reference: Any, expected: str, limit: int) -> bytes:
        if not isinstance(reference, dict) or set(reference) != {"path", "size_bytes", "sha256"}:
            raise MissionPersistenceError(f"invalid {expected} reference")
        if reference.get("path") != expected:
            raise UnsafeStoreError(f"invalid {expected} reference path")
        raw = self._read_bounded(directory / expected, limit)
        if reference.get("size_bytes") != len(raw):
            raise MissionPersistenceError(f"{expected} size mismatch")
        if reference.get("sha256") != hashlib.sha256(raw).hexdigest():
            raise MissionPersistenceError(f"{expected} hash mismatch")
        return raw

    def _load_mission(self, directory: Path) -> Mission:
        mission_id = directory.name
        if not valid_mission_id(mission_id):
            raise UnsafeStoreError("invalid mission directory name")
        if not self._is_directory(directory):
            raise UnsafeStoreError("mission entry is not a real directory")
        state_raw = self._read_bounded(directory / "mission.json", self.max_state_bytes)
        state = json.loads(state_raw)
        if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
            raise MissionPersistenceError("invalid mission metadata schema")
        if state.get("id") != mission_id:
            raise MissionPersistenceError("mission id does not match directory")
        payload_raw = self._verify_ref(
            directory, state.get("payload_ref"), "payload.json", self.max_payload_bytes
        )
        payload = json.loads(payload_raw)
        if not isinstance(payload, dict):
            raise MissionPersistenceError("payload.json is not an object")
        if payload_raw != _json_bytes(payload, canonical=True):
            raise MissionPersistenceError("payload.json is not canonical JSON")
        request_hash = hashlib.sha256(payload_raw).hexdigest()
        if state.get("request_sha256") != request_hash:
            raise MissionPersistenceError("request_sha256 does not match payload")

        events_ref = state.get("events_ref")
        if not isinstance(events_ref, dict) or set(events_ref) != {
            "path", "count", "size_bytes", "sha256"
        }:
            raise MissionPersistenceError("invalid events reference")
        events_raw = self._read_bounded(directory / "events.json", self.max_events_bytes)
        if events_ref.get("path") != "events.json":
            raise UnsafeStoreError("invalid events reference path")
        if events_ref.get("size_bytes") != len(events_raw):
            raise MissionPersistenceError("events size mismatch")
        if events_ref.get("sha256") != hashlib.sha256(events_raw).hexdigest():
            raise MissionPersistenceError("events hash mismatch")
        events = json.loads(events_raw)
        if not isinstance(events, list) or events_ref.get("count") != len(events):
            raise MissionPersistenceError("events count mismatch")
        for index, event in enumerate(events, 1):
            if not isinstance(event, dict) or event.get("seq") != index:
                raise MissionPersistenceError("event sequence is invalid")
            if len(_json_bytes(event)) > self.max_event_bytes:
                raise EventLimitError("persisted event exceeds limit")

        mission = Mission(mission_id, payload, request_hash, payload_raw)
        status = state.get("status")
        if status not in ALL_STATUSES:
            raise MissionPersistenceError("persisted mission status is invalid")
        mission.status = status
        mission.created = float(state.get("created"))
        mission.updated = float(state.get("updated"))
        mission.attempt = int(state.get("attempt"))
        mission.question = state.get("question")
        turns = state.get("clarification_turns")
        if not isinstance(turns, list) or len(turns) > MAX_CLARIFICATION_TURNS:
            raise MissionPersistenceError("persisted clarification turns are invalid")
        aggregate = 0
        for turn in turns:
            if not isinstance(turn, dict) or set(turn) != {"question", "answer"}:
                raise MissionPersistenceError("persisted clarification turn is invalid")
            question, answer = turn.get("question"), turn.get("answer")
            if type(question) is not str or type(answer) is not str:
                raise MissionPersistenceError("persisted clarification turn is invalid")
            question_size = len(question.encode("utf-8"))
            answer_size = len(answer.encode("utf-8"))
            if (
                not question.strip()
                or not answer.strip()
                or question_size > self.max_question_bytes
                or answer_size > self.max_answer_bytes
            ):
                raise MissionPersistenceError("persisted clarification turn is invalid")
            aggregate += question_size + answer_size
            if aggregate > MAX_CLARIFICATION_TOTAL_BYTES:
                raise MissionPersistenceError("persisted clarification turns exceed byte limit")
            mission.clarification_turns.append(
                {"question": question, "answer": answer}
            )
        try:
            mission.revision_turns = [
                dict(turn)
                for turn in validate_revision_turns(
                    state.get("revision_turns") or ()
                )
            ]
        except (TypeError, ValueError, UnicodeError) as exc:
            raise MissionPersistenceError(
                f"persisted revision turns are invalid: {exc}"
            ) from exc
        mission.events = list(events)
        mission.storage_error = state.get("storage_error")
        mission.inflight = False  # no worker from the previous process survives recovery
        mission.cleanup_complete = bool(state.get("cleanup_complete"))
        mission.adopted_count = int(state.get("adopted_count", 0))

        raw_refs = state.get("result_refs")
        if not isinstance(raw_refs, list) or len(raw_refs) > self.max_attempts:
            raise MissionPersistenceError("persisted result references are invalid")
        result_files: set[str] = set()
        for reference in raw_refs:
            if not isinstance(reference, dict):
                raise MissionPersistenceError("persisted result reference is invalid")
            name = reference.get("path")
            if type(name) is not str or not re.fullmatch(r"result-[0-9]{6}\.json", name):
                raise UnsafeStoreError("invalid result reference path")
            if name in result_files:
                raise MissionPersistenceError("duplicate result reference")
            result_files.add(name)
            result_raw = self._verify_ref(directory, reference, name, self.max_result_bytes)
            result = json.loads(result_raw)
            if not isinstance(result, dict):
                raise MissionPersistenceError("persisted pipeline result is not an object")
            mission.result_refs.append(dict(reference))
            mission.result = result
        current = state.get("current_result")
        expected_current = mission.result_refs[-1]["path"] if mission.result_refs else None
        if current != expected_current:
            raise MissionPersistenceError("current result reference is inconsistent")
        result_hashes = {
            int(str(reference["path"])[7:13]): str(reference["sha256"])
            for reference in mission.result_refs
        }
        for turn in mission.revision_turns:
            if result_hashes.get(turn["attempt"]) != turn["result_sha256"]:
                raise MissionPersistenceError(
                    "persisted revision turn is not bound to its exact attempt result"
                )

        allowed = {"mission.json", "payload.json", "events.json", *result_files}
        actual: set[str] = set()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    raise UnsafeStoreError(f"symlink found in mission directory: {entry.name}")
                actual.add(entry.name)
        unexpected = actual - allowed
        if unexpected:
            raise MissionPersistenceError(
                "uncommitted or unknown mission files: " + ", ".join(sorted(unexpected))
            )
        if mission.status in TERMINAL_STATUSES and (
            bool(state.get("inflight")) or not mission.cleanup_complete
        ):
            raise MissionPersistenceError("terminal mission was not cleanly finalized")
        if mission.status in {"queued", "running", "cancelling"}:
            mission.cleanup_complete = False
        return mission

    def _rehydrate(self) -> None:
        with self._lock:
            entries = []
            with os.scandir(self.root) as iterator:
                for entry in iterator:
                    if entry.name in {".store.json", ".service.lock"}:
                        continue
                    entries.append(Path(entry.path))
                    if len(entries) > self.max_missions:
                        raise MissionCapacityError(
                            "persisted mission count exceeds configured capacity"
                        )
            if self._tree_bytes() > self.max_store_bytes:
                raise MissionCapacityError("persisted mission store exceeds byte capacity")
            for directory in sorted(entries, key=lambda item: item.name):
                mission_id = directory.name
                if not valid_mission_id(mission_id):
                    self.recovery_errors.append(f"ignored unsafe store entry: {mission_id!r}")
                    continue
                try:
                    mission = self._load_mission(directory)
                except Exception as exc:
                    mission = self._placeholder(
                        mission_id, f"persisted mission is unavailable: {type(exc).__name__}: {exc}"
                    )
                    self.recovery_errors.append(f"{mission_id}: {mission.storage_error}")
                self.missions[mission_id] = mission
                self.recovered_count += 1
            self._prune_locked()

    def recovery_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "safe": True,
                "store_id": self.store_id,
                "root": str(self.root),
                "recovered": self.recovered_count,
                "loaded": len(self.missions),
                "adoptable": sum(
                    1 for mission in self.missions.values() if mission.status in {"queued", "running"}
                ),
                "needs_user": sum(
                    1 for mission in self.missions.values() if mission.status == "needs_user"
                ),
                "blocked_entries": len(self.recovery_errors),
                "errors": list(self.recovery_errors),
            }


_DEFAULT_STORE: MissionStore | None = None
_DEFAULT_STORE_LOCK = threading.Lock()


def default_store() -> MissionStore:
    """Return the process singleton using only RESEARCH_WARBAND_* configuration."""
    global _DEFAULT_STORE
    with _DEFAULT_STORE_LOCK:
        if _DEFAULT_STORE is None:
            _DEFAULT_STORE = MissionStore()
        return _DEFAULT_STORE


def reset_default_store_for_tests() -> None:
    global _DEFAULT_STORE
    with _DEFAULT_STORE_LOCK:
        _DEFAULT_STORE = None


__all__ = [
    "ACTIVE_STATUSES",
    "ALL_STATUSES",
    "AttemptContext",
    "EventLimitError",
    "Mission",
    "MissionCapacityError",
    "MissionConflictError",
    "MissionExistsError",
    "MissionPersistenceError",
    "MissionStore",
    "MissionStoreError",
    "OUTCOME_STATUS",
    "PayloadTooLargeError",
    "ResultTooLargeError",
    "RunnerSpec",
    "RunnerReadinessError",
    "TERMINAL_STATUSES",
    "UnsafeStoreError",
    "attest_runner",
    "read_runtime_readiness",
    "verify_linux_cgroup_delegation",
    "default_store",
    "request_sha256",
    "reset_default_store_for_tests",
    "valid_mission_id",
]
