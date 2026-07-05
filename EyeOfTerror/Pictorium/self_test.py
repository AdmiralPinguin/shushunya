#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PICTORIUM = PROJECT_ROOT / "EyeOfTerror" / "Pictorium"
CONTRACT = PICTORIUM / "Moriana" / "contracts" / "moriana_department.json"
EXPECTED_WORKERS = {
    "Promptwright",
    "ModelQuartermaster",
    "ForgeDispatcher",
    "ImageVerifier",
    "ArtifactFinalis",
}

try:
    from DemonsForge.forge_service.schemas import PlanRequest, ProjectPlanRequest
    from EyeOfTerror.Pictorium.Moriana.moriana_core.prompt_thinker import PlannerThinker
    from EyeOfTerror.Pictorium.Moriana.moriana_core.promptwright import plan_txt2img
    from EyeOfTerror.Pictorium.Moriana.moriana_core.project_planner import plan_project
except ModuleNotFoundError as exc:
    PlanRequest = ProjectPlanRequest = PlannerThinker = plan_txt2img = plan_project = None  # type: ignore[assignment]
    OPTIONAL_IMPORT_ERROR = str(exc)
else:
    OPTIONAL_IMPORT_ERROR = ""


def main() -> int:
    if not CONTRACT.exists():
        raise AssertionError(f"missing Moriana contract: {CONTRACT}")
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if payload.get("department") != "Pictorium":
        raise AssertionError(f"unexpected department: {payload}")
    governor = payload.get("governor") if isinstance(payload.get("governor"), dict) else {}
    if governor.get("name") != "Moriana" or governor.get("status") != "planned":
        raise AssertionError(f"Moriana must stay planned until service activation: {governor}")
    if governor.get("planned_port") != 7103:
        raise AssertionError(f"Moriana should inherit planned image governor port 7103: {governor}")
    workers = payload.get("workers") if isinstance(payload.get("workers"), list) else []
    names = {str(item.get("name") or "") for item in workers if isinstance(item, dict)}
    if names != EXPECTED_WORKERS:
        raise AssertionError(f"unexpected worker set: {names}")
    for name in EXPECTED_WORKERS:
        readme = PICTORIUM / "Brigade" / name / "README.md"
        if not readme.exists():
            raise AssertionError(f"missing worker README: {readme}")
    for worker in workers:
        for raw_path in worker.get("source_modules", []) if isinstance(worker, dict) else []:
            source = PROJECT_ROOT / str(raw_path)
            if not source.exists():
                raise AssertionError(f"mapped DemonsForge source does not exist: {source}")
    if OPTIONAL_IMPORT_ERROR:
        if "pydantic" not in OPTIONAL_IMPORT_ERROR:
            raise AssertionError(f"unexpected optional import failure: {OPTIONAL_IMPORT_ERROR}")
    else:
        spec = plan_txt2img(PlanRequest(request="smoke test portrait image 512x512", use_memory=False, use_thinker=False))
        if spec.type.value != "txt2img" or spec.width != 512 or spec.height != 512:
            raise AssertionError(f"Promptwright failed to plan a basic image spec: {spec}")
        project = plan_project(ProjectPlanRequest(request="comic storyboard smoke test", panels=2, use_memory=False, use_thinker=False))
        if project.project_type != "comic_storyboard" or len(project.steps) != 2:
            raise AssertionError(f"ProjectPlanner failed to plan storyboard: {project}")
        thinker_status = PlannerThinker(enabled=False, base_url="", api_key="", model="", timeout=1).status()
        if thinker_status.get("ready"):
            raise AssertionError(f"disabled thinker should not be ready: {thinker_status}")
    removed_forge_brains = (
        "planner.py",
        "thinker.py",
        "evaluator.py",
        "characters.py",
        "registries.py",
        "downloader.py",
        "reports.py",
    )
    for filename in removed_forge_brains:
        old_path = PROJECT_ROOT / "DemonsForge" / "forge_service" / filename
        if old_path.exists():
            raise AssertionError(f"agent-owned module must not remain in DemonsForge: {old_path}")
    for filename in ("quality_bench.py", "shushunya_project_bench.py", "long_forge_api.py"):
        old_path = PROJECT_ROOT / "DemonsForge" / "tests" / filename
        if old_path.exists():
            raise AssertionError(f"Pictorium bench must not remain in DemonsForge tests: {old_path}")
    print("[ok] Pictorium Moriana scaffold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
