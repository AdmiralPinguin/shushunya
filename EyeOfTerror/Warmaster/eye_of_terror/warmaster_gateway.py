from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from EyeOfTerror.model_brain import attach_model_brain, request_model_decision

from .contracts import validate_task_contract_payload
from .inner_circle.ceraxia import plan_code_task
from .inner_circle.iskandar import plan_lore_reconstruction
from doctor import run_doctor
from .http_executor import execute_run as execute_http_run, preflight_workers as preflight_http_workers
from .governors import governor_by_name, governor_refs
from .ledger import TaskLedger
from .local_executor import WORKER_COMMANDS, execute_run as execute_local_run, input_artifact_errors, ordered_dispatch_paths
from .pipeline import write_pipeline_run
from .registry import worker_refs
from .routing import route_message
from .orchestrator import (
    cancel_http_worker_tasks,
    execute_run_cycle,
    execute_with_ledger_failure_guard,
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
    validate_requested_step_ids,
)
from .task_prepare import (
    cleanup_unregistered_run_dir,
    preflight_task,
    prepare_task,
    prepare_task_via_governor_service,
    route_failure_payload,
)
from .capabilities import (
    gateway_capabilities,
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
    resolve_artifact,
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
    REPO_ROOT,
    TASK_ID_RE,
)


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


