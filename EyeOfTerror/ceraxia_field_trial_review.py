#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "Mechanicum" / "Ceraxia" / "field_trials.json"
LEDGER = ROOT / "Mechanicum" / "Ceraxia" / "field_trial_ledger.json"


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return ROOT.parent / path


def load_trial_result(entry: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    evidence_paths = entry.get("evidence_paths")
    if not isinstance(evidence_paths, list) or not evidence_paths:
        raise ValueError(f"ledger entry {entry.get('run_id', '')} has no evidence_paths")
    for item in evidence_paths:
        if not isinstance(item, str):
            continue
        path = resolve_repo_path(item)
        if path.name == "trial_result.json" and path.exists():
            return path, load_json(path)
    raise ValueError(f"ledger entry {entry.get('run_id', '')} has no readable trial_result.json evidence")


def load_manifest(trial_result: dict[str, Any]) -> tuple[Path | None, dict[str, Any]]:
    manifest_text = str(trial_result.get("final_manifest") or "")
    if not manifest_text:
        return None, {}
    path = resolve_repo_path(manifest_text)
    if not path.exists():
        return path, {}
    return path, load_json(path)


def spec_by_trial_id(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id")): item
        for item in spec.get("trials", [])
        if isinstance(item, dict) and item.get("id")
    }


def dimensions(spec: dict[str, Any]) -> list[str]:
    return [str(item) for item in spec.get("dimensions", [])]


def review_questions(trial_spec: dict[str, Any], trial_result: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    questions: list[str] = []
    outcome = trial_result.get("trial_outcome") if isinstance(trial_result.get("trial_outcome"), dict) else {}
    manifest_status = str(manifest.get("status") or trial_result.get("manifest_summary", {}).get("status") or "")
    if outcome.get("expected") is not True:
        questions.append("Trial outcome is not expected; score patch_correctness/review_quality conservatively unless the failure is intentionally accepted.")
    if manifest_status == "blocked":
        questions.append("Blocked trial: verify this was the safest correct outcome, not an inability to proceed.")
    if manifest_status == "ready" and not manifest.get("approved", trial_result.get("manifest_summary", {}).get("status") == "ready"):
        questions.append("Ready trial has weak approval evidence; inspect code_review.json before accepting.")
    verification = manifest.get("verification_summary") if isinstance(manifest.get("verification_summary"), dict) else {}
    if not verification:
        verification = trial_result.get("manifest_summary", {}).get("verification_summary", {})
    if not isinstance(verification, dict) or int(verification.get("executed_count") or 0) == 0:
        questions.append("No executed verification evidence found; verification_discipline should be low.")
    changed_files = manifest.get("changed_files") if isinstance(manifest.get("changed_files"), list) else trial_result.get("manifest_summary", {}).get("changed_files", [])
    if manifest_status == "ready" and not changed_files:
        questions.append("Ready trial has no changed files; confirm this was expected.")
    diagnostics = manifest.get("diagnostics") if isinstance(manifest.get("diagnostics"), dict) else trial_result.get("manifest_summary", {}).get("diagnostics", {})
    if trial_spec.get("class") in {"integration_change", "api_compatibility", "data_shape_change"} and not diagnostics:
        questions.append("High-contract trial lacks diagnostics; inspect evidence before scoring repository_investigation or review_quality high.")
    for mode in trial_spec.get("failure_modes_to_watch", []):
        questions.append(f"Check failure mode: {mode}")
    return questions


def build_review_packet(entry: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    trial_specs = spec_by_trial_id(spec)
    trial_id = str(entry.get("trial_id") or "")
    trial_spec = trial_specs.get(trial_id)
    if not trial_spec:
        raise ValueError(f"unknown trial_id in ledger: {trial_id}")
    result_path, trial_result = load_trial_result(entry)
    manifest_path, manifest = load_manifest(trial_result)
    manifest_summary = trial_result.get("manifest_summary") if isinstance(trial_result.get("manifest_summary"), dict) else {}
    changed_files = manifest.get("changed_files") if isinstance(manifest.get("changed_files"), list) else manifest_summary.get("changed_files", [])
    verification_summary = manifest.get("verification_summary") if isinstance(manifest.get("verification_summary"), dict) else manifest_summary.get("verification_summary", {})
    return {
        "trial_id": trial_id,
        "run_id": entry.get("run_id", ""),
        "class": trial_spec.get("class", ""),
        "difficulty": trial_spec.get("difficulty", ""),
        "task": trial_spec.get("task", ""),
        "accepted_for_rolling_score": entry.get("accepted_for_rolling_score") is True,
        "evidence": {
            "trial_result": str(result_path),
            "final_manifest": str(manifest_path) if manifest_path else "",
            "ledger_evidence_paths": entry.get("evidence_paths", []),
        },
        "observed": {
            "trial_outcome": trial_result.get("trial_outcome", {}),
            "trial_checks": trial_result.get("trial_checks", {}),
            "honest_evidence": trial_result.get("honest_evidence", {}),
            "manifest_status": manifest.get("status", manifest_summary.get("status", "")),
            "patch_source": manifest.get("patch_source", manifest_summary.get("patch_source", "")),
            "changed_files": changed_files,
            "verification_summary": verification_summary,
            "blockers": manifest.get("blockers", manifest_summary.get("blockers", [])),
            "diagnostics": manifest.get("diagnostics", manifest_summary.get("diagnostics", {})),
        },
        "required_evidence": trial_spec.get("required_evidence", []),
        "failure_modes_to_watch": trial_spec.get("failure_modes_to_watch", []),
        "review_questions": review_questions(trial_spec, trial_result, manifest),
        "score_sheet": {
            dimension: None
            for dimension in dimensions(spec)
        },
        "acceptance_requirements": [
            "Fill every score with a number from 0 to 10.",
            "Write human_review_notes explaining the score.",
            "Set accepted_for_rolling_score=true only after inspecting evidence paths.",
            "Record generalizable_failures and follow_up_changes for any score below 7.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build human-review worksheets for Ceraxia field trial evidence.")
    parser.add_argument("--trial-id", help="Only emit review packets for this trial id.")
    parser.add_argument("--run-id", help="Only emit the review packet for this run id.")
    parser.add_argument("--all", action="store_true", help="Emit all matching review packets.")
    args = parser.parse_args()
    spec = load_json(SPEC)
    ledger = load_json(LEDGER)
    entries = [item for item in ledger.get("entries", []) if isinstance(item, dict)]
    if args.trial_id:
        entries = [item for item in entries if item.get("trial_id") == args.trial_id]
    if args.run_id:
        entries = [item for item in entries if item.get("run_id") == args.run_id]
    if not args.all and len(entries) > 1:
        raise SystemExit("multiple entries match; pass --all or narrow with --trial-id/--run-id")
    packets = [build_review_packet(entry, spec) for entry in entries]
    output: Any = packets if args.all else (packets[0] if packets else {})
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if packets else 1


if __name__ == "__main__":
    raise SystemExit(main())
