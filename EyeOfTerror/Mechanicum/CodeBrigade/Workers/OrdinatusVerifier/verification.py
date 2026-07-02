from __future__ import annotations

"""Verification role implementation."""

from common.codewright_core import *  # noqa: F403 - role modules use the shared Codewright helper surface.


def run_verification(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    patch = load_json_optional(workspace_root, sibling_artifact(output_path, "patch_manifest.json"))
    role_policy = role_policy_from_request(request)
    task_profile = task_profile_from_request(request)
    worker_brief = worker_brief_from_request(request)
    blockers = [str(item) for item in patch.get("blockers", [])] if isinstance(patch.get("blockers"), list) else []
    executed: list[dict[str, Any]] = []
    repo_root = target_repo_root(request)
    changed_files = patch.get("changed_files") if isinstance(patch.get("changed_files"), list) else []
    repairs: list[dict[str, Any]] = []
    blocked_repairs: list[dict[str, Any]] = []
    candidate_source_paths: list[str] = []
    ranked_survey_sources = ranked_source_candidates_from_survey(workspace_root, output_path)
    repairs_allowed = role_policy_allows_source_mutation(role_policy)
    if patch.get("status") == "applied":
        py_files = [
            str(item.get("path"))
            for item in changed_files
            if isinstance(item, dict) and str(item.get("path") or "").endswith(".py")
        ]
        if py_files:
            cmd = [sys.executable, "-m", "py_compile", *py_files]
            completed = subprocess.run(cmd, cwd=repo_root, text=True, capture_output=True, check=False)
            executed.append(
                {
                    "command": " ".join(cmd),
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                }
            )
            if completed.returncode != 0:
                for candidate in source_candidates_from_traceback_text(completed.stderr, repo_root):
                    if candidate not in candidate_source_paths:
                        candidate_source_paths.append(candidate)
                for candidate in ranked_survey_sources:
                    if candidate not in candidate_source_paths:
                        candidate_source_paths.append(candidate)
                repaired_any = False
                for py_file in py_files:
                    if not repairs_allowed:
                        blockers.append("role_policy forbids source mutation repair")
                        blocked_repairs.append({"kind": "py_compile_repair", "path": py_file, "reason": "role_policy forbids source mutation repair"})
                        break
                    repair = repair_expected_colon(repo_root, py_file, completed.stderr)
                    if repair.get("applied"):
                        repairs.append(repair)
                        repaired_any = True
                if repaired_any:
                    completed = subprocess.run(cmd, cwd=repo_root, text=True, capture_output=True, check=False)
                    executed.append(
                        {
                            "command": " ".join(cmd),
                            "returncode": completed.returncode,
                            "stdout": completed.stdout[-4000:],
                            "stderr": completed.stderr[-4000:],
                            "after_repair": True,
                        }
                    )
                if completed.returncode != 0:
                    blockers.append("py_compile failed for changed Python files")
        if (repo_root / ".git").exists():
            cmd = ["git", "diff", "--check"]
            completed = subprocess.run(cmd, cwd=repo_root, text=True, capture_output=True, check=False)
            executed.append(
                {
                    "command": "git diff --check",
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                }
            )
            if completed.returncode != 0:
                blockers.append("git diff --check failed")
        raw_commands = patch.get("verification_commands") if isinstance(patch.get("verification_commands"), list) else []
        for raw_command in raw_commands:
            if not isinstance(raw_command, str) or not raw_command.strip():
                blockers.append("verification command must be a non-empty string")
                continue
            try:
                result = run_verification_command(repo_root, raw_command)
            except subprocess.TimeoutExpired:
                result = {"command": raw_command, "returncode": 124, "stdout": "", "stderr": "verification command timed out"}
            executed.append(result)
            if result.get("returncode") != 0:
                output = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
                for candidate in source_candidates_from_traceback_text(output, repo_root):
                    if candidate not in candidate_source_paths:
                        candidate_source_paths.append(candidate)
                for candidate in ranked_survey_sources:
                    if candidate not in candidate_source_paths:
                        candidate_source_paths.append(candidate)
                if not repairs_allowed:
                    repair = {"applied": False, "blocked": "role_policy forbids source mutation repair"}
                    blockers.append("role_policy forbids source mutation repair")
                    blocked_repairs.append({"kind": "command_repair", "command": raw_command, "reason": "role_policy forbids source mutation repair"})
                else:
                    repair = repair_import_error_missing_function(repo_root, py_files, output)
                if not repair.get("applied") and repairs_allowed:
                    repair = repair_name_error_return_literal(repo_root, py_files, output)
                if not repair.get("applied") and repairs_allowed:
                    repair = repair_assertion_return_mismatch(repo_root, py_files, output)
                if repair.get("applied"):
                    repairs.append(repair)
                    try:
                        result = run_verification_command(repo_root, raw_command)
                    except subprocess.TimeoutExpired:
                        result = {"command": raw_command, "returncode": 124, "stdout": "", "stderr": "verification command timed out"}
                    result["after_repair"] = True
                    executed.append(result)
                if result.get("returncode") != 0:
                    blockers.append(f"verification command failed: {raw_command}")
    report = {
        "status": "blocked" if blockers else "passed",
        "task_id": request.get("task_id"),
        "role_policy": role_policy,
        "task_profile": task_profile,
        "worker_brief": worker_brief,
        "repo_grade_workflow": patch.get("repo_grade_workflow", repo_grade_workflow_from_request(request, changed_files)),
        "verification_strategy": {
            "focused_commands": [
                item.get("command")
                for item in executed
                if isinstance(item, dict)
                and isinstance(item.get("command"), str)
                and item.get("command") != "git diff --check"
                and "unittest discover" not in item.get("command", "")
            ],
            "broad_commands": [
                item.get("command")
                for item in executed
                if isinstance(item, dict)
                and isinstance(item.get("command"), str)
                and ("unittest discover" in item.get("command", "") or item.get("command") == "git diff --check")
            ],
        },
        "commands": [
            "python -m py_compile <changed .py files>",
            "git diff --check",
        ],
        "executed": executed,
        "repairs": repairs,
        "blockers": blockers,
        "warnings": patch.get("warnings", []),
        "summary": "Verification passed for applied changes." if not blockers else "Verification is blocked or failed.",
    }
    failed_commands = [
        item
        for item in executed
        if isinstance(item, dict) and int(item.get("returncode") or 0) != 0
    ]
    repair_state = {
        "status": "blocked" if blockers else "passed",
        "task_id": request.get("task_id"),
        "role_policy": role_policy,
        "task_profile": task_profile,
        "worker_brief": worker_brief,
        "repairs_allowed": repairs_allowed,
        "repair_attempts": repairs,
        "blocked_repairs": blocked_repairs,
        "commands_executed_count": len(executed),
        "failed_commands": failed_commands,
        "candidate_source_paths": candidate_source_paths[:20],
        "pending_blockers": blockers,
        "next_action": "inspect_blockers_or_revision_plan" if blockers else "continue_to_code_review",
        "summary": "Repair loop state recorded for verification step.",
    }
    diagnostic_extraction = diagnostic_extraction_from_execution(patch, executed, candidate_source_paths, repo_root)
    write_json(workspace_root, output_path, report)
    write_json(workspace_root, sibling_artifact(output_path, "repair_loop_state.json"), repair_state)
    write_json(workspace_root, sibling_artifact(output_path, "diagnostic_extraction.json"), diagnostic_extraction)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": "Verification report written.",
        "artifacts": [
            output_path,
            sibling_artifact(output_path, "repair_loop_state.json"),
            sibling_artifact(output_path, "diagnostic_extraction.json"),
        ],
        "confidence": "medium",
    }
