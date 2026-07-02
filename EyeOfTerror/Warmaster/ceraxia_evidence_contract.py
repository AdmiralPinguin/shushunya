#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQUIRED_HONEST_CHECKS = {
    "source_correct",
    "tests_not_adjusted",
    "patch_minimal",
    "verification_meaningful",
    "review_artifacts_present",
}

NEXT_STAGE_PACKAGE_KIND = "ceraxia_next_stage_evidence_package"
NEXT_STAGE_SUCCESS_STATUSES = {"fully_successful", "success", "passed", "repaired_success", "success_after_repair"}
NEXT_STAGE_BLOCKED_OR_FAILED_STATUSES = {
    "failed",
    "broken",
    "honest_blocked",
    "expected_blocked",
    "blocked",
    "reviewer_rejected",
}
REQUIRED_NEXT_STAGE_ARTIFACTS = {
    "repo_investigation",
    "planning",
    "execution",
    "verification",
    "review",
}


def resolve_repo_path(repo_root: Path, path_text: str) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return repo_root / path


def load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def find_trial_result_path(repo_root: Path, evidence_paths: Any) -> Path | None:
    if not isinstance(evidence_paths, list):
        return None
    for item in evidence_paths:
        if not isinstance(item, str):
            continue
        path = resolve_repo_path(repo_root, item)
        if path.name == "trial_result.json" and path.exists():
            return path
    return None


def find_manifest_path(repo_root: Path, evidence_paths: Any, trial_result: dict[str, Any]) -> Path | None:
    manifest_text = str(trial_result.get("final_manifest") or "")
    if manifest_text:
        manifest_path = resolve_repo_path(repo_root, manifest_text)
        if manifest_path.exists():
            return manifest_path
    if isinstance(evidence_paths, list):
        for item in evidence_paths:
            if not isinstance(item, str):
                continue
            path = resolve_repo_path(repo_root, item)
            if path.name == "final_manifest.json" and path.exists():
                return path
    return None


def validate_honest_evidence_payload(honest: Any) -> list[str]:
    if not isinstance(honest, dict) or honest.get("status") != "passed":
        return ["honest_evidence.status must be passed"]
    checks = honest.get("checks") if isinstance(honest.get("checks"), dict) else {}
    missing = sorted(REQUIRED_HONEST_CHECKS - set(checks))
    errors = [f"missing honest_evidence checks: {', '.join(missing)}"] if missing else []
    failed = [
        name
        for name in REQUIRED_HONEST_CHECKS
        if name in checks and (not isinstance(checks[name], dict) or checks[name].get("passed") is not True)
    ]
    if failed:
        errors.append(f"failed honest_evidence checks: {', '.join(sorted(failed))}")
    return errors


def validate_final_manifest_payload(manifest: Any, manifest_status: str) -> list[str]:
    if not isinstance(manifest, dict):
        return ["final_manifest must be an object"]
    errors: list[str] = []
    if manifest_status != "ready":
        errors.append(f"final_manifest status is not ready: {manifest_status or '<missing>'}")
    if manifest.get("approved") is not True:
        errors.append("final_manifest approved must be true")
    changed_files = manifest.get("changed_files")
    if not isinstance(changed_files, list) or not changed_files:
        errors.append("final_manifest changed_files must be a non-empty list")
    verification = manifest.get("verification_summary") if isinstance(manifest.get("verification_summary"), dict) else {}
    if not verification:
        errors.append("final_manifest verification_summary is missing")
    elif int(verification.get("executed_count") or 0) <= 0:
        errors.append("final_manifest verification_summary.executed_count must be positive")
    if int(verification.get("blocker_count") or 0) > 0:
        errors.append("final_manifest verification_summary.blocker_count must be zero")
    review_record = manifest.get("review_decision_record")
    if not isinstance(review_record, list) or not review_record:
        errors.append("final_manifest review_decision_record must be a non-empty list")
    return errors


