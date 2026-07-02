from __future__ import annotations

"""Patch implementation role implementation."""

from common.codewright_core import *  # noqa: F403 - role modules use the shared Codewright helper surface.


def run_implementation(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    plan = read_text_optional(workspace_root, sibling_artifact(output_path, "change_plan.md"))
    survey = load_json_optional(workspace_root, sibling_artifact(output_path, "repo_survey.json"))
    problem_statement = load_json_optional(workspace_root, sibling_artifact(output_path, "problem_statement.json"))
    architecture_options = load_json_optional(workspace_root, sibling_artifact(output_path, "architecture_options.json"))
    readiness = survey.get("engineering_readiness") if isinstance(survey.get("engineering_readiness"), dict) else {}
    role_policy = role_policy_from_request(request)
    task_profile = task_profile_from_request(request)
    worker_brief = worker_brief_from_request(request)
    blockers: list[str] = []
    changed_files: list[dict[str, Any]] = []
    rolled_back_files: list[dict[str, Any]] = []
    patch_spec: dict[str, Any] = {}
    repo_root = target_repo_root(request)
    excerpts = source_excerpt_pack(workspace_root, output_path, repo_root)
    patch_resolution = {"patch_spec": {}, "candidates": [], "selected_candidate": {}}
    dirty_worktree = {"git_repo": False, "dirty_targets": []}
    ambiguity_analysis: dict[str, Any] = {}
    ast_patch_plan: dict[str, Any] = {}
    try:
        patch_resolution = patch_spec_resolution_from_request(request)
        patch_spec = patch_resolution["patch_spec"] if isinstance(patch_resolution.get("patch_spec"), dict) else {}
        if patch_spec:
            ast_patch_plan = ast_patch_plan_from_spec(repo_root, patch_spec)
            for blocker in ast_patch_plan.get("blockers", []) if isinstance(ast_patch_plan.get("blockers"), list) else []:
                blockers.append(str(blocker))
            if ast_patch_plan.get("status") == "missing" and ast_patch_plan_required_for_source(str(patch_spec.get("source") or "")):
                blockers.append("AST minimal patch plan is required for this inferred repair but was not produced.")
            if not role_policy_allows_source_mutation(role_policy):
                blockers.append("role_policy forbids source mutation for this step")
            elif not blockers:
                operations = patch_spec["operations"] if isinstance(patch_spec.get("operations"), list) else []
                dirty_worktree = git_dirty_target_evidence(repo_root, operations)
                dirty_targets = dirty_worktree.get("dirty_targets") if isinstance(dirty_worktree.get("dirty_targets"), list) else []
                if dirty_targets:
                    dirty_paths = ", ".join(str(item.get("path")) for item in dirty_targets if isinstance(item, dict))
                    blockers.append(f"target file has uncommitted user changes; refusing source mutation: {dirty_paths}")
                else:
                    changed_files.extend(apply_patch_operations_atomically(repo_root, operations))
        else:
            ambiguity_analysis = ambiguity_analysis_from_goal(request_goal(request), repo_root)
            if ambiguity_analysis:
                blockers.append("Ambiguous code task requires clarification before source mutation.")
            else:
                blockers.append(
                    "No patch candidate could be selected from explicit contract, task text, or test evidence."
                )
    except PatchApplyError as exc:
        blockers.append(str(exc))
        rolled_back_files = exc.rolled_back_files
    except ValueError as exc:
        blockers.append(str(exc))
    status = "applied" if changed_files and not blockers else "handoff_required"
    repo_grade_workflow = repo_grade_workflow_from_request(request, changed_files)
    architecture_decision_record = architecture_decision_record_from_evidence(request, survey, changed_files)
    unshaped_repair_plan = unshaped_repair_plan_from_resolution(request, survey, patch_resolution, patch_spec, excerpts)
    manifest = {
        "status": status,
        "mode": "explicit_patch_apply" if status == "applied" else "auditable_handoff",
        "task_id": request.get("task_id"),
        "summary": "Ceraxia applied scoped patch operations." if status == "applied" else "Ceraxia prepared implementation intent, but no source files were mutated by this worker.",
        "intended_actions": [
            "read concrete target files before editing",
            "apply minimal scoped patch",
            "run verification commands from verification_report.json",
            "return focused revision steps on failure",
        ],
        "plan_excerpt": plan[:3000],
        "problem_statement": problem_statement,
        "architecture_options": architecture_options,
        "role_policy": role_policy,
        "task_profile": task_profile,
        "worker_brief": worker_brief,
        "dirty_worktree": dirty_worktree,
        "ambiguity_analysis": ambiguity_analysis,
        "patch_spec_present": bool(patch_spec),
        "patch_source": str(patch_spec.get("source") or "explicit_json_patch") if patch_spec else "",
        "patch_candidates": patch_resolution.get("candidates", []) if isinstance(patch_resolution.get("candidates"), list) else [],
        "selected_patch_candidate": patch_resolution.get("selected_candidate", {})
        if isinstance(patch_resolution.get("selected_candidate"), dict)
        else {},
        "source_excerpt_pack": excerpts,
        "source_excerpt_summary": [
            {
                "path": item.get("path", ""),
                "status": item.get("status", ""),
                "bytes": item.get("bytes", 0),
                "truncated": item.get("truncated", False),
            }
            for item in excerpts
        ],
        "implementation_decision_record": [
            {
                "check": "source_evidence_loaded",
                "status": "pass" if any(item.get("status") == "read" for item in excerpts) else "warn",
                "detail": f"{sum(1 for item in excerpts if item.get('status') == 'read')} targeted files read",
            },
            {
                "check": "patch_candidate_selected",
                "status": "pass" if patch_spec else "fail",
                "detail": str(
                    (
                        patch_resolution.get("selected_candidate", {})
                        if isinstance(patch_resolution.get("selected_candidate"), dict)
                        else {}
                    ).get("source")
                    or "none"
                ),
            },
            {
                "check": "mutation_authority",
                "status": "pass" if role_policy_allows_source_mutation(role_policy) else "blocked",
                "detail": str(role_policy.get("authority") or "default_source_mutation_allowed"),
            },
        ],
        "architecture_decision_record": architecture_decision_record,
        "repo_grade_workflow": repo_grade_workflow,
        "unshaped_repair_plan": unshaped_repair_plan,
        "ast_patch_plan": ast_patch_plan,
        "diagnostics": patch_spec.get("diagnostics", {}) if isinstance(patch_spec.get("diagnostics"), dict) else {},
        "operation_count": len(patch_spec.get("operations", [])) if isinstance(patch_spec.get("operations"), list) else 0,
        "changed_files": changed_files,
        "recommended_read_order": recommended_read_order_from_survey(workspace_root, output_path),
        "engineering_readiness": readiness,
        "patch_scope_evidence": patch_scope_evidence(
            workspace_root,
            output_path,
            changed_files,
            patch_spec.get("diagnostics", {}) if isinstance(patch_spec.get("diagnostics"), dict) else {},
        ),
        "rollback": {
            "applied": bool(rolled_back_files),
            "files": rolled_back_files,
        },
        "verification_commands": patch_spec.get("verification_commands", []) if isinstance(patch_spec.get("verification_commands"), list) else [],
        "blockers": blockers,
        "warnings": [
            "Patch was selected from Ceraxia's guarded patch contracts or safe inference modes; broad synthesis still requires explicit evidence.",
        ] if status == "applied" else [
            "The current package is an auditable implementation handoff, not a completed code change.",
        ]
    }
    write_json(workspace_root, output_path, manifest)
    write_json(workspace_root, sibling_artifact(output_path, "unshaped_repair_plan.json"), unshaped_repair_plan)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": "Patch manifest written with applied changes." if status == "applied" else "Patch manifest written as auditable handoff; source mutation remains blocked.",
        "artifacts": [output_path, sibling_artifact(output_path, "unshaped_repair_plan.json")],
        "confidence": "medium",
    }
