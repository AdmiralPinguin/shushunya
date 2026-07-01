#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from execution_contract import CONTRACT_VERSION, build_blocked_execution_result


def is_safe_repo_relative_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return bool(normalized) and not normalized.startswith(("/", "~")) and ".." not in normalized.split("/")


def validate_path_list(paths: Any, label: str) -> list[str]:
    problems: list[str] = []
    if not isinstance(paths, list):
        return [f"diagnostic repair request {label} must be a list"]
    for index, path in enumerate(paths):
        if not isinstance(path, str) or not is_safe_repo_relative_path(path):
            problems.append(f"diagnostic repair request {label}[{index}] must be a safe repo-relative path")
    return problems


def validate_diagnostic_repair_request(request: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if request.get("kind") != "ceraxia_code_brigade_diagnostic_repair_request":
        problems.append("diagnostic repair request kind is unsupported")
    if request.get("contract_version") != CONTRACT_VERSION:
        problems.append("diagnostic repair request contract_version is unsupported")
    if request.get("target") != "CodeBrigade":
        problems.append("diagnostic repair request target must be CodeBrigade")
    if request.get("status") not in {"required", "not_required"}:
        problems.append("diagnostic repair request status must be required or not_required")
    for key in ("run_id", "repo_path", "task", "verification_status", "review_decision"):
        if not isinstance(request.get(key), str):
            problems.append(f"diagnostic repair request {key} must be a string")
    queue = request.get("diagnostic_repair_queue") if isinstance(request.get("diagnostic_repair_queue"), dict) else {}
    if not queue:
        problems.append("diagnostic repair request must include diagnostic_repair_queue")
        return problems
    items = queue.get("items") if isinstance(queue.get("items"), list) else []
    item_count = queue.get("item_count")
    if not isinstance(item_count, int) or item_count != len(items):
        problems.append("diagnostic repair queue item_count must match items")
    if queue.get("status") == "queued" and not items:
        problems.append("queued diagnostic repair request must include items")
    if request.get("status") == "required" and queue.get("status") != "queued":
        problems.append("required diagnostic repair request must have queued repair items")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            problems.append(f"diagnostic repair queue item {index} must be an object")
            continue
        for key in (
            "command",
            "status",
            "priority",
            "diagnostic_signals",
            "impacted_surfaces",
            "package_ids",
            "read_before_repair",
            "concrete_read_targets",
            "stop_conditions",
            "repair_evidence_required",
        ):
            if key not in item:
                problems.append(f"diagnostic repair queue item {index} missing {key}")
        for key in ("diagnostic_signals", "read_before_repair", "stop_conditions", "repair_evidence_required"):
            if not isinstance(item.get(key), list) or not item.get(key):
                problems.append(f"diagnostic repair queue item {index} {key} must be a non-empty list")
        if not isinstance(item.get("max_repair_attempts"), int) or item.get("max_repair_attempts", 0) < 1:
            problems.append(f"diagnostic repair queue item {index} max_repair_attempts must be positive")
    problems.extend(validate_path_list(request.get("target_files_to_inspect"), "target_files_to_inspect"))
    problems.extend(validate_path_list(request.get("test_files_to_preserve"), "test_files_to_preserve"))
    if not isinstance(request.get("return_contract"), list):
        problems.append("diagnostic repair request return_contract must be a list")
    if not isinstance(request.get("reverse_dependency_index"), dict):
        problems.append("diagnostic repair request reverse_dependency_index must be an object")
    if not isinstance(request.get("scope_budget"), dict):
        problems.append("diagnostic repair request scope_budget must be an object")
    for index, item in enumerate(items):
        if isinstance(item, dict):
            problems.extend(validate_path_list(item.get("concrete_read_targets"), f"diagnostic_repair_queue.items[{index}].concrete_read_targets"))
    return problems


def build_diagnostic_repair_intake(request: dict[str, Any]) -> dict[str, Any]:
    problems = validate_diagnostic_repair_request(request)
    queue = request.get("diagnostic_repair_queue") if isinstance(request.get("diagnostic_repair_queue"), dict) else {}
    items = queue.get("items") if isinstance(queue.get("items"), list) else []
    attempt_plan = [
        {
            "attempt_id": f"repair-{index + 1}",
            "command": str(item.get("command") or ""),
            "priority": str(item.get("priority") or "normal"),
            "diagnostic_signals": item.get("diagnostic_signals", []) if isinstance(item.get("diagnostic_signals"), list) else [],
            "read_order": item.get("concrete_read_targets", []) if isinstance(item.get("concrete_read_targets"), list) else [],
            "package_ids": item.get("package_ids", []) if isinstance(item.get("package_ids"), list) else [],
            "stop_conditions": item.get("stop_conditions", []) if isinstance(item.get("stop_conditions"), list) else [],
            "required_evidence": item.get("repair_evidence_required", []) if isinstance(item.get("repair_evidence_required"), list) else [],
        }
        for index, item in enumerate(items)
        if isinstance(item, dict)
    ]
    return {
        "kind": "code_brigade_diagnostic_repair_intake",
        "contract_version": CONTRACT_VERSION,
        "status": "blocked" if problems else ("ready" if request.get("status") == "required" else "not_required"),
        "request_status": request.get("status", ""),
        "item_count": len(items),
        "attempt_plan": attempt_plan,
        "high_priority_count": sum(1 for item in items if isinstance(item, dict) and item.get("priority") == "high"),
        "impacted_surfaces": sorted(
            {
                str(surface)
                for item in items
                if isinstance(item, dict)
                for surface in (item.get("impacted_surfaces") if isinstance(item.get("impacted_surfaces"), list) else [])
                if isinstance(surface, str)
            }
        ),
        "package_ids": sorted(
            {
                str(package_id)
                for item in items
                if isinstance(item, dict)
                for package_id in (item.get("package_ids") if isinstance(item.get("package_ids"), list) else [])
                if isinstance(package_id, str)
            }
        ),
        "target_files_to_inspect": request.get("target_files_to_inspect", []) if isinstance(request.get("target_files_to_inspect"), list) else [],
        "test_files_to_preserve": request.get("test_files_to_preserve", []) if isinstance(request.get("test_files_to_preserve"), list) else [],
        "refusal_conditions": [
            "repair request validation fails",
            "repair item has no safe concrete read target",
            "repair would require editing tests without explicit authorization",
            "same verification failure repeats through max_repair_attempts",
            "requested edit would exceed scope_budget",
        ],
        "blockers": problems,
    }


def build_repair_execution_brief(request: dict[str, Any], intake: dict[str, Any]) -> dict[str, Any]:
    surfaces = intake.get("impacted_surfaces") if isinstance(intake.get("impacted_surfaces"), list) else []
    if not surfaces:
        surfaces = ["source_behavior", "test_surface"]
    package_ids = intake.get("package_ids") if isinstance(intake.get("package_ids"), list) else []
    ordered_packages = [
        package_id
        for package_id in ["evidence_survey_package", *package_ids, "minimal_patch_package", "verification_evidence_package"]
        if isinstance(package_id, str)
    ]
    ordered_packages = list(dict.fromkeys(ordered_packages))
    if "verification_evidence_package" not in ordered_packages:
        ordered_packages.append("verification_evidence_package")
    source_files = [path for path in request.get("target_files_to_inspect", []) if isinstance(path, str)]
    test_files = [path for path in request.get("test_files_to_preserve", []) if isinstance(path, str)]
    scope_budget = request.get("scope_budget") if isinstance(request.get("scope_budget"), dict) else {}
    if not scope_budget:
        scope_budget = {
            "max_source_files_to_edit": 1,
            "max_test_files_to_edit_without_explicit_user_request": 0,
            "max_docs_files_to_edit": 0,
            "requires_ceraxia_replan_when": ["diagnostic repair exceeds guarded scope"],
        }
    packages = [
        {
            "id": package_id,
            "owner": "CodeBrigade",
            "purpose": "Diagnostic repair package generated from Ceraxia repair request.",
            "impact_surfaces": surfaces,
            "read_scope": ["diagnostic_repair_request.json"],
            "edit_scope": ["candidate source files only"],
            "verification_scope": ["rerun failed command"],
            "risk_controls": ["do not edit tests to mask source behavior"],
            "blocking_policy": ["block when guarded repair inference is unavailable"],
            "handoff_criteria": ["execution_result.json names changed files or residual blockers"],
        }
        for package_id in ordered_packages
    ]
    graph_rows = [
        {
            "package_id": package_id,
            "depends_on": [] if index == 0 else ordered_packages[:index],
            "dependency_reason": "diagnostic repair keeps package order explicit",
        }
        for index, package_id in enumerate(ordered_packages)
    ]
    worker_output_contract = {
        "role": "PlanningBrigade",
        "target": "CodeBrigade",
        "required_reports": [
            "worker_report.json",
            "verification_report.json",
            "review_gate.json",
            "final_report.md",
        ],
        "required_package_statuses": ordered_packages,
        "package_result_contract": [
            {
                "package_id": package_id,
                "required_status_field": "work_package_statuses[].status",
                "allowed_statuses": ["planned", "implemented", "blocked"],
                "required_evidence_source": "work_package_statuses[].evidence_source",
                "acceptance_evidence": ["execution_result.json", "rerun failed command"],
                "constraint_evidence": ["changed files exclude tests"],
                "blocker_contract": [
                    "blocked packages must name a concrete blocker",
                    "blocked packages must preserve dependency context",
                    "blocked verification packages must return command output or execution blocker",
                ],
            }
            for package_id in ordered_packages
        ],
        "final_review_inputs": [
            "worker_report.work_package_statuses",
            "worker_report.changed_files",
            "verification_report.commands_executed",
            "review_gate.findings",
        ],
        "failure_contract": [
            "return blocked status instead of claiming partial success",
            "name residual blockers in worker_report.notes",
            "queue diagnostic repair when verification output identifies a repo-local failure",
        ],
        "diagnostic_repair_required_when": request.get("diagnostic_repair_plan", {}).get("stop_conditions", []) if isinstance(request.get("diagnostic_repair_plan"), dict) else [],
        "handoff_to": "CodeBrigade",
    }
    return {
        "kind": "ceraxia_code_brigade_implementation_brief",
        "contract_version": CONTRACT_VERSION,
        "owner": "Ceraxia",
        "target": "CodeBrigade",
        "task": str(request.get("task") or "diagnostic repair"),
        "repo_path": str(request.get("repo_path") or ""),
        "task_kinds": ["bugfix", "test_repair"],
        "risk_level": "medium",
        "selected_strategy": "diagnostic_guarded_repair",
        "allowed_scope": ["candidate files identified by diagnostic repair request"],
        "forbidden_approaches": ["editing tests to fit a broken patch", "broad rewrite without repo evidence"],
        "expected_artifacts": ["worker_report.json", "verification_report.json", "diagnostic_summary"],
        "required_verification": {"targeted_commands": [str(item.get("command") or "") for item in intake.get("attempt_plan", []) if isinstance(item, dict) and item.get("command")]},
        "surface_verification_matrix": {
            "complete": True,
            "blockers": [],
            "rows": [
                {
                    "surface": surface,
                    "risk": "medium",
                    "evidence_needed": ["failed command rerun"],
                    "covered_by": ["rerun failed command"],
                    "output_evidence_required": ["command status is recorded", "output signal is classified", "repair command output is linked to this surface"],
                    "blockers": [],
                }
                for surface in surfaces
            ],
        },
        "surface_package_matrix": {
            "complete": True,
            "blockers": [],
            "rows": [{"surface": surface, "risk": "medium", "verification_evidence": ["rerun failed command"], "package_ids": ordered_packages, "blockers": []} for surface in surfaces],
        },
        "survey_quality_gate": {"decision": "passed", "warnings": [], "blockers": []},
        "acceptance_gates": ["diagnostic repair request validates"],
        "quality_bar": {"must_have_evidence": ["diagnostic_summary", "rerun failed command"]},
        "acceptance_contract": {"must_prove": ["diagnostic repair request validates", "failed behavior is repaired or explicitly blocked"]},
        "acceptance_trace_matrix": {
            "rows": [
                {
                    "requirement": "failed behavior is repaired or explicitly blocked",
                    "source": ["diagnostic_repair_request.json"],
                    "linked_surfaces": surfaces,
                    "package_ids": ordered_packages,
                    "planned_evidence": ["rerun failed command"],
                    "status": "planned",
                }
            ],
            "row_count": 1,
            "complete": True,
            "blockers": [],
        },
        "constraint_trace_matrix": {
            "rows": [
                {
                    "constraint": "do not edit tests without explicit authorization",
                    "source": "diagnostic_repair_request.scope_budget",
                    "package_ids": ordered_packages,
                    "planned_evidence": ["changed files exclude tests"],
                    "status": "planned",
                }
            ],
            "row_count": 1,
            "complete": True,
            "blockers": [],
        },
        "expert_quality_plan": {
            "level": "standard",
            "required_for_expert_gate": False,
            "tradeoff_register": [
                {"decision": "guarded_repair_vs_broad_edit", "prefer": "guarded_repair", "reason": "Only apply repair when diagnostics and tests provide a narrow source edit."},
                {"decision": "source_fix_vs_test_masking", "prefer": "source_fix", "reason": "Tests are preserved by policy."},
            ],
            "rollback_strategy": ["rollback all touched files on patch failure", "return blockers when inference is unavailable"],
            "observability_plan": ["record operation_results", "record residual blockers"],
            "review_checklist": ["request validates", "source path is safe", "tests are preserved", "failed command is rerunnable"],
            "escalation_policy": ["return blocked when repair exceeds scope", "return blocked when failure repeats"],
        },
        "change_control_plan": {
            "target": "CodeBrigade",
            "allowed_change_intents": ["repair only failed diagnostic surface", "touch only source candidates", "preserve tests"],
            "protected_invariants": ["tests are not edited", "scope budget is honored", "safe repo-relative paths only"],
            "mutation_requires": ["diagnostic repair request validates", "execution preflight passes", "candidate source is named", "test oracle is preserved"],
            "diff_review_questions": ["Does changed source map to the repair item?", "Were tests preserved?", "Did the patch stay within budget?"],
            "rollback_triggers": ["patch operation fails", "verification cannot be rerun", "scope budget is exceeded"],
            "post_change_proofs": ["changed files are listed", "operation results are recorded", "verification command is returned for rerun"],
        },
        "investigation_playbook": {
            "target": "CodeBrigade",
            "read_stages": [
                {"stage": "repair_request", "must_collect": ["diagnostic item", "failed command"], "blocks_mutation_until": "repair item is valid"},
                {"stage": "source_target", "must_collect": ["source candidates"], "blocks_mutation_until": "source candidate is safe"},
                {"stage": "test_oracle", "must_collect": ["test files"], "blocks_mutation_until": "test oracle is preserved"},
                {"stage": "dependency_context", "must_collect": ["reverse dependencies"], "blocks_mutation_until": "caller impact is known or absent"},
                {"stage": "scope_review", "must_collect": ["scope budget"], "blocks_mutation_until": "scope budget allows source edit"},
            ],
            "evidence_questions": ["Which command failed?", "Which source file is in scope?", "Which test file is preserved?", "Which blocker stops repair?"],
            "mutation_blockers": ["repair request invalid", "source target missing", "test oracle missing"],
            "replan_triggers": ["repair needs test edit", "repair needs broad rewrite", "same failure repeats"],
        },
        "implementation_brief_blueprint": {
            "target": "CodeBrigade",
            "required_sections": ["expert_quality_plan", "investigation_playbook", "change_control_plan", "acceptance_trace_matrix", "constraint_trace_matrix", "assumption_register", "worker_output_contract"],
            "mutation_preconditions": ["diagnostic repair request validates", "execution preflight passes"],
        },
        "implementation_work_packages": {
            "packages": packages,
            "review_order": ordered_packages,
            "package_dependency_graph": {
                "rows": graph_rows,
                "root_packages": [ordered_packages[0]],
                "terminal_packages": ["verification_evidence_package"],
                "parallelizable_after_survey": [package_id for package_id in ordered_packages if package_id not in {"evidence_survey_package", "verification_evidence_package"}],
                "complete": True,
                "blockers": [],
            },
            "global_handoff_criteria": ["repair item is resolved or blocked", "tests are preserved", "verification command is ready to rerun"],
        },
        "worker_output_contract": worker_output_contract,
        "planning_review_gate": {"decision": "ready_for_ceraxia_review", "score": 90, "blockers": [], "warnings": []},
        "planning_dependency_map": {"critical_path": ["task_contract", "repo_evidence", "design_decision", "verification_contract", "implementation_brief"]},
        "work_breakdown": {
            "phases": [
                {"id": "frame_task", "owner": "Ceraxia", "exit_gate": "repair request is known"},
                {"id": "survey_repo", "owner": "CodeBrigade", "exit_gate": "source and test files are known"},
                {"id": "choose_design", "owner": "CodeBrigade", "exit_gate": "guarded inference applies"},
                {"id": "prepare_verification", "owner": "CodeBrigade", "exit_gate": "failed command is known"},
                {"id": "handoff_to_code_brigade", "owner": "CodeBrigade", "exit_gate": "brief validates"},
                {"id": "review_result", "owner": "Ceraxia", "exit_gate": "repair result is auditable"},
            ],
            "stop_conditions": ["guarded repair inference unavailable", "scope budget exceeded"],
        },
        "impact_analysis": {"surfaces": [{"surface": surface, "risk": "medium", "evidence_needed": ["failed command rerun"]} for surface in surfaces], "highest_risk_surface": surfaces[0], "requires_cross_surface_review": len(surfaces) > 1},
        "execution_forecast": {"complexity": "medium", "expected_code_brigade_iterations": 1, "recommended_timeout_minutes": 15, "scope_budget": scope_budget, "escalation_triggers": ["same failure repeats"]},
        "execution_intent": {
            "kind": "ceraxia_code_brigade_execution_intent",
            "contract_version": CONTRACT_VERSION,
            "mode": "guarded_inferred_patch_execution",
            "adapter_capability": "explicit_or_guarded_inference_adapter",
            "explicit_patch_present": False,
            "real_execution_supported": True,
            "dry_run_requested": False,
            "blockers": [],
            "required_next_adapter": "",
        },
        "code_brigade_handoff": {
            "target": "CodeBrigade",
            "steps": [{"step": "execute_guarded_diagnostic_repair", "owner": "CodeBrigade"}],
            "worker_output_contract": worker_output_contract,
            "package_dependency_graph": {
                "rows": graph_rows,
                "root_packages": [ordered_packages[0]],
                "terminal_packages": ["verification_evidence_package"],
                "parallelizable_after_survey": [package_id for package_id in ordered_packages if package_id not in {"evidence_survey_package", "verification_evidence_package"}],
                "complete": True,
                "blockers": [],
            },
        },
        "assumption_register": {
            "assumptions": [
                {"id": "repair_request_is_sufficient", "assumption": "request contains enough diagnostic context", "risk_if_false": "wrong source edit", "validation_source": "diagnostic_repair_request.json", "blocks_when_false": "repair request blocks", "owner": "Ceraxia"},
                {"id": "source_target_is_safe", "assumption": "source target is safe repo-relative", "risk_if_false": "unsafe path mutation", "validation_source": "diagnostic repair intake", "blocks_when_false": "preflight blocks", "owner": "CodeBrigade"},
                {"id": "test_oracle_is_preserved", "assumption": "test files remain unchanged", "risk_if_false": "test masking", "validation_source": "scope budget", "blocks_when_false": "repair blocks", "owner": "CodeBrigade"},
            ],
            "replan_when_false": ["repair request blocks", "preflight blocks", "repair blocks"],
        },
        "repo_survey_evidence": {
            "candidate_files": source_files,
            "test_files": test_files,
            "path_hints": [*source_files, *test_files],
            "existing_path_hints": [*source_files, *test_files],
            "missing_path_hints": [],
            "unsafe_path_hints": [],
            "entrypoint_candidates": source_files,
            "recommended_read_order": [{"path": path, "reason": "diagnostic repair read target"} for path in source_files + test_files],
            "source_summaries": [],
            "local_import_edges": [],
            "generic_import_edges": [],
            "reverse_dependency_index": request.get("reverse_dependency_index", {}) if isinstance(request.get("reverse_dependency_index"), dict) else {},
            "test_coverage_links": [{"test": test, "target": source} for test in test_files for source in source_files],
            "caller_candidates": [{"target": source, "callers": test_files, "caller_count": len(test_files)} for source in source_files],
            "contract_surface_candidates": [],
            "survey_truncated": False,
            "python_symbols_truncated": False,
        },
        "blocked": False,
        "blockers": [],
    }


def execute_diagnostic_repair_request(request: dict[str, Any]) -> dict[str, Any]:
    intake = build_diagnostic_repair_intake(request)
    if intake["status"] == "blocked":
        return build_blocked_execution_result([f"invalid diagnostic repair request: {problem}" for problem in intake["blockers"]])
    if intake["status"] == "not_required":
        return build_blocked_execution_result(["diagnostic repair request is not required"])
    supported_signals = {"assertion_failure", "failed_command", "traceback", "missing_import"}
    supported = any(
        isinstance(item, dict)
        and bool(supported_signals.intersection(item.get("diagnostic_signals") if isinstance(item.get("diagnostic_signals"), list) else []))
        for item in request.get("diagnostic_repair_queue", {}).get("items", [])
        if isinstance(request.get("diagnostic_repair_queue"), dict)
    )
    if not supported:
        return build_blocked_execution_result(["diagnostic repair executor currently supports assertion_failure, failed_command, traceback, or missing_import guarded inference only"])
    from execution_adapter import execute_implementation_brief

    return execute_implementation_brief(build_repair_execution_brief(request, intake))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Ceraxia diagnostic repair request for CodeBrigade.")
    parser.add_argument("request", help="Path to diagnostic_repair_request.json")
    parser.add_argument("--execute", action="store_true", help="Execute the narrow guarded diagnostic repair adapter.")
    args = parser.parse_args()
    payload = json.loads(Path(args.request).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        print(json.dumps({"status": "blocked", "blockers": ["request payload must be an object"]}, ensure_ascii=False, indent=2))
        return 2
    if args.execute:
        result = execute_diagnostic_repair_request(payload)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.get("status") == "implemented" else 2
    intake = build_diagnostic_repair_intake(payload)
    print(json.dumps(intake, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if intake["status"] in {"ready", "not_required"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
