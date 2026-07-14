"""Bounded, crash-visible persistence for asynchronous Skitarii missions.

The request payload is the resumable source of truth.  It is stored exactly (as
JSON data), while journals and results are deliberately compactable so an
untrusted mission cannot grow the service state without bound.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import threading
import time
from pathlib import Path
from typing import Any, Callable


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


STORE_ROOT = Path(__file__).resolve().parent / "runtime" / "missions"
MISSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
ACTIVE_STATUSES = {"queued", "running", "needs_user", "cancelling"}
TERMINAL_STATUSES = {"done", "failed", "blocked", "cancelled"}

# Every limit can be tightened without a code change.  Payload has its own cap
# and is never truncated; state leaves headroom for metadata around it.
MAX_PERSISTED_STATE_BYTES = _env_int("SKITARII_MISSION_STATE_MAX_BYTES", 100_000_000, 4096)
MAX_PERSISTED_PAYLOAD_BYTES = _env_int("SKITARII_MISSION_PAYLOAD_MAX_BYTES", 75_000_000, 1024)
MAX_PERSISTED_RESULT_BYTES = _env_int("SKITARII_MISSION_RESULT_MAX_BYTES", 75_000_000, 1024)
MAX_MISSION_DURABLE_BYTES = _env_int("SKITARII_MISSION_DURABLE_MAX_BYTES", 160_000_000, 4096)
MAX_EVENT_FILE_BYTES = _env_int("SKITARII_MISSION_EVENTS_MAX_BYTES", 2_000_000, 256)
MAX_EVENT_BYTES = _env_int("SKITARII_MISSION_EVENT_MAX_BYTES", 64_000, 128)
MAX_EVENTS_IN_MEMORY = _env_int("SKITARII_MISSION_EVENTS_IN_MEMORY", 512, 1)
MAX_TEXT_BYTES = _env_int("SKITARII_MISSION_TEXT_MAX_BYTES", 64_000, 128)
MAX_TERMINAL_MISSIONS = _env_int("SKITARII_MISSION_TERMINAL_MAX_COUNT", 64, 0)
TERMINAL_TTL_SECONDS = _env_float("SKITARII_MISSION_TERMINAL_TTL_SECONDS", 7 * 24 * 3600, 0.0)
MAX_TERMINAL_DURABLE_BYTES = _env_int(
    "SKITARII_MISSION_TERMINAL_MAX_BYTES", 2_000_000_000, 0
)
MAX_STORE_DURABLE_BYTES = _env_int("SKITARII_MISSION_STORE_MAX_BYTES", 4_000_000_000, 4096)
MAX_STORE_MISSIONS = _env_int("SKITARII_MISSION_STORE_MAX_COUNT", 128, 1)
MAX_ACTIVE_MISSIONS = _env_int("SKITARII_MISSION_ACTIVE_MAX_COUNT", 1, 1)
MAX_AUTO_REVISION_ATTEMPTS = _env_int(
    "SKITARII_MISSION_AUTO_REVISION_ATTEMPTS", 3, 1
)
MAX_TASK_CHECKPOINT_COMMIT_ATTEMPTS = _env_int(
    "SKITARII_TASK_CHECKPOINT_COMMIT_ATTEMPTS", 8, 1
)
TASK_CHECKPOINT_RETRY_BASE_SECONDS = _env_float(
    "SKITARII_TASK_CHECKPOINT_RETRY_BASE_SECONDS", 1.0, 0.0
)
TASK_CHECKPOINT_RETRY_MAX_SECONDS = _env_float(
    "SKITARII_TASK_CHECKPOINT_RETRY_MAX_SECONDS", 30.0, 0.0
)
MAX_REVISION_TURNS = _env_int("SKITARII_MISSION_REVISION_TURNS", 8, 1)
MAX_REVISION_FINDINGS = 20
MAX_REVISION_CONTEXT_BYTES = _env_int(
    "SKITARII_MISSION_REVISION_CONTEXT_BYTES", 128_000, 4096
)

_REVIEW_FINDING_FIELDS = frozenset({
    "code",
    "entity_kind",
    "entity_id",
    "what_failed",
    "evidence",
    "expected",
    "remediation",
    "revision_owner",
    "retryable",
})
_REVIEW_REVISION_OWNERS = frozenset({
    "scout",
    "reader",
    "analyst",
    "writer",
    "fighter",
    "governor",
    "infrastructure",
    "operator",
})


def _revision_findings(value: Any) -> list[dict[str, Any]]:
    """Return bounded actionable findings safe to persist as retry context."""

    if not isinstance(value, list) or not 1 <= len(value) <= MAX_REVISION_FINDINGS:
        return []
    normalized: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != _REVIEW_FINDING_FIELDS:
            return []
        item: dict[str, Any] = {}
        for field in _REVIEW_FINDING_FIELDS - {"retryable"}:
            field_value = raw.get(field)
            if type(field_value) is not str or not field_value.strip():
                return []
            encoded = field_value.strip().encode("utf-8", errors="strict")
            if len(encoded) > 2_000:
                return []
            item[field] = field_value.strip()
        if item["revision_owner"] not in _REVIEW_REVISION_OWNERS:
            return []
        if type(raw.get("retryable")) is not bool:
            return []
        item["retryable"] = raw["retryable"]
        normalized.append(item)
    if len(_json_bytes(normalized)) > MAX_REVISION_CONTEXT_BYTES:
        return []
    return normalized


def _revision_turn(verdict: dict[str, Any], attempt: int) -> dict[str, Any] | None:
    if (
        verdict.get("accepted") is not False
        or verdict.get("revision_required") is not True
        or verdict.get("needs_user") is True
        or verdict.get("cleanup_complete") is False
    ):
        return None
    findings = _revision_findings(verdict.get("verification_findings"))
    retryable = [item for item in findings if item["retryable"]]
    if not retryable:
        return None
    order = " ".join(item["remediation"] for item in retryable[:5]).strip()
    order = _bounded_text(order, 8_000)
    raw = _json_bytes(verdict, sort_keys=True)
    turn = {
        "attempt": int(attempt),
        "result_sha256": hashlib.sha256(raw).hexdigest(),
        "decision_owner": (
            "Ceraxia"
            if any(item["revision_owner"] == "governor" for item in retryable)
            else "SkitariiWarband"
        ),
        "leader_order": order,
        "findings": findings,
    }
    return turn if len(_json_bytes(turn)) <= MAX_REVISION_CONTEXT_BYTES else None


def _revision_time_available(mission: "Mission") -> bool:
    payload = mission.payload or {}
    try:
        max_wall = max(1, int(payload.get("max_wall_sec") or 3600))
    except (TypeError, ValueError):
        max_wall = 3600
    return time.time() - mission.created < max(1, max_wall - 30)


def _task_checkpoint_commit_pending(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("error_code") == "task_checkpoint_commit_pending"
        and isinstance(value.get("pending_task_checkpoint"), dict)
        and bool(str(value.get("pending_task_checkpoint_key") or ""))
        and isinstance(value.get("checkpoint_pending_original"), dict)
    )


def _task_checkpoint_commit_attempts(value: dict[str, Any]) -> int:
    try:
        attempts = int(value.get("task_checkpoint_commit_attempts") or 1)
    except (TypeError, ValueError):
        attempts = 1
    return max(1, attempts)


def _task_checkpoint_retry_delay(attempts: int) -> float:
    base = max(0.0, float(TASK_CHECKPOINT_RETRY_BASE_SECONDS))
    maximum = max(0.0, float(TASK_CHECKPOINT_RETRY_MAX_SECONDS))
    if base <= 0.0 or maximum <= 0.0:
        return 0.0
    return min(maximum, base * (2 ** min(16, max(0, attempts - 1))))


def _valid_restart_workspace_checkpoint(value: Any) -> bool:
    """Accept only a complete, identity-bound patch that can really be replayed."""
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return False
    diff = value.get("unified_diff")
    changed = value.get("changed_files")
    base_tree = str(value.get("base_tree") or "")
    patch_sha256 = str(value.get("patch_sha256") or "")
    task_memory_id = str(value.get("task_memory_id") or "")
    root_task_id = str(value.get("root_task_id") or "")
    parent_task_id = value.get("parent_task_id")
    try:
        diff_raw = diff.encode("utf-8", errors="strict") if isinstance(diff, str) else b""
    except UnicodeEncodeError:
        return False
    if (
        not isinstance(diff, str)
        or not diff
        or len(diff_raw) > MAX_PERSISTED_RESULT_BYTES
        or not isinstance(changed, list)
        or not changed
        or any(type(path) is not str or not path or "\x00" in path for path in changed)
        or not re.fullmatch(r"[0-9a-f]{40,64}", base_tree)
        or not re.fullmatch(r"[0-9a-f]{64}", patch_sha256)
        or hashlib.sha256(diff_raw).hexdigest() != patch_sha256
        or not valid_mission_id(task_memory_id)
        or not valid_mission_id(root_task_id)
        or type(parent_task_id) is not str
        or (bool(parent_task_id) and not valid_mission_id(parent_task_id))
    ):
        return False
    return True


def _valid_restart_pending_task_checkpoint(value: Any) -> bool:
    if not _task_checkpoint_commit_pending(value):
        return False
    assert isinstance(value, dict)
    checkpoint = value.get("pending_task_checkpoint")
    parent_task_id = value.get("parent_task_id")
    return bool(
        valid_mission_id(str(value.get("task_memory_id") or ""))
        and valid_mission_id(str(value.get("root_task_id") or ""))
        and type(parent_task_id) is str
        and (not parent_task_id or valid_mission_id(parent_task_id))
        and isinstance(checkpoint, dict)
        and checkpoint.get("version") == 1
        and bool(str(checkpoint.get("current_state") or "").strip())
    )


def _cancelled_result(value: Any) -> dict[str, Any]:
    """Overlay cancellation without erasing patches or pending leader commits."""
    result = dict(value) if isinstance(value, dict) else {}
    result.update({
        "status": "cancelled",
        "accepted": False,
        "cancelled": True,
        "summary": "Mission was cancelled; any durable recovery checkpoint is preserved.",
    })
    return result


class MissionExistsError(RuntimeError):
    """The mission id is already reserved on disk or in this process."""


class PayloadTooLargeError(ValueError):
    """The exact request payload cannot fit within the persistence contract."""


class PersistenceLimitError(RuntimeError):
    """A bounded mission snapshot cannot be produced."""


class ResultTooLargeError(ValueError):
    """A worker result cannot be persisted without losing its deliverable."""


class MissionCapacityError(RuntimeError):
    """Admission would exceed the bounded global store or active queue."""


class MissionPersistenceError(RuntimeError):
    """A lifecycle transition could not be durably committed."""


def valid_mission_id(value: str) -> bool:
    return bool(MISSION_ID_RE.fullmatch(str(value))) and str(value) not in {".", ".."}


def _json_bytes(value: Any, *, sort_keys: bool = False) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=sort_keys,
        separators=(",", ":"),
    ).encode("utf-8")


def _bounded_text(value: Any, limit: int = MAX_TEXT_BYTES) -> str:
    text = str(value)
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= max(0, limit):
        return text
    marker = "…"
    marker_raw = marker.encode("utf-8")
    available = max(0, limit - len(marker_raw))
    return raw[:available].decode("utf-8", errors="ignore") + (marker if limit >= len(marker_raw) else "")


def _canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    bound_payload = dict(payload)
    bound_payload.pop("task_id", None)
    return _json_bytes(bound_payload, sort_keys=True)


def request_sha256(payload: dict[str, Any]) -> str:
    """Hash canonical JSON request data, deliberately excluding ``task_id``."""
    if not isinstance(payload, dict):
        raise TypeError("mission payload must be a JSON object")
    return hashlib.sha256(_canonical_payload_bytes(payload)).hexdigest()


def _exact_json_object(payload: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    if not isinstance(payload, dict):
        raise TypeError("mission payload must be a JSON object")
    try:
        raw = _json_bytes(payload)
        restored = json.loads(raw.decode("utf-8"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("mission payload must contain JSON-serializable data") from exc
    if not isinstance(restored, dict):
        raise TypeError("mission payload must be a JSON object")
    return restored, raw


def _safe_timestamp(value: Any, fallback: float) -> float:
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return fallback
    return stamp if math.isfinite(stamp) and stamp >= 0 else fallback


def _owned_by_service(info: os.stat_result) -> bool:
    getuid = getattr(os, "geteuid", None)
    return getuid is None or info.st_uid == getuid()


def _secure_directory(path: Path, *, fix_mode: bool) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"unsafe directory: {path}")
    if not _owned_by_service(info):
        raise PermissionError(f"directory is owned by another uid: {path}")
    if stat.S_IMODE(info.st_mode) != 0o700:
        if not fix_mode:
            raise PermissionError(f"directory mode is not 0700: {path}")
        path.chmod(0o700)


def _secure_file(path: Path, *, fix_mode: bool) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"unsafe regular file: {path}")
    if not _owned_by_service(info):
        raise PermissionError(f"file is owned by another uid: {path}")
    if stat.S_IMODE(info.st_mode) != 0o600:
        if not fix_mode:
            raise PermissionError(f"file mode is not 0600: {path}")
        path.chmod(0o600)


def _ensure_store_root() -> Path:
    raw = STORE_ROOT
    if raw.is_symlink():
        raise ValueError("mission store root must not be a symlink")
    raw.mkdir(mode=0o700, parents=True, exist_ok=True)
    _secure_directory(raw, fix_mode=True)
    return raw.resolve()


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError:
        if os.name == "nt":
            return
        raise
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _mission_path(mission_id: str) -> Path:
    if not valid_mission_id(mission_id):
        raise ValueError("invalid mission_id")
    root = _ensure_store_root()
    candidate = root / mission_id
    if candidate.is_symlink():
        raise ValueError("mission directory must not be a symlink")
    resolved = candidate.resolve()
    if resolved.parent != root:
        raise ValueError("mission_id escapes mission store")
    return resolved


def _tree_durable_bytes(path: Path, limit: int | None = None) -> int:
    """Count allocated mission bytes without following attacker-controlled links."""
    try:
        info = path.lstat()
    except OSError:
        return 0
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return int(info.st_size)
    total = int(info.st_size)
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    child = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                child_path = Path(entry.path)
                if stat.S_ISDIR(child.st_mode) and not stat.S_ISLNK(child.st_mode):
                    remaining = None if limit is None else max(0, limit - total)
                    total += _tree_durable_bytes(child_path, remaining)
                else:
                    total += int(child.st_size)
                if limit is not None and total > limit:
                    return total
    except OSError:
        return total
    return total


def _store_durable_bytes() -> int:
    if not STORE_ROOT.exists() or STORE_ROOT.is_symlink():
        return 0
    return _tree_durable_bytes(STORE_ROOT, MAX_STORE_DURABLE_BYTES)


def _store_entry_count() -> int:
    try:
        count = 0
        with os.scandir(STORE_ROOT) as entries:
            for _entry in entries:
                count += 1
                if count >= max(1, int(MAX_STORE_MISSIONS)):
                    return count
        return count
    except OSError:
        return 0


def _active_count_locked() -> int:
    return sum(
        1
        for mission in _MISSIONS.values()
        if mission.inflight or mission.status in ACTIVE_STATUSES
    )


class Mission:
    def __init__(self, mission_id: str, goal: str):
        if not valid_mission_id(mission_id):
            raise ValueError("invalid mission_id")
        self.id = mission_id
        self.goal = _bounded_text(goal)
        # queued|running|needs_user|cancelling|done|failed|blocked|cancelled
        self.status = "queued"
        self.events: list[dict[str, Any]] = []
        self._result: dict[str, Any] | None = None
        self._result_loaded = True
        self._result_ref: dict[str, Any] | None = None
        self._payload: dict[str, Any] | None = None
        self._payload_loaded = True
        self._payload_ref: dict[str, Any] | None = None
        self.request_sha256: str | None = None
        self.created = time.time()
        self.updated = time.time()
        self.attempt = 0
        self.revision_turns: list[dict[str, Any]] = []
        self.question: str | None = None
        self.answer: str | None = None
        self.storage_error: str | None = None
        self._resume_disabled = False
        self._gc_after_restart = False
        self.inflight = False
        self.cleanup_complete = True
        self._answer_ev = threading.Event()
        self.cancelled = threading.Event()
        self._lock = threading.RLock()

    @property
    def payload(self) -> dict[str, Any] | None:
        with self._lock:
            self._load_payload_locked()
        return self._payload

    @payload.setter
    def payload(self, value: dict[str, Any] | None) -> None:
        # Preserve compatibility with callers that used direct assignment while
        # still enforcing the exact-payload persistence boundary.
        self.set_payload(value)

    @property
    def result(self) -> dict[str, Any] | None:
        with self._lock:
            self._load_result_locked()
            return self._result

    @result.setter
    def result(self, value: dict[str, Any] | None) -> None:
        with self._lock:
            self._result = value
            self._result_loaded = True

    # Compatibility for older in-process callers/tests.  Persistence and the
    # public API use the explicit lifecycle name ``inflight``.
    @property
    def _worker_active(self) -> bool:
        return self.inflight

    @_worker_active.setter
    def _worker_active(self, value: bool) -> None:
        self.inflight = bool(value)
        if self.inflight:
            self.cleanup_complete = False

    def _dir(self) -> Path:
        directory = _mission_path(self.id)
        _ensure_store_root()
        directory.mkdir(mode=0o700, parents=False, exist_ok=True)
        _secure_directory(directory, fix_mode=True)
        return directory

    def _storage_load_failure_locked(self, error: str) -> None:
        self.status = "blocked"
        self.storage_error = _bounded_text(error, 4096)
        self._resume_disabled = True
        self.inflight = False
        self.cleanup_complete = False
        self.question = None
        self._result = {
            "status": "blocked",
            "accepted": False,
            "error": self.storage_error,
        }
        self._result_loaded = True
        self._result_ref = None

    def _mark_persistence_failure_locked(self, error: BaseException | str) -> None:
        detail = _bounded_text(
            f"mission lifecycle persistence failed: {type(error).__name__}: {error}"
            if isinstance(error, BaseException) else str(error),
            4096,
        )
        self.status = "blocked"
        self.storage_error = detail
        self._resume_disabled = True
        self.cleanup_complete = False
        self.question = None
        self._result = {
            "status": "blocked",
            "accepted": False,
            "cleanup_complete": False,
            "error": detail,
        }
        self._result_loaded = True
        self._result_ref = None

    def _load_payload_locked(self) -> None:
        if self._payload_loaded:
            return
        if not self._payload_ref:
            self._payload = None
            self._payload_loaded = True
            return
        value, error = _read_json_blob(self._dir(), self._payload_ref, "payload.json")
        if error:
            self._payload = None
            self._payload_loaded = True
            self._storage_load_failure_locked(error)
            return
        computed_hash = request_sha256(value)
        if self.request_sha256 and computed_hash != self.request_sha256:
            self._payload = None
            self._payload_loaded = True
            self._storage_load_failure_locked(
                "payload.json canonical request hash does not match mission metadata"
            )
            return
        self._payload = value
        self.request_sha256 = computed_hash
        self._payload_loaded = True

    def _load_result_locked(self) -> None:
        if self._result_loaded:
            return
        if not self._result_ref:
            self._result = None
            self._result_loaded = True
            return
        value, error = _read_json_blob(self._dir(), self._result_ref, "result.json")
        if error:
            self._storage_load_failure_locked(error)
            return
        self._result = value
        self._result_loaded = True

    def _payload_blob_locked(self) -> bytes | None:
        if not self._payload_loaded:
            return None
        if self._payload is None:
            return None
        raw = _json_bytes(self._payload)
        if len(raw) > MAX_PERSISTED_PAYLOAD_BYTES:
            raise PayloadTooLargeError(
                f"mission payload exceeds {MAX_PERSISTED_PAYLOAD_BYTES} bytes"
            )
        return raw

    def _result_blob_locked(self) -> bytes | None:
        if not self._result_loaded:
            return None
        if self._result is None:
            return None
        if not isinstance(self._result, dict):
            raise ValueError("mission result must be a JSON object")
        try:
            raw = _json_bytes(self._result)
        except (TypeError, ValueError) as exc:
            raise ValueError("mission result must contain JSON-serializable data") from exc
        if len(raw) > MAX_PERSISTED_RESULT_BYTES:
            raise ResultTooLargeError(
                f"mission result exceeds {MAX_PERSISTED_RESULT_BYTES} bytes"
            )
        return raw

    @staticmethod
    def _blob_ref(path: str, raw: bytes | None) -> dict[str, Any] | None:
        if raw is None:
            return None
        return {
            "path": path,
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }

    def _state_locked(
        self,
        payload_raw: bytes | None = None,
        result_raw: bytes | None = None,
    ) -> dict[str, Any]:
        if payload_raw is None and self._payload_loaded and self._payload is not None:
            payload_raw = self._payload_blob_locked()
        if result_raw is None and self._result_loaded and self._result is not None:
            result_raw = self._result_blob_locked()
        payload_ref = (
            self._blob_ref("payload.json", payload_raw)
            if payload_raw is not None
            else (self._payload_ref if not self._payload_loaded else None)
        )
        result_ref = (
            self._blob_ref("result.json", result_raw)
            if result_raw is not None
            else (self._result_ref if not self._result_loaded else None)
        )
        state = {
            "id": self.id,
            "goal": self.goal,
            "status": self.status,
            "question": self.question,
            "answer": self.answer,
            # Large request/result bodies live in separately bounded files.  The
            # metadata commit is last, so it never points at an unwritten blob.
            "result": None,
            "result_ref": result_ref,
            "payload": None,
            "payload_ref": payload_ref,
            "request_sha256": self.request_sha256,
            "attempt": self.attempt,
            "revision_turns": self.revision_turns,
            "storage_error": self.storage_error,
            "inflight": self.inflight,
            "cleanup_complete": self.cleanup_complete,
            "created": self.created,
            "updated": self.updated,
        }
        encoded = _json_bytes(state) + b"\n"
        if len(encoded) > MAX_PERSISTED_STATE_BYTES:
            raise PersistenceLimitError(
                f"mission metadata exceeds {MAX_PERSISTED_STATE_BYTES} bytes"
            )
        return state

    @staticmethod
    def _atomic_write(target: Path, raw: bytes) -> None:
        temporary = target.with_name(target.name + ".tmp")
        if target.exists() or target.is_symlink():
            _secure_file(target, fix_mode=True)
        if temporary.exists() or temporary.is_symlink():
            _secure_file(temporary, fix_mode=False)
            temporary.unlink()
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, target)
            target.chmod(0o600)
            _fsync_directory(target.parent)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _persist(self, *, raise_errors: bool = False) -> bool:
        try:
            with self._lock:
                payload_raw = self._payload_blob_locked()
                result_raw = self._result_blob_locked()
                state = self._state_locked(payload_raw, result_raw)
                encoded = _json_bytes(state) + b"\n"
                if len(encoded) > MAX_PERSISTED_STATE_BYTES:
                    raise PersistenceLimitError("mission state exceeds persistence limit")
                directory = self._dir()
                events_path = directory / "events.jsonl"
                events_bytes = events_path.stat().st_size if events_path.is_file() else 0
                payload_bytes = (
                    len(payload_raw)
                    if payload_raw is not None
                    else int((self._payload_ref or {}).get("size_bytes") or 0)
                )
                result_bytes = (
                    len(result_raw)
                    if result_raw is not None
                    else int((self._result_ref or {}).get("size_bytes") or 0)
                )
                durable_bytes = (
                    len(encoded)
                    + payload_bytes
                    + result_bytes
                    + events_bytes
                )
                if durable_bytes > MAX_MISSION_DURABLE_BYTES:
                    raise PersistenceLimitError(
                        f"mission durable state exceeds {MAX_MISSION_DURABLE_BYTES} bytes"
                    )
                current_mission_bytes = _tree_durable_bytes(directory)
                projected_store_bytes = (
                    max(0, _store_durable_bytes() - current_mission_bytes)
                    + durable_bytes
                    + 4096
                )
                if projected_store_bytes > MAX_STORE_DURABLE_BYTES:
                    raise PersistenceLimitError(
                        f"mission store exceeds {MAX_STORE_DURABLE_BYTES} bytes"
                    )
                if payload_raw is not None:
                    new_ref = self._blob_ref("payload.json", payload_raw)
                    if new_ref != self._payload_ref or not (directory / "payload.json").is_file():
                        self._atomic_write(directory / "payload.json", payload_raw)
                    self._payload_ref = new_ref
                if result_raw is not None:
                    new_ref = self._blob_ref("result.json", result_raw)
                    if new_ref != self._result_ref or not (directory / "result.json").is_file():
                        self._atomic_write(directory / "result.json", result_raw)
                    self._result_ref = new_ref
                self._atomic_write(directory / "mission.json", encoded)
                if self._payload_loaded and self._payload is None:
                    (directory / "payload.json").unlink(missing_ok=True)
                    self._payload_ref = None
                if self._result_loaded and self._result is None:
                    (directory / "result.json").unlink(missing_ok=True)
                    self._result_ref = None
                _fsync_directory(directory)
            return True
        except (OSError, PersistenceLimitError, PayloadTooLargeError, ResultTooLargeError, ValueError):
            if raise_errors:
                raise
            return False

    def set_payload(self, payload: dict[str, Any] | None) -> None:
        """Atomically persist an exact resumable request before work is launched."""
        with self._lock:
            self._load_payload_locked()
            previous = self._payload
            previous_hash = self.request_sha256
            previous_ref = self._payload_ref
            if payload is None:
                candidate = None
                candidate_hash = None
            else:
                candidate, raw = _exact_json_object(payload)
                if len(raw) > MAX_PERSISTED_PAYLOAD_BYTES:
                    raise PayloadTooLargeError(
                        f"mission payload exceeds {MAX_PERSISTED_PAYLOAD_BYTES} bytes"
                    )
                candidate_hash = request_sha256(candidate)
            self._payload = candidate
            self._payload_loaded = True
            self.request_sha256 = candidate_hash
            try:
                # This also checks the payload plus all snapshot metadata against
                # the stricter total-state limit.
                self._persist(raise_errors=True)
            except (OSError, PersistenceLimitError) as exc:
                self._payload = previous
                self.request_sha256 = previous_hash
                self._payload_ref = previous_ref
                if isinstance(exc, PersistenceLimitError):
                    raise PayloadTooLargeError(str(exc)) from exc
                raise

    def complete_result(self, verdict: dict[str, Any]) -> str:
        """Persist a terminal verdict without ever compacting an accepted result.

        A deliverable that cannot fit is converted to a durable ``blocked``
        verdict.  The original verdict object is never modified and an
        ``accepted`` status is not exposed unless its complete JSON body has
        been committed to ``result.json``.
        """
        with self._lock:
            rejection: Exception | None = None
            try:
                if not isinstance(verdict, dict):
                    raise ValueError("mission result must be a JSON object")
                raw = _json_bytes(verdict)
                if len(raw) > MAX_PERSISTED_RESULT_BYTES:
                    raise ResultTooLargeError(
                        f"mission result exceeds {MAX_PERSISTED_RESULT_BYTES} bytes"
                    )
                self.result = verdict
                verdict_status = str(verdict.get("status") or "")
                if verdict_status not in TERMINAL_STATUSES:
                    verdict_status = "done" if verdict.get("accepted") else "failed"
                self.status = verdict_status
                self.record("status", {"status": verdict_status})
                self._persist(raise_errors=True)
                return verdict_status
            except (TypeError, ValueError, OSError, PersistenceLimitError) as exc:
                rejection = exc

            self.result = {
                "status": "blocked",
                "accepted": False,
                "error": _bounded_text(
                    f"complete mission result could not be persisted: {rejection}", 4096
                ),
            }
            self.status = "blocked"
            self.record("result_rejected", {"error": self.result["error"]})
            self.record("status", {"status": "blocked"})
            self._persist(raise_errors=True)
            return "blocked"

    def _bounded_event(self, event: dict[str, Any]) -> dict[str, Any]:
        try:
            raw = _json_bytes(event)
        except (TypeError, ValueError):
            raw = repr(event).encode("utf-8", errors="replace")
        if len(raw) <= MAX_EVENT_BYTES:
            return event
        compact: dict[str, Any] = {
            "at": str(event.get("at") or ""),
            "type": _bounded_text(event.get("type") or "event", 128),
            "compacted": True,
            "original_size_bytes": len(raw),
            "original_sha256": hashlib.sha256(raw).hexdigest(),
        }
        for key in ("status", "error", "question", "answer"):
            if key in event:
                compact[key] = _bounded_text(event[key], min(4096, MAX_EVENT_BYTES // 2))
        # Extremely small test/config limits still get a valid bounded record.
        if len(_json_bytes(compact)) > MAX_EVENT_BYTES:
            compact = {
                "type": _bounded_text(event.get("type") or "event", 32),
                "compacted": True,
            }
        return compact

    def _rewrite_events_locked(self) -> None:
        limit = max(0, int(MAX_EVENT_FILE_BYTES))
        chosen: list[bytes] = []
        used = 0
        for event in reversed(self.events):
            line = _json_bytes(event) + b"\n"
            if len(line) > limit or used + len(line) > limit:
                continue
            chosen.append(line)
            used += len(line)
        encoded = b"".join(reversed(chosen))
        target = self._dir() / "events.jsonl"
        self._atomic_write(target, encoded)

    def record(self, etype: str, data: dict[str, Any] | None = None) -> None:
        event = {
            **(data or {}),
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "type": _bounded_text(etype, 128),
        }
        with self._lock:
            event = self._bounded_event(event)
            self.events.append(event)
            memory_limit = max(0, int(MAX_EVENTS_IN_MEMORY))
            if memory_limit == 0:
                self.events = []
            elif len(self.events) > memory_limit:
                self.events = self.events[-memory_limit:]
            self.updated = time.time()
            try:
                self._rewrite_events_locked()
            except OSError:
                pass

    def set_status(self, status: str) -> None:
        with self._lock:
            try:
                self.status = str(status)
                self.record("status", {"status": self.status})
                self._persist(raise_errors=True)
            except Exception as exc:
                self._mark_persistence_failure_locked(exc)
                raise MissionPersistenceError(str(self.storage_error)) from exc

    def ask_user(self, question: str, timeout: float = 3600) -> str:
        """Wait for a user answer, cancellation, or timeout."""
        with self._lock:
            self.question = _bounded_text(question)
            self.answer = None
            self._answer_ev.clear()
        self.set_status("needs_user")
        self.record("question", {"question": self.question})
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.cancelled.is_set():
                return ""
            if self._answer_ev.wait(timeout=min(1.0, max(0.0, deadline - time.time()))):
                break
        if self.cancelled.is_set():
            return ""
        with self._lock:
            answer = self.answer or ""
            self.question = None
        self.set_status("running")
        self.record("answer", {"answer": _bounded_text(answer, 500)})
        return answer

    def provide_answer(self, text: str) -> bool:
        with self._lock:
            if self.status != "needs_user":
                return False
            previous = self.answer
            self.answer = _bounded_text(text)
            self.updated = time.time()
            try:
                self._persist(raise_errors=True)
            except (OSError, PersistenceLimitError, ValueError):
                self.answer = previous
                return False
            # Wake the worker only after the answer is durably committed.
            self._answer_ev.set()
            return True

    def snapshot(self, event_limit: int = 0, *, include_result: bool = True) -> dict[str, Any]:
        with self._lock:
            events = self.events[-event_limit:] if event_limit else list(self.events)
            snapshot = {
                "id": self.id,
                "status": self.status,
                "question": self.question,
                "request_sha256": self.request_sha256,
                "attempt": self.attempt,
                "revision_turns": [dict(turn) for turn in self.revision_turns],
                "inflight": self.inflight,
                "cleanup_complete": self.cleanup_complete,
                "events": events,
                "created": self.created,
                "updated": self.updated,
            }
            snapshot["result"] = self.result if include_result else None
            return snapshot

    def events_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.events)


_MISSIONS: dict[str, Mission] = {}
_GLOCK = threading.RLock()


def _remove_mission_dir(mission_id: str) -> bool:
    try:
        raw = STORE_ROOT.resolve() / mission_id
        if raw.is_symlink():
            raw.unlink(missing_ok=True)
            return True
        directory = _mission_path(mission_id)
        if directory.is_dir():
            shutil.rmtree(directory)
            _fsync_directory(_ensure_store_root())
        return not directory.exists() and not directory.is_symlink()
    except (OSError, ValueError, PermissionError):
        return False


def _prune_locked(now: float | None = None) -> list[str]:
    """Remove expired/excess terminal missions; active missions are ineligible."""
    current = time.time() if now is None else float(now)
    terminal: list[tuple[float, str, Mission]] = []
    for mission_id, mission in list(_MISSIONS.items()):
        with mission._lock:
            if (
                mission.status not in TERMINAL_STATUSES
                or mission.inflight
                or (not mission.cleanup_complete and not mission._gc_after_restart)
            ):
                continue
            terminal.append((mission.updated, mission_id, mission))

    terminal.sort(key=lambda item: (item[0], item[1]), reverse=True)
    expired = {
        mission_id
        for updated, mission_id, _mission in terminal
        if current - updated > max(0.0, float(TERMINAL_TTL_SECONDS))
    }
    survivors = [item for item in terminal if item[1] not in expired]
    keep_count = max(0, int(MAX_TERMINAL_MISSIONS))
    excess = {mission_id for _updated, mission_id, _mission in survivors[keep_count:]}
    candidates = expired | excess
    kept_bytes = 0
    for _updated, mission_id, _mission in survivors:
        if mission_id in candidates:
            continue
        mission_bytes = _tree_durable_bytes(STORE_ROOT / mission_id)
        if kept_bytes + mission_bytes > max(0, int(MAX_TERMINAL_DURABLE_BYTES)):
            candidates.add(mission_id)
        else:
            kept_bytes += mission_bytes
    removed: list[str] = []
    for _updated, mission_id, mission in reversed(terminal):
        if mission_id not in candidates:
            continue
        # Re-check while holding the mission lock so a concurrent resume cannot
        # cross the terminal-to-active boundary during deletion.
        with mission._lock:
            if (
                mission.status not in TERMINAL_STATUSES
                or mission.inflight
                or (not mission.cleanup_complete and not mission._gc_after_restart)
            ):
                continue
            if _MISSIONS.get(mission_id) is not mission:
                continue
            if not _remove_mission_dir(mission_id):
                continue
            _MISSIONS.pop(mission_id, None)
            removed.append(mission_id)
    return removed


def prune(now: float | None = None) -> list[str]:
    with _GLOCK:
        return _prune_locked(now)


def create(
    mission_id: str,
    goal: str,
    payload: dict[str, Any] | None = None,
) -> Mission:
    """Atomically reserve an id and, when supplied, persist its exact request.

    The mission is inserted into ``_MISSIONS`` only after both the initial state
    and payload/hash validation have succeeded.  A rejected payload therefore
    leaves neither a GET-visible object nor a stale on-disk reservation.
    """
    if not valid_mission_id(mission_id):
        raise ValueError("invalid mission_id")
    with _GLOCK:
        _prune_locked()
        if mission_id in _MISSIONS:
            raise MissionExistsError(f"mission already exists: {mission_id}")
        store_root = _ensure_store_root()
        directory = _mission_path(mission_id)
        if directory.exists() or directory.is_symlink():
            raise MissionExistsError(f"mission already exists: {mission_id}")
        mission = Mission(mission_id, goal)
        payload_raw: bytes | None = None
        if payload is not None:
            candidate, raw = _exact_json_object(payload)
            if len(raw) > MAX_PERSISTED_PAYLOAD_BYTES:
                raise PayloadTooLargeError(
                    f"mission payload exceeds {MAX_PERSISTED_PAYLOAD_BYTES} bytes"
                )
            mission._payload = candidate
            mission.request_sha256 = request_sha256(candidate)
            payload_raw = raw
            try:
                with mission._lock:
                    mission._state_locked()
            except PersistenceLimitError as exc:
                raise PayloadTooLargeError(str(exc)) from exc
        if _active_count_locked() >= max(1, int(MAX_ACTIVE_MISSIONS)):
            raise MissionCapacityError("the bounded mission worker queue is full")
        if _store_entry_count() >= max(1, int(MAX_STORE_MISSIONS)):
            raise MissionCapacityError("the bounded mission store count is full")
        with mission._lock:
            initial_state = _json_bytes(mission._state_locked(payload_raw, None)) + b"\n"
        estimated_bytes = len(initial_state) + len(payload_raw or b"") + 4096
        if _store_durable_bytes() + estimated_bytes > MAX_STORE_DURABLE_BYTES:
            raise MissionCapacityError("the bounded mission store byte budget is full")
        try:
            # Exclusive mkdir is also the cross-process id reservation.
            directory.mkdir(mode=0o700, parents=False, exist_ok=False)
            directory.chmod(0o700)
            _fsync_directory(store_root)
        except FileExistsError as exc:
            raise MissionExistsError(f"mission already exists: {mission_id}") from exc
        try:
            mission._persist(raise_errors=True)
        except PersistenceLimitError as exc:
            shutil.rmtree(directory, ignore_errors=True)
            _fsync_directory(store_root)
            if "mission store" in str(exc):
                raise MissionCapacityError(str(exc)) from exc
            raise PayloadTooLargeError(str(exc)) from exc
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            _fsync_directory(store_root)
            raise
        _MISSIONS[mission_id] = mission
        return mission


def get(mission_id: str) -> Mission | None:
    if not valid_mission_id(mission_id):
        return None
    with _GLOCK:
        return _MISSIONS.get(mission_id)


def run_async(mission: Mission, fn: Callable[[Mission], dict[str, Any]]) -> None:
    """Run ``fn`` and automatically feed actionable failures into a new attempt."""

    with mission._lock:
        if mission.inflight:
            raise RuntimeError("mission worker is already active")
        # Mark this synchronously so cancel/GC cannot see a terminal mission in
        # the interval before the new thread starts.
        mission.inflight = True
        mission.cleanup_complete = False
        # A verified candidate waiting only for its canonical wiki commit is
        # not a new coding attempt.  Keep the coding/revision budget untouched.
        checkpoint_only_resume = _task_checkpoint_commit_pending(mission.result)
        if not checkpoint_only_resume:
            mission.attempt += 1
        try:
            mission._persist(raise_errors=True)
        except Exception as exc:
            mission.inflight = False
            mission._mark_persistence_failure_locked(exc)
            raise MissionPersistenceError(str(mission.storage_error)) from exc

    def _run() -> None:
        cleanup_proven = True
        verdict: dict[str, Any] | None = None
        try:
            with mission._lock:
                if mission.cancelled.is_set():
                    mission.result = _cancelled_result(mission.result)
                    return
                mission.set_status("running")
            while True:
                verdict = fn(mission)
                if not isinstance(verdict, dict):
                    raise TypeError("mission worker returned a non-object verdict")
                if verdict.get("cleanup_complete") is False:
                    cleanup_proven = False
                    verdict = {
                        **verdict,
                        "status": "blocked",
                        "accepted": False,
                        "cleanup_complete": False,
                    }
                if mission.cancelled.is_set() and cleanup_proven:
                    with mission._lock:
                        mission.result = _cancelled_result(verdict)
                    return

                if _task_checkpoint_commit_pending(verdict):
                    checkpoint_attempts = _task_checkpoint_commit_attempts(verdict)
                    can_retry_checkpoint = bool(
                        cleanup_proven
                        and checkpoint_attempts < MAX_TASK_CHECKPOINT_COMMIT_ATTEMPTS
                    )
                    if not can_retry_checkpoint:
                        durable_pending = {
                            **verdict,
                            "status": "failed",
                            "accepted": False,
                            "retryable": True,
                            "revision_required": False,
                            "task_checkpoint_retry_exhausted": True,
                            "summary": _bounded_text(
                                "The verified candidate is preserved, but its canonical "
                                "task-page commit is still pending. POST resume will retry "
                                "only that idempotent commit; coding will not restart.",
                                8_000,
                            ),
                        }
                        with mission._lock:
                            mission.complete_result(durable_pending)
                        break

                    delay = _task_checkpoint_retry_delay(checkpoint_attempts)
                    retry_at = time.time() + delay
                    durable_pending = {
                        **verdict,
                        "status": "failed",
                        "accepted": False,
                        "retryable": True,
                        "revision_required": False,
                        "task_checkpoint_retry_after_seconds": delay,
                        "task_checkpoint_retry_at": retry_at,
                    }
                    with mission._lock:
                        mission.result = durable_pending
                        mission.status = "queued"
                        mission.record(
                            "task_checkpoint_retry_scheduled",
                            {
                                "checkpoint_attempt": checkpoint_attempts,
                                "next_checkpoint_attempt": checkpoint_attempts + 1,
                                "retry_after_seconds": delay,
                                "retry_at": retry_at,
                            },
                        )
                        mission._persist(raise_errors=True)
                    if mission.cancelled.wait(delay):
                        return
                    with mission._lock:
                        mission.set_status("running")
                    continue

                turn = _revision_turn(verdict, mission.attempt)
                can_retry = bool(
                    turn
                    and cleanup_proven
                    and mission.attempt < MAX_AUTO_REVISION_ATTEMPTS
                    and len(mission.revision_turns) < MAX_REVISION_TURNS
                    and _revision_time_available(mission)
                )
                if not can_retry:
                    if verdict.get("revision_required") is True and not verdict.get("accepted"):
                        verdict = {
                            **verdict,
                            "status": "failed",
                            "accepted": False,
                            "revision_exhausted": True,
                            "revision_attempts": mission.attempt,
                            "summary": _bounded_text(
                                str(verdict.get("summary") or "Code verification failed.")
                                + " Autonomous repair approaches were exhausted; "
                                "the actionable findings are preserved in this result.",
                                8_000,
                            ),
                        }
                    with mission._lock:
                        mission.complete_result(verdict)
                    break

                assert turn is not None
                with mission._lock:
                    mission.revision_turns.append(turn)
                    mission.result = verdict
                    mission.status = "queued"
                    mission.record(
                        "revision_scheduled",
                        {
                            "attempt": mission.attempt,
                            "next_attempt": mission.attempt + 1,
                            "decision_owner": turn["decision_owner"],
                            "result_sha256": turn["result_sha256"],
                            "finding_codes": [
                                item["code"] for item in turn["findings"]
                            ],
                        },
                    )
                    mission.attempt += 1
                    mission._persist(raise_errors=True)
                    mission.set_status("running")
        except Exception as exc:  # noqa: BLE001
            with mission._lock:
                if mission.cancelled.is_set() and cleanup_proven:
                    mission.result = _cancelled_result(
                        verdict if isinstance(verdict, dict) else mission.result
                    )
                elif not cleanup_proven:
                    mission.result = {
                        "status": "blocked",
                        "accepted": False,
                        "cleanup_complete": False,
                        "error": _bounded_text(
                            f"sandbox cleanup could not be proven: {type(exc).__name__}: {exc}",
                            4096,
                        ),
                    }
                    mission.status = "blocked"
                else:
                    mission.result = {
                        "status": "failed",
                        "accepted": False,
                        "error": _bounded_text(f"{type(exc).__name__}: {exc}", 4096),
                    }
                    mission.set_status("failed")
            mission.record("error", {"error": _bounded_text(exc, 4096)})
        finally:
            with mission._lock:
                mission.inflight = False
                mission.cleanup_complete = cleanup_proven
                try:
                    if mission.cancelled.is_set() and cleanup_proven:
                        mission.question = None
                        mission.result = _cancelled_result(mission.result)
                        mission.set_status("cancelled")
                    elif not cleanup_proven:
                        mission.question = None
                        current = mission.result
                        if not isinstance(current, dict) or current.get("accepted"):
                            mission.result = {
                                "status": "blocked",
                                "accepted": False,
                                "cleanup_complete": False,
                                "error": "sandbox cleanup could not be proven",
                            }
                        mission.status = "blocked"
                        mission._persist(raise_errors=True)
                    else:
                        mission._persist(raise_errors=True)
                except Exception as exc:
                    mission._mark_persistence_failure_locked(exc)
            prune()

    worker = threading.Thread(target=_run, daemon=True, name=f"mission-{mission.id}")
    try:
        worker.start()
    except Exception as exc:
        with mission._lock:
            mission.inflight = False
            mission._mark_persistence_failure_locked(exc)
        raise MissionPersistenceError(str(mission.storage_error)) from exc


def create_and_run(
    mission_id: str,
    goal: str,
    payload: dict[str, Any],
    fn: Callable[[Mission], dict[str, Any]],
    *,
    on_created: Callable[[Mission], None] | None = None,
) -> Mission:
    """Atomically publish a fully persisted mission with its worker already started."""
    with _GLOCK:
        mission = create(mission_id, goal, payload=payload)
        try:
            if on_created is not None:
                on_created(mission)
            run_async(mission, fn)
        except Exception:
            if _MISSIONS.get(mission_id) is mission:
                _MISSIONS.pop(mission_id, None)
                _remove_mission_dir(mission_id)
            raise
        return mission


def resume(
    mission_id: str,
    fn: Callable[[Mission], dict[str, Any]],
    *,
    expected: Mission | None = None,
    require_payload: bool = False,
    preserve_result: bool = False,
) -> bool:
    """Restart a stopped, healthy persisted mission while retaining its journal."""
    if not valid_mission_id(mission_id):
        return False
    with _GLOCK:
        mission = _MISSIONS.get(mission_id)
        if not mission or (expected is not None and mission is not expected):
            return False
        with mission._lock:
            if require_payload:
                mission._load_payload_locked()
            if (
                mission.status in ACTIVE_STATUSES
                or mission._resume_disabled
                or mission.inflight
                or not mission.cleanup_complete
                or (require_payload and not isinstance(mission._payload, dict))
            ):
                return False
            if _active_count_locked() >= max(1, int(MAX_ACTIVE_MISSIONS)):
                return False
            from_status = mission.status
            mission.cancelled = threading.Event()
            mission._answer_ev = threading.Event()
            mission.answer = None
            mission.question = None
            if not preserve_result:
                mission.result = None
            mission.cleanup_complete = True
            mission.record("resume", {"from_status": from_status})
            # Make it GC-ineligible before releasing the global lock.
            mission.set_status("queued")
            run_async(mission, fn)
            return True


def prepare_restart_salvage(
    mission_id: str,
    *,
    expected: Mission | None = None,
) -> bool:
    """Unlock a restart envelope only after the service proved a boundary sweep."""
    if not valid_mission_id(mission_id):
        return False
    with _GLOCK:
        mission = _MISSIONS.get(mission_id)
        if not mission or (expected is not None and mission is not expected):
            return False
        with mission._lock:
            result = mission.result if isinstance(mission.result, dict) else {}
            if (
                mission.status != "blocked"
                or result.get("restart_recovery_required") is not True
                or not (
                    _valid_restart_workspace_checkpoint(
                        result.get("workspace_checkpoint")
                    )
                    or _valid_restart_pending_task_checkpoint(result)
                )
            ):
                return False
            mission.cleanup_complete = True
            mission._resume_disabled = False
            mission._gc_after_restart = False
            mission.record("restart_boundary_swept", {
                "checkpoint_only": result.get("error_code") == "task_checkpoint_commit_pending",
            })
            mission._persist(raise_errors=True)
            return True


def cancel(mission_id: str, *, expected: Mission | None = None) -> bool:
    if not valid_mission_id(mission_id):
        return False
    with _GLOCK:
        mission = _MISSIONS.get(mission_id)
        if (
            not mission
            or (expected is not None and mission is not expected)
            or mission.status in TERMINAL_STATUSES
        ):
            return False
        with mission._lock:
            mission.cancelled.set()
            mission._answer_ev.set()
            if mission.inflight:
                mission.cleanup_complete = False
                mission.set_status("cancelling")
            else:
                mission.cleanup_complete = True
                mission.set_status("cancelled")
        _prune_locked()
        return True


def _blocked_metadata(mission_id: str, reason: str, updated: float | None = None) -> Mission:
    mission = Mission(mission_id, "")
    mission.status = "blocked"
    mission.storage_error = _bounded_text(reason, 4096)
    mission._resume_disabled = True
    mission.result = {
        "status": "blocked",
        "accepted": False,
        "error": mission.storage_error,
    }
    mission.updated = time.time()
    mission.created = min(mission.updated, updated or mission.updated)
    mission.events = [
        mission._bounded_event(
            {
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "type": "storage_blocked",
                "error": mission.storage_error,
            }
        )
    ]
    return mission


def _load_events(mission: Mission, events_path: Path) -> str | None:
    if not events_path.is_file():
        return None
    try:
        _secure_file(events_path, fix_mode=True)
        if events_path.stat().st_size > MAX_EVENT_FILE_BYTES:
            return f"event journal exceeds {MAX_EVENT_FILE_BYTES} bytes"
        events: list[dict[str, Any]] = []
        for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # A torn final append (or one corrupt line) does not hide later
                # valid events and does not destroy an otherwise resumable request.
                continue
            if isinstance(event, dict):
                events.append(mission._bounded_event(event))
        memory_limit = max(0, int(MAX_EVENTS_IN_MEMORY))
        mission.events = events[-memory_limit:] if memory_limit else []
    except (OSError, ValueError) as exc:
        return f"event journal cannot be read: {exc}"
    return None


def _validate_json_blob(
    directory: Path,
    reference: Any,
    expected_name: str,
    max_bytes: int,
) -> tuple[dict[str, Any] | None, int, str | None]:
    if not isinstance(reference, dict):
        return None, 0, f"{expected_name} reference is invalid"
    if reference.get("path") != expected_name:
        return None, 0, f"{expected_name} reference path is invalid"
    target = directory / expected_name
    try:
        _secure_file(target, fix_mode=True)
        actual_size = target.stat().st_size
        try:
            declared_size = int(reference.get("size_bytes"))
        except (TypeError, ValueError):
            return None, actual_size, f"{expected_name} size metadata is invalid"
        if actual_size != declared_size:
            return None, actual_size, f"{expected_name} size does not match metadata"
        if actual_size > max_bytes:
            return None, actual_size, f"{expected_name} exceeds {max_bytes} bytes"
        digest = hashlib.sha256()
        with open(target, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        if digest.hexdigest() != str(reference.get("sha256") or ""):
            return None, actual_size, f"{expected_name} hash does not match metadata"
        return dict(reference), actual_size, None
    except (OSError, ValueError) as exc:
        return None, 0, f"{expected_name} cannot be read: {exc}"


def _read_json_blob(
    directory: Path,
    reference: dict[str, Any],
    expected_name: str,
) -> tuple[dict[str, Any] | None, str | None]:
    max_bytes = (
        MAX_PERSISTED_PAYLOAD_BYTES
        if expected_name == "payload.json"
        else MAX_PERSISTED_RESULT_BYTES
    )
    _validated, _size, error = _validate_json_blob(
        directory, reference, expected_name, max_bytes
    )
    if error:
        return None, error
    try:
        value = json.loads((directory / expected_name).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return None, f"{expected_name} is not a JSON object"
        return value, None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"{expected_name} cannot be decoded: {exc}"


def _rehydrate() -> None:
    """Load bounded state; represent corrupt/oversized entries as blocked records."""
    if not STORE_ROOT.is_dir():
        return
    try:
        _secure_directory(STORE_ROOT, fix_mode=True)
    except (OSError, ValueError, PermissionError) as exc:
        raise RuntimeError(f"unsafe mission store root: {exc}") from exc
    with _GLOCK:
        entries: list[Path] = []
        try:
            with os.scandir(STORE_ROOT) as iterator:
                for entry in iterator:
                    if len(entries) >= max(1, int(MAX_STORE_MISSIONS)):
                        raise RuntimeError(
                            f"mission store contains more than {MAX_STORE_MISSIONS} entries"
                        )
                    entries.append(Path(entry.path))
        except OSError:
            return
        entries.sort(key=lambda path: path.name)
        durable_store_bytes = _store_durable_bytes()
        if durable_store_bytes > MAX_STORE_DURABLE_BYTES:
            raise RuntimeError(
                f"mission store exceeds {MAX_STORE_DURABLE_BYTES} bytes"
            )
        for directory in entries:
            mission_id = directory.name
            if not valid_mission_id(mission_id) or mission_id in _MISSIONS:
                continue
            state_path = directory / "mission.json"
            try:
                _secure_directory(directory, fix_mode=True)
                if not state_path.is_file():
                    raise ValueError("mission.json is missing")
                _secure_file(state_path, fix_mode=True)
                state_size = state_path.stat().st_size
                if state_size > MAX_PERSISTED_STATE_BYTES:
                    raise PersistenceLimitError(
                        f"mission.json exceeds {MAX_PERSISTED_STATE_BYTES} bytes"
                    )
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if not isinstance(state, dict):
                    raise ValueError("mission.json is not an object")
                stored_id = str(state.get("id") or mission_id)
                if stored_id != mission_id or not valid_mission_id(stored_id):
                    raise ValueError("mission id does not match its directory")
            except (OSError, ValueError, json.JSONDecodeError, PersistenceLimitError) as exc:
                stamp = None
                try:
                    stamp = state_path.stat().st_mtime
                except OSError:
                    pass
                _MISSIONS[mission_id] = _blocked_metadata(
                    mission_id, f"persisted mission is unavailable: {exc}", stamp
                )
                continue

            now = time.time()
            mission = Mission(mission_id, str(state.get("goal") or ""))
            mission.status = str(state.get("status") or "blocked")
            mission.inflight = bool(state.get("inflight", mission.status in ACTIVE_STATUSES))
            mission.cleanup_complete = bool(
                state.get("cleanup_complete", not mission.inflight)
            )
            mission.question = _bounded_text(state.get("question")) if state.get("question") else None
            mission.answer = _bounded_text(state.get("answer")) if state.get("answer") else None
            mission.created = _safe_timestamp(state.get("created"), now)
            mission.updated = _safe_timestamp(state.get("updated"), mission.created)
            mission.storage_error = (
                _bounded_text(state.get("storage_error"), 4096) if state.get("storage_error") else None
            )

            revision_problem: str | None = None
            stored_attempt = state.get("attempt", 0)
            if type(stored_attempt) is not int or stored_attempt < 0:
                revision_problem = "persisted mission attempt is invalid"
            else:
                mission.attempt = stored_attempt
            stored_turns = state.get("revision_turns", [])
            if not isinstance(stored_turns, list) or len(stored_turns) > MAX_REVISION_TURNS:
                revision_problem = revision_problem or "persisted revision history is invalid"
            else:
                restored_turns: list[dict[str, Any]] = []
                for raw_turn in stored_turns:
                    if (
                        not isinstance(raw_turn, dict)
                        or set(raw_turn) != {
                            "attempt", "result_sha256", "decision_owner",
                            "leader_order", "findings",
                        }
                        or type(raw_turn.get("attempt")) is not int
                        or raw_turn["attempt"] < 1
                        or not re.fullmatch(
                            r"[0-9a-f]{64}", str(raw_turn.get("result_sha256") or "")
                        )
                        or raw_turn.get("decision_owner") not in {
                            "Ceraxia", "SkitariiWarband",
                        }
                        or type(raw_turn.get("leader_order")) is not str
                        or not raw_turn["leader_order"].strip()
                        or len(raw_turn["leader_order"].encode("utf-8")) > 8_000
                    ):
                        revision_problem = "persisted revision turn is invalid"
                        break
                    findings = _revision_findings(raw_turn.get("findings"))
                    if not findings:
                        revision_problem = "persisted revision findings are invalid"
                        break
                    restored_turns.append({**raw_turn, "findings": findings})
                if revision_problem is None:
                    mission.revision_turns = restored_turns

            storage_problem: str | None = revision_problem
            payload_size = 0
            result_size = 0
            stored_payload: Any = state.get("payload")
            payload_is_external = state.get("payload_ref") is not None
            if payload_is_external:
                payload_ref, payload_size, storage_problem = _validate_json_blob(
                    directory,
                    state.get("payload_ref"),
                    "payload.json",
                    MAX_PERSISTED_PAYLOAD_BYTES,
                )
                if payload_ref is not None:
                    mission._payload = None
                    mission._payload_loaded = False
                    mission._payload_ref = payload_ref
                    stored_payload = None
                    stored_hash = str(state.get("request_sha256") or "")
                    if not re.fullmatch(r"[0-9a-f]{64}", stored_hash):
                        storage_problem = storage_problem or (
                            "request_sha256 is missing or invalid"
                        )
                    else:
                        mission.request_sha256 = stored_hash
            if not payload_is_external and stored_payload is not None:
                if not isinstance(stored_payload, dict):
                    storage_problem = "persisted payload is not a JSON object"
                else:
                    try:
                        exact_payload, payload_raw = _exact_json_object(stored_payload)
                        mission._payload = exact_payload
                        mission.request_sha256 = request_sha256(exact_payload)
                        if payload_is_external:
                            payload_size = payload_size or len(payload_raw)
                        if len(payload_raw) > MAX_PERSISTED_PAYLOAD_BYTES:
                            storage_problem = (
                                f"persisted payload exceeds {MAX_PERSISTED_PAYLOAD_BYTES} bytes"
                            )
                        stored_hash = state.get("request_sha256")
                        if stored_hash and str(stored_hash) != mission.request_sha256:
                            storage_problem = "persisted request_sha256 does not match payload"
                    except (TypeError, ValueError) as exc:
                        storage_problem = f"persisted payload is invalid: {exc}"
            elif not payload_is_external and state.get("request_sha256"):
                storage_problem = storage_problem or (
                    "request_sha256 exists without a persisted payload"
                )

            stored_result: Any = state.get("result")
            result_problem: str | None = None
            result_is_external = state.get("result_ref") is not None
            if result_is_external:
                result_ref, result_size, result_problem = _validate_json_blob(
                    directory,
                    state.get("result_ref"),
                    "result.json",
                    MAX_PERSISTED_RESULT_BYTES,
                )
                if result_ref is not None:
                    mission._result = None
                    mission._result_loaded = False
                    mission._result_ref = result_ref
                    stored_result = None
            if result_problem:
                storage_problem = storage_problem or result_problem
            elif not result_is_external and stored_result is not None:
                if not isinstance(stored_result, dict):
                    storage_problem = storage_problem or "persisted result is not a JSON object"
                else:
                    try:
                        result_raw = _json_bytes(stored_result)
                        if len(result_raw) > MAX_PERSISTED_RESULT_BYTES:
                            storage_problem = storage_problem or (
                                f"persisted result exceeds {MAX_PERSISTED_RESULT_BYTES} bytes"
                            )
                        else:
                            mission.result = stored_result
                            if result_is_external:
                                result_size = result_size or len(result_raw)
                    except (TypeError, ValueError) as exc:
                        storage_problem = storage_problem or f"persisted result is invalid: {exc}"

            events_path = directory / "events.jsonl"
            event_problem = _load_events(mission, events_path)
            storage_problem = storage_problem or event_problem
            try:
                event_size = events_path.stat().st_size if events_path.is_file() else 0
            except OSError:
                event_size = 0
            if state_size + payload_size + result_size + event_size > MAX_MISSION_DURABLE_BYTES:
                storage_problem = storage_problem or (
                    f"mission durable state exceeds {MAX_MISSION_DURABLE_BYTES} bytes"
                )
            if mission.status not in ACTIVE_STATUSES | TERMINAL_STATUSES:
                storage_problem = storage_problem or f"unknown persisted status: {mission.status}"

            _MISSIONS[mission_id] = mission
            if storage_problem or mission.storage_error:
                was_inflight = mission.inflight
                mission.status = "blocked"
                mission.storage_error = _bounded_text(storage_problem or mission.storage_error, 4096)
                mission._resume_disabled = True
                mission.inflight = False
                if was_inflight or not mission.cleanup_complete:
                    mission.cleanup_complete = False
                    mission._gc_after_restart = True
                mission.question = None
                mission.result = {
                    "status": "blocked",
                    "accepted": False,
                    "error": mission.storage_error,
                }
                mission.updated = time.time()
                mission.events.append(
                    mission._bounded_event(
                        {
                            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "type": "storage_blocked",
                            "error": mission.storage_error,
                        }
                    )
                )
                memory_limit = max(0, int(MAX_EVENTS_IN_MEMORY))
                mission.events = mission.events[-memory_limit:] if memory_limit else []
            elif (
                mission.status in ACTIVE_STATUSES
                or mission.inflight
                or not mission.cleanup_complete
            ):
                prior_result = (
                    dict(mission.result) if isinstance(mission.result, dict) else {}
                )
                has_recovery = bool(
                    _valid_restart_workspace_checkpoint(
                        prior_result.get("workspace_checkpoint")
                    )
                    or _valid_restart_pending_task_checkpoint(prior_result)
                )
                mission.result = {
                    **prior_result,
                    "status": "blocked",
                    "accepted": False,
                    "error": "service restarted while mission was active",
                    "restart_recovery_required": has_recovery,
                }
                mission.question = None
                mission.inflight = False
                mission.cleanup_complete = False
                mission._resume_disabled = True
                # The crashed worker no longer exists in this process. Its record
                # remains explicitly unclean and non-resumable, but may age out
                # under the bounded retention policy. The VM boundary performs an
                # independent full sweep before the next mission.
                mission._gc_after_restart = True
                mission.set_status("blocked")

        _prune_locked()


_rehydrate()
