from __future__ import annotations

from typing import Any, Callable


ROLE_NAME = "RiskScribe"
OUTPUTS = [
    "risk_register",
    "quality_bar",
    "acceptance_contract",
    "acceptance_trace_matrix",
    "constraint_trace_matrix",
    "implementation_brief_blueprint",
    "implementation_work_packages",
    "worker_output_contract",
    "surface_package_matrix",
    "planning_review_gate",
    "code_brigade_handoff",
]


def run(context: dict[str, Any], helpers: dict[str, Callable[..., Any]]) -> dict[str, Any]:
    triage = context["task_triage"]
    problem = context["problem_statement"]
    survey = context["repo_survey_request"]
    dependency = context["dependency_map"]
    breakdown = context["work_breakdown"]
    impact = context["impact_analysis"]
    forecast = context["execution_forecast"]
    expert_plan = context["expert_quality_plan"]
    change_control = context["change_control_plan"]
    design = context["design_options"]
    verification = context["verification_strategy"]
    repair_plan = context["diagnostic_repair_plan"]
    surface_matrix = context["surface_verification_matrix"]
    risks = helpers["risk_register"](triage, survey, design, verification)
    quality = helpers["quality_bar"](triage, verification)
    acceptance = helpers["acceptance_contract"](problem, triage, verification, quality, surface_matrix, expert_plan)
    blueprint = helpers["implementation_brief_blueprint"](
        triage,
        design,
        verification,
        risks,
        quality,
        dependency,
        breakdown,
        impact,
        surface_matrix,
        forecast,
        expert_plan,
        context["investigation_playbook"],
        change_control,
    )
    work_packages = helpers["implementation_work_packages"](triage, problem, dependency, impact, verification, risks, forecast)
    package_matrix = helpers["surface_package_matrix"](surface_matrix, work_packages)
    acceptance_trace = helpers["acceptance_trace_matrix"](problem, quality, acceptance, verification, surface_matrix, work_packages)
    constraint_trace = helpers["constraint_trace_matrix"](problem, work_packages, acceptance_trace)
    output_contract = helpers["worker_output_contract"](work_packages, acceptance_trace, constraint_trace, repair_plan)
    review = helpers["planning_review_gate"](
        triage,
        problem,
        survey,
        dependency,
        breakdown,
        verification,
        surface_matrix,
        acceptance,
        expert_plan,
        change_control,
        work_packages,
        package_matrix,
        acceptance_trace,
        constraint_trace,
    )
    handoff = helpers["code_brigade_handoff"](triage, verification, quality, work_packages, acceptance_trace, repair_plan, output_contract)
    return {
        "role": ROLE_NAME,
        "outputs": {
            "risk_register": risks,
            "quality_bar": quality,
            "acceptance_contract": acceptance,
            "implementation_brief_blueprint": blueprint,
            "implementation_work_packages": work_packages,
            "surface_package_matrix": package_matrix,
            "acceptance_trace_matrix": acceptance_trace,
            "constraint_trace_matrix": constraint_trace,
            "worker_output_contract": output_contract,
            "planning_review_gate": review,
            "code_brigade_handoff": handoff,
        },
    }

