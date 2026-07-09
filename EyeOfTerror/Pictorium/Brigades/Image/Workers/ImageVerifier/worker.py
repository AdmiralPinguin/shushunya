from __future__ import annotations

from pathlib import Path
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
from EyeOfTerror.Pictorium.Moriana.moriana_core.image_evaluator import evaluate_artifact


WORKER = "ImageVerifier"


def worker_contract() -> dict[str, Any]:
    return base_contract(
        name=WORKER,
        role="generated artifact verifier and visual risk reporter",
        capabilities=["image_metadata_checks", "dimension_check", "edit_delta_check", "planned_artifact_manifest"],
        inputs=["artifact_path", "metadata", "job_spec", "job_record"],
        outputs=["verification", "blockers"],
    )


def _metadata_from_payload(data: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(data.get("metadata") or {}) if isinstance(data.get("metadata"), dict) else {}
    spec = data.get("job_spec") if isinstance(data.get("job_spec"), dict) else {}
    if spec:
        metadata.setdefault("prompt", spec.get("prompt"))
        metadata.setdefault("engine", spec.get("engine"))
        metadata.setdefault("model", spec.get("model"))
        metadata.setdefault("quality_preset", spec.get("quality_preset"))
        metadata.setdefault("source_images", spec.get("source_images") or [])
        metadata.setdefault("mask_image", spec.get("mask_image"))
        metadata.setdefault("dimensions", {"width": spec.get("width"), "height": spec.get("height")})
        metadata.setdefault("raw_spec", spec)
    job = data.get("job_record") if isinstance(data.get("job_record"), dict) else {}
    if job:
        metadata.setdefault("job_id", job.get("id"))
    return metadata


def verify_image(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = require_payload(payload)
    guidance = worker_model_guidance(
        WORKER,
        "generated artifact verifier and visual risk reporter",
        data,
        "Verify generated image evidence against the requested visual constraints and return structured JSON with risks and revision targets.",
    )
    model_blockers = guidance_blockers(guidance, worker=WORKER, step="image_verification")
    artifact_path = str(data.get("artifact_path") or "").strip()
    metadata = _metadata_from_payload(data)
    if not artifact_path:
        blockers = [{"code": "artifact_not_generated", "message": "no artifact_path supplied yet", "target_worker": "ForgeDispatcher", "target_step": "forge_dispatch"}, *model_blockers]
        return response(
            WORKER,
            with_model_guidance(
                {
                    "artifact": "/work/pictorium/image_verification.json",
                    "verification": {
                        "status": "planned",
                        "checks": ["dimension_match", "metadata_present", "pixel_statistics_after_generation"],
                        "metadata_preview": metadata,
                    },
                    "blockers": blockers,
                    "execution_packet": execution_packet(
                        worker=WORKER,
                        step="image_verification",
                        produced_artifacts=["/work/pictorium/image_verification.json"],
                        blockers=blockers,
                        handoff={"artifact_present": False},
                    ),
                    "revision_packet": revision_packet(
                        worker=WORKER,
                        source_step="image_verification",
                        blockers=blockers,
                        default_target_worker="ForgeDispatcher",
                        default_target_step="forge_dispatch",
                        action="wait for generated artifact or resubmit the Forge job",
                    ),
                },
                guidance,
            ),
            ok=False,
        )
    path = Path(artifact_path)
    if not path.exists():
        blockers = [{"code": "artifact_missing", "message": f"artifact does not exist: {artifact_path}", "target_worker": "ForgeDispatcher", "target_step": "forge_dispatch"}, *model_blockers]
        return response(
            WORKER,
            with_model_guidance(
                {
                    "artifact": "/work/pictorium/image_verification.json",
                    "verification": {"status": "missing", "artifact_path": artifact_path, "metadata_preview": metadata},
                    "blockers": blockers,
                    "execution_packet": execution_packet(
                        worker=WORKER,
                        step="image_verification",
                        produced_artifacts=["/work/pictorium/image_verification.json"],
                        blockers=blockers,
                        handoff={"artifact_present": False, "artifact_path": artifact_path},
                    ),
                    "revision_packet": revision_packet(
                        worker=WORKER,
                        source_step="image_verification",
                        blockers=blockers,
                        default_target_worker="ForgeDispatcher",
                        default_target_step="forge_dispatch",
                        action="locate generated artifact or resubmit the Forge job",
                    ),
                },
                guidance,
            ),
            ok=False,
        )
    verification = evaluate_artifact(path, metadata)
    blockers = []
    dimension_match = verification.get("dimension_match") if isinstance(verification.get("dimension_match"), dict) else {}
    if dimension_match and not dimension_match.get("ok"):
        blockers.append(
            {
                "code": "dimension_mismatch",
                "message": "artifact dimensions do not match requested dimensions",
                "details": dimension_match,
                "target_worker": "Promptwright",
                "target_step": "image_planning",
                "requested_change": "regenerate with dimensions matching the job_spec",
            }
        )
    blockers = [*blockers, *model_blockers]
    return response(
        WORKER,
        with_model_guidance(
            {
                "artifact": "/work/pictorium/image_verification.json",
                "verification": verification,
                "blockers": blockers,
                "execution_packet": execution_packet(
                    worker=WORKER,
                    step="image_verification",
                    produced_artifacts=["/work/pictorium/image_verification.json"],
                    next_steps=[] if blockers else ["finalize"],
                    blockers=blockers,
                    handoff={"artifact_present": True, "artifact_path": artifact_path},
                ),
                "revision_packet": revision_packet(
                    worker=WORKER,
                    source_step="image_verification",
                    blockers=blockers,
                    default_target_worker="Promptwright",
                    default_target_step="image_planning",
                    action="regenerate or adjust prompt/parameters until verification passes",
                ),
            },
            guidance,
        ),
        ok=not blockers,
    )


def handle(payload: dict[str, Any] | None) -> dict[str, Any]:
    return verify_image(payload)

def run(request, workspace_root=None):  # noqa: ARG001
    """HTTP worker-launcher entrypoint: the LegacyMechanicum server calls
    run(request, workspace_root); the image brigade's logic lives in handle()."""
    return handle(request)
