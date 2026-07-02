#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ceraxia_evidence_contract import load_json_object, next_stage_evidence_status, resolve_repo_path
from ceraxia_field_trial_report import load_json, task_catalog_by_id


WARMASTER_ROOT = Path(__file__).resolve().parent
EYE_ROOT = WARMASTER_ROOT.parent
REPO_ROOT = EYE_ROOT.parent
SPEC = EYE_ROOT / "Mechanicum" / "Ceraxia" / "field_trials.json"
LEDGER = EYE_ROOT / "Mechanicum" / "Ceraxia" / "field_trial_ledger.json"
DEFAULT_OUTPUT = EYE_ROOT / "Mechanicum" / "Ceraxia" / "live_benchmark_learnings.json"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_package(entry: dict[str, Any]) -> dict[str, Any]:
    next_stage = entry.get("next_stage") if isinstance(entry.get("next_stage"), dict) else {}
    package_ref = next_stage.get("evidence_package")
    if not isinstance(package_ref, str) or not package_ref:
        return {}
    path = resolve_repo_path(REPO_ROOT, package_ref)
    try:
        return load_json_object(path)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def classify_failure(package: dict[str, Any]) -> list[str]:
    postmortem = str(package.get("postmortem") or "").lower()
    summary = package.get("summary") if isinstance(package.get("summary"), dict) else {}
    patterns: list[str] = []
    if "explicit path hints" in postmortem or "path hint" in postmortem:
        patterns.append("future_artifact_filename_leaked_as_repo_path_hint")
    if summary.get("ceraxia_package_ok") is False:
        patterns.append("package_not_finalized_or_review_blocked")
    if package.get("status") == "honest_blocked":
        patterns.append("correctly_blocked_without_false_success")
    if "planned_only" in postmortem:
        patterns.append("dry_run_planned_only_no_mutation")
    if not patterns and package.get("status") not in {"fully_successful", "repaired_success"}:
        patterns.append("unclassified_non_success")
    return patterns


def build_learnings(spec: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    catalog = task_catalog_by_id(spec)
    entries = [
        entry
        for entry in ledger.get("entries", [])
        if isinstance(entry, dict) and isinstance(entry.get("next_stage"), dict)
    ]
    accepted = [entry for entry in entries if entry.get("accepted_for_next_stage") is True]
    rows: list[dict[str, Any]] = []
    pattern_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    class_counts: dict[str, int] = {}
    for entry in accepted:
        task = catalog.get(str(entry.get("trial_id") or ""), {})
        package = load_package(entry)
        evidence_status = next_stage_evidence_status(REPO_ROOT, entry, task)
        status = str(entry.get("next_stage", {}).get("status") or package.get("status") or "")
        task_class = str(entry.get("next_stage", {}).get("class") or task.get("class") or "")
        patterns = classify_failure(package)
        status_counts[status] = status_counts.get(status, 0) + 1
        if task_class:
            class_counts[task_class] = class_counts.get(task_class, 0) + 1
        for pattern in patterns:
            pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1
        rows.append(
            {
                "trial_id": entry.get("trial_id", ""),
                "run_id": entry.get("run_id", ""),
                "class": task_class,
                "status": status,
                "evidence_passed": evidence_status.get("passed") is True,
                "patterns": patterns,
                "postmortem": package.get("postmortem", ""),
            }
        )
    return {
        "kind": "ceraxia_live_benchmark_learnings",
        "contract_version": "eye-mechanicum.v1",
        "accepted_live_count": len(accepted),
        "status_counts": status_counts,
        "class_counts": class_counts,
        "pattern_counts": pattern_counts,
        "rows": rows,
        "mandatory_next_actions": [
            "keep accepted_for_next_stage separate from draft registration",
            "do not pass future evidence artifact filenames into Ceraxia task prompts as repo path hints",
            "treat honest_blocked as benchmark signal, not success",
            "only count successful live tasks when package_ok, review, verification, and readable evidence all pass",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate Ceraxia live benchmark learnings from ledger and evidence packages.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_learnings(load_json(SPEC), load_json(LEDGER))
    write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
