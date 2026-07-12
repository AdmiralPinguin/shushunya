"""Context-isolated semantic-review orchestration for ResearchWarband.

The mission pipeline owns sequencing and budgets.  This module owns the
security-sensitive semantic review protocol: the immutable request, exact
subject manifest, one-shot attestation consumption, reviewed ledger, and final
acceptance binding.  It intentionally accepts serialized spec/unit mappings so
it does not depend on pipeline domain types or create an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import re
from typing import Any, Mapping, Sequence

from .model_client import (
    ModelProtocolError,
    ReviewSession,
    TrustedReviewBoundary,
    canonical_json_sha256,
    final_review_attestation_digest,
    parse_json_object,
)
from .reader import ReaderCandidate, REVIEW_PASS_COVERAGE_ROLES
from .schema import EvidenceLedger
from .verifier import (
    ReviewAttestation,
    claim_review_digest,
    edge_review_digest,
)


_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
_ALIGNMENTS = frozenset({"entailed", "not_entailed", "uncertain"})
_PROJECTION_SCHEMA = "research-semantic-review-projection-v1"
_MODEL_REQUEST_DIGEST_CONTEXT = "model-visible semantic review request"


class SemanticReviewError(ModelProtocolError):
    """A semantic reviewer or its trust boundary violated the protocol."""


@dataclass(frozen=True, slots=True)
class ReviewCoverageCandidate:
    """Trusted exact Reader output prepared for semantic coverage review."""

    candidate: ReaderCandidate
    coverage_role: str
    normalized_text: str

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, ReaderCandidate):
            raise TypeError("coverage candidate must be a ReaderCandidate")
        if self.coverage_role not in REVIEW_PASS_COVERAGE_ROLES:
            raise ValueError("coverage candidate role is unsupported")
        if type(self.normalized_text) is not str or not self.normalized_text:
            raise TypeError("coverage candidate normalized_text must be non-empty")
        candidate = self.candidate
        if (
            candidate.end_char > len(self.normalized_text)
            or candidate.end_char - candidate.start_char != len(candidate.excerpt)
            or self.normalized_text[candidate.start_char : candidate.end_char]
            != candidate.excerpt
        ):
            raise ValueError(
                "coverage candidate excerpt is not exact at its trusted source bounds"
            )


def _nonempty(value: Any, context: str) -> str:
    if type(value) is not str or not value.strip():
        raise SemanticReviewError(f"{context} must be a non-empty string")
    return value.strip()


def _identifier(value: Any, context: str) -> str:
    result = _nonempty(value, context)
    if not _ID_RE.fullmatch(result):
        raise SemanticReviewError(f"{context} is not a valid identifier")
    return result


def _mapping(
    value: Any,
    context: str,
    *,
    required: frozenset[str] = frozenset(),
    optional: frozenset[str] = frozenset(),
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SemanticReviewError(f"{context} must be an object")
    if any(type(key) is not str for key in value):
        raise SemanticReviewError(f"{context} keys must be strings")
    keys = set(value)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise SemanticReviewError(
            f"{context} missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise SemanticReviewError(
            f"{context} unknown fields: {', '.join(sorted(unknown))}"
        )
    return value


def _array(value: Any, context: str) -> list[Any]:
    if type(value) is not list:
        raise SemanticReviewError(f"{context} must be an array")
    return value


def _strings(value: Any, context: str) -> tuple[str, ...]:
    result = tuple(_nonempty(item, f"{context} item") for item in _array(value, context))
    if len(set(result)) != len(result):
        raise SemanticReviewError(f"{context} must not contain duplicates")
    return result


def _choice(value: Any, choices: frozenset[str], context: str) -> str:
    result = _nonempty(value, context)
    if result not in choices:
        raise SemanticReviewError(
            f"{context} must be one of: {', '.join(sorted(choices))}"
        )
    return result


def _strict_spec(spec_payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(spec_payload, Mapping):
        raise TypeError("spec_payload must be a mapping")
    return parse_json_object(dict(spec_payload))


def _strict_units(
    draft_units: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    if isinstance(draft_units, (str, bytes)) or not isinstance(draft_units, Sequence):
        raise TypeError("draft_units must be a sequence of mappings")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(draft_units, 1):
        if not isinstance(item, Mapping):
            raise TypeError(f"draft_units[{index}] must be a mapping")
        strict = parse_json_object(dict(item))
        unit_id = _identifier(strict.get("id"), f"draft_units[{index}].id")
        if unit_id in seen:
            raise SemanticReviewError(f"duplicate draft unit {unit_id}")
        seen.add(unit_id)
        result.append(strict)
    if not result:
        raise SemanticReviewError("semantic review requires at least one draft unit")
    return tuple(result)


def _required_value(data: Mapping[str, Any], field: str, context: str) -> Any:
    if field not in data:
        raise SemanticReviewError(f"{context} missing field: {field}")
    return data[field]


def _review_mission_contract(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Project the complete mission semantics without duplicated policy fields.

    ``ResearchSpec.to_dict`` intentionally carries both the derived research view
    and the governor execution policy.  Several values are therefore serialized
    twice.  The reviewer needs every distinct policy fact, but not two copies or
    the application-owned content digests.  The original, unprojected spec remains
    bound into the final attestation by :func:`_final_base_sha256`.
    """

    policy = _required_value(spec, "execution_policy", "ResearchSpec")
    if not isinstance(policy, Mapping):
        raise SemanticReviewError("ResearchSpec.execution_policy must be an object")
    return {
        "task_id": _required_value(spec, "task_id", "ResearchSpec"),
        "mission_id": _required_value(spec, "mission_id", "ResearchSpec"),
        "question": _required_value(spec, "question", "ResearchSpec"),
        "mode": _required_value(spec, "mode", "ResearchSpec"),
        "depth": _required_value(policy, "depth", "execution policy"),
        "error_tolerance": _required_value(
            policy, "error_tolerance", "execution policy"
        ),
        "answer_mode": _required_value(policy, "answer_mode", "execution policy"),
        "priorities": _required_value(spec, "priorities", "ResearchSpec"),
        "scope_boundaries": _required_value(
            spec, "scope_boundaries", "ResearchSpec"
        ),
        "execution_source_policy": _required_value(
            policy, "source_policy", "execution policy"
        ),
        "research_source_policy": _required_value(
            spec, "source_policy", "ResearchSpec"
        ),
        "allowed_source_classes": _required_value(
            policy, "allowed_source_classes", "execution policy"
        ),
        "prohibited_source_classes": _required_value(
            policy, "prohibited_source_classes", "execution policy"
        ),
        "constraints": _required_value(policy, "constraints", "execution policy"),
        "language_policy": _required_value(spec, "language_policy", "ResearchSpec"),
        "success_conditions": _required_value(
            spec, "success_conditions", "ResearchSpec"
        ),
        "output_requirements": _required_value(
            policy, "output_requirements", "execution policy"
        ),
        "uncertainty_policy": _required_value(
            spec, "uncertainty_policy", "ResearchSpec"
        ),
        "escalation_conditions": _required_value(
            policy, "escalation_conditions", "execution policy"
        ),
        "clarification_turns": _required_value(
            spec, "clarification_turns", "ResearchSpec"
        ),
        "hypotheses": _required_value(spec, "hypotheses", "ResearchSpec"),
    }


