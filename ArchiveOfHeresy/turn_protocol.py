"""LLM-facing turn protocol for capability-aware chat decisions."""
from __future__ import annotations

import json
from typing import Any


TURN_ACTIONS = {
    "answer_in_chat",
    "ask_clarification",
    "request_warmaster_mission",
    "create_administratum_task",
    "deliver_pending_reports",
}


WARMASTER_CAPABILITY_AREAS = [
    {
        "area": "research",
        "description": "deep research, source comparison, lore/event reconstruction, long-form synthesis, translation-backed source work",
    },
    {
        "area": "code",
        "description": "software engineering, project creation, code repair, architecture, tests, repository work",
    },
    {
        "area": "image",
        "description": "image generation, drawing workflows, Stable Diffusion/Forge/Flux/SDXL jobs, comics, panels, visual series",
    },
    {
        "area": "mixed",
        "description": "multi-department tasks that need planning, execution, progress reporting, and acceptance review",
    },
    {
        "area": "administration",
        "description": "coordination-heavy task tracking when the user asks for organized execution, not a time reminder",
    },
]


def turn_capability_manifest(*, image_attached: bool = False, pending_reports: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = {
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
                "action": "request_warmaster_mission",
                "available": True,
                "description": (
                    "Ask EyeOfTerror Warmaster to command a real multi-step task. Shushunya decides only that the work "
                    "needs Warmaster-level execution and describes the requested outcome. Warmaster chooses the department, "
                    "brigadier, workers, plan, and acceptance process."
                ),
                "server_effect": "ArchiveOfHeresy creates a real Warmaster task_id and starts the background pipeline.",
                "warmaster_capability_areas": WARMASTER_CAPABILITY_AREAS,
                "required_fields": ["warmaster_request"],
                "limits": [
                    "Use only when the user is actually asking for work to be performed, not when they are merely discussing architecture or asking a question.",
                    "Do not choose a concrete brigadier, governor, internal worker, or department here; that is Warmaster's job.",
                    "For vague follow-up commands, recover the intended task from recent chat history and include that full recovered task in warmaster_request.user_request.",
                    "If the task lacks essential domain input, use ask_clarification instead of requesting Warmaster. Examples: image generation without a subject/description, code work without a target repository or greenfield scope, research without a topic.",
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
    if pending_reports and int(pending_reports.get("count") or 0) > 0:
        manifest["capabilities"].append(
            {
                "action": "deliver_pending_reports",
                "available": True,
                "description": (
                    "Deliver Shushunya's queued proactive reports (finished/blocked brigade tasks, Administratum reminders "
                    "and summaries) into the chat. Choose this when the user asks for news, pending reports, or whether "
                    "Shushunya wants to say something ('что там ещё', 'что нового', 'докладывай', 'хочешь что-то сказать?')."
                ),
                "server_effect": "ArchiveOfHeresy injects the queued report contents into this turn and marks them delivered.",
                "pending": pending_reports,
                "limits": [
                    "Use only when the user asks to hear the news/reports.",
                    "Report contents come from the server queue; never invent them.",
                ],
            }
        )
    return manifest


def capability_contract_message(manifest: dict[str, Any] | None = None, decision: dict[str, Any] | None = None) -> dict[str, str]:
    """Compact per-turn contract for the ANSWERING model. The full capability
    manifest (~5K chars) belongs only to the turn-decision call; repeating it
    in every answer prompt drowned the model in boilerplate."""
    del manifest  # the answering model needs only the selected action, not the menu
    decision = decision or {}
    payload = {
        "selected_action": {
            "action": str(decision.get("action") or "answer_in_chat"),
            "task": str(decision.get("task") or "")[:300],
            "reason": str(decision.get("reason") or "")[:200],
        },
        "runtime_contract": [
            "Внешние действия исполняет сервер, а не текст: не обещай и не описывай фоновую работу, поиск, файлы, бригады или напоминания, если selected_action этого не выбрал.",
            "answer_in_chat / ask_clarification: просто ответь или уточни, никакая работа не запускалась.",
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
                    "Shushunya is male: any user-facing reply you produce must use masculine Russian forms for him and for self-reference. "
                    "Choose exactly one action from the supplied capability manifest. "
                    "Return one valid JSON object only. No markdown, no prose. "
                    "Do not use keyword rules; infer the user's intent from the whole recent dialogue and the manifest. "
                    "If a real workflow would need missing essential input, choose ask_clarification instead of request_warmaster_mission. "
                    "Do not hide missing inputs as risks inside a warmaster_request when Warmaster cannot start responsibly. "
                    "The JSON schema is: "
                    "{\"action\":\"answer_in_chat|ask_clarification|request_warmaster_mission|create_administratum_task|deliver_pending_reports\","
                    "\"task\":\"full task text if an external action is selected, otherwise empty\","
                    "\"warmaster_request\":{\"user_request\":\"full recovered user request\","
                    "\"capability_area\":\"research|code|image|mixed|administration|unknown\","
                    "\"why_warmaster_needed\":\"concrete reason this needs Warmaster instead of direct chat\","
                    "\"expected_outcome\":\"concrete outcome\","
                    "\"success_conditions\":[\"testable acceptance criteria\"],"
                    "\"constraints\":[\"hard user constraints\"],"
                    "\"known_missing_inputs\":[\"non-blocking unknowns Warmaster should investigate\"]},"
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
    warmaster_request = item.get("warmaster_request") if isinstance(item.get("warmaster_request"), dict) else {}
    reply = str(item.get("reply") or "").strip()
    reason = str(item.get("reason") or "").strip()
    if action == "request_warmaster_mission":
        warmaster_request = normalize_warmaster_request(warmaster_request, fallback_task=task)
        if not warmaster_request.get("user_request") or not warmaster_request.get("expected_outcome"):
            action = "ask_clarification"
            reason = reason or "Warmaster request selected without required request/outcome fields"
    if action == "create_administratum_task" and not task:
        action = "ask_clarification"
        reason = reason or "external action selected without required task field"
    if action == "ask_clarification" and not reply:
        reply = "Уточни, пожалуйста, что именно нужно сделать и каким результатом это должно закончиться."
    return {
        "action": action,
        "task": task,
        "warmaster_request": warmaster_request if isinstance(warmaster_request, dict) else {},
        "reply": reply,
        "confidence": confidence,
        "reason": reason,
    }


def normalize_warmaster_request(raw: dict[str, Any], *, fallback_task: str = "") -> dict[str, Any]:
    request = dict(raw or {})
    allowed_areas = {item["area"] for item in WARMASTER_CAPABILITY_AREAS} | {"unknown"}
    capability_area = str(request.get("capability_area") or "unknown").strip().lower()
    if capability_area not in allowed_areas:
        capability_area = "unknown"
    success = request.get("success_conditions") if isinstance(request.get("success_conditions"), list) else []
    constraints = request.get("constraints") if isinstance(request.get("constraints"), list) else []
    missing = request.get("known_missing_inputs") if isinstance(request.get("known_missing_inputs"), list) else []
    return {
        "user_request": str(request.get("user_request") or fallback_task).strip(),
        "capability_area": capability_area,
        "why_warmaster_needed": str(request.get("why_warmaster_needed") or "").strip(),
        "expected_outcome": str(request.get("expected_outcome") or fallback_task).strip(),
        "success_conditions": [str(item).strip() for item in success if str(item).strip()],
        "constraints": [str(item).strip() for item in constraints if str(item).strip()],
        "known_missing_inputs": [str(item).strip() for item in missing if str(item).strip()],
    }


def warmaster_request_to_message(request: dict[str, Any]) -> str:
    normalized = normalize_warmaster_request(request)
    sections = [
        "Запрос Шушуни к EyeOfTerror Warmaster.",
        "Шушуня не выбирает бригадира или отдел. Warmaster сам назначает бригадира, работников, план и приемку.",
        f"Область задачи: {normalized.get('capability_area')}",
        f"Почему нужен Warmaster: {normalized.get('why_warmaster_needed')}",
        f"Исходный запрос пользователя: {normalized.get('user_request')}",
        f"Ожидаемый результат: {normalized.get('expected_outcome')}",
    ]
    if normalized.get("success_conditions"):
        sections.append("Критерии приемки:\n" + "\n".join(f"- {item}" for item in normalized["success_conditions"]))
    if normalized.get("constraints"):
        sections.append("Ограничения:\n" + "\n".join(f"- {item}" for item in normalized["constraints"]))
    if normalized.get("known_missing_inputs"):
        sections.append("Что Warmaster должен выяснить по ходу работы:\n" + "\n".join(f"- {item}" for item in normalized["known_missing_inputs"]))
    sections.append("Warmaster должен оформить это как commander_order и не подменять смысл задачи пустой маршрутизацией.")
    return "\n\n".join(item for item in sections if str(item).strip())
