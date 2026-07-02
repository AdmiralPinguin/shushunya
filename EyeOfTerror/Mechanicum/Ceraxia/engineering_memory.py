#!/usr/bin/env python3
from __future__ import annotations

from typing import Any


CONTRACT_VERSION = "eye-mechanicum.v1"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _strings(value: Any) -> list[str]:
    return [str(item) for item in _list(value) if str(item)]


def build_engineering_memory_update(
    brief: dict[str, Any],
    worker_report: dict[str, Any],
    verification_report: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    implementation_plan = _dict(worker_report.get("implementation_plan"))
    repo_evidence = _dict(brief.get("repo_survey_evidence"))
    task_kinds = _strings(brief.get("task_kinds"))
    risk_level = str(brief.get("risk_level") or "high")
    findings = _list(review.get("findings"))
    warnings = _list(review.get("warnings"))
    output_summary = _list(verification_report.get("output_summary"))
    diagnostic_signals = sorted(
        {
            str(signal)
            for row in output_summary
            if isinstance(row, dict)
            for signal in [
                "traceback" if row.get("has_traceback") else "",
                "assertion_failure" if row.get("has_assertion_failure") else "",
                "syntax_error" if row.get("has_syntax_error") else "",
                "no_tests_ran" if row.get("has_no_tests_ran") else "",
            ]
            if signal
        }
    )
    inspected_paths = [
        str(path)
        for path in [
            *_list(repo_evidence.get("candidate_files")),
            *_list(implementation_plan.get("target_files_to_inspect")),
        ]
        if str(path)
    ]
    dangerous_modules = sorted(
        {
            path
            for path in inspected_paths
            if "security" in task_kinds
            or risk_level == "high"
            or any(token in path.lower() for token in ["auth", "security", "api", "config", "migration", "schema"])
        }
    )
    finding_texts = [str(item.get("finding") or "") for item in findings if isinstance(item, dict)]
    warning_texts = [str(item.get("finding") or "") for item in warnings if isinstance(item, dict)]
    contract_trace = _dict(verification_report.get("contract_trace"))
    falsification_review = _dict(contract_trace.get("falsification_review"))
    commands_executed = _list(verification_report.get("commands_executed"))
    verification_was_executed = bool(commands_executed) or verification_report.get("status") in {"passed", "failed", "blocked"}
    falsification_concerns = _strings(falsification_review.get("concerns")) if verification_was_executed else []
    repair_queue = _dict(review.get("diagnostic_repair_queue"))
    repair_items = _list(repair_queue.get("items"))
    failure_patterns = [
        {
            "pattern": "review_blocker",
            "evidence": finding_texts,
            "required_next_check": "do not claim completion until review_gate.decision is ready or dry_run_ready",
        }
    ] if finding_texts else []
    if diagnostic_signals:
        failure_patterns.append(
            {
                "pattern": "verification_diagnostic_signal",
                "evidence": diagnostic_signals,
                "required_next_check": "classify failing command output before attempting another patch",
            }
        )
    if falsification_concerns:
        failure_patterns.append(
            {
                "pattern": "falsification_concern",
                "evidence": falsification_concerns,
                "required_next_check": "add a counterexample probe before accepting the next verification report",
            }
        )
    repair_strategy_rows = []
    for item in repair_items:
        if not isinstance(item, dict):
            continue
        classification = _dict(item.get("failure_classification"))
        signals = _strings(item.get("diagnostic_signals"))
        if "no_tests_ran" in signals:
            likely_cause = "bad_or_missing_test_oracle"
            next_hypothesis = "repair or replace verification oracle before source mutation"
        elif "assertion_failure" in signals or classification.get("type") == "behavior_regression_or_unmet_acceptance":
            likely_cause = "source_behavior_or_acceptance_gap"
            next_hypothesis = "read implicated source and test oracle, then patch source or block with evidence"
        elif "syntax_error" in signals or "traceback" in signals:
            likely_cause = "runtime_or_syntax_failure"
            next_hypothesis = "read traceback target and apply minimal source repair"
        else:
            likely_cause = "unknown_failure_needs_replan"
            next_hypothesis = "return to PlanningBrigade with preserved attempt history"
        repair_strategy_rows.append(
            {
                "command": str(item.get("command") or ""),
                "failure_type": str(classification.get("type") or "unknown"),
                "diagnostic_signals": signals,
                "likely_cause": likely_cause,
                "next_hypothesis": next_hypothesis,
                "must_preserve": ["attempt_history", "verification output", "read-before-repair evidence"],
                "must_not_repeat": ["same repair signature without new evidence", "test masking", "syntax-only acceptance"],
            }
        )
    mandatory_checks_by_task_kind = {
        "security": ["negative boundary test or explicit blocker", "rollback trigger before mutation"],
        "api_compatibility": ["caller and contract surface review", "backward compatibility verification"],
        "bugfix": ["reproduce or explain why reproduction is blocked", "targeted verification after mutation"],
        "feature": ["scope budget", "acceptance trace", "verification command"],
        "general_code_change": ["repo survey", "pre-mutation read evidence", "review gate"],
    }
    reuse_plan = []
    for task_kind in task_kinds or ["general_code_change"]:
        checks = mandatory_checks_by_task_kind.get(task_kind, mandatory_checks_by_task_kind["general_code_change"])
        reuse_plan.append(
            {
                "task_kind": task_kind,
                "mandatory_checks": checks,
                "reuse_trigger": f"before planning the next {task_kind} task",
                "evidence_required": "planning_packet constraints, implementation_plan gates, and review_gate findings must show these checks were considered",
            }
        )
    return {
        "kind": "ceraxia_engineering_memory_update",
        "contract_version": CONTRACT_VERSION,
        "status": "recorded",
        "task_kinds": task_kinds,
        "risk_level": risk_level,
        "selected_strategy": str(brief.get("selected_strategy") or ""),
        "observed_failure_patterns": failure_patterns,
        "observed_warnings": warning_texts,
        "reusable_patterns": [
            "always preserve planning_department.json as an explicit handoff artifact",
            "bind CodeBrigade work packages to dependency graph and acceptance evidence before mutation",
            "treat review_gate.decision as the source of truth for completion claims",
        ],
        "mandatory_checks_by_task_kind": mandatory_checks_by_task_kind,
        "reuse_plan": reuse_plan,
        "repair_strategy_memory": {
            "status": "recorded" if repair_strategy_rows or falsification_concerns else "not_applicable",
            "rows": repair_strategy_rows,
            "falsification_concerns": falsification_concerns,
            "attempt_history_policy": [
                "preserve failed attempt signatures",
                "require new source or verification evidence before repeating a repair",
                "send repeated or maxed repairs back to PlanningBrigade",
            ],
        },
        "dangerous_modules": dangerous_modules,
        "false_success_guards": [
            "package_ok must be true before reporting done",
            "review_gate.decision and run_summary.review_decision must not be blocked",
            "implemented worker reports require verification_after_mutation_evidence",
            "failed or blocked tasks require postmortem before being counted as benchmark output",
        ],
    }
