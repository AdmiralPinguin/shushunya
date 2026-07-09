from __future__ import annotations

from pathlib import Path
from typing import Any

from EyeOfTerror.Pictorium.Brigades.Image.worker_api import (
    execution_packet,
    guidance_blockers,
    model_dump,
    require_payload,
    response,
    revision_packet,
    with_model_guidance,
    worker_model_guidance,
)
from EyeOfTerror.Pictorium.Brigades.Image.worker_api import worker_contract as base_contract


WORKER = "ForgeDispatcher"


def worker_contract() -> dict[str, Any]:
    return base_contract(
        name=WORKER,
        role="ForgeRuntime dry-run validator and job submitter",
        capabilities=["job_validation", "dry_run", "queued_submit", "structured_runtime_blockers"],
        inputs=["job_spec", "submit", "db_path"],
        outputs=["dispatch", "job_record", "blockers"],
    )


def _forge_runtime():
    # Lazy import: the forge runtime needs Pillow/pydantic from the forge venv,
    # while worker_contract/planning must import under any interpreter.
    from EyeOfTerror.Pictorium.Moriana.forge_runtime.queue import ForgeQueue
    from EyeOfTerror.Pictorium.Moriana.forge_runtime.schemas import JobSpec
    from EyeOfTerror.Pictorium.Moriana.forge_runtime.storage import ForgeStore

    return ForgeQueue, JobSpec, ForgeStore


def _store(data: dict[str, Any]):
    _ForgeQueue, _JobSpec, ForgeStore = _forge_runtime()
    raw_db_path = str(data.get("db_path") or "").strip()
    if raw_db_path:
        return ForgeStore(Path(raw_db_path))
    return ForgeStore()


def prepare_dispatch(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = require_payload(payload)
    guidance = worker_model_guidance(
        WORKER,
        "ForgeRuntime dry-run validator and job submitter",
        data,
        "Validate dispatch readiness and return structured JSON about submit/wait/retry risks without inventing completed artifacts.",
    )
    model_blockers = guidance_blockers(guidance, worker=WORKER, step="forge_dispatch")
    raw_spec = data.get("job_spec") if isinstance(data.get("job_spec"), dict) else data.get("spec")
    if not isinstance(raw_spec, dict):
        raise ValueError("ForgeDispatcher requires job_spec")
    ForgeQueue, JobSpec, _ForgeStore = _forge_runtime()
    spec = JobSpec(**raw_spec)
    queue = ForgeQueue(_store(data), start_worker=False)
    # Real generation requires submission. The order may set it; otherwise the
    # live service defaults from FORGE_AUTOSUBMIT so a mission actually produces
    # an image, while tests (no env) keep the safe dry-run default.
    import os as _os

    submit = bool(data.get("submit", _os.environ.get("FORGE_AUTOSUBMIT") == "1"))
    try:
        validation = queue.validate(spec)
    except Exception as exc:
        blockers = [{"code": "forge_validation_failed", "message": str(exc)}, *model_blockers]
        return response(
            WORKER,
            with_model_guidance(
                {
                    "artifact": "/work/pictorium/forge_jobs.json",
                    "dispatch": {"valid": False, "submitted": False},
                    "job_spec": model_dump(spec),
                    "blockers": blockers,
                    "execution_packet": execution_packet(
                        worker=WORKER,
                        step="forge_dispatch",
                        produced_artifacts=["/work/pictorium/forge_jobs.json"],
                        blockers=blockers,
                        handoff={"submitted": False},
                    ),
                    "revision_packet": revision_packet(
                        worker=WORKER,
                        source_step="forge_dispatch",
                        blockers=blockers,
                        default_target_worker="Promptwright",
                        default_target_step="image_planning",
                        action="produce a Forge-valid job_spec",
                    ),
                },
                guidance,
            ),
            ok=False,
        )
    job_record = None
    if submit:
        job_record = queue.submit(spec)
    return response(
        WORKER,
        with_model_guidance(
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
                "blockers": model_blockers,
                "execution_packet": execution_packet(
                    worker=WORKER,
                    step="forge_dispatch",
                    produced_artifacts=["/work/pictorium/forge_jobs.json"],
                    next_steps=[] if model_blockers else ["image_verification"],
                    blockers=model_blockers,
                    handoff={"submitted": submit, "job_id": job_record.id if job_record else ""},
                ),
                "revision_packet": revision_packet(
                    worker=WORKER,
                    source_step="forge_dispatch",
                    blockers=model_blockers,
                    default_target_worker="Promptwright",
                    default_target_step="image_planning",
                    action="produce a Forge-valid job_spec",
                ),
            },
            guidance,
        ),
        ok=not model_blockers,
    )


def handle(payload: dict[str, Any] | None) -> dict[str, Any]:
    return prepare_dispatch(payload)

def run(request, workspace_root=None):
    """HTTP worker-launcher entrypoint: the LegacyMechanicum server calls
    run(request, workspace_root); the image brigade's logic lives in handle().
    After handling, materialise declared artifacts so the next step's input
    preflight passes."""
    try:
        from EyeOfTerror.Pictorium.Brigades.Image.worker_api import inject_input_artifacts, persist_expected_artifacts
        inject_input_artifacts(request, workspace_root)
    except Exception as exc:  # noqa: BLE001
        print(f'artifact inject failed: {exc}', flush=True)
    result = handle(request)
    try:
        persist_expected_artifacts(request, workspace_root, result)
    except Exception as exc:  # noqa: BLE001
        print(f'artifact persist failed: {exc}', flush=True)
    return result
