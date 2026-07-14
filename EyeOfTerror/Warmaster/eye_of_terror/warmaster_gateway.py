from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import shutil
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from EyeOfTerror.model_brain import attach_model_brain, request_model_decision

from .contracts import validate_task_contract_payload
from doctor import run_doctor
from .governors import governor_by_name, governor_refs
from .ledger import TaskLedger
from .pipeline import write_pipeline_run
from .registry import worker_refs
from .routing import route_message
from .mission_control import (
    build_commander_order,
    governor_task_from_order,
    list_missions,
    mission_id_for,
    mission_state,
    task_id_for_message,
)
from .orchestrator import (
    TaskMemoryParentConflict,
    cancel_http_worker_tasks,
    execute_routed_run,
    execute_run_cycle,
    execute_with_ledger_failure_guard,
    ensure_task_memory_for_intake,
    execution_backend_route,
    orchestrate_prepare_task,
    orchestrate_run_task,
    orchestrate_start_run,
    planned_step_ids_from_run,
    prepare_run_root,
    record_research_loop_event,
    record_run_preflight_event,
    recover_stale_runs,
    recovery_summary,
    research_loop_run,
    resume_step_ids_from_run,
    revision_plan_fingerprint,
    revision_step_ids_from_run,
    run_execution_preflight,
    start_background,
    start_recoverable_runs,
    task_memory_start_guard,
    validate_requested_step_ids,
)
from .task_prepare import (
    cleanup_unregistered_run_dir,
    preflight_task,
    prepare_task_via_governor_service,
    route_failure_payload,
)
from .capabilities import (
    gateway_capabilities,
)
from .campaigns import (
    campaign_preflight,
    campaign_state,
    cancel_campaign,
    list_campaigns,
    prepare_campaign,
    resume_campaign,
)
from .run_state import (
    all_run_events,
    last_run_preflight,
    list_runs,
    orchestration_state,
    payload_with_run_view,
    run_events,
    run_progress,
    run_snapshot,
    run_status_summary,
    run_step_artifacts,
    run_step_state,
    run_summary,
    run_worker_tasks,
)
from .actions import (
    action_for_mode,
    created_task_actions,
    gateway_actions,
    run_actions,
    run_preflight_actions,
    task_preflight_actions,
    task_preflight_body,
)
from .run_validation import (
    dispatch_dependencies_by_step,
    dispatch_workers_by_step,
    plan_oversight_errors,
    revision_plan_summary,
    run_dispatch_package_errors,
    run_oversight_diagnostics,
    run_oversight_summary,
    run_oversight_validation_errors,
    run_package_action_errors,
    run_package_diagnostics,
    validate_oversight_against_run,
    validate_revision_plan,
    verify_prepared_run_package,
)
from .artifacts import (
    artifact_status,
    artifact_text,
    compact_manifest_summary,
    final_manifest_summary,
    final_package,
    open_artifact_binary,
)
from .skitarii_bridge import (
    answer_skitarii_mission,
    apply_staged_patch,
    begin_run_cancellation,
    cancel_skitarii_mission_for_run,
)
from .native_runs import native_adapter_for_run
from .research_warband_bridge import (
    answer_research_warband_mission,
    cancel_research_warband_mission_for_run,
)
from .brigade import (
    brigade_health_snapshot,
    brigade_plan_snapshot,
    brigade_readiness_summary,
    compact_brigade_readiness,
    contract_required_workers,
    contract_summary,
    enrich_worker_metadata,
    fetch_json_endpoint,
    fetch_service_capabilities,
    fetch_worker_health,
    governor_pipeline_summaries,
    governor_registry_snapshot,
    governor_worker_requirements,
    missing_contract_workers,
    registry_summary,
    required_workers_from_capabilities,
    worker_availability,
    worker_registry_snapshot,
)
from .run_package import (
    load_json_file,
    load_json_object,
    load_ledger_dict,
    run_contract,
    run_dispatch_packets,
    run_oversight,
    sandbox_artifact_file_status,
)
from .gateway_util import (
    parse_limit,
    parse_nonnegative_int,
    post_json,
    read_payload,
    redact_host_paths,
    requested_step_ids_from_payload,
    resolve_run_child_path,
    response,
    valid_task_id,
    validate_service_host,
)
from .oversight_guard import (
    compact_oversight_summary,
    downstream_revision_steps,
    validate_oversight_payload,
)
from .views import (
    display_events_for,
    event_display,
    executable_client_action,
    orchestration_display,
    orchestration_view_fields,
    payload_with_client_action,
    payload_with_task_view,
    recovery_candidate_display,
    registry_display,
    run_orchestration_card,
    run_orchestration_cards,
)


from .runtime_state import (
    ACTIVE_RUNS,
    ACTIVE_RUNS_LOCK,
    ALLOWED_SERVICE_HOSTS,
    MAX_ARTIFACT_TEXT_BYTES,
    MAX_LIST_LIMIT,
    TASK_ID_RE,
)


APPLY_TRUSTED_ORIGINS_ENV = "WARMMASTER_APPLY_TRUSTED_ORIGINS"
TRUSTED_HOSTS_ENV = "WARMMASTER_TRUSTED_HOSTS"
ARTIFACT_STREAM_CHUNK_BYTES = 1024 * 1024


def _artifact_download_headers(artifact_path: str) -> tuple[str, str]:
    """Return bounded, injection-safe MIME and Content-Disposition values."""
    raw_name = PurePosixPath(str(artifact_path).replace("\\", "/")).name
    unicode_name = (raw_name or "artifact")[:180]
    fallback = "".join(
        char if 0x20 <= ord(char) < 0x7F and char not in {'"', "\\"} else "_"
        for char in unicode_name
    ).strip(" .") or "artifact"
    media_type = mimetypes.guess_type(unicode_name)[0] or "application/octet-stream"
    disposition = (
        f'attachment; filename="{fallback}"; '
        f"filename*=UTF-8''{quote(unicode_name, safe='')}"
    )
    return media_type, disposition


def _canonical_http_origin(value: str) -> str:
    """Return a comparable serialized HTTP(S) origin, or an empty string."""
    raw = value.strip()
    if not raw or raw == "null" or "," in raw or "\\" in raw or any(char.isspace() for char in raw):
        return ""
    try:
        parsed = urlparse(raw)
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
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return ""
    rendered_host = f"[{host}]" if ":" in host else host
    default_port = 80 if scheme == "http" else 443
    port_suffix = "" if port is None or port == default_port else f":{port}"
    return f"{scheme}://{rendered_host}{port_suffix}"


def _trusted_apply_origins() -> set[str]:
    origins: set[str] = set()
    for item in os.environ.get(APPLY_TRUSTED_ORIGINS_ENV, "").split(","):
        canonical = _canonical_http_origin(item)
        if canonical:
            origins.add(canonical)
    return origins


def _is_loopback_origin(origin: str) -> bool:
    """Accept only literal loopback/localhost origins without performing DNS."""
    host = (urlparse(origin).hostname or "").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _resolved_run_dir(run_root: Path, task_id: str) -> Path:
    """Resolve one non-symlink run directory strictly beneath run_root."""
    if not valid_task_id(task_id):
        raise ValueError("invalid task_id")
    root = run_root.resolve()
    candidate = root / task_id
    if candidate.is_symlink():
        raise ValueError("run directory must not be a symlink")
    resolved = candidate.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError("run directory escapes run_root")
    return resolved


def _gateway_host_allowed(raw_host: str) -> bool:
    host = raw_host.strip().lower()
    if not host or any(char.isspace() for char in host) or "," in host or "\\" in host:
        return False
    canonical = _canonical_http_origin(f"http://{host}")
    if canonical and _is_loopback_origin(canonical):
        return True
    trusted = {
        item.strip().lower()
        for item in os.environ.get(TRUSTED_HOSTS_ENV, "").split(",")
        if item.strip()
    }
    return host in trusted


def _gateway_peer_allowed(raw_peer: str) -> bool:
    """The gateway is an unauthenticated local control plane, so peers are loopback only."""
    try:
        address = ipaddress.ip_address(str(raw_peer).split("%", 1)[0])
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def _validate_gateway_bind_host(host: str) -> str:
    try:
        address = ipaddress.ip_address(str(host).split("%", 1)[0])
    except ValueError as exc:
        raise ValueError("Abaddon gateway bind host must be a literal loopback address") from exc
    if not address.is_loopback:
        raise ValueError("Abaddon gateway cannot bind an unauthenticated control plane off loopback")
    return str(host)


