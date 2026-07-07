"""Run state view-model: progress, summaries, events, snapshots, and
per-step/worker-task inspection built from a run package."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .actions import run_actions
from .artifacts import artifact_status, final_manifest_summary, final_package
from .gateway_util import validate_service_host
from .run_package import load_ledger_dict, load_json_object, run_dispatch_packets, sandbox_artifact_file_status
from .run_validation import (
    revision_plan_summary,
    run_oversight_summary,
    run_oversight_validation_errors,
    run_package_action_errors,
    validate_revision_plan,
)
from .runtime_state import ACTIVE_RUNS, ACTIVE_RUNS_LOCK
from .views import display_events_for, event_display, executable_client_action, orchestration_view_fields


def run_progress(status: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    planned_steps = status.get("steps", [])
    ledger_steps = ledger.get("steps", [])
    result = ledger.get("result", {}) if isinstance(ledger.get("result"), dict) else {}
    workspace_root = str(result.get("workspace_root") or "")
    if not isinstance(planned_steps, list):
        planned_steps = []
    if not isinstance(ledger_steps, list):
        ledger_steps = []
    by_status: dict[str, int] = {}
    ledger_by_step: dict[str, dict[str, Any]] = {}
    for step in ledger_steps:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("step_id") or "")
        if step_id:
            ledger_by_step[step_id] = step
        step_status = str(step.get("status") or "unknown")
        by_status[step_status] = by_status.get(step_status, 0) + 1
    completed = by_status.get("completed", 0) + by_status.get("ready", 0)
    failed = by_status.get("failed", 0)
    planned_step_ids = [
        str(step.get("step_id") or "")
        for step in planned_steps
        if isinstance(step, dict) and step.get("step_id")
    ]
    completed_statuses = {"completed", "ready", "passed_with_warnings"}
    completed_step_ids = [
        step_id
        for step_id in planned_step_ids
        if str(ledger_by_step.get(step_id, {}).get("status") or "") in completed_statuses
    ]
    failed_step_ids = [
        step_id
        for step_id in planned_step_ids
        if str(ledger_by_step.get(step_id, {}).get("status") or "") in {"failed", "blocked", "needs_revision", "preflight_failed"}
    ]
    pending_step_ids = [
        step_id
        for step_id in planned_step_ids
        if step_id not in completed_step_ids and step_id not in failed_step_ids
    ]
    completed_set = set(completed_step_ids)
    failed_set = set(failed_step_ids)
    ready_step_ids: list[str] = []
    blocked_step_ids: list[str] = []
    waiting_step_ids: list[str] = []
    step_states: list[dict[str, Any]] = []
    for planned in planned_steps:
        if not isinstance(planned, dict):
            continue
        step_id = str(planned.get("step_id") or "")
        if not step_id:
            continue
        recorded = ledger_by_step.get(step_id, {})
        recorded_status = str(recorded.get("status") or "")
        input_artifacts = planned.get("input_artifacts") if isinstance(planned.get("input_artifacts"), list) else []
        expected_artifacts = planned.get("expected_artifacts") if isinstance(planned.get("expected_artifacts"), list) else []
        quality_hints = planned.get("quality_hints") if isinstance(planned.get("quality_hints"), dict) else {}
        artifacts = recorded.get("artifacts") if isinstance(recorded.get("artifacts"), list) else []
        depends_on = planned.get("depends_on") if isinstance(planned.get("depends_on"), list) else []
        details = recorded.get("details") if isinstance(recorded.get("details"), dict) else {}
        worker_view = details.get("worker_view") if isinstance(details.get("worker_view"), dict) else {}
        dependency_status = [
            {
                "step_id": str(dependency),
                "completed": str(dependency) in completed_set,
                "failed": str(dependency) in failed_set,
            }
            for dependency in depends_on
        ]
        dependency_blocked = any(item["failed"] for item in dependency_status)
        dependency_ready = all(item["completed"] for item in dependency_status)
        if step_id in pending_step_ids:
            if dependency_ready:
                ready_step_ids.append(step_id)
            elif dependency_blocked:
                blocked_step_ids.append(step_id)
            else:
                waiting_step_ids.append(step_id)
        step_states.append(
            {
                "step_id": step_id,
                "worker": str(planned.get("worker") or recorded.get("worker") or ""),
                "status": recorded_status or "pending",
                "depends_on": depends_on,
                "dependency_status": dependency_status,
                "dependencies_ready": dependency_ready,
                "dependencies_blocked": dependency_blocked,
                "input_artifacts": input_artifacts,
                "input_artifact_status": [sandbox_artifact_file_status(workspace_root, str(path)) for path in input_artifacts],
                "expected_artifacts": expected_artifacts,
                "expected_artifact_status": [sandbox_artifact_file_status(workspace_root, str(path)) for path in expected_artifacts],
                "quality_hints": quality_hints,
                "artifacts": artifacts,
                "artifact_status": [sandbox_artifact_file_status(workspace_root, str(path)) for path in artifacts],
                "summary": str(recorded.get("summary") or ""),
                "recorded": bool(recorded),
                "worker_view": worker_view,
            }
        )
    return {
        "planned_steps": len(planned_steps),
        "recorded_steps": len(ledger_steps),
        "completed_steps": completed,
        "failed_steps": failed,
        "pending_steps": len(pending_step_ids),
        "ready_steps": len(ready_step_ids),
        "blocked_steps": len(blocked_step_ids),
        "waiting_steps": len(waiting_step_ids),
        "by_status": by_status,
        "planned_step_ids": planned_step_ids,
        "completed_step_ids": completed_step_ids,
        "failed_step_ids": failed_step_ids,
        "pending_step_ids": pending_step_ids,
        "ready_step_ids": ready_step_ids,
        "blocked_step_ids": blocked_step_ids,
        "waiting_step_ids": waiting_step_ids,
        "next_step_id": pending_step_ids[0] if pending_step_ids else "",
        "next_ready_step_id": ready_step_ids[0] if ready_step_ids else "",
        "step_states": step_states,
    }


def run_summary(run_dir: Path) -> dict[str, Any]:
    status_path = run_dir / "status.json"
    ledger_path = run_dir / "task_ledger.json"
    status, status_error = load_json_object(status_path, "status") if status_path.exists() else ({}, "")
    ledger, ledger_error = load_ledger_dict(ledger_path)
    result = ledger.get("result", {}) if isinstance(ledger.get("result"), dict) else {}
    revision_plan = result.get("revision_plan") if isinstance(result.get("revision_plan"), dict) else {"required": False, "steps": []}
    revision_plan_errors = validate_revision_plan(run_dir, revision_plan)
    package_errors = run_package_action_errors(run_dir)
    oversight_errors = run_oversight_validation_errors(run_dir, status)
    summary = {
        "task_id": ledger.get("task_id") or status.get("task_id") or run_dir.name,
        "run_dir": str(run_dir),
        "status": "corrupt" if (ledger_error and ledger_path.exists()) or status_error else ledger.get("status") or status.get("status") or "unknown",
        "goal": ledger.get("goal") or "",
        "governor": ledger.get("governor") or status.get("governor") or "",
        "created_at": ledger.get("created_at") or "",
        "updated_at": ledger.get("updated_at") or "",
        "result": result,
        "revision_plan": revision_plan,
        "revision_plan_errors": revision_plan_errors,
        "revision_plan_summary": revision_plan_summary(revision_plan, revision_plan_errors),
        "package_errors": package_errors,
        "oversight_errors": oversight_errors,
        "oversight_summary": run_oversight_summary(run_dir),
        "final_manifest_summary": final_manifest_summary(result),
        "progress": run_progress(status, ledger),
        "last_preflight": last_run_preflight(ledger),
    }
    summary["actions"] = run_actions(
        str(summary["status"]),
        revision_plan,
        revision_plan_errors=revision_plan_errors,
        package_errors=package_errors,
        oversight_errors=oversight_errors,
        research_loop_blocked=bool(result.get("research_loop_blocked")),
    )
    if status_error:
        summary["status_error"] = status_error
    if ledger_error and ledger_path.exists():
        summary["ledger_error"] = ledger_error
    return summary


def list_runs(run_root: Path) -> list[dict[str, Any]]:
    if not run_root.exists():
        return []
    runs = [run_summary(path) for path in run_root.iterdir() if path.is_dir() and not path.name.startswith("_")]
    return sorted(runs, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)


def run_status_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for run in runs:
        status = str(run.get("status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
    active = sum(by_status.get(status, 0) for status in ("running", "cancelling", "queued"))
    return {"total": len(runs), "active": active, "by_status": by_status}


def last_run_preflight(ledger: dict[str, Any]) -> dict[str, Any]:
    events = ledger.get("events") if isinstance(ledger.get("events"), list) else []
    for event in reversed(events):
        if not isinstance(event, dict) or event.get("type") != "run_preflight_recorded":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        return {"at": str(event.get("at") or ""), **payload}
    return {}


def _short_text(value: Any, max_chars: int = 800) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def _status_severity(status: str) -> str:
    if status in {"failed", "blocked", "corrupt"}:
        return "error"
    if status in {"needs_revision", "preflight_failed", "cancelled", "interrupted", "passed_with_warnings"}:
        return "warning"
    return "info"


def _step_activity_text(step: dict[str, Any]) -> tuple[str, str]:
    step_id = str(step.get("step_id") or "")
    worker = str(step.get("worker") or "")
    status = str(step.get("status") or "pending")
    summary = _short_text(step.get("summary"), 900)
    label = f"{worker} / {step_id}" if worker and step_id else worker or step_id or "step"
    if status in {"pending", "ready"}:
        return f"Планирую шаг: {label}", summary or "Шаг еще не запускался."
    if status == "running":
        return f"Сейчас занимаюсь шагом: {label}", summary or "Шаг выполняется."
    if status in {"completed", "ready", "passed_with_warnings"}:
        return f"Закончил шаг: {label}", summary or f"Статус: {status}."
    if status == "needs_revision":
        return f"Шаг требует доработки: {label}", summary or "Проверка нашла недостающие данные или слабое качество."
    if status in {"failed", "blocked", "preflight_failed"}:
        return f"Шаг остановлен: {label}", summary or f"Статус: {status}."
    return f"Шаг обновлен: {label}", summary or f"Статус: {status}."


def _revision_reasons(revision_plan: dict[str, Any], limit: int = 8) -> list[dict[str, str]]:
    steps = revision_plan.get("steps") if isinstance(revision_plan.get("steps"), list) else []
    reasons: list[dict[str, str]] = []
    for item in steps[:limit]:
        if not isinstance(item, dict):
            continue
        reasons.append(
            {
                "step_id": str(item.get("step_id") or ""),
                "worker": str(item.get("worker") or ""),
                "priority": str(item.get("priority") or ""),
                "reason": _short_text(item.get("reason"), 1200),
            }
        )
    return reasons


def governor_activity_report(summary: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    """Build a brigade-tab activity log independent from Shushunya chat replies."""
    task_id = str(summary.get("task_id") or ledger.get("task_id") or "")
    governor = str(summary.get("governor") or ledger.get("governor") or "")
    status = str(summary.get("status") or ledger.get("status") or "unknown")
    progress = summary.get("progress") if isinstance(summary.get("progress"), dict) else {}
    step_states = progress.get("step_states") if isinstance(progress.get("step_states"), list) else []
    result = summary.get("result") if isinstance(summary.get("result"), dict) else {}
    revision_plan = summary.get("revision_plan") if isinstance(summary.get("revision_plan"), dict) else {}
    revision_summary = summary.get("revision_plan_summary") if isinstance(summary.get("revision_plan_summary"), dict) else {}
    manifest_summary = summary.get("final_manifest_summary") if isinstance(summary.get("final_manifest_summary"), dict) else {}
    blockers = manifest_summary.get("blockers") if isinstance(manifest_summary.get("blockers"), list) else []
    warnings = manifest_summary.get("warnings") if isinstance(manifest_summary.get("warnings"), list) else []
    entries: list[dict[str, Any]] = [
        {
            "kind": "task_received",
            "severity": "info",
            "at": str(ledger.get("created_at") or summary.get("created_at") or ""),
            "headline": f"{governor or 'Governor'} получил задачу",
            "detail": _short_text(summary.get("goal") or ledger.get("goal"), 1400),
        }
    ]
    for step in step_states:
        if not isinstance(step, dict):
            continue
        status_text = str(step.get("status") or "pending")
        headline, detail = _step_activity_text(step)
        entries.append(
            {
                "kind": "step",
                "severity": _status_severity(status_text),
                "at": str(step.get("updated_at") or ""),
                "step_id": str(step.get("step_id") or ""),
                "worker": str(step.get("worker") or ""),
                "status": status_text,
                "headline": headline,
                "detail": detail,
                "artifacts": step.get("artifacts") if isinstance(step.get("artifacts"), list) else [],
                "artifact_status": step.get("artifact_status") if isinstance(step.get("artifact_status"), list) else [],
            }
        )
    final_headline = "Финальный отчет бригадира"
    final_detail = _short_text(result.get("summary") or status, 1000)
    if status == "completed":
        final_headline = "Финальный отчет: задача завершена"
        final_detail = final_detail or "Бригада завершила задачу."
    elif revision_plan.get("required"):
        final_headline = "Финальный отчет: нужна ревизия"
        final_detail = f"Бригада не выпускает результат как окончательный: требуется {int(revision_summary.get('step_count') or 0)} revision-шагов."
    elif status in {"failed", "blocked"}:
        final_headline = "Финальный отчет: задача остановлена"
        final_detail = final_detail or "Бригада остановила выполнение; нужны диагностика или новая команда."
    entries.append(
        {
            "kind": "final_report",
            "severity": _status_severity(status),
            "at": str(summary.get("updated_at") or ledger.get("updated_at") or ""),
            "headline": final_headline,
            "detail": final_detail,
            "blockers": blockers,
            "warnings": warnings,
            "revision_reasons": _revision_reasons(revision_plan),
        }
    )
    log_lines = [
        f"{entry.get('headline')}: {entry.get('detail')}".strip()
        for entry in entries
        if entry.get("headline") or entry.get("detail")
    ]
    return {
        "kind": "governor_activity_report",
        "task_id": task_id,
        "governor": governor,
        "status": status,
        "source": "task_ledger_and_run_summary",
        "chat_independent": True,
        "entries": entries,
        "final_report": entries[-1] if entries else {},
        "log_text": "\n".join(log_lines),
        "polling": {
            "endpoint": f"GET /runs/{quote(task_id, safe='')}/activity",
            "orchestration_endpoint": f"GET /runs/{quote(task_id, safe='')}/orchestration",
        },
    }


def payload_with_run_view(payload: dict[str, Any], run_dir: Path, task_id: str = "") -> dict[str, Any]:
    summary = run_summary(run_dir)
    view = orchestration_view_fields(summary, task_id=task_id or run_dir.name)
    enriched = dict(payload)
    enriched.update(
        {
            "run_summary": summary,
            "phase": view.get("phase", ""),
            "status": view.get("status", ""),
            "decision": view.get("decision", {}),
            "display": view.get("display", {}),
            "next_action": view.get("next_action", {}),
            "client_action": view.get("client_action", {}),
        }
    )
    return enriched


def run_worker_tasks(run_dir: Path, include_health: bool = False, host: str = "127.0.0.1") -> dict[str, Any]:
    host = validate_service_host(host)
    dispatch_payload = run_dispatch_packets(run_dir)
    if not dispatch_payload.get("ok"):
        return dispatch_payload
    tasks: list[dict[str, Any]] = []
    for item in dispatch_payload.get("dispatch", []):
        packet = item.get("packet") if isinstance(item, dict) else {}
        if not isinstance(packet, dict):
            continue
        request_payload = packet.get("request") if isinstance(packet.get("request"), dict) else {}
        task_id = str(request_payload.get("task_id") or packet.get("task_id") or "")
        worker = str(packet.get("worker") or "")
        port = int(packet.get("port") or 0)
        task: dict[str, Any] = {
            "step_id": str(packet.get("step_id") or ""),
            "worker": worker,
            "port": port,
            "task_id": task_id,
        }
        if include_health and task_id and port:
            try:
                with urllib.request.urlopen(f"http://{host}:{port}/tasks/{quote(task_id, safe='')}", timeout=1.0) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                task["runtime"] = payload if isinstance(payload, dict) else {"ok": False, "error": "task response is not a JSON object"}
            except Exception as exc:  # noqa: BLE001 - worker task lookup is best-effort.
                task["runtime"] = {"ok": False, "error": str(exc)}
        tasks.append(task)
    return {"ok": True, "worker_tasks": tasks}


def run_events(run_dir: Path, limit: int | None = None, after: int | None = None) -> dict[str, Any]:
    ledger_path = run_dir / "task_ledger.json"
    ledger, ledger_error = load_ledger_dict(ledger_path)
    if ledger_error:
        return {"ok": False, "error": ledger_error}
    events = ledger.get("events", [])
    if not isinstance(events, list):
        events = []
    total = len(events)
    start = None
    if after is not None:
        start = max(0, min(after, total))
        events = events[start:]
        if limit is not None and limit >= 0:
            events = events[:limit]
    elif limit is not None and limit >= 0:
        start = max(0, total - limit)
        events = events[-limit:]
    else:
        start = 0
    next_cursor = start + len(events)
    task_id = str(ledger.get("task_id") or run_dir.name)
    summary = run_summary(run_dir)
    actions = summary.get("actions") if isinstance(summary.get("actions"), dict) else {}
    next_action = actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {}
    return {
        "ok": True,
        "task_id": task_id,
        "events": events,
        "display_events": display_events_for(task_id, events),
        "run_client_action": executable_client_action(task_id, next_action),
        "cursor": {"after": start, "next": next_cursor, "total": total},
    }


def all_run_events(run_root: Path, limit: int | None = None, after: int | None = None) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    if not run_root.exists():
        return {"ok": True, "events": [], "cursor": {"after": 0, "next": 0, "total": 0}, "errors": []}
    for run_dir in run_root.iterdir():
        if not run_dir.is_dir() or run_dir.name.startswith("_"):
            continue
        ledger, ledger_error = load_ledger_dict(run_dir / "task_ledger.json")
        if ledger_error:
            errors.append({"task_id": run_dir.name, "error": ledger_error})
            continue
        task_id = str(ledger.get("task_id") or run_dir.name)
        run_status = str(ledger.get("status") or "")
        governor = str(ledger.get("governor") or "")
        run_updated_at = str(ledger.get("updated_at") or "")
        summary = run_summary(run_dir)
        actions = summary.get("actions") if isinstance(summary.get("actions"), dict) else {}
        next_action = actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {}
        manifest_summary = summary.get("final_manifest_summary") if isinstance(summary.get("final_manifest_summary"), dict) else {}
        raw_events = ledger.get("events") if isinstance(ledger.get("events"), list) else []
        for index, event in enumerate(raw_events):
            if not isinstance(event, dict):
                continue
            events.append(
                {
                    "task_id": task_id,
                    "run_status": run_status,
                    "governor": governor,
                    "run_updated_at": run_updated_at,
                    "event_index": index,
                    "at": str(event.get("at") or ""),
                    "type": str(event.get("type") or ""),
                    "run_next_action": next_action,
                    "run_client_action": executable_client_action(task_id, next_action),
                    "run_final_manifest_summary": manifest_summary,
                    "display": event_display(event, task_id=task_id),
                    "payload": event.get("payload") if isinstance(event.get("payload"), dict) else {},
                }
            )
    events.sort(key=lambda item: (str(item.get("at") or ""), str(item.get("task_id") or ""), int(item.get("event_index") or 0)))
    for index, event in enumerate(events):
        event["global_index"] = index
    total = len(events)
    if after is not None:
        start = max(0, min(after, total))
        selected = events[start:]
        if limit is not None and limit >= 0:
            selected = selected[:limit]
    elif limit is not None and limit >= 0:
        start = max(0, total - limit)
        selected = events[-limit:]
    else:
        start = 0
        selected = events
    return {
        "ok": True,
        "events": selected,
        "display_events": [item.get("display") for item in selected if isinstance(item.get("display"), dict)],
        "cursor": {"after": start, "next": start + len(selected), "total": total},
        "errors": errors,
    }


def run_snapshot(run_dir: Path, event_limit: int | None = None, events_after: int | None = None) -> dict[str, Any]:
    task_id = run_dir.name
    with ACTIVE_RUNS_LOCK:
        active = task_id in ACTIVE_RUNS
    payload: dict[str, Any] = {
        "ok": True,
        "task_id": task_id,
        "summary": run_summary(run_dir),
        "active": active,
    }
    events_payload = run_events(run_dir, limit=event_limit, after=events_after)
    payload["events"] = events_payload.get("events", [])
    payload["display_events"] = events_payload.get("display_events", [])
    payload["run_client_action"] = events_payload.get("run_client_action", {})
    payload["event_cursor"] = events_payload.get("cursor", {"after": 0, "next": 0, "total": 0})
    payload["revision_plan"] = payload["summary"].get("revision_plan", {"required": False, "steps": []})
    payload["revision_plan_summary"] = payload["summary"].get("revision_plan_summary", {})
    if not events_payload.get("ok"):
        payload["events_error"] = events_payload.get("error", "events unavailable")
    ledger_path = run_dir / "task_ledger.json"
    ledger, ledger_error = load_ledger_dict(ledger_path)
    if ledger_error:
        payload["artifacts_error"] = ledger_error
        payload["artifacts"] = []
    else:
        payload.update(artifact_status(ledger))
        payload["governor_activity"] = governor_activity_report(payload["summary"], ledger)
    return payload


def orchestration_state(run_dir: Path, event_limit: int | None = 20, events_after: int | None = 0, max_bytes: int = 2000) -> dict[str, Any]:
    snapshot = run_snapshot(run_dir, event_limit=event_limit, events_after=events_after)
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    status = str(summary.get("status") or "")
    final_payload: dict[str, Any] = {}
    if status == "completed":
        ledger_path = run_dir / "task_ledger.json"
        ledger, ledger_error = load_ledger_dict(ledger_path)
        if ledger_error:
            final_payload = {"ok": False, "error": ledger_error}
        else:
            final_payload = final_package(ledger, max_bytes=max_bytes)
    view = orchestration_view_fields(
        summary,
        active=bool(snapshot.get("active")),
        event_cursor_next=int(snapshot.get("event_cursor", {}).get("next", 0)),
        final_payload=final_payload,
        final_max_bytes=max_bytes,
        task_id=run_dir.name,
    )
    return {
        "ok": True,
        "task_id": run_dir.name,
        "phase": view["phase"],
        "status": view["status"],
        "active": view["active"],
        "decision": view["decision"],
        "display": view["display"],
        "display_events": snapshot.get("display_events", []),
        "governor_activity": snapshot.get("governor_activity", {}),
        "snapshot": snapshot,
        "final": final_payload,
        "next_action": view["next_action"],
        "client_action": view["client_action"],
    }


def run_step_state(run_dir: Path, step_id: str) -> dict[str, Any]:
    summary = run_summary(run_dir)
    for step in summary.get("progress", {}).get("step_states", []):
        if isinstance(step, dict) and step.get("step_id") == step_id:
            return {"ok": True, "task_id": run_dir.name, "step": step, "summary": summary}
    return {"ok": False, "task_id": run_dir.name, "error": "step not found", "step_id": step_id}


def run_step_artifacts(run_dir: Path, step_id: str) -> dict[str, Any]:
    state = run_step_state(run_dir, step_id)
    if not state.get("ok"):
        return state
    step = state.get("step") if isinstance(state.get("step"), dict) else {}
    return {
        "ok": True,
        "task_id": run_dir.name,
        "step_id": step_id,
        "worker": step.get("worker", ""),
        "status": step.get("status", ""),
        "input_artifacts": step.get("input_artifacts", []),
        "input_artifact_status": step.get("input_artifact_status", []),
        "expected_artifacts": step.get("expected_artifacts", []),
        "expected_artifact_status": step.get("expected_artifact_status", []),
        "artifacts": step.get("artifacts", []),
        "artifact_status": step.get("artifact_status", []),
    }
