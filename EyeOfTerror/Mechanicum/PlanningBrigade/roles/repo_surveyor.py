from __future__ import annotations

from typing import Any, Callable


ROLE_NAME = "RepoSurveyor"
OUTPUTS = ["repo_survey_request", "assumption_register", "dependency_map"]


def run(context: dict[str, Any], helpers: dict[str, Callable[..., Any]]) -> dict[str, Any]:
    payload = context["payload"]
    triage = context["task_triage"]
    problem = context["problem_statement"]
    survey = helpers["repo_survey_request"](payload, triage)
    assumptions = helpers["assumption_register"](triage, problem, survey)
    dependency = helpers["dependency_map"](triage, survey)
    return {
        "role": ROLE_NAME,
        "outputs": {
            "repo_survey_request": survey,
            "assumption_register": assumptions,
            "dependency_map": dependency,
        },
    }

