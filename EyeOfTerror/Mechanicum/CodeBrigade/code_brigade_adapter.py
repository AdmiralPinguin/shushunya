#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from execution_contract import CONTRACT_VERSION, build_blocked_execution_result
from execution_contract import build_implemented_execution_result, build_patch_manifest
from execution_preflight import build_execution_preflight
from implementation_brief_contract import validate_implementation_brief

REAL_EXECUTION_STATUS = "blocked_until_adapter_is_wired"
PLANNING_HANDOFF_REQUIRED_RISKS = {"medium", "high"}


def repo_path_from_brief(brief: dict[str, Any]) -> Path:
    return Path(str(brief.get("repo_path") or ""))


def path_stays_in_repo(repo: Path, rel_path: str) -> bool:
    try:
        (repo / rel_path).resolve().relative_to(repo.resolve())
        return True
    except ValueError:
        return False


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_edit_plan(brief: dict[str, Any], implementation_plan: dict[str, Any], execution_intent: dict[str, Any]) -> dict[str, Any]:
    allowed_new_files = implementation_plan.get("missing_path_hints", [])
    if brief.get("controller_execution_mode") == "project_creation":
        try:
            from greenfield_project import extract_project_spec, normalize_project_file_rows

            project_files = [row["path"] for row in normalize_project_file_rows(extract_project_spec(str(brief.get("task") or "")).get("files"))]
            if "greenfield_project_brief.json" not in project_files:
                project_files.append("greenfield_project_brief.json")
            allowed_new_files = list(dict.fromkeys([*(allowed_new_files if isinstance(allowed_new_files, list) else []), *project_files]))
        except Exception:
            allowed_new_files = allowed_new_files if isinstance(allowed_new_files, list) else []
    return {
        "kind": "code_brigade_edit_plan",
        "contract_version": CONTRACT_VERSION,
        "controller_execution_mode": brief.get("controller_execution_mode", "dry_run"),
        "execution_intent_mode": execution_intent.get("mode", ""),
        "read_before_edit": implementation_plan.get("recommended_read_order", [])[:12],
        "target_files": implementation_plan.get("target_files_to_inspect", []),
        "allowed_new_files": allowed_new_files,
        "test_files": implementation_plan.get("test_files_to_preserve", []),
        "planned_diff_summary": {
            "change_intents": implementation_plan.get("change_allowed_intents", []),
            "protected_invariants": implementation_plan.get("change_protected_invariants", []),
            "post_change_proofs": implementation_plan.get("change_post_change_proofs", []),
        },
        "acceptance_criteria": implementation_plan.get("acceptance_evidence_required", []),
        "verification_commands": implementation_plan.get("verification_commands", []),
    }


def build_planning_handoff_gate(brief: dict[str, Any]) -> dict[str, Any]:
    risk_level = str(brief.get("risk_level") or "high")
    required = risk_level in PLANNING_HANDOFF_REQUIRED_RISKS
    planning_department = brief.get("planning_department") if isinstance(brief.get("planning_department"), dict) else {}
    work_handoff = brief.get("planning_department_handoff") if isinstance(brief.get("planning_department_handoff"), dict) else {}
    brigade_handoff = planning_department.get("brigade_handoff_contract") if isinstance(planning_department.get("brigade_handoff_contract"), dict) else {}
    rfc = planning_department.get("engineering_rfc") if isinstance(planning_department.get("engineering_rfc"), dict) else {}
    investigation = planning_department.get("multi_pass_repo_investigation") if isinstance(planning_department.get("multi_pass_repo_investigation"), dict) else {}
    blockers: list[str] = []
    if required:
        if planning_department.get("status") != "ready_for_code_brigade":
            blockers.append("planning_department.status must be ready_for_code_brigade")
        if work_handoff.get("status") != "ready":
            blockers.append("planning_department_handoff.status must be ready")
        if brigade_handoff.get("status") != "ready":
            blockers.append("brigade_handoff_contract.status must be ready")
        if rfc.get("status") != "accepted_for_code_brigade_handoff":
            blockers.append("engineering_rfc.status must be accepted_for_code_brigade_handoff")
        if investigation.get("status") != "complete":
            blockers.append("multi_pass_repo_investigation.status must be complete")
    return {
        "kind": "code_brigade_planning_handoff_gate",
        "contract_version": CONTRACT_VERSION,
        "required": required,
        "risk_level": risk_level,
        "decision": "blocked" if blockers else "passed",
        "required_before_source_mutation": required,
        "planning_department_status": str(planning_department.get("status") or ""),
        "work_package_handoff_status": str(work_handoff.get("status") or ""),
        "brigade_handoff_contract_status": str(brigade_handoff.get("status") or ""),
        "engineering_rfc_status": str(rfc.get("status") or ""),
        "multi_pass_investigation_status": str(investigation.get("status") or ""),
        "blockers": blockers,
    }


