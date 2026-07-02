#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from ceraxia_evidence_contract import load_json_object, next_stage_evidence_status
from ceraxia_field_trial_report import build_report, load_json, validate_ledger


WARMASTER_ROOT = Path(__file__).resolve().parent
EYE_ROOT = WARMASTER_ROOT.parent
REPO_ROOT = EYE_ROOT.parent
SPEC = EYE_ROOT / "Mechanicum" / "Ceraxia" / "field_trials.json"
LEDGER = EYE_ROOT / "Mechanicum" / "Ceraxia" / "field_trial_ledger.json"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def repo_path_text(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path)


def live_task_by_id(spec: dict[str, Any], task_id: str) -> dict[str, Any]:
    for item in spec.get("live_tasks", []):
        if isinstance(item, dict) and item.get("id") == task_id:
            return item
    raise ValueError(f"unknown live task id: {task_id}")


def empty_scores(spec: dict[str, Any]) -> dict[str, None]:
    return {str(dimension): None for dimension in spec.get("dimensions", [])}


def build_next_stage_from_package(task: dict[str, Any], package_path: Path, package: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": str(package.get("status") or ""),
        "attempt_count": int(package.get("attempt_count") or 0),
        "class": str(package.get("task_class") or task.get("class") or ""),
        "multi_file_nonfixture": package.get("multi_file_nonfixture") is True,
        "false_success": package.get("false_success") is True,
        "postmortem": str(package.get("postmortem") or ""),
        "evidence_package": repo_path_text(package_path),
    }


def validate_live_task_fit(task: dict[str, Any], package: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    changed_files = package.get("changed_files") if isinstance(package.get("changed_files"), list) else []
    minimum_changed = int(task.get("minimum_changed_files") or 0)
    if len(changed_files) < minimum_changed:
        errors.append(f"changed_files below live task minimum: {len(changed_files)} < {minimum_changed}")
    if task.get("multi_file_expected") is True and package.get("multi_file_nonfixture") is not True:
        errors.append("live task expects multi_file_nonfixture=true")
    if str(package.get("task_class") or "") != str(task.get("class") or ""):
        errors.append("package task_class does not match live task class")
    return errors


def build_ledger_entry(
    spec: dict[str, Any],
    task: dict[str, Any],
    package_path: Path,
    package: dict[str, Any],
    reviewer: str,
    notes: str,
    accepted_for_next_stage: bool,
) -> dict[str, Any]:
    run_id = str(package.get("run_id") or "")
    task_id = str(task.get("id") or "")
    next_stage = build_next_stage_from_package(task, package_path, package)
    evidence_paths = [repo_path_text(package_path)]
    artifacts = package.get("artifacts") if isinstance(package.get("artifacts"), dict) else {}
    for value in artifacts.values():
        if isinstance(value, str) and value:
            evidence_paths.append(value)
    return {
        "trial_id": task_id,
        "run_id": run_id,
        "date": time.strftime("%Y-%m-%d"),
        "reviewer": reviewer,
        "scores": empty_scores(spec),
        "evidence_paths": sorted(set(evidence_paths)),
        "human_review_notes": notes,
        "generalizable_failures": [],
        "follow_up_changes": [],
        "accepted_for_rolling_score": False,
        "accepted_for_next_stage": accepted_for_next_stage,
        "next_stage": next_stage,
    }


def register_live_task(
    spec: dict[str, Any],
    ledger: dict[str, Any],
    task_id: str,
    package_path: Path,
    reviewer: str,
    notes: str,
    accepted_for_next_stage: bool = False,
) -> dict[str, Any]:
    task = live_task_by_id(spec, task_id)
    package = load_json_object(package_path)
    if accepted_for_next_stage and (not reviewer.strip() or len(notes.strip()) < 40):
        raise ValueError("accepted next-stage entries require reviewer and notes of at least 40 characters")
    entry = build_ledger_entry(spec, task, package_path, package, reviewer, notes, accepted_for_next_stage)
    entries = ledger.setdefault("entries", [])
    run_id = str(entry.get("run_id") or "")
    if not run_id:
        raise ValueError("evidence package run_id is required")
    if any(isinstance(item, dict) and item.get("run_id") == run_id for item in entries):
        raise ValueError(f"ledger already contains run_id: {run_id}")
    evidence_status = next_stage_evidence_status(REPO_ROOT, entry, task)
    fit_errors = validate_live_task_fit(task, package)
    if evidence_status.get("passed") is not True or fit_errors:
        reasons = [str(evidence_status.get("reason") or "next-stage evidence package failed")]
        reasons.extend(fit_errors)
        raise ValueError("; ".join(reason for reason in reasons if reason))
    entries.append(entry)
    errors = validate_ledger(spec, ledger)
    if errors:
        entries.pop()
        raise ValueError("; ".join(errors))
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description="Register a validated live Ceraxia task evidence package in the ledger.")
    parser.add_argument("--task-id", required=True, help="ID from field_trials.json live_tasks.")
    parser.add_argument("--package", required=True, type=Path, help="Path to ceraxia_next_stage_evidence_package JSON.")
    parser.add_argument("--reviewer", default="", help="Optional reviewer name for draft traceability.")
    parser.add_argument("--notes", default="", help="Optional human notes/postmortem for draft traceability.")
    parser.add_argument("--accept-for-next-stage", action="store_true", help="Count this validated entry toward the live next-stage benchmark.")
    parser.add_argument("--ledger", type=Path, default=LEDGER, help="Ledger path. Defaults to Ceraxia field_trial_ledger.json.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the entry/report without writing.")
    args = parser.parse_args()

    spec = load_json(SPEC)
    ledger = load_json(args.ledger)
    entry = register_live_task(
        spec,
        ledger,
        args.task_id,
        args.package.resolve(),
        reviewer=args.reviewer,
        notes=args.notes,
        accepted_for_next_stage=args.accept_for_next_stage,
    )
    report = build_report(spec, ledger)
    payload = {
        "entry": entry,
        "ledger_path": str(args.ledger),
        "dry_run": args.dry_run,
        "next_stage_metrics": report.get("next_stage_metrics", {}),
    }
    if not args.dry_run:
        write_json(args.ledger, ledger)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
