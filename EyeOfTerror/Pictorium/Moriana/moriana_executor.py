from __future__ import annotations

import re
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
from EyeOfTerror.Pictorium.Moriana.moriana_quality import write_quality_report
from EyeOfTerror.Pictorium.Moriana.moriana_revision import read_revision_decision, write_revision_decision
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
    model_guidance = payload.get("model_guidance") if isinstance(payload.get("model_guidance"), dict) else {}
    metadata = {"ok": payload.get("ok"), "worker": payload.get("worker")}
    if model_guidance:
        metadata["model_guidance_status"] = model_guidance.get("status")
        metadata["model_guidance_required"] = model_guidance.get("required")
        metadata["model_guidance_kind"] = model_guidance.get("kind")
    return store.register_artifact(
        run_id,
        artifact_type=artifact_type,
        path=path,
        created_by=created_by,
        step=step,
        attempt=attempt,
        status=status,
        rejection_reason=rejection_reason,
        metadata=metadata,
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


def requested_image_count(task: str, default: int = 3) -> int:
    lowered = task.lower()
    for pattern in (
        r"\b(\d{1,2})\s*(?:картин|изображен|images|pictures)\b",
        r"(?:серия|series|batch)\s*(?:из|of)?\s*(\d{1,2})",
    ):
        match = re.search(pattern, lowered)
        if match:
            return max(2, min(8, int(match.group(1))))
    return default


def next_attempt(store: MorianaRunStore, run_id: str) -> int:
    attempts = [int(item.get("attempt") or 0) for item in store.artifacts(run_id) if isinstance(item.get("attempt"), int)]
    status_attempt = int(store.status(run_id).get("attempt_count") or 0)
    attempts.append(status_attempt)
    return max(attempts or [0]) + 1


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
    quality_report = write_quality_report(store, run_id)
    revision_decision = write_revision_decision(store, run_id, quality_report)
    return {
        "ok": final_payload.get("status") == "ready",
        "governor": "Moriana",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "status": store.status(run_id),
        "final": final_payload,
        "artifacts": store.artifacts(run_id),
        "forge_monitor": forge_monitor,
        "quality_report": quality_report,
        "revision_decision": revision_decision,
    }


def execute_revision_run(
    store: MorianaRunStore,
    run_id: str,
    *,
    submit: bool = False,
    test_artifact_mode: str = "",
    wait_for_result: bool = False,
    max_wait_sec: float = 0.0,
    poll_interval_sec: float = 0.5,
    run_inline_once: bool = False,
) -> dict[str, Any]:
    status = store.status(run_id)
    task_kind = str(status.get("task_kind") or "")
    task = str(status.get("task") or "")
    decision = read_revision_decision(store.run_dir(run_id))
    if decision.get("error"):
        quality_report = write_quality_report(store, run_id)
        decision = write_revision_decision(store, run_id, quality_report)
    if decision.get("action") == "accept_final" and not decision.get("revision_required"):
        return {
            "ok": True,
            "governor": "Moriana",
            "run_id": run_id,
            "status": store.status(run_id),
            "revision_decision": decision,
            "final": store.final_result(run_id),
            "revision_applied": False,
        }
    if task_kind == "image_series":
        attempt = next_attempt(store, run_id)
        store.set_status(run_id, "revising", "Moriana is applying image-series revision decision", attempt_count=attempt)
        result = execute_image_series_run(
            store,
            run_id,
            task,
            submit=submit,
            test_artifact_mode=test_artifact_mode,
            wait_for_result=wait_for_result,
            max_wait_sec=max_wait_sec,
            poll_interval_sec=poll_interval_sec,
            run_inline_once=run_inline_once,
            attempt=attempt,
            revision_decision=decision,
        )
        execution_summary = {
            "kind": "pictorium_revision_execution",
            "run_id": run_id,
            "attempt": attempt,
            "decision": decision,
            "ok": result.get("ok"),
            "final_status": result.get("final", {}).get("status") if isinstance(result.get("final"), dict) else "",
            "next_decision": result.get("revision_decision"),
        }
        execution_path = store.write_step(run_id, f"revision_execution_attempt_{attempt:02d}", execution_summary, subdir="revisions")
        store.register_artifact(
            run_id,
            artifact_type="revision_execution",
            path=execution_path,
            created_by="Moriana",
            step="revision_execution",
            attempt=attempt,
            status="accepted" if result.get("ok") else "rejected",
            metadata={"action": decision.get("action"), "task_kind": task_kind},
        )
        result["revision_execution"] = execution_summary
        return result
    if task_kind == "comic":
        attempt = next_attempt(store, run_id)
        store.set_status(run_id, "revising", "Moriana is applying comic revision decision", attempt_count=attempt)
        result = execute_comic_run(
            store,
            run_id,
            task,
            submit=submit,
            test_artifact_mode=test_artifact_mode,
            wait_for_result=wait_for_result,
            max_wait_sec=max_wait_sec,
            poll_interval_sec=poll_interval_sec,
            run_inline_once=run_inline_once,
            attempt=attempt,
            revision_decision=decision,
        )
        execution_summary = {
            "kind": "pictorium_revision_execution",
            "run_id": run_id,
            "attempt": attempt,
            "decision": decision,
            "ok": result.get("ok"),
            "final_status": result.get("final", {}).get("status") if isinstance(result.get("final"), dict) else "",
            "next_decision": result.get("revision_decision"),
        }
        execution_path = store.write_step(run_id, f"revision_execution_attempt_{attempt:02d}", execution_summary, subdir="revisions")
        store.register_artifact(
            run_id,
            artifact_type="revision_execution",
            path=execution_path,
            created_by="Moriana",
            step="revision_execution",
            attempt=attempt,
            status="accepted" if result.get("ok") else "rejected",
            metadata={"action": decision.get("action"), "task_kind": task_kind},
        )
        result["revision_execution"] = execution_summary
        return result
    if task_kind != "image":
        raise ValueError(f"apply_revision supports image, image_series, and comic runs; got {task_kind!r}")

    run_dir = store.run_dir(run_id)
    attempt = next_attempt(store, run_id)
    store.set_status(run_id, "revising", "Moriana is applying revision decision", attempt_count=attempt)
    execution_summary = {
        "kind": "pictorium_revision_execution",
        "run_id": run_id,
        "attempt": attempt,
        "decision": decision,
        "rerun_steps": decision.get("rerun_steps", []),
        "downstream_steps": decision.get("downstream_steps", []),
    }
    plan = prepare_image_plan({"request": f"{task}\nRevision: {decision.get('reason')}", "use_memory": False, "use_thinker": False})
    register_json_artifact(store, run_id, step="image_plan", payload=plan, artifact_type="prompt", created_by="Promptwright", attempt=attempt, subdir="prompts")
    job_spec = plan.get("job_spec") if isinstance(plan.get("job_spec"), dict) else {}
    resources = inspect_resources({"job_spec": job_spec})
    register_json_artifact(store, run_id, step="resource_report", payload=resources, artifact_type="resource_report", created_by="ModelQuartermaster", attempt=attempt, subdir="parameters")
    forge_db_path = run_dir / "forge.sqlite3"
    dispatch = prepare_dispatch({"job_spec": job_spec, "submit": submit, "db_path": str(forge_db_path)})
    register_json_artifact(store, run_id, step="forge_dispatch", payload=dispatch, artifact_type="dispatch", created_by="ForgeDispatcher", attempt=attempt)
    store.set_status(run_id, "generating", "Revision generation package prepared", attempt_count=attempt)

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
            attempt=attempt,
            status="accepted" if forge_monitor.get("ok") else "rejected",
            subdir="results",
            rejection_reason="; ".join(str(item.get("code") or "") for item in forge_monitor.get("blockers", []) if isinstance(item, dict)),
        )
        paths = forge_monitor.get("artifact_paths") if isinstance(forge_monitor.get("artifact_paths"), list) else []
        artifact_path = str(paths[0]) if paths else ""
    if test_artifact_mode in {"good", "revision_good"}:
        width, height = job_spec_dimensions(job_spec)
        synthetic_path = run_dir / "artifacts" / f"image_attempt_{attempt:02d}.png"
        make_synthetic_image(synthetic_path, width, height, (104, 94, 82))
        artifact_path = str(synthetic_path)

    store.set_status(run_id, "checking", "ImageVerifier is checking revised artifact", attempt_count=attempt)
    verification = verify_image({"artifact_path": artifact_path, "job_spec": job_spec, "job_record": dispatch.get("job_record")})
    blockers = [*blockers_from(resources), *blockers_from(dispatch), *blockers_from(forge_monitor), *blockers_from(verification)]
    register_json_artifact(
        store,
        run_id,
        step="image_verification",
        payload=verification,
        artifact_type="verification",
        created_by="ImageVerifier",
        attempt=attempt,
        status="accepted" if not blockers else "rejected",
        rejection_reason="; ".join(str(item.get("code") or "") for item in blockers),
    )
    accepted_artifact_id = ""
    if artifact_path:
        image_record = store.register_artifact(
            run_id,
            artifact_type="image",
            path=Path(artifact_path),
            created_by="ForgeDispatcher",
            step="image_generation",
            attempt=attempt,
            status="accepted" if not blockers else "rejected",
            rejection_reason="; ".join(str(item.get("message") or item.get("code") or "") for item in blockers),
            metadata={"job_spec": job_spec, "revision_decision": decision},
        )
        if not blockers:
            accepted_artifact_id = str(image_record["artifact_id"])
    final = build_final_manifest({"plan": plan, "resources": resources, "dispatch": dispatch, "verification": verification, "artifacts": [artifact_path] if artifact_path else []})
    register_json_artifact(store, run_id, step="finalize", payload=final, artifact_type="final", created_by="ArtifactFinalis", attempt=attempt, status="final" if final.get("final_manifest", {}).get("status") == "ready" else "rejected")
    final_payload = dict(final.get("final_manifest") if isinstance(final.get("final_manifest"), dict) else {})
    final_payload.setdefault("kind", "pictorium_image_final_manifest")
    final_payload["run_id"] = run_id
    final_payload["attempt"] = attempt
    final_payload["artifact_registry"] = str(run_dir / "artifact_registry.json")
    final_payload["accepted_artifact_id"] = accepted_artifact_id
    final_payload["revision_decision"] = decision
    if blockers and final_payload.get("status") != "ready":
        store.write_revision(run_id, attempt, blockers, "revision_execution_needs_followup")
    store.write_final(run_id, final_payload, final_artifact_id=accepted_artifact_id)
    quality_report = write_quality_report(store, run_id)
    next_decision = write_revision_decision(store, run_id, quality_report)
    execution_summary.update(
        {
            "ok": final_payload.get("status") == "ready",
            "artifact_path": artifact_path,
            "accepted_artifact_id": accepted_artifact_id,
            "blockers": blockers,
            "next_decision": next_decision,
        }
    )
    execution_path = store.write_step(run_id, f"revision_execution_attempt_{attempt:02d}", execution_summary, subdir="revisions")
    store.register_artifact(
        run_id,
        artifact_type="revision_execution",
        path=execution_path,
        created_by="Moriana",
        step="revision_execution",
        attempt=attempt,
        status="accepted" if execution_summary["ok"] else "rejected",
        rejection_reason="; ".join(str(item.get("code") or "") for item in blockers),
        metadata={"action": decision.get("action"), "next_action": next_decision.get("action")},
    )
    return {
        "ok": final_payload.get("status") == "ready",
        "governor": "Moriana",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "status": store.status(run_id),
        "final": final_payload,
        "artifacts": store.artifacts(run_id),
        "quality_report": quality_report,
        "revision_decision": next_decision,
        "revision_execution": execution_summary,
    }


