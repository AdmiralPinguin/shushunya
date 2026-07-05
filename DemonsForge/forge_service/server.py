from __future__ import annotations

from pathlib import Path
import hashlib
import sys
import time

import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from . import __version__, config
from .archive_memory import ArchiveMemoryClient
from EyeOfTerror.Pictorium.Moriana.moriana_core.asset_catalog import (
    ASPECT_PRESETS,
    SAMPLERS,
    SCHEDULERS,
    asset_profiles,
    capabilities,
    clear_registry_caches,
    discover_embeddings,
    discover_loras,
    discover_models,
)
from EyeOfTerror.Pictorium.Moriana.moriana_core.character_profiles import character_profiles
from EyeOfTerror.Pictorium.Moriana.moriana_core.forge_reports import (
    list_reports,
    prune_reports,
    report_path,
    summarize_reports,
)
from EyeOfTerror.Pictorium.Moriana.moriana_core.image_evaluator import evaluate_artifact
from EyeOfTerror.Pictorium.Moriana.moriana_core.project_planner import plan_project
from EyeOfTerror.Pictorium.Moriana.moriana_core.prompt_thinker import PlannerThinker
from EyeOfTerror.Pictorium.Moriana.moriana_core.promptwright import plan_txt2img
from .projects import create_project_mask, get_project, list_projects, save_project
from .queue import ForgeQueue
from .schemas import (
    JobCloneRequest,
    JobSpec,
    MemoryProposal,
    PlanRequest,
    ProjectInpaintRequest,
    ProjectPlanRequest,
    ProjectRefineRequest,
    ProjectStep,
    utc_now,
)
from .storage import ForgeStore

config.force_cpu_runtime()
config.ensure_dirs()
store = ForgeStore()
forge_queue = ForgeQueue(store, start_worker=config.EMBEDDED_WORKER)
app = FastAPI(title="DemonsForge Forge API", version=__version__)
STARTED_AT = utc_now()
STARTED_MONOTONIC = time.monotonic()


def _refresh_project_from_jobs(project):
    changed = False
    for step in project.steps:
        if not step.job_id:
            continue
        job = store.get_job(step.job_id)
        if job is None:
            continue
        status = job.status.value
        artifacts = list(job.artifacts)
        if step.status != status or step.artifacts != artifacts:
            step.status = status
            step.artifacts = artifacts
            changed = True
    submitted_steps = [step for step in project.steps if step.job_id]
    if submitted_steps and all(step.status == "succeeded" for step in submitted_steps):
        new_status = "succeeded"
    elif any(step.status in {"failed", "canceled"} for step in project.steps):
        new_status = "failed"
    elif any(step.status == "running" for step in project.steps):
        new_status = "running"
    elif any(step.job_id for step in project.steps):
        new_status = "submitted"
    else:
        new_status = "planned"
    if project.status != new_status:
        project.status = new_status
        changed = True
    if changed:
        save_project(project)
    return project


def _character_safety(project) -> dict[str, object] | None:
    if not project.character_profile:
        return None
    return {
        "id": project.character_profile.get("id"),
        "name": project.character_profile.get("name"),
        "must_preserve": project.character_profile.get("must_preserve", []),
        "avoid": project.character_profile.get("avoid", []),
        "profile_source": "quality_assets/character_profiles.json",
    }


def _select_project_artifact(project, artifact_id: str | None, action: str) -> tuple[str, str | None]:
    source_step_id = None
    selected_artifact = artifact_id
    if selected_artifact is None:
        for step in project.steps:
            if step.status == "succeeded" and step.artifacts:
                selected_artifact = step.artifacts[0]
                source_step_id = step.id
                break
    else:
        for step in project.steps:
            if selected_artifact in step.artifacts:
                source_step_id = step.id
                break
    if selected_artifact is None:
        raise HTTPException(status_code=400, detail=f"project has no artifact to {action}")
    return selected_artifact, source_step_id


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "DemonsForge",
        "version": __version__,
        "database": str(config.DB_PATH),
        "db_schema_version": store.schema_version(),
        "artifacts": str(config.ARTIFACTS_DIR),
        "device_policy": "cpu-only",
        "cpu_threads": config.CPU_THREADS,
        "thread_policy": config.thread_policy(),
        "git_commit": config.BUILD_COMMIT or None,
        "memory": ArchiveMemoryClient.from_config().status(),
    }


