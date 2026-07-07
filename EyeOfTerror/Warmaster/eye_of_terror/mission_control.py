from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from EyeOfTerror.common_protocol import (
    LIFECYCLE_STATUSES,
    acceptance_review,
    append_progress_event,
    commander_order,
    final_response,
    governor_plan_from_contract,
    governor_report,
    mission_intake,
    progress_event,
    revision_order,
    validate_protocol_payload,
    worker_report,
)
from EyeOfTerror.model_brain import request_model_decision

from .command_text import task_text_from_commander_order
from .ledger import TaskLedger
from .routing import route_message


def mission_id_for(task_id: str | None, message: str) -> str:
    if task_id:
        safe_task_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", task_id.strip()).strip("-")
        if safe_task_id:
            return f"mission-{safe_task_id}"[:128]
    digest = hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]
    return f"mission-{digest}"


def mission_root_for(warmaster_root: Path) -> Path:
    return warmaster_root / "missions"


def mission_dir_for(warmaster_root: Path, mission_id: str) -> Path:
    root = mission_root_for(warmaster_root).resolve()
    target = (root / mission_id).resolve()
    if target != root and root not in target.parents:
        raise ValueError("mission_id resolved outside mission root")
    return target


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    elif "{" in stripped and "}" in stripped:
        stripped = stripped[stripped.find("{") : stripped.rfind("}") + 1]
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("model response is not a JSON object")
    return parsed


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _next_numbered_path(directory: Path, prefix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    existing = sorted(directory.glob(f"{prefix}-*.json"))
    return directory / f"{prefix}-{len(existing) + 1:03d}.json"


def _mission_identity(mission_dir: Path, mission: dict[str, Any]) -> str:
    return str(mission.get("mission_id") or mission_dir.name)


def record_mission_state(
    mission_dir: Path,
    status: str,
    *,
    run_status: str = "",
    active: bool = False,
    phase: str = "",
) -> dict[str, Any]:
    if status not in LIFECYCLE_STATUSES:
        raise ValueError(f"unknown mission lifecycle status: {status}")
    mission = _read_json(mission_dir / "mission.json")
    mission_id = _mission_identity(mission_dir, mission)
    mission["mission_id"] = mission_id
    mission["status"] = status
    _write_json(mission_dir / "mission.json", mission)
    intake = _read_json(mission_dir / "mission_intake.json")
    command = _read_json(mission_dir / "commander_order.json")
    state = mission_state_projection(mission_id, mission, intake, command)
    state["run_status"] = run_status
    state["phase"] = phase or status
    state["active"] = bool(active)
    _write_json(mission_dir / "mission_state.json", state)
    return state


def commander_order_prompt_payload(message: str, route: dict[str, Any], mission_id: str) -> dict[str, Any]:
    return {
        "mission_id": mission_id,
        "user_request": message,
        "route": route,
        "required_json_schema": {
            "commander_intent": "short command intent, not a detailed brigade plan",
            "primary_goal": "one concrete goal",
            "success_conditions": ["conditions Warmaster will use for acceptance"],
            "constraints": ["hard limits or user preferences"],
            "escalate_to_user_if": ["only true user-decision blockers"],
        },
    }


def build_commander_order(message: str, mission_id: str) -> dict[str, Any]:
    route = route_message(message)
    route_payload = route.to_dict()
    if not route.ok:
        return {
            "ok": False,
            "error": route.reason,
            "error_code": route.error_code or "route_failed",
            "mission_id": mission_id,
            "route": route_payload,
        }
    model_decision = request_model_decision(
        "WarmasterCommander",
        "Commander intake and mission framing",
        commander_order_prompt_payload(message, route_payload, mission_id),
        layer="command",
        instructions=(
            "Return one strict JSON object and nothing else. Do not create a detailed brigade work plan. "
            "Warmaster's job is to define command intent, success conditions, constraints, and true escalation "
            "conditions. The assigned governor will plan the domain work. Revisions are internal and must not be "
            "reported to the user as final answers."
        ),
    )
    if not model_decision.get("ok"):
        return {
            "ok": False,
            "error": "WarmasterCommander model brain unavailable",
            "error_code": "commander_model_unavailable",
            "mission_id": mission_id,
            "route": route_payload,
            "model_brain": model_decision,
        }
    try:
        payload = _extract_json_object(str(model_decision.get("content") or ""))
        order = commander_order(
            mission_id,
            to=route.governor,
            supporting_governors=[item.get("name") for item in route.supporting_governors if isinstance(item, dict)],
            user_request=message,
            commander_intent=str(payload.get("commander_intent") or "").strip(),
            primary_goal=str(payload.get("primary_goal") or "").strip(),
            success_conditions=payload.get("success_conditions") if isinstance(payload.get("success_conditions"), list) else [],
            constraints=payload.get("constraints") if isinstance(payload.get("constraints"), list) else [],
            escalate_to_user_if=payload.get("escalate_to_user_if") if isinstance(payload.get("escalate_to_user_if"), list) else [],
        )
        validate_protocol_payload(order, expected_type="commander_order")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "error": f"WarmasterCommander returned invalid commander_order: {exc}",
            "error_code": "invalid_commander_order",
            "mission_id": mission_id,
            "route": route_payload,
            "model_brain": model_decision,
        }
    return {
        "ok": True,
        "mission_id": mission_id,
        "route": route_payload,
        "commander_order": order,
        "model_brain": model_decision,
    }


