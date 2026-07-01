#!/usr/bin/env python3
from __future__ import annotations

import code_brigade_adapter


def valid_brief() -> dict:
    return {
        "kind": "ceraxia_code_brigade_implementation_brief",
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
        "blocked": False,
        "blockers": [],
    }


def main() -> int:
    dry_report = code_brigade_adapter.build_worker_report(valid_brief(), dry_run=True)
    if dry_report["status"] != "dry_run_handoff_ready" or not dry_report["implementation_brief_acknowledged"]:
        raise AssertionError(f"valid dry-run brief should be accepted: {dry_report}")
    execute_report = code_brigade_adapter.build_worker_report(valid_brief(), dry_run=False)
    if execute_report["status"] != "blocked" or "not configured" not in " ".join(execute_report["notes"]):
        raise AssertionError(f"real execution should be honestly blocked until adapter is wired: {execute_report}")
    invalid = valid_brief()
    invalid.pop("allowed_scope")
    invalid_report = code_brigade_adapter.build_worker_report(invalid, dry_run=True)
    if invalid_report["status"] != "blocked" or invalid_report["implementation_brief_acknowledged"]:
        raise AssertionError(f"invalid brief should be blocked: {invalid_report}")
    print("[ok] Ceraxia CodeBrigade adapter")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
