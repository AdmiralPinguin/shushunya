#!/usr/bin/env python3
from __future__ import annotations

from typing import Any


def build_greenfield_memory_record(
    project_brief: dict[str, Any],
    dependency_report: dict[str, Any],
    verification_loop: dict[str, Any],
    greenfield_review: dict[str, Any],
) -> dict[str, Any]:
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
    return {
        "kind": "code_brigade_greenfield_memory_record",
        "contract_version": "eye-mechanicum.v1",
        "project_name": project_brief.get("project_name", ""),
        "project_type": project_brief.get("project_type", ""),
        "template_id": project_brief.get("template_id", ""),
        "stack": project_brief.get("stack", {}),
        "dependency_status": dependency_report.get("status", ""),
        "dependency_blockers": dependency_report.get("blockers", []),
        "dependency_warnings": dependency_report.get("warnings", []),
        "dependency_manager_status": dependency_report.get("manager_status", {}),
        "dependency_new_lockfiles": dependency_report.get("new_lockfiles", []),
        "verification_status": verification_loop.get("status", ""),
        "verification_stop_reason": verification_loop.get("stop_reason", ""),
        "verification_attempt_count": len(verification_loop.get("attempts", [])) if isinstance(verification_loop.get("attempts"), list) else 0,
        "repair_attempt_count": len(repair_attempts),
        "repaired_files": repaired_files,
        "review_status": greenfield_review.get("status", ""),
        "review_blockers": greenfield_review.get("blockers", []),
        "review_warnings": greenfield_review.get("warnings", []),
        "semantic_review_status": greenfield_review.get("semantic_review", {}).get("status", ""),
        "semantic_review_blockers": greenfield_review.get("semantic_review", {}).get("blockers", []),
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
        ],
    }
