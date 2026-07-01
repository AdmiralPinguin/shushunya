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
    surface_matrix = brief.get("surface_verification_matrix") if isinstance(brief.get("surface_verification_matrix"), dict) else {}
    if not isinstance(surface_matrix.get("rows"), list) or not surface_matrix.get("rows"):
        problems.append("brief surface_verification_matrix.rows is required")
    if surface_matrix.get("complete") is False:
        problems.append("brief surface_verification_matrix is incomplete")
    survey_quality = brief.get("survey_quality_gate") if isinstance(brief.get("survey_quality_gate"), dict) else {}
    if survey_quality.get("decision") == "blocked":
        problems.append("brief survey_quality_gate is blocked")
    elif survey_quality.get("decision") not in {"passed"}:
        problems.append("brief survey_quality_gate decision is required")
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
    planning_review = brief.get("planning_review_gate") if isinstance(brief.get("planning_review_gate"), dict) else {}
    if planning_review.get("decision") == "blocked":
        problems.append("brief planning_review_gate is blocked")
    elif planning_review.get("decision") not in {"ready_for_ceraxia_review", "revise"}:
        problems.append("brief planning_review_gate decision is required")
    dependency = brief.get("planning_dependency_map") if isinstance(brief.get("planning_dependency_map"), dict) else {}
    if not isinstance(dependency.get("critical_path"), list) or not dependency.get("critical_path"):
        problems.append("brief planning_dependency_map.critical_path is required")
    breakdown = brief.get("work_breakdown") if isinstance(brief.get("work_breakdown"), dict) else {}
    phases = breakdown.get("phases") if isinstance(breakdown.get("phases"), list) else []
    if len(phases) < 6:
        problems.append("brief work_breakdown phases are required")
    elif not all(isinstance(phase, dict) and phase.get("id") and phase.get("owner") and phase.get("exit_gate") for phase in phases):
        problems.append("brief work_breakdown phases must include id, owner, and exit_gate")
    impact = brief.get("impact_analysis") if isinstance(brief.get("impact_analysis"), dict) else {}
    if not isinstance(impact.get("surfaces"), list) or not impact.get("surfaces"):
        problems.append("brief impact_analysis.surfaces is required")
    if not isinstance(impact.get("highest_risk_surface"), str) or not impact.get("highest_risk_surface"):
        problems.append("brief impact_analysis.highest_risk_surface is required")
    forecast = brief.get("execution_forecast") if isinstance(brief.get("execution_forecast"), dict) else {}
    if forecast.get("complexity") not in {"low", "medium", "high"}:
        problems.append("brief execution_forecast.complexity is required")
    if not isinstance(forecast.get("expected_code_brigade_iterations"), int) or forecast.get("expected_code_brigade_iterations", 0) < 1:
        problems.append("brief execution_forecast.expected_code_brigade_iterations is required")
    handoff = brief.get("code_brigade_handoff") if isinstance(brief.get("code_brigade_handoff"), dict) else {}
    if handoff.get("target") != "CodeBrigade":
        problems.append("brief code_brigade_handoff must target CodeBrigade")
    steps = handoff.get("steps") if isinstance(handoff.get("steps"), list) else []
    if not steps:
        problems.append("brief code_brigade_handoff steps are required")
    elif not all(isinstance(step, dict) and step.get("step") and step.get("owner") for step in steps):
        problems.append("brief code_brigade_handoff steps must include step and owner")
    return problems
