from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest

from ResearchWarband import (
    SCHEMA_VERSION,
    Claim,
    EpubLocator,
    EvidenceEdge,
    EvidenceLedger,
    EvidenceVerifier,
    Fb2Locator,
    Gap,
    HtmlLocator,
    Hypothesis,
    Inference,
    PdfLocator,
    RegisteredNormalizer,
    ReviewAttestation,
    SchemaError,
    SnapshotByteLimitError,
    SnapshotIntegrityError,
    SnapshotNormalizationError,
    SnapshotSecurityError,
    SnapshotStore,
    UnknownNormalizerError,
    SourceSpan,
    TextLocator,
    claim_review_digest,
    edge_review_digest,
)


FETCHED_AT = "2026-07-12T00:00:00+00:00"
NORMALIZER_ID = "normalizer-v1"


def trusted_test_normalizer(raw: bytes, medium: str) -> str:
    decoded = raw.decode("utf-8")
    return decoded[4:] if decoded.startswith("raw:") else decoded


class EvidenceCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.normalizer = RegisteredNormalizer(
            id=NORMALIZER_ID,
            media=frozenset({"text", "html", "pdf", "epub", "fb2"}),
            callback=trusted_test_normalizer,
        )
        self.store = SnapshotStore(
            self.root / "store", normalizers=(self.normalizer,)
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def put_snapshot(
        self,
        *,
        snapshot_id: str = "source-1",
        medium: str = "text",
        normalized: str = "Alpha evidence confirms the event.",
    ):
        return self.store.put(
            snapshot_id=snapshot_id,
            uri=f"https://example.test/{snapshot_id}",
            fetched_at=FETCHED_AT,
            medium=medium,
            raw=("raw:" + normalized).encode("utf-8"),
            normalized=normalized,
            normalizer_version=NORMALIZER_ID,
        )

    def verifier_for(
        self,
        ledger: EvidenceLedger,
        *,
        trusted_reviewer_ids: set[str] | None = None,
        include_attestations: bool = True,
    ) -> EvidenceVerifier:
        trusted = trusted_reviewer_ids or {
            "semantic-verifier",
            "inference-verifier",
            "second-verifier",
        }
        attestations = self.attestations_for(ledger) if include_attestations else []
        return EvidenceVerifier(
            self.store,
            trusted_reviewer_ids=trusted,
            attestations=attestations,
        )

    @staticmethod
    def attestations_for(ledger: EvidenceLedger) -> list[ReviewAttestation]:
        attestations: list[ReviewAttestation] = []
        snapshots = {item.id: item for item in ledger.snapshots}
        spans = {item.id: item for item in ledger.spans}
        claims = {item.id: item for item in ledger.claims}
        inferences = {
            item.conclusion_claim_id: item for item in ledger.inferences
        }
        for claim in ledger.claims:
            if claim.verified_by is not None:
                inference = inferences.get(claim.id)
                if claim.kind == "inference" and inference is None:
                    continue
                attestations.append(
                    ReviewAttestation(
                        subject_kind="claim",
                        subject_id=claim.id,
                        reviewer_id=claim.verified_by,
                        subject_sha256=claim_review_digest(
                            claim, inference
                        ),
                    )
                )
        for edge in ledger.edges:
            if edge.assessed_by is not None:
                span = spans[edge.span_id]
                attestations.append(
                    ReviewAttestation(
                        subject_kind="edge",
                        subject_id=edge.id,
                        reviewer_id=edge.assessed_by,
                        subject_sha256=edge_review_digest(
                            edge,
                            claims[edge.claim_id],
                            span,
                            snapshots[span.snapshot_id],
                        ),
                    )
                )
        return attestations

    @staticmethod
    def reviewed_claim(
        *,
        claim_id: str = "claim-1",
        kind: str = "source_assertion",
        importance: str = "major",
        status: str = "entailed",
        authored_by: str = "writer-agent",
        verified_by: str | None = "semantic-verifier",
    ) -> Claim:
        return Claim(
            id=claim_id,
            text=f"Text for {claim_id}",
            kind=kind,
            importance=importance,
            verification_status=status,
            authored_by=authored_by,
            verified_by=verified_by,
            confidence="high",
            conflict_claim_ids=(),
        )

    @staticmethod
    def ledger(
        *,
        snapshots=(),
        spans=(),
        claims=(),
        edges=(),
        inferences=(),
        gaps=(),
        hypotheses=(),
    ) -> EvidenceLedger:
        return EvidenceLedger(
            schema_version=SCHEMA_VERSION,
            snapshots=tuple(snapshots),
            spans=tuple(spans),
            claims=tuple(claims),
            edges=tuple(edges),
            inferences=tuple(inferences),
            gaps=tuple(gaps),
            hypotheses=tuple(hypotheses),
        )

    def supported_fixture(self):
        snapshot = self.put_snapshot()
        span = SourceSpan(
            id="span-1",
            snapshot_id=snapshot.id,
            locator=TextLocator(6, 14),
            excerpt="evidence",
        )
        claim = self.reviewed_claim()
        edge = EvidenceEdge(
            id="edge-1",
            claim_id=claim.id,
            span_id=span.id,
            relation="supports",
            entailment_status="entailed",
            assessed_by="semantic-verifier",
        )
        ledger = self.ledger(
            snapshots=(snapshot,), spans=(span,), claims=(claim,), edges=(edge,)
        )
        return ledger, snapshot, span, claim, edge

    def test_happy_path_verifies_provenance_but_not_truth(self) -> None:
        ledger, *_ = self.supported_fixture()
        report = self.verifier_for(ledger).verify(ledger)
        self.assertTrue(report.accepted)
        self.assertTrue(report.integrity_ok)
        self.assertTrue(report.major_claims_supported)
        self.assertFalse(report.truth_assessed)
        self.assertIn("truth", report.scope)
        self.assertEqual([], report.to_dict()["issues"])

    def test_round_trip_preserves_strict_ledger(self) -> None:
        ledger, *_ = self.supported_fixture()
        self.assertEqual(ledger, EvidenceLedger.from_dict(ledger.to_dict()))

    def test_all_medium_specific_locators_extract_exact_quotes(self) -> None:
        cases = (
            ("text", "before quote after", TextLocator(7, 12), "quote"),
            ("html", "before quote after", HtmlLocator("main p", 7, 12), "quote"),
            ("pdf", "first page\fsecond quote page", PdfLocator(2, 7, 12), "quote"),
            (
                "epub",
                "first spine\fchapter quote text",
                EpubLocator(1, "chapter.xhtml", 8, 13),
                "quote",
            ),
            (
                "fb2",
                "paragraph zero\n\nnext quote here\fother section",
                Fb2Locator(0, 1, 5, 10),
                "quote",
            ),
        )
        for index, (medium, normalized, locator, excerpt) in enumerate(cases):
            with self.subTest(medium=medium):
                snapshot = self.put_snapshot(
                    snapshot_id=f"source-{index}",
                    medium=medium,
                    normalized=normalized,
                )
                span = SourceSpan(
                    id=f"span-{index}",
                    snapshot_id=snapshot.id,
                    locator=locator,
                    excerpt=excerpt,
                )
                claim = self.reviewed_claim(claim_id=f"claim-{index}")
                edge = EvidenceEdge(
                    id=f"edge-{index}",
                    claim_id=claim.id,
                    span_id=span.id,
                    relation="supports",
                    entailment_status="entailed",
                    assessed_by="semantic-verifier",
                )
                ledger = self.ledger(
                    snapshots=(snapshot,),
                    spans=(span,),
                    claims=(claim,),
                    edges=(edge,),
                )
                self.assertTrue(self.verifier_for(ledger).verify(ledger).accepted)

    def test_every_schema_rejects_unknown_and_missing_fields(self) -> None:
        ledger, snapshot, span, claim, edge = self.supported_fixture()
        inference = Inference(
            id="inference-1",
            conclusion_claim_id="claim-2",
            premise_claim_ids=(claim.id,),
            rationale="Premise entails conclusion under the stated rule.",
            authored_by="analyst-agent",
        )
        gap = Gap(
            id="gap-1",
            question="What evidence is still unavailable?",
            status="open",
            related_claim_ids=(claim.id,),
            search_attempts=("catalog query 1",),
        )
        hypothesis = Hypothesis(
            id="hypothesis-1",
            text="A competing interpretation.",
            status="proposed",
            supporting_claim_ids=(claim.id,),
            challenging_claim_ids=(),
            gap_ids=(gap.id,),
        )
        objects_and_parsers = (
            (snapshot, type(snapshot).from_dict),
            (span, SourceSpan.from_dict),
            (claim, Claim.from_dict),
            (edge, EvidenceEdge.from_dict),
            (inference, Inference.from_dict),
            (gap, Gap.from_dict),
            (hypothesis, Hypothesis.from_dict),
            (ledger, EvidenceLedger.from_dict),
            (TextLocator(0, 1), TextLocator.from_dict),
        )
        for obj, parser in objects_and_parsers:
            with self.subTest(schema=type(obj).__name__, error="unknown"):
                payload = dict(obj.to_dict())
                payload["unexpected"] = True
                with self.assertRaises(SchemaError):
                    parser(payload)
            with self.subTest(schema=type(obj).__name__, error="missing"):
                payload = dict(obj.to_dict())
                payload.pop(next(iter(payload)))
                with self.assertRaises(SchemaError):
                    parser(payload)

    def test_programmatic_schema_is_strict_about_tuple_fields(self) -> None:
        with self.assertRaises(SchemaError):
            Claim(
                id="claim-1",
                text="claim",
                kind="assumption",
                importance="minor",
                verification_status="unverified",
                authored_by="writer",
                verified_by=None,
                confidence="low",
                conflict_claim_ids=[],  # type: ignore[arg-type]
            )

    def test_medium_mismatch_is_rejected(self) -> None:
        snapshot = self.put_snapshot(medium="text")
        span = SourceSpan(
            id="span-1",
            snapshot_id=snapshot.id,
            locator=HtmlLocator("p", 0, 1),
            excerpt="A",
        )
        with self.assertRaisesRegex(SchemaError, "medium"):
            self.ledger(snapshots=(snapshot,), spans=(span,))

    def test_missing_snapshot_claim_span_and_premise_refs_are_rejected(self) -> None:
        snapshot = self.put_snapshot()
        missing_snapshot_span = SourceSpan(
            id="span-missing", snapshot_id="source-missing", locator=TextLocator(0, 1), excerpt="A"
        )
        with self.assertRaisesRegex(SchemaError, "missing snapshot"):
            self.ledger(snapshots=(snapshot,), spans=(missing_snapshot_span,))

        span = SourceSpan(
            id="span-1", snapshot_id=snapshot.id, locator=TextLocator(0, 1), excerpt="A"
        )
        edge_missing_claim = EvidenceEdge(
            id="edge-1",
            claim_id="claim-missing",
            span_id=span.id,
            relation="supports",
            entailment_status="unreviewed",
            assessed_by=None,
        )
        with self.assertRaisesRegex(SchemaError, "missing claim"):
            self.ledger(
                snapshots=(snapshot,), spans=(span,), edges=(edge_missing_claim,)
            )

        claim = self.reviewed_claim(claim_id="claim-conclusion", kind="inference")
        inference = Inference(
            id="inference-1",
            conclusion_claim_id=claim.id,
            premise_claim_ids=("claim-missing",),
            rationale="rationale",
            authored_by="analyst",
        )
        with self.assertRaisesRegex(SchemaError, "missing premises"):
            self.ledger(claims=(claim,), inferences=(inference,))

    def test_missing_hypothesis_refs_and_inference_cycles_are_rejected(self) -> None:
        hypothesis = Hypothesis(
            id="hypothesis-1",
            text="hypothesis",
            status="proposed",
            supporting_claim_ids=(),
            challenging_claim_ids=(),
            gap_ids=("gap-missing",),
        )
        with self.assertRaisesRegex(SchemaError, "missing gap"):
            self.ledger(hypotheses=(hypothesis,))

        claim_a = self.reviewed_claim(claim_id="claim-a", kind="inference")
        claim_b = self.reviewed_claim(claim_id="claim-b", kind="inference")
        inference_a = Inference(
            id="inference-a",
            conclusion_claim_id=claim_a.id,
            premise_claim_ids=(claim_b.id,),
            rationale="a from b",
            authored_by="analyst",
        )
        inference_b = Inference(
            id="inference-b",
            conclusion_claim_id=claim_b.id,
            premise_claim_ids=(claim_a.id,),
            rationale="b from a",
            authored_by="analyst",
        )
        with self.assertRaisesRegex(SchemaError, "cycle"):
            self.ledger(
                claims=(claim_a, claim_b),
                inferences=(inference_a, inference_b),
            )

    def test_normalized_text_is_derived_and_cannot_launder_raw_bytes(self) -> None:
        with self.assertRaises(SnapshotNormalizationError):
            self.store.put(
                snapshot_id="source-malicious",
                uri="https://example.test/malicious",
                fetched_at=FETCHED_AT,
                medium="text",
                raw=b"NO",
                normalized="YES",
                normalizer_version=NORMALIZER_ID,
            )

        no_snapshot = self.store.put(
            snapshot_id="source-no",
            uri="https://example.test/no",
            fetched_at=FETCHED_AT,
            medium="text",
            raw=b"NO",
            normalizer_version=NORMALIZER_ID,
        )
        yes_snapshot = self.store.put(
            snapshot_id="source-yes",
            uri="https://example.test/yes",
            fetched_at=FETCHED_AT,
            medium="text",
            raw=b"YES",
            normalizer_version=NORMALIZER_ID,
        )
        self.assertEqual("NO", self.store.read_normalized(no_snapshot))

        laundered = replace(
            no_snapshot,
            normalized_sha256=yes_snapshot.normalized_sha256,
            normalized_size=yes_snapshot.normalized_size,
            normalized_path=yes_snapshot.normalized_path,
        )
        with self.assertRaises(SnapshotIntegrityError):
            self.store.verify(laundered)

    def test_unknown_or_wrong_medium_normalizer_fails_closed(self) -> None:
        with self.assertRaises(UnknownNormalizerError):
            self.store.put(
                snapshot_id="source-unknown",
                uri="https://example.test/unknown",
                fetched_at=FETCHED_AT,
                medium="text",
                raw=b"content",
                normalizer_version="not-registered",
            )
        text_only = RegisteredNormalizer(
            id="text-only-v1",
            media=frozenset({"text"}),
            callback=trusted_test_normalizer,
        )
        store = SnapshotStore(self.root / "text-only", normalizers=(text_only,))
        with self.assertRaises(UnknownNormalizerError):
            store.put(
                snapshot_id="source-html",
                uri="https://example.test/html",
                fetched_at=FETCHED_AT,
                medium="html",
                raw=b"content",
                normalizer_version="text-only-v1",
            )

    def test_content_addressing_deduplicates_and_leaves_no_temp_files(self) -> None:
        first = self.put_snapshot(snapshot_id="source-1")
        second = self.put_snapshot(snapshot_id="source-2")
        self.assertEqual(first.raw_sha256, second.raw_sha256)
        self.assertEqual(first.normalized_sha256, second.normalized_sha256)
        self.assertEqual(first.raw_path, second.raw_path)
        self.assertEqual(first.normalized_path, second.normalized_path)
        self.assertFalse(list(self.store.root.rglob(".snapshot-*")))

    def test_raw_and_normalized_byte_limits_are_enforced(self) -> None:
        raw_limited = SnapshotStore(
            self.root / "raw-limited",
            max_raw_bytes=3,
            max_normalized_bytes=20,
            normalizers=(self.normalizer,),
        )
        with self.assertRaises(SnapshotByteLimitError):
            raw_limited.put(
                snapshot_id="source-1",
                uri="https://example.test",
                fetched_at=FETCHED_AT,
                medium="text",
                raw=b"four",
                normalizer_version=NORMALIZER_ID,
            )
        normalized_limited = SnapshotStore(
            self.root / "normalized-limited",
            max_raw_bytes=20,
            max_normalized_bytes=3,
            normalizers=(self.normalizer,),
        )
        with self.assertRaises(SnapshotByteLimitError):
            normalized_limited.put(
                snapshot_id="source-1",
                uri="https://example.test",
                fetched_at=FETCHED_AT,
                medium="text",
                raw="éé".encode("utf-8"),
                normalizer_version=NORMALIZER_ID,
            )

    def test_traversal_and_windows_style_escape_are_rejected(self) -> None:
        for path in ("../escape", "objects/raw/../../escape", "..\\escape", "/escape"):
            with self.subTest(path=path):
                with self.assertRaises(SnapshotSecurityError):
                    self.store.object_path(path)

    def test_snapshot_schema_rejects_tampered_content_address_path(self) -> None:
        snapshot = self.put_snapshot()
        with self.assertRaises(SchemaError):
            replace(snapshot, raw_path="../outside")
        alternate = replace(snapshot, raw_path="objects/raw/aa/" + "0" * 64 + ".bin")
        with self.assertRaises(SnapshotSecurityError):
            self.store.verify(alternate)

    def test_symlinked_object_is_rejected(self) -> None:
        snapshot = self.put_snapshot()
        raw_path = self.store.object_path(snapshot.raw_path)
        outside = self.root / "outside.bin"
        outside.write_bytes(b"outside")
        raw_path.unlink()
        try:
            os.symlink(outside, raw_path)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaises(SnapshotSecurityError):
            self.store.verify(snapshot)

    def test_hash_tamper_is_detected(self) -> None:
        snapshot = self.put_snapshot()
        self.store.object_path(snapshot.raw_path).write_bytes(b"tampered")
        with self.assertRaises(SnapshotIntegrityError):
            self.store.verify(snapshot)

    def test_quote_mismatch_and_locator_range_fail_integrity(self) -> None:
        ledger, snapshot, span, claim, edge = self.supported_fixture()
        wrong_span = replace(span, excerpt="fabricated")
        wrong_ledger = replace(ledger, spans=(wrong_span,))
        report = self.verifier_for(wrong_ledger).verify(wrong_ledger)
        self.assertFalse(report.accepted)
        self.assertFalse(report.integrity_ok)
        self.assertIn("quote_mismatch", {issue.code for issue in report.issues})

        out_of_range = replace(span, locator=TextLocator(6, 10_000))
        range_ledger = replace(ledger, spans=(out_of_range,))
        range_report = self.verifier_for(range_ledger).verify(range_ledger)
        self.assertIn(
            "locator_out_of_range", {issue.code for issue in range_report.issues}
        )

    def test_unreviewed_and_self_reviewed_major_claims_are_rejected(self) -> None:
        unreviewed = self.reviewed_claim(
            status="unverified", verified_by=None, kind="source_assertion"
        )
        unreviewed_ledger = self.ledger(claims=(unreviewed,))
        report = self.verifier_for(unreviewed_ledger).verify(unreviewed_ledger)
        codes = {issue.code for issue in report.issues}
        self.assertIn("major_entailment_missing", codes)
        self.assertIn("unsupported_major_claim", codes)

        ledger, snapshot, span, _, _ = self.supported_fixture()
        self_reviewed = self.reviewed_claim(
            authored_by="same-agent", verified_by="same-agent"
        )
        self_edge = EvidenceEdge(
            id="edge-self",
            claim_id=self_reviewed.id,
            span_id=span.id,
            relation="supports",
            entailment_status="entailed",
            assessed_by="same-agent",
        )
        self_ledger = self.ledger(
            snapshots=(snapshot,),
            spans=(span,),
            claims=(self_reviewed,),
            edges=(self_edge,),
        )
        codes = {
            issue.code for issue in self.verifier_for(self_ledger).verify(self_ledger).issues
        }
        self.assertIn("major_entailment_not_independent", codes)
        self.assertIn("unsupported_major_claim", codes)

    def test_empty_minor_only_and_assumption_ledgers_are_not_eligible(self) -> None:
        empty = self.ledger()
        empty_report = EvidenceVerifier(self.store).verify(empty)
        self.assertFalse(empty_report.accepted)
        self.assertFalse(empty_report.eligible_for_acceptance)
        self.assertIn("empty_ledger", {issue.code for issue in empty_report.issues})
        self.assertIn("no_major_claim", {issue.code for issue in empty_report.issues})

        ledger, snapshot, span, claim, edge = self.supported_fixture()
        minor = replace(claim, importance="minor")
        minor_ledger = self.ledger(
            snapshots=(snapshot,), spans=(span,), claims=(minor,), edges=(edge,)
        )
        minor_report = self.verifier_for(minor_ledger).verify(minor_ledger)
        self.assertFalse(minor_report.accepted)
        self.assertFalse(minor_report.eligible_for_acceptance)
        self.assertIn("no_major_claim", {issue.code for issue in minor_report.issues})

        assumption = self.reviewed_claim(
            claim_id="claim-assumption",
            kind="assumption",
            importance="minor",
        )
        assumption_ledger = replace(ledger, claims=(claim, assumption))
        assumption_report = self.verifier_for(assumption_ledger).verify(
            assumption_ledger
        )
        self.assertFalse(assumption_report.accepted)
        self.assertFalse(assumption_report.eligible_for_acceptance)
        self.assertIn(
            "unacceptable_assumption",
            {issue.code for issue in assumption_report.issues},
        )

    def test_minor_unverified_factual_claim_blocks_an_otherwise_good_answer(self) -> None:
        ledger, snapshot, span, major, edge = self.supported_fixture()
        minor = self.reviewed_claim(
            claim_id="claim-minor",
            importance="minor",
            status="unverified",
            verified_by=None,
        )
        mixed = replace(ledger, claims=(major, minor))
        report = self.verifier_for(mixed).verify(mixed)
        self.assertFalse(report.accepted)
        self.assertTrue(report.eligible_for_acceptance)
        self.assertIn(
            "unsupported_factual_claim", {issue.code for issue in report.issues}
        )

    def test_reviewer_names_fail_closed_without_trust_and_attestation(self) -> None:
        ledger, snapshot, span, claim, edge = self.supported_fixture()
        no_context = EvidenceVerifier(self.store).verify(ledger)
        self.assertFalse(no_context.accepted)
        self.assertIn(
            "unsupported_factual_claim", {issue.code for issue in no_context.issues}
        )

        fake_claim = replace(claim, verified_by="invented-reviewer")
        fake_edge = replace(edge, assessed_by="invented-reviewer")
        fake_ledger = self.ledger(
            snapshots=(snapshot,),
            spans=(span,),
            claims=(fake_claim,),
            edges=(fake_edge,),
        )
        fake_context = EvidenceVerifier(
            self.store,
            trusted_reviewer_ids={"semantic-verifier"},
            attestations=self.attestations_for(fake_ledger),
        ).verify(fake_ledger)
        self.assertFalse(fake_context.accepted)
        self.assertIn(
            "major_entailment_not_independent",
            {issue.code for issue in fake_context.issues},
        )

        missing_attestations = EvidenceVerifier(
            self.store,
            trusted_reviewer_ids={"semantic-verifier"},
        ).verify(ledger)
        self.assertFalse(missing_attestations.accepted)

    def test_attestations_are_bound_to_exact_claim_edge_span_and_snapshot(self) -> None:
        ledger, snapshot, span, claim, edge = self.supported_fixture()
        original_attestations = self.attestations_for(ledger)
        changed_claim = replace(claim, text="Silently changed after semantic review")
        changed_ledger = replace(ledger, claims=(changed_claim,))
        report = EvidenceVerifier(
            self.store,
            trusted_reviewer_ids={"semantic-verifier"},
            attestations=original_attestations,
        ).verify(changed_ledger)
        self.assertFalse(report.accepted)
        self.assertIn(
            "unsupported_factual_claim", {issue.code for issue in report.issues}
        )

        changed_span = replace(span, excerpt="fabricated")
        changed_span_ledger = replace(ledger, spans=(changed_span,))
        span_report = EvidenceVerifier(
            self.store,
            trusted_reviewer_ids={"semantic-verifier"},
            attestations=original_attestations,
        ).verify(changed_span_ledger)
        self.assertFalse(span_report.accepted)
        self.assertIn("quote_mismatch", {issue.code for issue in span_report.issues})

    def test_inference_reviewer_must_differ_from_inference_author(self) -> None:
        _, snapshot, span, premise, edge = self.supported_fixture()
        premise = replace(premise, importance="minor")
        conclusion = self.reviewed_claim(
            claim_id="claim-conclusion",
            kind="inference",
            authored_by="claim-writer",
            verified_by="analyst-agent",
        )
        inference = Inference(
            id="inference-1",
            conclusion_claim_id=conclusion.id,
            premise_claim_ids=(premise.id,),
            rationale="Inference author must not attest their own conclusion.",
            authored_by="analyst-agent",
        )
        ledger = self.ledger(
            snapshots=(snapshot,),
            spans=(span,),
            claims=(premise, conclusion),
            edges=(edge,),
            inferences=(inference,),
        )
        report = EvidenceVerifier(
            self.store,
            trusted_reviewer_ids={"semantic-verifier", "analyst-agent"},
            attestations=self.attestations_for(ledger),
        ).verify(ledger)
        self.assertFalse(report.accepted)
        self.assertIn(
            "major_entailment_not_independent",
            {issue.code for issue in report.issues},
        )

    def test_semantically_unreviewed_edge_does_not_support_major_claim(self) -> None:
        ledger, snapshot, span, claim, _ = self.supported_fixture()
        edge = EvidenceEdge(
            id="edge-unreviewed",
            claim_id=claim.id,
            span_id=span.id,
            relation="supports",
            entailment_status="unreviewed",
            assessed_by=None,
        )
        unreviewed_edge_ledger = self.ledger(
            snapshots=(snapshot,), spans=(span,), claims=(claim,), edges=(edge,)
        )
        report = self.verifier_for(unreviewed_edge_ledger).verify(
            unreviewed_edge_ledger
        )
        self.assertIn(
            "unsupported_major_claim", {issue.code for issue in report.issues}
        )

    def test_supported_inference_uses_independently_supported_premises(self) -> None:
        _, snapshot, span, premise, edge = self.supported_fixture()
        premise = replace(premise, importance="minor")
        conclusion = self.reviewed_claim(
            claim_id="claim-conclusion",
            kind="inference",
            authored_by="analyst-agent",
            verified_by="inference-verifier",
        )
        inference = Inference(
            id="inference-1",
            conclusion_claim_id=conclusion.id,
            premise_claim_ids=(premise.id,),
            rationale="The conclusion follows from the recorded premise.",
            authored_by="analyst-agent",
        )
        ledger = self.ledger(
            snapshots=(snapshot,),
            spans=(span,),
            claims=(premise, conclusion),
            edges=(edge,),
            inferences=(inference,),
        )
        self.assertTrue(self.verifier_for(ledger).verify(ledger).accepted)

    def test_unsupported_inference_premise_and_missing_inference_fail(self) -> None:
        premise = self.reviewed_claim(
            claim_id="claim-premise",
            importance="minor",
            status="unverified",
            verified_by=None,
        )
        conclusion = self.reviewed_claim(
            claim_id="claim-conclusion", kind="inference"
        )
        inference = Inference(
            id="inference-1",
            conclusion_claim_id=conclusion.id,
            premise_claim_ids=(premise.id,),
            rationale="Unsupported premise should block this inference.",
            authored_by="analyst-agent",
        )
        unsupported_ledger = self.ledger(
            claims=(premise, conclusion), inferences=(inference,)
        )
        report = self.verifier_for(unsupported_ledger).verify(unsupported_ledger)
        codes = {issue.code for issue in report.issues}
        self.assertIn("unsupported_inference_premise", codes)
        self.assertIn("unsupported_major_claim", codes)

        missing_ledger = self.ledger(claims=(conclusion,))
        missing = self.verifier_for(missing_ledger).verify(missing_ledger)
        self.assertIn("missing_inference", {issue.code for issue in missing.issues})

    def test_independently_entailed_refutation_blocks_acceptance(self) -> None:
        ledger, snapshot, span, claim, support = self.supported_fixture()
        refutation = EvidenceEdge(
            id="edge-refute",
            claim_id=claim.id,
            span_id=span.id,
            relation="refutes",
            entailment_status="entailed",
            assessed_by="second-verifier",
        )
        refuted_ledger = replace(ledger, edges=(support, refutation))
        report = self.verifier_for(refuted_ledger).verify(refuted_ledger)
        self.assertIn("unresolved_refutation", {issue.code for issue in report.issues})
        self.assertFalse(report.accepted)


if __name__ == "__main__":
    unittest.main()
