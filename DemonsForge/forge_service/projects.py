from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from . import config
from .characters import character_profile_for_text, character_profiles
from .planner import plan_txt2img
from .schemas import JobSpec, PlanRequest, ProjectPlanRequest, ProjectSpec, ProjectStep, utc_now


def _project_path(project_id: str) -> Path:
    safe = Path(str(project_id)).name
    if safe != project_id or not safe:
        raise ValueError("project_id must be a basename")
    return config.PROJECTS_DIR / f"{safe}.json"


def _profile_by_id(profile_id: str | None) -> dict[str, Any] | None:
    if not profile_id:
        return None
    for profile in character_profiles().get("profiles", []):
        if str(profile.get("id", "")).lower() == profile_id.lower():
            return profile
    return None


def _resolve_character(request: ProjectPlanRequest) -> dict[str, Any] | None:
    return _profile_by_id(request.character_id) or character_profile_for_text(request.request)


def _project_type(request: ProjectPlanRequest) -> str:
    if request.project_type != "auto":
        return request.project_type
    lowered = request.request.lower()
    if any(token in lowered for token in ["комикс", "comic", "storyboard", "сториборд", "панел"]):
        return "comic_storyboard"
    if any(token in lowered for token in ["sheet", "лист персонажа", "character sheet", "референс"]):
        return "character_sheet"
    return "concept_batch"


def _dims(request: ProjectPlanRequest) -> tuple[int | None, int | None]:
    if request.width and request.height:
        return request.width, request.height
    return None, None


def _make_spec(
    prompt: str,
    request: ProjectPlanRequest,
    role: str,
    seed_offset: int,
    preferred_engine: str | None = None,
) -> JobSpec:
    width, height = _dims(request)
    plan_text = prompt
    if width and height:
        plan_text = f"{plan_text} {width}x{height}"
    baseline = plan_txt2img(
        PlanRequest(
            request=plan_text,
            preferred_engine=preferred_engine,
            use_memory=request.use_memory,
            use_thinker=request.use_thinker,
        )
    )
    baseline.safety["project_role"] = role
    baseline.safety["project_seed_offset"] = seed_offset
    if preferred_engine == "sdxl" and baseline.type.value == "txt2img" and baseline.steps < 8:
        baseline.steps = 8
        baseline.quality_preset = "draft"
        baseline.safety["quality_preset"] = "draft"
        baseline.safety["project_adjustment"] = "SDXL txt2img concept steps raised to 8 to avoid one-step noise mosaics."
    if baseline.seed is not None:
        baseline.seed += seed_offset
    return baseline


def _concept_steps(request: ProjectPlanRequest, character: dict[str, Any] | None) -> list[ProjectStep]:
    variants = max(1, min(request.variants, 8))
    base = request.request
    angles = [
        "full body silhouette, readable small scale, dark neutral background",
        "front three-quarter view, terrifying face and asymmetry emphasized",
        "close-up head study, preserved blue cat fragment and demonic right side",
        "side view, long corrupted tail, claws and small cursed familiar posture",
        "low angle dramatic horror lighting, turquoise violet warp glow",
        "character turnaround reference, body horror details clearly separated",
        "pose exploration, stalking on uneven demonic limbs",
        "expression study, pitiful frightened left eye and predatory right eye",
    ]
    steps = []
    mixed_engines = ["flux", "sdxl"] if character and request.engine_strategy in {"auto", "mixed_concept"} and variants > 1 else []
    for index in range(variants):
        prompt = f"{base}, first concept, {angles[index]}"
        preferred_engine = mixed_engines[index % len(mixed_engines)] if mixed_engines else None
        steps.append(
            ProjectStep(
                id=f"concept_{index + 1}",
                phase="concept",
                title=f"Concept {index + 1}",
                role="first_concept",
                spec=_make_spec(prompt, request, "first_concept", index, preferred_engine=preferred_engine),
            )
        )
    return steps


