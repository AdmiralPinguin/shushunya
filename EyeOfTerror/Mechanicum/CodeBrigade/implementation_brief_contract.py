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
    assumptions = brief.get("assumption_register") if isinstance(brief.get("assumption_register"), dict) else {}
    assumption_rows = assumptions.get("assumptions") if isinstance(assumptions.get("assumptions"), list) else []
    if len(assumption_rows) < 3:
        problems.append("brief assumption_register.assumptions are required")
    for row in assumption_rows:
        if not isinstance(row, dict):
            problems.append("brief assumption_register row must be an object")
            continue
        for key in ("id", "assumption", "risk_if_false", "validation_source", "blocks_when_false", "owner"):
            if key not in row:
                problems.append(f"brief assumption_register row missing {key}")
    if not isinstance(assumptions.get("replan_when_false"), list) or len(assumptions.get("replan_when_false", [])) < 3:
        problems.append("brief assumption_register.replan_when_false is required")
    surface_matrix = brief.get("surface_verification_matrix") if isinstance(brief.get("surface_verification_matrix"), dict) else {}
    if not isinstance(surface_matrix.get("rows"), list) or not surface_matrix.get("rows"):
        problems.append("brief surface_verification_matrix.rows is required")
    if surface_matrix.get("complete") is False:
        problems.append("brief surface_verification_matrix is incomplete")
    package_matrix = brief.get("surface_package_matrix") if isinstance(brief.get("surface_package_matrix"), dict) else {}
    package_matrix_rows = package_matrix.get("rows") if isinstance(package_matrix.get("rows"), list) else []
    if not package_matrix_rows:
        problems.append("brief surface_package_matrix.rows is required")
    if package_matrix.get("complete") is False:
        problems.append("brief surface_package_matrix is incomplete")
    survey_quality = brief.get("survey_quality_gate") if isinstance(brief.get("survey_quality_gate"), dict) else {}
    if survey_quality.get("decision") == "blocked":
        problems.append("brief survey_quality_gate is blocked")
    elif survey_quality.get("decision") not in {"passed"}:
        problems.append("brief survey_quality_gate decision is required")
    if not isinstance(brief.get("acceptance_gates"), list) or not brief.get("acceptance_gates"):
        problems.append("brief acceptance_gates are required")
    evidence = brief.get("repo_survey_evidence") if isinstance(brief.get("repo_survey_evidence"), dict) else {}
    if not evidence:
        problems.append("brief repo_survey_evidence is required")
    elif not isinstance(evidence.get("candidate_files"), list):
        problems.append("brief repo_survey_evidence.candidate_files is required")
    else:
        if not isinstance(evidence.get("caller_candidates"), list):
            problems.append("brief repo_survey_evidence.caller_candidates is required")
        if not isinstance(evidence.get("contract_surface_candidates"), list):
            problems.append("brief repo_survey_evidence.contract_surface_candidates is required")
    quality = brief.get("quality_bar") if isinstance(brief.get("quality_bar"), dict) else {}
    if not isinstance(quality.get("must_have_evidence"), list) or not quality.get("must_have_evidence"):
        problems.append("brief quality_bar.must_have_evidence is required")
    acceptance = brief.get("acceptance_contract") if isinstance(brief.get("acceptance_contract"), dict) else {}
    if not isinstance(acceptance.get("must_prove"), list) or not acceptance.get("must_prove"):
        problems.append("brief acceptance_contract.must_prove is required")
    trace_matrix = brief.get("acceptance_trace_matrix") if isinstance(brief.get("acceptance_trace_matrix"), dict) else {}
    trace_rows = trace_matrix.get("rows") if isinstance(trace_matrix.get("rows"), list) else []
    if not trace_rows:
        problems.append("brief acceptance_trace_matrix.rows is required")
    if trace_matrix.get("complete") is not True:
        problems.append("brief acceptance_trace_matrix must be complete")
    for row in trace_rows:
        if not isinstance(row, dict):
            problems.append("brief acceptance_trace_matrix row must be an object")
            continue
        if not row.get("requirement"):
            problems.append("brief acceptance_trace_matrix row requirement is required")
        if not isinstance(row.get("planned_evidence"), list) or not row.get("planned_evidence"):
            problems.append("brief acceptance_trace_matrix row planned_evidence is required")
        if not isinstance(row.get("package_ids"), list) or not row.get("package_ids"):
            problems.append("brief acceptance_trace_matrix row package_ids is required")
    expert_plan = brief.get("expert_quality_plan") if isinstance(brief.get("expert_quality_plan"), dict) else {}
    if expert_plan.get("level") not in {"standard", "expert"}:
        problems.append("brief expert_quality_plan.level is required")
    for key in ("tradeoff_register", "rollback_strategy", "observability_plan", "review_checklist", "escalation_policy"):
        if not isinstance(expert_plan.get(key), list) or len(expert_plan.get(key, [])) < 2:
            problems.append(f"brief expert_quality_plan.{key} is required")
    if brief.get("risk_level") == "high" and (
        expert_plan.get("level") != "expert" or expert_plan.get("required_for_expert_gate") is not True
    ):
        problems.append("brief high-risk work must include an expert quality plan")
    change_control = brief.get("change_control_plan") if isinstance(brief.get("change_control_plan"), dict) else {}
    if change_control.get("target") != "CodeBrigade":
        problems.append("brief change_control_plan must target CodeBrigade")
    for key in ("allowed_change_intents", "protected_invariants", "diff_review_questions", "rollback_triggers", "post_change_proofs"):
        if not isinstance(change_control.get(key), list) or len(change_control.get(key, [])) < 3:
            problems.append(f"brief change_control_plan.{key} is required")
    if not isinstance(change_control.get("mutation_requires"), list) or len(change_control.get("mutation_requires", [])) < 4:
        problems.append("brief change_control_plan.mutation_requires is required")
    playbook = brief.get("investigation_playbook") if isinstance(brief.get("investigation_playbook"), dict) else {}
    if playbook.get("target") != "CodeBrigade":
        problems.append("brief investigation_playbook must target CodeBrigade")
    stages = playbook.get("read_stages") if isinstance(playbook.get("read_stages"), list) else []
    if len(stages) < 5:
        problems.append("brief investigation_playbook.read_stages are required")
    elif not all(isinstance(stage, dict) and stage.get("stage") and stage.get("must_collect") for stage in stages):
        problems.append("brief investigation_playbook stages must include stage and must_collect")
    if not isinstance(playbook.get("evidence_questions"), list) or len(playbook.get("evidence_questions", [])) < 4:
        problems.append("brief investigation_playbook.evidence_questions are required")
    if not isinstance(playbook.get("mutation_blockers"), list) or len(playbook.get("mutation_blockers", [])) < 3:
        problems.append("brief investigation_playbook.mutation_blockers are required")
    blueprint = brief.get("implementation_brief_blueprint") if isinstance(brief.get("implementation_brief_blueprint"), dict) else {}
    if blueprint.get("target") != "CodeBrigade":
        problems.append("brief implementation_brief_blueprint must target CodeBrigade")
    if not isinstance(blueprint.get("mutation_preconditions"), list) or not blueprint.get("mutation_preconditions"):
        problems.append("brief implementation_brief_blueprint mutation_preconditions are required")
    if "expert_quality_plan" not in blueprint.get("required_sections", []):
        problems.append("brief implementation_brief_blueprint must require expert_quality_plan")
    if "investigation_playbook" not in blueprint.get("required_sections", []):
        problems.append("brief implementation_brief_blueprint must require investigation_playbook")
    if "change_control_plan" not in blueprint.get("required_sections", []):
        problems.append("brief implementation_brief_blueprint must require change_control_plan")
    if "acceptance_trace_matrix" not in blueprint.get("required_sections", []):
        problems.append("brief implementation_brief_blueprint must require acceptance_trace_matrix")
    if "assumption_register" not in blueprint.get("required_sections", []):
        problems.append("brief implementation_brief_blueprint must require assumption_register")
    work_packages = brief.get("implementation_work_packages") if isinstance(brief.get("implementation_work_packages"), dict) else {}
    packages = work_packages.get("packages") if isinstance(work_packages.get("packages"), list) else []
    if len(packages) < 3:
        problems.append("brief implementation_work_packages packages are required")
    else:
        for package in packages:
            if not isinstance(package, dict):
                problems.append("brief implementation_work_packages package must be an object")
                continue
            if package.get("owner") != "CodeBrigade":
                problems.append(f"brief implementation work package must target CodeBrigade: {package.get('id', '<unknown>')}")
            for key in ("id", "purpose", "impact_surfaces", "read_scope", "edit_scope", "verification_scope", "risk_controls", "blocking_policy", "handoff_criteria"):
                if key not in package:
                    problems.append(f"brief implementation work package missing {key}: {package.get('id', '<unknown>')}")
            for key in ("impact_surfaces", "read_scope", "edit_scope", "verification_scope", "risk_controls", "blocking_policy", "handoff_criteria"):
                if not isinstance(package.get(key), list):
                    problems.append(f"brief implementation work package {key} must be a list: {package.get('id', '<unknown>')}")
            if not package.get("blocking_policy"):
                problems.append(f"brief implementation work package blocking_policy must be non-empty: {package.get('id', '<unknown>')}")
    review_order = work_packages.get("review_order") if isinstance(work_packages.get("review_order"), list) else []
    if len(review_order) != len(packages):
        problems.append("brief implementation_work_packages review_order must cover every package")
    planned_surfaces = {
        row.get("surface")
        for row in surface_matrix.get("rows", [])
        if isinstance(row, dict) and row.get("surface")
    }
    covered_surfaces = {
        surface
        for package in packages
        if isinstance(package, dict)
        for surface in package.get("impact_surfaces", [])
        if isinstance(surface, str) and surface
    }
    missing_package_surfaces = sorted(surface for surface in planned_surfaces if surface not in covered_surfaces)
    if missing_package_surfaces:
        problems.append("brief implementation_work_packages must cover every planned surface: " + ", ".join(missing_package_surfaces))
    matrix_surfaces = {
        row.get("surface")
        for row in package_matrix_rows
        if isinstance(row, dict) and row.get("surface")
    }
    missing_matrix_surfaces = sorted(surface for surface in planned_surfaces if surface not in matrix_surfaces)
    if missing_matrix_surfaces:
        problems.append("brief surface_package_matrix must cover every planned surface: " + ", ".join(missing_matrix_surfaces))
    for row in package_matrix_rows:
        if not isinstance(row, dict):
            problems.append("brief surface_package_matrix row must be an object")
            continue
        if not isinstance(row.get("verification_evidence"), list):
            problems.append(f"brief surface_package_matrix verification_evidence must be a list: {row.get('surface', '<unknown>')}")
        package_ids = row.get("package_ids") if isinstance(row.get("package_ids"), list) else []
        if not package_ids:
            problems.append(f"brief surface_package_matrix package_ids are required: {row.get('surface', '<unknown>')}")
        missing_ids = sorted(package_id for package_id in package_ids if package_id not in review_order)
        if missing_ids:
            problems.append("brief surface_package_matrix references unknown packages: " + ", ".join(missing_ids))
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
    scope_budget = forecast.get("scope_budget") if isinstance(forecast.get("scope_budget"), dict) else {}
    if not isinstance(scope_budget.get("max_source_files_to_edit"), int) or scope_budget.get("max_source_files_to_edit", 0) < 1:
        problems.append("brief execution_forecast.scope_budget.max_source_files_to_edit is required")
    if scope_budget.get("max_test_files_to_edit_without_explicit_user_request") != 0:
        problems.append("brief execution_forecast.scope_budget must forbid unrequested test edits")
    if not isinstance(scope_budget.get("requires_ceraxia_replan_when"), list) or not scope_budget.get("requires_ceraxia_replan_when"):
        problems.append("brief execution_forecast.scope_budget replan triggers are required")
    execution_intent = brief.get("execution_intent") if isinstance(brief.get("execution_intent"), dict) else {}
    if execution_intent.get("kind") != "ceraxia_code_brigade_execution_intent":
        problems.append("brief execution_intent kind is required")
    if execution_intent.get("contract_version") != CONTRACT_VERSION:
        problems.append("brief execution_intent contract_version is unsupported")
    if execution_intent.get("mode") not in {"planning_handoff_only", "explicit_patch_execution", "guarded_inferred_patch_execution"}:
        problems.append("brief execution_intent.mode is required")
    if execution_intent.get("adapter_capability") not in {"explicit_patch_adapter_only", "explicit_or_guarded_inference_adapter"}:
        problems.append("brief execution_intent.adapter_capability is required")
    if not isinstance(execution_intent.get("explicit_patch_present"), bool):
        problems.append("brief execution_intent.explicit_patch_present is required")
    if not isinstance(execution_intent.get("real_execution_supported"), bool):
        problems.append("brief execution_intent.real_execution_supported is required")
    handoff = brief.get("code_brigade_handoff") if isinstance(brief.get("code_brigade_handoff"), dict) else {}
    if handoff.get("target") != "CodeBrigade":
        problems.append("brief code_brigade_handoff must target CodeBrigade")
    steps = handoff.get("steps") if isinstance(handoff.get("steps"), list) else []
    if not steps:
        problems.append("brief code_brigade_handoff steps are required")
    elif not all(isinstance(step, dict) and step.get("step") and step.get("owner") for step in steps):
        problems.append("brief code_brigade_handoff steps must include step and owner")
    return problems
