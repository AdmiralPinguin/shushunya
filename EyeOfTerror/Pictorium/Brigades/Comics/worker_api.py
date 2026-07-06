from __future__ import annotations

import re
from typing import Any

from EyeOfTerror.Pictorium.Brigades.Image.worker_api import (
    API_VERSION,
    execution_packet,
    model_dump,
    require_payload,
    response,
    revision_packet,
    task_text,
    guidance_blockers,
    with_model_guidance,
    worker_model_guidance,
    worker_contract,
)


def requested_panel_count(text: str, default: int = 4) -> int:
    for pattern in (
        r"(\d{1,2})\s*(?:панел|кадр|panel|panels)",
        r"(?:панел|кадр|panel|panels)\s*[:=]?\s*(\d{1,2})",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return max(1, min(12, int(match.group(1))))
    return default


def compact_title(text: str) -> str:
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return "Untitled comic"
    return cleaned[:80]


def split_beats(text: str, count: int) -> list[str]:
    raw_parts = [part.strip(" .;:-") for part in re.split(r"[.;\n]+", text) if part.strip(" .;:-")]
    if len(raw_parts) >= count:
        return raw_parts[:count]
    fallback = [
        "establish the setting and main character",
        "introduce the visual conflict",
        "show the decisive action beat",
        "resolve with a clear final image",
        "add an environmental reaction",
        "show a close-up detail insert",
        "show motion or escalation",
        "end with a strong silhouette",
    ]
    beats = raw_parts[:]
    while len(beats) < count:
        beats.append(fallback[len(beats) % len(fallback)])
    return beats


__all__ = [
    "API_VERSION",
    "compact_title",
    "execution_packet",
    "model_dump",
    "requested_panel_count",
    "require_payload",
    "response",
    "revision_packet",
    "split_beats",
    "task_text",
    "guidance_blockers",
    "with_model_guidance",
    "worker_model_guidance",
    "worker_contract",
]
