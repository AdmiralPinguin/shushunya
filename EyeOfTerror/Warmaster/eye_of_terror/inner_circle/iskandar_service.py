"""Fail-closed Iskandar leadership facade for native ResearchWarband runs.

Port 7101 owns only the leadership decision and persistence of the exact native
handoff package.  Detailed research planning and execution belong to the
bearer-protected ResearchWarband service on loopback port 7201.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import re
import shutil
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


REPO_ROOT = next(
    candidate
    for candidate in Path(__file__).resolve().parents
    if (candidate / "EyeOfTerror" / "common_protocol" / "iskandar_directive.py").is_file()
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from EyeOfTerror.model_brain import model_contract, request_model_decision
from EyeOfTerror.common_protocol.iskandar_directive import (
    IskandarDirectiveError,
    build_iskandar_directive,
    directive_model_instructions,
    directive_request_payload,
    validate_directive_for_commander,
)

from ..command_text import task_text_from_commander_order
from ..native_research_run import (
    build_native_research_contract,
    load_native_research_run,
    native_research_governor_plan,
    native_research_prepare_request_sha256,
    validate_native_research_commander_order,
    validate_native_research_run_package,
    write_native_research_run,
)


MAX_ISKANDAR_REQUEST_BYTES = int(os.environ.get("ISKANDAR_MAX_REQUEST_BYTES", "2000000"))
MAX_BACKEND_HEALTH_BYTES = 2_000_000
ISKANDAR_TRUSTED_ORIGINS_ENV = "ISKANDAR_TRUSTED_ORIGINS"
_TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_PREPARE_LOCK = threading.RLock()


class PrepareIdentityConflict(ValueError):
    """An existing run cannot be proven to belong to this prepare request."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _control_token() -> str:
    token = os.environ.get("RESEARCH_WARBAND_BEARER_TOKEN", "")
    if (
        len(token) < 32
        or token.startswith("REPLACE_")
        or len(set(token)) < 8
        or any(ord(char) < 32 or ord(char) == 127 for char in token)
    ):
        return ""
    return token


def _backend_token() -> str:
    token = os.environ.get("RESEARCH_WARBAND_BEARER_TOKEN", "")
    if (
        len(token) < 32
        or token.startswith("REPLACE_")
        or len(set(token)) < 8
        or any(ord(char) < 32 or ord(char) == 127 for char in token)
    ):
        return ""
    return token


def _literal_loopback_origin(value: str, *, expected_port: int) -> str:
    try:
        parsed = urlsplit(str(value).strip())
        port = parsed.port
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError as exc:
        raise ValueError("ResearchWarband URL must be a literal loopback origin") from exc
    mapped = getattr(address, "ipv4_mapped", None)
    if (
        parsed.scheme != "http"
        or not (address.is_loopback or (mapped and mapped.is_loopback))
        or port != expected_port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"ResearchWarband URL must be literal loopback port {expected_port}")
    host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    return f"http://{host}:{expected_port}"


