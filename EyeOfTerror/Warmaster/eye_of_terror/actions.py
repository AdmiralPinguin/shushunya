"""Action-hint builders: the control affordances (start/preflight/resume/
revision/diagnostics) clients render from task and run state."""
from __future__ import annotations

from typing import Any


def created_task_actions(task_id: str) -> dict[str, Any]:
    return {
        "can_preflight_run": True,
        "can_start_run": False,
        "next_action": {
            "kind": "preflight_run",
            "method": "POST",
            "endpoint": "POST /runs/{task_id}/preflight_http",
            "body": {},
            "reason": "run package was created and should be preflighted before execution",
        },
        "run_summary": {
            "method": "GET",
            "endpoint": "GET /runs/{task_id}/summary",
            "body": {},
        },
    }


def task_preflight_body(
    task_id: str,
    include_brigade_health: bool = False,
    governor_transport: str = "",
    governor_host: str = "",
    message: str = "<same message>",
) -> dict[str, Any]:
    body = {"message": message, "task_id": task_id} if task_id else {"message": message}
    if governor_transport:
        body["governor_transport"] = governor_transport
    if governor_host:
        body["governor_host"] = governor_host
    if include_brigade_health:
        body["include_brigade_health"] = True
    return body


def task_preflight_actions(
    ok: bool,
    error_code: str,
    task_id: str,
    include_brigade_health: bool = False,
    governor_transport: str = "",
    governor_host: str = "",
    message: str = "<same message>",
) -> dict[str, Any]:
    actions = {
        "can_create_task": ok,
        "can_check_brigade_readiness": True,
    }
    retry_body = task_preflight_body(task_id, include_brigade_health, governor_transport, governor_host, message)
    create_body = task_preflight_body(task_id, False, governor_transport, governor_host, message)
    if ok:
        next_action = {
            "kind": "prepare_orchestrated_task",
            "method": "POST",
            "endpoint": "POST /orchestrate",
            "body": create_body,
            "reason": "task preflight passed; prepare through Warmaster commander protocol",
        }
    elif error_code == "task_exists":
        next_action = {
            "kind": "inspect_existing_run",
            "method": "GET",
            "endpoint": "GET /runs/{task_id}/summary",
            "body": {},
            "reason": "task_id already exists",
        }
    elif error_code in {"contract_workers_missing", "contract_workers_unavailable", "governor_workers_missing", "governor_workers_unavailable"}:
        next_action = {
            "kind": "inspect_brigade",
            "method": "GET",
            "endpoint": "GET /brigade_health",
            "body": {},
            "reason": "required workers are missing or unavailable",
        }
    elif error_code in {"governor_service_unavailable", "governor_plan_failed", "governor_prepare_failed", "governor_prepare_invalid_run", "invalid_governor_task_id", "invalid_task_contract"}:
        next_action = {
            "kind": "inspect_governor",
            "method": "GET",
            "endpoint": "GET /governors?health=1",
            "body": {},
            "reason": error_code or "governor diagnostics are required",
        }
    elif error_code == "invalid_oversight":
        next_action = {
            "kind": "inspect_governor",
            "method": "GET",
            "endpoint": "GET /governors?health=1",
            "body": {},
            "reason": "governor oversight is invalid",
        }
    elif error_code in {"governor_inactive", "no_supported_governor"}:
        next_action = {
            "kind": "inspect_capabilities",
            "method": "GET",
            "endpoint": "GET /capabilities",
            "body": {},
            "reason": "no active governor can accept this task",
        }
    elif error_code == "multi_governor_decomposition_required":
        next_action = {
            "kind": "prepare_campaign",
            "method": "POST",
            "endpoint": "POST /campaign_preflight",
            "body": retry_body,
            "reason": "task matches multiple active governors and must be split into a campaign before execution",
        }
    elif include_brigade_health:
        next_action = {
            "kind": "inspect_preflight",
            "method": "POST",
            "endpoint": "POST /task_preflight",
            "body": retry_body,
            "reason": error_code or "task preflight failed",
        }
    else:
        next_action = {
            "kind": "inspect_preflight",
            "method": "POST",
            "endpoint": "POST /task_preflight",
            "body": retry_body,
            "reason": error_code or "task preflight failed",
        }
    actions["next_action"] = next_action
    return actions


