#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from ceraxia_evidence_contract import REQUIRED_HONEST_CHECKS, NEXT_STAGE_PACKAGE_KIND, evidence_package_status, next_stage_evidence_status
from ceraxia_field_trial_runner import (
    DEFAULT_FIELD_TRIAL_RUN_ROOT,
    apply_trial_checks_to_outcome,
    classify_trial_outcome,
    honest_evidence_summary,
    resolve_run_storage,
)
from ceraxia_field_trial_report import build_report
from ceraxia_field_trial_auto_review import principal_evidence_signals, repair_evidence_signals, senior_evidence_signals


WARMASTER_ROOT = Path(__file__).resolve().parent
EYE_ROOT = WARMASTER_ROOT.parent
SPEC = EYE_ROOT / "Mechanicum" / "Ceraxia" / "field_trials.json"
PROTOCOL = EYE_ROOT / "Mechanicum" / "Ceraxia" / "EVALUATION.md"
LEDGER = EYE_ROOT / "Mechanicum" / "Ceraxia" / "field_trial_ledger.json"
REPORTER = WARMASTER_ROOT / "ceraxia_field_trial_report.py"
RUNNER = WARMASTER_ROOT / "ceraxia_field_trial_runner.py"
EXPERT_SUITE = WARMASTER_ROOT / "ceraxia_expert_suite.py"
REVIEWER = WARMASTER_ROOT / "ceraxia_field_trial_review.py"
EXPERT_REVIEWER = WARMASTER_ROOT / "ceraxia_expert_review.py"
ACCEPTER = WARMASTER_ROOT / "ceraxia_field_trial_accept.py"
AUTO_REVIEWER = WARMASTER_ROOT / "ceraxia_field_trial_auto_review.py"
NEXT_STAGE_BUILDER = WARMASTER_ROOT / "ceraxia_next_stage_package.py"
LIVE_TASK_REGISTER = WARMASTER_ROOT / "ceraxia_live_task_register.py"


