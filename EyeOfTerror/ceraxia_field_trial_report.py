#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "InnerCircle" / "Ceraxia" / "field_trials.json"
LEDGER = ROOT / "InnerCircle" / "Ceraxia" / "field_trial_ledger.json"


def average(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def validate_ledger(spec: dict[str, Any], ledger: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    dimensions = [str(item) for item in spec.get("dimensions", [])]
    trial_ids = {str(item.get("id")) for item in spec.get("trials", []) if isinstance(item, dict)}
    entries = ledger.get("entries") if isinstance(ledger.get("entries"), list) else []
    seen_runs: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"entry {index} must be an object")
            continue
        trial_id = str(entry.get("trial_id") or "")
        run_id = str(entry.get("run_id") or "")
        accepted = entry.get("accepted_for_rolling_score") is True
        if trial_id not in trial_ids:
            errors.append(f"entry {index} references unknown trial_id: {trial_id}")
        if run_id:
            if run_id in seen_runs:
                errors.append(f"entry {index} duplicates run_id: {run_id}")
            seen_runs.add(run_id)
        scores = entry.get("scores") if isinstance(entry.get("scores"), dict) else {}
        if set(scores) != set(dimensions):
            errors.append(f"entry {index} score dimensions drift from spec")
        if accepted:
            if not run_id:
                errors.append(f"accepted entry {index} must include run_id")
            if not entry.get("human_review_notes"):
                errors.append(f"accepted entry {index} must include human_review_notes")
            evidence_paths = entry.get("evidence_paths")
            if not isinstance(evidence_paths, list) or not evidence_paths:
                errors.append(f"accepted entry {index} must include evidence_paths")
            for dimension in dimensions:
                value = scores.get(dimension)
                if not isinstance(value, (int, float)) or value < 0 or value > 10:
                    errors.append(f"accepted entry {index} has invalid score for {dimension}: {value}")
    return errors


def build_report(spec: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    dimensions = [str(item) for item in spec.get("dimensions", [])]
    target = spec.get("target") if isinstance(spec.get("target"), dict) else {}
    trial_by_id = {
        str(item.get("id")): item
        for item in spec.get("trials", [])
        if isinstance(item, dict) and item.get("id")
    }
    entries = [item for item in ledger.get("entries", []) if isinstance(item, dict)]
    accepted = [item for item in entries if item.get("accepted_for_rolling_score") is True]
    scores_by_dimension: dict[str, list[float]] = {dimension: [] for dimension in dimensions}
    classes: set[str] = set()
    for entry in accepted:
        trial = trial_by_id.get(str(entry.get("trial_id") or ""), {})
        if trial.get("class"):
            classes.add(str(trial.get("class")))
        scores = entry.get("scores") if isinstance(entry.get("scores"), dict) else {}
        for dimension in dimensions:
            value = scores.get(dimension)
            if isinstance(value, (int, float)):
                scores_by_dimension[dimension].append(float(value))
    dimension_averages = {
        dimension: average(values)
        for dimension, values in scores_by_dimension.items()
    }
    overall = average(list(dimension_averages.values())) if dimension_averages else 0.0
    enough_trials = len(accepted) >= int(target.get("minimum_representative_trials") or 0)
    enough_dimensions = all(value >= float(target.get("dimension_average_min") or 0) for value in dimension_averages.values())
    enough_overall = overall >= float(target.get("rolling_average_min") or 0)
    target_met = bool(enough_trials and enough_dimensions and enough_overall)
    return {
        "target_met": target_met,
        "overall_score": overall,
        "dimension_averages": dimension_averages,
        "accepted_trial_count": len(accepted),
        "draft_trial_count": len(entries) - len(accepted),
        "covered_classes": sorted(classes),
        "target": target,
        "gaps": {
            "needs_more_accepted_trials": not enough_trials,
            "needs_higher_overall": not enough_overall,
            "needs_higher_dimension_scores": [
                dimension
                for dimension, value in dimension_averages.items()
                if value < float(target.get("dimension_average_min") or 0)
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Report human-reviewed Ceraxia field trial progress.")
    parser.add_argument("--require-target", action="store_true", help="Exit non-zero unless the real 7/10 target is met.")
    args = parser.parse_args()
    spec = load_json(SPEC)
    ledger = load_json(LEDGER)
    errors = validate_ledger(spec, ledger)
    report = build_report(spec, ledger)
    report["ledger_valid"] = not errors
    report["ledger_errors"] = errors
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        return 2
    if args.require_target and not report["target_met"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