def run_actions(
    status: str,
    revision_plan: dict[str, Any],
    revision_plan_errors: list[str] | None = None,
    package_errors: list[str] | None = None,
    oversight_errors: list[str] | None = None,
    research_loop_blocked: bool = False,
) -> dict[str, Any]:
    terminal_locked = status in {"completed", "running", "cancelling", "queued", "corrupt"}
    preflightable = status != "corrupt"
    revision_required = bool(revision_plan.get("required"))
    revision_valid = not (revision_plan_errors or [])
    package_valid = not (package_errors or [])
    oversight_valid = not (oversight_errors or [])
    resume_required = status == "interrupted"
    runnable = not terminal_locked and not revision_required and not resume_required and package_valid and oversight_valid
    revision_runnable = (
        revision_required
        and revision_valid
        and package_valid
        and oversight_valid
        and not research_loop_blocked
        and status not in {"running", "cancelling", "queued", "corrupt", "blocked"}
    )
    loop_runnable = runnable or revision_runnable or (resume_required and package_valid and oversight_valid)
    actions = {
        "can_preflight_local": preflightable,
        "can_preflight_http": preflightable,
        "can_execute": runnable,
        "can_start": runnable,
        "can_cancel": status in {"running", "cancelling", "queued"},
        "can_resume": status == "interrupted" and not revision_required and package_valid and oversight_valid,
        "can_execute_revision": revision_runnable,
        "can_start_revision": revision_runnable,
        "can_research_loop": loop_runnable and not research_loop_blocked,
        "can_start_research_loop": loop_runnable and not research_loop_blocked,
        "force_required_for_rerun": status == "completed" and not revision_required,
    }
    if status == "corrupt":
        next_action = {"kind": "inspect", "method": "GET", "endpoint": "GET /runs/{task_id}", "body": {}, "reason": "run state is corrupt"}
    elif status in {"running", "queued"}:
        next_action = {"kind": "poll", "method": "GET", "endpoint": "GET /runs/{task_id}/snapshot", "body": {}, "reason": "run is already active"}
    elif status == "cancelling":
        next_action = {"kind": "poll", "method": "GET", "endpoint": "GET /runs/{task_id}/snapshot", "body": {}, "reason": "cancellation is in progress"}
    elif not package_valid:
        next_action = {"kind": "inspect_package", "method": "GET", "endpoint": "GET /runs/{task_id}/package", "body": {}, "reason": "run package is incomplete or inconsistent"}
    elif not oversight_valid:
        next_action = {"kind": "inspect_oversight", "method": "GET", "endpoint": "GET /runs/{task_id}/oversight", "body": {}, "reason": "governor oversight is missing or inconsistent"}
    elif revision_required and not revision_valid:
        next_action = {"kind": "inspect_revision", "method": "GET", "endpoint": "GET /runs/{task_id}/summary", "body": {}, "reason": "revision_plan is invalid"}
    elif research_loop_blocked:
        next_action = {"kind": "inspect_blockers", "method": "GET", "endpoint": "GET /runs/{task_id}/summary", "body": {}, "reason": "research loop stopped on a stable blocker"}
    elif revision_runnable:
        next_action = {"kind": "execute_revision", "method": "POST", "endpoint": "POST /runs/{task_id}/start_revision_http", "body": {}, "reason": "revision_plan requires selected steps to rerun"}
    elif resume_required:
        next_action = {"kind": "resume", "method": "POST", "endpoint": "POST /runs/{task_id}/start_resume_http", "body": {}, "reason": "run is interrupted and has pending steps"}
    elif actions["force_required_for_rerun"]:
        next_action = {"kind": "rerun_requires_force", "method": "POST", "endpoint": "POST /runs/{task_id}/start_http", "body": {"force": True}, "reason": "run already completed"}
    elif runnable:
        next_action = {"kind": "start", "method": "POST", "endpoint": "POST /runs/{task_id}/start_http", "body": {}, "reason": "run is ready to execute"}
    else:
        next_action = {"kind": "inspect", "method": "GET", "endpoint": "GET /runs/{task_id}/summary", "body": {}, "reason": f"no automatic action for status {status or 'unknown'}"}
    actions["next_action"] = next_action
    return actions


def action_for_mode(action: dict[str, Any], mode: str) -> dict[str, Any]:
    result = dict(action)
    endpoint = str(result.get("endpoint") or "")
    if mode == "local":
        endpoint = endpoint.replace("_http", "_local")
    elif mode == "http":
        endpoint = endpoint.replace("_local", "_http")
    result["endpoint"] = endpoint
    return result


