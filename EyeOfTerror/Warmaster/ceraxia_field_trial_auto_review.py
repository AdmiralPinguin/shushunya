#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ceraxia_evidence_contract import REQUIRED_HONEST_CHECKS
from ceraxia_field_trial_accept import apply_review
from ceraxia_field_trial_report import build_report, load_json, validate_ledger
from ceraxia_field_trial_review import build_review_packet


WARMASTER_ROOT = Path(__file__).resolve().parent
EYE_ROOT = WARMASTER_ROOT.parent
SPEC = EYE_ROOT / "Mechanicum" / "Ceraxia" / "field_trials.json"
LEDGER = EYE_ROOT / "Mechanicum" / "Ceraxia" / "field_trial_ledger.json"


DIMENSIONS = [
    "task_understanding",
    "repository_investigation",
    "multi_file_reasoning",
    "patch_correctness",
    "verification_discipline",
    "self_repair",
    "review_quality",
    "safety",
    "reporting",
]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def candidate_entries(ledger: dict[str, Any], trial_id: str = "", run_id: str = "", *, include_accepted: bool = False) -> list[dict[str, Any]]:
    entries = [entry for entry in ledger.get("entries", []) if isinstance(entry, dict)]
    if not include_accepted:
        entries = [entry for entry in entries if entry.get("accepted_for_rolling_score") is not True]
    if trial_id:
        entries = [entry for entry in entries if entry.get("trial_id") == trial_id]
    if run_id:
        entries = [entry for entry in entries if entry.get("run_id") == run_id]
    return entries


def require_passed_packet(packet: dict[str, Any]) -> None:
    evidence = packet.get("evidence") if isinstance(packet.get("evidence"), dict) else {}
    if not evidence.get("trial_result") or not evidence.get("final_manifest"):
        raise ValueError("missing complete evidence package")
    observed = packet.get("observed") if isinstance(packet.get("observed"), dict) else {}
    honest = observed.get("honest_evidence") if isinstance(observed.get("honest_evidence"), dict) else {}
    honest_checks = honest.get("checks") if isinstance(honest.get("checks"), dict) else {}
    if honest.get("status") != "passed":
        raise ValueError("honest_evidence is not passed")
    missing = REQUIRED_HONEST_CHECKS - set(honest_checks)
    if missing:
        raise ValueError(f"missing honest evidence checks: {', '.join(sorted(missing))}")
    failed = [
        name
        for name in REQUIRED_HONEST_CHECKS
        if not isinstance(honest_checks.get(name), dict) or honest_checks[name].get("passed") is not True
    ]
    if failed:
        raise ValueError(f"failed honest evidence checks: {', '.join(sorted(failed))}")
    outcome = observed.get("trial_outcome") if isinstance(observed.get("trial_outcome"), dict) else {}
    if outcome.get("expected") is not True:
        raise ValueError("trial_outcome was not expected")
    if str(observed.get("manifest_status") or "") != "ready":
        raise ValueError("final manifest is not ready")
    blockers = observed.get("blockers") if isinstance(observed.get("blockers"), list) else []
    if blockers:
        raise ValueError(f"final manifest has blockers: {blockers}")
    verification = observed.get("verification_summary") if isinstance(observed.get("verification_summary"), dict) else {}
    if int(verification.get("executed_count") or 0) <= 0:
        raise ValueError("no executed verification evidence")


def manifest_for_packet(packet: dict[str, Any]) -> dict[str, Any]:
    evidence = packet.get("evidence") if isinstance(packet.get("evidence"), dict) else {}
    path_text = str(evidence.get("final_manifest") or "")
    if not path_text:
        return {}
    path = Path(path_text)
    if not path.exists():
        return {}
    return load_json(path)


