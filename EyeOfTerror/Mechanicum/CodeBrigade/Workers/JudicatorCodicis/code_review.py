from __future__ import annotations

"""Code review role implementation."""

from common.codewright_core import *  # noqa: F403 - role modules use the shared Codewright helper surface.






def patch_scope_review(scope: dict[str, Any]) -> dict[str, Any]:
    in_map = [str(item) for item in scope.get("changed_files_in_repo_map", [])] if isinstance(scope.get("changed_files_in_repo_map"), list) else []
    outside_map = [str(item) for item in scope.get("changed_files_outside_repo_map", [])] if isinstance(scope.get("changed_files_outside_repo_map"), list) else []
    evidence = scope.get("evidence") if isinstance(scope.get("evidence"), list) else []
    source_without_linked_tests = [
        str(item.get("path"))
        for item in evidence
        if (
            isinstance(item, dict)
            and str(item.get("path") or "").endswith(".py")
            and not test_like_path(str(item.get("path") or ""))
            and not item.get("linked_tests")
            and item.get("diagnostic_declared_surface") is not True
        )
    ]
    total = len(in_map) + len(outside_map)
    return {
        "status": "needs_attention" if outside_map or source_without_linked_tests else "covered",
        "changed_file_count": total,
        "mapped_changed_file_count": len(in_map),
        "unmapped_changed_file_count": len(outside_map),
        "unmapped_changed_files": outside_map,
        "source_without_linked_tests": source_without_linked_tests[:12],
    }


def repository_investigation_review(
    survey: dict[str, Any],
    patch: dict[str, Any],
    scope_review: dict[str, Any],
) -> dict[str, Any]:
    repo_map = survey.get("repo_map") if isinstance(survey.get("repo_map"), dict) else {}
    investigation = survey.get("engineering_investigation") if isinstance(survey.get("engineering_investigation"), dict) else {}
    readiness = survey.get("engineering_readiness") if isinstance(survey.get("engineering_readiness"), dict) else {}
    ranked_files = repo_map.get("ranked_files") if isinstance(repo_map.get("ranked_files"), list) else []
    read_order = repo_map.get("recommended_read_order") if isinstance(repo_map.get("recommended_read_order"), list) else []
    targeted_reads = investigation.get("targeted_reading_plan") if isinstance(investigation.get("targeted_reading_plan"), list) else []
    hypotheses = investigation.get("hypotheses") if isinstance(investigation.get("hypotheses"), list) else []
    impact_matrix = readiness.get("impact_matrix") if isinstance(readiness.get("impact_matrix"), list) else []
    source_excerpt_summary = (
        patch.get("source_excerpt_summary")
        if isinstance(patch.get("source_excerpt_summary"), list)
        else []
    )
    read_excerpts = [
        item
        for item in source_excerpt_summary
        if isinstance(item, dict) and item.get("status") == "read"
    ]
    changed_files = patch.get("changed_files") if isinstance(patch.get("changed_files"), list) else []
    concrete_changes = [item for item in changed_files if isinstance(item, dict) and item.get("path")]
    changed_file_count = len(concrete_changes)
    preexisting_changed_file_count = len([item for item in concrete_changes if not item.get("created")])
    mapped_changed_file_count = int(scope_review.get("mapped_changed_file_count") or 0)
    diagnostics = patch.get("diagnostics") if isinstance(patch.get("diagnostics"), dict) else {}
    planned_output_paths = [
        str(value)
        for key, value in diagnostics.items()
        if key.endswith("_path") and isinstance(value, str) and value
    ]
    marker_write_outputs = [
        str(item.get("path") or "")
        for item in concrete_changes
        if item.get("operation") == "write_file" and (item.get("created") or item.get("idempotent"))
    ]
    for path in marker_write_outputs:
        if path and path not in planned_output_paths:
            planned_output_paths.append(path)
    planned_output_path_set = set(planned_output_paths)
    explicit_output_surface = bool(planned_output_paths) and all(
        str(item.get("path") or "") in planned_output_path_set
        for item in concrete_changes
        if item.get("operation") == "write_file" and (item.get("created") or item.get("idempotent"))
    ) and all(
        item.get("operation") == "write_file" and (item.get("created") or item.get("idempotent"))
        for item in concrete_changes
    )
    raw_unmapped_changed_files = (
        scope_review.get("unmapped_changed_files")
        if isinstance(scope_review.get("unmapped_changed_files"), list)
        else []
    )
    unmapped_changed_files = {str(path) for path in raw_unmapped_changed_files}
    unmapped_preexisting = [
        str(item.get("path") or "")
        for item in concrete_changes
        if not item.get("created")
        and not item.get("idempotent")
        and str(item.get("path") or "") not in planned_output_path_set
        and str(item.get("path") or "") in unmapped_changed_files
    ]
    created_changes = [str(item.get("path") or "") for item in concrete_changes if item.get("created")]
    checks = [
        {
            "check": "ranked_repo_map_present",
            "status": "pass" if ranked_files or explicit_output_surface else "blocker",
            "evidence": {
                "ranked_file_count": len(ranked_files),
                "explicit_output_surface": explicit_output_surface,
                "planned_output_paths": planned_output_paths[:12],
            },
        },
        {
            "check": "targeted_reading_plan_present",
            "status": "pass" if (targeted_reads and read_order) or explicit_output_surface else "blocker",
            "evidence": {
                "targeted_read_count": len(targeted_reads),
                "recommended_read_count": len(read_order),
                "explicit_output_surface": explicit_output_surface,
            },
        },
        {
            "check": "hypotheses_present",
            "status": "pass" if hypotheses else "blocker",
            "evidence": {"hypothesis_count": len(hypotheses)},
        },
        {
            "check": "impact_matrix_present",
            "status": "pass" if impact_matrix or explicit_output_surface else "blocker",
            "evidence": {
                "impact_file_count": len(impact_matrix),
                "explicit_output_surface": explicit_output_surface,
            },
        },
        {
            "check": "pre_mutation_source_reads_present",
            "status": "pass" if read_excerpts or explicit_output_surface else "blocker",
            "evidence": {
                "read_excerpt_count": len(read_excerpts),
                "read_paths": [str(item.get("path") or "") for item in read_excerpts[:12]],
                "explicit_output_surface": explicit_output_surface,
            },
        },
        {
            "check": "changed_files_mapped_to_survey",
            "status": "pass" if preexisting_changed_file_count == 0 or not unmapped_preexisting else "blocker",
            "evidence": {
                "changed_file_count": changed_file_count,
                "preexisting_changed_file_count": preexisting_changed_file_count,
                "mapped_changed_file_count": mapped_changed_file_count,
                "created_changes_allowed_as_explicit_outputs": created_changes[:12],
                "unmapped_preexisting_changed_files": unmapped_preexisting[:12],
            },
        },
    ]
    blockers = [
        check
        for check in checks
        if check.get("status") == "blocker"
    ]
    return {
        "status": "blocked" if blockers else "covered",
        "checks": checks,
        "blockers": blockers,
        "summary": "Repository investigation evidence is sufficient." if not blockers else "Repository investigation evidence is incomplete.",
    }


