"""Run lifecycle engine: execution preflight, orchestration, the research
loop, interrupted-run recovery, and background execution start. This is
the controller Warmaster runs above the governors; the HTTP gateway wires
these into endpoints."""
from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .actions import run_preflight_actions
from .gateway_util import resolve_run_child_path, validate_service_host
from .http_executor import execute_run as execute_http_run, preflight_workers as preflight_http_workers
from .ledger import TaskLedger
from .local_executor import WORKER_COMMANDS, execute_run as execute_local_run, input_artifact_errors, ordered_dispatch_paths
from .mission_control import link_run_to_mission, open_mission, record_warmaster_acceptance
from .run_package import load_json_file, load_json_object, load_ledger_dict, run_oversight, sandbox_artifact_file_status
from .run_state import list_runs, orchestration_state, run_progress, run_snapshot, run_summary
from .run_validation import revision_plan_summary, run_oversight_summary, validate_oversight_against_run, validate_revision_plan
from .runtime_state import ACTIVE_RUNS, ACTIVE_RUNS_LOCK, REPO_ROOT
from .task_prepare import prepare_task, preflight_task
from .views import executable_client_action, orchestration_view_fields, recovery_candidate_display


def run_execution_preflight(
    run_dir: Path,
    mode: str,
    workspace_root: Path | None = None,
    host: str = "127.0.0.1",
    timeout_sec: int = 10,
    step_ids: list[str] | None = None,
) -> dict[str, Any]:
    if mode not in {"local", "http"}:
        raise ValueError("mode must be local or http")
    host = validate_service_host(host)
    status = load_json_file(run_dir / "status.json")
    planned_steps = status.get("steps") if isinstance(status.get("steps"), list) else []
    selected = set(step_ids or [])
    order = [
        str(step.get("step_id") or "")
        for step in planned_steps
        if isinstance(step, dict) and step.get("step_id") and (not selected or str(step.get("step_id") or "") in selected)
    ]
    selected_order = {step_id: index for index, step_id in enumerate(order)}
    producer_by_artifact: dict[str, str] = {}
    for step in planned_steps:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("step_id") or "")
        expected = step.get("expected_artifacts") if isinstance(step.get("expected_artifacts"), list) else []
        for artifact in expected:
            if isinstance(artifact, str):
                producer_by_artifact[artifact] = step_id

    dispatch_errors: list[dict[str, Any]] = []
    input_failures: list[dict[str, Any]] = []
    step_checks: list[dict[str, Any]] = []
    missing_local_commands: list[dict[str, Any]] = []
    oversight_errors: list[str] = []
    oversight_payload = run_oversight(run_dir)
    if not oversight_payload.get("ok"):
        oversight_errors.append(str(oversight_payload.get("error") or "oversight unavailable"))
    else:
        oversight = oversight_payload.get("oversight") if isinstance(oversight_payload.get("oversight"), dict) else {}
        oversight_errors.extend(validate_oversight_against_run(run_dir, oversight, status))
    for dispatch_path in ordered_dispatch_paths(run_dir, step_ids=step_ids):
        try:
            packet = load_json_file(dispatch_path)
        except Exception as exc:  # noqa: BLE001 - preflight reports all unreadable dispatch packets.
            dispatch_errors.append({"dispatch": str(dispatch_path), "error": str(exc)})
            continue
        step_id = str(packet.get("step_id") or dispatch_path.stem)
        worker = str(packet.get("worker") or "")
        request = packet.get("request") if isinstance(packet.get("request"), dict) else packet
        input_artifacts = request.get("input_artifacts") if isinstance(request.get("input_artifacts"), list) else []
        input_status: list[dict[str, Any]] = []
        for artifact in input_artifacts:
            artifact_text = str(artifact)
            producer = producer_by_artifact.get(artifact_text, "")
            produced_by_selected = producer in selected_order and selected_order[producer] < selected_order.get(step_id, -1)
            status_item: dict[str, Any] = {
                "path": artifact_text,
                "producer_step_id": producer,
                "produced_by_selected_step": produced_by_selected,
            }
            if produced_by_selected:
                status_item["exists"] = None
                status_item["source"] = "selected_dependency"
            elif workspace_root is not None:
                errors = input_artifact_errors({"input_artifacts": [artifact]}, workspace_root)
                status_item.update(sandbox_artifact_file_status(str(workspace_root), artifact_text))
                if errors:
                    status_item["errors"] = errors
                    input_failures.append({"step_id": step_id, "worker": worker, "path": artifact_text, "errors": errors})
            else:
                status_item["exists"] = None
                status_item["source"] = "workspace_unknown"
            input_status.append(status_item)
        if mode == "local" and worker not in WORKER_COMMANDS:
            missing_local_commands.append({"step_id": step_id, "worker": worker, "error": "no local command registered"})
        step_checks.append(
            {
                "step_id": step_id,
                "worker": worker,
                "dispatch": str(dispatch_path),
                "input_artifacts": input_artifacts,
                "input_artifact_status": input_status,
            }
        )
    worker_failures = preflight_http_workers(run_dir, host, timeout_sec, step_ids=step_ids) if mode == "http" else []
    preflight = {
        "ok": not dispatch_errors and not input_failures and not missing_local_commands and not worker_failures and not oversight_errors,
        "task_id": run_dir.name,
        "mode": mode,
        "run_dir": str(run_dir),
        "host": host if mode == "http" else "",
        "workspace_root": str(workspace_root) if workspace_root is not None else "",
        "step_ids": order,
        "steps": step_checks,
        "dispatch_errors": dispatch_errors,
        "oversight_errors": oversight_errors,
        "oversight_summary": run_oversight_summary(run_dir) if not oversight_errors else {},
        "input_failures": input_failures,
        "missing_local_commands": missing_local_commands,
        "worker_preflight_failures": worker_failures,
    }
    summary = run_summary(run_dir)
    preflight["run_status"] = str(summary.get("status") or "")
    preflight["run_next_action"] = summary.get("actions", {}).get("next_action", {}) if isinstance(summary.get("actions"), dict) else {}
    preflight["actions"] = run_preflight_actions(
        preflight,
        summary.get("actions") if isinstance(summary.get("actions"), dict) else {},
    )
    return preflight


