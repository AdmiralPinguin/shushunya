#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ceraxia_field_trial_report import build_report, load_json, validate_ledger
from ceraxia_field_trial_review import build_review_packet


WARMASTER_ROOT = Path(__file__).resolve().parent
EYE_ROOT = WARMASTER_ROOT.parent
SPEC = EYE_ROOT / "Mechanicum" / "Ceraxia" / "field_trials.json"
LEDGER = EYE_ROOT / "Mechanicum" / "Ceraxia" / "field_trial_ledger.json"


DIMENSIONS = [
    "task_understanding",
    "repository_investigation",
    "multi_file_reasoning",
    "patch_correctness",
    "verification_discipline",
    "self_repair",
    "review_quality",
    "safety",
    "reporting",
]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def draft_expert_entries(ledger: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]]:
    trial_by_id = {
        str(item.get("id")): item
        for item in spec.get("trials", [])
        if isinstance(item, dict) and item.get("id")
    }
    entries = []
    for entry in ledger.get("entries", []):
        if not isinstance(entry, dict) or entry.get("accepted_for_rolling_score") is True:
            continue
        trial = trial_by_id.get(str(entry.get("trial_id") or ""), {})
        if trial.get("difficulty") == "expert":
            entries.append(entry)
    return entries


def score_from_packet(packet: dict[str, Any]) -> tuple[dict[str, float], list[str], list[str], str]:
    observed = packet.get("observed") if isinstance(packet.get("observed"), dict) else {}
    outcome = observed.get("trial_outcome") if isinstance(observed.get("trial_outcome"), dict) else {}
    checks = observed.get("trial_checks") if isinstance(observed.get("trial_checks"), dict) else {}
    verification = observed.get("verification_summary") if isinstance(observed.get("verification_summary"), dict) else {}
    diagnostics = observed.get("diagnostics") if isinstance(observed.get("diagnostics"), dict) else {}
    patch_source = str(observed.get("patch_source") or "")
    trial_id = str(packet.get("trial_id") or "")
    trial_class = str(packet.get("class") or "")
    changed_files = observed.get("changed_files") if isinstance(observed.get("changed_files"), list) else []
    blockers = observed.get("blockers") if isinstance(observed.get("blockers"), list) else []
    honest = observed.get("honest_evidence") if isinstance(observed.get("honest_evidence"), dict) else {}
    honest_checks = honest.get("checks") if isinstance(honest.get("checks"), dict) else {}
    required_honest_checks = {
        "source_correct",
        "tests_not_adjusted",
        "patch_minimal",
        "verification_meaningful",
        "review_artifacts_present",
    }
    all_checks_pass = bool(checks) and all(
        isinstance(value, dict) and value.get("passed") is True
        for value in checks.values()
    )
    if outcome.get("status") != "passed" or outcome.get("expected") is not True:
        raise ValueError(f"{trial_id} did not pass expected expert outcome")
    if not all_checks_pass:
        raise ValueError(f"{trial_id} lacks passing trial-specific checks")
    if honest.get("status") != "passed":
        raise ValueError(f"{trial_id} lacks passed honest evidence")
    if not required_honest_checks.issubset(honest_checks):
        missing = ", ".join(sorted(required_honest_checks - set(honest_checks)))
        raise ValueError(f"{trial_id} lacks required honest evidence checks: {missing}")
    if not all(
        isinstance(value, dict) and value.get("passed") is True
        for name, value in honest_checks.items()
        if name in required_honest_checks
    ):
        raise ValueError(f"{trial_id} has failed honest evidence checks")
    if int(verification.get("executed_count") or 0) <= 0:
        raise ValueError(f"{trial_id} lacks executed verification evidence")
    if blockers:
        raise ValueError(f"{trial_id} has blockers: {blockers}")
    if not changed_files:
        raise ValueError(f"{trial_id} has no changed-file evidence")

    is_unshaped = trial_id.startswith("ceraxia-expert-unshaped-")
    is_marker = not patch_source.startswith("test_inferred_")
    scores = {dimension: 9.5 for dimension in DIMENSIONS}
    failures: list[str] = []
    followups: list[str] = []

    if is_marker:
        scores.update(
            {
                "task_understanding": 8.5,
                "repository_investigation": 8.0,
                "multi_file_reasoning": 8.5,
                "review_quality": 8.5,
                "reporting": 8.5,
            }
        )
        failures.append("Marker-shaped expert evidence proves execution but not full unshaped engineering autonomy.")
        followups.append("Prefer unshaped test-inferred expert trials when measuring the 10/10 target.")
    else:
        if not diagnostics:
            raise ValueError(f"{trial_id} is test-inferred but lacks diagnostics")
        scores["repository_investigation"] = 9.5
        scores["review_quality"] = 9.5

    if trial_class.endswith("self_repair"):
        if int(verification.get("repair_count") or 0) != 1:
            raise ValueError(f"{trial_id} self-repair evidence must show exactly one repair")
        scores["self_repair"] = 10.0
        scores["verification_discipline"] = 9.75
    elif int(verification.get("repair_count") or 0) > 0:
        scores["self_repair"] = 9.75

    if "security" in trial_class:
        scores["safety"] = 9.75
    if "concurrency" in trial_class or "integration" in trial_class:
        scores["multi_file_reasoning"] = 9.75 if is_unshaped else scores["multi_file_reasoning"]
    if "api" in trial_class or "data_migration" in trial_class:
        scores["multi_file_reasoning"] = 9.75 if is_unshaped else scores["multi_file_reasoning"]

    note = (
        f"Strict expert evidence review: {trial_id} passed expected outcome, all trial-specific checks passed, "
        f"verification executed {verification.get('executed_count')} command(s), patch_source={patch_source}, "
        f"changed_files={len(changed_files)}. "
    )
    if is_unshaped:
        note += "Accepted as unshaped expert evidence because the patch was inferred from tests/repo evidence with diagnostics."
    else:
        note += "Accepted as marker-shaped expert evidence only; scores are capped below 10-target strength where autonomy is not proven."
    return scores, failures, followups, note


