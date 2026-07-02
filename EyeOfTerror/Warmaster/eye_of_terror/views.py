"""Client-facing view helpers for the Warmaster gateway.

These build the compact ``phase`` / ``decision`` / ``display`` /
``next_action`` / ``client_action`` cards that chat and mobile clients
render. They are pure presentation: no run-package IO, no worker calls.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote


def executable_client_action(task_id: str, action: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(action, dict) or not action:
        return {}
    method = str(action.get("method") or "").upper()
    endpoint = str(action.get("endpoint") or "")
    endpoint_method = ""
    path = endpoint
    if " " in endpoint:
        endpoint_method, path = endpoint.split(" ", 1)
        endpoint_method = endpoint_method.upper()
    method = method or endpoint_method
    if "{task_id}" in path:
        path = path.replace("{task_id}", quote(task_id, safe=""))
    body = action.get("body") if isinstance(action.get("body"), dict) else {}
    return {
        "kind": str(action.get("kind") or ""),
        "method": method,
        "path": path,
        "body": body,
        "reason": str(action.get("reason") or ""),
    }


def payload_with_client_action(payload: dict[str, Any], fallback_task_id: str = "") -> dict[str, Any]:
    actions = payload.get("actions") if isinstance(payload.get("actions"), dict) else {}
    next_action = actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {}
    if not next_action or isinstance(payload.get("client_action"), dict):
        return payload
    task_id = str(payload.get("task_id") or fallback_task_id or "")
    enriched = dict(payload)
    enriched["client_action"] = executable_client_action(task_id, next_action)
    return enriched


def payload_with_task_view(payload: dict[str, Any], fallback_task_id: str = "") -> dict[str, Any]:
    actions = payload.get("actions") if isinstance(payload.get("actions"), dict) else {}
    next_action = actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {}
    task_id = str(payload.get("task_id") or fallback_task_id or "")
    ok = bool(payload.get("ok"))
    error_code = str(payload.get("error_code") or "")
    route = payload.get("route") if isinstance(payload.get("route"), dict) else {}
    governor = str(payload.get("governor") or route.get("governor") or "")
    if ok and next_action.get("kind") == "create_task":
        phase = "task_ready"
        headline = "Task is ready"
        detail = str(next_action.get("reason") or "Task can be created")
        severity = "info"
    elif ok:
        phase = "task_created"
        headline = "Task created"
        detail = str(next_action.get("reason") or "Run package is ready for preflight")
        severity = "info"
    elif error_code == "task_exists":
        phase = "existing_task"
        headline = "Task already exists"
        detail = "Open the existing run instead of creating a duplicate"
        severity = "warning"
    elif error_code in {"governor_inactive", "no_supported_governor"}:
        phase = "unsupported_task"
        headline = "No active governor for this task"
        detail = str(payload.get("error") or next_action.get("reason") or error_code)
        severity = "warning"
    elif error_code in {"contract_workers_missing", "contract_workers_unavailable", "governor_workers_missing", "governor_workers_unavailable"}:
        phase = "brigade_blocked"
        headline = "Required workers are unavailable"
        detail = str(next_action.get("reason") or error_code)
        severity = "warning"
    elif error_code:
        phase = "task_blocked"
        headline = "Task cannot be prepared"
        detail = str(payload.get("error") or next_action.get("reason") or error_code)
        severity = "error"
    else:
        phase = "task_blocked"
        headline = "Task cannot be prepared"
        detail = str(payload.get("error") or "Task request failed")
        severity = "error"
    decision = {
        "can_create_task": bool(actions.get("can_create_task")),
        "can_check_brigade_readiness": bool(actions.get("can_check_brigade_readiness")),
        "recommended_kind": str(next_action.get("kind") or ""),
        "recommended_endpoint": str(next_action.get("endpoint") or ""),
    }
    enriched = payload_with_client_action(payload, fallback_task_id=task_id)
    enriched.update(
        {
            "phase": phase,
            "decision": decision,
            "display": {
                "headline": headline,
                "detail": detail,
                "severity": severity,
                "task_id": task_id,
                "governor": governor,
            },
            "next_action": next_action,
            "client_action": executable_client_action(task_id, next_action),
        }
    )
    return enriched


def event_display(event: dict[str, Any], task_id: str = "") -> dict[str, Any]:
    event_type = str(event.get("type") or "")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    severity = "info"
    headline = event_type.replace("_", " ").strip().capitalize() or "Run event"
    detail = ""
    if event_type == "task_created":
        headline = "Task created"
        detail = f"Governor: {payload.get('governor') or 'unknown'}"
    elif event_type == "run_preflight_recorded":
        ok = bool(payload.get("ok"))
        headline = "Preflight passed" if ok else "Preflight needs attention"
        severity = "info" if ok else "warning"
        step_ids = payload.get("step_ids") if isinstance(payload.get("step_ids"), list) else []
        detail = f"{len(step_ids)} steps checked"
    elif event_type == "background_start_requested":
        headline = "Execution queued"
        operation = str(payload.get("operation") or "")
        mode = str(payload.get("mode") or "")
        detail = " ".join(part for part in [operation, mode] if part)
    elif event_type == "status_changed":
        status = str(payload.get("status") or "")
        headline = f"Status: {status}" if status else "Status changed"
        if status in {"failed", "corrupt"}:
            severity = "error"
        elif status in {"cancelled", "interrupted", "preflight_failed"}:
            severity = "warning"
    elif event_type == "step_recorded":
        step_id = str(payload.get("step_id") or "")
        worker = str(payload.get("worker") or "")
        status = str(payload.get("status") or "")
        headline = f"Step {status or 'recorded'}"
        detail = " / ".join(part for part in [step_id, worker] if part)
        if status in {"failed", "blocked", "needs_revision", "preflight_failed"}:
            severity = "warning"
    elif event_type == "result_recorded":
        ok = bool(payload.get("ok"))
        headline = "Result recorded" if ok else "Result failed"
        severity = "info" if ok else "error"
        detail = str(payload.get("summary") or payload.get("status") or "")
    elif event_type in {"cancel_requested", "resume_execution_requested"}:
        headline = event_type.replace("_", " ").capitalize()
        detail = str(payload.get("reason") or payload.get("mode") or "")
    display = {
        "task_id": task_id,
        "at": str(event.get("at") or ""),
        "type": event_type,
        "headline": headline,
        "detail": detail,
        "severity": severity,
    }
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    worker_view = details.get("worker_view") if isinstance(details.get("worker_view"), dict) else {}
    worker_display_payload = worker_view.get("display") if isinstance(worker_view.get("display"), dict) else {}
    worker_client_action = worker_view.get("client_action") if isinstance(worker_view.get("client_action"), dict) else {}
    if worker_display_payload:
        display["worker_display"] = worker_display_payload
    if worker_client_action:
        display["worker_client_action"] = worker_client_action
    return display


def display_events_for(task_id: str, events: list[Any]) -> list[dict[str, Any]]:
    return [event_display(event, task_id=task_id) for event in events if isinstance(event, dict)]


def orchestration_display(
    phase: str,
    status: str,
    snapshot: dict[str, Any],
    next_action: dict[str, Any],
    final_payload: dict[str, Any],
) -> dict[str, Any]:
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    progress = summary.get("progress") if isinstance(summary.get("progress"), dict) else {}
    planned_steps = int(progress.get("planned_steps") or 0)
    completed_steps = int(progress.get("completed_steps") or 0)
    failed_steps = int(progress.get("failed_steps") or 0)
    pending_steps = int(progress.get("pending_steps") or 0)
    next_step_id = str(progress.get("next_ready_step_id") or progress.get("next_step_id") or "")
    next_worker = ""
    step_states = progress.get("step_states") if isinstance(progress.get("step_states"), list) else []
    for step in step_states:
        if isinstance(step, dict) and step.get("step_id") == next_step_id:
            next_worker = str(step.get("worker") or "")
            break
    final_summary = final_payload.get("summary") if isinstance(final_payload.get("summary"), dict) else {}
    final_deliverable = str(final_payload.get("deliverable") or "")
    revision_summary = summary.get("revision_plan_summary") if isinstance(summary.get("revision_plan_summary"), dict) else {}
    headlines = {
        "running": "Run is active",
        "completed": "Run completed",
        "ready_to_start": "Run is ready to start",
        "resume_required": "Run can be resumed",
        "revision_required": "Revision is required",
        "blocked": "Run is blocked",
        "needs_attention": "Run needs attention",
        "ready_to_preflight": "Run needs preflight",
        "inspect": "Inspect run state",
    }
    headline = headlines.get(phase, "Inspect run state")
    if phase == "running" and planned_steps:
        detail = f"{completed_steps}/{planned_steps} steps complete"
    elif phase == "completed" and final_summary:
        detail = f"Final package status: {final_summary.get('status') or 'unknown'}"
    elif phase == "revision_required":
        detail = f"{int(revision_summary.get('step_count') or 0)} revision steps ready"
    elif phase == "blocked":
        detail = str(next_action.get("reason") or "Stable blocker requires inspection")
    elif phase == "resume_required":
        detail = f"{pending_steps} pending steps can resume"
    elif phase == "ready_to_start":
        detail = "Preflight passed; execution can start"
    elif phase == "needs_attention":
        detail = str(next_action.get("reason") or "Diagnostics are required")
    else:
        detail = str(next_action.get("reason") or status or phase)
    severity = "info"
    if phase in {"needs_attention", "revision_required", "blocked"} or failed_steps:
        severity = "warning"
    if status in {"failed", "corrupt"}:
        severity = "error"
    return {
        "headline": headline,
        "detail": detail,
        "severity": severity,
        "progress": {
            "planned_steps": planned_steps,
            "completed_steps": completed_steps,
            "failed_steps": failed_steps,
            "pending_steps": pending_steps,
        },
        "next_step": {
            "step_id": next_step_id,
            "worker": next_worker,
        },
        "final_deliverable": final_deliverable,
    }


def orchestration_view_fields(
    summary: dict[str, Any],
    active: bool = False,
    event_cursor_next: int = 0,
    final_payload: dict[str, Any] | None = None,
    final_max_bytes: int | None = None,
    task_id: str = "",
) -> dict[str, Any]:
    actions = summary.get("actions") if isinstance(summary.get("actions"), dict) else {}
    next_action = actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {}
    status = str(summary.get("status") or "")
    phase = "inspect"
    final_payload = final_payload if isinstance(final_payload, dict) else {}
    if active or status in {"running", "queued", "cancelling"}:
        phase = "running"
        next_action = {"kind": "poll", "method": "GET", "endpoint": "GET /runs/{task_id}/orchestration", "body": {"events_after": event_cursor_next}, "reason": "run is active"}
    elif next_action.get("kind") == "inspect_blockers":
        phase = "blocked"
    elif actions.get("can_start_revision"):
        phase = "revision_required"
    elif status == "completed":
        phase = "completed"
        if not final_payload:
            final_payload = {
                "summary": summary.get("final_manifest_summary") if isinstance(summary.get("final_manifest_summary"), dict) else {},
                "deliverable": "",
            }
        if final_payload.get("ok") or final_payload.get("summary"):
            body = {"max_bytes": final_max_bytes} if final_max_bytes is not None else {}
            next_action = {"kind": "inspect_final", "method": "GET", "endpoint": "GET /runs/{task_id}/final", "body": body, "reason": "run completed and final package is available"}
    elif actions.get("can_resume"):
        phase = "resume_required"
    elif actions.get("can_start"):
        phase = "ready_to_start"
    elif status in {"failed", "cancelled", "interrupted", "preflight_failed"}:
        phase = "needs_attention"
    elif status == "created":
        phase = "ready_to_preflight"
    decision = {
        "can_poll": phase == "running",
        "can_start": phase == "ready_to_start",
        "can_resume": phase == "resume_required",
        "can_execute_revision": phase == "revision_required",
        "can_inspect_final": phase == "completed" and bool(final_payload.get("ok") or final_payload.get("summary")),
        "can_inspect_diagnostics": phase in {"needs_attention", "inspect", "ready_to_preflight"},
        "recommended_kind": str(next_action.get("kind") or ""),
        "recommended_endpoint": str(next_action.get("endpoint") or ""),
    }
    snapshot = {"summary": summary, "active": active}
    resolved_task_id = task_id or str(summary.get("task_id") or "")
    return {
        "status": status,
        "phase": phase,
        "active": active,
        "decision": decision,
        "display": orchestration_display(phase, status, snapshot, next_action, final_payload),
        "next_action": next_action,
        "client_action": executable_client_action(resolved_task_id, next_action),
    }


def run_orchestration_card(run: dict[str, Any], active: bool = False) -> dict[str, Any]:
    task_id = str(run.get("task_id") or "")
    view = orchestration_view_fields(run, active=active, event_cursor_next=0, task_id=task_id)
    return {
        "task_id": task_id,
        "status": view["status"],
        "phase": view["phase"],
        "active": view["active"],
        "goal": str(run.get("goal") or ""),
        "governor": str(run.get("governor") or ""),
        "created_at": str(run.get("created_at") or ""),
        "updated_at": str(run.get("updated_at") or ""),
        "decision": view["decision"],
        "display": view["display"],
        "next_action": view["next_action"],
        "client_action": view["client_action"],
    }


def run_orchestration_cards(runs: list[dict[str, Any]], active_task_ids: list[str] | set[str] | None = None) -> list[dict[str, Any]]:
    active_set = set(active_task_ids or [])
    return [run_orchestration_card(run, active=str(run.get("task_id") or "") in active_set) for run in runs]


def recovery_candidate_display(run: dict[str, Any], resume_ready: bool, resume_errors: list[str], pending_step_ids: list[Any]) -> dict[str, Any]:
    progress = run.get("progress") if isinstance(run.get("progress"), dict) else {}
    planned_steps = int(progress.get("planned_steps") or 0)
    completed_steps = int(progress.get("completed_steps") or 0)
    failed_steps = int(progress.get("failed_steps") or 0)
    pending_steps = len(pending_step_ids) if pending_step_ids else int(progress.get("pending_steps") or 0)
    if resume_ready:
        headline = "Recovery is ready"
        detail = f"{pending_steps} pending steps can resume"
        severity = "info"
    else:
        headline = "Recovery needs inspection"
        detail = resume_errors[0] if resume_errors else "Run package cannot be resumed automatically"
        severity = "warning"
    return {
        "headline": headline,
        "detail": detail,
        "severity": severity,
        "progress": {
            "planned_steps": planned_steps,
            "completed_steps": completed_steps,
            "failed_steps": failed_steps,
            "pending_steps": pending_steps,
        },
    }


def registry_display(kind: str, summary: dict[str, Any], include_health: bool = False) -> dict[str, Any]:
    total = int(summary.get("total") or 0)
    active = int(summary.get("active") or 0)
    planned = int(summary.get("planned") or 0)
    reachable = int(summary.get("reachable") or 0)
    if include_health:
        detail = f"{reachable}/{total} reachable, {active} active, {planned} planned"
        severity = "info" if reachable == total else "warning"
    else:
        detail = f"{active} active, {planned} planned"
        severity = "info"
    return {
        "headline": f"{kind} registry",
        "detail": detail,
        "severity": severity,
        "total": total,
        "active": active,
        "planned": planned,
        "reachable": reachable if include_health else None,
    }