def planned_step_ids_from_run(run_dir: Path) -> list[str]:
    status = load_json_file(run_dir / "status.json")
    steps = status.get("steps") if isinstance(status.get("steps"), list) else []
    return [
        str(step.get("step_id") or "")
        for step in steps
        if isinstance(step, dict) and str(step.get("step_id") or "")
    ]


def validate_requested_step_ids(run_dir: Path, requested: list[str], allowed: list[str] | None = None) -> None:
    available = planned_step_ids_from_run(run_dir)
    unknown = [step_id for step_id in requested if step_id not in available]
    if unknown:
        raise ValueError(f"step_ids reference unknown run steps: {unknown}")
    if allowed is not None:
        blocked = [step_id for step_id in requested if step_id not in allowed]
        if blocked:
            raise ValueError(f"step_ids are not valid for this execution mode: {blocked}")


def record_run_preflight_event(run_dir: Path, preflight: dict[str, Any]) -> None:
    ledger_path = run_dir / "task_ledger.json"
    if not ledger_path.exists():
        return
    payload = {
        "mode": str(preflight.get("mode") or ""),
        "ok": bool(preflight.get("ok")),
        "step_ids": preflight.get("step_ids") if isinstance(preflight.get("step_ids"), list) else [],
        "dispatch_errors": len(preflight.get("dispatch_errors") if isinstance(preflight.get("dispatch_errors"), list) else []),
        "oversight_errors": len(preflight.get("oversight_errors") if isinstance(preflight.get("oversight_errors"), list) else []),
        "input_failures": len(preflight.get("input_failures") if isinstance(preflight.get("input_failures"), list) else []),
        "missing_local_commands": len(preflight.get("missing_local_commands") if isinstance(preflight.get("missing_local_commands"), list) else []),
        "worker_preflight_failures": len(preflight.get("worker_preflight_failures") if isinstance(preflight.get("worker_preflight_failures"), list) else []),
    }
    TaskLedger.load(ledger_path).record_event("run_preflight_recorded", payload)


def orchestrate_prepare_task(
    message: str,
    task_id: str | None,
    run_root: Path,
    governor_transport: str = "local",
    governor_host: str = "127.0.0.1",
    run_mode: str = "http",
    host: str = "127.0.0.1",
    timeout_sec: int = 30,
    include_brigade_health: bool = False,
    forced_governor: str | None = None,
    commander_order: dict[str, Any] | None = None,
    require_commander_order: bool = True,
) -> dict[str, Any]:
    if run_mode not in {"local", "http"}:
        raise ValueError("run_mode must be local or http")
    trace: list[dict[str, Any]] = []
    task_preflight = preflight_task(
        message,
        task_id,
        run_root,
        governor_transport=governor_transport,
        governor_host=governor_host,
        include_brigade_health=include_brigade_health,
        forced_governor=forced_governor,
        commander_order=commander_order,
        require_commander_order=require_commander_order,
    )
    task_preflight_actions = task_preflight.get("actions") if isinstance(task_preflight.get("actions"), dict) else {}
    trace.append({"stage": "task_preflight", "ok": bool(task_preflight.get("ok")), "next_action": task_preflight_actions.get("next_action", {})})
    if not task_preflight.get("ok"):
        next_action = task_preflight_actions.get("next_action", {}) if isinstance(task_preflight_actions.get("next_action"), dict) else {}
        return {
            "ok": False,
            "phase": "task_preflight",
            "trace": trace,
            "task_preflight": task_preflight,
            "actions": task_preflight_actions,
            "next_action": next_action,
            "client_action": executable_client_action(str(task_preflight.get("task_id") or task_id or ""), next_action),
        }
    task = prepare_task(
        message,
        task_id,
        run_root,
        governor_transport=governor_transport,
        governor_host=governor_host,
        forced_governor=forced_governor,
        commander_order=commander_order,
        require_commander_order=require_commander_order,
    )
    task_actions = task.get("actions") if isinstance(task.get("actions"), dict) else {}
    trace.append({"stage": "task", "ok": bool(task.get("ok")), "task_id": str(task.get("task_id") or ""), "next_action": task_actions.get("next_action", {})})
    if not task.get("ok"):
        next_action = task_actions.get("next_action", {}) if isinstance(task_actions.get("next_action"), dict) else {}
        return {
            "ok": False,
            "phase": "task",
            "trace": trace,
            "task_preflight": task_preflight,
            "task": task,
            "actions": task_actions,
            "next_action": next_action,
            "client_action": executable_client_action(str(task.get("task_id") or task_id or ""), next_action),
        }
    run_dir = Path(str(task.get("run_dir") or ""))
    if not run_dir.exists():
        next_action = {"kind": "inspect_existing_run", "method": "GET", "endpoint": "GET /runs/{task_id}/summary", "body": {}, "reason": "run directory is missing after task creation"}
        return {
            "ok": False,
            "phase": "task",
            "trace": trace,
            "task_preflight": task_preflight,
            "task": task,
            "error": "task did not create a run directory",
            "next_action": next_action,
            "client_action": executable_client_action(str(task.get("task_id") or task_id or ""), next_action),
        }
    preflight_workspace = resolve_run_child_path(run_dir, "", "work") if run_mode == "local" else None
    run_preflight = run_execution_preflight(
        run_dir,
        mode=run_mode,
        workspace_root=preflight_workspace,
        host=host,
        timeout_sec=timeout_sec,
    )
    record_run_preflight_event(run_dir, run_preflight)
    run_preflight_actions = run_preflight.get("actions") if isinstance(run_preflight.get("actions"), dict) else {}
    trace.append(
        {
            "stage": "run_preflight",
            "ok": bool(run_preflight.get("ok")),
            "task_id": str(run_preflight.get("task_id") or task.get("task_id") or ""),
            "next_action": run_preflight_actions.get("next_action", {}),
        }
    )
    next_action = run_preflight_actions.get("next_action", {}) if isinstance(run_preflight_actions.get("next_action"), dict) else {}
    prepared_task_id = str(task.get("task_id") or run_preflight.get("task_id") or "")
    return {
        "ok": bool(run_preflight.get("ok")),
        "phase": "ready_to_start" if run_preflight.get("ok") else "run_preflight",
        "task_id": prepared_task_id,
        "run_dir": str(run_dir),
        "run_mode": run_mode,
        "trace": trace,
        "task_preflight": task_preflight,
        "task": task,
        "run_preflight": run_preflight,
        "actions": run_preflight_actions,
        "next_action": next_action,
        "client_action": executable_client_action(prepared_task_id, next_action),
    }