def senior_evidence_signals(manifest: dict[str, Any]) -> dict[str, Any]:
    readiness = manifest.get("engineering_readiness") if isinstance(manifest.get("engineering_readiness"), dict) else {}
    readiness_checks = readiness.get("readiness_checks") if isinstance(readiness.get("readiness_checks"), dict) else {}
    repo_review = manifest.get("repository_investigation_review") if isinstance(manifest.get("repository_investigation_review"), dict) else {}
    repo_review_checks = repo_review.get("checks") if isinstance(repo_review.get("checks"), list) else []
    adr = manifest.get("architecture_decision_record") if isinstance(manifest.get("architecture_decision_record"), dict) else {}
    verification_strategy = manifest.get("verification_strategy") if isinstance(manifest.get("verification_strategy"), dict) else {}
    scope = manifest.get("patch_scope_evidence") if isinstance(manifest.get("patch_scope_evidence"), dict) else {}
    diagnostics = manifest.get("diagnostics") if isinstance(manifest.get("diagnostics"), dict) else {}
    review_discipline = manifest.get("code_review_discipline") if isinstance(manifest.get("code_review_discipline"), dict) else {}
    unshaped_plan = manifest.get("unshaped_repair_plan") if isinstance(manifest.get("unshaped_repair_plan"), dict) else {}
    review_record = manifest.get("review_decision_record") if isinstance(manifest.get("review_decision_record"), list) else []
    implementation_record = manifest.get("implementation_decision_record") if isinstance(manifest.get("implementation_decision_record"), list) else []
    verification_summary = manifest.get("verification_summary") if isinstance(manifest.get("verification_summary"), dict) else {}
    changed_files = manifest.get("changed_files") if isinstance(manifest.get("changed_files"), list) else []
    changed_paths = {
        str(item.get("path") or "")
        for item in changed_files
        if isinstance(item, dict) and item.get("path")
    }
    diagnostic_paths = {
        str(value)
        for key, value in diagnostics.items()
        if key.endswith("_path") and isinstance(value, str) and value
    }
    outside_scope = scope.get("changed_files_outside_repo_map")
    outside_scope_paths = set(outside_scope) if isinstance(outside_scope, list) else set()
    outside_scope_explained = bool(outside_scope_paths) and outside_scope_paths.issubset(changed_paths) and outside_scope_paths.issubset(diagnostic_paths)
    rich_verification = int(verification_summary.get("executed_count") or 0) >= 3 and int(verification_summary.get("blocker_count") or 0) == 0
    signals = {
        "unshaped_repo_repair": unshaped_plan.get("mode") == "unshaped_repo_repair",
        "readiness_complete": all(readiness_checks.get(key) is True for key in ["has_ranked_sources", "has_acceptance_criteria", "has_test_strategy"]),
        "repository_review_covered": repo_review.get("status") == "covered"
        and not repo_review.get("blockers")
        and bool(repo_review_checks)
        and all(isinstance(item, dict) and item.get("status") == "pass" for item in repo_review_checks),
        "architecture_decision_recorded": adr.get("status") == "recorded"
        and bool(adr.get("drivers"))
        and bool(adr.get("alternatives_considered"))
        and bool(adr.get("rollback")),
        "verification_strategy_broad_and_focused": bool(verification_strategy.get("focused_commands"))
        and (bool(verification_strategy.get("broad_commands")) or rich_verification),
        "patch_scope_mapped": isinstance(outside_scope, list)
        and (not outside_scope_paths or outside_scope_explained)
        and bool(scope.get("evidence")),
        "code_review_clean": int(review_discipline.get("blocker_count") or 0) == 0,
        "review_decision_record_rich": len(review_record) >= 10,
        "implementation_decision_recorded": bool(implementation_record)
        and all(isinstance(item, dict) and item.get("status") == "pass" for item in implementation_record),
        "verification_executed_rich": int(verification_summary.get("executed_count") or 0) >= 3,
    }
    signals["complete"] = all(signals.values())
    return signals


def principal_evidence_signals(manifest: dict[str, Any]) -> dict[str, Any]:
    summary = manifest.get("principal_evidence_summary") if isinstance(manifest.get("principal_evidence_summary"), dict) else {}
    checks = summary.get("checks") if isinstance(summary.get("checks"), dict) else {}
    missing = summary.get("missing_checks") if isinstance(summary.get("missing_checks"), list) else []
    expected_checks = {
        "ready_and_approved",
        "problem_and_options_recorded",
        "investigation_depth",
        "acceptance_and_impact_model",
        "scope_and_rollback_control",
        "verification_after_mutation",
        "broad_or_repo_grade_coverage",
        "review_gate_rich",
        "architecture_and_package_recorded",
        "diagnostic_or_repair_trace",
        "repair_loop_accounted",
    }
    present = expected_checks.issubset(checks)
    complete = (
        summary.get("status") == "complete"
        and present
        and not missing
        and all(checks.get(name) is True for name in expected_checks)
    )
    return {
        "present": bool(summary),
        "expected_checks_present": present,
        "complete": complete,
        "strength_count": int(summary.get("strength_count") or 0),
        "check_count": int(summary.get("check_count") or 0),
        "missing_checks": missing,
    }


