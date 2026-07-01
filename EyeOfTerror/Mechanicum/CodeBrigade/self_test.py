#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import code_brigade_adapter
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
                    "blockers": [],
                },
                {
                    "surface": "test_surface",
                    "risk": "medium",
                    "evidence_needed": ["existing tests"],
                    "covered_by": ["rerun failing test command"],
                    "blockers": [],
                }
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
        "implementation_brief_blueprint": {
            "target": "CodeBrigade",
            "mutation_preconditions": [
                "implementation brief validates",
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
            "escalation_triggers": ["verification fails twice on the same behavior"],
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
            "survey_truncated": False,
            "python_symbols_truncated": False,
        },
        "suggested_verification_commands": ["python -m pytest test_app.py"],
        "code_brigade_handoff": {
            "target": "CodeBrigade",
            "steps": [
                {"step": "inspect_repo_evidence", "owner": "CodeBrigade"},
                {"step": "return_for_ceraxia_review", "owner": "Ceraxia"},
            ],
        },
        "blocked": False,
        "blockers": [],
    }


def main() -> int:
    policy = json.loads((Path(__file__).resolve().parent / "execution_policy.json").read_text(encoding="utf-8"))
    if policy["real_execution_status"] != "explicit_patch_adapter_only":
        raise AssertionError(f"execution policy must stay honest about narrow explicit patch execution: {policy}")
    if "implementation_brief validates against the CodeBrigade contract" not in policy["mutation_preconditions"]:
        raise AssertionError(f"execution policy must require brief validation before mutation: {policy}")
    if "execution preflight passes before source mutation" not in policy["mutation_preconditions"]:
        raise AssertionError(f"execution policy must require preflight before mutation: {policy}")
    dry_report = code_brigade_adapter.build_worker_report(valid_brief(), dry_run=True)
    if dry_report["status"] != "dry_run_handoff_ready" or not dry_report["implementation_brief_acknowledged"]:
        raise AssertionError(f"valid dry-run brief should be accepted: {dry_report}")
    if dry_report["contract_version"] != "eye-mechanicum.v1":
        raise AssertionError(f"worker report contract version drifted: {dry_report}")
    if dry_report["execution_policy_status"] != "blocked_until_adapter_is_wired":
        raise AssertionError(f"dry-run worker report must expose blocked execution policy: {dry_report}")
    plan = dry_report["implementation_plan"]
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
    if "final report answers the original task rather than only package-local success" not in plan["work_package_handoff_criteria"]:
        raise AssertionError(f"implementation plan should preserve global handoff criteria: {plan}")
    if "the original user-visible request is satisfied" not in plan["acceptance_evidence_required"]:
        raise AssertionError(f"implementation plan should preserve acceptance evidence: {plan}")
    if not plan["surface_verification_complete"] or plan["surface_verification_rows"][0]["surface"] != "source_behavior":
        raise AssertionError(f"implementation plan should preserve surface verification matrix: {plan}")
    if plan["survey_quality_decision"] != "passed":
        raise AssertionError(f"implementation plan should preserve survey quality gate: {plan}")
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
        patch_report = code_brigade_adapter.build_worker_report(patch_brief, dry_run=False)
        if patch_report["status"] != "implemented" or patch_report["changed_files"] != ["app.py"]:
            raise AssertionError(f"explicit patch execution should report implemented changed files: {patch_report}")
        if patch_report["execution_result"]["status"] != "implemented":
            raise AssertionError(f"explicit patch execution result should be implemented: {patch_report}")
        if "return True" not in Path(tmp, "app.py").read_text(encoding="utf-8"):
            raise AssertionError("explicit patch execution did not update app.py")
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
    invalid = valid_brief()
    invalid.pop("allowed_scope")
    invalid_report = code_brigade_adapter.build_worker_report(invalid, dry_run=True)
    if invalid_report["status"] != "blocked" or invalid_report["implementation_brief_acknowledged"]:
        raise AssertionError(f"invalid brief should be blocked: {invalid_report}")
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
    missing_work_packages = valid_brief()
    missing_work_packages["implementation_work_packages"] = {"packages": []}
    missing_work_packages_report = code_brigade_adapter.build_worker_report(missing_work_packages, dry_run=True)
    if missing_work_packages_report["status"] != "blocked" or not any("implementation_work_packages" in item for item in missing_work_packages_report["validation_problems"]):
        raise AssertionError(f"missing implementation work packages should be blocked: {missing_work_packages_report}")
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
