from __future__ import annotations

from pathlib import Path
from typing import Any

from EyeOfTerror.Pictorium.Moriana.moriana_quality import build_quality_report
from EyeOfTerror.Pictorium.Moriana.moriana_runtime import MorianaRunStore, read_json


IMAGE_DOWNSTREAM_STEPS = ["resource_readiness", "forge_dispatch", "image_verification", "finalize"]
FORGE_DOWNSTREAM_STEPS = ["image_verification", "finalize"]
COMIC_DOWNSTREAM_STEPS = ["panel_generation", "layout_manifest"]

ACTION_POLICIES: dict[str, dict[str, Any]] = {
    "dimension_mismatch": {
        "priority": 100,
        "action": "regenerate_with_verified_dimensions",
        "target_worker": "Promptwright",
        "target_step": "image_planning",
        "downstream_steps": IMAGE_DOWNSTREAM_STEPS,
        "requested_change": "rewrite generation package with verified width and height, then rerun Forge and verification",
    },
    "artifact_not_generated": {
        "priority": 90,
        "action": "wait_or_resubmit_forge_job",
        "target_worker": "ForgeDispatcher",
        "target_step": "forge_dispatch",
        "downstream_steps": FORGE_DOWNSTREAM_STEPS,
        "requested_change": "wait for a queued Forge job or resubmit a valid generation job",
    },
    "artifact_missing": {
        "priority": 90,
        "action": "wait_or_resubmit_forge_job",
        "target_worker": "ForgeDispatcher",
        "target_step": "forge_dispatch",
        "downstream_steps": FORGE_DOWNSTREAM_STEPS,
        "requested_change": "locate the generated artifact or resubmit the Forge job",
    },
    "forge_job_not_finished": {
        "priority": 85,
        "action": "wait_or_resubmit_forge_job",
        "target_worker": "ForgeDispatcher",
        "target_step": "forge_dispatch",
        "downstream_steps": FORGE_DOWNSTREAM_STEPS,
        "requested_change": "continue monitoring the queued job, then verify its artifact",
    },
    "forge_job_has_no_artifacts": {
        "priority": 85,
        "action": "wait_or_resubmit_forge_job",
        "target_worker": "ForgeDispatcher",
        "target_step": "forge_dispatch",
        "downstream_steps": FORGE_DOWNSTREAM_STEPS,
        "requested_change": "resubmit or repair Forge output registration and verify the new artifact",
    },
    "forge_job_missing": {
        "priority": 80,
        "action": "repair_job_spec_or_runtime",
        "target_worker": "ForgeDispatcher",
        "target_step": "forge_dispatch",
        "downstream_steps": FORGE_DOWNSTREAM_STEPS,
        "requested_change": "create a valid Forge job record before verification",
    },
    "forge_job_failed": {
        "priority": 80,
        "action": "repair_job_spec_or_runtime",
        "target_worker": "ForgeDispatcher",
        "target_step": "forge_dispatch",
        "downstream_steps": FORGE_DOWNSTREAM_STEPS,
        "requested_change": "repair failed Forge job inputs or runtime state, then rerun generation",
    },
    "forge_job_canceled": {
        "priority": 80,
        "action": "repair_job_spec_or_runtime",
        "target_worker": "ForgeDispatcher",
        "target_step": "forge_dispatch",
        "downstream_steps": FORGE_DOWNSTREAM_STEPS,
        "requested_change": "resubmit the canceled Forge job if the task is still wanted",
    },
    "forge_validation_failed": {
        "priority": 80,
        "action": "repair_job_spec_or_runtime",
        "target_worker": "Promptwright",
        "target_step": "image_planning",
        "downstream_steps": IMAGE_DOWNSTREAM_STEPS,
        "requested_change": "repair invalid job_spec fields, then rerun dispatch and verification",
    },
    "unknown_engine": {
        "priority": 75,
        "action": "change_model_or_assets",
        "target_worker": "ModelQuartermaster",
        "target_step": "resource_readiness",
        "downstream_steps": FORGE_DOWNSTREAM_STEPS,
        "requested_change": "select a supported image engine before dispatch",
    },
    "engine_model_missing": {
        "priority": 75,
        "action": "change_model_or_assets",
        "target_worker": "ModelQuartermaster",
        "target_step": "resource_readiness",
        "downstream_steps": FORGE_DOWNSTREAM_STEPS,
        "requested_change": "select or install an available model before dispatch",
    },
    "model_missing": {
        "priority": 75,
        "action": "change_model_or_assets",
        "target_worker": "ModelQuartermaster",
        "target_step": "resource_readiness",
        "downstream_steps": FORGE_DOWNSTREAM_STEPS,
        "requested_change": "select or install the requested model before dispatch",
    },
    "lora_unsupported": {
        "priority": 75,
        "action": "change_model_or_assets",
        "target_worker": "ModelQuartermaster",
        "target_step": "resource_readiness",
        "downstream_steps": FORGE_DOWNSTREAM_STEPS,
        "requested_change": "remove unsupported LoRA usage or select a runtime that supports it",
    },
    "negative_prompt_unsupported": {
        "priority": 70,
        "action": "change_model_or_assets",
        "target_worker": "ModelQuartermaster",
        "target_step": "resource_readiness",
        "downstream_steps": FORGE_DOWNSTREAM_STEPS,
        "requested_change": "remove unsupported negative prompt or select a compatible engine",
    },
    "asset_approval_required": {
        "priority": 70,
        "action": "change_model_or_assets",
        "target_worker": "ModelQuartermaster",
        "target_step": "resource_readiness",
        "downstream_steps": FORGE_DOWNSTREAM_STEPS,
        "requested_change": "approve, download, or replace requested visual assets before dispatch",
    },
}


