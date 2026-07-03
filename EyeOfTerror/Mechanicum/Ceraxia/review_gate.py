from __future__ import annotations

"""Sufficiency review gate and diagnostic-repair queue (mutually coupled)."""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ceraxia_common import (  # noqa: F401
    CONTRACT_VERSION,
    DIAGNOSTIC_REPAIR_MAX_ATTEMPTS,
    EXECUTION_MODES,
    LIFECYCLE,
    PROJECT_ROOT,
    REQUIRED_RUN_ARTIFACTS,
    RUNS_ROOT,
    CeraxiaInput,
)

from planning_brigade import build_planning_packet  # noqa: E402,F401
from planning_packet_contract import validate_planning_packet as validate_planning_packet_contract  # noqa: E402,F401
from code_brigade_adapter import build_worker_report  # noqa: E402,F401
from diagnostic_repair_contract import execute_diagnostic_repair_request  # noqa: E402,F401
from engineering_memory import build_engineering_memory_update  # noqa: E402,F401
from execution_adapter import can_infer_guarded_natural_language_patch  # noqa: E402,F401
from planning_department import build_planning_department_package  # noqa: E402,F401
from verification_adapter import run_verification_commands  # noqa: E402,F401
from repo_survey import survey_repository  # noqa: E402,F401

from brief_builder import validate_planning_packet
from verification_report import (
    diagnostic_signals_from_summary_item,
    output_diagnostic_counts_from_summary,
    output_signal_counts_from_summary,
)


def failure_classification_from_repair_item(command_status: str, signals: list[str]) -> dict[str, Any]:
    if "syntax_error" in signals:
        failure_type = "syntax_error"
        severity = "high"
    elif "missing_import" in signals:
        failure_type = "missing_dependency_or_import"
        severity = "high"
    elif "assertion_failure" in signals:
        failure_type = "behavior_regression_or_unmet_acceptance"
        severity = "high"
    elif "no_tests_ran" in signals:
        failure_type = "verification_command_mismatch"
        severity = "normal"
    elif "traceback" in signals:
        failure_type = "runtime_exception"
        severity = "high"
    elif command_status == "blocked":
        failure_type = "blocked_verification_command"
        severity = "normal"
    else:
        failure_type = "failed_verification_command"
        severity = "normal"
    return {
        "type": failure_type,
        "severity": severity,
        "signals": signals,
        "command_status": command_status,
    }


def repair_hypotheses_from_failure(
    failure_classification: dict[str, Any],
    source_candidates: list[str],
    missing_imports: list[str],
) -> list[dict[str, Any]]:
    failure_type = str(failure_classification.get("type") or "")
    primary = source_candidates[0] if source_candidates else ""
    if failure_type == "missing_dependency_or_import":
        return [
            {
                "hypothesis": "map missing import to an existing or explicitly planned source module before editing",
                "source_candidates": source_candidates,
                "missing_imports": missing_imports,
                "mutation_allowed_after": "missing import maps to candidate source or allowed create path",
            }
        ]
    if failure_type == "syntax_error":
        return [
            {
                "hypothesis": "repair syntax in the traceback source without changing tests",
                "source_candidates": source_candidates,
                "mutation_allowed_after": "traceback file is read and syntax failure is reproduced",
            }
        ]
    if failure_type == "behavior_regression_or_unmet_acceptance":
        return [
            {
                "hypothesis": "repair behavior in candidate source while preserving the test oracle",
                "primary_candidate": primary,
                "source_candidates": source_candidates,
                "mutation_allowed_after": "source candidate and related caller/test evidence are read",
            }
        ]
    if failure_type == "verification_command_mismatch":
        return [
            {
                "hypothesis": "classify zero-test or runner mismatch before source mutation",
                "source_candidates": source_candidates,
                "mutation_allowed_after": "verification command is corrected or explicitly blocked",
            }
        ]
    return [
        {
            "hypothesis": "inspect diagnostic source candidates before attempting a narrow repair",
            "source_candidates": source_candidates,
            "mutation_allowed_after": "failure is mapped to a repo-local source surface",
        }
    ]