def governor_task_from_order(order: dict[str, Any]) -> str:
    return task_text_from_commander_order(order)


def open_mission(warmaster_root: Path, message: str, task_id: str | None, source_channel: str = "main_chat") -> dict[str, Any]:
    mission_id = mission_id_for(task_id, message)
    mission_dir = mission_dir_for(warmaster_root, mission_id)
    intake = mission_intake(mission_id, message, source_channel=source_channel)
    validate_protocol_payload(intake, expected_type="mission_intake")
    commander = build_commander_order(message, mission_id)
    if not commander.get("ok"):
        mission_dir.mkdir(parents=True, exist_ok=True)
        _write_json(
            mission_dir / "mission.json",
            {
                "mission_id": mission_id,
                "task_id": task_id or "",
                "status": "failed",
                "assigned_governor": "",
                "source_channel": source_channel,
            },
        )
        _write_json(mission_dir / "mission_intake.json", intake)
        _write_json(mission_dir / "commander_error.json", commander)
        record_mission_state(mission_dir, "failed")
        append_progress_event(
            mission_dir / "progress_events.jsonl",
            progress_event(
                mission_id,
                actor="Warmaster",
                role="commander",
                phase="intake",
                status="failed",
                title="Не удалось сформировать приказ",
                body=str(commander.get("error") or "Командный intake завершился ошибкой."),
            ),
        )
        return commander
    order = commander["commander_order"]
    mission = {
        "mission_id": mission_id,
        "task_id": task_id or "",
        "status": "assigned",
        "assigned_governor": order["to"],
        "source_channel": source_channel,
    }
    _write_json(mission_dir / "mission.json", mission)
    _write_json(mission_dir / "mission_intake.json", intake)
    _write_json(mission_dir / "commander_order.json", order)
    _write_json(mission_dir / "route.json", commander.get("route", {}))
    record_mission_state(mission_dir, "assigned")
    append_progress_event(
        mission_dir / "progress_events.jsonl",
        progress_event(
            mission_id,
            actor="Warmaster",
            role="commander",
            phase="assigned",
            status="done",
            title=f"Назначен бригадир {order['to']}",
            body=f"Сформирован приказ: {order['primary_goal']}",
        ),
    )
    return {
        **commander,
        "mission": mission,
        "mission_dir": str(mission_dir),
        "governor_task": governor_task_from_order(order),
    }


def record_governor_plan(run_dir: Path, mission_id: str, mission_dir: Path) -> dict[str, Any]:
    command = _read_json(mission_dir / "commander_order.json")
    plan = _read_json(run_dir / "governor_plan.json")
    if plan:
        plan["mission_id"] = mission_id
        if str(plan.get("understanding") or "").strip().startswith("ПРИКАЗ ВАРМАСТЕРА"):
            plan["understanding"] = str(command.get("primary_goal") or command.get("commander_intent") or plan.get("understanding") or "").strip()
        validate_protocol_payload(plan, expected_type="governor_plan")
    else:
        contract = _read_json(run_dir / "contract.json")
        if not contract:
            return {"ok": False, "error": "contract.json is missing"}
        plan = governor_plan_from_contract(mission_id, contract, command)
        validate_protocol_payload(plan, expected_type="governor_plan")
    _write_json(mission_dir / "governor_plan.json", plan)
    _write_json(_next_numbered_path(mission_dir / "governor_plans", "governor_plan"), plan)
    mission = _read_json(mission_dir / "mission.json")
    if mission:
        record_mission_state(mission_dir, "planning")
    append_progress_event(
        mission_dir / "progress_events.jsonl",
        progress_event(
            mission_id,
            actor=str(plan.get("governor") or "Governor"),
            role="governor",
            phase="planning",
            status="done",
            title="Бригадир составил план",
            body=f"Шагов: {len(plan.get('work_plan') if isinstance(plan.get('work_plan'), list) else [])}. Цель: {str(plan.get('understanding') or '').strip()}",
        ),
    )
    return {"ok": True, "governor_plan": plan}


