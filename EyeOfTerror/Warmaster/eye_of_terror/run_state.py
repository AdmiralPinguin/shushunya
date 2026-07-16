"""Run state view-model: progress, summaries, events, snapshots, and
per-step/worker-task inspection built from a run package."""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote

from EyeOfTerror.common_protocol import LIFECYCLE_STATUSES, TERMINAL_LIFECYCLE_STATUSES

from .actions import run_actions
from .artifacts import artifact_status, final_manifest_summary, final_package
from .gateway_util import validate_service_host
from .native_runs import native_adapter_for_run
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


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_json_dir(directory: Path, limit: int = 200) -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    payloads: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json"))[-max(0, limit) :]:
        payload = _read_json(path)
        if payload:
            payloads.append(payload)
    return payloads


def _read_jsonl(path: Path, limit: int = 200) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events[-max(0, limit) :]


def mission_ref_for_run(run_dir: Path) -> dict[str, Any]:
    return _read_json(run_dir / "mission_ref.json")


def mission_dir_for_run(run_dir: Path) -> Path | None:
    ref = mission_ref_for_run(run_dir)
    raw_mission_dir = str(ref.get("mission_dir") or "")
    if not raw_mission_dir:
        return None
    return Path(raw_mission_dir)


def mission_protocol_payloads_for_run(run_dir: Path) -> dict[str, dict[str, Any]]:
    mission_dir = mission_dir_for_run(run_dir)
    if mission_dir is None:
        return {}
    from .mission_control import mission_protocol_audit, mission_protocol_summary

    return {
        "mission": _read_json(mission_dir / "mission.json"),
        "mission_state": _read_json(mission_dir / "mission_state.json"),
        "commander_order": _read_json(mission_dir / "commander_order.json"),
        "governor_plan": _read_json(mission_dir / "governor_plan.json"),
        "worker_orders": _read_json_dir(mission_dir / "worker_orders", limit=1000),
        "worker_reports": _read_json_dir(mission_dir / "worker_reports", limit=1000),
        "governor_reports": _read_json_dir(mission_dir / "governor_reports", limit=1000),
        "acceptance_reviews": _read_json_dir(mission_dir / "acceptance_reviews", limit=1000),
        "revision_orders": _read_json_dir(mission_dir / "revision_orders", limit=1000),
        "final_response": _read_json(mission_dir / "final_response.json"),
        "protocol_summary": mission_protocol_summary(mission_dir),
        "protocol_audit": mission_protocol_audit(mission_dir),
    }


def mission_progress_events_for_run(run_dir: Path, limit: int = 200) -> list[dict[str, Any]]:
    mission_dir = mission_dir_for_run(run_dir)
    if mission_dir is None:
        return []
    return _read_jsonl(mission_dir / "progress_events.jsonl", limit=limit)


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
    completed_statuses = {"completed", "ready", "passed", "passed_with_warnings"}
    completed = sum(count for status, count in by_status.items() if status in completed_statuses)
    failed = by_status.get("failed", 0)
    planned_step_ids = [
        str(step.get("step_id") or "")
        for step in planned_steps
        if isinstance(step, dict) and step.get("step_id")
    ]
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


RUN_STATUS_TO_LIFECYCLE = {
    "created": "created",
    "queued": "assigned",
    "ready": "plan_review",
    "ready_to_start": "plan_review",
    "running": "executing",
    "apply_intent": "executing",
    "applied_unverified": "executing",
    "publishing": "executing",
    "push_pending": "executing",
    "protocol_finalize_pending": "executing",
    "cancelling": "cancelled",
    "cancelled": "cancelled",
    "completed": "completed",
    "failed": "failed",
    "corrupt": "failed",
    "interrupted": "failed",
    "blocked": "blocked",
    "preflight_failed": "failed",
    "needs_revision": "revision",
    "revision": "revision",
}


def lifecycle_status_for(summary_status: str, mission: dict[str, Any]) -> str:
    mission_status = str(mission.get("status") or "").strip()
    if mission_status in LIFECYCLE_STATUSES:
        return mission_status
    normalized = str(summary_status or "").strip().lower()
    return RUN_STATUS_TO_LIFECYCLE.get(normalized, "created")


def _mission_user_visible_state(
    lifecycle_status: str,
    active: bool = False,
    *,
    needs_user: bool = False,
) -> str:
    if lifecycle_status == "completed":
        return "final_ready"
    if lifecycle_status == "cancelled":
        return "cancelled"
    if lifecycle_status == "failed":
        return "failed"
    if lifecycle_status == "blocked":
        return "needs_user_decision" if needs_user else "internal_repair_required"
    if active or lifecycle_status in {"executing", "governor_review", "warmaster_acceptance", "revision"}:
        return "working"
    return "accepted"


