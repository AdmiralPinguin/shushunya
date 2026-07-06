from __future__ import annotations

from typing import Any

from EyeOfTerror.Pictorium.Brigades.Comics.worker_api import execution_packet, require_payload, response, revision_packet
from EyeOfTerror.Pictorium.Brigades.Comics.worker_api import worker_contract as base_contract
from EyeOfTerror.Pictorium.Brigades.Image.Workers.ForgeDispatcher.worker import prepare_dispatch
from EyeOfTerror.Pictorium.Brigades.Image.Workers.ModelQuartermaster.worker import inspect_resources
from EyeOfTerror.Pictorium.Brigades.Image.Workers.Promptwright.worker import prepare_image_plan


WORKER = "Panelwright"


def worker_contract() -> dict[str, Any]:
    return base_contract(
        name=WORKER,
        role="per-panel Image Brigade package builder",
        capabilities=["panel_image_plans", "resource_checks", "forge_dry_run", "image_brigade_execution_layer"],
        inputs=["storyboard", "character_sheet", "submit", "db_path"],
        outputs=["panels", "panel_forge_jobs", "blockers"],
    )


def build_panel_packages(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = require_payload(payload)
    storyboard = data.get("storyboard") if isinstance(data.get("storyboard"), dict) else {}
    panels = storyboard.get("panels") if isinstance(storyboard.get("panels"), list) else []
    if not panels:
        raise ValueError("Panelwright requires storyboard.panels")
    submit = bool(data.get("submit", False))
    db_path = data.get("db_path")
    panel_packages = []
    blockers: list[dict[str, Any]] = []
    for panel in panels:
        request = str(panel.get("image_request") or panel.get("caption") or panel.get("id") or "").strip()
        image_plan = prepare_image_plan({"request": request, "use_memory": False, "use_thinker": False})
        resources = inspect_resources({"job_spec": image_plan.get("job_spec", {})})
        dispatch_payload = {"job_spec": image_plan.get("job_spec", {}), "submit": submit}
        if db_path:
            dispatch_payload["db_path"] = db_path
        dispatch = prepare_dispatch(dispatch_payload)
        for source_name, result in (("resources", resources), ("dispatch", dispatch)):
            for blocker in result.get("blockers", []) if isinstance(result.get("blockers"), list) else []:
                if isinstance(blocker, dict):
                    blockers.append({"panel_id": panel.get("id"), "source": source_name, **blocker})
        panel_packages.append(
            {
                "panel_id": panel.get("id"),
                "order": panel.get("order"),
                "image_request": request,
                "image_plan": image_plan,
                "resources": resources,
                "dispatch": dispatch,
            }
        )
    return response(
        WORKER,
        {
            "artifact": "/work/pictorium/panels.json",
            "panel_jobs_artifact": "/work/pictorium/panel_forge_jobs.json",
            "panels": panel_packages,
            "panel_forge_jobs": [
                {"panel_id": item["panel_id"], "dispatch": item["dispatch"].get("dispatch", {})}
                for item in panel_packages
            ],
            "blockers": blockers,
            "image_brigade_used": ["Promptwright", "ModelQuartermaster", "ForgeDispatcher"],
            "execution_packet": execution_packet(
                worker=WORKER,
                step="panel_generation",
                produced_artifacts=["/work/pictorium/panels.json", "/work/pictorium/panel_forge_jobs.json"],
                next_steps=[] if blockers else ["layout_manifest"],
                blockers=blockers,
                handoff={
                    "panel_count": len(panel_packages),
                    "image_brigade_used": ["Promptwright", "ModelQuartermaster", "ForgeDispatcher"],
                    "submit": submit,
                },
            ),
            "revision_packet": revision_packet(
                worker=WORKER,
                source_step="panel_generation",
                blockers=[
                    {
                        **blocker,
                        "target_worker": blocker.get("target_worker") or ("Promptwright" if blocker.get("source") in {"resources", "dispatch"} else "Panelwright"),
                        "target_step": blocker.get("target_step") or "panel_generation",
                    }
                    for blocker in blockers
                ],
                default_target_worker="Panelwright",
                default_target_step="panel_generation",
                action="rebuild failed panel packages and rerun downstream layout",
            ),
        },
        ok=not blockers,
    )


def handle(payload: dict[str, Any] | None) -> dict[str, Any]:
    return build_panel_packages(payload)