def _safe_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _effective_code(blocker: dict[str, Any]) -> str:
    code = str(blocker.get("code") or "unknown_blocker")
    if code != "artifact_rejected":
        return code
    message = str(blocker.get("message") or "")
    for candidate in ACTION_POLICIES:
        if candidate in message:
            return candidate
    return code


def _normalise_blocker(blocker: dict[str, Any], task_kind: str) -> dict[str, Any]:
    code = _effective_code(blocker)
    step = str(blocker.get("target_step") or blocker.get("step") or "")
    if task_kind == "comic" or step in {"panel_generation", "layout_manifest", "character_sheet"}:
        policy = {
            "priority": 88,
            "action": "rerun_panel_generation_and_layout",
            "target_worker": str(blocker.get("target_worker") or "Panelwright"),
            "target_step": step or "panel_generation",
            "downstream_steps": COMIC_DOWNSTREAM_STEPS,
            "requested_change": "repair comic panel, character-sheet, or layout blockers and rebuild the manifest",
        }
    else:
        policy = ACTION_POLICIES.get(
            code,
            {
                "priority": 10,
                "action": "inspect_blocker_and_rerun_target_step",
                "target_worker": str(blocker.get("target_worker") or ""),
                "target_step": step,
                "downstream_steps": [step] if step else [],
                "requested_change": str(blocker.get("requested_change") or "inspect blocker and rerun affected step"),
            },
        )
    target_worker = str(blocker.get("target_worker") or policy.get("target_worker") or "")
    target_step = str(blocker.get("target_step") or blocker.get("step") or policy.get("target_step") or "")
    requested_change = str(blocker.get("requested_change") or policy.get("requested_change") or "rerun affected step")
    return {
        "code": code,
        "message": str(blocker.get("message") or blocker.get("reason") or code),
        "action": str(policy.get("action") or "inspect_blocker_and_rerun_target_step"),
        "target_worker": target_worker,
        "target_step": target_step,
        "downstream_steps": list(policy.get("downstream_steps") or []),
        "requested_change": requested_change,
        "priority": int(policy.get("priority") or 0),
        "source_code": str(blocker.get("code") or "unknown_blocker"),
        "source": blocker,
    }


