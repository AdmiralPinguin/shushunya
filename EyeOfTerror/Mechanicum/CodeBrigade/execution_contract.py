#!/usr/bin/env python3
from __future__ import annotations

from typing import Any


CONTRACT_VERSION = "eye-mechanicum.v1"


def build_blocked_execution_result(
    blockers: list[str],
    preflight: dict[str, Any] | None = None,
    rollback_notes: str = "",
    operation_results: list[dict[str, Any]] | None = None,
    patch_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    operations = operation_results or []
    result: dict[str, Any] = {
        "kind": "code_brigade_execution_result",
        "contract_version": CONTRACT_VERSION,
        "status": "blocked",
        "changed_files": [],
        "patch_summary": "",
        "verification_commands_executed": [],
        "blockers": blockers,
        "rollback_notes": rollback_notes,
        "operation_results": operations,
        "patch_manifest": patch_manifest or build_patch_manifest([], operations, rollback_notes),
    }
    if preflight is not None:
        result["preflight"] = preflight
    return result


def build_implemented_execution_result(
    changed_files: list[str],
    patch_summary: str,
    preflight: dict[str, Any],
    operation_results: list[dict[str, Any]] | None = None,
    patch_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    operations = operation_results or []
    return {
        "kind": "code_brigade_execution_result",
        "contract_version": CONTRACT_VERSION,
        "status": "implemented",
        "changed_files": changed_files,
        "patch_summary": patch_summary,
        "verification_commands_executed": [],
        "blockers": [],
        "rollback_notes": "",
        "operation_results": operations,
        "patch_manifest": patch_manifest or build_patch_manifest(changed_files, operations, ""),
        "preflight": preflight,
    }


def build_patch_manifest(changed_files: list[str], operation_results: list[dict[str, Any]], rollback_notes: str) -> dict[str, Any]:
    operation_counts: dict[str, int] = {}
    failed_operations = 0
    file_rows: dict[str, dict[str, Any]] = {}
    for row in operation_results:
        if not isinstance(row, dict):
            continue
        operation = str(row.get("operation") or "unknown")
        path = str(row.get("path") or "")
        status = str(row.get("status") or "")
        operation_counts[operation] = operation_counts.get(operation, 0) + 1
        if path:
            file_row = file_rows.setdefault(
                path,
                {
                    "path": path,
                    "operations": [],
                    "applied_operation_count": 0,
                    "failed_operation_count": 0,
                    "rollback_touched": False,
                    "before_sha256": "",
                    "after_sha256": "",
                },
            )
            file_row["operations"].append(operation)
            if row.get("before_sha256"):
                file_row["before_sha256"] = str(row.get("before_sha256") or "")
            if row.get("after_sha256"):
                file_row["after_sha256"] = str(row.get("after_sha256") or "")
            if status == "applied":
                file_row["applied_operation_count"] += 1
            if status.startswith("failed"):
                file_row["failed_operation_count"] += 1
                file_row["rollback_touched"] = bool(rollback_notes)
        if status.startswith("failed"):
            failed_operations += 1
    for path in changed_files:
        file_rows.setdefault(
            path,
            {
                "path": path,
                "operations": [],
                "applied_operation_count": 0,
                "failed_operation_count": 0,
                "rollback_touched": False,
                "before_sha256": "",
                "after_sha256": "",
            },
        )
    return {
        "kind": "code_brigade_patch_manifest",
        "contract_version": CONTRACT_VERSION,
        "changed_files": changed_files,
        "files": [file_rows[path] for path in sorted(file_rows)],
        "changed_file_count": len(changed_files),
        "multi_file": len(set(changed_files)) > 1,
        "operation_count": len(operation_results),
        "operation_counts": operation_counts,
        "failed_operation_count": failed_operations,
        "rollback_performed": bool(rollback_notes),
        "rollback_notes": rollback_notes,
    }
