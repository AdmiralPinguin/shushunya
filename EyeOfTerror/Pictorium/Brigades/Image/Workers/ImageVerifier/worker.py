from __future__ import annotations

from pathlib import Path
from typing import Any

from EyeOfTerror.Pictorium.Brigades.Image.worker_api import require_payload, response
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
    artifact_path = str(data.get("artifact_path") or "").strip()
    metadata = _metadata_from_payload(data)
    if not artifact_path:
        return response(
            WORKER,
            {
                "artifact": "/work/pictorium/image_verification.json",
                "verification": {
                    "status": "planned",
                    "checks": ["dimension_match", "metadata_present", "pixel_statistics_after_generation"],
                    "metadata_preview": metadata,
                },
                "blockers": [{"code": "artifact_not_generated", "message": "no artifact_path supplied yet"}],
            },
            ok=False,
        )
    path = Path(artifact_path)
    if not path.exists():
        return response(
            WORKER,
            {
                "artifact": "/work/pictorium/image_verification.json",
                "verification": {"status": "missing", "artifact_path": artifact_path, "metadata_preview": metadata},
                "blockers": [{"code": "artifact_missing", "message": f"artifact does not exist: {artifact_path}"}],
            },
            ok=False,
        )
    verification = evaluate_artifact(path, metadata)
    blockers = []
    dimension_match = verification.get("dimension_match") if isinstance(verification.get("dimension_match"), dict) else {}
    if dimension_match and not dimension_match.get("ok"):
        blockers.append({"code": "dimension_mismatch", "message": "artifact dimensions do not match requested dimensions", "details": dimension_match})
    return response(
        WORKER,
        {
            "artifact": "/work/pictorium/image_verification.json",
            "verification": verification,
            "blockers": blockers,
        },
        ok=not blockers,
    )


def handle(payload: dict[str, Any] | None) -> dict[str, Any]:
    return verify_image(payload)
