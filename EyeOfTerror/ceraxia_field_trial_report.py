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
    expert_target = spec.get("expert_target") if isinstance(spec.get("expert_target"), dict) else {}
    trial_by_id = {
        str(item.get("id")): item
        for item in spec.get("trials", [])
        if isinstance(item, dict) and item.get("id")
    }
    entries = [item for item in ledger.get("entries", []) if isinstance(item, dict)]
    accepted = [item for item in entries if item.get("accepted_for_rolling_score") is True]
    scores_by_dimension: dict[str, list[float]] = {dimension: [] for dimension in dimensions}
    expert_scores_by_dimension: dict[str, list[float]] = {dimension: [] for dimension in dimensions}
    low_score_entries: dict[str, list[dict[str, Any]]] = {dimension: [] for dimension in dimensions}
    classes: set[str] = set()
    expert_classes: set[str] = set()
    expert_entries: list[dict[str, Any]] = []
    unshaped_expert_entries: list[dict[str, Any]] = []
    dimension_min = float(target.get("dimension_average_min") or 0)
    dimension_sample_min = int(target.get("dimension_sample_min") or 0)
    for entry in accepted:
        trial = trial_by_id.get(str(entry.get("trial_id") or ""), {})
        if trial.get("class"):
            classes.add(str(trial.get("class")))
            if trial.get("difficulty") == "expert":
                expert_classes.add(str(trial.get("class")))
        if trial.get("difficulty") == "expert":
            expert_entries.append(entry)
            if str(entry.get("trial_id") or "").startswith("ceraxia-expert-unshaped-"):
                unshaped_expert_entries.append(entry)
        applicable = trial.get("applicable_dimensions")
        applicable_dimensions = (
            {str(item) for item in applicable}
            if isinstance(applicable, list) and applicable
            else set(dimensions)
        )
        scores = entry.get("scores") if isinstance(entry.get("scores"), dict) else {}
        for dimension in dimensions:
            if dimension not in applicable_dimensions:
                continue
            value = scores.get(dimension)
            if isinstance(value, (int, float)):
                scores_by_dimension[dimension].append(float(value))
                if trial.get("difficulty") == "expert":
                    expert_scores_by_dimension[dimension].append(float(value))
                if float(value) < dimension_min:
                    low_score_entries[dimension].append(
                        {
                            "trial_id": entry.get("trial_id", ""),
                            "run_id": entry.get("run_id", ""),
                            "class": trial.get("class", ""),
                            "score": float(value),
                            "follow_up_changes": entry.get("follow_up_changes", []),
                            "generalizable_failures": entry.get("generalizable_failures", []),
                        }
                    )
    dimension_averages = {
        dimension: average(values)
        for dimension, values in scores_by_dimension.items()
    }
    expert_dimension_averages = {
        dimension: average(values)
        for dimension, values in expert_scores_by_dimension.items()
    }
    dimension_sample_counts = {
        dimension: len(values)
        for dimension, values in scores_by_dimension.items()
    }
    expert_dimension_sample_counts = {
        dimension: len(values)
        for dimension, values in expert_scores_by_dimension.items()
    }
    overall = average(list(dimension_averages.values())) if dimension_averages else 0.0
    expert_overall = average(list(expert_dimension_averages.values())) if expert_dimension_averages else 0.0
    enough_trials = len(accepted) >= int(target.get("minimum_representative_trials") or 0)
    enough_dimensions = all(value >= dimension_min for value in dimension_averages.values())
    enough_dimension_samples = all(count >= dimension_sample_min for count in dimension_sample_counts.values())
    enough_overall = overall >= float(target.get("rolling_average_min") or 0)
    target_met = bool(enough_trials and enough_dimensions and enough_dimension_samples and enough_overall)
    expert_dimension_min = float(expert_target.get("dimension_average_min") or 0)
    expert_sample_min = int(expert_target.get("dimension_sample_min") or 0)
    expert_entry_min = float(expert_target.get("minimum_entry_score") or 0)
    expert_low_entry_scores: dict[str, list[dict[str, Any]]] = {dimension: [] for dimension in dimensions}
    if expert_entry_min:
        for entry in expert_entries:
            trial = trial_by_id.get(str(entry.get("trial_id") or ""), {})
            applicable = trial.get("applicable_dimensions")
            applicable_dimensions = (
                {str(item) for item in applicable}
                if isinstance(applicable, list) and applicable
                else set(dimensions)
            )
            scores = entry.get("scores") if isinstance(entry.get("scores"), dict) else {}
            for dimension in dimensions:
                if dimension not in applicable_dimensions:
                    continue
                value = scores.get(dimension)
                if isinstance(value, (int, float)) and float(value) < expert_entry_min:
                    expert_low_entry_scores[dimension].append(
                        {
                            "trial_id": entry.get("trial_id", ""),
                            "run_id": entry.get("run_id", ""),
                            "class": trial.get("class", ""),
                            "difficulty": trial.get("difficulty", ""),
                            "score": float(value),
                        }
                    )
    enough_expert_trials = len(expert_entries) >= int(expert_target.get("minimum_expert_trials") or 0)
    enough_expert_classes = len(expert_classes) >= int(expert_target.get("minimum_expert_classes") or 0)
    enough_unshaped_expert_trials = len(unshaped_expert_entries) >= int(expert_target.get("minimum_unshaped_expert_trials") or 0)
    enough_expert_dimensions = all(value >= expert_dimension_min for value in expert_dimension_averages.values())
    enough_expert_samples = all(count >= expert_sample_min for count in expert_dimension_sample_counts.values())
    enough_expert_overall = expert_overall >= float(expert_target.get("rolling_average_min") or 0)
    enough_expert_entry_scores = not any(expert_low_entry_scores.values())
    expert_target_met = bool(
        expert_target
        and enough_expert_trials
        and enough_expert_classes
        and enough_unshaped_expert_trials
        and enough_expert_dimensions
        and enough_expert_samples
        and enough_expert_overall
        and enough_expert_entry_scores
    )
    return {
        "target_met": target_met,
        "expert_target_met": expert_target_met,
        "overall_score": overall,
        "expert_overall_score": expert_overall,
        "dimension_averages": dimension_averages,
        "expert_dimension_averages": expert_dimension_averages,
        "dimension_sample_counts": dimension_sample_counts,
        "expert_dimension_sample_counts": expert_dimension_sample_counts,
        "accepted_trial_count": len(accepted),
        "draft_trial_count": len(entries) - len(accepted),
        "covered_classes": sorted(classes),
        "covered_expert_classes": sorted(expert_classes),
        "target": target,
        "expert_target": expert_target,
        "gaps": {
            "needs_more_accepted_trials": not enough_trials,
            "needs_higher_overall": not enough_overall,
            "needs_more_dimension_evidence": [
                dimension
                for dimension, count in dimension_sample_counts.items()
                if count < dimension_sample_min
            ],
            "needs_higher_dimension_scores": [
                dimension
                for dimension, value in dimension_averages.items()
                if value < dimension_min
            ],
            "low_score_entries": {
                dimension: items
                for dimension, items in low_score_entries.items()
                if items
            },
        },
        "expert_gaps": {
            "needs_more_expert_trials": not enough_expert_trials,
            "needs_more_expert_classes": not enough_expert_classes,
            "needs_higher_overall": not enough_expert_overall,
            "needs_more_dimension_evidence": [
                dimension
                for dimension, count in expert_dimension_sample_counts.items()
                if count < expert_sample_min
            ],
            "needs_higher_dimension_scores": [
                dimension
                for dimension, value in expert_dimension_averages.items()
                if value < expert_dimension_min
            ],
            "needs_higher_entry_scores": {
                dimension: items
                for dimension, items in expert_low_entry_scores.items()
                if items
            },
            "expert_trial_count": len(expert_entries),
            "expert_class_count": len(expert_classes),
            "unshaped_expert_trial_count": len(unshaped_expert_entries),
            "needs_more_unshaped_expert_trials": not enough_unshaped_expert_trials,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Report human-reviewed Ceraxia field trial progress.")
    parser.add_argument("--require-target", action="store_true", help="Exit non-zero unless the real 7/10 target is met.")
    parser.add_argument("--require-expert-target", action="store_true", help="Exit non-zero unless the real 10/10 expert target is met.")
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
    if args.require_expert_target and not report["expert_target_met"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
