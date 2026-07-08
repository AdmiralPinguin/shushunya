"""LLM-facing turn protocol for capability-aware chat decisions."""
from __future__ import annotations

import json
from typing import Any


TURN_ACTIONS = {
    "answer_in_chat",
    "ask_clarification",
    "issue_mission_order",
    "create_administratum_task",
}


GOVERNOR_CAPABILITIES = [
    {
        "governor": "IskandarKhayon",
        "department": "Scriptorium",
        "available": True,
        "task_kinds": ["research", "research_writing", "lore_reconstruction"],
        "responsibility": "research, source comparison, lore/event reconstruction, long-form synthesis, translation-backed source work",
    },
    {
        "governor": "Ceraxia",
        "department": "Mechanicum",
        "available": True,
        "task_kinds": ["code", "software_architecture", "repo_repair", "greenfield_project"],
        "responsibility": "software engineering, project creation, code repair, architecture, tests, repository work",
    },
    {
        "governor": "Moriana",
        "department": "Pictorium",
        "available": True,
        "task_kinds": ["image_generation", "image_series_generation", "comic_generation"],
        "responsibility": "image generation, drawing workflows, Stable Diffusion/Forge/Flux/SDXL jobs, comics, panels, visual series",
    },
]


def turn_capability_manifest(*, image_attached: bool = False) -> dict[str, Any]:
    return {
        "version": 1,
        "principle": (
            "Shushunya may speak and act only through the capabilities listed here. "
            "If an action is not listed, it does not exist for this turn."
        ),
        "current_input": {
            "image_attached": bool(image_attached),
        },
        "capabilities": [
            {
                "action": "answer_in_chat",
                "available": True,
                "description": "Immediate conversational answer in the shared chat.",
                "limits": [
                    "Cannot honestly claim that background work, search, file generation, monitoring, or brigade execution has started.",
                    "Can explain limits, answer from known context, or ask the user to launch/confirm a real workflow.",
                ],
            },
            {
                "action": "ask_clarification",
                "available": True,
                "description": "Ask one concise clarifying question when the next real action cannot be chosen safely.",
                "limits": ["Must not claim execution has started."],
            },
            {
                "action": "issue_mission_order",
                "available": True,
                "description": (
                    "Create a conscious mission order for EyeOfTerror. Shushunya must decide what work is being ordered, "
                    "which governor/department should own it, why that governor is appropriate, and what success means. "
                    "Warmaster is only the command gateway that records and executes this order."
                ),
                "server_effect": "ArchiveOfHeresy creates a real Warmaster task_id and starts the background pipeline.",
                "available_governors": GOVERNOR_CAPABILITIES,
                "required_fields": ["mission_order"],
                "limits": [
                    "Use only when the user is actually asking for work to be performed, not when they are merely discussing architecture or asking a question.",
                    "Do not hand work to Warmaster as an unknown black box. Select a target_governor and explain the selection.",
                    "For vague follow-up commands, recover the intended task from recent chat history and include that full recovered task in mission_order.user_request.",
                    "If the task lacks essential domain input, use ask_clarification instead of issuing a mission order. Examples: image generation without a subject/description, code work without a target repository or greenfield scope, research without a topic.",
                ],
            },
            {
                "action": "create_administratum_task",
                "available": True,
                "description": "Create reminders, todos, recurring routines, and watches through Administratum.",
                "server_effect": "ArchiveOfHeresy asks Administratum to create the scheduled task/watch.",
                "required_fields": ["task"],
                "limits": [
                    "Use only for time-based duties, reminders, routines, todos, or watch requests.",
                    "If date/time/condition is ambiguous, use ask_clarification.",
                ],
            },
        ],
        "absolute_rules": [
            "The assistant must never promise or describe an external action unless this protocol selected that action and the server executed it.",
            "The assistant must never pretend to browse, search, work in files, run brigades, schedule reminders, or monitor anything through plain text.",
            "After delegation, the truthful user-facing answer is only the server acceptance with task_id/status.",
        ],
    }


def capability_contract_message(manifest: dict[str, Any] | None = None, decision: dict[str, Any] | None = None) -> dict[str, str]:
    payload = {
        "capabilities": manifest or turn_capability_manifest(),
        "selected_action": decision or {},
        "runtime_contract": [
            "Speak only from the listed capabilities and selected_action.",
            "If selected_action.action is answer_in_chat, answer normally but do not claim any background/external work has started.",
            "If selected_action.action is ask_clarification, ask the clarification; do not start work.",
            "If selected_action.action is issue_mission_order or create_administratum_task, the server, not prose, performs that action.",
            "For issue_mission_order, selected_action must show what governor/department is being ordered and why.",
        ],
    }
    return {
        "role": "system",
        "content": "ArchiveOfHeresy capability contract for this turn:\n" + json.dumps(payload, ensure_ascii=False, indent=2),
    }


