#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "InnerCircle" / "Ceraxia" / "field_trials.json"
RUNNER = ROOT / "ceraxia_field_trial_runner.py"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def expert_trial_ids() -> list[str]:
    spec = load_json(SPEC)
    return [
        str(item.get("id"))
        for item in spec.get("trials", [])
        if isinstance(item, dict) and item.get("difficulty") == "expert" and item.get("id")
    ]


def run_trial(trial_id: str, timeout_sec: int, run_root: Path | None, ledger_draft: bool) -> dict[str, Any]:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    command = [sys.executable, str(RUNNER), "--trial", trial_id]
    if run_root is not None:
        command.extend(["--run-root", str(run_root)])
    if ledger_draft:
        command.append("--ledger-draft")
    completed = subprocess.run(
        command,
        cwd=str(ROOT.parent),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_sec,
    )
    if completed.returncode != 0:
        return {
            "trial_id": trial_id,
            "status": "runner_failed",
            "expected": False,
            "runner_returncode": completed.returncode,
            "stderr": completed.stderr[-2000:],
        }
    payload = json.loads(completed.stdout)
    checks = {
        name: value.get("passed")
        for name, value in payload.get("trial_checks", {}).items()
        if isinstance(value, dict)
    }
    return {
        "trial_id": trial_id,
        "run_id": payload.get("run_id", ""),
        "trial_root": payload.get("trial_root", ""),
        "status": payload.get("trial_outcome", {}).get("status", ""),
        "expected": payload.get("trial_outcome", {}).get("expected", False),
        "reason": payload.get("trial_outcome", {}).get("reason", ""),
        "final_manifest": payload.get("final_manifest", ""),
        "patch_source": payload.get("manifest_summary", {}).get("patch_source", ""),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run every Ceraxia expert field trial fixture.")
    parser.add_argument("--require-all", action="store_true", help="Exit non-zero unless every expert trial passes.")
    parser.add_argument("--run-root", type=Path, default=None, help="Directory for preserved trial runs.")
    parser.add_argument("--ledger-draft", action="store_true", help="Preserve runs and append draft ledger entries for review.")
    parser.add_argument("--timeout-sec", type=int, default=180, help="Per-trial timeout.")
    args = parser.parse_args()
    run_root = args.run_root
    if args.ledger_draft and run_root is None:
        run_root = ROOT / "field_trial_runs"
    if run_root is not None:
        run_root.mkdir(parents=True, exist_ok=True)
    results = [run_trial(trial_id, args.timeout_sec, run_root, args.ledger_draft) for trial_id in expert_trial_ids()]
    passed = [item for item in results if item.get("status") == "passed" and item.get("expected") is True]
    unshaped = [
        item
        for item in results
        if str(item.get("patch_source") or "").startswith("test_inferred_")
    ]
    report = {
        "expert_trial_count": len(results),
        "passed_count": len(passed),
        "unshaped_inferred_count": len(unshaped),
        "all_passed": len(passed) == len(results),
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.require_all and not report["all_passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
