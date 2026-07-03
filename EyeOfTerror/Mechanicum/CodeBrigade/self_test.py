#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import code_brigade_adapter
from diagnostic_repair_contract import build_diagnostic_repair_intake, execute_diagnostic_repair_request
import execution_adapter
from execution_preflight import build_execution_preflight


def valid_brief() -> dict:
    return {
        "kind": "ceraxia_code_brigade_implementation_brief",
        "contract_version": "eye-mechanicum.v1",
        "owner": "Ceraxia",
        "target": "CodeBrigade",
        "task": "почини pytest",
        "repo_path": "/repo",
        "task_kinds": ["test_repair"],
        "risk_level": "medium",
        "selected_strategy": "minimal_design",
        "assumption_register": {
            "assumptions": [
                {
                    "id": "task_contract_is_sufficient",
                    "assumption": "task text is sufficient",
                    "risk_if_false": "wrong subset",
                    "validation_source": "problem_statement.definition_of_done",
                    "blocks_when_false": "task requires clarification before source mutation",
                    "owner": "PlanningBrigade",
                },
                {
                    "id": "repo_survey_can_find_relevant_surface",
                    "assumption": "repo survey can find files",
                    "risk_if_false": "scope guessed",
                    "validation_source": "repo_survey.json",
                    "blocks_when_false": "survey_quality_gate blocks implementation brief",
                    "owner": "Ceraxia",
                },
                {
                    "id": "verification_can_prove_user_visible_behavior",
                    "assumption": "verification can prove behavior",
                    "risk_if_false": "syntax-only proof",
                    "validation_source": "verification_report.json",
                    "blocks_when_false": "review_gate blocks finalization or requests replan",
                    "owner": "CodeBrigade",
                },
            ],
            "replan_when_false": [
                "task requires clarification before source mutation",
                "survey_quality_gate blocks implementation brief",
                "review_gate blocks finalization or requests replan",
            ],
        },
        "allowed_scope": ["candidate files identified by repository survey"],
        "forbidden_approaches": ["hardcoded one-off behavior"],
        "expected_artifacts": ["worker_report.json", "verification_report.json", "final_report.md"],
        "required_verification": {"targeted_commands": ["rerun failing test command"]},
        "surface_verification_matrix": {
            "complete": True,
            "blockers": [],
            "rows": [
                {
                    "surface": "source_behavior",
                    "risk": "medium",
                    "evidence_needed": ["candidate source files"],
                    "covered_by": ["targeted behavior verification"],
                    "output_evidence_required": ["command status is recorded", "output signal is classified"],
                    "blockers": [],
                },
                {
                    "surface": "test_surface",
                    "risk": "medium",
                    "evidence_needed": ["existing tests"],
                    "covered_by": ["rerun failing test command"],
                    "output_evidence_required": ["command status is recorded", "output signal is classified"],
                    "blockers": [],
                }
            ],
        },
        "surface_package_matrix": {
            "complete": True,
            "blockers": [],
            "rows": [
                {
                    "surface": "source_behavior",
                    "risk": "medium",
                    "verification_evidence": ["targeted behavior verification"],
                    "package_ids": ["evidence_survey_package", "minimal_patch_package", "verification_evidence_package"],
                    "blockers": [],
                },
                {
                    "surface": "test_surface",
                    "risk": "medium",
                    "verification_evidence": ["rerun failing test command"],
                    "package_ids": ["evidence_survey_package", "verification_evidence_package"],
                    "blockers": [],
                },
            ],
        },
        "survey_quality_gate": {
            "decision": "passed",
            "warnings": [],
            "blockers": [],
        },
        "acceptance_gates": ["planning packet includes all five planning roles"],
        "quality_bar": {
            "must_have_evidence": [
                "task intent is restated in implementable terms",
                "candidate files are chosen from repository evidence",
            ],
        },
        "acceptance_contract": {
            "must_prove": [
                "the original user-visible request is satisfied",
                "the changed behavior is covered by targeted verification",
            ],
        },
        "acceptance_trace_matrix": {
            "rows": [
                {
                    "requirement": "the original user-visible request is satisfied",
                    "source": ["problem_statement.definition_of_done", "acceptance_contract.must_prove"],
                    "linked_surfaces": ["source_behavior"],
                    "package_ids": ["minimal_patch_package", "verification_evidence_package"],
                    "planned_evidence": ["targeted behavior verification"],
                    "status": "planned",
                },
                {
                    "requirement": "the changed behavior is covered by targeted verification",
                    "source": ["problem_statement.definition_of_done", "acceptance_contract.must_prove"],
                    "linked_surfaces": ["source_behavior", "test_surface"],
                    "package_ids": ["minimal_patch_package", "verification_evidence_package"],
                    "planned_evidence": ["rerun failing test command"],
                    "status": "planned",
                },
            ],
            "row_count": 2,
            "definition_of_done_count": 2,
            "traced_definition_of_done_count": 2,
            "definition_of_done_complete": True,
            "missing_definition_of_done": [],
            "complete": True,
            "blockers": [],
        },
        "constraint_trace_matrix": {
            "rows": [
                {
                    "constraint": "preserve public behavior unless the task explicitly asks to change it",
                    "source": "problem_statement.known_constraints",
                    "package_ids": ["minimal_patch_package", "verification_evidence_package"],
                    "planned_evidence": ["targeted behavior verification"],
                    "status": "planned",
                },
                {
                    "constraint": "prefer repository evidence over guessing candidate files",
                    "source": "problem_statement.known_constraints",
                    "package_ids": ["evidence_survey_package", "verification_evidence_package"],
                    "planned_evidence": ["repo_survey.json", "verification_report.json"],
                    "status": "planned",
                },
            ],
            "row_count": 2,
            "complete": True,
            "blockers": [],
        },
        "expert_quality_plan": {
            "level": "standard",
            "required_for_expert_gate": False,
            "impact_surfaces": ["source_behavior", "test_surface"],
            "tradeoff_register": [
                {
                    "decision": "minimal_patch_vs_broad_rewrite",
                    "prefer": "minimal_patch",
                    "reason": "Preserve public behavior until repo evidence proves a wider rewrite.",
                },
                {
                    "decision": "fast_green_checks_vs_behavior_proof",
                    "prefer": "behavior_proof",
                    "reason": "Syntax checks are not enough for user-visible behavior.",
                },
            ],
            "rollback_strategy": [
                "keep changed-file set small enough to revert as one package",
                "name previous behavior that must still work after the patch",
            ],
            "observability_plan": [
                "record executed, skipped, failed, and blocked verification commands",
                "preserve changed files and package blockers in the worker report",
            ],
            "review_checklist": [
                "does the final package satisfy the original task",
                "are changed files justified by repository evidence",
                "does every risk surface have evidence or a blocker",
                "are residual risks named when present",
            ],
            "escalation_policy": [
                "return to Ceraxia when implementation scope exceeds the selected strategy",
                "return to PlanningBrigade when verification cannot prove the acceptance contract",
            ],
        },
        "change_control_plan": {
            "target": "CodeBrigade",
            "allowed_change_intents": [
                "change only behavior required by the original task contract",
                "touch source files only when repo evidence links them to the impacted surface",
                "adjust docs only when required to preserve the implemented contract",
            ],
            "protected_invariants": [
                "public behavior not named by the task remains compatible",
                "tests are not changed to make a broken source patch pass",
                "verification evidence must stay tied to every risk surface",
            ],
            "mutation_requires": [
                "implementation brief validates",
                "investigation playbook evidence has been acknowledged",
                "candidate file and caller impact are named",
                "rollback trigger is known before source mutation",
            ],
            "diff_review_questions": [
                "Does each changed file map to a planned impact surface?",
                "Does the diff preserve protected invariants outside the requested change?",
                "Does the diff avoid broad rewrite, hardcode, and test-masking shortcuts?",
            ],
            "rollback_triggers": [
                "changed-file set exceeds the forecast scope budget",
                "verification cannot prove the changed behavior",
                "new public contract breakage appears outside the planned impact surfaces",
            ],
            "post_change_proofs": [
                "changed files are listed with repo evidence rationale",
                "targeted verification command is executed or concretely blocked",
                "final report answers every definition_of_done item",
            ],
            "expert_review_required": False,
        },
        "investigation_playbook": {
            "target": "CodeBrigade",
            "read_stages": [
                {
                    "stage": "entrypoints_first",
                    "must_collect": ["public entrypoints", "runtime or CLI/API boundaries"],
                    "blocks_mutation_until": "the user-visible surface is named or explicitly absent",
                },
                {
                    "stage": "candidate_source_second",
                    "must_collect": ["candidate source files", "reason each candidate is in scope"],
                    "blocks_mutation_until": "candidate files are justified by repo evidence",
                },
                {
                    "stage": "callers_and_dependencies",
                    "must_collect": ["direct callers", "local import edges", "reverse dependency impact"],
                    "blocks_mutation_until": "caller impact is mapped or blocked",
                },
                {
                    "stage": "tests_and_oracles",
                    "must_collect": ["existing tests", "behavior oracle"],
                    "blocks_mutation_until": "verification can prove the requested behavior",
                },
                {
                    "stage": "contract_and_risk_review",
                    "must_collect": ["public contract assumptions", "highest-risk surface", "rollback or refusal condition"],
                    "blocks_mutation_until": "risk controls and acceptance evidence are attached",
                },
            ],
            "evidence_questions": [
                "Which file proves this behavior?",
                "Which callers could break?",
                "Which command proves user-visible behavior?",
                "What blocker stops mutation?",
            ],
            "mutation_blockers": [
                "candidate files are absent",
                "caller or test surface is unknown",
                "verification would be syntax-only",
            ],
            "replan_triggers": [
                "new impacted surface appears",
                "source edit scope exceeds budget",
                "verification cannot map to high-risk surface",
            ],
        },
        "implementation_brief_blueprint": {
            "target": "CodeBrigade",
            "required_sections": [
                "task",
                "repo_path",
                "expert_quality_plan",
                "investigation_playbook",
                "change_control_plan",
                "acceptance_trace_matrix",
                "constraint_trace_matrix",
                "assumption_register",
                "worker_output_contract",
            ],
            "mutation_preconditions": [
                "implementation brief validates",
                "investigation playbook read stages are acknowledged",
                "change control plan protected invariants are acknowledged",
                "execution preflight passes",
                "candidate files are repo-relative existing non-symlink paths",
            ],
        },
        "implementation_work_packages": {
            "packages": [
                {
                    "id": "evidence_survey_package",
                    "owner": "CodeBrigade",
                    "purpose": "Confirm candidate files before editing.",
                    "impact_surfaces": ["source_behavior", "test_surface"],
                    "read_scope": ["repo_survey_evidence.recommended_read_order"],
                    "edit_scope": [],
                    "verification_scope": ["no mutation; evidence only"],
                    "risk_controls": ["block if candidate files are missing"],
                    "blocking_policy": ["block when repo survey has no candidate files"],
                    "handoff_criteria": ["candidate file decision is grounded in repo_survey.json"],
                },
                {
                    "id": "minimal_patch_package",
                    "owner": "CodeBrigade",
                    "purpose": "Apply the smallest source change.",
                    "impact_surfaces": ["source_behavior"],
                    "read_scope": ["implementation_brief_blueprint"],
                    "edit_scope": ["candidate files identified by repository survey"],
                    "verification_scope": ["rerun failing test command"],
                    "risk_controls": ["do not edit tests to mask broken source behavior"],
                    "blocking_policy": ["block when patch preflight fails"],
                    "handoff_criteria": ["worker_report.json lists changed files"],
                },
                {
                    "id": "verification_evidence_package",
                    "owner": "CodeBrigade",
                    "purpose": "Prove each planned impact surface.",
                    "impact_surfaces": ["source_behavior", "test_surface"],
                    "read_scope": ["surface_verification_matrix"],
                    "edit_scope": [],
                    "verification_scope": ["rerun failing test command"],
                    "risk_controls": ["do not treat syntax-only checks as behavior proof"],
                    "blocking_policy": ["block when planned verification cannot run"],
                    "handoff_criteria": ["verification_report.json names executed checks"],
                },
            ],
            "review_order": [
                "evidence_survey_package",
                "minimal_patch_package",
                "verification_evidence_package",
            ],
            "global_handoff_criteria": [
                "each package is passed, blocked, or explicitly deferred",
                "package blockers are reflected in review_gate.json",
                "final report answers the original task rather than only package-local success",
            ],
            "package_dependency_graph": {
                "rows": [
                    {
                        "package_id": "evidence_survey_package",
                        "depends_on": [],
                        "dependency_reason": "root package; establishes repository evidence before mutation",
                    },
                    {
                        "package_id": "minimal_patch_package",
                        "depends_on": ["evidence_survey_package"],
                        "dependency_reason": "source mutation waits for repository evidence",
                    },
                    {
                        "package_id": "verification_evidence_package",
                        "depends_on": ["evidence_survey_package", "minimal_patch_package"],
                        "dependency_reason": "final verification waits for evidence and mutation packages",
                    },
                ],
                "root_packages": ["evidence_survey_package"],
                "terminal_packages": ["verification_evidence_package"],
                "parallelizable_after_survey": ["minimal_patch_package"],
                "execution_batches": [["evidence_survey_package"], ["minimal_patch_package"], ["verification_evidence_package"]],
                "complete": True,
                "blockers": [],
            },
        },
        "worker_output_contract": {
            "role": "PlanningBrigade",
            "target": "CodeBrigade",
            "required_reports": [
                "worker_report.json",
                "verification_report.json",
                "review_gate.json",
                "final_report.md",
            ],
            "required_package_statuses": [
                "evidence_survey_package",
                "minimal_patch_package",
                "verification_evidence_package",
            ],
            "package_result_contract": [
                {
                    "package_id": "evidence_survey_package",
                    "required_status_field": "work_package_statuses[].status",
                    "allowed_statuses": ["planned", "implemented", "blocked"],
                    "required_evidence_source": "work_package_statuses[].evidence_source",
                    "acceptance_requirements": ["the original user-visible request is satisfied"],
                    "acceptance_evidence": ["worker_report.json", "verification_report.json"],
                    "constraint_evidence": [],
                    "blocker_contract": [
                        "blocked packages must name a concrete blocker",
                        "blocked packages must preserve dependency context",
                        "blocked verification packages must return command output or execution blocker",
                    ],
                },
                {
                    "package_id": "minimal_patch_package",
                    "required_status_field": "work_package_statuses[].status",
                    "allowed_statuses": ["planned", "implemented", "blocked"],
                    "required_evidence_source": "work_package_statuses[].evidence_source",
                    "acceptance_requirements": ["the original user-visible request is satisfied"],
                    "acceptance_evidence": ["targeted behavior verification"],
                    "constraint_evidence": [],
                    "blocker_contract": [
                        "blocked packages must name a concrete blocker",
                        "blocked packages must preserve dependency context",
                        "blocked verification packages must return command output or execution blocker",
                    ],
                },
                {
                    "package_id": "verification_evidence_package",
                    "required_status_field": "work_package_statuses[].status",
                    "allowed_statuses": ["planned", "implemented", "blocked"],
                    "required_evidence_source": "work_package_statuses[].evidence_source",
                    "acceptance_requirements": ["the changed behavior is covered by targeted verification"],
                    "acceptance_evidence": ["targeted behavior verification"],
                    "constraint_evidence": ["targeted behavior verification"],
                    "blocker_contract": [
                        "blocked packages must name a concrete blocker",
                        "blocked packages must preserve dependency context",
                        "blocked verification packages must return command output or execution blocker",
                    ],
                },
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
            "diagnostic_repair_required_when": [
                "same verification failure repeats after a mutation",
            ],
            "handoff_to": "CodeBrigade",
        },
        "planning_review_gate": {
            "decision": "ready_for_ceraxia_review",
            "score": 95,
            "blockers": [],
            "warnings": [],
        },
        "planning_dependency_map": {
            "critical_path": [
                "task_contract",
                "repo_evidence",
                "design_decision",
                "verification_contract",
                "implementation_brief",
            ],
        },
        "work_breakdown": {
            "phases": [
                {"id": "frame_task", "owner": "PlanningBrigade", "exit_gate": "task intent and unknowns are explicit"},
                {"id": "survey_repo", "owner": "Ceraxia", "exit_gate": "candidate files are recorded"},
                {"id": "capture_failing_test", "owner": "CodeBrigade", "exit_gate": "failure mode is known"},
                {"id": "choose_design", "owner": "PlanningBrigade", "exit_gate": "selected strategy is approved"},
                {"id": "prepare_verification", "owner": "PlanningBrigade", "exit_gate": "verification is planned"},
                {"id": "handoff_to_code_brigade", "owner": "Ceraxia", "exit_gate": "brief validates"},
                {"id": "review_result", "owner": "Ceraxia", "exit_gate": "final package proves the request"},
            ],
            "stop_conditions": [
                "repo survey cannot identify candidate files or tests",
                "verification cannot prove the requested behavior",
            ],
        },
        "impact_analysis": {
            "surfaces": [
                {
                    "surface": "source_behavior",
                    "risk": "medium",
                    "evidence_needed": ["candidate source files", "targeted behavior verification"],
                },
                {
                    "surface": "test_surface",
                    "risk": "medium",
                    "evidence_needed": ["existing tests", "test edits avoided unless explicitly requested"],
                },
            ],
            "highest_risk_surface": "source_behavior",
            "requires_cross_surface_review": False,
        },
        "execution_forecast": {
            "complexity": "medium",
            "expected_code_brigade_iterations": 2,
            "recommended_timeout_minutes": 30,
            "scope_budget": {
                "max_source_files_to_edit": 4,
                "max_test_files_to_edit_without_explicit_user_request": 0,
                "max_docs_files_to_edit": 2,
                "requires_ceraxia_replan_when": [
                    "needed source edits exceed max_source_files_to_edit",
                    "test edits are needed but were not explicitly requested by the user",
                ],
            },
            "escalation_triggers": ["verification fails twice on the same behavior"],
        },
        "execution_intent": {
            "kind": "ceraxia_code_brigade_execution_intent",
            "contract_version": "eye-mechanicum.v1",
            "mode": "planning_handoff_only",
            "adapter_capability": "explicit_patch_adapter_only",
            "explicit_patch_present": False,
            "real_execution_supported": False,
            "dry_run_requested": False,
            "blockers": ["unshaped source mutation requires a future CodeBrigade autonomous execution adapter"],
            "required_next_adapter": "autonomous CodeBrigade source-edit adapter",
        },
        "diagnostic_repair_plan": {
            "role": "VerificationArchitect",
            "target": "CodeBrigade",
            "max_repair_attempts": 3,
            "diagnostic_inputs_required": [
                "latest verification_execution.results[].diagnostics",
                "verification_execution.results[].diagnostics.traceback_files",
                "verification_execution.results[].diagnostics.missing_imports",
                "verification_execution.results[].diagnostics.has_assertion_failure",
                "verification_execution.results[].diagnostics.has_syntax_error",
                "verification_execution.results[].diagnostics.has_no_tests_ran",
            ],
            "read_before_repair": [
                "traceback_files",
                "target_files_to_inspect",
                "test_files_to_preserve",
                "reverse_dependency_index",
                "changed-file verification output",
            ],
            "stop_conditions": [
                "same verification failure repeats after a mutation",
                "diagnostics identify no repo-local source or test surface",
                "repair would exceed execution_forecast.scope_budget",
                "zero-test diagnostics indicate wrong test runner or command mismatch",
                "missing import cannot be mapped to an allowed existing or planned file",
            ],
            "repair_evidence_required": [
                "diagnostic_summary",
                "changed files mapped to impact surfaces",
                "verification commands rerun after final mutation",
                "residual blockers when repair stops",
            ],
            "scope_budget": {
                "max_source_files_to_edit": 4,
                "max_test_files_to_edit_without_explicit_user_request": 0,
                "max_docs_files_to_edit": 2,
                "requires_ceraxia_replan_when": [
                    "needed source edits exceed max_source_files_to_edit",
                    "test edits are needed but were not explicitly requested by the user",
                ],
            },
            "requires_ceraxia_review_after_each_attempt": False,
            "handoff_to": "CodeBrigade",
        },
        "repo_survey_evidence": {
            "candidate_files": ["app.py"],
            "test_files": ["test_app.py"],
            "path_hints": ["app.py", "test_app.py"],
            "existing_path_hints": ["app.py", "test_app.py"],
            "missing_path_hints": [],
            "unsafe_path_hints": [],
            "entrypoint_candidates": ["main.py"],
            "recommended_read_order": [
                {"path": "app.py", "reason": "explicit user path hint"},
                {"path": "test_app.py", "reason": "explicit user path hint"},
            ],
            "source_summaries": [{"path": "app.ts", "language": "typescript", "symbols": ["app"], "import_like": []}],
            "local_import_edges": [{"source": "app.py", "import": "util.enabled", "target": "util.py"}],
            "generic_import_edges": [{"source": "client.ts", "import": "./api", "target": "api.ts", "language": "typescript"}],
            "reverse_dependency_index": {"app.py": ["test_app.py"], "util.py": ["app.py"]},
            "test_coverage_links": [{"test": "test_app.py", "target": "app.py"}],
            "caller_candidates": [{"target": "app.py", "callers": ["test_app.py"], "caller_count": 1}],
            "contract_surface_candidates": [{"path": "api/schema.json", "score": 6, "reason": "api/schema/contract naming or file type"}],
            "package_manifest_candidates": [{"path": "package.json", "ecosystem": "node", "package_name": "demo", "dependency_count": 1, "dev_dependency_count": 1, "script_count": 1, "parse_error": ""}],
            "survey_truncated": False,
            "python_symbols_truncated": False,
        },
        "suggested_verification_commands": ["python -m pytest test_app.py"],
        "code_brigade_handoff": {
            "target": "CodeBrigade",
            "worker_output_contract": {
                "role": "PlanningBrigade",
                "target": "CodeBrigade",
                "required_reports": [
                    "worker_report.json",
                    "verification_report.json",
                    "review_gate.json",
                    "final_report.md",
                ],
                "required_package_statuses": [
                    "evidence_survey_package",
                    "minimal_patch_package",
                    "verification_evidence_package",
                ],
                "package_result_contract": [
                    {
                        "package_id": "evidence_survey_package",
                        "required_status_field": "work_package_statuses[].status",
                        "allowed_statuses": ["planned", "implemented", "blocked"],
                        "required_evidence_source": "work_package_statuses[].evidence_source",
                        "acceptance_requirements": ["the original user-visible request is satisfied"],
                        "acceptance_evidence": ["worker_report.json", "verification_report.json"],
                        "constraint_evidence": [],
                        "blocker_contract": [
                            "blocked packages must name a concrete blocker",
                            "blocked packages must preserve dependency context",
                            "blocked verification packages must return command output or execution blocker",
                        ],
                    },
                    {
                        "package_id": "minimal_patch_package",
                        "required_status_field": "work_package_statuses[].status",
                        "allowed_statuses": ["planned", "implemented", "blocked"],
                        "required_evidence_source": "work_package_statuses[].evidence_source",
                        "acceptance_requirements": ["the original user-visible request is satisfied"],
                        "acceptance_evidence": ["targeted behavior verification"],
                        "constraint_evidence": [],
                        "blocker_contract": [
                            "blocked packages must name a concrete blocker",
                            "blocked packages must preserve dependency context",
                            "blocked verification packages must return command output or execution blocker",
                        ],
                    },
                    {
                        "package_id": "verification_evidence_package",
                        "required_status_field": "work_package_statuses[].status",
                        "allowed_statuses": ["planned", "implemented", "blocked"],
                        "required_evidence_source": "work_package_statuses[].evidence_source",
                        "acceptance_requirements": ["the changed behavior is covered by targeted verification"],
                        "acceptance_evidence": ["targeted behavior verification"],
                        "constraint_evidence": ["targeted behavior verification"],
                        "blocker_contract": [
                            "blocked packages must name a concrete blocker",
                            "blocked packages must preserve dependency context",
                            "blocked verification packages must return command output or execution blocker",
                        ],
                    },
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
                "diagnostic_repair_required_when": [
                    "same verification failure repeats after a mutation",
                ],
                "handoff_to": "CodeBrigade",
            },
            "diagnostic_repair_plan": {
                "target": "CodeBrigade",
                "max_repair_attempts": 3,
                "diagnostic_inputs_required": [
                    "latest verification_execution.results[].diagnostics",
                    "verification_execution.results[].diagnostics.traceback_files",
                    "verification_execution.results[].diagnostics.missing_imports",
                    "verification_execution.results[].diagnostics.has_assertion_failure",
                    "verification_execution.results[].diagnostics.has_syntax_error",
                    "verification_execution.results[].diagnostics.has_no_tests_ran",
                ],
                "read_before_repair": [
                    "traceback_files",
                    "target_files_to_inspect",
                    "test_files_to_preserve",
                    "reverse_dependency_index",
                    "changed-file verification output",
                ],
                "stop_conditions": [
                    "same verification failure repeats after a mutation",
                    "diagnostics identify no repo-local source or test surface",
                    "repair would exceed execution_forecast.scope_budget",
                    "zero-test diagnostics indicate wrong test runner or command mismatch",
                    "missing import cannot be mapped to an allowed existing or planned file",
                ],
                "repair_evidence_required": [
                    "diagnostic_summary",
                    "changed files mapped to impact surfaces",
                    "verification commands rerun after final mutation",
                    "residual blockers when repair stops",
                ],
                "scope_budget": {},
                "requires_ceraxia_review_after_each_attempt": False,
                "handoff_to": "CodeBrigade",
            },
            "steps": [
                {"step": "inspect_repo_evidence", "owner": "CodeBrigade"},
                {"step": "return_for_ceraxia_review", "owner": "Ceraxia"},
            ],
            "package_dependency_graph": {
                "rows": [
                    {
                        "package_id": "evidence_survey_package",
                        "depends_on": [],
                        "dependency_reason": "root package; establishes repository evidence before mutation",
                    },
                    {
                        "package_id": "minimal_patch_package",
                        "depends_on": ["evidence_survey_package"],
                        "dependency_reason": "source mutation waits for repository evidence",
                    },
                    {
                        "package_id": "verification_evidence_package",
                        "depends_on": ["evidence_survey_package", "minimal_patch_package"],
                        "dependency_reason": "final verification waits for evidence and mutation packages",
                    },
                ],
                "root_packages": ["evidence_survey_package"],
                "terminal_packages": ["verification_evidence_package"],
                "parallelizable_after_survey": ["minimal_patch_package"],
                "execution_batches": [["evidence_survey_package"], ["minimal_patch_package"], ["verification_evidence_package"]],
                "complete": True,
                "blockers": [],
            },
        },
        "planning_department": {
            "kind": "ceraxia_planning_department_package",
            "contract_version": "eye-mechanicum.v1",
            "owner": "Ceraxia",
            "status": "ready_for_code_brigade",
            "engineering_rfc": {"status": "accepted_for_code_brigade_handoff"},
            "multi_pass_repo_investigation": {"status": "complete", "phases": []},
            "code_brigade_work_package_handoff": {
                "status": "ready",
                "packages": [
                    {"id": "evidence_survey_package"},
                    {"id": "minimal_patch_package"},
                    {"id": "verification_evidence_package"},
                ],
            },
            "brigade_handoff_contract": {
                "status": "ready",
                "package_ids": [
                    "evidence_survey_package",
                    "minimal_patch_package",
                    "verification_evidence_package",
                ],
            },
            "blockers": [],
        },
        "planning_department_handoff": {
            "status": "ready",
            "packages": [
                {"id": "evidence_survey_package"},
                {"id": "minimal_patch_package"},
                {"id": "verification_evidence_package"},
            ],
        },
        "blocked": False,
        "blockers": [],
    }


def main() -> int:
    intake_schema = json.loads((Path(__file__).resolve().parent / "diagnostic_repair_intake.schema.json").read_text(encoding="utf-8"))
    attempt_required = intake_schema["properties"]["attempt_plan"]["items"]["required"]
    if "executor_supported" not in attempt_required or "unsupported_reason" not in attempt_required:
        raise AssertionError(f"diagnostic repair intake schema must contract executor support classification: {intake_schema}")
    policy = json.loads((Path(__file__).resolve().parent / "execution_policy.json").read_text(encoding="utf-8"))
    if policy["real_execution_status"] != "explicit_or_guarded_inference_adapter":
        raise AssertionError(f"execution policy must stay honest about narrow guarded execution: {policy}")
    if "implementation_brief validates against the CodeBrigade contract" not in policy["mutation_preconditions"]:
        raise AssertionError(f"execution policy must require brief validation before mutation: {policy}")
    if "implementation_plan lists target files, test files, caller candidates, contract surfaces, dependency edges, and refusal conditions" not in policy["mutation_preconditions"]:
        raise AssertionError(f"execution policy must require caller and contract evidence before mutation: {policy}")
    if "medium and high risk source mutation requires a ready PlanningBrigade planning_department handoff" not in policy["mutation_preconditions"]:
        raise AssertionError(f"execution policy must require PlanningBrigade handoff before mutation: {policy}")
    if "execution preflight passes before source mutation" not in policy["mutation_preconditions"]:
        raise AssertionError(f"execution policy must require preflight before mutation: {policy}")
    if "investigation playbook read stages are acknowledged before source mutation" not in policy["mutation_preconditions"]:
        raise AssertionError(f"execution policy must require investigation playbook before mutation: {policy}")
    if "change control protected invariants are acknowledged before source mutation" not in policy["mutation_preconditions"]:
        raise AssertionError(f"execution policy must require change control before mutation: {policy}")
    dry_report = code_brigade_adapter.build_worker_report(valid_brief(), dry_run=True)
    if dry_report["status"] != "dry_run_handoff_ready" or not dry_report["implementation_brief_acknowledged"]:
        raise AssertionError(f"valid dry-run brief should be accepted: {dry_report}")
    if dry_report["contract_version"] != "eye-mechanicum.v1":
        raise AssertionError(f"worker report contract version drifted: {dry_report}")
    if dry_report["execution_policy_status"] != "blocked_until_adapter_is_wired":
        raise AssertionError(f"dry-run worker report must expose blocked execution policy: {dry_report}")
    edit_plan = dry_report.get("edit_plan", {})
    if edit_plan.get("kind") != "code_brigade_edit_plan":
        raise AssertionError(f"worker report must expose a formal edit plan: {dry_report}")
    if not edit_plan.get("read_before_edit") or not edit_plan.get("target_files") or not edit_plan.get("acceptance_criteria"):
        raise AssertionError(f"edit plan must preserve read, target, and acceptance gates: {dry_report}")
    if not edit_plan.get("verification_commands"):
        raise AssertionError(f"edit plan must preserve verification commands: {dry_report}")
    if dry_report["execution_intent"]["mode"] != "planning_handoff_only" or dry_report["execution_intent"]["real_execution_supported"]:
        raise AssertionError(f"dry-run unshaped worker report should expose planning-only execution intent: {dry_report}")
    if dry_report["autonomous_execution_request"]["status"] != "required":
        raise AssertionError(f"dry-run unshaped worker report should prepare autonomous execution request: {dry_report}")
    if dry_report["autonomous_execution_request"]["scope_budget"]["max_test_files_to_edit_without_explicit_user_request"] != 0:
        raise AssertionError(f"autonomous request should preserve scope budget: {dry_report}")
    autonomous_request = dry_report["autonomous_execution_request"]
    if "latest verification_execution.results[].diagnostics" not in autonomous_request["diagnostic_inputs_required"]:
        raise AssertionError(f"autonomous request should declare diagnostic inputs: {dry_report}")
    if "traceback_files" not in autonomous_request["repair_loop_contract"]["must_read_before_edit"]:
        raise AssertionError(f"autonomous repair loop should require traceback reads before edits: {dry_report}")
    if not any("same verification failure repeats" in item for item in autonomous_request["repair_loop_contract"]["must_stop_when"]):
        raise AssertionError(f"autonomous repair loop should declare repeat-failure stop condition: {dry_report}")
    if not dry_report["work_package_statuses"] or any(item["status"] != "planned" for item in dry_report["work_package_statuses"]):
        raise AssertionError(f"dry-run worker report should mark work packages planned: {dry_report}")
    minimal_status = next(item for item in dry_report["work_package_statuses"] if item["package_id"] == "minimal_patch_package")
    verification_status = next(item for item in dry_report["work_package_statuses"] if item["package_id"] == "verification_evidence_package")
    if minimal_status["depends_on"] != ["evidence_survey_package"] or minimal_status["blocked_by_dependencies"]:
        raise AssertionError(f"dry-run worker report should expose package dependencies without blocking planned packages: {dry_report}")
    if "minimal_patch_package" not in verification_status["depends_on"] or verification_status["blocked_by_dependencies"]:
        raise AssertionError(f"verification package should depend on mutation package without blocking dry-run plan: {dry_report}")
    plan = dry_report["implementation_plan"]
    report_schema = json.loads((Path(__file__).resolve().parent / "code_brigade_contract.schema.json").read_text(encoding="utf-8"))
    required_plan_fields = report_schema["properties"]["implementation_plan"]["required"]
    missing_plan_fields = [field for field in required_plan_fields if field not in plan]
    if missing_plan_fields:
        raise AssertionError(f"implementation plan must satisfy schema required fields: missing={missing_plan_fields} plan={plan}")
    if plan["target_files_to_inspect"] != ["app.py"]:
        raise AssertionError(f"implementation plan should preserve survey candidates: {plan}")
    if plan["test_files_to_preserve"] != ["test_app.py"]:
        raise AssertionError(f"implementation plan should preserve test evidence: {plan}")
    if plan["existing_path_hints"] != ["app.py", "test_app.py"]:
        raise AssertionError(f"implementation plan should preserve explicit path hints: {plan}")
    if plan["recommended_read_order"][0]["path"] != "app.py":
        raise AssertionError(f"implementation plan should preserve recommended read order: {plan}")
    if plan["source_summaries_to_consider"] != [{"path": "app.ts", "language": "typescript", "symbols": ["app"], "import_like": []}]:
        raise AssertionError(f"implementation plan should preserve multi-language source summaries: {plan}")
    if "python -m pytest test_app.py" not in plan["verification_commands"]:
        raise AssertionError(f"implementation plan should include suggested verification: {plan}")
    if plan["dependency_edges_to_check"] != [{"source": "app.py", "import": "util.enabled", "target": "util.py"}]:
        raise AssertionError(f"implementation plan should preserve local dependency edges: {plan}")
    if plan["generic_dependency_edges_to_check"] != [{"source": "client.ts", "import": "./api", "target": "api.ts", "language": "typescript"}]:
        raise AssertionError(f"implementation plan should preserve generic dependency edges: {plan}")
    if plan["reverse_dependency_index"] != {"app.py": ["test_app.py"], "util.py": ["app.py"]}:
        raise AssertionError(f"implementation plan should preserve reverse dependency index: {plan}")
    if plan["test_coverage_links"] != [{"test": "test_app.py", "target": "app.py"}]:
        raise AssertionError(f"implementation plan should preserve test coverage links: {plan}")
    if plan["caller_candidates"] != [{"target": "app.py", "callers": ["test_app.py"], "caller_count": 1}]:
        raise AssertionError(f"implementation plan should preserve caller candidates: {plan}")
    if plan["contract_surface_candidates"][0]["path"] != "api/schema.json":
        raise AssertionError(f"implementation plan should preserve contract surface candidates: {plan}")
    if plan["package_manifest_candidates"][0]["path"] != "package.json" or plan["package_manifest_candidates"][0]["dependency_count"] != 1:
        raise AssertionError(f"implementation plan should preserve package manifest candidates: {plan}")
    if plan["planning_critical_path"][-1] != "implementation_brief":
        raise AssertionError(f"implementation plan should preserve planning critical path: {plan}")
    if plan["planning_review_decision"] != "ready_for_ceraxia_review" or plan["planning_review_score"] < 80:
        raise AssertionError(f"implementation plan should preserve planning review gate: {plan}")
    if not any(phase["id"] == "capture_failing_test" for phase in plan["work_phases"]):
        raise AssertionError(f"implementation plan should preserve work phases: {plan}")
    if "verification cannot prove the requested behavior" not in plan["stop_conditions"]:
        raise AssertionError(f"implementation plan should preserve stop conditions: {plan}")
    if plan["highest_risk_surface"] != "source_behavior":
        raise AssertionError(f"implementation plan should preserve impact analysis: {plan}")
    if not any(surface["surface"] == "test_surface" for surface in plan["impact_surfaces"]):
        raise AssertionError(f"implementation plan should preserve impacted surfaces: {plan}")
    if plan["execution_complexity"] != "medium" or plan["expected_code_brigade_iterations"] != 2:
        raise AssertionError(f"implementation plan should preserve execution forecast: {plan}")
    if plan["scope_budget"]["max_test_files_to_edit_without_explicit_user_request"] != 0:
        raise AssertionError(f"implementation plan should forbid unrequested test edits: {plan}")
    if plan["execution_intent"]["required_next_adapter"] != "autonomous CodeBrigade source-edit adapter":
        raise AssertionError(f"implementation plan should preserve execution intent: {plan}")
    if plan["diagnostic_repair_plan"]["target"] != "CodeBrigade":
        raise AssertionError(f"implementation plan should preserve diagnostic repair target: {plan}")
    if plan["worker_output_contract"]["target"] != "CodeBrigade":
        raise AssertionError(f"implementation plan should preserve worker output contract target: {plan}")
    if plan["worker_output_contract"]["required_package_statuses"] != plan["work_package_review_order"]:
        raise AssertionError(f"worker output contract should track package review order: {plan}")
    if not any(row["package_id"] == "minimal_patch_package" for row in plan["worker_output_contract"]["package_result_contract"]):
        raise AssertionError(f"worker output contract should require package result rows: {plan}")
    if not all(row.get("acceptance_requirements") for row in plan["worker_output_contract"]["package_result_contract"]):
        raise AssertionError(f"worker output contract should preserve package acceptance requirements: {plan}")
    if autonomous_request["repair_loop_contract"]["max_attempts"] != plan["diagnostic_repair_plan"]["max_repair_attempts"]:
        raise AssertionError(f"autonomous request should derive repair attempts from planning: {dry_report}")
    if autonomous_request["repair_loop_contract"]["must_read_before_edit"] != plan["diagnostic_repair_plan"]["read_before_repair"]:
        raise AssertionError(f"autonomous request should derive repair reads from planning: {dry_report}")
    if autonomous_request["repair_loop_contract"]["must_stop_when"] != plan["diagnostic_repair_plan"]["stop_conditions"]:
        raise AssertionError(f"autonomous request should derive stop conditions from planning: {dry_report}")
    if "execution preflight passes" not in plan["mutation_preconditions"]:
        raise AssertionError(f"implementation plan should preserve mutation preconditions: {plan}")
    if [package["id"] for package in plan["implementation_work_packages"]] != [
        "evidence_survey_package",
        "minimal_patch_package",
        "verification_evidence_package",
    ]:
        raise AssertionError(f"implementation plan should preserve work packages: {plan}")
    if plan["work_package_review_order"][0] != "evidence_survey_package":
        raise AssertionError(f"implementation plan should preserve work package review order: {plan}")
    if (
        plan["work_package_dependency_graph"]["root_packages"] != ["evidence_survey_package"]
        or plan["work_package_dependency_graph"]["terminal_packages"] != ["verification_evidence_package"]
        or plan["work_package_dependency_graph"]["rows"][-1]["package_id"] != "verification_evidence_package"
        or "minimal_patch_package" not in plan["work_package_dependency_graph"]["rows"][-1]["depends_on"]
        or plan["work_package_dependency_graph"]["execution_batches"][0] != ["evidence_survey_package"]
        or plan["work_package_dependency_graph"]["execution_batches"][-1] != ["verification_evidence_package"]
    ):
        raise AssertionError(f"implementation plan should preserve work package dependency graph: {plan}")
    repair_request = {
        "kind": "ceraxia_code_brigade_diagnostic_repair_request",
        "contract_version": "eye-mechanicum.v1",
        "run_id": "run-1",
        "status": "required",
        "target": "CodeBrigade",
        "repo_path": "/repo",
        "task": "почини pytest",
        "verification_status": "failed",
        "review_decision": "blocked",
        "diagnostic_repair_plan": plan["diagnostic_repair_plan"],
        "diagnostic_repair_queue": {
            "status": "queued",
            "item_count": 1,
            "items": [
                {
                    "command": "python -m pytest",
                    "status": "failed",
                    "priority": "normal",
                    "diagnostic_signals": ["assertion_failure"],
                    "impacted_surfaces": ["source_behavior"],
                    "package_ids": ["minimal_patch_package", "verification_evidence_package"],
                    "traceback_files": [],
                    "missing_imports": [],
                    "read_before_repair": plan["diagnostic_repair_plan"]["read_before_repair"],
                    "concrete_read_targets": ["app.py"],
                    "stop_conditions": plan["diagnostic_repair_plan"]["stop_conditions"],
                    "repair_evidence_required": plan["diagnostic_repair_plan"]["repair_evidence_required"],
                    "max_repair_attempts": 3,
                }
            ],
            "source": "verification_output_diagnostics",
            "plan_present": True,
        },
        "target_files_to_inspect": ["app.py"],
        "test_files_to_preserve": ["test_app.py"],
        "reverse_dependency_index": {"app.py": ["test_app.py"]},
        "scope_budget": plan["scope_budget"],
        "return_contract": [
            "worker_report.json with changed files, package statuses, and residual blockers",
            "verification_report.json after rerunning relevant failed commands",
            "diagnostic_summary mapped to repaired queue items",
        ],
    }
    repair_intake = build_diagnostic_repair_intake(repair_request)
    if repair_intake["status"] != "ready" or repair_intake["item_count"] != 1:
        raise AssertionError(f"diagnostic repair intake should be ready for a valid request: {repair_intake}")
    if repair_intake["package_ids"] != ["minimal_patch_package", "verification_evidence_package"]:
        raise AssertionError(f"diagnostic repair intake should preserve package ids: {repair_intake}")
    if repair_intake["attempt_plan"][0]["read_order"] != ["app.py"]:
        raise AssertionError(f"diagnostic repair intake should build attempt read order: {repair_intake}")
    if repair_intake["attempt_plan"][0]["executor_supported"] is not True or repair_intake["attempt_plan"][0]["unsupported_reason"]:
        raise AssertionError(f"diagnostic repair intake should mark assertion repair as executor-supported: {repair_intake}")
    if not repair_intake["attempt_plan"][0]["repair_signature"] or repair_intake["attempt_plan"][0]["repeated_fix_guard"]["status"] != "clear":
        raise AssertionError(f"diagnostic repair intake should expose clean repair signature guard: {repair_intake}")
    if repair_intake["attempt_history_count"] != 0 or repair_intake["repeated_fix_count"] != 0 or repair_intake["replan_required"]:
        raise AssertionError(f"fresh diagnostic repair intake should not require replan: {repair_intake}")
    if "repair item has no safe concrete read target" not in repair_intake["refusal_conditions"]:
        raise AssertionError(f"diagnostic repair intake should expose refusal conditions: {repair_intake}")
    repeated_repair_request = json.loads(json.dumps(repair_request))
    repeated_repair_request["attempt_history"] = [
        {
            "attempt_id": "repair-old",
            "repair_signature": repair_intake["attempt_plan"][0]["repair_signature"],
            "status": "failed",
        }
    ]
    repeated_intake = build_diagnostic_repair_intake(repeated_repair_request)
    if repeated_intake["status"] != "blocked" or repeated_intake["repeated_fix_count"] != 1 or not repeated_intake["replan_required"]:
        raise AssertionError(f"repeated diagnostic repair should require replan: {repeated_intake}")
    if repeated_intake["attempt_plan"][0]["repeated_fix_guard"]["matching_attempt_ids"] != ["repair-old"]:
        raise AssertionError(f"repeated diagnostic repair should identify matching prior attempt: {repeated_intake}")
    replan_packet = repeated_intake["replan_packet"]
    if replan_packet["status"] != "required" or replan_packet["target"] != "PlanningBrigade":
        raise AssertionError(f"repeated diagnostic repair should produce a PlanningBrigade replan packet: {replan_packet}")
    if not replan_packet["new_hypothesis_required"] or not replan_packet["new_evidence_required"]:
        raise AssertionError(f"replan packet should require new hypothesis and evidence: {replan_packet}")
    if repair_intake["attempt_plan"][0]["repair_signature"] not in replan_packet["blocked_repair_signatures"]:
        raise AssertionError(f"replan packet should preserve blocked repair signature: {replan_packet}")
    if not any("same repair_signature" in item for item in replan_packet["forbidden_retries"]):
        raise AssertionError(f"replan packet should forbid repeating the same repair signature: {replan_packet}")
    repeated_execution = execute_diagnostic_repair_request(repeated_repair_request)
    if repeated_execution["status"] != "blocked" or not any("replan required" in blocker for blocker in repeated_execution["blockers"]):
        raise AssertionError(f"repeated diagnostic repair execution should block before mutation: {repeated_execution}")
    if repeated_execution["replan_packet"]["status"] != "required":
        raise AssertionError(f"blocked repeated execution should return replan packet: {repeated_execution}")
    syntax_error_request = json.loads(json.dumps(repair_request))
    syntax_error_request["diagnostic_repair_queue"]["items"][0]["diagnostic_signals"] = ["syntax_error"]
    syntax_error_intake = build_diagnostic_repair_intake(syntax_error_request)
    if syntax_error_intake["status"] != "ready" or syntax_error_intake["attempt_plan"][0]["executor_supported"] is not False:
        raise AssertionError(f"syntax-error diagnostic intake should be valid but unsupported by current executor: {syntax_error_intake}")
    syntax_error_execution = execute_diagnostic_repair_request(syntax_error_request)
    if syntax_error_execution["status"] != "blocked" or not any("supports assertion_failure" in blocker for blocker in syntax_error_execution["blockers"]):
        raise AssertionError(f"syntax-error diagnostic execution should block with supported-signal reason: {syntax_error_execution}")
    broken_repair_request = dict(repair_request)
    broken_repair_request["diagnostic_repair_queue"] = dict(repair_request["diagnostic_repair_queue"], item_count=2)
    broken_intake = build_diagnostic_repair_intake(broken_repair_request)
    if broken_intake["status"] != "blocked" or not broken_intake["blockers"]:
        raise AssertionError(f"diagnostic repair intake should block inconsistent queue counts: {broken_intake}")
    unsafe_repair_request = dict(repair_request)
    unsafe_queue = dict(repair_request["diagnostic_repair_queue"])
    unsafe_item = dict(unsafe_queue["items"][0])
    unsafe_item["concrete_read_targets"] = ["/etc/passwd"]
    unsafe_queue["items"] = [unsafe_item]
    unsafe_repair_request["diagnostic_repair_queue"] = unsafe_queue
    unsafe_intake = build_diagnostic_repair_intake(unsafe_repair_request)
    if unsafe_intake["status"] != "blocked" or not any("safe repo-relative path" in blocker for blocker in unsafe_intake["blockers"]):
        raise AssertionError(f"diagnostic repair intake should block unsafe paths: {unsafe_intake}")
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        test_text = "import app\n\n\ndef test_value():\n    assert app.value() == 2\n"
        (repo / "test_app.py").write_text(test_text, encoding="utf-8")
        executable_request = dict(repair_request)
        executable_request.update({"repo_path": str(repo), "target_files_to_inspect": ["app.py"], "test_files_to_preserve": ["test_app.py"]})
        execution = execute_diagnostic_repair_request(executable_request)
        if execution["status"] != "implemented" or execution["changed_files"] != ["app.py"]:
            raise AssertionError(f"diagnostic repair executor should apply guarded source repair: {execution}")
        if "return 2" not in (repo / "app.py").read_text(encoding="utf-8"):
            raise AssertionError("diagnostic repair executor should update source return literal")
        if (repo / "test_app.py").read_text(encoding="utf-8") != test_text:
            raise AssertionError("diagnostic repair executor must preserve test file content")
        (repo / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        traceback_request = json.loads(json.dumps(executable_request))
        traceback_request["diagnostic_repair_queue"]["items"][0]["diagnostic_signals"] = ["traceback"]
        traceback_execution = execute_diagnostic_repair_request(traceback_request)
        if traceback_execution["status"] != "implemented" or traceback_execution["changed_files"] != ["app.py"]:
            raise AssertionError(f"traceback diagnostic repair should use guarded source repair: {traceback_execution}")
        if "return 2" not in (repo / "app.py").read_text(encoding="utf-8"):
            raise AssertionError("traceback diagnostic repair should update source return literal")
        (repo / "app.py").write_text("", encoding="utf-8")
        missing_import_request = json.loads(json.dumps(executable_request))
        missing_import_request["diagnostic_repair_queue"]["items"][0]["diagnostic_signals"] = ["missing_import"]
        missing_import_request["diagnostic_repair_queue"]["items"][0]["missing_imports"] = ["value"]
        missing_import_execution = execute_diagnostic_repair_request(missing_import_request)
        if missing_import_execution["status"] != "implemented" or missing_import_execution["changed_files"] != ["app.py"]:
            raise AssertionError(f"missing-import diagnostic repair should use guarded source repair: {missing_import_execution}")
        if "def value():\n    return 2\n" not in (repo / "app.py").read_text(encoding="utf-8"):
            raise AssertionError("missing-import diagnostic repair should add source function from test oracle")
        (repo / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        request_path = repo / "diagnostic_repair_request.json"
        request_path.write_text(json.dumps(executable_request), encoding="utf-8")
        cli = subprocess.run(
            ["python3", str(Path(__file__).resolve().parent / "diagnostic_repair_contract.py"), "--execute", str(request_path)],
            text=True,
            capture_output=True,
            check=False,
        )
        if cli.returncode != 0 or '"status": "implemented"' not in cli.stdout:
            raise AssertionError(f"diagnostic repair CLI --execute should implement guarded repair: rc={cli.returncode} out={cli.stdout} err={cli.stderr}")
        if "return 2" not in (repo / "app.py").read_text(encoding="utf-8"):
            raise AssertionError("diagnostic repair CLI --execute should update source return literal")
    with tempfile.TemporaryDirectory() as tmp:
        request_path = Path(tmp) / "diagnostic_repair_request.json"
        request_path.write_text(json.dumps(repair_request), encoding="utf-8")
        cli = subprocess.run(
            ["python3", str(Path(__file__).resolve().parent / "diagnostic_repair_contract.py"), str(request_path)],
            text=True,
            capture_output=True,
            check=False,
        )
        if cli.returncode != 0 or '"status": "ready"' not in cli.stdout:
            raise AssertionError(f"diagnostic repair CLI should accept valid requests: rc={cli.returncode} out={cli.stdout} err={cli.stderr}")
        request_path.write_text(json.dumps(broken_repair_request), encoding="utf-8")
        cli = subprocess.run(
            ["python3", str(Path(__file__).resolve().parent / "diagnostic_repair_contract.py"), str(request_path)],
            text=True,
            capture_output=True,
            check=False,
        )
        if cli.returncode != 2 or "item_count must match" not in cli.stdout:
            raise AssertionError(f"diagnostic repair CLI should reject invalid requests: rc={cli.returncode} out={cli.stdout} err={cli.stderr}")
    if plan["work_package_blocking_policies"].get("minimal_patch_package") != ["block when patch preflight fails"]:
        raise AssertionError(f"implementation plan should expose work package blocking policies: {plan}")
    if "final report answers the original task rather than only package-local success" not in plan["work_package_handoff_criteria"]:
        raise AssertionError(f"implementation plan should preserve global handoff criteria: {plan}")
    if "the original user-visible request is satisfied" not in plan["acceptance_evidence_required"]:
        raise AssertionError(f"implementation plan should preserve acceptance evidence: {plan}")
    if not plan["acceptance_trace_complete"] or plan["acceptance_trace_rows"][0]["requirement"] != "the original user-visible request is satisfied":
        raise AssertionError(f"implementation plan should preserve acceptance trace matrix: {plan}")
    if not plan["definition_of_done_trace_complete"] or plan["definition_of_done_count"] != 2 or plan["traced_definition_of_done_count"] != 2:
        raise AssertionError(f"implementation plan should preserve definition_of_done trace coverage: {plan}")
    if not plan["constraint_trace_complete"] or plan["constraint_trace_rows"][0]["constraint"] != "preserve public behavior unless the task explicitly asks to change it":
        raise AssertionError(f"implementation plan should preserve constraint trace matrix: {plan}")
    if plan["expert_quality_level"] != "standard" or plan["expert_quality_required"]:
        raise AssertionError(f"implementation plan should preserve expert quality level: {plan}")
    if plan["expert_tradeoff_register"][0]["decision"] != "minimal_patch_vs_broad_rewrite":
        raise AssertionError(f"implementation plan should preserve expert quality tradeoffs: {plan}")
    if "preserve changed files and package blockers in the worker report" not in plan["expert_observability_plan"]:
        raise AssertionError(f"implementation plan should preserve expert observability requirements: {plan}")
    if "return to PlanningBrigade when verification cannot prove the acceptance contract" not in plan["expert_escalation_policy"]:
        raise AssertionError(f"implementation plan should preserve expert escalation policy: {plan}")
    if "tests are not changed to make a broken source patch pass" not in plan["change_protected_invariants"]:
        raise AssertionError(f"implementation plan should preserve change protected invariants: {plan}")
    if "rollback trigger is known before source mutation" not in plan["change_mutation_requires"]:
        raise AssertionError(f"implementation plan should preserve change mutation requirements: {plan}")
    if "verification cannot prove the changed behavior" not in plan["change_rollback_triggers"]:
        raise AssertionError(f"implementation plan should preserve change rollback triggers: {plan}")
    if "final report answers every definition_of_done item" not in plan["change_post_change_proofs"]:
        raise AssertionError(f"implementation plan should preserve change post-change proofs: {plan}")
    if plan["investigation_read_stages"][0]["stage"] != "entrypoints_first":
        raise AssertionError(f"implementation plan should preserve investigation read stages: {plan}")
    if "Which callers could break?" not in plan["investigation_evidence_questions"]:
        raise AssertionError(f"implementation plan should preserve investigation evidence questions: {plan}")
    if "verification would be syntax-only" not in plan["investigation_mutation_blockers"]:
        raise AssertionError(f"implementation plan should preserve investigation mutation blockers: {plan}")
    if not plan["surface_verification_complete"] or plan["surface_verification_rows"][0]["surface"] != "source_behavior":
        raise AssertionError(f"implementation plan should preserve surface verification matrix: {plan}")
    if not plan["surface_package_matrix_complete"] or plan["surface_package_matrix_rows"][0]["package_ids"][0] != "evidence_survey_package":
        raise AssertionError(f"implementation plan should preserve surface package matrix: {plan}")
    if plan["survey_quality_decision"] != "passed":
        raise AssertionError(f"implementation plan should preserve survey quality gate: {plan}")
    if len(plan["assumption_rows"]) < 3 or "review_gate blocks finalization or requests replan" not in plan["assumption_replan_triggers"]:
        raise AssertionError(f"implementation plan should preserve assumption register: {plan}")
    if plan["survey_truncated"]:
        raise AssertionError(f"small survey fixture should not be marked truncated: {plan}")
    if plan["python_symbols_truncated"]:
        raise AssertionError(f"small survey fixture should not have truncated python symbols: {plan}")
    if not plan["refusal_conditions"]:
        raise AssertionError(f"implementation plan should include refusal conditions: {plan}")
    execute_report = code_brigade_adapter.build_worker_report(valid_brief(), dry_run=False)
    if execute_report["status"] != "blocked" or not execute_report["notes"]:
        raise AssertionError(f"real execution should be honestly blocked until adapter is wired: {execute_report}")
    if execute_report["execution_result"]["status"] != "blocked":
        raise AssertionError(f"blocked execution should expose a formal execution_result: {execute_report}")
    if not execute_report["execution_result"]["blockers"]:
        raise AssertionError(f"blocked execution_result should explain blockers: {execute_report}")
    direct_execution = execution_adapter.execute_implementation_brief(valid_brief())
    if direct_execution["status"] != "blocked" or not direct_execution["blockers"]:
        raise AssertionError(f"execution adapter stub should return a formal blocker: {direct_execution}")
    if direct_execution["preflight"]["candidate_file_count"] != 1:
        raise AssertionError(f"execution preflight should summarize survey evidence: {direct_execution}")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("def app():\n    return False\n", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import app\n\ndef test_app():\n    assert app() is True\n", encoding="utf-8")
        patch_brief = valid_brief()
        patch_brief["repo_path"] = tmp
        patch_brief["task"] += "\nCERAXIA_PATCH:\n" + json.dumps(
            {
                "operations": [
                    {
                        "type": "replace",
                        "path": "app.py",
                        "old": "return False",
                        "new": "return True",
                    }
                ]
            }
        )
        patch_brief["execution_intent"] = {
            "kind": "ceraxia_code_brigade_execution_intent",
            "contract_version": "eye-mechanicum.v1",
            "mode": "explicit_patch_execution",
            "adapter_capability": "explicit_patch_adapter_only",
            "explicit_patch_present": True,
            "real_execution_supported": True,
            "dry_run_requested": False,
            "blockers": [],
            "required_next_adapter": "",
        }
        missing_handoff_brief = dict(patch_brief)
        missing_handoff_brief.pop("planning_department", None)
        missing_handoff_brief.pop("planning_department_handoff", None)
        missing_handoff_report = code_brigade_adapter.build_worker_report(missing_handoff_brief, dry_run=False)
        if (
            missing_handoff_report["status"] != "blocked"
            or missing_handoff_report["planning_handoff_gate"]["decision"] != "blocked"
            or not any("PlanningBrigade handoff blocked" in note for note in missing_handoff_report["notes"])
        ):
            raise AssertionError(f"real source mutation must require PlanningBrigade handoff: {missing_handoff_report}")
        patch_report = code_brigade_adapter.build_worker_report(patch_brief, dry_run=False)
        if patch_report["status"] != "implemented" or patch_report["changed_files"] != ["app.py"]:
            raise AssertionError(f"explicit patch execution should report implemented changed files: {patch_report}")
        if patch_report["planning_handoff_gate"]["decision"] != "passed":
            raise AssertionError(f"explicit patch execution should pass PlanningBrigade handoff gate: {patch_report}")
        if patch_report["execution_intent"]["mode"] != "explicit_patch_execution" or not patch_report["execution_intent"]["real_execution_supported"]:
            raise AssertionError(f"explicit patch execution should expose executable intent: {patch_report}")
        if patch_report["autonomous_execution_request"]["status"] != "not_required":
            raise AssertionError(f"explicit patch execution should not request autonomous adapter: {patch_report}")
        if "supports this execution path" not in patch_report["autonomous_execution_request"]["reason"]:
            raise AssertionError(f"explicit patch execution should explain why autonomous adapter is not required: {patch_report}")
        if not patch_report["work_package_statuses"] or any(item["status"] != "implemented" for item in patch_report["work_package_statuses"]):
            raise AssertionError(f"implemented patch should mark work packages implemented: {patch_report}")
        if any(item["blocked_by_dependencies"] for item in patch_report["work_package_statuses"]):
            raise AssertionError(f"implemented patch should not mark package dependencies blocked: {patch_report}")
        if patch_report["execution_result"]["status"] != "implemented":
            raise AssertionError(f"explicit patch execution result should be implemented: {patch_report}")
        patch_manifest = patch_report["execution_result"]["patch_manifest"]
        if patch_manifest["operation_count"] != 1 or patch_manifest["changed_file_count"] != 1 or patch_manifest["multi_file"]:
            raise AssertionError(f"single-file explicit patch should expose patch manifest: {patch_report}")
        if "return True" not in Path(tmp, "app.py").read_text(encoding="utf-8"):
            raise AssertionError("explicit patch execution did not update app.py")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("def app():\n    return False\n", encoding="utf-8")
        Path(tmp, "config.py").write_text("ENABLED = False\n", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import app\n\ndef test_app():\n    assert app() is True\n", encoding="utf-8")
        multi_file_brief = valid_brief()
        multi_file_brief["repo_path"] = tmp
        multi_file_brief["repo_survey_evidence"]["candidate_files"] = ["app.py", "config.py"]
        multi_file_brief["execution_forecast"]["scope_budget"]["max_source_files_to_edit"] = 2
        multi_file_brief["task"] += "\nCERAXIA_PATCH:\n" + json.dumps(
            {
                "operations": [
                    {"type": "replace", "path": "app.py", "old": "return False", "new": "return True"},
                    {"type": "replace_python_constant", "path": "config.py", "symbol_name": "ENABLED", "old_literal": "False", "new_literal": "True"},
                ]
            }
        )
        multi_file_report = code_brigade_adapter.build_worker_report(multi_file_brief, dry_run=False)
        result = multi_file_report["execution_result"]
        manifest = result["patch_manifest"]
        if multi_file_report["status"] != "implemented" or sorted(multi_file_report["changed_files"]) != ["app.py", "config.py"]:
            raise AssertionError(f"multi-file patch should report both changed files: {multi_file_report}")
        if manifest["multi_file"] is not True or manifest["changed_file_count"] != 2 or manifest["operation_count"] != 2:
            raise AssertionError(f"multi-file patch manifest should expose operation/file counts: {multi_file_report}")
        if manifest["operation_counts"].get("replace") != 1 or manifest["operation_counts"].get("replace_python_constant") != 1:
            raise AssertionError(f"multi-file patch manifest should count operation types: {multi_file_report}")
        file_rows = {row["path"]: row for row in manifest["files"]}
        if sorted(file_rows) != ["app.py", "config.py"]:
            raise AssertionError(f"multi-file patch manifest should expose per-file rows: {multi_file_report}")
        if file_rows["app.py"]["operations"] != ["replace"] or file_rows["config.py"]["operations"] != ["replace_python_constant"]:
            raise AssertionError(f"multi-file patch manifest should map operations to files: {multi_file_report}")
        if file_rows["app.py"]["applied_operation_count"] != 1 or file_rows["config.py"]["rollback_touched"]:
            raise AssertionError(f"multi-file patch manifest should expose per-file application state: {multi_file_report}")
        if not file_rows["app.py"]["before_sha256"] or not file_rows["app.py"]["after_sha256"] or file_rows["app.py"]["before_sha256"] == file_rows["app.py"]["after_sha256"]:
            raise AssertionError(f"multi-file patch manifest should expose changed file digests: {multi_file_report}")
        if not result["operation_results"][0].get("before_sha256") or not result["operation_results"][0].get("after_sha256"):
            raise AssertionError(f"operation results should expose before/after digests: {multi_file_report}")
        if "return True" not in Path(tmp, "app.py").read_text(encoding="utf-8") or "ENABLED = True" not in Path(tmp, "config.py").read_text(encoding="utf-8"):
            raise AssertionError("multi-file patch did not apply both files")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("def app():\n    return False\n", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import app\n\ndef test_app():\n    assert app() is True\n", encoding="utf-8")
        inferred_replace_brief = valid_brief()
        inferred_replace_brief["repo_path"] = tmp
        inferred_replace_brief["task"] = "В файле `app.py` замени `return False` на `return True`."
        inferred_replace_report = code_brigade_adapter.build_worker_report(inferred_replace_brief, dry_run=False)
        if inferred_replace_report["status"] != "implemented" or inferred_replace_report["changed_files"] != ["app.py"]:
            raise AssertionError(f"guarded inferred replace should report implemented changed files: {inferred_replace_report}")
        if inferred_replace_report["execution_intent"]["mode"] != "guarded_inferred_patch_execution":
            raise AssertionError(f"guarded inferred replace should expose inferred intent: {inferred_replace_report}")
        if inferred_replace_report["autonomous_execution_request"]["status"] != "not_required":
            raise AssertionError(f"guarded inferred replace should not request autonomous adapter: {inferred_replace_report}")
        if "supports this execution path" not in inferred_replace_report["autonomous_execution_request"]["reason"]:
            raise AssertionError(f"guarded inferred replace should explain why autonomous adapter is not required: {inferred_replace_report}")
        if "natural_language_simple_replace" not in inferred_replace_report["execution_result"]["patch_summary"]:
            raise AssertionError(f"guarded inferred replace should expose patch source: {inferred_replace_report}")
        if "return True" not in Path(tmp, "app.py").read_text(encoding="utf-8"):
            raise AssertionError("guarded inferred replace did not update app.py")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import value\n\ndef test_value():\n    assert value() == 42\n", encoding="utf-8")
        inferred_add_brief = valid_brief()
        inferred_add_brief["repo_path"] = tmp
        inferred_add_brief["task"] = "В файле `app.py` добавь функцию `value`, возвращающую `42`."
        inferred_add_report = code_brigade_adapter.build_worker_report(inferred_add_brief, dry_run=False)
        if inferred_add_report["status"] != "implemented" or inferred_add_report["changed_files"] != ["app.py"]:
            raise AssertionError(f"guarded inferred add-function should report implemented changed files: {inferred_add_report}")
        if "def value():\n    return 42\n" not in Path(tmp, "app.py").read_text(encoding="utf-8"):
            raise AssertionError("guarded inferred add-function did not update app.py")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import values\n\ndef test_values():\n    assert values() == [1, 2, 3]\n", encoding="utf-8")
        list_literal_brief = valid_brief()
        list_literal_brief["repo_path"] = tmp
        list_literal_brief["task"] = "почини app.py чтобы тест проходил"
        list_literal_report = code_brigade_adapter.build_worker_report(list_literal_brief, dry_run=False)
        if list_literal_report["status"] != "implemented" or list_literal_report["changed_files"] != ["app.py"]:
            raise AssertionError(f"test-inferred list literal should report implemented changed files: {list_literal_report}")
        if "def values():\n    return [1, 2, 3]\n" not in Path(tmp, "app.py").read_text(encoding="utf-8"):
            raise AssertionError("test-inferred list literal did not update app.py")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("", encoding="utf-8")
        Path(tmp, "test_app.py").write_text(
            "import unittest\nimport app\n\n"
            "class ValueTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(app.value(), 42)\n",
            encoding="utf-8",
        )
        module_function_brief = valid_brief()
        module_function_brief["repo_path"] = tmp
        module_function_brief["task"] = "почини app.py чтобы тест проходил"
        module_function_report = code_brigade_adapter.build_worker_report(module_function_brief, dry_run=False)
        if module_function_report["status"] != "implemented" or module_function_report["changed_files"] != ["app.py"]:
            raise AssertionError(f"test-inferred module function should report implemented changed files: {module_function_report}")
        if "def value():\n    return 42\n" not in Path(tmp, "app.py").read_text(encoding="utf-8"):
            raise AssertionError("test-inferred module function did not update app.py")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import value\n\ndef test_value():\n    assert value() == 42\n", encoding="utf-8")
        duplicate_add_brief = valid_brief()
        duplicate_add_brief["repo_path"] = tmp
        duplicate_add_brief["task"] = "В файле `app.py` добавь функцию `value`, возвращающую `42`."
        duplicate_add_report = code_brigade_adapter.build_worker_report(duplicate_add_brief, dry_run=False)
        if duplicate_add_report["status"] != "blocked" or "function already exists" not in " ".join(duplicate_add_report["execution_result"]["blockers"]):
            raise AssertionError(f"guarded inferred duplicate function should block: {duplicate_add_report}")
        if Path(tmp, "app.py").read_text(encoding="utf-8").count("def value") != 1:
            raise AssertionError("blocked duplicate add-function should not mutate app.py")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import value\n\ndef test_value():\n    assert value() == 42\n", encoding="utf-8")
        test_inferred_brief = valid_brief()
        test_inferred_brief["repo_path"] = tmp
        test_inferred_brief["task"] = "почини app.py чтобы тест проходил"
        test_inferred_report = code_brigade_adapter.build_worker_report(test_inferred_brief, dry_run=False)
        if test_inferred_report["status"] != "implemented" or test_inferred_report["changed_files"] != ["app.py"]:
            raise AssertionError(f"test-inferred missing function should report implemented changed files: {test_inferred_report}")
        if test_inferred_report["execution_intent"]["mode"] != "guarded_inferred_patch_execution":
            raise AssertionError(f"test-inferred missing function should expose guarded inferred intent: {test_inferred_report}")
        if "test_inferred_missing_function" not in test_inferred_report["execution_result"]["patch_summary"]:
            raise AssertionError(f"test-inferred missing function should expose patch source: {test_inferred_report}")
        if "def value():\n    return 42\n" not in Path(tmp, "app.py").read_text(encoding="utf-8"):
            raise AssertionError("test-inferred missing function did not update app.py")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import enabled\n\ndef test_enabled():\n    assert enabled() is True\n", encoding="utf-8")
        inferred_bool_brief = valid_brief()
        inferred_bool_brief["repo_path"] = tmp
        inferred_bool_brief["task"] = "почини app.py чтобы тест проходил"
        inferred_bool_report = code_brigade_adapter.build_worker_report(inferred_bool_brief, dry_run=False)
        if inferred_bool_report["status"] != "implemented" or inferred_bool_report["changed_files"] != ["app.py"]:
            raise AssertionError(f"test-inferred boolean function should report implemented changed files: {inferred_bool_report}")
        if "def enabled():\n    return True\n" not in Path(tmp, "app.py").read_text(encoding="utf-8"):
            raise AssertionError("test-inferred boolean function did not update app.py")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import value\n\ndef test_value():\n    assert value() == 42\n", encoding="utf-8")
        return_mismatch_brief = valid_brief()
        return_mismatch_brief["repo_path"] = tmp
        return_mismatch_brief["task"] = "почини app.py чтобы тест проходил"
        return_mismatch_report = code_brigade_adapter.build_worker_report(return_mismatch_brief, dry_run=False)
        if return_mismatch_report["status"] != "implemented" or return_mismatch_report["changed_files"] != ["app.py"]:
            raise AssertionError(f"test-inferred return mismatch should report implemented changed files: {return_mismatch_report}")
        if "runtime_diagnostic_return_mismatch" not in return_mismatch_report["execution_result"]["patch_summary"]:
            raise AssertionError(f"test-inferred return mismatch should expose patch source: {return_mismatch_report}")
        if "return 42" not in Path(tmp, "app.py").read_text(encoding="utf-8"):
            raise AssertionError("test-inferred return mismatch did not update app.py")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import ANSWER\n\ndef test_answer():\n    assert ANSWER == 42\n", encoding="utf-8")
        missing_constant_brief = valid_brief()
        missing_constant_brief["repo_path"] = tmp
        missing_constant_brief["task"] = "почини app.py чтобы тест проходил"
        missing_constant_report = code_brigade_adapter.build_worker_report(missing_constant_brief, dry_run=False)
        if missing_constant_report["status"] != "implemented" or missing_constant_report["changed_files"] != ["app.py"]:
            raise AssertionError(f"test-inferred missing constant should report implemented changed files: {missing_constant_report}")
        if "test_inferred_missing_constant" not in missing_constant_report["execution_result"]["patch_summary"]:
            raise AssertionError(f"test-inferred missing constant should expose patch source: {missing_constant_report}")
        if "ANSWER = 42\n" not in Path(tmp, "app.py").read_text(encoding="utf-8"):
            raise AssertionError("test-inferred missing constant did not update app.py")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("", encoding="utf-8")
        Path(tmp, "test_app.py").write_text(
            "import unittest\nimport app\n\n"
            "class AnswerTest(unittest.TestCase):\n"
            "    def test_answer(self):\n"
            "        self.assertEqual(app.ANSWER, 42)\n",
            encoding="utf-8",
        )
        module_constant_brief = valid_brief()
        module_constant_brief["repo_path"] = tmp
        module_constant_brief["task"] = "почини app.py чтобы тест проходил"
        module_constant_report = code_brigade_adapter.build_worker_report(module_constant_brief, dry_run=False)
        if module_constant_report["status"] != "implemented" or module_constant_report["changed_files"] != ["app.py"]:
            raise AssertionError(f"test-inferred module constant should report implemented changed files: {module_constant_report}")
        if "ANSWER = 42\n" not in Path(tmp, "app.py").read_text(encoding="utf-8"):
            raise AssertionError("test-inferred module constant did not update app.py")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import ENABLED\n\ndef test_enabled():\n    assert ENABLED is True\n", encoding="utf-8")
        missing_bool_constant_brief = valid_brief()
        missing_bool_constant_brief["repo_path"] = tmp
        missing_bool_constant_brief["task"] = "почини app.py чтобы тест проходил"
        missing_bool_constant_report = code_brigade_adapter.build_worker_report(missing_bool_constant_brief, dry_run=False)
        if missing_bool_constant_report["status"] != "implemented" or missing_bool_constant_report["changed_files"] != ["app.py"]:
            raise AssertionError(f"test-inferred boolean constant should report implemented changed files: {missing_bool_constant_report}")
        if "ENABLED = True\n" not in Path(tmp, "app.py").read_text(encoding="utf-8"):
            raise AssertionError("test-inferred boolean constant did not update app.py")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("ANSWER = 1\n", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import ANSWER\n\ndef test_answer():\n    assert ANSWER == 42\n", encoding="utf-8")
        constant_mismatch_brief = valid_brief()
        constant_mismatch_brief["repo_path"] = tmp
        constant_mismatch_brief["task"] = "почини app.py чтобы тест проходил"
        constant_mismatch_report = code_brigade_adapter.build_worker_report(constant_mismatch_brief, dry_run=False)
        if constant_mismatch_report["status"] != "implemented" or constant_mismatch_report["changed_files"] != ["app.py"]:
            raise AssertionError(f"test-inferred constant mismatch should report implemented changed files: {constant_mismatch_report}")
        if "test_inferred_constant_mismatch" not in constant_mismatch_report["execution_result"]["patch_summary"]:
            raise AssertionError(f"test-inferred constant mismatch should expose patch source: {constant_mismatch_report}")
        if "ANSWER = 42\n" not in Path(tmp, "app.py").read_text(encoding="utf-8"):
            raise AssertionError("test-inferred constant mismatch did not update app.py")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("def value():\n    return 42\n", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import value\n\ndef test_value():\n    assert value() == 42\n", encoding="utf-8")
        matching_return_brief = valid_brief()
        matching_return_brief["repo_path"] = tmp
        matching_return_brief["task"] = "почини app.py чтобы тест проходил"
        matching_return_report = code_brigade_adapter.build_worker_report(matching_return_brief, dry_run=False)
        if matching_return_report["status"] != "blocked" or matching_return_report["autonomous_execution_request"]["status"] != "required":
            raise AssertionError(f"matching return should not trigger a no-op inferred mutation: {matching_return_report}")
        if Path(tmp, "app.py").read_text(encoding="utf-8").count("return 42") != 1:
            raise AssertionError("matching return should not mutate app.py")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("", encoding="utf-8")
        Path(tmp, "test_app.py").write_text(
            "from app import value\nfrom app import other\n\n"
            "def test_value():\n    assert value() == 42\n\n"
            "def test_other():\n    assert other() == 7\n",
            encoding="utf-8",
        )
        ambiguous_test_inferred_brief = valid_brief()
        ambiguous_test_inferred_brief["repo_path"] = tmp
        ambiguous_test_inferred_brief["task"] = "почини app.py чтобы тесты проходили"
        ambiguous_test_inferred_report = code_brigade_adapter.build_worker_report(ambiguous_test_inferred_brief, dry_run=False)
        if ambiguous_test_inferred_report["status"] != "blocked" or ambiguous_test_inferred_report["autonomous_execution_request"]["status"] != "required":
            raise AssertionError(f"ambiguous test-inferred task should still request autonomous adapter: {ambiguous_test_inferred_report}")
        if Path(tmp, "app.py").read_text(encoding="utf-8"):
            raise AssertionError("ambiguous test-inferred task should not mutate app.py")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("def app(value):\n    return False\n", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import app\n\ndef test_app():\n    assert app(1) is True\n", encoding="utf-8")
        ambiguous_brief = valid_brief()
        ambiguous_brief["repo_path"] = tmp
        ambiguous_brief["task"] = "почини app.py чтобы тест проходил"
        ambiguous_report = code_brigade_adapter.build_worker_report(ambiguous_brief, dry_run=False)
        if ambiguous_report["status"] != "blocked" or ambiguous_report["autonomous_execution_request"]["status"] != "required":
            raise AssertionError(f"ambiguous unshaped task should still request autonomous adapter: {ambiguous_report}")
        if "no executable guarded patch path" not in ambiguous_report["autonomous_execution_request"]["reason"]:
            raise AssertionError(f"ambiguous unshaped task should explain why autonomous adapter is required: {ambiguous_report}")
        if "return False" not in Path(tmp, "app.py").read_text(encoding="utf-8"):
            raise AssertionError("ambiguous unshaped task should not mutate app.py")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("def total(left, right):\n    return left - right\n", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import total\n\ndef test_total():\n    assert total(2, 3) == 5\n", encoding="utf-8")
        ast_patch_brief = valid_brief()
        ast_patch_brief["repo_path"] = tmp
        ast_patch_brief["task"] += "\nCERAXIA_PATCH:\n" + json.dumps(
            {
                "operations": [
                    {
                        "type": "replace_return_expression",
                        "path": "app.py",
                        "function_name": "total",
                        "old_expression": "left - right",
                        "new_expression": "left + right",
                    }
                ]
            }
        )
        ast_patch_report = code_brigade_adapter.build_worker_report(ast_patch_brief, dry_run=False)
        if ast_patch_report["status"] != "implemented" or ast_patch_report["changed_files"] != ["app.py"]:
            raise AssertionError(f"AST return patch should report implemented changed files: {ast_patch_report}")
        if "return left + right" not in Path(tmp, "app.py").read_text(encoding="utf-8"):
            raise AssertionError("AST return patch did not update app.py")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import app\n\ndef test_app():\n    assert app()\n", encoding="utf-8")
        create_brief = valid_brief()
        create_brief["repo_path"] = tmp
        create_brief["repo_survey_evidence"]["missing_path_hints"] = ["helpers.py"]
        create_brief["task"] += "\nCERAXIA_PATCH:\n" + json.dumps(
            {
                "operations": [
                    {
                        "type": "create_file",
                        "path": "helpers.py",
                        "content": "def helper():\n    return True\n",
                    }
                ]
            }
        )
        create_report = code_brigade_adapter.build_worker_report(create_brief, dry_run=False)
        if create_report["status"] != "implemented" or create_report["changed_files"] != ["helpers.py"]:
            raise AssertionError(f"explicit create_file should report implemented changed file: {create_report}")
        if "helpers.py" not in create_report["execution_result"]["preflight"]["allowed_new_files"]:
            raise AssertionError(f"create_file preflight should expose allowed new files: {create_report}")
        if "def helper" not in Path(tmp, "helpers.py").read_text(encoding="utf-8"):
            raise AssertionError("explicit create_file did not create helpers.py")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import app\n\ndef test_app():\n    assert app()\n", encoding="utf-8")
        inferred_create_brief = valid_brief()
        inferred_create_brief["repo_path"] = tmp
        inferred_create_brief["repo_survey_evidence"]["missing_path_hints"] = ["helpers.py"]
        inferred_create_brief["task"] = "Создай файл `helpers.py` с содержимым `def helper():\n    return True\n`."
        inferred_create_report = code_brigade_adapter.build_worker_report(inferred_create_brief, dry_run=False)
        if inferred_create_report["status"] != "implemented" or inferred_create_report["changed_files"] != ["helpers.py"]:
            raise AssertionError(f"guarded inferred create_file should report implemented changed file: {inferred_create_report}")
        if "natural_language_create_file" not in inferred_create_report["execution_result"]["patch_summary"]:
            raise AssertionError(f"guarded inferred create_file should expose patch source: {inferred_create_report}")
        if "def helper" not in Path(tmp, "helpers.py").read_text(encoding="utf-8"):
            raise AssertionError("guarded inferred create_file did not create helpers.py")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import app\n\ndef test_app():\n    assert app()\n", encoding="utf-8")
        forbidden_inferred_create_brief = valid_brief()
        forbidden_inferred_create_brief["repo_path"] = tmp
        forbidden_inferred_create_brief["task"] = "Создай файл `helpers.py` с содержимым `def helper():\n    return True\n`."
        forbidden_inferred_create_report = code_brigade_adapter.build_worker_report(forbidden_inferred_create_brief, dry_run=False)
        if forbidden_inferred_create_report["status"] != "blocked" or "explicit missing path hint" not in " ".join(forbidden_inferred_create_report["execution_result"]["blockers"]):
            raise AssertionError(f"guarded inferred create_file without missing path hint should block: {forbidden_inferred_create_report}")
        if Path(tmp, "helpers.py").exists():
            raise AssertionError("blocked guarded inferred create_file should not leave helpers.py behind")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import app\n\ndef test_app():\n    assert app()\n", encoding="utf-8")
        forbidden_create_brief = valid_brief()
        forbidden_create_brief["repo_path"] = tmp
        forbidden_create_brief["task"] += "\nCERAXIA_PATCH:\n" + json.dumps(
            {
                "operations": [
                    {
                        "type": "create_file",
                        "path": "helpers.py",
                        "content": "def helper():\n    return True\n",
                    }
                ]
            }
        )
        forbidden_create_report = code_brigade_adapter.build_worker_report(forbidden_create_brief, dry_run=False)
        if forbidden_create_report["status"] != "blocked" or "explicit missing path hint" not in " ".join(forbidden_create_report["execution_result"]["blockers"]):
            raise AssertionError(f"create_file without missing path hint should block: {forbidden_create_report}")
        if Path(tmp, "helpers.py").exists():
            raise AssertionError("blocked create_file should not leave helpers.py behind")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("def app():\n    return False\n", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import app\n\ndef test_app():\n    assert app() is False\n", encoding="utf-8")
        test_edit_brief = valid_brief()
        test_edit_brief["repo_path"] = tmp
        test_edit_brief["task"] += "\nCERAXIA_PATCH:\n" + json.dumps(
            {
                "operations": [
                    {
                        "type": "replace",
                        "path": "test_app.py",
                        "old": "assert app() is False",
                        "new": "assert app() is True",
                    }
                ]
            }
        )
        test_edit_report = code_brigade_adapter.build_worker_report(test_edit_brief, dry_run=False)
        if test_edit_report["status"] != "blocked" or "test files" not in " ".join(test_edit_report["execution_result"]["blockers"]):
            raise AssertionError(f"explicit patch should reject unbudgeted test edits: {test_edit_report}")
        if "assert app() is False" not in Path(tmp, "test_app.py").read_text(encoding="utf-8"):
            raise AssertionError("blocked test edit should leave test_app.py unchanged")
        requested_test_edit_brief = valid_brief()
        requested_test_edit_brief["repo_path"] = tmp
        requested_test_edit_brief["task"] = "Update `test_app.py` self-test to prove docs contract drift is caught.\nCERAXIA_PATCH:\n" + json.dumps(
            {
                "operations": [
                    {
                        "type": "replace",
                        "path": "test_app.py",
                        "old": "assert app() is False",
                        "new": "assert app() is True",
                    }
                ]
            }
        )
        requested_test_edit_report = code_brigade_adapter.build_worker_report(requested_test_edit_brief, dry_run=False)
        if requested_test_edit_report["status"] != "implemented" or requested_test_edit_report["changed_files"] != ["test_app.py"]:
            raise AssertionError(f"explicit requested test edit should be allowed: {requested_test_edit_report}")
        if "assert app() is True" not in Path(tmp, "test_app.py").read_text(encoding="utf-8"):
            raise AssertionError("requested test edit did not update test_app.py")
    with tempfile.TemporaryDirectory() as tmp:
        for name in ["a.py", "b.py", "c.py"]:
            Path(tmp, name).write_text("def value():\n    return 0\n", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("def test_placeholder():\n    assert True\n", encoding="utf-8")
        over_budget_brief = valid_brief()
        over_budget_brief["repo_path"] = tmp
        over_budget_brief["repo_survey_evidence"]["candidate_files"] = ["a.py", "b.py", "c.py"]
        over_budget_brief["execution_forecast"]["scope_budget"]["max_source_files_to_edit"] = 2
        over_budget_brief["task"] += "\nCERAXIA_PATCH:\n" + json.dumps(
            {
                "operations": [
                    {"type": "replace", "path": "a.py", "old": "return 0", "new": "return 1"},
                    {"type": "replace", "path": "b.py", "old": "return 0", "new": "return 1"},
                    {"type": "replace", "path": "c.py", "old": "return 0", "new": "return 1"},
                ]
            }
        )
        over_budget_report = code_brigade_adapter.build_worker_report(over_budget_brief, dry_run=False)
        if over_budget_report["status"] != "blocked" or "scope budget" not in " ".join(over_budget_report["execution_result"]["blockers"]):
            raise AssertionError(f"explicit patch should reject source edits beyond scope budget: {over_budget_report}")
        if any("return 1" in Path(tmp, name).read_text(encoding="utf-8") for name in ["a.py", "b.py", "c.py"]):
            raise AssertionError("over-budget patch should not mutate source files")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("def value():\n    return 0\n", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("def test_placeholder():\n    assert True\n", encoding="utf-8")
        generated_dir = Path(tmp, "generated")
        generated_dir.mkdir()
        bulky = generated_dir / "huge_report.json"
        bulky.write_text("x" * (600 * 1024), encoding="utf-8")
        large_file_brief = valid_brief()
        large_file_brief["repo_path"] = tmp
        large_file_brief["repo_survey_evidence"]["candidate_files"] = ["app.py", "generated/huge_report.json"]
        large_file_brief["task"] += "\nCERAXIA_PATCH:\n" + json.dumps(
            {
                "operations": [
                    {"type": "replace", "path": "generated/huge_report.json", "old": "x", "new": "y"}
                ]
            }
        )
        large_file_report = code_brigade_adapter.build_worker_report(large_file_brief, dry_run=False)
        if large_file_report["status"] != "blocked" or "generated or too large" not in " ".join(large_file_report["execution_result"]["blockers"]):
            raise AssertionError(f"explicit patch should reject generated or large files: {large_file_report}")
        if bulky.read_text(encoding="utf-8") != "x" * (600 * 1024):
            raise AssertionError("blocked large/generated patch should not mutate bulky artifact")
        source_fix_brief = valid_brief()
        source_fix_brief["repo_path"] = tmp
        source_fix_brief["repo_survey_evidence"]["candidate_files"] = ["app.py", "generated/huge_report.json"]
        source_fix_brief["task"] += "\nCERAXIA_PATCH:\n" + json.dumps(
            {
                "operations": [
                    {"type": "replace", "path": "app.py", "old": "return 0", "new": "return 1"}
                ]
            }
        )
        source_fix_report = code_brigade_adapter.build_worker_report(source_fix_brief, dry_run=False)
        if source_fix_report["status"] != "implemented" or source_fix_report["changed_files"] != ["app.py"]:
            raise AssertionError(f"source fix beside generated artifact should stay allowed: {source_fix_report}")
        if "return 1" not in Path(tmp, "app.py").read_text(encoding="utf-8"):
            raise AssertionError("source fix beside generated artifact did not apply")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("def app():\n    return False\n", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import app\n\ndef test_app():\n    assert app()\n", encoding="utf-8")
        rollback_brief = valid_brief()
        rollback_brief["repo_path"] = tmp
        rollback_brief["task"] += "\nCERAXIA_PATCH:\n" + json.dumps(
            {
                "operations": [
                    {"type": "replace", "path": "app.py", "old": "return False", "new": "return True"},
                    {"type": "replace", "path": "app.py", "old": "not present", "new": "broken"},
                ]
            }
        )
        rollback_report = code_brigade_adapter.build_worker_report(rollback_brief, dry_run=False)
        result = rollback_report["execution_result"]
        if rollback_report["status"] != "blocked" or result["status"] != "blocked":
            raise AssertionError(f"failed patch batch should block execution: {rollback_report}")
        if "rolled back" not in result["rollback_notes"] or not result["operation_results"]:
            raise AssertionError(f"failed patch batch should expose rollback evidence: {rollback_report}")
        if result["patch_manifest"]["rollback_performed"] is not True or result["patch_manifest"]["failed_operation_count"] < 1:
            raise AssertionError(f"failed patch batch should expose rollback patch manifest: {rollback_report}")
        rollback_file_rows = {row["path"]: row for row in result["patch_manifest"]["files"]}
        if rollback_file_rows["app.py"]["rollback_touched"] is not True or rollback_file_rows["app.py"]["failed_operation_count"] < 1:
            raise AssertionError(f"failed patch batch should expose per-file rollback state: {rollback_report}")
        if "return False" not in Path(tmp, "app.py").read_text(encoding="utf-8"):
            raise AssertionError("failed patch batch did not roll app.py back")
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import app\n\ndef test_app():\n    assert app()\n", encoding="utf-8")
        preflight_brief = valid_brief()
        preflight_brief["repo_path"] = tmp
        preflight = build_execution_preflight(preflight_brief)
        if not preflight["ok"] or not preflight["repo_exists"] or not preflight["repo_is_dir"]:
            raise AssertionError(f"valid preflight should pass before execution adapter policy blocks mutation: {preflight}")
        if preflight["existing_candidate_file_count"] != 1 or preflight["missing_candidate_files"]:
            raise AssertionError(f"valid preflight should prove candidate files exist: {preflight}")
        if preflight["existing_test_file_count"] != 1 or preflight["missing_test_files"]:
            raise AssertionError(f"valid preflight should prove listed test files exist: {preflight}")
        empty_survey_brief = valid_brief()
        empty_survey_brief["repo_path"] = tmp
        empty_survey_brief["repo_survey_evidence"]["candidate_files"] = []
        blocked_preflight = build_execution_preflight(empty_survey_brief)
        if blocked_preflight["ok"] or not any("candidate files" in item for item in blocked_preflight["blockers"]):
            raise AssertionError(f"preflight should block source mutation without survey candidates: {blocked_preflight}")
        stale_survey_brief = valid_brief()
        stale_survey_brief["repo_path"] = tmp
        stale_survey_brief["repo_survey_evidence"]["candidate_files"] = ["missing.py"]
        stale_preflight = build_execution_preflight(stale_survey_brief)
        if stale_preflight["ok"] or stale_preflight["missing_candidate_files"] != ["missing.py"]:
            raise AssertionError(f"preflight should block stale survey candidate files: {stale_preflight}")
        stale_tests_brief = valid_brief()
        stale_tests_brief["repo_path"] = tmp
        stale_tests_brief["repo_survey_evidence"]["test_files"] = ["missing_test.py"]
        stale_tests_preflight = build_execution_preflight(stale_tests_brief)
        if stale_tests_preflight["ok"] or stale_tests_preflight["missing_test_files"] != ["missing_test.py"]:
            raise AssertionError(f"preflight should block stale survey test files: {stale_tests_preflight}")
        unsafe_candidate_brief = valid_brief()
        unsafe_candidate_brief["repo_path"] = tmp
        unsafe_candidate_brief["repo_survey_evidence"]["candidate_files"] = ["../outside.py"]
        unsafe_candidate_preflight = build_execution_preflight(unsafe_candidate_brief)
        if unsafe_candidate_preflight["ok"] or unsafe_candidate_preflight["unsafe_candidate_files"] != ["../outside.py"]:
            raise AssertionError(f"preflight should block unsafe candidate paths: {unsafe_candidate_preflight}")
        unsafe_test_brief = valid_brief()
        unsafe_test_brief["repo_path"] = tmp
        unsafe_test_brief["repo_survey_evidence"]["test_files"] = ["/tmp/test_app.py"]
        unsafe_test_preflight = build_execution_preflight(unsafe_test_brief)
        if unsafe_test_preflight["ok"] or unsafe_test_preflight["unsafe_test_files"] != ["/tmp/test_app.py"]:
            raise AssertionError(f"preflight should block unsafe test paths: {unsafe_test_preflight}")
        Path(tmp, "linked_app.py").symlink_to(Path(tmp, "app.py"))
        symlink_candidate_brief = valid_brief()
        symlink_candidate_brief["repo_path"] = tmp
        symlink_candidate_brief["repo_survey_evidence"]["candidate_files"] = ["linked_app.py"]
        symlink_candidate_preflight = build_execution_preflight(symlink_candidate_brief)
        if symlink_candidate_preflight["ok"] or symlink_candidate_preflight["symlink_candidate_files"] != ["linked_app.py"]:
            raise AssertionError(f"preflight should block symlink candidate paths: {symlink_candidate_preflight}")
        Path(tmp, "linked_test_app.py").symlink_to(Path(tmp, "test_app.py"))
        symlink_test_brief = valid_brief()
        symlink_test_brief["repo_path"] = tmp
        symlink_test_brief["repo_survey_evidence"]["test_files"] = ["linked_test_app.py"]
        symlink_test_preflight = build_execution_preflight(symlink_test_brief)
        if symlink_test_preflight["ok"] or symlink_test_preflight["symlink_test_files"] != ["linked_test_app.py"]:
            raise AssertionError(f"preflight should block symlink test paths: {symlink_test_preflight}")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "init"], cwd=tmp, text=True, capture_output=True, check=True)
        Path(tmp, "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
        Path(tmp, "test_app.py").write_text("from app import app\n\ndef test_app():\n    assert app()\n", encoding="utf-8")
        subprocess.run(["git", "add", "app.py", "test_app.py"], cwd=tmp, text=True, capture_output=True, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "baseline"],
            cwd=tmp,
            text=True,
            capture_output=True,
            check=True,
        )
        Path(tmp, "notes.md").write_text("user draft\n", encoding="utf-8")
        dirty_brief = valid_brief()
        dirty_brief["repo_path"] = tmp
        dirty_preflight = build_execution_preflight(dirty_brief)
        if not dirty_preflight["ok"] or dirty_preflight["dirty_worktree"]["paths"] != ["notes.md"]:
            raise AssertionError(f"preflight should record unrelated dirty files without blocking: {dirty_preflight}")
        Path(tmp, "app.py").write_text("def app():\n    return False\n", encoding="utf-8")
        dirty_target_preflight = build_execution_preflight(dirty_brief)
        if dirty_target_preflight["ok"] or dirty_target_preflight["dirty_worktree"]["target_conflicts"] != ["app.py"]:
            raise AssertionError(f"preflight should block dirty mutation targets: {dirty_target_preflight}")
    invalid = valid_brief()
    invalid.pop("allowed_scope")
    invalid_report = code_brigade_adapter.build_worker_report(invalid, dry_run=True)
    if invalid_report["status"] != "blocked" or invalid_report["implementation_brief_acknowledged"]:
        raise AssertionError(f"invalid brief should be blocked: {invalid_report}")
    if not invalid_report["work_package_statuses"] or any(item["status"] != "blocked" for item in invalid_report["work_package_statuses"]):
        raise AssertionError(f"invalid brief should mark work packages blocked: {invalid_report}")
    weak_planning = valid_brief()
    weak_planning["acceptance_contract"] = {"must_prove": []}
    weak_planning_report = code_brigade_adapter.build_worker_report(weak_planning, dry_run=True)
    if weak_planning_report["status"] != "blocked" or not any("acceptance_contract" in item for item in weak_planning_report["validation_problems"]):
        raise AssertionError(f"weak planning evidence should be blocked: {weak_planning_report}")
    missing_breakdown = valid_brief()
    missing_breakdown["work_breakdown"] = {"phases": []}
    missing_breakdown_report = code_brigade_adapter.build_worker_report(missing_breakdown, dry_run=True)
    if missing_breakdown_report["status"] != "blocked" or not any("work_breakdown" in item for item in missing_breakdown_report["validation_problems"]):
        raise AssertionError(f"missing work breakdown should be blocked: {missing_breakdown_report}")
    missing_impact = valid_brief()
    missing_impact["impact_analysis"] = {"surfaces": []}
    missing_impact_report = code_brigade_adapter.build_worker_report(missing_impact, dry_run=True)
    if missing_impact_report["status"] != "blocked" or not any("impact_analysis" in item for item in missing_impact_report["validation_problems"]):
        raise AssertionError(f"missing impact analysis should be blocked: {missing_impact_report}")
    missing_forecast = valid_brief()
    missing_forecast["execution_forecast"] = {"complexity": "broken"}
    missing_forecast_report = code_brigade_adapter.build_worker_report(missing_forecast, dry_run=True)
    if missing_forecast_report["status"] != "blocked" or not any("execution_forecast" in item for item in missing_forecast_report["validation_problems"]):
        raise AssertionError(f"missing execution forecast should be blocked: {missing_forecast_report}")
    weak_scope_budget = valid_brief()
    weak_scope_budget["execution_forecast"]["scope_budget"] = {
        "max_source_files_to_edit": 4,
        "max_test_files_to_edit_without_explicit_user_request": 1,
        "requires_ceraxia_replan_when": [],
    }
    weak_scope_budget_report = code_brigade_adapter.build_worker_report(weak_scope_budget, dry_run=True)
    if weak_scope_budget_report["status"] != "blocked" or not any("scope_budget" in item for item in weak_scope_budget_report["validation_problems"]):
        raise AssertionError(f"weak scope budget should be blocked: {weak_scope_budget_report}")
    missing_work_packages = valid_brief()
    missing_work_packages["implementation_work_packages"] = {"packages": []}
    missing_work_packages_report = code_brigade_adapter.build_worker_report(missing_work_packages, dry_run=True)
    if missing_work_packages_report["status"] != "blocked" or not any("implementation_work_packages" in item for item in missing_work_packages_report["validation_problems"]):
        raise AssertionError(f"missing implementation work packages should be blocked: {missing_work_packages_report}")
    missing_blocking_policy = valid_brief()
    missing_blocking_policy["implementation_work_packages"]["packages"][0]["blocking_policy"] = []
    missing_blocking_policy_report = code_brigade_adapter.build_worker_report(missing_blocking_policy, dry_run=True)
    if missing_blocking_policy_report["status"] != "blocked" or not any("blocking_policy" in item for item in missing_blocking_policy_report["validation_problems"]):
        raise AssertionError(f"missing package blocking policy should be blocked: {missing_blocking_policy_report}")
    missing_package_graph = valid_brief()
    missing_package_graph["implementation_work_packages"].pop("package_dependency_graph")
    missing_package_graph_report = code_brigade_adapter.build_worker_report(missing_package_graph, dry_run=True)
    if missing_package_graph_report["status"] != "blocked" or not any("package_dependency_graph" in item for item in missing_package_graph_report["validation_problems"]):
        raise AssertionError(f"missing package dependency graph should be blocked: {missing_package_graph_report}")
    mismatched_handoff_graph = valid_brief()
    mismatched_handoff_graph["code_brigade_handoff"]["package_dependency_graph"] = {"rows": [], "complete": False}
    mismatched_handoff_graph_report = code_brigade_adapter.build_worker_report(mismatched_handoff_graph, dry_run=True)
    if mismatched_handoff_graph_report["status"] != "blocked" or not any("code_brigade_handoff package_dependency_graph" in item for item in mismatched_handoff_graph_report["validation_problems"]):
        raise AssertionError(f"mismatched handoff package dependency graph should be blocked: {mismatched_handoff_graph_report}")
    uncovered_surface = valid_brief()
    uncovered_surface["implementation_work_packages"]["packages"][0]["impact_surfaces"] = ["source_behavior"]
    uncovered_surface["implementation_work_packages"]["packages"][2]["impact_surfaces"] = ["source_behavior"]
    uncovered_surface_report = code_brigade_adapter.build_worker_report(uncovered_surface, dry_run=True)
    if uncovered_surface_report["status"] != "blocked" or not any("cover every planned surface" in item for item in uncovered_surface_report["validation_problems"]):
        raise AssertionError(f"uncovered surface should be blocked: {uncovered_surface_report}")
    missing_evidence = valid_brief()
    missing_evidence.pop("repo_survey_evidence")
    missing_evidence_report = code_brigade_adapter.build_worker_report(missing_evidence, dry_run=True)
    if missing_evidence_report["status"] != "blocked" or not any("repo_survey_evidence" in item for item in missing_evidence_report["validation_problems"]):
        raise AssertionError(f"missing repo survey evidence should be blocked: {missing_evidence_report}")
    incomplete_surface_matrix = valid_brief()
    incomplete_surface_matrix["surface_verification_matrix"] = {"rows": [{"surface": "source_behavior"}], "complete": False}
    incomplete_surface_matrix_report = code_brigade_adapter.build_worker_report(incomplete_surface_matrix, dry_run=True)
    if incomplete_surface_matrix_report["status"] != "blocked" or not any("surface_verification_matrix" in item for item in incomplete_surface_matrix_report["validation_problems"]):
        raise AssertionError(f"incomplete surface verification matrix should be blocked: {incomplete_surface_matrix_report}")
    missing_surface_package_matrix = valid_brief()
    missing_surface_package_matrix["surface_package_matrix"] = {"rows": [], "complete": True, "blockers": []}
    missing_surface_package_report = code_brigade_adapter.build_worker_report(missing_surface_package_matrix, dry_run=True)
    if missing_surface_package_report["status"] != "blocked" or not any("surface_package_matrix" in item for item in missing_surface_package_report["validation_problems"]):
        raise AssertionError(f"missing surface package matrix should be blocked: {missing_surface_package_report}")
    blocked_survey_quality = valid_brief()
    blocked_survey_quality["survey_quality_gate"] = {"decision": "blocked", "blockers": ["missing path"]}
    blocked_survey_quality_report = code_brigade_adapter.build_worker_report(blocked_survey_quality, dry_run=True)
    if blocked_survey_quality_report["status"] != "blocked" or not any("survey_quality_gate" in item for item in blocked_survey_quality_report["validation_problems"]):
        raise AssertionError(f"blocked survey quality should block CodeBrigade: {blocked_survey_quality_report}")
    blocked_review = valid_brief()
    blocked_review["planning_review_gate"] = {"decision": "blocked", "score": 20, "blockers": ["unclear task"]}
    blocked_review_report = code_brigade_adapter.build_worker_report(blocked_review, dry_run=True)
    if blocked_review_report["status"] != "blocked" or not any("planning_review_gate" in item for item in blocked_review_report["validation_problems"]):
        raise AssertionError(f"blocked planning review should block CodeBrigade: {blocked_review_report}")
    missing_handoff = valid_brief()
    missing_handoff["code_brigade_handoff"] = {"target": "CodeBrigade", "steps": []}
    missing_handoff_report = code_brigade_adapter.build_worker_report(missing_handoff, dry_run=True)
    if missing_handoff_report["status"] != "blocked" or not any("handoff steps" in item for item in missing_handoff_report["validation_problems"]):
        raise AssertionError(f"missing handoff steps should be blocked: {missing_handoff_report}")
    print("[ok] Ceraxia CodeBrigade adapter")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