def diagnostic_repair_dedupe_key(
    command: str,
    failure_classification: dict[str, Any],
    source_candidates: list[str],
    signals: list[str],
) -> str:
    source_key = ",".join(source_candidates[:3]) if source_candidates else "unknown-source"
    signal_key = ",".join(signals) if signals else "no-signal"
    payload = "|".join(
        [
            str(failure_classification.get("type") or "unknown-failure"),
            command.strip(),
            source_key,
            signal_key,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_diagnostic_repair_queue(
    brief: dict[str, Any],
    verification_report: dict[str, Any],
    worker_report: dict[str, Any],
) -> dict[str, Any]:
    repair_plan = brief.get("diagnostic_repair_plan") if isinstance(brief.get("diagnostic_repair_plan"), dict) else {}
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    output_summary = verification_report.get("output_summary") if isinstance(verification_report.get("output_summary"), list) else []
    read_before_repair = repair_plan.get("read_before_repair") if isinstance(repair_plan.get("read_before_repair"), list) else []
    stop_conditions = repair_plan.get("stop_conditions") if isinstance(repair_plan.get("stop_conditions"), list) else []
    repair_evidence = repair_plan.get("repair_evidence_required") if isinstance(repair_plan.get("repair_evidence_required"), list) else []
    default_targets = implementation_plan.get("target_files_to_inspect") if isinstance(implementation_plan.get("target_files_to_inspect"), list) else []
    if not default_targets:
        repo_evidence = brief.get("repo_survey_evidence") if isinstance(brief.get("repo_survey_evidence"), dict) else {}
        default_targets = repo_evidence.get("candidate_files") if isinstance(repo_evidence.get("candidate_files"), list) else []
    surface_matrix = brief.get("surface_verification_matrix") if isinstance(brief.get("surface_verification_matrix"), dict) else {}
    surface_rows = surface_matrix.get("rows") if isinstance(surface_matrix.get("rows"), list) else []
    surface_evidence = surface_evidence_rows(surface_rows, verification_report)
    package_matrix = brief.get("surface_package_matrix") if isinstance(brief.get("surface_package_matrix"), dict) else {}
    package_rows = package_matrix.get("rows") if isinstance(package_matrix.get("rows"), list) else []
    items: list[dict[str, Any]] = []
    for item in output_summary:
        if not isinstance(item, dict):
            continue
        command_status = str(item.get("status") or "")
        signals = diagnostic_signals_from_summary_item(item)
        if command_status not in {"failed", "blocked"} and not signals:
            continue
        traceback_files = item.get("traceback_files") if isinstance(item.get("traceback_files"), list) else []
        missing_imports = item.get("missing_imports") if isinstance(item.get("missing_imports"), list) else []
        concrete_read_targets = [str(path) for path in traceback_files if isinstance(path, str)]
        if not concrete_read_targets:
            concrete_read_targets = [str(path) for path in default_targets if isinstance(path, str)]
        source_candidates = [
            str(path).split(":", 1)[0]
            for path in concrete_read_targets
            if isinstance(path, str) and path
        ]
        command = str(item.get("command") or "")
        impacted_surfaces = [
            str(row.get("surface") or "")
            for row in surface_evidence
            if isinstance(row, dict) and command in (row.get("matched_commands") if isinstance(row.get("matched_commands"), list) else [])
        ]
        if not impacted_surfaces and command_status in {"failed", "blocked"}:
            impacted_surfaces = [
                str(row.get("surface") or "")
                for row in surface_evidence
                if isinstance(row, dict) and row.get("status") in {"failed", "blocked"}
            ]
        package_ids = sorted(
            {
                str(package_id)
                for row in package_rows
                if isinstance(row, dict) and str(row.get("surface") or "") in impacted_surfaces
                for package_id in (row.get("package_ids") if isinstance(row.get("package_ids"), list) else [])
                if isinstance(package_id, str)
            }
        )
        failure_classification = failure_classification_from_repair_item(command_status, signals)
        max_attempts = int(repair_plan.get("max_repair_attempts", DIAGNOSTIC_REPAIR_MAX_ATTEMPTS) or DIAGNOSTIC_REPAIR_MAX_ATTEMPTS)
        dedupe_key = diagnostic_repair_dedupe_key(command, failure_classification, source_candidates, signals)
        items.append(
            {
                "command": command,
                "status": command_status,
                "priority": "high" if "traceback" in signals or "syntax_error" in signals else "normal",
                "failure_classification": failure_classification,
                "diagnostic_signals": signals,
                "impacted_surfaces": impacted_surfaces,
                "package_ids": package_ids,
                "traceback_files": traceback_files,
                "missing_imports": missing_imports,
                "source_candidates": source_candidates,
                "repair_hypotheses": repair_hypotheses_from_failure(failure_classification, source_candidates, [str(item) for item in missing_imports if isinstance(item, str)]),
                "read_before_repair": read_before_repair,
                "concrete_read_targets": concrete_read_targets,
                "stop_conditions": stop_conditions,
                "repair_evidence_required": repair_evidence,
                "max_repair_attempts": max_attempts,
                "attempt_history_policy": {
                    "required": True,
                    "dedupe_key": dedupe_key,
                    "max_attempts": max_attempts,
                    "block_repeat_without_new_evidence": True,
                    "required_fields": [
                        "attempt",
                        "hypothesis",
                        "changed_files",
                        "verification_command",
                        "result",
                    ],
                },
                "replan_required_when": [
                    "same dedupe_key fails after 2 attempts",
                    "max attempts reached without passing evidence",
                    "next attempt repeats the same hypothesis without new source evidence",
                    "no source candidates remain after repo investigation",
                ],
                "escalation_policy": {
                    "status": "planning_replan_required_after_limit",
                    "escalate_to": "PlanningBrigade",
                    "after_attempts": max_attempts,
                    "requires_new_hypothesis_before_retry": True,
                },
            }
        )
    return {
        "status": "queued" if items else "empty",
        "item_count": len(items),
        "items": items,
        "source": "verification_output_diagnostics",
        "plan_present": bool(repair_plan),
        "requires_attempt_history": bool(items),
        "max_attempts_per_item": max((int(item.get("max_repair_attempts", 0)) for item in items), default=0),
        "replan_trigger_count": sum(len(item.get("replan_required_when", [])) for item in items),
        "replan_contract": {
            "target": "PlanningBrigade",
            "required_when": [
                "same repair dedupe_key or repair_signature appears in attempt_history",
                "max_repair_attempts is reached",
                "next hypothesis lacks new source or verification evidence",
            ],
            "required_output": [
                "new failure hypothesis",
                "fresh source read evidence",
                "updated package dependency and risk notes",
                "preserved attempt_history",
            ],
            "forbidden_retry_policy": [
                "do not repeat the same repair hypothesis without new evidence",
                "do not drop attempt_history between repair cycles",
                "do not convert a failed repair into success without rerun evidence",
            ],
        }
        if items
        else {},
    }


def build_diagnostic_repair_request(
    run_id: str,
    brief: dict[str, Any],
    worker_report: dict[str, Any],
    verification_report: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    queue = review.get("diagnostic_repair_queue") if isinstance(review.get("diagnostic_repair_queue"), dict) else {}
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    return {
        "kind": "ceraxia_code_brigade_diagnostic_repair_request",
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "status": "required" if queue.get("item_count", 0) else "not_required",
        "target": "CodeBrigade",
        "repo_path": brief.get("repo_path", ""),
        "task": brief.get("task", ""),
        "verification_status": verification_report.get("status", ""),
        "review_decision": review.get("decision", ""),
        "diagnostic_repair_plan": brief.get("diagnostic_repair_plan", {}) if isinstance(brief.get("diagnostic_repair_plan"), dict) else {},
        "diagnostic_repair_queue": queue,
        "attempt_history": [],
        "diagnostic_repair_replan_contract": queue.get("replan_contract", {}) if isinstance(queue.get("replan_contract"), dict) else {},
        "suggested_code_brigade_command": [
            "python3",
            "EyeOfTerror/Mechanicum/CodeBrigade/diagnostic_repair_contract.py",
            "--execute",
            "diagnostic_repair_request.json",
        ],
        "target_files_to_inspect": implementation_plan.get("target_files_to_inspect", []) if isinstance(implementation_plan.get("target_files_to_inspect"), list) else [],
        "test_files_to_preserve": implementation_plan.get("test_files_to_preserve", []) if isinstance(implementation_plan.get("test_files_to_preserve"), list) else [],
        "reverse_dependency_index": implementation_plan.get("reverse_dependency_index", {}) if isinstance(implementation_plan.get("reverse_dependency_index"), dict) else {},
        "scope_budget": implementation_plan.get("scope_budget", {}) if isinstance(implementation_plan.get("scope_budget"), dict) else {},
        "return_contract": [
            "worker_report.json with changed files, package statuses, and residual blockers",
            "verification_report.json after rerunning relevant failed commands",
            "diagnostic_summary mapped to repaired queue items",
            "replan_packet when the same repair repeats or max attempts are reached",
        ],
    }


def build_planning_feedback_request(
    run_id: str,
    packet: dict[str, Any],
    brief: dict[str, Any],
    worker_report: dict[str, Any],
    verification_report: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    findings = review.get("findings") if isinstance(review.get("findings"), list) else []
    feedback_findings = [
        item
        for item in findings
        if isinstance(item, dict)
        and any(
            needle in str(item.get("finding") or "")
            for needle in [
                "planning packet",
                "surface verification matrix",
                "surface package matrix",
                "investigation playbook",
                "change control plan",
                "acceptance trace matrix",
                "constraint trace matrix",
                "assumption register",
                "worker output contract",
                "planning department",
            ]
        )
    ]
    worker_output_sufficiency = review.get("worker_output_contract_sufficiency") if isinstance(review.get("worker_output_contract_sufficiency"), dict) else {}
    planning_department_sufficiency = review.get("planning_department_sufficiency") if isinstance(review.get("planning_department_sufficiency"), dict) else {}
    return {
        "kind": "ceraxia_planning_feedback_request",
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "status": "required" if feedback_findings else "not_required",
        "target": "PlanningBrigade",
        "source": "Ceraxia.review_gate",
        "repo_path": brief.get("repo_path", ""),
        "task": brief.get("task", packet.get("task", "")),
        "review_decision": review.get("decision", ""),
        "worker_status": worker_report.get("status", ""),
        "verification_status": verification_report.get("status", ""),
        "planning_review_decision": brief.get("planning_review_gate", {}).get("decision", "") if isinstance(brief.get("planning_review_gate"), dict) else "",
        "feedback_findings": feedback_findings,
        "worker_output_contract_sufficiency": worker_output_sufficiency,
        "planning_department_sufficiency": planning_department_sufficiency,
        "replan_focus": [
            "repair planning packet contract drift",
            "repair planning department RFC, multi-pass investigation, and CodeBrigade handoff",
            "refresh implementation brief sections before CodeBrigade mutation",
            "tighten worker-output contract when package statuses or evidence sources are missing",
            "return a new planning_packet.json and implementation_brief.json candidate",
        ],
        "required_return_artifacts": [
            "planning_packet.json",
            "planning_department.json",
            "implementation_brief.json",
            "worker_output_contract",
            "planning_review_gate",
        ],
        "suggested_planning_command": [
            "python3",
            "EyeOfTerror/Mechanicum/PlanningBrigade/planning_brigade.py",
            "--validate",
            "--input-json",
            "task.json",
        ],
    }


def output_consistency_findings(verification_report: dict[str, Any], output_summary: list[Any]) -> list[str]:
    problems: list[str] = []
    report_status = str(verification_report.get("status") or "")
    for item in output_summary:
        if not isinstance(item, dict):
            continue
        signal = str(item.get("output_signal") or "")
        command = str(item.get("command") or "<unknown command>")
        command_status = str(item.get("status") or "")
        if signal in {"failure_text", "traceback"} and command_status == "passed":
            problems.append(f"verification command reported passed but output contains {signal}: {command}")
        if signal in {"failure_text", "traceback"} and report_status == "passed":
            problems.append(f"verification report is passed but output contains {signal}: {command}")
    return problems


def meaningful_executed_commands(verification_report: dict[str, Any]) -> list[dict[str, Any]]:
    commands = verification_report.get("commands_executed") if isinstance(verification_report.get("commands_executed"), list) else []
    return [item for item in commands if isinstance(item, dict) and item.get("status") in {"passed", "failed", "blocked"}]


def command_texts(commands: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("command", "")) for item in commands if isinstance(item, dict)]


def commands_matching_surface(surface: str, commands: list[dict[str, Any]], negative_tests: list[Any]) -> list[str]:
    texts = command_texts(commands)
    if surface == "source_behavior":
        return [command for command in texts if "py_compile" in command or "pytest" in command or "test" in command or "unittest" in command]
    if surface == "test_surface":
        return [command for command in texts if "pytest" in command or "test" in command or "unittest" in command]
    if surface in {"public_api_contract", "security_boundary", "data_compatibility", "concurrency_runtime", "runtime_configuration"} and negative_tests:
        return [command for command in texts if "pytest" in command or "test" in command or "unittest" in command]
    return []


def output_summary_for_commands(output_summary: list[Any], commands: list[str]) -> list[dict[str, Any]]:
    command_set = set(commands)
    return [
        item
        for item in output_summary
        if isinstance(item, dict) and str(item.get("command") or "") in command_set
    ]


def output_signal_counts_for_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        signal = str(row.get("output_signal") or "unknown")
        counts[signal] = counts.get(signal, 0) + 1
    return counts


def output_diagnostic_counts_for_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "traceback": sum(1 for row in rows if row.get("has_traceback")),
        "assertion_failure": sum(1 for row in rows if row.get("has_assertion_failure")),
        "syntax_error": sum(1 for row in rows if row.get("has_syntax_error")),
        "no_tests_ran": sum(1 for row in rows if row.get("has_no_tests_ran")),
        "missing_import": sum(1 for row in rows if row.get("missing_imports")),
    }


def output_rows_have_failure_semantics(rows: list[dict[str, Any]]) -> bool:
    for row in rows:
        signal = str(row.get("output_signal") or "")
        if signal in {"failure_text", "traceback"}:
            return True
        if row.get("has_traceback") or row.get("has_assertion_failure") or row.get("has_syntax_error") or row.get("has_no_tests_ran") or row.get("missing_imports"):
            return True
    return False


def has_test_command(commands: list[dict[str, Any]]) -> bool:
    return any("pytest" in command or "test" in command or "unittest" in command for command in command_texts(commands))


def has_source_command(commands: list[dict[str, Any]]) -> bool:
    return any("py_compile" in command or "pytest" in command or "test" in command or "unittest" in command for command in command_texts(commands))


def surface_evidence_rows(surface_rows: list[Any], verification_report: dict[str, Any]) -> list[dict[str, Any]]:
    planned_commands = verification_report.get("commands_planned") if isinstance(verification_report.get("commands_planned"), list) else []
    executed_commands = meaningful_executed_commands(verification_report)
    output_summary = verification_report.get("output_summary") if isinstance(verification_report.get("output_summary"), list) else []
    negative_tests = verification_report.get("negative_tests_required") if isinstance(verification_report.get("negative_tests_required"), list) else []
    verification_status = str(verification_report.get("status", ""))
    rows: list[dict[str, Any]] = []
    for surface_row in surface_rows:
        if not isinstance(surface_row, dict):
            continue
        surface = str(surface_row.get("surface", ""))
        blockers = surface_row.get("blockers") if isinstance(surface_row.get("blockers"), list) else []
        if blockers:
            status = "blocked"
            reason = "surface planning row has blockers"
            matched_commands: list[str] = []
        elif verification_status in {"failed", "blocked"}:
            status = verification_status
            reason = f"verification report status is {verification_status}"
            matched_commands = commands_matching_surface(surface, executed_commands, negative_tests)
        elif not planned_commands:
            status = "missing"
            reason = "no verification command is planned"
            matched_commands = []
        elif not executed_commands:
            status = "planned_only"
            reason = "verification is planned but not executed"
            matched_commands = []
        elif surface == "source_behavior" and has_source_command(executed_commands):
            status = "executed"
            reason = "source command executed"
            matched_commands = commands_matching_surface(surface, executed_commands, negative_tests)
        elif surface == "test_surface" and has_test_command(executed_commands):
            status = "executed"
            reason = "test command executed"
            matched_commands = commands_matching_surface(surface, executed_commands, negative_tests)
        elif surface in {"public_api_contract", "security_boundary", "data_compatibility", "concurrency_runtime", "runtime_configuration"}:
            if negative_tests and has_test_command(executed_commands):
                status = "executed"
                reason = "negative or compatibility test command executed"
                matched_commands = commands_matching_surface(surface, executed_commands, negative_tests)
            else:
                status = "partial"
                reason = "executed commands do not directly prove this high-risk surface"
                matched_commands = []
        elif executed_commands:
            status = "partial"
            reason = "some verification executed, but this surface has no direct evidence"
            matched_commands = []
        else:
            status = "planned_only"
            reason = "verification is planned but not executed"
            matched_commands = []
        matched_output_rows = output_summary_for_commands(output_summary, matched_commands)
        if matched_output_rows and output_rows_have_failure_semantics(matched_output_rows):
            status = "failed"
            reason = "matched verification output contains failure or diagnostic signals"
        rows.append(
            {
                "surface": surface,
                "status": status,
                "reason": reason,
                "matched_commands": matched_commands,
                "matched_output_signal_counts": output_signal_counts_for_rows(matched_output_rows),
                "matched_output_diagnostic_counts": output_diagnostic_counts_for_rows(matched_output_rows),
                "covered_by": surface_row.get("covered_by", []) if isinstance(surface_row.get("covered_by"), list) else [],
                "evidence_needed": surface_row.get("evidence_needed", []) if isinstance(surface_row.get("evidence_needed"), list) else [],
            }
        )
    return rows


def surface_status_from_rows(rows: list[dict[str, Any]]) -> str:
    statuses = {row.get("status") for row in rows}
    if not rows:
        return "missing"
    if "blocked" in statuses:
        return "blocked"
    if "failed" in statuses:
        return "failed"
    if statuses == {"executed"}:
        return "executed"
    if "executed" in statuses or "partial" in statuses:
        return "partial"
    if "missing" in statuses:
        return "missing"
    return "planned_only"


def investigation_sufficiency_from_worker(worker_report: dict[str, Any]) -> dict[str, Any]:
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    read_stages = implementation_plan.get("investigation_read_stages") if isinstance(implementation_plan.get("investigation_read_stages"), list) else []
    evidence_questions = (
        implementation_plan.get("investigation_evidence_questions")
        if isinstance(implementation_plan.get("investigation_evidence_questions"), list)
        else []
    )
    mutation_blockers = (
        implementation_plan.get("investigation_mutation_blockers")
        if isinstance(implementation_plan.get("investigation_mutation_blockers"), list)
        else []
    )
    replan_triggers = (
        implementation_plan.get("investigation_replan_triggers")
        if isinstance(implementation_plan.get("investigation_replan_triggers"), list)
        else []
    )
    blockers: list[str] = []
    if len(read_stages) < 5:
        blockers.append("investigation playbook has fewer than five read stages")
    if not all(isinstance(stage, dict) and stage.get("stage") and stage.get("must_collect") for stage in read_stages):
        blockers.append("investigation playbook read stages are incomplete")
    if len(evidence_questions) < 4:
        blockers.append("investigation playbook has too few evidence questions")
    if len(mutation_blockers) < 3:
        blockers.append("investigation playbook has too few mutation blockers")
    if len(replan_triggers) < 3:
        blockers.append("investigation playbook has too few replan triggers")
    return {
        "status": "complete" if not blockers else "blocked",
        "read_stage_count": len(read_stages),
        "evidence_question_count": len(evidence_questions),
        "mutation_blocker_count": len(mutation_blockers),
        "replan_trigger_count": len(replan_triggers),
        "first_stage": str(read_stages[0].get("stage") or "") if read_stages and isinstance(read_stages[0], dict) else "",
        "blockers": blockers,
    }


def change_control_sufficiency_from_worker(worker_report: dict[str, Any]) -> dict[str, Any]:
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    allowed_intents = implementation_plan.get("change_allowed_intents") if isinstance(implementation_plan.get("change_allowed_intents"), list) else []
    protected_invariants = implementation_plan.get("change_protected_invariants") if isinstance(implementation_plan.get("change_protected_invariants"), list) else []
    mutation_requires = implementation_plan.get("change_mutation_requires") if isinstance(implementation_plan.get("change_mutation_requires"), list) else []
    diff_questions = implementation_plan.get("change_diff_review_questions") if isinstance(implementation_plan.get("change_diff_review_questions"), list) else []
    rollback_triggers = implementation_plan.get("change_rollback_triggers") if isinstance(implementation_plan.get("change_rollback_triggers"), list) else []
    post_change_proofs = implementation_plan.get("change_post_change_proofs") if isinstance(implementation_plan.get("change_post_change_proofs"), list) else []
    blockers: list[str] = []
    for label, items, minimum in [
        ("allowed change intents", allowed_intents, 3),
        ("protected invariants", protected_invariants, 3),
        ("mutation requirements", mutation_requires, 4),
        ("diff review questions", diff_questions, 3),
        ("rollback triggers", rollback_triggers, 3),
        ("post-change proofs", post_change_proofs, 3),
    ]:
        if len(items) < minimum:
            blockers.append(f"change control plan has too few {label}")
    return {
        "status": "complete" if not blockers else "blocked",
        "allowed_intent_count": len(allowed_intents),
        "protected_invariant_count": len(protected_invariants),
        "mutation_requirement_count": len(mutation_requires),
        "diff_review_question_count": len(diff_questions),
        "rollback_trigger_count": len(rollback_triggers),
        "post_change_proof_count": len(post_change_proofs),
        "blockers": blockers,
    }


def acceptance_trace_sufficiency_from_worker(worker_report: dict[str, Any]) -> dict[str, Any]:
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    rows = implementation_plan.get("acceptance_trace_rows") if isinstance(implementation_plan.get("acceptance_trace_rows"), list) else []
    complete = implementation_plan.get("acceptance_trace_complete") is True
    definition_of_done_complete = implementation_plan.get("definition_of_done_trace_complete") is True
    definition_of_done_count = implementation_plan.get("definition_of_done_count")
    traced_definition_of_done_count = implementation_plan.get("traced_definition_of_done_count")
    missing_definition_of_done = implementation_plan.get("missing_definition_of_done") if isinstance(implementation_plan.get("missing_definition_of_done"), list) else []
    blockers: list[str] = []
    if not rows:
        blockers.append("acceptance trace matrix has no rows")
    if not complete:
        blockers.append("acceptance trace matrix is not complete")
    if not definition_of_done_complete:
        blockers.append("definition_of_done trace is not complete")
    if not isinstance(definition_of_done_count, int) or definition_of_done_count < 1:
        blockers.append("definition_of_done_count is missing")
        definition_of_done_count = 0
    if not isinstance(traced_definition_of_done_count, int):
        blockers.append("traced_definition_of_done_count is missing")
        traced_definition_of_done_count = 0
    if traced_definition_of_done_count < definition_of_done_count:
        blockers.append("not every definition_of_done item is traced")
    if missing_definition_of_done:
        blockers.append("missing definition_of_done items: " + ", ".join(str(item) for item in missing_definition_of_done))
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            blockers.append(f"acceptance trace row {index} is not an object")
            continue
        if not row.get("requirement"):
            blockers.append(f"acceptance trace row {index} has no requirement")
        if not isinstance(row.get("planned_evidence"), list) or not row.get("planned_evidence"):
            blockers.append(f"acceptance trace row {index} has no planned evidence")
        if not isinstance(row.get("package_ids"), list) or not row.get("package_ids"):
            blockers.append(f"acceptance trace row {index} has no package ids")
    return {
        "status": "complete" if not blockers else "blocked",
        "row_count": len(rows),
        "blocked_row_count": sum(1 for row in rows if isinstance(row, dict) and row.get("status") == "blocked"),
        "complete": complete,
        "definition_of_done_complete": definition_of_done_complete,
        "definition_of_done_count": definition_of_done_count,
        "traced_definition_of_done_count": traced_definition_of_done_count,
        "missing_definition_of_done": missing_definition_of_done,
        "blockers": blockers,
    }


def constraint_trace_sufficiency_from_worker(worker_report: dict[str, Any]) -> dict[str, Any]:
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    rows = implementation_plan.get("constraint_trace_rows") if isinstance(implementation_plan.get("constraint_trace_rows"), list) else []
    complete = implementation_plan.get("constraint_trace_complete") is True
    blockers: list[str] = []
    if not rows:
        blockers.append("constraint trace matrix has no rows")
    if not complete:
        blockers.append("constraint trace matrix is not complete")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            blockers.append(f"constraint trace row {index} is not an object")
            continue
        if not row.get("constraint"):
            blockers.append(f"constraint trace row {index} has no constraint")
        if not isinstance(row.get("planned_evidence"), list) or not row.get("planned_evidence"):
            blockers.append(f"constraint trace row {index} has no planned evidence")
        if not isinstance(row.get("package_ids"), list) or not row.get("package_ids"):
            blockers.append(f"constraint trace row {index} has no package ids")
    return {
        "status": "complete" if not blockers else "blocked",
        "row_count": len(rows),
        "blocked_row_count": sum(1 for row in rows if isinstance(row, dict) and row.get("status") == "blocked"),
        "complete": complete,
        "blockers": blockers,
    }


def assumption_sufficiency_from_worker(worker_report: dict[str, Any]) -> dict[str, Any]:
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    rows = implementation_plan.get("assumption_rows") if isinstance(implementation_plan.get("assumption_rows"), list) else []
    replan_triggers = implementation_plan.get("assumption_replan_triggers") if isinstance(implementation_plan.get("assumption_replan_triggers"), list) else []
    blockers: list[str] = []
    if len(rows) < 3:
        blockers.append("assumption register has fewer than three assumptions")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            blockers.append(f"assumption row {index} is not an object")
            continue
        for key in ("id", "assumption", "risk_if_false", "validation_source", "blocks_when_false", "owner"):
            if key not in row:
                blockers.append(f"assumption row {index} missing {key}")
    if len(replan_triggers) < 3:
        blockers.append("assumption register has too few replan triggers")
    return {
        "status": "complete" if not blockers else "blocked",
        "assumption_count": len(rows),
        "replan_trigger_count": len(replan_triggers),
        "blockers": blockers,
    }


def worker_output_contract_sufficiency_from_worker(worker_report: dict[str, Any]) -> dict[str, Any]:
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    output_contract = implementation_plan.get("worker_output_contract") if isinstance(implementation_plan.get("worker_output_contract"), dict) else {}
    package_statuses = worker_report.get("work_package_statuses") if isinstance(worker_report.get("work_package_statuses"), list) else []
    status_by_package = {
        str(row.get("package_id") or ""): row
        for row in package_statuses
        if isinstance(row, dict) and row.get("package_id")
    }
    required_packages = output_contract.get("required_package_statuses") if isinstance(output_contract.get("required_package_statuses"), list) else []
    contract_rows = output_contract.get("package_result_contract") if isinstance(output_contract.get("package_result_contract"), list) else []
    allowed_statuses = {"planned", "implemented", "blocked"}
    blockers: list[str] = []
    if not output_contract:
        blockers.append("worker output contract is missing from implementation plan")
    if len(output_contract.get("required_reports", []) if isinstance(output_contract.get("required_reports"), list) else []) < 3:
        blockers.append("worker output contract does not name required reports")
    for package_id in [str(item) for item in required_packages if str(item)]:
        status_row = status_by_package.get(package_id)
        if not status_row:
            blockers.append(f"worker output contract required package status is missing: {package_id}")
            continue
        if status_row.get("status") not in allowed_statuses:
            blockers.append(f"worker output contract package has invalid status: {package_id}")
        if not status_row.get("evidence_source"):
            blockers.append(f"worker output contract package has no evidence source: {package_id}")
        if status_row.get("status") == "blocked" and not worker_report.get("notes"):
            blockers.append(f"blocked package has no worker_report notes: {package_id}")
    contract_package_ids = [
        str(row.get("package_id") or "")
        for row in contract_rows
        if isinstance(row, dict) and row.get("package_id")
    ]
    rows_with_acceptance_requirements = 0
    for row in contract_rows:
        if not isinstance(row, dict):
            blockers.append("worker output contract package row is not an object")
            continue
        package_id = str(row.get("package_id") or "<unknown>")
        requirements = row.get("acceptance_requirements")
        if isinstance(requirements, list) and requirements:
            rows_with_acceptance_requirements += 1
        else:
            blockers.append(f"worker output contract package has no acceptance requirements: {package_id}")
        if not isinstance(row.get("acceptance_evidence"), list) or not row.get("acceptance_evidence"):
            blockers.append(f"worker output contract package has no acceptance evidence: {package_id}")
    if sorted(contract_package_ids) != sorted(str(item) for item in required_packages if str(item)):
        blockers.append("worker output contract package rows do not match required package statuses")
    return {
        "status": "complete" if not blockers else "blocked",
        "required_package_count": len(required_packages),
        "reported_package_count": len(package_statuses),
        "contract_row_count": len(contract_rows),
        "rows_with_acceptance_requirements": rows_with_acceptance_requirements,
        "blockers": blockers,
    }


def planning_department_sufficiency_from_worker(worker_report: dict[str, Any]) -> dict[str, Any]:
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    roles = implementation_plan.get("planning_department_roles") if isinstance(implementation_plan.get("planning_department_roles"), list) else []
    phases = implementation_plan.get("multi_pass_investigation_phases") if isinstance(implementation_plan.get("multi_pass_investigation_phases"), list) else []
    handoff = (
        implementation_plan.get("planning_department_work_package_handoff")
        if isinstance(implementation_plan.get("planning_department_work_package_handoff"), dict)
        else {}
    )
    brigade_handoff = (
        implementation_plan.get("brigade_handoff_contract")
        if isinstance(implementation_plan.get("brigade_handoff_contract"), dict)
        else {}
    )
    required_phase_ids = {
        "project_map",
        "dependency_public_api_map",
        "test_ci_manifest_map",
        "targeted_pre_mutation_reads",
    }
    phase_ids = {
        str(phase.get("id") or "")
        for phase in phases
        if isinstance(phase, dict)
    }
    packages = handoff.get("packages") if isinstance(handoff.get("packages"), list) else []
    blockers: list[str] = []
    if implementation_plan.get("planning_department_status") != "ready_for_code_brigade":
        blockers.append("planning department is not ready for CodeBrigade")
    if len(roles) < 5:
        blockers.append("planning department has fewer than five specialist roles")
    if implementation_plan.get("engineering_rfc_status") != "accepted_for_code_brigade_handoff":
        blockers.append("engineering RFC/ADR is not accepted for CodeBrigade handoff")
    missing_phases = sorted(required_phase_ids - phase_ids)
    if implementation_plan.get("multi_pass_investigation_status") != "complete":
        blockers.append("multi-pass repository investigation is not complete")
    if missing_phases:
        blockers.append("multi-pass repository investigation is missing phases: " + ", ".join(missing_phases))
    for phase in phases:
        if not isinstance(phase, dict):
            blockers.append("multi-pass repository investigation phase is not an object")
            continue
        if not phase.get("required_before_mutation"):
            blockers.append(f"multi-pass phase is not required before mutation: {phase.get('id')}")
    if handoff.get("status") != "ready":
        blockers.append("planning department CodeBrigade work package handoff is not ready")
    expected_handoff_roles = {
        "Ceraxia",
        "PlanningBrigade",
        "RepoSurveyor",
        "CodeBrigade",
        "Verifier",
        "Reviewer",
        "RepairStrategist",
    }
    handoff_role_names = {
        str(role.get("name") or "")
        for role in (brigade_handoff.get("roles") if isinstance(brigade_handoff.get("roles"), list) else [])
        if isinstance(role, dict)
    }
    if brigade_handoff.get("status") != "ready":
        blockers.append("planning department brigade handoff contract is not ready")
    missing_handoff_roles = sorted(expected_handoff_roles - handoff_role_names)
    if missing_handoff_roles:
        blockers.append("planning department brigade handoff contract is missing roles: " + ", ".join(missing_handoff_roles))
    for role in brigade_handoff.get("roles", []) if isinstance(brigade_handoff.get("roles"), list) else []:
        if not isinstance(role, dict):
            blockers.append("planning department brigade handoff role is not an object")
            continue
        if not role.get("inputs") or not role.get("outputs") or not role.get("acceptance_gate"):
            blockers.append(f"planning department brigade handoff role has incomplete contract: {role.get('name', '<unknown>')}")
    if not packages:
        blockers.append("planning department handoff has no work packages")
    for package in packages:
        if not isinstance(package, dict):
            blockers.append("planning department handoff package is not an object")
            continue
        package_id = str(package.get("id") or "<unknown>")
        if not package.get("acceptance_requirements"):
            blockers.append(f"planning department package has no acceptance requirements: {package_id}")
        if not isinstance(package.get("depends_on"), list):
            blockers.append(f"planning department package has no dependency list: {package_id}")
    return {
        "status": "complete" if not blockers else "blocked",
        "role_count": len(roles),
        "handoff_role_count": len(handoff_role_names),
        "phase_count": len(phases),
        "package_count": len(packages),
        "missing_phase_ids": missing_phases,
        "missing_handoff_roles": missing_handoff_roles,
        "blockers": blockers,
    }


def pre_mutation_read_sufficiency_from_worker(worker_report: dict[str, Any]) -> dict[str, Any]:
    evidence = worker_report.get("pre_mutation_read_evidence") if isinstance(worker_report.get("pre_mutation_read_evidence"), dict) else {}
    blockers: list[str] = []
    if worker_report.get("status") == "implemented":
        if not evidence:
            blockers.append("implemented worker report lacks pre_mutation_read_evidence")
        elif evidence.get("status") != "complete":
            blockers.extend(
                str(item)
                for item in evidence.get("blockers", [])
                if isinstance(item, str)
            )
            if not blockers:
                blockers.append("pre_mutation_read_evidence is not complete")
        elif int(evidence.get("recorded_read_count") or 0) <= 0 and int(evidence.get("planned_new_file_count") or 0) <= 0:
            blockers.append("pre_mutation_read_evidence has no read or planned-new-file rows")
    return {
        "status": "complete" if not blockers else "blocked",
        "evidence_status": evidence.get("status", "missing") if evidence else "missing",
        "recorded_read_count": int(evidence.get("recorded_read_count") or 0) if evidence else 0,
        "planned_new_file_count": int(evidence.get("planned_new_file_count") or 0) if evidence else 0,
        "required_read_count": int(evidence.get("required_read_count") or 0) if evidence else 0,
        "blockers": blockers,
    }


def source_mutation_scope_sufficiency_from_worker(worker_report: dict[str, Any]) -> dict[str, Any]:
    edit_plan = worker_report.get("edit_plan") if isinstance(worker_report.get("edit_plan"), dict) else {}
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    changed_files_raw = worker_report.get("changed_files") if isinstance(worker_report.get("changed_files"), list) else []
    changed_files: list[str] = []
    blockers: list[str] = []
    for item in changed_files_raw:
        if isinstance(item, str):
            path = item
        elif isinstance(item, dict) and isinstance(item.get("path"), str):
            path = item["path"]
        else:
            blockers.append("changed_files contains a non-path entry")
            continue
        if path:
            changed_files.append(path)
    allowed_files: set[str] = set()
    for key in ("target_files", "allowed_new_files"):
        values = edit_plan.get(key)
        if isinstance(values, list):
            allowed_files.update(str(item) for item in values if isinstance(item, str) and item)
    for key in ("target_files_to_inspect", "missing_path_hints"):
        values = implementation_plan.get(key)
        if isinstance(values, list):
            allowed_files.update(str(item) for item in values if isinstance(item, str) and item)
    scope_budget = implementation_plan.get("scope_budget") if isinstance(implementation_plan.get("scope_budget"), dict) else {}
    test_edit_budget = int(scope_budget.get("max_test_files_to_edit_without_explicit_user_request") or 0)
    if test_edit_budget > 0:
        values = edit_plan.get("test_files")
        if isinstance(values, list):
            allowed_files.update(str(item) for item in values if isinstance(item, str) and item)
        values = implementation_plan.get("test_files_to_preserve")
        if isinstance(values, list):
            allowed_files.update(str(item) for item in values if isinstance(item, str) and item)
    task_text = str((worker_report.get("autonomous_execution_request") or {}).get("task") or "").lower()
    explicit_test_edit_requested = bool(
        re.search(r"\b(update|change|edit|add|repair|tighten)\b.{0,80}\b(test|tests|self-test|self-tests|self test|self tests)\b", task_text)
        or re.search(r"\b(test|tests|self-test|self-tests|self test|self tests)\b.{0,80}\b(update|change|edit|add|repair|tighten)\b", task_text)
        or re.search(r"\b(drift|prove)\b", task_text)
        or re.search(r"(обнов|измени|добав|исправ).{0,80}тест", task_text)
        or re.search(r"тест.{0,80}(обнов|измени|добав|исправ|доказ)", task_text)
    )
    if explicit_test_edit_requested:
        explicit_existing = set()
        values = implementation_plan.get("existing_path_hints")
        if isinstance(values, list):
            explicit_existing.update(str(item) for item in values if isinstance(item, str) and item)
        for key in ("test_files",):
            values = edit_plan.get(key)
            if isinstance(values, list):
                allowed_files.update(str(item) for item in values if isinstance(item, str) and item in explicit_existing)
        values = implementation_plan.get("test_files_to_preserve")
        if isinstance(values, list):
            allowed_files.update(str(item) for item in values if isinstance(item, str) and item in explicit_existing)
    intent = worker_report.get("execution_intent") if isinstance(worker_report.get("execution_intent"), dict) else {}
    if intent.get("mode") == "greenfield_project_creation":
        greenfield = worker_report.get("execution_result", {}).get("greenfield_project", {}) if isinstance(worker_report.get("execution_result"), dict) else {}
        project_brief = greenfield.get("greenfield_project_brief") if isinstance(greenfield.get("greenfield_project_brief"), dict) else {}
        allowed_files.update(str(path) for path in project_brief.get("expected_files", []) if isinstance(path, str) and path)
        allowed_files.update(
            {
                "architecture_plan.json",
                "file_tree_plan.json",
                "greenfield_file_set_synthesis_report.json",
                "greenfield_memory_record.json",
                "greenfield_model_guidance_ledger.json",
                "greenfield_module_synthesis_report.json",
                "greenfield_project_brief.json",
                "greenfield_run_report.json",
                "implementation_trace.json",
                "module_contracts.json",
                "scenario_plan.json",
                "verification_plan.json",
            }
        )
    escaping_files = [
        path for path in changed_files
        if path.startswith("/") or path == ".." or path.startswith("../") or "/../" in path
    ]
    unexpected_files = sorted({path for path in changed_files if path not in allowed_files})
    if worker_report.get("status") == "implemented":
        if not changed_files:
            blockers.append("implemented worker report has no changed_files")
        if not allowed_files:
            blockers.append("implemented worker report has no planned mutation scope")
        if escaping_files:
            blockers.append("changed_files contains paths outside the repository: " + ", ".join(escaping_files[:8]))
        if unexpected_files:
            blockers.append("changed_files includes paths outside edit_plan scope: " + ", ".join(unexpected_files[:8]))
    return {
        "status": "complete" if not blockers else "blocked",
        "changed_file_count": len(changed_files),
        "allowed_file_count": len(allowed_files),
        "changed_files": changed_files,
        "allowed_files": sorted(allowed_files),
        "unexpected_files": unexpected_files,
        "escaping_files": escaping_files,
        "test_edit_budget": test_edit_budget,
        "blockers": blockers,
    }


def verification_after_mutation_sufficiency(
    worker_report: dict[str, Any],
    verification_report: dict[str, Any],
) -> dict[str, Any]:
    evidence = (
        verification_report.get("verification_after_mutation_evidence")
        if isinstance(verification_report.get("verification_after_mutation_evidence"), dict)
        else {}
    )
    commands_executed = verification_report.get("commands_executed") if isinstance(verification_report.get("commands_executed"), list) else []
    blockers: list[str] = []
    if worker_report.get("status") == "implemented":
        if not evidence:
            blockers.append("verification_after_mutation_evidence is missing")
        elif evidence.get("status") != "complete":
            blockers.extend(str(item) for item in evidence.get("blockers", []) if isinstance(item, str))
            if not blockers:
                blockers.append("verification_after_mutation_evidence is not complete")
        if not commands_executed:
            blockers.append("implemented worker report has no executed verification commands")
    return {
        "status": "complete" if not blockers else "blocked",
        "evidence_status": evidence.get("status", "missing") if evidence else "missing",
        "changed_file_count": int(evidence.get("changed_file_count") or 0) if evidence else 0,
        "commands_executed_count": len(commands_executed),
        "blockers": blockers,
    }


def review_gate(
    packet: dict[str, Any],
    brief: dict[str, Any],
    worker_report: dict[str, Any],
    verification_report: dict[str, Any],
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    commands_planned = verification_report.get("commands_planned") if isinstance(verification_report.get("commands_planned"), list) else []
    commands_executed = verification_report.get("commands_executed") if isinstance(verification_report.get("commands_executed"), list) else []
    meaningful_commands_executed = meaningful_executed_commands(verification_report)
    output_summary = verification_report.get("output_summary") if isinstance(verification_report.get("output_summary"), list) else []
    output_signal_counts = output_signal_counts_from_summary(output_summary)
    output_diagnostic_counts = output_diagnostic_counts_from_summary(output_summary)
    negative_tests = verification_report.get("negative_tests_required", [])
    contract_trace = verification_report.get("contract_trace") if isinstance(verification_report.get("contract_trace"), dict) else {}
    verification_sufficiency = {
        "risk_level": brief.get("risk_level", "high"),
        "status": "executed" if meaningful_commands_executed else ("planned_only" if commands_planned else "missing"),
        "commands_planned_count": len(commands_planned),
        "commands_executed_count": len(commands_executed),
        "meaningful_commands_executed_count": len(meaningful_commands_executed),
        "output_summary_count": len(output_summary),
        "output_signal_counts": output_signal_counts,
        "output_diagnostic_counts": output_diagnostic_counts,
        "negative_tests_required_count": len(negative_tests) if isinstance(negative_tests, list) else 0,
        "broad_verification_required": bool(verification_report.get("broad_verification_required")),
    }
    surface_matrix = brief.get("surface_verification_matrix") if isinstance(brief.get("surface_verification_matrix"), dict) else {}
    surface_rows = surface_matrix.get("rows") if isinstance(surface_matrix.get("rows"), list) else []
    surface_blockers = surface_matrix.get("blockers") if isinstance(surface_matrix.get("blockers"), list) else []
    surface_evidence = surface_evidence_rows(surface_rows, verification_report)
    surface_evidence_status_counts = {
        status: sum(1 for row in surface_evidence if row.get("status") == status)
        for status in ("executed", "partial", "planned_only", "missing", "failed", "blocked")
    }
    surface_status = "blocked" if surface_blockers else surface_status_from_rows(surface_evidence)
    surface_verification_sufficiency = {
        "planned_complete": surface_matrix.get("complete") is True,
        "status": surface_status,
        "surface_count": len(surface_rows),
        "status_counts": surface_evidence_status_counts,
        "executed_surface_count": surface_evidence_status_counts["executed"],
        "partial_surface_count": surface_evidence_status_counts["partial"],
        "missing_surface_count": surface_evidence_status_counts["missing"],
        "blocked_surface_count": surface_evidence_status_counts["blocked"],
        "blocker_count": len(surface_blockers),
        "executed_evidence": bool(meaningful_commands_executed),
        "surface_evidence": surface_evidence,
    }
    package_statuses = worker_report.get("work_package_statuses") if isinstance(worker_report.get("work_package_statuses"), list) else []
    package_status_counts = {
        status: sum(1 for item in package_statuses if isinstance(item, dict) and item.get("status") == status)
        for status in ("planned", "implemented", "blocked")
    }
    package_status_missing_evidence = [
        str(item.get("package_id") or "<unknown>")
        for item in package_statuses
        if isinstance(item, dict) and not item.get("evidence_source")
    ]
    package_status_sufficiency = {
        "package_count": len(package_statuses),
        "status_counts": package_status_counts,
        "blocked_package_ids": [
            str(item.get("package_id") or "")
            for item in package_statuses
            if isinstance(item, dict) and item.get("status") == "blocked"
        ],
        "missing_evidence_source_package_ids": package_status_missing_evidence,
    }
    surface_package_matrix = brief.get("surface_package_matrix") if isinstance(brief.get("surface_package_matrix"), dict) else {}
    surface_package_rows = surface_package_matrix.get("rows") if isinstance(surface_package_matrix.get("rows"), list) else []
    package_status_ids = {
        str(item.get("package_id") or "")
        for item in package_statuses
        if isinstance(item, dict) and item.get("package_id")
    }
    surface_package_sufficiency_rows: list[dict[str, Any]] = []
    missing_surface_package_statuses: list[str] = []
    for row in surface_package_rows:
        if not isinstance(row, dict):
            continue
        package_ids = [str(item) for item in row.get("package_ids", []) if isinstance(item, str)]
        missing_ids = [package_id for package_id in package_ids if package_id not in package_status_ids]
        missing_surface_package_statuses.extend(missing_ids)
        surface_package_sufficiency_rows.append(
            {
                "surface": str(row.get("surface") or ""),
                "package_ids": package_ids,
                "missing_status_package_ids": missing_ids,
                "blockers": row.get("blockers", []) if isinstance(row.get("blockers"), list) else [],
            }
        )
    surface_package_sufficiency = {
        "planned_complete": surface_package_matrix.get("complete") is True,
        "surface_count": len(surface_package_rows),
        "rows": surface_package_sufficiency_rows,
        "missing_status_package_ids": sorted(set(missing_surface_package_statuses)),
    }
    investigation_sufficiency = investigation_sufficiency_from_worker(worker_report)
    change_control_sufficiency = change_control_sufficiency_from_worker(worker_report)
    acceptance_trace_sufficiency = acceptance_trace_sufficiency_from_worker(worker_report)
    constraint_trace_sufficiency = constraint_trace_sufficiency_from_worker(worker_report)
    assumption_sufficiency = assumption_sufficiency_from_worker(worker_report)
    worker_output_contract_sufficiency = worker_output_contract_sufficiency_from_worker(worker_report)
    planning_department_sufficiency = planning_department_sufficiency_from_worker(worker_report)
    pre_mutation_read_sufficiency = pre_mutation_read_sufficiency_from_worker(worker_report)
    source_mutation_scope_sufficiency = source_mutation_scope_sufficiency_from_worker(worker_report)
    verification_after_mutation = verification_after_mutation_sufficiency(worker_report, verification_report)
    diagnostic_repair_queue = build_diagnostic_repair_queue(brief, verification_report, worker_report)
    for problem in validate_planning_packet(packet):
        findings.append({"severity": "blocker", "finding": problem})
    if not worker_report.get("implementation_brief_acknowledged", False):
        findings.append({"severity": "blocker", "finding": "implementation brief was not acknowledged"})
    if worker_report["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "worker report is blocked"})
    if package_status_sufficiency["blocked_package_ids"]:
        findings.append({"severity": "blocker", "finding": "work packages are blocked: " + ", ".join(package_status_sufficiency["blocked_package_ids"])})
    if package_status_sufficiency["missing_evidence_source_package_ids"]:
        findings.append({"severity": "blocker", "finding": "work packages lack evidence_source: " + ", ".join(package_status_sufficiency["missing_evidence_source_package_ids"])})
    if surface_package_matrix.get("complete") is False:
        findings.append({"severity": "blocker", "finding": "surface package matrix has blockers"})
    if surface_package_sufficiency["missing_status_package_ids"]:
        findings.append({"severity": "blocker", "finding": "surface package matrix references packages without worker status: " + ", ".join(surface_package_sufficiency["missing_status_package_ids"])})
    if investigation_sufficiency["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "investigation playbook is incomplete: " + "; ".join(investigation_sufficiency["blockers"])})
    if change_control_sufficiency["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "change control plan is incomplete: " + "; ".join(change_control_sufficiency["blockers"])})
    if acceptance_trace_sufficiency["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "acceptance trace matrix is incomplete: " + "; ".join(acceptance_trace_sufficiency["blockers"])})
    if constraint_trace_sufficiency["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "constraint trace matrix is incomplete: " + "; ".join(constraint_trace_sufficiency["blockers"])})
    if assumption_sufficiency["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "assumption register is incomplete: " + "; ".join(assumption_sufficiency["blockers"])})
    if worker_output_contract_sufficiency["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "worker output contract is incomplete: " + "; ".join(worker_output_contract_sufficiency["blockers"])})
    if planning_department_sufficiency["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "planning department handoff is incomplete: " + "; ".join(planning_department_sufficiency["blockers"])})
    if pre_mutation_read_sufficiency["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "pre-mutation read evidence is incomplete: " + "; ".join(pre_mutation_read_sufficiency["blockers"])})
    if source_mutation_scope_sufficiency["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "source mutation scope is incomplete: " + "; ".join(source_mutation_scope_sufficiency["blockers"])})
    if verification_after_mutation["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "verification-after-mutation evidence is incomplete: " + "; ".join(verification_after_mutation["blockers"])})
    if worker_report["dry_run"] and package_status_counts["planned"]:
        warnings.append({"severity": "warning", "finding": "work packages are planned but not implemented"})
    if negative_tests and verification_report["status"] not in {"planned_only", "requires_execution", "passed"}:
        findings.append({"severity": "blocker", "finding": "negative tests are missing or not planned"})
    if verification_report.get("status") in {"failed", "blocked"}:
        findings.append({"severity": "blocker", "finding": f"verification report status is {verification_report.get('status')}"})
    for problem in output_consistency_findings(verification_report, output_summary):
        findings.append({"severity": "blocker", "finding": problem})
    if verification_report.get("broad_verification_required") and not verification_report.get("commands_planned"):
        findings.append({"severity": "blocker", "finding": "broad verification is required but no commands are planned"})
    if verification_report.get("broad_verification_required") and verification_report.get("status") == "planned_only":
        warnings.append({"severity": "warning", "finding": "broad verification is planned but not executed"})
    if surface_blockers:
        findings.append({"severity": "blocker", "finding": "surface verification matrix has blockers"})
    if surface_rows and not meaningful_commands_executed:
        warnings.append({"severity": "warning", "finding": "surface verification coverage is planned but not executed"})
    if surface_status == "failed":
        findings.append({"severity": "blocker", "finding": "surface verification output contains failure semantics"})
    if surface_status == "partial":
        warnings.append({"severity": "warning", "finding": "surface verification has only partial executed evidence"})
    if brief.get("risk_level") == "high" and surface_status == "partial" and meaningful_commands_executed:
        findings.append({"severity": "blocker", "finding": "high-risk task has only partial executed surface evidence"})
    if verification_report.get("commands_executable") and not verification_report.get("commands_executed"):
        warnings.append({"severity": "warning", "finding": "executable verification commands exist but were not run"})
    if brief.get("risk_level") == "high" and not commands_executed:
        warnings.append({"severity": "warning", "finding": "high-risk task has no executed verification evidence yet"})
    if (
        contract_trace.get("requirement_count")
        and int(contract_trace.get("blocking_requirement_count") or 0) > 0
        and verification_report.get("status") == "passed"
        and (brief.get("risk_level") == "high" or verification_report.get("broad_verification_required"))
    ):
        findings.append({"severity": "blocker", "finding": "verification contract trace has unproven acceptance requirements"})
    elif contract_trace.get("requirement_count") and int(contract_trace.get("blocking_requirement_count") or 0) > 0:
        warnings.append({"severity": "warning", "finding": "verification contract trace has unproven acceptance requirements"})
    falsification_review = contract_trace.get("falsification_review") if isinstance(contract_trace.get("falsification_review"), dict) else {}
    if (
        falsification_review.get("status") == "blocked"
        and int(falsification_review.get("blocking_concern_count") or 0) > 0
        and verification_report.get("status") == "passed"
        and (brief.get("risk_level") == "high" or verification_report.get("broad_verification_required"))
    ):
        findings.append({"severity": "blocker", "finding": "verification falsification review found unresolved counterexample concerns"})
    elif falsification_review.get("status") == "blocked" and int(falsification_review.get("blocking_concern_count") or 0) > 0:
        warnings.append({"severity": "warning", "finding": "verification falsification review found unresolved counterexample concerns"})
    repo_evidence = brief.get("repo_survey_evidence") if isinstance(brief.get("repo_survey_evidence"), dict) else {}
    if repo_evidence.get("survey_truncated"):
        warnings.append({"severity": "warning", "finding": "repository survey reached file limit; coverage is partial"})
    if repo_evidence.get("python_symbols_truncated"):
        warnings.append({"severity": "warning", "finding": "python symbol survey reached file limit; dependency evidence is partial"})
    if repo_evidence.get("source_summaries_truncated"):
        warnings.append({"severity": "warning", "finding": "source summary survey reached file limit; multi-language evidence is partial"})
    if any("hardcode" in approach for approach in brief.get("forbidden_approaches", [])):
        hardcode_rejected = any(
            option.get("name") == "hardcode" and option.get("decision") == "reject"
            for option in packet.get("design_options", {}).get("options", [])
        )
        if not hardcode_rejected:
            findings.append({"severity": "blocker", "finding": "hardcode rejection is missing"})
    decision = "blocked" if any(item["severity"] == "blocker" for item in findings) else "ready"
    if worker_report["dry_run"] and decision == "ready":
        decision = "dry_run_ready"
    return {
        "kind": "ceraxia_review_gate",
        "decision": decision,
        "findings": findings,
        "warnings": warnings,
        "verification_sufficiency": verification_sufficiency,
        "surface_verification_sufficiency": surface_verification_sufficiency,
        "package_status_sufficiency": package_status_sufficiency,
        "surface_package_sufficiency": surface_package_sufficiency,
        "investigation_sufficiency": investigation_sufficiency,
        "change_control_sufficiency": change_control_sufficiency,
        "acceptance_trace_sufficiency": acceptance_trace_sufficiency,
        "constraint_trace_sufficiency": constraint_trace_sufficiency,
        "assumption_sufficiency": assumption_sufficiency,
        "worker_output_contract_sufficiency": worker_output_contract_sufficiency,
        "planning_department_sufficiency": planning_department_sufficiency,
        "pre_mutation_read_sufficiency": pre_mutation_read_sufficiency,
        "source_mutation_scope_sufficiency": source_mutation_scope_sufficiency,
        "verification_after_mutation_sufficiency": verification_after_mutation,
        "diagnostic_repair_queue": diagnostic_repair_queue,
        "checked_against": [
            "planning packet completeness",
            "strategy approval",
            "scope control",
            "verification strategy",
            "surface verification coverage",
            "surface package ownership",
            "work package status coverage",
            "investigation playbook coverage",
            "change control plan coverage",
            "acceptance traceability coverage",
            "constraint traceability coverage",
            "assumption register coverage",
            "worker output contract coverage",
            "planning department RFC and multi-pass handoff coverage",
            "pre-mutation read evidence",
            "source mutation scope",
            "verification after final mutation",
            "worker report honesty",
        ],
    }
