#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import code_brigade_adapter


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
