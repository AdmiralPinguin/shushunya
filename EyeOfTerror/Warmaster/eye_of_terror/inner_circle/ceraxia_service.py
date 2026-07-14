from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import hashlib
import os
import re
import secrets
import shutil
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

REPO_ROOT = next(
    candidate
    for candidate in Path(__file__).resolve().parents
    if (candidate / "EyeOfTerror" / "model_brain.py").is_file()
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from EyeOfTerror.model_brain import model_contract, request_model_decision
from EyeOfTerror.common_protocol import validate_protocol_payload
from EyeOfTerror.common_protocol.ceraxia_directive import (
    CeraxiaDirectiveError,
    build_ceraxia_directive,
    directive_model_instructions,
    directive_request_payload,
    validate_directive_for_commander,
)

from ..command_text import task_text_from_commander_order
from ..native_code_run import (
    build_native_code_contract,
    load_native_code_run,
    native_governor_plan,
    validate_native_code_run_package,
    write_native_code_run,
)
from .ceraxia import executable_client_action, patch_contract_capabilities


SKITARII_SOURCE_FILES = (
    "service.py", "spec.py", "acceptor.py", "warband.py", "planner.py",
    "executor.py", "explorer.py", "reviewer.py", "clarify.py",
    "mission_store.py", "tools.py", "harness.py",
)
SKITARII_SHARED_SOURCE_FILES = (
    "EyeOfTerror/common_protocol/ceraxia_directive.py",
    "EyeOfTerror/common_protocol/protocol.py",
)
MAX_CERAXIA_REQUEST_BYTES = int(os.environ.get("CERAXIA_MAX_REQUEST_BYTES", "2000000"))
CERAXIA_TRUSTED_ORIGINS_ENV = "CERAXIA_TRUSTED_ORIGINS"
_TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_PREPARE_LOCK = threading.RLock()
ARCHIVE_URL = os.environ.get("CERAXIA_ARCHIVE_URL", "http://127.0.0.1:8090").rstrip("/")
ARCHIVE_API_KEY = os.environ.get("ARCHIVE_API_KEY", "").strip()
TASK_MEMORY_CONTEXT_CHARS = int(os.environ.get("CERAXIA_TASK_MEMORY_CONTEXT_CHARS", "12000"))


class PrepareIdentityConflict(ValueError):
    """An existing run cannot be proven to belong to this prepare request."""


class TaskMemoryContextError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        retryable: bool,
        http_status: int,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable
        self.http_status = http_status


def _fsync_directory(path: Path) -> None:
    """Durably order directory-entry publication where the host supports it."""
    flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0))
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json_durable(path: Path, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _prepare_request_sha256(
    task: str,
    task_id: str,
    command: dict[str, Any],
    task_memory_id: str = "",
) -> str:
    canonical = json.dumps(
        {
            "task": task,
            "task_id": task_id,
            "mission_id": str(command.get("mission_id") or f"mission-{task_id}"),
            "commander_order": command,
            "task_memory_id": task_memory_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _load_task_memory_context(task_memory_id: str) -> dict[str, Any]:
    """Load the exact durable goal page for a fresh leadership decision."""
    memory_id = str(task_memory_id or "").strip()
    if not memory_id or not _TASK_ID_RE.fullmatch(memory_id):
        raise TaskMemoryContextError(
            "Ceraxia received an invalid task-memory identity",
            error_code="task_memory_identity_invalid",
            retryable=False,
            http_status=409,
        )
    headers = {"Accept": "application/json"}
    if ARCHIVE_API_KEY:
        if any(char in ARCHIVE_API_KEY for char in "\r\n"):
            raise TaskMemoryContextError(
                "Ceraxia Archive API key contains invalid control characters",
                error_code="task_memory_auth_invalid",
                retryable=False,
                http_status=502,
            )
        headers["Authorization"] = f"Bearer {ARCHIVE_API_KEY}"
    request = Request(
        f"{ARCHIVE_URL}/archive/task-page?task_memory_id={quote(memory_id, safe='')}",
        headers=headers,
        method="GET",
    )
    try:
        with urlopen(request, timeout=10.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        retryable = exc.code >= 500 or exc.code in {408, 425, 429}
        raise TaskMemoryContextError(
            f"Archive rejected Ceraxia task-memory read with HTTP {exc.code}",
            error_code=(
                "task_memory_unavailable" if retryable else "task_memory_read_rejected"
            ),
            retryable=retryable,
            http_status=503 if retryable else 502,
        ) from exc
    except (URLError, OSError, TimeoutError) as exc:
        raise TaskMemoryContextError(
            f"Archive task memory is temporarily unavailable: {exc}",
            error_code="task_memory_unavailable",
            retryable=True,
            http_status=503,
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskMemoryContextError(
            f"Archive returned invalid task-memory JSON: {exc}",
            error_code="task_memory_invalid_response",
            retryable=True,
            http_status=503,
        ) from exc
    if not isinstance(payload, dict):
        raise TaskMemoryContextError(
            "Archive returned a non-object task-memory response",
            error_code="task_memory_invalid_response",
            retryable=True,
            http_status=503,
        )
    returned_id = str(payload.get("task_memory_id") or "").strip()
    if returned_id != memory_id:
        raise TaskMemoryContextError(
            "Archive task-memory identity does not match Ceraxia request",
            error_code="task_memory_identity_conflict",
            retryable=False,
            http_status=409,
        )
    content = str(payload.get("context") or payload.get("content") or "")[
        : max(1_000, TASK_MEMORY_CONTEXT_CHARS)
    ]
    if not content or not isinstance(payload.get("snapshot"), dict):
        raise TaskMemoryContextError(
            "Archive has not initialised the required task page yet",
            error_code="task_memory_not_initialised",
            retryable=True,
            http_status=503,
        )
    return {
        "task_memory_id": memory_id,
        "root_task_id": str(payload.get("root_task_id") or ""),
        "available": True,
        "revision": int(payload.get("revision") or 0),
        "sha256": str(payload.get("sha256") or payload.get("snapshot_sha256") or "")[:64],
        "content": content,
    }


def _load_prepare_replay(
    run_dir: Path,
    request_sha256: str,
    command: dict[str, Any],
) -> dict[str, Any]:
    receipt_path = run_dir / "native_run_receipt.json"
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise PrepareIdentityConflict(
            "existing run has no native Ceraxia prepare receipt; create a fresh run",
        )
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrepareIdentityConflict(f"prepare receipt is unreadable: {exc}") from exc
    if not isinstance(receipt, dict):
        raise PrepareIdentityConflict("prepare receipt must be an object")
    if receipt.get("kind") != "native_code_run_receipt" or receipt.get("version") != 1:
        raise PrepareIdentityConflict("existing run has an unsupported prepare receipt")
    if not hmac.compare_digest(
        str(receipt.get("prepare_request_sha256") or ""),
        request_sha256,
    ):
        raise PrepareIdentityConflict("existing run belongs to a different prepare request")
    errors = validate_native_code_run_package(run_dir)
    if errors:
        raise PrepareIdentityConflict("existing native run is invalid: " + "; ".join(errors))
    package = load_native_code_run(run_dir)
    contract = package.get("contract") if isinstance(package.get("contract"), dict) else {}
    task_id = str(contract.get("task_id") or "")
    mission_id = str(contract.get("mission_id") or "")
    directive_path = run_dir / "ceraxia_directive.json"
    try:
        directive_payload = json.loads(directive_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrepareIdentityConflict(f"persisted directive is unreadable: {exc}") from exc
    directive = validate_directive_for_commander(
        directive_payload,
        command,
        expected_task_id=task_id,
        expected_mission_id=mission_id,
        require_delegation=True,
    )
    plan_payload = native_plan_payload(
        contract["goal"],
        task_id,
        command,
        backend=skitarii_backend_health(),
    )
    governor_plan_payload = package.get("governor_plan")
    status_payload = package.get("status")
    return _with_execution_contract({
        "ok": True,
        "governor": "Ceraxia",
        "model_brain": {
            "status": "persisted",
            "reason": "idempotent replay of the original leadership decision",
        },
        "contract": contract,
        "governor_plan": governor_plan_payload,
        "leadership_directive": directive,
        "status": status_payload,
        "phase": "run_prepared",
        "prepare_replayed": True,
        "decision": {
            "can_handoff_to_abaddon": True,
            "delegated_to": "SkitariiWarband",
            "recommended_kind": "start_native_code_run",
        },
        "display": {
            "headline": "Native code run already prepared",
            "detail": "The original persisted Ceraxia decision was replayed safely",
            "severity": "info",
            "task_id": task_id,
        },
        "next_action": {},
        "client_action": {},
        "pipeline": plan_payload.get("pipeline", {}),
    }, plan_payload.get("active_execution_backend"))


def expected_skitarii_source_sha256() -> str:
    repo_root = Path(
        os.environ.get("SHUSHUNYA_REPO_ROOT", "/media/shushunya/SHUSHUNYA/shushunya"),
    ).resolve()
    if not repo_root.is_dir():
        repo_root = Path(__file__).resolve().parents[4]
    source_root = repo_root / "EyeOfTerror" / "Mechanicum" / "Skitarii"
    digest = hashlib.sha256()
    try:
        for name in SKITARII_SOURCE_FILES:
            digest.update(name.encode("utf-8") + b"\0")
            digest.update((source_root / name).read_bytes())
        for relative in SKITARII_SHARED_SOURCE_FILES:
            digest.update(relative.encode("utf-8") + b"\0")
            digest.update((repo_root / relative).read_bytes())
    except OSError:
        return ""
    return digest.hexdigest()


def required_workers() -> list[str]:
    # Skitarii is a warband backend, not a synthetic Mechanicum worker.  Its
    # readiness is attested separately by ``skitarii_backend_health``.
    return []


def skitarii_backend_health(timeout_sec: float = 1.0) -> dict[str, Any]:
    """Attest the native Skitarii warband backend used by every code run."""
    endpoint = os.environ.get(
        "SKITARII_URL",
        os.environ.get("SKITARII_WARBAND_URL", "http://127.0.0.1:7200"),
    ).rstrip("/")
    health_endpoint = f"{endpoint}/health?vm=1"
    payload: dict[str, Any] = {}
    try:
        headers = {"Accept": "application/json"}
        bearer = os.environ.get("SKITARII_BEARER_TOKEN", "")
        if bearer:
            if any(char in bearer for char in "\r\n"):
                raise ValueError("invalid Skitarii bearer token")
            headers["Authorization"] = f"Bearer {bearer}"
        request = Request(health_endpoint, headers=headers, method="GET")
        with urlopen(request, timeout=timeout_sec) as backend_response:  # noqa: S310 - operator-configured local service URL
            status_code = int(getattr(backend_response, "status", 200))
            raw = backend_response.read().decode("utf-8")
        decoded = json.loads(raw)
        payload = decoded if isinstance(decoded, dict) else {}
        reported_ok = payload.get("ok")
        if reported_ok is None:
            reported_ok = str(payload.get("status") or "").lower() in {"ok", "healthy", "ready"}
        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        models = identity.get("models") if isinstance(identity.get("models"), dict) else {}
        required_models = {"planner", "reviewer", "spec", "fighter", "held_out"}
        expected_source = expected_skitarii_source_sha256()
        identity_ready = (
            bool(expected_source)
            and str(identity.get("source_sha256") or "") == expected_source
            and bool(str(identity.get("instance_id") or ""))
            and identity.get("held_out_required") is True
            and required_models.issubset(models)
            and all(
                isinstance(models.get(role), dict) and bool(str(models[role].get("model") or ""))
                for role in required_models
            )
        )
        healthy = (
            200 <= status_code < 300
            and bool(reported_ok)
            and payload.get("vm_alive") is True
            and payload.get("process_boundary_ready") is True
            and identity_ready
        )
        error = "" if healthy else (
            "backend identity, hidden-verifier policy, sandbox VM, or process boundary is not ready"
        )
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        healthy = False
        error = f"{type(exc).__name__}: {exc}"
    return {
        "name": "SkitariiWarband",
        "kind": "vm_isolated_code_warband",
        "endpoint": endpoint,
        "health_endpoint": health_endpoint,
        "healthy": healthy,
        "lifecycle": "active",
        "status": "healthy" if healthy else "unavailable",
        "health": payload,
        "error": error,
        "dispatch_owner": "native_code_backend_router",
        "contract_relation": "executes one native Ceraxia-delegated code mission",
    }


def _native_pipeline(pipeline: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(pipeline)
    enriched.update(
        {
            "mode": "native_skitarii_mission",
            "authoritative": True,
            "purpose": "one leadership handoff to one warband mission",
            "active_execution_backend": "SkitariiWarband",
            "execution_handoff": "native_code_backend_router",
        }
    )
    return enriched


def _with_execution_contract(payload: dict[str, Any], backend: dict[str, Any] | None = None) -> dict[str, Any]:
    """Expose one consistent contract on every public Ceraxia response."""
    backend_payload = backend or skitarii_backend_health()
    enriched = dict(payload)
    pipeline = enriched.get("pipeline") if isinstance(enriched.get("pipeline"), dict) else {}
    nested_plan = enriched.get("plan")
    if not pipeline and isinstance(nested_plan, dict) and isinstance(nested_plan.get("pipeline"), dict):
        pipeline = nested_plan["pipeline"]
    if pipeline:
        enriched["pipeline"] = _native_pipeline(pipeline)
    enriched.update(
        {
            "api_version": 2,
            "contract_mode": "native_skitarii_mission_v2",
            "required_workers": required_workers(),
            "active_execution_backend": backend_payload,
            "execution_contract": {
                "planning_and_preflight": "ceraxia_leadership_only",
                "execution": "SkitariiWarband",
                "handoff": "native_code_backend_router",
                "backend_healthy": bool(backend_payload.get("healthy")),
                "leadership": "Ceraxia",
                "detailed_planning": "SkitariiWarband",
                "native_directive_artifact": "ceraxia_directive.json",
                "leadership_contract": "native_ceraxia_directive_v1",
                "legacy_worker_plan_present": False,
            },
        }
    )
    if isinstance(nested_plan, dict):
        nested = dict(nested_plan)
        nested_pipeline = nested.get("pipeline") if isinstance(nested.get("pipeline"), dict) else {}
        if nested_pipeline:
            nested["pipeline"] = _native_pipeline(nested_pipeline)
        nested.update(
            {
                "api_version": 2,
                "contract_mode": "native_skitarii_mission_v2",
                "required_workers": required_workers(),
                "active_execution_backend": backend_payload,
                "execution_contract": enriched["execution_contract"],
            }
        )
        enriched["plan"] = nested
    return enriched


def _bind_commander_order(payload: dict[str, Any], command: dict[str, Any]) -> dict[str, Any]:
    """Make returned prepare actions executable with the validated protocol object."""
    def walk(value: Any) -> Any:
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not isinstance(value, dict):
            return value
        current = {key: walk(item) for key, item in value.items()}
        if current.get("kind") == "prepare_run" and str(current.get("method") or "").upper() == "POST":
            body = dict(current.get("body") if isinstance(current.get("body"), dict) else {})
            body["commander_order"] = command
            current["body"] = body
            current.pop("requires", None)
        return current

    return walk(payload)


def pipeline_summary() -> dict[str, Any]:
    return {
        "kind": "native_code_run",
        "step_count": 1,
        "required_workers": [],
        "steps": [
            {
                "step_id": "skitarii",
                "backend": "SkitariiWarband",
                "depends_on": [],
                "ownership": (
                    "repository exploration, detailed planning, implementation, "
                    "verification, and internal repair"
                ),
            }
        ],
    }


def oversight_template() -> dict[str, Any]:
    return {
        "governor": "Ceraxia",
        "kind": "native_code_leadership_oversight",
        "leader_owns": [
            "delegation decision",
            "mission intent",
            "priorities and constraints",
            "success and escalation conditions",
        ],
        "warband_owns": [
            "repository exploration",
            "detailed planning",
            "implementation",
            "verification",
            "internal repair",
        ],
    }


def _header_values(headers: Any, name: str) -> list[str]:
    getter = getattr(headers, "get_all", None)
    if callable(getter):
        return [str(value) for value in (getter(name) or [])]
    value = headers.get(name) if hasattr(headers, "get") else None
    return [] if value is None else [str(value)]


def _literal_loopback_authority(value: str) -> tuple[str, int] | None:
    """Parse a literal loopback Host/origin authority without DNS."""
    raw = str(value or "")
    if (
        not raw or any(char.isspace() for char in raw)
        or any(char in raw for char in "/\\@,%")
    ):
        return None
    host = raw
    port_text = ""
    if raw.startswith("["):
        close = raw.find("]")
        if close < 0:
            return None
        host = raw[1:close]
        suffix = raw[close + 1:]
        if suffix:
            if not suffix.startswith(":"):
                return None
            port_text = suffix[1:]
    elif raw.count(":") == 1:
        host, port_text = raw.rsplit(":", 1)
    elif ":" in raw:
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    mapped = getattr(address, "ipv4_mapped", None)
    if not address.is_loopback and not (mapped and mapped.is_loopback):
        return None
    if port_text:
        if not port_text.isascii() or not port_text.isdigit():
            return None
        port = int(port_text)
        if not 1 <= port <= 65535:
            return None
    else:
        port = 80
    return address.compressed, port


def _peer_allowed(value: str) -> bool:
    try:
        address = ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(address.is_loopback or (mapped and mapped.is_loopback))


def _host_allowed(headers: Any) -> bool:
    values = _header_values(headers, "Host")
    return len(values) == 1 and _literal_loopback_authority(values[0]) is not None


def _canonical_http_origin(value: str) -> str:
    raw = str(value or "").strip()
    if (
        not raw or raw == "null" or "," in raw or "\\" in raw
        or any(char.isspace() for char in raw)
    ):
        return ""
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return ""
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if (
        scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return ""
    rendered_host = f"[{host}]" if ":" in host else host
    default_port = 80 if scheme == "http" else 443
    suffix = "" if port is None or port == default_port else f":{port}"
    return f"{scheme}://{rendered_host}{suffix}"


def _trusted_origins() -> set[str]:
    return {
        canonical
        for item in os.environ.get(CERAXIA_TRUSTED_ORIGINS_ENV, "").split(",")
        if (canonical := _canonical_http_origin(item))
    }


def _cors_origin_for_headers(headers: Any) -> str:
    origins = _header_values(headers, "Origin")
    hosts = _header_values(headers, "Host")
    if len(origins) != 1 or len(hosts) != 1:
        return ""
    canonical = _canonical_http_origin(origins[0])
    if not canonical:
        return ""
    if canonical in _trusted_origins():
        return canonical
    try:
        parsed = urlsplit(canonical)
    except ValueError:
        return ""
    if parsed.scheme != "http":
        return ""
    origin_authority = _literal_loopback_authority(parsed.netloc)
    host_authority = _literal_loopback_authority(hosts[0])
    return canonical if origin_authority and origin_authority == host_authority else ""


def _state_change_origin_policy(headers: Any) -> tuple[bool, str]:
    origins = _header_values(headers, "Origin")
    fetch_sites = _header_values(headers, "Sec-Fetch-Site")
    fetch_site = fetch_sites[0].strip().lower() if len(fetch_sites) == 1 else ""
    if not origins:
        if fetch_site == "cross-site":
            return False, "cross-site POST requests are forbidden"
        return True, ""
    if len(origins) != 1:
        return False, "exactly one Origin header is required"
    if not _cors_origin_for_headers(headers):
        return False, "cross-origin POST requests are forbidden"
    return True, ""


def _post_authorized(headers: Any) -> bool:
    expected = os.environ.get("CERAXIA_BEARER_TOKEN", "")
    if not expected:
        return True
    if any(char in expected for char in "\r\n"):
        return False
    values = _header_values(headers, "Authorization")
    if len(values) != 1:
        return False
    return hmac.compare_digest(values[0], f"Bearer {expected}")


def _json_content_type(headers: Any) -> bool:
    values = _header_values(headers, "Content-Type")
    return len(values) == 1 and values[0].split(";", 1)[0].strip().lower() == "application/json"


def response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    cors_origin = _cors_origin_for_headers(handler.headers)
    if cors_origin:
        handler.send_header("Access-Control-Allow-Origin", cors_origin)
        handler.send_header("Vary", "Origin")
    handler.end_headers()
    handler.wfile.write(data)


def payload_from(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    if not _json_content_type(handler.headers):
        raise ValueError("Content-Type must be application/json")
    if _header_values(handler.headers, "Transfer-Encoding"):
        raise ValueError("Transfer-Encoding is not supported")
    lengths = _header_values(handler.headers, "Content-Length")
    if (
        len(lengths) != 1
        or not lengths[0].isascii()
        or not lengths[0].isdigit()
    ):
        raise ValueError("exactly one decimal Content-Length is required")
    length = int(lengths[0])
    if length > MAX_CERAXIA_REQUEST_BYTES:
        raise ValueError(f"request body exceeds {MAX_CERAXIA_REQUEST_BYTES} bytes")
    raw = handler.rfile.read(length)
    if len(raw) != length:
        raise ValueError("request body ended before Content-Length")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"request body is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def task_from_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    command = payload.get("commander_order") if isinstance(payload.get("commander_order"), dict) else {}
    if command:
        validate_protocol_payload(command, expected_type="commander_order")
        task = task_text_from_commander_order(command)
        return task, command
    raise ValueError("commander_order is required; direct governor task input is not accepted")


def request_leadership_directive(
    task: str,
    task_id: str,
    command: dict[str, Any],
    task_memory_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ask Ceraxia for a leader decision, not a detailed implementation plan."""
    mission_id = str(command.get("mission_id") or f"mission-{task_id}")
    request_payload = directive_request_payload(task, task_id, command)
    if task_memory_context:
        request_payload["task_memory"] = task_memory_context
    model_decision = request_model_decision(
        "Ceraxia",
        "Leader of the coding warband",
        request_payload,
        layer="governor_service",
        instructions=directive_model_instructions(),
    )
    directive = build_ceraxia_directive(
        model_decision,
        task_id=task_id,
        mission_id=mission_id,
        commander_order=command,
    )
    return directive, model_decision


def native_plan_payload(
    task: str,
    task_id: str | None,
    command: dict[str, Any],
    *,
    backend: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the structural one-mission shape without asking the leader model."""
    mission_id = str(command.get("mission_id") or "")
    contract = build_native_code_contract(task, task_id, mission_id=mission_id)
    plan = native_governor_plan(contract, command)
    backend_payload = backend or skitarii_backend_health()
    pipeline = _native_pipeline(pipeline_summary())
    ready = bool(backend_payload.get("healthy"))
    next_action = {
        "kind": "prepare_run" if ready else "inspect_capabilities",
        "method": "POST" if ready else "GET",
        "endpoint": "POST /prepare_run" if ready else "GET /capabilities",
        "body": {"task_id": contract["task_id"]} if ready else {},
        "requires": ["commander_order"] if ready else [],
        "reason": (
            "native run shape is valid; ask Ceraxia for one leadership decision"
            if ready else "SkitariiWarband backend is not ready"
        ),
    }
    payload = {
        "ok": ready,
        "governor": "Ceraxia",
        "api_version": 3,
        "contract": contract,
        "governor_plan": plan,
        "pipeline": pipeline,
        "oversight": oversight_template(),
        "leadership_authorization": "pending_prepare",
        "validation": {"ok": True, "errors": []},
        "missing_workers": [],
        "unavailable_workers": [],
        "actions": {
            "can_prepare_run": ready,
            "can_inspect_capabilities": True,
            "next_action": next_action,
        },
        "phase": "native_plan_ready" if ready else "native_plan_blocked",
        "decision": {
            "can_prepare_run": ready,
            "recommended_kind": str(next_action.get("kind") or ""),
            "recommended_endpoint": str(next_action.get("endpoint") or ""),
        },
        "display": {
            "headline": "Native Skitarii mission is ready" if ready else "Skitarii backend is unavailable",
            "detail": str(next_action.get("reason") or ""),
            "severity": "info" if ready else "warning",
            "task_id": contract["task_id"],
            "step_count": 1,
        },
        "next_action": next_action,
        "client_action": executable_client_action(contract["task_id"], next_action),
    }
    return _with_execution_contract(payload, backend_payload)


_REPO_MARKER = re.compile(r"(?mi)^\s*CERAXIA_TARGET_REPO:\s*(.+?)\s*$")


def _configured_repo_path(repo_path: str) -> str:
    requested = str(repo_path or "").strip()
    if not requested:
        return ""
    resolved = Path(requested).expanduser().resolve()
    configured = REPO_ROOT.resolve()
    if resolved != configured:
        raise ValueError(
            f"repo_path is restricted to the configured repository: {configured}"
        )
    return str(configured)


def task_with_repo_marker(task: str, repo_path: str) -> str:
    """Validate repo scope and remove host-only absolute paths from the fighter goal."""
    marker_paths = [match.group(1).strip() for match in _REPO_MARKER.finditer(task)]
    requested = _configured_repo_path(repo_path)
    for marker_path in marker_paths:
        marker_resolved = _configured_repo_path(marker_path)
        if requested and marker_resolved != requested:
            raise ValueError("repo_path conflicts with CERAXIA_TARGET_REPO")
        requested = requested or marker_resolved
    normalized = _REPO_MARKER.sub("", task).strip()
    if requested and "CERAXIA_REPOSITORY_SCOPE:" not in normalized:
        normalized += "\nCERAXIA_REPOSITORY_SCOPE: configured repository preloaded in current workdir"
    return normalized


def callable_contract_payload(task: str, task_id: str | None, repo_path: str = "", constraints: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized_task = task_with_repo_marker(task, repo_path)
    backend = skitarii_backend_health()
    contract = build_native_code_contract(normalized_task, task_id)
    return _with_execution_contract({
        "ok": bool(backend.get("healthy")),
        "governor": "Ceraxia",
        "api_version": 3,
        "callable_kind": "native_code_warband",
        "model_brain": model_contract("Ceraxia", "Inner Circle code task governor", layer="governor_callable"),
        "task_id": contract["task_id"],
        "normalized_task": normalized_task,
        "contract": contract,
        "input_contract": {
            "required": ["commander_order"],
            "optional": ["task_id", "repo_path", "run_dir"],
            "repo_scope": "configured_repository_only",
            "configured_repo_path": str(REPO_ROOT.resolve()),
            "run_dir_scope": "exact task child of a configured run root",
            "prepare_semantics": "idempotent by canonical commander request hash",
            "constraints_status": "preserved by the Ceraxia directive validator",
            "received_constraints": constraints or {},
        },
        "execution_flow": [
            {"step": 1, "method": "POST", "endpoint": "/prepare_run", "purpose": "make one Ceraxia leadership decision and persist one native run"},
            {"step": 2, "method": "POST", "endpoint": "Abaddon /runs/{task_id}/start_*", "purpose": "execute the declared Skitarii backend"},
            {"step": 3, "method": "GET", "endpoint": "Abaddon /runs/{task_id}/final", "purpose": "retrieve the verified terminal result"},
        ],
        "pipeline": _native_pipeline(pipeline_summary()),
        "patch_contract": patch_contract_capabilities(),
        "next_action": {
            "kind": "prepare_run",
            "method": "POST",
            "endpoint": "POST /prepare_run",
            "body": {"task_id": contract["task_id"], "repo_path": repo_path},
            "requires": ["commander_order"],
            "reason": "ask Ceraxia for the single authoritative leadership decision",
        },
    }, backend)


def service_capabilities() -> dict[str, Any]:
    pipeline = _native_pipeline(pipeline_summary())
    backend = skitarii_backend_health()
    next_action = {
        "kind": "plan_task",
        "method": "POST",
        "endpoint": "POST /plan",
        "body": {},
        "body_schema": {"commander_order": "protocol object", "task_id": "optional string"},
        "reason": "inspect the native one-mission code run shape without invoking the leader model",
    }
    return _with_execution_contract({
        "ok": bool(backend.get("healthy")),
        "governor": "Ceraxia",
        "api_version": 3,
        "task_kinds": ["code"],
        "required_workers": [],
        "worker_availability": {
            "ok": bool(backend.get("healthy")),
            "scope": "native_warband_backend",
            "missing_workers": [],
            "unavailable_workers": [],
        },
        "model_brain": model_contract("Ceraxia", "Inner Circle code task governor", layer="governor_service"),
        "pipeline": pipeline,
        "patch_contract": patch_contract_capabilities(),
        "oversight": oversight_template(),
        "summary": {
            "pipeline_kind": str(pipeline.get("kind") or ""),
            "step_count": 1,
            "required_worker_count": 0,
            "worker_availability_ok": bool(backend.get("healthy")),
            "active_backend_healthy": bool(backend.get("healthy")),
        },
        "display": {
            "headline": "Ceraxia capabilities",
            "detail": f"One native Skitarii mission; backend {backend.get('status')}",
            "severity": "info" if backend.get("healthy") else "warning",
        },
        "next_action": next_action,
        "client_action": executable_client_action("", next_action),
        "capabilities": [
            "single_authoritative_leadership_decision",
            "validated_leadership_directive",
            "ceraxia_to_skitarii_delegation",
            "idempotent_native_run_prepare",
            "bounded_exact_snapshot_with_hashed_external_assets",
            "vm_isolated_agentic_execution",
            "private_held_out_behavioral_verification",
            "host_rerun_of_public_and_held_out_checks",
            "advisory_sampled_diff_review",
            "mandatory_patch_staging",
            "clarify_cancel_resume",
        ],
        "endpoints": [
            "GET /health",
            "GET /capabilities",
            "POST /plan",
            "POST /callable_contract",
            "POST /prepare_run",
        ],
    }, backend)


def resolve_run_dir(
    default_run_root: Path,
    requested: str,
    task_id: str,
    *,
    allow_existing: bool = False,
) -> Path:
    root = default_run_root.resolve()
    if not _TASK_ID_RE.fullmatch(str(task_id or "")) or ".." in task_id:
        raise ValueError("task_id is not safe for a run directory")
    expected_paths = {root / task_id}
    candidate = Path(requested).expanduser() if requested else root / task_id
    if not candidate.is_absolute():
        candidate = root / candidate
    # Lexical equality blocks ``other-run/../task`` aliases; resolution and the
    # explicit symlink check then block filesystem aliases and cross-run writes.
    lexical = Path(os.path.abspath(candidate))
    if lexical not in expected_paths:
        raise ValueError("run_dir must be the exact task-scoped child of a configured run root")
    if candidate.is_symlink() or lexical.is_symlink():
        raise ValueError("run_dir must not be a symlink")
    resolved = candidate.resolve()
    if resolved not in expected_paths:
        raise ValueError("run_dir resolves outside its exact task-scoped location")
    if os.path.lexists(resolved):
        metadata = os.lstat(resolved)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("existing task-scoped run path is not a real directory")
        if not allow_existing:
            raise FileExistsError("task-scoped run directory already exists")
    return resolved


def make_handler(default_run_root: Path) -> type[BaseHTTPRequestHandler]:
    class CeraxiaHandler(BaseHTTPRequestHandler):
        server_version = "Ceraxia/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _request_boundary_allowed(self) -> bool:
            if not _peer_allowed(str(self.client_address[0])) or not _host_allowed(self.headers):
                response(
                    self, 421,
                    {"ok": False, "error": "Ceraxia requires a literal loopback peer and Host"},
                )
                return False
            return True

        def do_OPTIONS(self) -> None:  # noqa: N802
            if not self._request_boundary_allowed():
                return
            allowed, error = _state_change_origin_policy(self.headers)
            if not allowed:
                response(self, 403, {"ok": False, "error": error})
                return
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            cors_origin = _cors_origin_for_headers(self.headers)
            if cors_origin:
                self.send_header("Access-Control-Allow-Origin", cors_origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.send_header(
                    "Access-Control-Allow-Headers", "Content-Type, Accept, Authorization",
                )
                self.send_header("Access-Control-Max-Age", "300")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if not self._request_boundary_allowed():
                return
            if self.path == "/health":
                backend = skitarii_backend_health()
                ready = bool(backend.get("healthy"))
                response(self, 200 if ready else 503, {
                    "ok": ready,
                    "governor": "Ceraxia",
                    "liveness": True,
                    "readiness": ready,
                    "backend": backend,
                })
                return
            if self.path == "/capabilities":
                response(self, 200, service_capabilities())
                return
            response(self, 404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._request_boundary_allowed():
                return
            allowed, origin_error = _state_change_origin_policy(self.headers)
            if not allowed:
                response(self, 403, {"ok": False, "error": origin_error})
                return
            if not _post_authorized(self.headers):
                response(self, 401, {"ok": False, "error": "Ceraxia bearer authentication failed"})
                return
            if self.path not in {"/plan", "/callable_contract", "/prepare_run"}:
                response(self, 404, {"ok": False, "error": "not found"})
                return
            if not _json_content_type(self.headers):
                response(self, 415, {"ok": False, "error": "Content-Type must be application/json"})
                return
            try:
                payload = payload_from(self)
                task, command = task_from_payload(payload)
                if not task:
                    response(self, 400, {"ok": False, "error": "task is required"})
                    return
                repo_path = str(payload.get("repo_path") or "").strip()
                task = task_with_repo_marker(task, repo_path)
                task_id = str(payload.get("task_id") or "").strip() or None
                task_memory_id = str(payload.get("task_memory_id") or task_id or "").strip()
                if task_memory_id and not _TASK_ID_RE.fullmatch(task_memory_id):
                    response(self, 400, {"ok": False, "error": "invalid task_memory_id"})
                    return
                if self.path == "/plan":
                    plan_payload = native_plan_payload(task, task_id, command)
                    plan_payload = _bind_commander_order(plan_payload, command)
                    response(self, 200, plan_payload)
                    return
                if self.path == "/callable_contract":
                    constraints = payload.get("constraints") if isinstance(payload.get("constraints"), dict) else {}
                    contract_payload = callable_contract_payload(task, task_id, repo_path=repo_path, constraints=constraints)
                    contract_payload = _bind_commander_order(contract_payload, command)
                    response(self, 200, contract_payload)
                    return
                if self.path == "/prepare_run":
                    plan_payload = native_plan_payload(task, task_id, command)
                    contract = plan_payload["contract"]
                    run_dir = resolve_run_dir(
                        default_run_root,
                        str(payload.get("run_dir") or ""),
                        contract["task_id"],
                        allow_existing=True,
                    )
                    request_sha256 = _prepare_request_sha256(
                        task,
                        contract["task_id"],
                        command,
                        task_memory_id,
                    )
                    with _PREPARE_LOCK:
                        if run_dir.exists():
                            replay = _load_prepare_replay(
                                run_dir,
                                request_sha256,
                                command,
                            )
                            response(self, 200, replay)
                            return
                        backend = skitarii_backend_health()
                        if not backend.get("healthy"):
                            response(
                                self,
                                503,
                                {
                                    "ok": False,
                                    "governor": "Ceraxia",
                                    "error": "SkitariiWarband backend is not ready",
                                    "error_code": "skitarii_backend_unavailable",
                                    "backend": backend,
                                },
                            )
                            return
                        try:
                            task_memory_context = _load_task_memory_context(task_memory_id)
                            leadership_directive, model_decision = request_leadership_directive(
                                task,
                                contract["task_id"],
                                command,
                                task_memory_context,
                            )
                        except TaskMemoryContextError as exc:
                            response(
                                self,
                                exc.http_status,
                                {
                                    "ok": False,
                                    "retryable": exc.retryable,
                                    "governor": "Ceraxia",
                                    "error": str(exc),
                                    "error_code": exc.error_code,
                                    "task_memory_id": task_memory_id,
                                },
                            )
                            return
                        except CeraxiaDirectiveError as exc:
                            response(
                                self,
                                502,
                                {
                                    "ok": False,
                                    "governor": "Ceraxia",
                                    "error": str(exc),
                                    "error_code": "invalid_leadership_directive",
                                },
                            )
                            return
                        if leadership_directive["decision"] != "delegate":
                            response(
                                self,
                                409,
                                {
                                    "ok": False,
                                    "governor": "Ceraxia",
                                    "error": "Ceraxia did not authorize delegation to Skitarii",
                                    "error_code": "delegation_not_authorized",
                                    "leadership_directive": leadership_directive,
                                    "model_brain": model_decision,
                                },
                            )
                            return
                        run_dir.parent.mkdir(parents=True, exist_ok=True)
                        staging_dir = run_dir.with_name(
                            f".{run_dir.name}.prepare-{secrets.token_hex(8)}"
                        )
                        os.mkdir(staging_dir, mode=0o755)
                        reserved_metadata = os.lstat(staging_dir)
                        reserved_identity = (reserved_metadata.st_dev, reserved_metadata.st_ino)
                        try:
                            governor_plan_payload = native_governor_plan(contract, command)
                            status = write_native_code_run(
                                staging_dir,
                                contract,
                                leadership_directive,
                                governor_plan_payload,
                                prepare_request_sha256=request_sha256,
                                published_run_dir=run_dir,
                            )
                            _write_json_durable(
                                staging_dir / "task_memory_context.json",
                                task_memory_context,
                            )
                            try:
                                _fsync_directory(staging_dir)
                            except OSError:
                                # Some hosts (notably Windows) cannot open a
                                # directory for fsync. Linux production can.
                                pass
                            # The final run directory becomes visible only after
                            # every required package and memory file is complete.
                            # A crash before this rename leaves at most an
                            # unreferenced hidden staging directory; a crash after
                            # it is an idempotently replayable prepared run.
                            os.replace(staging_dir, run_dir)
                            try:
                                _fsync_directory(run_dir.parent)
                            except OSError:
                                # Windows cannot fsync a directory. Atomic rename
                                # is still the strongest available publication.
                                pass
                            response_payload = _with_execution_contract({
                                "ok": True,
                                "governor": "Ceraxia",
                                "model_brain": model_decision,
                                "contract": contract,
                                "governor_plan": governor_plan_payload,
                                "leadership_directive": leadership_directive,
                                "status": status,
                                "phase": "run_prepared",
                                "prepare_replayed": False,
                                "decision": {
                                    "can_handoff_to_abaddon": True,
                                    "delegated_to": "SkitariiWarband",
                                    "recommended_kind": "start_native_code_run",
                                },
                                "display": {
                                    "headline": "Native code run prepared",
                                    "detail": "One Ceraxia directive and one Skitarii mission were persisted",
                                    "severity": "info",
                                    "task_id": contract["task_id"],
                                },
                                "next_action": {},
                                "client_action": {},
                                "pipeline": plan_payload.get("pipeline", {}),
                            }, backend)
                        except Exception:
                            try:
                                current = os.lstat(staging_dir)
                                if (
                                    stat.S_ISDIR(current.st_mode)
                                    and (current.st_dev, current.st_ino) == reserved_identity
                                ):
                                    shutil.rmtree(staging_dir)
                            except OSError:
                                pass
                            raise
                    response(self, 200, response_payload)
                    return
                response(self, 404, {"ok": False, "error": "not found"})
            except PrepareIdentityConflict as exc:
                response(
                    self,
                    409,
                    {
                        "ok": False,
                        "governor": "Ceraxia",
                        "error": str(exc),
                        "error_code": "prepare_identity_conflict",
                    },
                )
            except FileExistsError as exc:
                response(self, 409, {"ok": False, "governor": "Ceraxia", "error": str(exc)})
            except ValueError as exc:
                response(self, 400, {"ok": False, "governor": "Ceraxia", "error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                response(self, 500, {"ok": False, "governor": "Ceraxia", "error": str(exc)})

    return CeraxiaHandler


def _validate_bind_host(host: str) -> str:
    raw = str(host or "")
    if "%" in raw:
        raise ValueError("Ceraxia bind host must be a literal loopback address")
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise ValueError("Ceraxia bind host must be a literal loopback address") from exc
    mapped = getattr(address, "ipv4_mapped", None)
    if not address.is_loopback and not (mapped and mapped.is_loopback):
        raise ValueError("Ceraxia cannot bind an unauthenticated endpoint off loopback")
    return raw


def serve(host: str, port: int, default_run_root: Path) -> None:
    host = _validate_bind_host(host)
    default_run_root.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), make_handler(default_run_root))
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve Ceraxia as an Inner Circle code governor.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7104)
    parser.add_argument(
        "--default-run-root",
        default=os.environ.get("WARMMASTER_RUN_ROOT", "runtime/warmaster-runs"),
    )
    args = parser.parse_args()
    serve(args.host, args.port, Path(args.default_run_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
