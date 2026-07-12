from __future__ import annotations

from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from typing import Any, Mapping

from ResearchWarband.execution_policy import ExecutionPolicy, ExecutionPolicyError
from ResearchWarband.model_client import (
    LlamaCppChatTokenCounter,
    ModelProtocolError,
    RoutedOpenAIModelClient,
    TokenCount,
    TrustedReviewBoundary,
    VLLMChatTokenCounter,
    parse_json_object,
)
from ResearchWarband.pipeline import (
    ClarificationTurn,
    ResearchBudgets,
    ResearchPipeline,
    ResearchSpec,
)
from ResearchWarband.reader import reader_cache_key
from ResearchWarband.research_tools import (
    AcquisitionError,
    ConfiguredDomainSourceClassifier,
    EyeWebFetchAdapter,
    FetchedSource,
    SearchHit,
    extract_epub_text_bounded,
    resolve_public_addresses,
)
from ResearchWarband.snapshot_store import RegisteredNormalizer, SnapshotStore


def directive(**updates: Any) -> dict[str, Any]:
    payload = {
        "kind": "iskandar_research_directive",
        "version": 1,
        "task_id": "9-task.valid",
        "mission_id": "7-mission.valid:deep",
        "leader": "IskandarKhayon",
        "decision": "delegate",
        "delegated_to": "ResearchWarband",
        "research_objective": "What is supported by the record?",
        "depth": "deep",
        "source_policy": "primary_required",
        "error_tolerance": "strict",
        "answer_mode": "direct_answer",
        "priorities": ["accuracy"],
        "allowed_source_classes": ["official_documentation"],
        "prohibited_source_classes": ["anonymous_or_unverified_web"],
        "constraints": ["public records only"],
        "success_conditions": ["every major claim has exact support"],
        "output_requirements": ["return a direct answer"],
        "escalation_conditions": ["no admissible source"],
        "clarification_question": "",
    }
    payload.update(updates)
    return payload


class StaticModel:
    def __init__(
        self,
        stable_identity: str,
        response: Mapping[str, Any] | None = None,
        independence_identity: str | None = None,
    ) -> None:
        self.stable_identity = stable_identity
        self.independence_identity = independence_identity or stable_identity
        self.response = dict(response or {})
        self.calls = 0

    def preflight(self, role: str, payload: Mapping[str, Any]) -> None:
        del role, payload

    def decide(self, role: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        del role, payload
        self.calls += 1
        return self.response


class StaticTokenCounter:
    stable_identity = "token-counter-test"

    def __init__(self, input_tokens: int, max_model_len: int) -> None:
        self.input_tokens = input_tokens
        self.max_model_len = max_model_len
        self.calls: list[tuple[str, list[dict[str, str]], Mapping[str, Any]]] = []

    def count(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        chat_template_kwargs: Mapping[str, Any],
    ) -> TokenCount:
        self.calls.append((model, messages, dict(chat_template_kwargs)))
        return TokenCount(self.input_tokens, self.max_model_len)


class FakeSocket:
    def __init__(self, peer: str) -> None:
        self.peer = peer

    def getpeername(self) -> tuple[str, int]:
        return self.peer, 80


class FakeResponse:
    def __init__(
        self,
        status: int,
        *,
        headers: list[tuple[str, str]],
        body: bytes = b"",
    ) -> None:
        self.status = status
        self._headers = headers
        self._body = body

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self._headers)

    def read(self, limit: int) -> bytes:
        return self._body[:limit]

    def close(self) -> None:
        pass


class FakeGatewayResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "FakeGatewayResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        del args

    def read(self, limit: int) -> bytes:
        return self.body[:limit]


class FakeConnection:
    def __init__(self, peer: str, response: FakeResponse) -> None:
        self.sock = FakeSocket(peer)
        self.response = response
        self.requests: list[tuple[str, str, Mapping[str, str]]] = []

    def request(self, method: str, path: str, headers: Mapping[str, str]) -> None:
        self.requests.append((method, path, headers))

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        pass


class EmptySearch:
    def search(self, query: str, limit: int) -> tuple[()]:
        del query, limit
        return ()


class EmptyFetch:
    def fetch(self, hit: SearchHit, max_bytes: int) -> FetchedSource:
        del hit, max_bytes
        raise AssertionError("fetch must not run in a constructor-only test")


