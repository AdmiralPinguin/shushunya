from __future__ import annotations

from typing import Any

from EyeOfTerror.Pictorium.Brigades.Comics.worker_api import (
    compact_title,
    require_payload,
    requested_panel_count,
    response,
    split_beats,
    task_text,
    worker_contract as base_contract,
)


WORKER = "ScenarioScribe"


def worker_contract() -> dict[str, Any]:
    return base_contract(
        name=WORKER,
        role="comic scenario planner",
        capabilities=["scenario", "beats", "cast_style_constraints"],
        inputs=["request|task|contract.goal", "panel_count"],
        outputs=["scenario"],
    )


def build_scenario(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = require_payload(payload)
    request = task_text(data)
    if not request:
        raise ValueError("ScenarioScribe requires request, task, or contract.goal")
    panel_count = int(data.get("panel_count") or requested_panel_count(request))
    beats = split_beats(request, panel_count)
    scenario = {
        "title": compact_title(request),
        "request": request,
        "panel_count": panel_count,
        "format": "comic_storyboard",
        "cast": [
            {
                "id": "main_character",
                "role": "primary subject",
                "visual_continuity": [
                    "preserve silhouette across panels",
                    "preserve costume or signature color accents",
                    "avoid unexplained identity changes",
                ],
            }
        ],
        "visual_style": {
            "rendering": "cinematic comic panels, readable composition, no text unless lettering stage requests it",
            "continuity_policy": "character sheet is the continuity source for all panels",
        },
        "beats": [
            {"id": f"beat_{index + 1}", "summary": beat, "panel_target": index + 1}
            for index, beat in enumerate(beats)
        ],
    }
    return response(WORKER, {"artifact": "/work/pictorium/scenario.json", "scenario": scenario})


def handle(payload: dict[str, Any] | None) -> dict[str, Any]:
    return build_scenario(payload)
