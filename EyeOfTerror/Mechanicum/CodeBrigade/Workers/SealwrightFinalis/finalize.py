from __future__ import annotations

"""Final packaging role implementation."""

from common.codewright_core import *  # noqa: F403 - role modules use the shared Codewright helper surface.


def principal_evidence_summary(
    *,
    status: str,
    survey: dict[str, Any],
    patch: dict[str, Any],
    verification: dict[str, Any],
    review: dict[str, Any],
    problem_statement: dict[str, Any],
    architecture_options: dict[str, Any],
    architecture_decision_record: dict[str, Any],
    unshaped_repair_plan: dict[str, Any],
    diagnostic_extraction: dict[str, Any],
    repair_state: dict[str, Any],
    patch_package: dict[str, Any],
    pr_summary: dict[str, Any],
) -> dict[str, Any]:
    investigation = survey.get("engineering_investigation") if isinstance(survey.get("engineering_investigation"), dict) else {}
    readiness = patch.get("engineering_readiness") if isinstance(patch.get("engineering_readiness"), dict) else {}
    scope = patch.get("patch_scope_evidence") if isinstance(patch.get("patch_scope_evidence"), dict) else {}
    scope_review = review.get("patch_scope_review") if isinstance(review.get("patch_scope_review"), dict) else {}
    investigation_review = review.get("repository_investigation_review") if isinstance(review.get("repository_investigation_review"), dict) else {}
    review_record = review.get("decision_record") if isinstance(review.get("decision_record"), list) else []
    discipline = review.get("code_review_discipline") if isinstance(review.get("code_review_discipline"), dict) else {}
    verification_strategy = verification.get("verification_strategy") if isinstance(verification.get("verification_strategy"), dict) else {}
    executed = verification.get("executed") if isinstance(verification.get("executed"), list) else []
    changed_files = patch.get("changed_files") if isinstance(patch.get("changed_files"), list) else []
    source_excerpts = patch.get("source_excerpt_summary") if isinstance(patch.get("source_excerpt_summary"), list) else []
    patch_source = str(patch.get("patch_source") or "")
    unshaped_required = is_unshaped_patch_source(patch_source)
    acceptance_criteria = readiness.get("acceptance_criteria") if isinstance(readiness.get("acceptance_criteria"), list) else []
    impact_matrix = readiness.get("impact_matrix") if isinstance(readiness.get("impact_matrix"), list) else []
    focused_commands = verification_strategy.get("focused_commands") if isinstance(verification_strategy.get("focused_commands"), list) else []
    broad_commands = verification_strategy.get("broad_commands") if isinstance(verification_strategy.get("broad_commands"), list) else []
    changed_sources_with_linked_tests = (
        scope.get("changed_sources_with_linked_tests")
        if isinstance(scope.get("changed_sources_with_linked_tests"), list)
        else []
    )
    checks = {
        "ready_and_approved": status == "ready" and review.get("approved") is True,
        "problem_and_options_recorded": problem_statement.get("status") == "recorded"
        and architecture_options.get("status") == "recorded",
        "investigation_depth": investigation_review.get("status") == "covered"
        and bool(investigation.get("targeted_reading_plan"))
        and bool(investigation.get("hypotheses"))
        and any(isinstance(item, dict) and item.get("status") == "read" for item in source_excerpts),
        "acceptance_and_impact_model": len(acceptance_criteria) >= 5 and bool(impact_matrix),
        "scope_and_rollback_control": scope_review.get("status") == "covered"
        and bool(changed_files)
        and bool(pr_summary.get("rollback")),
        "verification_after_mutation": verification.get("status") == "passed"
        and len(executed) >= 3
        and bool(focused_commands),
        "broad_or_repo_grade_coverage": bool(broad_commands)
        or patch_package.get("workflow_mode") == "repo_grade"
        or (len(executed) >= 3 and bool(changed_sources_with_linked_tests)),
        "review_gate_rich": len(review_record) >= 12
        and int(discipline.get("blocker_count") or 0) == 0
        and all(isinstance(item, dict) and item.get("status") != "blocker" for item in review_record),
        "architecture_and_package_recorded": architecture_decision_record.get("status") == "recorded"
        and patch_package.get("kind") == "ceraxia_patch_package"
        and bool(patch_package.get("review_decision_record")),
        "diagnostic_or_repair_trace": (
            not unshaped_required
            or (
                unshaped_repair_plan.get("mode") == "unshaped_repo_repair"
                and diagnostic_extraction.get("status") == "recorded"
            )
        ),
        "repair_loop_accounted": repair_state.get("status") in {"passed", "not_required", "unknown"}
        and isinstance(repair_state.get("repair_attempts", []), list),
    }
    missing = [name for name, passed in checks.items() if passed is not True]
    return {
        "kind": "ceraxia_principal_evidence_summary",
        "status": "complete" if not missing else "partial",
        "checks": checks,
        "missing_checks": missing,
        "strength_count": sum(1 for passed in checks.values() if passed is True),
        "check_count": len(checks),
        "score_ceiling_hint": 9.75 if not missing else 9.4,
        "evidence_sources": [
            "problem_statement.json",
            "architecture_options.json",
            "repo_survey.json",
            "patch_manifest.json",
            "verification_report.json",
            "repair_loop_state.json",
            "code_review.json",
        ],
    }