def link_run_to_mission(run_dir: Path, mission: dict[str, Any]) -> None:
    if not mission.get("ok"):
        return
    raw_mission_dir = str(mission.get("mission_dir") or "")
    if raw_mission_dir:
        Path(raw_mission_dir).mkdir(parents=True, exist_ok=True)
    payload = {
        "mission_id": str(mission.get("mission_id") or ""),
        "mission_dir": raw_mission_dir,
        "assigned_governor": str((mission.get("commander_order") or {}).get("to") or ""),
    }
    _write_json(run_dir / "mission_ref.json", payload)
    sync_dispatch_worker_orders(run_dir, payload["mission_id"])
    if raw_mission_dir:
        record_governor_plan(run_dir, payload["mission_id"], Path(raw_mission_dir))
        record_worker_orders(run_dir, payload["mission_id"], Path(raw_mission_dir))


def sync_dispatch_worker_orders(run_dir: Path, mission_id: str) -> None:
    dispatch_dir = run_dir / "dispatch"
    if not dispatch_dir.exists():
        return
    for dispatch_path in dispatch_dir.glob("*.json"):
        packet = _read_json(dispatch_path)
        if not packet:
            continue
        order = packet.get("worker_order") if isinstance(packet.get("worker_order"), dict) else {}
        request = packet.get("request") if isinstance(packet.get("request"), dict) else {}
        request_order = request.get("worker_order") if isinstance(request.get("worker_order"), dict) else {}
        if order:
            order["mission_id"] = mission_id
            packet["worker_order"] = order
        if request_order:
            request_order["mission_id"] = mission_id
            request["worker_order"] = request_order
            packet["request"] = request
        _write_json(dispatch_path, packet)


def record_worker_orders(run_dir: Path, mission_id: str, mission_dir: Path) -> dict[str, Any]:
    dispatch_dir = run_dir / "dispatch"
    if not dispatch_dir.exists():
        return {"ok": False, "error": "dispatch directory is missing", "count": 0}
    count = 0
    for dispatch_path in sorted(dispatch_dir.glob("*.json")):
        packet = _read_json(dispatch_path)
        order = packet.get("worker_order") if isinstance(packet.get("worker_order"), dict) else {}
        if not order:
            continue
        order = dict(order)
        order["mission_id"] = mission_id
        validate_protocol_payload(order, expected_type="worker_order")
        step_id = str(order.get("step_id") or dispatch_path.stem)
        _write_json(mission_dir / "worker_orders" / f"worker_order-{step_id}.json", order)
        count += 1
        append_progress_event(
            mission_dir / "progress_events.jsonl",
            progress_event(
                mission_id,
                actor=str(order.get("from") or "Governor"),
                role="governor",
                phase="executing",
                status="started",
                title=f"Выдан приказ воркеру {order.get('to')}",
                body=f"Шаг {step_id}: {order.get('task')}",
            ),
        )
    mission = _read_json(mission_dir / "mission.json")
    if mission and count:
        record_mission_state(mission_dir, "plan_review")
    return {"ok": count > 0, "count": count}


def record_worker_execution_started(run_dir: Path, packet: dict[str, Any]) -> None:
    ref = mission_ref_for_run(run_dir)
    mission_dir = mission_dir_from_ref(ref)
    if not mission_dir:
        return
    order = packet.get("worker_order") if isinstance(packet.get("worker_order"), dict) else {}
    if not order:
        return
    mission_id = str(ref.get("mission_id") or order.get("mission_id") or mission_dir.name)
    step_id = str(order.get("step_id") or packet.get("step_id") or "")
    worker = str(order.get("to") or packet.get("worker") or "Worker")
    mission = _read_json(mission_dir / "mission.json")
    if mission:
        record_mission_state(mission_dir, "executing", active=True)
    append_progress_event(
        mission_dir / "progress_events.jsonl",
        progress_event(
            mission_id,
            actor=worker,
            role="worker",
            phase="executing",
            status="running",
            title=f"Воркер начал шаг {step_id}",
            body=str(order.get("task") or packet.get("purpose") or ""),
        ),
    )


def mission_ref_for_run(run_dir: Path) -> dict[str, Any]:
    return _read_json(run_dir / "mission_ref.json")


def mission_dir_from_ref(ref: dict[str, Any]) -> Path | None:
    raw = str(ref.get("mission_dir") or "")
    if not raw:
        return None
    path = Path(raw).resolve()
    if not path.exists():
        return None
    return path