def _mission_next_owner(
    lifecycle_status: str,
    active: bool = False,
    *,
    needs_user: bool = False,
) -> str:
    if lifecycle_status in {"created", "intake", "assigned", "warmaster_acceptance"}:
        return "Warmaster"
    if lifecycle_status in {"planning", "plan_review", "executing", "governor_review", "revision"} or active:
        return "governor"
    if lifecycle_status == "blocked":
        return "user" if needs_user else "governor"
    if lifecycle_status in TERMINAL_LIFECYCLE_STATUSES:
        return "none"
    return "Warmaster"


def mission_state_view(summary: dict[str, Any], active: bool = False, phase: str = "") -> dict[str, Any]:
    protocol = summary.get("mission_protocol") if isinstance(summary.get("mission_protocol"), dict) else {}
    mission = protocol.get("mission") if isinstance(protocol.get("mission"), dict) else {}
    durable_state = protocol.get("mission_state") if isinstance(protocol.get("mission_state"), dict) else {}
    mission_ref = summary.get("mission_ref") if isinstance(summary.get("mission_ref"), dict) else {}
    command = protocol.get("commander_order") if isinstance(protocol.get("commander_order"), dict) else {}
    mission_id = str(durable_state.get("mission_id") or mission.get("mission_id") or mission_ref.get("mission_id") or command.get("mission_id") or "")
    task_id = str(durable_state.get("task_id") or summary.get("task_id") or mission.get("task_id") or "")
    lifecycle_status = str(durable_state.get("status") or summary.get("lifecycle_status") or "").strip()
    if lifecycle_status not in LIFECYCLE_STATUSES:
        lifecycle_status = lifecycle_status_for(str(summary.get("status") or ""), mission)
    revision_plan = summary.get("revision_plan") if isinstance(summary.get("revision_plan"), dict) else {}
    if lifecycle_status == "failed" and bool(revision_plan.get("required")):
        lifecycle_status = "revision"
    assigned_governor = str(durable_state.get("assigned_governor") or mission.get("assigned_governor") or mission_ref.get("assigned_governor") or command.get("to") or summary.get("governor") or "")
    result = summary.get("result") if isinstance(summary.get("result"), dict) else {}
    final_response = (
        protocol.get("final_response")
        if isinstance(protocol.get("final_response"), dict)
        else {}
    )
    # The protocol state is the durable authority. Acceptance escalation can
    # exist before a final_response and must survive a run-summary round trip.
    needs_user = any(source.get("needs_user") is True for source in (
        durable_state, result, final_response,
    ))
    return {
        "kind": "mission_state",
        "mission_id": mission_id,
        "task_id": task_id,
        "status": lifecycle_status,
        "run_status": str(summary.get("status") or ""),
        "mission_status": str(durable_state.get("mission_status") or summary.get("mission_status") or mission.get("status") or ""),
        "phase": phase or lifecycle_status,
        "active": bool(active),
        "assigned_governor": assigned_governor,
        "needs_user": needs_user,
        "next_owner": _mission_next_owner(
            lifecycle_status,
            active=active,
            needs_user=needs_user,
        ),
        "user_visible_state": _mission_user_visible_state(
            lifecycle_status,
            active=active,
            needs_user=needs_user,
        ),
        "revision_is_internal": True,
        "source": "durable_mission_state" if durable_state else ("mission_protocol" if mission_id else "legacy_run_summary"),
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
    mission_protocol = mission_protocol_payloads_for_run(run_dir)
    mission = mission_protocol.get("mission") if isinstance(mission_protocol.get("mission"), dict) else {}
    summary_status = "corrupt" if (ledger_error and ledger_path.exists()) or status_error else ledger.get("status") or status.get("status") or "unknown"
    summary = {
        "task_id": ledger.get("task_id") or status.get("task_id") or run_dir.name,
        "run_dir": str(run_dir),
        "status": summary_status,
        "lifecycle_status": lifecycle_status_for(str(summary_status), mission),
        "mission_status": str(mission.get("status") or ""),
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
        "mission_ref": mission_ref_for_run(run_dir),
        "mission_protocol": mission_protocol,
        "mission_progress_events": mission_progress_events_for_run(run_dir),
        "progress": run_progress(status, ledger),
        "last_preflight": last_run_preflight(ledger),
    }
    summary["mission_state"] = mission_state_view(summary)
    result_next_action = (
        result.get("next_action")
        if isinstance(result.get("next_action"), dict)
        else {}
    )
    if (
        not result_next_action
        and str(summary["status"]) == "blocked"
        and str(summary.get("governor") or "") == "Ceraxia"
        and native_adapter_for_run(run_dir, declared=True) is None
        and (
            str(result.get("phase") or "") == "ceraxia_directive_invalid"
            or "ceraxia_directive" in str(result.get("error") or "").lower()
        )
    ):
        result_next_action = {
            "kind": "reprepare_ceraxia_run",
            "method": "POST",
            "endpoint": "POST /orchestrate_run",
            "body": {
                "message": str(ledger.get("goal") or ""),
                "governor_transport": "http",
                "run_mode": "http",
                "auto_start": True,
            },
            "reason": "historical Ceraxia evidence cannot be revised as a native run; create a fresh mission",
        }
    summary["actions"] = run_actions(
        str(summary["status"]),
        revision_plan,
        revision_plan_errors=revision_plan_errors,
        package_errors=package_errors,
        oversight_errors=oversight_errors,
        research_loop_blocked=bool(result.get("research_loop_blocked")),
        result_next_action=result_next_action,
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


def _protocol_task_brief(summary: dict[str, Any], ledger: dict[str, Any], max_chars: int = 520) -> tuple[str, str]:
    protocol = summary.get("mission_protocol") if isinstance(summary.get("mission_protocol"), dict) else {}
    command = protocol.get("commander_order") if isinstance(protocol.get("commander_order"), dict) else {}
    plan = protocol.get("governor_plan") if isinstance(protocol.get("governor_plan"), dict) else {}
    for source, candidate in (
        ("commander_order.primary_goal", command.get("primary_goal")),
        ("commander_order.commander_intent", command.get("commander_intent")),
        ("governor_plan.understanding", plan.get("understanding")),
    ):
        text = str(candidate or "").strip()
        if text:
            return _short_text(text, max_chars), source
    raw_goal = str(summary.get("goal") or ledger.get("goal") or "").strip()
    if raw_goal.startswith("ПРИКАЗ ВАРМАСТЕРА"):
        match = re.search(r"Главная цель:\s*(.+?)(?:\n\n|\n[A-ZА-ЯЁ][^:\n]{1,80}:|$)", raw_goal, re.S)
        if match:
            return _short_text(match.group(1), max_chars), "legacy_goal.extracted_primary_goal"
    return _short_text(raw_goal, max_chars), "legacy_goal"


def _status_severity(status: str) -> str:
    if status in {"failed", "blocked", "corrupt"}:
        return "error"
    if status in {"needs_revision", "preflight_failed", "cancelled", "interrupted", "passed_with_warnings"}:
        return "warning"
    return "info"


STEP_ACTIVITY_LABELS = {
    "corpus_ingestion": "поиск локальных материалов",
    "source_discovery": "карту источников",
    "source_acquisition": "загрузку источников",
    "source_rendering": "рендер сложных страниц",
    "fact_extraction": "извлечение фактов",
    "structure_mapping": "хронологию и структуру",
    "synthesis_planning": "план сборки текста",
    "draft_reconstruction": "черновик реконструкции",
    "critic_review": "проверку качества",
    "finalize": "финальную упаковку результата",
}


WORKER_ACTIVITY_LABELS = {
    "CorpusIngestor": "сборщик корпуса",
    "Lexmechanic": "поисковик источников",
    "AuspexBrowser": "загрузчик страниц",
    "OcularisRenderium": "рендерер страниц",
    "NoosphericExtractor": "извлекатель фактов",
    "Chronologis": "хронолог",
    "ScriptoriumArchitect": "планировщик текста",
    "ScriptoriumDaemon": "писарь черновика",
    "ReductorVerifier": "проверяющий",
    "FabricatorFinalis": "финализатор",
}


GOVERNOR_ACTIVITY_LABELS = {
    "Warmaster": "Абаддон",
    "IskandarKhayon": "Искандар",
    "Iskandar": "Искандар",
    "Scriptorium": "Искандар",
    "Ceraxia": "Цераксия",
    "CeraxiaTheRed": "Цераксия",
    "Mechanicum": "Цераксия",
    "Moriana": "Мориана",
    "Pictorium": "Мориана",
    "AshurKai": "Ашур-Кай",
    "Administratum": "Ашур-Кай",
}


WORKER_GOVERNOR_HINTS = {
    "CorpusIngestor": "IskandarKhayon",
    "Lexmechanic": "IskandarKhayon",
    "AuspexBrowser": "IskandarKhayon",
    "OcularisRenderium": "IskandarKhayon",
    "NoosphericExtractor": "IskandarKhayon",
    "Chronologis": "IskandarKhayon",
    "ScriptoriumArchitect": "IskandarKhayon",
    "ScriptoriumDaemon": "IskandarKhayon",
    "ReductorVerifier": "IskandarKhayon",
    "FabricatorFinalis": "IskandarKhayon",
    "ResearchWarband": "IskandarKhayon",
    "SkitariiWarband": "Ceraxia",
    "Promptwright": "Moriana",
    "Canvaswright": "Moriana",
    "Compositionwright": "Moriana",
    "CharacterSheetwright": "Moriana",
    "ForgeRunner": "Moriana",
}


def _step_label(step_id: str) -> str:
    return STEP_ACTIVITY_LABELS.get(step_id, step_id.replace("_", " ") or "шаг")


def _worker_label(worker: str) -> str:
    return WORKER_ACTIVITY_LABELS.get(worker, worker or "воркер")


def _governor_label(governor: str) -> str:
    clean = str(governor or "").strip()
    return GOVERNOR_ACTIVITY_LABELS.get(clean, clean or "Бригада")


def _governor_key(governor: str) -> str:
    clean = str(governor or "").strip()
    if clean in {"IskandarKhayon", "Iskandar", "Scriptorium"}:
        return "iskandar"
    if clean in {"Ceraxia", "CeraxiaTheRed", "Mechanicum"}:
        return "ceraxia"
    if clean in {"Moriana", "Pictorium"}:
        return "moriana"
    if clean in {"AshurKai", "Administratum"}:
        return "ashur_kai"
    if clean == "Warmaster":
        return "warmaster"
    return re.sub(r"[^a-z0-9_]+", "_", clean.lower()).strip("_") or "brigade"


def _extract_ints(text: str) -> list[int]:
    return [int(item) for item in re.findall(r"\d+", text or "")]


def _russian_step_detail(step_id: str, worker: str, status: str, summary: str) -> str:
    numbers = _extract_ints(summary)
    actor = _worker_label(worker)
    if status in {"pending", "ready"}:
        return f"{actor} готовит {_step_label(step_id)}; шаг еще не выполнен."
    if status == "running":
        return f"{actor} сейчас выполняет {_step_label(step_id)}."
    if step_id == "corpus_ingestion" and numbers:
        return f"{actor} проверил локальный корпус и нашел {numbers[0]} подходящих материалов."
    if step_id == "source_discovery" and numbers:
        return f"{actor} составил карту источников: найдено {numbers[0]} кандидатов для проверки."
    if step_id == "source_acquisition" and numbers:
        failed = numbers[1] if len(numbers) > 1 else 0
        return f"{actor} загрузил материалы из {numbers[0]} источников; не удалось получить {failed}."
    if step_id == "source_rendering" and numbers:
        rendered = numbers[0]
        total = numbers[1] if len(numbers) > 1 else rendered
        return f"{actor} проверил страницы, которым мог потребоваться браузерный рендер: обработано {rendered} из {total}."
    if step_id == "fact_extraction" and len(numbers) >= 2:
        return f"{actor} извлек {numbers[0]} событий и {numbers[1]} проверяемых утверждений для исследовательского корпуса."
    if step_id == "structure_mapping" and len(numbers) >= 2:
        return f"{actor} выстроил структуру: {numbers[0]} раздела и {numbers[1]} событий в хронологии."
    if step_id == "synthesis_planning":
        return f"{actor} подготовил план, по которому черновик должен собираться из найденных фактов."
    if step_id == "draft_reconstruction":
        return f"{actor} собрал черновик реконструкции по подготовленному плану и корпусу фактов."
    if step_id == "critic_review" and numbers:
        warnings = numbers[1] if len(numbers) > 1 else 0
        return f"{actor} проверил результат и нашел {numbers[0]} замечаний; предупреждений: {warnings}."
    if step_id == "finalize":
        if status in {"blocked", "failed", "needs_revision"}:
            return f"{actor} отказался выпускать результат как готовый: проверка качества требует доработки."
        return f"{actor} собрал финальный пакет и подготовил результат к выдаче."
    if status in {"completed", "passed_with_warnings"}:
        return f"{actor} завершил {_step_label(step_id)}; подробности сохранены в артефактах шага."
    if status == "needs_revision":
        return f"{actor} пометил {_step_label(step_id)} как требующий доработки."
    if status in {"failed", "blocked", "preflight_failed"}:
        return f"{actor} остановил {_step_label(step_id)}; шаг нельзя считать успешно закрытым."
    return f"{actor} обновил состояние шага: {_step_label(step_id)}."


def _step_activity_text(step: dict[str, Any]) -> tuple[str, str]:
    step_id = str(step.get("step_id") or "")
    worker = str(step.get("worker") or "")
    status = str(step.get("status") or "pending")
    summary = _short_text(step.get("summary"), 900)
    label = _step_label(step_id)
    detail = _russian_step_detail(step_id, worker, status, summary)
    if status in {"pending", "ready"}:
        return f"Планирую: {label}", detail
    if status == "running":
        return f"Сейчас занимаюсь: {label}", detail
    if status in {"completed", "ready", "passed_with_warnings"}:
        return f"Закончил: {label}", detail
    if status == "needs_revision":
        return f"Требует доработки: {label}", detail
    if status in {"failed", "blocked", "preflight_failed"}:
        return f"Остановлено: {label}", detail
    return f"Обновлен шаг: {label}", detail


def _translate_revision_fragment(fragment: str) -> str:
    text = _short_text(fragment, 900).strip()
    if not text:
        return ""
    missing_event = re.search(r"Draft does not visibly cover required event:\s*(.+)", text, re.I)
    if missing_event:
        return "Черновик не раскрывает одно из обязательных событий."
    mapped = re.search(r"too few mapped sources:\s*(\d+)\s*/\s*(\d+)", text, re.I)
    if mapped:
        return f"Недостаточно источников в карте: {mapped.group(1)} из {mapped.group(2)}."
    live = re.search(r"too few live-discovered source candidates:\s*(\d+)\s*/\s*(\d+)", text, re.I)
    if live:
        return f"Недостаточно живых кандидатов, найденных через поиск: {live.group(1)} из {live.group(2)}."
    direct = re.search(r"too few direct-evidence sources:\s*(\d+)\s*/\s*(\d+)", text, re.I)
    if direct:
        return f"Недостаточно источников с прямыми свидетельствами: {direct.group(1)} из {direct.group(2)}."
    draft = re.search(r"draft is too short for requested depth:\s*(\d+)\s*/\s*(\d+)", text, re.I)
    if draft:
        return f"Черновик слишком короткий для требуемой глубины: {draft.group(1)} из {draft.group(2)} символов."
    missing_primary = re.search(r"lacks accessible primary text URLs or local corpus files for:\s*(.+)", text, re.I)
    if missing_primary:
        return f"Нет доступных первичных текстов или локальных файлов для: {missing_primary.group(1).strip()}."
    missing_local = re.search(r"Missing required local primary corpus texts:\s*(.+)", text, re.I)
    if missing_local:
        return f"В локальном корпусе не хватает обязательных первичных текстов: {missing_local.group(1).strip()}."
    depends = re.search(r"Depends on revised step\s+([A-Za-z0-9_:-]+)", text, re.I)
    if depends:
        return f"Ждет доработки зависимого шага: {_step_label(depends.group(1))}."
    return "Есть замечание проверки качества, которое требует ручного разбора."


def _unique_texts(items: list[str], limit: int = 8) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        text = item.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
        if len(unique) >= limit:
            break
    return unique


def _russian_plural(value: int, one: str, few: str, many: str) -> str:
    value_abs = abs(value)
    if value_abs % 100 in {11, 12, 13, 14}:
        return many
    if value_abs % 10 == 1:
        return one
    if value_abs % 10 in {2, 3, 4}:
        return few
    return many


def _revision_reason_text(raw_reason: str) -> str:
    fragments = [fragment.strip() for fragment in str(raw_reason or "").split("|") if fragment.strip()]
    missing_events = [
        fragment
        for fragment in fragments
        if re.search(r"Draft does not visibly cover required event:", fragment, re.I)
    ]
    if missing_events and len(missing_events) == len(fragments):
        return f"Черновик не раскрывает {len(missing_events)} обязательных событий."
    translated = _unique_texts([_translate_revision_fragment(fragment) for fragment in fragments])
    return " ".join(translated).strip()


def _revision_reasons(revision_plan: dict[str, Any], limit: int = 8) -> list[dict[str, str]]:
    steps = revision_plan.get("steps") if isinstance(revision_plan.get("steps"), list) else []
    reasons: list[dict[str, str]] = []
    for item in steps[:limit]:
        if not isinstance(item, dict):
            continue
        reason = _revision_reason_text(str(item.get("reason") or ""))
        reasons.append(
            {
                "step_id": str(item.get("step_id") or ""),
                "worker": str(item.get("worker") or ""),
                "priority": str(item.get("priority") or ""),
                "reason": reason or "Шаг требует доработки по результатам проверки качества.",
            }
        )
    return reasons


def _final_report_detail(status: str, result: dict[str, Any], revision_plan: dict[str, Any], revision_summary: dict[str, Any]) -> str:
    if status == "completed":
        return "Бригада завершила задачу и подготовила результат к выдаче."
    if revision_plan.get("required"):
        step_count = int(revision_summary.get("step_count") or 0)
        reasons = _revision_reasons(revision_plan, limit=3)
        reason_text = " ".join(item.get("reason", "") for item in reasons if item.get("reason")).strip()
        step_word = _russian_plural(step_count, "шаг", "шага", "шагов")
        count_text = f"Нужно выполнить {step_count} {step_word} доработки." if step_count else "Нужна доработка."
        return f"Я не выпускаю результат как окончательный. {count_text}" + (f" Главные причины: {reason_text}" if reason_text else "")
    if status in {"failed", "blocked"}:
        return "Бригада остановила выполнение; результат нельзя считать готовым без диагностики или новой команды."
    if status == "running":
        return "Бригада продолжает работу; финального результата еще нет."
    if status == "cancelled":
        return "Задача остановлена по запросу отмены."
    if status:
        return f"Текущий статус задачи: {status}."
    return _short_text(result.get("summary"), 1000) or "Финальное состояние пока не определено."


def _progress_event_activity_card(event: dict[str, Any]) -> dict[str, Any]:
    actor = str(event.get("actor") or "")
    role = str(event.get("role") or "")
    phase = str(event.get("phase") or "")
    status = str(event.get("status") or "")
    title = str(event.get("title") or "")
    body = str(event.get("body") or "")
    return {
        "kind": "progress_event",
        "source": "mission_protocol",
        "severity": _status_severity(status),
        "at": str(event.get("created_at") or ""),
        "actor": actor,
        "role": role,
        "phase": phase,
        "status": status,
        "headline": title,
        "detail": body,
        "card_title": title,
        "card_body": body,
        "display_title": title,
        "display_body": body,
        "mission_id": str(event.get("mission_id") or ""),
        "protocol_type": str(event.get("type") or ""),
    }


def _worker_governor_map(protocol: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for order in protocol.get("worker_orders", []) if isinstance(protocol.get("worker_orders"), list) else []:
        if not isinstance(order, dict):
            continue
        worker = str(order.get("to") or "").strip()
        governor = str(order.get("from") or "").strip()
        if worker and governor:
            mapping[worker] = governor
    plan = protocol.get("governor_plan") if isinstance(protocol.get("governor_plan"), dict) else {}
    plan_governor = str(plan.get("governor") or "").strip()
    for step in plan.get("work_plan", []) if isinstance(plan.get("work_plan"), list) else []:
        if not isinstance(step, dict):
            continue
        worker = str(step.get("worker") or "").strip()
        if worker and plan_governor:
            mapping.setdefault(worker, plan_governor)
    for worker, governor in WORKER_GOVERNOR_HINTS.items():
        mapping.setdefault(worker, governor)
    return mapping


def _card_governor(card: dict[str, Any], fallback_governor: str, worker_governors: dict[str, str]) -> str:
    role = str(card.get("role") or "").strip()
    actor = str(card.get("actor") or "").strip()
    worker = str(card.get("worker") or "").strip()
    if role == "commander" or actor == "Warmaster":
        return "Warmaster"
    if role == "governor" and actor:
        return actor
    if role == "worker" and actor:
        return worker_governors.get(actor, WORKER_GOVERNOR_HINTS.get(actor, fallback_governor))
    if worker:
        return worker_governors.get(worker, WORKER_GOVERNOR_HINTS.get(worker, fallback_governor))
    return fallback_governor


def _brigade_tabs(
    protocol_cards: list[dict[str, Any]],
    mission_events: list[dict[str, Any]],
    summary: dict[str, Any],
    ledger: dict[str, Any],
) -> list[dict[str, Any]]:
    protocol = summary.get("mission_protocol") if isinstance(summary.get("mission_protocol"), dict) else {}
    worker_governors = _worker_governor_map(protocol)
    fallback_governor = str(summary.get("governor") or ledger.get("governor") or "")
    canonical_active = str(
        summary.get("status") or ledger.get("status") or "",
    ).strip().lower() in {"running", "queued", "cancelling"}
    mission_state = (
        summary.get("mission_state")
        if isinstance(summary.get("mission_state"), dict)
        else {}
    )
    if str(mission_state.get("status") or "").strip().lower() in {
        "blocked", "cancelled", "completed", "failed",
    }:
        canonical_active = False
    events_by_index = {
        index: event
        for index, event in enumerate(mission_events)
        if isinstance(event, dict)
    }
    tabs: dict[str, dict[str, Any]] = {}

    def ensure_tab(governor: str) -> dict[str, Any]:
        key = _governor_key(governor)
        tab = tabs.get(key)
        if tab is None:
            tab = {
                "key": key,
                "label": _governor_label(governor),
                "governor": governor,
                "status": "idle",
                "active": False,
                "card_count": 0,
                "progress_events": [],
                "activity_cards": [],
                "latest_card": {},
            }
            tabs[key] = tab
        return tab

    for index, card in enumerate(protocol_cards):
        if not isinstance(card, dict):
            continue
        governor = _card_governor(card, fallback_governor, worker_governors)
        tab = ensure_tab(governor)
        event = events_by_index.get(index, {})
        tab["activity_cards"].append(card)
        if event:
            tab["progress_events"].append(event)
        tab["latest_card"] = card
        status = str(card.get("status") or "").strip()
        if status:
            tab["status"] = status
        # Historical progress is append-only and may end in a stale `running`
        # card after a finalization failure.  Canonical run status dominates it.
        tab["active"] = canonical_active and status in {"started", "running"}

    ordered = sorted(
        tabs.values(),
        key=lambda item: (0 if item.get("key") != "warmaster" else 1, str(item.get("label") or "")),
    )
    for tab in ordered:
        cards = tab.get("activity_cards") if isinstance(tab.get("activity_cards"), list) else []
        tab["card_count"] = len(cards)
        tab["empty"] = len(cards) == 0
    return ordered


def governor_activity_report(summary: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    """Build brigade-tab cards from mission protocol events and run summaries.

    The main chat must not consume this report. It is a structured activity
    surface for brigade tabs; ``progress_events`` are the only UI activity
    stream, while run-summary cards stay separate as diagnostics.
    """
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
    task_brief, task_brief_source = _protocol_task_brief(summary, ledger)
    mission_events = [
        event
        for event in summary.get("mission_progress_events", [])
        if isinstance(event, dict) and bool(event.get("visible_to_user", True))
    ] if isinstance(summary.get("mission_progress_events"), list) else []
    protocol_cards = [_progress_event_activity_card(event) for event in mission_events]
    summary_cards: list[dict[str, Any]] = [
        {
            "kind": "task_received",
            "source": "run_summary",
            "severity": "info",
            "at": str(ledger.get("created_at") or summary.get("created_at") or ""),
            "headline": f"{governor or 'Бригадир'} получил задачу",
            "detail": task_brief,
            "protocol_source": task_brief_source,
        }
    ]
    for step in step_states:
        if not isinstance(step, dict):
            continue
        status_text = str(step.get("status") or "pending")
        headline, detail = _step_activity_text(step)
        summary_cards.append(
            {
                "kind": "step",
                "source": "run_summary",
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
    final_detail = _final_report_detail(status, result, revision_plan, revision_summary)
    if status == "completed":
        final_headline = "Финальный отчет: задача завершена"
    elif revision_plan.get("required"):
        final_headline = "Финальный отчет: нужна ревизия"
    elif status in {"failed", "blocked"}:
        final_headline = "Финальный отчет: задача остановлена"
    summary_cards.append(
        {
            "kind": "final_report",
            "source": "run_summary",
            "severity": _status_severity(status),
            "at": str(summary.get("updated_at") or ledger.get("updated_at") or ""),
            "headline": final_headline,
            "detail": final_detail,
            "blockers": blockers,
            "warnings": warnings,
            "revision_reasons": _revision_reasons(revision_plan),
        }
    )
    # The fighter's own plain-language steps, relayed onto the run ledger, so the
    # mobile activity feed shows what the worker is actually doing — not just the
    # governor's plan/delegation cards. Merged chronologically with protocol cards.
    fighter_cards = [
        {
            "kind": "worker_step",
            "source": "skitarii",
            "severity": "info",
            "at": str(event.get("at") or ""),
            "headline": "Боец",
            "detail": str((event.get("payload") or {}).get("text") or "").strip()[:1000],
        }
        for event in (ledger.get("events") or [])
        if isinstance(event, dict) and event.get("type") == "skitarii_step"
        and str((event.get("payload") or {}).get("text") or "").strip()
    ]
    entries = sorted(protocol_cards + fighter_cards, key=lambda card: str(card.get("at") or ""))
    brigade_tabs = _brigade_tabs(protocol_cards, mission_events, summary, ledger)
    return {
        "kind": "governor_activity_report",
        "task_id": task_id,
        "governor": governor,
        "status": status,
        "source": "mission_protocol_progress_events",
        "chat_independent": True,
        "brigade_tabs": brigade_tabs,
        "progress_events": mission_events,
        "protocol_activity_cards": protocol_cards,
        "summary_activity_cards": summary_cards,
        "diagnostic_summary_cards": summary_cards,
        "entries": entries,
        "activity_cards": entries,
        "final_report": summary_cards[-1] if summary_cards else {},
        "log_text": "",
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
    native_adapter = native_adapter_for_run(run_dir, declared=True)
    if native_adapter is not None:
        ledger, ledger_error = load_ledger_dict(run_dir / "task_ledger.json")
        if ledger_error:
            return {"ok": False, "error": ledger_error}
        mission = (
            ledger.get(native_adapter.ledger_mission_key)
            if isinstance(ledger.get(native_adapter.ledger_mission_key), dict)
            else {}
        )
        service_mission_id = str(mission.get("id") or "")
        task: dict[str, Any] = {
            "step_id": native_adapter.step_id,
            "worker": native_adapter.backend,
            "port": native_adapter.service_port,
            "task_id": service_mission_id,
            "status": str(mission.get("status") or "not_started"),
            "request_sha256": str(mission.get("request_sha256") or ""),
        }
        if include_health and service_mission_id:
            try:
                if native_adapter.backend == "ResearchWarband":
                    # Port 7201 is a bearer-authenticated exact-origin boundary.
                    # Keep credentials and identity validation inside its bridge
                    # instead of constructing a bare inspection URL in a view.
                    from .research_warband_bridge import (
                        inspect_research_warband_mission,
                    )

                    payload = inspect_research_warband_mission(
                        service_mission_id,
                        str(mission.get("request_sha256") or ""),
                        timeout_sec=1.0,
                    )
                else:
                    endpoint = (
                        f"http://{host}:{native_adapter.service_port}/missions/"
                        f"{quote(service_mission_id, safe='')}"
                    )
                    with urllib.request.urlopen(endpoint, timeout=1.0) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                task["runtime"] = payload if isinstance(payload, dict) else {"ok": False, "error": "mission response is not a JSON object"}
            except Exception as exc:  # noqa: BLE001 - mission lookup is best-effort.
                task["runtime"] = {"ok": False, "error": str(exc)}
        return {"ok": True, "native": True, "worker_tasks": [task]}
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
    summary = run_summary(run_dir)
    state_view = mission_state_view(summary, active=active)
    summary["mission_state"] = state_view
    payload: dict[str, Any] = {
        "ok": True,
        "task_id": task_id,
        "summary": summary,
        "active": active,
        "mission_state": state_view,
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
    state_view = mission_state_view(summary, active=bool(snapshot.get("active")), phase=str(view.get("phase") or ""))
    if isinstance(snapshot.get("summary"), dict):
        snapshot["summary"]["mission_state"] = state_view
    snapshot["mission_state"] = state_view
    # The fighter's plain-language steps, extracted so upstream (Core, the app)
    # can show what the worker is actually doing without parsing display cards.
    worker_steps = [
        {"text": str(ev.get("detail") or "").strip(), "at": str(ev.get("at") or "")}
        for ev in snapshot.get("display_events", [])
        if isinstance(ev, dict) and ev.get("type") == "skitarii_step"
        and str(ev.get("detail") or "").strip()
    ]
    return {
        "ok": True,
        "task_id": run_dir.name,
        "phase": view["phase"],
        "status": view["status"],
        "mission_state": state_view,
        "active": view["active"],
        "decision": view["decision"],
        "display": view["display"],
        "display_events": snapshot.get("display_events", []),
        "worker_steps": worker_steps,
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
