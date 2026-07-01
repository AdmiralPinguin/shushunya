#!/usr/bin/env python3
from __future__ import annotations

from typing import Any


CONTRACT_VERSION = "eye-mechanicum.v1"


def build_blocked_execution_result(
    blockers: list[str],
    preflight: dict[str, Any] | None = None,
    rollback_notes: str = "",
    operation_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": "code_brigade_execution_result",
        "contract_version": CONTRACT_VERSION,
        "status": "blocked",
        "changed_files": [],
        "patch_summary": "",
        "verification_commands_executed": [],
        "blockers": blockers,
        "rollback_notes": rollback_notes,
        "operation_results": operation_results or [],
    }
    if preflight is not None:
        result["preflight"] = preflight
    return result


def build_implemented_execution_result(
    changed_files: list[str],
    patch_summary: str,
    preflight: dict[str, Any],
    operation_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "kind": "code_brigade_execution_result",
        "contract_version": CONTRACT_VERSION,
        "status": "implemented",
        "changed_files": changed_files,
        "patch_summary": patch_summary,
        "verification_commands_executed": [],
        "blockers": [],
        "rollback_notes": "",
        "operation_results": operation_results or [],
        "preflight": preflight,
    }
