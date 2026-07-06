from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from EyeOfTerror.Pictorium.Moriana.moriana_runtime import MorianaRunStore, read_json


def _blockers_from(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    blockers = value.get("blockers") if isinstance(value.get("blockers"), list) else []
    return [item for item in blockers if isinstance(item, dict)]


def _artifact_status_counts(artifacts: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(item.get("status") or "unknown") for item in artifacts)
    return dict(sorted(counts.items()))


def _artifact_type_counts(artifacts: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(item.get("type") or "unknown") for item in artifacts)
    return dict(sorted(counts.items()))


def _weak_steps(artifacts: list[dict[str, Any]], blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rejected_by_step = Counter(
        str(item.get("step") or "unknown")
        for item in artifacts
        if str(item.get("status") or "") == "rejected"
    )
    blocker_targets = Counter(
        str(item.get("target_step") or item.get("step") or "unknown")
        for item in blockers
    )
    steps = sorted(set(rejected_by_step) | set(blocker_targets))
    return [
        {
            "step": step,
            "rejected_artifacts": rejected_by_step.get(step, 0),
            "blocker_count": blocker_targets.get(step, 0),
        }
        for step in steps
        if step != "unknown" or rejected_by_step.get(step, 0) or blocker_targets.get(step, 0)
    ]


def _revision_targets(blockers: list[dict[str, Any]], weak_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    targets = []
    seen = set()
    for blocker in blockers:
        worker = str(blocker.get("target_worker") or "")
        step = str(blocker.get("target_step") or blocker.get("step") or "")
        if not worker and step:
            worker = {
                "image_planning": "Promptwright",
                "resource_readiness": "ModelQuartermaster",
                "forge_dispatch": "ForgeDispatcher",
                "image_verification": "ImageVerifier",
                "panel_generation": "Panelwright",
                "layout_manifest": "LayoutFinalis",
            }.get(step, "")
        key = (worker, step)
        if key in seen or (not worker and not step):
            continue
        seen.add(key)
        targets.append(
            {
                "worker": worker,
                "step": step,
                "reason": blocker.get("message") or blocker.get("code") or "quality blocker",
                "requested_change": blocker.get("requested_change") or "rerun this step with corrected inputs",
            }
        )
    if targets:
        return targets
    for item in weak_steps:
        step = str(item.get("step") or "")
        if step:
            targets.append(
                {
                    "worker": "",
                    "step": step,
                    "reason": "step produced rejected artifacts",
                    "requested_change": "inspect and rerun the step",
                }
            )
    return targets


def _quality_score(final: dict[str, Any], artifacts: list[dict[str, Any]], blockers: list[dict[str, Any]]) -> int:
    score = 100
    score -= min(60, len(blockers) * 20)
    score -= min(25, len([item for item in artifacts if str(item.get("status") or "") == "rejected"]) * 5)
    if final.get("status") != "ready":
        score -= 25
    if not artifacts:
        score -= 20
    return max(0, min(100, score))


def build_quality_report(store: MorianaRunStore, run_id: str) -> dict[str, Any]:
    status = store.status(run_id)
    final = store.final_result(run_id)
    artifacts = store.artifacts(run_id)
    blockers = _blockers_from(final)
    for artifact in artifacts:
        metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
        if artifact.get("status") == "rejected" and artifact.get("rejection_reason"):
            blockers.append(
                {
                    "code": "artifact_rejected",
                    "message": str(artifact.get("rejection_reason") or ""),
                    "step": artifact.get("step"),
                    "artifact_id": artifact.get("artifact_id"),
                    "metadata": metadata,
                }
            )
    weak_steps = _weak_steps(artifacts, blockers)
    revision_targets = _revision_targets(blockers, weak_steps)
    accepted_images = [
        item
        for item in artifacts
        if item.get("type") == "image" and item.get("status") == "accepted"
    ]
    final_ready = final.get("status") == "ready"
    report = {
        "kind": "pictorium_quality_report",
        "run_id": run_id,
        "task_kind": status.get("task_kind"),
        "run_status": status.get("status"),
        "final_status": final.get("status"),
        "score": _quality_score(final, artifacts, blockers),
        "artifact_counts": {
            "by_status": _artifact_status_counts(artifacts),
            "by_type": _artifact_type_counts(artifacts),
            "total": len(artifacts),
        },
        "accepted_image_count": len(accepted_images),
        "blocker_count": len(blockers),
        "blockers": blockers,
        "weak_steps": weak_steps,
        "revision_targets": revision_targets,
        "next_action": "accept_final" if final_ready and not blockers else ("revise" if revision_targets else "inspect"),
        "delivery_ready": final_ready and not blockers,
        "audit_limits": [
            "No semantic vision model is used in this report.",
            "Visual quality is inferred from worker verifiers, metadata, final manifests, and artifact states.",
        ],
    }
    return report


def write_quality_report(store: MorianaRunStore, run_id: str) -> dict[str, Any]:
    report = build_quality_report(store, run_id)
    path = store.write_step(run_id, "quality_report", report, subdir="final")
    store.register_artifact(
        run_id,
        artifact_type="quality_report",
        path=path,
        created_by="Moriana",
        step="quality_audit",
        attempt=int(report.get("attempt") or 1),
        status="accepted" if report.get("delivery_ready") else "draft",
        metadata={"score": report.get("score"), "next_action": report.get("next_action")},
    )
    return report


def read_quality_report(run_dir: Path) -> dict[str, Any]:
    return read_json(run_dir / "final" / "quality_report.json", {"ok": False, "error": "quality report is not ready"})
