"""LLM-facing turn protocol for capability-aware chat decisions."""
from __future__ import annotations

import json
from typing import Any


TURN_ACTIONS = {
    "answer_in_chat",
    "ask_clarification",
    "request_warmaster_mission",
    "answer_pending_decision",
    "create_administratum_task",
    "deliver_pending_reports",
    "deliver_artifact",
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


def turn_capability_manifest(
    *,
    image_attached: bool = False,
    pending_reports: dict[str, Any] | None = None,
    pending_decisions: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    available_artifacts = [dict(item) for item in (artifacts or []) if isinstance(item, dict)][:12]
    # Keep enough exact identity/question context to route an explicit answer to
    # an older parallel mission.  The store orders questions oldest -> newest,
    # so retain the newest bounded window rather than silently keeping the first
    # three forever.
    decision_candidates = [
        dict(item)
        for item in (pending_decisions or [])
        if isinstance(item, dict)
        and str(item.get("task_id") or "").strip()
        and str(item.get("question") or "").strip()
    ]
    open_decisions = []
    for item in decision_candidates[-3:]:
        decision = {
            "task_id": str(item.get("task_id") or "").strip()[:240],
            "decision_id": str(item.get("decision_id") or "").strip()[:128],
            "problem": str(item.get("problem") or "").strip()[:900],
            "question": str(item.get("question") or "").strip()[:700],
            "recommendation": str(item.get("recommendation") or "").strip()[:500],
            "options": [
                {
                    "id": str(option.get("id") or "").strip()[:160],
                    "label": str(option.get("label") or "").strip()[:300],
                    "effect": str(option.get("effect") or "").strip()[:500],
                }
                for option in item.get("options") or []
                if isinstance(option, dict)
            ][:3],
        }
        open_decisions.append(
            {key: value for key, value in decision.items() if value != "" and value is not None}
        )
    # Ordinary replies naturally answer the most recently asked open question.
    # Older questions remain in Core context and can be disambiguated explicitly.
    bound_decision = open_decisions[-1] if open_decisions else {}
    manifest = {
        "version": 1,
        "principle": (
            "Shushunya may speak and act only through the capabilities listed here. "
            "If an action is not listed, it does not exist for this turn."
        ),
        "current_input": {
            "image_attached": bool(image_attached),
        },
        "pending_decision_task_id": str(bound_decision.get("task_id") or ""),
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
                "action": "answer_pending_decision",
                "available": bool(bound_decision),
                "description": (
                    "Resume the exact existing task when the user's current message answers one of Shushunya's "
                    "open decision questions. This is not a new task."
                ),
                "server_effect": "Archive forwards the answer to the bound waiting run and resumes that same run.",
                "problem": str(bound_decision.get("problem") or ""),
                "question": str(bound_decision.get("question") or ""),
                "recommended_option": str(bound_decision.get("recommendation") or ""),
                "pending_decisions": open_decisions,
                "options": [
                    {
                        "id": str(option.get("id") or ""),
                        "label": str(option.get("label") or ""),
                        "description": str(option.get("effect") or ""),
                    }
                    for option in bound_decision.get("options") or []
                    if isinstance(option, dict)
                ],
                "required_fields": ["pending_decision_task_id"],
                "limits": [
                    "Select only when the current message actually answers the pending question.",
                    "pending_decision_task_id must be copied exactly from pending_decisions; never invent an id.",
                    "If the message appears to answer an older open question instead of this most recent one, use ask_clarification.",
                ],
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
            {
                "action": "deliver_artifact",
                "available": bool(available_artifacts),
                "description": "Attach one already registered Archive artifact to the owner's current chat.",
                "server_effect": "Core asks Archive to persist exactly one artifact-bearing chat message.",
                "required_fields": ["artifact_delivery.artifact_id"],
                "artifacts": available_artifacts,
                "limits": [
                    "Choose only an exact artifact_id from this turn's catalog; filenames and host paths are never authority.",
                    "Use only when the owner asks to receive an existing file. Creating or changing a file requires a real brigade mission.",
                    "The catalog is limited to recent artifacts visible to this chat session and client source.",
                ],
            },
        ],
        "absolute_rules": [
            "The assistant must never promise or describe an external action unless this protocol selected that action and the server executed it.",
            "The assistant must never pretend to browse, search, work in files, run brigades, schedule reminders, or monitor anything through plain text.",
            "After delegation, ordinary chat gives a short first-person acknowledgement. Technical ids/status belong only to Warbands/debug surfaces.",
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
            "answer_pending_decision: сервер возобновляет ту же задачу; скажи коротко от первого лица, без внутренних имён и идентификаторов.",
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
