from __future__ import annotations

from typing import Any

from .storage import add_journal, list_journal


def record(event_kind: str, message: str, *, task_id: str = "", watch_id: str = "", payload: Any = None) -> dict:
    return add_journal(event_kind, message, task_id=task_id, watch_id=watch_id, payload=payload)


def recent(limit: int = 100) -> list[dict]:
    return list_journal(limit=limit)
