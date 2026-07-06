from __future__ import annotations

from typing import Any

from EyeOfTerror.Pictorium.Brigades.Comics.worker_api import execution_packet, require_payload, response, revision_packet
from EyeOfTerror.Pictorium.Brigades.Comics.worker_api import worker_contract as base_contract
from EyeOfTerror.Pictorium.Brigades.Image.Workers.Promptwright.worker import prepare_image_plan


WORKER = "CharacterSheetwright"


def worker_contract() -> dict[str, Any]:
    return base_contract(
        name=WORKER,
        role="comic character sheet planner using Image Brigade",
        capabilities=["character_sheet_plan", "image_brigade_promptwright"],
        inputs=["scenario"],
        outputs=["character_sheet", "image_plan"],
    )


def build_character_sheet(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = require_payload(payload)
    scenario = data.get("scenario") if isinstance(data.get("scenario"), dict) else {}
    request = str(scenario.get("request") or scenario.get("title") or "").strip()
    if not request:
        raise ValueError("CharacterSheetwright requires scenario request/title")
    sheet_request = (
        f"character sheet for comic continuity, {request}, front view, side view, head close-up, "
        "costume and silhouette reference, plain background, no text labels"
    )
    image_plan = prepare_image_plan(
        {
            "request": sheet_request,
            "mode": "character_sheet",
            "variants": 4,
            "use_memory": False,
            "use_thinker": False,
        }
    )
    character_sheet = {
        "source": "Image Brigade Promptwright",
        "request": sheet_request,
        "image_plan": image_plan,
        "continuity_rules": scenario.get("visual_style", {}).get("continuity_policy", "preserve character identity"),
    }
    blockers = [] if image_plan.get("ok") else [{"code": "character_sheet_image_plan_failed", "message": "Image Brigade Promptwright did not produce a usable character sheet plan"}]
    return response(
        WORKER,
        {
            "artifact": "/work/pictorium/character_sheet.json",
            "character_sheet": character_sheet,
            "image_brigade_used": ["Promptwright"],
            "execution_packet": execution_packet(
                worker=WORKER,
                step="character_sheet",
                produced_artifacts=["/work/pictorium/character_sheet.json"],
                next_steps=["panel_generation"],
                blockers=blockers,
                handoff={"image_brigade_used": ["Promptwright"], "continuity_source": "character_sheet"},
            ),
            "revision_packet": revision_packet(
                worker=WORKER,
                source_step="character_sheet",
                blockers=blockers,
                default_target_worker="Promptwright",
                default_target_step="image_planning",
                action="produce a valid character sheet image plan",
            ),
        },
        ok=bool(image_plan.get("ok")),
    )


def handle(payload: dict[str, Any] | None) -> dict[str, Any]:
    return build_character_sheet(payload)
