from __future__ import annotations

from typing import Any, Callable


ROLE_NAME = "TaskTriage"
OUTPUTS = ["problem_statement", "task_triage"]


def run(payload: dict[str, Any], helpers: dict[str, Callable[..., Any]]) -> dict[str, Any]:
    triage = helpers["task_triage"](payload)
    problem = helpers["problem_statement"](payload, triage)
    return {
        "role": ROLE_NAME,
        "outputs": {
            "task_triage": triage,
            "problem_statement": problem,
        },
    }