def orchestrate_run_task(
    message: str,
    task_id: str | None,
    run_root: Path,
    governor_transport: str = "local",
    governor_host: str = "127.0.0.1",
    run_mode: str = "http",
    host: str = "127.0.0.1",
    timeout_sec: int = 1800,
    include_brigade_health: bool = False,
    auto_start: bool = True,
    force: bool = False,
    reuse_existing: bool = True,
) -> dict[str, Any]:
    prepare_timeout_sec = max(1, min(int(timeout_sec), 7200))
    warmaster_root = Path(__file__).resolve().parents[1]
    mission = open_mission(warmaster_root, message, task_id, source_channel="main_chat")
    if not mission.get("ok"):
        return {
            "ok": False,
            "phase": "commander_intake",
            "task_id": task_id or "",
            "mission_id": str(mission.get("mission_id") or ""),
            "mission": mission,
            "error": str(mission.get("error") or "Warmaster commander intake failed"),
            "error_code": str(mission.get("error_code") or "commander_intake_failed"),
            "next_action": {
                "kind": "inspect_commander_intake",
                "method": "GET",
                "endpoint": "GET /missions/{mission_id}",
                "body": {},
                "reason": "Warmaster could not form a commander order",
            },
        }
    command = mission.get("commander_order") if isinstance(mission.get("commander_order"), dict) else {}
    governor_message = str(mission.get("governor_task") or message)
    prepared = orchestrate_prepare_task(
        governor_message,
        task_id,
        run_root,
        governor_transport=governor_transport,
        governor_host=governor_host,
        run_mode=run_mode,
        host=host,
        timeout_sec=min(prepare_timeout_sec, 300),
        include_brigade_health=include_brigade_health,
        forced_governor=str(command.get("to") or "") or None,
        commander_order=command,
        require_commander_order=True,
    )
    trace = list(prepared.get("trace") if isinstance(prepared.get("trace"), list) else [])
    trace.insert(
        0,
        {
            "stage": "commander_intake",
            "ok": True,
            "mission_id": str(mission.get("mission_id") or ""),
            "assigned_governor": str((mission.get("commander_order") or {}).get("to") or ""),
        },
    )
    run_task_id = str(prepared.get("task_id") or task_id or "")
    if not prepared.get("ok"):
        task_preflight = prepared.get("task_preflight") if isinstance(prepared.get("task_preflight"), dict) else {}
        if reuse_existing and task_preflight.get("error_code") == "task_exists" and task_id:
            run_dir = run_root / task_id
            state = orchestration_state(run_dir, event_limit=5, events_after=0) if run_dir.exists() else {}
            decision = state.get("decision") if isinstance(state.get("decision"), dict) else {}
            should_start = auto_start and (
                bool(decision.get("can_start"))
                or bool(decision.get("can_resume"))
                or bool(decision.get("can_execute_revision"))
                or force
            )
            if should_start:
                started = orchestrate_start_run(
                    run_root,
                    task_id,
                    run_mode=run_mode,
                    host=host,
                    timeout_sec=prepare_timeout_sec,
                    force=force,
                )
                trace.append(
                    {
                        "stage": "orchestrate_start",
                        "ok": bool(started.get("ok")),
                        "task_id": task_id,
                        "next_action": started.get("next_action") if isinstance(started.get("next_action"), dict) else {},
                    }
                )
                state = orchestration_state(run_dir, event_limit=5, events_after=0) if run_dir.exists() else {}
                return {
                    "ok": bool(started.get("ok")),
                    "phase": "started" if started.get("ok") else "existing_run",
                    "task_id": task_id,
                    "run_mode": run_mode,
                    "reused_existing": True,
                    "trace": trace,
                    "prepare": prepared,
                    "start": started,
                    "orchestration": state,
                    "decision": state.get("decision", {}) if isinstance(state, dict) else {},
                    "display": state.get("display", {}) if isinstance(state, dict) else {},
                    "display_events": state.get("display_events", []) if isinstance(state, dict) else [],
                    "next_action": started.get("next_action") if isinstance(started.get("next_action"), dict) else state.get("next_action", {}),
                    "client_action": state.get("client_action", {}) if isinstance(state, dict) else {},
                }
            return {
                "ok": True,
                "phase": "existing_run",
                "task_id": task_id,
                "run_mode": run_mode,
                "reused_existing": True,
                "trace": trace,
                "prepare": prepared,
                "orchestration": state,
                "decision": state.get("decision", {}) if isinstance(state, dict) else {},
                "display": state.get("display", {}) if isinstance(state, dict) else {},
                "display_events": state.get("display_events", []) if isinstance(state, dict) else [],
                "next_action": state.get("next_action", {}) if isinstance(state, dict) else {},
                "client_action": state.get("client_action", {}) if isinstance(state, dict) else {},
            }
        return {
            "ok": False,
            "phase": str(prepared.get("phase") or "prepare_failed"),
            "task_id": run_task_id,
            "trace": trace,
            "prepare": prepared,
            "next_action": prepared.get("next_action") if isinstance(prepared.get("next_action"), dict) else {},
            "client_action": prepared.get("client_action") if isinstance(prepared.get("client_action"), dict) else {},
        }
    if not auto_start:
        if run_task_id:
            link_run_to_mission(run_root / run_task_id, mission)
        state = orchestration_state(run_root / run_task_id, event_limit=5, events_after=0)
        return {
            "ok": True,
            "phase": "ready_to_start",
            "task_id": run_task_id,
            "run_dir": str(run_root / run_task_id),
            "mission_id": str(mission.get("mission_id") or ""),
            "mission": {
                "mission_id": str(mission.get("mission_id") or ""),
                "assigned_governor": str((mission.get("commander_order") or {}).get("to") or ""),
                "mission_dir": str(mission.get("mission_dir") or ""),
            },
            "trace": trace,
            "prepare": prepared,
            "next_action": prepared.get("next_action") if isinstance(prepared.get("next_action"), dict) else {},
            "orchestration": state,
            "decision": state.get("decision", {}),
            "display": state.get("display", {}),
            "display_events": state.get("display_events", []),
            "client_action": prepared.get("client_action") if isinstance(prepared.get("client_action"), dict) else state.get("client_action", {}),
        }
    started = orchestrate_start_run(
        run_root,
        run_task_id,
        run_mode=run_mode,
        host=host,
        timeout_sec=prepare_timeout_sec,
        force=force,
    )
    if run_task_id:
        link_run_to_mission(run_root / run_task_id, mission)
    trace.append(
        {
            "stage": "orchestrate_start",
            "ok": bool(started.get("ok")),
            "task_id": run_task_id,
            "next_action": started.get("next_action") if isinstance(started.get("next_action"), dict) else {},
        }
    )
    run_dir = run_root / run_task_id
    state = orchestration_state(run_dir, event_limit=5, events_after=0) if run_dir.exists() else {}
    return {
        "ok": bool(started.get("ok")),
        "phase": "started" if started.get("ok") else "start_failed",
        "task_id": run_task_id,
        "mission_id": str(mission.get("mission_id") or ""),
        "mission": {
            "mission_id": str(mission.get("mission_id") or ""),
            "assigned_governor": str((mission.get("commander_order") or {}).get("to") or ""),
            "mission_dir": str(mission.get("mission_dir") or ""),
        },
        "run_mode": run_mode,
        "trace": trace,
        "prepare": prepared,
        "start": started,
        "orchestration": state,
        "decision": state.get("decision", {}) if isinstance(state, dict) else {},
        "display": state.get("display", {}) if isinstance(state, dict) else {},
        "display_events": state.get("display_events", []) if isinstance(state, dict) else [],
        "next_action": started.get("next_action") if isinstance(started.get("next_action"), dict) else {},
        "client_action": state.get("client_action", {}) if isinstance(state, dict) else {},
    }


