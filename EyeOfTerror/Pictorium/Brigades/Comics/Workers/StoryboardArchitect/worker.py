from __future__ import annotations

from typing import Any

from EyeOfTerror.Pictorium.Brigades.Comics.worker_api import (
    execution_packet,
    guidance_blockers,
    require_payload,
    response,
    revision_packet,
    with_model_guidance,
    worker_model_guidance,
)
from EyeOfTerror.Pictorium.Brigades.Comics.worker_api import worker_contract as base_contract


WORKER = "StoryboardArchitect"
CAMERAS = ["wide establishing shot", "medium character shot", "low angle action shot", "close-up emotional beat"]


def worker_contract() -> dict[str, Any]:
    return base_contract(
        name=WORKER,
        role="storyboard and continuity planner",
        capabilities=["storyboard", "panel_prompts", "continuity_notes"],
        inputs=["scenario"],
        outputs=["storyboard"],
    )


def build_storyboard(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = require_payload(payload)
    guidance = worker_model_guidance(
        WORKER,
        "storyboard and continuity planner",
        data,
        "Turn scenario beats into structured panel plans with camera, composition, continuity, image_request, risks, and confidence.",
    )
    model_blockers = guidance_blockers(guidance, worker=WORKER, step="storyboard")
    if model_blockers:
        return response(
            WORKER,
            with_model_guidance(
                {
                    "artifact": "/work/pictorium/storyboard.json",
                    "blockers": model_blockers,
                    "execution_packet": execution_packet(
                        worker=WORKER,
                        step="storyboard",
                        produced_artifacts=["/work/pictorium/storyboard.json"],
                        blockers=model_blockers,
                    ),
                    "revision_packet": revision_packet(
                        worker=WORKER,
                        source_step="storyboard",
                        blockers=model_blockers,
                        default_target_worker=WORKER,
                        default_target_step="storyboard",
                        action="retry StoryboardArchitect after model_brain returns structured JSON",
                    ),
                },
                guidance,
            ),
            ok=False,
        )
    scenario = data.get("scenario") if isinstance(data.get("scenario"), dict) else {}
    beats = scenario.get("beats") if isinstance(scenario.get("beats"), list) else []
    if not beats:
        raise ValueError("StoryboardArchitect requires scenario.beats")
    panels = []
    for index, beat in enumerate(beats):
        summary = str(beat.get("summary") or f"panel {index + 1}")
        panel_id = f"panel_{index + 1:02d}"
        panels.append(
            {
                "id": panel_id,
                "order": index + 1,
                "beat_id": beat.get("id") or f"beat_{index + 1}",
                "caption": "",
                "dialogue": [],
                "camera": CAMERAS[index % len(CAMERAS)],
                "composition": "clear foreground subject, readable background, no speech bubbles in generated image",
                "continuity": ["match main_character sheet", "preserve style and palette"],
                "image_request": f"{scenario.get('title', 'comic')}, comic panel {index + 1}, {summary}, {CAMERAS[index % len(CAMERAS)]}, no text, no speech bubbles",
            }
        )
    storyboard = {
        "title": scenario.get("title", "Untitled comic"),
        "panel_count": len(panels),
        "panels": panels,
        "layout_policy": {
            "reading_order": "left_to_right_top_to_bottom",
            "lettering": "separate layout stage; generated art should not contain text",
        },
    }
    return response(
        WORKER,
        with_model_guidance(
            {
                "artifact": "/work/pictorium/storyboard.json",
                "storyboard": storyboard,
                "execution_packet": execution_packet(
                    worker=WORKER,
                    step="storyboard",
                    produced_artifacts=["/work/pictorium/storyboard.json"],
                    next_steps=["character_sheet", "panel_generation"],
                    handoff={"panel_count": len(panels), "reading_order": storyboard["layout_policy"]["reading_order"]},
                ),
            },
            guidance,
        ),
    )


def handle(payload: dict[str, Any] | None) -> dict[str, Any]:
    return build_storyboard(payload)