@app.get("/forge/capabilities")
def get_capabilities() -> dict[str, object]:
    return capabilities()


@app.get("/forge/runtime")
def get_runtime() -> dict[str, object]:
    return forge_queue.runtime_state()


@app.get("/forge/state")
def get_state() -> dict[str, object]:
    runtime = forge_queue.runtime_state()
    queue_state = forge_queue.queue_state()
    recent_failed = [
        {
            "id": item.id,
            "type": item.spec.type.value,
            "engine": item.spec.engine,
            "updated_at": item.updated_at,
            "error": item.error,
        }
        for item in store.list_jobs(status="failed", limit=5)
    ]
    caps = capabilities()
    return {
        "service": "DemonsForge",
        "version": __version__,
        "ok": True,
        "started_at": STARTED_AT,
        "uptime_sec": round(time.monotonic() - STARTED_MONOTONIC, 3),
        "git_commit": config.BUILD_COMMIT or None,
        "queue": queue_state,
        "runtime": runtime,
        "job_status_counts": queue_state["status_counts"],
        "recent_failed_jobs": recent_failed,
        "dependencies": caps.get("dependencies", {}),
        "recent_reports": list_reports(limit=5),
        "memory": runtime["memory"],
    }


@app.post("/forge/runtime/unload")
def unload_runtime(engine: str | None = None) -> dict[str, object]:
    return forge_queue.unload_engines(engine_name=engine)


@app.post("/forge/runtime/checkpoint")
def checkpoint_runtime() -> dict[str, object]:
    return store.checkpoint()


@app.post("/forge/queue/pause")
def pause_queue() -> dict[str, object]:
    return forge_queue.pause()


@app.get("/forge/queue")
def get_queue() -> dict[str, object]:
    return forge_queue.queue_state()


@app.get("/forge/events")
def get_events(limit: int = 100, job_id: str | None = None) -> dict[str, object]:
    return store.list_event_logs(limit=limit, job_id=job_id)


@app.post("/forge/queue/resume")
def resume_queue() -> dict[str, object]:
    return forge_queue.resume()


@app.post("/forge/queue/recover-stale")
def recover_stale_jobs(max_age_seconds: int = 3600, dry_run: bool = True) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    recovered = []
    for item in store.list_jobs(status="running", limit=500):
        try:
            updated_at = datetime.fromisoformat(item.updated_at)
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
        except ValueError:
            updated_at = now
        age_seconds = max(0.0, (now - updated_at).total_seconds())
        if age_seconds < max(1, max_age_seconds):
            continue
        entry = {
            "id": item.id,
            "type": item.spec.type.value,
            "engine": item.spec.engine,
            "updated_at": item.updated_at,
            "age_seconds": round(age_seconds, 3),
        }
        if not dry_run:
            store.update_job(
                item.id,
                status="failed",
                progress=item.progress,
                error=f"recovered stale running job after {int(age_seconds)} seconds",
            )
        recovered.append(entry)
    return {"ok": True, "dry_run": dry_run, "max_age_seconds": max_age_seconds, "recovered": recovered}


@app.get("/forge/memory/status")
def memory_status() -> dict[str, object]:
    return ArchiveMemoryClient.from_config().status()


@app.get("/forge/memory/policy")
def memory_policy() -> dict[str, object]:
    return ArchiveMemoryClient.from_config().policy()


@app.get("/forge/memory/gateway")
def memory_gateway() -> dict[str, object]:
    return ArchiveMemoryClient.from_config().gateway()


@app.get("/forge/memory/catalog")
def memory_catalog(create: bool = False) -> dict[str, object]:
    return ArchiveMemoryClient.from_config().catalog(create=create)


@app.get("/forge/memory/search")
def memory_search(
    q: str,
    limit: int = 5,
    layers: str = "focus,wiki,vector,graph",
    include_content: bool = False,
    create: bool = False,
) -> dict[str, object]:
    return ArchiveMemoryClient.from_config().search(
        query=q,
        limit=limit,
        layers=layers,
        include_content=include_content,
        create=create,
    )


