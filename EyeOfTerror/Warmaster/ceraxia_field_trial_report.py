#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ceraxia_evidence_contract import evidence_package_status, next_stage_evidence_status


WARMASTER_ROOT = Path(__file__).resolve().parent
EYE_ROOT = WARMASTER_ROOT.parent
SPEC = EYE_ROOT / "Mechanicum" / "Ceraxia" / "field_trials.json"
LEDGER = EYE_ROOT / "Mechanicum" / "Ceraxia" / "field_trial_ledger.json"


def average(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def honest_evidence_status(entry: dict[str, Any]) -> dict[str, Any]:
    return evidence_package_status(EYE_ROOT.parent, entry)


def next_stage_package_status(entry: dict[str, Any], trial: dict[str, Any]) -> dict[str, Any]:
    return next_stage_evidence_status(EYE_ROOT.parent, entry, trial)


def task_catalog_by_id(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for section in ("trials", "live_tasks"):
        for item in spec.get(section, []):
            if isinstance(item, dict) and item.get("id"):
                catalog[str(item["id"])] = item
    return catalog


def validate_ledger(spec: dict[str, Any], ledger: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    dimensions = [str(item) for item in spec.get("dimensions", [])]
    trial_ids = set(task_catalog_by_id(spec))
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


def build_next_stage_metrics(spec: dict[str, Any], entries: list[dict[str, Any]], trial_by_id: dict[str, Any]) -> dict[str, Any]:
    target = spec.get("next_stage_target") if isinstance(spec.get("next_stage_target"), dict) else {}
    live_entries = [
        entry
        for entry in entries
        if isinstance(entry.get("next_stage"), dict)
    ]
    class_names: set[str] = set()
    successful = 0
    repaired_successes = 0
    honest_blocks = 0
    broken = 0
    reviewer_rejected = 0
    false_successes = 0
    multi_file_nonfixture = 0
    attempts: list[float] = []
    postmortem_gaps: list[dict[str, str]] = []
    evidence_gaps: list[dict[str, str]] = []
    for entry in live_entries:
        next_stage = entry.get("next_stage") if isinstance(entry.get("next_stage"), dict) else {}
        trial = trial_by_id.get(str(entry.get("trial_id") or ""), {})
        class_name = str(next_stage.get("class") or trial.get("class") or "")
        if class_name:
            class_names.add(class_name)
        status = str(next_stage.get("status") or "")
        if status in {"fully_successful", "success", "passed"}:
            successful += 1
        if status in {"repaired_success", "success_after_repair"}:
            successful += 1
            repaired_successes += 1
        if status in {"honest_blocked", "expected_blocked", "blocked"}:
            honest_blocks += 1
        if status in {"failed", "broken"}:
            broken += 1
        if next_stage.get("reviewer_rejected") is True or status == "reviewer_rejected":
            reviewer_rejected += 1
        if next_stage.get("false_success") is True:
            false_successes += 1
        if next_stage.get("multi_file_nonfixture") is True:
            multi_file_nonfixture += 1
        attempt_count = next_stage.get("attempt_count", next_stage.get("attempts"))
        if isinstance(attempt_count, (int, float)):
            attempts.append(float(attempt_count))
        needs_postmortem = status in {"failed", "broken", "honest_blocked", "expected_blocked", "blocked", "reviewer_rejected"}
        has_postmortem = bool(next_stage.get("postmortem")) or bool(entry.get("human_review_notes"))
        if needs_postmortem and not has_postmortem:
            postmortem_gaps.append(
                {
                    "trial_id": str(entry.get("trial_id") or ""),
                    "run_id": str(entry.get("run_id") or ""),
                    "status": status,
                }
            )
        evidence_status = next_stage_package_status(entry, trial)
        if evidence_status.get("passed") is not True:
            evidence_gaps.append(
                {
                    "trial_id": str(entry.get("trial_id") or ""),
                    "run_id": str(entry.get("run_id") or ""),
                    "reason": str(evidence_status.get("reason") or "next-stage evidence package missing or incomplete"),
                }
            )
    live_count = len(live_entries)
    success_rate = round(successful / live_count, 3) if live_count else 0.0
    minimum_live_tasks = int(target.get("minimum_live_tasks") or 0)
    minimum_task_classes = int(target.get("minimum_task_classes") or target.get("minimum_task_class_variety") or 0)
    minimum_success_rate = float(target.get("minimum_success_rate") or 0)
    maximum_false_successes = int(target.get("maximum_false_successes", 0))
    minimum_multifile = int(target.get("minimum_multifile_nonfixture_tasks") or 0)
    require_evidence = target.get("require_next_stage_evidence_package") is True
    target_met = bool(
        target
        and live_count >= minimum_live_tasks
        and len(class_names) >= minimum_task_classes
        and success_rate >= minimum_success_rate
        and false_successes <= maximum_false_successes
        and multi_file_nonfixture >= minimum_multifile
        and not postmortem_gaps
        and (not require_evidence or not evidence_gaps)
    )
    return {
        "target_met": target_met,
        "target": target,
        "live_task_count": live_count,
        "task_class_count": len(class_names),
        "task_classes": sorted(class_names),
        "fully_successful_count": successful,
        "repaired_success_count": repaired_successes,
        "honest_blocked_count": honest_blocks,
        "broken_count": broken,
        "reviewer_rejected_count": reviewer_rejected,
        "false_success_count": false_successes,
        "success_rate": success_rate,
        "average_attempt_count": average(attempts),
        "multi_file_nonfixture_count": multi_file_nonfixture,
        "postmortem_gap_count": len(postmortem_gaps),
        "postmortem_gaps": postmortem_gaps,
        "evidence_gap_count": len(evidence_gaps),
        "evidence_gaps": evidence_gaps,
        "gaps": {
            "needs_more_live_tasks": live_count < minimum_live_tasks,
            "needs_more_task_classes": len(class_names) < minimum_task_classes,
            "needs_higher_success_rate": success_rate < minimum_success_rate,
            "has_false_successes": false_successes > maximum_false_successes,
            "needs_more_multifile_nonfixture_tasks": multi_file_nonfixture < minimum_multifile,
            "has_postmortem_gaps": bool(postmortem_gaps),
            "has_evidence_gaps": bool(evidence_gaps),
        },
    }


def build_report(spec: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    dimensions = [str(item) for item in spec.get("dimensions", [])]
    target = spec.get("target") if isinstance(spec.get("target"), dict) else {}
    expert_target = spec.get("expert_target") if isinstance(spec.get("expert_target"), dict) else {}
    trial_by_id = task_catalog_by_id(spec)
    entries = [item for item in ledger.get("entries", []) if isinstance(item, dict)]
    accepted = [item for item in entries if item.get("accepted_for_rolling_score") is True]
    next_stage_metrics = build_next_stage_metrics(spec, entries, trial_by_id)

    def applicable_scores_for(entries_to_score: list[dict[str, Any]]) -> dict[str, list[float]]:
        collected: dict[str, list[float]] = {dimension: [] for dimension in dimensions}
        for score_entry in entries_to_score:
            trial = trial_by_id.get(str(score_entry.get("trial_id") or ""), {})
            applicable = trial.get("applicable_dimensions")
            applicable_dimensions = (
                {str(item) for item in applicable}
                if isinstance(applicable, list) and applicable
                else set(dimensions)
            )
            scores = score_entry.get("scores") if isinstance(score_entry.get("scores"), dict) else {}
            for dimension in dimensions:
                if dimension not in applicable_dimensions:
                    continue
                value = scores.get(dimension)
                if isinstance(value, (int, float)):
                    collected[dimension].append(float(value))
        return collected

    def low_scores_for(entries_to_score: list[dict[str, Any]], floor: float) -> dict[str, list[dict[str, Any]]]:
        lows: dict[str, list[dict[str, Any]]] = {dimension: [] for dimension in dimensions}
        if not floor:
            return lows
        for score_entry in entries_to_score:
            trial = trial_by_id.get(str(score_entry.get("trial_id") or ""), {})
            applicable = trial.get("applicable_dimensions")
            applicable_dimensions = (
                {str(item) for item in applicable}
                if isinstance(applicable, list) and applicable
                else set(dimensions)
            )
            scores = score_entry.get("scores") if isinstance(score_entry.get("scores"), dict) else {}
            for dimension in dimensions:
                if dimension not in applicable_dimensions:
                    continue
                value = scores.get(dimension)
                if isinstance(value, (int, float)) and float(value) < floor:
                    lows[dimension].append(
                        {
                            "trial_id": score_entry.get("trial_id", ""),
                            "run_id": score_entry.get("run_id", ""),
                            "class": trial.get("class", ""),
                            "difficulty": trial.get("difficulty", ""),
                            "score": float(value),
                        }
                    )
        return lows

    scores_by_dimension: dict[str, list[float]] = {dimension: [] for dimension in dimensions}
    honest_scores_by_dimension: dict[str, list[float]] = {dimension: [] for dimension in dimensions}
    expert_scores_by_dimension: dict[str, list[float]] = {dimension: [] for dimension in dimensions}
    honest_expert_scores_by_dimension: dict[str, list[float]] = {dimension: [] for dimension in dimensions}
    low_score_entries: dict[str, list[dict[str, Any]]] = {dimension: [] for dimension in dimensions}
    classes: set[str] = set()
    expert_classes: set[str] = set()
    expert_entries: list[dict[str, Any]] = []
    unshaped_expert_entries: list[dict[str, Any]] = []
    honest_evidence_by_run: dict[str, dict[str, Any]] = {}
    accepted_legacy_without_honest_evidence: list[dict[str, str]] = []
    honest_entries: list[dict[str, Any]] = []
    honest_expert_entries: list[dict[str, Any]] = []
    honest_unshaped_expert_entries: list[dict[str, Any]] = []
    dimension_min = float(target.get("dimension_average_min") or 0)
    dimension_sample_min = int(target.get("dimension_sample_min") or 0)
    for entry in accepted:
        trial = trial_by_id.get(str(entry.get("trial_id") or ""), {})
        run_id = str(entry.get("run_id") or "")
        honest_status = honest_evidence_status(entry)
        honest_passed = honest_status.get("passed") is True
        honest_evidence_by_run[run_id] = honest_status
        if not honest_passed:
            accepted_legacy_without_honest_evidence.append(
                {
                    "trial_id": str(entry.get("trial_id") or ""),
                    "run_id": run_id,
                    "reason": str(honest_status.get("reason") or "honest evidence missing or incomplete"),
                }
            )
        else:
            honest_entries.append(entry)
        if trial.get("class"):
            classes.add(str(trial.get("class")))
            if trial.get("difficulty") == "expert":
                expert_classes.add(str(trial.get("class")))
        if trial.get("difficulty") == "expert":
            expert_entries.append(entry)
            if honest_passed:
                honest_expert_entries.append(entry)
            if str(entry.get("trial_id") or "").startswith("ceraxia-expert-unshaped-"):
                unshaped_expert_entries.append(entry)
                if honest_passed:
                    honest_unshaped_expert_entries.append(entry)
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
                if honest_passed:
                    honest_scores_by_dimension[dimension].append(float(value))
                if trial.get("difficulty") == "expert":
                    expert_scores_by_dimension[dimension].append(float(value))
                    if honest_passed:
                        honest_expert_scores_by_dimension[dimension].append(float(value))
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
    honest_dimension_averages = {
        dimension: average(values)
        for dimension, values in honest_scores_by_dimension.items()
    }
    honest_expert_dimension_averages = {
        dimension: average(values)
        for dimension, values in honest_expert_scores_by_dimension.items()
    }
    dimension_sample_counts = {
        dimension: len(values)
        for dimension, values in scores_by_dimension.items()
    }
    expert_dimension_sample_counts = {
        dimension: len(values)
        for dimension, values in expert_scores_by_dimension.items()
    }
    honest_dimension_sample_counts = {
        dimension: len(values)
        for dimension, values in honest_scores_by_dimension.items()
    }
    honest_expert_dimension_sample_counts = {
        dimension: len(values)
        for dimension, values in honest_expert_scores_by_dimension.items()
    }
    overall = average(list(dimension_averages.values())) if dimension_averages else 0.0
    expert_overall = average(list(expert_dimension_averages.values())) if expert_dimension_averages else 0.0
    honest_overall = average(list(honest_dimension_averages.values())) if honest_dimension_averages else 0.0
    honest_expert_overall = average(list(honest_expert_dimension_averages.values())) if honest_expert_dimension_averages else 0.0
    enough_trials = len(accepted) >= int(target.get("minimum_representative_trials") or 0)
    enough_dimensions = all(value >= dimension_min for value in dimension_averages.values())
    enough_dimension_samples = all(count >= dimension_sample_min for count in dimension_sample_counts.values())
    enough_overall = overall >= float(target.get("rolling_average_min") or 0)
    enough_honest_trials = len(honest_entries) >= int(target.get("minimum_representative_trials") or 0)
    enough_honest_dimensions = all(value >= dimension_min for value in honest_dimension_averages.values())
    enough_honest_dimension_samples = all(count >= dimension_sample_min for count in honest_dimension_sample_counts.values())
    enough_honest_overall = honest_overall >= float(target.get("rolling_average_min") or 0)
    fresh_window_size = int(target.get("fresh_window_size") or target.get("minimum_representative_trials") or 0)
    minimum_fresh_classes = int(target.get("minimum_fresh_classes") or 0)
    fresh_honest_entries = honest_entries[-fresh_window_size:] if fresh_window_size else list(honest_entries)
    fresh_honest_scores_by_dimension = applicable_scores_for(fresh_honest_entries)
    fresh_honest_dimension_averages = {
        dimension: average(values)
        for dimension, values in fresh_honest_scores_by_dimension.items()
    }
    fresh_honest_dimension_sample_counts = {
        dimension: len(values)
        for dimension, values in fresh_honest_scores_by_dimension.items()
    }
    fresh_honest_overall = average(list(fresh_honest_dimension_averages.values())) if fresh_honest_dimension_averages else 0.0
    fresh_honest_classes = {
        str(trial_by_id.get(str(entry.get("trial_id") or ""), {}).get("class") or "")
        for entry in fresh_honest_entries
        if trial_by_id.get(str(entry.get("trial_id") or ""), {}).get("class")
    }
    enough_fresh_honest_trials = len(fresh_honest_entries) >= int(target.get("minimum_representative_trials") or 0)
    enough_fresh_honest_classes = len(fresh_honest_classes) >= minimum_fresh_classes
    enough_fresh_honest_dimensions = all(value >= dimension_min for value in fresh_honest_dimension_averages.values())
    enough_fresh_honest_dimension_samples = all(count >= dimension_sample_min for count in fresh_honest_dimension_sample_counts.values())
    enough_fresh_honest_overall = fresh_honest_overall >= float(target.get("rolling_average_min") or 0)
    legacy_score_target_met = bool(enough_trials and enough_dimensions and enough_dimension_samples and enough_overall)
    all_time_target_met = bool(
        enough_honest_trials
        and enough_honest_dimensions
        and enough_honest_dimension_samples
        and enough_honest_overall
    )
    fresh_target_met = bool(
        all_time_target_met
        and enough_fresh_honest_trials
        and enough_fresh_honest_classes
        and enough_fresh_honest_dimensions
        and enough_fresh_honest_dimension_samples
        and enough_fresh_honest_overall
    )
    target_met = fresh_target_met
    expert_dimension_min = float(expert_target.get("dimension_average_min") or 0)
    expert_sample_min = int(expert_target.get("dimension_sample_min") or 0)
    expert_entry_min = float(expert_target.get("minimum_entry_score") or 0)
    expert_low_entry_scores: dict[str, list[dict[str, Any]]] = {dimension: [] for dimension in dimensions}
    honest_expert_low_entry_scores: dict[str, list[dict[str, Any]]] = {dimension: [] for dimension in dimensions}
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
        for entry in honest_expert_entries:
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
                    honest_expert_low_entry_scores[dimension].append(
                        {
                            "trial_id": entry.get("trial_id", ""),
                            "run_id": entry.get("run_id", ""),
                            "class": trial.get("class", ""),
                            "difficulty": trial.get("difficulty", ""),
                            "score": float(value),
                        }
                    )
    enough_expert_trials = len(expert_entries) >= int(expert_target.get("minimum_expert_trials") or 0)
    enough_honest_expert_trials = len(honest_expert_entries) >= int(expert_target.get("minimum_expert_trials") or 0)
    enough_expert_classes = len(expert_classes) >= int(expert_target.get("minimum_expert_classes") or 0)
    enough_unshaped_expert_trials = len(unshaped_expert_entries) >= int(expert_target.get("minimum_unshaped_expert_trials") or 0)
    enough_honest_unshaped_expert_trials = len(honest_unshaped_expert_entries) >= int(expert_target.get("minimum_unshaped_expert_trials") or 0)
    enough_expert_dimensions = all(value >= expert_dimension_min for value in expert_dimension_averages.values())
    enough_expert_samples = all(count >= expert_sample_min for count in expert_dimension_sample_counts.values())
    enough_expert_overall = expert_overall >= float(expert_target.get("rolling_average_min") or 0)
    enough_honest_expert_dimensions = all(value >= expert_dimension_min for value in honest_expert_dimension_averages.values())
    enough_honest_expert_samples = all(count >= expert_sample_min for count in honest_expert_dimension_sample_counts.values())
    enough_honest_expert_overall = honest_expert_overall >= float(expert_target.get("rolling_average_min") or 0)
    enough_expert_entry_scores = not any(expert_low_entry_scores.values())
    enough_honest_expert_entry_scores = not any(honest_expert_low_entry_scores.values())
    expert_window_size = int(expert_target.get("minimum_representative_trials") or expert_target.get("minimum_expert_trials") or 0)
    rolling_honest_expert_entries = honest_expert_entries[-expert_window_size:] if expert_window_size else list(honest_expert_entries)
    rolling_honest_expert_scores_by_dimension = applicable_scores_for(rolling_honest_expert_entries)
    rolling_honest_expert_dimension_averages = {
        dimension: average(values)
        for dimension, values in rolling_honest_expert_scores_by_dimension.items()
    }
    rolling_honest_expert_dimension_sample_counts = {
        dimension: len(values)
        for dimension, values in rolling_honest_expert_scores_by_dimension.items()
    }
    rolling_honest_expert_overall = average(list(rolling_honest_expert_dimension_averages.values())) if rolling_honest_expert_dimension_averages else 0.0
    rolling_honest_expert_classes = {
        str(trial_by_id.get(str(entry.get("trial_id") or ""), {}).get("class") or "")
        for entry in rolling_honest_expert_entries
        if trial_by_id.get(str(entry.get("trial_id") or ""), {}).get("class")
    }
    rolling_honest_unshaped_expert_entries = [
        entry
        for entry in rolling_honest_expert_entries
        if str(entry.get("trial_id") or "").startswith("ceraxia-expert-unshaped-")
    ]
    rolling_honest_expert_low_entry_scores = low_scores_for(rolling_honest_expert_entries, expert_entry_min)
    enough_rolling_honest_expert_trials = len(rolling_honest_expert_entries) >= int(expert_target.get("minimum_representative_trials") or 0)
    enough_rolling_honest_expert_classes = len(rolling_honest_expert_classes) >= int(expert_target.get("minimum_expert_classes") or 0)
    enough_rolling_honest_unshaped_expert_trials = len(rolling_honest_unshaped_expert_entries) >= int(expert_target.get("minimum_unshaped_expert_trials") or 0)
    enough_rolling_honest_expert_dimensions = all(value >= expert_dimension_min for value in rolling_honest_expert_dimension_averages.values())
    enough_rolling_honest_expert_samples = all(count >= expert_sample_min for count in rolling_honest_expert_dimension_sample_counts.values())
    enough_rolling_honest_expert_overall = rolling_honest_expert_overall >= float(expert_target.get("rolling_average_min") or 0)
    enough_rolling_honest_expert_entry_scores = not any(rolling_honest_expert_low_entry_scores.values())
    expert_target_met = bool(
        expert_target
        and enough_rolling_honest_expert_trials
        and enough_rolling_honest_expert_classes
        and enough_rolling_honest_unshaped_expert_trials
        and enough_rolling_honest_expert_dimensions
        and enough_rolling_honest_expert_samples
        and enough_rolling_honest_expert_overall
        and enough_rolling_honest_expert_entry_scores
    )
    return {
        "target_met": target_met,
        "next_stage_target_met": next_stage_metrics.get("target_met", False),
        "next_stage_metrics": next_stage_metrics,
        "legacy_score_target_met": legacy_score_target_met,
        "expert_target_met": expert_target_met,
        "overall_score": overall,
        "expert_overall_score": expert_overall,
        "honest_overall_score": honest_overall,
        "fresh_honest_overall_score": fresh_honest_overall,
        "honest_expert_overall_score": honest_expert_overall,
        "rolling_honest_expert_overall_score": rolling_honest_expert_overall,
        "dimension_averages": dimension_averages,
        "expert_dimension_averages": expert_dimension_averages,
        "honest_dimension_averages": honest_dimension_averages,
        "fresh_honest_dimension_averages": fresh_honest_dimension_averages,
        "honest_expert_dimension_averages": honest_expert_dimension_averages,
        "rolling_honest_expert_dimension_averages": rolling_honest_expert_dimension_averages,
        "dimension_sample_counts": dimension_sample_counts,
        "expert_dimension_sample_counts": expert_dimension_sample_counts,
        "honest_dimension_sample_counts": honest_dimension_sample_counts,
        "fresh_honest_dimension_sample_counts": fresh_honest_dimension_sample_counts,
        "honest_expert_dimension_sample_counts": honest_expert_dimension_sample_counts,
        "rolling_honest_expert_dimension_sample_counts": rolling_honest_expert_dimension_sample_counts,
        "accepted_trial_count": len(accepted),
        "accepted_honest_evidence_count": sum(
            1 for status in honest_evidence_by_run.values() if status.get("passed")
        ),
        "fresh_target_met": fresh_target_met,
        "all_time_honest_target_met": all_time_target_met,
        "fresh_honest_trial_count": len(fresh_honest_entries),
        "fresh_honest_window_size": fresh_window_size,
        "fresh_honest_class_count": len(fresh_honest_classes),
        "fresh_honest_classes": sorted(fresh_honest_classes),
        "accepted_legacy_without_honest_evidence": accepted_legacy_without_honest_evidence,
        "draft_trial_count": len(entries) - len(accepted),
        "covered_classes": sorted(classes),
        "covered_expert_classes": sorted(expert_classes),
        "target": target,
        "expert_target": expert_target,
        "gaps": {
            "needs_more_accepted_trials": not enough_trials,
            "honest_trial_count": len(honest_entries),
            "needs_more_honest_evidence": not enough_honest_trials,
            "needs_more_fresh_honest_evidence": not enough_fresh_honest_trials,
            "needs_more_fresh_honest_classes": not enough_fresh_honest_classes,
            "needs_higher_overall": not enough_honest_overall,
            "fresh_needs_higher_overall": not enough_fresh_honest_overall,
            "legacy_needs_higher_overall": not enough_overall,
            "needs_more_dimension_evidence": [
                dimension
                for dimension, count in honest_dimension_sample_counts.items()
                if count < dimension_sample_min
            ],
            "fresh_needs_more_dimension_evidence": [
                dimension
                for dimension, count in fresh_honest_dimension_sample_counts.items()
                if count < dimension_sample_min
            ],
            "needs_higher_dimension_scores": [
                dimension
                for dimension, value in honest_dimension_averages.items()
                if value < dimension_min
            ],
            "fresh_needs_higher_dimension_scores": [
                dimension
                for dimension, value in fresh_honest_dimension_averages.items()
                if value < dimension_min
            ],
            "legacy_needs_more_dimension_evidence": [
                dimension
                for dimension, count in dimension_sample_counts.items()
                if count < dimension_sample_min
            ],
            "legacy_needs_higher_dimension_scores": [
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
            "needs_higher_overall": not enough_honest_expert_overall,
            "legacy_needs_higher_overall": not enough_expert_overall,
            "rolling_needs_higher_overall": not enough_rolling_honest_expert_overall,
            "needs_more_dimension_evidence": [
                dimension
                for dimension, count in rolling_honest_expert_dimension_sample_counts.items()
                if count < expert_sample_min
            ],
            "needs_higher_dimension_scores": [
                dimension
                for dimension, value in rolling_honest_expert_dimension_averages.items()
                if value < expert_dimension_min
            ],
            "legacy_needs_more_dimension_evidence": [
                dimension
                for dimension, count in expert_dimension_sample_counts.items()
                if count < expert_sample_min
            ],
            "legacy_needs_higher_dimension_scores": [
                dimension
                for dimension, value in expert_dimension_averages.items()
                if value < expert_dimension_min
            ],
            "needs_higher_entry_scores": {
                dimension: items
                for dimension, items in rolling_honest_expert_low_entry_scores.items()
                if items
            },
            "all_time_needs_higher_entry_scores": {
                dimension: items
                for dimension, items in honest_expert_low_entry_scores.items()
                if items
            },
            "legacy_needs_higher_entry_scores": {
                dimension: items
                for dimension, items in expert_low_entry_scores.items()
                if items
            },
            "expert_trial_count": len(expert_entries),
            "honest_expert_trial_count": len(honest_expert_entries),
            "rolling_honest_expert_trial_count": len(rolling_honest_expert_entries),
            "rolling_honest_expert_window_size": expert_window_size,
            "expert_class_count": len(expert_classes),
            "rolling_honest_expert_class_count": len(rolling_honest_expert_classes),
            "unshaped_expert_trial_count": len(unshaped_expert_entries),
            "honest_unshaped_expert_trial_count": len(honest_unshaped_expert_entries),
            "rolling_honest_unshaped_expert_trial_count": len(rolling_honest_unshaped_expert_entries),
            "needs_more_unshaped_expert_trials": not enough_unshaped_expert_trials,
            "needs_more_honest_expert_evidence": not enough_honest_expert_trials,
            "needs_more_honest_unshaped_expert_evidence": not enough_honest_unshaped_expert_trials,
            "needs_more_rolling_honest_expert_evidence": not enough_rolling_honest_expert_trials,
            "needs_more_rolling_honest_expert_classes": not enough_rolling_honest_expert_classes,
            "needs_more_rolling_honest_unshaped_expert_evidence": not enough_rolling_honest_unshaped_expert_trials,
            "expert_entries_without_honest_evidence": [
                item
                for item in accepted_legacy_without_honest_evidence
                if trial_by_id.get(item["trial_id"], {}).get("difficulty") == "expert"
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Report human-reviewed Ceraxia field trial progress.")
    parser.add_argument("--require-target", action="store_true", help="Exit non-zero unless the real 7/10 target is met.")
    parser.add_argument("--require-expert-target", action="store_true", help="Exit non-zero unless the real 10/10 expert target is met.")
    parser.add_argument("--require-next-stage-target", action="store_true", help="Exit non-zero unless the live next-stage target is met.")
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
    if args.require_next_stage_target and not report["next_stage_target_met"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
