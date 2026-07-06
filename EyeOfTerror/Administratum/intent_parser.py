from __future__ import annotations

import json
from typing import Any

from .timeutil import DEFAULT_TZ


INTENT_SYSTEM_PROMPT = (
    "You detect whether the user's Russian text creates a reminder, todo, routine, or watch for Administratum. "
    "Return one strict JSON object only. If no time/task/watch instruction exists, return {\"intent\":\"none\",\"confidence\":0}. "
    "For create_task return: intent=create_task, kind=reminder|todo|routine|watch, title, body, due_at ISO-8601 or empty, "
    "interval string or empty, timezone, confidence 0..1, needs_confirmation boolean, reason. "
    "For watch also include target and condition. Use current date/time from the user message context. "
    "If the date or watch condition is ambiguous, set needs_confirmation=true."
)


INTENT_EXAMPLES = [
    "напомни завтра в 9 проверить билд",
    "каждое утро говори что у нас по задачам",
    "следи за ценой 3090",
    "через час пни меня вернуться к агенту",
]


def build_intent_detection_request(user_text: str, *, model: str, now: str, timezone: str = DEFAULT_TZ) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": INTENT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "now": now,
                        "timezone": timezone,
                        "text": user_text,
                        "examples": INTENT_EXAMPLES,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0,
        "max_tokens": 512,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
    }


def normalize_intent(intent: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(intent or {})
    normalized = {
        "ok": bool(raw.get("ok", True)),
        "intent": str(raw.get("intent") or "none").strip(),
        "kind": str(raw.get("kind") or "").strip().lower(),
        "title": str(raw.get("title") or "").strip(),
        "body": str(raw.get("body") or "").strip(),
        "due_at": str(raw.get("due_at") or "").strip(),
        "interval": str(raw.get("interval") or "").strip(),
        "timezone": str(raw.get("timezone") or DEFAULT_TZ).strip() or DEFAULT_TZ,
        "needs_confirmation": bool(raw.get("needs_confirmation")),
        "reason": str(raw.get("reason") or "").strip(),
        "target": str(raw.get("target") or "").strip(),
        "watch_type": str(raw.get("watch_type") or "generic").strip() or "generic",
        "condition": raw.get("condition") if isinstance(raw.get("condition"), dict) else {},
    }
    try:
        normalized["confidence"] = float(raw.get("confidence") or 0)
    except (TypeError, ValueError):
        normalized["confidence"] = 0.0
    if normalized["intent"] == "create_task" and normalized["kind"] not in {"reminder", "todo", "routine", "watch"}:
        normalized["kind"] = "reminder"
    return normalized


def administratum_payload_from_intent(intent: dict[str, Any], *, session_id: str, client_source: str) -> tuple[str, dict[str, Any]]:
    normalized = normalize_intent(intent)
    kind = normalized.get("kind") or "reminder"
    if kind == "watch":
        return (
            "watch",
            {
                "title": normalized["title"],
                "watch_type": normalized["watch_type"],
                "target": normalized["target"] or normalized["body"] or normalized["title"],
                "condition": normalized["condition"] or {"description": normalized["body"], "interval": "15m"},
                "status": "active",
            },
        )
    return (
        "task",
        {
            "kind": kind,
            "title": normalized["title"],
            "body": normalized["body"],
            "due_at": normalized["due_at"],
            "interval": normalized["interval"],
            "timezone": normalized["timezone"],
            "created_by": client_source or "chat",
            "created_from_session": session_id,
            "payload": {"intent": normalized},
        },
    )
