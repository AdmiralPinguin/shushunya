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
    quality = brief.get("quality_bar") if isinstance(brief.get("quality_bar"), dict) else {}
    if not isinstance(quality.get("must_have_evidence"), list) or not quality.get("must_have_evidence"):
        problems.append("brief quality_bar.must_have_evidence is required")
    acceptance = brief.get("acceptance_contract") if isinstance(brief.get("acceptance_contract"), dict) else {}
    if not isinstance(acceptance.get("must_prove"), list) or not acceptance.get("must_prove"):
        problems.append("brief acceptance_contract.must_prove is required")
    blueprint = brief.get("implementation_brief_blueprint") if isinstance(brief.get("implementation_brief_blueprint"), dict) else {}
    if blueprint.get("target") != "CodeBrigade":
        problems.append("brief implementation_brief_blueprint must target CodeBrigade")
    if not isinstance(blueprint.get("mutation_preconditions"), list) or not blueprint.get("mutation_preconditions"):
        problems.append("brief implementation_brief_blueprint mutation_preconditions are required")
    dependency = brief.get("planning_dependency_map") if isinstance(brief.get("planning_dependency_map"), dict) else {}
    if not isinstance(dependency.get("critical_path"), list) or not dependency.get("critical_path"):
        problems.append("brief planning_dependency_map.critical_path is required")
    breakdown = brief.get("work_breakdown") if isinstance(brief.get("work_breakdown"), dict) else {}
    phases = breakdown.get("phases") if isinstance(breakdown.get("phases"), list) else []
    if len(phases) < 6:
        problems.append("brief work_breakdown phases are required")
    elif not all(isinstance(phase, dict) and phase.get("id") and phase.get("owner") and phase.get("exit_gate") for phase in phases):
        problems.append("brief work_breakdown phases must include id, owner, and exit_gate")
    handoff = brief.get("code_brigade_handoff") if isinstance(brief.get("code_brigade_handoff"), dict) else {}
    if handoff.get("target") != "CodeBrigade":
        problems.append("brief code_brigade_handoff must target CodeBrigade")
    steps = handoff.get("steps") if isinstance(handoff.get("steps"), list) else []
    if not steps:
        problems.append("brief code_brigade_handoff steps are required")
    elif not all(isinstance(step, dict) and step.get("step") and step.get("owner") for step in steps):
        problems.append("brief code_brigade_handoff steps must include step and owner")
    return problems
