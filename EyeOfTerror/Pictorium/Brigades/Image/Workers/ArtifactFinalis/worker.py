from __future__ import annotations

from typing import Any

from EyeOfTerror.Pictorium.Brigades.Image.worker_api import require_payload, response
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
        {
            "artifact": "/work/pictorium/final_manifest.json",
            "final_manifest": manifest,
            "blockers": blockers,
        },
        ok=not blockers,
    )


def handle(payload: dict[str, Any] | None) -> dict[str, Any]:
    return build_final_manifest(payload)
