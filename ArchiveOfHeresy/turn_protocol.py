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
                    "Ask EyeOfTerror Abaddon to command a real multi-step task. Shushunya decides only that the work "
                    "needs Abaddon-level execution and describes the requested outcome. Abaddon chooses the strategic route "
                    "and brigadier, arbitrates across warbands, and owns final escalation. The assigned brigadier makes "
                    "warband-level decisions; subordinates own the detailed plan, execution, and checks."
                ),
                "server_effect": "ArchiveOfHeresy creates a real Abaddon task_id and starts the background pipeline.",
                "warmaster_capability_areas": WARMASTER_CAPABILITY_AREAS,
                "required_fields": ["warmaster_request"],
                "limits": [
                    "Use only when the user is actually asking for work to be performed, not when they are merely discussing architecture or asking a question.",
                    "Do not choose a concrete brigadier, governor, or department here; Abaddon owns that strategic route. Never choose internal workers or a detailed implementation plan here; the assigned brigadier and subordinates own those layers.",
                    "For vague follow-up commands, recover the intended task from recent chat history and include that full recovered task in warmaster_request.user_request.",
                    "If the task lacks essential domain input, use ask_clarification instead of requesting Abaddon. Examples: image generation without a subject/description, code work without a target repository or greenfield scope, research without a topic.",
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
        "Запрос Шушуни к EyeOfTerror Abaddon.",
        "Шушуня не выбирает бригадира или отдел. Абаддон выбирает стратегический маршрут и назначает бригадира; он не выбирает работников и не составляет подробный план.",
        "Бригадир принимает решения своей варбанды, а подчинённые составляют детальный план, выполняют работу и проводят проверки. Абаддон держит межбригадную координацию и финальную эскалацию.",
        f"Область задачи: {normalized.get('capability_area')}",
        f"Почему нужен Абаддон: {normalized.get('why_warmaster_needed')}",
        f"Исходный запрос пользователя: {normalized.get('user_request')}",
        f"Ожидаемый результат: {normalized.get('expected_outcome')}",
    ]
    if normalized.get("success_conditions"):
        sections.append("Критерии приемки:\n" + "\n".join(f"- {item}" for item in normalized["success_conditions"]))
    if normalized.get("constraints"):
        sections.append("Ограничения:\n" + "\n".join(f"- {item}" for item in normalized["constraints"]))
    if normalized.get("known_missing_inputs"):
        sections.append("Что Абаддон должен выяснить по ходу работы:\n" + "\n".join(f"- {item}" for item in normalized["known_missing_inputs"]))
    sections.append("Абаддон должен оформить это как commander_order и не подменять смысл задачи пустой маршрутизацией.")
    return "\n\n".join(item for item in sections if str(item).strip())
