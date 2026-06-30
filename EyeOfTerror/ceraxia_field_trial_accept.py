#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ceraxia_field_trial_report import build_report, load_json, validate_ledger
from ceraxia_field_trial_review import build_review_packet


ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "InnerCircle" / "Ceraxia" / "field_trials.json"
LEDGER = ROOT / "InnerCircle" / "Ceraxia" / "field_trial_ledger.json"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dimensions(spec: dict[str, Any]) -> list[str]:
    return [str(item) for item in spec.get("dimensions", [])]


def validate_review_payload(spec: dict[str, Any], ledger_entry: dict[str, Any], review: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    dims = dimensions(spec)
    run_id = str(review.get("run_id") or "")
    trial_id = str(review.get("trial_id") or "")
    if run_id != str(ledger_entry.get("run_id") or ""):
        errors.append(f"review run_id does not match ledger entry: {run_id}")
    if trial_id != str(ledger_entry.get("trial_id") or ""):
        errors.append(f"review trial_id does not match ledger entry: {trial_id}")
    reviewer = str(review.get("reviewer") or "").strip()
    if not reviewer:
        errors.append("reviewer is required")
    notes = str(review.get("human_review_notes") or "").strip()
    if len(notes) < 40:
        errors.append("human_review_notes must be at least 40 characters")
    scores = review.get("scores") if isinstance(review.get("scores"), dict) else {}
    if set(scores) != set(dims):
        errors.append("scores must contain exactly the spec dimensions")
    low_scores: list[str] = []
    for dimension in dims:
        value = scores.get(dimension)
        if not isinstance(value, (int, float)) or value < 0 or value > 10:
            errors.append(f"invalid score for {dimension}: {value}")
        elif value < 7:
            low_scores.append(dimension)
    generalizable_failures = review.get("generalizable_failures")
    follow_up_changes = review.get("follow_up_changes")
    if low_scores:
        if not isinstance(generalizable_failures, list) or not generalizable_failures:
            errors.append(f"scores below 7 require generalizable_failures: {', '.join(low_scores)}")
        if not isinstance(follow_up_changes, list) or not follow_up_changes:
            errors.append(f"scores below 7 require follow_up_changes: {', '.join(low_scores)}")
    if review.get("accepted_for_rolling_score") is not True:
        errors.append("accepted_for_rolling_score must be true")
    return errors


def apply_review(spec: dict[str, Any], ledger: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    entries = ledger.get("entries") if isinstance(ledger.get("entries"), list) else []
    run_id = str(review.get("run_id") or "")
    matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("run_id") == run_id]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one ledger entry for run_id {run_id!r}, found {len(matches)}")
    entry = matches[0]
    packet = build_review_packet(entry, spec)
    if not packet.get("evidence", {}).get("trial_result"):
        raise ValueError(f"review packet for {run_id} lacks trial_result evidence")
    errors = validate_review_payload(spec, entry, review)
    if errors:
        raise ValueError("; ".join(errors))
    entry["reviewer"] = str(review.get("reviewer") or "").strip()
    entry["scores"] = review["scores"]
    entry["human_review_notes"] = str(review.get("human_review_notes") or "").strip()
    entry["generalizable_failures"] = review.get("generalizable_failures", [])
    entry["follow_up_changes"] = review.get("follow_up_changes", [])
    entry["accepted_for_rolling_score"] = True
    return ledger


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply a human review result to the Ceraxia field trial ledger.")
    parser.add_argument("--review-file", required=True, type=Path, help="JSON review payload with run_id, scores, notes, and acceptance flag.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the resulting report without writing the ledger.")
    args = parser.parse_args()
    spec = load_json(SPEC)
    ledger = load_json(LEDGER)
    review = load_json(args.review_file)
    try:
        updated = apply_review(spec, ledger, review)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    errors = validate_ledger(spec, updated)
    if errors:
        print(json.dumps({"ok": False, "error": "updated ledger is invalid", "ledger_errors": errors}, ensure_ascii=False, indent=2))
        return 2
    report = build_report(spec, updated)
    print(json.dumps({"ok": True, "dry_run": args.dry_run, "report": report}, ensure_ascii=False, indent=2))
    if not args.dry_run:
        write_json(LEDGER, updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