def repair_evidence_signals(manifest: dict[str, Any]) -> dict[str, Any]:
    verification = manifest.get("verification_summary") if isinstance(manifest.get("verification_summary"), dict) else {}
    executed = manifest.get("verification_executed") if isinstance(manifest.get("verification_executed"), list) else []
    repairs = manifest.get("verification_repairs") if isinstance(manifest.get("verification_repairs"), list) else []
    repair_state = manifest.get("repair_loop_state") if isinstance(manifest.get("repair_loop_state"), dict) else {}
    diagnostic = manifest.get("diagnostic_extraction") if isinstance(manifest.get("diagnostic_extraction"), dict) else {}
    parser_coverage = diagnostic.get("parser_coverage") if isinstance(diagnostic.get("parser_coverage"), dict) else {}
    runtime_candidates = diagnostic.get("runtime_minimal_patch_candidates") if isinstance(diagnostic.get("runtime_minimal_patch_candidates"), list) else []
    changed_files = manifest.get("changed_files") if isinstance(manifest.get("changed_files"), list) else []
    changed_tests = [
        str(item.get("path") or "")
        for item in changed_files
        if isinstance(item, dict)
        and ("test" in Path(str(item.get("path") or "")).name.lower() or "/tests/" in f"/{item.get('path')}")
    ]
    failed_commands = repair_state.get("failed_commands") if isinstance(repair_state.get("failed_commands"), list) else []
    after_repair = [
        item
        for item in executed
        if isinstance(item, dict) and item.get("after_repair") is True
    ]
    applied_repairs = [
        item
        for item in repairs
        if isinstance(item, dict) and item.get("applied") is True
    ]
    signals = {
        "repair_count_recorded": int(verification.get("repair_count") or 0) >= 1,
        "failed_verification_preserved": bool(failed_commands)
        or any(isinstance(item, dict) and int(item.get("returncode") or 0) != 0 for item in executed),
        "after_repair_verification_passed": bool(after_repair)
        and all(isinstance(item, dict) and int(item.get("returncode") or 0) == 0 for item in after_repair),
        "applied_repair_recorded": bool(applied_repairs),
        "repair_loop_passed": repair_state.get("status") == "passed",
        "runtime_diagnostic_parsed": int(parser_coverage.get("runtime_test_failures") or 0) >= 1
        and int(parser_coverage.get("runtime_minimal_patch_candidates") or 0) >= 1
        and bool(runtime_candidates),
        "tests_preserved": not changed_tests,
    }
    signals["complete"] = all(signals.values())
    return signals


