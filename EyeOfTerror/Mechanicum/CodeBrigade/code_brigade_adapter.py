#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from execution_contract import CONTRACT_VERSION, build_blocked_execution_result
from implementation_brief_contract import validate_implementation_brief

REAL_EXECUTION_STATUS = "blocked_until_adapter_is_wired"


def build_implementation_plan(brief: dict[str, Any]) -> dict[str, Any]:
    evidence = brief.get("repo_survey_evidence") if isinstance(brief.get("repo_survey_evidence"), dict) else {}
    verification = brief.get("required_verification") if isinstance(brief.get("required_verification"), dict) else {}
    surface_matrix = brief.get("surface_verification_matrix") if isinstance(brief.get("surface_verification_matrix"), dict) else {}
    package_matrix = brief.get("surface_package_matrix") if isinstance(brief.get("surface_package_matrix"), dict) else {}
    survey_quality = brief.get("survey_quality_gate") if isinstance(brief.get("survey_quality_gate"), dict) else {}
    handoff = brief.get("code_brigade_handoff") if isinstance(brief.get("code_brigade_handoff"), dict) else {}
    acceptance = brief.get("acceptance_contract") if isinstance(brief.get("acceptance_contract"), dict) else {}
    expert_plan = brief.get("expert_quality_plan") if isinstance(brief.get("expert_quality_plan"), dict) else {}
    blueprint = brief.get("implementation_brief_blueprint") if isinstance(brief.get("implementation_brief_blueprint"), dict) else {}
    work_packages = brief.get("implementation_work_packages") if isinstance(brief.get("implementation_work_packages"), dict) else {}
    packages = work_packages.get("packages") if isinstance(work_packages.get("packages"), list) else []
    package_blocking_policies = {
        str(package.get("id") or ""): package.get("blocking_policy", [])
        for package in packages
        if isinstance(package, dict) and package.get("id") and isinstance(package.get("blocking_policy"), list)
    }
    planning_review = brief.get("planning_review_gate") if isinstance(brief.get("planning_review_gate"), dict) else {}
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
        "reverse_dependency_index": evidence.get("reverse_dependency_index", {}) if isinstance(evidence.get("reverse_dependency_index"), dict) else {},
        "test_coverage_links": evidence.get("test_coverage_links", []) if isinstance(evidence.get("test_coverage_links"), list) else [],
        "survey_truncated": bool(evidence.get("survey_truncated")),
        "python_symbols_truncated": bool(evidence.get("python_symbols_truncated")),
        "handoff_steps": handoff.get("steps", []) if isinstance(handoff.get("steps"), list) else [],
        "planning_critical_path": dependency.get("critical_path", []) if isinstance(dependency.get("critical_path"), list) else [],
        "planning_review_decision": planning_review.get("decision", ""),
        "planning_review_score": planning_review.get("score", 0),
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
        "mutation_preconditions": blueprint.get("mutation_preconditions", []) if isinstance(blueprint.get("mutation_preconditions"), list) else [],
        "implementation_work_packages": packages,
        "work_package_review_order": work_packages.get("review_order", []) if isinstance(work_packages.get("review_order"), list) else [],
        "work_package_blocking_policies": package_blocking_policies,
        "work_package_handoff_criteria": work_packages.get("global_handoff_criteria", []) if isinstance(work_packages.get("global_handoff_criteria"), list) else [],
        "acceptance_evidence_required": acceptance.get("must_prove", []) if isinstance(acceptance.get("must_prove"), list) else [],
        "expert_quality_level": expert_plan.get("level", ""),
        "expert_quality_required": bool(expert_plan.get("required_for_expert_gate")),
        "expert_tradeoff_register": expert_plan.get("tradeoff_register", []) if isinstance(expert_plan.get("tradeoff_register"), list) else [],
        "expert_rollback_strategy": expert_plan.get("rollback_strategy", []) if isinstance(expert_plan.get("rollback_strategy"), list) else [],
        "expert_observability_plan": expert_plan.get("observability_plan", []) if isinstance(expert_plan.get("observability_plan"), list) else [],
        "expert_review_checklist": expert_plan.get("review_checklist", []) if isinstance(expert_plan.get("review_checklist"), list) else [],
        "expert_escalation_policy": expert_plan.get("escalation_policy", []) if isinstance(expert_plan.get("escalation_policy"), list) else [],
        "verification_commands": commands,
        "surface_verification_complete": bool(surface_matrix.get("complete")),
        "surface_verification_rows": surface_matrix.get("rows", []) if isinstance(surface_matrix.get("rows"), list) else [],
        "surface_package_matrix_complete": bool(package_matrix.get("complete")),
        "surface_package_matrix_rows": package_matrix.get("rows", []) if isinstance(package_matrix.get("rows"), list) else [],
        "survey_quality_decision": survey_quality.get("decision", ""),
        "survey_quality_warnings": survey_quality.get("warnings", []) if isinstance(survey_quality.get("warnings"), list) else [],
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
        "return_contract": [
            "patch_manifest.json with changed files and rationale",
            "verification_report.json with executed, failed, skipped, or blocked commands",
            "worker_report.json with package statuses and blockers",
        ],
    }