def _compact_coverage_candidates(
    candidates: Sequence[ReviewCoverageCandidate],
    *,
    snapshots: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project typed, source-verified candidates without normalizing excerpts."""

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, ReviewCoverageCandidate):
            raise TypeError("review_pass_coverage_candidates must be typed candidates")
        candidate = item.candidate
        if candidate.id in seen:
            raise SemanticReviewError("duplicate review-pass coverage candidate")
        seen.add(candidate.id)
        snapshot = snapshots.get(candidate.snapshot_id)
        if snapshot is None:
            raise SemanticReviewError(
                "review-pass coverage candidate references an unknown snapshot"
            )
        normalized_bytes = item.normalized_text.encode("utf-8")
        if (
            len(normalized_bytes) != snapshot.normalized_size
            or hashlib.sha256(normalized_bytes).hexdigest()
            != snapshot.normalized_sha256
        ):
            raise SemanticReviewError(
                "review-pass coverage candidate normalized source binding changed"
            )
        expected_id = "extract-" + canonical_json_sha256(
            {
                "schema": "research-reader-candidate-v1",
                "source_snapshot": snapshot.to_dict(),
                "start_char": candidate.start_char,
                "end_char": candidate.end_char,
                "excerpt": candidate.excerpt,
            },
            "reader candidate identity",
        )
        if candidate.id != expected_id:
            raise SemanticReviewError(
                "review-pass coverage candidate content identity changed"
            )
        result.append(
            {
                "id": candidate.id,
                "coverage_role": item.coverage_role,
                "source_id": candidate.snapshot_id,
                "start_char": candidate.start_char,
                "end_char": candidate.end_char,
                "excerpt_as_untrusted_data": candidate.excerpt,
            }
        )
    return result


def _bind_semantic_review_payload(
    payload: Mapping[str, Any],
    *,
    require_existing_binding: bool,
) -> dict[str, Any]:
    data = parse_json_object(dict(payload))
    trusted = data.get("trusted_review_context")
    if not isinstance(trusted, Mapping):
        raise SemanticReviewError("semantic payload lacks trusted review context")
    trusted_copy = dict(trusted)
    expected_keys = {"review_attestation_manifest", "review_provenance"}
    if require_existing_binding:
        expected_keys |= {"projection_schema", "expected_model_payload_sha256"}
    if set(trusted_copy) != expected_keys:
        raise SemanticReviewError("semantic trusted review context fields changed")
    existing_schema = trusted_copy.pop("projection_schema", None)
    existing_digest = trusted_copy.pop("expected_model_payload_sha256", None)
    visible = {
        key: value for key, value in data.items() if key != "trusted_review_context"
    }
    visible_digest = canonical_json_sha256(
        visible, _MODEL_REQUEST_DIGEST_CONTEXT
    )
    if require_existing_binding and (
        existing_schema != _PROJECTION_SCHEMA or existing_digest != visible_digest
    ):
        raise SemanticReviewError("semantic model projection changed before repair")
    trusted_copy["projection_schema"] = _PROJECTION_SCHEMA
    trusted_copy["expected_model_payload_sha256"] = visible_digest
    return {**visible, "trusted_review_context": trusted_copy}


def build_semantic_review_repair_payload(
    payload: Mapping[str, Any], repair_request: Mapping[str, Any]
) -> dict[str, Any]:
    """Add an app-owned repair overlay and bind the exact new visible request."""

    rebound = _bind_semantic_review_payload(
        payload, require_existing_binding=True
    )
    if "repair_request" in rebound:
        raise SemanticReviewError("semantic review payload already contains repair_request")
    if not isinstance(repair_request, Mapping):
        raise TypeError("semantic review repair_request must be a mapping")
    trusted = dict(rebound["trusted_review_context"])
    trusted.pop("projection_schema")
    trusted.pop("expected_model_payload_sha256")
    rebound["trusted_review_context"] = trusted
    rebound["repair_request"] = parse_json_object(dict(repair_request))
    return _bind_semantic_review_payload(
        rebound, require_existing_binding=False
    )


def _final_base_sha256(
    spec_payload: Mapping[str, Any],
    spec_sha256: str,
    ledger_before_review: EvidenceLedger,
    draft_units: Sequence[Mapping[str, Any]],
) -> str:
    return canonical_json_sha256(
        {
            "attestation_schema": "research-final-base-v1",
            "research_spec": dict(spec_payload),
            "research_spec_sha256": spec_sha256,
            "ledger_before_review": ledger_before_review.to_dict(),
            "draft_units": [dict(item) for item in draft_units],
        },
        "final semantic review base",
    )


@dataclass(frozen=True, slots=True)
class SemanticReviewRecord:
    round_number: int
    reviewer_id: str
    decision: str
    reason: str
    claim_statuses: tuple[tuple[str, str], ...]
    edge_statuses: tuple[tuple[str, str], ...]
    unit_statuses: tuple[tuple[str, str], ...]
    mission_alignment: str
    scope_alignment: str
    policy_alignment: str
    spec_sha256: str
    review_request_sha256: str
    review_response_sha256: str
    final_base_sha256: str
    next_queries: tuple[str, ...]
    attestations: tuple[ReviewAttestation, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_number": self.round_number,
            "reviewer_id": self.reviewer_id,
            "decision": self.decision,
            "reason": self.reason,
            "claim_statuses": dict(self.claim_statuses),
            "edge_statuses": dict(self.edge_statuses),
            "unit_statuses": dict(self.unit_statuses),
            "mission_alignment": self.mission_alignment,
            "scope_alignment": self.scope_alignment,
            "policy_alignment": self.policy_alignment,
            "spec_sha256": self.spec_sha256,
            "review_request_sha256": self.review_request_sha256,
            "review_response_sha256": self.review_response_sha256,
            "final_base_sha256": self.final_base_sha256,
            "next_queries": list(self.next_queries),
            "attestations": [
                {
                    "subject_kind": item.subject_kind,
                    "subject_id": item.subject_id,
                    "reviewer_id": item.reviewer_id,
                    "subject_sha256": item.subject_sha256,
                }
                for item in self.attestations
            ],
        }


def build_semantic_review_payload(
    *,
    task_id: str,
    spec_payload: Mapping[str, Any],
    ledger: EvidenceLedger,
    draft_units: Sequence[Mapping[str, Any]],
    review_pass_coverage_candidates: Sequence[ReviewCoverageCandidate],
    round_number: int,
    author_identity: str,
    reviewer_model_identity: str,
    author_model_authority_identity: str,
    reviewer_model_authority_identity: str,
    review_assurance_mode: str,
    reviewer_identity: str,
) -> dict[str, Any]:
    """Build the immutable reviewer request and all allowed attestation variants."""

    if not isinstance(ledger, EvidenceLedger):
        raise TypeError("ledger must be an EvidenceLedger")
    if type(task_id) is not str or not task_id:
        raise TypeError("task_id must be a non-empty string")
    if type(round_number) is not int or round_number < 1:
        raise ValueError("round_number must be a positive integer")
    author_identity = _identifier(author_identity, "author identity")
    reviewer_model_identity = _identifier(
        reviewer_model_identity, "review model identity"
    )
    author_model_authority_identity = _identifier(
        author_model_authority_identity, "author model authority identity"
    )
    reviewer_model_authority_identity = _identifier(
        reviewer_model_authority_identity, "reviewer model authority identity"
    )
    reviewer_identity = _identifier(reviewer_identity, "reviewer identity")
    if review_assurance_mode != "same_model_context_isolated":
        raise SemanticReviewError("semantic review assurance mode is unsupported")
    shares_model_authority = (
        author_model_authority_identity == reviewer_model_authority_identity
    )
    if not shares_model_authority:
        raise SemanticReviewError(
            "same-model semantic review requires one shared model authority"
        )
    if author_identity == reviewer_identity:
        raise SemanticReviewError("review role authority must differ from author identity")

    spec = _strict_spec(spec_payload)
    units = _strict_units(draft_units)
    if isinstance(review_pass_coverage_candidates, (str, bytes)) or not isinstance(
        review_pass_coverage_candidates, Sequence
    ):
        raise TypeError("review_pass_coverage_candidates must be a sequence")
    spans = {span.id: span for span in ledger.spans}
    snapshots = {snapshot.id: snapshot for snapshot in ledger.snapshots}
    claims = {claim.id: claim for claim in ledger.claims}
    coverage_candidates = _compact_coverage_candidates(
        review_pass_coverage_candidates,
        snapshots=snapshots,
    )
    inference_by_claim = {
        inference.conclusion_claim_id: inference for inference in ledger.inferences
    }
    spec_sha256 = canonical_json_sha256(spec, "ResearchSpec")

    claim_manifest: dict[str, dict[str, str]] = {}
    for claim in ledger.claims:
        claim_manifest[claim.id] = {}
        for status in ("entailed", "not_entailed", "uncertain", "contested"):
            reviewed = replace(
                claim,
                verification_status=status,
                verified_by=reviewer_identity,
            )
            claim_manifest[claim.id][status] = claim_review_digest(
                reviewed, inference_by_claim.get(claim.id)
            )

    edge_manifest: dict[str, dict[str, str]] = {}
    for edge in ledger.edges:
        edge_manifest[edge.id] = {}
        span = spans[edge.span_id]
        for status in ("entailed", "not_entailed", "uncertain"):
            reviewed = replace(
                edge,
                entailment_status=status,
                assessed_by=reviewer_identity,
            )
            edge_manifest[edge.id][status] = edge_review_digest(
                reviewed,
                claims[edge.claim_id],
                span,
                snapshots[span.snapshot_id],
            )

    final_id = f"final-review-{round_number}"
    final_base_sha256 = _final_base_sha256(spec, spec_sha256, ledger, units)
    trusted_context = {
        "review_attestation_manifest": {
            "claims": claim_manifest,
            "edges": edge_manifest,
            "final": {
                "subject_id": final_id,
                "base_sha256": final_base_sha256,
            },
        },
        "review_provenance": {
            "author_generation_contract_identity": author_identity,
            "review_generation_contract_identity": reviewer_model_identity,
            "author_model_authority_identity": author_model_authority_identity,
            "review_model_authority_identity": reviewer_model_authority_identity,
            "review_authority_identity": reviewer_identity,
            "assurance_mode": review_assurance_mode,
            "fresh_stateless_context_required": True,
            "author_role_output_hidden_except_immutable_review_payload": True,
            "separate_physical_model": not shares_model_authority,
            "epistemic_independence_claimed": False,
        },
    }
    payload = {
        "task_id": task_id,
        "round": round_number,
        "mission_contract": _review_mission_contract(spec),
        "claims": [
            {
                "id": claim.id,
                "text": claim.text,
                "kind": claim.kind,
                "importance": claim.importance,
                "confidence": claim.confidence,
                "conflict_claim_ids": list(claim.conflict_claim_ids),
            }
            for claim in ledger.claims
        ],
        "evidence_graph": {
            "sources": [
                {
                    "id": snapshot.id,
                    "uri": snapshot.uri,
                    "medium": snapshot.medium,
                    "normalized_sha256": snapshot.normalized_sha256,
                    "source_class": snapshot.source_class,
                }
                for snapshot in ledger.snapshots
            ],
            "spans": [
                {
                    "id": span.id,
                    "source_id": span.snapshot_id,
                    "locator": span.locator.to_dict(),
                    "excerpt_as_untrusted_data": span.excerpt,
                }
                for span in ledger.spans
            ],
            "edges": [
                {
                    "id": edge.id,
                    "claim_id": edge.claim_id,
                    "span_id": edge.span_id,
                    "relation": edge.relation,
                }
                for edge in ledger.edges
            ],
        },
        "inferences": [
            {
                "id": item.id,
                "conclusion_claim_id": item.conclusion_claim_id,
                "premise_claim_ids": list(item.premise_claim_ids),
                "rationale": item.rationale,
            }
            for item in ledger.inferences
        ],
        "gaps": [item.to_dict() for item in ledger.gaps],
        "draft_units": [dict(item) for item in units],
        "review_material_candidates": coverage_candidates,
        "coverage_requirements": {
            "supplied_source_chunks_completely_scanned": True,
            "every_material_candidate_must_be_ledger_accounted": True,
            "check_corrections_conflicts_and_qualifications": True,
            "absolute_semantic_completeness_claimed": False,
        },
        "response_contract": {
            "exact_fields": [
                "decision",
                "reason",
                "claim_reviews",
                "edge_reviews",
                "unit_reviews",
                "mission_alignment",
                "scope_alignment",
                "policy_alignment",
                "next_queries",
            ],
            "unknown_fields_forbidden": True,
            "decision_enum": ["accepted", "search_more", "blocked"],
            "review_items": {
                "claim_reviews": {
                    "exact_fields": ["claim_id", "status"],
                    "ids_from": "claims.id",
                    "status_enum": [
                        "entailed",
                        "not_entailed",
                        "uncertain",
                        "contested",
                    ],
                },
                "edge_reviews": {
                    "exact_fields": ["edge_id", "status"],
                    "ids_from": "evidence_graph.edges.id",
                    "status_enum": ["entailed", "not_entailed", "uncertain"],
                },
                "unit_reviews": {
                    "exact_fields": ["unit_id", "status"],
                    "ids_from": "draft_units.id",
                    "status_enum": ["entailed", "not_entailed", "uncertain"],
                },
            },
            "alignment_status_enum": ["entailed", "not_entailed", "uncertain"],
            "accepted_requires": (
                "exactly one entailed review per claim, edge, and unit; mission, "
                "scope, and policy alignments all entailed"
            ),
            "next_queries_rule": (
                "non-empty only for search_more; [] for accepted or blocked"
            ),
        },
        "trusted_review_context": trusted_context,
    }
    return _bind_semantic_review_payload(
        payload, require_existing_binding=False
    )


def _parse_review_items(
    value: Any,
    entity: str,
    valid_ids: set[str],
    statuses: frozenset[str],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for index, raw_item in enumerate(_array(value, f"{entity}_reviews"), 1):
        data = _mapping(
            raw_item,
            f"{entity}_reviews[{index}]",
            required=frozenset({f"{entity}_id", "status"}),
        )
        entity_id = _identifier(data[f"{entity}_id"], f"{entity}_id")
        if entity_id not in valid_ids:
            raise SemanticReviewError(
                f"semantic review references unknown {entity} {entity_id}"
            )
        if entity_id in result:
            raise SemanticReviewError(f"semantic review duplicates {entity} {entity_id}")
        result[entity_id] = _choice(
            data["status"], statuses, f"{entity} review status"
        )
    return result


def apply_semantic_review(
    *,
    spec_payload: Mapping[str, Any],
    ledger: EvidenceLedger,
    draft_units: Sequence[Mapping[str, Any]],
    session: ReviewSession,
    boundary: TrustedReviewBoundary,
    reviewer_identity: str,
    round_number: int,
) -> tuple[EvidenceLedger, SemanticReviewRecord]:
    """Parse one review and consume its exact manifest-derived attestations."""

    if not isinstance(ledger, EvidenceLedger):
        raise TypeError("ledger must be an EvidenceLedger")
    if not isinstance(session, ReviewSession):
        raise TypeError("semantic review requires a ReviewSession")
    if not isinstance(boundary, TrustedReviewBoundary):
        raise TypeError("boundary must be a TrustedReviewBoundary")
    reviewer_identity = _identifier(reviewer_identity, "reviewer identity")
    if reviewer_identity != boundary.authority_id:
        raise SemanticReviewError("semantic review authority identity changed")
    if type(round_number) is not int or round_number < 1:
        raise ValueError("round_number must be a positive integer")

    spec = _strict_spec(spec_payload)
    units = _strict_units(draft_units)
    raw = session.response()
    data = _mapping(
        raw,
        "semantic verifier response",
        required=frozenset(
            {
                "decision",
                "reason",
                "claim_reviews",
                "edge_reviews",
                "unit_reviews",
                "mission_alignment",
                "scope_alignment",
                "policy_alignment",
                "next_queries",
            }
        ),
    )
    decision = _choice(
        data["decision"],
        frozenset({"accepted", "search_more", "blocked"}),
        "semantic verifier decision",
    )
    reason = _nonempty(data["reason"], "semantic verifier reason")
    next_queries = _strings(data["next_queries"], "semantic next_queries")
    if decision == "search_more" and not next_queries:
        raise SemanticReviewError(
            "semantic search_more requires at least one next query"
        )
    if decision != "search_more" and next_queries:
        raise SemanticReviewError(
            "semantic accepted/blocked decisions require next_queries=[]"
        )
    mission_alignment = _choice(
        data["mission_alignment"], _ALIGNMENTS, "mission_alignment"
    )
    scope_alignment = _choice(
        data["scope_alignment"], _ALIGNMENTS, "scope_alignment"
    )
    policy_alignment = _choice(
        data["policy_alignment"], _ALIGNMENTS, "policy_alignment"
    )
    if decision == "accepted" and {
        mission_alignment,
        scope_alignment,
        policy_alignment,
    } != {"entailed"}:
        raise SemanticReviewError(
            "accepted semantic review requires entailed mission, scope, and policy alignment"
        )

    claim_statuses = _parse_review_items(
        data["claim_reviews"],
        "claim",
        {claim.id for claim in ledger.claims},
        frozenset({"entailed", "not_entailed", "uncertain", "contested"}),
    )
    edge_statuses = _parse_review_items(
        data["edge_reviews"],
        "edge",
        {edge.id for edge in ledger.edges},
        frozenset({"entailed", "not_entailed", "uncertain"}),
    )
    unit_statuses = _parse_review_items(
        data["unit_reviews"],
        "unit",
        {_identifier(item["id"], "draft unit ID") for item in units},
        frozenset({"entailed", "not_entailed", "uncertain"}),
    )
    if decision == "accepted":
        expected_claim_ids = {claim.id for claim in ledger.claims}
        expected_edge_ids = {edge.id for edge in ledger.edges}
        expected_unit_ids = {
            _identifier(item["id"], "draft unit ID") for item in units
        }
        if set(claim_statuses) != expected_claim_ids:
            raise SemanticReviewError(
                "accepted semantic review must cover every claim exactly once"
            )
        if set(edge_statuses) != expected_edge_ids:
            raise SemanticReviewError(
                "accepted semantic review must cover every evidence edge exactly once"
            )
        if set(unit_statuses) != expected_unit_ids:
            raise SemanticReviewError(
                "accepted semantic review must cover every draft unit exactly once"
            )
        if any(status != "entailed" for status in claim_statuses.values()):
            raise SemanticReviewError(
                "accepted semantic review requires every claim status=entailed"
            )
        if any(status != "entailed" for status in edge_statuses.values()):
            raise SemanticReviewError(
                "accepted semantic review requires every edge status=entailed"
            )
        if any(status != "entailed" for status in unit_statuses.values()):
            raise SemanticReviewError(
                "accepted semantic review requires every unit status=entailed"
            )

    reviewed_claims = tuple(
        replace(
            claim,
            verification_status=claim_statuses[claim.id],
            verified_by=reviewer_identity,
        )
        if claim.id in claim_statuses
        else claim
        for claim in ledger.claims
    )
    reviewed_edges = tuple(
        replace(
            edge,
            entailment_status=edge_statuses[edge.id],
            assessed_by=reviewer_identity,
        )
        if edge.id in edge_statuses
        else edge
        for edge in ledger.edges
    )
    reviewed_ledger = EvidenceLedger(
        schema_version=ledger.schema_version,
        snapshots=ledger.snapshots,
        spans=ledger.spans,
        claims=reviewed_claims,
        edges=reviewed_edges,
        inferences=ledger.inferences,
        gaps=ledger.gaps,
        hypotheses=ledger.hypotheses,
    )

    claims_by_id = {claim.id: claim for claim in reviewed_ledger.claims}
    inference_by_claim = {
        inference.conclusion_claim_id: inference
        for inference in reviewed_ledger.inferences
    }
    spans_by_id = {span.id: span for span in reviewed_ledger.spans}
    snapshots_by_id = {
        snapshot.id: snapshot for snapshot in reviewed_ledger.snapshots
    }
    expected_attestations: dict[tuple[str, str], str] = {}
    for claim in reviewed_ledger.claims:
        if claim.id in claim_statuses:
            expected_attestations[("claim", claim.id)] = claim_review_digest(
                claim, inference_by_claim.get(claim.id)
            )
    for edge in reviewed_ledger.edges:
        if edge.id not in edge_statuses:
            continue
        span = spans_by_id[edge.span_id]
        expected_attestations[("edge", edge.id)] = edge_review_digest(
            edge,
            claims_by_id[edge.claim_id],
            span,
            snapshots_by_id[span.snapshot_id],
        )

    spec_sha256 = canonical_json_sha256(spec, "ResearchSpec")
    final_id = f"final-review-{round_number}"
    final_base_sha256 = _final_base_sha256(spec, spec_sha256, ledger, units)
    expected_attestations[("final", final_id)] = final_review_attestation_digest(
        request_sha256=session.request_sha256,
        response_sha256=session.response_sha256,
        base_sha256=final_base_sha256,
    )
    attestations = boundary.issue_attestations(session)
    actual_attestations = {
        (item.subject_kind, item.subject_id): item.subject_sha256
        for item in attestations
        if item.reviewer_id == reviewer_identity
    }
    if actual_attestations != expected_attestations:
        raise SemanticReviewError(
            "review boundary attestation set does not match reviewed request subjects"
        )

    return reviewed_ledger, SemanticReviewRecord(
        round_number=round_number,
        reviewer_id=reviewer_identity,
        decision=decision,
        reason=reason,
        claim_statuses=tuple(sorted(claim_statuses.items())),
        edge_statuses=tuple(sorted(edge_statuses.items())),
        unit_statuses=tuple(sorted(unit_statuses.items())),
        mission_alignment=mission_alignment,
        scope_alignment=scope_alignment,
        policy_alignment=policy_alignment,
        spec_sha256=spec_sha256,
        review_request_sha256=session.request_sha256,
        review_response_sha256=session.response_sha256,
        final_base_sha256=final_base_sha256,
        next_queries=next_queries,
        attestations=attestations,
    )


def require_complete_semantic_acceptance(
    *,
    spec_payload: Mapping[str, Any],
    ledger: EvidenceLedger,
    draft_units: Sequence[Mapping[str, Any]],
    review: SemanticReviewRecord,
    reviewer_identity: str,
) -> None:
    """Require exact, complete semantic coverage before deterministic verification."""

    if not isinstance(ledger, EvidenceLedger):
        raise TypeError("ledger must be an EvidenceLedger")
    if not isinstance(review, SemanticReviewRecord):
        raise TypeError("review must be a SemanticReviewRecord")
    reviewer_identity = _identifier(reviewer_identity, "reviewer identity")
    spec = _strict_spec(spec_payload)
    units = _strict_units(draft_units)
    claim_statuses = dict(review.claim_statuses)
    edge_statuses = dict(review.edge_statuses)
    unit_statuses = dict(review.unit_statuses)

    if review.reviewer_id != reviewer_identity:
        raise SemanticReviewError("semantic review authority identity changed")
    if (
        review.mission_alignment != "entailed"
        or review.scope_alignment != "entailed"
        or review.policy_alignment != "entailed"
    ):
        raise SemanticReviewError(
            "accepted review lacks mission/scope/policy alignment"
        )
    if review.spec_sha256 != canonical_json_sha256(spec, "ResearchSpec"):
        raise SemanticReviewError(
            "semantic review is bound to a different ResearchSpec"
        )
    if set(claim_statuses) != {claim.id for claim in ledger.claims}:
        raise SemanticReviewError(
            "accepted semantic review must cover every claim"
        )
    if set(edge_statuses) != {edge.id for edge in ledger.edges}:
        raise SemanticReviewError(
            "accepted semantic review must cover every evidence edge"
        )
    if set(unit_statuses) != {item["id"] for item in units}:
        raise SemanticReviewError(
            "accepted semantic review must cover every draft unit"
        )
    if any(status != "entailed" for status in unit_statuses.values()):
        raise SemanticReviewError(
            "accepted semantic review contains an unaligned draft unit"
        )

    final_id = f"final-review-{review.round_number}"
    expected_final_digest = final_review_attestation_digest(
        request_sha256=review.review_request_sha256,
        response_sha256=review.review_response_sha256,
        base_sha256=review.final_base_sha256,
    )
    if not any(
        item.subject_kind == "final"
        and item.subject_id == final_id
        and item.reviewer_id == reviewer_identity
        and item.subject_sha256 == expected_final_digest
        for item in review.attestations
    ):
        raise SemanticReviewError(
            "accepted semantic review lacks an exact content-bound final attestation"
        )
    for claim in ledger.claims:
        if claim.importance == "major" and claim.verification_status != "entailed":
            raise SemanticReviewError(
                f"major claim {claim.id} was not review-entailed"
            )
        if claim.kind == "assumption" and claim.importance == "major":
            raise SemanticReviewError("a major assumption cannot be accepted")


__all__ = [
    "ReviewCoverageCandidate",
    "SemanticReviewError",
    "SemanticReviewRecord",
    "apply_semantic_review",
    "build_semantic_review_payload",
    "build_semantic_review_repair_payload",
    "require_complete_semantic_acceptance",
]
