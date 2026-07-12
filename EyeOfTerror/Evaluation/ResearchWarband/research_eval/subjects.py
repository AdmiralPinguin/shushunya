"""Bounded JSON subject boundary with an externally owned OS process tree."""

from __future__ import annotations

import copy
import hashlib
import json
import multiprocessing
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen
import uuid

from .process_guard import KillableProcessTree, enter_isolated_group


MAX_CONTROL_BYTES = 4096
MAX_HEALTH_BYTES = 64 * 1024
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
_CALL_ID = re.compile(r"[0-9a-f]{32}\Z")


@dataclass(frozen=True)
class SubjectExecution:
    result: dict[str, Any]
    terminal: bool = True
    cleanup_proven: bool = True
    cleanup_detail: str = "cleanup proven"


class SubjectAdapter(ABC):
    """Spawn-pickleable evaluator adapter; the SUT remains outside this trust boundary."""

    @abstractmethod
    def health(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def execute(self, payload: dict[str, Any], *, timeout_sec: int) -> SubjectExecution:
        raise NotImplementedError


class SubjectBoundaryError(RuntimeError):
    pass


class SubjectTimeoutError(SubjectBoundaryError):
    pass


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _encode_json(value: Any, *, limit: int, context: str) -> bytes:
    try:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise SubjectBoundaryError(f"{context} is not strict JSON: {type(exc).__name__}") from exc
    if len(raw) > limit:
        raise SubjectBoundaryError(f"{context} exceeds {limit} bytes")
    return raw


def _decode_json(raw: bytes, *, context: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise SubjectBoundaryError(f"{context} is malformed strict JSON") from exc
    if not isinstance(value, dict):
        raise SubjectBoundaryError(f"{context} must be a JSON object")
    return value


def _regular_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)


def _write_atomic(path: Path, raw: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _read_bounded(path: Path, limit: int) -> bytes:
    if not _regular_file(path):
        raise SubjectBoundaryError(f"unsafe or missing IPC file: {path.name}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
            raise SubjectBoundaryError(f"IPC file exceeds {limit} bytes")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read(limit + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > limit:
        raise SubjectBoundaryError(f"IPC file exceeds {limit} bytes")
    return raw


def _safe_error(exc: BaseException) -> dict[str, Any]:
    message = str(exc).encode("utf-8", errors="replace")[:1000].decode(
        "utf-8", errors="ignore"
    )
    return {"ok": False, "error_type": type(exc).__name__, "error": message}


def _subject_worker(
    connection: Any,
    subject: SubjectAdapter,
    ipc_root: str,
    ready: Any,
    gate: Any,
) -> None:
    root = Path(ipc_root)
    try:
        enter_isolated_group(ready, gate)
        safe_send_bytes = connection.send_bytes
        while True:
            try:
                command_raw = connection.recv_bytes(MAX_CONTROL_BYTES)
            except (EOFError, OSError):
                return
            command = _decode_json(command_raw, context="subject control frame")
            operation = command.get("operation")
            if operation == "stop":
                safe_send_bytes(b'{"operation":"stopped"}')
                return
            call_id = command.get("call_id")
            response_limit = command.get("response_limit")
            if (
                operation not in {"health", "execute"}
                or type(call_id) is not str
                or not _CALL_ID.fullmatch(call_id)
                or type(response_limit) is not int
                or not 1024 <= response_limit <= MAX_RESPONSE_BYTES
            ):
                raise SubjectBoundaryError("invalid subject control frame")
            request_path = root / f"{call_id}.request.json"
            response_path = root / f"{call_id}.response.json"
            try:
                request = _decode_json(
                    _read_bounded(request_path, MAX_REQUEST_BYTES),
                    context="subject request",
                )
                if operation == "health":
                    value = subject.health()
                    if not isinstance(value, dict):
                        raise TypeError("subject health must return an object")
                    envelope = {"ok": True, "kind": "health", "value": value}
                else:
                    payload = request.get("payload")
                    timeout_sec = request.get("timeout_sec")
                    if not isinstance(payload, dict) or type(timeout_sec) is not int:
                        raise TypeError("execute request is invalid")
                    value = subject.execute(payload, timeout_sec=timeout_sec)
                    if not isinstance(value, SubjectExecution):
                        raise TypeError("subject execute returned an invalid envelope")
                    if type(value.terminal) is not bool or type(value.cleanup_proven) is not bool:
                        raise TypeError("subject lifecycle flags must be boolean")
                    if not isinstance(value.result, dict):
                        raise TypeError("subject result must be an object")
                    envelope = {
                        "ok": True,
                        "kind": "execution",
                        "result": value.result,
                        "terminal": value.terminal,
                        "subject_cleanup_claim": value.cleanup_proven,
                        "cleanup_detail": str(value.cleanup_detail)[:1000],
                    }
                response_raw = _encode_json(
                    envelope,
                    limit=response_limit,
                    context="subject response",
                )
            except BaseException as exc:
                response_raw = _encode_json(
                    _safe_error(exc),
                    limit=min(response_limit, MAX_HEALTH_BYTES),
                    context="subject error response",
                )
            finally:
                request_path.unlink(missing_ok=True)
            _write_atomic(response_path, response_raw)
            notification = _encode_json(
                {"call_id": call_id},
                limit=MAX_CONTROL_BYTES,
                context="subject notification",
            )
            safe_send_bytes(notification)
    except BaseException:
        return
    finally:
        connection.close()


class SubjectProcessBoundary:
    """Persistent subject controller with bounded JSON IPC and kill-tree watchdog."""

    def __init__(self, subject: SubjectAdapter) -> None:
        if not isinstance(subject, SubjectAdapter):
            raise TypeError("subject must implement SubjectAdapter")
        self.subject = subject
        self._ipc = tempfile.TemporaryDirectory(prefix="research-eval-subject-")
        self._ipc_root = Path(self._ipc.name)
        try:
            self._ipc_root.chmod(0o700)
        except OSError:
            pass
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        self._connection = parent
        try:
            self._tree = KillableProcessTree.spawn(
                context,
                target=_subject_worker,
                args=(child, subject, str(self._ipc_root)),
                name="research-eval-subject",
            )
        except BaseException:
            parent.close()
            child.close()
            self._ipc.cleanup()
            raise
        child.close()
        self._closed = False
        self._last_cleanup_proven = True

    def _terminate(self) -> bool:
        if self._closed:
            return self._last_cleanup_proven
        self._last_cleanup_proven = self._tree.terminate(grace_seconds=1.0)
        self._closed = True
        return self._last_cleanup_proven

    def _call(
        self,
        operation: str,
        payload: dict[str, Any] | None,
        *,
        timeout_sec: float,
        response_limit: int,
    ) -> dict[str, Any]:
        if self._closed or not self._tree.process.is_alive():
            raise SubjectBoundaryError("isolated subject process is unavailable")
        call_id = uuid.uuid4().hex
        request_path = self._ipc_root / f"{call_id}.request.json"
        response_path = self._ipc_root / f"{call_id}.response.json"
        request_raw = _encode_json(
            {"payload": payload, "timeout_sec": max(1, int(timeout_sec))},
            limit=MAX_REQUEST_BYTES,
            context="subject request",
        )
        _write_atomic(request_path, request_raw)
        control = _encode_json(
            {
                "operation": operation,
                "call_id": call_id,
                "response_limit": int(response_limit),
            },
            limit=MAX_CONTROL_BYTES,
            context="subject control frame",
        )
        started = time.monotonic()
        try:
            self._connection.send_bytes(control)
        except (BrokenPipeError, EOFError, OSError) as exc:
            clean = self._terminate()
            raise SubjectBoundaryError(
                f"cannot submit call to isolated subject; cleanup_proven={clean}"
            ) from exc
        remaining = max(0.0, timeout_sec - (time.monotonic() - started))
        if not self._connection.poll(remaining):
            clean = self._terminate()
            raise SubjectTimeoutError(
                f"subject {operation} exceeded {timeout_sec:g} seconds; "
                f"cleanup_proven={clean}"
            )
        try:
            notification = _decode_json(
                self._connection.recv_bytes(MAX_CONTROL_BYTES),
                context="subject notification",
            )
        except (BrokenPipeError, EOFError, OSError) as exc:
            clean = self._terminate()
            raise SubjectBoundaryError(
                f"isolated subject returned an invalid control frame; cleanup_proven={clean}"
            ) from exc
        if notification != {"call_id": call_id}:
            clean = self._terminate()
            raise SubjectBoundaryError(
                f"isolated subject response identity mismatch; cleanup_proven={clean}"
            )
        response = _decode_json(
            _read_bounded(response_path, response_limit),
            context="subject response",
        )
        response_path.unlink(missing_ok=True)
        if response.get("ok") is not True:
            error_type = str(response.get("error_type") or "SubjectError")
            error = str(response.get("error") or "")[:1000]
            raise SubjectBoundaryError(f"{error_type}: {error}")
        return response

    def health(self, *, timeout_sec: float) -> dict[str, Any]:
        response = self._call(
            "health",
            None,
            timeout_sec=timeout_sec,
            response_limit=MAX_HEALTH_BYTES,
        )
        if response.get("kind") != "health" or not isinstance(response.get("value"), dict):
            raise SubjectBoundaryError("subject health returned an invalid envelope")
        if not self._tree.controller_only():
            clean = self._terminate()
            raise SubjectBoundaryError(
                f"subject health leaked descendant processes; cleanup_proven={clean}"
            )
        return response["value"]

    def execute(
        self,
        payload: dict[str, Any],
        *,
        timeout_sec: int,
        max_result_bytes: int = MAX_RESPONSE_BYTES - 4096,
    ) -> SubjectExecution:
        response_limit = min(MAX_RESPONSE_BYTES, max(4096, int(max_result_bytes) + 4096))
        response = self._call(
            "execute",
            payload,
            timeout_sec=float(timeout_sec),
            response_limit=response_limit,
        )
        if response.get("kind") != "execution" or not isinstance(response.get("result"), dict):
            raise SubjectBoundaryError("subject execute returned an invalid envelope")
        external_cleanup = self._tree.controller_only()
        claimed_cleanup = response.get("subject_cleanup_claim") is True
        cleanup_proven = external_cleanup and claimed_cleanup
        details: list[str] = []
        if not claimed_cleanup:
            details.append(str(response.get("cleanup_detail") or "subject reported cleanup failure"))
        if not external_cleanup:
            details.append("external process-tree cleanup check found live descendants")
            self._terminate()
        return SubjectExecution(
            result=response["result"],
            terminal=response.get("terminal") is True,
            cleanup_proven=cleanup_proven,
            cleanup_detail="; ".join(details) if details else "cleanup proven externally",
        )

    def close(self) -> bool:
        if not self._closed and self._tree.process.is_alive():
            try:
                self._connection.send_bytes(b'{"operation":"stop"}')
                if self._connection.poll(1.0):
                    self._connection.recv_bytes(MAX_CONTROL_BYTES)
            except (BrokenPipeError, EOFError, OSError):
                pass
            self._tree.process.join(timeout=1)
        if not self._closed:
            self._last_cleanup_proven = self._tree.terminate(grace_seconds=1.0)
            self._closed = True
        self._connection.close()
        self._ipc.cleanup()
        return self._last_cleanup_proven

    def __enter__(self) -> "SubjectProcessBoundary":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()


class FakeSubjectAdapter(SubjectAdapter):
    """Deterministic replay subject used to harden the evaluator before live models."""

    def __init__(
        self,
        results: dict[str, dict[str, Any]],
        *,
        identity: dict[str, Any] | None = None,
        fail_tasks: set[str] | None = None,
        unclean_tasks: set[str] | None = None,
        end_identity: dict[str, Any] | None = None,
        exercise_fixture_gateway: bool = True,
        source_request_method: str = "GET",
    ) -> None:
        self.results = copy.deepcopy(results)
        self.fail_tasks = set(fail_tasks or ())
        self.unclean_tasks = set(unclean_tasks or ())
        self._health_calls = 0
        self.identity = identity or {
            "instance_id": "fake-research-subject-1",
            "source_sha256": "0" * 64,
            "model": "deterministic-replay",
            "standalone_test_mode": True,
        }
        self.end_identity = end_identity
        self.exercise_fixture_gateway = bool(exercise_fixture_gateway)
        if source_request_method not in {"GET", "HEAD"}:
            raise ValueError("source_request_method must be GET or HEAD")
        self.source_request_method = source_request_method

    def _read_replayed_sources(self, payload: dict[str, Any], result: dict[str, Any]) -> None:
        if not self.exercise_fixture_gateway:
            return
        ledger = result.get("ledger") if isinstance(result.get("ledger"), dict) else {}
        sources = ledger.get("sources") if isinstance(ledger.get("sources"), list) else []
        spans = ledger.get("spans") if isinstance(ledger.get("spans"), list) else []
        source_objects = {
            str(source.get("source_id") or ""): source
            for source in sources
            if isinstance(source, dict) and str(source.get("source_id") or "")
        }
        gateway = str(payload.get("source_gateway_url") or "").rstrip("/")
        if source_objects and not gateway:
            raise ValueError("fixture gateway is required for replayed source access")
        for source_id, source in sorted(source_objects.items()):
            slug = source_id.removeprefix("source-")
            request = Request(
                gateway + "/documents/" + quote(slug, safe="-"),
                method=self.source_request_method,
            )
            with urlopen(request, timeout=5) as response:
                body = response.read()
            digest = hashlib.sha256(body).hexdigest()
            source["raw_sha256"] = digest
            source["normalized_sha256"] = digest
            for span in spans:
                if isinstance(span, dict) and span.get("source_id") == source_id:
                    span["representation_sha256"] = digest

    def health(self) -> dict[str, Any]:
        self._health_calls += 1
        identity = (
            self.end_identity
            if self._health_calls > 1 and self.end_identity is not None
            else self.identity
        )
        return {
            "status": "ok",
            "service": "FakeResearchSubject",
            "identity": copy.deepcopy(identity),
        }

    def execute(self, payload: dict[str, Any], *, timeout_sec: int) -> SubjectExecution:
        del timeout_sec
        task_id = str(payload.get("task_id") or "")
        case_id = task_id.removeprefix("eval-")
        if case_id in self.fail_tasks:
            raise RuntimeError(f"injected subject failure for {case_id}")
        if case_id not in self.results:
            raise KeyError(f"no fake result for {case_id}")
        result = copy.deepcopy(self.results[case_id])
        result["mission_id"] = task_id
        self._read_replayed_sources(payload, result)
        clean = case_id not in self.unclean_tasks
        return SubjectExecution(
            result=result,
            terminal=True,
            cleanup_proven=clean,
            cleanup_detail="cleanup proven" if clean else "injected cleanup failure",
        )


__all__ = [
    "FakeSubjectAdapter",
    "SubjectAdapter",
    "SubjectBoundaryError",
    "SubjectExecution",
    "SubjectProcessBoundary",
    "SubjectTimeoutError",
]
