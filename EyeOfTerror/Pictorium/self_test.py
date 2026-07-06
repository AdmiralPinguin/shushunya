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
EXTRACTION_PLAN = PICTORIUM / "Moriana" / "demonsforge_extraction_plan.md"
EXPECTED_IMAGE_WORKERS = {
    "Promptwright",
    "ModelQuartermaster",
    "ForgeDispatcher",
    "ImageVerifier",
    "ArtifactFinalis",
}
EXPECTED_COMICS_WORKERS = {
    "ScenarioScribe",
    "StoryboardArchitect",
    "CharacterSheetwright",
    "Panelwright",
    "LayoutFinalis",
}
EXPECTED_WORKERS = EXPECTED_IMAGE_WORKERS | EXPECTED_COMICS_WORKERS
EXPECTED_BRIGADES = {"Image", "Comics", "Video"}

try:
    from EyeOfTerror.Pictorium.Brigades.Comics.self_test import main as comics_brigade_self_test
    from EyeOfTerror.Pictorium.Brigades.Image.self_test import main as image_brigade_self_test
    from EyeOfTerror.Pictorium.Moriana.moriana_governor import plan_image_task, service_capabilities
    from EyeOfTerror.Pictorium.Moriana.forge_runtime.schemas import PlanRequest, ProjectPlanRequest
    from EyeOfTerror.Pictorium.Moriana.moriana_core.prompt_thinker import PlannerThinker
    from EyeOfTerror.Pictorium.Moriana.moriana_core.promptwright import plan_txt2img
    from EyeOfTerror.Pictorium.Moriana.moriana_core.project_planner import plan_project
except ModuleNotFoundError as exc:
    comics_brigade_self_test = image_brigade_self_test = plan_image_task = service_capabilities = None  # type: ignore[assignment]
    PlanRequest = ProjectPlanRequest = PlannerThinker = plan_txt2img = plan_project = None  # type: ignore[assignment]
    OPTIONAL_IMPORT_ERROR = str(exc)
else:
    OPTIONAL_IMPORT_ERROR = ""


