#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
WARMMASTER_ROOT = PROJECT_ROOT / "EyeOfTerror" / "Warmaster"
if str(WARMMASTER_ROOT) not in sys.path:
    sys.path.insert(0, str(WARMMASTER_ROOT))

from PIL import Image

from EyeOfTerror.common_protocol import commander_order, validate_protocol_payload
from EyeOfTerror.Pictorium.Brigades.Image.Workers.ArtifactFinalis.worker import build_final_manifest
from EyeOfTerror.Pictorium.Brigades.Image.Workers.ForgeDispatcher.worker import prepare_dispatch
from EyeOfTerror.Pictorium.Brigades.Image.Workers.ImageVerifier.worker import verify_image
from EyeOfTerror.Pictorium.Brigades.Image.Workers.ModelQuartermaster.worker import inspect_resources
from EyeOfTerror.Pictorium.Brigades.Image.Workers.Promptwright.worker import prepare_image_plan
from EyeOfTerror.Pictorium.testing.fake_model_server import fake_pictorium_model
from EyeOfTerror.Warmaster.eye_of_terror.task_prepare import prepare_task, preflight_task


def moriana_command(task: str, task_id: str) -> dict[str, object]:
    order = commander_order(
        f"mission-{task_id}",
        to="Moriana",
        user_request=task,
        commander_intent="Проверить визуальный пайплайн через бригадира Мориану.",
        primary_goal=task,
        success_conditions=[
            "Moriana produces a valid image/comic governor plan",
            "worker_order packets are generated from the shared mission protocol",
            "final artifacts pass the local forge verification checks",
        ],
        constraints=["Do not answer the user directly from the governor layer."],
        escalate_to_user_if=["the requested visual task cannot be represented by the active image brigade"],
    )
    validate_protocol_payload(order, expected_type="commander_order")
    return order


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _main() -> int:
    task = "нарисуй картинку тестового механикум-алтаря stable diffusion 512x512"
    with tempfile.TemporaryDirectory(prefix="moriana-e2e-") as tmp:
        root = Path(tmp)
        run_root = root / "runs"
        task_id = "moriana-e2e-image"
        command = moriana_command(task, task_id)
        preflight = preflight_task(task, task_id, run_root, forced_governor="Moriana", commander_order=command, require_commander_order=True)
        if (
            not preflight.get("ok")
            or preflight.get("governor") != "Moriana"
            or preflight.get("contract_summary", {}).get("step_count") != 5
        ):
            raise AssertionError(f"Warmaster preflight did not route through Moriana: {preflight}")

        prepared = prepare_task(task, task_id, run_root, forced_governor="Moriana", commander_order=command, require_commander_order=True)
        if not prepared.get("ok") or prepared.get("governor") != "Moriana":
            raise AssertionError(f"Warmaster prepare did not create Moriana run: {prepared}")
        run_dir = Path(str(prepared["run_dir"]))
        status = load_json(run_dir / "status.json")
        if status.get("governor") != "Moriana" or status.get("step_count") != 5:
            raise AssertionError(f"bad prepared status: {status}")
        dispatch_workers = [step.get("worker") for step in status.get("steps", []) if isinstance(step, dict)]
        if dispatch_workers != ["Promptwright", "ModelQuartermaster", "ForgeDispatcher", "ImageVerifier", "ArtifactFinalis"]:
            raise AssertionError(f"bad dispatch order: {dispatch_workers}")

        plan = prepare_image_plan({"request": task, "use_memory": False, "use_thinker": False})
        resources = inspect_resources({"job_spec": plan["job_spec"]})
        dispatch = prepare_dispatch({"job_spec": plan["job_spec"], "submit": True, "db_path": str(root / "forge.sqlite3")})
        if not dispatch.get("ok") or not dispatch.get("job_record"):
            raise AssertionError(f"Forge dispatch did not submit queued job: {dispatch}")

        artifact_path = root / "artifact.png"
        Image.new("RGB", (512, 512), (80, 72, 64)).save(artifact_path)
        verification = verify_image({"artifact_path": str(artifact_path), "job_spec": plan["job_spec"], "job_record": dispatch["job_record"]})
        final = build_final_manifest(
            {
                "plan": plan,
                "resources": resources,
                "dispatch": dispatch,
                "verification": verification,
                "artifacts": [str(artifact_path)],
            }
        )
        if not final.get("ok") or final.get("final_manifest", {}).get("status") != "ready":
            raise AssertionError(f"final image manifest is not ready: {final}")

        comic_task = "сделай комикс 3 панели про техножреца у древней кузни"
        comic_id = "moriana-e2e-comic"
        comic_command = moriana_command(comic_task, comic_id)
        comic_preflight = preflight_task(comic_task, comic_id, run_root, forced_governor="Moriana", commander_order=comic_command, require_commander_order=True)
        if (
            not comic_preflight.get("ok")
            or comic_preflight.get("governor") != "Moriana"
            or comic_preflight.get("contract_summary", {}).get("steps", [])[0].get("worker") != "ScenarioScribe"
        ):
            raise AssertionError(f"Warmaster preflight did not route comic through Moriana: {comic_preflight}")
        comic_prepared = prepare_task(comic_task, comic_id, run_root, forced_governor="Moriana", commander_order=comic_command, require_commander_order=True)
        if not comic_prepared.get("ok") or comic_prepared.get("governor") != "Moriana":
            raise AssertionError(f"Warmaster prepare did not create Moriana comic run: {comic_prepared}")
        comic_status = load_json(Path(str(comic_prepared["run_dir"])) / "status.json")
        comic_workers = [step.get("worker") for step in comic_status.get("steps", []) if isinstance(step, dict)]
        if comic_workers != ["ScenarioScribe", "StoryboardArchitect", "CharacterSheetwright", "Panelwright", "LayoutFinalis"]:
            raise AssertionError(f"bad comic dispatch order: {comic_workers}")
    print("[ok] Moriana Warmaster -> Image Brigade -> ForgeRuntime e2e")
    return 0


def main() -> int:
    with fake_pictorium_model():
        return _main()


if __name__ == "__main__":
    raise SystemExit(main())
