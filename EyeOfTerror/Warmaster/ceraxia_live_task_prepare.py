#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from ceraxia_field_trial_report import load_json


WARMASTER_ROOT = Path(__file__).resolve().parent
EYE_ROOT = WARMASTER_ROOT.parent
REPO_ROOT = EYE_ROOT.parent
SPEC = EYE_ROOT / "Mechanicum" / "Ceraxia" / "field_trials.json"


def live_tasks(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in spec.get("live_tasks", []) if isinstance(item, dict)]


def find_live_task(spec: dict[str, Any], task_id: str) -> dict[str, Any]:
    for task in live_tasks(spec):
        if task.get("id") == task_id:
            return task
    raise ValueError(f"unknown live task id: {task_id}")


def build_task_packet(task: dict[str, Any], run_id: str, repo_root: Path) -> dict[str, Any]:
    return {
        "kind": "ceraxia_live_task_packet",
        "contract_version": 1,
        "task_id": task["id"],
        "run_id": run_id,
        "task_class": task["class"],
        "difficulty": task["difficulty"],
        "repo_root": str(repo_root),
        "task": task["task"],
        "required_evidence": task["required_evidence"],
        "minimum_changed_files": task["minimum_changed_files"],
        "multi_file_expected": task["multi_file_expected"],
        "artifact_contract": {
            "repo_investigation": "repo_investigation.json",
            "planning": "planning_department.json",
            "execution": "execution_result.json or patch_manifest.json",
            "verification": "verification_report.json",
            "review": "review_gate.json",
            "next_stage_package": "ceraxia_next_stage_evidence_package.json",
        },
        "acceptance": {
            "must_use_real_repo_task": True,
            "fixture_only_must_be_false": True,
            "false_success_must_be_false": True,
            "register_with": "EyeOfTerror/Warmaster/ceraxia_live_task_register.py",
            "package_builder": "EyeOfTerror/Warmaster/ceraxia_next_stage_package.py",
        },
        "operating_rules": [
            "Perform multi-pass repo investigation before mutation.",
            "Record planning alternatives, risks, rollback, impact map, and test strategy.",
            "Write a patch manifest for every changed file.",
            "Run focused verification and the broadest safe regression command.",
            "Run reviewer gate before claiming success.",
            "If blocked or failed, write a concrete postmortem instead of claiming completion.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a Ceraxia live task packet from the live task catalog.")
    parser.add_argument("--list", action="store_true", help="List available live task ids.")
    parser.add_argument("--task-id", help="Live task id from field_trials.json.")
    parser.add_argument("--run-id", default="", help="Optional stable run id.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT, help="Repository root for the live task.")
    args = parser.parse_args()

    spec = load_json(SPEC)
    if args.list:
        print(json.dumps({"live_tasks": [task["id"] for task in live_tasks(spec)]}, ensure_ascii=False, indent=2))
        return 0
    if not args.task_id:
        parser.error("--task-id is required unless --list is used")
    task = find_live_task(spec, args.task_id)
    run_id = args.run_id or f"{args.task_id}-{time.strftime('%Y%m%d-%H%M%S')}"
    packet = build_task_packet(task, run_id, args.repo_root.resolve())
    print(json.dumps(packet, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
