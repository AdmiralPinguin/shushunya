from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from EyeOfTerror.common_protocol import append_progress_event, commander_order, mission_intake, progress_event, validate_protocol_payload
from EyeOfTerror.model_brain import request_model_decision

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
    success = "\n".join(f"- {item}" for item in order.get("success_conditions", []) if isinstance(item, str))
    constraints = "\n".join(f"- {item}" for item in order.get("constraints", []) if isinstance(item, str))
    escalation = "\n".join(f"- {item}" for item in order.get("escalate_to_user_if", []) if isinstance(item, str))
    return (
        "ПРИКАЗ ВАРМАСТЕРА\n"
        f"Mission ID: {order['mission_id']}\n"
        f"Назначенный бригадир: {order['to']}\n\n"
        f"Исходный запрос пользователя:\n{order['user_request']}\n\n"
        f"Замысел командующего:\n{order['commander_intent']}\n\n"
        f"Главная цель:\n{order['primary_goal']}\n\n"
        f"Условия приемки Вармастером:\n{success or '- Выполнить исходную задачу и явно проверить качество.'}\n\n"
        f"Ограничения:\n{constraints or '- Не сдавать внутреннюю ревизию как финал пользователю.'}\n\n"
        f"Эскалация пользователю допускается только если:\n{escalation or '- Нужен реальный выбор пользователя или внешний доступ.'}\n\n"
        "Твоя зона ответственности: составить доменный план, управлять воркерами, ревизировать результат внутри отдела "
        "и вернуть Вармастеру структурированный финальный отчет. Не отвечай пользователю напрямую."
    )


def open_mission(warmaster_root: Path, message: str, task_id: str | None, source_channel: str = "main_chat") -> dict[str, Any]:
    mission_id = mission_id_for(task_id, message)
    mission_dir = mission_dir_for(warmaster_root, mission_id)
    intake = mission_intake(mission_id, message, source_channel=source_channel)
    validate_protocol_payload(intake, expected_type="mission_intake")
    commander = build_commander_order(message, mission_id)
    if not commander.get("ok"):
        mission_dir.mkdir(parents=True, exist_ok=True)
        _write_json(mission_dir / "mission_intake.json", intake)
        _write_json(mission_dir / "commander_error.json", commander)
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


def link_run_to_mission(run_dir: Path, mission: dict[str, Any]) -> None:
    if not mission.get("ok"):
        return
    payload = {
        "mission_id": str(mission.get("mission_id") or ""),
        "mission_dir": str(mission.get("mission_dir") or ""),
        "assigned_governor": str((mission.get("commander_order") or {}).get("to") or ""),
    }
    _write_json(run_dir / "mission_ref.json", payload)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    parsed = json.loads(path.read_text(encoding="utf-8"))
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


def mission_state(warmaster_root: Path, mission_id: str, event_limit: int = 100) -> dict[str, Any]:
    mission_dir = mission_dir_for(warmaster_root, mission_id)
    if not mission_dir.exists():
        raise FileNotFoundError(mission_id)
    return {
        "ok": True,
        "mission_id": mission_id,
        "mission_dir": str(mission_dir),
        "mission": _read_json(mission_dir / "mission.json"),
        "mission_intake": _read_json(mission_dir / "mission_intake.json"),
        "commander_order": _read_json(mission_dir / "commander_order.json"),
        "route": _read_json(mission_dir / "route.json"),
        "commander_error": _read_json(mission_dir / "commander_error.json"),
        "progress_events": _read_events(mission_dir / "progress_events.jsonl", limit=event_limit),
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
