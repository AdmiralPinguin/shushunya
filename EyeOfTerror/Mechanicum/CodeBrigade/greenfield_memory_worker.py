#!/usr/bin/env python3
from __future__ import annotations

from typing import Any


def _feature_ids(project_brief: dict[str, Any]) -> list[str]:
    return [
        str(feature.get("id") or "")
        for feature in (project_brief.get("acceptance_features", []) if isinstance(project_brief.get("acceptance_features"), list) else [])
        if isinstance(feature, dict) and feature.get("id")
    ]


def _verification_results(verification_loop: dict[str, Any]) -> list[dict[str, str]]:
    final_verification = verification_loop.get("final_verification", {})
    rows = final_verification.get("results", []) if isinstance(final_verification, dict) else []
    return [
        {
            "command": str(row.get("command") or ""),
            "status": str(row.get("status") or ""),
        }
        for row in rows
        if isinstance(row, dict) and row.get("command")
    ]


def _definition_of_done_status(project_brief: dict[str, Any], verification_loop: dict[str, Any], greenfield_review: dict[str, Any]) -> dict[str, Any]:
    items = [str(item) for item in project_brief.get("definition_of_done", []) if isinstance(item, str)]
    passed = verification_loop.get("status") == "passed" and greenfield_review.get("status") == "passed"
    return {
        "status": "passed" if passed else "blocked",
        "items": [{"item": item, "status": "passed" if passed else "needs_review"} for item in items],
    }


def build_greenfield_memory_record(
    project_brief: dict[str, Any],
    dependency_report: dict[str, Any],
    verification_loop: dict[str, Any],
    greenfield_review: dict[str, Any],
    implementation_synthesis_report: dict[str, Any] | None = None,
    file_set_synthesis_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    implementation_synthesis_report = implementation_synthesis_report or {}
    file_set_synthesis_report = file_set_synthesis_report or {}
    repair_attempts = [
        attempt.get("repair_execution", {})
        for attempt in verification_loop.get("attempts", [])
        if isinstance(attempt, dict) and isinstance(attempt.get("repair_execution"), dict)
    ]
    repaired_files = [
        str(row.get("path") or "")
        for attempt in repair_attempts
        for row in (attempt.get("repaired_files", []) if isinstance(attempt.get("repaired_files"), list) else [])
        if isinstance(row, dict) and row.get("path")
    ]
    synthesis_repair_attempts = [
        attempt
        for attempt in repair_attempts
        if attempt.get("repair_strategy") == "module_synthesis_repair"
    ]
    feature_ids = _feature_ids(project_brief)
    dod_status = _definition_of_done_status(project_brief, verification_loop, greenfield_review)
    return {
        "kind": "code_brigade_greenfield_memory_record",
        "contract_version": "eye-mechanicum.v1",
        "project_name": project_brief.get("project_name", ""),
        "project_type": project_brief.get("project_type", ""),
        "template_id": project_brief.get("template_id", ""),
        "stack": project_brief.get("stack", {}),
        "acceptance_feature_ids": feature_ids,
        "acceptance_feature_count": len(feature_ids),
        "acceptance_feature_coverage": {
            "status": "covered" if feature_ids and greenfield_review.get("status") == "passed" else "not_applicable" if not feature_ids else "needs_review",
            "feature_ids": feature_ids,
            "implementation_strategy": project_brief.get("implementation_feature_report", {}).get("implementation_strategy", ""),
        },
        "implementation_synthesis_status": implementation_synthesis_report.get("status", ""),
        "implementation_synthesis_applied_count": implementation_synthesis_report.get("applied_count", 0),
        "implementation_synthesis_changed_files": implementation_synthesis_report.get("changed_files", []),
        "implementation_synthesis_model_unavailable_count": implementation_synthesis_report.get("model_unavailable_count", 0),
        "implementation_synthesis_blocked_count": implementation_synthesis_report.get("blocked_count", 0),
        "file_set_synthesis_status": file_set_synthesis_report.get("status", ""),
        "file_set_synthesis_changed_files": file_set_synthesis_report.get("changed_files", []),
        "file_set_synthesis_changed_file_count": file_set_synthesis_report.get("changed_file_count", 0),
        "definition_of_done_status": dod_status,
        "dependency_status": dependency_report.get("status", ""),
        "dependency_blockers": dependency_report.get("blockers", []),
        "dependency_warnings": dependency_report.get("warnings", []),
        "dependency_manager_status": dependency_report.get("manager_status", {}),
        "dependency_new_lockfiles": dependency_report.get("new_lockfiles", []),
        "verification_status": verification_loop.get("status", ""),
        "verification_stop_reason": verification_loop.get("stop_reason", ""),
        "verification_stop_condition_evidence": verification_loop.get("stop_condition_evidence", {}),
        "verification_attempt_count": len(verification_loop.get("attempts", [])) if isinstance(verification_loop.get("attempts"), list) else 0,
        "repair_attempt_count": len(repair_attempts),
        "synthesis_repair_attempt_count": len(synthesis_repair_attempts),
        "repaired_files": repaired_files,
        "review_status": greenfield_review.get("status", ""),
        "review_blockers": greenfield_review.get("blockers", []),
        "review_warnings": greenfield_review.get("warnings", []),
        "semantic_review_status": greenfield_review.get("semantic_review", {}).get("status", ""),
        "semantic_review_blockers": greenfield_review.get("semantic_review", {}).get("blockers", []),
        "verification_results": _verification_results(verification_loop),
        "commands": {
            "install": project_brief.get("dependency_plan", {}).get("install_commands", []),
            "run": project_brief.get("run_commands", []),
            "verification": project_brief.get("verification_commands", []),
        },
        "template_failure_fixes": project_brief.get("template_contract", {}).get("common_failure_fixes", []),
        "reusable_learnings": [
            "preserve greenfield workspace marker before writing generated files",
            "keep README commands identical to run_commands and verification_commands",
            "keep implementation modules and tests separate for non-trivial projects",
            "treat module synthesis as applied only when model JSON passes path, requirement, test, and placeholder validation",
            "prefer coordinated file-set synthesis when source and tests must evolve together",
        ],
    }
