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


def main() -> int:
    data = json.loads(SPEC.read_text(encoding="utf-8"))
    protocol = PROTOCOL.read_text(encoding="utf-8")
    dimensions = data.get("dimensions", [])
    trials = data.get("trials", [])
    target = data.get("target", {})
    if target.get("minimum_representative_trials", 0) < 12:
        raise AssertionError(f"Ceraxia field trials target is too small: {target}")
    if len(trials) < target.get("minimum_representative_trials", 0):
        raise AssertionError(f"Ceraxia field trial suite is undersized: {len(trials)} {target}")
    if len(set(dimensions)) != len(dimensions) or len(dimensions) < 8:
        raise AssertionError(f"Ceraxia dimensions are missing or duplicated: {dimensions}")
    seen: set[str] = set()
    classes: set[str] = set()
    for trial in trials:
        trial_id = str(trial.get("id") or "")
        if not trial_id or trial_id in seen:
            raise AssertionError(f"bad or duplicate Ceraxia trial id: {trial}")
        seen.add(trial_id)
        classes.add(str(trial.get("class") or ""))
        if not trial.get("task") or not trial.get("required_evidence") or not trial.get("failure_modes_to_watch"):
            raise AssertionError(f"Ceraxia field trial lacks task/evidence/failure modes: {trial}")
    if len(classes) < 8:
        raise AssertionError(f"Ceraxia field trials are not diverse enough: {classes}")
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
    strict_report = subprocess.run(
        [sys.executable, str(REPORTER), "--require-target"],
        cwd=str(ROOT.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    if not ledger.get("entries") and strict_report.returncode == 0:
        raise AssertionError("strict Ceraxia field trial report must fail while no accepted trials exist")
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
    required_runner_trials = {
        "ceraxia-field-ambiguous-task",
        "ceraxia-field-bugfix-unnamed-source",
        "ceraxia-field-cross-language-config",
        "ceraxia-field-data-migration",
        "ceraxia-field-large-file-restraint",
        "ceraxia-field-multifile-feature",
        "ceraxia-field-negative-test",
        "ceraxia-field-refactor-preserve-behavior",
        "ceraxia-field-repair-after-bad-first-patch",
        "ceraxia-field-safety-dirty-worktree",
    }
    if not required_runner_trials.issubset(runner_trials):
        raise AssertionError(f"Ceraxia field trial runner lacks first reproducible trial: {runner_payload}")
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