def code_review_discipline_findings(
    patch_source: str,
    changed_files: list[dict[str, Any]],
    repo_root: Path | None = None,
    diagnostics: dict[str, Any] | None = None,
    ast_patch_plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    changed_paths = [
        str(item.get("path") or "")
        for item in changed_files
        if isinstance(item, dict) and item.get("path")
    ]
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    ast_patch_plan = ast_patch_plan if isinstance(ast_patch_plan, dict) else {}
    findings: list[dict[str, Any]] = []
    changed_tests = [path for path in changed_paths if test_like_path(path)]
    if patch_source.startswith("test_inferred_") and changed_tests:
        findings.append(
            {
                "check": "test_inferred_patch_did_not_edit_tests",
                "status": "blocker",
                "message": "Test-inferred repairs must not edit tests to fit the patch.",
                "evidence": {"changed_tests": changed_tests},
                }
            )
    risk_negative_tests = negative_test_evidence_for_risk_patch(repo_root, patch_source, diagnostics)
    if risk_negative_tests.get("required") and not risk_negative_tests.get("present"):
        findings.append(
            {
                "check": "risk_patch_has_negative_tests",
                "status": "blocker",
                "message": "Risk-sensitive repairs must be covered by negative tests for the boundary they change.",
                "evidence": risk_negative_tests,
            }
        )
    example = diagnostics.get("example") if isinstance(diagnostics.get("example"), dict) else {}
    expected = example.get("expected")
    function_name = str(diagnostics.get("function_name") or "")
    module_path = str(diagnostics.get("module_path") or "")
    if (
        patch_source.startswith("test_inferred_")
        and repo_root is not None
        and expected is not None
        and function_name
        and module_path
        and module_path in changed_paths
        and not test_like_path(module_path)
    ):
        planned_operations = ast_patch_plan.get("planned_operations") if isinstance(ast_patch_plan.get("planned_operations"), list) else []
        replacement_ops = [
            item
            for item in planned_operations
            if isinstance(item, dict)
            and item.get("kind") == "replace_return_expression"
            and item.get("path") == module_path
            and item.get("function_name") == function_name
        ]
        if replacement_ops:
            replacement = str(replacement_ops[0].get("new_expression") or "").strip()
            expected_literal = repr(expected) if isinstance(expected, str) else str(expected)
            try:
                function = simple_function_return_segment(safe_repo_path(repo_root, module_path), function_name)
            except (OSError, ValueError):
                function = {}
            args = function.get("args") if isinstance(function.get("args"), list) else []
            if args and replacement == expected_literal:
                findings.append(
                    {
                        "check": "inferred_patch_did_not_hardcode_example_expected",
                        "status": "blocker",
                        "message": "Test-inferred repair hardcodes the example expected value instead of deriving behavior from inputs.",
                        "evidence": {
                            "module_path": module_path,
                            "function_name": function_name,
                            "argument_count": len(args),
                            "replacement_expression": replacement,
                            "example_expected": expected,
                        },
                    }
                )
    if patch_source in {"test_inferred_security_boundary", "test_inferred_retry_policy"}:
        changed_non_tests = [path for path in changed_paths if not test_like_path(path)]
        if not changed_non_tests:
            findings.append(
                {
                    "check": "risk_patch_changed_source_surface",
                    "status": "blocker",
                    "message": "Risk-sensitive inferred repairs must mutate the implementation surface, not only supporting artifacts.",
                    "evidence": {"changed_paths": changed_paths},
                }
            )
    return findings


def negative_test_evidence_for_risk_patch(repo_root: Path | None, patch_source: str, diagnostics: dict[str, Any]) -> dict[str, Any]:
    if patch_source not in {"test_inferred_security_boundary", "test_inferred_config_runtime"}:
        return {"required": False, "present": True, "reason": "patch source does not require risk negative-test evidence"}
    test_path = str(diagnostics.get("test_path") or "")
    evidence: dict[str, Any] = {
        "required": True,
        "patch_source": patch_source,
        "test_path": test_path,
        "present": False,
    }
    if repo_root is None or not test_path:
        evidence["missing"] = "test path evidence is absent"
        return evidence
    try:
        test_text = safe_repo_path(repo_root, test_path).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        evidence["missing"] = f"test file could not be read: {exc}"
        return evidence
    if patch_source == "test_inferred_security_boundary":
        exception_assertion = "assertRaises" in test_text or "pytest.raises" in test_text
        malicious_input = any(marker in test_text for marker in ("../", "..\\", "/etc/", "passwd", "traversal", "absolute"))
        evidence.update(
            {
                "exception_assertion": exception_assertion,
                "malicious_input": malicious_input,
                "present": exception_assertion and malicious_input,
            }
        )
        return evidence
    env_override = "SERVICE_URL" in test_text or "os.environ" in test_text or "monkeypatch" in test_text
    default_or_fallback = "pop(" in test_text or "default" in test_text.lower() or "fallback" in test_text.lower()
    multiple_cases = len(re.findall(r"\bdef\s+test_", test_text)) >= 2
    evidence.update(
        {
            "env_override_case": env_override,
            "default_or_fallback_case": default_or_fallback,
            "multiple_cases": multiple_cases,
            "present": env_override and default_or_fallback and multiple_cases,
        }
    )
    return evidence


def public_surface_review_from_evidence(
    patch_source: str,
    diagnostics: dict[str, Any],
    changed_files: list[dict[str, Any]],
    broad_commands: list[Any],
) -> dict[str, Any]:
    requires_review = patch_source in {
        "test_inferred_api_deprecation",
        "public_api_compat_marker_synthesis",
    }
    changed_paths = [
        str(item.get("path") or "")
        for item in changed_files
        if isinstance(item, dict) and item.get("path")
    ]
    docs_path = str(diagnostics.get("docs_path") or "")
    caller_path = str(diagnostics.get("caller_path") or "")
    caller = diagnostics.get("caller") if isinstance(diagnostics.get("caller"), dict) else {}
    if not caller_path:
        caller_path = str(caller.get("caller_path") or "")
    checks = [
        {
            "check": "public_surface_review_required",
            "status": "pass" if requires_review else "not_applicable",
            "evidence": {"patch_source": patch_source},
        },
        {
            "check": "docs_surface_updated",
            "status": "pass" if (not requires_review or (docs_path and docs_path in changed_paths)) else "blocker",
            "evidence": {"docs_path": docs_path, "changed": docs_path in changed_paths if docs_path else False},
        },
        {
            "check": "caller_surface_updated_or_not_required",
            "status": "pass" if (not requires_review or not caller_path or caller_path in changed_paths) else "blocker",
            "evidence": {"caller_path": caller_path, "changed": caller_path in changed_paths if caller_path else False},
        },
        {
            "check": "public_surface_broad_verification",
            "status": "pass" if (not requires_review or bool(broad_commands)) else "blocker",
            "evidence": {"broad_commands": broad_commands},
        },
    ]
    blockers = [item for item in checks if item.get("status") == "blocker"]
    return {
        "status": "blocked" if blockers else "covered",
        "required": requires_review,
        "checks": checks,
        "blockers": blockers,
    }

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
    model_guidance = code_model_guidance(request, "code review, blocker detection, revision planning, and final approval risk")
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
        "model_guidance_review": {
            "status": model_guidance.get("status"),
            "used_by_worker": model_guidance.get("used_by_worker"),
            "risk_markers": model_guidance.get("risk_markers", []),
            "content_excerpt": str(model_guidance.get("content") or "")[:2000],
        },
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
            },
            {
                "severity": "warning",
                "message": "Model guidance was considered as advisory review context; blockers still require structured evidence.",
                "markers": model_guidance.get("risk_markers", []),
            },
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
        "model_guidance": model_guidance,
    }