def research_warband_backend_health(timeout_sec: float = 3.0) -> dict[str, Any]:
    """Deeply attest the exact bearer-protected production backend on 7201."""
    payload: dict[str, Any] = {}
    endpoint = ""
    try:
        endpoint = _literal_loopback_origin(
            os.environ.get("RESEARCH_WARBAND_URL", "http://127.0.0.1:7201"),
            expected_port=7201,
        )
        token = _backend_token()
        if not token:
            raise ValueError("RESEARCH_WARBAND_BEARER_TOKEN is missing or unsafe")
        target = endpoint + "/health"
        request = Request(
            target,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "Connection": "close",
            },
            method="GET",
        )
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        with opener.open(request, timeout=max(0.1, float(timeout_sec))) as response:
            if response.geturl() != target:
                raise ValueError("ResearchWarband health attempted a redirect")
            status_code = int(response.status)
            media = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if media != "application/json":
                raise ValueError("ResearchWarband health is not JSON")
            raw = response.read(MAX_BACKEND_HEALTH_BYTES + 1)
        if len(raw) > MAX_BACKEND_HEALTH_BYTES:
            raise ValueError("ResearchWarband health response is oversized")
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
        )
        if not isinstance(payload, dict):
            raise ValueError("ResearchWarband health must be an object")
        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        readiness = identity.get("readiness") if isinstance(identity.get("readiness"), dict) else {}
        deployment = (
            readiness.get("deployment_integrity")
            if isinstance(readiness.get("deployment_integrity"), dict)
            else {}
        )
        runner = (
            readiness.get("runner_deployment")
            if isinstance(readiness.get("runner_deployment"), dict)
            else {}
        )
        isolation = (
            readiness.get("process_isolation")
            if isinstance(readiness.get("process_isolation"), dict)
            else {}
        )
        authorization = (
            identity.get("execution_authorization")
            if isinstance(identity.get("execution_authorization"), dict)
            else {}
        )
        healthy = bool(
            200 <= status_code < 300
            and payload.get("ok") is True
            and payload.get("status") == "ok"
            and payload.get("service") == "ResearchWarband"
            and identity.get("bearer_auth_required") is True
            and identity.get("standalone_test_mode") is False
            and authorization.get("iskandar_handoff_required") is True
            and authorization.get("standalone_test_mode_enabled") is False
            and readiness.get("ready") is True
            and readiness.get("store_safe") is True
            and deployment.get("ok") is True
            and runner.get("configured") is True
            and runner.get("ready") is True
            and isolation.get("required") is True
            and isolation.get("ready") is True
            and bool(str(identity.get("source_sha256") or ""))
            and identity.get("source_sha256") == identity.get("authorized_source_sha256")
        )
        error = "" if healthy else "ResearchWarband production identity or readiness is invalid"
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        healthy = False
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
    return {
        "name": "ResearchWarband",
        "kind": "native_research_warband",
        "endpoint": endpoint,
        "health_endpoint": endpoint + "/health" if endpoint else "",
        "healthy": healthy,
        "status": "healthy" if healthy else "unavailable",
        "health": payload,
        "error": error,
        "dispatch_owner": "native_research_backend_router",
        "contract_relation": "executes one native Iskandar-delegated research mission",
    }


def required_workers() -> list[str]:
    return []


def pipeline_summary() -> dict[str, Any]:
    return {
        "kind": "native_research_run",
        "mode": "native_research_warband_mission",
        "authoritative": True,
        "step_count": 1,
        "required_workers": [],
        "steps": [
            {
                "step_id": "research_warband",
                "backend": "ResearchWarband",
                "depends_on": [],
                "ownership": (
                    "detailed planning, search, acquisition, reading, evidence construction, "
                    "analysis, writing, semantic verification, and internal repair"
                ),
            }
        ],
    }


def oversight_template() -> dict[str, Any]:
    return {
        "governor": "IskandarKhayon",
        "kind": "native_research_leadership_oversight",
        "leader_owns": [
            "delegation decision",
            "research objective and depth",
            "source-class policy and error tolerance",
            "success, output, and escalation conditions",
        ],
        "warband_owns": [
            "subquestions and hypotheses",
            "search queries and source selection",
            "evidence construction and analysis",
            "writing, verification, and internal repair",
        ],
    }


