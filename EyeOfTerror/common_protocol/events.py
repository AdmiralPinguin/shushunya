from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .protocol import PROGRESS_PHASES, PROGRESS_STATUSES, protocol_envelope
from .validation import validate_protocol_payload


def progress_event(
    mission_id: str,
    actor: str,
    role: str,
    phase: str,
    status: str,
    title: str,
    body: str,
    visible_to_user: bool = True,
) -> dict[str, Any]:
    if phase not in PROGRESS_PHASES:
        raise ValueError(f"invalid progress phase: {phase}")
    if status not in PROGRESS_STATUSES:
        raise ValueError(f"invalid progress status: {status}")
    return {
        **protocol_envelope("progress_event", mission_id),
        "actor": actor,
        "role": role,
        "phase": phase,
        "status": status,
        "title": title,
        "body": body,
        "visible_to_user": visible_to_user,
    }


def append_progress_event(events_path: Path, event: dict[str, Any]) -> None:
    validate_protocol_payload(event, expected_type="progress_event")
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
