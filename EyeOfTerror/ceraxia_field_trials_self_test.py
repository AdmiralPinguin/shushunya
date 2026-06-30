#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ceraxia_field_trial_runner import apply_trial_checks_to_outcome, classify_trial_outcome


ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "InnerCircle" / "Ceraxia" / "field_trials.json"
PROTOCOL = ROOT / "InnerCircle" / "Ceraxia" / "EVALUATION.md"
LEDGER = ROOT / "InnerCircle" / "Ceraxia" / "field_trial_ledger.json"
REPORTER = ROOT / "ceraxia_field_trial_report.py"
RUNNER = ROOT / "ceraxia_field_trial_runner.py"
EXPERT_SUITE = ROOT / "ceraxia_expert_suite.py"
REVIEWER = ROOT / "ceraxia_field_trial_review.py"
EXPERT_REVIEWER = ROOT / "ceraxia_expert_review.py"
ACCEPTER = ROOT / "ceraxia_field_trial_accept.py"


def main() -> int:
    data = json.loads(SPEC.read_text(encoding="utf-8"))
    protocol = PROTOCOL.read_text(encoding="utf-8")
    dimensions = data.get("dimensions", [])
    trials = data.get("trials", [])
    target = data.get("target", {})
    expert_target = data.get("expert_target", {})
    if target.get("minimum_representative_trials", 0) < 12:
        raise AssertionError(f"Ceraxia field trials target is too small: {target}")
    if target.get("dimension_sample_min", 0) < 2:
        raise AssertionError(f"Ceraxia dimension sample target is too weak: {target}")
    if expert_target.get("level") != 10 or expert_target.get("rolling_average_min", 0) < 9.5:
        raise AssertionError(f"Ceraxia expert target must represent a real 10/10 gate: {expert_target}")
    if expert_target.get("minimum_expert_trials", 0) < 6:
        raise AssertionError(f"Ceraxia expert target needs enough expert trials: {expert_target}")
    if expert_target.get("minimum_unshaped_expert_trials", 0) < 4:
        raise AssertionError(f"Ceraxia expert target must require unshaped expert evidence: {expert_target}")
    if len(trials) < target.get("minimum_representative_trials", 0):
        raise AssertionError(f"Ceraxia field trial suite is undersized: {len(trials)} {target}")
    if len(set(dimensions)) != len(dimensions) or len(dimensions) < 8:
        raise AssertionError(f"Ceraxia dimensions are missing or duplicated: {dimensions}")
    seen: set[str] = set()
    classes: set[str] = set()
    expert_classes: set[str] = set()
    for trial in trials:
        trial_id = str(trial.get("id") or "")
        if not trial_id or trial_id in seen:
            raise AssertionError(f"bad or duplicate Ceraxia trial id: {trial}")
        seen.add(trial_id)
        classes.add(str(trial.get("class") or ""))
        if trial.get("difficulty") == "expert":
            expert_classes.add(str(trial.get("class") or ""))
        if not trial.get("task") or not trial.get("required_evidence") or not trial.get("failure_modes_to_watch"):
            raise AssertionError(f"Ceraxia field trial lacks task/evidence/failure modes: {trial}")
        applicable = trial.get("applicable_dimensions")
        if not isinstance(applicable, list) or not applicable or not set(applicable).issubset(set(dimensions)):
            raise AssertionError(f"Ceraxia trial must define applicable dimensions from the rubric: {trial}")
    if len(classes) < 8:
        raise AssertionError(f"Ceraxia field trials are not diverse enough: {classes}")
    if len(expert_classes) < expert_target.get("minimum_expert_classes", 0):
        raise AssertionError(f"Ceraxia expert trials are not diverse enough: {expert_classes}")
    ledger_scores = data.get("ledger_template", {}).get("scores", {})
    if set(ledger_scores) != set(dimensions):
        raise AssertionError(f"Ceraxia ledger scores drift from dimensions: {ledger_scores} {dimensions}")
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    if not isinstance(ledger.get("entries"), list):
        raise AssertionError(f"Ceraxia field trial ledger must expose entries list: {ledger}")
    report = subprocess.run(
        [sys.executable, str(REPORTER)],
        cwd=str(ROOT.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    if report.returncode != 0:
        raise AssertionError(f"Ceraxia field trial reporter failed: {report.stdout} {report.stderr}")
    report_payload = json.loads(report.stdout)
    if report_payload.get("target_met") is True and not ledger.get("entries"):
        raise AssertionError(f"empty Ceraxia ledger must not prove target completion: {report_payload}")
    if report_payload.get("expert_target_met") is True:
        expert_gaps = report_payload.get("expert_gaps", {})
        if (
            expert_gaps.get("expert_trial_count", 0) < expert_target.get("minimum_expert_trials", 0)
            or expert_gaps.get("expert_class_count", 0) < expert_target.get("minimum_expert_classes", 0)
            or expert_gaps.get("unshaped_expert_trial_count", 0) < expert_target.get("minimum_unshaped_expert_trials", 0)
            or report_payload.get("expert_overall_score", 0) < expert_target.get("rolling_average_min", 0)
        ):
            raise AssertionError(f"Ceraxia expert target cannot pass without required evidence: {report_payload}")
    if report_payload.get("accepted_trial_count", 0) and report_payload.get("target_met") is not True:
        low_entries = report_payload.get("gaps", {}).get("low_score_entries", {})
        if not isinstance(low_entries, dict):
            raise AssertionError(f"Ceraxia report must expose low score entries after accepted reviews: {report_payload}")
    sample_counts = report_payload.get("dimension_sample_counts", {})
    if set(sample_counts) != set(dimensions):
        raise AssertionError(f"Ceraxia report must expose sample counts for every dimension: {report_payload}")
    expert_sample_counts = report_payload.get("expert_dimension_sample_counts", {})
    if set(expert_sample_counts) != set(dimensions):
        raise AssertionError(f"Ceraxia report must expose expert sample counts for every dimension: {report_payload}")
    expert_dimension_averages = report_payload.get("expert_dimension_averages", {})
    if set(expert_dimension_averages) != set(dimensions):
        raise AssertionError(f"Ceraxia report must expose expert averages for every dimension: {report_payload}")
    strict_report = subprocess.run(
        [sys.executable, str(REPORTER), "--require-target"],
        cwd=str(ROOT.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    if not ledger.get("entries") and strict_report.returncode == 0:
        raise AssertionError("strict Ceraxia field trial report must fail while no accepted trials exist")
    expert_strict_report = subprocess.run(
        [sys.executable, str(REPORTER), "--require-expert-target"],
        cwd=str(ROOT.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    if report_payload.get("expert_target_met") is True and expert_strict_report.returncode != 0:
        raise AssertionError(f"strict Ceraxia expert report rejected proven expert target: {expert_strict_report.stdout}")
    if report_payload.get("expert_target_met") is not True and expert_strict_report.returncode == 0:
        raise AssertionError("strict Ceraxia expert report must fail until expert evidence proves 10/10")
    runner_list = subprocess.run(
        [sys.executable, str(RUNNER), "--list"],
        cwd=str(ROOT.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    if runner_list.returncode != 0:
        raise AssertionError(f"Ceraxia field trial runner list failed: {runner_list.stdout} {runner_list.stderr}")
    runner_payload = json.loads(runner_list.stdout)
    runner_trials = set(runner_payload.get("trials", []))
    spec_trial_ids = {str(trial.get("id") or "") for trial in trials}
    if spec_trial_ids != runner_trials:
        raise AssertionError(f"Ceraxia runner/spec trial drift: missing={sorted(spec_trial_ids - runner_trials)} extra={sorted(runner_trials - spec_trial_ids)}")
    required_runner_trials = {
        "ceraxia-field-ambiguous-task",
        "ceraxia-field-bugfix-unnamed-source",
        "ceraxia-field-cross-language-config",
        "ceraxia-field-data-migration",
        "ceraxia-expert-concurrency-cache",
        "ceraxia-expert-failed-review-revision",
        "ceraxia-expert-flaky-test-root-cause",
        "ceraxia-expert-legacy-migration",
        "ceraxia-expert-public-api-deprecation",
        "ceraxia-expert-repo-grade-workflow",
        "ceraxia-expert-security-boundary",
        "ceraxia-expert-unshaped-config-runtime",
        "ceraxia-expert-unshaped-design-choice",
        "ceraxia-expert-unshaped-runtime-alias",
        "ceraxia-field-integration-contract",
        "ceraxia-field-large-file-restraint",
        "ceraxia-field-multifile-feature",
        "ceraxia-field-negative-test",
        "ceraxia-field-public-api-compat",
        "ceraxia-field-refactor-preserve-behavior",
        "ceraxia-field-repair-after-bad-first-patch",
        "ceraxia-field-safety-dirty-worktree",
    }
    if not required_runner_trials.issubset(runner_trials):
        raise AssertionError(f"Ceraxia field trial runner lacks first reproducible trial: {runner_payload}")
    expert_suite = subprocess.run(
        [sys.executable, str(EXPERT_SUITE), "--require-all"],
        cwd=str(ROOT.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    if expert_suite.returncode != 0:
        raise AssertionError(f"Ceraxia expert suite runner failed: {expert_suite.stdout} {expert_suite.stderr}")
    expert_suite_payload = json.loads(expert_suite.stdout)
    if (
        expert_suite_payload.get("expert_trial_count", 0) < expert_target.get("minimum_expert_trials", 0)
        or expert_suite_payload.get("unshaped_inferred_count", 0) < expert_target.get("minimum_unshaped_expert_trials", 0)
        or expert_suite_payload.get("all_passed") is not True
    ):
        raise AssertionError(f"Ceraxia expert suite runner did not prove current arena health: {expert_suite_payload}")
    expert_review = subprocess.run(
        [sys.executable, str(EXPERT_REVIEWER), "--dry-run"],
        cwd=str(ROOT.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    if expert_review.returncode != 0:
        raise AssertionError(f"Ceraxia strict expert reviewer failed: {expert_review.stdout} {expert_review.stderr}")
    expert_review_payload = json.loads(expert_review.stdout)
    if "review_count" not in expert_review_payload or "report" not in expert_review_payload:
        raise AssertionError(f"Ceraxia strict expert reviewer returned malformed payload: {expert_review_payload}")
    unshaped_draft_count = sum(
        1
        for entry in ledger.get("entries", [])
        if isinstance(entry, dict)
        and entry.get("accepted_for_rolling_score") is not True
        and str(entry.get("trial_id") or "").startswith("ceraxia-expert-unshaped-")
    )
    if unshaped_draft_count:
        expert_review_unshaped = subprocess.run(
            [sys.executable, str(EXPERT_REVIEWER), "--unshaped-only", "--dry-run"],
            cwd=str(ROOT.parent),
            text=True,
            capture_output=True,
            check=False,
        )
        if expert_review_unshaped.returncode != 0:
            raise AssertionError(f"Ceraxia unshaped expert reviewer failed: {expert_review_unshaped.stdout} {expert_review_unshaped.stderr}")
        unshaped_payload = json.loads(expert_review_unshaped.stdout)
        if unshaped_payload.get("review_count", 0) < expert_target.get("minimum_unshaped_expert_trials", 0):
            raise AssertionError(f"Ceraxia unshaped expert reviewer lacks enough drafts: {unshaped_payload}")
        if unshaped_payload.get("report", {}).get("expert_target_met") is not True:
            raise AssertionError(f"Ceraxia unshaped expert reviewer should prove the strict expert target before acceptance: {unshaped_payload}")
    review_all = subprocess.run(
        [sys.executable, str(REVIEWER), "--all"],
        cwd=str(ROOT.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    if review_all.returncode != 0:
        raise AssertionError(f"Ceraxia field trial review helper failed: {review_all.stdout} {review_all.stderr}")
    review_payload = json.loads(review_all.stdout)
    if ledger.get("entries") and len(review_payload) != len(ledger.get("entries", [])):
        raise AssertionError(f"Ceraxia review helper did not cover ledger entries: {len(review_payload)} {len(ledger.get('entries', []))}")
    if review_payload:
        sample = review_payload[0]
        if set(sample.get("score_sheet", {})) != set(dimensions):
            raise AssertionError(f"Ceraxia review helper score sheet drifts from dimensions: {sample}")
        if not sample.get("review_questions") or not sample.get("acceptance_requirements"):
            raise AssertionError(f"Ceraxia review helper must expose questions and requirements: {sample}")
    ambiguous_review = subprocess.run(
        [sys.executable, str(REVIEWER)],
        cwd=str(ROOT.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    if len(ledger.get("entries", [])) > 1 and ambiguous_review.returncode == 0:
        raise AssertionError("Ceraxia review helper must require --all or a narrow selector for multiple entries")
    if ledger.get("entries"):
        accepted_before_dry_run = sum(1 for entry in ledger.get("entries", []) if entry.get("accepted_for_rolling_score") is True)
        first_entry = ledger["entries"][0]
        bad_review_path = ROOT / "tmp_bad_ceraxia_review.json"
        good_review_path = ROOT / "tmp_good_ceraxia_review.json"
        try:
            bad_review_path.write_text(
                json.dumps(
                    {
                        "trial_id": first_entry.get("trial_id"),
                        "run_id": first_entry.get("run_id"),
                        "reviewer": "",
                        "scores": {},
                        "human_review_notes": "too short",
                        "accepted_for_rolling_score": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            bad_accept = subprocess.run(
                [sys.executable, str(ACCEPTER), "--review-file", str(bad_review_path), "--dry-run"],
                cwd=str(ROOT.parent),
                text=True,
                capture_output=True,
                check=False,
            )
            if bad_accept.returncode == 0:
                raise AssertionError(f"Ceraxia accept helper accepted incomplete review: {bad_accept.stdout}")
            good_review_path.write_text(
                json.dumps(
                    {
                        "trial_id": first_entry.get("trial_id"),
                        "run_id": first_entry.get("run_id"),
                        "reviewer": "self-test reviewer",
                        "scores": {dimension: 7 for dimension in dimensions},
                        "human_review_notes": "Self-test dry run confirms complete review payload validation without mutating the ledger.",
                        "generalizable_failures": [],
                        "follow_up_changes": [],
                        "accepted_for_rolling_score": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            good_accept = subprocess.run(
                [sys.executable, str(ACCEPTER), "--review-file", str(good_review_path), "--dry-run"],
                cwd=str(ROOT.parent),
                text=True,
                capture_output=True,
                check=False,
            )
            if good_accept.returncode != 0:
                raise AssertionError(f"Ceraxia accept helper rejected complete dry-run review: {good_accept.stdout} {good_accept.stderr}")
            good_payload = json.loads(good_accept.stdout)
            expected_accepted = accepted_before_dry_run if first_entry.get("accepted_for_rolling_score") is True else accepted_before_dry_run + 1
            if good_payload.get("dry_run") is not True or good_payload.get("report", {}).get("accepted_trial_count") != expected_accepted:
                raise AssertionError(f"Ceraxia accept helper dry-run report is wrong: {good_payload}")
            ledger_after_dry_run = json.loads(LEDGER.read_text(encoding="utf-8"))
            if ledger_after_dry_run != ledger:
                raise AssertionError("Ceraxia accept helper dry-run mutated the ledger")
        finally:
            bad_review_path.unlink(missing_ok=True)
            good_review_path.unlink(missing_ok=True)
    blocked_outcome = classify_trial_outcome(
        "ceraxia-field-ambiguous-task",
        {"ok": False, "phase": "revision_cycle_limit"},
        {"status": "blocked", "blockers": ["requirements are ambiguous"]},
    )
    if blocked_outcome.get("status") != "expected_blocked" or blocked_outcome.get("expected") is not True:
        raise AssertionError(f"expected blocker trial outcome was not classified correctly: {blocked_outcome}")
    failed_outcome = classify_trial_outcome(
        "ceraxia-field-bugfix-unnamed-source",
        {"ok": False, "phase": "revision_cycle_limit"},
        {"status": "blocked", "blockers": ["unexpected blocker"]},
    )
    if failed_outcome.get("status") != "failed" or failed_outcome.get("expected") is not False:
        raise AssertionError(f"unexpected blocker trial outcome was not classified as failed: {failed_outcome}")
    checked_outcome = apply_trial_checks_to_outcome(
        {"status": "passed", "expected": True, "reason": "base outcome passed"},
        {"large_file_restraint": {"passed": False}},
    )
    if checked_outcome.get("status") != "failed" or checked_outcome.get("expected") is not False:
        raise AssertionError(f"failed trial-specific check did not fail the trial: {checked_outcome}")
    repair_checked_outcome = apply_trial_checks_to_outcome(
        {"status": "passed", "expected": True, "reason": "base outcome passed"},
        {"repair_after_bad_first_patch": {"passed": False}},
    )
    if repair_checked_outcome.get("status") != "failed" or repair_checked_outcome.get("expected") is not False:
        raise AssertionError(f"failed repair-specific check did not fail the trial: {repair_checked_outcome}")
    integration_checked_outcome = apply_trial_checks_to_outcome(
        {"status": "passed", "expected": True, "reason": "base outcome passed"},
        {"integration_contract": {"passed": False}},
    )
    if integration_checked_outcome.get("status") != "failed" or integration_checked_outcome.get("expected") is not False:
        raise AssertionError(f"failed integration-specific check did not fail the trial: {integration_checked_outcome}")
    public_api_checked_outcome = apply_trial_checks_to_outcome(
        {"status": "passed", "expected": True, "reason": "base outcome passed"},
        {"public_api_compat": {"passed": False}},
    )
    if public_api_checked_outcome.get("status") != "failed" or public_api_checked_outcome.get("expected") is not False:
        raise AssertionError(f"failed public-api-specific check did not fail the trial: {public_api_checked_outcome}")
    required_phrases = [
        "A scripted self-test proves only that a known scenario still works.",
        "The real 7/10 target is met only when",
        "human-readable review notes",
    ]
    for phrase in required_phrases:
        if phrase not in protocol:
            raise AssertionError(f"Ceraxia evaluation protocol lost core warning: {phrase}")
    print("[ok] Ceraxia field trials specification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
