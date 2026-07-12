"""Metric aggregation. Empty denominators are reported as null, never as zero."""

from __future__ import annotations

from typing import Any


def _rate(numerator: int, denominator: int) -> float | None:
    return round(100.0 * numerator / denominator, 2) if denominator else None


def aggregate_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    verdicts = [str(row.get("verdict") or "unknown") for row in rows]
    accepted = sum(bool(row.get("declared_accepted")) for row in rows)
    false_accepts = verdicts.count("FALSE_ACCEPT")
    counters = {
        key: sum(int((row.get("counters") or {}).get(key) or 0) for row in rows)
        for key in (
            "required_facts", "required_facts_passed", "major_claims",
            "unsupported_major_claims", "quotes", "valid_quotes",
            "provenance_items", "valid_provenance_items",
        )
    }
    per_category: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = per_category.setdefault(str(row.get("category") or "unknown"), {"total": 0})
        bucket["total"] += 1
        verdict = str(row.get("verdict") or "unknown")
        bucket[verdict] = bucket.get(verdict, 0) + 1
    correct = sum(verdict in {"correct", "correct_blocked", "asked_clarification"} for verdict in verdicts)
    return {
        "total": len(rows),
        "correct": correct,
        "false_accepted": false_accepts,
        "failed": verdicts.count("failed"),
        "unverified": verdicts.count("unverified"),
        "errored": verdicts.count("error"),
        "declared_accepted": accepted,
        "correct_outcome_rate_pct": _rate(correct, len(rows)),
        "false_accepted_pct_of_accepted": _rate(false_accepts, accepted),
        "required_fact_recall_pct": _rate(counters["required_facts_passed"], counters["required_facts"]),
        "unsupported_major_claim_rate_pct": _rate(counters["unsupported_major_claims"], counters["major_claims"]),
        "quote_accuracy_pct": _rate(counters["valid_quotes"], counters["quotes"]),
        "provenance_integrity_pct": _rate(counters["valid_provenance_items"], counters["provenance_items"]),
        "counters": counters,
        "per_category": per_category,
    }