@app.get("/forge/memory/events")
def memory_events(
    limit: int = 20,
    component: str | None = None,
    event_action: str | None = None,
    create: bool = False,
) -> dict[str, object]:
    return ArchiveMemoryClient.from_config().events(
        limit=limit,
        component=component,
        event_action=event_action,
        create=create,
    )


@app.get("/forge/memory/proposals")
def memory_proposals(limit: int = 100) -> list[dict[str, object]]:
    return store.list_memory_proposals(limit=max(1, min(limit, 500)))


@app.post("/forge/memory/propose")
def memory_propose(request: MemoryProposal, dry_run: bool = False) -> dict[str, object]:
    proposal_hash = store.memory_proposal_hash(
        request.proposal,
        request.evidence or "",
        request.target,
    )
    existing = store.get_memory_proposal(proposal_hash)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "duplicate": existing is not None,
            "proposal_hash": proposal_hash,
            "existing": existing,
            "memory": ArchiveMemoryClient.from_config().status(),
        }
    if existing is not None:
        return {
            "ok": True,
            "duplicate": True,
            "proposal_hash": proposal_hash,
            "existing": existing,
        }
    response = ArchiveMemoryClient.from_config().propose(
        proposal=request.proposal,
        evidence=request.evidence or "",
        target=request.target,
        importance=request.importance,
    )
    should_record = response.get("ok") is not False or "timed out" in str(response.get("error", "")).lower()
    if should_record:
        store.record_memory_proposal(
            proposal_hash,
            request.proposal,
            request.evidence or "",
            request.target,
            request.importance,
            response,
        )
    return {**response, "proposal_hash": proposal_hash}


@app.get("/forge/schema/job")
def get_job_schema() -> dict[str, object]:
    return JobSpec.model_json_schema()


@app.get("/forge/models")
def get_models() -> list[dict[str, object]]:
    return discover_models()


@app.get("/forge/engines")
def get_engines() -> dict[str, object]:
    return capabilities()["engines"]


@app.get("/forge/loras")
def get_loras() -> list[dict[str, object]]:
    return discover_loras()


@app.get("/forge/embeddings")
def get_embeddings() -> list[dict[str, object]]:
    return discover_embeddings()


@app.get("/forge/samplers")
def get_samplers() -> list[str]:
    return SAMPLERS


@app.get("/forge/schedulers")
def get_schedulers() -> list[dict[str, object]]:
    return SCHEDULERS


@app.get("/forge/aspect-presets")
def get_aspect_presets() -> dict[str, dict[str, int]]:
    return ASPECT_PRESETS


@app.get("/forge/planner/thinker")
def get_planner_thinker() -> dict[str, object]:
    return PlannerThinker.from_config().status()


@app.post("/forge/registries/refresh")
def refresh_registries() -> dict[str, object]:
    clear_registry_caches()
    return {"ok": True, "capabilities": capabilities()}


@app.get("/forge/assets/downloads")
def list_asset_downloads(limit: int = 100) -> list[dict[str, object]]:
    return [item.model_dump() for item in store.list_asset_downloads(limit=max(1, min(limit, 500)))]


@app.get("/forge/assets/profiles")
def get_asset_profiles() -> dict[str, object]:
    return asset_profiles()


@app.get("/forge/characters")
def get_character_profiles() -> dict[str, object]:
    return character_profiles()


@app.get("/forge/reports")
def get_reports(limit: int = 100) -> list[dict[str, object]]:
    return list_reports(limit=limit)


@app.get("/forge/reports/summary")
def get_report_summary(limit: int = 100) -> dict[str, object]:
    return summarize_reports(limit=limit)


@app.post("/forge/reports/prune")
def prune_report_files(max_files: int | None = None) -> dict[str, object]:
    return prune_reports(max_files=max_files)


@app.get("/forge/reports/{filename}")
def get_report_file(filename: str):
    try:
        path = report_path(filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="report not found") from None
    return FileResponse(path)


@app.get("/forge/jobs")
def list_jobs(
    status: str | None = None,
    limit: int = 100,
    engine: str | None = None,
    job_type: str | None = None,
) -> list[dict[str, object]]:
    return [
        item.model_dump()
        for item in store.list_jobs(
            status=status,
            limit=max(1, min(limit, 500)),
            engine=engine,
            job_type=job_type,
        )
    ]


