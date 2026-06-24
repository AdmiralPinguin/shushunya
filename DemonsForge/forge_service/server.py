from __future__ import annotations

from pathlib import Path
import hashlib
import time

import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from . import __version__, config
from .archive_memory import ArchiveMemoryClient
from .evaluator import evaluate_artifact
from .planner import plan_txt2img
from .queue import ForgeQueue
from .registries import (
    ASPECT_PRESETS,
    SAMPLERS,
    SCHEDULERS,
    capabilities,
    clear_registry_caches,
    discover_embeddings,
    discover_loras,
    discover_models,
)
from .schemas import JobCloneRequest, JobSpec, MemoryProposal, PlanRequest, utc_now
from .storage import ForgeStore
from .thinker import PlannerThinker

config.force_cpu_runtime()
config.ensure_dirs()
store = ForgeStore()
forge_queue = ForgeQueue(store, start_worker=config.EMBEDDED_WORKER)
app = FastAPI(title="DemonsForge Forge API", version=__version__)
STARTED_AT = utc_now()
STARTED_MONOTONIC = time.monotonic()


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
