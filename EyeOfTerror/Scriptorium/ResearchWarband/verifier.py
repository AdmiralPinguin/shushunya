"""Deterministic provenance verifier for an :class:`EvidenceLedger`.

This module verifies immutable bytes, locators, exact excerpts, references, and
the presence of application-attested semantic-review decisions. It deliberately
does not decide whether a source or claim is true and contains no model or HTTP
calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Iterable

from .schema import (
    Claim,
    EpubLocator,
    EvidenceEdge,
    EvidenceLedger,
    Fb2Locator,
    HtmlLocator,
    Inference,
    PdfLocator,
    SourceSnapshot,
    SourceSpan,
    TextLocator,
)
from .snapshot_store import SnapshotStore, SnapshotStoreError


@dataclass(frozen=True, slots=True)
class VerificationIssue:
    code: str
    entity_id: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "entity_id": self.entity_id,
            "message": self.message,
        }


_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ReviewAttestation:
    """Trusted external statement that a reviewer assessed exact content.

    Attestations are supplied by the orchestrator, never read from the ledger.
    ``subject_sha256`` binds the decision to canonical claim or edge/span/source
    content so changing a quote, relation, status, or snapshot invalidates it.
    """

    subject_kind: str
    subject_id: str
    reviewer_id: str
    subject_sha256: str

    def __post_init__(self) -> None:
        if self.subject_kind not in {"claim", "edge", "final"}:
            raise ValueError("attestation subject_kind must be claim, edge, or final")
        for field_name in ("subject_id", "reviewer_id"):
            value = getattr(self, field_name)
            if type(value) is not str or not value.strip():
                raise ValueError(f"attestation {field_name} must be non-empty")
        if type(self.subject_sha256) is not str or not _DIGEST_RE.fullmatch(
            self.subject_sha256
        ):
            raise ValueError("attestation subject_sha256 must be lowercase SHA256")


def _canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def claim_review_digest(
    claim: Claim, inference: Inference | None = None
) -> str:
    """Digest a claim decision and, for inference claims, its exact derivation."""

    if not isinstance(claim, Claim):
        raise TypeError("claim must be a Claim")
    if claim.kind == "inference":
        if not isinstance(inference, Inference):
            raise TypeError("inference claims require their Inference record")
        if inference.conclusion_claim_id != claim.id:
            raise ValueError("Inference record does not conclude the supplied claim")
    elif inference is not None:
        raise ValueError("non-inference claim must not include an Inference record")
    return _canonical_digest(
        {
            "attestation_schema": "claim-semantic-v1",
            "claim": claim.to_dict(),
            "inference": inference.to_dict() if inference is not None else None,
        }
    )


def edge_review_digest(
    edge: EvidenceEdge,
    claim: Claim,
    span: SourceSpan,
    snapshot: SourceSnapshot,
) -> str:
    """Digest an edge together with exact claim, span, and snapshot identity."""

    if not isinstance(edge, EvidenceEdge):
        raise TypeError("edge must be an EvidenceEdge")
    if not isinstance(claim, Claim):
        raise TypeError("claim must be a Claim")
    if not isinstance(span, SourceSpan):
        raise TypeError("span must be a SourceSpan")
    if not isinstance(snapshot, SourceSnapshot):
        raise TypeError("snapshot must be a SourceSnapshot")
    claim_content = claim.to_dict()
    # Claim review status has its own attestation. Edge entailment binds the
    # proposition and provenance content, not a second copy of mutable review
    # metadata; this also lets a review boundary predeclare exact edge variants.
    claim_content.pop("verification_status", None)
    claim_content.pop("verified_by", None)
    return _canonical_digest(
        {
            "attestation_schema": "edge-semantic-v1",
            "edge": edge.to_dict(),
            "claim": claim_content,
            "span": span.to_dict(),
            "snapshot": snapshot.to_dict(),
        }
    )


@dataclass(frozen=True, slots=True)
class VerificationReport:
    accepted: bool
    eligible_for_acceptance: bool
    integrity_ok: bool
    major_claims_supported: bool
    issues: tuple[VerificationIssue, ...]
    truth_assessed: bool = field(default=False, init=False)
    scope: str = field(
        default=(
            "provenance integrity and recorded semantic entailment only; "
            "source reliability and real-world truth are not established"
        ),
        init=False,
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "eligible_for_acceptance": self.eligible_for_acceptance,
            "integrity_ok": self.integrity_ok,
            "major_claims_supported": self.major_claims_supported,
            "truth_assessed": self.truth_assessed,
            "scope": self.scope,
            "issues": [issue.to_dict() for issue in self.issues],
        }


_INTEGRITY_CODES = frozenset(
    {"snapshot_integrity", "locator_out_of_range", "quote_mismatch"}
)
_MAJOR_CODES = frozenset(
    {
        "major_entailment_missing",
        "major_entailment_review_untrusted",
        "unsupported_major_claim",
        "unresolved_refutation",
        "missing_inference",
        "unsupported_inference_premise",
        "no_major_claim",
        "unacceptable_assumption",
        "unresolved_claim_conflict",
        "unresolved_research_gap",
    }
)


# These values explicitly mean that offsets address the trusted canonical
# normalized representation.  They are not claims about a live DOM or an EPUB
# package path.  Rich source-native locator maps need a separate, parser-bound
# artifact before they can participate in acceptance.
CANONICAL_HTML_SELECTOR = "canonical-normalized-document"
CANONICAL_EPUB_HREF_PREFIX = "canonical-normalized-spine:"


class EvidenceVerifier:
    """Run deterministic checks against a ledger and its local snapshot store."""

    def __init__(
        self,
        snapshot_store: SnapshotStore,
        *,
        trusted_reviewer_ids: Iterable[str] = (),
        attestations: Iterable[ReviewAttestation] = (),
    ) -> None:
        if not isinstance(snapshot_store, SnapshotStore):
            raise TypeError("snapshot_store must be a SnapshotStore")
        self.snapshot_store = snapshot_store
        trusted: set[str] = set()
        for reviewer_id in trusted_reviewer_ids:
            if type(reviewer_id) is not str or not reviewer_id.strip():
                raise ValueError("trusted reviewer IDs must be non-empty strings")
            trusted.add(reviewer_id)
        self._trusted_reviewers = frozenset(trusted)
        attestation_keys: set[tuple[str, str, str, str]] = set()
        for attestation in attestations:
            if not isinstance(attestation, ReviewAttestation):
                raise TypeError("attestations must contain ReviewAttestation objects")
            attestation_keys.add(
                (
                    attestation.subject_kind,
                    attestation.subject_id,
                    attestation.reviewer_id,
                    attestation.subject_sha256,
                )
            )
        self._attestations = frozenset(attestation_keys)

    def _is_attested(
        self, *, subject_kind: str, subject_id: str, reviewer_id: str, digest: str
    ) -> bool:
        return (
            subject_kind,
            subject_id,
            reviewer_id,
            digest,
        ) in self._attestations

    @staticmethod
    def _slice(text: str, start: int, end: int) -> str:
        if start < 0 or end <= start or end > len(text):
            raise IndexError("character range is outside normalized text")
        return text[start:end]

    @classmethod
    def _extract_excerpt(cls, span: SourceSpan, normalized: str) -> str:
        locator = span.locator
        if isinstance(locator, TextLocator):
            return cls._slice(normalized, locator.start_char, locator.end_char)
        if isinstance(locator, HtmlLocator):
            if locator.selector != CANONICAL_HTML_SELECTOR:
                raise ValueError(
                    "HTML selector is not the trusted canonical-text sentinel"
                )
            return cls._slice(normalized, locator.start_char, locator.end_char)
        if isinstance(locator, PdfLocator):
            pages = normalized.split("\f")
            index = locator.page - 1
            if index >= len(pages):
                raise IndexError("PDF page is outside normalized snapshot")
            return cls._slice(pages[index], locator.start_char, locator.end_char)
        if isinstance(locator, EpubLocator):
            spine_items = normalized.split("\f")
            if locator.spine_index >= len(spine_items):
                raise IndexError("EPUB spine index is outside normalized snapshot")
            expected_href = f"{CANONICAL_EPUB_HREF_PREFIX}{locator.spine_index}"
            if locator.href != expected_href:
                raise ValueError(
                    "EPUB href is not bound to the canonical normalized spine"
                )
            return cls._slice(
                spine_items[locator.spine_index],
                locator.start_char,
                locator.end_char,
            )
        if isinstance(locator, Fb2Locator):
            sections = normalized.split("\f")
            if locator.section_index >= len(sections):
                raise IndexError("FB2 section index is outside normalized snapshot")
            paragraphs = sections[locator.section_index].split("\n\n")
            if locator.paragraph_index >= len(paragraphs):
                raise IndexError("FB2 paragraph index is outside normalized section")
            return cls._slice(
                paragraphs[locator.paragraph_index],
                locator.start_char,
                locator.end_char,
            )
        raise TypeError("unsupported locator type")

    def verify(self, ledger: EvidenceLedger) -> VerificationReport:
        if not isinstance(ledger, EvidenceLedger):
            raise TypeError("ledger must be an EvidenceLedger")

        issues: list[VerificationIssue] = []
        snapshots = {snapshot.id: snapshot for snapshot in ledger.snapshots}
        spans = {span.id: span for span in ledger.spans}
        claims = {claim.id: claim for claim in ledger.claims}
        edges_by_claim: dict[str, list[EvidenceEdge]] = {
            claim.id: [] for claim in ledger.claims
        }
        for edge in ledger.edges:
            edges_by_claim[edge.claim_id].append(edge)
        inference_by_conclusion: dict[str, Inference] = {
            inference.conclusion_claim_id: inference for inference in ledger.inferences
        }

        if not ledger.claims:
            issues.append(
                VerificationIssue(
                    "empty_ledger",
                    "ledger",
                    "an empty ledger is not eligible for answer acceptance",
                )
            )
        has_acceptable_major = any(
            claim.importance == "major" and claim.kind != "assumption"
            for claim in ledger.claims
        )
        if not has_acceptable_major:
            issues.append(
                VerificationIssue(
                    "no_major_claim",
                    "ledger",
                    "acceptance requires at least one non-assumption major claim",
                )
            )
        for claim in ledger.claims:
            if claim.kind == "assumption":
                issues.append(
                    VerificationIssue(
                        "unacceptable_assumption",
                        claim.id,
                        "assumptions may be recorded as gaps but cannot enter an accepted answer ledger",
                    )
                )

        normalized_by_snapshot: dict[str, str] = {}
        valid_snapshots: set[str] = set()
        for snapshot in ledger.snapshots:
            try:
                self.snapshot_store.verify(snapshot)
                normalized_by_snapshot[snapshot.id] = (
                    self.snapshot_store.read_normalized(snapshot)
                )
                valid_snapshots.add(snapshot.id)
            except SnapshotStoreError as exc:
                issues.append(
                    VerificationIssue(
                        "snapshot_integrity", snapshot.id, str(exc)
                    )
                )

        valid_spans: set[str] = set()
        for span in ledger.spans:
            if span.snapshot_id not in valid_snapshots:
                continue
            normalized = normalized_by_snapshot[span.snapshot_id]
            try:
                actual = self._extract_excerpt(span, normalized)
            except (IndexError, TypeError, ValueError) as exc:
                issues.append(
                    VerificationIssue("locator_out_of_range", span.id, str(exc))
                )
                continue
            if actual != span.excerpt:
                issues.append(
                    VerificationIssue(
                        "quote_mismatch",
                        span.id,
                        "locator text does not exactly equal SourceSpan.excerpt",
                    )
                )
                continue
            valid_spans.add(span.id)

        support_cache: dict[str, bool] = {}
        support_visiting: set[str] = set()

        def claim_review_is_trusted(claim: Claim) -> bool:
            reviewer_id = claim.verified_by
            if claim.verification_status != "entailed" or reviewer_id is None:
                return False
            authors = {claim.authored_by}
            inference = inference_by_conclusion.get(claim.id)
            if claim.kind == "inference" and inference is None:
                return False
            if inference is not None:
                authors.add(inference.authored_by)
            if reviewer_id in authors or reviewer_id not in self._trusted_reviewers:
                return False
            return self._is_attested(
                subject_kind="claim",
                subject_id=claim.id,
                reviewer_id=reviewer_id,
                digest=claim_review_digest(claim, inference),
            )

        def edge_review_is_trusted(edge: EvidenceEdge, claim: Claim) -> bool:
            reviewer_id = edge.assessed_by
            if edge.entailment_status != "entailed" or reviewer_id is None:
                return False
            if reviewer_id == claim.authored_by or reviewer_id not in self._trusted_reviewers:
                return False
            span = spans[edge.span_id]
            snapshot = snapshots[span.snapshot_id]
            return self._is_attested(
                subject_kind="edge",
                subject_id=edge.id,
                reviewer_id=reviewer_id,
                digest=edge_review_digest(edge, claim, span, snapshot),
            )

        def review_attested_support(claim_id: str) -> bool:
            cached = support_cache.get(claim_id)
            if cached is not None:
                return cached
            if claim_id in support_visiting:
                # Cycles are rejected by the ledger schema; keep this defensive.
                return False
            support_visiting.add(claim_id)
            claim = claims[claim_id]
            supported = claim_review_is_trusted(claim)
            if supported and claim.kind in {"source_assertion", "direct_observation"}:
                supported = any(
                    edge.relation == "supports"
                    and edge_review_is_trusted(edge, claim)
                    and edge.span_id in valid_spans
                    for edge in edges_by_claim[claim_id]
                )
            elif supported and claim.kind == "inference":
                inference = inference_by_conclusion.get(claim_id)
                supported = inference is not None and all(
                    review_attested_support(premise_id)
                    for premise_id in inference.premise_claim_ids
                )
            elif claim.kind == "assumption":
                supported = False
            support_visiting.remove(claim_id)
            support_cache[claim_id] = supported
            return supported

        inference_claim_ids = {
            claim.id for claim in ledger.claims if claim.kind == "inference"
        }
        for claim_id in sorted(inference_claim_ids):
            if claim_id not in inference_by_conclusion:
                issues.append(
                    VerificationIssue(
                        "missing_inference",
                        claim_id,
                        "inference claim has no Inference record and premise linkage",
                    )
                )

        for inference in ledger.inferences:
            for premise_id in inference.premise_claim_ids:
                if not review_attested_support(premise_id):
                    issues.append(
                        VerificationIssue(
                            "unsupported_inference_premise",
                            inference.id,
                            f"premise claim {premise_id} lacks review-attested provenance",
                        )
                    )

        for claim_id in sorted(inference_claim_ids):
            if not review_attested_support(claim_id):
                issues.append(
                    VerificationIssue(
                        "unsupported_inference_claim",
                        claim_id,
                        "every inference claim requires attested review and supported premises",
                    )
                )

        for claim in ledger.claims:
            if claim.kind not in {"source_assertion", "direct_observation"}:
                continue
            if not review_attested_support(claim.id):
                issues.append(
                    VerificationIssue(
                        "unsupported_factual_claim",
                        claim.id,
                        "every factual claim, including minor claims, requires trusted attested semantic support",
                    )
                )

        for claim in ledger.claims:
            if claim.importance != "major":
                continue
            if claim.verification_status != "entailed":
                issues.append(
                    VerificationIssue(
                        "major_entailment_missing",
                        claim.id,
                        "major claim lacks a review-attested semantic status of entailed",
                    )
                )
            elif not claim_review_is_trusted(claim):
                issues.append(
                    VerificationIssue(
                        "major_entailment_review_untrusted",
                        claim.id,
                        "major claim review is self-authored, untrusted, or lacks a content-bound attestation",
                    )
                )

            if not review_attested_support(claim.id):
                issues.append(
                    VerificationIssue(
                        "unsupported_major_claim",
                        claim.id,
                        "major claim lacks intact, review-entailed support or premises",
                    )
                )

        for claim in ledger.claims:
            has_review_attested_refutation = any(
                edge.relation == "refutes"
                and edge_review_is_trusted(edge, claim)
                and edge.span_id in valid_spans
                for edge in edges_by_claim[claim.id]
            )
            if claim.verification_status == "entailed" and has_review_attested_refutation:
                issues.append(
                    VerificationIssue(
                        "unresolved_refutation",
                        claim.id,
                        "entailed claim has review-entailed refuting evidence",
                    )
                )

            if claim.conflict_claim_ids:
                issues.append(
                    VerificationIssue(
                        "unresolved_claim_conflict",
                        claim.id,
                        "claim has an explicit unresolved conflict; only a separately verified uncertainty disclosure may publish it",
                    )
                )

        for gap in ledger.gaps:
            if gap.status == "resolved":
                continue
            issues.append(
                VerificationIssue(
                    "unresolved_research_gap",
                    gap.id,
                    "an unresolved research gap requires explicit uncertainty disclosure",
                )
            )

        integrity_ok = not any(issue.code in _INTEGRITY_CODES for issue in issues)
        major_claims_supported = not any(
            issue.code in _MAJOR_CODES for issue in issues
        )
        eligible_for_acceptance = has_acceptable_major and not any(
            claim.kind == "assumption" for claim in ledger.claims
        )
        return VerificationReport(
            accepted=eligible_for_acceptance and not issues,
            eligible_for_acceptance=eligible_for_acceptance,
            integrity_ok=integrity_ok,
            major_claims_supported=major_claims_supported,
            issues=tuple(issues),
        )
