"""LLM-facing turn protocol for capability-aware chat decisions."""
from __future__ import annotations

import json
from typing import Any


TURN_ACTIONS = {
    "answer_in_chat",
    "ask_clarification",
    "delegate_to_warmaster",
    "create_administratum_task",
}


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
                "action": "delegate_to_warmaster",
                "available": True,
                "description": (
                    "Start the Warmaster orchestration pipeline for substantial work that needs brigades, external research, "
                    "code/project work, generation workflows, long-running execution, or final acceptance review."
                ),
                "server_effect": "ArchiveOfHeresy creates a real Warmaster task_id and starts the background pipeline.",
                "required_fields": ["task"],
                "limits": [
                    "Use only when the user is actually asking for work to be performed, not when they are merely discussing architecture or asking a question.",
                    "For vague follow-up commands, recover the intended task from recent chat history and include that full recovered task.",
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
            "If selected_action.action is delegate_to_warmaster or create_administratum_task, the server, not prose, performs that action.",
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
                    "The JSON schema is: "
                    "{\"action\":\"answer_in_chat|ask_clarification|delegate_to_warmaster|create_administratum_task\","
                    "\"task\":\"full task text if an external action is selected, otherwise empty\","
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
    reply = str(item.get("reply") or "").strip()
    reason = str(item.get("reason") or "").strip()
    if action in {"delegate_to_warmaster", "create_administratum_task"} and not task:
        action = "ask_clarification"
        reason = reason or "external action selected without required task field"
    if action == "ask_clarification" and not reply:
        reply = "Уточни, пожалуйста, что именно нужно сделать и каким результатом это должно закончиться."
    return {
        "action": action,
        "task": task,
        "reply": reply,
        "confidence": confidence,
        "reason": reason,
    }
