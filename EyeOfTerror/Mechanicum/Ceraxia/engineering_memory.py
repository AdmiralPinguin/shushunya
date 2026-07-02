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
        "mandatory_checks_by_task_kind": {
            "security": ["negative boundary test or explicit blocker", "rollback trigger before mutation"],
            "api_compatibility": ["caller and contract surface review", "backward compatibility verification"],
            "bugfix": ["reproduce or explain why reproduction is blocked", "targeted verification after mutation"],
            "feature": ["scope budget", "acceptance trace", "verification command"],
            "general_code_change": ["repo survey", "pre-mutation read evidence", "review gate"],
        },
        "dangerous_modules": dangerous_modules,
        "false_success_guards": [
            "package_ok must be true before reporting done",
            "review_gate.decision and run_summary.review_decision must not be blocked",
            "implemented worker reports require verification_after_mutation_evidence",
            "failed or blocked tasks require postmortem before being counted as benchmark output",
        ],
    }