def main() -> int:
    if not CONTRACT.exists():
        raise AssertionError(f"missing Moriana contract: {CONTRACT}")
    extraction_text = EXTRACTION_PLAN.read_text(encoding="utf-8")
    if "## Still Not Active" in extraction_text or "- Moriana governor service implementation." in extraction_text:
        raise AssertionError(f"DemonsForge extraction plan still contains stale inactive-state claims: {EXTRACTION_PLAN}")
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if payload.get("department") != "Pictorium":
        raise AssertionError(f"unexpected department: {payload}")
    governor = payload.get("governor") if isinstance(payload.get("governor"), dict) else {}
    if governor.get("name") != "Moriana" or governor.get("status") != "active":
        raise AssertionError(f"Moriana must be active after service activation: {governor}")
    if governor.get("port") != 7103:
        raise AssertionError(f"Moriana should own image governor port 7103: {governor}")
    workers = payload.get("workers") if isinstance(payload.get("workers"), list) else []
    brigades = payload.get("brigades") if isinstance(payload.get("brigades"), list) else []
    brigade_names = {str(item.get("name") or "") for item in brigades if isinstance(item, dict)}
    if brigade_names != EXPECTED_BRIGADES:
        raise AssertionError(f"unexpected brigade set: {brigade_names}")
    brigade_status = {str(item.get("name") or ""): str(item.get("status") or "") for item in brigades if isinstance(item, dict)}
    if brigade_status.get("Image") != "active" or brigade_status.get("Comics") != "active" or brigade_status.get("Video") != "planned":
        raise AssertionError(f"unexpected brigade statuses: {brigade_status}")
    for brigade in brigades:
        raw_path = str(brigade.get("path") or "")
        readme = PROJECT_ROOT / raw_path / "README.md"
        if not readme.exists():
            raise AssertionError(f"missing brigade README: {readme}")
    names = {str(item.get("name") or "") for item in workers if isinstance(item, dict)}
    if names != EXPECTED_WORKERS:
        raise AssertionError(f"unexpected worker set: {names}")
    for brigade_name, expected_workers in (("Image", EXPECTED_IMAGE_WORKERS), ("Comics", EXPECTED_COMICS_WORKERS)):
        for name in expected_workers:
            worker_py = PICTORIUM / "Brigades" / brigade_name / "Workers" / name / "worker.py"
            worker_json = PICTORIUM / "Brigades" / brigade_name / "Workers" / name / "worker.json"
            if not worker_py.exists() or not worker_json.exists():
                raise AssertionError(f"worker must expose callable module and metadata: {brigade_name}/{name}")
            worker_metadata = json.loads(worker_json.read_text(encoding="utf-8"))
            model_brain = worker_metadata.get("model_brain") if isinstance(worker_metadata, dict) else {}
            failure_policy = str(model_brain.get("failure_policy") or "") if isinstance(model_brain, dict) else ""
            if model_brain.get("required") is not True or "remains available" in failure_policy:
                raise AssertionError(f"worker metadata must require model_brain without no-LLM fallback wording: {worker_json}")
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
        image_plan = plan_image_task("нарисуй тестовую картинку 512x512", task_id="moriana-self-test-image").to_dict()
        if not image_plan.get("ok") or image_plan.get("contract", {}).get("assigned_governor") != "Moriana":
            raise AssertionError(f"Moriana failed to plan image task: {image_plan}")
        comic_plan = plan_image_task("сделай комикс 3 панели про кузню", task_id="moriana-self-test-comic").to_dict()
        if (
            not comic_plan.get("ok")
            or comic_plan.get("contract", {}).get("assigned_governor") != "Moriana"
            or comic_plan.get("contract", {}).get("kind") != "comic_generation"
            or comic_plan.get("contract", {}).get("worker_plan", [])[0].get("worker") != "ScenarioScribe"
        ):
            raise AssertionError(f"Moriana failed to plan comic task: {comic_plan}")
        series_plan = plan_image_task("сделай серию 3 изображения про кузню", task_id="moriana-self-test-series").to_dict()
        if (
            not series_plan.get("ok")
            or series_plan.get("contract", {}).get("assigned_governor") != "Moriana"
            or series_plan.get("contract", {}).get("kind") != "image_series_generation"
        ):
            raise AssertionError(f"Moriana failed to classify image series task: {series_plan}")
        capabilities_payload = service_capabilities()
        if (
            not capabilities_payload.get("ok")
            or set(capabilities_payload.get("required_workers", [])) != EXPECTED_WORKERS
        ):
            raise AssertionError(f"Moriana capabilities are incomplete: {capabilities_payload}")
        if image_brigade_self_test() != 0:
            raise AssertionError("Image Brigade self-test failed")
        if comics_brigade_self_test() != 0:
            raise AssertionError("Comics Brigade self-test failed")
    removed_forge_brains = (
        "planner.py",
        "thinker.py",
        "evaluator.py",
        "characters.py",
        "registries.py",
        "downloader.py",
        "reports.py",
        "archive_memory.py",
        "client.py",
        "config.py",
        "projects.py",
        "queue.py",
        "schemas.py",
        "server.py",
        "storage.py",
    )
    for filename in removed_forge_brains:
        old_path = PROJECT_ROOT / "DemonsForge" / "forge_service" / filename
        if old_path.exists():
            raise AssertionError(f"agent-owned module must not remain in DemonsForge: {old_path}")
    for filename in ("quality_bench.py", "shushunya_project_bench.py", "long_forge_api.py"):
        old_path = PROJECT_ROOT / "DemonsForge" / "tests" / filename
        if old_path.exists():
            raise AssertionError(f"Pictorium bench must not remain in DemonsForge tests: {old_path}")
    forge_service = PROJECT_ROOT / "DemonsForge" / "forge_service"
    allowed_forge_files = {
        forge_service / "__init__.py",
        forge_service / "engines" / "__init__.py",
        forge_service / "engines" / "base.py",
        forge_service / "engines" / "diffusers_adapter.py",
    }
    for path in forge_service.rglob("*.py"):
        if path not in allowed_forge_files:
            raise AssertionError(f"non-engine Python module must not remain in DemonsForge: {path}")
    old_tests = PROJECT_ROOT / "DemonsForge" / "tests"
    if old_tests.exists() and any(old_tests.rglob("*.py")):
        raise AssertionError(f"Pictorium tests must not remain in DemonsForge/tests: {old_tests}")
    for filename in ("run_forge_api.py", "run_forge_worker.py"):
        old_path = PROJECT_ROOT / "DemonsForge" / filename
        if old_path.exists():
            raise AssertionError(f"Forge runtime script must not remain in DemonsForge: {old_path}")
    old_brigade_dir = PICTORIUM / "Brigade"
    if old_brigade_dir.exists():
        raise AssertionError(f"old single Brigade directory must not remain: {old_brigade_dir}")
    warmaster_registry_files = [
        PROJECT_ROOT / "EyeOfTerror" / "Warmaster" / "registry" / "governors.json",
        PROJECT_ROOT / "EyeOfTerror" / "Warmaster" / "registry" / "ports.json",
    ]
    for registry_file in warmaster_registry_files:
        text = registry_file.read_text(encoding="utf-8")
        if "Forge" + "MasterGovernor" in text:
            raise AssertionError(f"legacy image governor must not remain in Warmaster registry: {registry_file}")
    print("[ok] Pictorium Moriana scaffold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
