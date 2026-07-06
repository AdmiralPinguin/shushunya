from __future__ import annotations

from pathlib import Path
from typing import Any

from EyeOfTerror.Pictorium.Brigades.Image.worker_api import model_dump, require_payload, response
from EyeOfTerror.Pictorium.Brigades.Image.worker_api import worker_contract as base_contract
from EyeOfTerror.Pictorium.Moriana.forge_runtime.queue import ForgeQueue
from EyeOfTerror.Pictorium.Moriana.forge_runtime.schemas import JobSpec
from EyeOfTerror.Pictorium.Moriana.forge_runtime.storage import ForgeStore


WORKER = "ForgeDispatcher"


def worker_contract() -> dict[str, Any]:
    return base_contract(
        name=WORKER,
        role="ForgeRuntime dry-run validator and job submitter",
        capabilities=["job_validation", "dry_run", "queued_submit", "structured_runtime_blockers"],
        inputs=["job_spec", "submit", "db_path"],
        outputs=["dispatch", "job_record", "blockers"],
    )


def _store(data: dict[str, Any]) -> ForgeStore:
    raw_db_path = str(data.get("db_path") or "").strip()
    if raw_db_path:
        return ForgeStore(Path(raw_db_path))
    return ForgeStore()


def prepare_dispatch(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = require_payload(payload)
    raw_spec = data.get("job_spec") if isinstance(data.get("job_spec"), dict) else data.get("spec")
    if not isinstance(raw_spec, dict):
        raise ValueError("ForgeDispatcher requires job_spec")
    spec = JobSpec(**raw_spec)
    queue = ForgeQueue(_store(data), start_worker=False)
    submit = bool(data.get("submit", False))
    try:
        validation = queue.validate(spec)
    except Exception as exc:
        return response(
            WORKER,
            {
                "artifact": "/work/pictorium/forge_jobs.json",
                "dispatch": {"valid": False, "submitted": False},
                "job_spec": model_dump(spec),
                "blockers": [{"code": "forge_validation_failed", "message": str(exc)}],
            },
            ok=False,
        )
    job_record = None
    if submit:
        job_record = queue.submit(spec)
    return response(
        WORKER,
        {
            "artifact": "/work/pictorium/forge_jobs.json",
            "dispatch": {
                "valid": True,
                "submitted": submit,
                "resource_estimate": validation.get("resource_estimate", {}),
                "queue_state": queue.queue_state(),
            },
            "job_spec": model_dump(spec),
            "job_record": model_dump(job_record) if job_record else None,
            "blockers": [],
        },
    )


def handle(payload: dict[str, Any] | None) -> dict[str, Any]:
    return prepare_dispatch(payload)
