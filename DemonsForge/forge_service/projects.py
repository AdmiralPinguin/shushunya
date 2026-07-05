from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from . import config
from .schemas import ProjectSpec, utc_now
from EyeOfTerror.Pictorium.Moriana.moriana_core.project_planner import plan_project  # noqa: F401


def _project_path(project_id: str) -> Path:
    safe = Path(str(project_id)).name
    if safe != project_id or not safe:
        raise ValueError("project_id must be a basename")
    return config.PROJECTS_DIR / f"{safe}.json"


def _project_mask_dir(project_id: str) -> Path:
    safe = Path(str(project_id)).name
    if safe != project_id or not safe:
        raise ValueError("project_id must be a basename")
    return config.PROJECTS_DIR / safe / "masks"


def create_project_mask(project_id: str, artifact_id: str, source_path: str, mask_mode: str) -> Path:
    """Create a deterministic inpaint mask for project-level SDXL edit passes."""
    from PIL import Image, ImageDraw

    source = Path(source_path).resolve()
    root = config.ROOT.resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise ValueError("source artifact must be inside DemonsForge") from exc
    if not source.is_file():
        raise ValueError("source artifact file does not exist")

    with Image.open(source) as image:
        width, height = image.size
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)

    if mask_mode == "right_body":
        draw.rounded_rectangle(
            (int(width * 0.42), int(height * 0.14), int(width * 0.96), int(height * 0.92)),
            radius=max(8, int(min(width, height) * 0.08)),
            fill=255,
        )
        draw.ellipse(
            (int(width * 0.28), int(height * 0.38), int(width * 0.92), int(height * 0.98)),
            fill=255,
        )
    elif mask_mode == "body":
        draw.ellipse(
            (int(width * 0.16), int(height * 0.28), int(width * 0.88), int(height * 0.98)),
            fill=255,
        )
    elif mask_mode == "head_right":
        draw.rounded_rectangle(
            (int(width * 0.48), int(height * 0.02), int(width * 0.94), int(height * 0.48)),
            radius=max(8, int(min(width, height) * 0.07)),
            fill=255,
        )
    elif mask_mode == "background":
        mask.paste(255)
        draw.ellipse(
            (int(width * 0.12), int(height * 0.08), int(width * 0.88), int(height * 0.98)),
            fill=0,
        )
    else:
        raise ValueError(f"unsupported mask_mode: {mask_mode}")

    target_dir = _project_mask_dir(project_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_artifact = Path(str(artifact_id)).name
    target = target_dir / f"{safe_artifact}-{mask_mode}.png"
    mask.save(target)
    return target


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