def mutation_preflight_blockers(implementation_plan: dict[str, Any], edit_plan: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    allowed_new_files = edit_plan.get("allowed_new_files") if isinstance(edit_plan.get("allowed_new_files"), list) else []
    if not implementation_plan.get("recommended_read_order") and not allowed_new_files:
        blockers.append("mutation preflight requires recommended_read_order before editing")
    if not implementation_plan.get("target_files_to_inspect") and not allowed_new_files:
        blockers.append("mutation preflight requires target_files_to_inspect before editing")
    if not implementation_plan.get("verification_commands"):
        blockers.append("mutation preflight requires verification_commands before editing")
    planned_diff = edit_plan.get("planned_diff_summary") if isinstance(edit_plan.get("planned_diff_summary"), dict) else {}
    if not planned_diff.get("change_intents"):
        blockers.append("mutation preflight requires planned diff change intents")
    if not edit_plan.get("acceptance_criteria"):
        blockers.append("mutation preflight requires acceptance criteria")
    return blockers


def collect_pre_mutation_read_evidence(brief: dict[str, Any], edit_plan: dict[str, Any]) -> dict[str, Any]:
    repo = repo_path_from_brief(brief)
    raw_paths: dict[str, set[str]] = {}
    for key in ("read_before_edit", "target_files", "test_files"):
        values = edit_plan.get(key)
        if isinstance(values, list):
            for item in values:
                if isinstance(item, str) and item:
                    raw_paths.setdefault(item, set()).add(key)
                elif isinstance(item, dict) and isinstance(item.get("path"), str):
                    raw_paths.setdefault(item["path"], set()).add(key)
    allowed_new = {
        str(item)
        for item in edit_plan.get("allowed_new_files", [])
        if isinstance(item, str)
    }
    for item in allowed_new:
        raw_paths.setdefault(item, set()).add("allowed_new_files")
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    for rel_path, roles in list(raw_paths.items())[:24]:
        role_list = sorted(roles)
        row: dict[str, Any] = {"path": rel_path, "roles": role_list}
        if not path_stays_in_repo(repo, rel_path):
            row.update({"status": "blocked", "reason": "path escapes repo"})
            blockers.append(f"read target escapes repo: {rel_path}")
        else:
            path = repo / rel_path
            if rel_path in allowed_new and not path.exists():
                row.update({"status": "planned_new_file", "reason": "explicitly allowed missing create path"})
            elif not path.exists() or not path.is_file() or path.is_symlink():
                if roles.intersection({"target_files", "test_files"}):
                    row.update({"status": "blocked", "reason": "required target/test read is missing, not a file, or a symlink"})
                    blockers.append(f"read target is unavailable before mutation: {rel_path}")
                else:
                    row.update({"status": "unavailable_recommended_read", "reason": "recommended read target is unavailable but not a mutation target"})
            else:
                text = path.read_text(encoding="utf-8")
                row.update(
                    {
                        "status": "read",
                        "sha256": sha256_file(path),
                        "byte_count": path.stat().st_size,
                        "line_count": len(text.splitlines()),
                        "excerpt": "\n".join(text.splitlines()[:8]),
                    }
                )
        rows.append(row)
    return {
        "kind": "code_brigade_pre_mutation_read_evidence",
        "repo_path": str(repo),
        "required_read_count": len(raw_paths),
        "recorded_read_count": sum(1 for row in rows if row.get("status") == "read"),
        "planned_new_file_count": sum(1 for row in rows if row.get("status") == "planned_new_file"),
        "rows": rows,
        "blockers": blockers,
        "status": "blocked" if blockers else "complete",
    }


def build_implementation_plan(brief: dict[str, Any]) -> dict[str, Any]:
    evidence = brief.get("repo_survey_evidence") if isinstance(brief.get("repo_survey_evidence"), dict) else {}
    verification = brief.get("required_verification") if isinstance(brief.get("required_verification"), dict) else {}
    repair_plan = brief.get("diagnostic_repair_plan") if isinstance(brief.get("diagnostic_repair_plan"), dict) else {}
    surface_matrix = brief.get("surface_verification_matrix") if isinstance(brief.get("surface_verification_matrix"), dict) else {}
    package_matrix = brief.get("surface_package_matrix") if isinstance(brief.get("surface_package_matrix"), dict) else {}
    survey_quality = brief.get("survey_quality_gate") if isinstance(brief.get("survey_quality_gate"), dict) else {}
    handoff = brief.get("code_brigade_handoff") if isinstance(brief.get("code_brigade_handoff"), dict) else {}
    assumptions = brief.get("assumption_register") if isinstance(brief.get("assumption_register"), dict) else {}
    acceptance = brief.get("acceptance_contract") if isinstance(brief.get("acceptance_contract"), dict) else {}
    acceptance_trace = brief.get("acceptance_trace_matrix") if isinstance(brief.get("acceptance_trace_matrix"), dict) else {}
    constraint_trace = brief.get("constraint_trace_matrix") if isinstance(brief.get("constraint_trace_matrix"), dict) else {}
    expert_plan = brief.get("expert_quality_plan") if isinstance(brief.get("expert_quality_plan"), dict) else {}
    change_control = brief.get("change_control_plan") if isinstance(brief.get("change_control_plan"), dict) else {}
    playbook = brief.get("investigation_playbook") if isinstance(brief.get("investigation_playbook"), dict) else {}
    blueprint = brief.get("implementation_brief_blueprint") if isinstance(brief.get("implementation_brief_blueprint"), dict) else {}
    work_packages = brief.get("implementation_work_packages") if isinstance(brief.get("implementation_work_packages"), dict) else {}
    output_contract = brief.get("worker_output_contract") if isinstance(brief.get("worker_output_contract"), dict) else {}
    packages = work_packages.get("packages") if isinstance(work_packages.get("packages"), list) else []
    package_blocking_policies = {
        str(package.get("id") or ""): package.get("blocking_policy", [])
        for package in packages
        if isinstance(package, dict) and package.get("id") and isinstance(package.get("blocking_policy"), list)
    }
    planning_review = brief.get("planning_review_gate") if isinstance(brief.get("planning_review_gate"), dict) else {}
    planning_department = brief.get("planning_department") if isinstance(brief.get("planning_department"), dict) else {}
    planning_department_handoff = brief.get("planning_department_handoff") if isinstance(brief.get("planning_department_handoff"), dict) else {}
    planning_role_trace = brief.get("planning_role_execution_trace") if isinstance(brief.get("planning_role_execution_trace"), list) else []
    dependency = brief.get("planning_dependency_map") if isinstance(brief.get("planning_dependency_map"), dict) else {}
    breakdown = brief.get("work_breakdown") if isinstance(brief.get("work_breakdown"), dict) else {}
    impact = brief.get("impact_analysis") if isinstance(brief.get("impact_analysis"), dict) else {}
    forecast = brief.get("execution_forecast") if isinstance(brief.get("execution_forecast"), dict) else {}
    execution_intent = brief.get("execution_intent") if isinstance(brief.get("execution_intent"), dict) else {}
    suggested_commands = brief.get("suggested_verification_commands")
    if not isinstance(suggested_commands, list):
        suggested_commands = []
    targeted_commands = verification.get("targeted_commands")
    if not isinstance(targeted_commands, list):
        targeted_commands = []
    commands: list[str] = []
    for command in [*targeted_commands, *suggested_commands]:
        if isinstance(command, str) and command and command not in commands:
            commands.append(command)
    return {
        "kind": "code_brigade_implementation_plan",
        "contract_version": CONTRACT_VERSION,
        "strategy": brief.get("selected_strategy", ""),
        "risk_level": brief.get("risk_level", "high"),
        "target_files_to_inspect": evidence.get("candidate_files", []) if isinstance(evidence.get("candidate_files"), list) else [],
        "test_files_to_preserve": evidence.get("test_files", []) if isinstance(evidence.get("test_files"), list) else [],
        "path_hints": evidence.get("path_hints", []) if isinstance(evidence.get("path_hints"), list) else [],
        "existing_path_hints": evidence.get("existing_path_hints", []) if isinstance(evidence.get("existing_path_hints"), list) else [],
        "missing_path_hints": evidence.get("missing_path_hints", []) if isinstance(evidence.get("missing_path_hints"), list) else [],
        "unsafe_path_hints": evidence.get("unsafe_path_hints", []) if isinstance(evidence.get("unsafe_path_hints"), list) else [],
        "entrypoints_to_check": evidence.get("entrypoint_candidates", []) if isinstance(evidence.get("entrypoint_candidates"), list) else [],
        "recommended_read_order": evidence.get("recommended_read_order", []) if isinstance(evidence.get("recommended_read_order"), list) else [],
        "source_summaries_to_consider": evidence.get("source_summaries", []) if isinstance(evidence.get("source_summaries"), list) else [],
        "dependency_edges_to_check": evidence.get("local_import_edges", []) if isinstance(evidence.get("local_import_edges"), list) else [],
        "generic_dependency_edges_to_check": evidence.get("generic_import_edges", []) if isinstance(evidence.get("generic_import_edges"), list) else [],
        "repository_dependency_graph": evidence.get("repository_dependency_graph", {}) if isinstance(evidence.get("repository_dependency_graph"), dict) else {},
        "reverse_dependency_index": evidence.get("reverse_dependency_index", {}) if isinstance(evidence.get("reverse_dependency_index"), dict) else {},
        "test_coverage_links": evidence.get("test_coverage_links", []) if isinstance(evidence.get("test_coverage_links"), list) else [],
        "caller_candidates": evidence.get("caller_candidates", []) if isinstance(evidence.get("caller_candidates"), list) else [],
        "contract_surface_candidates": evidence.get("contract_surface_candidates", []) if isinstance(evidence.get("contract_surface_candidates"), list) else [],
        "package_manifest_candidates": evidence.get("package_manifest_candidates", []) if isinstance(evidence.get("package_manifest_candidates"), list) else [],
        "repository_cartography": evidence.get("repository_cartography", {}) if isinstance(evidence.get("repository_cartography"), dict) else {},
        "survey_truncated": bool(evidence.get("survey_truncated")),
        "python_symbols_truncated": bool(evidence.get("python_symbols_truncated")),
        "handoff_steps": handoff.get("steps", []) if isinstance(handoff.get("steps"), list) else [],
        "planning_critical_path": dependency.get("critical_path", []) if isinstance(dependency.get("critical_path"), list) else [],
        "planning_review_decision": planning_review.get("decision", ""),
        "planning_review_score": planning_review.get("score", 0),
        "planning_department_status": planning_department.get("status", ""),
        "planning_department_roles": planning_department.get("roles", []) if isinstance(planning_department.get("roles"), list) else [],
        "planning_role_execution_trace": planning_role_trace,
        "brigade_handoff_contract": planning_department.get("brigade_handoff_contract", {}) if isinstance(planning_department.get("brigade_handoff_contract"), dict) else {},
        "engineering_rfc_status": planning_department.get("engineering_rfc", {}).get("status", "") if isinstance(planning_department.get("engineering_rfc"), dict) else "",
        "multi_pass_investigation_status": planning_department.get("multi_pass_repo_investigation", {}).get("status", "") if isinstance(planning_department.get("multi_pass_repo_investigation"), dict) else "",
        "multi_pass_investigation_phases": planning_department.get("multi_pass_repo_investigation", {}).get("phases", []) if isinstance(planning_department.get("multi_pass_repo_investigation"), dict) and isinstance(planning_department.get("multi_pass_repo_investigation", {}).get("phases"), list) else [],
        "planning_department_work_package_handoff": planning_department_handoff,
        "work_phases": breakdown.get("phases", []) if isinstance(breakdown.get("phases"), list) else [],
        "stop_conditions": breakdown.get("stop_conditions", []) if isinstance(breakdown.get("stop_conditions"), list) else [],
        "impact_surfaces": impact.get("surfaces", []) if isinstance(impact.get("surfaces"), list) else [],
        "highest_risk_surface": impact.get("highest_risk_surface", ""),
        "requires_cross_surface_review": bool(impact.get("requires_cross_surface_review")),
        "execution_complexity": forecast.get("complexity", ""),
        "expected_code_brigade_iterations": forecast.get("expected_code_brigade_iterations", 0),
        "recommended_timeout_minutes": forecast.get("recommended_timeout_minutes", 0),
        "scope_budget": forecast.get("scope_budget", {}) if isinstance(forecast.get("scope_budget"), dict) else {},
        "escalation_triggers": forecast.get("escalation_triggers", []) if isinstance(forecast.get("escalation_triggers"), list) else [],
        "execution_intent": execution_intent,
        "diagnostic_repair_plan": repair_plan,
        "mutation_preconditions": blueprint.get("mutation_preconditions", []) if isinstance(blueprint.get("mutation_preconditions"), list) else [],
        "implementation_work_packages": packages,
        "worker_output_contract": output_contract,
        "work_package_review_order": work_packages.get("review_order", []) if isinstance(work_packages.get("review_order"), list) else [],
        "work_package_dependency_graph": work_packages.get("package_dependency_graph", {}) if isinstance(work_packages.get("package_dependency_graph"), dict) else {},
        "work_package_blocking_policies": package_blocking_policies,
        "work_package_handoff_criteria": work_packages.get("global_handoff_criteria", []) if isinstance(work_packages.get("global_handoff_criteria"), list) else [],
        "acceptance_evidence_required": acceptance.get("must_prove", []) if isinstance(acceptance.get("must_prove"), list) else [],
        "acceptance_trace_rows": acceptance_trace.get("rows", []) if isinstance(acceptance_trace.get("rows"), list) else [],
        "acceptance_trace_complete": acceptance_trace.get("complete") is True,
        "definition_of_done_trace_complete": acceptance_trace.get("definition_of_done_complete") is True,
        "definition_of_done_count": acceptance_trace.get("definition_of_done_count", 0),
        "traced_definition_of_done_count": acceptance_trace.get("traced_definition_of_done_count", 0),
        "missing_definition_of_done": acceptance_trace.get("missing_definition_of_done", []) if isinstance(acceptance_trace.get("missing_definition_of_done"), list) else [],
        "constraint_trace_rows": constraint_trace.get("rows", []) if isinstance(constraint_trace.get("rows"), list) else [],
        "constraint_trace_complete": constraint_trace.get("complete") is True,
        "expert_quality_level": expert_plan.get("level", ""),
        "expert_quality_required": bool(expert_plan.get("required_for_expert_gate")),
        "expert_tradeoff_register": expert_plan.get("tradeoff_register", []) if isinstance(expert_plan.get("tradeoff_register"), list) else [],
        "expert_rollback_strategy": expert_plan.get("rollback_strategy", []) if isinstance(expert_plan.get("rollback_strategy"), list) else [],
        "expert_observability_plan": expert_plan.get("observability_plan", []) if isinstance(expert_plan.get("observability_plan"), list) else [],
        "expert_review_checklist": expert_plan.get("review_checklist", []) if isinstance(expert_plan.get("review_checklist"), list) else [],
        "expert_escalation_policy": expert_plan.get("escalation_policy", []) if isinstance(expert_plan.get("escalation_policy"), list) else [],
        "change_allowed_intents": change_control.get("allowed_change_intents", []) if isinstance(change_control.get("allowed_change_intents"), list) else [],
        "change_protected_invariants": change_control.get("protected_invariants", []) if isinstance(change_control.get("protected_invariants"), list) else [],
        "change_mutation_requires": change_control.get("mutation_requires", []) if isinstance(change_control.get("mutation_requires"), list) else [],
        "change_diff_review_questions": change_control.get("diff_review_questions", []) if isinstance(change_control.get("diff_review_questions"), list) else [],
        "change_rollback_triggers": change_control.get("rollback_triggers", []) if isinstance(change_control.get("rollback_triggers"), list) else [],
        "change_post_change_proofs": change_control.get("post_change_proofs", []) if isinstance(change_control.get("post_change_proofs"), list) else [],
        "change_expert_review_required": bool(change_control.get("expert_review_required")),
        "investigation_read_stages": playbook.get("read_stages", []) if isinstance(playbook.get("read_stages"), list) else [],
        "investigation_evidence_questions": playbook.get("evidence_questions", []) if isinstance(playbook.get("evidence_questions"), list) else [],
        "investigation_mutation_blockers": playbook.get("mutation_blockers", []) if isinstance(playbook.get("mutation_blockers"), list) else [],
        "investigation_replan_triggers": playbook.get("replan_triggers", []) if isinstance(playbook.get("replan_triggers"), list) else [],
        "verification_commands": commands,
        "surface_verification_complete": bool(surface_matrix.get("complete")),
        "surface_verification_rows": surface_matrix.get("rows", []) if isinstance(surface_matrix.get("rows"), list) else [],
        "surface_package_matrix_complete": bool(package_matrix.get("complete")),
        "surface_package_matrix_rows": package_matrix.get("rows", []) if isinstance(package_matrix.get("rows"), list) else [],
        "survey_quality_decision": survey_quality.get("decision", ""),
        "survey_quality_warnings": survey_quality.get("warnings", []) if isinstance(survey_quality.get("warnings"), list) else [],
        "assumption_rows": assumptions.get("assumptions", []) if isinstance(assumptions.get("assumptions"), list) else [],
        "assumption_replan_triggers": assumptions.get("replan_when_false", []) if isinstance(assumptions.get("replan_when_false"), list) else [],
        "acceptance_gates": brief.get("acceptance_gates", []) if isinstance(brief.get("acceptance_gates"), list) else [],
        "refusal_conditions": [
            "brief validation fails",
            "requested source tree is unavailable",
            "required behavior cannot be proven by existing or newly planned verification",
            "requested patch would require a broad rewrite outside allowed_scope",
            "expert quality plan cannot be satisfied for high-risk work",
        ],
    }


def build_autonomous_execution_request(brief: dict[str, Any], implementation_plan: dict[str, Any], execution_intent: dict[str, Any] | None = None) -> dict[str, Any]:
    if execution_intent is None:
        execution_intent = brief.get("execution_intent") if isinstance(brief.get("execution_intent"), dict) else {}
    request_required = execution_intent.get("real_execution_supported") is False
    repair_plan = implementation_plan.get("diagnostic_repair_plan") if isinstance(implementation_plan.get("diagnostic_repair_plan"), dict) else {}
    diagnostic_inputs_required = repair_plan.get("diagnostic_inputs_required") if isinstance(repair_plan.get("diagnostic_inputs_required"), list) else [
        "latest verification_execution.results[].diagnostics",
        "traceback_files mapped to repo-relative paths",
        "missing_imports from failed verification output",
        "assertion, syntax, and zero-test signals",
        "changed-file verification commands after every mutation",
    ]
    read_before_repair = repair_plan.get("read_before_repair") if isinstance(repair_plan.get("read_before_repair"), list) else [
        "target_files_to_inspect",
        "traceback_files",
        "test_files_to_preserve",
    ]
    stop_conditions = repair_plan.get("stop_conditions") if isinstance(repair_plan.get("stop_conditions"), list) else [
        "diagnostics do not identify a repo-local source or test surface",
        "next patch would exceed scope_budget",
        "verification has zero-test or missing-import diagnostics without a safe source edit",
        "the same verification failure repeats after a mutation",
    ]
    required_outputs = repair_plan.get("repair_evidence_required") if isinstance(repair_plan.get("repair_evidence_required"), list) else [
        "diagnostic_summary",
        "attempted_patch_summary",
        "verification_commands_executed",
        "residual_blockers",
    ]
    return {
        "kind": "code_brigade_autonomous_execution_request",
        "contract_version": CONTRACT_VERSION,
        "status": "required" if request_required else "not_required",
        "target_adapter": "autonomous CodeBrigade source-edit adapter",
        "repo_path": brief.get("repo_path", ""),
        "task": brief.get("task", ""),
        "reason": "unshaped task has no executable guarded patch path" if request_required else "current CodeBrigade guarded adapter supports this execution path",
        "scope_budget": implementation_plan.get("scope_budget", {}),
        "target_files_to_inspect": implementation_plan.get("target_files_to_inspect", []),
        "test_files_to_preserve": implementation_plan.get("test_files_to_preserve", []),
        "recommended_read_order": implementation_plan.get("recommended_read_order", []),
        "reverse_dependency_index": implementation_plan.get("reverse_dependency_index", {}),
        "test_coverage_links": implementation_plan.get("test_coverage_links", []),
        "verification_commands": implementation_plan.get("verification_commands", []),
        "acceptance_evidence_required": implementation_plan.get("acceptance_evidence_required", []),
        "refusal_conditions": implementation_plan.get("refusal_conditions", []),
        "diagnostic_inputs_required": diagnostic_inputs_required,
        "repair_loop_contract": {
            "max_attempts": repair_plan.get("max_repair_attempts", 3),
            "must_read_before_edit": read_before_repair,
            "must_stop_when": stop_conditions,
            "required_outputs": required_outputs,
        },
        "return_contract": [
            "patch_manifest.json with changed files and rationale",
            "verification_report.json with executed, failed, skipped, or blocked commands",
            "worker_report.json with package statuses and blockers",
        ],
    }


def code_worker_request(step_id: str, artifact: str, brief: dict[str, Any]) -> dict[str, Any]:
    implementation_plan = build_implementation_plan(brief)
    role_policies = {
        "repository_survey": {"role": "repository_mapper", "authority": "read_only_repository_mapping", "may_mutate_source": False},
        "change_planning": {"role": "change_strategist", "authority": "scoped_plan_from_repository_evidence", "may_mutate_source": False},
        "implementation": {
            "role": "patchwright",
            "authority": "scoped_source_mutation_from_patch_contract_or_safe_inference",
            "may_mutate_source": True,
        },
        "verification": {"role": "verifier", "authority": "allowlisted_verification_and_narrow_repairs", "may_mutate_source": True},
        "code_review": {"role": "critic", "authority": "read_only_package_review_and_revision_ordering", "may_mutate_source": False},
        "finalize": {"role": "final_packager", "authority": "read_only_final_manifest_packaging", "may_mutate_source": False},
    }
    request = {
        "task_id": f"ceraxia-code-worker:{step_id}",
        "goal": str(brief.get("task") or ""),
        "target_repo_root": str(brief.get("repo_path") or ""),
        "step": {"step_id": step_id, "expected_artifacts": [artifact]},
        "quality_expectations": {
            "step_quality": {
                "step_id": step_id,
                "role_policy": role_policies[step_id],
            },
            "task_profile": {
                "task_kinds": brief.get("task_kinds", []),
                "risk_level": brief.get("risk_level", ""),
            },
            "worker_brief": {
                "selected_strategy": brief.get("selected_strategy", ""),
                "acceptance_contract": brief.get("acceptance_contract", {}),
                "required_verification": brief.get("required_verification", {}),
                "implementation_plan": implementation_plan,
            },
        },
    }
    return request


def load_pipeline_artifacts(workspace_root: Path) -> dict[str, Any]:
    code_dir = workspace_root / "code"

    def load(name: str) -> dict[str, Any]:
        path = code_dir / name
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}

    return {
        "repo_survey": load("repo_survey.json"),
        "problem_statement": load("problem_statement.json"),
        "architecture_options": load("architecture_options.json"),
        "patch_manifest": load("patch_manifest.json"),
        "verification_report": load("verification_report.json"),
        "repair_loop_state": load("repair_loop_state.json"),
        "diagnostic_extraction": load("diagnostic_extraction.json"),
        "code_review": load("code_review.json"),
        "final_manifest": load("final_manifest.json"),
    }