def revision_step_ids_from_run(run_dir: Path) -> list[str]:
    ledger_path = run_dir / "task_ledger.json"
    ledger, ledger_error = load_ledger_dict(ledger_path)
    if ledger_error:
        raise ValueError(f"ledger unavailable for revision execution: {ledger_error}")
    result = ledger.get("result", {}) if isinstance(ledger.get("result"), dict) else {}
    revision_plan = result.get("revision_plan") if isinstance(result.get("revision_plan"), dict) else {}
    if not revision_plan.get("required"):
        raise ValueError("run does not have a required revision_plan")
    revision_errors = validate_revision_plan(run_dir, revision_plan)
    if revision_errors:
        raise ValueError(f"revision_plan is invalid: {revision_errors}")
    raw_steps = revision_plan.get("steps", [])
    if not isinstance(raw_steps, list):
        raise ValueError("revision_plan.steps must be a list")
    requested: list[str] = []
    for item in raw_steps:
        if isinstance(item, dict):
            step_id = str(item.get("step_id") or "").strip()
            if step_id and step_id not in requested:
                requested.append(step_id)
    oversight_payload = run_oversight(run_dir)
    oversight = oversight_payload.get("oversight") if isinstance(oversight_payload.get("oversight"), dict) else {}
    revision_policy = oversight.get("revision_policy") if isinstance(oversight.get("revision_policy"), dict) else {}
    policy_final_steps = revision_policy.get("final_steps") if isinstance(revision_policy.get("final_steps"), list) else []
    final_steps = [str(step_id) for step_id in policy_final_steps if isinstance(step_id, str) and step_id] or ["critic_review", "finalize"]
    for final_step in final_steps:
        if final_step not in requested:
            requested.append(final_step)
    available = {
        path.stem
        for path in ordered_dispatch_paths(run_dir)
    }
    missing = [step_id for step_id in requested if step_id not in available]
    if missing:
        raise ValueError(f"revision_plan references unknown dispatch steps: {missing}")
    return requested


