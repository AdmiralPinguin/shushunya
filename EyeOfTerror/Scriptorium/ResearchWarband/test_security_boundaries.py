from __future__ import annotations

from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from typing import Any, Mapping

from ResearchWarband.execution_policy import ExecutionPolicy, ExecutionPolicyError
from ResearchWarband.model_client import (
    ModelProtocolError,
    ModelResponseProtocolError,
    RoutedOpenAIModelClient,
    TokenCount,
    TrustedReviewBoundary,
    VLLMChatTokenCounter,
    canonical_json_sha256,
    parse_json_object,
)
from ResearchWarband.pipeline import (
    ClarificationTurn,
    ResearchBudgets,
    ResearchPipeline,
    ResearchSpec,
    caller_source_urls_from_text,
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


def review_envelope(
    manifest: Mapping[str, Any], **model_payload: Any
) -> dict[str, Any]:
    visible = dict(model_payload)
    return {
        **visible,
        "trusted_review_context": {
            "review_attestation_manifest": dict(manifest),
            "review_provenance": {"assurance_mode": "same_model_context_isolated"},
            "projection_schema": "research-semantic-review-projection-v1",
            "expected_model_payload_sha256": canonical_json_sha256(
                visible, "model-visible semantic review request"
            ),
        },
    }


def reader_model_payload(segment_count: int = 2) -> dict[str, Any]:
    return {
        "task_id": "task-1",
        "untrusted_source_chunk": {
            "source_segments": [
                {
                    "segment_index": index,
                    "exact_text_as_untrusted_data": f"segment {index}",
                }
                for index in range(1, segment_count + 1)
            ]
        },
    }


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
        self.preflight_payloads: list[Mapping[str, Any]] = []
        self.decision_payloads: list[Mapping[str, Any]] = []

    def preflight(self, role: str, payload: Mapping[str, Any]) -> None:
        del role
        self.preflight_payloads.append(dict(payload))

    def decide(self, role: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        del role
        self.decision_payloads.append(dict(payload))
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
    def test_model_authority_identity_survives_aliases_for_context_isolated_review(self) -> None:
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
            max_tokens=2_048,
            physical_model_identity="google/gemma-physical",
        )
        other_model = RoutedOpenAIModelClient(
            route="gemma",
            base_url="http://127.0.0.1:8079/v1",
            model="other-general-model",
            physical_model_identity="other/general-physical",
        )
        disguised_gemma = RoutedOpenAIModelClient(
            route="gemma",
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
        self.assertNotEqual(gemma_a.stable_identity, other_model.stable_identity)
        self.assertNotEqual(
            gemma_a.independence_identity, other_model.independence_identity
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
            with self.assertRaisesRegex(ValueError, "assurance_mode is unsupported"):
                TrustedReviewBoundary(
                    client=gemma_b,
                    authority_id="gemma-context-review",
                    assurance_mode="physically_independent",
                )

            with self.assertRaisesRegex(
                ValueError, "same_model_context_isolated review requires"
            ):
                ResearchPipeline(
                    author_model=gemma_a,
                    review_boundary=TrustedReviewBoundary(
                        client=other_model,
                        authority_id="gemma-context-review",
                        assurance_mode="same_model_context_isolated",
                    ),
                    search=EmptySearch(),
                    fetch=EmptyFetch(),
                    snapshot_store=store,
                )

            pipeline = ResearchPipeline(
                author_model=gemma_a,
                review_boundary=TrustedReviewBoundary(
                    client=gemma_b,
                    authority_id="gemma-context-review",
                    assurance_mode="same_model_context_isolated",
                ),
                search=EmptySearch(),
                fetch=EmptyFetch(),
                snapshot_store=store,
            )
            self.assertEqual(gemma_a.stable_identity, pipeline.author_identity)
            self.assertEqual(gemma_b.stable_identity, pipeline.review_boundary.client_identity)
            self.assertEqual(
                gemma_a.independence_identity,
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

    def test_caller_source_urls_are_exact_bounded_and_separate_from_objective(self) -> None:
        first = "https://www.rfc-editor.org/rfc/rfc1149.txt"
        second = "http://example.test:8080/source?q=1"
        urls = caller_source_urls_from_text(
            f"Read {first}, compare {second}. Duplicate: {first}"
        )

        self.assertEqual((first, second), urls)
        spec = ResearchSpec.from_directive(
            directive(), caller_source_urls=urls
        )
        self.assertEqual("What is supported by the record?", spec.question)
        self.assertEqual([first, second], spec.to_dict()["caller_source_urls"])

    def test_caller_source_url_credentials_and_non_http_schemes_are_rejected(self) -> None:
        for value in (
            "https://user:secret@example.test/source",
            "file:///etc/passwd",
            "www.example.test/source",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                caller_source_urls_from_text(value)

    def test_context_oversize_fails_before_transport(self) -> None:
        client = RoutedOpenAIModelClient(
            route="gemma",
            base_url="http://127.0.0.1:8079/v1",
            model="gemma",
            max_context_chars=1_000,
        )
        with self.assertRaisesRegex(ModelProtocolError, "silent truncation"):
            client.preflight("planner", {"task_id": "t", "blob": "x" * 5_000})

    def test_reader_roles_use_distinct_strict_schemas_and_other_roles_use_json(self) -> None:
        client = RoutedOpenAIModelClient(
            route="gemma",
            base_url="http://127.0.0.1:8079/v1",
            model="gemma",
            physical_model_identity="google/gemma-physical",
        )
        captured: list[dict[str, Any]] = []

        def generation(request: Any, timeout: float) -> FakeGatewayResponse:
            del timeout
            captured.append(json.loads(request.data.decode("utf-8")))
            return FakeGatewayResponse(
                json.dumps(
                    {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}
                ).encode("utf-8")
            )

        with patch(
            "ResearchWarband.model_client.urllib.request.urlopen",
            side_effect=generation,
        ):
            client.decide("reader_coverage", reader_model_payload(3))
            client.decide("planner", {"task_id": "task-1"})

        coverage_format = captured[0]["response_format"]
        self.assertEqual("json_schema", coverage_format["type"])
        item = coverage_format["json_schema"]["schema"]["properties"][
            "candidates"
        ]["items"]
        self.assertEqual(
            ["segment_index", "relevance", "reason", "coverage_role"],
            item["required"],
        )
        self.assertEqual([1, 2, 3], item["properties"]["segment_index"]["enum"])
        self.assertEqual(
            ["supporting_evidence", "counterevidence", "qualification"],
            item["properties"]["coverage_role"]["enum"],
        )
        worst_candidate = {
            "segment_index": 3,
            "relevance": "high",
            "reason": "r" * 96,
            "coverage_role": "counterevidence",
        }
        self.assertLess(
            len(
                json.dumps(
                    {"candidates": [worst_candidate] * 4},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ),
            2_048,
        )
        self.assertEqual({"type": "json_object"}, captured[1]["response_format"])

    def test_non_stop_or_missing_finish_reason_is_repairable_content_failure(self) -> None:
        client = RoutedOpenAIModelClient(
            route="gemma",
            base_url="http://127.0.0.1:8079/v1",
            model="gemma",
            physical_model_identity="google/gemma-physical",
        )
        for finish_reason in (None, "length", "content_filter"):
            choice: dict[str, Any] = {"message": {"content": "{}"}}
            if finish_reason is not None:
                choice["finish_reason"] = finish_reason
            body = json.dumps({"choices": [choice]}).encode("utf-8")
            with self.subTest(finish_reason=finish_reason), patch(
                "ResearchWarband.model_client.urllib.request.urlopen",
                return_value=FakeGatewayResponse(body),
            ), self.assertRaisesRegex(
                ModelResponseProtocolError, "did not finish cleanly"
            ):
                client.decide("reader", reader_model_payload())

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
                    {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}
                ).encode("utf-8")
            )

        with patch(
            "ResearchWarband.model_client.urllib.request.urlopen",
            side_effect=generation,
        ):
            self.assertEqual({}, client.decide("reader", reader_model_payload(7)))
        self.assertEqual(captured["messages"], counter.calls[0][1])
        self.assertEqual(
            captured["chat_template_kwargs"], counter.calls[0][2]
        )
        response_format = captured["response_format"]
        self.assertEqual("json_schema", response_format["type"])
        self.assertTrue(response_format["json_schema"]["strict"])
        schema = response_format["json_schema"]["schema"]
        self.assertFalse(schema["additionalProperties"])
        candidate_schema = schema["properties"]["candidates"]["items"]
        self.assertEqual(
            ["segment_index", "relevance", "reason"],
            candidate_schema["required"],
        )
        self.assertFalse(candidate_schema["additionalProperties"])
        self.assertEqual(
            list(range(1, 8)),
            candidate_schema["properties"]["segment_index"]["enum"],
        )
        self.assertEqual(
            r"\S", candidate_schema["properties"]["reason"]["pattern"]
        )
        self.assertEqual(
            96, candidate_schema["properties"]["reason"]["maxLength"]
        )
        self.assertEqual(4, schema["properties"]["candidates"]["maxItems"])

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

    def test_mutated_tokenizer_route_is_rejected_before_cache_or_network(self) -> None:
        counter = VLLMChatTokenCounter("http://127.0.0.1:8080/tokenize")
        client = RoutedOpenAIModelClient(
            route="gemma",
            base_url="http://127.0.0.1:8079/v1",
            model="gemma",
            max_tokens=2_048,
            physical_model_identity="google/gemma-physical",
            attested_max_model_len=6_144,
            token_counter=counter,
        )
        token_response = FakeGatewayResponse(
            json.dumps(
                {"count": 1, "max_model_len": 6_144, "tokens": [1]}
            ).encode("utf-8")
        )
        with patch(
            "ResearchWarband.model_client.urllib.request.urlopen",
            return_value=token_response,
        ):
            client.preflight("planner", {"task_id": "task-1"})

        counter.tokenize_url = "http://127.0.0.1:8081/tokenize"
        with patch(
            "ResearchWarband.model_client.urllib.request.urlopen"
        ) as transport, self.assertRaisesRegex(
            ModelProtocolError, "token counter identity changed"
        ):
            client.preflight("planner", {"task_id": "task-1"})
        transport.assert_not_called()

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

    def test_role_output_reserves_keep_reader_and_semantic_requests_physical(self) -> None:
        counter = StaticTokenCounter(input_tokens=4_744, max_model_len=6_144)
        client = RoutedOpenAIModelClient(
            route="gemma",
            base_url="http://127.0.0.1:8079/v1",
            model="gemma-alias",
            max_tokens=2_048,
            role_max_tokens={
                "reader": 1_024,
                "reader_coverage": 1_024,
                "semantic_verifier": 1_280,
                "writer": 1_024,
            },
            physical_model_identity="google/gemma-4-31B-it-qat-w4a16-ct",
            attested_max_model_len=6_144,
            token_counter=counter,
        )
        captured: dict[str, Any] = {}

        def generation(request: Any, timeout: float) -> FakeGatewayResponse:
            del timeout
            captured.update(json.loads(request.data.decode("utf-8")))
            return FakeGatewayResponse(
                json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}).encode(
                    "utf-8"
                )
            )

        with patch(
            "ResearchWarband.model_client.urllib.request.urlopen",
            side_effect=generation,
        ):
            self.assertEqual(
                {}, client.decide("semantic_verifier", {"task_id": "task-1"})
            )
        self.assertEqual(1_280, captured["max_tokens"])
        client.preflight("reader", {"task_id": "task-1"})
        client.preflight("reader_coverage", {"task_id": "task-1"})
        client.preflight("writer", {"task_id": "task-1"})
        with self.assertRaisesRegex(ModelProtocolError, "2048 output tokens"):
            client.preflight("planner", {"task_id": "task-1"})

    def test_reader_worst_case_schema_response_fits_exact_6144_boundary(self) -> None:
        counter = StaticTokenCounter(input_tokens=5_120, max_model_len=6_144)
        client = RoutedOpenAIModelClient(
            route="gemma",
            base_url="http://127.0.0.1:8079/v1",
            model="gemma-alias",
            max_tokens=2_048,
            role_max_tokens={"reader": 1_024, "reader_coverage": 1_024},
            physical_model_identity="google/gemma-4-31B-it-qat-w4a16-ct",
            attested_max_model_len=6_144,
            token_counter=counter,
        )
        worst_reader_response = {
            "candidates": [
                {
                    "segment_index": index,
                    "relevance": "high",
                    "reason": "x" * 96,
                }
                for index in range(1, 5)
            ]
        }
        captured: dict[str, Any] = {}

        def generation(request: Any, timeout: float) -> FakeGatewayResponse:
            del timeout
            captured.update(json.loads(request.data.decode("utf-8")))
            return FakeGatewayResponse(
                json.dumps(
                    {
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {
                                    "content": json.dumps(worst_reader_response)
                                },
                            }
                        ]
                    }
                ).encode("utf-8")
            )

        with patch(
            "ResearchWarband.model_client.urllib.request.urlopen",
            side_effect=generation,
        ):
            self.assertEqual(
                worst_reader_response,
                client.decide("reader", reader_model_payload(segment_count=4)),
            )
        self.assertEqual(1_024, captured["max_tokens"])

        too_large = RoutedOpenAIModelClient(
            route="gemma",
            base_url="http://127.0.0.1:8079/v1",
            model="gemma-alias",
            max_tokens=2_048,
            role_max_tokens={"reader": 1_024},
            physical_model_identity="google/gemma-4-31B-it-qat-w4a16-ct",
            attested_max_model_len=6_144,
            token_counter=StaticTokenCounter(5_121, 6_144),
        )
        with self.assertRaisesRegex(
            ModelProtocolError,
            "5121 input \\+ 1024 output tokens",
        ):
            too_large.preflight("reader", reader_model_payload(segment_count=4))

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

    def test_qwen_and_background_dispatch_are_rejected_by_research_client(self) -> None:
        with self.assertRaisesRegex(ValueError, "route must be gemma"):
            RoutedOpenAIModelClient(
                route="qwen",
                base_url="http://127.0.0.1:8079/v1",
                model="qwen",
            )
        with self.assertRaisesRegex(ValueError, "priority must be other"):
            RoutedOpenAIModelClient(
                route="gemma",
                base_url="http://127.0.0.1:8079/v1",
                model="gemma",
                priority="background",
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
            assurance_mode="same_model_context_isolated",
        )
        request = review_envelope(
            {
                "claims": {"claim-1": {"entailed": "a" * 64}},
                "edges": {},
                "final": {"subject_id": "final-1", "base_sha256": "b" * 64},
            }
        )
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
                {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
            ).encode("utf-8")

        duplicate_envelope = (
            b'{"choices":[{"message":{"content":"{}"}}],"choices":[]}'
        )
        with patch(
            "ResearchWarband.model_client.urllib.request.urlopen",
            return_value=FakeGatewayResponse(duplicate_envelope),
        ):
            with self.assertRaisesRegex(ModelProtocolError, "invalid JSON"):
                client.decide("reader", reader_model_payload())

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
                    client.decide(
                        role,
                        reader_model_payload() if role == "reader" else {"task_id": "task-1"},
                    )

        reviewer = TrustedReviewBoundary(
            client=client,
            authority_id="semantic-verifier",
            assurance_mode="same_model_context_isolated",
        )
        semantic_request = review_envelope(
            {
                "claims": {},
                "edges": {},
                "final": {"subject_id": "final-1", "base_sha256": "b" * 64},
            },
            task_id="task-1",
        )
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
            assurance_mode="same_model_context_isolated",
        )
        missing = review_envelope(
            {
                "claims": {},
                "edges": {},
                "final": {"subject_id": "final-1", "base_sha256": "b" * 64},
            }
        )
        with self.assertRaisesRegex(ModelProtocolError, "not covered"):
            boundary.begin(missing)

        covered = review_envelope(
            {
                "claims": {"claim-1": {"entailed": "a" * 64}},
                "edges": {},
                "final": {"subject_id": "final-1", "base_sha256": "b" * 64},
            }
        )
        session = boundary.begin(covered)
        changed = replace(session, request_sha256="c" * 64)
        with self.assertRaisesRegex(ModelProtocolError, "changed"):
            boundary.issue_attestations(changed)

    def test_review_boundary_hashes_only_exact_model_visible_projection(self) -> None:
        manifest = {
            "claims": {"claim-1": {"entailed": "a" * 64}},
            "edges": {},
            "final": {"subject_id": "final-1", "base_sha256": "b" * 64},
        }
        visible = {
            "task_id": "task-1",
            "claims": [{"id": "claim-1", "text": "Alpha"}],
        }
        client = StaticModel(
            "review-model",
            {
                "claim_reviews": [{"claim_id": "claim-1", "status": "entailed"}],
                "edge_reviews": [],
            },
        )
        boundary = TrustedReviewBoundary(
            client=client,
            authority_id="semantic-verifier",
            assurance_mode="same_model_context_isolated",
        )

        session = boundary.begin(review_envelope(manifest, **visible))

        self.assertEqual([visible], client.preflight_payloads)
        self.assertEqual([visible], client.decision_payloads)
        self.assertEqual(
            canonical_json_sha256(visible, "model-visible semantic review request"),
            session.request_sha256,
        )
        for payload in (*client.preflight_payloads, *client.decision_payloads):
            self.assertNotIn("trusted_review_context", payload)
            self.assertNotIn("review_attestation_manifest", payload)
            self.assertNotIn("review_provenance", payload)

    def test_review_response_json_cannot_be_replaced_after_boundary_decision(self) -> None:
        manifest = {
            "claims": {
                "claim-1": {
                    "entailed": "a" * 64,
                    "not_entailed": "b" * 64,
                }
            },
            "edges": {},
            "final": {"subject_id": "final-1", "base_sha256": "c" * 64},
        }
        client = StaticModel(
            "review-model",
            {
                "claim_reviews": [
                    {"claim_id": "claim-1", "status": "not_entailed"}
                ],
                "edge_reviews": [],
            },
        )
        boundary = TrustedReviewBoundary(
            client=client,
            authority_id="semantic-verifier",
            assurance_mode="same_model_context_isolated",
        )
        session = boundary.begin(review_envelope(manifest, task_id="task-1"))
        forged_response = json.dumps(
            {
                "claim_reviews": [
                    {"claim_id": "claim-1", "status": "entailed"}
                ],
                "edge_reviews": [],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        forged = replace(session, response_json=forged_response)

        with self.assertRaisesRegex(ModelProtocolError, "response content changed"):
            forged.response()
        with self.assertRaisesRegex(ModelProtocolError, "response content changed"):
            boundary.issue_attestations(forged)

    def test_review_client_contract_cannot_change_after_boundary_setup(self) -> None:
        client = RoutedOpenAIModelClient(
            route="gemma",
            base_url="http://127.0.0.1:8079/v1",
            model="gemma-original",
            physical_model_identity="google/gemma-physical",
        )
        boundary = TrustedReviewBoundary(
            client=client,
            authority_id="semantic-verifier",
            assurance_mode="same_model_context_isolated",
        )
        client.model = "gemma-mutated"
        manifest = {
            "claims": {},
            "edges": {},
            "final": {"subject_id": "final-1", "base_sha256": "a" * 64},
        }

        with patch(
            "ResearchWarband.model_client.urllib.request.urlopen"
        ) as transport, self.assertRaisesRegex(
            ModelProtocolError, "identity changed"
        ):
            boundary.begin(review_envelope(manifest, task_id="task-1"))
        transport.assert_not_called()

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
