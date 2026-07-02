#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ceraxia_evidence_contract import NEXT_STAGE_PACKAGE_KIND, next_stage_evidence_status
from ceraxia_field_trial_report import load_json
from ceraxia_live_task_prepare import build_task_packet, find_live_task
from ceraxia_live_task_register import register_live_task, write_json


WARMASTER_ROOT = Path(__file__).resolve().parent
EYE_ROOT = WARMASTER_ROOT.parent
REPO_ROOT = EYE_ROOT.parent
CERAXIA = EYE_ROOT / "Mechanicum" / "Ceraxia" / "ceraxia.py"
SPEC = EYE_ROOT / "Mechanicum" / "Ceraxia" / "field_trials.json"
LEDGER = EYE_ROOT / "Mechanicum" / "Ceraxia" / "field_trial_ledger.json"


def load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def repo_path_text(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path)


def changed_files_from_worker(run_dir: Path) -> list[str]:
    worker = load_optional_json(run_dir / "worker_report.json")
    values = worker.get("changed_files") if isinstance(worker.get("changed_files"), list) else []
    return [str(item) for item in values if isinstance(item, str) and item]


def status_from_run(task: dict[str, Any], result: dict[str, Any], run_dir: Path, changed_files: list[str]) -> str:
    review = load_optional_json(run_dir / "review_gate.json")
    verification = load_optional_json(run_dir / "verification_report.json")
    minimum_changed = int(task.get("minimum_changed_files") or 0)
    review_passed = review.get("decision") == "passed"
    verification_passed = verification.get("status") == "passed"
    if result.get("package_ok") is True and changed_files and review_passed and verification_passed:
        return "fully_successful"
    if result.get("package_ok") is True and minimum_changed == 0 and not changed_files:
        return "honest_blocked"
    return "failed"


def build_live_task_prompt(packet: dict[str, Any]) -> str:
    return (
        "CERAXIA_LIVE_TASK_PACKET:\n"
        f"{json.dumps(packet, ensure_ascii=False, indent=2)}\n\n"
        "Execute this as a live Ceraxia engineering task. Preserve evidence for every required artifact. "
        "Do not claim benchmark success unless the package can be registered through the live task registrar."
    )


def run_ceraxia_for_task(task: dict[str, Any], run_id: str, repo_root: Path, runs_root: Path, mode: str) -> dict[str, Any]:
    packet = build_task_packet(task, run_id, repo_root)
    command = [
        sys.executable,
        str(CERAXIA),
        "--task",
        build_live_task_prompt(packet),
        "--repo-path",
        str(repo_root),
        "--runs-root",
        str(runs_root),
        "--mode",
        mode,
    ]
    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip()) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Ceraxia did not return a JSON object")
    payload["ceraxia_returncode"] = completed.returncode
    return payload


