from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import hashlib
import os
import re
import shutil
import stat
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from EyeOfTerror.model_brain import model_contract, request_model_decision
from EyeOfTerror.common_protocol import governor_plan_from_contract, validate_protocol_payload

from ..command_text import task_text_from_commander_order
from ..contracts import build_code_task_contract, code_worker_plan
from ..pipeline import write_pipeline_run
from .ceraxia import executable_client_action, oversight_plan, patch_contract_capabilities, payload_with_plan_view, plan_code_task


SKITARII_SOURCE_FILES = (
    "service.py", "spec.py", "acceptor.py", "warband.py", "planner.py",
    "executor.py", "explorer.py", "reviewer.py", "clarify.py",
    "mission_store.py", "tools.py", "harness.py",
)
MAX_CERAXIA_REQUEST_BYTES = int(os.environ.get("CERAXIA_MAX_REQUEST_BYTES", "2000000"))
CERAXIA_TRUSTED_ORIGINS_ENV = "CERAXIA_TRUSTED_ORIGINS"
_TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


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
    except OSError:
        return ""
    return digest.hexdigest()


def required_workers() -> list[str]:
    workers: list[str] = []
    for step in code_worker_plan("capabilities"):
        if step.worker not in workers:
            workers.append(step.worker)
    return workers


def skitarii_backend_health(timeout_sec: float = 1.0) -> dict[str, Any]:
    """Probe the real code execution backend without making it a registry worker.

    Warmaster still prepares the established six-worker contract. Its Skitarii
    bridge consumes that compatibility contract and hands execution to the
    warband on port 7200. Keeping the two roles separate prevents registry
    preflight from looking for a synthetic ``SkitariiWarband`` worker while
    still reporting whether the actual execution backend is reachable.
    """
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
        "dispatch_owner": "Warmaster skitarii_bridge",
        "contract_relation": "executes the six-worker Ceraxia compatibility contract",
    }


