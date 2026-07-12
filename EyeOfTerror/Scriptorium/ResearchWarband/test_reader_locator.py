from __future__ import annotations

import hashlib
import unittest

from ResearchWarband.reader import (
    ReaderProtocolError,
    build_independent_reader_payload,
    build_reader_payload,
    parse_independent_reader_response,
    parse_reader_response,
)
from ResearchWarband.schema import SourceSnapshot


def _snapshot(text: str, *, snapshot_id: str = "snapshot-1") -> SourceSnapshot:
    payload = text.encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return SourceSnapshot(
        id=snapshot_id,
        uri="https://example.test/source",
        fetched_at="2026-07-12T00:00:00+00:00",
        medium="text",
        raw_sha256=digest,
        normalized_sha256=digest,
        raw_size=len(payload),
        normalized_size=len(payload),
        raw_path=f"raw/{digest}",
        normalized_path=f"normalized/{digest}",
        normalizer_version="reader-locator-test-v1",
        source_class="official_documentation",
        source_classifier_id="reader-locator-test-classifier-v1",
    )


def _payload(
    text: str,
    *,
    chunk_start: int = 0,
    chunk_end: int | None = None,
    cache_key: str = "reader-cache-author-test",
) -> tuple[SourceSnapshot, dict[str, object]]:
    snapshot = _snapshot(text)
    end = len(text) if chunk_end is None else chunk_end
    return snapshot, build_reader_payload(
        task_id="reader-locator-test",
        spec_payload={},
        snapshot=snapshot,
        normalized_text=text,
        chunk_start=chunk_start,
        chunk_end=end,
        chunk_index=1,
        chunk_count=1,
        cache_key=cache_key,
    )


def _candidate(chunk_id: str, excerpt: str) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "excerpt": excerpt,
        "relevance": "high",
        "reason": "the exact excerpt is material to the research question",
    }


class ReaderLocatorTests(unittest.TestCase):
    def test_engine_resolves_unique_excerpt_to_absolute_offsets_without_trimming(self) -> None:
        prefix = "outside::"
        excerpt = "  Alpha evidence.  "
        suffix = "::outside"
        text = prefix + excerpt + suffix
        snapshot, payload = _payload(
            text,
            chunk_start=len(prefix),
            chunk_end=len(prefix) + len(excerpt),
        )
        chunk = payload["untrusted_source_chunk"]

        candidates = parse_reader_response(
            {"candidates": [_candidate(chunk["chunk_id"], excerpt)]},
            snapshot=snapshot,
            normalized_text=text,
            chunk_start=len(prefix),
            chunk_end=len(prefix) + len(excerpt),
            chunk_index=1,
            cache_key="reader-cache-author-test",
            model_identity="reader-author-test",
        )

        self.assertEqual(1, len(candidates))
        self.assertEqual(len(prefix), candidates[0].start_char)
        self.assertEqual(len(prefix) + len(excerpt), candidates[0].end_char)
        self.assertEqual(excerpt, candidates[0].excerpt)
        self.assertEqual(excerpt, text[candidates[0].start_char : candidates[0].end_char])

    def test_repeated_excerpt_is_ambiguous_even_when_occurrences_overlap(self) -> None:
        text = "aaa"
        snapshot, payload = _payload(text)
        chunk = payload["untrusted_source_chunk"]

        with self.assertRaisesRegex(ReaderProtocolError, "ambiguous within its labeled chunk"):
            parse_reader_response(
                {"candidates": [_candidate(chunk["chunk_id"], "aa")]},
                snapshot=snapshot,
                normalized_text=text,
                chunk_start=0,
                chunk_end=len(text),
                chunk_index=1,
                cache_key="reader-cache-author-test",
                model_identity="reader-author-test",
            )

    def test_excerpt_absent_from_labeled_chunk_is_rejected(self) -> None:
        text = "Alpha evidence."
        snapshot, payload = _payload(text)
        chunk = payload["untrusted_source_chunk"]

        with self.assertRaisesRegex(ReaderProtocolError, "does not occur exactly"):
            parse_reader_response(
                {"candidates": [_candidate(chunk["chunk_id"], "Omega evidence.")]},
                snapshot=snapshot,
                normalized_text=text,
                chunk_start=0,
                chunk_end=len(text),
                chunk_index=1,
                cache_key="reader-cache-author-test",
                model_identity="reader-author-test",
            )

    def test_candidate_cannot_target_a_different_chunk(self) -> None:
        text = "Alpha evidence."
        snapshot, _payload_value = _payload(text)

        with self.assertRaisesRegex(ReaderProtocolError, "chunk_id does not match"):
            parse_reader_response(
                {"candidates": [_candidate("reader-chunk-wrong", text)]},
                snapshot=snapshot,
                normalized_text=text,
                chunk_start=0,
                chunk_end=len(text),
                chunk_index=1,
                cache_key="reader-cache-author-test",
                model_identity="reader-author-test",
            )

    def test_legacy_model_offsets_are_not_accepted(self) -> None:
        text = "Alpha evidence."
        snapshot, payload = _payload(text)
        chunk = payload["untrusted_source_chunk"]
        legacy = {
            **_candidate(chunk["chunk_id"], text),
            "start_char": 0,
            "end_char": len(text),
        }

        with self.assertRaisesRegex(ReaderProtocolError, "exact contract"):
            parse_reader_response(
                {"candidates": [legacy]},
                snapshot=snapshot,
                normalized_text=text,
                chunk_start=0,
                chunk_end=len(text),
                chunk_index=1,
                cache_key="reader-cache-author-test",
                model_identity="reader-author-test",
            )

    def test_independent_reader_uses_same_chunk_identity_and_trusted_resolver(self) -> None:
        text = "Earlier report. Later correction: launch was 2021."
        excerpt = "Later correction: launch was 2021."
        snapshot, author_payload = _payload(text)
        independent_payload = build_independent_reader_payload(
            task_id="reader-locator-test",
            spec_payload={},
            snapshot=snapshot,
            normalized_text=text,
            chunk_start=0,
            chunk_end=len(text),
            chunk_index=1,
            chunk_count=1,
            cache_key="reader-cache-reviewer-test",
        )
        author_chunk = author_payload["untrusted_source_chunk"]
        independent_chunk = independent_payload["untrusted_source_chunk"]
        self.assertEqual(author_chunk["chunk_id"], independent_chunk["chunk_id"])

        response = _candidate(independent_chunk["chunk_id"], excerpt)
        response["coverage_role"] = "counterevidence"
        selected = parse_independent_reader_response(
            {"candidates": [response]},
            snapshot=snapshot,
            normalized_text=text,
            chunk_start=0,
            chunk_end=len(text),
            chunk_index=1,
            cache_key="reader-cache-reviewer-test",
            model_identity="reader-reviewer-test",
        )

        self.assertEqual(1, len(selected))
        candidate, role = selected[0]
        self.assertEqual("counterevidence", role)
        self.assertEqual(text.index(excerpt), candidate.start_char)
        self.assertEqual(text.index(excerpt) + len(excerpt), candidate.end_char)


if __name__ == "__main__":
    unittest.main()
