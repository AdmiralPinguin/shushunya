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
        "acceptance_gates": ["planning packet includes all five planning roles"],
        "repo_survey_evidence": {
            "candidate_files": ["app.py"],
            "test_files": ["test_app.py"],
            "entrypoint_candidates": ["main.py"],
            "local_import_edges": [{"source": "app.py", "import": "util.enabled", "target": "util.py"}],
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
    if policy["real_execution_status"] != "blocked_until_adapter_is_wired":
        raise AssertionError(f"execution policy must stay honest until real adapter exists: {policy}")
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
    if "python -m pytest test_app.py" not in plan["verification_commands"]:
        raise AssertionError(f"implementation plan should include suggested verification: {plan}")
    if plan["dependency_edges_to_check"] != [{"source": "app.py", "import": "util.enabled", "target": "util.py"}]:
        raise AssertionError(f"implementation plan should preserve local dependency edges: {plan}")
    if plan["survey_truncated"]:
        raise AssertionError(f"small survey fixture should not be marked truncated: {plan}")
    if plan["python_symbols_truncated"]:
        raise AssertionError(f"small survey fixture should not have truncated python symbols: {plan}")
    if not plan["refusal_conditions"]:
        raise AssertionError(f"implementation plan should include refusal conditions: {plan}")
    execute_report = code_brigade_adapter.build_worker_report(valid_brief(), dry_run=False)
    if execute_report["status"] != "blocked" or "not configured" not in " ".join(execute_report["notes"]):
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
    missing_handoff = valid_brief()
    missing_handoff["code_brigade_handoff"] = {"target": "CodeBrigade", "steps": []}
    missing_handoff_report = code_brigade_adapter.build_worker_report(missing_handoff, dry_run=True)
    if missing_handoff_report["status"] != "blocked" or not any("handoff steps" in item for item in missing_handoff_report["validation_problems"]):
        raise AssertionError(f"missing handoff steps should be blocked: {missing_handoff_report}")
    print("[ok] Ceraxia CodeBrigade adapter")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
