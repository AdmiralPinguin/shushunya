from __future__ import annotations

from typing import Any

from EyeOfTerror.Pictorium.Brigades.Image.worker_api import execution_packet, model_dump, require_payload, response
from EyeOfTerror.Pictorium.Brigades.Image.worker_api import task_text as payload_task_text
from EyeOfTerror.Pictorium.Brigades.Image.worker_api import worker_contract as base_contract
from EyeOfTerror.Pictorium.Moriana.forge_runtime.schemas import PlanRequest, ProjectPlanRequest
from EyeOfTerror.Pictorium.Moriana.moriana_core.project_planner import plan_project
from EyeOfTerror.Pictorium.Moriana.moriana_core.promptwright import plan_txt2img


WORKER = "Promptwright"


def worker_contract() -> dict[str, Any]:
    return base_contract(
        name=WORKER,
        role="image intent parser and Forge job/project planner",
        capabilities=["txt2img_plan", "project_plan", "prompt_normalization"],
        inputs=["request|task|contract.goal", "mode", "preferred_engine", "use_memory", "use_thinker"],
        outputs=["job_spec|project_spec", "plan_kind", "artifact"],
    )


def prepare_image_plan(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = require_payload(payload)
    request = payload_task_text(data)
    if not request:
        raise ValueError("Promptwright requires request, task, or contract.goal")
    mode = str(data.get("mode") or data.get("plan_kind") or "job").strip().lower()
    use_memory = bool(data.get("use_memory", False))
    use_thinker = bool(data.get("use_thinker", False))
    if mode in {"project", "comic_storyboard", "character_sheet", "concept_batch"}:
        project_type = str(data.get("project_type") or ("auto" if mode == "project" else mode))
        project = plan_project(
            ProjectPlanRequest(
                request=request,
                project_type=project_type,  # type: ignore[arg-type]
                character_id=data.get("character_id"),
                variants=int(data.get("variants") or 4),
                panels=int(data.get("panels") or 4),
                width=data.get("width"),
                height=data.get("height"),
                engine_strategy=str(data.get("engine_strategy") or "auto"),  # type: ignore[arg-type]
                use_memory=use_memory,
                use_thinker=use_thinker,
            )
        )
        return response(
            WORKER,
            {
                "plan_kind": "project",
                "artifact": "/work/pictorium/image_plan.json",
                "project_spec": model_dump(project),
                "job_spec": model_dump(project.steps[0].spec) if project.steps else {},
                "execution_packet": execution_packet(
                    worker=WORKER,
                    step="image_planning",
                    produced_artifacts=["/work/pictorium/image_plan.json"],
                    next_steps=["resource_readiness"],
                    handoff={"plan_kind": "project", "project_step_count": len(project.steps)},
                ),
            },
        )
    spec = plan_txt2img(
        PlanRequest(
            request=request,
            preferred_engine=data.get("preferred_engine"),
            use_memory=use_memory,
            use_thinker=use_thinker,
        )
    )
    return response(
        WORKER,
        {
            "plan_kind": "job",
            "artifact": "/work/pictorium/image_plan.json",
            "job_spec": model_dump(spec),
            "execution_packet": execution_packet(
                worker=WORKER,
                step="image_planning",
                produced_artifacts=["/work/pictorium/image_plan.json"],
                next_steps=["resource_readiness"],
                handoff={"plan_kind": "job", "prompt_ready": bool(spec.prompt)},
            ),
        },
    )


def handle(payload: dict[str, Any] | None) -> dict[str, Any]:
    return prepare_image_plan(payload)