def gateway_model_decision(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = dict(payload)
    request["operation"] = operation
    return request_model_decision(
        "WarmasterGateway",
        "top-level orchestration gateway",
        request,
        layer="gateway_service",
        instructions="Route and supervise the task at gateway level. Identify the governor, execution path, and immediate orchestration risk without doing worker-specific work.",
    )


def gateway_model_required_payload(model_decision: dict[str, Any]) -> dict[str, Any]:
    return attach_model_brain(
        {
            "ok": False,
            "gateway": "WarmasterGateway",
            "error": "gateway model brain did not answer",
            "error_code": "model_brain_unavailable",
        },
        model_decision,
    )


def gateway_state(run_root: Path, run_limit: int = 20, include_health: bool = False, host: str = "127.0.0.1") -> dict[str, Any]:
    all_runs = list_runs(run_root)
    runs = all_runs[: parse_limit(str(run_limit), default=20)]
    with ACTIVE_RUNS_LOCK:
        process_active_runs = sorted(ACTIVE_RUNS)
    payload = {
        "ok": True,
        "gateway": "WarmasterGateway",
        "display_name": "Abaddon",
        "capabilities": gateway_capabilities(),
        "actions": gateway_actions(),
        "governors": governor_registry_snapshot(),
        "workers": worker_registry_snapshot(),
        "brigade_plan": brigade_plan_snapshot(),
        "run_summary": run_status_summary(all_runs),
        "recovery": recovery_summary(all_runs),
        "process_active_runs": process_active_runs,
        "campaigns": list_campaigns(run_root),
        "runs": runs,
        "orchestration_cards": run_orchestration_cards(runs, process_active_runs),
    }
    if include_health:
        payload["brigade_health"] = brigade_health_snapshot(host=host)
    return payload


def execution_response_status(summary: dict[str, Any], post_summary: dict[str, Any]) -> int:
    if summary.get("ok"):
        return 200
    revision_plan = summary.get("revision_plan") if isinstance(summary.get("revision_plan"), dict) else {}
    post_revision_plan = post_summary.get("revision_plan") if isinstance(post_summary.get("revision_plan"), dict) else {}
    summary_result = summary.get("result") if isinstance(summary.get("result"), dict) else {}
    post_result = post_summary.get("result") if isinstance(post_summary.get("result"), dict) else {}
    final_status = str(summary.get("status") or summary_result.get("status") or post_result.get("status") or "").lower()
    if revision_plan.get("required") or post_revision_plan.get("required") or final_status in {"blocked", "needs_revision"}:
        return 409
    return 500


def make_handler(run_root: Path, default_governor_transport: str = "local", default_governor_host: str = "127.0.0.1") -> type[BaseHTTPRequestHandler]:
    class WarmasterHandler(BaseHTTPRequestHandler):
        server_version = "WarmasterGateway/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _apply_origin_policy(self) -> tuple[bool, str | None, str]:
            """Protect the state-changing apply action from browser cross-origin calls."""
            raw_origin = self.headers.get("Origin", "").strip()
            fetch_site = self.headers.get("Sec-Fetch-Site", "").strip().lower()
            if not raw_origin:
                if fetch_site == "cross-site":
                    return False, None, "cross-site apply requests are forbidden"
                # Non-browser operator clients (curl, CLI, local automation) normally
                # do not send Origin and remain supported.
                return True, None, ""

            origin = _canonical_http_origin(raw_origin)
            if not origin:
                return False, None, "invalid Origin header"
            host = self.headers.get("Host", "").strip()
            same_origin = _canonical_http_origin(f"http://{host}") if host else ""
            if origin in _trusted_apply_origins():
                return True, origin, ""
            if origin == same_origin and _is_loopback_origin(origin):
                return True, origin, ""
            return False, None, "cross-origin apply requests are forbidden"

        def _apply_response(
            self,
            status: int,
            payload: dict[str, Any],
            cors_origin: str | None = None,
        ) -> None:
            """Send an apply response without the gateway's wildcard CORS policy."""
            data = json.dumps(redact_host_paths(payload), ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if cors_origin:
                self.send_header("Access-Control-Allow-Origin", cors_origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
            if (
                not _gateway_peer_allowed(str(self.client_address[0]))
                or not _gateway_host_allowed(self.headers.get("Host", ""))
            ):
                self._apply_response(421, {"ok": False, "error": "gateway requires a loopback peer and Host"})
                return
            parts = [part for part in urlparse(self.path).path.split("/") if part]
            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "apply_patch":
                allowed, cors_origin, error = self._apply_origin_policy()
                if not allowed:
                    self._apply_response(403, {"ok": False, "error": error})
                    return
                task_id = parts[1]
                if not valid_task_id(task_id):
                    self._apply_response(
                        400,
                        {"ok": False, "error": "invalid task_id", "task_id": task_id},
                        cors_origin,
                    )
                    return
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                if cors_origin:
                    self.send_header("Access-Control-Allow-Origin", cors_origin)
                    self.send_header("Vary", "Origin")
                    self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                    self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
                    self.send_header("Access-Control-Max-Age", "300")
                self.end_headers()
                return
            self.send_response(204)
            allowed, cors_origin, _error = self._apply_origin_policy()
            if allowed and cors_origin:
                self.send_header("Access-Control-Allow-Origin", cors_origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if (
                not _gateway_peer_allowed(str(self.client_address[0]))
                or not _gateway_host_allowed(self.headers.get("Host", ""))
            ):
                self._apply_response(421, {"ok": False, "error": "gateway requires a loopback peer and Host"})
                return
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                response(self, 200, {"ok": True, "gateway": "WarmasterGateway", "display_name": "Abaddon"})
                return
            if parsed.path == "/capabilities":
                response(self, 200, gateway_capabilities())
                return
            if parsed.path == "/state":
                query = parse_qs(parsed.query)
                raw_limit = query.get("run_limit", ["20"])[0]
                run_limit = parse_limit(raw_limit, default=20)
                include_health = query.get("health", ["0"])[0] in {"1", "true", "yes"}
                host = query.get("host", ["127.0.0.1"])[0]
                try:
                    payload = gateway_state(run_root, run_limit=run_limit, include_health=include_health, host=host)
                except ValueError as exc:
                    response(self, 400, {"ok": False, "error": str(exc)})
                    return
                response(self, 200, payload)
                return
            if parsed.path == "/doctor":
                payload = run_doctor()
                response(self, 200 if payload.get("ok") else 500, payload)
                return
            if parsed.path == "/recovery":
                all_runs = list_runs(run_root)
                response(self, 200, {"ok": True, "recovery": recovery_summary(all_runs)})
                return
            if parsed.path == "/brigade_plan":
                query = parse_qs(parsed.query)
                host = query.get("host", ["127.0.0.1"])[0]
                try:
                    payload = brigade_plan_snapshot(host=host)
                except ValueError as exc:
                    response(self, 400, {"ok": False, "error": str(exc)})
                    return
                response(self, 200, payload)
                return
            if parsed.path == "/brigade_health":
                query = parse_qs(parsed.query)
                host = query.get("host", ["127.0.0.1"])[0]
                try:
                    payload = brigade_health_snapshot(host=host)
                except ValueError as exc:
                    response(self, 400, {"ok": False, "error": str(exc)})
                    return
                response(self, 200, payload)
                return
            if parsed.path == "/governors":
                query = parse_qs(parsed.query)
                include_health = query.get("health", ["0"])[0] in {"1", "true", "yes"}
                governors = governor_registry_snapshot(include_health=include_health)
                summary = registry_summary(governors, include_health=include_health)
                response(
                    self,
                    200,
                    {
                        "ok": True,
                        "health_checked": include_health,
                        "summary": summary,
                        "display": registry_display("Governor", summary, include_health=include_health),
                        "governors": governors,
                    },
                )
                return
            if parsed.path == "/workers":
                query = parse_qs(parsed.query)
                include_health = query.get("health", ["0"])[0] in {"1", "true", "yes"}
                workers = worker_registry_snapshot(include_health=include_health)
                summary = registry_summary(workers, include_health=include_health)
                response(
                    self,
                    200,
                    {
                        "ok": True,
                        "health_checked": include_health,
                        "summary": summary,
                        "display": registry_display("Worker", summary, include_health=include_health),
                        "workers": workers,
                    },
                )
                return
            if parsed.path == "/events":
                query = parse_qs(parsed.query)
                raw_limit = query.get("limit", [""])[0]
                limit = parse_limit(raw_limit, default=MAX_LIST_LIMIT) if raw_limit else None
                raw_after = query.get("after", [""])[0]
                after = parse_nonnegative_int(raw_after, default=0) if raw_after else None
                response(self, 200, all_run_events(run_root, limit=limit, after=after))
                return
            parts = [part for part in parsed.path.split("/") if part]
            warmaster_root = Path(__file__).resolve().parents[1]
            if parts == ["missions"]:
                query = parse_qs(parsed.query)
                limit = parse_limit(query.get("limit", ["50"])[0], default=50)
                response(self, 200, {"ok": True, "missions": list_missions(warmaster_root, limit=limit)})
                return
            if len(parts) == 2 and parts[0] == "missions":
                query = parse_qs(parsed.query)
                event_limit = parse_limit(query.get("event_limit", ["100"])[0], default=100)
                try:
                    payload = mission_state(warmaster_root, parts[1], event_limit=event_limit)
                except FileNotFoundError:
                    response(self, 404, {"ok": False, "error": "mission not found", "mission_id": parts[1]})
                    return
                response(self, 200, payload)
                return
            if parts == ["campaigns"]:
                response(self, 200, {"ok": True, "campaigns": list_campaigns(run_root)})
                return
            if len(parts) == 2 and parts[0] == "campaigns":
                campaign_id = parts[1]
                try:
                    payload = campaign_state(run_root, campaign_id)
                except FileNotFoundError:
                    response(self, 404, {"ok": False, "error": "campaign not found", "campaign_id": campaign_id})
                    return
                response(self, 200, payload)
                return
            if parts == ["runs"]:
                query = parse_qs(parsed.query)
                raw_limit = query.get("limit", [""])[0]
                all_runs = list_runs(run_root)
                runs = all_runs[: parse_limit(raw_limit, default=MAX_LIST_LIMIT)] if raw_limit else all_runs
                with ACTIVE_RUNS_LOCK:
                    process_active_runs = sorted(ACTIVE_RUNS)
                response(
                    self,
                    200,
                    {
                        "ok": True,
                        "run_summary": run_status_summary(all_runs),
                        "recovery": recovery_summary(all_runs),
                        "runs": runs,
                        "orchestration_cards": run_orchestration_cards(runs, process_active_runs),
                    },
                )
                return
            if len(parts) == 4 and parts[0] == "runs" and parts[2] == "steps":
                task_id = parts[1]
                try:
                    run_dir = _resolved_run_dir(run_root, task_id)
                except ValueError as exc:
                    response(self, 400, {"ok": False, "error": str(exc), "task_id": task_id})
                    return
                if not valid_task_id(parts[3]):
                    response(self, 400, {"ok": False, "error": "invalid step_id", "task_id": task_id})
                    return
                if not run_dir.exists():
                    response(self, 404, {"ok": False, "error": "run not found", "task_id": task_id})
                    return
                payload = run_step_state(run_dir, parts[3])
                payload = payload_with_run_view(payload, run_dir, task_id)
                response(self, 200 if payload.get("ok") else 404, payload)
                return
            if len(parts) == 5 and parts[0] == "runs" and parts[2] == "steps" and parts[4] == "artifacts":
                task_id = parts[1]
                try:
                    run_dir = _resolved_run_dir(run_root, task_id)
                except ValueError as exc:
                    response(self, 400, {"ok": False, "error": str(exc), "task_id": task_id})
                    return
                if not valid_task_id(parts[3]):
                    response(self, 400, {"ok": False, "error": "invalid step_id", "task_id": task_id})
                    return
                if not run_dir.exists():
                    response(self, 404, {"ok": False, "error": "run not found", "task_id": task_id})
                    return
                payload = run_step_artifacts(run_dir, parts[3])
                payload = payload_with_run_view(payload, run_dir, task_id)
                response(self, 200 if payload.get("ok") else 404, payload)
                return
            if len(parts) in {2, 3} and parts[0] == "runs":
                task_id = parts[1]
                try:
                    run_dir = _resolved_run_dir(run_root, task_id)
                except ValueError as exc:
                    response(self, 400, {"ok": False, "error": str(exc), "task_id": task_id})
                    return
                status_path = run_dir / "status.json"
                ledger_path = run_dir / "task_ledger.json"
                if not run_dir.exists():
                    response(self, 404, {"ok": False, "error": "run not found", "task_id": task_id})
                    return
                if len(parts) == 3 and parts[2] == "summary":
                    summary = run_summary(run_dir)
                    view = orchestration_view_fields(summary, task_id=task_id)
                    response(
                        self,
                        200,
                        {
                            "ok": True,
                            "summary": summary,
                            "phase": view.get("phase", ""),
                            "status": view.get("status", ""),
                            "decision": view.get("decision", {}),
                            "display": view.get("display", {}),
                            "next_action": view.get("next_action", {}),
                            "client_action": view.get("client_action", {}),
                        },
                    )
                    return
                if len(parts) == 3 and parts[2] == "snapshot":
                    query = parse_qs(parsed.query)
                    raw_event_limit = query.get("event_limit", [""])[0]
                    event_limit = parse_limit(raw_event_limit, default=MAX_LIST_LIMIT) if raw_event_limit else None
                    raw_events_after = query.get("events_after", [""])[0]
                    events_after = parse_nonnegative_int(raw_events_after, default=0) if raw_events_after else None
                    response(self, 200, run_snapshot(run_dir, event_limit=event_limit, events_after=events_after))
                    return
                if len(parts) == 3 and parts[2] == "activity":
                    snapshot = run_snapshot(run_dir, event_limit=0, events_after=0)
                    activity = snapshot.get("governor_activity", {}) if isinstance(snapshot.get("governor_activity"), dict) else {}
                    entries = activity.get("entries") if isinstance(activity.get("entries"), list) else []
                    activity_cards = activity.get("activity_cards") if isinstance(activity.get("activity_cards"), list) else entries
                    progress_events = activity.get("progress_events") if isinstance(activity.get("progress_events"), list) else []
                    protocol_cards = activity.get("protocol_activity_cards") if isinstance(activity.get("protocol_activity_cards"), list) else []
                    summary_cards = activity.get("summary_activity_cards") if isinstance(activity.get("summary_activity_cards"), list) else []
                    summary = snapshot.get("summary", {}) if isinstance(snapshot.get("summary"), dict) else {}
                    protocol_summary = {}
                    mission_protocol = summary.get("mission_protocol") if isinstance(summary.get("mission_protocol"), dict) else {}
                    mission_ref = summary.get("mission_ref") if isinstance(summary.get("mission_ref"), dict) else {}
                    mission_id = str(mission_ref.get("mission_id") or "")
                    if mission_id:
                        protocol_summary = mission_protocol.get("protocol_summary") if isinstance(mission_protocol.get("protocol_summary"), dict) else {}
                    response(
                        self,
                        200,
                        {
                            "ok": bool(snapshot.get("ok")),
                            "task_id": task_id,
                            "governor_activity": activity,
                            "progress_events": progress_events,
                            "protocol_activity_cards": protocol_cards,
                            "summary_activity_cards": summary_cards,
                            "entries": entries,
                            "activity_cards": activity_cards,
                            "activity_log": "",
                            "protocol_summary": protocol_summary,
                            "mission_state": snapshot.get("mission_state", {}),
                            "summary": summary,
                            "active": bool(snapshot.get("active")),
                        },
                    )
                    return
                if len(parts) == 3 and parts[2] == "orchestration":
                    query = parse_qs(parsed.query)
                    raw_event_limit = query.get("event_limit", [""])[0]
                    event_limit = parse_limit(raw_event_limit, default=20) if raw_event_limit else 20
                    raw_events_after = query.get("events_after", [""])[0]
                    events_after = parse_nonnegative_int(raw_events_after, default=0) if raw_events_after else 0
                    raw_max_bytes = query.get("max_bytes", [""])[0]
                    max_bytes = parse_limit(raw_max_bytes, default=2000, maximum=MAX_ARTIFACT_TEXT_BYTES) if raw_max_bytes else 2000
                    response(self, 200, orchestration_state(run_dir, event_limit=event_limit, events_after=events_after, max_bytes=max_bytes))
                    return
                if len(parts) == 3 and parts[2] == "active":
                    with ACTIVE_RUNS_LOCK:
                        active = task_id in ACTIVE_RUNS
                    response(self, 200, {"ok": True, "task_id": task_id, "active": active})
                    return
                if len(parts) == 3 and parts[2] == "ledger":
                    if not ledger_path.exists():
                        response(self, 404, {"ok": False, "error": "ledger not found", "task_id": task_id})
                        return
                    ledger, ledger_error = load_ledger_dict(ledger_path)
                    if ledger_error:
                        response(self, 500, {"ok": False, "error": ledger_error, "task_id": task_id})
                        return
                    response(self, 200, {"ok": True, "ledger": ledger})
                    return
                if len(parts) == 3 and parts[2] == "package":
                    payload = run_package_diagnostics(run_dir)
                    payload = payload_with_run_view(payload, run_dir, task_id)
                    response(self, 200 if payload.get("ok") else 409, payload)
                    return
                if len(parts) == 3 and parts[2] == "contract":
                    payload = run_contract(run_dir)
                    payload = payload_with_run_view(payload, run_dir, task_id)
                    status_code = 500 if payload.get("error_code") == "corrupt_contract" else 404
                    response(self, 200 if payload.get("ok") else status_code, payload)
                    return
                if len(parts) == 3 and parts[2] == "oversight":
                    payload = run_oversight_diagnostics(run_dir)
                    payload = payload_with_run_view(payload, run_dir, task_id)
                    status_code = 500 if payload.get("error_code") == "corrupt_oversight" else 404
                    response(self, 200 if payload.get("ok") else status_code, payload)
                    return
                if len(parts) == 3 and parts[2] == "dispatch":
                    native_adapter = native_adapter_for_run(run_dir, declared=True)
                    payload = (
                        {
                            "ok": True,
                            "native": True,
                            "execution": dict(native_adapter.execution),
                            "dispatch": [],
                            "dispatch_count": 0,
                        }
                        if native_adapter is not None
                        else run_dispatch_packets(run_dir)
                    )
                    payload = payload_with_run_view(payload, run_dir, task_id)
                    response(self, 200 if payload.get("ok") else 404, payload)
                    return
                if len(parts) == 3 and parts[2] == "worker_tasks":
                    query = parse_qs(parsed.query)
                    include_live = query.get("live", ["0"])[0] in {"1", "true", "yes"}
                    host = query.get("host", ["127.0.0.1"])[0]
                    try:
                        payload = run_worker_tasks(run_dir, include_health=include_live, host=host)
                    except ValueError as exc:
                        payload = payload_with_run_view({"ok": False, "error": str(exc), "task_id": task_id}, run_dir, task_id)
                        response(self, 400, payload)
                        return
                    payload = payload_with_run_view(payload, run_dir, task_id)
                    response(self, 200 if payload.get("ok") else 404, payload)
                    return
                if len(parts) == 3 and parts[2] == "events":
                    query = parse_qs(parsed.query)
                    raw_limit = query.get("limit", [""])[0]
                    limit = parse_limit(raw_limit, default=MAX_LIST_LIMIT) if raw_limit else None
                    raw_after = query.get("after", [""])[0]
                    after = parse_nonnegative_int(raw_after, default=0) if raw_after else None
                    payload = run_events(run_dir, limit=limit, after=after)
                    payload = payload_with_run_view(payload, run_dir, task_id)
                    response(self, 200 if payload.get("ok") else 404, payload)
                    return
                if len(parts) == 3 and parts[2] == "artifacts":
                    if not ledger_path.exists():
                        payload = payload_with_run_view({"ok": False, "error": "ledger not found", "task_id": task_id}, run_dir, task_id)
                        response(self, 404, payload)
                        return
                    ledger, ledger_error = load_ledger_dict(ledger_path)
                    if ledger_error:
                        payload = payload_with_run_view({"ok": False, "error": ledger_error, "task_id": task_id}, run_dir, task_id)
                        response(self, 500, payload)
                        return
                    payload = payload_with_run_view({"ok": True, "task_id": task_id, **artifact_status(ledger)}, run_dir, task_id)
                    response(self, 200, payload)
                    return
                if len(parts) == 3 and parts[2] == "artifact":
                    if not ledger_path.exists():
                        response(
                            self,
                            404,
                            {"ok": False, "error": "ledger not found", "task_id": task_id},
                        )
                        return
                    query = parse_qs(parsed.query)
                    artifact_path = query.get("path", [""])[0]
                    if not artifact_path:
                        response(
                            self,
                            400,
                            {"ok": False, "error": "artifact path is required", "task_id": task_id},
                        )
                        return
                    ledger, ledger_error = load_ledger_dict(ledger_path)
                    if ledger_error:
                        response(
                            self,
                            500,
                            {"ok": False, "error": ledger_error, "task_id": task_id},
                        )
                        return
                    result = ledger.get("result") if isinstance(ledger.get("result"), dict) else {}
                    ledger_status = str(ledger.get("status") or result.get("status") or "").lower()
                    if ledger_status != "completed":
                        response(
                            self,
                            409,
                            {
                                "ok": False,
                                "error": "binary artifacts are exportable only after run completion",
                                "task_id": task_id,
                            },
                        )
                        return
                    headers_started = False
                    try:
                        with open_artifact_binary(ledger, artifact_path) as (reader, size):
                            media_type, disposition = _artifact_download_headers(artifact_path)
                            self.send_response(200)
                            self.send_header("Content-Type", media_type)
                            self.send_header("Content-Length", str(size))
                            self.send_header("Content-Disposition", disposition)
                            self.send_header("Cache-Control", "no-store")
                            self.send_header("X-Content-Type-Options", "nosniff")
                            self.send_header("Accept-Ranges", "none")
                            self.end_headers()
                            headers_started = True
                            remaining = size
                            while remaining:
                                chunk = reader.read(min(ARTIFACT_STREAM_CHUNK_BYTES, remaining))
                                if not chunk:
                                    raise OSError("recorded artifact changed during export")
                                self.wfile.write(chunk)
                                remaining -= len(chunk)
                    except ValueError as exc:
                        if not headers_started:
                            response(
                                self,
                                400,
                                {"ok": False, "error": str(exc), "task_id": task_id},
                            )
                        else:
                            self.close_connection = True
                        return
                    except (BrokenPipeError, ConnectionResetError):
                        self.close_connection = True
                        return
                    except OSError as exc:
                        if not headers_started:
                            response(
                                self,
                                500,
                                {"ok": False, "error": f"artifact export failed: {exc}", "task_id": task_id},
                            )
                        else:
                            self.close_connection = True
                        return
                    return
                if len(parts) == 3 and parts[2] == "final":
                    if not ledger_path.exists():
                        payload = payload_with_run_view({"ok": False, "error": "ledger not found", "task_id": task_id}, run_dir, task_id)
                        response(self, 404, payload)
                        return
                    query = parse_qs(parsed.query)
                    raw_max_bytes = query.get("max_bytes", [""])[0]
                    max_bytes = parse_limit(raw_max_bytes, default=20000, maximum=MAX_ARTIFACT_TEXT_BYTES) if raw_max_bytes else 20000
                    ledger, ledger_error = load_ledger_dict(ledger_path)
                    if ledger_error:
                        payload = payload_with_run_view({"ok": False, "error": ledger_error, "task_id": task_id}, run_dir, task_id)
                        response(self, 500, payload)
                        return
                    try:
                        payload = final_package(ledger, max_bytes=max_bytes)
                    except ValueError as exc:
                        payload = payload_with_run_view({"ok": False, "error": str(exc), "task_id": task_id}, run_dir, task_id)
                        response(self, 400, payload)
                        return
                    final_payload = {"task_id": task_id, **payload}
                    payload = payload_with_run_view(final_payload, run_dir, task_id)
                    if final_payload.get("kind") == "skitarii_bridge_result":
                        # Generic run-view lifecycle is durably "blocked" for a
                        # ready-to-apply result; preserve the more precise native
                        # Skitarii phase/next_action while still attaching run_summary.
                        payload.update(final_payload)
                        native_action = (
                            final_payload.get("next_action")
                            if isinstance(final_payload.get("next_action"), dict)
                            else {}
                        )
                        payload["client_action"] = executable_client_action(task_id, native_action)
                    retrieved = payload.get("kind") == "skitarii_bridge_result"
                    response(self, 200 if payload.get("ok") or retrieved else 404, payload)
                    return
                if len(parts) == 3 and parts[2] == "artifact_text":
                    if not ledger_path.exists():
                        payload = payload_with_run_view({"ok": False, "error": "ledger not found", "task_id": task_id}, run_dir, task_id)
                        response(self, 404, payload)
                        return
                    query = parse_qs(parsed.query)
                    artifact_path = query.get("path", [""])[0]
                    raw_max_bytes = query.get("max_bytes", [""])[0]
                    max_bytes = parse_limit(raw_max_bytes, default=MAX_ARTIFACT_TEXT_BYTES, maximum=MAX_ARTIFACT_TEXT_BYTES) if raw_max_bytes else MAX_ARTIFACT_TEXT_BYTES
                    ledger, ledger_error = load_ledger_dict(ledger_path)
                    if ledger_error:
                        payload = payload_with_run_view({"ok": False, "error": ledger_error, "task_id": task_id}, run_dir, task_id)
                        response(self, 500, payload)
                        return
                    try:
                        payload = artifact_text(ledger, artifact_path, max_bytes=max_bytes)
                    except ValueError as exc:
                        payload = payload_with_run_view({"ok": False, "error": str(exc), "task_id": task_id}, run_dir, task_id)
                        response(self, 400, payload)
                        return
                    payload = payload_with_run_view(payload, run_dir, task_id)
                    response(self, 200 if payload.get("ok") else 404, payload)
                    return
                status, status_error = load_json_object(status_path, "status") if status_path.exists() else ({}, "")
                ledger, ledger_error = load_ledger_dict(ledger_path)
                status_payload = {"ok": True, "task_id": task_id, "run_dir": str(run_dir), "status": status, "ledger": ledger}
                if status_error:
                    status_payload["status_error"] = status_error
                if ledger_error and ledger_path.exists():
                    status_payload["ledger"] = {}
                    status_payload["ledger_error"] = ledger_error
                    response(self, 200, status_payload)
                    return
                summary = run_summary(run_dir)
                view = orchestration_view_fields(summary, task_id=task_id)
                status_payload.update(
                    {
                        "summary": summary,
                        "phase": view.get("phase", ""),
                        "decision": view.get("decision", {}),
                        "display": view.get("display", {}),
                        "next_action": view.get("next_action", {}),
                        "client_action": view.get("client_action", {}),
                    }
                )
                response(self, 200, status_payload)
                return
            response(self, 404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if (
                not _gateway_peer_allowed(str(self.client_address[0]))
                or not _gateway_host_allowed(self.headers.get("Host", ""))
            ):
                self._apply_response(421, {"ok": False, "error": "gateway requires a loopback peer and Host"})
                return
            request_parts = [part for part in urlparse(self.path).path.split("/") if part]
            apply_request = (
                len(request_parts) == 3
                and request_parts[0] == "runs"
                and request_parts[2] == "apply_patch"
            )
            apply_cors_origin: str | None = None
            try:
                allowed, apply_cors_origin, error = self._apply_origin_policy()
                if not allowed:
                    if apply_request:
                        self._apply_response(403, {"ok": False, "error": error})
                    else:
                        response(self, 403, {"ok": False, "error": error})
                    return
                payload = read_payload(self)
                if self.path == "/orchestrate":
                    model_decision = gateway_model_decision("orchestrate", payload)
                    if not model_decision.get("ok"):
                        response(self, 503, gateway_model_required_payload(model_decision))
                        return
                    message = str(payload.get("message") or payload.get("task") or "").strip()
                    if not message:
                        response(self, 400, {"ok": False, "error": "message is required"})
                        return
                    task_id = str(payload.get("task_id") or "").strip() or task_id_for_message(message)
                    governor_transport = str(payload.get("governor_transport") or default_governor_transport).strip() or default_governor_transport
                    governor_host = str(payload.get("governor_host") or default_governor_host).strip() or default_governor_host
                    run_mode = str(payload.get("run_mode") or "http").strip() or "http"
                    host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                    timeout_sec = max(1, min(int(payload.get("timeout_sec") or 30), 7200))
                    include_brigade_health = bool(payload.get("include_brigade_health"))
                    prepared = orchestrate_run_task(
                        message,
                        task_id,
                        run_root,
                        governor_transport=governor_transport,
                        governor_host=governor_host,
                        run_mode=run_mode,
                        host=host,
                        timeout_sec=timeout_sec,
                        include_brigade_health=include_brigade_health,
                        auto_start=False,
                        force=bool(payload.get("force")),
                        reuse_existing=bool(payload.get("reuse_existing", True)),
                        task_memory_id=str(payload.get("task_memory_id") or payload.get("goal_id") or "").strip(),
                        root_task_id=str(payload.get("root_task_id") or "").strip(),
                        parent_task_id=str(payload.get("parent_task_id") or payload.get("continuation_of") or "").strip(),
                    )
                    prepared = attach_model_brain(prepared, model_decision)
                    response(self, 200 if prepared.get("ok") else 409, prepared)
                    return
                if self.path == "/orchestrate_start":
                    model_decision = gateway_model_decision("orchestrate_start", payload)
                    if not model_decision.get("ok"):
                        response(self, 503, gateway_model_required_payload(model_decision))
                        return
                    task_id = str(payload.get("task_id") or "").strip()
                    if not task_id:
                        response(self, 400, {"ok": False, "error": "task_id is required"})
                        return
                    if not valid_task_id(task_id):
                        response(self, 400, {"ok": False, "error": "invalid task_id", "task_id": task_id})
                        return
                    run_mode = str(payload.get("run_mode") or "http").strip() or "http"
                    host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                    default_timeout = int(
                        os.environ.get("RESEARCH_WARBAND_BRIDGE_TIMEOUT_SEC", "604800")
                    )
                    timeout_sec = max(
                        1,
                        min(
                            int(payload.get("timeout_sec") or default_timeout),
                            604800,
                        ),
                    )
                    started = orchestrate_start_run(
                        run_root,
                        task_id,
                        run_mode=run_mode,
                        host=host,
                        timeout_sec=timeout_sec,
                        force=bool(payload.get("force")),
                        revision_token=str(payload.get("revision_token") or ""),
                    )
                    started = attach_model_brain(started, model_decision)
                    response(
                        self,
                        202
                        if started.get("ok")
                        else 503
                        if started.get("retryable")
                        else 409,
                        started,
                    )
                    return
                if self.path == "/orchestrate_run":
                    model_decision = gateway_model_decision("orchestrate_run", payload)
                    if not model_decision.get("ok"):
                        response(self, 503, gateway_model_required_payload(model_decision))
                        return
                    message = str(payload.get("message") or payload.get("task") or "").strip()
                    if not message:
                        response(self, 400, {"ok": False, "error": "message is required"})
                        return
                    task_id = str(payload.get("task_id") or "").strip() or task_id_for_message(message)
                    governor_transport = str(payload.get("governor_transport") or default_governor_transport).strip() or default_governor_transport
                    governor_host = str(payload.get("governor_host") or default_governor_host).strip() or default_governor_host
                    run_mode = str(payload.get("run_mode") or "http").strip() or "http"
                    host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                    default_timeout = int(
                        os.environ.get("RESEARCH_WARBAND_BRIDGE_TIMEOUT_SEC", "604800")
                    )
                    timeout_sec = max(
                        1,
                        min(
                            int(payload.get("timeout_sec") or default_timeout),
                            604800,
                        ),
                    )
                    include_brigade_health = bool(payload.get("include_brigade_health"))
                    auto_start = bool(payload.get("auto_start", True))
                    submitted = orchestrate_run_task(
                        message,
                        task_id,
                        run_root,
                        governor_transport=governor_transport,
                        governor_host=governor_host,
                        run_mode=run_mode,
                        host=host,
                        timeout_sec=timeout_sec,
                        include_brigade_health=include_brigade_health,
                        auto_start=auto_start,
                        force=bool(payload.get("force")),
                        reuse_existing=bool(payload.get("reuse_existing", True)),
                        task_memory_id=str(payload.get("task_memory_id") or payload.get("goal_id") or "").strip(),
                        root_task_id=str(payload.get("root_task_id") or "").strip(),
                        parent_task_id=str(payload.get("parent_task_id") or payload.get("continuation_of") or "").strip(),
                    )
                    submitted = attach_model_brain(submitted, model_decision)
                    if submitted.get("ok") and submitted.get("phase") == "started":
                        response(self, 202, submitted)
                    else:
                        response(
                            self,
                            200
                            if submitted.get("ok")
                            else 503
                            if submitted.get("retryable")
                            else 409,
                            submitted,
                        )
                    return
                if self.path == "/task_preflight":
                    model_decision = gateway_model_decision("task_preflight", payload)
                    if not model_decision.get("ok"):
                        response(self, 503, gateway_model_required_payload(model_decision))
                        return
                    message = str(payload.get("message") or payload.get("task") or "").strip()
                    if not message:
                        response(self, 400, {"ok": False, "error": "message is required"})
                        return
                    task_id = str(payload.get("task_id") or "").strip() or task_id_for_message(message)
                    governor_transport = str(payload.get("governor_transport") or default_governor_transport).strip() or default_governor_transport
                    governor_host = str(payload.get("governor_host") or default_governor_host).strip() or default_governor_host
                    include_brigade_health = bool(payload.get("include_brigade_health"))
                    mission_id = mission_id_for(task_id, message)
                    commander = build_commander_order(message, mission_id)
                    if not commander.get("ok"):
                        failed = {
                            "ok": False,
                            "phase": "commander_intake",
                            "task_id": task_id or "",
                            "mission_id": mission_id,
                            "error": str(commander.get("error") or "Abaddon commander intake failed"),
                            "error_code": str(commander.get("error_code") or "commander_intake_failed"),
                            "commander_preview": commander,
                        }
                        failed = attach_model_brain(failed, model_decision)
                        response(self, 400, failed)
                        return
                    command = commander.get("commander_order") if isinstance(commander.get("commander_order"), dict) else {}
                    try:
                        task_memory_init, task_memory_ref = ensure_task_memory_for_intake(
                            run_root=run_root,
                            task_id=task_id,
                            message=message,
                            mission_id=mission_id,
                            task_memory_id=str(
                                payload.get("task_memory_id")
                                or payload.get("goal_id")
                                or ""
                            ).strip(),
                            root_task_id=str(payload.get("root_task_id") or "").strip(),
                            parent_task_id=str(
                                payload.get("parent_task_id")
                                or payload.get("continuation_of")
                                or ""
                            ).strip(),
                        )
                    except TaskMemoryParentConflict as exc:
                        response(
                            self,
                            409,
                            {
                                "ok": False,
                                "phase": "task_memory_identity",
                                "task_id": task_id,
                                "error_code": "task_memory_parent_conflict",
                                "error": str(exc),
                            },
                        )
                        return
                    except ValueError as exc:
                        response(
                            self,
                            400,
                            {
                                "ok": False,
                                "phase": "task_memory_identity",
                                "task_id": task_id,
                                "error_code": "invalid_task_memory_identity",
                                "error": str(exc),
                            },
                        )
                        return
                    if not task_memory_init.get("ok"):
                        failed = attach_model_brain(
                            {
                                "ok": False,
                                "retryable": bool(task_memory_init.get("retryable")),
                                "phase": "task_memory_retry",
                                "task_id": task_id,
                                "error_code": str(
                                    task_memory_init.get("error_code")
                                    or "task_memory_unavailable"
                                ),
                                "error": str(task_memory_init.get("warning") or ""),
                                "task_memory": task_memory_init,
                            },
                            model_decision,
                        )
                        response(
                            self,
                            503 if failed.get("retryable") else 409,
                            failed,
                        )
                        return
                    command_task = governor_task_from_order(command)
                    preflight = preflight_task(
                        command_task,
                        task_id,
                        run_root,
                        governor_transport=governor_transport,
                        governor_host=governor_host,
                        include_brigade_health=include_brigade_health,
                        forced_governor=str(command.get("to") or "") or None,
                        commander_order=command,
                        require_commander_order=True,
                    )
                    actions = preflight.get("actions") if isinstance(preflight.get("actions"), dict) else {}
                    next_action = actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {}
                    body = next_action.get("body") if isinstance(next_action.get("body"), dict) else {}
                    if body:
                        body["message"] = message
                        task_memory_id = str(task_memory_ref["task_memory_id"])
                        root_task_id = str(task_memory_ref["root_task_id"])
                        parent_task_id = str(task_memory_ref["parent_task_id"])
                        body["task_memory_id"] = task_memory_id
                        body["root_task_id"] = root_task_id
                        if parent_task_id:
                            body["parent_task_id"] = parent_task_id
                            body["continuation_of"] = parent_task_id
                        next_action["body"] = body
                        actions["next_action"] = next_action
                        preflight["actions"] = actions
                    preflight = payload_with_task_view(preflight, fallback_task_id=task_id or "")
                    preflight["protocol_mode"] = "commander_order"
                    if isinstance(commander.get("route"), dict):
                        preflight["route"] = commander["route"]
                    preflight["mission"] = {
                        "mission_id": mission_id,
                        "assigned_governor": str(command.get("to") or ""),
                        "mission_dir": "",
                    }
                    preflight["commander_order"] = command
                    preflight = attach_model_brain(preflight, model_decision)
                    response(self, 409 if preflight.get("error_code") == "task_exists" else (200 if preflight.get("ok") else 400), preflight)
                    return
                if self.path == "/campaign_preflight":
                    model_decision = gateway_model_decision("campaign_preflight", payload)
                    if not model_decision.get("ok"):
                        response(self, 503, gateway_model_required_payload(model_decision))
                        return
                    message = str(payload.get("message") or payload.get("task") or "").strip()
                    if not message:
                        response(self, 400, {"ok": False, "error": "message is required"})
                        return
                    campaign_id = str(payload.get("campaign_id") or payload.get("task_id") or "").strip() or None
                    preflight = campaign_preflight(message, campaign_id=campaign_id)
                    preflight = attach_model_brain(preflight, model_decision)
                    response(self, 200 if preflight.get("ok") else 400, preflight)
                    return
                if self.path == "/campaign":
                    model_decision = gateway_model_decision("campaign", payload)
                    if not model_decision.get("ok"):
                        response(self, 503, gateway_model_required_payload(model_decision))
                        return
                    message = str(payload.get("message") or payload.get("task") or "").strip()
                    if not message:
                        response(self, 400, {"ok": False, "error": "message is required"})
                        return
                    campaign_id = str(payload.get("campaign_id") or payload.get("task_id") or "").strip() or None
                    prepared = prepare_campaign(run_root, message, campaign_id=campaign_id, force=bool(payload.get("force")))
                    prepared = attach_model_brain(prepared, model_decision)
                    response(self, 200 if prepared.get("ok") else 409, prepared)
                    return
                if self.path == "/recover_stale":
                    recovered = recover_stale_runs(run_root)
                    response(self, 200, {"ok": True, "recovered": recovered})
                    return
                if self.path in {"/recovery/start_resume_local", "/recovery/start_resume_http"}:
                    try:
                        mode = "local" if self.path.endswith("_local") else "http"
                        host = str(payload.get("host") or "127.0.0.1")
                        timeout_sec = int(payload.get("timeout_sec") or 1800)
                        recovered = start_recoverable_runs(run_root, mode=mode, host=host, timeout_sec=timeout_sec)
                    except ValueError as exc:
                        response(self, 400, {"ok": False, "error": str(exc)})
                        return
                    response(self, 202, recovered)
                    return
                parts = [part for part in self.path.split("?")[0].split("/") if part]
                if len(parts) == 3 and parts[0] == "campaigns" and parts[2] in {"start", "resume", "cancel"}:
                    campaign_id = parts[1]
                    if not valid_task_id(campaign_id):
                        response(self, 400, {"ok": False, "error": "invalid campaign_id", "campaign_id": campaign_id})
                        return
                    if parts[2] == "cancel":
                        reason = str(payload.get("reason") or "").strip()
                        host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                        try:
                            cancelled = cancel_campaign(run_root, campaign_id, reason=reason, host=host)
                        except FileNotFoundError:
                            response(self, 404, {"ok": False, "error": "campaign not found", "campaign_id": campaign_id})
                            return
                        response(self, 200 if cancelled.get("ok") else 409, cancelled)
                        return
                    run_mode = str(payload.get("run_mode") or "http").strip() or "http"
                    host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                    timeout_sec = max(1, min(int(payload.get("timeout_sec") or 1800), 7200))
                    max_revision_cycles = max(0, min(int(payload.get("max_revision_cycles") or 3), 8))
                    allow_resume = bool(payload.get("allow_resume", True))
                    governor_transport = str(payload.get("governor_transport") or default_governor_transport).strip() or default_governor_transport
                    governor_host = str(payload.get("governor_host") or default_governor_host).strip() or default_governor_host
                    max_subruns = max(1, min(int(payload.get("max_subruns") or 8), 32))
                    try:
                        campaign_state(run_root, campaign_id)
                    except FileNotFoundError:
                        response(self, 404, {"ok": False, "error": "campaign not found", "campaign_id": campaign_id})
                        return
                    executor = lambda: resume_campaign(
                        run_root,
                        campaign_id,
                        run_mode=run_mode,
                        host=host,
                        timeout_sec=timeout_sec,
                        max_revision_cycles=max_revision_cycles,
                        allow_resume=allow_resume,
                        governor_transport=governor_transport,
                        governor_host=governor_host,
                        max_subruns=max_subruns,
                    )
                    if parts[2] == "start":
                        active_key = f"campaign:{campaign_id}"
                        started = start_background(active_key, executor)
                        if not started:
                            response(self, 409, {"ok": False, "error": "campaign already active", "campaign_id": campaign_id})
                            return
                        poll_action = {
                            "kind": "poll_campaign",
                            "method": "GET",
                            "endpoint": "GET /campaigns/{campaign_id}",
                            "body": {},
                            "reason": "campaign started in background",
                        }
                        response(
                            self,
                            202,
                            {
                                "ok": True,
                                "campaign_id": campaign_id,
                                "status": "started",
                                "next_action": poll_action,
                                "client_action": {
                                    "method": "GET",
                                    "path": f"/campaigns/{campaign_id}",
                                    "body": {},
                                    "reason": "campaign started in background",
                                },
                            },
                        )
                        return
                    try:
                        resumed = executor()
                    except FileNotFoundError:
                        response(self, 404, {"ok": False, "error": "campaign not found", "campaign_id": campaign_id})
                        return
                    response(self, 200 if resumed.get("ok") else 409, resumed)
                    return
                if len(parts) == 3 and parts[0] == "runs" and parts[2] == "clarification":
                    task_id = parts[1]
                    try:
                        run_dir = _resolved_run_dir(run_root, task_id)
                    except ValueError as exc:
                        response(self, 400, {"ok": False, "error": str(exc), "task_id": task_id})
                        return
                    if not run_dir.exists():
                        response(self, 404, {"ok": False, "error": "run not found", "task_id": task_id})
                        return
                    answer = str(payload.get("answer") or "").strip()
                    if not answer:
                        response(self, 400, {"ok": False, "error": "answer is required", "task_id": task_id})
                        return
                    try:
                        backend_route = execution_backend_route(run_dir)
                        if not backend_route.get("ok"):
                            response(self, 409, backend_route)
                            return
                        task_memory_guard = task_memory_start_guard(run_dir)
                        if not task_memory_guard.get("ok"):
                            response(
                                self,
                                503 if task_memory_guard.get("retryable") else 409,
                                {
                                    "ok": False,
                                    "phase": "clarification_task_memory_guard",
                                    "task_id": task_id,
                                    "retryable": bool(task_memory_guard.get("retryable")),
                                    "error_code": str(
                                        task_memory_guard.get("error_code")
                                        or "task_memory_guard_failed"
                                    ),
                                    "error": str(
                                        task_memory_guard.get("warning")
                                        or "task-memory guard rejected clarification resume"
                                    ),
                                    "task_memory": task_memory_guard,
                                },
                            )
                            return
                        backend = str(backend_route.get("backend") or "")
                        if backend == "ResearchWarband":
                            answered = answer_research_warband_mission(
                                run_dir, task_id, answer
                            )
                        elif backend == "SkitariiWarband":
                            answered = answer_skitarii_mission(
                                run_dir, task_id, answer
                            )
                        else:
                            answered = {
                                "ok": False,
                                "status": "conflict",
                                "error": "run backend does not support durable clarification",
                            }
                    except (OSError, RuntimeError, ValueError) as exc:
                        response(
                            self,
                            502,
                            {"ok": False, "error": f"clarification forwarding failed: {exc}", "task_id": task_id},
                        )
                        return
                    next_action = {
                        "kind": "poll",
                        "method": "GET",
                        "endpoint": "GET /runs/{task_id}/snapshot",
                        "body": {"events_after": 0},
                        "reason": "clarification accepted; the same native mission resumed",
                    }
                    answered["next_action"] = next_action if answered.get("ok") else {}
                    answered["client_action"] = (
                        executable_client_action(task_id, next_action) if answered.get("ok") else {}
                    )
                    response(self, 200 if answered.get("ok") else 409, answered)
                    return
                if len(parts) == 3 and parts[0] == "runs" and parts[2] == "apply_patch":
                    task_id = parts[1]
                    if not valid_task_id(task_id):
                        self._apply_response(
                            400,
                            {"ok": False, "error": "invalid task_id", "task_id": task_id},
                            apply_cors_origin,
                        )
                        return
                    run_root_resolved = run_root.resolve()
                    run_candidate = run_root_resolved / task_id
                    if run_candidate.is_symlink():
                        self._apply_response(
                            400,
                            {"ok": False, "error": "run directory must not be a symlink", "task_id": task_id},
                            apply_cors_origin,
                        )
                        return
                    run_dir = run_candidate.resolve()
                    if run_dir == run_root_resolved or run_root_resolved not in run_dir.parents:
                        self._apply_response(
                            400,
                            {"ok": False, "error": "run directory escapes run_root", "task_id": task_id},
                            apply_cors_origin,
                        )
                        return
                    if payload.get("confirm_apply") is not True:
                        self._apply_response(
                            400,
                            {"ok": False, "error": "confirm_apply must be true", "task_id": task_id},
                            apply_cors_origin,
                        )
                        return
                    digests: dict[str, str] = {}
                    for field in (
                        "expected_repository_fingerprint",
                        "expected_patch_sha256",
                        "expected_checks_sha256",
                    ):
                        digest = str(payload.get(field) or "").strip().lower()
                        if not re.fullmatch(r"[0-9a-f]{64}", digest):
                            self._apply_response(
                                400,
                                {
                                    "ok": False,
                                    "error": f"{field} must be a SHA-256 hex digest",
                                    "task_id": task_id,
                                },
                                apply_cors_origin,
                            )
                            return
                        digests[field] = digest
                    ledger_candidate = run_dir / "task_ledger.json"
                    if ledger_candidate.is_symlink():
                        self._apply_response(
                            400,
                            {"ok": False, "error": "ledger must not be a symlink", "task_id": task_id},
                            apply_cors_origin,
                        )
                        return
                    ledger_path = ledger_candidate.resolve()
                    if run_dir not in ledger_path.parents:
                        self._apply_response(
                            400,
                            {"ok": False, "error": "ledger escapes run directory", "task_id": task_id},
                            apply_cors_origin,
                        )
                        return
                    if not ledger_path.exists():
                        self._apply_response(
                            404,
                            {"ok": False, "error": "ledger not found", "task_id": task_id},
                            apply_cors_origin,
                        )
                        return
                    ledger = TaskLedger.load(ledger_path)
                    applied = apply_staged_patch(
                        run_dir,
                        ledger,
                        digests["expected_repository_fingerprint"],
                        expected_patch_sha256=digests["expected_patch_sha256"],
                        expected_checks_sha256=digests["expected_checks_sha256"],
                    )
                    applied_status = str(applied.get("status") or applied.get("phase") or "")
                    response_status = (
                        200 if applied.get("ok")
                        else 202 if applied_status in {
                            "apply_intent", "applied_unverified", "publishing",
                            "push_pending", "protocol_finalize_pending",
                        }
                        else 409
                    )
                    self._apply_response(
                        response_status,
                        applied,
                        apply_cors_origin,
                    )
                    return
                if len(parts) == 3 and parts[0] == "runs" and parts[2] == "cancel":
                    task_id = parts[1]
                    try:
                        run_dir = _resolved_run_dir(run_root, task_id)
                    except ValueError as exc:
                        response(self, 400, {"ok": False, "error": str(exc), "task_id": task_id})
                        return
                    ledger_path = run_dir / "task_ledger.json"
                    if not ledger_path.exists():
                        response(self, 404, {"ok": False, "error": "ledger not found", "task_id": task_id})
                        return
                    backend_route = execution_backend_route(run_dir)
                    if not backend_route.get("ok"):
                        response(self, 409, backend_route)
                        return
                    backend = str(backend_route.get("backend") or "")
                    reason = str(payload.get("reason") or "").strip()
                    cancellation = begin_run_cancellation(run_dir, reason)
                    if not cancellation.get("ok"):
                        cancellation_status = str(cancellation.get("status") or "")
                        inspect_action = {"kind": "inspect", "method": "GET", "endpoint": "GET /runs/{task_id}/summary", "body": {}, "reason": "run is already terminal"}
                        if cancellation_status in {
                            "apply_intent", "applied_unverified", "publishing",
                            "push_pending", "protocol_finalize_pending",
                        }:
                            inspect_action = {
                                "kind": "poll", "method": "GET",
                                "endpoint": "GET /runs/{task_id}/orchestration", "body": {},
                                "reason": "repository mutation is already durable and must finish safely",
                            }
                        response(
                            self,
                            409,
                            {
                                "ok": False,
                                "task_id": task_id,
                                "status": cancellation_status,
                                "error": str(cancellation.get("error") or "run is already terminal"),
                                "ledger": cancellation.get("ledger") or {},
                                "next_action": inspect_action,
                                "client_action": executable_client_action(task_id, inspect_action),
                            },
                        )
                        return
                    try:
                        if backend == "ResearchWarband":
                            backend_cancellation = cancel_research_warband_mission_for_run(
                                run_dir, task_id,
                            )
                        elif backend == "SkitariiWarband":
                            backend_cancellation = cancel_skitarii_mission_for_run(
                                run_dir, task_id,
                            )
                        else:
                            backend_cancellation = {
                                "ok": False,
                                "status": "not_active",
                                "error": "run has no active native backend mission",
                            }
                    except (OSError, RuntimeError, ValueError) as exc:
                        backend_cancellation = {
                            "ok": False,
                            "status": "error",
                            "error": f"native backend cancellation failed: {exc}",
                        }
                    host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                    worker_cancellations = (
                        []
                        if backend in {"SkitariiWarband", "ResearchWarband"}
                        else cancel_http_worker_tasks(run_dir, host=host)
                    )
                    backend_status = str(backend_cancellation.get("status") or "")
                    if backend_status == "cancelled" and backend_cancellation.get("ok") is True:
                        inspect_action = {
                            "kind": "inspect", "method": "GET",
                            "endpoint": "GET /runs/{task_id}/summary", "body": {},
                            "reason": "native backend cancellation and cleanup are complete",
                        }
                        refreshed = TaskLedger.load(ledger_path)
                        response(self, 200, {
                            "ok": True, "task_id": task_id, "status": "cancelled",
                            "ledger": refreshed.to_dict(),
                            "backend_cancellation": backend_cancellation,
                            "skitarii_cancellation": (
                                backend_cancellation if backend == "SkitariiWarband" else {}
                            ),
                            "research_warband_cancellation": (
                                backend_cancellation if backend == "ResearchWarband" else {}
                            ),
                            "worker_cancellations": worker_cancellations,
                            "next_action": inspect_action,
                            "client_action": executable_client_action(task_id, inspect_action),
                        })
                        return
                    if (
                        backend_cancellation.get("ok") is not True
                        and backend_status not in {"", "not_active"}
                    ):
                        inspect_action = {
                            "kind": "inspect", "method": "GET",
                            "endpoint": "GET /runs/{task_id}/summary", "body": {},
                            "reason": "cancellation cleanup failed and the run is blocked",
                        }
                        refreshed = TaskLedger.load(ledger_path)
                        response(self, 409, {
                            "ok": False, "task_id": task_id,
                            "status": str(refreshed.to_dict().get("status") or "blocked"),
                            "error": str(backend_cancellation.get("error") or "native backend cancellation failed"),
                            "ledger": refreshed.to_dict(),
                            "backend_cancellation": backend_cancellation,
                            "skitarii_cancellation": (
                                backend_cancellation if backend == "SkitariiWarband" else {}
                            ),
                            "research_warband_cancellation": (
                                backend_cancellation if backend == "ResearchWarband" else {}
                            ),
                            "worker_cancellations": worker_cancellations,
                            "next_action": inspect_action,
                            "client_action": executable_client_action(task_id, inspect_action),
                        })
                        return
                    poll_action = {"kind": "poll", "method": "GET", "endpoint": "GET /runs/{task_id}/snapshot", "body": {"events_after": 0}, "reason": "cancellation is cooperative and should be polled"}
                    response(
                        self,
                        200,
                        {
                            "ok": True,
                            "task_id": task_id,
                            "status": "cancelling",
                            "ledger": TaskLedger.load(ledger_path).to_dict(),
                            "backend_cancellation": backend_cancellation,
                            "skitarii_cancellation": (
                                backend_cancellation if backend == "SkitariiWarband" else {}
                            ),
                            "research_warband_cancellation": (
                                backend_cancellation if backend == "ResearchWarband" else {}
                            ),
                            "worker_cancellations": worker_cancellations,
                            "next_action": poll_action,
                            "client_action": executable_client_action(task_id, poll_action),
                        },
                    )
                    return
                research_loop_modes = {
                    "research_loop_local",
                    "research_loop_http",
                    "start_research_loop_local",
                    "start_research_loop_http",
                }
                if len(parts) == 3 and parts[0] == "runs" and parts[2] in research_loop_modes:
                    task_id = parts[1]
                    try:
                        run_dir = _resolved_run_dir(run_root, task_id)
                    except ValueError as exc:
                        response(self, 400, {"ok": False, "error": str(exc), "task_id": task_id})
                        return
                    if not run_dir.exists():
                        response(self, 404, {"ok": False, "error": "run not found", "task_id": task_id})
                        return
                    run_mode = "local" if parts[2].endswith("_local") else "http"
                    host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                    timeout_sec = max(
                        1,
                        min(
                            int(payload.get("timeout_sec") or 604800),
                            604800,
                        ),
                    )
                    raw_max_revision_cycles = payload.get("max_revision_cycles", 3)
                    max_revision_cycles = max(0, min(int(raw_max_revision_cycles), 8))
                    allow_resume = bool(payload.get("allow_resume", True))
                    if parts[2].startswith("start_"):
                        executor = lambda: research_loop_run(
                            run_root,
                            task_id,
                            run_mode=run_mode,
                            host=host,
                            timeout_sec=timeout_sec,
                            max_revision_cycles=max_revision_cycles,
                            allow_resume=allow_resume,
                            claim_active=False,
                        )
                        record_research_loop_event(
                            run_dir,
                            "research_loop_background_requested",
                            {
                                "mode": parts[2],
                                "max_revision_cycles": max_revision_cycles,
                                "allow_resume": allow_resume,
                            },
                        )
                        started = start_background(task_id, executor)
                        if not started:
                            poll_action = {"kind": "poll", "method": "GET", "endpoint": "GET /runs/{task_id}/snapshot", "body": {"events_after": 0}, "reason": "run is already active"}
                            response(
                                self,
                                409,
                                {
                                    "ok": False,
                                    "error": "run already active",
                                    "task_id": task_id,
                                    "next_action": poll_action,
                                    "client_action": executable_client_action(task_id, poll_action),
                                },
                            )
                            return
                        poll_action = {"kind": "poll", "method": "GET", "endpoint": "GET /runs/{task_id}/snapshot", "body": {"events_after": 0}, "reason": "research loop started in background"}
                        response(
                            self,
                            202,
                            {
                                "ok": True,
                                "task_id": task_id,
                                "status": "started",
                                "operation": "research_loop",
                                "run_mode": run_mode,
                                "next_action": poll_action,
                                "client_action": executable_client_action(task_id, poll_action),
                            },
                        )
                        return
                    loop_result = research_loop_run(
                        run_root,
                        task_id,
                        run_mode=run_mode,
                        host=host,
                        timeout_sec=timeout_sec,
                        max_revision_cycles=max_revision_cycles,
                        allow_resume=allow_resume,
                    )
                    response(self, 200 if loop_result.get("ok") else 409, loop_result)
                    return
                execution_modes = {
                    "preflight_local",
                    "preflight_http",
                    "execute_local",
                    "execute_http",
                    "start_local",
                    "start_http",
                    "execute_revision_local",
                    "execute_revision_http",
                    "resume_local",
                    "resume_http",
                    "start_revision_local",
                    "start_revision_http",
                    "start_resume_local",
                    "start_resume_http",
                }
                if len(parts) == 3 and parts[0] == "runs" and parts[2] in execution_modes:
                    task_id = parts[1]
                    try:
                        run_dir = _resolved_run_dir(run_root, task_id)
                    except ValueError as exc:
                        response(self, 400, {"ok": False, "error": str(exc), "task_id": task_id})
                        return
                    if not run_dir.exists():
                        response(self, 404, {"ok": False, "error": "run not found", "task_id": task_id})
                        return
                    preflight_mode = parts[2] in {"preflight_local", "preflight_http"}
                    backend_route = execution_backend_route(run_dir)
                    if not backend_route.get("ok"):
                        response(self, 409, backend_route)
                        return
                    if not preflight_mode:
                        task_memory_guard = task_memory_start_guard(run_dir)
                        if not task_memory_guard.get("ok"):
                            response(
                                self,
                                503 if task_memory_guard.get("retryable") else 409,
                                {
                                    "ok": False,
                                    "retryable": bool(task_memory_guard.get("retryable")),
                                    "phase": "task_memory_retry",
                                    "task_id": task_id,
                                    "error_code": str(
                                        task_memory_guard.get("error_code")
                                        or "task_memory_unavailable"
                                    ),
                                    "error": str(
                                        task_memory_guard.get("warning")
                                        or "task memory is not ready for execution"
                                    ),
                                    "task_memory": task_memory_guard,
                                    "next_action": {},
                                    "client_action": {},
                                },
                            )
                            return
                    native_backend = backend_route.get("backend") in {
                        "SkitariiWarband", "ResearchWarband"
                    }
                    ledger_path = run_dir / "task_ledger.json"
                    force = bool(payload.get("force"))
                    revision_mode = "revision" in parts[2]
                    resume_mode = "resume" in parts[2]
                    if ledger_path.exists():
                        ledger = TaskLedger.load(ledger_path)
                        ledger_data = ledger.to_dict()
                        if resume_mode and ledger_data.get("status") != "interrupted":
                            response(
                                self,
                                409,
                                {
                                    "ok": False,
                                    "error": "resume requires an interrupted run",
                                    "ledger": ledger_data,
                                },
                            )
                            return
                        if not preflight_mode and not force and ledger_data.get("status") == "completed" and not revision_mode:
                            response(
                                self,
                                409,
                                {
                                    "ok": False,
                                    "error": "run already completed; pass force=true to rerun",
                                    "ledger": ledger_data,
                                },
                            )
                            return
                        if resume_mode:
                            ledger.record_event("resume_execution_requested", {"mode": parts[2]})
                    requested_step_ids = requested_step_ids_from_payload(payload)
                    if native_backend:
                        if requested_step_ids:
                            raise ValueError(
                                "native warband runs are atomic missions and do not accept step_ids"
                            )
                        mode_step_ids = None
                        restricted_step_ids = None
                    else:
                        mode_step_ids = revision_step_ids_from_run(run_dir) if revision_mode else (resume_step_ids_from_run(run_dir) if resume_mode else None)
                        if requested_step_ids:
                            validate_requested_step_ids(run_dir, requested_step_ids, allowed=mode_step_ids)
                            restricted_step_ids = requested_step_ids
                        else:
                            restricted_step_ids = mode_step_ids
                    workspace_root = resolve_run_child_path(run_dir, str(payload.get("workspace_root") or ""), "work")
                    if backend_route.get("backend") == "ResearchWarband":
                        default_timeout = int(
                            os.environ.get(
                                "RESEARCH_WARBAND_BRIDGE_TIMEOUT_SEC", "604800"
                            )
                        )
                        timeout_sec = max(
                            1,
                            min(
                                int(payload.get("timeout_sec") or default_timeout),
                                604800,
                            ),
                        )
                    else:
                        timeout_sec = max(1, min(int(payload.get("timeout_sec") or 1800), 7200))
                    execution_mode = "revision" if revision_mode else ("resume" if resume_mode else "full")
                    if parts[2] in {"preflight_local", "preflight_http"}:
                        if parts[2] == "preflight_http":
                            host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                            http_workspace_root = workspace_root if "workspace_root" in payload else None
                            preflight = run_execution_preflight(
                                run_dir,
                                mode="http",
                                workspace_root=http_workspace_root,
                                host=host,
                                timeout_sec=timeout_sec,
                                step_ids=restricted_step_ids,
                                force=force,
                            )
                        else:
                            preflight = run_execution_preflight(
                                run_dir,
                                mode="local",
                                workspace_root=workspace_root,
                                timeout_sec=timeout_sec,
                                step_ids=restricted_step_ids,
                                force=force,
                            )
                        record_run_preflight_event(run_dir, preflight)
                        post_summary = run_summary(run_dir)
                        post_view = orchestration_view_fields(post_summary, task_id=task_id)
                        preflight_actions = preflight.get("actions") if isinstance(preflight.get("actions"), dict) else {}
                        next_action = preflight_actions.get("next_action") if isinstance(preflight_actions.get("next_action"), dict) else post_view.get("next_action", {})
                        preflight = {
                            **preflight,
                            "run_summary": post_summary,
                            "phase": post_view.get("phase", ""),
                            "status": post_view.get("status", ""),
                            "decision": post_view.get("decision", {}),
                            "display": post_view.get("display", {}),
                            "next_action": next_action,
                            "client_action": executable_client_action(task_id, next_action),
                        }
                        response(self, 200 if preflight.get("ok") else 409, preflight)
                        return
                    local_execution = parts[2] in {
                        "execute_local",
                        "start_local",
                        "execute_revision_local",
                        "start_revision_local",
                        "resume_local",
                        "start_resume_local",
                    }
                    routed_run_mode = "local" if local_execution else "http"
                    routed_host = (
                        "127.0.0.1"
                        if local_execution
                        else validate_service_host(str(payload.get("host") or "127.0.0.1"))
                    )
                    routed_workspace = (
                        workspace_root
                        if local_execution or "workspace_root" in payload
                        else None
                    )
                    if native_backend:
                        native_preflight = run_execution_preflight(
                            run_dir,
                            mode=routed_run_mode,
                            workspace_root=routed_workspace,
                            host=routed_host,
                            timeout_sec=timeout_sec,
                            force=force,
                        )
                        record_run_preflight_event(run_dir, native_preflight)
                        native_actions = (
                            native_preflight.get("actions")
                            if isinstance(native_preflight.get("actions"), dict)
                            else {}
                        )
                        if (
                            not native_preflight.get("ok")
                            or native_actions.get("can_start_run") is not True
                        ):
                            next_action = (
                                native_actions.get("next_action")
                                if isinstance(native_actions.get("next_action"), dict)
                                else {}
                            )
                            response(
                                self,
                                409,
                                {
                                    **native_preflight,
                                    "ok": False,
                                    "error_code": "native_preflight_failed",
                                    "error": (
                                        "native run is not startable under its current durable state"
                                    ),
                                    "task_id": task_id,
                                    "backend_route": backend_route,
                                    "run_preflight": native_preflight,
                                    "next_action": next_action,
                                    "client_action": executable_client_action(task_id, next_action),
                                },
                            )
                            return
                    native_revision_reserved = False
                    if native_backend and revision_mode:
                        revision_rejection: dict[str, Any] = {}
                        # Validate and reserve under the same process-wide lock.
                        # This makes the revision token a single-flight command
                        # for both asynchronous start_* and synchronous execute_*
                        # routes; no second request may pass validation before the
                        # first request makes the ledger state change visible.
                        with ACTIVE_RUNS_LOCK:
                            if task_id in ACTIVE_RUNS:
                                poll_action = {
                                    "kind": "poll",
                                    "method": "GET",
                                    "endpoint": "GET /runs/{task_id}/snapshot",
                                    "body": {"events_after": 0},
                                    "reason": "the exact native revision is already active",
                                }
                                revision_rejection = {
                                    "ok": False,
                                    "error_code": "native_revision_already_active",
                                    "error": "the native revision is already active",
                                    "remediation": (
                                        "poll the current run; do not submit the same revision token again"
                                    ),
                                    "task_id": task_id,
                                    "next_action": poll_action,
                                    "client_action": executable_client_action(
                                        task_id, poll_action
                                    ),
                                }
                            else:
                                current_summary = run_summary(run_dir)
                                current_actions = (
                                    current_summary.get("actions")
                                    if isinstance(current_summary.get("actions"), dict)
                                    else {}
                                )
                                current_action = (
                                    current_actions.get("next_action")
                                    if isinstance(current_actions.get("next_action"), dict)
                                    else {}
                                )
                                current_body = (
                                    current_action.get("body")
                                    if isinstance(current_action.get("body"), dict)
                                    else {}
                                )
                                expected_token = str(
                                    current_body.get("revision_token") or ""
                                )
                                provided_token = str(payload.get("revision_token") or "")
                                if (
                                    not expected_token
                                    or not provided_token
                                    or not secrets.compare_digest(
                                        provided_token, expected_token
                                    )
                                ):
                                    revision_rejection = {
                                        "ok": False,
                                        "error_code": "stale_or_missing_revision_token",
                                        "error": (
                                            "native revision was not started: the revision token "
                                            "is missing or no longer matches the current mission attempt"
                                        ),
                                        "remediation": (
                                            "fetch the current run summary and execute its published "
                                            "revision action unchanged"
                                        ),
                                        "task_id": task_id,
                                        "status": current_summary.get("status", ""),
                                        "next_action": current_action,
                                        "client_action": executable_client_action(
                                            task_id, current_action
                                        ),
                                    }
                                else:
                                    ACTIVE_RUNS.add(task_id)
                                    native_revision_reserved = True
                        if revision_rejection:
                            response(self, 409, revision_rejection)
                            return

                    def release_native_revision_reservation() -> None:
                        if not native_revision_reserved:
                            return
                        with ACTIVE_RUNS_LOCK:
                            ACTIVE_RUNS.discard(task_id)

                    executor = lambda: execute_with_ledger_failure_guard(
                        run_dir,
                        lambda: execute_routed_run(
                            run_dir,
                            run_mode=routed_run_mode,
                            host=routed_host,
                            timeout_sec=timeout_sec,
                            workspace_root=routed_workspace,
                            step_ids=restricted_step_ids,
                            execution_mode=execution_mode,
                        ),
                    )
                    if parts[2].startswith("start_"):
                        if ledger_path.exists():
                            try:
                                event_payload: dict[str, Any] = {
                                    "mode": parts[2],
                                    "backend": str(backend_route.get("backend") or ""),
                                }
                                if restricted_step_ids:
                                    event_payload["step_ids"] = restricted_step_ids
                                TaskLedger.load(ledger_path).record_event("background_start_requested", event_payload)
                            except Exception:
                                pass
                        if native_revision_reserved:
                            def reserved_background_execution() -> None:
                                try:
                                    executor()
                                finally:
                                    release_native_revision_reservation()

                            try:
                                threading.Thread(
                                    target=reserved_background_execution,
                                    daemon=True,
                                    name=f"native-revision-{task_id}",
                                ).start()
                            except Exception:
                                release_native_revision_reservation()
                                raise
                            started = True
                        else:
                            started = start_background(task_id, executor)
                        if not started:
                            poll_action = {"kind": "poll", "method": "GET", "endpoint": "GET /runs/{task_id}/snapshot", "body": {"events_after": 0}, "reason": "run is already active"}
                            response(
                                self,
                                409,
                                {
                                    "ok": False,
                                    "error": "run already active",
                                    "task_id": task_id,
                                    "next_action": poll_action,
                                    "client_action": executable_client_action(task_id, poll_action),
                                },
                            )
                            return
                        poll_action = {"kind": "poll", "method": "GET", "endpoint": "GET /runs/{task_id}/snapshot", "body": {"events_after": 0}, "reason": "run started in background"}
                        response(
                            self,
                            202,
                            {
                                "ok": True,
                                "task_id": task_id,
                                "status": "started",
                                "backend_route": backend_route,
                                "next_action": poll_action,
                                "client_action": executable_client_action(task_id, poll_action),
                            },
                        )
                        return
                    if native_revision_reserved:
                        try:
                            summary = executor()
                        finally:
                            release_native_revision_reservation()
                    else:
                        summary = executor()
                    post_summary = run_summary(run_dir)
                    post_view = orchestration_view_fields(post_summary, task_id=task_id)
                    response(
                        self,
                        execution_response_status(summary, post_summary),
                        {
                            "ok": bool(summary.get("ok")),
                            "summary": summary,
                            "backend_route": backend_route,
                            "run_summary": post_summary,
                            "phase": post_view.get("phase", ""),
                            "status": post_view.get("status", ""),
                            "decision": post_view.get("decision", {}),
                            "display": post_view.get("display", {}),
                            "next_action": post_view.get("next_action", {}),
                            "client_action": post_view.get("client_action", {}),
                        },
                    )
                    return
                response(self, 404, {"ok": False, "error": "not found"})
            except ValueError as exc:
                payload = {"ok": False, "gateway": "WarmasterGateway", "error": str(exc)}
                if apply_request:
                    self._apply_response(400, payload, apply_cors_origin)
                else:
                    response(self, 400, payload)
            except Exception as exc:  # noqa: BLE001 - gateway boundary records routing failures.
                payload = {"ok": False, "gateway": "WarmasterGateway", "error": str(exc)}
                if apply_request:
                    self._apply_response(500, payload, apply_cors_origin)
                else:
                    response(self, 500, payload)

    return WarmasterHandler


def orphan_run_watchdog(run_root: Path, interval_sec: float = 60.0, grace_sec: float = 120.0) -> None:
    """Start research loops for runs that were prepared but never started.

    Mission creation and loop start are two separate client calls; when planning
    outlives the client's HTTP timeout, the client disconnects before sending
    the start command and the run sits in plan_review forever. The watchdog
    adopts such orphans instead of leaving them stuck.
    """
    import time as _time
    from datetime import datetime, timezone

    while True:
        _time.sleep(interval_sec)
        try:
            _resume_pending_publications(run_root)
            for ledger_path in run_root.glob("*/task_ledger.json"):
                try:
                    ledger_data = json.loads(ledger_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if str(ledger_data.get("status") or "") != "created":
                    continue
                events = ledger_data.get("events") or []
                if any(str(event.get("type") or "").startswith("research_loop") for event in events):
                    continue
                if not any(str(event.get("type") or "") == "run_preflight_recorded" for event in events):
                    continue
                try:
                    updated = datetime.fromisoformat(str(ledger_data.get("updated_at")))
                    age = (datetime.now(timezone.utc) - updated).total_seconds()
                except (TypeError, ValueError):
                    age = grace_sec + 1
                if age < grace_sec:
                    continue
                task_id = str(ledger_data.get("task_id") or ledger_path.parent.name)
                run_dir = ledger_path.parent
                executor = lambda tid=task_id: research_loop_run(
                    run_root,
                    tid,
                    run_mode="http",
                    host="127.0.0.1",
                    timeout_sec=1800,
                    max_revision_cycles=3,
                    allow_resume=True,
                    claim_active=False,
                )
                record_research_loop_event(
                    run_dir,
                    "research_loop_background_requested",
                    {"mode": "orphan_watchdog", "max_revision_cycles": 3, "allow_resume": True, "orphan_age_sec": int(age)},
                )
                if start_background(task_id, executor):
                    print(f"orphan watchdog: adopted stuck run {task_id} (age {int(age)}s)", flush=True)
        except Exception as exc:  # noqa: BLE001 - the watchdog must survive anything
            print(f"orphan watchdog error: {exc}", flush=True)


def _resume_pending_publications(run_root: Path) -> list[str]:
    """Idempotently resume commit/push/protocol checkpoints after a restart."""
    if os.environ.get("SKITARII_AUTOPUBLISH") != "1":
        return []
    started: list[str] = []
    for ledger_path in run_root.glob("*/task_ledger.json"):
        try:
            ledger = TaskLedger.load(ledger_path)
            payload = ledger.to_dict()
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            phase = str(result.get("phase") or result.get("status") or "")
            if phase not in {
                "apply_intent", "applied_unverified", "publishing",
                "push_pending", "protocol_finalize_pending",
            }:
                continue
            stage = result.get("patch_stage") if isinstance(result.get("patch_stage"), dict) else {}
            fingerprint = str(stage.get("baseline_fingerprint") or "")
            patch_sha = str(stage.get("patch_sha256") or "")
            checks_sha = str(stage.get("checks_sha256") or "")
            if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in (
                fingerprint, patch_sha, checks_sha,
            )):
                continue
            task_id = str(payload.get("task_id") or ledger_path.parent.name)
            run_dir = ledger_path.parent

            def retry(
                directory: Path = run_dir,
                expected: str = fingerprint,
                expected_patch: str = patch_sha,
                expected_checks: str = checks_sha,
            ) -> None:
                try:
                    apply_staged_patch(
                        directory,
                        TaskLedger.load(directory / "task_ledger.json"),
                        expected,
                        expected_patch_sha256=expected_patch,
                        expected_checks_sha256=expected_checks,
                    )
                except Exception as exc:  # noqa: BLE001 - preserve recoverable checkpoint.
                    try:
                        current = TaskLedger.load(directory / "task_ledger.json")
                        current.record_event(
                            "skitarii_publication_retry_error",
                            {"type": type(exc).__name__, "error": str(exc)[:240]},
                        )
                    except Exception:
                        pass

            if start_background(task_id, retry):
                started.append(task_id)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return started


def serve(host: str, port: int, run_root: Path, recover_stale_on_start: bool = True, governor_transport: str = "local", governor_host: str = "127.0.0.1") -> None:
    host = _validate_gateway_bind_host(host)
    run_root.mkdir(parents=True, exist_ok=True)
    _resume_pending_publications(run_root)
    prepare_run_root(run_root, recover_stale_on_start=recover_stale_on_start)
    threading.Thread(target=orphan_run_watchdog, args=(run_root,), daemon=True, name="orphan-run-watchdog").start()
    server = ThreadingHTTPServer((host, port), make_handler(run_root, default_governor_transport=governor_transport, default_governor_host=governor_host))
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the EyeOfTerror Abaddon orchestration gateway.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7000)
    parser.add_argument("--run-root", default="runtime/warmaster-runs")
    parser.add_argument("--no-recover-stale-on-start", action="store_true")
    parser.add_argument("--governor-transport", choices=["local", "http"], default="local")
    parser.add_argument("--governor-host", default="127.0.0.1")
    args = parser.parse_args()
    serve(
        args.host,
        args.port,
        Path(args.run_root),
        recover_stale_on_start=not args.no_recover_stale_on_start,
        governor_transport=args.governor_transport,
        governor_host=args.governor_host,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
