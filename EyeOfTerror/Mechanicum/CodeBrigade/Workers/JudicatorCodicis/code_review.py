from __future__ import annotations

"""Code review role implementation."""

from common.codewright_core import *  # noqa: F403 - role modules use the shared Codewright helper surface.


def run_code_review(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    survey = load_json_optional(workspace_root, sibling_artifact(output_path, "repo_survey.json"))
    problem_statement = load_json_optional(workspace_root, sibling_artifact(output_path, "problem_statement.json"))
    architecture_options = load_json_optional(workspace_root, sibling_artifact(output_path, "architecture_options.json"))
    patch = load_json_optional(workspace_root, sibling_artifact(output_path, "patch_manifest.json"))
    verification = load_json_optional(workspace_root, sibling_artifact(output_path, "verification_report.json"))
    repair_state = load_json_optional(workspace_root, sibling_artifact(output_path, "repair_loop_state.json"))
    unshaped_repair_plan = load_json_optional(workspace_root, sibling_artifact(output_path, "unshaped_repair_plan.json"))
    diagnostic_extraction = load_json_optional(workspace_root, sibling_artifact(output_path, "diagnostic_extraction.json"))
    problem_statement = load_json_optional(workspace_root, sibling_artifact(output_path, "problem_statement.json"))
    architecture_options = load_json_optional(workspace_root, sibling_artifact(output_path, "architecture_options.json"))
    role_policy = role_policy_from_request(request)
    task_profile = task_profile_from_request(request)
    worker_brief = worker_brief_from_request(request)
    blockers = verification.get("blockers") if isinstance(verification.get("blockers"), list) else []
    warnings = verification.get("warnings") if isinstance(verification.get("warnings"), list) else []
    scope = patch.get("patch_scope_evidence") if isinstance(patch.get("patch_scope_evidence"), dict) else {}
    readiness = patch.get("engineering_readiness") if isinstance(patch.get("engineering_readiness"), dict) else {}
    readiness_checks = readiness.get("readiness_checks") if isinstance(readiness.get("readiness_checks"), dict) else {}
    acceptance_criteria = readiness.get("acceptance_criteria") if isinstance(readiness.get("acceptance_criteria"), list) else []
    risk_register = readiness.get("risk_register") if isinstance(readiness.get("risk_register"), list) else []
    impact_matrix = readiness.get("impact_matrix") if isinstance(readiness.get("impact_matrix"), list) else []
    scope_review = patch_scope_review(scope)
    investigation_review = repository_investigation_review(survey, patch, scope_review)
    patch_source = str(patch.get("patch_source") or "")
    diagnostics = patch.get("diagnostics") if isinstance(patch.get("diagnostics"), dict) else {}
    ast_patch_plan = patch.get("ast_patch_plan") if isinstance(patch.get("ast_patch_plan"), dict) else {}
    changed_files = patch.get("changed_files") if isinstance(patch.get("changed_files"), list) else []
    repo_grade_workflow = patch.get("repo_grade_workflow") if isinstance(patch.get("repo_grade_workflow"), dict) else repo_grade_workflow_from_request(request, changed_files)
    architecture_decision_record = patch.get("architecture_decision_record") if isinstance(patch.get("architecture_decision_record"), dict) else {}
    verification_strategy = verification.get("verification_strategy") if isinstance(verification.get("verification_strategy"), dict) else {}
    focused_commands = verification_strategy.get("focused_commands") if isinstance(verification_strategy.get("focused_commands"), list) else []
    broad_commands = verification_strategy.get("broad_commands") if isinstance(verification_strategy.get("broad_commands"), list) else []
    changed_sources_with_linked_tests = (
        scope.get("changed_sources_with_linked_tests")
        if isinstance(scope.get("changed_sources_with_linked_tests"), list)
        else []
    )
    repo_grade_mode = repo_grade_workflow.get("mode") == "repo_grade"
    public_surface_review = public_surface_review_from_evidence(patch_source, diagnostics, changed_files, broad_commands)
    discipline_findings = code_review_discipline_findings(
        patch_source,
        changed_files,
        target_repo_root(request),
        diagnostics,
        ast_patch_plan,
    )
    unshaped_required = is_unshaped_patch_source(patch_source)
    ast_patch_required = ast_patch_plan_required_for_source(patch_source)
    failed_commands = repair_state.get("failed_commands") if isinstance(repair_state.get("failed_commands"), list) else []
    candidate_source_paths = repair_state.get("candidate_source_paths") if isinstance(repair_state.get("candidate_source_paths"), list) else []
    decision_record: list[dict[str, Any]] = [
        {
            "check": "patch_applied",
            "status": "pass" if patch.get("status") == "applied" else "blocker",
            "evidence": patch.get("status", "unknown"),
        },
        {
            "check": "verification_passed",
            "status": "pass" if verification.get("status") == "passed" else "blocker",
            "evidence": verification.get("status", "unknown"),
        },
        {
            "check": "scope_review",
            "status": str(scope_review.get("status") or "unknown"),
            "evidence": scope_review,
        },
        {
            "check": "diagnostic_linkage",
            "status": "pass" if (not patch_source.startswith("test_inferred_") or diagnostics) else "blocker",
            "evidence": diagnostics,
        },
        {
            "check": "unshaped_repair_plan_present",
            "status": "pass"
            if (not unshaped_required or unshaped_repair_plan.get("mode") == "unshaped_repo_repair")
            else "blocker",
            "evidence": {
                "required": unshaped_required,
                "status": unshaped_repair_plan.get("status", ""),
                "mode": unshaped_repair_plan.get("mode", ""),
                "hypothesis_count": len(unshaped_repair_plan.get("defect_hypotheses", []))
                if isinstance(unshaped_repair_plan.get("defect_hypotheses"), list)
                else 0,
                "candidate_count": len(unshaped_repair_plan.get("minimal_patch_candidates", []))
                if isinstance(unshaped_repair_plan.get("minimal_patch_candidates"), list)
                else 0,
            },
        },
        {
            "check": "diagnostic_extraction_present",
            "status": "pass"
            if (not unshaped_required or diagnostic_extraction.get("status") == "recorded")
            else "blocker",
            "evidence": diagnostic_extraction.get("parser_coverage", {}),
        },
        {
            "check": "ast_minimal_patch_plan_present",
            "status": "pass"
            if (not ast_patch_required or ast_patch_plan.get("status") == "recorded")
            else "blocker",
            "evidence": {
                "required": ast_patch_required,
                "status": ast_patch_plan.get("status", ""),
                "operation_count": ast_patch_plan.get("operation_count", 0),
                "blockers": ast_patch_plan.get("blockers", []),
            },
        },
        {
            "check": "readiness_model_present",
            "status": "pass" if readiness_checks.get("has_acceptance_criteria") and readiness_checks.get("has_test_strategy") else "blocker",
            "evidence": readiness_checks,
        },
        {
            "check": "impact_matrix_present",
            "status": "pass" if impact_matrix else "warning",
            "evidence": {"impact_file_count": len(impact_matrix)},
        },
        {
            "check": "repository_investigation_review",
            "status": "pass" if investigation_review.get("status") == "covered" else "blocker",
            "evidence": investigation_review,
        },
        {
            "check": "problem_statement_present",
            "status": "pass" if problem_statement.get("status") == "recorded" else "blocker",
            "evidence": problem_statement,
        },
        {
            "check": "architecture_options_present",
            "status": "pass" if architecture_options.get("status") == "recorded" else "blocker",
            "evidence": architecture_options,
        },
        {
            "check": "architecture_decision_record_present",
            "status": "pass" if (not repo_grade_mode or architecture_decision_record.get("status") == "recorded") else "blocker",
            "evidence": architecture_decision_record,
        },
        {
            "check": "focused_verification_present",
            "status": "pass" if focused_commands or verification.get("status") == "passed" else "blocker",
            "evidence": focused_commands,
        },
        {
            "check": "broad_verification_present",
            "status": "pass" if (not repo_grade_mode or broad_commands) else "blocker",
            "evidence": broad_commands,
        },
        {
            "check": "public_surface_review",
            "status": "pass" if public_surface_review.get("status") == "covered" else "blocker",
            "evidence": public_surface_review,
        },
        {
            "check": "review_discipline_findings",
            "status": "pass" if not discipline_findings else "blocker",
            "evidence": discipline_findings,
        },
    ]
    review_warnings = [
        {"severity": "warning", "message": str(item)}
        for item in warnings
    ]
    if scope_review.get("unmapped_changed_file_count", 0):
        files = ", ".join(scope_review.get("unmapped_changed_files", [])[:5])
        review_warnings.append(
            {
                "severity": "warning",
                "message": f"Changed file(s) outside ranked repo map should be manually checked for scope drift: {files}",
            }
        )
    if scope_review.get("source_without_linked_tests"):
        files = ", ".join(scope_review.get("source_without_linked_tests", [])[:5])
        review_warnings.append(
            {
                "severity": "warning",
                "message": f"Changed source file(s) have no static linked tests in repo map; verify coverage manually: {files}",
            }
        )
    if patch.get("status") != "applied":
        blockers = [*blockers, "Patch manifest was not applied."]
    if verification.get("status") != "passed":
        blockers = [*blockers, "Verification did not pass."]
    if patch_source.startswith("test_inferred_") and not diagnostics:
        blockers = [*blockers, "Test-inferred patch lacks diagnostics linking test evidence to source mutation."]
    if unshaped_required and unshaped_repair_plan.get("mode") != "unshaped_repo_repair":
        blockers = [*blockers, "Unshaped repair lacks a recorded repair plan."]
    if unshaped_required and diagnostic_extraction.get("status") != "recorded":
        blockers = [*blockers, "Unshaped repair lacks diagnostic extraction evidence."]
    if ast_patch_required and ast_patch_plan.get("status") != "recorded":
        blockers = [*blockers, "Inferred source repair lacks AST minimal patch plan evidence."]
    if investigation_review.get("status") != "covered":
        for item in investigation_review.get("blockers", []) if isinstance(investigation_review.get("blockers"), list) else []:
            check_name = str(item.get("check") or "repository_investigation")
            blockers = [*blockers, f"Repository investigation is incomplete: {check_name}."]
    if not acceptance_criteria:
        blockers = [*blockers, "Engineering readiness model lacks acceptance criteria."]
    if not readiness_checks.get("has_test_strategy"):
        blockers = [*blockers, "Engineering readiness model lacks test strategy."]
    if problem_statement.get("status") != "recorded":
        blockers = [*blockers, "Change plan lacks machine-readable problem_statement evidence."]
    if architecture_options.get("status") != "recorded":
        blockers = [*blockers, "Change plan lacks machine-readable architecture_options evidence."]
    if repo_grade_mode and architecture_decision_record.get("status") != "recorded":
        blockers = [*blockers, "Repo-grade task lacks architecture decision record."]
    if repo_grade_mode and not broad_commands:
        blockers = [*blockers, "Repo-grade task lacks broad verification evidence."]
    if public_surface_review.get("status") != "covered":
        for item in public_surface_review.get("blockers", []) if isinstance(public_surface_review.get("blockers"), list) else []:
            blockers = [*blockers, f"Public surface review failed: {item.get('check', 'unknown')}."]
    for finding in discipline_findings:
        blockers = [*blockers, str(finding.get("message") or finding.get("check") or "Code review discipline finding.")]
    high_risks = [item for item in risk_register if isinstance(item, dict) and item.get("severity") == "high"]
    if high_risks and not changed_files:
        blockers = [*blockers, "High-risk task has no applied source change or explicit handoff resolution."]
    focused_revision_context = {
        "candidate_source_paths": [str(item) for item in candidate_source_paths[:12]],
        "changed_files": [
            str(item.get("path"))
            for item in changed_files
            if isinstance(item, dict) and item.get("path")
        ][:12],
        "failed_commands": [
            str(item.get("command"))
            for item in failed_commands
            if isinstance(item, dict) and item.get("command")
        ][:8],
        "patch_source": patch_source,
        "diagnostics": diagnostics,
        "engineering_readiness": {
            "acceptance_criteria": acceptance_criteria,
            "risk_register": risk_register,
            "impact_matrix": impact_matrix,
            "readiness_checks": readiness_checks,
        },
        "architecture_decision_record": architecture_decision_record,
        "problem_statement": problem_statement,
        "architecture_options": architecture_options,
        "repo_grade_workflow": repo_grade_workflow,
        "unshaped_repair_plan": unshaped_repair_plan,
        "diagnostic_extraction": diagnostic_extraction,
        "ast_patch_plan": ast_patch_plan,
        "verification_strategy": verification_strategy,
        "repository_investigation_review": investigation_review,
        "public_surface_review": public_surface_review,
        "unshaped_repair_review": {
            "required": unshaped_required,
            "plan_present": unshaped_repair_plan.get("mode") == "unshaped_repo_repair",
            "diagnostic_extraction_present": diagnostic_extraction.get("status") == "recorded",
            "hypothesis_count": len(unshaped_repair_plan.get("defect_hypotheses", []))
            if isinstance(unshaped_repair_plan.get("defect_hypotheses"), list)
            else 0,
        },
        "ast_patch_review": {
            "required": ast_patch_required,
            "plan_present": ast_patch_plan.get("status") == "recorded",
            "operation_count": ast_patch_plan.get("operation_count", 0),
        },
    }
    revision_steps = [
        {
            "step_id": "implementation",
            "worker": "FerrumPatchwright",
            "reason": "Rebuild the patch from focused_context and preserve diagnostic linkage.",
            "source": "code_review",
            "priority": "blocker",
        },
        {
            "step_id": "verification",
            "worker": "OrdinatusVerifier",
            "reason": "Rerun allowlisted verification and preserve failed command output if it still fails.",
            "source": "code_review",
            "priority": "blocker",
        },
    ] if blockers else []
    review_repair_loop = {
        "required": bool(blockers),
        "trigger": "code_review_blockers" if blockers else "",
        "blocked_checks": [
            str(item.get("check"))
            for item in decision_record
            if isinstance(item, dict) and item.get("status") == "blocker"
        ],
        "rerun_steps": revision_steps,
        "focused_context": focused_revision_context if blockers else {},
        "completion_gate": "rerun implementation and verification, then rerun code_review and finalize",
    }
    review = {
        "status": "blocked" if blockers else "passed",
        "approved": not blockers,
        "role_policy": role_policy,
        "task_profile": task_profile,
        "worker_brief": worker_brief,
        "repair_loop_status": repair_state.get("status", "unknown"),
        "patch_scope_review": scope_review,
        "engineering_readiness_review": {
            "readiness_checks": readiness_checks,
            "acceptance_criteria_count": len(acceptance_criteria),
            "risk_count": len(risk_register),
            "high_risk_count": len(high_risks),
            "impact_file_count": len(impact_matrix),
        },
        "repo_grade_workflow_review": {
            "mode": repo_grade_workflow.get("mode"),
            "architecture_decision_record_present": architecture_decision_record.get("status") == "recorded",
            "focused_verification_count": len(focused_commands),
            "broad_verification_count": len(broad_commands),
        },
        "code_review_discipline": {
            "findings": discipline_findings,
            "blocker_count": len([item for item in discipline_findings if item.get("status") == "blocker"]),
        },
        "architect_review": {
            "problem_statement_present": problem_statement.get("status") == "recorded",
            "architecture_options_present": architecture_options.get("status") == "recorded",
            "architecture_decision_record_present": architecture_decision_record.get("status") == "recorded",
        },
        "ast_patch_review": focused_revision_context.get("ast_patch_review", {}),
        "repository_investigation_review": investigation_review,
        "public_surface_review": public_surface_review,
        "decision_record": decision_record,
        "review_repair_loop": review_repair_loop,
        "findings": [
            {"severity": "blocker", "message": str(item)}
            for item in blockers
        ],
        "warnings": [
            *review_warnings,
            {
                "severity": "warning",
                "message": "Ceraxia supports explicit, marker-synthesized, and guarded test-inferred patches; broader repo-grade synthesis must still block with evidence.",
            }
        ],
        "revision_plan": {
            "required": bool(blockers),
            "focused_context": focused_revision_context if blockers else {},
            "steps": revision_steps,
        },
    }
    write_json(workspace_root, output_path, review)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "needs_revision" if blockers else "passed",
        "summary": f"Code review written with {len(blockers)} blocker(s).",
        "artifacts": [output_path],
        "revision_plan": review["revision_plan"],
        "confidence": "medium",
    }