@app.post("/forge/jobs")
def create_job(spec: JobSpec, dry_run: bool = False) -> dict[str, object]:
    if dry_run:
        try:
            return forge_queue.validate(spec)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
    try:
        record = forge_queue.submit(spec)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return record.model_dump()


@app.get("/forge/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, object]:
    record = store.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    return record.model_dump()


@app.get("/forge/jobs/{job_id}/manifest")
def get_job_manifest(job_id: str) -> dict[str, object]:
    record = store.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    artifacts = []
    for artifact_id in record.artifacts:
        artifact = store.get_artifact(artifact_id)
        if artifact is not None:
            artifacts.append(artifact.model_dump())
    return {
        "job": record.model_dump(),
        "artifacts": artifacts,
        "artifact_count": len(artifacts),
    }


@app.get("/forge/jobs/{job_id}/spec")
def get_job_spec(job_id: str) -> dict[str, object]:
    record = store.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    return record.spec.model_dump(mode="json")


@app.get("/forge/jobs/{job_id}/logs")
def get_job_logs(job_id: str) -> dict[str, object]:
    record = store.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "job_id": job_id,
        "status": record.status.value,
        "logs": record.logs,
        "log_count": len(record.logs),
    }


@app.get("/forge/jobs/{job_id}/events")
async def job_events(job_id: str):
    if store.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")

    async def stream():
        while True:
            record = store.get_job(job_id)
            if record is None:
                yield "event: error\ndata: {\"error\":\"job not found\"}\n\n"
                break
            payload = record.model_dump_json()
            yield f"event: status\ndata: {payload}\n\n"
            if record.status.value in {"succeeded", "failed", "canceled"}:
                break
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/forge/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, object]:
    try:
        return forge_queue.cancel(job_id).model_dump()
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found") from None


@app.post("/forge/jobs/{job_id}/clone")
def clone_job(job_id: str, request: JobCloneRequest | None = None, dry_run: bool = False) -> dict[str, object]:
    original = store.get_job(job_id)
    if original is None:
        raise HTTPException(status_code=404, detail="job not found")
    payload = original.spec.model_dump(mode="json")
    request = request or JobCloneRequest()
    if not request.reuse_seed:
        payload["seed"] = None
    payload.update(request.overrides)
    safety = dict(payload.get("safety") or {})
    safety.setdefault("cloned_from", job_id)
    payload["safety"] = safety
    try:
        spec = JobSpec(**payload)
        if dry_run:
            result = forge_queue.validate(spec)
            return {**result, "cloned_from": job_id, "spec": spec.model_dump(mode="json")}
        record = forge_queue.submit(spec)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    data = record.model_dump()
    data["cloned_from"] = job_id
    return data


@app.post("/forge/jobs/{job_id}/retry")
def retry_job(job_id: str, dry_run: bool = False) -> dict[str, object]:
    return clone_job(job_id, JobCloneRequest(), dry_run=dry_run)


