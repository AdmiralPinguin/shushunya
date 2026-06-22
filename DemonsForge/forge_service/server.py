from __future__ import annotations

from pathlib import Path

import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from . import __version__, config
from .planner import plan_txt2img
from .queue import ForgeQueue
from .registries import capabilities, discover_loras, discover_models
from .schemas import JobSpec, PlanRequest
from .storage import ForgeStore

config.force_cpu_runtime()
config.ensure_dirs()
store = ForgeStore()
forge_queue = ForgeQueue(store)
app = FastAPI(title="DemonsForge Forge API", version=__version__)


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "DemonsForge",
        "version": __version__,
        "database": str(config.DB_PATH),
        "artifacts": str(config.ARTIFACTS_DIR),
        "device_policy": "cpu-only",
        "cpu_threads": config.CPU_THREADS,
    }


@app.get("/forge/capabilities")
def get_capabilities() -> dict[str, object]:
    return capabilities()


@app.get("/forge/models")
def get_models() -> list[dict[str, object]]:
    return discover_models()


@app.get("/forge/loras")
def get_loras() -> list[dict[str, object]]:
    return discover_loras()


@app.get("/forge/jobs")
def list_jobs(status: str | None = None, limit: int = 100) -> list[dict[str, object]]:
    return [
        item.model_dump()
        for item in store.list_jobs(status=status, limit=max(1, min(limit, 500)))
    ]


@app.post("/forge/jobs")
def create_job(spec: JobSpec, dry_run: bool = False) -> dict[str, object]:
    if dry_run:
        return forge_queue.validate(spec)
    record = forge_queue.submit(spec)
    return record.model_dump()


@app.get("/forge/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, object]:
    record = store.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    return record.model_dump()


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


@app.get("/forge/gallery")
def get_gallery(limit: int = 100) -> list[dict[str, object]]:
    records = []
    for item in store.list_gallery(limit=max(1, min(limit, 500))):
        data = item.model_dump()
        data["artifact_url"] = f"/forge/artifacts/{item.id}?file=true"
        if item.metadata.get("thumbnail_path"):
            data["thumbnail_url"] = f"/forge/artifacts/{item.id}/thumbnail"
        records.append(data)
    return records


@app.post("/forge/plan")
def plan(request: PlanRequest) -> JSONResponse:
    spec = plan_txt2img(request)
    return JSONResponse(content=spec.model_dump(mode="json"))