def _with_execution_contract(payload: dict[str, Any], backend: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(payload)
    enriched.update(
        {
            "api_version": 3,
            "contract_mode": "native_research_warband_v1",
            "required_workers": [],
            "pipeline": pipeline_summary(),
            "active_execution_backend": backend,
            "execution_contract": {
                "planning_and_preflight": "iskandar_leadership_only",
                "execution": "ResearchWarband",
                "handoff": "native_research_backend_router",
                "backend_healthy": backend.get("healthy") is True,
                "leadership": "IskandarKhayon",
                "detailed_planning": "ResearchWarband",
                "native_directive_artifact": "iskandar_directive.json",
                "leadership_contract": "iskandar_research_directive_v1",
                "legacy_worker_plan_present": False,
            },
        }
    )
    return enriched


def task_from_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    command = payload.get("commander_order") if isinstance(payload.get("commander_order"), dict) else {}
    if not command:
        raise ValueError("commander_order is required; direct governor task input is not accepted")
    command = validate_native_research_commander_order(command)
    return task_text_from_commander_order(command), command


def native_plan_payload(
    task: str,
    task_id: str | None,
    command: dict[str, Any],
    *,
    backend: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the structural one-mission plan without calling the leader model."""
    contract = build_native_research_contract(
        task,
        task_id,
        mission_id=str(command.get("mission_id") or ""),
    )
    plan = native_research_governor_plan(contract, command)
    backend_payload = backend or research_warband_backend_health()
    ready = backend_payload.get("healthy") is True
    next_action = {
        "kind": "prepare_run" if ready else "inspect_capabilities",
        "method": "POST" if ready else "GET",
        "endpoint": "POST /prepare_run" if ready else "GET /capabilities",
        "body": {"task_id": contract["task_id"]} if ready else {},
        "requires": ["commander_order"] if ready else [],
        "reason": (
            "native run shape is valid; ask Iskandar for one leadership decision"
            if ready
            else "ResearchWarband backend is not ready"
        ),
    }
    return _with_execution_contract(
        {
            "ok": ready,
            "governor": "IskandarKhayon",
            "contract": contract,
            "governor_plan": plan,
            "oversight": oversight_template(),
            "leadership_authorization": "pending_prepare",
            "validation": {"ok": True, "errors": []},
            "phase": "native_plan_ready" if ready else "native_plan_blocked",
            "decision": {
                "can_prepare_run": ready,
                "recommended_kind": next_action["kind"],
                "recommended_endpoint": next_action["endpoint"],
            },
            "actions": {
                "can_prepare_run": ready,
                "can_inspect_capabilities": True,
                "next_action": next_action,
            },
            "next_action": next_action,
            "client_action": {
                "kind": next_action["kind"],
                "method": next_action["method"],
                "path": next_action["endpoint"].split(" ", 1)[-1],
                "body": next_action["body"],
                "reason": next_action["reason"],
            },
        },
        backend_payload,
    )


def service_capabilities() -> dict[str, Any]:
    backend = research_warband_backend_health()
    return _with_execution_contract(
        {
            "ok": backend.get("healthy") is True,
            "governor": "IskandarKhayon",
            "task_kinds": ["research", "research_writing", "lore_reconstruction"],
            "worker_availability": {
                "ok": backend.get("healthy") is True,
                "scope": "native_warband_backend",
                "missing_workers": [],
                "unavailable_workers": [],
            },
            "model_brain": model_contract(
                "IskandarKhayon",
                "Inner Circle research leadership governor",
                layer="governor_service",
            ),
            "oversight": oversight_template(),
            "summary": {
                "pipeline_kind": "native_research_run",
                "step_count": 1,
                "required_worker_count": 0,
                "active_backend_healthy": backend.get("healthy") is True,
            },
            "display": {
                "headline": "Iskandar Khayon capabilities",
                "detail": f"One native ResearchWarband mission; backend {backend.get('status')}",
                "severity": "info" if backend.get("healthy") else "warning",
            },
            "capabilities": [
                "single_authoritative_leadership_decision",
                "validated_iskandar_research_directive",
                "iskandar_to_research_warband_delegation",
                "idempotent_native_run_prepare",
                "exact_commander_bound_prepare_receipt",
                "clarify_cancel_resume_via_backend",
            ],
            "endpoints": [
                "GET /health",
                "GET /capabilities",
                "POST /plan",
                "POST /prepare_run",
            ],
            "next_action": {
                "kind": "plan_task",
                "method": "POST",
                "endpoint": "POST /plan",
                "body": {},
                "reason": "inspect the one-mission shape without invoking Iskandar",
            },
        },
        backend,
    )


def request_leadership_directive(
    task: str,
    task_id: str,
    command: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    mission_id = str(command.get("mission_id") or f"mission-{task_id}")
    model_decision = request_model_decision(
        "IskandarKhayon",
        "Leader of the research warband",
        directive_request_payload(task, task_id, command),
        layer="governor_service",
        instructions=directive_model_instructions(),
    )
    directive = build_iskandar_directive(
        model_decision,
        task_id=task_id,
        mission_id=mission_id,
        commander_order=command,
    )
    return directive, model_decision


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
    expected = root / task_id
    candidate = Path(requested).expanduser() if requested else expected
    if not candidate.is_absolute():
        candidate = root / candidate
    lexical = Path(os.path.abspath(candidate))
    if lexical != expected:
        raise ValueError("run_dir must be the exact task-scoped child of the configured run root")
    if candidate.is_symlink() or lexical.is_symlink():
        raise ValueError("run_dir must not be a symlink")
    resolved = candidate.resolve()
    if resolved != expected:
        raise ValueError("run_dir resolves outside its exact task-scoped location")
    if os.path.lexists(resolved):
        metadata = os.lstat(resolved)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("existing task-scoped run path is not a real directory")
        if not allow_existing:
            raise FileExistsError("task-scoped run directory already exists")
    return resolved


def _load_prepare_replay(
    run_dir: Path,
    request_sha256: str,
    command: dict[str, Any],
    backend: dict[str, Any],
) -> dict[str, Any]:
    errors = validate_native_research_run_package(run_dir)
    if errors:
        raise PrepareIdentityConflict("existing native run is invalid: " + "; ".join(errors))
    package = load_native_research_run(run_dir)
    receipt = package.get("receipt") if isinstance(package.get("receipt"), dict) else {}
    if receipt.get("kind") != "native_research_run_receipt" or receipt.get("version") != 1:
        raise PrepareIdentityConflict("existing run has an unsupported native research receipt")
    if not hmac.compare_digest(str(receipt.get("prepare_request_sha256") or ""), request_sha256):
        raise PrepareIdentityConflict("existing run belongs to a different prepare request")
    contract = package["contract"]
    directive = validate_directive_for_commander(
        package["leadership_directive"],
        command,
        expected_task_id=contract["task_id"],
        expected_mission_id=contract["mission_id"],
        require_delegation=True,
    )
    return _with_execution_contract(
        {
            "ok": True,
            "governor": "IskandarKhayon",
            "model_brain": {
                "status": "persisted",
                "reason": "idempotent replay of the original leadership decision",
            },
            "contract": contract,
            "governor_plan": package["governor_plan"],
            "leadership_directive": directive,
            "status": package["status"],
            "phase": "run_prepared",
            "prepare_replayed": True,
            "decision": {
                "can_handoff_to_abaddon": True,
                "delegated_to": "ResearchWarband",
                "recommended_kind": "start_native_research_run",
            },
            "next_action": {},
            "client_action": {},
        },
        backend,
    )


def _header_values(headers: Any, name: str) -> list[str]:
    getter = getattr(headers, "get_all", None)
    if callable(getter):
        return [str(value) for value in (getter(name) or [])]
    value = headers.get(name) if hasattr(headers, "get") else None
    return [] if value is None else [str(value)]


def _literal_loopback_authority(value: str) -> tuple[str, int] | None:
    raw = str(value or "")
    if not raw or any(char.isspace() for char in raw) or any(char in raw for char in "/\\@,%"):
        return None
    try:
        if raw.startswith("["):
            close = raw.find("]")
            if close < 0:
                return None
            host = raw[1:close]
            suffix = raw[close + 1 :]
            port = int(suffix[1:]) if suffix.startswith(":") else 80
        elif raw.count(":") == 1:
            host, port_text = raw.rsplit(":", 1)
            port = int(port_text)
        elif ":" in raw:
            return None
        else:
            host, port = raw, 80
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    mapped = getattr(address, "ipv4_mapped", None)
    if not (address.is_loopback or (mapped and mapped.is_loopback)) or not 1 <= port <= 65535:
        return None
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


def _origin_allowed(headers: Any) -> bool:
    origins = _header_values(headers, "Origin")
    fetch_sites = _header_values(headers, "Sec-Fetch-Site")
    if not origins:
        return not (len(fetch_sites) == 1 and fetch_sites[0].strip().lower() == "cross-site")
    if len(origins) != 1:
        return False
    try:
        parsed = urlsplit(origins[0])
    except ValueError:
        return False
    if parsed.scheme != "http" or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return False
    origin = _literal_loopback_authority(parsed.netloc)
    hosts = _header_values(headers, "Host")
    return len(hosts) == 1 and origin is not None and origin == _literal_loopback_authority(hosts[0])


def _post_authorized(headers: Any) -> bool:
    expected = _control_token()
    values = _header_values(headers, "Authorization")
    return bool(expected and len(values) == 1 and hmac.compare_digest(values[0], f"Bearer {expected}"))


def _json_content_type(headers: Any) -> bool:
    values = _header_values(headers, "Content-Type")
    return len(values) == 1 and values[0].split(";", 1)[0].strip().lower() == "application/json"


def response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def payload_from(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    if not _json_content_type(handler.headers):
        raise ValueError("Content-Type must be application/json")
    if _header_values(handler.headers, "Transfer-Encoding"):
        raise ValueError("Transfer-Encoding is not supported")
    lengths = _header_values(handler.headers, "Content-Length")
    if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
        raise ValueError("exactly one decimal Content-Length is required")
    length = int(lengths[0])
    if length > MAX_ISKANDAR_REQUEST_BYTES:
        raise ValueError(f"request body exceeds {MAX_ISKANDAR_REQUEST_BYTES} bytes")
    raw = handler.rfile.read(length)
    if len(raw) != length:
        raise ValueError("request body ended before Content-Length")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"request body is not strict JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def make_handler(default_run_root: Path) -> type[BaseHTTPRequestHandler]:
    class IskandarHandler(BaseHTTPRequestHandler):
        server_version = "Iskandar/1.0"

        def log_message(self, _fmt: str, *_args: Any) -> None:
            return

        def _boundary(self) -> bool:
            if not _peer_allowed(str(self.client_address[0])) or not _host_allowed(self.headers):
                response(self, 421, {"ok": False, "error": "Iskandar requires literal loopback peer and Host"})
                return False
            return True

        def do_OPTIONS(self) -> None:  # noqa: N802
            if not self._boundary():
                return
            if not _origin_allowed(self.headers):
                response(self, 403, {"ok": False, "error": "cross-origin POST requests are forbidden"})
                return
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if not self._boundary():
                return
            if self.path == "/health":
                backend = research_warband_backend_health()
                ready = backend.get("healthy") is True and bool(_control_token())
                response(
                    self,
                    200 if ready else 503,
                    {
                        "ok": ready,
                        "governor": "IskandarKhayon",
                        "liveness": True,
                        "readiness": ready,
                        "control_auth_configured": bool(_control_token()),
                        "backend": backend,
                    },
                )
                return
            if self.path == "/capabilities":
                response(self, 200, service_capabilities())
                return
            response(self, 404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._boundary():
                return
            if not _origin_allowed(self.headers):
                response(self, 403, {"ok": False, "error": "cross-origin POST requests are forbidden"})
                return
            if not _post_authorized(self.headers):
                response(self, 401, {"ok": False, "error": "Iskandar bearer authentication failed"})
                return
            if self.path not in {"/plan", "/prepare_run"}:
                response(self, 404, {"ok": False, "error": "not found"})
                return
            if not _json_content_type(self.headers):
                response(self, 415, {"ok": False, "error": "Content-Type must be application/json"})
                return
            try:
                payload = payload_from(self)
                task, command = task_from_payload(payload)
                task_id = str(payload.get("task_id") or "").strip() or None
                plan_payload = native_plan_payload(task, task_id, command)
                if self.path == "/plan":
                    response(self, 200, plan_payload)
                    return
                contract = plan_payload["contract"]
                run_dir = resolve_run_dir(
                    default_run_root,
                    str(payload.get("run_dir") or ""),
                    contract["task_id"],
                    allow_existing=True,
                )
                request_sha256 = native_research_prepare_request_sha256(contract, command)
                with _PREPARE_LOCK:
                    backend = research_warband_backend_health()
                    if run_dir.exists():
                        replay = _load_prepare_replay(run_dir, request_sha256, command, backend)
                        response(self, 200, replay)
                        return
                    if backend.get("healthy") is not True:
                        response(
                            self,
                            503,
                            {
                                "ok": False,
                                "governor": "IskandarKhayon",
                                "error": "ResearchWarband backend is not ready",
                                "error_code": "research_warband_backend_unavailable",
                                "backend": backend,
                            },
                        )
                        return
                    try:
                        directive, model_decision = request_leadership_directive(
                            task, contract["task_id"], command,
                        )
                    except IskandarDirectiveError as exc:
                        response(
                            self,
                            502,
                            {
                                "ok": False,
                                "governor": "IskandarKhayon",
                                "error": str(exc),
                                "error_code": "invalid_leadership_directive",
                            },
                        )
                        return
                    if directive["decision"] != "delegate":
                        response(
                            self,
                            409,
                            {
                                "ok": False,
                                "governor": "IskandarKhayon",
                                "error": "Iskandar did not authorize delegation to ResearchWarband",
                                "error_code": "delegation_not_authorized",
                                "leadership_directive": directive,
                                "model_brain": model_decision,
                            },
                        )
                        return
                    run_dir.parent.mkdir(parents=True, exist_ok=True)
                    os.mkdir(run_dir, mode=0o755)
                    reserved = os.lstat(run_dir)
                    reserved_identity = (reserved.st_dev, reserved.st_ino)
                    try:
                        governor_plan = native_research_governor_plan(contract, command)
                        status = write_native_research_run(
                            run_dir,
                            contract,
                            directive,
                            governor_plan,
                            command,
                            prepare_request_sha256=request_sha256,
                        )
                        result = _with_execution_contract(
                            {
                                "ok": True,
                                "governor": "IskandarKhayon",
                                "model_brain": model_decision,
                                "contract": contract,
                                "governor_plan": governor_plan,
                                "leadership_directive": directive,
                                "status": status,
                                "phase": "run_prepared",
                                "prepare_replayed": False,
                                "decision": {
                                    "can_handoff_to_abaddon": True,
                                    "delegated_to": "ResearchWarband",
                                    "recommended_kind": "start_native_research_run",
                                },
                                "next_action": {},
                                "client_action": {},
                            },
                            backend,
                        )
                    except Exception:
                        try:
                            current = os.lstat(run_dir)
                            if stat.S_ISDIR(current.st_mode) and (current.st_dev, current.st_ino) == reserved_identity:
                                shutil.rmtree(run_dir)
                        except OSError:
                            pass
                        raise
                response(self, 200, result)
            except PrepareIdentityConflict as exc:
                response(
                    self,
                    409,
                    {
                        "ok": False,
                        "governor": "IskandarKhayon",
                        "error": str(exc),
                        "error_code": "prepare_identity_conflict",
                    },
                )
            except ValueError as exc:
                response(self, 400, {"ok": False, "governor": "IskandarKhayon", "error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                response(self, 500, {"ok": False, "governor": "IskandarKhayon", "error": str(exc)})

    return IskandarHandler


def _validate_bind_host(host: str) -> str:
    try:
        address = ipaddress.ip_address(str(host or ""))
    except ValueError as exc:
        raise ValueError("Iskandar bind host must be a literal loopback address") from exc
    mapped = getattr(address, "ipv4_mapped", None)
    if not (address.is_loopback or (mapped and mapped.is_loopback)):
        raise ValueError("Iskandar cannot bind off loopback")
    return str(host)


def serve(host: str, port: int, default_run_root: Path) -> None:
    if not _control_token():
        raise RuntimeError("RESEARCH_WARBAND_BEARER_TOKEN must be a high-entropy value of at least 32 characters")
    host = _validate_bind_host(host)
    default_run_root.mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer((host, port), make_handler(default_run_root)).serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve Iskandar as a native research governor.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7101)
    parser.add_argument(
        "--default-run-root",
        default=os.environ.get("WARMMASTER_RUN_ROOT", "runtime/warmaster-runs"),
    )
    args = parser.parse_args()
    serve(args.host, args.port, Path(args.default_run_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
