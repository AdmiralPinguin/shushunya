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
    if manifest_status != "ready":
        honest_errors.append(f"final_manifest status is not ready: {manifest_status or '<missing>'}")
    return {
        "present": True,
        "passed": not honest_errors,
        "reason": "; ".join(honest_errors),
        "trial_result": str(trial_result_path),
        "final_manifest": str(manifest_path),
        "missing_checks": sorted(REQUIRED_HONEST_CHECKS - set(honest.get("checks", {}) if isinstance(honest.get("checks"), dict) else {})),
        "manifest_status": manifest_status,
        "honest_evidence": honest,
    }
