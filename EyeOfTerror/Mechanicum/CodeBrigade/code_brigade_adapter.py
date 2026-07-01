#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


CONTRACT_VERSION = "eye-mechanicum.v1"


def validate_implementation_brief(brief: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if brief.get("kind") != "ceraxia_code_brigade_implementation_brief":
        problems.append("brief kind must be ceraxia_code_brigade_implementation_brief")
    if brief.get("contract_version") != CONTRACT_VERSION:
        problems.append("brief contract_version is unsupported")
    if brief.get("target") != "CodeBrigade":
        problems.append("brief target must be CodeBrigade")
    if not isinstance(brief.get("task"), str) or not brief.get("task"):
        problems.append("brief task is required")
    if not isinstance(brief.get("allowed_scope"), list) or not brief.get("allowed_scope"):
        problems.append("brief allowed_scope is required")
    if not isinstance(brief.get("forbidden_approaches"), list) or not brief.get("forbidden_approaches"):
        problems.append("brief forbidden_approaches is required")
    if not isinstance(brief.get("required_verification"), dict):
        problems.append("brief required_verification is required")
    if not isinstance(brief.get("acceptance_gates"), list) or not brief.get("acceptance_gates"):
        problems.append("brief acceptance_gates are required")
    handoff = brief.get("code_brigade_handoff") if isinstance(brief.get("code_brigade_handoff"), dict) else {}
    if handoff.get("target") != "CodeBrigade":
        problems.append("brief code_brigade_handoff must target CodeBrigade")
    steps = handoff.get("steps") if isinstance(handoff.get("steps"), list) else []
    if not steps:
        problems.append("brief code_brigade_handoff steps are required")
    elif not all(isinstance(step, dict) and step.get("step") and step.get("owner") for step in steps):
        problems.append("brief code_brigade_handoff steps must include step and owner")
    return problems


def build_worker_report(brief: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    validation_problems = validate_implementation_brief(brief)
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
        status = "blocked"
        notes.append("real CodeBrigade execution adapter is not configured")
    return {
        "kind": "ceraxia_code_brigade_worker_report",
        "contract_version": CONTRACT_VERSION,
        "target": "CodeBrigade",
        "status": status,
        "dry_run": dry_run,
        "changed_files": changed_files,
        "notes": notes,
        "implementation_brief_acknowledged": not validation_problems,
        "validation_problems": validation_problems,
        "adapter": "EyeOfTerror/Mechanicum/CodeBrigade/code_brigade_adapter.py",
    }


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