def build_turn_decision_request(
    *,
    model: str,
    user_text: str,
    recent_history: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the ArchiveOfHeresy turn controller for Shushunya. "
                    "Choose exactly one action from the supplied capability manifest. "
                    "Return one valid JSON object only. No markdown, no prose. "
                    "Do not use keyword rules; infer the user's intent from the whole recent dialogue and the manifest. "
                    "If a real workflow would need missing essential input, choose ask_clarification instead of issue_mission_order. "
                    "Do not hide missing inputs as risks inside a mission_order when the governor cannot start responsibly. "
                    "The JSON schema is: "
                    "{\"action\":\"answer_in_chat|ask_clarification|issue_mission_order|create_administratum_task\","
                    "\"task\":\"full task text if an external action is selected, otherwise empty\","
                    "\"mission_order\":{\"user_request\":\"full recovered user request\","
                    "\"target_governor\":\"IskandarKhayon|Ceraxia|Moriana\","
                    "\"department\":\"Scriptorium|Mechanicum|Pictorium\","
                    "\"task_kind\":\"one listed task kind\","
                    "\"why_this_governor\":\"concrete reason\","
                    "\"primary_goal\":\"concrete outcome\","
                    "\"success_conditions\":[\"testable acceptance criteria\"],"
                    "\"constraints\":[\"hard user constraints\"],"
                    "\"risks\":[\"known risks or ambiguity\"]},"
                    "\"reply\":\"short user-facing text only for answer_in_chat or ask_clarification\","
                    "\"confidence\":0.0,\"reason\":\"brief reason\"}."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "latest_user_text": user_text,
                        "recent_history": recent_history[-12:],
                        "capability_manifest": manifest,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0,
        "max_tokens": 900,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
    }


def normalize_turn_decision(raw: dict[str, Any] | None) -> dict[str, Any]:
    item = dict(raw or {})
    action = str(item.get("action") or "").strip()
    if action not in TURN_ACTIONS:
        action = "ask_clarification"
    try:
        confidence = float(item.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    task = str(item.get("task") or "").strip()
    mission_order = item.get("mission_order") if isinstance(item.get("mission_order"), dict) else {}
    reply = str(item.get("reply") or "").strip()
    reason = str(item.get("reason") or "").strip()
    if action == "issue_mission_order":
        mission_order = normalize_mission_order(mission_order, fallback_task=task)
        if not mission_order.get("user_request") or not mission_order.get("target_governor") or not mission_order.get("primary_goal"):
            action = "ask_clarification"
            reason = reason or "mission order selected without required governor/request/goal fields"
    if action == "create_administratum_task" and not task:
        action = "ask_clarification"
        reason = reason or "external action selected without required task field"
    if action == "ask_clarification" and not reply:
        reply = "Уточни, пожалуйста, что именно нужно сделать и каким результатом это должно закончиться."
    return {
        "action": action,
        "task": task,
        "mission_order": mission_order if isinstance(mission_order, dict) else {},
        "reply": reply,
        "confidence": confidence,
        "reason": reason,
    }


def normalize_mission_order(raw: dict[str, Any], *, fallback_task: str = "") -> dict[str, Any]:
    order = dict(raw or {})
    governor = str(order.get("target_governor") or "").strip()
    known = {item["governor"]: item for item in GOVERNOR_CAPABILITIES}
    if governor not in known:
        governor = ""
    capability = known.get(governor, {})
    task_kind = str(order.get("task_kind") or "").strip()
    if capability and task_kind not in set(capability.get("task_kinds") or []):
        task_kind = str((capability.get("task_kinds") or [""])[0] or "")
    success = order.get("success_conditions") if isinstance(order.get("success_conditions"), list) else []
    constraints = order.get("constraints") if isinstance(order.get("constraints"), list) else []
    risks = order.get("risks") if isinstance(order.get("risks"), list) else []
    return {
        "user_request": str(order.get("user_request") or fallback_task).strip(),
        "target_governor": governor,
        "department": str(order.get("department") or capability.get("department") or "").strip(),
        "task_kind": task_kind,
        "why_this_governor": str(order.get("why_this_governor") or "").strip(),
        "primary_goal": str(order.get("primary_goal") or fallback_task).strip(),
        "success_conditions": [str(item).strip() for item in success if str(item).strip()],
        "constraints": [str(item).strip() for item in constraints if str(item).strip()],
        "risks": [str(item).strip() for item in risks if str(item).strip()],
    }


def mission_order_to_warmaster_message(order: dict[str, Any]) -> str:
    normalized = normalize_mission_order(order)
    sections = [
        "Приказ Шушуни для EyeOfTerror.",
        f"Целевой бригадир: {normalized.get('target_governor')}",
        f"Отдел: {normalized.get('department')}",
        f"Тип задачи: {normalized.get('task_kind')}",
        f"Почему этот бригадир: {normalized.get('why_this_governor')}",
        f"Исходный запрос пользователя: {normalized.get('user_request')}",
        f"Главная цель: {normalized.get('primary_goal')}",
    ]
    if normalized.get("success_conditions"):
        sections.append("Критерии приемки:\n" + "\n".join(f"- {item}" for item in normalized["success_conditions"]))
    if normalized.get("constraints"):
        sections.append("Ограничения:\n" + "\n".join(f"- {item}" for item in normalized["constraints"]))
    if normalized.get("risks"):
        sections.append("Риски и неясности:\n" + "\n".join(f"- {item}" for item in normalized["risks"]))
    sections.append("Warmaster должен оформить это как commander_order и не подменять выбранный смысл задачи пустой маршрутизацией.")
    return "\n\n".join(item for item in sections if str(item).strip())