def build_package(task: dict[str, Any], result: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    changed_files = changed_files_from_worker(run_dir)
    status = status_from_run(task, result, run_dir, changed_files)
    verification = load_optional_json(run_dir / "verification_report.json")
    review = load_optional_json(run_dir / "review_gate.json")
    summary = load_optional_json(run_dir / "run_summary.json")
    postmortem = ""
    if status != "fully_successful":
        postmortem = (
            f"Ceraxia live task ended as {status}; package_ok={result.get('package_ok')}; "
            f"review={review.get('decision', '')}; verification={verification.get('status', '')}; "
            f"next_action={result.get('next_action', '')}."
        )
    return {
        "kind": NEXT_STAGE_PACKAGE_KIND,
        "contract_version": 1,
        "trial_id": task["id"],
        "run_id": result["run_id"],
        "task_class": task["class"],
        "status": status,
        "attempt_count": 1,
        "real_repo_task": True,
        "fixture_only": False,
        "false_success": False,
        "multi_file_nonfixture": len(set(changed_files)) > 1,
        "changed_files": changed_files,
        "verification_passed": verification.get("status") == "passed",
        "review_accepted": review.get("decision") == "passed",
        "postmortem": postmortem,
        "summary": {
            "ceraxia_package_ok": result.get("package_ok"),
            "ceraxia_execution_mode": result.get("execution_mode"),
            "ready_for_execution": result.get("ready_for_execution"),
            "run_summary_status": summary.get("kind", ""),
        },
        "artifacts": {
            "repo_investigation": repo_path_text(run_dir / "repo_survey.json"),
            "planning": repo_path_text(run_dir / "planning_department.json"),
            "execution": repo_path_text(run_dir / "worker_report.json"),
            "verification": repo_path_text(run_dir / "verification_report.json"),
            "review": repo_path_text(run_dir / "review_gate.json"),
        },
    }


def export_evidence_bundle(package: dict[str, Any], run_dir: Path, evidence_root: Path) -> tuple[dict[str, Any], Path]:
    bundle_root = evidence_root / str(package["run_id"])
    bundle_root.mkdir(parents=True, exist_ok=True)
    exported = json.loads(json.dumps(package))
    artifact_targets = {
        "repo_investigation": "repo_survey.json",
        "planning": "planning_department.json",
        "execution": "worker_report.json",
        "verification": "verification_report.json",
        "review": "review_gate.json",
    }
    artifacts: dict[str, str] = {}
    for artifact_name, file_name in artifact_targets.items():
        source = run_dir / file_name
        target = bundle_root / file_name
        if source.exists():
            shutil.copy2(source, target)
        artifacts[artifact_name] = repo_path_text(target)
    exported["artifacts"] = artifacts
    package_path = bundle_root / "next_stage_evidence_package.json"
    write_json(package_path, exported)
    return exported, package_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Ceraxia live task and build a next-stage evidence package.")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--runs-root", type=Path, default=WARMASTER_ROOT / "runs" / "live_task_runs")
    parser.add_argument("--evidence-root", type=Path, default=None, help="Optional persistent root for copied live evidence bundles.")
    parser.add_argument("--mode", choices=["dry_run", "guarded_patch", "repo_engineer", "review_only"], default="dry_run")
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--register", action="store_true", help="Append a draft live entry to the ledger.")
    parser.add_argument("--accept-for-next-stage", action="store_true", help="Register and count the entry toward the live benchmark.")
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    spec = load_json(SPEC)
    task = find_live_task(spec, args.task_id)
    run_id = args.run_id or args.task_id
    result = run_ceraxia_for_task(task, run_id, args.repo_root.resolve(), args.runs_root, args.mode)
    run_dir = Path(str(result["run_dir"]))
    package = build_package(task, result, run_dir)
    package_path = run_dir / "next_stage_evidence_package.json"
    write_json(package_path, package)
    if args.evidence_root:
        package, package_path = export_evidence_bundle(package, run_dir, args.evidence_root.resolve())
    entry = {
        "trial_id": task["id"],
        "run_id": result["run_id"],
        "human_review_notes": package.get("postmortem", ""),
        "next_stage": {
            "status": package["status"],
            "attempt_count": package["attempt_count"],
            "class": package["task_class"],
            "multi_file_nonfixture": package["multi_file_nonfixture"],
            "false_success": package["false_success"],
            "postmortem": package["postmortem"],
            "evidence_package": repo_path_text(package_path),
        },
    }
    evidence_status = next_stage_evidence_status(REPO_ROOT, entry, task)
    registered_entry: dict[str, Any] | None = None
    if args.register or args.accept_for_next_stage:
        ledger = load_json(args.ledger)
        registered_entry = register_live_task(
            spec,
            ledger,
            task["id"],
            package_path,
            reviewer=args.reviewer,
            notes=args.notes or str(package.get("postmortem") or "Ceraxia live task package reviewed for next-stage evidence."),
            accepted_for_next_stage=args.accept_for_next_stage,
        )
        write_json(args.ledger, ledger)
    payload = {
        "ok": evidence_status.get("passed") is True,
        "task_id": task["id"],
        "run_id": result["run_id"],
        "run_dir": str(run_dir),
        "package_path": str(package_path),
        "package": package,
        "evidence_status": evidence_status,
        "registered_entry": registered_entry,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
