"""Killable spawn-process boundary for one ResearchWarband attempt.

This module owns OS process isolation, hard deadlines, cancellation escalation,
and runner source attestation.  It deliberately knows nothing about mission
persistence or lifecycle statuses.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import hmac
from importlib.machinery import PathFinder
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import re
import signal
import shutil
import stat
import sys
import tempfile
import time
import types
from typing import Any
import uuid

try:
    from .deployment_guard import DeploymentManifest, verify_manifest
except ImportError:
    from deployment_guard import DeploymentManifest, verify_manifest  # type: ignore[no-redef]


class SupervisorError(RuntimeError):
    pass


class RunnerTimeoutError(SupervisorError):
    pass


class RunnerCleanupError(SupervisorError):
    pass


class RunnerExecutionError(SupervisorError):
    pass


class RunnerReadinessError(SupervisorError):
    pass


class RunnerIsolationUnavailableError(SupervisorError):
    pass


@dataclass(frozen=True)
class RunnerSpec:
    """Spawn-importable, content-bound identity of a trusted runner."""

    target: str
    module_path: str
    module_sha256: str
    callable_sha256: str


@dataclass
class AttemptContext:
    """Minimal spawn-safe mission view exposed to a child runner."""

    id: str
    attempt: int
    clarification_turns: tuple[dict[str, str], ...]
    cancelled: Any
    revision_turns: tuple[dict[str, Any], ...] = ()


MAX_CLARIFICATION_TURNS = 16
MAX_CLARIFICATION_FIELD_BYTES = 8_000
MAX_CLARIFICATION_TOTAL_BYTES = 16_000
MAX_REVISION_TURNS = 16
MAX_REVISION_FINDINGS = 20
MAX_REVISION_FIELD_BYTES = 2_000
MAX_REVISION_REASON_BYTES = 4_096
MAX_REVISION_TOTAL_BYTES = 256_000
_REVISION_TURN_FIELDS = frozenset(
    {"attempt", "result_sha256", "reason", "findings"}
)
_REVIEW_FINDING_FIELDS = frozenset(
    {
        "code",
        "entity_kind",
        "entity_id",
        "what_failed",
        "evidence",
        "expected",
        "remediation",
        "revision_owner",
        "retryable",
    }
)


def _clarification_turns(value: Any) -> tuple[dict[str, str], ...]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_CLARIFICATION_TURNS:
        raise TypeError("clarification_turns must be a bounded ordered sequence")
    restored: list[dict[str, str]] = []
    total = 0
    for item in value:
        if not isinstance(item, dict) or set(item) != {"question", "answer"}:
            raise TypeError("clarification turn must contain exactly question and answer")
        question, answer = item.get("question"), item.get("answer")
        if type(question) is not str or type(answer) is not str:
            raise TypeError("clarification question and answer must be strings")
        question_size = len(question.encode("utf-8"))
        answer_size = len(answer.encode("utf-8"))
        if not question.strip() or not answer.strip():
            raise ValueError("clarification question and answer must be non-empty")
        if max(question_size, answer_size) > MAX_CLARIFICATION_FIELD_BYTES:
            raise ValueError("clarification field exceeds byte limit")
        total += question_size + answer_size
        if total > MAX_CLARIFICATION_TOTAL_BYTES:
            raise ValueError("clarification turns exceed aggregate byte limit")
        restored.append({"question": question, "answer": answer})
    return tuple(restored)


def validate_revision_turns(value: Any) -> tuple[dict[str, Any], ...]:
    """Validate bounded, result-bound internal feedback for a later attempt."""

    if not isinstance(value, (list, tuple)) or len(value) > MAX_REVISION_TURNS:
        raise TypeError("revision_turns must be a bounded ordered sequence")
    restored: list[dict[str, Any]] = []
    previous_attempt = 0
    for turn_index, raw_turn in enumerate(value):
        if not isinstance(raw_turn, dict) or set(raw_turn) != _REVISION_TURN_FIELDS:
            raise TypeError(
                f"revision_turns[{turn_index}] must contain exactly the revision turn fields"
            )
        attempt = raw_turn.get("attempt")
        result_sha256 = raw_turn.get("result_sha256")
        reason = raw_turn.get("reason")
        raw_findings = raw_turn.get("findings")
        if (
            type(attempt) is not int
            or attempt < 1
            or attempt > MAX_REVISION_TURNS
            or attempt <= previous_attempt
        ):
            raise ValueError(f"revision_turns[{turn_index}].attempt is invalid")
        if (
            type(result_sha256) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", result_sha256)
        ):
            raise ValueError(
                f"revision_turns[{turn_index}].result_sha256 is invalid"
            )
        if type(reason) is not str or not reason.strip():
            raise ValueError(f"revision_turns[{turn_index}].reason must not be empty")
        if len(reason.encode("utf-8")) > MAX_REVISION_REASON_BYTES:
            raise ValueError(f"revision_turns[{turn_index}].reason exceeds byte limit")
        if (
            type(raw_findings) is not list
            or not raw_findings
            or len(raw_findings) > MAX_REVISION_FINDINGS
        ):
            raise ValueError(
                f"revision_turns[{turn_index}].findings must be a bounded non-empty array"
            )
        findings: list[dict[str, Any]] = []
        for finding_index, raw_finding in enumerate(raw_findings):
            if (
                not isinstance(raw_finding, dict)
                or set(raw_finding) != _REVIEW_FINDING_FIELDS
            ):
                raise TypeError(
                    "revision finding must contain exactly the shared diagnostic fields"
                )
            finding: dict[str, Any] = {}
            for field in _REVIEW_FINDING_FIELDS - {"retryable"}:
                item = raw_finding.get(field)
                if type(item) is not str or not item.strip():
                    raise ValueError(
                        f"revision finding {turn_index}:{finding_index}.{field} must not be empty"
                    )
                if len(item.encode("utf-8")) > MAX_REVISION_FIELD_BYTES:
                    raise ValueError(
                        f"revision finding {turn_index}:{finding_index}.{field} exceeds byte limit"
                    )
                finding[field] = item.strip()
            if type(raw_finding.get("retryable")) is not bool:
                raise TypeError("revision finding retryable must be boolean")
            finding["retryable"] = raw_finding["retryable"]
            findings.append(finding)
        previous_attempt = attempt
        restored.append(
            {
                "attempt": attempt,
                "result_sha256": result_sha256,
                "reason": reason.strip(),
                "findings": findings,
            }
        )
    if len(_json_bytes(restored)) > MAX_REVISION_TOTAL_BYTES:
        raise ValueError("revision_turns exceed aggregate byte limit")
    return tuple(restored)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _error_text(error: BaseException | str, limit: int = 4096) -> str:
    text = str(error)
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= limit:
        return text
    return raw[: max(0, limit - 3)].decode("utf-8", errors="ignore") + "..."


def _target_parts(target: str) -> tuple[str, str]:
    if type(target) is not str or ":" not in target:
        raise TypeError("runner target must be an importable module:qualname")
    module_name, qualname = target.split(":", 1)
    if not module_name or not qualname or "<locals>" in qualname or "<lambda>" in qualname:
        raise TypeError("runner target must be an importable module:qualname")
    if any(not component or component.startswith("<") for component in qualname.split(".")):
        raise TypeError("runner target contains an unimportable component")
    return module_name, qualname


def _lookup_target(module: Any, qualname: str) -> Callable[..., Any]:
    value: Any = module
    for component in qualname.split("."):
        value = getattr(value, component)
    if not callable(value):
        raise TypeError("resolved runner target is not callable")
    return value


def _module_spec_without_import(module_name: str) -> Any:
    search: Any = None
    current = ""
    spec: Any = None
    for component in module_name.split("."):
        current = component if not current else f"{current}.{component}"
        spec = PathFinder.find_spec(current, search)
        if spec is None:
            raise ImportError(f"runner module cannot be located without import: {module_name}")
        search = spec.submodule_search_locations
    return spec


def _source_path(module_name: str) -> Path:
    spec = _module_spec_without_import(module_name)
    origin = getattr(spec, "origin", None)
    if type(origin) is not str or not origin.endswith(".py"):
        raise RuntimeError("runner module must resolve to Python source, not bytecode/native code")
    path = Path(os.path.abspath(origin))
    for component in reversed((path, *path.parents)):
        try:
            metadata = component.lstat()
        except OSError as exc:
            raise RuntimeError("runner source path is missing or unreadable") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeError("runner source path must not contain a symlink")
        junction = getattr(os.path, "isjunction", None)
        if junction is not None and junction(component):
            raise RuntimeError("runner source path must not contain a junction")
    if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
        raise RuntimeError("runner source must not resolve through executable bytecode")
    return path


def _read_source(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 32_000_000:
            raise RuntimeError("runner source must be a bounded regular file")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            raw = handle.read(32_000_001)
            after = os.fstat(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > 32_000_000:
        raise RuntimeError("runner source exceeds attestation byte limit")
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after or len(raw) != after.st_size:
        raise RuntimeError("runner source changed while being read")
    return raw


def _code_for_qualname(module_code: types.CodeType, qualname: str) -> types.CodeType:
    matches: list[types.CodeType] = []
    pending = [module_code]
    while pending:
        code = pending.pop()
        if code.co_qualname == qualname:
            matches.append(code)
        pending.extend(
            item for item in code.co_consts if isinstance(item, types.CodeType)
        )
    if len(matches) != 1:
        raise RuntimeError("runner qualname is absent or ambiguous in attested source")
    return matches[0]


def _constant_fingerprint(value: Any) -> dict[str, Any]:
    """Return a typed, reference-order-independent code-constant record."""

    if value is None:
        return {"type": "none"}
    if value is Ellipsis:
        return {"type": "ellipsis"}
    if type(value) is bool:
        return {"type": "bool", "value": value}
    if type(value) is int:
        return {"type": "int", "value": str(value)}
    if type(value) is float:
        return {"type": "float", "value": value.hex()}
    if type(value) is complex:
        return {
            "type": "complex",
            "real": value.real.hex(),
            "imag": value.imag.hex(),
        }
    if type(value) is str:
        return {"type": "str", "value": value}
    if type(value) is bytes:
        return {"type": "bytes", "value": value.hex()}
    if type(value) is tuple:
        return {
            "type": "tuple",
            "items": [_constant_fingerprint(item) for item in value],
        }
    if type(value) is frozenset:
        items = [_constant_fingerprint(item) for item in value]
        items.sort(
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
        return {"type": "frozenset", "items": items}
    if isinstance(value, types.CodeType):
        return {"type": "code", "value": _code_fingerprint(value)}
    raise RuntimeError(
        f"runner code contains an unsupported constant type: {type(value).__name__}"
    )


def _code_fingerprint(code: types.CodeType) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "argcount": code.co_argcount,
        "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount,
        "nlocals": code.co_nlocals,
        "stacksize": code.co_stacksize,
        "flags": code.co_flags,
        "code": code.co_code.hex(),
        "consts": [_constant_fingerprint(item) for item in code.co_consts],
        "names": list(code.co_names),
        "varnames": list(code.co_varnames),
        "freevars": list(code.co_freevars),
        "cellvars": list(code.co_cellvars),
        "filename": code.co_filename,
        "name": code.co_name,
        "qualname": code.co_qualname,
        "firstlineno": code.co_firstlineno,
        "linetable": code.co_linetable.hex(),
        "exceptiontable": code.co_exceptiontable.hex(),
    }


def _code_sha256(code: types.CodeType) -> str:
    canonical = json.dumps(
        _code_fingerprint(code),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _compile_callable_code(
    raw: bytes,
    path: Path,
    qualname: str,
) -> types.CodeType:
    try:
        module_code = compile(raw, str(path), "exec", dont_inherit=True)
    except (SyntaxError, ValueError) as exc:
        raise RuntimeError("runner source cannot be compiled for attestation") from exc
    return _code_for_qualname(module_code, qualname)


def _source_runner_spec(target: str) -> RunnerSpec:
    module_name, qualname = _target_parts(target)
    path = _source_path(module_name)
    raw = _read_source(path)
    callable_code = _compile_callable_code(raw, path, qualname)
    return RunnerSpec(
        target=target,
        module_path=str(path),
        module_sha256=hashlib.sha256(raw).hexdigest(),
        callable_sha256=_code_sha256(callable_code),
    )


def _resolve_attested_target(spec: RunnerSpec) -> Callable[..., Any]:
    """Execute only source bytes that were hashed before import, never a local pyc."""

    fresh = _source_runner_spec(spec.target)
    if fresh != spec:
        raise RuntimeError("runner attestation no longer matches its source bytes")
    module_name, qualname = _target_parts(spec.target)
    source_path = Path(spec.module_path)
    raw = _read_source(source_path)
    if hashlib.sha256(raw).hexdigest() != spec.module_sha256:
        raise RuntimeError("runner source changed before controlled import")
    expected_code = _compile_callable_code(raw, source_path, qualname)
    if _code_sha256(expected_code) != spec.callable_sha256:
        raise RuntimeError("runner callable attestation no longer matches source")
    module = sys.modules.get(module_name)
    if module is None:
        module = types.ModuleType(module_name)
        module.__file__ = spec.module_path
        module.__package__ = module_name.rpartition(".")[0]
        module.__loader__ = None
        module.__spec__ = importlib.util.spec_from_file_location(
            module_name, spec.module_path
        )
        previous = sys.modules.get(module_name)
        sys.modules[module_name] = module
        sys.dont_write_bytecode = True
        try:
            exec(
                compile(raw, spec.module_path, "exec", dont_inherit=True),
                module.__dict__,
            )
        except BaseException:
            if previous is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous
            raise
        if _source_runner_spec(spec.target) != spec:
            sys.modules.pop(module_name, None)
            raise RuntimeError("runner source changed during controlled import")
    resolved = _lookup_target(module, qualname)
    code = getattr(resolved, "__code__", None)
    if (
        not isinstance(code, types.CodeType)
        or code != expected_code
        or _code_sha256(code) != spec.callable_sha256
    ):
        raise RuntimeError("loaded runner bytecode does not match attested source")
    return resolved


def attest_runner(runner: RunnerSpec | str | Callable[..., Any]) -> RunnerSpec:
    """Content-bind a runner from source before any target module is imported."""

    if isinstance(runner, RunnerSpec):
        fresh = _source_runner_spec(runner.target)
        if fresh != runner:
            raise RuntimeError("runner attestation no longer matches its source bytes")
        return runner
    if isinstance(runner, str):
        return _source_runner_spec(runner)
    elif callable(runner):
        module_name = getattr(runner, "__module__", None)
        qualname = getattr(runner, "__qualname__", None)
        if type(module_name) is not str or type(qualname) is not str:
            raise TypeError("runner must expose an importable module and qualname")
        target = f"{module_name}:{qualname}"
        _target_parts(target)
        module = sys.modules.get(module_name)
        if module is None or _lookup_target(module, qualname) is not runner:
            raise TypeError("runner target does not resolve to the supplied callable")
    else:
        raise TypeError("runner must be RunnerSpec, module:qualname, or callable")

    spec = _source_runner_spec(target)
    code = getattr(runner, "__code__", None)
    source_path = Path(spec.module_path)
    raw = _read_source(source_path)
    if hashlib.sha256(raw).hexdigest() != spec.module_sha256:
        raise RuntimeError("runner source changed before callable verification")
    expected_code = _compile_callable_code(raw, source_path, qualname)
    if (
        not isinstance(code, types.CodeType)
        or code != expected_code
        or _code_sha256(code) != spec.callable_sha256
    ):
        raise RuntimeError("supplied runner bytecode does not match its source")
    return spec


def read_runtime_readiness(
    spec: RunnerSpec,
    *,
    require_attestation: bool = False,
    expected_attestation_sha256: str | None = None,
) -> dict[str, Any]:
    """Call an attested zero-argument readiness probe and validate its tiny contract."""

    resolved = _resolve_attested_target(attest_runner(spec))
    value = resolved()
    if not isinstance(value, Mapping):
        raise RunnerReadinessError("readiness probe result must be an object")
    exact = dict(value)
    if set(exact) - {"ready", "reason", "attestation_sha256"}:
        raise RunnerReadinessError("readiness probe returned unknown fields")
    if type(exact.get("ready")) is not bool:
        raise RunnerReadinessError("readiness probe ready must be boolean")
    reason = exact.get("reason", "")
    if type(reason) is not str or len(reason.encode("utf-8")) > 512:
        raise RunnerReadinessError("readiness probe reason is invalid")
    attestation = exact.get("attestation_sha256")
    if attestation is not None and (
        type(attestation) is not str
        or len(attestation) != 64
        or any(character not in "0123456789abcdef" for character in attestation)
    ):
        raise RunnerReadinessError("readiness probe attestation is invalid")
    if require_attestation and attestation is None:
        raise RunnerReadinessError(
            "readiness probe omitted the required deployment attestation"
        )
    if (
        expected_attestation_sha256 is not None
        and attestation != expected_attestation_sha256
    ):
        raise RunnerReadinessError("runner deployment attestation changed")
    if len(_json_bytes(exact)) > 16_384:
        raise RunnerReadinessError("readiness probe result exceeds byte limit")
    return exact


def verify_runtime_readiness(
    spec: RunnerSpec | None,
    *,
    require_attestation: bool = False,
    expected_attestation_sha256: str | None = None,
) -> dict[str, Any] | None:
    if spec is None:
        if require_attestation or expected_attestation_sha256 is not None:
            raise RunnerReadinessError("attested readiness probe is required")
        return None
    exact = read_runtime_readiness(
        spec,
        require_attestation=require_attestation,
        expected_attestation_sha256=expected_attestation_sha256,
    )
    if not exact["ready"]:
        raise RunnerReadinessError("runner deployment is not ready")
    return exact


def _write_envelope(path: str, value: dict[str, Any], limit: int) -> None:
    nonce = value.get("nonce")
    try:
        raw = _json_bytes(value)
    except (TypeError, ValueError, RecursionError) as exc:
        raw = _json_bytes(
            {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": _error_text(exc),
                "nonce": nonce,
            }
        )
    if len(raw) > limit:
        raw = _json_bytes(
            {
                "ok": False,
                "error_type": "ResultTooLargeError",
                "error": f"child result exceeds {limit} bytes",
                "nonce": nonce,
            }
        )
    target = Path(path)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _child_main(
    spec: RunnerSpec,
    deployment_manifest: DeploymentManifest | None,
    readiness_spec: RunnerSpec | None,
    readiness_attestation_sha256: str | None,
    require_readiness_attestation: bool,
    payload: dict[str, Any],
    context_data: dict[str, Any],
    cancelled: Any,
    start_gate: Any,
    output_path: str,
    ipc_nonce: str,
    result_limit: int,
) -> None:
    try:
        if os.name != "nt":
            os.setsid()
        if not start_gate.wait(30):
            raise RuntimeError("parent did not authorize isolated runner startup")
        if deployment_manifest is not None:
            verify_manifest(deployment_manifest)
        runner = _resolve_attested_target(attest_runner(spec))
        if deployment_manifest is not None:
            verify_manifest(deployment_manifest)
        verify_runtime_readiness(
            readiness_spec,
            require_attestation=require_readiness_attestation,
            expected_attestation_sha256=readiness_attestation_sha256,
        )
        context = AttemptContext(
            id=str(context_data["id"]),
            attempt=int(context_data["attempt"]),
            clarification_turns=_clarification_turns(
                context_data.get("clarification_turns") or ()
            ),
            cancelled=cancelled,
            revision_turns=validate_revision_turns(
                context_data.get("revision_turns") or ()
            ),
        )
        produced = runner(payload, context)
        if deployment_manifest is not None:
            verify_manifest(deployment_manifest)
        verify_runtime_readiness(
            readiness_spec,
            require_attestation=require_readiness_attestation,
            expected_attestation_sha256=readiness_attestation_sha256,
        )
        if isinstance(produced, Mapping):
            result = dict(produced)
        else:
            serializer = getattr(produced, "to_dict", None)
            if not callable(serializer):
                raise TypeError("runner must return a JSON object or to_dict() result")
            result = serializer()
        if not isinstance(result, dict):
            raise TypeError("runner result must be a JSON object")
        result_raw = _json_bytes(result)
        if len(result_raw) > result_limit:
            raise ValueError(f"pipeline result exceeds {result_limit} bytes")
        envelope = {
            "ok": True,
            "result": json.loads(result_raw),
            "nonce": ipc_nonce,
        }
    except BaseException as exc:
        envelope = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": _error_text(exc),
            "nonce": ipc_nonce,
        }
    try:
        _write_envelope(output_path, envelope, result_limit + 65_536)
    except BaseException:
        pass


class _WindowsJob:
    """Stdlib-only kill-on-close Job Object wrapper."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
                )
            ]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimit),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class Accounting(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_longlong),
                ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._accounting_type = Accounting
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32 = kernel32
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
        )
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        kernel32.QueryInformationJobObject.argtypes = (
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p
        )
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.handle = kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        limits = ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(self.handle)
            self.handle = None
            raise OSError(error, "SetInformationJobObject failed")

    def assign(self, process: multiprocessing.Process) -> None:
        process_handle = self._wintypes.HANDLE(int(process.sentinel))
        if not self._kernel32.AssignProcessToJobObject(self.handle, process_handle):
            raise OSError(self._ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def active_count(self) -> int:
        info = self._accounting_type()
        if not self._kernel32.QueryInformationJobObject(
            self.handle, 1, self._ctypes.byref(info), self._ctypes.sizeof(info), None
        ):
            raise OSError(self._ctypes.get_last_error(), "QueryInformationJobObject failed")
        return int(info.ActiveProcesses)

    def terminate(self) -> None:
        if not self._kernel32.TerminateJobObject(self.handle, 1):
            error = self._ctypes.get_last_error()
            if error:
                raise OSError(error, "TerminateJobObject failed")

    def close(self) -> None:
        if self.handle:
            self._kernel32.CloseHandle(self.handle)
            self.handle = None


class _LinuxAttemptCgroup:
    """Delegated cgroup-v2 boundary for one trusted runner's entire process tree."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @staticmethod
    def _self_relative_path() -> str:
        try:
            raw = Path("/proc/self/cgroup").read_text(encoding="ascii")
        except OSError as exc:
            raise RunnerIsolationUnavailableError(
                "cannot read the service cgroup identity"
            ) from exc
        matches = [line[3:] for line in raw.splitlines() if line.startswith("0::")]
        if len(matches) != 1 or not matches[0].startswith("/"):
            raise RunnerIsolationUnavailableError("unified cgroup-v2 is required")
        return matches[0]

    @classmethod
    def create(cls) -> "_LinuxAttemptCgroup":
        mount = Path("/sys/fs/cgroup")
        if not (mount / "cgroup.controllers").is_file():
            raise RunnerIsolationUnavailableError("unified cgroup-v2 is unavailable")
        relative = cls._self_relative_path().lstrip("/")
        parent = mount / relative
        if not parent.is_dir():
            raise RunnerIsolationUnavailableError("service cgroup is unavailable")
        path = parent / f"research-warband-attempt-{os.getpid()}-{uuid.uuid4().hex}"
        try:
            path.mkdir(mode=0o700, parents=False, exist_ok=False)
            required = ("cgroup.procs", "cgroup.events", "cgroup.kill")
            if any(not (path / name).exists() for name in required):
                raise RunnerIsolationUnavailableError(
                    "delegated cgroup lacks kill/population controls"
                )
            return cls(path)
        except RunnerIsolationUnavailableError:
            try:
                path.rmdir()
            except OSError:
                pass
            raise
        except OSError as exc:
            try:
                path.rmdir()
            except OSError:
                pass
            raise RunnerIsolationUnavailableError(
                "service cgroup is not delegated for per-attempt isolation"
            ) from exc

    def attach(self, process_id: int) -> None:
        try:
            (self.path / "cgroup.procs").write_text(
                str(int(process_id)), encoding="ascii"
            )
            raw = Path(f"/proc/{int(process_id)}/cgroup").read_text(encoding="ascii")
        except OSError as exc:
            raise RunnerIsolationUnavailableError(
                "runner could not be attached to its delegated cgroup"
            ) from exc
        expected = "/" + str(self.path.relative_to(Path("/sys/fs/cgroup"))).replace(
            os.sep, "/"
        )
        memberships = [line[3:] for line in raw.splitlines() if line.startswith("0::")]
        if memberships != [expected]:
            raise RunnerIsolationUnavailableError(
                "runner cgroup membership could not be proven before startup"
            )

    def populated(self) -> bool:
        try:
            raw = (self.path / "cgroup.events").read_text(encoding="ascii")
        except OSError as exc:
            raise RunnerCleanupError("cannot read runner cgroup population") from exc
        if len(raw.encode("ascii", errors="ignore")) > 4096:
            raise RunnerCleanupError("runner cgroup event record is oversized")
        values: dict[str, str] = {}
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) == 2:
                values[parts[0]] = parts[1]
        if values.get("populated") not in {"0", "1"}:
            raise RunnerCleanupError("runner cgroup population is malformed")
        return values["populated"] == "1"

    def terminate(self) -> None:
        try:
            (self.path / "cgroup.kill").write_text("1", encoding="ascii")
        except OSError as exc:
            raise RunnerCleanupError("runner cgroup could not be killed") from exc

    def signal_all(self, selected: signal.Signals) -> None:
        try:
            raw = (self.path / "cgroup.procs").read_text(encoding="ascii")
        except OSError as exc:
            raise RunnerCleanupError("cannot enumerate runner cgroup") from exc
        if len(raw) > 64_000:
            raise RunnerCleanupError("runner cgroup process list is oversized")
        for line in raw.splitlines():
            try:
                process_id = int(line)
                os.kill(process_id, selected)
            except ProcessLookupError:
                continue
            except (OSError, ValueError) as exc:
                raise RunnerCleanupError("runner cgroup could not be signalled") from exc

    def close(self) -> None:
        try:
            self.path.rmdir()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RunnerCleanupError("runner cgroup could not be removed") from exc


class ProcessSupervisor:
    def __init__(
        self,
        *,
        max_result_bytes: int,
        cancel_grace_seconds: float,
        terminate_grace_seconds: float,
    ) -> None:
        self.max_result_bytes = int(max_result_bytes)
        self.cancel_grace_seconds = float(cancel_grace_seconds)
        self.terminate_grace_seconds = float(terminate_grace_seconds)

    @staticmethod
    def _regular(path: Path) -> bool:
        try:
            mode = path.lstat().st_mode
        except OSError:
            return False
        return stat.S_ISREG(mode) and not stat.S_ISLNK(mode)

    @staticmethod
    def _group_exists(process_id: int) -> bool:
        if os.name == "nt":
            return False
        try:
            os.killpg(process_id, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def _stop_tree(
        self,
        process: multiprocessing.Process,
        job: _WindowsJob | None,
        cgroup: _LinuxAttemptCgroup | None,
        *,
        graceful: bool,
    ) -> None:
        if cgroup is not None:
            if graceful:
                cgroup.signal_all(signal.SIGTERM)
            else:
                cgroup.terminate()
        elif os.name == "nt" and job is not None:
            job.terminate()
        elif os.name != "nt" and process.pid and self._group_exists(process.pid):
            os.killpg(process.pid, signal.SIGTERM if graceful else signal.SIGKILL)
        elif process.is_alive():
            if graceful:
                process.terminate()
            elif hasattr(process, "kill"):
                process.kill()
            else:
                process.terminate()

    def _prove_cleanup(
        self,
        process: multiprocessing.Process,
        job: _WindowsJob | None,
        cgroup: _LinuxAttemptCgroup | None,
    ) -> bool:
        process.join(timeout=0)
        if cgroup is not None:
            try:
                if cgroup.populated():
                    cgroup.signal_all(signal.SIGTERM)
                deadline = time.monotonic() + self.terminate_grace_seconds
                while time.monotonic() < deadline and cgroup.populated():
                    time.sleep(0.01)
                if cgroup.populated():
                    cgroup.terminate()
                    deadline = time.monotonic() + max(
                        1.0, self.terminate_grace_seconds
                    )
                    while time.monotonic() < deadline and cgroup.populated():
                        time.sleep(0.01)
                process.join(timeout=max(1.0, self.terminate_grace_seconds))
                empty = not cgroup.populated()
                if empty:
                    cgroup.close()
                return empty and not process.is_alive()
            except (OSError, RunnerCleanupError):
                return False
        if os.name == "nt":
            if job is None:
                return not process.is_alive()
            try:
                if job.active_count() > 0:
                    job.terminate()
                deadline = time.monotonic() + max(1.0, self.terminate_grace_seconds)
                while time.monotonic() < deadline and job.active_count() > 0:
                    time.sleep(0.01)
                return job.active_count() == 0 and not process.is_alive()
            except OSError:
                return False
        if process.pid and self._group_exists(process.pid):
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + self.terminate_grace_seconds
            while time.monotonic() < deadline and self._group_exists(process.pid):
                time.sleep(0.01)
            if self._group_exists(process.pid):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + max(1.0, self.terminate_grace_seconds)
                while time.monotonic() < deadline and self._group_exists(process.pid):
                    time.sleep(0.01)
        process.join(timeout=max(1.0, self.terminate_grace_seconds))
        return not process.is_alive() and not (
            process.pid and self._group_exists(process.pid)
        )

    @staticmethod
    def _read_bounded(path: Path, limit: int) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise RunnerExecutionError("runner envelope is unsafe or oversized")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                raw = handle.read(limit + 1)
                after = os.fstat(handle.fileno())
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(raw) > limit:
            raise RunnerExecutionError("runner envelope is oversized")
        if (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or len(raw) != after.st_size:
            raise RunnerExecutionError("runner envelope changed while being read")
        return raw

    def run(
        self,
        *,
        spec: RunnerSpec,
        payload_bytes: bytes,
        mission_id: str,
        attempt: int,
        clarification_turns: tuple[dict[str, str], ...],
        cancelled: Any,
        hard_timeout_seconds: float,
        revision_turns: tuple[dict[str, Any], ...] = (),
        deployment_manifest: DeploymentManifest | None = None,
        readiness_spec: RunnerSpec | None = None,
        require_readiness_attestation: bool = False,
        require_linux_cgroup: bool = False,
    ) -> dict[str, Any]:
        if deployment_manifest is not None:
            verify_manifest(deployment_manifest)
        spec = attest_runner(spec)
        if readiness_spec is not None:
            readiness_spec = attest_runner(readiness_spec)
        initial_readiness = verify_runtime_readiness(
            readiness_spec,
            require_attestation=require_readiness_attestation,
        )
        readiness_attestation_sha256 = (
            initial_readiness.get("attestation_sha256")
            if initial_readiness is not None
            else None
        )
        if deployment_manifest is not None:
            verify_manifest(deployment_manifest)
        turns = _clarification_turns(clarification_turns)
        revisions = validate_revision_turns(revision_turns)
        payload = json.loads(payload_bytes)
        if not isinstance(payload, dict):
            raise RunnerExecutionError("canonical attempt payload is not an object")
        context = multiprocessing.get_context("spawn")
        child_cancelled = context.Event()
        start_gate = context.Event()
        output_directory = tempfile.mkdtemp(
            prefix=f"research-{mission_id}-{attempt:06d}-"
        )
        try:
            Path(output_directory).chmod(0o700)
        except OSError:
            pass
        output_name = str(Path(output_directory) / "result.json")
        ipc_nonce = uuid.uuid4().hex + uuid.uuid4().hex
        process = context.Process(
            target=_child_main,
            args=(
                spec,
                deployment_manifest,
                readiness_spec,
                readiness_attestation_sha256,
                require_readiness_attestation,
                payload,
                {
                    "id": mission_id,
                    "attempt": attempt,
                    "clarification_turns": turns,
                    "revision_turns": revisions,
                },
                child_cancelled,
                start_gate,
                output_name,
                ipc_nonce,
                self.max_result_bytes,
            ),
            name=f"research-attempt-{mission_id}-{attempt}",
            daemon=False,
        )
        job: _WindowsJob | None = None
        cgroup: _LinuxAttemptCgroup | None = None
        timed_out = False
        escalation_at: float | None = None
        try:
            if deployment_manifest is not None:
                verify_manifest(deployment_manifest)
            verify_runtime_readiness(
                readiness_spec,
                require_attestation=require_readiness_attestation,
                expected_attestation_sha256=readiness_attestation_sha256,
            )
            if require_linux_cgroup and os.name != "nt":
                cgroup = _LinuxAttemptCgroup.create()
            process.start()
            try:
                if os.name == "nt":
                    job = _WindowsJob()
                    job.assign(process)
                elif cgroup is not None:
                    if process.pid is None:
                        raise RunnerIsolationUnavailableError(
                            "runner pid is unavailable for cgroup attachment"
                        )
                    cgroup.attach(process.pid)
            except Exception as exc:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=max(1.0, self.terminate_grace_seconds))
                if process.is_alive() and hasattr(process, "kill"):
                    process.kill()
                    process.join(timeout=1)
                if isinstance(exc, RunnerIsolationUnavailableError):
                    raise
                raise RunnerCleanupError(
                    f"runner could not be assigned to its killable OS boundary: {exc}"
                ) from exc
            start_gate.set()
            deadline = time.monotonic() + float(hard_timeout_seconds)
            while process.is_alive():
                now = time.monotonic()
                if cancelled.is_set():
                    child_cancelled.set()
                    if escalation_at is None:
                        escalation_at = now + self.cancel_grace_seconds
                elif now >= deadline:
                    timed_out = True
                    child_cancelled.set()
                    if escalation_at is None:
                        escalation_at = now + self.cancel_grace_seconds
                if escalation_at is not None and now >= escalation_at:
                    self._stop_tree(process, job, cgroup, graceful=True)
                    process.join(timeout=self.terminate_grace_seconds)
                    if process.is_alive():
                        self._stop_tree(process, job, cgroup, graceful=False)
                    break
                process.join(timeout=0.05)
            process.join(timeout=max(1.0, self.terminate_grace_seconds))
            if not self._prove_cleanup(process, job, cgroup):
                raise RunnerCleanupError("runner process-tree cleanup could not be proven")
            if timed_out:
                raise RunnerTimeoutError(
                    f"runner exceeded hard deadline of {float(hard_timeout_seconds):g} seconds"
                )
            if deployment_manifest is not None:
                verify_manifest(deployment_manifest)
            verify_runtime_readiness(
                readiness_spec,
                require_attestation=require_readiness_attestation,
                expected_attestation_sha256=readiness_attestation_sha256,
            )
            output = Path(output_name)
            if output.is_symlink() or not self._regular(output):
                raise RunnerExecutionError("runner exited without a safe result envelope")
            raw = self._read_bounded(output, self.max_result_bytes + 65_536)
            try:
                envelope = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RunnerExecutionError("runner returned malformed JSON") from exc
            if not isinstance(envelope, dict) or type(envelope.get("ok")) is not bool:
                raise RunnerExecutionError("runner returned an invalid envelope")
            envelope_nonce = envelope.get("nonce")
            if type(envelope_nonce) is not str or not hmac.compare_digest(
                envelope_nonce, ipc_nonce
            ):
                raise RunnerExecutionError("runner envelope authentication failed")
            if not envelope["ok"]:
                error_type = str(envelope.get("error_type") or "RunnerError")
                error = str(envelope.get("error") or "isolated runner failed")
                raise RunnerExecutionError(f"{error_type}: {error}")
            result = envelope.get("result")
            if not isinstance(result, dict):
                raise RunnerExecutionError("runner result is not an object")
            return result
        finally:
            if process.pid is not None and process.is_alive():
                try:
                    self._stop_tree(process, job, cgroup, graceful=False)
                except Exception:
                    pass
                process.join(timeout=1)
            if job is not None:
                try:
                    job.close()
                except Exception:
                    pass
            if cgroup is not None and cgroup.path.exists():
                try:
                    if cgroup.populated():
                        cgroup.terminate()
                        deadline = time.monotonic() + max(
                            1.0, self.terminate_grace_seconds
                        )
                        while time.monotonic() < deadline and cgroup.populated():
                            time.sleep(0.01)
                    if not cgroup.populated():
                        cgroup.close()
                except Exception:
                    pass
            shutil.rmtree(output_directory, ignore_errors=True)


def verify_linux_cgroup_delegation() -> None:
    """Prove production can create and remove an empty per-attempt cgroup."""

    if os.name == "nt":
        return
    cgroup = _LinuxAttemptCgroup.create()
    try:
        if cgroup.populated():
            raise RunnerIsolationUnavailableError(
                "new attempt cgroup is unexpectedly populated"
            )
    finally:
        cgroup.close()


__all__ = [
    "AttemptContext",
    "ProcessSupervisor",
    "RunnerCleanupError",
    "RunnerExecutionError",
    "RunnerIsolationUnavailableError",
    "RunnerReadinessError",
    "RunnerSpec",
    "RunnerTimeoutError",
    "SupervisorError",
    "attest_runner",
    "read_runtime_readiness",
    "verify_runtime_readiness",
    "verify_linux_cgroup_delegation",
    "validate_revision_turns",
]