def _comic_steps(request: ProjectPlanRequest, character: dict[str, Any] | None) -> list[ProjectStep]:
    panels = max(1, min(request.panels, 8))
    beats = [
        "panel 1 establishing shot, a tiny terrifying corrupted familiar appears near a demonic forge",
        "panel 2 close character reveal, asymmetrical cat-like silhouette and warped flesh details",
        "panel 3 action beat, claws, tentacles and warp glow flare in a threatening moment",
        "panel 4 payoff, tragic unsettling expression, pitiful and horrifying final image",
        "panel 5 environmental reaction, forge shadows bend around the creature",
        "panel 6 detail insert, eye glow teeth horns and preserved blue fur fragment",
        "panel 7 movement beat, tail and claws create a dynamic horror pose",
        "panel 8 final ominous silhouette, small cursed familiar disappears into smoke",
    ]
    steps = []
    for index in range(panels):
        prompt = f"{request.request}, first concept image, comic storyboard, {beats[index]}, no text, no speech bubbles"
        steps.append(
            ProjectStep(
                id=f"panel_{index + 1}",
                phase="storyboard",
                title=f"Panel {index + 1}",
                role="comic_panel",
                spec=_make_spec(prompt, request, "comic_panel", index),
            )
        )
    return steps


def _character_sheet_steps(request: ProjectPlanRequest, character: dict[str, Any] | None) -> list[ProjectStep]:
    views = [
        ("front", "front view character sheet pose, neutral stance, full body visible"),
        ("side", "side view character sheet pose, long corrupted tail visible"),
        ("head", "head close-up reference, asymmetrical face, eyes, teeth, horns and preserved blue fur"),
        ("details", "detail callouts, claws, tail tip, embedded eyes, feathers, tentacles, warp growths"),
    ]
    steps = []
    for index, (name, view_prompt) in enumerate(views[: max(1, min(request.variants, 4))]):
        prompt = f"{request.request}, first concept image, character sheet, {view_prompt}, plain dark background, no text labels"
        steps.append(
            ProjectStep(
                id=f"sheet_{name}",
                phase="reference",
                title=f"Character sheet {name}",
                role="character_reference",
                spec=_make_spec(prompt, request, "character_reference", index),
            )
        )
    return steps


def plan_project(request: ProjectPlanRequest) -> ProjectSpec:
    project_type = _project_type(request)
    character = _resolve_character(request)
    if character and not any(str(alias).lower() in request.request.lower() for alias in character.get("aliases", [])):
        request = request.model_copy(update={"request": f"{request.request}, {character.get('name')}"})
    if project_type == "comic_storyboard":
        steps = _comic_steps(request, character)
        title = "Comic storyboard"
    elif project_type == "character_sheet":
        steps = _character_sheet_steps(request, character)
        title = "Character sheet"
    else:
        steps = _concept_steps(request, character)
        title = "Concept batch"
    if character:
        title = f"{character.get('name', 'Character')} {title}"
    project = ProjectSpec(
        id=uuid.uuid4().hex,
        title=title,
        request=request.request,
        project_type=project_type,
        character_profile=character,
        selection_policy="manual",
        steps=steps,
        notes=[
            "Project steps are ordinary Forge jobs; generated artifacts retain normal job metadata.",
            "Selection is manual until a real semantic/vision evaluator is configured.",
        ],
    )
    for step in project.steps:
        step.spec.safety["project_id"] = project.id
        step.spec.safety["project_step_id"] = step.id
        if character:
            step.spec.safety.setdefault(
                "character_profile",
                {
                    "id": character.get("id"),
                    "name": character.get("name"),
                    "must_preserve": character.get("must_preserve", []),
                    "avoid": character.get("avoid", []),
                    "profile_source": "quality_assets/character_profiles.json",
                },
            )
    return project


def save_project(project: ProjectSpec) -> ProjectSpec:
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    project.updated_at = utc_now()
    _project_path(project.id).write_text(project.model_dump_json(indent=2), encoding="utf-8")
    return project


def get_project(project_id: str) -> ProjectSpec | None:
    path = _project_path(project_id)
    if not path.is_file():
        return None
    return ProjectSpec.model_validate_json(path.read_text(encoding="utf-8"))


def list_projects(limit: int = 100) -> list[dict[str, Any]]:
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    paths = sorted(config.PROJECTS_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    projects = []
    for path in paths[: max(1, min(limit, 500))]:
        try:
            project = ProjectSpec.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        projects.append(
            {
                "id": project.id,
                "title": project.title,
                "project_type": project.project_type,
                "status": project.status,
                "created_at": project.created_at,
                "updated_at": project.updated_at,
                "step_count": len(project.steps),
                "submitted_jobs": len([step for step in project.steps if step.job_id]),
            }
        )
    return projects
