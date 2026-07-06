from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from EyeOfTerror.Pictorium.Moriana.forge_runtime.queue import ForgeQueue
from EyeOfTerror.Pictorium.Moriana.forge_runtime.storage import ForgeStore


TERMINAL_STATUSES = {"succeeded", "failed", "canceled"}


def _job_id_from(job_record: dict[str, Any] | None) -> str:
    if not isinstance(job_record, dict):
        return ""
    return str(job_record.get("id") or "").strip()


def _artifact_payloads(store: ForgeStore, artifact_ids: list[str]) -> list[dict[str, Any]]:
    artifacts = []
    for artifact_id in artifact_ids:
        artifact = store.get_artifact(artifact_id)
        if artifact is None:
            artifacts.append({"id": artifact_id, "missing": True})
            continue
        artifacts.append(artifact.model_dump(mode="json"))
    return artifacts


def monitor_forge_job(
    *,
    db_path: Path,
    job_record: dict[str, Any] | None,
    max_wait_sec: float = 0.0,
    poll_interval_sec: float = 0.5,
    run_inline_once: bool = False,
) -> dict[str, Any]:
    job_id = _job_id_from(job_record)
    if not job_id:
        return {
            "ok": False,
            "status": "missing_job",
            "blockers": [{"code": "forge_job_missing", "message": "Forge dispatch did not return a job_record"}],
            "artifact_paths": [],
            "artifacts": [],
        }
    store = ForgeStore(db_path)
    inline_queue = ForgeQueue(store, start_worker=False) if run_inline_once else None
    try:
        if inline_queue is not None:
            inline_queue.run_pending_once()
        deadline = time.monotonic() + max(0.0, float(max_wait_sec))
        record = store.get_job(job_id)
        while record is not None and record.status.value not in TERMINAL_STATUSES and time.monotonic() < deadline:
            time.sleep(max(0.05, float(poll_interval_sec)))
            if inline_queue is not None:
                inline_queue.run_pending_once()
            record = store.get_job(job_id)
    finally:
        if inline_queue is not None:
            inline_queue.unload_engines()
    if record is None:
        return {
            "ok": False,
            "status": "missing_job",
            "job_id": job_id,
            "blockers": [{"code": "forge_job_missing", "message": f"Forge job does not exist in db: {job_id}"}],
            "artifact_paths": [],
            "artifacts": [],
        }
    artifacts = _artifact_payloads(store, list(record.artifacts))
    artifact_paths = [
        str(item.get("path") or "")
        for item in artifacts
        if isinstance(item, dict) and item.get("path") and not item.get("missing")
    ]
    status = record.status.value
    blockers: list[dict[str, Any]] = []
    if status == "failed":
        blockers.append(
            {
                "code": "forge_job_failed",
                "message": record.error or "Forge job failed without a detailed error",
                "target_worker": "ForgeDispatcher",
                "target_step": "forge_dispatch",
            }
        )
    elif status == "canceled":
        blockers.append(
            {
                "code": "forge_job_canceled",
                "message": "Forge job was canceled",
                "target_worker": "ForgeDispatcher",
                "target_step": "forge_dispatch",
            }
        )
    elif status != "succeeded":
        blockers.append(
            {
                "code": "forge_job_not_finished",
                "message": f"Forge job is still {status}",
                "target_worker": "ForgeDispatcher",
                "target_step": "forge_dispatch",
            }
        )
    elif not artifact_paths:
        blockers.append(
            {
                "code": "forge_job_has_no_artifacts",
                "message": "Forge job succeeded but did not register artifact files",
                "target_worker": "ForgeDispatcher",
                "target_step": "forge_dispatch",
            }
        )
    return {
        "ok": not blockers,
        "job_id": job_id,
        "status": status,
        "progress": record.progress,
        "error": record.error,
        "logs": record.logs,
        "artifacts": artifacts,
        "artifact_paths": artifact_paths,
        "blockers": blockers,
        "waited": max_wait_sec > 0,
        "run_inline_once": run_inline_once,
    }