def _dedupe_targets(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    deduped = []
    for target in sorted(targets, key=lambda item: (-int(item.get("priority") or 0), str(item.get("code") or ""))):
        key = (str(target.get("action") or ""), str(target.get("target_worker") or ""), str(target.get("target_step") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(target)
    return deduped


def _revision_strategy(action: str, targets: list[dict[str, Any]], rerun_steps: list[str], downstream_steps: list[str]) -> dict[str, Any]:
    if action == "accept_final":
        return {
            "kind": "acceptance",
            "mode": "accept_best_selected_final",
            "worker_sequence": [],
            "rerun_steps": [],
            "acceptance_rule": "final manifest is ready, quality report has no blockers, and final_selection identifies accepted artifacts",
            "stop_condition": "final_ready",
        }
    if action == "wait_or_resubmit_forge_job":
        mode = "continue_or_resubmit_generation"
        worker_sequence = ["ForgeDispatcher", "ImageVerifier", "ArtifactFinalis"]
    elif action == "change_model_or_assets":
        mode = "resource_reselection_then_regeneration"
        worker_sequence = ["ModelQuartermaster", "ForgeDispatcher", "ImageVerifier", "ArtifactFinalis"]
    elif action == "rerun_panel_generation_and_layout":
        mode = "rerun_comic_panels_then_layout"
        worker_sequence = ["Panelwright", "LayoutFinalis"]
    elif action == "regenerate_with_verified_dimensions":
        mode = "prompt_repair_then_regeneration"
        worker_sequence = ["Promptwright", "ModelQuartermaster", "ForgeDispatcher", "ImageVerifier", "ArtifactFinalis"]
    else:
        mode = "targeted_repair_then_reaudit"
        worker_sequence = [
            str(target.get("target_worker") or "")
            for target in targets
            if str(target.get("target_worker") or "")
        ]
    return {
        "kind": "revision",
        "mode": mode,
        "worker_sequence": list(dict.fromkeys(worker_sequence)),
        "rerun_steps": rerun_steps,
        "downstream_steps": downstream_steps,
        "acceptance_rule": "rerun affected steps, write a new final manifest, then accept only if quality_report.next_action is accept_final",
        "stop_condition": "new_accepted_final_or_repeated_blocker_fingerprint",
    }


def build_revision_decision(
    store: MorianaRunStore,
    run_id: str,
    quality_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = quality_report if isinstance(quality_report, dict) else build_quality_report(store, run_id)
    status = store.status(run_id)
    blockers = _safe_list(report.get("blockers"))
    task_kind = str(report.get("task_kind") or status.get("task_kind") or "")
    delivery_ready = bool(report.get("delivery_ready"))
    if delivery_ready and not blockers:
        final_selection = store.final_selection(run_id, store.final_result(run_id))
        return {
            "kind": "pictorium_revision_decision",
            "run_id": run_id,
            "task_kind": task_kind,
            "quality_score": report.get("score"),
            "delivery_ready": True,
            "revision_required": False,
            "action": "accept_final",
            "reason": "quality report has no blockers and final manifest is ready",
            "blocker_codes": [],
            "targets": [],
            "rerun_steps": [],
            "downstream_steps": [],
            "final_selection": final_selection,
            "revision_strategy": _revision_strategy("accept_final", [], [], []),
            "stop_condition": "final_ready",
        }
    targets = _dedupe_targets([_normalise_blocker(blocker, task_kind) for blocker in blockers])
    primary = targets[0] if targets else {}
    action = str(primary.get("action") or report.get("next_action") or "inspect")
    rerun_steps = []
    for target in targets:
        step = str(target.get("target_step") or "")
        if step and step not in rerun_steps:
            rerun_steps.append(step)
    downstream_steps = []
    for target in targets:
        for step in target.get("downstream_steps") or []:
            step = str(step)
            if step and step not in downstream_steps:
                downstream_steps.append(step)
    final_selection = store.final_selection(run_id, store.final_result(run_id))
    return {
        "kind": "pictorium_revision_decision",
        "run_id": run_id,
        "task_kind": task_kind,
        "quality_score": report.get("score"),
        "delivery_ready": False,
        "revision_required": True,
        "action": action,
        "reason": str(primary.get("requested_change") or "quality report requires revision"),
        "blocker_codes": [_effective_code(blocker) for blocker in blockers],
        "targets": targets,
        "rerun_steps": rerun_steps,
        "downstream_steps": downstream_steps,
        "final_selection": final_selection,
        "revision_strategy": _revision_strategy(action, targets, rerun_steps, downstream_steps),
        "stop_condition": "revision_required",
    }


def write_revision_decision(
    store: MorianaRunStore,
    run_id: str,
    quality_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decision = build_revision_decision(store, run_id, quality_report)
    path = store.write_step(run_id, "revision_decision", decision, subdir="final")
    store.register_artifact(
        run_id,
        artifact_type="revision_decision",
        path=path,
        created_by="Moriana",
        step="revision_decision",
        attempt=1,
        status="accepted" if decision.get("action") == "accept_final" else "draft",
        metadata={
            "action": decision.get("action"),
            "revision_required": decision.get("revision_required"),
            "target_count": len(decision.get("targets") or []),
        },
    )
    return decision


def read_revision_decision(run_dir: Path) -> dict[str, Any]:
    return read_json(run_dir / "final" / "revision_decision.json", {"ok": False, "error": "revision decision is not ready"})