def gateway_state(run_root: Path, run_limit: int = 20, include_health: bool = False, host: str = "127.0.0.1") -> dict[str, Any]:
    all_runs = list_runs(run_root)
    runs = all_runs[: parse_limit(str(run_limit), default=20)]
    with ACTIVE_RUNS_LOCK:
        process_active_runs = sorted(ACTIVE_RUNS)
    payload = {
        "ok": True,
        "gateway": "WarmasterGateway",
        "capabilities": gateway_capabilities(),
        "actions": gateway_actions(),
        "governors": governor_registry_snapshot(),
        "workers": worker_registry_snapshot(),
        "brigade_plan": brigade_plan_snapshot(),
        "run_summary": run_status_summary(all_runs),
        "recovery": recovery_summary(all_runs),
        "process_active_runs": process_active_runs,
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

        def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                response(self, 200, {"ok": True, "gateway": "WarmasterGateway"})
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
                run_dir = run_root / task_id
                if not run_dir.exists():
                    response(self, 404, {"ok": False, "error": "run not found", "task_id": task_id})
                    return
                payload = run_step_state(run_dir, parts[3])
                payload = payload_with_run_view(payload, run_dir, task_id)
                response(self, 200 if payload.get("ok") else 404, payload)
                return
            if len(parts) == 5 and parts[0] == "runs" and parts[2] == "steps" and parts[4] == "artifacts":
                task_id = parts[1]
                run_dir = run_root / task_id
                if not run_dir.exists():
                    response(self, 404, {"ok": False, "error": "run not found", "task_id": task_id})
                    return
                payload = run_step_artifacts(run_dir, parts[3])
                payload = payload_with_run_view(payload, run_dir, task_id)
                response(self, 200 if payload.get("ok") else 404, payload)
                return
            if len(parts) in {2, 3} and parts[0] == "runs":
                task_id = parts[1]
                run_dir = run_root / task_id
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
                    payload = run_dispatch_packets(run_dir)
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
                    payload = payload_with_run_view({"task_id": task_id, **payload}, run_dir, task_id)
                    response(self, 200 if payload.get("ok") else 404, payload)
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
            try:
                payload = read_payload(self)
                if self.path == "/orchestrate":
                    model_decision = gateway_model_decision("orchestrate", payload)
                    message = str(payload.get("message") or payload.get("task") or "").strip()
                    if not message:
                        response(self, 400, {"ok": False, "error": "message is required"})
                        return
                    task_id = str(payload.get("task_id") or "").strip() or None
                    governor_transport = str(payload.get("governor_transport") or default_governor_transport).strip() or default_governor_transport
                    governor_host = str(payload.get("governor_host") or default_governor_host).strip() or default_governor_host
                    run_mode = str(payload.get("run_mode") or "http").strip() or "http"
                    host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                    timeout_sec = max(1, min(int(payload.get("timeout_sec") or 30), 7200))
                    include_brigade_health = bool(payload.get("include_brigade_health"))
                    prepared = orchestrate_prepare_task(
                        message,
                        task_id,
                        run_root,
                        governor_transport=governor_transport,
                        governor_host=governor_host,
                        run_mode=run_mode,
                        host=host,
                        timeout_sec=timeout_sec,
                        include_brigade_health=include_brigade_health,
                    )
                    prepared = attach_model_brain(prepared, model_decision)
                    response(self, 200 if prepared.get("ok") else 409, prepared)
                    return
                if self.path == "/orchestrate_start":
                    model_decision = gateway_model_decision("orchestrate_start", payload)
                    task_id = str(payload.get("task_id") or "").strip()
                    if not task_id:
                        response(self, 400, {"ok": False, "error": "task_id is required"})
                        return
                    if not valid_task_id(task_id):
                        response(self, 400, {"ok": False, "error": "invalid task_id", "task_id": task_id})
                        return
                    run_mode = str(payload.get("run_mode") or "http").strip() or "http"
                    host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                    timeout_sec = max(1, min(int(payload.get("timeout_sec") or 1800), 7200))
                    started = orchestrate_start_run(
                        run_root,
                        task_id,
                        run_mode=run_mode,
                        host=host,
                        timeout_sec=timeout_sec,
                        force=bool(payload.get("force")),
                    )
                    started = attach_model_brain(started, model_decision)
                    response(self, 202 if started.get("ok") else 409, started)
                    return
                if self.path == "/orchestrate_run":
                    model_decision = gateway_model_decision("orchestrate_run", payload)
                    message = str(payload.get("message") or payload.get("task") or "").strip()
                    if not message:
                        response(self, 400, {"ok": False, "error": "message is required"})
                        return
                    task_id = str(payload.get("task_id") or "").strip() or None
                    governor_transport = str(payload.get("governor_transport") or default_governor_transport).strip() or default_governor_transport
                    governor_host = str(payload.get("governor_host") or default_governor_host).strip() or default_governor_host
                    run_mode = str(payload.get("run_mode") or "http").strip() or "http"
                    host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                    timeout_sec = max(1, min(int(payload.get("timeout_sec") or 1800), 7200))
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
                    )
                    submitted = attach_model_brain(submitted, model_decision)
                    if submitted.get("ok") and submitted.get("phase") == "started":
                        response(self, 202, submitted)
                    else:
                        response(self, 200 if submitted.get("ok") else 409, submitted)
                    return
                if self.path == "/task":
                    model_decision = gateway_model_decision("task", payload)
                    message = str(payload.get("message") or payload.get("task") or "").strip()
                    if not message:
                        response(self, 400, {"ok": False, "error": "message is required"})
                        return
                    task_id = str(payload.get("task_id") or "").strip() or None
                    governor_transport = str(payload.get("governor_transport") or default_governor_transport).strip() or default_governor_transport
                    governor_host = str(payload.get("governor_host") or default_governor_host).strip() or default_governor_host
                    prepared = prepare_task(message, task_id, run_root, governor_transport=governor_transport, governor_host=governor_host)
                    prepared = payload_with_task_view(prepared, fallback_task_id=task_id or "")
                    prepared = attach_model_brain(prepared, model_decision)
                    response(self, 409 if prepared.get("error_code") == "task_exists" else (200 if prepared.get("ok") else 400), prepared)
                    return
                if self.path == "/task_preflight":
                    model_decision = gateway_model_decision("task_preflight", payload)
                    message = str(payload.get("message") or payload.get("task") or "").strip()
                    if not message:
                        response(self, 400, {"ok": False, "error": "message is required"})
                        return
                    task_id = str(payload.get("task_id") or "").strip() or None
                    governor_transport = str(payload.get("governor_transport") or default_governor_transport).strip() or default_governor_transport
                    governor_host = str(payload.get("governor_host") or default_governor_host).strip() or default_governor_host
                    include_brigade_health = bool(payload.get("include_brigade_health"))
                    preflight = preflight_task(
                        message,
                        task_id,
                        run_root,
                        governor_transport=governor_transport,
                        governor_host=governor_host,
                        include_brigade_health=include_brigade_health,
                    )
                    preflight = payload_with_task_view(preflight, fallback_task_id=task_id or "")
                    preflight = attach_model_brain(preflight, model_decision)
                    response(self, 409 if preflight.get("error_code") == "task_exists" else (200 if preflight.get("ok") else 400), preflight)
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
                if len(parts) == 3 and parts[0] == "runs" and parts[2] == "cancel":
                    task_id = parts[1]
                    ledger_path = run_root / task_id / "task_ledger.json"
                    if not ledger_path.exists():
                        response(self, 404, {"ok": False, "error": "ledger not found", "task_id": task_id})
                        return
                    reason = str(payload.get("reason") or "").strip()
                    ledger = TaskLedger.load(ledger_path)
                    if ledger.to_dict().get("status") in {"completed", "failed", "cancelled", "corrupt"}:
                        inspect_action = {"kind": "inspect", "method": "GET", "endpoint": "GET /runs/{task_id}/summary", "body": {}, "reason": "run is already terminal"}
                        response(
                            self,
                            409,
                            {
                                "ok": False,
                                "task_id": task_id,
                                "error": "run is already terminal",
                                "ledger": ledger.to_dict(),
                                "next_action": inspect_action,
                                "client_action": executable_client_action(task_id, inspect_action),
                            },
                        )
                        return
                    ledger.request_cancel(reason)
                    host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                    worker_cancellations = cancel_http_worker_tasks(run_root / task_id, host=host)
                    poll_action = {"kind": "poll", "method": "GET", "endpoint": "GET /runs/{task_id}/snapshot", "body": {"events_after": 0}, "reason": "cancellation is cooperative and should be polled"}
                    response(
                        self,
                        200,
                        {
                            "ok": True,
                            "task_id": task_id,
                            "status": "cancelling",
                            "ledger": ledger.to_dict(),
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
                    run_dir = run_root / task_id
                    if not run_dir.exists():
                        response(self, 404, {"ok": False, "error": "run not found", "task_id": task_id})
                        return
                    run_mode = "local" if parts[2].endswith("_local") else "http"
                    host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                    timeout_sec = max(1, min(int(payload.get("timeout_sec") or 1800), 7200))
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
                    run_dir = run_root / task_id
                    if not run_dir.exists():
                        response(self, 404, {"ok": False, "error": "run not found", "task_id": task_id})
                        return
                    ledger_path = run_dir / "task_ledger.json"
                    force = bool(payload.get("force"))
                    preflight_mode = parts[2] in {"preflight_local", "preflight_http"}
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
                    mode_step_ids = revision_step_ids_from_run(run_dir) if revision_mode else (resume_step_ids_from_run(run_dir) if resume_mode else None)
                    if requested_step_ids:
                        validate_requested_step_ids(run_dir, requested_step_ids, allowed=mode_step_ids)
                        restricted_step_ids = requested_step_ids
                    else:
                        restricted_step_ids = mode_step_ids
                    workspace_root = resolve_run_child_path(run_dir, str(payload.get("workspace_root") or ""), "work")
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
                            )
                        else:
                            preflight = run_execution_preflight(
                                run_dir,
                                mode="local",
                                workspace_root=workspace_root,
                                timeout_sec=timeout_sec,
                                step_ids=restricted_step_ids,
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
                    if parts[2] in {"execute_local", "start_local", "execute_revision_local", "start_revision_local", "resume_local", "start_resume_local"}:
                        executor = lambda: execute_with_ledger_failure_guard(
                            run_dir,
                            lambda: execute_local_run(
                                REPO_ROOT,
                                run_dir,
                                workspace_root,
                                timeout_sec=timeout_sec,
                                step_ids=restricted_step_ids,
                                execution_mode=execution_mode,
                            ),
                        )
                    else:
                        host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                        http_workspace_root = workspace_root if "workspace_root" in payload else None
                        executor = lambda: execute_with_ledger_failure_guard(
                            run_dir,
                            lambda: execute_http_run(
                                run_dir,
                                host=host,
                                timeout_sec=timeout_sec,
                                workspace_root=http_workspace_root,
                                step_ids=restricted_step_ids,
                                execution_mode=execution_mode,
                            ),
                        )
                    if parts[2].startswith("start_"):
                        if ledger_path.exists():
                            try:
                                event_payload: dict[str, Any] = {"mode": parts[2]}
                                if restricted_step_ids:
                                    event_payload["step_ids"] = restricted_step_ids
                                TaskLedger.load(ledger_path).record_event("background_start_requested", event_payload)
                            except Exception:
                                pass
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
                                "next_action": poll_action,
                                "client_action": executable_client_action(task_id, poll_action),
                            },
                        )
                        return
                    if parts[2] in {"execute_local", "execute_revision_local", "resume_local"}:
                        summary = execute_local_run(REPO_ROOT, run_dir, workspace_root, timeout_sec=timeout_sec, step_ids=restricted_step_ids, execution_mode=execution_mode)
                    else:
                        host = validate_service_host(str(payload.get("host") or "127.0.0.1"))
                        http_workspace_root = workspace_root if "workspace_root" in payload else None
                        summary = execute_http_run(run_dir, host=host, timeout_sec=timeout_sec, workspace_root=http_workspace_root, step_ids=restricted_step_ids, execution_mode=execution_mode)
                    post_summary = run_summary(run_dir)
                    post_view = orchestration_view_fields(post_summary, task_id=task_id)
                    response(
                        self,
                        execution_response_status(summary, post_summary),
                        {
                            "ok": bool(summary.get("ok")),
                            "summary": summary,
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
                response(self, 400, {"ok": False, "gateway": "WarmasterGateway", "error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - gateway boundary records routing failures.
                response(self, 500, {"ok": False, "gateway": "WarmasterGateway", "error": str(exc)})

    return WarmasterHandler


def serve(host: str, port: int, run_root: Path, recover_stale_on_start: bool = True, governor_transport: str = "local", governor_host: str = "127.0.0.1") -> None:
    prepare_run_root(run_root, recover_stale_on_start=recover_stale_on_start)
    server = ThreadingHTTPServer((host, port), make_handler(run_root, default_governor_transport=governor_transport, default_governor_host=governor_host))
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the EyeOfTerror Warmaster Gateway.")
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