def validate_next_stage_evidence_payload(
    payload: Any,
    next_stage: dict[str, Any],
    entry: dict[str, Any],
    trial: dict[str, Any],
) -> list[str]:
    if not isinstance(payload, dict):
        return ["next_stage evidence_package must be an object"]

    errors: list[str] = []
    status = str(next_stage.get("status") or "")
    package_status = str(payload.get("status") or "")
    class_name = str(next_stage.get("class") or trial.get("class") or "")
    package_class = str(payload.get("task_class") or "")
    attempt_count = next_stage.get("attempt_count", next_stage.get("attempts"))
    package_attempt_count = payload.get("attempt_count", payload.get("attempts"))
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
    missing_artifacts = sorted(REQUIRED_NEXT_STAGE_ARTIFACTS - set(artifacts))
    changed_files = payload.get("changed_files")
    package_multi_file = payload.get("multi_file_nonfixture")

    if payload.get("kind") != NEXT_STAGE_PACKAGE_KIND:
        errors.append(f"evidence_package.kind must be {NEXT_STAGE_PACKAGE_KIND}")
    if payload.get("contract_version") not in {1, "1"}:
        errors.append("evidence_package.contract_version must be 1")
    if str(payload.get("run_id") or "") != str(entry.get("run_id") or ""):
        errors.append("evidence_package.run_id must match ledger entry")
    if str(payload.get("trial_id") or "") != str(entry.get("trial_id") or ""):
        errors.append("evidence_package.trial_id must match ledger entry")
    if payload.get("real_repo_task") is not True:
        errors.append("evidence_package.real_repo_task must be true")
    if payload.get("fixture_only") is True:
        errors.append("evidence_package.fixture_only must not be true")
    if not class_name:
        errors.append("next_stage class or trial class is required")
    elif package_class != class_name:
        errors.append("evidence_package.task_class must match next_stage/trial class")
    if not status:
        errors.append("next_stage.status is required")
    elif package_status != status:
        errors.append("evidence_package.status must match next_stage.status")
    if not isinstance(attempt_count, (int, float)) or int(attempt_count) <= 0:
        errors.append("next_stage.attempt_count must be positive")
    elif package_attempt_count != attempt_count:
        errors.append("evidence_package.attempt_count must match next_stage.attempt_count")
    if payload.get("false_success") is True or next_stage.get("false_success") is True:
        errors.append("next_stage evidence cannot contain false_success=true")
    if missing_artifacts:
        errors.append(f"evidence_package missing artifacts: {', '.join(missing_artifacts)}")
    if not isinstance(changed_files, list):
        errors.append("evidence_package.changed_files must be a list")
    if status in NEXT_STAGE_SUCCESS_STATUSES:
        if not changed_files:
            errors.append("successful next_stage evidence requires changed_files")
        if payload.get("verification_passed") is not True:
            errors.append("successful next_stage evidence requires verification_passed=true")
        if payload.get("review_accepted") is not True:
            errors.append("successful next_stage evidence requires review_accepted=true")
    if status in NEXT_STAGE_BLOCKED_OR_FAILED_STATUSES and not (payload.get("postmortem") or next_stage.get("postmortem") or entry.get("human_review_notes")):
        errors.append("failed/blocked next_stage evidence requires a postmortem")
    if next_stage.get("multi_file_nonfixture") is True:
        if package_multi_file is not True:
            errors.append("evidence_package.multi_file_nonfixture must match next_stage")
        if not isinstance(changed_files, list) or len(changed_files) < 2:
            errors.append("multi_file_nonfixture evidence requires at least two changed files")
    return errors


def load_next_stage_package(repo_root: Path, package_ref: Any) -> tuple[dict[str, Any] | None, str]:
    if isinstance(package_ref, dict):
        return package_ref, "<inline>"
    if not isinstance(package_ref, str) or not package_ref:
        return None, "missing evidence_package"
    path = resolve_repo_path(repo_root, package_ref)
    try:
        return load_json_object(path), str(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return None, f"unreadable evidence_package: {exc}"


def next_stage_evidence_status(repo_root: Path, entry: dict[str, Any], trial: dict[str, Any]) -> dict[str, Any]:
    next_stage = entry.get("next_stage") if isinstance(entry.get("next_stage"), dict) else {}
    if not next_stage:
        return {"present": False, "passed": False, "reason": "missing next_stage"}
    payload, package_source = load_next_stage_package(repo_root, next_stage.get("evidence_package"))
    if payload is None:
        return {"present": False, "passed": False, "reason": package_source}
    errors = validate_next_stage_evidence_payload(payload, next_stage, entry, trial)
    return {
        "present": True,
        "passed": not errors,
        "reason": "; ".join(errors),
        "package": package_source,
        "missing_artifacts": sorted(
            REQUIRED_NEXT_STAGE_ARTIFACTS
            - set(payload.get("artifacts", {}) if isinstance(payload.get("artifacts"), dict) else {})
        ),
    }


def evidence_package_status(repo_root: Path, entry: dict[str, Any]) -> dict[str, Any]:
    evidence_paths = entry.get("evidence_paths")
    if not isinstance(evidence_paths, list):
        return {"present": False, "passed": False, "reason": "missing evidence_paths"}
    trial_result_path = find_trial_result_path(repo_root, evidence_paths)
    if trial_result_path is None:
        return {"present": False, "passed": False, "reason": "missing readable trial_result.json"}
    try:
        trial_result = load_json_object(trial_result_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {"present": False, "passed": False, "reason": f"unreadable trial_result.json: {exc}"}

    manifest_path = find_manifest_path(repo_root, evidence_paths, trial_result)
    if manifest_path is None:
        return {
            "present": True,
            "passed": False,
            "reason": "missing readable final_manifest.json",
            "trial_result": str(trial_result_path),
        }
    try:
        manifest = load_json_object(manifest_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {
            "present": True,
            "passed": False,
            "reason": f"unreadable final_manifest.json: {exc}",
            "trial_result": str(trial_result_path),
            "final_manifest": str(manifest_path),
        }

    honest = trial_result.get("honest_evidence") if isinstance(trial_result.get("honest_evidence"), dict) else {}
    honest_errors = validate_honest_evidence_payload(honest)
    manifest_status = str(manifest.get("status") or trial_result.get("manifest_summary", {}).get("status") or "")
    honest_errors.extend(validate_final_manifest_payload(manifest, manifest_status))
    return {
        "present": True,
        "passed": not honest_errors,
        "reason": "; ".join(honest_errors),
        "trial_result": str(trial_result_path),
        "final_manifest": str(manifest_path),
        "missing_checks": sorted(REQUIRED_HONEST_CHECKS - set(honest.get("checks", {}) if isinstance(honest.get("checks"), dict) else {})),
        "manifest_status": manifest_status,
        "manifest_approved": manifest.get("approved") is True,
        "honest_evidence": honest,
    }