def execute_existing_image_artifact_run(
    store: MorianaRunStore,
    run_id: str,
    task: str,
    *,
    artifact_path: str,
    job_spec: dict[str, Any] | None = None,
    created_by: str = "external_live_artifact",
) -> dict[str, Any]:
    run_dir = store.run_dir(run_id)
    store.set_status(run_id, "checking", "ImageVerifier is checking supplied live artifact", attempt_count=1)
    spec = job_spec or prepare_image_plan({"request": task, "use_memory": False, "use_thinker": False}).get("job_spec", {})
    plan = {
        "ok": True,
        "worker": "Promptwright",
        "plan_kind": "external_artifact",
        "artifact": "/work/pictorium/image_plan.json",
        "job_spec": spec,
    }
    register_json_artifact(store, run_id, step="image_plan", payload=plan, artifact_type="prompt", created_by="Promptwright", attempt=1, subdir="prompts")
    artifact = Path(artifact_path)
    verification = verify_image({"artifact_path": str(artifact), "job_spec": spec, "job_record": {}})
    blockers = blockers_from(verification)
    register_json_artifact(
        store,
        run_id,
        step="image_verification",
        payload=verification,
        artifact_type="verification",
        created_by="ImageVerifier",
        attempt=1,
        status="accepted" if not blockers else "rejected",
        rejection_reason="; ".join(str(item.get("code") or "") for item in blockers),
    )
    accepted_artifact_id = ""
    if artifact.exists():
        image_record = store.register_artifact(
            run_id,
            artifact_type="image",
            path=artifact,
            created_by=created_by,
            step="live_artifact_ingest",
            attempt=1,
            status="accepted" if not blockers else "rejected",
            rejection_reason="; ".join(str(item.get("message") or item.get("code") or "") for item in blockers),
            metadata={"job_spec": spec, "live_artifact": True},
        )
        accepted_artifact_id = str(image_record["artifact_id"]) if not blockers else ""
    final = build_final_manifest({"plan": plan, "resources": {}, "dispatch": {}, "verification": verification, "artifacts": [str(artifact)] if artifact.exists() else []})
    final_payload = dict(final.get("final_manifest") if isinstance(final.get("final_manifest"), dict) else {})
    final_payload.setdefault("kind", "pictorium_image_final_manifest")
    final_payload["run_id"] = run_id
    final_payload["attempt"] = 1
    final_payload["artifact_registry"] = str(run_dir / "artifact_registry.json")
    final_payload["accepted_artifact_id"] = accepted_artifact_id
    if blockers and final_payload.get("status") != "ready":
        store.write_revision(run_id, 1, blockers, "revise supplied artifact or regenerate image")
    store.write_final(run_id, final_payload, final_artifact_id=accepted_artifact_id)
    quality_report = write_quality_report(store, run_id)
    revision_decision = write_revision_decision(store, run_id, quality_report)
    return {
        "ok": final_payload.get("status") == "ready",
        "governor": "Moriana",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "status": store.status(run_id),
        "final": final_payload,
        "artifacts": store.artifacts(run_id),
        "quality_report": quality_report,
        "revision_decision": revision_decision,
    }


