#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ceraxia_evidence_contract import REQUIRED_HONEST_CHECKS
from ceraxia_field_trial_accept import apply_review
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


def candidate_entries(ledger: dict[str, Any], trial_id: str = "", run_id: str = "") -> list[dict[str, Any]]:
    entries = [entry for entry in ledger.get("entries", []) if isinstance(entry, dict)]
    entries = [entry for entry in entries if entry.get("accepted_for_rolling_score") is not True]
    if trial_id:
        entries = [entry for entry in entries if entry.get("trial_id") == trial_id]
    if run_id:
        entries = [entry for entry in entries if entry.get("run_id") == run_id]
    return entries


def require_passed_packet(packet: dict[str, Any]) -> None:
    evidence = packet.get("evidence") if isinstance(packet.get("evidence"), dict) else {}
    if not evidence.get("trial_result") or not evidence.get("final_manifest"):
        raise ValueError("missing complete evidence package")
    observed = packet.get("observed") if isinstance(packet.get("observed"), dict) else {}
    honest = observed.get("honest_evidence") if isinstance(observed.get("honest_evidence"), dict) else {}
    honest_checks = honest.get("checks") if isinstance(honest.get("checks"), dict) else {}
    if honest.get("status") != "passed":
        raise ValueError("honest_evidence is not passed")
    missing = REQUIRED_HONEST_CHECKS - set(honest_checks)
    if missing:
        raise ValueError(f"missing honest evidence checks: {', '.join(sorted(missing))}")
    failed = [
        name
        for name in REQUIRED_HONEST_CHECKS
        if not isinstance(honest_checks.get(name), dict) or honest_checks[name].get("passed") is not True
    ]
    if failed:
        raise ValueError(f"failed honest evidence checks: {', '.join(sorted(failed))}")
    outcome = observed.get("trial_outcome") if isinstance(observed.get("trial_outcome"), dict) else {}
    if outcome.get("expected") is not True:
        raise ValueError("trial_outcome was not expected")
    if str(observed.get("manifest_status") or "") != "ready":
        raise ValueError("final manifest is not ready")
    blockers = observed.get("blockers") if isinstance(observed.get("blockers"), list) else []
    if blockers:
        raise ValueError(f"final manifest has blockers: {blockers}")
    verification = observed.get("verification_summary") if isinstance(observed.get("verification_summary"), dict) else {}
    if int(verification.get("executed_count") or 0) <= 0:
        raise ValueError("no executed verification evidence")


def scores_for_packet(packet: dict[str, Any]) -> tuple[dict[str, float], list[str], list[str], str]:
    require_passed_packet(packet)
    observed = packet.get("observed") if isinstance(packet.get("observed"), dict) else {}
    verification = observed.get("verification_summary") if isinstance(observed.get("verification_summary"), dict) else {}
    changed_files = observed.get("changed_files") if isinstance(observed.get("changed_files"), list) else []
    diagnostics = observed.get("diagnostics") if isinstance(observed.get("diagnostics"), dict) else {}
    patch_source = str(observed.get("patch_source") or "")
    trial_class = str(packet.get("class") or "")
    difficulty = str(packet.get("difficulty") or "")
    scores = {dimension: 7.5 for dimension in DIMENSIONS}
    failures: list[str] = []
    followups: list[str] = []

    if difficulty == "expert":
        scores = {dimension: 8.0 for dimension in DIMENSIONS}
    if patch_source.startswith("test_inferred_") or patch_source.startswith("runtime_diagnostic_"):
        scores["repository_investigation"] += 1.0
        scores["review_quality"] += 0.5
    elif patch_source:
        scores["repository_investigation"] = min(scores["repository_investigation"], 7.5)
        failures.append("Evidence is executable but partly marker-shaped; autonomy is not fully proven.")
        followups.append("Prefer unshaped trials when raising the long-term engineering score.")

    if len(changed_files) >= 2:
        scores["multi_file_reasoning"] += 0.75
    if int(verification.get("repair_count") or 0) > 0:
        scores["self_repair"] += 1.0
        scores["verification_discipline"] += 0.5
    if diagnostics:
        scores["repository_investigation"] += 0.25
        scores["review_quality"] += 0.25
    if "security" in trial_class or "safety" in trial_class:
        scores["safety"] += 0.75
    if "api" in trial_class or "migration" in trial_class or "integration" in trial_class:
        scores["multi_file_reasoning"] += 0.5
    scores = {dimension: min(round(value, 2), 9.0) for dimension, value in scores.items()}
    note = (
        f"Automated evidence review accepted {packet.get('run_id')} conservatively: "
        f"honest_evidence passed, manifest is ready, verification executed "
        f"{verification.get('executed_count')} command(s), patch_source={patch_source}, "
        f"changed_files={len(changed_files)}. Scores are capped because this is evidence-based triage, "
        "not a human senior-code-review claim."
    )
    return scores, failures, followups, note


def review_for_entry(entry: dict[str, Any], spec: dict[str, Any], reviewer: str) -> dict[str, Any]:
    packet = build_review_packet(entry, spec)
    scores, failures, followups, note = scores_for_packet(packet)
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate conservative accepted reviews for evidence-complete Ceraxia draft trials.")
    parser.add_argument("--trial-id", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--write-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reviewer", default="Codex conservative evidence review")
    args = parser.parse_args()

    spec = load_json(SPEC)
    ledger = load_json(LEDGER)
    reviews: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for entry in candidate_entries(ledger, trial_id=args.trial_id, run_id=args.run_id):
        try:
            reviews.append(review_for_entry(entry, spec, args.reviewer))
        except ValueError as exc:
            rejected.append(
                {
                    "trial_id": str(entry.get("trial_id") or ""),
                    "run_id": str(entry.get("run_id") or ""),
                    "reason": str(exc),
                }
            )
    updated = json.loads(json.dumps(ledger))
    for review in reviews:
        updated = apply_review(spec, updated, review)
    errors = validate_ledger(spec, updated)
    if errors:
        raise SystemExit("; ".join(errors))
    if args.write_dir:
        for review in reviews:
            write_json(args.write_dir / f"{review['run_id']}.json", review)
    report = build_report(spec, updated)
    print(
        json.dumps(
            {
                "ok": True,
                "review_count": len(reviews),
                "rejected_count": len(rejected),
                "applied": bool(args.apply and not args.dry_run),
                "reviews": reviews,
                "rejected_entries": rejected,
                "report": report,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.apply and not args.dry_run:
        write_json(LEDGER, updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