def build_worker_report(brief: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    validation_problems = validate_implementation_brief(brief)
    implementation_plan = build_implementation_plan(brief)
    execution_intent = dict(brief.get("execution_intent") if isinstance(brief.get("execution_intent"), dict) else {})
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
    execution_intent["dry_run_requested"] = dry_run
    if dry_run and "dry run requested; source mutation is intentionally skipped" not in execution_intent.get("blockers", []):
        blockers = execution_intent.get("blockers") if isinstance(execution_intent.get("blockers"), list) else []
        execution_intent["blockers"] = [*blockers, "dry run requested; source mutation is intentionally skipped"]
    work_packages = implementation_plan.get("implementation_work_packages") if isinstance(implementation_plan.get("implementation_work_packages"), list) else []
    changed_files: list[str] = []
    notes: list[str] = []
    if validation_problems:
        status = "blocked"
        notes.extend(f"invalid implementation brief: {problem}" for problem in validation_problems)
    elif brief.get("blocked"):
        status = "blocked"
        notes.extend(str(item) for item in brief.get("blockers", []))
        notes.append("implementation not started because the implementation brief is blocked")
    elif dry_run:
        status = "dry_run_handoff_ready"
        notes.append("CodeBrigade adapter accepted the implementation brief without source mutation")
    else:
        from execution_adapter import execute_implementation_brief

        execution_result = execute_implementation_brief(brief)
        status = "implemented" if execution_result.get("status") == "implemented" else "blocked"
        notes.extend(str(item) for item in execution_result.get("blockers", []))
        if status == "implemented":
            changed_files = execution_result.get("changed_files", []) if isinstance(execution_result.get("changed_files"), list) else []
            notes.append("CodeBrigade guarded execution adapter applied the requested changes")
    if status == "implemented":
        package_status = "implemented"
        package_evidence = "execution_result"
    elif status == "dry_run_handoff_ready":
        package_status = "planned"
        package_evidence = "implementation_plan"
    else:
        package_status = "blocked"
        package_evidence = "validation_problems" if validation_problems else "blockers"
    package_statuses = [
        {
            "package_id": str(package.get("id") or ""),
            "owner": str(package.get("owner") or "CodeBrigade"),
            "impact_surfaces": package.get("impact_surfaces", []) if isinstance(package.get("impact_surfaces"), list) else [],
            "status": package_status,
            "evidence_source": package_evidence,
        }
        for package in work_packages
        if isinstance(package, dict)
    ]
    report = {
        "kind": "ceraxia_code_brigade_worker_report",
        "contract_version": CONTRACT_VERSION,
        "target": "CodeBrigade",
        "status": status,
        "dry_run": dry_run,
        "changed_files": changed_files,
        "execution_intent": execution_intent,
        "autonomous_execution_request": build_autonomous_execution_request(brief, implementation_plan, execution_intent),
        "implementation_plan": implementation_plan,
        "work_package_statuses": package_statuses,
        "execution_policy_status": REAL_EXECUTION_STATUS if dry_run or status == "blocked" else "real_execution_adapter_active",
        "notes": notes,
        "implementation_brief_acknowledged": not validation_problems,
        "validation_problems": validation_problems,
        "adapter": "EyeOfTerror/Mechanicum/CodeBrigade/code_brigade_adapter.py",
    }
    if "execution_result" in locals():
        report["execution_result"] = execution_result
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
