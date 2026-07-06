from __future__ import annotations

from pathlib import Path
from typing import Any

from EyeOfTerror.Pictorium.Brigades.Comics.Workers.CharacterSheetwright.worker import build_character_sheet
from EyeOfTerror.Pictorium.Brigades.Comics.Workers.LayoutFinalis.worker import build_layout_manifest
from EyeOfTerror.Pictorium.Brigades.Comics.Workers.Panelwright.worker import build_panel_packages
from EyeOfTerror.Pictorium.Brigades.Comics.Workers.ScenarioScribe.worker import build_scenario
from EyeOfTerror.Pictorium.Brigades.Comics.Workers.StoryboardArchitect.worker import build_storyboard
from EyeOfTerror.Pictorium.Brigades.Image.Workers.ArtifactFinalis.worker import build_final_manifest
from EyeOfTerror.Pictorium.Brigades.Image.Workers.ForgeDispatcher.worker import prepare_dispatch
from EyeOfTerror.Pictorium.Brigades.Image.Workers.ImageVerifier.worker import verify_image
from EyeOfTerror.Pictorium.Brigades.Image.Workers.ModelQuartermaster.worker import inspect_resources
from EyeOfTerror.Pictorium.Brigades.Image.Workers.Promptwright.worker import prepare_image_plan
from EyeOfTerror.Pictorium.Moriana.moriana_forge_monitor import monitor_forge_job
from EyeOfTerror.Pictorium.Moriana.moriana_runtime import MorianaRunStore

try:
    from PIL import Image
except ModuleNotFoundError:  # pragma: no cover - exercised only in stripped runtimes.
    Image = None  # type: ignore[assignment]


def register_json_artifact(
    store: MorianaRunStore,
    run_id: str,
    *,
    step: str,
    payload: dict[str, Any],
    artifact_type: str,
    created_by: str,
    attempt: int,
    status: str = "draft",
    subdir: str = "brigade",
    rejection_reason: str = "",
) -> dict[str, Any]:
    path = store.write_step(run_id, f"{step}_attempt_{attempt:02d}", payload, subdir=subdir)
    return store.register_artifact(
        run_id,
        artifact_type=artifact_type,
        path=path,
        created_by=created_by,
        step=step,
        attempt=attempt,
        status=status,
        rejection_reason=rejection_reason,
        metadata={"ok": payload.get("ok"), "worker": payload.get("worker")},
    )


def make_synthetic_image(path: Path, width: int, height: int, color: tuple[int, int, int]) -> None:
    if Image is None:
        raise RuntimeError("Pillow is required for synthetic Moriana self-test artifacts")
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (width, height), color).save(path)


def blockers_from(payload: dict[str, Any]) -> list[dict[str, Any]]:
    blockers = payload.get("blockers") if isinstance(payload.get("blockers"), list) else []
    return [item for item in blockers if isinstance(item, dict)]


def job_spec_dimensions(job_spec: dict[str, Any]) -> tuple[int, int]:
    return int(job_spec.get("width") or 512), int(job_spec.get("height") or 512)