def main() -> int:
    data = json.loads(SPEC.read_text(encoding="utf-8"))
    protocol = PROTOCOL.read_text(encoding="utf-8")
    dimensions = data.get("dimensions", [])
    trials = data.get("trials", [])
    live_tasks = data.get("live_tasks", [])
    target = data.get("target", {})
    next_stage_target = data.get("next_stage_target", {})
    expert_target = data.get("expert_target", {})
    if target.get("minimum_representative_trials", 0) < 12:
        raise AssertionError(f"Ceraxia field trials target is too small: {target}")
    if target.get("fresh_window_size", 0) < target.get("minimum_representative_trials", 0):
        raise AssertionError(f"Ceraxia fresh target window is too small: {target}")
    if target.get("minimum_fresh_classes", 0) < 8:
        raise AssertionError(f"Ceraxia fresh target must require at least 8 classes: {target}")
    if target.get("dimension_sample_min", 0) < 2:
        raise AssertionError(f"Ceraxia dimension sample target is too weak: {target}")
    if next_stage_target.get("minimum_live_tasks", 0) < 20:
        raise AssertionError(f"Ceraxia next-stage target must require at least 20 live tasks: {next_stage_target}")
    if next_stage_target.get("minimum_task_classes", 0) < 10:
        raise AssertionError(f"Ceraxia next-stage target must require at least 10 task classes: {next_stage_target}")
    if float(next_stage_target.get("minimum_success_rate") or 0) < 0.7:
        raise AssertionError(f"Ceraxia next-stage target must require at least 70% success: {next_stage_target}")
    if int(next_stage_target.get("maximum_false_successes", 1)) != 0:
        raise AssertionError(f"Ceraxia next-stage target must reject false success: {next_stage_target}")
    if next_stage_target.get("minimum_multifile_nonfixture_tasks", 0) < 5:
        raise AssertionError(f"Ceraxia next-stage target must require multi-file nonfixture tasks: {next_stage_target}")
    if next_stage_target.get("failed_or_blocked_require_postmortem") is not True:
        raise AssertionError(f"Ceraxia next-stage target must require postmortems: {next_stage_target}")
    if next_stage_target.get("track_repaired_successes_separately") is not True or next_stage_target.get("track_honest_blocks_separately") is not True:
        raise AssertionError(f"Ceraxia next-stage target must classify repaired successes and honest blocks: {next_stage_target}")
    if next_stage_target.get("require_next_stage_evidence_package") is not True:
        raise AssertionError(f"Ceraxia next-stage target must require evidence packages: {next_stage_target}")
    if expert_target.get("level") != 10 or expert_target.get("rolling_average_min", 0) < 9.5:
        raise AssertionError(f"Ceraxia expert target must represent a real 10/10 gate: {expert_target}")
    if expert_target.get("minimum_expert_trials", 0) < 6:
        raise AssertionError(f"Ceraxia expert target needs enough expert trials: {expert_target}")
    if expert_target.get("minimum_unshaped_expert_trials", 0) < 4:
        raise AssertionError(f"Ceraxia expert target must require unshaped expert evidence: {expert_target}")
    storage_root, storage_keep, storage_ledger = resolve_run_storage(None, keep=False, ledger_draft=True)
    if storage_root != DEFAULT_FIELD_TRIAL_RUN_ROOT or storage_keep is not True or storage_ledger is not True:
        raise AssertionError("ledger draft field trials must default to persistent evidence storage")
    temp_root, temp_keep, temp_ledger = resolve_run_storage(None, keep=False, ledger_draft=False)
    if temp_root is not None or temp_keep is not False or temp_ledger is not False:
        raise AssertionError("plain field trial runs should remain temporary by default")
    if len(trials) < target.get("minimum_representative_trials", 0):
        raise AssertionError(f"Ceraxia field trial suite is undersized: {len(trials)} {target}")
    if len(live_tasks) < next_stage_target.get("minimum_live_tasks", 0):
        raise AssertionError(f"Ceraxia live task catalog is undersized: {len(live_tasks)} {next_stage_target}")
    live_task_ids = {str(item.get("id") or "") for item in live_tasks if isinstance(item, dict)}
    if len(live_task_ids) != len(live_tasks):
        raise AssertionError(f"Ceraxia live task catalog has duplicate or invalid ids: {live_tasks}")
    live_classes = {str(item.get("class") or "") for item in live_tasks if isinstance(item, dict)}
    if len(live_classes) < next_stage_target.get("minimum_task_classes", 0):
        raise AssertionError(f"Ceraxia live task catalog does not cover enough classes: {live_classes}")
    live_multifile = [
        item for item in live_tasks
        if isinstance(item, dict) and item.get("multi_file_expected") is True
    ]
    if len(live_multifile) < next_stage_target.get("minimum_multifile_nonfixture_tasks", 0):
        raise AssertionError(f"Ceraxia live task catalog lacks multi-file tasks: {live_multifile}")
    for live_task in live_tasks:
        if not isinstance(live_task, dict):
            raise AssertionError(f"Ceraxia live task must be an object: {live_task}")
        if not live_task.get("task") or not live_task.get("required_evidence"):
            raise AssertionError(f"Ceraxia live task lacks task/evidence requirements: {live_task}")
        if not isinstance(live_task.get("minimum_changed_files"), int):
            raise AssertionError(f"Ceraxia live task must declare minimum_changed_files: {live_task}")
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
        cwd=str(EYE_ROOT.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    if report.returncode != 0:
        raise AssertionError(f"Ceraxia field trial reporter failed: {report.stdout} {report.stderr}")
    report_payload = json.loads(report.stdout)
    if "accepted_honest_evidence_count" not in report_payload:
        raise AssertionError(f"Ceraxia report must expose honest accepted evidence count: {report_payload}")
    if "next_stage_target_met" not in report_payload or "next_stage_metrics" not in report_payload:
        raise AssertionError(f"Ceraxia report must expose next-stage target metrics: {report_payload}")
    next_stage_metrics = report_payload.get("next_stage_metrics", {})
    for key in {
        "live_task_count",
        "task_class_count",
        "fully_successful_count",
        "repaired_success_count",
        "honest_blocked_count",
        "broken_count",
        "reviewer_rejected_count",
        "false_success_count",
        "success_rate",
        "average_attempt_count",
        "multi_file_nonfixture_count",
        "postmortem_gap_count",
        "evidence_gap_count",
        "gaps",
    }:
        if key not in next_stage_metrics:
            raise AssertionError(f"Ceraxia next-stage metrics missing {key}: {next_stage_metrics}")
    if next_stage_metrics.get("live_task_count", 0) == 0 and report_payload.get("next_stage_target_met") is True:
        raise AssertionError(f"Ceraxia next-stage target cannot pass from legacy reviews: {report_payload}")
    if "legacy_score_target_met" not in report_payload:
        raise AssertionError(f"Ceraxia report must expose legacy score target separately from honest target: {report_payload}")
    for key in {
        "honest_overall_score",
        "fresh_honest_overall_score",
        "honest_dimension_averages",
        "fresh_honest_dimension_averages",
        "honest_dimension_sample_counts",
        "fresh_honest_dimension_sample_counts",
        "honest_expert_overall_score",
        "honest_expert_dimension_averages",
        "honest_expert_dimension_sample_counts",
        "rolling_honest_expert_overall_score",
        "rolling_honest_expert_dimension_averages",
        "rolling_honest_expert_dimension_sample_counts",
    }:
        if key not in report_payload:
            raise AssertionError(f"Ceraxia report must expose honest-only metric {key}: {report_payload}")
    for key in {
        "fresh_target_met",
        "all_time_honest_target_met",
        "fresh_honest_trial_count",
        "fresh_honest_window_size",
        "fresh_honest_class_count",
        "fresh_honest_classes",
    }:
        if key not in report_payload:
            raise AssertionError(f"Ceraxia report must expose fresh target metric {key}: {report_payload}")
    if report_payload.get("target_met") is not report_payload.get("fresh_target_met"):
        raise AssertionError(f"Ceraxia main target must be the fresh honest target, not stale all-time evidence: {report_payload}")
    if not isinstance(report_payload.get("accepted_legacy_without_honest_evidence"), list):
        raise AssertionError(f"Ceraxia report must expose accepted legacy honest-evidence gaps: {report_payload}")
    if report_payload.get("target_met") is True and report_payload.get("accepted_honest_evidence_count", 0) < target.get("minimum_representative_trials", 0):
        raise AssertionError(f"Ceraxia target cannot pass without enough honest evidence: {report_payload}")
    if report_payload.get("target_met") is True:
        if report_payload.get("fresh_honest_trial_count", 0) < target.get("minimum_representative_trials", 0):
            raise AssertionError(f"Ceraxia target cannot pass without enough fresh honest evidence: {report_payload}")
        if report_payload.get("fresh_honest_class_count", 0) < target.get("minimum_fresh_classes", 0):
            raise AssertionError(f"Ceraxia target cannot pass without enough fresh honest classes: {report_payload}")
        if report_payload.get("fresh_honest_window_size", 0) < target.get("fresh_window_size", 0):
            raise AssertionError(f"Ceraxia report fresh window drifted from target: {report_payload}")
        if report_payload.get("fresh_honest_overall_score", 0) < target.get("rolling_average_min", 0):
            raise AssertionError(f"Ceraxia target cannot pass on stale all-time score: {report_payload}")
        if report_payload.get("honest_overall_score", 0) < target.get("rolling_average_min", 0):
            raise AssertionError(f"Ceraxia target cannot pass on legacy-only overall score: {report_payload}")
        honest_counts = report_payload.get("honest_dimension_sample_counts", {})
        honest_averages = report_payload.get("honest_dimension_averages", {})
        fresh_counts = report_payload.get("fresh_honest_dimension_sample_counts", {})
        fresh_averages = report_payload.get("fresh_honest_dimension_averages", {})
        if any(honest_counts.get(dimension, 0) < target.get("dimension_sample_min", 0) for dimension in dimensions):
            raise AssertionError(f"Ceraxia target cannot pass on legacy-only dimension samples: {report_payload}")
        if any(honest_averages.get(dimension, 0) < target.get("dimension_average_min", 0) for dimension in dimensions):
            raise AssertionError(f"Ceraxia target cannot pass on legacy-only dimension averages: {report_payload}")
        if any(fresh_counts.get(dimension, 0) < target.get("dimension_sample_min", 0) for dimension in dimensions):
            raise AssertionError(f"Ceraxia target cannot pass on stale dimension samples: {report_payload}")
        if any(fresh_averages.get(dimension, 0) < target.get("dimension_average_min", 0) for dimension in dimensions):
            raise AssertionError(f"Ceraxia target cannot pass on stale dimension averages: {report_payload}")
    if report_payload.get("target_met") is True and not ledger.get("entries"):
        raise AssertionError(f"empty Ceraxia ledger must not prove target completion: {report_payload}")
    synthetic_entries = []
    synthetic_trials = data["trials"][:20]
    for index, trial in enumerate(synthetic_trials):
        status = "repaired_success" if index in {2, 7} else "fully_successful"
        attempt_count = 2 if index in {2, 7} else 1
        changed_files = [f"src/task_{index}.py", f"tests/test_task_{index}.py"] if index < 5 else [f"src/task_{index}.py"]
        synthetic_entries.append(
            {
                "trial_id": trial["id"],
                "run_id": f"next-stage-{index}",
                "accepted_for_rolling_score": False,
                "human_review_notes": "postmortem/evidence recorded",
                "next_stage": {
                    "status": status,
                    "attempt_count": attempt_count,
                    "multi_file_nonfixture": index < 5,
                    "false_success": False,
                    "postmortem": "not required for successful run",
                    "evidence_package": {
                        "kind": NEXT_STAGE_PACKAGE_KIND,
                        "contract_version": 1,
                        "trial_id": trial["id"],
                        "run_id": f"next-stage-{index}",
                        "task_class": trial["class"],
                        "status": status,
                        "attempt_count": attempt_count,
                        "real_repo_task": True,
                        "fixture_only": False,
                        "false_success": False,
                        "multi_file_nonfixture": index < 5,
                        "changed_files": changed_files,
                        "verification_passed": True,
                        "review_accepted": True,
                        "artifacts": {
                            "repo_investigation": "evidence/repo_investigation.json",
                            "planning": "evidence/planning_department.json",
                            "execution": "evidence/execution_result.json",
                            "verification": "evidence/verification_report.json",
                            "review": "evidence/review_gate.json",
                        },
                    },
                },
            }
        )
    synthetic_report = build_report(data, {"entries": synthetic_entries})
    synthetic_next_stage = synthetic_report["next_stage_metrics"]
    if synthetic_report["next_stage_target_met"] is not True:
        raise AssertionError(f"synthetic next-stage target should pass when all requirements are met: {synthetic_next_stage}")
    false_success_entries = json.loads(json.dumps(synthetic_entries))
    false_success_entries[0]["next_stage"]["false_success"] = True
    false_success_report = build_report(data, {"entries": false_success_entries})
    if false_success_report["next_stage_target_met"] is True:
        raise AssertionError(f"next-stage target must fail on false success: {false_success_report['next_stage_metrics']}")
    missing_evidence_entries = json.loads(json.dumps(synthetic_entries))
    missing_evidence_entries[0]["next_stage"].pop("evidence_package")
    missing_evidence_report = build_report(data, {"entries": missing_evidence_entries})
    if missing_evidence_report["next_stage_target_met"] is True:
        raise AssertionError(f"next-stage target must fail without evidence package: {missing_evidence_report['next_stage_metrics']}")
    blocked_without_postmortem = json.loads(json.dumps(synthetic_entries))
    blocked_without_postmortem[0]["human_review_notes"] = ""
    blocked_without_postmortem[0]["next_stage"]["status"] = "honest_blocked"
    blocked_without_postmortem[0]["next_stage"]["postmortem"] = ""
    blocked_without_postmortem[0]["next_stage"]["evidence_package"]["status"] = "honest_blocked"
    blocked_without_postmortem[0]["next_stage"]["evidence_package"]["postmortem"] = ""
    blocked_report = build_report(data, {"entries": blocked_without_postmortem})
    if blocked_report["next_stage_target_met"] is True:
        raise AssertionError(f"next-stage target must fail without blocked postmortem: {blocked_report['next_stage_metrics']}")
    if report_payload.get("expert_target_met") is True:
        expert_gaps = report_payload.get("expert_gaps", {})
        if (
            expert_gaps.get("rolling_honest_expert_trial_count", 0) < expert_target.get("minimum_representative_trials", 0)
            or expert_gaps.get("rolling_honest_expert_class_count", 0) < expert_target.get("minimum_expert_classes", 0)
            or expert_gaps.get("rolling_honest_unshaped_expert_trial_count", 0) < expert_target.get("minimum_unshaped_expert_trials", 0)
            or report_payload.get("rolling_honest_expert_overall_score", 0) < expert_target.get("rolling_average_min", 0)
        ):
            raise AssertionError(f"Ceraxia expert target cannot pass without required evidence: {report_payload}")
    if report_payload.get("accepted_trial_count", 0) and report_payload.get("target_met") is not True:
        low_entries = report_payload.get("gaps", {}).get("low_score_entries", {})
        if not isinstance(low_entries, dict):
            raise AssertionError(f"Ceraxia report must expose low score entries after accepted reviews: {report_payload}")
        if "needs_more_honest_evidence" not in report_payload.get("gaps", {}):
            raise AssertionError(f"Ceraxia report must expose honest-evidence target gap: {report_payload}")
    sample_counts = report_payload.get("dimension_sample_counts", {})
    if set(sample_counts) != set(dimensions):
        raise AssertionError(f"Ceraxia report must expose sample counts for every dimension: {report_payload}")
    honest_sample_counts = report_payload.get("honest_dimension_sample_counts", {})
    if set(honest_sample_counts) != set(dimensions):
        raise AssertionError(f"Ceraxia report must expose honest sample counts for every dimension: {report_payload}")
    expert_sample_counts = report_payload.get("expert_dimension_sample_counts", {})
    if set(expert_sample_counts) != set(dimensions):
        raise AssertionError(f"Ceraxia report must expose expert sample counts for every dimension: {report_payload}")
    honest_expert_sample_counts = report_payload.get("honest_expert_dimension_sample_counts", {})
    if set(honest_expert_sample_counts) != set(dimensions):
        raise AssertionError(f"Ceraxia report must expose honest expert sample counts for every dimension: {report_payload}")
    rolling_honest_expert_sample_counts = report_payload.get("rolling_honest_expert_dimension_sample_counts", {})
    if set(rolling_honest_expert_sample_counts) != set(dimensions):
        raise AssertionError(f"Ceraxia report must expose rolling honest expert sample counts for every dimension: {report_payload}")
    expert_dimension_averages = report_payload.get("expert_dimension_averages", {})
    if set(expert_dimension_averages) != set(dimensions):
        raise AssertionError(f"Ceraxia report must expose expert averages for every dimension: {report_payload}")
    honest_dimension_averages = report_payload.get("honest_dimension_averages", {})
    if set(honest_dimension_averages) != set(dimensions):
        raise AssertionError(f"Ceraxia report must expose honest averages for every dimension: {report_payload}")
    honest_expert_dimension_averages = report_payload.get("honest_expert_dimension_averages", {})
    if set(honest_expert_dimension_averages) != set(dimensions):
        raise AssertionError(f"Ceraxia report must expose honest expert averages for every dimension: {report_payload}")
    rolling_honest_expert_dimension_averages = report_payload.get("rolling_honest_expert_dimension_averages", {})
    if set(rolling_honest_expert_dimension_averages) != set(dimensions):
        raise AssertionError(f"Ceraxia report must expose rolling honest expert averages for every dimension: {report_payload}")
    expert_gaps = report_payload.get("expert_gaps", {})
    for key in {
        "honest_expert_trial_count",
        "honest_unshaped_expert_trial_count",
        "needs_more_honest_expert_evidence",
        "needs_more_honest_unshaped_expert_evidence",
        "rolling_honest_expert_trial_count",
        "rolling_honest_expert_window_size",
        "rolling_honest_expert_class_count",
        "rolling_honest_unshaped_expert_trial_count",
        "needs_more_rolling_honest_expert_evidence",
        "needs_more_rolling_honest_expert_classes",
        "needs_more_rolling_honest_unshaped_expert_evidence",
        "expert_entries_without_honest_evidence",
    }:
        if key not in expert_gaps:
            raise AssertionError(f"Ceraxia expert report must expose honest-evidence gap {key}: {report_payload}")
    strict_report = subprocess.run(
        [sys.executable, str(REPORTER), "--require-target"],
        cwd=str(EYE_ROOT.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    if report_payload.get("target_met") is True and strict_report.returncode != 0:
        raise AssertionError(f"strict Ceraxia field trial report rejected proven 7/10 target: {strict_report.stdout}")
    if report_payload.get("target_met") is not True and strict_report.returncode == 0:
        raise AssertionError("strict Ceraxia field trial report must fail until honest evidence proves 7/10")
    expert_strict_report = subprocess.run(
        [sys.executable, str(REPORTER), "--require-expert-target"],
        cwd=str(EYE_ROOT.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    if report_payload.get("expert_target_met") is True and expert_strict_report.returncode != 0:
        raise AssertionError(f"strict Ceraxia expert report rejected proven expert target: {expert_strict_report.stdout}")
    if report_payload.get("expert_target_met") is not True and expert_strict_report.returncode == 0:
        raise AssertionError("strict Ceraxia expert report must fail until expert evidence proves 10/10")
    next_stage_strict_report = subprocess.run(
        [sys.executable, str(REPORTER), "--require-next-stage-target"],
        cwd=str(EYE_ROOT.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    if report_payload.get("next_stage_target_met") is True and next_stage_strict_report.returncode != 0:
        raise AssertionError(f"strict Ceraxia next-stage report rejected proven live target: {next_stage_strict_report.stdout}")
    if report_payload.get("next_stage_target_met") is not True and next_stage_strict_report.returncode == 0:
        raise AssertionError("strict Ceraxia next-stage report must fail until live evidence packages prove the target")
    runner_list = subprocess.run(
        [sys.executable, str(RUNNER), "--list"],
        cwd=str(EYE_ROOT.parent),
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
        "ceraxia-expert-unshaped-pytest-runtime",
        "ceraxia-expert-unshaped-runtime-alias",
        "ceraxia-expert-unshaped-self-repair-batch-limit",
        "ceraxia-expert-unshaped-self-repair-retention-days",
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
    smoke_trial = subprocess.run(
        [sys.executable, str(RUNNER), "--trial", "ceraxia-expert-unshaped-pytest-runtime"],
        cwd=str(EYE_ROOT.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    if smoke_trial.returncode != 0:
        raise AssertionError(f"Ceraxia honest-evidence smoke trial failed: {smoke_trial.stdout} {smoke_trial.stderr}")
    smoke_payload = json.loads(smoke_trial.stdout)
    honest = smoke_payload.get("honest_evidence") if isinstance(smoke_payload.get("honest_evidence"), dict) else {}
    honest_checks = honest.get("checks") if isinstance(honest.get("checks"), dict) else {}
    required_honest_checks = REQUIRED_HONEST_CHECKS
    if honest.get("status") != "passed" or not required_honest_checks.issubset(honest_checks):
        raise AssertionError(f"Ceraxia trial result must expose honest evidence checks: {smoke_payload}")
    if not all(isinstance(item, dict) and item.get("passed") is True for item in honest_checks.values()):
        raise AssertionError(f"Ceraxia honest evidence checks must all pass for accepted smoke trial: {honest}")
    smoke_next_stage = smoke_payload.get("next_stage") if isinstance(smoke_payload.get("next_stage"), dict) else {}
    if not smoke_next_stage.get("evidence_package"):
        raise AssertionError(f"Ceraxia smoke trial must write next-stage evidence package reference: {smoke_payload}")
    smoke_next_stage_entry = json.loads(json.dumps(smoke_payload))
    smoke_next_stage_entry["next_stage"]["evidence_package"] = smoke_payload.get("next_stage_evidence_package_payload")
    smoke_next_stage_status = next_stage_evidence_status(EYE_ROOT.parent, smoke_next_stage_entry, {"class": "runtime_configuration"})
    if smoke_next_stage_status.get("passed") is True or "real_repo_task" not in str(smoke_next_stage_status.get("reason", "")):
        raise AssertionError(f"Ceraxia fixture smoke trial must not count as live next-stage evidence: {smoke_next_stage_status}")
    structured_honest = honest_evidence_summary(
        {
            "status": "ready",
            "approved": True,
            "patch_source": "feature_marker_synthesis",
            "changed_files": [
                {"path": "billing/discounts.py"},
                {"path": "tests/test_discounts.py"},
            ],
            "verification_executed": [
                {"command": "python -m pytest tests/test_discounts.py", "returncode": 0}
            ],
            "verification_summary": {"repair_count": 0, "blocker_count": 0},
            "review_decision_record": [{"decision": "approve"}],
            "unshaped_repair_plan": {"mode": "structured_patch"},
        },
        {},
    )
    if structured_honest.get("status") != "passed":
        raise AssertionError(f"Ceraxia structured patch honest evidence was falsely rejected: {structured_honest}")
    unsafe_test_edit = honest_evidence_summary(
        {
            "status": "ready",
            "approved": True,
            "patch_source": "test_inferred_arithmetic_return",
            "changed_files": [
                {"path": "calculator.py"},
                {"path": "tests/test_calculator.py"},
            ],
            "verification_executed": [
                {"command": "python -m pytest tests/test_calculator.py", "returncode": 0}
            ],
            "verification_summary": {"repair_count": 0, "blocker_count": 0},
            "review_decision_record": [{"decision": "approve"}],
            "unshaped_repair_plan": {"mode": "structured_patch"},
        },
        {},
    )
    if unsafe_test_edit.get("checks", {}).get("tests_not_adjusted", {}).get("passed") is True:
        raise AssertionError(f"Ceraxia inferred source repair must not hide changed tests: {unsafe_test_edit}")
    senior_manifest = {
        "changed_files": [{"path": "app/config.py"}, {"path": "docs/config.md"}],
        "diagnostics": {"source_path": "app/config.py", "docs_path": "docs/config.md"},
        "engineering_readiness": {"readiness_checks": {"has_ranked_sources": True, "has_acceptance_criteria": True, "has_test_strategy": True}},
        "repository_investigation_review": {"status": "covered", "blockers": [], "checks": [{"status": "pass"}]},
        "architecture_decision_record": {
            "status": "recorded",
            "drivers": ["compatibility"],
            "alternatives_considered": [{"option": "rewrite", "rejected_because": "too broad"}],
            "rollback": "revert changed files",
        },
        "verification_strategy": {"focused_commands": ["python -m unittest tests.test_config"], "broad_commands": []},
        "verification_summary": {"executed_count": 3, "blocker_count": 0},
        "patch_scope_evidence": {"changed_files_outside_repo_map": ["docs/config.md"], "evidence": [{"path": "docs/config.md"}]},
        "code_review_discipline": {"blocker_count": 0},
        "unshaped_repair_plan": {"mode": "unshaped_repo_repair"},
        "review_decision_record": [{"status": "pass"} for _ in range(10)],
        "implementation_decision_record": [{"status": "pass"}],
    }
    if senior_evidence_signals(senior_manifest).get("complete") is not True:
        raise AssertionError(f"Ceraxia senior evidence signals rejected explained rich manifest: {senior_evidence_signals(senior_manifest)}")
    principal_manifest = json.loads(json.dumps(senior_manifest))
    principal_manifest.update(
        {
            "status": "ready",
            "approved": True,
            "problem_statement": {"status": "recorded"},
            "architecture_options": {"status": "recorded"},
            "verification_strategy": {
                "focused_commands": ["python -m unittest tests.test_config"],
                "broad_commands": ["python -m unittest discover -s tests"],
            },
            "verification_summary": {"executed_count": 4, "blocker_count": 0},
            "patch_scope_evidence": {"changed_files_outside_repo_map": [], "evidence": [{"path": "app/config.py"}]},
            "patch_package": {
                "kind": "ceraxia_patch_package",
                "workflow_mode": "repo_grade",
                "review_decision_record": [{"status": "pass"}],
            },
            "pr_summary": {"rollback": "revert changed files"},
            "principal_evidence_summary": {
                "status": "complete",
                "checks": {
                    "ready_and_approved": True,
                    "problem_and_options_recorded": True,
                    "investigation_depth": True,
                    "acceptance_and_impact_model": True,
                    "scope_and_rollback_control": True,
                    "verification_after_mutation": True,
                    "broad_or_repo_grade_coverage": True,
                    "review_gate_rich": True,
                    "architecture_and_package_recorded": True,
                    "diagnostic_or_repair_trace": True,
                    "repair_loop_accounted": True,
                },
                "missing_checks": [],
                "strength_count": 11,
                "check_count": 11,
            },
        }
    )
    if principal_evidence_signals(principal_manifest).get("complete") is not True:
        raise AssertionError(f"Ceraxia principal evidence signals rejected complete manifest: {principal_evidence_signals(principal_manifest)}")
    unexplained_scope = json.loads(json.dumps(senior_manifest))
    unexplained_scope["diagnostics"] = {"source_path": "app/config.py"}
    if senior_evidence_signals(unexplained_scope).get("patch_scope_mapped") is True:
        raise AssertionError(f"Ceraxia senior evidence signals accepted unexplained outside scope: {senior_evidence_signals(unexplained_scope)}")
    repair_manifest = {
        "verification_summary": {"repair_count": 1},
        "verification_executed": [
            {"command": "python -m unittest tests.test_quota", "returncode": 1},
            {"command": "python -m unittest tests.test_quota", "returncode": 0, "after_repair": True},
        ],
        "verification_repairs": [{"applied": True, "kind": "assertion_return_mismatch", "path": "quota.py"}],
        "repair_loop_state": {
            "status": "passed",
            "failed_commands": [{"command": "python -m unittest tests.test_quota", "returncode": 1}],
        },
        "diagnostic_extraction": {
            "parser_coverage": {"runtime_test_failures": 1, "runtime_minimal_patch_candidates": 1},
            "runtime_minimal_patch_candidates": [{"kind": "replace_return_expression", "path": "quota.py"}],
        },
        "changed_files": [{"path": "quota.py"}],
    }
    if repair_evidence_signals(repair_manifest).get("complete") is not True:
        raise AssertionError(f"Ceraxia repair evidence signals rejected complete repair loop: {repair_evidence_signals(repair_manifest)}")
    incomplete_repair = json.loads(json.dumps(repair_manifest))
    incomplete_repair["verification_executed"] = [{"command": "python -m unittest tests.test_quota", "returncode": 0}]
    if repair_evidence_signals(incomplete_repair).get("complete") is True:
        raise AssertionError(f"Ceraxia repair evidence signals accepted repair without failed/after-repair evidence: {repair_evidence_signals(incomplete_repair)}")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        trial_result = tmp_root / "trial_result.json"
        trial_result.write_text(
            json.dumps({"honest_evidence": {"status": "passed", "checks": {key: {"passed": True} for key in REQUIRED_HONEST_CHECKS}}}),
            encoding="utf-8",
        )
        incomplete_status = evidence_package_status(tmp_root, {"evidence_paths": [str(trial_result)]})
        if incomplete_status.get("passed") is True or "final_manifest" not in str(incomplete_status.get("reason", "")):
            raise AssertionError(f"Ceraxia evidence contract must reject incomplete packages: {incomplete_status}")
        manifest = tmp_root / "final_manifest.json"
        manifest.write_text(json.dumps({"status": "ready"}), encoding="utf-8")
        weak_status = evidence_package_status(tmp_root, {"evidence_paths": [str(trial_result), str(manifest)]})
        if weak_status.get("passed") is True or "approved" not in str(weak_status.get("reason", "")):
            raise AssertionError(f"Ceraxia evidence contract must reject weak ready-only manifest: {weak_status}")
        manifest.write_text(
            json.dumps(
                {
                    "status": "ready",
                    "approved": True,
                    "changed_files": [{"path": "app.py"}],
                    "verification_summary": {"executed_count": 1, "blocker_count": 0},
                    "review_decision_record": [{"status": "pass"}],
                }
            ),
            encoding="utf-8",
        )
        complete_status = evidence_package_status(tmp_root, {"evidence_paths": [str(trial_result), str(manifest)]})
        if complete_status.get("passed") is not True:
            raise AssertionError(f"Ceraxia evidence contract rejected complete package: {complete_status}")
        next_stage_entry = synthetic_entries[0]
        next_stage_status = next_stage_evidence_status(tmp_root, next_stage_entry, synthetic_trials[0])
        if next_stage_status.get("passed") is not True:
            raise AssertionError(f"Ceraxia next-stage evidence contract rejected complete inline package: {next_stage_status}")
        weak_next_stage_entry = json.loads(json.dumps(next_stage_entry))
        weak_next_stage_entry["next_stage"]["evidence_package"]["artifacts"].pop("review")
        weak_next_stage_status = next_stage_evidence_status(tmp_root, weak_next_stage_entry, synthetic_trials[0])
        if weak_next_stage_status.get("passed") is True or "artifacts" not in str(weak_next_stage_status.get("reason", "")):
            raise AssertionError(f"Ceraxia next-stage evidence contract accepted incomplete package: {weak_next_stage_status}")
        live_package_path = tmp_root / "live_next_stage_package.json"
        live_builder = subprocess.run(
            [
                sys.executable,
                str(NEXT_STAGE_BUILDER),
                "--trial-id",
                "ceraxia-live-cli-contract-flag",
                "--run-id",
                "live-code-task-001",
                "--task-class",
                "cli_contract",
                "--status",
                "fully_successful",
                "--attempt-count",
                "1",
                "--changed-file",
                "app/service.py",
                "--changed-file",
                "tests/test_service.py",
                "--artifact",
                "repo_investigation=evidence/repo_investigation.json",
                "--artifact",
                "planning=evidence/planning_department.json",
                "--artifact",
                "execution=evidence/execution_result.json",
                "--artifact",
                "verification=evidence/verification_report.json",
                "--artifact",
                "review=evidence/review_gate.json",
                "--multi-file-nonfixture",
                "--verification-passed",
                "--review-accepted",
                "--output",
                str(live_package_path),
            ],
            cwd=str(EYE_ROOT.parent),
            text=True,
            capture_output=True,
            check=False,
        )
        if live_builder.returncode != 0:
            raise AssertionError(f"Ceraxia live next-stage builder rejected valid package: {live_builder.stdout} {live_builder.stderr}")
        live_builder_payload = json.loads(live_builder.stdout)
        if live_builder_payload.get("status", {}).get("passed") is not True or not live_package_path.exists():
            raise AssertionError(f"Ceraxia live next-stage builder did not write a valid package: {live_builder_payload}")
        live_ledger = tmp_root / "live_ledger.json"
        live_ledger.write_text(json.dumps({"version": 1, "purpose": "test", "entries": []}), encoding="utf-8")
        live_register = subprocess.run(
            [
                sys.executable,
                str(LIVE_TASK_REGISTER),
                "--task-id",
                "ceraxia-live-cli-contract-flag",
                "--package",
                str(live_package_path),
                "--ledger",
                str(live_ledger),
                "--reviewer",
                "self-test",
                "--notes",
                "validated live package registration dry run",
                "--dry-run",
            ],
            cwd=str(EYE_ROOT.parent),
            text=True,
            capture_output=True,
            check=False,
        )
        if live_register.returncode != 0:
            raise AssertionError(f"Ceraxia live task registrar rejected valid package: {live_register.stdout} {live_register.stderr}")
        live_register_payload = json.loads(live_register.stdout)
        if live_register_payload.get("entry", {}).get("trial_id") != "ceraxia-live-cli-contract-flag":
            raise AssertionError(f"Ceraxia live task registrar returned malformed entry: {live_register_payload}")
        weak_builder = subprocess.run(
            [
                sys.executable,
                str(NEXT_STAGE_BUILDER),
                "--trial-id",
                "live-code-task",
                "--run-id",
                "live-code-task-002",
                "--task-class",
                "live_multi_file_feature",
                "--status",
                "fully_successful",
                "--attempt-count",
                "1",
                "--changed-file",
                "app/service.py",
                "--artifact",
                "repo_investigation=evidence/repo_investigation.json",
                "--artifact",
                "planning=evidence/planning_department.json",
                "--artifact",
                "execution=evidence/execution_result.json",
                "--artifact",
                "verification=evidence/verification_report.json",
                "--verification-passed",
                "--review-accepted",
            ],
            cwd=str(EYE_ROOT.parent),
            text=True,
            capture_output=True,
            check=False,
        )
        if weak_builder.returncode == 0:
            raise AssertionError(f"Ceraxia live next-stage builder accepted incomplete package: {weak_builder.stdout}")
        weak_live_package = tmp_root / "weak_live_package.json"
        weak_live_package.write_text(
            json.dumps(
                {
                    "kind": NEXT_STAGE_PACKAGE_KIND,
                    "contract_version": 1,
                    "trial_id": "ceraxia-live-cli-contract-flag",
                    "run_id": "live-code-task-003",
                    "task_class": "cli_contract",
                    "status": "fully_successful",
                    "attempt_count": 1,
                    "real_repo_task": True,
                    "fixture_only": False,
                    "false_success": False,
                    "multi_file_nonfixture": True,
                    "changed_files": ["app/service.py"],
                    "verification_passed": True,
                    "review_accepted": True,
                    "postmortem": "",
                    "artifacts": {
                        "repo_investigation": "evidence/repo_investigation.json",
                        "planning": "evidence/planning_department.json",
                        "execution": "evidence/execution_result.json",
                        "verification": "evidence/verification_report.json",
                        "review": "evidence/review_gate.json",
                    },
                }
            ),
            encoding="utf-8",
        )
        weak_register = subprocess.run(
            [
                sys.executable,
                str(LIVE_TASK_REGISTER),
                "--task-id",
                "ceraxia-live-cli-contract-flag",
                "--package",
                str(weak_live_package),
                "--ledger",
                str(live_ledger),
                "--dry-run",
            ],
            cwd=str(EYE_ROOT.parent),
            text=True,
            capture_output=True,
            check=False,
        )
        if weak_register.returncode == 0:
            raise AssertionError(f"Ceraxia live task registrar accepted too-small package: {weak_register.stdout}")
    expert_suite = subprocess.run(
        [sys.executable, str(EXPERT_SUITE), "--require-all"],
        cwd=str(EYE_ROOT.parent),
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
        cwd=str(EYE_ROOT.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    if expert_review.returncode != 0:
        raise AssertionError(f"Ceraxia strict expert reviewer failed: {expert_review.stdout} {expert_review.stderr}")
    expert_review_payload = json.loads(expert_review.stdout)
    if "review_count" not in expert_review_payload or "report" not in expert_review_payload or "rejected_entries" not in expert_review_payload:
        raise AssertionError(f"Ceraxia strict expert reviewer returned malformed payload: {expert_review_payload}")
    auto_review = subprocess.run(
        [sys.executable, str(AUTO_REVIEWER), "--dry-run"],
        cwd=str(EYE_ROOT.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    if auto_review.returncode != 0:
        raise AssertionError(f"Ceraxia conservative auto reviewer failed: {auto_review.stdout} {auto_review.stderr}")
    auto_review_payload = json.loads(auto_review.stdout)
    if "review_count" not in auto_review_payload or "rejected_entries" not in auto_review_payload or "report" not in auto_review_payload:
        raise AssertionError(f"Ceraxia conservative auto reviewer returned malformed payload: {auto_review_payload}")
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
            cwd=str(EYE_ROOT.parent),
            text=True,
            capture_output=True,
            check=False,
        )
        if expert_review_unshaped.returncode != 0:
            raise AssertionError(f"Ceraxia unshaped expert reviewer failed: {expert_review_unshaped.stdout} {expert_review_unshaped.stderr}")
        unshaped_payload = json.loads(expert_review_unshaped.stdout)
        if unshaped_payload.get("review_count", 0) != unshaped_draft_count:
            raise AssertionError(f"Ceraxia unshaped expert reviewer must cover every current draft: {unshaped_payload}")
        if unshaped_payload.get("rejected_count", 0) != 0:
            raise AssertionError(f"Ceraxia unshaped expert reviewer rejected current drafts: {unshaped_payload}")
    review_all = subprocess.run(
        [sys.executable, str(REVIEWER), "--all"],
        cwd=str(EYE_ROOT.parent),
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
        cwd=str(EYE_ROOT.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    if len(ledger.get("entries", [])) > 1 and ambiguous_review.returncode == 0:
        raise AssertionError("Ceraxia review helper must require --all or a narrow selector for multiple entries")
    if ledger.get("entries"):
        accepted_before_dry_run = sum(1 for entry in ledger.get("entries", []) if entry.get("accepted_for_rolling_score") is True)
        first_entry = ledger["entries"][0]
        bad_review_path = WARMASTER_ROOT / "tmp_bad_ceraxia_review.json"
        good_review_path = WARMASTER_ROOT / "tmp_good_ceraxia_review.json"
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
                cwd=str(EYE_ROOT.parent),
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
                cwd=str(EYE_ROOT.parent),
                text=True,
                capture_output=True,
                check=False,
            )
            if good_accept.returncode != 0:
                good_payload = json.loads(good_accept.stdout)
                error_text = str(good_payload.get("error", ""))
                if "honest_evidence" not in error_text and "lacks trial_result evidence" not in error_text:
                    raise AssertionError(f"Ceraxia accept helper rejected complete dry-run review for the wrong reason: {good_accept.stdout} {good_accept.stderr}")
            else:
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
