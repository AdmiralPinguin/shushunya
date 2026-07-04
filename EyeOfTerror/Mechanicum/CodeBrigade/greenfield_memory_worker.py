#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
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
    dod_review = greenfield_review.get("definition_of_done_review") if isinstance(greenfield_review.get("definition_of_done_review"), dict) else {}
    if dod_review:
        rows = dod_review.get("rows") if isinstance(dod_review.get("rows"), list) else []
        return {
            "status": str(dod_review.get("status") or "blocked"),
            "items": [
                {
                    "item": str(row.get("item") or ""),
                    "status": str(row.get("status") or ""),
                    "evidence": row.get("evidence", []) if isinstance(row.get("evidence"), list) else [],
                    "missing_evidence": row.get("missing_evidence", []) if isinstance(row.get("missing_evidence"), list) else [],
                }
                for row in rows
                if isinstance(row, dict)
            ],
            "passed_count": dod_review.get("passed_count", 0),
            "blocked_count": dod_review.get("blocked_count", 0),
        }
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
    scenario_review = greenfield_review.get("scenario_review", {}) if isinstance(greenfield_review.get("scenario_review"), dict) else {}
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
        "file_set_synthesis_semantic_quality_status": file_set_synthesis_report.get("semantic_quality_status", ""),
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
        "scenario_review_status": scenario_review.get("status", ""),
        "scenario_count": scenario_review.get("scenario_count", 0),
        "scenario_blocked_count": scenario_review.get("blocked_count", 0),
        "scenario_review_blockers": scenario_review.get("blockers", []),
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
            "reject generated source/test sets when semantic quality gates identify weak source or assertionless tests",
            "treat scenario_plan as the user-workflow contract and block review when source/test evidence misses required behavior markers",
        ],
    }


def build_greenfield_memory_index(records: list[dict[str, Any]], *, max_records: int = 20) -> dict[str, Any]:
    bounded = [record for record in records if isinstance(record, dict)][-max_records:]
    template_counter = Counter(str(record.get("template_id") or "") for record in bounded if record.get("template_id"))
    status_counter = Counter(str(record.get("review_status") or record.get("verification_status") or "") for record in bounded)
    blockers = Counter(
        str(blocker)
        for record in bounded
        for blocker in (record.get("review_blockers", []) if isinstance(record.get("review_blockers"), list) else [])
        if str(blocker).strip()
    )
    dependency_blockers = Counter(
        str(blocker)
        for record in bounded
        for blocker in (record.get("dependency_blockers", []) if isinstance(record.get("dependency_blockers"), list) else [])
        if str(blocker).strip()
    )
    learnings = list(
        dict.fromkeys(
            str(learning)
            for record in bounded
            for learning in (record.get("reusable_learnings", []) if isinstance(record.get("reusable_learnings"), list) else [])
            if str(learning).strip()
        )
    )
    return {
        "kind": "code_brigade_greenfield_memory_index",
        "contract_version": "eye-mechanicum.v1",
        "record_count": len(bounded),
        "templates_seen": dict(sorted(template_counter.items())),
        "status_counts": dict(sorted(status_counter.items())),
        "recent_runs": [
            {
                "project_name": str(record.get("project_name") or ""),
                "project_type": str(record.get("project_type") or ""),
                "template_id": str(record.get("template_id") or ""),
                "verification_status": str(record.get("verification_status") or ""),
                "review_status": str(record.get("review_status") or ""),
                "scenario_review_status": str(record.get("scenario_review_status") or ""),
                "implementation_synthesis_status": str(record.get("implementation_synthesis_status") or ""),
                "repaired_files": record.get("repaired_files", []) if isinstance(record.get("repaired_files"), list) else [],
            }
            for record in bounded[-10:]
        ],
        "common_review_blockers": [{"blocker": blocker, "count": count} for blocker, count in blockers.most_common(10)],
        "common_dependency_blockers": [{"blocker": blocker, "count": count} for blocker, count in dependency_blockers.most_common(10)],
        "reusable_learnings": learnings[:50],
    }


def update_greenfield_memory_index(repo: Path, memory_record: dict[str, Any], *, max_records: int = 20) -> dict[str, Any]:
    index_path = repo / "greenfield_memory_index.json"
    records: list[dict[str, Any]] = []
    if index_path.exists():
        try:
            existing = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        if isinstance(existing, dict) and isinstance(existing.get("records"), list):
            records = [record for record in existing["records"] if isinstance(record, dict)]
    records.append(memory_record)
    records = records[-max_records:]
    index = build_greenfield_memory_index(records, max_records=max_records)
    index["records"] = records
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return index