@app.get("/forge/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, file: bool = False):
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    if file:
        path = Path(artifact.path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="artifact file missing")
        return FileResponse(path)
    return artifact.model_dump()


@app.get("/forge/artifacts/{artifact_id}/thumbnail")
def get_artifact_thumbnail(artifact_id: str):
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    thumbnail_path = artifact.metadata.get("thumbnail_path")
    if not thumbnail_path or not Path(str(thumbnail_path)).exists():
        raise HTTPException(status_code=404, detail="thumbnail not found")
    return FileResponse(Path(str(thumbnail_path)))


@app.get("/forge/artifacts/{artifact_id}/file")
def get_artifact_file(artifact_id: str):
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    path = Path(artifact.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="artifact file missing")
    return FileResponse(path)


@app.get("/forge/artifacts/{artifact_id}/metadata")
def get_artifact_metadata(artifact_id: str) -> dict[str, object]:
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    metadata_path = Path(artifact.metadata_path)
    if not metadata_path.exists():
        raise HTTPException(status_code=404, detail="artifact metadata file missing")
    return artifact.metadata


@app.get("/forge/artifacts/{artifact_id}/verify")
def verify_artifact(artifact_id: str) -> dict[str, object]:
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    path = Path(artifact.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="artifact file missing")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    expected = artifact.metadata.get("image_sha256") or artifact.metadata.get("sha256")
    verified = expected is not None
    return {
        "artifact_id": artifact_id,
        "path": str(path),
        "sha256": actual,
        "expected_sha256": expected,
        "verified_against_metadata": verified,
        "ok": not verified or str(expected).lower() == actual,
    }


@app.get("/forge/artifacts/{artifact_id}/evaluation")
def get_artifact_evaluation(artifact_id: str) -> dict[str, object]:
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    path = Path(artifact.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="artifact file missing")
    return evaluate_artifact(path, artifact.metadata)


@app.get("/forge/gallery")
def get_gallery(
    limit: int = 100,
    q: str | None = None,
    engine: str | None = None,
    model: str | None = None,
    job_type: str | None = None,
    kind: str | None = None,
) -> list[dict[str, object]]:
    records = []
    for item in store.list_gallery(
        limit=max(1, min(limit, 500)),
        query=q,
        engine=engine,
        model=model,
        job_type=job_type,
        kind=kind,
    ):
        data = item.model_dump()
        data["artifact_url"] = f"/forge/artifacts/{item.id}/file"
        if item.metadata.get("thumbnail_path"):
            data["thumbnail_url"] = f"/forge/artifacts/{item.id}/thumbnail"
        records.append(data)
    return records


@app.post("/forge/plan")
def plan(request: PlanRequest) -> JSONResponse:
    spec = plan_txt2img(request)
    return JSONResponse(content=spec.model_dump(mode="json"))


@app.post("/forge/projects/plan")
def plan_forge_project(request: ProjectPlanRequest) -> JSONResponse:
    project = plan_project(request)
    return JSONResponse(content=project.model_dump(mode="json"))


@app.post("/forge/projects")
def create_forge_project(request: ProjectPlanRequest, dry_run: bool = False) -> dict[str, object]:
    project = plan_project(request)
    validations = []
    for step in project.steps:
        try:
            validations.append({"step_id": step.id, "valid": True, "validation": forge_queue.validate(step.spec)})
        except RuntimeError as exc:
            validations.append({"step_id": step.id, "valid": False, "error": str(exc)})
    if dry_run:
        return {"project": project.model_dump(mode="json"), "validations": validations}
    if not all(item["valid"] for item in validations):
        raise HTTPException(status_code=400, detail={"message": "project contains invalid steps", "validations": validations})
    submitted = []
    for step in project.steps:
        try:
            record = forge_queue.submit(step.spec)
        except RuntimeError as exc:
            step.status = "failed"
            project.status = "partially_submitted" if submitted else "failed"
            save_project(project)
            raise HTTPException(status_code=400, detail=str(exc)) from None
        step.job_id = record.id
        step.status = record.status.value
        submitted.append({"step_id": step.id, "job_id": record.id})
    project.status = "submitted"
    save_project(project)
    return {"project": project.model_dump(mode="json"), "submitted": submitted}


@app.get("/forge/projects")
def get_forge_projects(limit: int = 100) -> list[dict[str, object]]:
    for item in list_projects(limit=limit):
        project = get_project(str(item["id"]))
        if project is not None:
            _refresh_project_from_jobs(project)
    return list_projects(limit=limit)


@app.get("/forge/projects/{project_id}")
def get_forge_project(project_id: str) -> dict[str, object]:
    try:
        project = get_project(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    project = _refresh_project_from_jobs(project)
    return project.model_dump(mode="json")


@app.post("/forge/projects/{project_id}/refresh")
def refresh_forge_project(project_id: str) -> dict[str, object]:
    try:
        project = get_project(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    project = _refresh_project_from_jobs(project)
    return project.model_dump(mode="json")


@app.post("/forge/projects/{project_id}/refine")
def refine_forge_project(project_id: str, request: ProjectRefineRequest, dry_run: bool = False) -> dict[str, object]:
    try:
        project = get_project(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    project = _refresh_project_from_jobs(project)
    artifact_id, source_step_id = _select_project_artifact(project, request.artifact_id, "refine")
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    prompt = request.prompt or (
        "SDXL img2img refinement of the source image, keep the small feline silhouette and pose, "
        "make the creature less like a normal blue cat, add asymmetric dark mutated demon flesh patches, "
        "preserve only a few bright blue cat-fur fragments, add turquoise or violet warp glow, "
        "keep organic body-horror texture, avoid decorative mechanical patterns"
    )
    spec = JobSpec(
        type="img2img",
        engine="sdxl",
        model="stable-diffusion-xl-base-1.0",
        prompt=prompt,
        negative_prompt=(
            str(project.character_profile.get("negative_prompt"))
            if project.character_profile and project.character_profile.get("negative_prompt")
            else "low quality, blurry, distorted"
        ),
        width=512,
        height=512,
        quality_preset="edit_balanced",
        steps=request.steps,
        guidance=7.0,
        sampler="default",
        scheduler="native",
        seed=request.seed,
        strength=request.strength,
        source_images=[artifact.path],
        safety={
            "project_id": project.id,
            "project_role": "sdxl_refine",
            "source_artifact_id": artifact_id,
            "source_step_id": source_step_id,
            "character_profile": _character_safety(project),
        },
    )
    validation = forge_queue.validate(spec)
    step = ProjectStep(
        id=f"refine_{len([item for item in project.steps if item.phase == 'refine']) + 1}",
        phase="refine",
        title="SDXL refine",
        role="sdxl_refine",
        spec=spec,
        depends_on=[source_step_id] if source_step_id else [],
        status="planned",
    )
    if dry_run:
        return {"project_id": project.id, "step": step.model_dump(mode="json"), "validation": validation}
    record = forge_queue.submit(spec)
    step.job_id = record.id
    step.status = record.status.value
    project.steps.append(step)
    project.status = "submitted"
    save_project(project)
    return {"project": project.model_dump(mode="json"), "step": step.model_dump(mode="json"), "job": record.model_dump()}


@app.post("/forge/projects/{project_id}/inpaint")
def inpaint_forge_project(project_id: str, request: ProjectInpaintRequest, dry_run: bool = False) -> dict[str, object]:
    try:
        project = get_project(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    project = _refresh_project_from_jobs(project)
    artifact_id, source_step_id = _select_project_artifact(project, request.artifact_id, "inpaint")
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    try:
        mask_path = create_project_mask(project.id, artifact_id, artifact.path, request.mask_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    prompt = request.prompt or (
        "SDXL inpaint masked corrupted regions only, replace the masked area with asymmetric dark mutated "
        "demonic flesh, scales, feathers, small tentacles, embedded closed eyes, bone spikes, turquoise violet "
        "warp glow, preserve the unmasked feline silhouette and any remaining bright blue fur fragments, "
        "organic body horror texture, no mechanical pattern, no text"
    )
    spec = JobSpec(
        type="inpaint",
        engine="sdxl",
        model="stable-diffusion-xl-base-1.0",
        prompt=prompt,
        negative_prompt=(
            str(project.character_profile.get("negative_prompt"))
            if project.character_profile and project.character_profile.get("negative_prompt")
            else "low quality, blurry, distorted, mechanical pattern, decorative armor"
        ),
        width=512,
        height=512,
        quality_preset="inpaint_precise",
        steps=request.steps,
        guidance=7.0,
        sampler="default",
        scheduler="native",
        seed=request.seed,
        strength=request.strength,
        source_images=[artifact.path],
        mask_image=str(mask_path),
        safety={
            "project_id": project.id,
            "project_role": "sdxl_masked_inpaint",
            "source_artifact_id": artifact_id,
            "source_step_id": source_step_id,
            "mask_mode": request.mask_mode,
            "mask_path": str(mask_path),
            "character_profile": _character_safety(project),
        },
    )
    validation = forge_queue.validate(spec)
    step = ProjectStep(
        id=f"inpaint_{len([item for item in project.steps if item.phase == 'inpaint']) + 1}",
        phase="inpaint",
        title="SDXL masked inpaint",
        role="sdxl_masked_inpaint",
        spec=spec,
        depends_on=[source_step_id] if source_step_id else [],
        status="planned",
    )
    if dry_run:
        return {
            "project_id": project.id,
            "step": step.model_dump(mode="json"),
            "validation": validation,
            "mask_path": str(mask_path),
        }
    record = forge_queue.submit(spec)
    step.job_id = record.id
    step.status = record.status.value
    project.steps.append(step)
    project.status = "submitted"
    save_project(project)
    return {
        "project": project.model_dump(mode="json"),
        "step": step.model_dump(mode="json"),
        "job": record.model_dump(),
        "mask_path": str(mask_path),
    }