def execute_image_series_run(
    store: MorianaRunStore,
    run_id: str,
    task: str,
    *,
    submit: bool = False,
    test_artifact_mode: str = "",
    wait_for_result: bool = False,
    max_wait_sec: float = 0.0,
    poll_interval_sec: float = 0.5,
    run_inline_once: bool = False,
    attempt: int = 1,
    revision_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_dir = store.run_dir(run_id)
    count = requested_image_count(task)
    store.set_status(run_id, "planning", f"Image Brigade is preparing {count} linked image packages", attempt_count=attempt)
    items = []
    all_blockers: list[dict[str, Any]] = []
    accepted_artifact_ids = []
    for index in range(1, count + 1):
        step_prefix = f"series_{index:02d}"
        image_task = f"{task}. Image {index} of {count}. Keep style and subject continuity across the series."
        plan = prepare_image_plan({"request": image_task, "use_memory": False, "use_thinker": False})
        register_json_artifact(store, run_id, step=f"{step_prefix}_image_plan", payload=plan, artifact_type="prompt", created_by="Promptwright", attempt=attempt, subdir="prompts")
        job_spec = plan.get("job_spec") if isinstance(plan.get("job_spec"), dict) else {}
        resources = inspect_resources({"job_spec": job_spec})
        register_json_artifact(store, run_id, step=f"{step_prefix}_resource_report", payload=resources, artifact_type="resource_report", created_by="ModelQuartermaster", attempt=attempt, subdir="parameters")
        forge_db_path = run_dir / "forge.sqlite3"
        dispatch = prepare_dispatch({"job_spec": job_spec, "submit": submit, "db_path": str(forge_db_path)})
        register_json_artifact(store, run_id, step=f"{step_prefix}_forge_dispatch", payload=dispatch, artifact_type="dispatch", created_by="ForgeDispatcher", attempt=attempt)
        store.set_status(run_id, "generating", f"Series image {index}/{count} dispatch package prepared", attempt_count=attempt)
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
                step=f"{step_prefix}_forge_monitor",
                payload=forge_monitor,
                artifact_type="result",
                created_by="Moriana",
                attempt=attempt,
                status="accepted" if forge_monitor.get("ok") else "rejected",
                subdir="results",
                rejection_reason="; ".join(str(item.get("code") or "") for item in forge_monitor.get("blockers", []) if isinstance(item, dict)),
            )
            paths = forge_monitor.get("artifact_paths") if isinstance(forge_monitor.get("artifact_paths"), list) else []
            artifact_path = str(paths[0]) if paths else ""
        if test_artifact_mode in {"good", "series_good"}:
            width, height = job_spec_dimensions(job_spec)
            synthetic_path = run_dir / "artifacts" / f"series_image_{index:02d}_attempt_{attempt:02d}.png"
            make_synthetic_image(synthetic_path, width, height, (60 + index * 12, 70 + index * 8, 90 + index * 5))
            artifact_path = str(synthetic_path)
        store.set_status(run_id, "checking", f"ImageVerifier is checking series image {index}/{count}", attempt_count=attempt)
        verification = verify_image({"artifact_path": artifact_path, "job_spec": job_spec, "job_record": dispatch.get("job_record")})
        blockers = [*blockers_from(resources), *blockers_from(dispatch), *blockers_from(forge_monitor), *blockers_from(verification)]
        all_blockers.extend({"series_index": index, **blocker} for blocker in blockers)
        register_json_artifact(
            store,
            run_id,
            step=f"{step_prefix}_image_verification",
            payload=verification,
            artifact_type="verification",
            created_by="ImageVerifier",
            attempt=attempt,
            status="accepted" if not blockers else "rejected",
            rejection_reason="; ".join(str(item.get("code") or "") for item in blockers),
        )
        image_artifact_id = ""
        if artifact_path:
            image_record = store.register_artifact(
                run_id,
                artifact_type="image",
                path=Path(artifact_path),
                created_by="ForgeDispatcher",
                step=f"{step_prefix}_image_generation",
                attempt=attempt,
                status="accepted" if not blockers else "rejected",
                rejection_reason="; ".join(str(item.get("message") or item.get("code") or "") for item in blockers),
                metadata={"job_spec": job_spec, "series_index": index, "series_count": count, "revision_decision": revision_decision or {}},
            )
            image_artifact_id = str(image_record["artifact_id"])
            if not blockers:
                accepted_artifact_ids.append(image_artifact_id)
        items.append(
            {
                "index": index,
                "prompt_artifact": f"/work/pictorium/series/{index:02d}/image_plan.json",
                "job_id": dispatch.get("job_record", {}).get("id") if isinstance(dispatch.get("job_record"), dict) else "",
                "artifact_path": artifact_path,
                "artifact_id": image_artifact_id,
                "status": "accepted" if not blockers else "blocked",
                "blockers": blockers,
            }
        )
    final_payload = {
        "kind": "pictorium_image_series_final_manifest",
        "run_id": run_id,
        "status": "ready" if not all_blockers and len(accepted_artifact_ids) == count else "blocked",
        "series_count": count,
        "accepted_artifact_ids": accepted_artifact_ids,
        "items": items,
        "blockers": all_blockers,
        "artifact_registry": str(run_dir / "artifact_registry.json"),
        "revision_decision": revision_decision or {},
        "handoff": {
            "ready_for_delivery": not all_blockers and len(accepted_artifact_ids) == count,
            "requires_generation": len(accepted_artifact_ids) < count,
            "requires_revision": bool(all_blockers),
        },
        "attempt": attempt,
    }
    if all_blockers:
        store.set_status(run_id, "revising", "one or more series images need revision", attempt_count=attempt)
        store.write_revision(run_id, attempt, all_blockers, "revise blocked series images and rerun final packaging")
    store.write_final(run_id, final_payload, final_artifact_id=accepted_artifact_ids[0] if accepted_artifact_ids else "")
    quality_report = write_quality_report(store, run_id)
    revision_decision = write_revision_decision(store, run_id, quality_report)
    return {
        "ok": final_payload["status"] == "ready",
        "governor": "Moriana",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "status": store.status(run_id),
        "final": final_payload,
        "artifacts": store.artifacts(run_id),
        "quality_report": quality_report,
        "revision_decision": revision_decision,
    }