def revision_plan_fingerprint(summary: dict[str, Any]) -> str:
    revision_summary = summary.get("revision_plan_summary") if isinstance(summary.get("revision_plan_summary"), dict) else {}
    revision_plan = summary.get("revision_plan") if isinstance(summary.get("revision_plan"), dict) else {}
    payload = {
        "status": str(summary.get("status") or ""),
        "required": bool(revision_summary.get("required") or revision_plan.get("required")),
        "valid": bool(revision_summary.get("valid")),
        "step_ids": revision_summary.get("step_ids") if isinstance(revision_summary.get("step_ids"), list) else [],
        "workers": revision_summary.get("workers") if isinstance(revision_summary.get("workers"), list) else [],
        "errors": revision_summary.get("errors") if isinstance(revision_summary.get("errors"), list) else [],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def record_research_loop_event(run_dir: Path, event_type: str, payload: dict[str, Any]) -> None:
    ledger_path = run_dir / "task_ledger.json"
    if not ledger_path.exists():
        return
    try:
        TaskLedger.load(ledger_path).record_event(event_type, payload)
    except Exception:
        pass


def execute_run_cycle(
    run_dir: Path,
    run_mode: str,
    host: str,
    timeout_sec: int,
    operation: str,
) -> dict[str, Any]:
    workspace_root = resolve_run_child_path(run_dir, "", "work")
    step_ids: list[str] | None = None
    execution_mode = "full"
    if operation == "revision":
        step_ids = revision_step_ids_from_run(run_dir)
        execution_mode = "revision"
    elif operation == "resume":
        step_ids = resume_step_ids_from_run(run_dir)
        execution_mode = "resume"
    if run_mode == "local":
        return execute_local_run(
            REPO_ROOT,
            run_dir,
            workspace_root,
            timeout_sec=timeout_sec,
            step_ids=step_ids,
            execution_mode=execution_mode,
        )
    return execute_http_run(
        run_dir,
        host=host,
        timeout_sec=timeout_sec,
        workspace_root=None,
        step_ids=step_ids,
        execution_mode=execution_mode,
    )


def research_loop_run(
    run_root: Path,
    task_id: str,
    run_mode: str = "local",
    host: str = "127.0.0.1",
    timeout_sec: int = 1800,
    max_revision_cycles: int = 3,
    allow_resume: bool = True,
    claim_active: bool = True,
) -> dict[str, Any]:
    if run_mode not in {"local", "http"}:
        raise ValueError("run_mode must be local or http")
    host = validate_service_host(host)
    timeout_sec = max(1, min(int(timeout_sec), 7200))
    max_revision_cycles = max(0, min(int(max_revision_cycles), 8))
    run_dir = run_root / task_id
    if not run_dir.exists():
        return {"ok": False, "phase": "missing_run", "task_id": task_id, "error": "run not found"}
    if claim_active:
        with ACTIVE_RUNS_LOCK:
            if task_id in ACTIVE_RUNS:
                return {
                    "ok": False,
                    "phase": "already_active",
                    "task_id": task_id,
                    "error": "run already active",
                    "snapshot": run_snapshot(run_dir, event_limit=5, events_after=0),
                }
            ACTIVE_RUNS.add(task_id)
    cycles: list[dict[str, Any]] = []
    seen_revision_fingerprints: set[str] = set()
    revision_cycles = 0
    stop_reason = "unknown"
    try:
        record_research_loop_event(
            run_dir,
            "research_loop_started",
            {
                "mode": f"research_loop_{run_mode}",
                "max_revision_cycles": max_revision_cycles,
                "allow_resume": allow_resume,
            },
        )
        while True:
            summary = run_summary(run_dir)
            actions = summary.get("actions") if isinstance(summary.get("actions"), dict) else {}
            status = str(summary.get("status") or "")
            progress = summary.get("progress") if isinstance(summary.get("progress"), dict) else {}
            pending_step_ids = [str(step_id) for step_id in progress.get("pending_step_ids", []) if step_id]
            cycle: dict[str, Any] = {
                "index": len(cycles),
                "status": status,
                "next_action": actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {},
            }
            if actions.get("can_start") or status in {"created", "assigned"}:
                operation = "full"
            elif allow_resume and actions.get("can_resume") and pending_step_ids:
                operation = "resume"
            elif allow_resume and actions.get("can_resume") and not pending_step_ids:
                planned_steps = int(progress.get("planned_steps") or 0)
                completed_steps = int(progress.get("completed_steps") or 0)
                failed_steps = int(progress.get("failed_steps") or 0)
                if planned_steps > 0 and completed_steps >= planned_steps and failed_steps == 0:
                    TaskLedger.load(run_dir / "task_ledger.json").set_status("completed")
                    record_research_loop_event(
                        run_dir,
                        "research_loop_completed_empty_resume",
                        {"planned_steps": planned_steps, "completed_steps": completed_steps},
                    )
                    cycle["stop_reason"] = "normalized_completed"
                    cycle["resume_skipped"] = "no_pending_steps"
                    cycles.append(cycle)
                    continue
                stop_reason = "needs_attention"
                cycle["stop_reason"] = stop_reason
                cycle["resume_skipped"] = "no_pending_steps"
                cycles.append(cycle)
                break
            elif actions.get("can_execute_revision"):
                if revision_cycles >= max_revision_cycles:
                    stop_reason = "revision_cycle_limit"
                    cycle["stop_reason"] = stop_reason
                    cycles.append(cycle)
                    break
                fingerprint = revision_plan_fingerprint(summary)
                if fingerprint in seen_revision_fingerprints:
                    stop_reason = "repeated_revision_plan"
                    cycle["stop_reason"] = stop_reason
                    cycles.append(cycle)
                    break
                seen_revision_fingerprints.add(fingerprint)
                revision_cycles += 1
                operation = "revision"
                cycle["revision_cycle"] = revision_cycles
                cycle["revision_fingerprint"] = fingerprint
            elif status == "completed":
                acceptance = record_warmaster_acceptance(run_dir)
                cycle["warmaster_acceptance"] = {
                    "ok": bool(acceptance.get("ok")),
                    "accepted": bool(acceptance.get("accepted")),
                    "blocked": bool(acceptance.get("blocked")),
                    "revision_required": bool(acceptance.get("revision_required")),
                    "skipped": bool(acceptance.get("skipped")),
                    "already_recorded": bool(acceptance.get("already_recorded")),
                }
                if acceptance.get("revision_required"):
                    cycle["stop_reason"] = "warmaster_revision_ordered"
                    cycles.append(cycle)
                    continue
                if acceptance.get("blocked"):
                    stop_reason = "warmaster_acceptance_blocked"
                    cycle["stop_reason"] = stop_reason
                    cycles.append(cycle)
                    break
                if acceptance.get("accepted") or acceptance.get("skipped"):
                    stop_reason = "completed"
                    cycle["stop_reason"] = stop_reason
                    cycles.append(cycle)
                    break
                stop_reason = "warmaster_acceptance_failed"
                cycle["stop_reason"] = stop_reason
                cycles.append(cycle)
                break
            elif bool(summary.get("revision_plan_summary", {}).get("required")) and not actions.get("can_execute_revision"):
                stop_reason = "invalid_revision"
                cycle["stop_reason"] = stop_reason
                cycles.append(cycle)
                break
            else:
                stop_reason = "needs_attention"
                cycle["stop_reason"] = stop_reason
                cycles.append(cycle)
                break
            cycle["operation"] = operation
            record_research_loop_event(
                run_dir,
                "research_loop_cycle_started",
                {"cycle": len(cycles), "operation": operation, "revision_cycle": revision_cycles},
            )
            execution = execute_run_cycle(run_dir, run_mode, host, timeout_sec, operation)
            cycle["execution_ok"] = bool(execution.get("ok"))
            cycle["execution_status"] = str(execution.get("status") or "")
            cycle["execution_mode"] = str(execution.get("mode") or operation)
            if isinstance(execution.get("step_ids"), list):
                cycle["step_ids"] = execution.get("step_ids")
            cycles.append(cycle)
            record_research_loop_event(
                run_dir,
                "research_loop_cycle_finished",
                {
                    "cycle": cycle["index"],
                    "operation": operation,
                    "ok": bool(execution.get("ok")),
                    "status": str(execution.get("status") or ""),
                    "step_ids": cycle.get("step_ids", []),
                },
            )
            if not execution.get("ok"):
                post_execution_summary = run_summary(run_dir)
                post_revision_summary = post_execution_summary.get("revision_plan_summary") if isinstance(post_execution_summary.get("revision_plan_summary"), dict) else {}
                worker_steps = execution.get("steps") if isinstance(execution.get("steps"), list) else []
                worker_steps_ok = bool(worker_steps) and all(isinstance(item, dict) and item.get("ok") for item in worker_steps)
                if worker_steps_ok and post_revision_summary.get("required") and post_revision_summary.get("valid"):
                    cycle["managed_blocker"] = True
                    cycle["revision_step_ids"] = post_revision_summary.get("step_ids", [])
                    continue
                stop_reason = "execution_failed"
                break
        stable_blocker_reasons = {"repeated_revision_plan", "revision_cycle_limit"}
        if stop_reason in stable_blocker_reasons:
            try:
                ledger = TaskLedger.load(run_dir / "task_ledger.json")
                existing_result = ledger.data.get("result") if isinstance(ledger.data.get("result"), dict) else {}
                blocked_result = dict(existing_result)
                blocked_result.update(
                    {
                        "ok": False,
                        "status": "blocked",
                        "summary": f"Research loop stopped on stable blocker: {stop_reason}.",
                        "research_loop_blocked": True,
                        "research_loop_stop_reason": stop_reason,
                        "research_loop_revision_cycles": revision_cycles,
                    }
                )
                ledger.set_result(blocked_result)
                ledger.set_status("blocked")
            except Exception:
                pass
        final_summary = run_summary(run_dir)
        final_view = orchestration_view_fields(final_summary, task_id=task_id)
        ok = stop_reason == "completed" or (
            str(final_summary.get("status") or "") == "completed"
            and not bool(final_summary.get("revision_plan_summary", {}).get("required"))
        )
        record_research_loop_event(
            run_dir,
            "research_loop_finished",
            {
                "ok": ok,
                "stop_reason": stop_reason,
                "cycles": len(cycles),
                "revision_cycles": revision_cycles,
                "final_status": str(final_summary.get("status") or ""),
            },
        )
        return {
            "ok": ok,
            "phase": "completed" if ok else stop_reason,
            "task_id": task_id,
            "run_mode": run_mode,
            "stop_reason": stop_reason,
            "cycles": cycles,
            "revision_cycles": revision_cycles,
            "max_revision_cycles": max_revision_cycles,
            "run_summary": final_summary,
            "decision": final_view.get("decision", {}),
            "display": final_view.get("display", {}),
            "next_action": final_view.get("next_action", {}),
            "client_action": final_view.get("client_action", {}),
        }
    finally:
        if claim_active:
            with ACTIVE_RUNS_LOCK:
                ACTIVE_RUNS.discard(task_id)


def resume_step_ids_from_run(run_dir: Path) -> list[str]:
    status, status_error = load_json_object(run_dir / "status.json", "status")
    if status_error:
        raise ValueError(f"status unavailable for resume execution: {status_error}")
    ledger, ledger_error = load_ledger_dict(run_dir / "task_ledger.json")
    if ledger_error:
        raise ValueError(f"ledger unavailable for resume execution: {ledger_error}")
    progress = run_progress(status, ledger)
    pending = [str(step_id) for step_id in progress.get("pending_step_ids", []) if step_id]
    if not pending:
        raise ValueError("run has no pending steps to resume")
    return pending


def recover_stale_runs(run_root: Path) -> list[dict[str, Any]]:
    recovered: list[dict[str, Any]] = []
    if not run_root.exists():
        return recovered
    with ACTIVE_RUNS_LOCK:
        active = set(ACTIVE_RUNS)
    for run_dir in run_root.iterdir():
        if not run_dir.is_dir() or run_dir.name.startswith("_") or run_dir.name in active:
            continue
        ledger_path = run_dir / "task_ledger.json"
        if not ledger_path.exists():
            continue
        try:
            ledger = TaskLedger.load(ledger_path)
        except Exception:  # noqa: BLE001 - corrupt runs are reported by run_summary and must not block recovery.
            recovered.append(run_summary(run_dir))
            continue
        if ledger.data.get("status") in {"running", "cancelling"}:
            ledger.set_status("interrupted")
            ledger.record_event("recovered_stale_run", {"reason": "gateway process has no active worker thread"})
            recovered.append(run_summary(run_dir))
    return recovered


def prepare_run_root(run_root: Path, recover_stale_on_start: bool = True) -> list[dict[str, Any]]:
    run_root.mkdir(parents=True, exist_ok=True)
    if not recover_stale_on_start:
        return []
    return recover_stale_runs(run_root)


def recovery_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for run in runs:
        actions = run.get("actions") if isinstance(run.get("actions"), dict) else {}
        if run.get("status") != "interrupted" or not actions.get("can_resume"):
            continue
        next_action = actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {}
        resume_ready = False
        resume_errors: list[str] = []
        pending_step_ids = run.get("progress", {}).get("pending_step_ids", []) if isinstance(run.get("progress"), dict) else []
        try:
            pending_step_ids = resume_step_ids_from_run(Path(str(run.get("run_dir") or "")))
            resume_ready = True
        except Exception as exc:  # noqa: BLE001 - recovery listing should diagnose malformed run packages.
            resume_errors.append(str(exc))
        task_id = str(run.get("task_id") or "")
        candidates.append(
            {
                "task_id": task_id,
                "status": str(run.get("status") or ""),
                "updated_at": str(run.get("updated_at") or ""),
                "pending_step_ids": pending_step_ids,
                "resume_ready": resume_ready,
                "resume_errors": resume_errors,
                "next_action": next_action,
                "client_action": executable_client_action(task_id, next_action),
                "display": recovery_candidate_display(run, resume_ready, resume_errors, pending_step_ids),
            }
        )
    startable = [candidate for candidate in candidates if candidate.get("resume_ready")]
    blocked = [candidate for candidate in candidates if not candidate.get("resume_ready")]
    return {
        "recoverable": len(candidates),
        "startable": len(startable),
        "blocked": len(blocked),
        "task_ids": [candidate["task_id"] for candidate in candidates if candidate.get("task_id")],
        "candidates": candidates,
    }


def start_background(task_id: str, target: Any) -> bool:
    with ACTIVE_RUNS_LOCK:
        if task_id in ACTIVE_RUNS:
            return False
        ACTIVE_RUNS.add(task_id)

    def wrapped() -> None:
        try:
            target()
        finally:
            with ACTIVE_RUNS_LOCK:
                ACTIVE_RUNS.discard(task_id)

    threading.Thread(target=wrapped, daemon=True).start()
    return True


def execute_with_ledger_failure_guard(run_dir: Path, target: Any) -> Any:
    try:
        return target()
    except Exception as exc:  # noqa: BLE001 - background failures must not leave runs stuck as running.
        ledger_path = run_dir / "task_ledger.json"
        if ledger_path.exists():
            try:
                ledger = TaskLedger.load(ledger_path)
                ledger.record_event("background_execution_failed", {"error": str(exc), "type": type(exc).__name__})
                ledger.set_result({"ok": False, "status": "failed", "summary": str(exc), "error": str(exc)})
                ledger.set_status("failed")
            except Exception:
                pass
        raise


def orchestrate_start_run(
    run_root: Path,
    task_id: str,
    run_mode: str = "http",
    host: str = "127.0.0.1",
    timeout_sec: int = 1800,
    force: bool = False,
) -> dict[str, Any]:
    if run_mode not in {"local", "http"}:
        raise ValueError("run_mode must be local or http")
    host = validate_service_host(host)
    timeout_sec = max(1, min(int(timeout_sec), 7200))
    run_dir = run_root / task_id
    if not run_dir.exists():
        return {"ok": False, "phase": "missing_run", "task_id": task_id, "error": "run not found"}
    summary = run_summary(run_dir)
    actions = summary.get("actions") if isinstance(summary.get("actions"), dict) else {}
    next_action = actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {}
    execution_mode = "full"
    step_ids: list[str] | None = None
    if actions.get("can_start"):
        operation = "start"
    elif actions.get("can_resume"):
        operation = "resume"
        execution_mode = "resume"
        step_ids = resume_step_ids_from_run(run_dir)
    elif actions.get("can_start_revision"):
        operation = "revision"
        execution_mode = "revision"
        step_ids = revision_step_ids_from_run(run_dir)
    elif force and actions.get("force_required_for_rerun"):
        operation = "force_rerun"
    else:
        return {
            "ok": False,
            "phase": "not_startable",
            "task_id": task_id,
            "summary": summary,
            "next_action": next_action,
            "client_action": executable_client_action(task_id, next_action),
            "snapshot": run_snapshot(run_dir, event_limit=5, events_after=0),
        }

    workspace_root = resolve_run_child_path(run_dir, "", "work")
    if run_mode == "local":
        executor = lambda: execute_with_ledger_failure_guard(
            run_dir,
            lambda: execute_local_run(
                REPO_ROOT,
                run_dir,
                workspace_root,
                timeout_sec=timeout_sec,
                step_ids=step_ids,
                execution_mode=execution_mode,
            ),
        )
    else:
        executor = lambda: execute_with_ledger_failure_guard(
            run_dir,
            lambda: execute_http_run(
                run_dir,
                host=host,
                timeout_sec=timeout_sec,
                workspace_root=None,
                step_ids=step_ids,
                execution_mode=execution_mode,
            ),
        )
    ledger_path = run_dir / "task_ledger.json"
    if ledger_path.exists():
        ledger = TaskLedger.load(ledger_path)
        if operation == "resume":
            ledger.record_event("resume_execution_requested", {"mode": f"orchestrate_start_{run_mode}", "step_ids": step_ids or []})
        event_payload: dict[str, Any] = {"mode": f"orchestrate_start_{run_mode}", "operation": operation}
        if step_ids:
            event_payload["step_ids"] = step_ids
        if force:
            event_payload["force"] = True
        ledger.record_event("background_start_requested", event_payload)
    if not start_background(task_id, executor):
        return {
            "ok": False,
            "phase": "already_active",
            "task_id": task_id,
            "error": "run already active",
            "snapshot": run_snapshot(run_dir, event_limit=5, events_after=0),
        }
    poll_action = {"kind": "poll", "method": "GET", "endpoint": "GET /runs/{task_id}/snapshot", "body": {"events_after": 0}, "reason": "run started in background"}
    return {
        "ok": True,
        "phase": "started",
        "task_id": task_id,
        "run_mode": run_mode,
        "operation": operation,
        "step_ids": step_ids or [],
        "next_action": poll_action,
        "client_action": executable_client_action(task_id, poll_action),
        "snapshot": run_snapshot(run_dir, event_limit=5, events_after=0),
    }


def start_recoverable_runs(run_root: Path, mode: str, host: str = "127.0.0.1", timeout_sec: int = 1800) -> dict[str, Any]:
    if mode not in {"local", "http"}:
        raise ValueError("mode must be local or http")
    host = validate_service_host(host)
    timeout_sec = max(1, min(int(timeout_sec), 7200))
    all_runs = list_runs(run_root)
    candidates = recovery_summary(all_runs).get("candidates", [])
    results: list[dict[str, Any]] = []
    started_count = 0
    poll_action = {"kind": "poll", "method": "GET", "endpoint": "GET /runs/{task_id}/snapshot", "body": {"events_after": 0}, "reason": "run started in background"}
    inspect_action = {"kind": "inspect_package", "method": "GET", "endpoint": "GET /runs/{task_id}/package", "body": {}, "reason": "run could not be resumed automatically"}
    for candidate in candidates:
        task_id = str(candidate.get("task_id") or "")
        if not task_id:
            continue
        run_dir = run_root / task_id
        ledger_path = run_dir / "task_ledger.json"
        try:
            step_ids = resume_step_ids_from_run(run_dir)
            if ledger_path.exists():
                ledger = TaskLedger.load(ledger_path)
                ledger.record_event("resume_execution_requested", {"mode": f"bulk_start_resume_{mode}"})
                ledger.record_event("background_start_requested", {"mode": f"bulk_start_resume_{mode}", "step_ids": step_ids})
            workspace_root = resolve_run_child_path(run_dir, "", "work")
            if mode == "local":
                executor = lambda run_dir=run_dir, workspace_root=workspace_root, step_ids=step_ids: execute_with_ledger_failure_guard(
                    run_dir,
                    lambda: execute_local_run(
                        REPO_ROOT,
                        run_dir,
                        workspace_root,
                        timeout_sec=timeout_sec,
                        step_ids=step_ids,
                        execution_mode="resume",
                    ),
                )
            else:
                executor = lambda run_dir=run_dir, step_ids=step_ids: execute_with_ledger_failure_guard(
                    run_dir,
                    lambda: execute_http_run(
                        run_dir,
                        host=host,
                        timeout_sec=timeout_sec,
                        workspace_root=None,
                        step_ids=step_ids,
                        execution_mode="resume",
                    ),
                )
            if not start_background(task_id, executor):
                already_active_action = {"kind": "poll", "method": "GET", "endpoint": "GET /runs/{task_id}/snapshot", "body": {"events_after": 0}, "reason": "run is already active"}
                results.append(
                    {
                        "task_id": task_id,
                        "ok": False,
                        "status": "already_active",
                        "next_action": already_active_action,
                        "client_action": executable_client_action(task_id, already_active_action),
                    }
                )
                continue
            started_count += 1
            results.append(
                {
                    "task_id": task_id,
                    "ok": True,
                    "status": "started",
                    "step_ids": step_ids,
                    "next_action": poll_action,
                    "client_action": executable_client_action(task_id, poll_action),
                }
            )
        except Exception as exc:  # noqa: BLE001 - one malformed recoverable run must not block the queue.
            results.append(
                {
                    "task_id": task_id,
                    "ok": False,
                    "status": "skipped",
                    "error": str(exc),
                    "next_action": inspect_action,
                    "client_action": executable_client_action(task_id, inspect_action),
                }
            )
    return {
        "ok": True,
        "mode": mode,
        "started": started_count,
        "total_candidates": len(candidates),
        "results": results,
    }


def cancel_http_worker_tasks(run_dir: Path, host: str = "127.0.0.1", timeout_sec: float = 1.0) -> list[dict[str, Any]]:
    host = validate_service_host(host)
    dispatch_dir = run_dir / "dispatch"
    if not dispatch_dir.exists():
        return []
    results: list[dict[str, Any]] = []
    for dispatch_path in sorted(dispatch_dir.glob("*.json")):
        try:
            packet = json.loads(dispatch_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            results.append({"dispatch": str(dispatch_path), "ok": False, "error": str(exc)})
            continue
        if not isinstance(packet, dict):
            results.append({"dispatch": str(dispatch_path), "ok": False, "error": "dispatch packet is not an object"})
            continue
        request_payload = packet.get("request") if isinstance(packet.get("request"), dict) else {}
        task_id = str(request_payload.get("task_id") or packet.get("task_id") or "")
        worker = str(packet.get("worker") or "")
        port = int(packet.get("port") or 0)
        if not task_id or not port:
            results.append({"worker": worker, "port": port, "task_id": task_id, "ok": False, "error": "missing task_id or port"})
            continue
        url = f"http://{host}:{port}/tasks/{quote(task_id, safe='')}/cancel"
        try:
            data = json.dumps({}).encode("utf-8")
            request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                payload = json.loads(response.read().decode("utf-8"))
            results.append({"worker": worker, "port": port, "task_id": task_id, "ok": bool(isinstance(payload, dict) and payload.get("ok")), "response": payload})
        except Exception as exc:  # noqa: BLE001 - cancellation fan-out is best-effort.
            results.append({"worker": worker, "port": port, "task_id": task_id, "ok": False, "error": str(exc)})
    return results