def execute_image_run(
    store: MorianaRunStore,
    run_id: str,
    task: str,
    *,
    submit: bool = False,
    test_artifact_mode: str = "",
    max_revision_cycles: int = 1,
    wait_for_result: bool = False,
    max_wait_sec: float = 0.0,
    poll_interval_sec: float = 0.5,
    run_inline_once: bool = False,
) -> dict[str, Any]:
    run_dir = store.run_dir(run_id)
    store.set_status(run_id, "planning", "Image Brigade is preparing executable package", attempt_count=1)
    plan = prepare_image_plan({"request": task, "use_memory": False, "use_thinker": False})
    register_json_artifact(store, run_id, step="image_plan", payload=plan, artifact_type="prompt", created_by="Promptwright", attempt=1, subdir="prompts")
    job_spec = plan.get("job_spec") if isinstance(plan.get("job_spec"), dict) else {}
    resources = inspect_resources({"job_spec": job_spec})
    register_json_artifact(store, run_id, step="resource_report", payload=resources, artifact_type="resource_report", created_by="ModelQuartermaster", attempt=1, subdir="parameters")
    forge_db_path = run_dir / "forge.sqlite3"
    dispatch = prepare_dispatch({"job_spec": job_spec, "submit": submit, "db_path": str(forge_db_path)})
    register_json_artifact(store, run_id, step="forge_dispatch", payload=dispatch, artifact_type="dispatch", created_by="ForgeDispatcher", attempt=1)
    store.set_status(run_id, "generating", "Forge dispatch package prepared", attempt_count=1)

    artifact_path = ""
    forge_monitor = {}
    if submit and (wait_for_result or run_inline_once):
        forge_monitor = monitor_forge_job(
            db_path=forge_db_path,
            job_record=dispatch.get("job_record") if isinstance(dispatch.get("job_record"), dict) else None,
            max_wait_sec=max_wait_sec,
            poll_interval_sec=poll_interval_sec,
            run_inline_once=run_inline_once,
        )
        register_json_artifact(
            store,
            run_id,
            step="forge_monitor",
            payload=forge_monitor,
            artifact_type="result",
            created_by="Moriana",
            attempt=1,
            status="accepted" if forge_monitor.get("ok") else "rejected",
            subdir="results",
            rejection_reason="; ".join(str(item.get("code") or "") for item in forge_monitor.get("blockers", []) if isinstance(item, dict)),
        )
        paths = forge_monitor.get("artifact_paths") if isinstance(forge_monitor.get("artifact_paths"), list) else []
        artifact_path = str(paths[0]) if paths else ""
    if test_artifact_mode in {"good", "bad", "bad_then_good"}:
        width, height = job_spec_dimensions(job_spec)
        if test_artifact_mode in {"bad", "bad_then_good"}:
            width = max(64, width // 2)
            height = max(64, height // 2)
        synthetic_path = run_dir / "artifacts" / "image_attempt_01.png"
        make_synthetic_image(synthetic_path, width, height, (80, 72, 64))
        artifact_path = str(synthetic_path)

    store.set_status(run_id, "checking", "ImageVerifier is checking generated artifact", attempt_count=1)
    verification = verify_image({"artifact_path": artifact_path, "job_spec": job_spec, "job_record": dispatch.get("job_record")})
    blockers = [*blockers_from(forge_monitor), *blockers_from(verification)]
    register_json_artifact(store, run_id, step="image_verification", payload=verification, artifact_type="verification", created_by="ImageVerifier", attempt=1, status="accepted" if not blockers else "rejected", rejection_reason="; ".join(str(item.get("code") or "") for item in blockers))
    accepted_artifact_id = ""
    if artifact_path:
        image_record = store.register_artifact(
            run_id,
            artifact_type="image",
            path=Path(artifact_path),
            created_by="ForgeDispatcher",
            step="image_generation",
            attempt=1,
            status="accepted" if not blockers else "rejected",
            rejection_reason="; ".join(str(item.get("message") or item.get("code") or "") for item in blockers),
            metadata={"job_spec": job_spec},
        )
        if not blockers:
            accepted_artifact_id = str(image_record["artifact_id"])

    final = build_final_manifest({"plan": plan, "resources": resources, "dispatch": dispatch, "verification": verification, "artifacts": [artifact_path] if artifact_path else []})
    if blockers and test_artifact_mode == "bad_then_good" and max_revision_cycles > 0:
        store.set_status(run_id, "revising", "verification rejected attempt 1; running focused revision", attempt_count=2)
        store.write_revision(run_id, 1, blockers, "regenerate_image_with_verified_dimensions")
        width, height = job_spec_dimensions(job_spec)
        revised_path = run_dir / "artifacts" / "image_attempt_02.png"
        make_synthetic_image(revised_path, width, height, (104, 94, 82))
        verification = verify_image({"artifact_path": str(revised_path), "job_spec": job_spec, "job_record": dispatch.get("job_record")})
        blockers = blockers_from(verification)
        register_json_artifact(store, run_id, step="image_verification", payload=verification, artifact_type="verification", created_by="ImageVerifier", attempt=2, status="accepted" if not blockers else "rejected")
        image_record = store.register_artifact(
            run_id,
            artifact_type="image",
            path=revised_path,
            created_by="ForgeDispatcher",
            step="image_generation",
            attempt=2,
            status="accepted" if not blockers else "rejected",
            rejection_reason="; ".join(str(item.get("message") or item.get("code") or "") for item in blockers),
            metadata={"job_spec": job_spec, "revision_of": artifact_path},
        )
        accepted_artifact_id = str(image_record["artifact_id"]) if not blockers else ""
        final = build_final_manifest({"plan": plan, "resources": resources, "dispatch": dispatch, "verification": verification, "artifacts": [str(revised_path)]})

    register_json_artifact(store, run_id, step="finalize", payload=final, artifact_type="final", created_by="ArtifactFinalis", attempt=2 if test_artifact_mode == "bad_then_good" else 1, status="final" if final.get("final_manifest", {}).get("status") == "ready" else "rejected")
    final_payload = dict(final.get("final_manifest") if isinstance(final.get("final_manifest"), dict) else {})
    final_payload.setdefault("kind", "pictorium_image_final_manifest")
    final_payload["run_id"] = run_id
    final_payload["attempt"] = 2 if test_artifact_mode == "bad_then_good" else 1
    final_payload["artifact_registry"] = str(run_dir / "artifact_registry.json")
    final_payload["accepted_artifact_id"] = accepted_artifact_id
    if blockers and final_payload.get("status") != "ready":
        store.write_revision(run_id, final_payload["attempt"], blockers, "manual_or_runtime_regeneration_required")
    store.write_final(run_id, final_payload, final_artifact_id=accepted_artifact_id)
    return {
        "ok": final_payload.get("status") == "ready",
        "governor": "Moriana",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "status": store.status(run_id),
        "final": final_payload,
        "artifacts": store.artifacts(run_id),
        "forge_monitor": forge_monitor,
    }


def execute_comic_run(
    store: MorianaRunStore,
    run_id: str,
    task: str,
    *,
    submit: bool = False,
) -> dict[str, Any]:
    run_dir = store.run_dir(run_id)
    store.set_status(run_id, "planning", "Comics Brigade is preparing scenario and storyboard", attempt_count=1)
    scenario = build_scenario({"request": task})
    register_json_artifact(store, run_id, step="scenario", payload=scenario, artifact_type="plan", created_by="ScenarioScribe", attempt=1)
    storyboard = build_storyboard({"scenario": scenario.get("scenario", {})})
    register_json_artifact(store, run_id, step="storyboard", payload=storyboard, artifact_type="plan", created_by="StoryboardArchitect", attempt=1)
    character_sheet = build_character_sheet({"scenario": scenario.get("scenario", {})})
    register_json_artifact(store, run_id, step="character_sheet", payload=character_sheet, artifact_type="character_sheet", created_by="CharacterSheetwright", attempt=1)
    store.set_status(run_id, "generating", "Panelwright is building panel generation packages", attempt_count=1)
    panels = build_panel_packages(
        {
            "storyboard": storyboard.get("storyboard", {}),
            "character_sheet": character_sheet.get("character_sheet", {}),
            "submit": submit,
            "db_path": str(run_dir / "forge.sqlite3"),
        }
    )
    register_json_artifact(store, run_id, step="panel_generation", payload=panels, artifact_type="comic_panel", created_by="Panelwright", attempt=1)
    store.set_status(run_id, "checking", "LayoutFinalis is checking layout and blockers", attempt_count=1)
    layout = build_layout_manifest(
        {
            "scenario": scenario.get("scenario", {}),
            "storyboard": storyboard.get("storyboard", {}),
            "character_sheet": character_sheet,
            "panels": panels,
        }
    )
    blockers = blockers_from(layout)
    register_json_artifact(store, run_id, step="layout", payload=layout, artifact_type="layout", created_by="LayoutFinalis", attempt=1, status="accepted" if not blockers else "rejected")
    final_payload = dict(layout.get("final_manifest") if isinstance(layout.get("final_manifest"), dict) else {})
    final_payload.setdefault("kind", "pictorium_comic_final_manifest")
    final_payload["run_id"] = run_id
    final_payload["attempt"] = 1
    final_payload["artifact_registry"] = str(run_dir / "artifact_registry.json")
    if blockers:
        store.set_status(run_id, "revising", "comic layout has unresolved blockers", attempt_count=1)
        store.write_revision(run_id, 1, blockers, "revise_panel_generation_or_layout")
    store.write_final(run_id, final_payload)
    return {
        "ok": final_payload.get("status") == "ready",
        "governor": "Moriana",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "status": store.status(run_id),
        "final": final_payload,
        "artifacts": store.artifacts(run_id),
    }
