#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

from code_brigade_adapter import build_blocked_execution_result, validate_implementation_brief


def execute_implementation_brief(brief: dict[str, Any]) -> dict[str, Any]:
    validation_problems = validate_implementation_brief(brief)
    if validation_problems:
        return build_blocked_execution_result([f"invalid implementation brief: {problem}" for problem in validation_problems])
    return build_blocked_execution_result(["real CodeBrigade execution adapter is not configured"])