class SecurityBoundaryTests(unittest.TestCase):
    def test_physical_identity_rejects_aliases_and_distinguishes_gemma_qwen(self) -> None:
        gemma_a = RoutedOpenAIModelClient(
            route="gemma",
            base_url="http://127.0.0.1:8079/v1",
            model="gemma",
            physical_model_identity="google/gemma-physical",
        )
        gemma_b = RoutedOpenAIModelClient(
            route="gemma",
            base_url="http://127.0.0.1:8079/v1",
            model="gemma",
            priority="background",
            max_tokens=2_048,
            physical_model_identity="google/gemma-physical",
        )
        qwen = RoutedOpenAIModelClient(
            route="qwen",
            base_url="http://127.0.0.1:8079/v1",
            model="qwen",
            physical_model_identity="qwen/qwen-physical",
        )
        disguised_gemma = RoutedOpenAIModelClient(
            route="qwen",
            base_url="http://127.0.0.1:9999/v1",
            model="totally-different-alias",
            max_tokens=1_024,
            physical_model_identity="google/gemma-physical",
        )
        self.assertNotEqual(gemma_a.stable_identity, gemma_b.stable_identity)
        self.assertEqual(
            gemma_a.independence_identity, gemma_b.independence_identity
        )
        self.assertEqual(
            gemma_a.independence_identity, disguised_gemma.independence_identity
        )
        self.assertNotEqual(gemma_a.stable_identity, qwen.stable_identity)
        self.assertNotEqual(
            gemma_a.independence_identity, qwen.independence_identity
        )

        with tempfile.TemporaryDirectory() as temporary:
            store = SnapshotStore(
                Path(temporary) / "snapshots",
                normalizers=(
                    RegisteredNormalizer(
                        id="identity-test-v1",
                        media=frozenset({"text"}),
                        callback=lambda raw, medium: raw.decode("utf-8"),
                    ),
                ),
            )
            with self.assertRaisesRegex(ValueError, "different physical/model authorities"):
                ResearchPipeline(
                    author_model=gemma_a,
                    review_boundary=TrustedReviewBoundary(
                        client=gemma_b, authority_id="semantic-verifier"
                    ),
                    search=EmptySearch(),
                    fetch=EmptyFetch(),
                    snapshot_store=store,
                )

            with self.assertRaisesRegex(ValueError, "different physical/model authorities"):
                ResearchPipeline(
                    author_model=gemma_a,
                    review_boundary=TrustedReviewBoundary(
                        client=disguised_gemma, authority_id="semantic-verifier"
                    ),
                    search=EmptySearch(),
                    fetch=EmptyFetch(),
                    snapshot_store=store,
                )

            pipeline = ResearchPipeline(
                author_model=gemma_a,
                review_boundary=TrustedReviewBoundary(
                    client=qwen, authority_id="semantic-verifier"
                ),
                search=EmptySearch(),
                fetch=EmptyFetch(),
                snapshot_store=store,
            )
            self.assertEqual(gemma_a.stable_identity, pipeline.author_identity)
            self.assertEqual(qwen.stable_identity, pipeline.review_boundary.client_identity)
            self.assertEqual(
                qwen.independence_identity,
                pipeline.review_boundary.client_independence_identity,
            )

    def test_directive_round_trip_is_lossless_and_policy_denies_unknown(self) -> None:
        source = directive()
        policy = ExecutionPolicy.from_directive(source)
        self.assertEqual(source, policy.to_directive_dict())
        spec = ResearchSpec.from_directive(source)
        self.assertEqual(policy.directive_sha256, spec.execution_policy.directive_sha256)
        self.assertEqual("lookup", spec.mode)
        turn = ClarificationTurn("Which period?", "2020-2024")
        resumed = ResearchSpec.from_directive(source, clarification_turns=(turn,))
        self.assertEqual(
            [{"question": "Which period?", "answer": "2020-2024"}],
            resumed.to_dict()["clarification_turns"],
        )
        self.assertEqual(
            policy.to_directive_dict(), resumed.execution_policy.to_directive_dict()
        )
        self.assertTrue(policy.allows_source_class("official_documentation"))
        self.assertFalse(policy.allows_source_class("anonymous_or_unverified_web"))
        self.assertFalse(policy.allows_source_class("unknown"))
        self.assertGreater(
            ResearchBudgets.for_depth("deep").max_sources,
            ResearchBudgets.for_depth("brief").max_sources,
        )
        tampered = directive()
        tampered.pop("constraints")
        with self.assertRaises(ExecutionPolicyError):
            ExecutionPolicy.from_directive(tampered)

    def test_context_oversize_fails_before_transport(self) -> None:
        client = RoutedOpenAIModelClient(
            route="gemma",
            base_url="http://127.0.0.1:8079/v1",
            model="gemma",
            max_context_chars=1_000,
        )
        with self.assertRaisesRegex(ModelProtocolError, "silent truncation"):
            client.preflight("planner", {"task_id": "t", "blob": "x" * 5_000})

    def test_exact_token_preflight_binds_generation_messages_and_physical_limit(self) -> None:
        counter = StaticTokenCounter(input_tokens=3_800, max_model_len=7_936)
        client = RoutedOpenAIModelClient(
            route="gemma",
            base_url="http://127.0.0.1:8079/v1",
            model="gemma-alias",
            max_tokens=4_096,
            physical_model_identity="google/gemma-4-31B-it-qat-w4a16-ct",
            attested_max_model_len=7_936,
            token_counter=counter,
        )
        captured: dict[str, Any] = {}

        def generation(request: Any, timeout: float) -> FakeGatewayResponse:
            del timeout
            captured.update(json.loads(request.data.decode("utf-8")))
            return FakeGatewayResponse(
                json.dumps(
                    {"choices": [{"message": {"content": "{}"}}]}
                ).encode("utf-8")
            )

        with patch(
            "ResearchWarband.model_client.urllib.request.urlopen",
            side_effect=generation,
        ):
            self.assertEqual({}, client.decide("reader", {"task_id": "task-1"}))
        self.assertEqual(captured["messages"], counter.calls[0][1])
        self.assertEqual(
            captured["chat_template_kwargs"], counter.calls[0][2]
        )

        different_root = RoutedOpenAIModelClient(
            route="gemma",
            base_url="http://127.0.0.1:8079/v1",
            model="gemma-alias",
            max_tokens=4_096,
            physical_model_identity="different/physical-root",
            attested_max_model_len=7_936,
            token_counter=StaticTokenCounter(3_800, 7_936),
        )
        self.assertNotEqual(client.stable_identity, different_root.stable_identity)

        overflow = RoutedOpenAIModelClient(
            route="gemma",
            base_url="http://127.0.0.1:8079/v1",
            model="gemma-alias",
            max_tokens=4_096,
            physical_model_identity="google/gemma-4-31B-it-qat-w4a16-ct",
            attested_max_model_len=7_936,
            token_counter=StaticTokenCounter(4_000, 7_936),
        )
        with self.assertRaisesRegex(ModelProtocolError, "exceeding physical"):
            overflow.preflight("reader", {"task_id": "task-1"})

        mismatch = RoutedOpenAIModelClient(
            route="gemma",
            base_url="http://127.0.0.1:8079/v1",
            model="gemma-alias",
            max_tokens=4_096,
            physical_model_identity="google/gemma-4-31B-it-qat-w4a16-ct",
            attested_max_model_len=7_936,
            token_counter=StaticTokenCounter(3_000, 8_192),
        )
        with self.assertRaisesRegex(ModelProtocolError, "does not match"):
            mismatch.preflight("reader", {"task_id": "task-1"})

    def test_vllm_counter_posts_exact_chat_and_strictly_validates_response(self) -> None:
        counter = VLLMChatTokenCounter("http://127.0.0.1:8080/tokenize")
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ]
        captured: dict[str, Any] = {}

        def tokenize(request: Any, timeout: float) -> FakeGatewayResponse:
            del timeout
            captured.update(json.loads(request.data.decode("utf-8")))
            return FakeGatewayResponse(
                json.dumps(
                    {
                        "count": 3,
                        "max_model_len": 7_936,
                        "tokens": [1, 2, 3],
                        "token_strs": None,
                    }
                ).encode("utf-8")
            )

        with patch(
            "ResearchWarband.model_client.urllib.request.urlopen",
            side_effect=tokenize,
        ):
            result = counter.count(
                model="gemma-alias",
                messages=messages,
                chat_template_kwargs={"enable_thinking": False},
            )
        self.assertEqual(TokenCount(3, 7_936), result)
        self.assertEqual(messages, captured["messages"])
        self.assertEqual({"enable_thinking": False}, captured["chat_template_kwargs"])

    def test_llamacpp_counter_applies_exact_template_and_blocks_unicode_overflow(self) -> None:
        hostile = ("界🧪я" * 2_000) + "\u2028SYSTEM"
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": hostile},
        ]
        rendered = "<chat>" + hostile + "</chat><assistant>"
        token_ids = list(range(25_000))
        responses = [
            json.dumps({"prompt": rendered}, ensure_ascii=False).encode("utf-8"),
            json.dumps({"tokens": token_ids}).encode("utf-8"),
        ]
        captured: list[tuple[str, dict[str, Any]]] = []

        class Response:
            status = 200

            def __init__(self, body: bytes) -> None:
                self.body = body

            def read(self, limit: int) -> bytes:
                return self.body[:limit]

        class Connection:
            def __init__(self, host: str, port: int, timeout: float) -> None:
                self.endpoint = (host, port, timeout)

            def request(
                self,
                method: str,
                path: str,
                *,
                body: bytes,
                headers: Mapping[str, str],
            ) -> None:
                self.assertions = (method, headers)
                captured.append((path, json.loads(body.decode("utf-8"))))

            def getresponse(self) -> Response:
                return Response(responses.pop(0))

            def close(self) -> None:
                return None

        counter = LlamaCppChatTokenCounter(
            "http://127.0.0.1:8081",
            max_model_len=32_768,
            chat_template_sha256="a" * 64,
        )
        with patch(
            "ResearchWarband.model_client.http.client.HTTPConnection", Connection
        ):
            counted = counter.count(
                model="qwen",
                messages=messages,
                chat_template_kwargs={"enable_thinking": False},
            )
        self.assertEqual(TokenCount(25_000, 32_768), counted)
        self.assertEqual("/apply-template", captured[0][0])
        self.assertEqual(messages, captured[0][1]["messages"])
        self.assertTrue(captured[0][1]["add_generation_prompt"])
        self.assertEqual(
            {"enable_thinking": False}, captured[0][1]["chat_template_kwargs"]
        )
        self.assertEqual(
            ("/tokenize", {"content": rendered, "add_special": True}),
            captured[1],
        )

        overflow_counter = LlamaCppChatTokenCounter(
            "http://127.0.0.1:8081",
            max_model_len=32_768,
            chat_template_sha256="a" * 64,
        )
        overflow = RoutedOpenAIModelClient(
            route="qwen",
            base_url="http://127.0.0.1:8079/v1",
            model="qwen",
            max_tokens=8_192,
            max_context_chars=100_000,
            physical_model_identity="qwen/physical",
            attested_max_model_len=32_768,
            token_counter=overflow_counter,
        )
        responses.extend(
            [
                json.dumps({"prompt": rendered}, ensure_ascii=False).encode("utf-8"),
                json.dumps({"tokens": token_ids}).encode("utf-8"),
            ]
        )
        with patch(
            "ResearchWarband.model_client.http.client.HTTPConnection", Connection
        ), self.assertRaisesRegex(ModelProtocolError, "exceeding physical"):
            overflow.preflight("semantic_verifier", {"task_id": "task-1", "text": hostile})

    def test_llamacpp_counter_is_loopback_strict_and_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "literal-loopback"):
            LlamaCppChatTokenCounter(
                "http://localhost:8081",
                max_model_len=32_768,
                chat_template_sha256="a" * 64,
            )
        counter = LlamaCppChatTokenCounter(
            "http://127.0.0.1:8081",
            max_model_len=32_768,
            chat_template_sha256="a" * 64,
        )
        with self.assertRaisesRegex(ModelProtocolError, "request exceeded"):
            counter.count(
                model="qwen",
                messages=[{"role": "user", "content": "x" * (4 * 1024 * 1024)}],
                chat_template_kwargs={"enable_thinking": False},
            )

        response_bodies = [b'{"prompt":"a","prompt":"b"}']

        class Response:
            status = 200

            def read(self, limit: int) -> bytes:
                return response_bodies.pop(0)[:limit]

        class Connection:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            def request(self, *args: Any, **kwargs: Any) -> None:
                pass

            def getresponse(self) -> Response:
                return Response()

            def close(self) -> None:
                pass

        with patch(
            "ResearchWarband.model_client.http.client.HTTPConnection", Connection
        ), self.assertRaisesRegex(ModelProtocolError, "invalid JSON"):
            counter.count(
                model="qwen",
                messages=[{"role": "user", "content": "hello"}],
                chat_template_kwargs={"enable_thinking": False},
            )

    def test_qwen_timeout_allows_superlong_fifo_waits(self) -> None:
        with patch.dict(os.environ, {"RESEARCH_QWEN_TIMEOUT_SEC": "86400"}):
            qwen = RoutedOpenAIModelClient(
                route="qwen",
                base_url="http://127.0.0.1:8079/v1",
                model="qwen",
            )
        self.assertEqual(86_400.0, qwen.timeout_sec)
        week = RoutedOpenAIModelClient(
            route="qwen",
            base_url="http://127.0.0.1:8079/v1",
            model="qwen",
            timeout_sec=604_800,
        )
        self.assertEqual(604_800.0, week.timeout_sec)
        with self.assertRaisesRegex(ValueError, "604800"):
            RoutedOpenAIModelClient(
                route="qwen",
                base_url="http://127.0.0.1:8079/v1",
                model="qwen",
                timeout_sec=604_801,
            )

    def test_model_json_rejects_duplicate_keys_and_malformed_review_status(self) -> None:
        with self.assertRaisesRegex(ModelProtocolError, "duplicate JSON object key"):
            parse_json_object('{"decision":"accepted","decision":"blocked"}')

        boundary = TrustedReviewBoundary(
            client=StaticModel(
                "review-model",
                {
                    "claim_reviews": [{"claim_id": "claim-1", "status": []}],
                    "edge_reviews": [],
                },
            ),
            authority_id="semantic-verifier",
        )
        request = {
            "review_attestation_manifest": {
                "claims": {"claim-1": {"entailed": "a" * 64}},
                "edges": {},
                "final": {"subject_id": "final-1", "base_sha256": "b" * 64},
            }
        }
        with self.assertRaisesRegex(ModelProtocolError, "status must be a string"):
            boundary.begin(request)

    def test_routed_gateway_rejects_duplicate_envelope_and_role_fields(self) -> None:
        client = RoutedOpenAIModelClient(
            route="gemma",
            base_url="http://127.0.0.1:8079/v1",
            model="gemma",
            physical_model_identity="google/gemma-physical",
        )

        def envelope(content: str) -> bytes:
            return json.dumps(
                {"choices": [{"message": {"content": content}}]}
            ).encode("utf-8")

        duplicate_envelope = (
            b'{"choices":[{"message":{"content":"{}"}}],"choices":[]}'
        )
        with patch(
            "ResearchWarband.model_client.urllib.request.urlopen",
            return_value=FakeGatewayResponse(duplicate_envelope),
        ):
            with self.assertRaisesRegex(ModelProtocolError, "invalid JSON"):
                client.decide("reader", {"task_id": "task-1"})

        role_duplicates = {
            "reader": '{"candidates":[],"candidates":[]}',
            "analyst": '{"decision":"ready","decision":"blocked"}',
        }
        for role, duplicate_content in role_duplicates.items():
            with self.subTest(role=role), patch(
                "ResearchWarband.model_client.urllib.request.urlopen",
                return_value=FakeGatewayResponse(envelope(duplicate_content)),
            ):
                with self.assertRaisesRegex(ModelProtocolError, "duplicate JSON object key"):
                    client.decide(role, {"task_id": "task-1"})

        reviewer = TrustedReviewBoundary(
            client=client,
            authority_id="semantic-verifier",
        )
        semantic_request = {
            "task_id": "task-1",
            "review_attestation_manifest": {
                "claims": {},
                "edges": {},
                "final": {"subject_id": "final-1", "base_sha256": "b" * 64},
            },
        }
        with patch(
            "ResearchWarband.model_client.urllib.request.urlopen",
            return_value=FakeGatewayResponse(
                envelope('{"decision":"accepted","decision":"blocked"}')
            ),
        ):
            with self.assertRaisesRegex(ModelProtocolError, "duplicate JSON object key"):
                reviewer.begin(semantic_request)

    def test_reader_cache_key_binds_content_bounds_spec_policy_and_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = SnapshotStore(
                Path(temporary) / "snapshots",
                normalizers=(
                    RegisteredNormalizer(
                        id="cache-key-normalizer-v1",
                        media=frozenset({"text"}),
                        callback=lambda raw, medium: raw.decode("utf-8"),
                    ),
                ),
            )
            first = store.put(
                snapshot_id="snapshot-1",
                uri="https://example.test/one",
                fetched_at="2026-07-12T00:00:00+00:00",
                medium="text",
                raw=b"Alpha",
                normalized="Alpha",
                normalizer_version="cache-key-normalizer-v1",
                source_class="official_documentation",
                source_classifier_id="classifier-v1",
            )
            second = store.put(
                snapshot_id="snapshot-2",
                uri="https://example.test/two",
                fetched_at="2026-07-12T00:00:00+00:00",
                medium="text",
                raw=b"Alpha",
                normalized="Alpha",
                normalizer_version="cache-key-normalizer-v1",
                source_class="official_documentation",
                source_classifier_id="classifier-v1",
            )

            def key(
                *,
                snapshot=first,
                end: int = 5,
                spec: str = "a" * 64,
                policy: str = "b" * 64,
                model: str = "model-gemma",
            ) -> str:
                return reader_cache_key(
                    snapshot=snapshot,
                    chunk_start=0,
                    chunk_end=end,
                    spec_sha256=spec,
                    policy_sha256=policy,
                    model_identity=model,
                )

            variants = {
                key(),
                key(end=4),
                key(spec="c" * 64),
                key(policy="d" * 64),
                key(model="model-other"),
                key(snapshot=second),
            }
            self.assertEqual(6, len(variants))

            base_contract = RoutedOpenAIModelClient(
                route="gemma",
                base_url="http://127.0.0.1:8079/v1",
                model="gemma",
                max_tokens=4_096,
                physical_model_identity="google/gemma-physical",
            )
            changed_contract = RoutedOpenAIModelClient(
                route="gemma",
                base_url="http://127.0.0.1:8079/v1",
                model="gemma",
                max_tokens=2_048,
                physical_model_identity="google/gemma-physical",
            )
            self.assertEqual(
                base_contract.independence_identity,
                changed_contract.independence_identity,
            )
            self.assertNotEqual(
                key(model=base_contract.stable_identity),
                key(model=changed_contract.stable_identity),
            )

    def test_review_boundary_cannot_attest_unseen_or_changed_subject(self) -> None:
        response = {
            "claim_reviews": [{"claim_id": "claim-1", "status": "entailed"}],
            "edge_reviews": [],
        }
        boundary = TrustedReviewBoundary(
            client=StaticModel("review-model", response),
            authority_id="semantic-verifier",
        )
        missing = {
            "review_attestation_manifest": {
                "claims": {},
                "edges": {},
                "final": {"subject_id": "final-1", "base_sha256": "b" * 64},
            }
        }
        with self.assertRaisesRegex(ModelProtocolError, "not covered"):
            boundary.begin(missing)

        covered = {
            "review_attestation_manifest": {
                "claims": {"claim-1": {"entailed": "a" * 64}},
                "edges": {},
                "final": {"subject_id": "final-1", "base_sha256": "b" * 64},
            }
        }
        session = boundary.begin(covered)
        changed = replace(session, request_sha256="c" * 64)
        with self.assertRaisesRegex(ModelProtocolError, "changed"):
            boundary.issue_attestations(changed)

    def test_domain_classifier_is_label_safe_and_content_identified(self) -> None:
        config = {
            "version": 1,
            "exact": {"docs.official.com": "official_documentation"},
            "suffix": {"official.com": "primary_source"},
        }
        classifier = ConfiguredDomainSourceClassifier(config)
        self.assertEqual(
            "official_documentation",
            classifier.classify(title="", url="https://docs.official.com/x", snippet=""),
        )
        self.assertEqual(
            "primary_source",
            classifier.classify(title="", url="https://sub.official.com/x", snippet=""),
        )
        for host in ("evilofficial.com", "official.com.evil"):
            self.assertEqual(
                "anonymous_or_unverified_web",
                classifier.classify(title="", url=f"https://{host}/", snippet=""),
            )
        changed = ConfiguredDomainSourceClassifier(
            {"version": 1, "exact": {}, "suffix": {}}
        )
        self.assertNotEqual(classifier.stable_identity, changed.stable_identity)
        bad = json.dumps(
            {"version": 1, "exact": {"x.test": "invented"}, "suffix": {}}
        ).encode()
        with self.assertRaises(ValueError):
            ConfiguredDomainSourceClassifier.from_json_bytes(bad)

    def test_pinned_fetch_resolves_once_and_connects_to_validated_ip(self) -> None:
        calls = 0

        def resolver(host: str, port: int, **kwargs: Any):
            nonlocal calls
            del host, port, kwargs
            calls += 1
            address = "93.184.216.34" if calls == 1 else "127.0.0.1"
            return [(2, 1, 6, "", (address, 80))]

        connections: list[tuple[str, str, int, str, float]] = []

        def factory(scheme: str, host: str, port: int, ip: str, timeout: float):
            connections.append((scheme, host, port, ip, timeout))
            return FakeConnection(
                ip,
                FakeResponse(
                    200,
                    headers=[("Content-Type", "text/plain"), ("Content-Length", "5")],
                    body=b"Alpha",
                ),
            )

        classifier = ConfiguredDomainSourceClassifier(
            {"version": 1, "exact": {}, "suffix": {}}
        )
        adapter = EyeWebFetchAdapter(
            classifier=classifier,
            resolver=resolver,
            connection_factory=factory,
        )
        hit = SearchHit(
            "source",
            "http://example.test/data",
            "",
            "anonymous_or_unverified_web",
            classifier.stable_identity,
        )
        result = adapter.fetch(hit, 10_000)
        self.assertEqual(b"Alpha", result.raw)
        self.assertEqual(1, calls)
        self.assertEqual("93.184.216.34", connections[0][3])

    def test_redirect_cannot_carry_official_class_to_attacker(self) -> None:
        classifier = ConfiguredDomainSourceClassifier(
            {
                "version": 1,
                "exact": {"official.test": "official_documentation"},
                "suffix": {},
            }
        )

        def resolver(host: str, port: int, **kwargs: Any):
            del host, port, kwargs
            return [(2, 1, 6, "", ("93.184.216.34", 80))]

        opened = 0

        def factory(scheme: str, host: str, port: int, ip: str, timeout: float):
            nonlocal opened
            del scheme, host, port, timeout
            opened += 1
            return FakeConnection(
                ip,
                FakeResponse(
                    302,
                    headers=[("Location", "http://attacker.test/payload")],
                ),
            )

        adapter = EyeWebFetchAdapter(
            classifier=classifier,
            resolver=resolver,
            connection_factory=factory,
        )
        hit = SearchHit(
            "official",
            "http://official.test/start",
            "",
            "official_documentation",
            classifier.stable_identity,
        )
        with self.assertRaisesRegex(AcquisitionError, "source-class boundary"):
            adapter.fetch(hit, 10_000)
        self.assertEqual(1, opened)

    def test_resolver_rejects_mixed_public_private_answers(self) -> None:
        def resolver(host: str, port: int, **kwargs: Any):
            del host, port, kwargs
            return [
                (2, 1, 6, "", ("93.184.216.34", 80)),
                (2, 1, 6, "", ("127.0.0.1", 80)),
            ]

        with self.assertRaisesRegex(AcquisitionError, "non-public"):
            resolve_public_addresses("example.test", 80, resolver=resolver)

    def test_epub_zip_bomb_and_path_tricks_fail_before_extraction(self) -> None:
        bomb = io.BytesIO()
        with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("chapter.xhtml", "A" * 500_000)
        with self.assertRaisesRegex(AcquisitionError, "compression ratio"):
            extract_epub_text_bounded(bomb.getvalue())

        traversal = io.BytesIO()
        with zipfile.ZipFile(traversal, "w") as archive:
            archive.writestr("../chapter.xhtml", "<p>Alpha</p>")
        with self.assertRaisesRegex(AcquisitionError, "escapes"):
            extract_epub_text_bounded(traversal.getvalue())

        encrypted = io.BytesIO()
        with zipfile.ZipFile(encrypted, "w") as archive:
            archive.writestr("chapter.xhtml", "<p>Alpha</p>")
        encrypted_bytes = bytearray(encrypted.getvalue())
        for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            position = encrypted_bytes.find(signature)
            self.assertGreaterEqual(position, 0)
            flags_at = position + flag_offset
            flags = int.from_bytes(encrypted_bytes[flags_at : flags_at + 2], "little")
            encrypted_bytes[flags_at : flags_at + 2] = (flags | 0x1).to_bytes(
                2, "little"
            )
        with self.assertRaisesRegex(AcquisitionError, "encrypted"):
            extract_epub_text_bounded(bytes(encrypted_bytes))


if __name__ == "__main__":
    unittest.main()