def run_preflight_actions(preflight: dict[str, Any], run_action_hints: dict[str, Any] | None = None) -> dict[str, Any]:
    mode = str(preflight.get("mode") or "http")
    ok = bool(preflight.get("ok"))
    step_ids = preflight.get("step_ids") if isinstance(preflight.get("step_ids"), list) else []
    run_action_hints = run_action_hints or {}
    summary_next_action = run_action_hints.get("next_action") if isinstance(run_action_hints.get("next_action"), dict) else {}
    body: dict[str, Any] = {}
    if step_ids:
        body["step_ids"] = step_ids
    actions = {
        "can_start_run": ok and bool(run_action_hints.get("can_start", True)),
        "can_inspect_package": True,
        "can_inspect_oversight": True,
        "can_check_brigade_readiness": True,
    }
    if ok and actions["can_start_run"]:
        next_action = {
            "kind": "start_run",
            "method": "POST",
            "endpoint": "POST /runs/{task_id}/start_http" if mode == "http" else "POST /runs/{task_id}/start_local",
            "body": body,
            "reason": "run preflight passed",
        }
    elif ok and summary_next_action:
        next_action = action_for_mode(summary_next_action, mode)
        if body and next_action.get("kind") in {"start", "start_run", "resume", "execute_revision"}:
            next_body = next_action.get("body") if isinstance(next_action.get("body"), dict) else {}
            next_action["body"] = {**next_body, **body}
    elif preflight.get("oversight_errors"):
        next_action = {
            "kind": "inspect_oversight",
            "method": "GET",
            "endpoint": "GET /runs/{task_id}/oversight",
            "body": {},
            "reason": "run oversight failed preflight",
        }
    elif preflight.get("dispatch_errors") or preflight.get("input_failures") or preflight.get("missing_local_commands"):
        next_action = {
            "kind": "inspect_package",
            "method": "GET",
            "endpoint": "GET /runs/{task_id}/package",
            "body": {},
            "reason": "run package failed preflight",
        }
    elif preflight.get("worker_preflight_failures"):
        next_action = {
            "kind": "inspect_brigade",
            "method": "GET",
            "endpoint": "GET /brigade_health",
            "body": {},
            "reason": "worker service preflight failed",
        }
    else:
        next_action = {
            "kind": "inspect_preflight",
            "method": "POST",
            "endpoint": "POST /runs/{task_id}/preflight_http" if mode == "http" else "POST /runs/{task_id}/preflight_local",
            "body": body,
            "reason": "run preflight failed",
        }
    actions["next_action"] = next_action
    return actions


def gateway_actions() -> dict[str, Any]:
    return {
        "can_preflight_task": True,
        "can_orchestrate_prepare": True,
        "can_orchestrate_start": True,
        "can_orchestrate_run": True,
        "can_preflight_runs": True,
        "can_create_task": True,
        "can_start_runs": True,
        "can_resume_interrupted_runs": True,
        "can_list_recoverable_runs": True,
        "can_list_orchestration_cards": True,
        "can_bulk_start_recoverable_runs": True,
        "can_poll_global_events": True,
        "can_execute_revisions": True,
        "can_run_research_loops": True,
        "can_execute_step_subsets": True,
        "can_cancel_runs": True,
        "can_check_brigade_readiness": True,
        "preferred_task_flow": ["POST /orchestrate_run", "GET /runs/{task_id}/orchestration?events_after=0"],
        "campaign_flow": ["POST /campaign_preflight", "POST /campaign", "POST /campaigns/{campaign_id}/start", "GET /campaigns/{campaign_id}"],
        "diagnostic_prepare_flow": ["POST /task_preflight", "POST /orchestrate", "POST /orchestrate_start", "GET /runs/{task_id}/orchestration?events_after=0"],
        "chat_task_flow": ["POST /orchestrate_run", "GET /runs/{task_id}/orchestration?events_after=0"],
        "research_loop_flow": ["POST /orchestrate", "POST /runs/{task_id}/start_research_loop_http", "GET /runs/{task_id}/orchestration?events_after=0"],
        "polling": ["GET /events?after=0", "GET /runs/{task_id}/snapshot?events_after=0", "GET /runs/{task_id}/activity"],
        "maintenance": ["GET /recovery", "POST /recovery/start_resume_local", "POST /recovery/start_resume_http", "POST /recover_stale"],
        "run_inspection": [
            "GET /runs/{task_id}/summary",
            "GET /runs/{task_id}/package",
            "GET /runs/{task_id}/oversight",
            "GET /runs/{task_id}/contract",
            "GET /runs/{task_id}/dispatch",
        ],
        "diagnostics": ["GET /state?health=1", "GET /brigade_health", "GET /doctor"],
    }
