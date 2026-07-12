"""Deterministic outcome, provenance, quote, and typed-relation oracles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .fixtures import LoadedFixture


STATUS_VALUES = {"accepted", "needs_user", "blocked", "failed"}
EPISTEMIC_KINDS = {"source_assertion", "direct_observation", "inference", "assumption"}
IMPORTANCE_VALUES = {"major", "minor"}
RELATIONS = {"reports", "supports", "refutes", "qualifies", "context"}
VERIFICATION_VALUES = {"unverified", "mechanically_valid", "semantically_verified", "contested"}


@dataclass(frozen=True)
class OracleReport:
    passed: bool
    failures: list[str]
    counters: dict[str, int]
    matched_facts: dict[str, list[str]]


def _fold(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _contains_all(text: Any, terms: list[str]) -> bool:
    folded = _fold(text)
    return all(_fold(term) in folded for term in terms)


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _duplicates(values: list[str]) -> set[str]:
    seen: set[str] = set()
    return {value for value in values if value in seen or seen.add(value)}


def evaluate_task(task: dict[str, Any], result: dict[str, Any], fixture: LoadedFixture) -> OracleReport:
    failures: list[str] = []
    counters = {
        "required_facts": len(task["oracle"]["required_facts"]),
        "required_facts_passed": 0,
        "major_claims": 0,
        "unsupported_major_claims": 0,
        "quotes": 0,
        "valid_quotes": 0,
        "provenance_items": 0,
        "valid_provenance_items": 0,
    }
    if not isinstance(result, dict):
        return OracleReport(False, ["subject result is not an object"], counters, {})
    allowed_root = {"contract_version", "mission_id", "status", "accepted", "final_text", "question", "ledger", "search_log"}
    required_root = {"contract_version", "mission_id", "status", "accepted", "ledger", "search_log"}
    unknown = sorted(set(result) - allowed_root)
    missing = sorted(required_root - set(result))
    if unknown:
        failures.append(f"subject result has unknown keys: {', '.join(unknown)}")
    if missing:
        failures.append(f"subject result is missing keys: {', '.join(missing)}")
    if result.get("contract_version") != "research-result/v1":
        failures.append("subject result contract_version is not research-result/v1")
    expected_mission_id = f"eval-{task['id']}"
    if result.get("mission_id") != expected_mission_id:
        failures.append("subject result mission_id does not match the submitted task")
    status = result.get("status")
    accepted = result.get("accepted")
    if status not in STATUS_VALUES:
        failures.append("subject result status is invalid")
    if type(accepted) is not bool:
        failures.append("subject result accepted must be boolean")
    elif accepted != (status == "accepted"):
        failures.append("subject result status and accepted flag disagree")
    if status not in task["expected_outcomes"]:
        failures.append(f"terminal outcome {status!r} is not expected")
    final_text = result.get("final_text") if isinstance(result.get("final_text"), str) else ""
    question = result.get("question") if isinstance(result.get("question"), str) else ""
    if status == "accepted" and not final_text.strip():
        failures.append("accepted result has no final_text")
    if status == "needs_user" and not question.strip():
        failures.append("needs_user result has no question")
    if not isinstance(result.get("search_log"), list):
        failures.append("subject result search_log must be an array")

    ledger = result.get("ledger") if isinstance(result.get("ledger"), dict) else {}
    ledger_keys = {"sources", "spans", "claims", "evidence_edges", "derivations", "conflicts", "gaps", "final_claim_refs"}
    if set(ledger) != ledger_keys:
        failures.append("ledger keys do not exactly match research-result/v1")
    sources = _dict_list(ledger.get("sources"))
    spans = _dict_list(ledger.get("spans"))
    claims = _dict_list(ledger.get("claims"))
    edges = _dict_list(ledger.get("evidence_edges"))
    derivations = _dict_list(ledger.get("derivations"))
    conflicts = _dict_list(ledger.get("conflicts"))
    gaps = _dict_list(ledger.get("gaps"))
    final_refs = _dict_list(ledger.get("final_claim_refs"))
    for key in ledger_keys:
        if not isinstance(ledger.get(key), list):
            failures.append(f"ledger.{key} must be an array")

    source_by_id: dict[str, dict[str, Any]] = {}
    for index, source in enumerate(sources):
        counters["provenance_items"] += 1
        source_id = source.get("source_id")
        if set(source) - {"source_id", "url", "raw_sha256", "normalized_sha256"} or not {"source_id", "raw_sha256", "normalized_sha256"} <= set(source):
            failures.append(f"ledger.sources[{index}] has invalid fields")
            continue
        if not isinstance(source_id, str) or source_id in source_by_id:
            failures.append(f"ledger.sources[{index}] has an invalid or duplicate source_id")
            continue
        try:
            document = fixture.document(source_id)
        except Exception:
            failures.append(f"ledger source {source_id!r} is outside the immutable fixture")
            continue
        if source.get("raw_sha256") != document.data["raw_sha256"] or source.get("normalized_sha256") != document.data["normalized_sha256"]:
            failures.append(f"ledger source {source_id!r} hash mismatch")
            continue
        source_by_id[source_id] = source
        counters["valid_provenance_items"] += 1

    valid_spans: dict[str, dict[str, Any]] = {}
    for index, span in enumerate(spans):
        counters["quotes"] += 1
        counters["provenance_items"] += 1
        required = {"span_id", "source_id", "representation_sha256", "start_byte", "end_byte", "excerpt"}
        if set(span) != required:
            failures.append(f"ledger.spans[{index}] has invalid fields")
            continue
        span_id, source_id = span.get("span_id"), span.get("source_id")
        if not isinstance(span_id, str) or not span_id or span_id in valid_spans:
            failures.append(f"ledger.spans[{index}] has an invalid or duplicate span_id")
            continue
        if source_id not in source_by_id:
            failures.append(f"span {span_id!r} references an invalid source")
            continue
        document = fixture.document(source_id)
        if span.get("representation_sha256") != document.data["normalized_sha256"]:
            failures.append(f"span {span_id!r} representation hash mismatch")
            continue
        start, end = span.get("start_byte"), span.get("end_byte")
        if type(start) is not int or type(end) is not int or start < 0 or end <= start or end > len(document.normalized):
            failures.append(f"span {span_id!r} byte range is invalid")
            continue
        excerpt = span.get("excerpt")
        if not isinstance(excerpt, str):
            failures.append(f"span {span_id!r} excerpt is not text")
            continue
        try:
            expected = document.normalized[start:end].decode("utf-8", errors="strict")
        except UnicodeError:
            failures.append(f"span {span_id!r} splits a UTF-8 character")
            continue
        if excerpt != expected:
            failures.append(f"span {span_id!r} excerpt does not match fixture bytes")
            continue
        valid_spans[span_id] = span
        counters["valid_quotes"] += 1
        counters["valid_provenance_items"] += 1

    claim_by_id: dict[str, dict[str, Any]] = {}
    for index, claim in enumerate(claims):
        required = {"claim_id", "text", "epistemic_kind", "importance", "verification_status"}
        if set(claim) != required:
            failures.append(f"ledger.claims[{index}] has invalid fields")
            continue
        claim_id = claim.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id or claim_id in claim_by_id:
            failures.append(f"ledger.claims[{index}] has an invalid or duplicate claim_id")
            continue
        if not isinstance(claim.get("text"), str) or not claim["text"].strip():
            failures.append(f"claim {claim_id!r} has no text")
            continue
        if claim.get("epistemic_kind") not in EPISTEMIC_KINDS or claim.get("importance") not in IMPORTANCE_VALUES or claim.get("verification_status") not in VERIFICATION_VALUES:
            failures.append(f"claim {claim_id!r} has invalid classification")
            continue
        claim_by_id[claim_id] = claim
    counters["major_claims"] = sum(claim.get("importance") == "major" for claim in claim_by_id.values())

    valid_edges: list[dict[str, Any]] = []
    for index, edge in enumerate(edges):
        if set(edge) != {"claim_id", "span_id", "relation"}:
            failures.append(f"ledger.evidence_edges[{index}] has invalid fields")
            continue
        if edge.get("claim_id") not in claim_by_id or edge.get("span_id") not in valid_spans or edge.get("relation") not in RELATIONS:
            failures.append(f"ledger.evidence_edges[{index}] has an invalid reference or relation")
            continue
        valid_edges.append(edge)

    for index, derivation in enumerate(derivations):
        if set(derivation) != {"claim_id", "premise_claim_ids"} or derivation.get("claim_id") not in claim_by_id:
            failures.append(f"ledger.derivations[{index}] is invalid")
            continue
        premises = derivation.get("premise_claim_ids")
        if not isinstance(premises, list) or not premises or any(premise not in claim_by_id for premise in premises):
            failures.append(f"ledger.derivations[{index}] has invalid premises")
    inference_ids = {claim_id for claim_id, claim in claim_by_id.items() if claim["epistemic_kind"] == "inference"}
    derived_ids = {item.get("claim_id") for item in derivations if isinstance(item.get("premise_claim_ids"), list) and item.get("premise_claim_ids")}
    for claim_id in sorted(inference_ids - derived_ids):
        failures.append(f"inference claim {claim_id!r} has no derivation")

    valid_conflicts: list[set[str]] = []
    for index, conflict in enumerate(conflicts):
        if set(conflict) != {"claim_ids", "reason"}:
            failures.append(f"ledger.conflicts[{index}] has invalid fields")
            continue
        claim_ids = conflict.get("claim_ids")
        if not isinstance(claim_ids, list) or len(set(claim_ids)) < 2 or any(claim_id not in claim_by_id for claim_id in claim_ids):
            failures.append(f"ledger.conflicts[{index}] has invalid claim references")
            continue
        valid_conflicts.append(set(claim_ids))
    gap_codes = {gap.get("code") for gap in gaps if set(gap) == {"code", "description"} and isinstance(gap.get("code"), str) and isinstance(gap.get("description"), str)}
    if len(gap_codes) != len(gaps):
        failures.append("ledger.gaps contains malformed or duplicate entries")

    final_bytes = final_text.encode("utf-8")
    for index, ref in enumerate(final_refs):
        if set(ref) != {"start_byte", "end_byte", "claim_ids"}:
            failures.append(f"ledger.final_claim_refs[{index}] has invalid fields")
            continue
        start, end, claim_ids = ref.get("start_byte"), ref.get("end_byte"), ref.get("claim_ids")
        if type(start) is not int or type(end) is not int or start < 0 or end <= start or end > len(final_bytes):
            failures.append(f"ledger.final_claim_refs[{index}] has an invalid byte range")
        if not isinstance(claim_ids, list) or not claim_ids or any(claim_id not in claim_by_id for claim_id in claim_ids):
            failures.append(f"ledger.final_claim_refs[{index}] has invalid claim references")

    oracle = task["oracle"]
    matched_facts: dict[str, list[str]] = {}
    matched_major: set[str] = set()
    for fact in oracle["required_facts"]:
        fact_failures: list[str] = []
        if not _contains_all(final_text, fact["final_contains_all"]):
            fact_failures.append("final text")
        matched_claim_ids = [
            claim_id for claim_id, claim in claim_by_id.items()
            if claim["importance"] == "major" and _contains_all(claim["text"], fact["claim_contains_all"])
        ]
        matched_facts[fact["id"]] = matched_claim_ids
        if not matched_claim_ids:
            fact_failures.append("major claim")
        evidence_ok = False
        for claim_id in matched_claim_ids:
            for edge in valid_edges:
                if edge["claim_id"] != claim_id or edge["relation"] not in fact["relations"]:
                    continue
                span = valid_spans[edge["span_id"]]
                if span["source_id"] in fact["source_ids"] and _contains_all(span["excerpt"], fact["span_contains_all"]):
                    evidence_ok = True
                    break
            if evidence_ok:
                break
        if not evidence_ok:
            fact_failures.append("typed evidence relation")
        if fact_failures:
            failures.append(f"required fact {fact['id']!r} missing: {', '.join(fact_failures)}")
        else:
            counters["required_facts_passed"] += 1
            matched_major.update(matched_claim_ids)

    searchable_text = "\n".join([final_text, *(claim["text"] for claim in claim_by_id.values())])
    for forbidden in oracle["forbidden_claims"]:
        if _contains_all(searchable_text, forbidden["contains_all"]):
            failures.append(f"forbidden claim {forbidden['id']!r} is present")
    for required in oracle["required_conflicts"]:
        left = set(matched_facts.get(required["left_fact_id"], []))
        right = set(matched_facts.get(required["right_fact_id"], []))
        if not any(conflict & left and conflict & right for conflict in valid_conflicts):
            failures.append(f"required conflict {required['id']!r} is missing")
    for code in oracle["required_gap_codes"]:
        if code not in gap_codes:
            failures.append(f"required gap code {code!r} is missing")
    clarification = oracle.get("clarification")
    if clarification:
        if len(question.strip()) < clarification["min_chars"] or not any(_fold(term) in _fold(question) for term in clarification["contains_any"]):
            failures.append("clarification question is not specific enough")

    unsupported_major = set(
        claim_id for claim_id, claim in claim_by_id.items()
        if claim["importance"] == "major" and claim_id not in matched_major
    )
    counters["unsupported_major_claims"] = len(unsupported_major)
    if len(unsupported_major) > oracle["max_unsupported_major_claims"]:
        failures.append("unsupported major claim budget exceeded: " + ", ".join(sorted(unsupported_major)))
    return OracleReport(not failures, failures, counters, matched_facts)


def evaluate_legacy_artifact(record: dict[str, Any]) -> dict[str, Any]:
    """Classify the pinned, sanitized LegacyIskandar audit without copied web text."""
    required = {
        "regression_id", "claimed_accepted", "claimed_source_count", "claimed_claim_count",
        "claimed_evidence_coverage_percent", "low_confidence_claim_count",
        "immutable_snapshot_hash_count", "evidence_locator_count",
        "required_history_fact_count", "artifact_hashes",
    }
    if not isinstance(record, dict) or set(record) != required:
        raise ValueError("legacy regression record has invalid fields")
    if not isinstance(record["artifact_hashes"], dict) or not record["artifact_hashes"]:
        raise ValueError("legacy regression has no pinned artifact hashes")
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in record["artifact_hashes"].values()):
        raise ValueError("legacy regression contains an invalid artifact hash")
    substantive_failure = (
        record["required_history_fact_count"] == 0
        or record["immutable_snapshot_hash_count"] == 0
        or record["evidence_locator_count"] == 0
        or record["low_confidence_claim_count"] >= record["claimed_claim_count"]
    )
    verdict = "FALSE_ACCEPT" if record["claimed_accepted"] is True and substantive_failure else "not_false_accept"
    return {
        "regression_id": record["regression_id"],
        "verdict": verdict,
        "substantive_failure": substantive_failure,
        "claimed_evidence_coverage_percent": record["claimed_evidence_coverage_percent"],
    }