def worker_for_step(run_dir: Path, step_id: str) -> str:
    status = _read_json(run_dir / "status.json")
    for step in status.get("steps", []) if isinstance(status.get("steps"), list) else []:
        if isinstance(step, dict) and str(step.get("step_id") or "") == step_id:
            return str(step.get("worker") or "")
    try:
        ledger = TaskLedger.load(run_dir / "task_ledger.json").to_dict()
    except Exception:
        ledger = {}
    for step in ledger.get("steps", []) if isinstance(ledger.get("steps"), list) else []:
        if isinstance(step, dict) and str(step.get("step_id") or "") == step_id:
            return str(step.get("worker") or "")
    return ""


def revision_source_step(run_dir: Path, result: dict[str, Any]) -> str:
    oversight = _read_json(run_dir / "oversight.json")
    policy = oversight.get("revision_policy") if isinstance(oversight.get("revision_policy"), dict) else {}
    source_step = str(policy.get("source_step") or "").strip()
    if source_step:
        return source_step
    final_step = str(result.get("final_step") or "").strip()
    if final_step:
        return final_step
    status = _read_json(run_dir / "status.json")
    steps = status.get("steps") if isinstance(status.get("steps"), list) else []
    if steps and isinstance(steps[-1], dict):
        return str(steps[-1].get("step_id") or "finalize")
    return "finalize"