def operation_results_from_pipeline(changed_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(changed_files):
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if not path:
            continue
        rows.append(
            {
                "index": index,
                "operation": str(item.get("operation") or "code_worker_pipeline"),
                "path": path,
                "status": "applied" if item.get("changed", True) else "unchanged",
                "before_sha256": str(item.get("before_sha256") or ""),
                "after_sha256": str(item.get("after_sha256") or ""),
            }
        )
    return rows


def verification_commands_from_pipeline(verification_report: dict[str, Any]) -> list[dict[str, Any]]:
    executed = verification_report.get("executed") if isinstance(verification_report.get("executed"), list) else []
    rows: list[dict[str, Any]] = []
    for item in executed:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "command": str(item.get("command") or ""),
                "returncode": item.get("returncode"),
                "status": "passed" if int(item.get("returncode") or 0) == 0 else "failed",
            }
        )
    return rows


def code_worker_pipeline_paths() -> Path:
    worker_path = Path(__file__).resolve().parent / "Workers" / "CogitatorCodewright"
    if str(worker_path) not in sys.path:
        sys.path.insert(0, str(worker_path))
    return worker_path


def execute_worker_pipeline_brief(brief: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    validation_problems = validate_implementation_brief(brief)
    if validation_problems:
        return build_blocked_execution_result([f"invalid implementation brief: {problem}" for problem in validation_problems]), {}
    preflight = build_execution_preflight(brief)
    if not preflight["ok"]:
        return build_blocked_execution_result(preflight["blockers"], preflight), {}
    code_worker_pipeline_paths()
    from cogitator_codewright import run as run_code_worker  # noqa: WPS433 - loaded lazily to keep dry-run imports cheap.

    steps = [
        ("repository_survey", "/work/code/repo_survey.json"),
        ("change_planning", "/work/code/change_plan.md"),
        ("implementation", "/work/code/patch_manifest.json"),
        ("verification", "/work/code/verification_report.json"),
        ("code_review", "/work/code/code_review.json"),
        ("finalize", "/work/code/final_manifest.json"),
    ]
    with tempfile.TemporaryDirectory(prefix="ceraxia-code-worker-") as temp_dir:
        workspace_root = Path(temp_dir) / "work"
        step_results: list[dict[str, Any]] = []
        for step_id, artifact in steps:
            result = run_code_worker(code_worker_request(step_id, artifact, brief), workspace_root)
            step_results.append(result if isinstance(result, dict) else {"ok": False, "error": "worker returned non-object"})
            if not result.get("ok") and result.get("status") not in {"blocked", "needs_revision", "passed_with_warnings"}:
                artifacts = load_pipeline_artifacts(workspace_root)
                artifacts["step_results"] = step_results
                return build_blocked_execution_result([f"{step_id} failed: {result}"], preflight), artifacts
        artifacts = load_pipeline_artifacts(workspace_root)
        artifacts["step_results"] = step_results
    final = artifacts.get("final_manifest") if isinstance(artifacts.get("final_manifest"), dict) else {}
    patch = artifacts.get("patch_manifest") if isinstance(artifacts.get("patch_manifest"), dict) else {}
    verification = artifacts.get("verification_report") if isinstance(artifacts.get("verification_report"), dict) else {}
    blockers = [str(item) for item in final.get("blockers", [])] if isinstance(final.get("blockers"), list) else []
    if final.get("status") != "ready":
        if not blockers:
            blockers = ["CodeBrigade worker pipeline did not produce a ready final manifest"]
        rollback = patch.get("rollback") if isinstance(patch.get("rollback"), dict) else {}
        rollback_files = rollback.get("files") if isinstance(rollback.get("files"), list) else []
        if rollback.get("applied") and rollback_files:
            rollback_notes = f"rolled back {len(rollback_files)} touched files after patch failure"
            rollback_operations = [
                {
                    "index": index,
                    "operation": "rollback",
                    "path": str(item.get("path") or ""),
                    "status": "failed_rolled_back",
                    "before_sha256": "",
                    "after_sha256": "",
                }
                for index, item in enumerate(rollback_files)
                if isinstance(item, dict) and item.get("path")
            ]
            return (
                build_blocked_execution_result(
                    blockers,
                    preflight,
                    rollback_notes,
                    rollback_operations,
                    build_patch_manifest([], rollback_operations, rollback_notes),
                ),
                artifacts,
            )
        return build_blocked_execution_result(blockers, preflight), artifacts
    changed_files = [
        str(item.get("path"))
        for item in final.get("changed_files", [])
        if isinstance(item, dict) and item.get("path")
    ]
    operation_results = operation_results_from_pipeline(final.get("changed_files", []) if isinstance(final.get("changed_files"), list) else [])
    patch_source = str(final.get("patch_source") or patch.get("patch_source") or "code_worker_pipeline")
    execution_result = build_implemented_execution_result(
        changed_files,
        f"{patch_source} via CogitatorCodewright worker pipeline",
        preflight,
        operation_results,
        build_patch_manifest(changed_files, operation_results, ""),
    )
    execution_result["verification_commands_executed"] = verification_commands_from_pipeline(verification)
    execution_result["code_worker_pipeline_status"] = "ready"
    return execution_result, artifacts


def build_worker_report(brief: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    validation_problems = validate_implementation_brief(brief)
    implementation_plan = build_implementation_plan(brief)
    execution_intent = dict(brief.get("execution_intent") if isinstance(brief.get("execution_intent"), dict) else {})
    controller_mode = str(brief.get("controller_execution_mode") or "dry_run")
    review_only = controller_mode == "review_only"
    project_creation = controller_mode == "project_creation"
    edit_plan = build_edit_plan(brief, implementation_plan, execution_intent)
    planning_handoff_gate = build_planning_handoff_gate(brief)
    task = str(brief.get("task") or "")
    has_explicit_patch = "CERAXIA_PATCH:" in task
    has_guarded_inferred_patch = False
    if not has_explicit_patch:
        from execution_adapter import can_infer_guarded_execution

        has_guarded_inferred_patch = can_infer_guarded_execution(brief)
    if has_explicit_patch or has_guarded_inferred_patch:
        intent_blockers = execution_intent.get("blockers") if isinstance(execution_intent.get("blockers"), list) else []
        execution_intent.update(
            {
                "mode": "explicit_patch_execution" if has_explicit_patch else "guarded_inferred_patch_execution",
                "adapter_capability": "explicit_or_guarded_inference_adapter",
                "explicit_patch_present": has_explicit_patch,
                "real_execution_supported": True,
                "required_next_adapter": "",
                "blockers": [
                    blocker
                    for blocker in intent_blockers
                    if "unshaped source mutation" not in str(blocker)
                ],
            }
        )
    elif project_creation:
        execution_intent.update(
            {
                "mode": "greenfield_project_creation",
                "adapter_capability": "greenfield_project_scaffold_adapter",
                "explicit_patch_present": False,
                "real_execution_supported": True,
                "required_next_adapter": "",
                "blockers": [
                    blocker
                    for blocker in (execution_intent.get("blockers") if isinstance(execution_intent.get("blockers"), list) else [])
                    if "unshaped source mutation" not in str(blocker)
                ],
            }
        )
    execution_intent["dry_run_requested"] = dry_run
    if dry_run and "dry run requested; source mutation is intentionally skipped" not in execution_intent.get("blockers", []):
        blockers = execution_intent.get("blockers") if isinstance(execution_intent.get("blockers"), list) else []
        execution_intent["blockers"] = [*blockers, "dry run requested; source mutation is intentionally skipped"]
    work_packages = implementation_plan.get("implementation_work_packages") if isinstance(implementation_plan.get("implementation_work_packages"), list) else []
    package_graph = implementation_plan.get("work_package_dependency_graph") if isinstance(implementation_plan.get("work_package_dependency_graph"), dict) else {}
    package_dependencies = {
        str(row.get("package_id") or ""): {
            "depends_on": row.get("depends_on", []) if isinstance(row.get("depends_on"), list) else [],
            "dependency_reason": str(row.get("dependency_reason") or ""),
        }
        for row in package_graph.get("rows", [])
        if isinstance(row, dict) and row.get("package_id")
    }
    changed_files: list[str] = []
    notes: list[str] = []
    read_evidence = collect_pre_mutation_read_evidence(brief, edit_plan)
    preflight_blockers = [] if dry_run or project_creation else mutation_preflight_blockers(implementation_plan, edit_plan)
    if not dry_run and not review_only and planning_handoff_gate["decision"] == "blocked":
        preflight_blockers.extend(f"PlanningBrigade handoff blocked: {blocker}" for blocker in planning_handoff_gate["blockers"])
    if not dry_run and not project_creation and read_evidence["blockers"]:
        preflight_blockers.extend(read_evidence["blockers"])
    if validation_problems:
        status = "blocked"
        notes.extend(f"invalid implementation brief: {problem}" for problem in validation_problems)
    elif preflight_blockers:
        status = "blocked"
        notes.extend(preflight_blockers)
    elif brief.get("blocked"):
        status = "blocked"
        notes.extend(str(item) for item in brief.get("blockers", []))
        notes.append("implementation not started because the implementation brief is blocked")
    elif review_only:
        status = "review_only_ready"
        notes.append("review_only mode requested; CodeBrigade produced review artifacts without source mutation")
    elif dry_run:
        status = "dry_run_handoff_ready"
        notes.append("CodeBrigade adapter accepted the implementation brief without source mutation")
    elif project_creation:
        from greenfield_project import execute_greenfield_project_brief

        execution_result = execute_greenfield_project_brief(brief)
        status = "implemented" if execution_result.get("status") == "implemented" else "blocked"
        notes.extend(str(item) for item in execution_result.get("blockers", []))
        if status == "implemented":
            changed_files = execution_result.get("changed_files", []) if isinstance(execution_result.get("changed_files"), list) else []
            notes.append("CodeBrigade created a greenfield project inside the assigned workspace")
    else:
        execution_result, code_worker_pipeline = execute_worker_pipeline_brief(brief)
        status = "implemented" if execution_result.get("status") == "implemented" else "blocked"
        notes.extend(str(item) for item in execution_result.get("blockers", []))
        if status == "implemented":
            changed_files = execution_result.get("changed_files", []) if isinstance(execution_result.get("changed_files"), list) else []
            notes.append("CogitatorCodewright worker pipeline applied the requested changes")
        elif execution_intent.get("real_execution_supported") is False:
            notes.append("future CodeBrigade autonomous execution adapter remains required when the worker pipeline cannot shape the task")
    if status == "implemented":
        package_status = "implemented"
        package_evidence = "execution_result"
    elif status in {"dry_run_handoff_ready", "review_only_ready"}:
        package_status = "planned"
        package_evidence = "implementation_plan"
    else:
        package_status = "blocked"
        package_evidence = "validation_problems" if validation_problems else "blockers"
    package_statuses = [
        {
            "package_id": package_id,
            "owner": str(package.get("owner") or "CodeBrigade"),
            "impact_surfaces": package.get("impact_surfaces", []) if isinstance(package.get("impact_surfaces"), list) else [],
            "status": package_status,
            "evidence_source": package_evidence,
            "depends_on": package_dependencies.get(package_id, {}).get("depends_on", []),
            "dependency_reason": package_dependencies.get(package_id, {}).get("dependency_reason", ""),
            "blocked_by_dependencies": package_dependencies.get(package_id, {}).get("depends_on", []) if package_status == "blocked" else [],
        }
        for package in work_packages
        if isinstance(package, dict)
        for package_id in [str(package.get("id") or "")]
    ]
    if review_only:
        execution_policy_status = "review_only_no_source_execution"
    elif dry_run or status == "blocked":
        execution_policy_status = REAL_EXECUTION_STATUS
    else:
        execution_policy_status = "real_execution_adapter_active"
    report = {
        "kind": "ceraxia_code_brigade_worker_report",
        "contract_version": CONTRACT_VERSION,
        "target": "CodeBrigade",
        "status": status,
        "dry_run": dry_run,
        "changed_files": changed_files,
        "execution_intent": execution_intent,
        "planning_handoff_gate": planning_handoff_gate,
        "edit_plan": edit_plan,
        "pre_mutation_read_evidence": read_evidence,
        "autonomous_execution_request": build_autonomous_execution_request(brief, implementation_plan, execution_intent),
        "implementation_plan": implementation_plan,
        "work_package_statuses": package_statuses,
        "execution_policy_status": execution_policy_status,
        "notes": notes,
        "implementation_brief_acknowledged": not validation_problems,
        "validation_problems": validation_problems,
        "adapter": "EyeOfTerror/Mechanicum/CodeBrigade/code_brigade_adapter.py",
    }
    if "code_worker_pipeline" in locals():
        report["code_worker_pipeline"] = code_worker_pipeline
    if "execution_result" in locals():
        report["execution_result"] = execution_result
        greenfield = execution_result.get("greenfield_project") if isinstance(execution_result.get("greenfield_project"), dict) else {}
        if greenfield:
            report["greenfield_project_brief"] = greenfield.get("greenfield_project_brief", {})
            report["greenfield_architecture_plan"] = greenfield.get("architecture_plan", {})
            report["greenfield_implementation_plan"] = greenfield.get("implementation_plan", {})
            report["greenfield_implementation_trace"] = greenfield.get("implementation_trace", {})
            report["greenfield_dependency_plan"] = greenfield.get("dependency_plan", {})
            report["greenfield_verification_plan"] = greenfield.get("verification_plan", {})
            report["greenfield_module_synthesis_report"] = greenfield.get("implementation_synthesis_report", {})
            report["greenfield_memory_record"] = greenfield.get("greenfield_memory_record", {})
            report["greenfield_model_guidance_ledger"] = greenfield.get("greenfield_model_guidance_ledger", {})
    elif status == "blocked":
        report["execution_result"] = build_blocked_execution_result(notes)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a CodeBrigade worker report from a Ceraxia implementation brief.")
    parser.add_argument("--brief-json", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    brief = json.loads(args.brief_json.read_text(encoding="utf-8"))
    if not isinstance(brief, dict):
        raise SystemExit("brief JSON must be an object")
    report = build_worker_report(brief, dry_run=not args.execute)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"dry_run_handoff_ready", "implemented"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
