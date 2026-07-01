#!/usr/bin/env python3
from __future__ import annotations

from typing import Any


CONTRACT_VERSION = "eye-mechanicum.v1"


def build_blocked_execution_result(blockers: list[str]) -> dict[str, Any]:
    return {
        "kind": "code_brigade_execution_result",
        "contract_version": CONTRACT_VERSION,
        "status": "blocked",
        "changed_files": [],
        "patch_summary": "",
        "verification_commands_executed": [],
        "blockers": blockers,
        "rollback_notes": "",
    }