def revision_plan_from_acceptance(run_dir: Path, result: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    step_id = revision_source_step(run_dir, result)
    worker = worker_for_step(run_dir, step_id)
    required_revision = review.get("required_revision") if isinstance(review.get("required_revision"), dict) else {}
    return {
        "required": True,
        "focused_context": {
            "warmaster_acceptance": review,
            "previous_result_summary": str(result.get("summary") or ""),
        },
        "steps": [
            {
                "step_id": step_id,
                "worker": worker,
                "reason": str(review.get("reason") or required_revision.get("order") or "Warmaster acceptance rejected the result."),
                "source": "warmaster_acceptance",
                "priority": "blocker",
            }
        ],
    }


def worker_report_from_payload(mission_id: str, step_id: str, worker: str, payload: dict[str, Any], ok: bool) -> dict[str, Any]:
    raw_status = str(payload.get("status") or "").strip().lower()
    if raw_status in {"blocked"}:
        status = "blocked"
    elif raw_status in {"needs_revision"} or (isinstance(payload.get("revision_plan"), dict) and payload.get("revision_plan", {}).get("required")):
        status = "needs_revision"
    elif ok and raw_status in {"ready", "completed", "passed", "passed_with_warnings", "done"}:
        status = "done"
    elif ok and not raw_status:
        status = "done"
    else:
        status = "failed"
    report = worker_report(
        mission_id,
        step_id=step_id,
        worker=worker,
        status=status,
        summary=str(payload.get("summary") or payload.get("error") or raw_status or "Worker step finished."),
        artifacts=[str(item) for item in payload.get("artifacts", [])] if isinstance(payload.get("artifacts"), list) else [],
        problems=[str(item) for item in payload.get("problems", [])] if isinstance(payload.get("problems"), list) else [],
        next_recommended_action=str(payload.get("next_recommended_action") or payload.get("next_action") or ""),
    )
    validate_protocol_payload(report, expected_type="worker_report")
    return report


def record_worker_protocol_report(run_dir: Path, report: dict[str, Any]) -> None:
    ref = mission_ref_for_run(run_dir)
    mission_dir = mission_dir_from_ref(ref)
    if not mission_dir:
        return
    mission_id = str(ref.get("mission_id") or report.get("mission_id") or mission_dir.name)
    report = dict(report)
    report["mission_id"] = mission_id
    validate_protocol_payload(report, expected_type="worker_report")
    step_id = str(report.get("step_id") or "step")
    _write_json(_next_numbered_path(mission_dir / "worker_reports", f"worker_report-{step_id}"), report)
    mission = _read_json(mission_dir / "mission.json")
    if mission:
        if report.get("status") == "failed":
            status = "failed"
        elif report.get("status") in {"blocked", "needs_revision"}:
            status = "revision" if report.get("status") == "needs_revision" else "blocked"
        else:
            status = "executing"
        record_mission_state(mission_dir, status, active=status in {"executing", "revision"})
    phase = "executing"
    event_status = "done"
    if report.get("status") in {"blocked", "needs_revision"}:
        phase = "revising" if report.get("status") == "needs_revision" else "blocked"
        event_status = "blocked"
    elif report.get("status") == "failed":
        phase = "failed"
        event_status = "failed"
    append_progress_event(
        mission_dir / "progress_events.jsonl",
        progress_event(
            mission_id,
            actor=str(report.get("worker") or "Worker"),
            role="worker",
            phase=phase,
            status=event_status,
            title=f"Шаг {step_id}: {report.get('status')}",
            body=str(report.get("summary") or ""),
        ),
    )


def governor_report_from_run(run_dir: Path, mission_id: str) -> dict[str, Any]:
    from .artifacts import final_manifest_summary

    ledger = TaskLedger.load(run_dir / "task_ledger.json").to_dict()
    result = ledger.get("result") if isinstance(ledger.get("result"), dict) else {}
    manifest = final_manifest_summary(result)
    revision_plan = result.get("revision_plan") if isinstance(result.get("revision_plan"), dict) else {"required": False, "steps": []}
    result_status = str(result.get("status") or "").strip().lower()
    if revision_plan.get("required") or result_status in {"needs_revision", "blocked"}:
        status = "needs_revision" if result_status != "blocked" else "blocked"
    elif bool(result.get("ok")) or result_status in {"ready", "completed", "passed", "passed_with_warnings"}:
        status = "ready"
    else:
        status = "failed"
    quality_checks = [
        {"name": "result_ok", "ok": bool(result.get("ok"))},
        {"name": "no_revision_required", "ok": not bool(revision_plan.get("required"))},
    ]
    if manifest:
        quality_checks.append({"name": "final_manifest_status", "ok": str(manifest.get("status") or "") in {"ready", "completed", "passed"}, "value": manifest.get("status", "")})
        quality_checks.append({"name": "final_manifest_blockers", "ok": int(manifest.get("blocker_count") or 0) == 0, "value": manifest.get("blocker_count", 0)})
    report = governor_report(
        mission_id,
        governor=str(ledger.get("governor") or ""),
        status=status,
        summary=str(result.get("summary") or ""),
        deliverables=[str(item) for item in result.get("artifacts", [])] if isinstance(result.get("artifacts"), list) else [],
        quality_review={
            "passed": status == "ready",
            "checks": quality_checks,
            "final_manifest_summary": manifest,
        },
        revision_plan=revision_plan,
        user_facing_answer=str(result.get("summary") or ""),
    )
    validate_protocol_payload(report, expected_type="governor_report")
    return report


def acceptance_prompt_payload(command: dict[str, Any], report: dict[str, Any], ledger_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "commander_order": command,
        "governor_report": report,
        "ledger_result": ledger_result,
        "required_json_schema": {
            "accepted": "boolean",
            "reason": "short concrete acceptance or rejection reason",
            "escalate_to_user": "boolean, true only for real user choice/access blocker",
            "required_revision": {
                "order": "revision order for the governor if accepted=false and escalate_to_user=false",
                "required_steps": ["optional existing pipeline step ids to prefer"],
            },
        },
    }


def build_acceptance_review(command: dict[str, Any], report: dict[str, Any], ledger_result: dict[str, Any]) -> dict[str, Any]:
    report_status = str(report.get("status") or "").strip()
    if report_status == "needs_revision":
        review = acceptance_review(
            str(report.get("mission_id") or ""),
            accepted=False,
            reason="Бригадир вернул внутренний needs_revision; пользовательский финал запрещен до доработки.",
            required_revision={
                "to": str(report.get("governor") or ""),
                "order": "Выполнить внутреннюю ревизию по governor_report.revision_plan и повторно передать отчет Вармастеру.",
                "required_steps": [],
            },
            escalate_to_user=False,
        )
        validate_protocol_payload(review, expected_type="acceptance_review")
        return {"ok": True, "acceptance_review": review, "model_brain": {"status": "skipped", "reason": "governor_report.status=needs_revision"}}
    if report_status == "blocked":
        review = acceptance_review(
            str(report.get("mission_id") or ""),
            accepted=False,
            reason="Бригадир заблокировал задачу; требуется решение или внешний ресурс пользователя.",
            required_revision={"to": str(report.get("governor") or ""), "order": "Ожидать решения пользователя по блокеру.", "required_steps": []},
            escalate_to_user=True,
        )
        validate_protocol_payload(review, expected_type="acceptance_review")
        return {"ok": True, "acceptance_review": review, "model_brain": {"status": "skipped", "reason": "governor_report.status=blocked"}}
    model_decision = request_model_decision(
        "WarmasterAcceptance",
        "Final acceptance authority over governor reports",
        acceptance_prompt_payload(command, report, ledger_result),
        layer="command",
        instructions=(
            "Return one strict JSON object and nothing else. Decide whether the governor report satisfies the commander_order. "
            "Do not accept internal needs_revision/blocker reports as final user answers. Reject shallow or incomplete results. "
            "Set escalate_to_user=true only when a real user decision, missing access, or external impossibility blocks progress."
        ),
    )
    if not model_decision.get("ok"):
        review = acceptance_review(
            str(report.get("mission_id") or ""),
            accepted=False,
            reason="WarmasterAcceptance model brain unavailable.",
            required_revision={"to": str(report.get("governor") or ""), "order": "Повторить приемку после восстановления модели."},
            escalate_to_user=True,
        )
        return {"ok": False, "acceptance_review": review, "model_brain": model_decision, "error_code": "acceptance_model_unavailable"}
    try:
        parsed = _extract_json_object(str(model_decision.get("content") or ""))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        review = acceptance_review(
            str(report.get("mission_id") or ""),
            accepted=False,
            reason=f"WarmasterAcceptance returned invalid JSON: {exc}",
            required_revision={"to": str(report.get("governor") or ""), "order": "Повторить приемку с валидным JSON-решением."},
            escalate_to_user=True,
        )
        return {"ok": False, "acceptance_review": review, "model_brain": model_decision, "error_code": "invalid_acceptance_json"}
    required_revision = parsed.get("required_revision") if isinstance(parsed.get("required_revision"), dict) else {}
    review = acceptance_review(
        str(report.get("mission_id") or ""),
        accepted=bool(parsed.get("accepted")),
        reason=str(parsed.get("reason") or "").strip() or "Warmaster acceptance decision recorded.",
        required_revision={
            "to": str(report.get("governor") or ""),
            "order": str(required_revision.get("order") or parsed.get("reason") or "Доработать результат по условиям приемки."),
            "required_steps": required_revision.get("required_steps") if isinstance(required_revision.get("required_steps"), list) else [],
        },
        escalate_to_user=bool(parsed.get("escalate_to_user")),
    )
    validate_protocol_payload(review, expected_type="acceptance_review")
    return {"ok": True, "acceptance_review": review, "model_brain": model_decision}


def record_warmaster_acceptance(run_dir: Path) -> dict[str, Any]:
    ref = mission_ref_for_run(run_dir)
    mission_dir = mission_dir_from_ref(ref)
    if not mission_dir:
        return {"ok": True, "skipped": True, "reason": "run has no mission_ref"}
    mission_id = str(ref.get("mission_id") or mission_dir.name)
    existing_final = _read_json(mission_dir / "final_response.json")
    existing_mission = _read_json(mission_dir / "mission.json")
    if existing_final and existing_mission.get("status") == "completed":
        return {"ok": True, "accepted": True, "already_recorded": True, "final_response": existing_final}
    command = _read_json(mission_dir / "commander_order.json")
    ledger = TaskLedger.load(run_dir / "task_ledger.json")
    result = ledger.to_dict().get("result") if isinstance(ledger.to_dict().get("result"), dict) else {}
    report = governor_report_from_run(run_dir, mission_id)
    mission = _read_json(mission_dir / "mission.json")
    if mission:
        record_mission_state(mission_dir, "governor_review", active=True)
    _write_json(mission_dir / "governor_report.json", report)
    _write_json(_next_numbered_path(mission_dir / "governor_reports", "governor_report"), report)
    append_progress_event(
        mission_dir / "progress_events.jsonl",
        progress_event(
            mission_id,
            actor=str(report.get("governor") or "Governor"),
            role="governor",
            phase="reviewing",
            status="done" if report.get("status") == "ready" else "blocked",
            title="Бригадир передал финальный отчет",
            body=str(report.get("summary") or report.get("status") or ""),
        ),
    )
    decision = build_acceptance_review(command, report, result)
    review = decision["acceptance_review"]
    mission = _read_json(mission_dir / "mission.json")
    if mission:
        record_mission_state(mission_dir, "warmaster_acceptance", active=True)
    _write_json(mission_dir / "acceptance_review.json", review)
    _write_json(_next_numbered_path(mission_dir / "acceptance_reviews", "acceptance_review"), review)
    ledger.record_event("warmaster_acceptance_recorded", {"accepted": bool(review.get("accepted")), "status": review.get("status"), "reason": review.get("reason")})
    if review.get("accepted"):
        final = final_response(mission_id, "completed", str(report.get("user_facing_answer") or report.get("summary") or "Задача выполнена."), artifacts=report.get("deliverables") if isinstance(report.get("deliverables"), list) else [])
        validate_protocol_payload(final, expected_type="final_response")
        _write_json(mission_dir / "final_response.json", final)
        record_mission_state(mission_dir, "completed")
        append_progress_event(
            mission_dir / "progress_events.jsonl",
            progress_event(mission_id, "Warmaster", "commander", "completed", "done", "Финал принят", str(review.get("reason") or "Результат принят.")),
        )
        return {"ok": True, "accepted": True, "governor_report": report, "acceptance_review": review, "decision": decision}
    if review.get("escalate_to_user"):
        record_mission_state(mission_dir, "blocked")
        ledger.force_status("blocked", reason=str(review.get("reason") or "Warmaster acceptance requires user escalation."))
        append_progress_event(
            mission_dir / "progress_events.jsonl",
            progress_event(mission_id, "Warmaster", "commander", "blocked", "blocked", "Нужна эскалация", str(review.get("reason") or "")),
        )
        return {"ok": False, "accepted": False, "blocked": True, "governor_report": report, "acceptance_review": review, "decision": decision}
    rev_order_payload = revision_order(
        mission_id,
        to=str(report.get("governor") or ""),
        reason=str(review.get("reason") or "Warmaster rejected the result."),
        order=str((review.get("required_revision") or {}).get("order") if isinstance(review.get("required_revision"), dict) else "Доработать результат."),
        required_steps=(review.get("required_revision") or {}).get("required_steps") if isinstance(review.get("required_revision"), dict) and isinstance((review.get("required_revision") or {}).get("required_steps"), list) else [],
    )
    validate_protocol_payload(rev_order_payload, expected_type="revision_order")
    _write_json(mission_dir / "revision_order.json", rev_order_payload)
    _write_json(_next_numbered_path(mission_dir / "revision_orders", "revision_order"), rev_order_payload)
    revision_plan = revision_plan_from_acceptance(run_dir, result, review)
    updated_result = dict(result)
    updated_result.update(
        {
            "ok": False,
            "status": "needs_revision",
            "summary": str(review.get("reason") or "Warmaster rejected the result and ordered revision."),
            "revision_plan": revision_plan,
            "warmaster_acceptance": review,
        }
    )
    ledger.set_result(updated_result)
    ledger.force_status("needs_revision", reason="Warmaster rejected completed run and ordered internal revision.")
    record_mission_state(mission_dir, "revision", active=True)
    append_progress_event(
        mission_dir / "progress_events.jsonl",
        progress_event(mission_id, "Warmaster", "commander", "revising", "running", "Назначена ревизия", str(review.get("reason") or "")),
    )
    return {"ok": False, "accepted": False, "revision_required": True, "governor_report": report, "acceptance_review": review, "revision_order": rev_order_payload, "revision_plan": revision_plan, "decision": decision}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _read_events(path: Path, limit: int = 100) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events[-max(0, limit) :]


def _read_json_dir(directory: Path, limit: int = 100) -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        payload = _read_json(path)
        if payload:
            payload.setdefault("_path", str(path))
            items.append(payload)
    return items[-max(0, limit) :]


def _count_by_key(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "").strip()
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def mission_next_owner(status: str) -> str:
    if status in {"completed", "failed", "cancelled"}:
        return "none"
    if status == "blocked":
        return "user_or_operator"
    if status in {"created", "intake", "assigned", "warmaster_acceptance"}:
        return "Warmaster"
    return "governor"


def mission_user_visible_state(status: str) -> str:
    if status == "completed":
        return "final_ready"
    if status == "failed":
        return "failed"
    if status == "cancelled":
        return "cancelled"
    if status == "blocked":
        return "needs_user_or_operator_decision"
    if status in {"executing", "governor_review", "warmaster_acceptance", "revision"}:
        return "working"
    return "accepted"


def mission_state_projection(mission_id: str, mission: dict[str, Any], intake: dict[str, Any], command: dict[str, Any]) -> dict[str, Any]:
    status = str(mission.get("status") or intake.get("status") or "created")
    if status not in LIFECYCLE_STATUSES:
        status = "created"
    return {
        "kind": "mission_state",
        "mission_id": mission_id,
        "task_id": str(mission.get("task_id") or ""),
        "status": status,
        "run_status": "",
        "mission_status": status,
        "phase": status,
        "active": False,
        "assigned_governor": str(mission.get("assigned_governor") or command.get("to") or ""),
        "next_owner": mission_next_owner(status),
        "user_visible_state": mission_user_visible_state(status),
        "revision_is_internal": True,
        "source": "mission_protocol",
    }


def mission_protocol_summary(mission_dir: Path) -> dict[str, Any]:
    worker_orders = _read_json_dir(mission_dir / "worker_orders", limit=1000)
    worker_reports = _read_json_dir(mission_dir / "worker_reports", limit=1000)
    governor_reports = _read_json_dir(mission_dir / "governor_reports", limit=1000)
    acceptance_reviews = _read_json_dir(mission_dir / "acceptance_reviews", limit=1000)
    revision_orders = _read_json_dir(mission_dir / "revision_orders", limit=1000)
    progress_events = _read_events(mission_dir / "progress_events.jsonl", limit=10000)
    return {
        "has_mission_state": bool(_read_json(mission_dir / "mission_state.json")),
        "has_mission_intake": bool(_read_json(mission_dir / "mission_intake.json")),
        "has_commander_order": bool(_read_json(mission_dir / "commander_order.json")),
        "has_governor_plan": bool(_read_json(mission_dir / "governor_plan.json")),
        "progress_event_count": len(progress_events),
        "progress_event_roles": _count_by_key(progress_events, "role"),
        "progress_event_phases": _count_by_key(progress_events, "phase"),
        "progress_event_statuses": _count_by_key(progress_events, "status"),
        "latest_progress_event": progress_events[-1] if progress_events else {},
        "worker_order_count": len(worker_orders),
        "worker_report_count": len(worker_reports),
        "governor_report_count": len(governor_reports),
        "acceptance_review_count": len(acceptance_reviews),
        "revision_order_count": len(revision_orders),
        "has_governor_report": bool(_read_json(mission_dir / "governor_report.json")),
        "has_acceptance_review": bool(_read_json(mission_dir / "acceptance_review.json")),
        "has_revision_order": bool(_read_json(mission_dir / "revision_order.json")),
        "has_final_response": bool(_read_json(mission_dir / "final_response.json")),
    }


def mission_state(warmaster_root: Path, mission_id: str, event_limit: int = 100) -> dict[str, Any]:
    mission_dir = mission_dir_for(warmaster_root, mission_id)
    if not mission_dir.exists():
        raise FileNotFoundError(mission_id)
    progress_events = _read_events(mission_dir / "progress_events.jsonl", limit=event_limit)
    mission = _read_json(mission_dir / "mission.json")
    intake = _read_json(mission_dir / "mission_intake.json")
    command = _read_json(mission_dir / "commander_order.json")
    state = _read_json(mission_dir / "mission_state.json") or mission_state_projection(mission_id, mission, intake, command)
    return {
        "ok": True,
        "mission_id": mission_id,
        "mission_dir": str(mission_dir),
        "mission_state": state,
        "mission": mission,
        "durable_mission_state": _read_json(mission_dir / "mission_state.json"),
        "mission_intake": intake,
        "commander_order": command,
        "governor_plan": _read_json(mission_dir / "governor_plan.json"),
        "route": _read_json(mission_dir / "route.json"),
        "commander_error": _read_json(mission_dir / "commander_error.json"),
        "worker_orders": _read_json_dir(mission_dir / "worker_orders", limit=event_limit),
        "worker_reports": _read_json_dir(mission_dir / "worker_reports", limit=event_limit),
        "governor_report": _read_json(mission_dir / "governor_report.json"),
        "governor_reports": _read_json_dir(mission_dir / "governor_reports", limit=event_limit),
        "acceptance_review": _read_json(mission_dir / "acceptance_review.json"),
        "acceptance_reviews": _read_json_dir(mission_dir / "acceptance_reviews", limit=event_limit),
        "revision_order": _read_json(mission_dir / "revision_order.json"),
        "revision_orders": _read_json_dir(mission_dir / "revision_orders", limit=event_limit),
        "final_response": _read_json(mission_dir / "final_response.json"),
        "progress_events": progress_events,
        "activity_cards": [
            {
                "actor": str(event.get("actor") or ""),
                "role": str(event.get("role") or ""),
                "phase": str(event.get("phase") or ""),
                "status": str(event.get("status") or ""),
                "title": str(event.get("title") or ""),
                "body": str(event.get("body") or ""),
                "created_at": str(event.get("created_at") or ""),
            }
            for event in progress_events
            if bool(event.get("visible_to_user", True))
        ],
        "protocol_summary": mission_protocol_summary(mission_dir),
    }


def list_missions(warmaster_root: Path, limit: int = 50) -> list[dict[str, Any]]:
    root = mission_root_for(warmaster_root)
    if not root.exists():
        return []
    items: list[dict[str, Any]] = []
    for mission_dir in sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: item.stat().st_mtime, reverse=True):
        mission = _read_json(mission_dir / "mission.json")
        intake = _read_json(mission_dir / "mission_intake.json")
        items.append(
            {
                "mission_id": mission_dir.name,
                "status": str(mission.get("status") or intake.get("status") or ""),
                "assigned_governor": str(mission.get("assigned_governor") or ""),
                "source_channel": str(mission.get("source_channel") or intake.get("source_channel") or ""),
                "user_request": str(intake.get("user_request") or ""),
                "mission_dir": str(mission_dir),
            }
        )
        if len(items) >= limit:
            break
    return items
