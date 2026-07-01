#!/usr/bin/env python3
from __future__ import annotations

from typing import Any


CONTRACT_VERSION = "eye-mechanicum.v1"


def build_blocked_execution_result(blockers: list[str], preflight: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": "code_brigade_execution_result",
        "contract_version": CONTRACT_VERSION,
        "status": "blocked",
        "changed_files": [],
        "patch_summary": "",
        "verification_commands_executed": [],
        "blockers": blockers,
        "rollback_notes": "",
    }
    if preflight is not None:
        result["preflight"] = preflight
    return result
