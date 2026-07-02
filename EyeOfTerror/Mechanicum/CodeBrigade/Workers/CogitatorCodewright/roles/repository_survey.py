from __future__ import annotations

"""Repository survey role implementation."""

from codewright_core import *  # noqa: F403 - role modules share the extracted Codewright helper surface.


def run_repository_survey(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    goal = request_goal(request)
    survey = repo_survey(target_repo_root(request), goal)
    survey["role_policy"] = role_policy_from_request(request)
    survey["task_profile"] = task_profile_from_request(request)
    survey["worker_brief"] = worker_brief_from_request(request)
    write_json(workspace_root, output_path, survey)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": survey["summary"],
        "artifacts": [output_path],
        "confidence": "medium",
    }
