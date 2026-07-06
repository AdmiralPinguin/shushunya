from __future__ import annotations

import re
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
from EyeOfTerror.Pictorium.Brigades.Image.Workers.ForgeDispatcher.worker import prepare_dispatch
from EyeOfTerror.Pictorium.Brigades.Image.Workers.ModelQuartermaster.worker import inspect_resources
from EyeOfTerror.Pictorium.Brigades.Image.Workers.Promptwright.worker import prepare_image_plan


WORKER = "Panelwright"


def _explicit_dimensions(text: str) -> tuple[int, int] | None:
    match = re.search(r"(\d{3,4})\s*[xх]\s*(\d{3,4})", text, re.I)
    if not match:
        return None
    width = max(512, min(1536, int(match.group(1)) // 8 * 8))
    height = max(512, min(1536, int(match.group(2)) // 8 * 8))
    return width, height


def panel_runtime_constraints(data: dict[str, Any]) -> dict[str, Any]:
    source_task = str(data.get("source_task") or data.get("task") or data.get("request") or "")
    explicit = _explicit_dimensions(source_task)
    width = int(data.get("width") or (explicit[0] if explicit else 512))
    height = int(data.get("height") or (explicit[1] if explicit else 512))
    steps = int(data.get("steps") or 8)
    return {
        "width": max(512, min(1536, width // 8 * 8)),
        "height": max(512, min(1536, height // 8 * 8)),
        "steps": max(1, min(steps, 16)),
        "preferred_engine": str(data.get("preferred_engine") or "").strip() or None,
    }


def constrained_panel_request(request: str, constraints: dict[str, Any]) -> str:
    text = request.strip()
    if not _explicit_dimensions(text):
        text = f"{text} {constraints['width']}x{constraints['height']}"
    if not re.search(r"(?:steps|шаг(?:ов|и|а)?)\s*[:=]?\s*\d{1,3}", text, re.I):
        text = f"{text} steps {constraints['steps']}"
    return text


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
    guidance = worker_model_guidance(
        WORKER,
        "per-panel Image Brigade package builder",
        data,
        "Prepare panel-level image execution packages and structured continuity risks before delegating Image Brigade workers.",
    )
    storyboard = data.get("storyboard") if isinstance(data.get("storyboard"), dict) else {}
    panels = storyboard.get("panels") if isinstance(storyboard.get("panels"), list) else []
    if not panels:
        raise ValueError("Panelwright requires storyboard.panels")
    submit = bool(data.get("submit", False))
    db_path = data.get("db_path")
    constraints = panel_runtime_constraints(data)
    panel_packages = []
    blockers: list[dict[str, Any]] = guidance_blockers(guidance, worker=WORKER, step="panel_generation")
    for panel in panels:
        request = str(panel.get("image_request") or panel.get("caption") or panel.get("id") or "").strip()
        request = constrained_panel_request(request, constraints)
        plan_payload = {
            "request": request,
            "preferred_engine": constraints.get("preferred_engine"),
            "use_memory": False,
            "use_thinker": False,
        }
        image_plan = prepare_image_plan(plan_payload)
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
        with_model_guidance(
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
                "runtime_constraints": constraints,
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
                        "runtime_constraints": constraints,
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
            guidance,
        ),
        ok=not blockers,
    )


def handle(payload: dict[str, Any] | None) -> dict[str, Any]:
    return build_panel_packages(payload)