def execute_comic_run(
    store: MorianaRunStore,
    run_id: str,
    task: str,
    *,
    submit: bool = False,
    test_artifact_mode: str = "",
    wait_for_result: bool = False,
    max_wait_sec: float = 0.0,
    poll_interval_sec: float = 0.5,
    run_inline_once: bool = False,
    attempt: int = 1,
    revision_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_dir = store.run_dir(run_id)
    store.set_status(run_id, "planning", "Comics Brigade is preparing scenario and storyboard", attempt_count=attempt)
    scenario = build_scenario({"request": task})
    register_json_artifact(store, run_id, step="scenario", payload=scenario, artifact_type="plan", created_by="ScenarioScribe", attempt=attempt)
    storyboard = build_storyboard({"scenario": scenario.get("scenario", {})})
    register_json_artifact(store, run_id, step="storyboard", payload=storyboard, artifact_type="plan", created_by="StoryboardArchitect", attempt=attempt)
    character_sheet = build_character_sheet({"scenario": scenario.get("scenario", {})})
    register_json_artifact(store, run_id, step="character_sheet", payload=character_sheet, artifact_type="character_sheet", created_by="CharacterSheetwright", attempt=attempt)
    store.set_status(run_id, "generating", "Panelwright is building panel generation packages", attempt_count=attempt)
    panels = build_panel_packages(
        {
            "storyboard": storyboard.get("storyboard", {}),
            "character_sheet": character_sheet.get("character_sheet", {}),
            "submit": submit,
            "db_path": str(run_dir / "forge.sqlite3"),
        }
    )
    register_json_artifact(store, run_id, step="panel_generation", payload=panels, artifact_type="comic_panel", created_by="Panelwright", attempt=attempt)
    panel_artifacts: list[dict[str, Any]] = []
    panel_art_blockers: list[dict[str, Any]] = []
    panel_packages = panels.get("panels") if isinstance(panels.get("panels"), list) else []
    if submit and (wait_for_result or run_inline_once):
        forge_db_path = run_dir / "forge.sqlite3"
        for index, panel in enumerate(panel_packages, start=1):
            if not isinstance(panel, dict):
                continue
            panel_id = str(panel.get("panel_id") or f"panel_{index:02d}")
            dispatch = panel.get("dispatch") if isinstance(panel.get("dispatch"), dict) else {}
            job_record = dispatch.get("job_record") if isinstance(dispatch.get("job_record"), dict) else None
            monitor = monitor_forge_job(
                db_path=forge_db_path,
                job_record=job_record,
                max_wait_sec=max_wait_sec,
                poll_interval_sec=poll_interval_sec,
                run_inline_once=run_inline_once,
            )
            register_json_artifact(
                store,
                run_id,
                step=f"panel_{index:02d}_forge_monitor",
                payload=monitor,
                artifact_type="result",
                created_by="Moriana",
                attempt=attempt,
                status="accepted" if monitor.get("ok") else "rejected",
                subdir="results",
                rejection_reason="; ".join(str(item.get("code") or "") for item in monitor.get("blockers", []) if isinstance(item, dict)),
            )
            paths = monitor.get("artifact_paths") if isinstance(monitor.get("artifact_paths"), list) else []
            job_spec = (
                panel.get("image_plan", {}).get("job_spec", {})
                if isinstance(panel.get("image_plan"), dict)
                else {}
            )
            monitor_blockers = blockers_from(monitor)
            if not paths:
                panel_art_blockers.extend({"panel_id": panel_id, **blocker} for blocker in monitor_blockers)
                continue
            for artifact_index, artifact_path in enumerate(paths, start=1):
                verification = verify_image({"artifact_path": str(artifact_path), "job_spec": job_spec, "job_record": job_record or {}})
                verification_blockers = [*monitor_blockers, *blockers_from(verification)]
                register_json_artifact(
                    store,
                    run_id,
                    step=f"panel_{index:02d}_image_verification",
                    payload=verification,
                    artifact_type="verification",
                    created_by="ImageVerifier",
                    attempt=attempt,
                    status="accepted" if not verification_blockers else "rejected",
                    rejection_reason="; ".join(str(item.get("code") or "") for item in verification_blockers),
                )
                record = store.register_artifact(
                    run_id,
                    artifact_type="comic_panel",
                    path=Path(str(artifact_path)),
                    created_by="ForgeDispatcher",
                    step="panel_art_generation",
                    attempt=attempt,
                    status="accepted" if not verification_blockers else "rejected",
                    rejection_reason="; ".join(str(item.get("message") or item.get("code") or "") for item in verification_blockers),
                    metadata={
                        "panel_id": panel_id,
                        "panel_order": panel.get("order") or index,
                        "artifact_index": artifact_index,
                        "job_spec": job_spec,
                        "revision_decision": revision_decision or {},
                    },
                )
                panel_artifacts.append(record)
                panel_art_blockers.extend({"panel_id": panel_id, **blocker} for blocker in verification_blockers)
    if not panel_artifacts and test_artifact_mode in {"comic_panels_good", "good"}:
        for index, panel in enumerate(panel_packages, start=1):
            panel_id = str(panel.get("panel_id") or f"panel_{index:02d}") if isinstance(panel, dict) else f"panel_{index:02d}"
            synthetic_path = run_dir / "artifacts" / f"comic_panel_{index:02d}_attempt_{attempt:02d}.png"
            make_synthetic_image(synthetic_path, 512, 512, (50 + index * 12, 48 + index * 10, 64 + index * 8))
            record = store.register_artifact(
                run_id,
                artifact_type="comic_panel",
                path=synthetic_path,
                created_by="ForgeDispatcher",
                step="panel_art_generation",
                attempt=attempt,
                status="accepted",
                metadata={
                    "panel_id": panel_id,
                    "panel_order": panel.get("order") if isinstance(panel, dict) else index,
                    "synthetic_quality_fixture": True,
                    "revision_decision": revision_decision or {},
                },
            )
            panel_artifacts.append(record)
    store.set_status(run_id, "checking", "LayoutFinalis is checking layout and blockers", attempt_count=attempt)
    layout = build_layout_manifest(
        {
            "scenario": scenario.get("scenario", {}),
            "storyboard": storyboard.get("storyboard", {}),
            "character_sheet": character_sheet,
            "panels": panels,
        }
    )
    blockers = [*blockers_from(layout), *panel_art_blockers]
    register_json_artifact(store, run_id, step="layout", payload=layout, artifact_type="layout", created_by="LayoutFinalis", attempt=attempt, status="accepted" if not blockers else "rejected")
    final_payload = dict(layout.get("final_manifest") if isinstance(layout.get("final_manifest"), dict) else {})
    final_payload.setdefault("kind", "pictorium_comic_final_manifest")
    final_payload["run_id"] = run_id
    final_payload["attempt"] = attempt
    final_payload["artifact_registry"] = str(run_dir / "artifact_registry.json")
    final_payload["revision_decision"] = revision_decision or {}
    final_payload["blockers"] = [*final_payload.get("blockers", []), *panel_art_blockers] if isinstance(final_payload.get("blockers"), list) else panel_art_blockers
    if blockers:
        final_payload["status"] = "blocked"
        handoff = final_payload.get("handoff") if isinstance(final_payload.get("handoff"), dict) else {}
        handoff["ready_for_delivery"] = False
        handoff["requires_revision"] = True
        final_payload["handoff"] = handoff
    final_payload["panel_artifacts"] = [
        {
            "artifact_id": item.get("artifact_id"),
            "path": item.get("path"),
            "panel_id": item.get("metadata", {}).get("panel_id") if isinstance(item.get("metadata"), dict) else "",
        }
        for item in panel_artifacts
    ]
    final_payload["panel_artifact_count"] = len(panel_artifacts)
    if panel_packages and len(panel_artifacts) < len(panel_packages):
        final_payload.setdefault("audit_limits", [])
        if isinstance(final_payload["audit_limits"], list):
            final_payload["audit_limits"].append("panel art artifacts are not generated for every planned comic panel")
    if blockers:
        store.set_status(run_id, "revising", "comic layout has unresolved blockers", attempt_count=attempt)
        store.write_revision(run_id, attempt, blockers, "revise_panel_generation_or_layout")
    store.write_final(run_id, final_payload)
    quality_report = write_quality_report(store, run_id)
    revision_decision = write_revision_decision(store, run_id, quality_report)
    return {
        "ok": final_payload.get("status") == "ready",
        "governor": "Moriana",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "status": store.status(run_id),
        "final": final_payload,
        "artifacts": store.artifacts(run_id),
        "quality_report": quality_report,
        "revision_decision": revision_decision,
    }
