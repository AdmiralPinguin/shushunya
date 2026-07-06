from __future__ import annotations

from math import ceil
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


WORKER = "LayoutFinalis"


def worker_contract() -> dict[str, Any]:
    return base_contract(
        name=WORKER,
        role="comic layout and final manifest builder",
        capabilities=["page_layout", "final_manifest", "blocker_rollup"],
        inputs=["scenario", "storyboard", "character_sheet", "panels"],
        outputs=["layout", "final_manifest", "blockers"],
    )


def _blockers_from(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    blockers = value.get("blockers") if isinstance(value.get("blockers"), list) else []
    return [item for item in blockers if isinstance(item, dict)]


def build_layout_manifest(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = require_payload(payload)
    guidance = worker_model_guidance(
        WORKER,
        "comic layout and final manifest builder",
        data,
        "Review comic layout, panel continuity, delivery readiness, and revision needs as structured JSON.",
    )
    scenario = data.get("scenario") if isinstance(data.get("scenario"), dict) else {}
    storyboard = data.get("storyboard") if isinstance(data.get("storyboard"), dict) else {}
    character_sheet = data.get("character_sheet") if isinstance(data.get("character_sheet"), dict) else {}
    panels_payload = data.get("panels") if isinstance(data.get("panels"), dict) else {}
    panel_packages = panels_payload.get("panels") if isinstance(panels_payload.get("panels"), list) else []
    blockers = [
        *_blockers_from(character_sheet),
        *_blockers_from(panels_payload),
        *guidance_blockers(guidance, worker=WORKER, step="layout_manifest"),
    ]
    panels_per_page = max(1, min(6, int(data.get("panels_per_page") or 4)))
    pages = []
    page_count = ceil(len(panel_packages) / panels_per_page) if panel_packages else 0
    for page_index in range(page_count):
        page_panels = panel_packages[page_index * panels_per_page : (page_index + 1) * panels_per_page]
        pages.append(
            {
                "page": page_index + 1,
                "grid": "2x2" if len(page_panels) <= 4 else "2x3",
                "panel_ids": [item.get("panel_id") for item in page_panels],
                "reading_order": "left_to_right_top_to_bottom",
            }
        )
    layout = {
        "title": scenario.get("title") or storyboard.get("title") or "Untitled comic",
        "page_count": page_count,
        "pages": pages,
        "lettering_policy": storyboard.get("layout_policy", {}).get("lettering", "lettering is separate from generated art"),
    }
    manifest = {
        "kind": "pictorium_comic_final_manifest",
        "status": "blocked" if blockers else "ready",
        "scenario": scenario,
        "layout": layout,
        "character_sheet_ready": bool(character_sheet),
        "panel_count": len(panel_packages),
        "panel_ids": [item.get("panel_id") for item in panel_packages],
        "blockers": blockers,
        "handoff": {
            "ready_for_delivery": not blockers and bool(panel_packages),
            "requires_generation": any(not item.get("dispatch", {}).get("job_record") for item in panel_packages),
            "uses_image_brigade_execution_layer": True,
        },
    }
    return response(
        WORKER,
        with_model_guidance(
            {
                "artifact": "/work/pictorium/layout.json",
                "final_artifact": "/work/pictorium/final_manifest.json",
                "layout": layout,
                "final_manifest": manifest,
                "blockers": blockers,
                "execution_packet": execution_packet(
                    worker=WORKER,
                    step="layout_manifest",
                    produced_artifacts=["/work/pictorium/layout.json", "/work/pictorium/final_manifest.json"],
                    blockers=blockers,
                    handoff=manifest["handoff"],
                ),
                "revision_packet": revision_packet(
                    worker=WORKER,
                    source_step="layout_manifest",
                    blockers=blockers,
                    default_target_worker="Panelwright",
                    default_target_step="panel_generation",
                    action="clear panel, character-sheet, or layout blockers and rebuild the manifest",
                ),
            },
            guidance,
        ),
        ok=not blockers,
    )


def handle(payload: dict[str, Any] | None) -> dict[str, Any]:
    return build_layout_manifest(payload)
