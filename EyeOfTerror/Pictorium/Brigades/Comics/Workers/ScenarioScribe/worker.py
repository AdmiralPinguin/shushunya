from __future__ import annotations

from typing import Any

from EyeOfTerror.Pictorium.Brigades.Comics.worker_api import (
    compact_title,
    execution_packet,
    guidance_blockers,
    require_payload,
    requested_panel_count,
    response,
    revision_packet,
    split_beats,
    task_text,
    with_model_guidance,
    worker_model_guidance,
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
    guidance = worker_model_guidance(
        WORKER,
        "comic scenario planner",
        data,
        "Expand the user request into a structured comic scenario with panel count, beats, cast, visual style, risks, and confidence.",
    )
    model_blockers = guidance_blockers(guidance, worker=WORKER, step="scenario")
    if model_blockers:
        return response(
            WORKER,
            with_model_guidance(
                {
                    "artifact": "/work/pictorium/scenario.json",
                    "blockers": model_blockers,
                    "execution_packet": execution_packet(
                        worker=WORKER,
                        step="scenario",
                        produced_artifacts=["/work/pictorium/scenario.json"],
                        blockers=model_blockers,
                    ),
                    "revision_packet": revision_packet(
                        worker=WORKER,
                        source_step="scenario",
                        blockers=model_blockers,
                        default_target_worker=WORKER,
                        default_target_step="scenario",
                        action="retry ScenarioScribe after model_brain returns structured JSON",
                    ),
                },
                guidance,
            ),
            ok=False,
        )
    decision = guidance.get("decision") if isinstance(guidance.get("decision"), dict) else {}
    panel_count = int(data.get("panel_count") or requested_panel_count(request))
    if isinstance(decision.get("panel_count"), int):
        panel_count = max(1, min(12, int(decision["panel_count"])))
    beats = split_beats(request, panel_count)
    if isinstance(decision.get("beats"), list) and decision["beats"]:
        beats = [str(item.get("summary") if isinstance(item, dict) else item).strip() for item in decision["beats"] if str(item).strip()][:panel_count]
        while len(beats) < panel_count:
            beats.append(split_beats(request, panel_count)[len(beats)])
    scenario = {
        "title": str(decision.get("title") or compact_title(request)),
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
    return response(
        WORKER,
        with_model_guidance(
            {
                "artifact": "/work/pictorium/scenario.json",
                "scenario": scenario,
                "execution_packet": execution_packet(
                    worker=WORKER,
                    step="scenario",
                    produced_artifacts=["/work/pictorium/scenario.json"],
                    next_steps=["storyboard"],
                    handoff={"panel_count": panel_count, "beat_count": len(beats)},
                ),
            },
            guidance,
        ),
    )


def handle(payload: dict[str, Any] | None) -> dict[str, Any]:
    return build_scenario(payload)