def scores_for_packet(packet: dict[str, Any]) -> tuple[dict[str, float], list[str], list[str], str]:
    require_passed_packet(packet)
    observed = packet.get("observed") if isinstance(packet.get("observed"), dict) else {}
    verification = observed.get("verification_summary") if isinstance(observed.get("verification_summary"), dict) else {}
    changed_files = observed.get("changed_files") if isinstance(observed.get("changed_files"), list) else []
    diagnostics = observed.get("diagnostics") if isinstance(observed.get("diagnostics"), dict) else {}
    manifest = manifest_for_packet(packet)
    senior_signals = senior_evidence_signals(manifest)
    principal_signals = principal_evidence_signals(manifest)
    repair_signals = repair_evidence_signals(manifest)
    patch_source = str(observed.get("patch_source") or "")
    trial_class = str(packet.get("class") or "")
    difficulty = str(packet.get("difficulty") or "")
    scores = {dimension: 7.5 for dimension in DIMENSIONS}
    failures: list[str] = []
    followups: list[str] = []

    if difficulty == "expert":
        scores = {dimension: 8.0 for dimension in DIMENSIONS}
    if difficulty == "expert" and senior_signals.get("complete") is True:
        scores = {
            "task_understanding": 9.2,
            "repository_investigation": 9.4,
            "multi_file_reasoning": 9.2,
            "patch_correctness": 9.2,
            "verification_discipline": 9.3,
            "self_repair": 9.0,
            "review_quality": 9.35,
            "safety": 9.15,
            "reporting": 9.2,
        }
    if difficulty == "expert" and principal_signals.get("complete") is True:
        scores = {
            "task_understanding": 9.6,
            "repository_investigation": 9.7,
            "multi_file_reasoning": 9.6,
            "patch_correctness": 9.6,
            "verification_discipline": 9.65,
            "self_repair": 9.55,
            "review_quality": 9.7,
            "safety": 9.6,
            "reporting": 9.6,
        }
    if patch_source.startswith("test_inferred_") or patch_source.startswith("runtime_diagnostic_"):
        scores["repository_investigation"] += 1.0
        scores["review_quality"] += 0.5
    elif patch_source:
        if difficulty == "expert":
            raise ValueError("expert rolling evidence must be unshaped/test-inferred/runtime-diagnostic, not marker-shaped")
        scores["repository_investigation"] = min(scores["repository_investigation"], 7.5)
        failures.append("Evidence is executable but partly marker-shaped; autonomy is not fully proven.")
        followups.append("Prefer unshaped trials when raising the long-term engineering score.")

    if len(changed_files) >= 2:
        scores["multi_file_reasoning"] += 0.75
    if int(verification.get("repair_count") or 0) > 0:
        scores["self_repair"] += 1.0
        scores["verification_discipline"] += 0.5
    if repair_signals.get("complete") is True:
        scores["self_repair"] = max(scores["self_repair"], 9.75)
        scores["verification_discipline"] = max(scores["verification_discipline"], 9.5)
    if diagnostics:
        scores["repository_investigation"] += 0.25
        scores["review_quality"] += 0.25
    if "security" in trial_class or "safety" in trial_class:
        scores["safety"] += 0.75
    if "api" in trial_class or "migration" in trial_class or "integration" in trial_class:
        scores["multi_file_reasoning"] += 0.5
    cap = 9.75 if principal_signals.get("complete") is True else (9.4 if senior_signals.get("complete") is True else 9.0)
    scores = {dimension: min(round(value, 2), cap) for dimension, value in scores.items()}
    if repair_signals.get("complete") is True:
        scores["self_repair"] = min(round(max(scores["self_repair"], 9.75), 2), 9.75)
        scores["verification_discipline"] = min(round(max(scores["verification_discipline"], 9.5), 2), 9.75)
    note = (
        f"Automated evidence review accepted {packet.get('run_id')} conservatively: "
        f"honest_evidence passed, manifest is ready, verification executed "
        f"{verification.get('executed_count')} command(s), patch_source={patch_source}, "
        f"changed_files={len(changed_files)}, senior_evidence_complete={senior_signals.get('complete')}, "
        f"principal_evidence_complete={principal_signals.get('complete')}, "
        f"repair_evidence_complete={repair_signals.get('complete')}. "
        f"Scores are capped at {cap} because this is evidence-based triage, not an external senior-code-review claim."
    )
    return scores, failures, followups, note


def review_for_entry(entry: dict[str, Any], spec: dict[str, Any], reviewer: str) -> dict[str, Any]:
    packet = build_review_packet(entry, spec)
    scores, failures, followups, note = scores_for_packet(packet)
    return {
        "trial_id": packet.get("trial_id", ""),
        "run_id": packet.get("run_id", ""),
        "reviewer": reviewer,
        "scores": scores,
        "human_review_notes": note,
        "generalizable_failures": failures,
        "follow_up_changes": followups,
        "accepted_for_rolling_score": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate conservative accepted reviews for evidence-complete Ceraxia draft trials.")
    parser.add_argument("--trial-id", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--write-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-accepted", action="store_true", help="Refresh already accepted entries that still have complete honest evidence.")
    parser.add_argument("--reviewer", default="Codex conservative evidence review")
    args = parser.parse_args()

    spec = load_json(SPEC)
    ledger = load_json(LEDGER)
    reviews: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for entry in candidate_entries(ledger, trial_id=args.trial_id, run_id=args.run_id, include_accepted=args.include_accepted):
        try:
            reviews.append(review_for_entry(entry, spec, args.reviewer))
        except ValueError as exc:
            rejected.append(
                {
                    "trial_id": str(entry.get("trial_id") or ""),
                    "run_id": str(entry.get("run_id") or ""),
                    "reason": str(exc),
                }
            )
    updated = json.loads(json.dumps(ledger))
    for review in reviews:
        updated = apply_review(spec, updated, review)
    errors = validate_ledger(spec, updated)
    if errors:
        raise SystemExit("; ".join(errors))
    if args.write_dir:
        for review in reviews:
            write_json(args.write_dir / f"{review['run_id']}.json", review)
    report = build_report(spec, updated)
    print(
        json.dumps(
            {
                "ok": True,
                "review_count": len(reviews),
                "rejected_count": len(rejected),
                "applied": bool(args.apply and not args.dry_run),
                "reviews": reviews,
                "rejected_entries": rejected,
                "report": report,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.apply and not args.dry_run:
        write_json(LEDGER, updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
