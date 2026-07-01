#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

from execution_contract import CONTRACT_VERSION


def validate_implementation_brief(brief: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if brief.get("kind") != "ceraxia_code_brigade_implementation_brief":
        problems.append("brief kind must be ceraxia_code_brigade_implementation_brief")
    if brief.get("contract_version") != CONTRACT_VERSION:
        problems.append("brief contract_version is unsupported")
    if brief.get("target") != "CodeBrigade":
        problems.append("brief target must be CodeBrigade")
    if not isinstance(brief.get("task"), str) or not brief.get("task"):
        problems.append("brief task is required")
    if not isinstance(brief.get("allowed_scope"), list) or not brief.get("allowed_scope"):
        problems.append("brief allowed_scope is required")
    if not isinstance(brief.get("forbidden_approaches"), list) or not brief.get("forbidden_approaches"):
        problems.append("brief forbidden_approaches is required")
    if not isinstance(brief.get("required_verification"), dict):
        problems.append("brief required_verification is required")
    if not isinstance(brief.get("acceptance_gates"), list) or not brief.get("acceptance_gates"):
        problems.append("brief acceptance_gates are required")
    handoff = brief.get("code_brigade_handoff") if isinstance(brief.get("code_brigade_handoff"), dict) else {}
    if handoff.get("target") != "CodeBrigade":
        problems.append("brief code_brigade_handoff must target CodeBrigade")
    steps = handoff.get("steps") if isinstance(handoff.get("steps"), list) else []
    if not steps:
        problems.append("brief code_brigade_handoff steps are required")
    elif not all(isinstance(step, dict) and step.get("step") and step.get("owner") for step in steps):
        problems.append("brief code_brigade_handoff steps must include step and owner")
    return problems