def run_finalize(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    survey = load_json_optional(workspace_root, sibling_artifact(output_path, "repo_survey.json"))
    problem_statement = load_json_optional(workspace_root, sibling_artifact(output_path, "problem_statement.json"))
    architecture_options = load_json_optional(workspace_root, sibling_artifact(output_path, "architecture_options.json"))
    patch = load_json_optional(workspace_root, sibling_artifact(output_path, "patch_manifest.json"))
    verification = load_json_optional(workspace_root, sibling_artifact(output_path, "verification_report.json"))
    repair_state = load_json_optional(workspace_root, sibling_artifact(output_path, "repair_loop_state.json"))
    unshaped_repair_plan = load_json_optional(workspace_root, sibling_artifact(output_path, "unshaped_repair_plan.json"))
    diagnostic_extraction = load_json_optional(workspace_root, sibling_artifact(output_path, "diagnostic_extraction.json"))
    review = load_json_optional(workspace_root, sibling_artifact(output_path, "code_review.json"))
    role_policy = role_policy_from_request(request)
    task_profile = task_profile_from_request(request)
    worker_brief = worker_brief_from_request(request)
    model_guidance = code_model_guidance(request, "final package validation, residual risk summary, and next safe action")
    status = "blocked" if review.get("approved") is False else "ready"
    changed_files = patch.get("changed_files", []) if isinstance(patch.get("changed_files"), list) else []
    repo_grade_workflow = patch.get("repo_grade_workflow") if isinstance(patch.get("repo_grade_workflow"), dict) else repo_grade_workflow_from_request(request, changed_files)
    architecture_decision_record = patch.get("architecture_decision_record") if isinstance(patch.get("architecture_decision_record"), dict) else {}
    ast_patch_plan = patch.get("ast_patch_plan") if isinstance(patch.get("ast_patch_plan"), dict) else {}
    verification_strategy = verification.get("verification_strategy") if isinstance(verification.get("verification_strategy"), dict) else {}
    changed_file_paths = [
        str(item.get("path"))
        for item in changed_files
        if isinstance(item, dict) and item.get("path")
    ]
    pr_summary = {
        "title": "Ceraxia code task package",
        "status": status,
        "scope": changed_file_paths,
        "verification": {
            "status": verification.get("status", "unknown"),
            "focused_commands": verification_strategy.get("focused_commands", []),
            "broad_commands": verification_strategy.get("broad_commands", []),
            "repair_count": len(verification.get("repairs", [])) if isinstance(verification.get("repairs"), list) else 0,
        },
        "architecture": architecture_decision_record,
        "risks": patch.get("engineering_readiness", {}).get("risk_register", []) if isinstance(patch.get("engineering_readiness"), dict) else [],
        "rollback": architecture_decision_record.get("rollback") or "Revert changed files from patch_package.changed_files.",
        "next_safe_action": "handoff_to_patch_worker" if status == "blocked" else "inspect_final_package",
    }
    patch_package = {
        "kind": "ceraxia_patch_package",
        "workflow_mode": repo_grade_workflow.get("mode"),
        "changed_files": changed_files,
        "patch_source": patch.get("patch_source", ""),
        "operation_count": patch.get("operation_count", 0),
        "problem_statement": problem_statement,
        "architecture_options": architecture_options,
        "architecture_decision_record": architecture_decision_record,
        "verification_strategy": verification_strategy,
        "unshaped_repair_plan": unshaped_repair_plan,
        "diagnostic_extraction": diagnostic_extraction,
        "ast_patch_plan": ast_patch_plan,
        "review_decision_record": review.get("decision_record", []),
        "review_repair_loop": review.get("review_repair_loop", {}),
        "pr_summary": pr_summary,
    }
    principal_summary = principal_evidence_summary(
        status=status,
        survey=survey,
        patch=patch,
        verification=verification,
        review=review,
        problem_statement=problem_statement,
        architecture_options=architecture_options,
        architecture_decision_record=architecture_decision_record,
        unshaped_repair_plan=unshaped_repair_plan,
        diagnostic_extraction=diagnostic_extraction,
        repair_state=repair_state,
        patch_package=patch_package,
        pr_summary=pr_summary,
    )
    manifest = {
        "status": status,
        "approved": review.get("approved") is True,
        "role_policy": role_policy,
        "task_profile": task_profile,
        "worker_brief": worker_brief,
        "role_policies": {
            "implementation": patch.get("role_policy", {}),
            "verification": verification.get("role_policy", {}),
            "code_review": review.get("role_policy", {}),
            "finalize": role_policy,
        },
        "deliverables": [
            sibling_artifact(output_path, "repo_survey.json"),
            sibling_artifact(output_path, "problem_statement.json"),
            sibling_artifact(output_path, "architecture_options.json"),
            sibling_artifact(output_path, "change_plan.md"),
            sibling_artifact(output_path, "patch_manifest.json"),
            sibling_artifact(output_path, "unshaped_repair_plan.json"),
            sibling_artifact(output_path, "verification_report.json"),
            sibling_artifact(output_path, "repair_loop_state.json"),
            sibling_artifact(output_path, "diagnostic_extraction.json"),
            sibling_artifact(output_path, "code_review.json"),
        ],
        "changed_files": changed_files,
        "repo_grade_workflow": repo_grade_workflow,
        "unshaped_repair_plan": unshaped_repair_plan,
        "diagnostic_extraction": diagnostic_extraction,
        "ast_patch_plan": ast_patch_plan,
        "problem_statement": problem_statement,
        "architecture_options": architecture_options,
        "architecture_decision_record": architecture_decision_record,
        "verification_strategy": verification_strategy,
        "patch_package": patch_package,
        "pr_summary": pr_summary,
        "recommended_read_order": patch.get("recommended_read_order", []),
        "engineering_investigation": survey.get("engineering_investigation", {}) if isinstance(survey.get("engineering_investigation"), dict) else {},
        "engineering_readiness": patch.get("engineering_readiness", {}),
        "engineering_readiness_review": review.get("engineering_readiness_review", {}),
        "architect_review": review.get("architect_review", {}),
        "code_review_discipline": review.get("code_review_discipline", {}),
        "repository_investigation_review": review.get("repository_investigation_review", {}),
        "patch_scope_evidence": patch.get("patch_scope_evidence", {}),
        "patch_source": patch.get("patch_source", ""),
        "patch_candidates": patch.get("patch_candidates", []),
        "selected_patch_candidate": patch.get("selected_patch_candidate", {}),
        "dirty_worktree": patch.get("dirty_worktree", {}),
        "ambiguity_analysis": patch.get("ambiguity_analysis", {}),
        "source_excerpt_summary": patch.get("source_excerpt_summary", []),
        "implementation_decision_record": patch.get("implementation_decision_record", []),
        "diagnostics": patch.get("diagnostics", {}),
        "operation_count": patch.get("operation_count", 0),
        "verification_status": verification.get("status", "unknown"),
        "verification_executed": verification.get("executed", []),
        "verification_repairs": verification.get("repairs", []),
        "repair_loop_state": repair_state,
        "verification_blockers": verification.get("blockers", []),
        "verification_summary": {
            "executed_count": len(verification.get("executed", [])) if isinstance(verification.get("executed"), list) else 0,
            "repair_count": len(verification.get("repairs", [])) if isinstance(verification.get("repairs"), list) else 0,
            "blocker_count": len(verification.get("blockers", [])) if isinstance(verification.get("blockers"), list) else 0,
        },
        "execution_report": {
            "task_profile": task_profile,
            "worker_briefs_present": {
                "repository_survey": bool(load_json_optional(workspace_root, sibling_artifact(output_path, "repo_survey.json")).get("worker_brief")),
                "implementation": bool(patch.get("worker_brief")),
                "verification": bool(verification.get("worker_brief")),
                "code_review": bool(review.get("worker_brief")),
                "finalize": bool(worker_brief),
            },
            "changed_file_count": len(patch.get("changed_files", [])) if isinstance(patch.get("changed_files"), list) else 0,
            "verification_command_count": len(verification.get("executed", [])) if isinstance(verification.get("executed"), list) else 0,
            "repair_attempt_count": len(repair_state.get("repair_attempts", [])) if isinstance(repair_state.get("repair_attempts"), list) else 0,
            "patch_candidate_count": len(patch.get("patch_candidates", [])) if isinstance(patch.get("patch_candidates"), list) else 0,
            "source_excerpt_count": len(patch.get("source_excerpt_summary", [])) if isinstance(patch.get("source_excerpt_summary"), list) else 0,
            "acceptance_criteria_count": len(
                patch.get("engineering_readiness", {}).get("acceptance_criteria", [])
                if isinstance(patch.get("engineering_readiness", {}), dict)
                and isinstance(patch.get("engineering_readiness", {}).get("acceptance_criteria"), list)
                else []
            ),
            "risk_count": len(
                patch.get("engineering_readiness", {}).get("risk_register", [])
                if isinstance(patch.get("engineering_readiness", {}), dict)
                and isinstance(patch.get("engineering_readiness", {}).get("risk_register"), list)
                else []
            ),
            "impact_file_count": len(
                patch.get("engineering_readiness", {}).get("impact_matrix", [])
                if isinstance(patch.get("engineering_readiness", {}), dict)
                and isinstance(patch.get("engineering_readiness", {}).get("impact_matrix"), list)
                else []
            ),
            "blocker_count": len([item.get("message") for item in review.get("findings", []) if isinstance(item, dict)]),
            "revision_required": bool(review.get("revision_plan", {}).get("required")) if isinstance(review.get("revision_plan"), dict) else False,
        },
        "principal_evidence_summary": principal_summary,
        "model_guidance": {
            "finalizer": model_guidance,
            "survey": survey.get("model_guidance", {}) if isinstance(survey.get("model_guidance"), dict) else {},
            "patch": patch.get("model_guidance", {}) if isinstance(patch.get("model_guidance"), dict) else {},
            "verification": verification.get("model_guidance", {}) if isinstance(verification.get("model_guidance"), dict) else {},
            "review": review.get("model_guidance_review", {}) if isinstance(review.get("model_guidance_review"), dict) else {},
        },
        "review_status": review.get("status", "unknown"),
        "patch_scope_review": review.get("patch_scope_review", {}),
        "review_decision_record": review.get("decision_record", []),
        "review_repair_loop": review.get("review_repair_loop", {}),
        "blockers": [item.get("message") for item in review.get("findings", []) if isinstance(item, dict)],
        "next_safe_action": "handoff_to_patch_worker" if status == "blocked" else "inspect_final_package",
        "summary": "Ceraxia code task package finalized.",
        "revision_plan": review.get("revision_plan", {"required": False, "steps": []}),
    }
    write_json(workspace_root, output_path, manifest)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": status,
        "summary": manifest["summary"],
        "artifacts": [output_path],
        "revision_plan": manifest["revision_plan"],
        "confidence": "medium",
        "model_guidance": model_guidance,
    }
