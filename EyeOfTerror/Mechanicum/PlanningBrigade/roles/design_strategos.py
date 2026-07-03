from __future__ import annotations

from typing import Any, Callable


ROLE_NAME = "DesignStrategos"
OUTPUTS = [
    "work_breakdown",
    "impact_analysis",
    "investigation_playbook",
    "design_options",
    "execution_forecast",
    "expert_quality_plan",
    "change_control_plan",
]


def run(context: dict[str, Any], helpers: dict[str, Callable[..., Any]]) -> dict[str, Any]:
    payload = context["payload"]
    triage = context["task_triage"]
    problem = context["problem_statement"]
    survey = context["repo_survey_request"]
    dependency = context["dependency_map"]
    playbook = helpers["investigation_playbook"](triage, problem, survey)
    breakdown = helpers["work_breakdown"](triage, dependency)
    impact = helpers["impact_analysis"](triage, problem, survey)
    forecast = helpers["execution_forecast"](triage, breakdown, impact)
    expert_plan = helpers["expert_quality_plan"](triage, impact, forecast)
    design = helpers["design_options"](payload, triage)
    return {
        "role": ROLE_NAME,
        "outputs": {
            "investigation_playbook": playbook,
            "work_breakdown": breakdown,
            "impact_analysis": impact,
            "execution_forecast": forecast,
            "expert_quality_plan": expert_plan,
            "design_options": design,
        },
    }


def finalize_change_control(context: dict[str, Any], helpers: dict[str, Callable[..., Any]]) -> dict[str, Any]:
    return helpers["change_control_plan"](
        context["task_triage"],
        context["impact_analysis"],
        context["verification_strategy"],
        context["expert_quality_plan"],
    )