def _compatibility_pipeline(pipeline: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(pipeline)
    enriched.update(
        {
            "mode": "legacy_six_worker_compatibility_adapter",
            "active_execution_backend": "SkitariiWarband",
            "execution_handoff": "Warmaster skitarii_bridge",
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
        enriched["pipeline"] = _compatibility_pipeline(pipeline)
    enriched.update(
        {
            "api_version": 2,
            "contract_mode": "legacy_six_worker_compatibility_adapter",
            "required_workers": required_workers(),
            "active_execution_backend": backend_payload,
            "execution_contract": {
                "planning_and_preflight": "six_worker_registry_compatibility_adapter",
                "execution": "SkitariiWarband",
                "handoff": "Warmaster skitarii_bridge",
                "backend_healthy": bool(backend_payload.get("healthy")),
            },
        }
    )
    if isinstance(nested_plan, dict):
        nested = dict(nested_plan)
        nested_pipeline = nested.get("pipeline") if isinstance(nested.get("pipeline"), dict) else {}
        if nested_pipeline:
            nested["pipeline"] = _compatibility_pipeline(nested_pipeline)
        nested.update(
            {
                "api_version": 2,
                "contract_mode": "legacy_six_worker_compatibility_adapter",
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
    steps = [step.to_dict() for step in code_worker_plan("capabilities")]
    return {
        "kind": "code_task",
        "step_count": len(steps),
        "required_workers": required_workers(),
        "steps": [
            {
                "step_id": step["step_id"],
                "worker": step["worker"],
                "depends_on": step["depends_on"],
                "expected_artifacts": step["expected_artifacts"],
                "expected_artifact_count": len(step["expected_artifacts"]),
            }
            for step in steps
        ],
    }


def oversight_template() -> dict[str, Any]:
    contract = build_code_task_contract("capabilities", task_id="capabilities")
    return oversight_plan(contract)


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


def protocol_governor_plan(plan_payload: dict[str, Any], command: dict[str, Any]) -> dict[str, Any]:
    contract = plan_payload.get("contract") if isinstance(plan_payload.get("contract"), dict) else {}
    mission_id = str(command.get("mission_id") or f"mission-{contract.get('task_id') or 'unassigned'}")
    payload = governor_plan_from_contract(mission_id, contract, command)
    validate_protocol_payload(payload, expected_type="governor_plan")
    return payload


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
    plan_payload = _with_execution_contract(
        payload_with_plan_view(plan_code_task(normalized_task, task_id=task_id).to_dict()),
        backend,
    )
    final_package_schema = {
        "kind": "skitarii_bridge_result",
        "required_fields": [
            "ok",
            "phase",
            "status",
            "summary",
            "artifacts",
            "patch_stage",
            "ready_to_apply",
        ],
        "completed_requires": [
            "ok == true",
            "phase == completed",
            "patch_stage.applied_to_live == true for repository mutations",
            "patch_stage.post_apply_tests_passed == true for repository mutations",
        ],
        "ready_to_apply_requires": [
            "ok == false",
            "phase == ready_to_apply",
            "patch_stage.ready_to_apply == true",
            "artifacts contains work/skitarii.patch",
            "next_action.endpoint == POST /runs/{task_id}/apply_patch",
        ],
        "legacy_final_manifest_schema": "not binding on the active Skitarii backend",
    }
    return _with_execution_contract({
        "ok": bool(plan_payload.get("ok")),
        "governor": "Ceraxia",
        "api_version": 2,
        "callable_kind": "specialized_code_brigade",
        "model_brain": model_contract("Ceraxia", "Inner Circle code task governor", layer="governor_callable"),
        "task_id": plan_payload.get("contract", {}).get("task_id", task_id or ""),
        "normalized_task": normalized_task,
        "input_contract": {
            "required": ["commander_order"],
            "optional": ["task_id", "repo_path", "run_dir"],
            "repo_scope": "configured_repository_only",
            "configured_repo_path": str(REPO_ROOT.resolve()),
            "run_dir_scope": (
                "exact_nonexistent_<configured_run_root>/<task_id>_or_"
                "<WARMMASTER_RUN_ROOT>/<task_id>"
            ),
            "constraints_status": "informational_only_not_enforced",
            "received_constraints": constraints or {},
        },
        "execution_flow": [
            {"step": 1, "method": "POST", "endpoint": "/callable_contract", "purpose": "inspect callable package contract"},
            {"step": 2, "method": "POST", "endpoint": "/prepare_run", "purpose": "write dispatch and oversight package"},
            {"step": 3, "method": "POST", "endpoint": "Warmaster /runs/{task_id}/start_*", "purpose": "execute pipeline"},
            {"step": 4, "method": "GET", "endpoint": "Warmaster /runs/{task_id}/final", "purpose": "retrieve native Skitarii result"},
            {"step": 5, "method": "POST", "endpoint": "Warmaster /runs/{task_id}/apply_patch", "purpose": "conditionally apply a fingerprint-matched ready patch"},
        ],
        "final_package_schema": final_package_schema,
        "task_profile": plan_payload.get("task_profile", {}),
        "worker_specialization_briefs": plan_payload.get("worker_specialization_briefs", []),
        "patch_contract": plan_payload.get("patch_contract", {}),
        "plan": plan_payload,
        "next_action": {
        "kind": "prepare_run",
        "method": "POST",
        "endpoint": "POST /prepare_run",
        "body": {
            "task_id": plan_payload.get("contract", {}).get("task_id", task_id or ""),
            "repo_path": repo_path,
        },
        "requires": ["commander_order"],
        "reason": "callable contract is ready; prepare a concrete Ceraxia run package",
    },
    }, backend)


def service_capabilities() -> dict[str, Any]:
    capability_plan = plan_code_task("capabilities", task_id="capabilities").to_dict()
    pipeline = _compatibility_pipeline(pipeline_summary())
    oversight = oversight_template()
    backend = skitarii_backend_health()
    adapter_available = not capability_plan.get("missing_workers") and not capability_plan.get("unavailable_workers")
    next_action = {
        "kind": "plan_task",
        "method": "POST",
        "endpoint": "POST /plan",
        "body": {},
        "body_schema": {"commander_order": "protocol object", "task_id": "optional string"},
        "reason": "inspect a Ceraxia code plan for a Warmaster commander_order",
    }
    return _with_execution_contract({
        "ok": True,
        "governor": "Ceraxia",
        "api_version": 2,
        "task_kinds": ["code"],
        "required_workers": required_workers(),
        "worker_availability": {
            "ok": adapter_available,
            "scope": "legacy_six_worker_compatibility_adapter",
            "missing_workers": capability_plan.get("missing_workers", []),
            "unavailable_workers": capability_plan.get("unavailable_workers", []),
            "resolved_workers": capability_plan.get("resolved_workers", {}),
        },
        "model_brain": model_contract("Ceraxia", "Inner Circle code task governor", layer="governor_service"),
        "pipeline": pipeline,
        "patch_contract": patch_contract_capabilities(),
        "oversight": oversight,
        "task_profile": capability_plan.get("task_profile", {}),
        "worker_specialization_briefs": capability_plan.get("worker_specialization_briefs", []),
        "summary": {
            "pipeline_kind": str(pipeline.get("kind") or ""),
            "step_count": int(pipeline.get("step_count") or 0),
            "required_worker_count": len(required_workers()),
            "quality_gate_count": len(oversight.get("quality_gates") if isinstance(oversight.get("quality_gates"), list) else []),
            "handoff_count": len(oversight.get("handoffs") if isinstance(oversight.get("handoffs"), list) else []),
            "step_quality_matrix_count": len(oversight.get("step_quality_matrix") if isinstance(oversight.get("step_quality_matrix"), list) else []),
            "worker_availability_ok": adapter_available,
            "active_backend_healthy": bool(backend.get("healthy")),
            "task_profile_complexity": str(capability_plan.get("task_profile", {}).get("complexity", "")) if isinstance(capability_plan.get("task_profile"), dict) else "",
        },
        "display": {
            "headline": "Ceraxia capabilities",
            "detail": (
                f"{int(pipeline.get('step_count') or 0)} compatibility steps; "
                f"Skitarii Warband backend {backend.get('status')}"
            ),
            "severity": "info" if adapter_available and backend.get("healthy") else "warning",
        },
        "next_action": next_action,
        "client_action": executable_client_action("", next_action),
        "capabilities": [
            "model_backed_governor_planning",
            "code_task_planning",
            "repository_survey",
            "patch_manifest_preparation",
            "verification_planning",
            "code_review_coordination",
            "safe_final_handoff",
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


def resolve_run_dir(default_run_root: Path, requested: str, task_id: str) -> Path:
    root = default_run_root.resolve()
    if not _TASK_ID_RE.fullmatch(str(task_id or "")) or ".." in task_id:
        raise ValueError("task_id is not safe for a run directory")
    handoff_root = Path(
        os.environ.get(
            "WARMMASTER_RUN_ROOT",
            str(REPO_ROOT / "EyeOfTerror" / "Warmaster" / "runtime" / "warmaster-runs"),
        ),
    ).expanduser().resolve()
    allowed_roots = {root, handoff_root}
    expected_paths = {allowed_root / task_id for allowed_root in allowed_roots}
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
                plan = plan_code_task(task, task_id=task_id)
                prepared_run_dir = None
                if self.path == "/prepare_run":
                    prepared_run_dir = resolve_run_dir(
                        default_run_root,
                        str(payload.get("run_dir") or ""),
                        plan.contract.task_id,
                    )
                model_decision = request_model_decision(
                    "Ceraxia",
                    "Inner Circle code task governor",
                    payload,
                    layer="governor_service",
                    instructions="Plan a software engineering brigade task, identify implementation and verification risks, and keep the answer scoped to governor oversight.",
                )
                if not model_decision.get("ok"):
                    response(
                        self,
                        503,
                        {
                            "ok": False,
                            "governor": "Ceraxia",
                            "error": "model brain did not answer",
                            "error_code": "model_brain_unavailable",
                            "model_brain": model_decision,
                        },
                    )
                    return
                if self.path == "/plan":
                    plan_payload = _with_execution_contract(payload_with_plan_view(plan.to_dict()))
                    plan_payload["governor_plan"] = protocol_governor_plan(plan_payload, command)
                    plan_payload["model_brain"] = model_decision
                    plan_payload = _bind_commander_order(plan_payload, command)
                    response(self, 200, plan_payload)
                    return
                if self.path == "/callable_contract":
                    constraints = payload.get("constraints") if isinstance(payload.get("constraints"), dict) else {}
                    contract_payload = callable_contract_payload(task, task_id, repo_path=repo_path, constraints=constraints)
                    contract_payload["model_brain"] = model_decision
                    contract_payload = _bind_commander_order(contract_payload, command)
                    response(self, 200, contract_payload)
                    return
                if self.path == "/prepare_run":
                    if prepared_run_dir is None:
                        raise ValueError("task-scoped run directory was not validated")
                    run_dir = prepared_run_dir
                    run_dir.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.mkdir(run_dir, mode=0o700)
                    except FileExistsError:
                        response(
                            self, 409,
                            {"ok": False, "error": "task-scoped run directory already exists"},
                        )
                        return
                    reserved_metadata = os.lstat(run_dir)
                    reserved_identity = (
                        reserved_metadata.st_dev, reserved_metadata.st_ino,
                    )
                    try:
                        mission_id = str(command.get("mission_id") or f"mission-{plan.contract.task_id}")
                        status = write_pipeline_run(
                            plan.contract, run_dir, oversight=oversight_plan(plan.contract),
                            mission_id=mission_id,
                        )
                        backend = skitarii_backend_health()
                        plan_payload = _with_execution_contract(
                            payload_with_plan_view(plan.to_dict()), backend,
                        )
                        governor_plan_payload = protocol_governor_plan(plan_payload, command)
                        (run_dir / "governor_plan.json").write_text(
                            json.dumps(
                                governor_plan_payload, ensure_ascii=False,
                                indent=2, sort_keys=True,
                            ) + "\n",
                            encoding="utf-8",
                        )
                        response_payload = _with_execution_contract({
                            "ok": status["ok"],
                            "governor": "Ceraxia",
                            "model_brain": model_decision,
                            "governor_plan": governor_plan_payload,
                            "status": status,
                            "phase": "run_prepared" if status.get("ok") else "prepare_failed",
                            "decision": {
                                "can_handoff_to_warmaster": bool(status.get("ok")),
                                "recommended_kind": "handoff_run_package" if status.get("ok") else "",
                                "recommended_endpoint": "",
                            },
                            "display": {
                                "headline": "Code run package prepared" if status.get("ok") else "Code run package preparation failed",
                                "detail": str(status.get("error") or "Run package was written for Warmaster verification"),
                                "severity": "info" if status.get("ok") else "error",
                                "task_id": plan.contract.task_id,
                            },
                            "next_action": {},
                            "client_action": {},
                            "pipeline": plan_payload.get("pipeline", {}),
                        }, backend)
                    except Exception:
                        # This process created the directory atomically above.  Never
                        # leave a half-prepared package that a retry could mistake for
                        # an existing valid run; shutil does not follow a replaced
                        # top-level symlink.
                        try:
                            current = os.lstat(run_dir)
                            current_identity = (current.st_dev, current.st_ino)
                            if (
                                stat.S_ISDIR(current.st_mode)
                                and current_identity == reserved_identity
                            ):
                                shutil.rmtree(run_dir)
                        except OSError:
                            pass
                        raise
                    response(self, 200, response_payload)
                    return
                response(self, 404, {"ok": False, "error": "not found"})
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
    parser.add_argument("--default-run-root", default="runtime/ceraxia-runs")
    args = parser.parse_args()
    serve(args.host, args.port, Path(args.default_run_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
