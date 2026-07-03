from __future__ import annotations

from typing import Any, Callable


ROLE_NAME = "VerificationArchitect"
OUTPUTS = ["verification_strategy", "diagnostic_repair_plan", "surface_verification_matrix"]


def run(context: dict[str, Any], helpers: dict[str, Callable[..., Any]]) -> dict[str, Any]:
    triage = context["task_triage"]
    payload = context["payload"]
    impact = context["impact_analysis"]
    forecast = context["execution_forecast"]
    verification = helpers["verification_strategy"](triage, payload)
    repair_plan = helpers["diagnostic_repair_plan"](triage, verification, impact, forecast)
    surface_matrix = helpers["surface_verification_matrix"](impact, verification)
    return {
        "role": ROLE_NAME,
        "outputs": {
            "verification_strategy": verification,
            "diagnostic_repair_plan": repair_plan,
            "surface_verification_matrix": surface_matrix,
        },
    }

