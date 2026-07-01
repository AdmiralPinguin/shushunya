#!/usr/bin/env python3
from __future__ import annotations

from typing import Any


ROLE_ORDER = [
    "TaskTriage",
    "RepoSurveyor",
    "DesignStrategos",
    "VerificationArchitect",
    "RiskScribe",
]

CONTRACT_VERSION = "eye-mechanicum.v1"

REQUIRED_PACKET_OBJECTS = [
    "problem_statement",
    "task_triage",
    "repo_survey_request",
    "investigation_playbook",
    "dependency_map",
    "work_breakdown",
    "impact_analysis",
    "execution_forecast",
    "expert_quality_plan",
    "design_options",
    "verification_strategy",
    "surface_verification_matrix",
    "surface_package_matrix",
    "risk_register",
    "quality_bar",
    "acceptance_contract",
    "implementation_brief_blueprint",
    "implementation_work_packages",
    "planning_review_gate",
    "code_brigade_handoff",
]


def object_field(packet: dict[str, Any], key: str) -> dict[str, Any]:
    value = packet.get(key)
    return value if isinstance(value, dict) else {}


def list_field(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def validate_planning_packet(packet: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if packet.get("roles_completed") != ROLE_ORDER:
        problems.append("planning packet must include all five planning roles in order")
    if packet.get("contract_version") != CONTRACT_VERSION:
        problems.append("planning packet contract_version is unsupported")
    for key in REQUIRED_PACKET_OBJECTS:
        if not isinstance(packet.get(key), dict):
            problems.append(f"planning packet missing object: {key}")
    if not packet.get("ok"):
        problems.append("planning packet is not ok")
    if packet.get("design_options", {}).get("requires_ceraxia_approval") is not True:
        problems.append("planning packet must require Ceraxia strategy approval")

    triage = object_field(packet, "task_triage")
    if not isinstance(triage.get("task_kinds"), list) or not triage.get("task_kinds"):
        problems.append("task triage must include task_kinds")
    if triage.get("risk_level") not in {"low", "medium", "high"}:
        problems.append("task triage must include a valid risk_level")
    if triage.get("handoff_to") != "RepoSurveyor":
        problems.append("task triage must hand off to RepoSurveyor")

    survey = object_field(packet, "repo_survey_request")
    if survey.get("read_only") is not True:
        problems.append("repo survey request must be read-only")
    if not isinstance(survey.get("focus"), list) or not survey.get("focus"):
        problems.append("repo survey request must include focus areas")
    if survey.get("handoff_to") != "DesignStrategos":
        problems.append("repo survey request must hand off to DesignStrategos")

    playbook = object_field(packet, "investigation_playbook")
    stages = list_field(playbook.get("read_stages"))
    if len(stages) < 5:
        problems.append("investigation playbook must include ordered read stages")
    elif not all(isinstance(stage, dict) and stage.get("stage") and stage.get("must_collect") for stage in stages):
        problems.append("investigation playbook stages must include stage and must_collect")
    if not isinstance(playbook.get("evidence_questions"), list) or len(playbook.get("evidence_questions", [])) < 4:
        problems.append("investigation playbook must include evidence questions")
    if not isinstance(playbook.get("mutation_blockers"), list) or len(playbook.get("mutation_blockers", [])) < 3:
        problems.append("investigation playbook must include mutation blockers")
    if playbook.get("handoff_to") != "CodeBrigade":
        problems.append("investigation playbook must target CodeBrigade")

    problem = object_field(packet, "problem_statement")
    if not isinstance(problem.get("definition_of_done"), list) or len(problem.get("definition_of_done", [])) < 3:
        problems.append("problem statement must include definition_of_done")

    dependency = object_field(packet, "dependency_map")
    if dependency.get("critical_path") != [
        "task_contract",
        "repo_evidence",
        "design_decision",
        "verification_contract",
        "implementation_brief",
    ]:
        problems.append("dependency map must preserve the planning critical path")
    if not isinstance(dependency.get("nodes"), list) or len(dependency.get("nodes", [])) < 5:
        problems.append("dependency map must include planning dependency nodes")

    breakdown = object_field(packet, "work_breakdown")
    phases = list_field(breakdown.get("phases"))
    phase_ids = [phase.get("id") for phase in phases if isinstance(phase, dict)]
    for required_phase in [
        "frame_task",
        "survey_repo",
        "choose_design",
        "prepare_verification",
        "handoff_to_code_brigade",
        "review_result",
    ]:
        if required_phase not in phase_ids:
            problems.append(f"work breakdown missing phase: {required_phase}")
    if "review_result" in phase_ids:
        review_phase = next((phase for phase in phases if isinstance(phase, dict) and phase.get("id") == "review_result"), {})
        if not isinstance(review_phase.get("depends_on"), list) or not review_phase.get("depends_on"):
            problems.append("work breakdown review_result must depend on evidence phases")

    impact = object_field(packet, "impact_analysis")
    if not isinstance(impact.get("surfaces"), list) or not impact.get("surfaces"):
        problems.append("impact analysis must include impacted surfaces")
    if not isinstance(impact.get("highest_risk_surface"), str) or not impact.get("highest_risk_surface"):
        problems.append("impact analysis must include highest_risk_surface")

    forecast = object_field(packet, "execution_forecast")
    if forecast.get("complexity") not in {"low", "medium", "high"}:
        problems.append("execution forecast must include complexity")
    if not isinstance(forecast.get("expected_code_brigade_iterations"), int) or forecast.get("expected_code_brigade_iterations", 0) < 1:
        problems.append("execution forecast must include expected_code_brigade_iterations")
    scope_budget = forecast.get("scope_budget") if isinstance(forecast.get("scope_budget"), dict) else {}
    if not isinstance(scope_budget.get("max_source_files_to_edit"), int) or scope_budget.get("max_source_files_to_edit", 0) < 1:
        problems.append("execution forecast must include scope_budget.max_source_files_to_edit")
    if scope_budget.get("max_test_files_to_edit_without_explicit_user_request") != 0:
        problems.append("execution forecast must forbid unrequested test edits")
    if not isinstance(scope_budget.get("requires_ceraxia_replan_when"), list) or not scope_budget.get("requires_ceraxia_replan_when"):
        problems.append("execution forecast must include scope budget replan triggers")

    expert_plan = object_field(packet, "expert_quality_plan")
    if expert_plan.get("level") not in {"standard", "expert"}:
        problems.append("expert quality plan must include level")
    for key in ("tradeoff_register", "rollback_strategy", "observability_plan", "review_checklist", "escalation_policy"):
        if not isinstance(expert_plan.get(key), list) or len(expert_plan.get(key, [])) < 2:
            problems.append(f"expert quality plan must include {key}")
    if triage.get("risk_level") == "high":
        if expert_plan.get("level") != "expert" or expert_plan.get("required_for_expert_gate") is not True:
            problems.append("high-risk planning packet must require an expert quality plan")
        if len(expert_plan.get("review_checklist", [])) < 4:
            problems.append("high-risk expert quality plan must include a review checklist")

    design = object_field(packet, "design_options")
    if not isinstance(design.get("selected_strategy"), str) or not design.get("selected_strategy"):
        problems.append("design options must include selected_strategy")
    options = list_field(design.get("options"))
    if not any(item.get("name") == "hardcode" and item.get("decision") == "reject" for item in options if isinstance(item, dict)):
        problems.append("design options must reject hardcode")
    if not any(item.get("name") == "broad_rewrite" and item.get("decision") == "reject" for item in options if isinstance(item, dict)):
        problems.append("design options must reject broad_rewrite")
    if not any(item.get("name") == design.get("selected_strategy") and item.get("decision") == "prefer" for item in options if isinstance(item, dict)):
        problems.append("selected strategy must be marked prefer")
    if design.get("handoff_to") != "VerificationArchitect":
        problems.append("design options must hand off to VerificationArchitect")

    verification = object_field(packet, "verification_strategy")
    if not isinstance(verification.get("targeted_commands"), list) or not verification.get("targeted_commands"):
        problems.append("verification strategy must include targeted_commands")
    if not isinstance(verification.get("checks"), list) or not verification.get("checks"):
        problems.append("verification strategy must include checks")
    if not isinstance(verification.get("negative_tests"), list):
        problems.append("verification strategy must include negative_tests list")
    if not isinstance(verification.get("broad_verification_required"), bool):
        problems.append("verification strategy must include broad_verification_required boolean")
    if verification.get("handoff_to") != "RiskScribe":
        problems.append("verification strategy must hand off to RiskScribe")

    surface_matrix = object_field(packet, "surface_verification_matrix")
    if not isinstance(surface_matrix.get("rows"), list) or not surface_matrix.get("rows"):
        problems.append("surface verification matrix must include rows")
    if not isinstance(surface_matrix.get("complete"), bool):
        problems.append("surface verification matrix must include complete boolean")
    if surface_matrix.get("complete") is False:
        problems.extend(f"surface verification blocker: {item}" for item in list_field(surface_matrix.get("blockers")))

    risks = object_field(packet, "risk_register")
    if not isinstance(risks.get("risks"), list) or not risks.get("risks"):
        problems.append("risk register must include risks")
    if not isinstance(risks.get("acceptance_gates"), list) or not risks.get("acceptance_gates"):
        problems.append("risk register must include acceptance_gates")
    if risks.get("handoff_to") != "Ceraxia":
        problems.append("risk register must hand authority back to Ceraxia")

    quality = object_field(packet, "quality_bar")
    if not isinstance(quality.get("must_have_evidence"), list) or not quality.get("must_have_evidence"):
        problems.append("quality bar must include must_have_evidence")
    if not isinstance(quality.get("forbidden_shortcuts"), list) or not quality.get("forbidden_shortcuts"):
        problems.append("quality bar must include forbidden_shortcuts")
    if not isinstance(quality.get("success_definition"), str) or not quality.get("success_definition"):
        problems.append("quality bar must include success_definition")

    acceptance = object_field(packet, "acceptance_contract")
    if not isinstance(acceptance.get("must_prove"), list) or len(acceptance.get("must_prove", [])) < 4:
        problems.append("acceptance contract must include must_prove evidence")
    if not isinstance(acceptance.get("review_questions"), list) or len(acceptance.get("review_questions", [])) < 3:
        problems.append("acceptance contract must include review questions")

    blueprint = object_field(packet, "implementation_brief_blueprint")
    if blueprint.get("target") != "CodeBrigade":
        problems.append("implementation brief blueprint must target CodeBrigade")
    if not isinstance(blueprint.get("mutation_preconditions"), list) or len(blueprint.get("mutation_preconditions", [])) < 3:
        problems.append("implementation brief blueprint must include mutation preconditions")
    if "expert_quality_plan" not in list_field(blueprint.get("required_sections")):
        problems.append("implementation brief blueprint must require expert_quality_plan")
    if "investigation_playbook" not in list_field(blueprint.get("required_sections")):
        problems.append("implementation brief blueprint must require investigation_playbook")

    work_packages = object_field(packet, "implementation_work_packages")
    packages = list_field(work_packages.get("packages"))
    if len(packages) < 3:
        problems.append("implementation work packages must include at least three packages")
    for package in packages:
        if not isinstance(package, dict):
            problems.append("implementation work package must be an object")
            continue
        if package.get("owner") != "CodeBrigade":
            problems.append(f"implementation work package must target CodeBrigade: {package.get('id', '<unknown>')}")
        for key in ("id", "purpose", "impact_surfaces", "read_scope", "edit_scope", "verification_scope", "risk_controls", "blocking_policy", "handoff_criteria"):
            if key not in package:
                problems.append(f"implementation work package missing {key}: {package.get('id', '<unknown>')}")
        for key in ("impact_surfaces", "read_scope", "edit_scope", "verification_scope", "risk_controls", "blocking_policy", "handoff_criteria"):
            if not isinstance(package.get(key), list):
                problems.append(f"implementation work package {key} must be a list: {package.get('id', '<unknown>')}")
        if not package.get("blocking_policy"):
            problems.append(f"implementation work package blocking_policy must be non-empty: {package.get('id', '<unknown>')}")
    if not isinstance(work_packages.get("review_order"), list) or len(work_packages.get("review_order", [])) != len(packages):
        problems.append("implementation work packages must include review_order for every package")
    planned_surfaces = {
        row.get("surface")
        for row in list_field(surface_matrix.get("rows"))
        if isinstance(row, dict) and row.get("surface")
    }
    covered_surfaces = {
        surface
        for package in packages
        if isinstance(package, dict)
        for surface in list_field(package.get("impact_surfaces"))
        if isinstance(surface, str) and surface
    }
    missing_package_surfaces = sorted(surface for surface in planned_surfaces if surface not in covered_surfaces)
    if missing_package_surfaces:
        problems.append("implementation work packages must cover every planned surface: " + ", ".join(missing_package_surfaces))

    package_matrix = object_field(packet, "surface_package_matrix")
    matrix_rows = list_field(package_matrix.get("rows"))
    if not matrix_rows:
        problems.append("surface package matrix must include rows")
    matrix_surfaces = {
        row.get("surface")
        for row in matrix_rows
        if isinstance(row, dict) and row.get("surface")
    }
    missing_matrix_surfaces = sorted(surface for surface in planned_surfaces if surface not in matrix_surfaces)
    if missing_matrix_surfaces:
        problems.append("surface package matrix must cover every planned surface: " + ", ".join(missing_matrix_surfaces))
    for row in matrix_rows:
        if not isinstance(row, dict):
            problems.append("surface package matrix row must be an object")
            continue
        if not list_field(row.get("package_ids")):
            problems.append(f"surface package matrix row must include package_ids: {row.get('surface', '<unknown>')}")
        if not isinstance(row.get("verification_evidence"), list):
            problems.append(f"surface package matrix row verification_evidence must be a list: {row.get('surface', '<unknown>')}")
    if package_matrix.get("complete") is not True:
        problems.extend(f"surface package matrix blocked: {item}" for item in list_field(package_matrix.get("blockers")))

    planning_review = object_field(packet, "planning_review_gate")
    if planning_review.get("decision") not in {"ready_for_ceraxia_review", "revise", "blocked"}:
        problems.append("planning review gate must include a valid decision")
    if not isinstance(planning_review.get("score"), int) or planning_review.get("score", -1) < 0:
        problems.append("planning review gate must include a non-negative score")
    if planning_review.get("decision") == "blocked":
        problems.extend(f"planning review blocked: {item}" for item in list_field(planning_review.get("blockers")))

    handoff = object_field(packet, "code_brigade_handoff")
    if handoff.get("target") != "CodeBrigade":
        problems.append("code brigade handoff must target CodeBrigade")
    if not isinstance(handoff.get("steps"), list) or not handoff.get("steps"):
        problems.append("code brigade handoff must include steps")
    if packet.get("next_action", {}).get("owner") != "Ceraxia":
        problems.append("next action must be owned by Ceraxia")
    return problems
