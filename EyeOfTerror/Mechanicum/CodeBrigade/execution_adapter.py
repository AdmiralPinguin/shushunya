#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

from execution_contract import build_blocked_execution_result
from execution_preflight import build_execution_preflight
from implementation_brief_contract import validate_implementation_brief


def execute_implementation_brief(brief: dict[str, Any]) -> dict[str, Any]:
    validation_problems = validate_implementation_brief(brief)
    if validation_problems:
        return build_blocked_execution_result([f"invalid implementation brief: {problem}" for problem in validation_problems])
    preflight = build_execution_preflight(brief)
    blockers = [*preflight["blockers"], "real CodeBrigade execution adapter is not configured"]
    return build_blocked_execution_result(blockers, preflight)