def review_payload_for_entry(entry: dict[str, Any], spec: dict[str, Any], reviewer: str) -> dict[str, Any]:
    packet = build_review_packet(entry, spec)
    scores, failures, followups, note = score_from_packet(packet)
    return {
        "trial_id": packet.get("trial_id", ""),
        "run_id": packet.get("run_id", ""),
        "reviewer": reviewer,
        "scores": scores,
        "human_review_notes": note,
        "generalizable_failures": failures,
        "follow_up_changes": followups,
        "accepted_for_rolling_score": True,
    }


def apply_reviews(spec: dict[str, Any], ledger: dict[str, Any], reviews: list[dict[str, Any]]) -> dict[str, Any]:
    entries = ledger.get("entries") if isinstance(ledger.get("entries"), list) else []
    by_run_id = {
        str(entry.get("run_id") or ""): entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("run_id")
    }
    for review in reviews:
        entry = by_run_id.get(str(review.get("run_id") or ""))
        if not entry:
            raise ValueError(f"missing ledger entry for {review.get('run_id')}")
        entry["reviewer"] = review["reviewer"]
        entry["scores"] = review["scores"]
        entry["human_review_notes"] = review["human_review_notes"]
        entry["generalizable_failures"] = review["generalizable_failures"]
        entry["follow_up_changes"] = review["follow_up_changes"]
        entry["accepted_for_rolling_score"] = True
    errors = validate_ledger(spec, ledger)
    if errors:
        raise ValueError("; ".join(errors))
    return ledger


def main() -> int:
    parser = argparse.ArgumentParser(description="Strictly review Ceraxia expert draft evidence.")
    parser.add_argument("--unshaped-only", action="store_true", help="Review only unshaped expert drafts.")
    parser.add_argument("--write-dir", type=Path, help="Write generated review payloads to this directory.")
    parser.add_argument("--apply", action="store_true", help="Apply generated reviews to the ledger.")
    parser.add_argument("--dry-run", action="store_true", help="Print report for the generated reviews without writing the ledger.")
    parser.add_argument("--reviewer", default="Codex strict expert evidence review")
    args = parser.parse_args()
    spec = load_json(SPEC)
    ledger = load_json(LEDGER)
    entries = draft_expert_entries(ledger, spec)
    if args.unshaped_only:
        entries = [
            entry for entry in entries
            if str(entry.get("trial_id") or "").startswith("ceraxia-expert-unshaped-")
        ]
    reviews: list[dict[str, Any]] = []
    rejected_entries: list[dict[str, str]] = []
    for entry in entries:
        try:
            reviews.append(review_payload_for_entry(entry, spec, args.reviewer))
        except ValueError as exc:
            rejected_entries.append(
                {
                    "trial_id": str(entry.get("trial_id") or ""),
                    "run_id": str(entry.get("run_id") or ""),
                    "reason": str(exc),
                }
            )
    if args.write_dir:
        for review in reviews:
            write_json(args.write_dir / f"{review['run_id']}.json", review)
    updated = apply_reviews(spec, json.loads(json.dumps(ledger)), reviews)
    report = build_report(spec, updated)
    output = {
        "ok": True,
        "review_count": len(reviews),
        "rejected_count": len(rejected_entries),
        "applied": bool(args.apply and not args.dry_run),
        "report": report,
        "reviews": reviews,
        "rejected_entries": rejected_entries,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if args.apply and not args.dry_run:
        write_json(LEDGER, updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
