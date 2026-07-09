from __future__ import annotations

from typing import Any

from EyeOfTerror.Pictorium.Brigades.Image.worker_api import (
    execution_packet,
    guidance_blockers,
    require_payload,
    response,
    revision_packet,
    with_model_guidance,
    worker_model_guidance,
)
from EyeOfTerror.Pictorium.Brigades.Image.worker_api import worker_contract as base_contract


WORKER = "ArtifactFinalis"


def worker_contract() -> dict[str, Any]:
    return base_contract(
        name=WORKER,
        role="final manifest and delivery package builder",
        capabilities=["final_manifest", "blocker_rollup", "artifact_inventory", "handoff_summary"],
        inputs=["plan", "resources", "dispatch", "verification", "artifacts"],
        outputs=["final_manifest", "blockers"],
    )


def _blockers_from(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    blockers = value.get("blockers")
    if not isinstance(blockers, list):
        return []
    return [item for item in blockers if isinstance(item, dict)]


def build_final_manifest(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = require_payload(payload)
    guidance = worker_model_guidance(
        WORKER,
        "final manifest and delivery package builder",
        data,
        "Review the completed visual task package and return structured JSON confirming final readiness, delivery risks, and revision needs.",
    )
    plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}
    resources = data.get("resources") if isinstance(data.get("resources"), dict) else {}
    dispatch = data.get("dispatch") if isinstance(data.get("dispatch"), dict) else {}
    verification = data.get("verification") if isinstance(data.get("verification"), dict) else {}
    artifacts = data.get("artifacts") if isinstance(data.get("artifacts"), list) else []
    blockers = [
        *_blockers_from(plan),
        *_blockers_from(resources),
        *_blockers_from(dispatch),
        *_blockers_from(verification),
        *guidance_blockers(guidance, worker=WORKER, step="finalize"),
    ]
    job_record = dispatch.get("job_record") if isinstance(dispatch.get("job_record"), dict) else None
    if job_record:
        artifacts = [*artifacts, *job_record.get("artifacts", [])]
    manifest = {
        "kind": "pictorium_image_final_manifest",
        "status": "blocked" if blockers else "ready",
        "plan_kind": plan.get("plan_kind", ""),
        "job_id": job_record.get("id") if job_record else "",
        "artifacts": artifacts,
        "blockers": blockers,
        "handoff": {
            "ready_for_delivery": not blockers and bool(artifacts or job_record),
            "requires_generation": not artifacts,
            "requires_user_action": any(item.get("code") == "asset_approval_required" for item in blockers),
        },
    }
    return response(
        WORKER,
        with_model_guidance(
            {
                "artifact": "/work/pictorium/final_manifest.json",
                "final_manifest": manifest,
                "blockers": blockers,
                "execution_packet": execution_packet(
                    worker=WORKER,
                    step="finalize",
                    produced_artifacts=["/work/pictorium/final_manifest.json"],
                    blockers=blockers,
                    handoff=manifest["handoff"],
                ),
                "revision_packet": revision_packet(
                    worker=WORKER,
                    source_step="finalize",
                    blockers=blockers,
                    default_target_worker="ImageVerifier",
                    default_target_step="image_verification",
                    action="clear upstream blockers and rebuild the final manifest",
                ),
            },
            guidance,
        ),
        ok=not blockers,
    )


def handle(payload: dict[str, Any] | None) -> dict[str, Any]:
    return build_final_manifest(payload)

def run(request, workspace_root=None):
    """HTTP worker-launcher entrypoint: the LegacyMechanicum server calls
    run(request, workspace_root); the image brigade's logic lives in handle().
    After handling, materialise declared artifacts so the next step's input
    preflight passes."""
    result = handle(request)
    try:
        from EyeOfTerror.Pictorium.Brigades.Image.worker_api import persist_expected_artifacts
        persist_expected_artifacts(request, workspace_root, result)
    except Exception as exc:  # noqa: BLE001
        print(f'artifact persist failed: {exc}', flush=True)
    return result
