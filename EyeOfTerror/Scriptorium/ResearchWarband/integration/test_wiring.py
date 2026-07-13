from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import pickle
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock


def _build_root() -> Path:
    """Locate either the isolated build tree or the installed repository root."""

    current = Path(__file__).resolve()
    for candidate in current.parents:
        if (
            candidate / "deploy" / "research-warband-model-runtime.31b-v2.json"
        ).is_file():
            return candidate
    raise RuntimeError("ResearchWarband build/repository root is unavailable")


BUILD_ROOT = _build_root()
STAGED_EVAL_ROOT = (
    BUILD_ROOT
    / "evaluation"
    / "EyeOfTerror"
    / "Evaluation"
    / "ResearchWarband"
)
EVAL_ROOT = (
    STAGED_EVAL_ROOT
    if STAGED_EVAL_ROOT.is_dir()
    else BUILD_ROOT / "EyeOfTerror" / "Evaluation" / "ResearchWarband"
)
STAGED_NATIVE_ROOT = BUILD_ROOT / "native_boundary"
NATIVE_ROOT = STAGED_NATIVE_ROOT if STAGED_NATIVE_ROOT.is_dir() else BUILD_ROOT
for path in (EVAL_ROOT, NATIVE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ResearchWarband import (
    deployment_guard,
    deployment_profile,
    production_runner,
    runtime_dependencies,
)
from ResearchWarband.deployment_profile import (
    DeploymentProfileError,
    validate_deployment_profile,
)
from ResearchWarband.integration.external_eval_subject import HTTPExternalEvalSubject
from ResearchWarband.integration.loopback_http import (
    LoopbackHTTPError,
    LoopbackJSONClient,
)
from ResearchWarband.pipeline import DraftUnit, ResearchResult
from ResearchWarband.schema import (
    Claim,
    EvidenceEdge,
    EvidenceLedger,
    SourceSpan,
    TextLocator,
)
from ResearchWarband.snapshot_store import RegisteredNormalizer, SnapshotStore
from ResearchWarband.service import ResearchServiceRuntime
from research_eval.manifest import load_suite
from research_eval.fixtures import load_fixture


@contextmanager
def http_server(handler: type[BaseHTTPRequestHandler]):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class DuplicateJSONHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        raw = b'{"status":"ok","status":"forged"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class NoHashFixtureHandler(BaseHTTPRequestHandler):
    body = "alpha evidence\n".encode("utf-8")
    base_url = ""
    fragment = False

    def log_message(self, *_args: object) -> None:
        pass

    def _send(self, value: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(value)))
        # Deliberately no served hash, nonce, oracle id, or fixture digest.
        self.end_headers()
        self.wfile.write(value)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/catalog":
            raw = json.dumps(
                {
                    "closed_world": True,
                    "results": [
                        {
                            "source_id": "source-alpha",
                            "title": "Alpha catalog record",
                            "url": self.base_url + "/documents/alpha",
                            "original_url": "https://example.invalid/alpha",
                        }
                    ],
                },
                separators=(",", ":"),
            ).encode("utf-8")
            self._send(raw, "application/json")
            return
        if self.path.startswith("/search?"):
            url = self.base_url + "/documents/alpha"
            if self.fragment:
                url += "#attacker-fragment"
            raw = json.dumps(
                {
                    "query": "alpha",
                    "closed_world": True,
                    "results": [
                        {
                            "source_id": "source-alpha",
                            "url": url,
                            "original_url": "https://example.invalid/alpha",
                        }
                    ],
                },
                separators=(",", ":"),
            ).encode("utf-8")
            self._send(raw, "application/json")
            return
        if self.path == "/documents/alpha":
            self._send(self.body, "text/plain; charset=utf-8")
            return
        self.send_error(404)


def exact_external_result(mission_id: str) -> dict[str, object]:
    return {
        "contract_version": "research-result/v1",
        "mission_id": mission_id,
        "status": "accepted",
        "accepted": True,
        "final_text": "",
        "question": "",
        "ledger": {
            "sources": [],
            "spans": [],
            "claims": [],
            "evidence_edges": [],
            "derivations": [],
            "conflicts": [],
            "gaps": [],
            "final_claim_refs": [],
        },
        "search_log": [],
    }


class FakeEvaluatorClient:
    bearer_token = ""

    def __init__(self, external_mission_id: str) -> None:
        self.external_mission_id = external_mission_id
        self.internal_mission_id = ""
        self.submitted: dict[str, object] | None = None
        self.health_calls = 0

    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        timeout_sec: float,
    ) -> dict[str, object]:
        del timeout_sec
        if method == "GET" and path == "/health":
            self.health_calls += 1
            return {
                "status": "ok",
                "service": "ResearchWarband",
                "identity": {
                    "standalone_test_mode": True,
                    "bearer_auth_required": False,
                    "instance_id": "eval-1",
                    "source_sha256": "0" * 64,
                    "store_recovery": {"loaded": self.health_calls},
                    "readiness": {
                        "ready": True,
                        "runner_deployment": {
                            "configured": True,
                            "ready": True,
                            "attestation_sha256": "1" * 64,
                        },
                        "deployment_integrity": {
                            "ok": True,
                            "startup_digest": "2" * 64,
                            "current_digest": "2" * 64,
                            "error": "",
                        },
                    },
                },
            }
        if method == "POST" and path == "/missions":
            self.submitted = payload
            self.internal_mission_id = (
                HTTPExternalEvalSubject._expected_internal_mission_id(payload)
            )
            return {
                "mission_id": self.internal_mission_id,
                "status": "queued",
                "request_sha256": "a" * 64,
                "idempotent": False,
            }
        if method == "GET" and path == f"/missions/{self.internal_mission_id}":
            return {
                "id": self.internal_mission_id,
                "status": "done",
                "inflight": False,
                "cleanup_complete": True,
                "result": {
                    "runner_contract_version": production_runner.RUNNER_CONTRACT_VERSION,
                    "outcome": "accepted",
                    "reason": "ok",
                    "external_evaluator_result": exact_external_result(
                        self.external_mission_id
                    ),
                    "pipeline_audit": {},
                },
            }
        raise AssertionError((method, path, payload))


class WiringTests(unittest.TestCase):
    def test_long_real_model_suite_is_versioned_without_mutating_fast_v1(self) -> None:
        long_path = EVAL_ROOT / "suites/public_smoke_long_v1/manifest.json"
        long_raw = long_path.read_bytes()
        long_text = long_raw.decode("utf-8", errors="strict")
        self.assertEqual(long_text.encode("utf-8"), long_raw)
        self.assertNotIn("РљР°", long_text)
        fast = load_suite(
            EVAL_ROOT / "suites/public_smoke_v1/manifest.json",
            allowed_root=EVAL_ROOT,
        )
        long = load_suite(
            long_path,
            allowed_root=EVAL_ROOT,
        )
        self.assertEqual(fast.data["suite_id"], "research-public-smoke-v1")
        self.assertEqual(long.data["suite_id"], "research-public-smoke-long-v1")
        self.assertNotEqual(fast.raw_sha256, long.raw_sha256)
        self.assertEqual(
            [task["id"] for task in fast.tasks],
            [task["id"] for task in long.tasks[: len(fast.tasks)]],
        )
        self.assertEqual(
            [task["limits"]["wall_sec"] for task in long.tasks],
            [21600, 21600, 21600, 1800, 21600, 21600, 21600],
        )
        for fast_task, long_task in zip(
            fast.tasks, long.tasks[: len(fast.tasks)], strict=True
        ):
            self.assertEqual(
                fast_task["request"]["goal"], long_task["request"]["goal"]
            )
            self.assertEqual(fast_task["oracle"], long_task["oracle"])
        correction_task = long.tasks[-1]
        self.assertEqual(correction_task["id"], "late-launch-correction")
        self.assertEqual(
            correction_task["oracle"]["required_conflicts"],
            [
                {
                    "id": "launch-status-conflict",
                    "left_fact_id": "initial-launch-bulletin",
                    "right_fact_id": "launch-bulletin-retracted",
                }
            ],
        )
        fixture = load_fixture(
            long.fixture_path,
            expected_sha256=long.data["fixture_sha256"],
        )
        correction = fixture.document("source-launch-correction").normalized.decode(
            "utf-8"
        )
        self.assertLess(correction.index("Initial bulletin"), 500)
        self.assertGreater(correction.index("Correction (17:45)"), 8000)

    def test_loopback_client_rejects_bearer_controls_and_duplicate_json(self) -> None:
        with self.assertRaises(ValueError):
            LoopbackJSONClient("http://127.0.0.1:7202", bearer_token="good\r\nforged")
        with http_server(DuplicateJSONHandler) as server:
            client = LoopbackJSONClient(
                f"http://127.0.0.1:{server.server_port}", max_response_bytes=4096
            )
            with self.assertRaisesRegex(LoopbackHTTPError, "malformed JSON"):
                client.request_json("GET", "/", timeout_sec=2)

    def test_fixture_fetch_succeeds_without_oracle_hash_header(self) -> None:
        NoHashFixtureHandler.fragment = False
        with http_server(NoHashFixtureHandler) as server:
            NoHashFixtureHandler.base_url = f"http://127.0.0.1:{server.server_port}"
            search = production_runner.FixtureGatewaySearchAdapter(
                NoHashFixtureHandler.base_url
            )
            hits = search.search("alpha", 3)
            self.assertEqual(len(hits), 1)
            catalog_hits = search.catalog()
            self.assertEqual("Alpha catalog record", catalog_hits[0].title)
            self.assertEqual(hits[0].url, catalog_hits[0].url)
            self.assertRegex(search.catalog_identity, r"^fixture-gateway-.+-closed-world$")
            fetcher = production_runner.FixtureGatewayFetchAdapter(search)
            fetched = fetcher.fetch(
                hits[0], 4096
            )
            catalog_fetched = fetcher.fetch(catalog_hits[0], 4096)
        self.assertEqual(fetched.raw, NoHashFixtureHandler.body)
        self.assertEqual(fetched.normalized.encode("utf-8"), NoHashFixtureHandler.body)
        self.assertIn("#eval_source_id=source-alpha", catalog_fetched.final_uri)
        self.assertEqual(
            fetched.metadata["raw_sha256"],
            __import__("hashlib").sha256(NoHashFixtureHandler.body).hexdigest(),
        )
        self.assertIn("#eval_source_id=source-alpha", fetched.final_uri)

    def test_fixture_search_rejects_preexisting_fragment(self) -> None:
        NoHashFixtureHandler.fragment = True
        try:
            with http_server(NoHashFixtureHandler) as server:
                NoHashFixtureHandler.base_url = f"http://127.0.0.1:{server.server_port}"
                search = production_runner.FixtureGatewaySearchAdapter(
                    NoHashFixtureHandler.base_url
                )
                with self.assertRaisesRegex(
                    production_runner.ProductionRunnerError, "off-origin"
                ):
                    search.search("alpha", 3)
        finally:
            NoHashFixtureHandler.fragment = False

    def test_converter_emits_exact_utf8_byte_envelope(self) -> None:
        raw = "alpha β evidence\n".encode("utf-8")

        def exact_normalizer(value: bytes, medium: str) -> str:
            self.assertEqual(medium, "text")
            return value.decode("utf-8")

        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(
                directory,
                normalizers=(
                    RegisteredNormalizer(
                        id="exact-test-v1",
                        media=frozenset({"text"}),
                        callback=exact_normalizer,
                    ),
                ),
            )
            snapshot = store.put(
                snapshot_id="snapshot-1",
                uri=(
                    "http://127.0.0.1:9999/documents/alpha"
                    "#eval_source_id=source-alpha"
                ),
                fetched_at=datetime.now(timezone.utc).isoformat(),
                medium="text",
                raw=raw,
                normalized=raw.decode("utf-8"),
                normalizer_version="exact-test-v1",
                source_class="user_provided_corpus",
                source_classifier_id="fixture-test",
            )
            text = raw.decode("utf-8")
            span = SourceSpan(
                id="span-1",
                snapshot_id=snapshot.id,
                locator=TextLocator(0, len(text)),
                excerpt=text,
            )
            claim = Claim(
                id="claim-1",
                text="Alpha beta evidence exists.",
                kind="source_assertion",
                importance="major",
                verification_status="entailed",
                authored_by="author-gemma",
                verified_by="gemma-semantic-review-pass-v1",
                confidence="high",
                conflict_claim_ids=(),
            )
            edge = EvidenceEdge(
                id="edge-1",
                claim_id=claim.id,
                span_id=span.id,
                relation="reports",
                entailment_status="entailed",
                assessed_by="gemma-semantic-review-pass-v1",
            )
            ledger = EvidenceLedger(
                schema_version="1.0",
                snapshots=(snapshot,),
                spans=(span,),
                claims=(claim,),
                edges=(edge,),
                inferences=(),
                gaps=(),
                hypotheses=(),
            )
            unit = DraftUnit(
                id="unit-1",
                classification="claim",
                text="Alpha beta evidence exists.",
                claim_refs=(claim.id,),
                gap_refs=(),
                searched_scope=(),
            )
            result = ResearchResult(
                outcome="accepted",
                reason="accepted",
                ledger=ledger,
                draft_units=(unit,),
                answer=unit.text,
                searched_queries=("alpha",),
                acquired_uris=(snapshot.uri,),
                semantic_reviews=(),
                verification_report=None,
                rounds_used=1,
                model_calls=4,
                diagnostics=(),
            )
            external = production_runner.build_external_evaluator_result(
                result, mission_id="eval-alpha", snapshot_store=store
            )
        self.assertEqual(set(external), production_runner.EXTERNAL_ROOT_FIELDS)
        source = external["ledger"]["sources"][0]
        self.assertEqual(source["source_id"], "source-alpha")
        self.assertEqual(source["raw_sha256"], source["normalized_sha256"])
        exported_span = external["ledger"]["spans"][0]
        self.assertEqual(exported_span["end_byte"], len(raw))
        self.assertEqual(
            external["ledger"]["claims"][0]["verification_status"],
            "semantically_verified",
        )
        self.assertEqual(
            external["ledger"]["final_claim_refs"],
            [
                {
                    "start_byte": 0,
                    "end_byte": len(unit.text.encode("utf-8")),
                    "claim_ids": ["claim-1"],
                }
            ],
        )

    def test_http_subject_returns_only_exact_external_result(self) -> None:
        mission_id = "eval-case"
        subject = HTTPExternalEvalSubject()
        fake = FakeEvaluatorClient(mission_id)
        subject.client = fake
        health_start = subject.health()
        health_end = subject.health()
        self.assertEqual(health_start["status"], "ok")
        self.assertEqual(health_start, health_end)
        payload = {
            "goal": "test",
            "task_id": mission_id,
            "max_wall_sec": 20,
            "standalone_test": True,
            "output_contract_version": "research-result/v1",
            "source_gateway_url": "http://127.0.0.1:9999",
        }
        execution = subject.execute(payload, timeout_sec=20)
        self.assertTrue(execution.terminal)
        self.assertTrue(execution.cleanup_proven)
        self.assertEqual(set(execution.result), production_runner.EXTERNAL_ROOT_FIELDS)
        self.assertEqual(execution.result["mission_id"], mission_id)
        self.assertNotEqual(fake.internal_mission_id, mission_id)
        self.assertEqual(fake.submitted, payload)
        pickle.dumps(HTTPExternalEvalSubject())

    def test_http_subject_internal_id_matches_service_derivation(self) -> None:
        payload = {
            "goal": "test",
            "task_id": "eval-derived-id",
            "max_wall_sec": 1800,
            "standalone_test": True,
            "output_contract_version": "research-result/v1",
            "source_gateway_url": "http://127.0.0.1:9999/",
        }
        runtime = object.__new__(ResearchServiceRuntime)
        runtime.standalone_test_mode = True
        runtime._tokenless_test_only = True
        normalized = runtime.validate_mission_request(payload)
        self.assertEqual(
            normalized["mission_id"],
            HTTPExternalEvalSubject._expected_internal_mission_id(payload),
        )
        self.assertNotEqual(normalized["mission_id"], payload["task_id"])

    def test_runner_rejects_unbound_clarification_answers(self) -> None:
        class Cancelled:
            @staticmethod
            def is_set() -> bool:
                return False

        context = types.SimpleNamespace(
            id="eval-case", attempt=2, answers=["bare answer"], cancelled=Cancelled()
        )
        with self.assertRaisesRegex(
            production_runner.ProductionRunnerError,
            "legacy bare clarification answers",
        ):
            production_runner._validate_context(
                {"mission_id": "eval-case"}, context
            )

    def test_runner_preserves_ordered_bound_clarification_turns(self) -> None:
        class Cancelled:
            @staticmethod
            def is_set() -> bool:
                return False

        context = types.SimpleNamespace(
            id="eval-case",
            attempt=3,
            clarification_turns=(
                {"question": "Which period?", "answer": "After 2020."},
                {"question": "Which language?", "answer": "Russian."},
            ),
            cancelled=Cancelled(),
        )
        turns = production_runner._validate_context(
            {"mission_id": "eval-case"}, context
        )
        self.assertEqual(
            [(item.question, item.answer) for item in turns],
            [
                ("Which period?", "After 2020."),
                ("Which language?", "Russian."),
            ],
        )

    def test_pipeline_factory_uses_only_gemma_for_context_isolated_review(self) -> None:
        calls: list[dict[str, object]] = []

        class FakeClient:
            def __init__(self, **kwargs: object) -> None:
                if kwargs.get("route") == "qwen":
                    raise AssertionError("build_pipeline must never construct a qwen route")
                calls.append(dict(kwargs))
                self.stable_identity = "model-" + str(kwargs["route"])
                self.independence_identity = "runtime-" + str(kwargs["route"])

            def preflight(self, _role: str, _payload: object) -> None:
                pass

            def decide(self, _role: str, _payload: object) -> dict[str, object]:
                return {}

        runtime_contract = {
            "version": 2,
            "dispatcher": {
                "base_url": "http://127.0.0.1:8079",
                "service_version": 2,
                "routes": {
                    "gemma": {
                        "model": "gemma.gguf",
                        "upstream": "http://127.0.0.1:8080",
                        "advertised_capacity": 4,
                        "upstream_timeout_sec": 600,
                        "queue_timeout_sec": 300,
                    },
                },
            },
            "gemma": {
                "base_url": "http://127.0.0.1:8080",
                "model_id": "gemma.gguf",
                "canonical_model_id": "gemma-31b",
                "root": "models/gemma-31b",
                "owned_by": "vllm",
                "max_model_len": 6144,
                "tokenizer_canary_version": 1,
                "tokenizer_canary_count": 40,
                "tokenizer_canary_max_model_len": 6144,
                "tokenizer_canary_token_ids_sha256": "c" * 64,
            },
            "operator_profile": {
                "gemma_max_num_seqs": 1,
                "research_max_active": 1,
                "gemma_max_tokens": 2048,
                "reader_max_tokens": 1024,
                "writer_max_tokens": 1024,
                "gemma_max_context_chars": 24000,
                "gemma_timeout_sec": 7200,
                "reader_chunk_chars": 8000,
                "tensor_parallel_size": 1,
                "modality": "text_only",
            },
            "review_pass": {
                "assurance_mode": "same_model_context_isolated",
                "route": "gemma",
                "priority": "other",
                "semantic_max_tokens": 1280,
                "roles": ["reader_coverage", "semantic_verifier"],
                "separate_physical_model": False,
                "epistemic_independence_claimed": False,
            },
        }
        runtime_report = {"attestation_sha256": "b" * 64}

        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ,
            {
                "RESEARCH_WARBAND_LLM_BASE_URL": "http://127.0.0.1:8079/v1",
                "RESEARCH_WARBAND_LLM_MODEL": "gemma.gguf",
                "RESEARCH_GEMMA_TIMEOUT_SEC": "7200",
                "RESEARCH_GEMMA_MAX_TOKENS": "2048",
                "RESEARCH_GEMMA_MAX_CONTEXT_CHARS": "24000",
                "RESEARCH_READER_CHUNK_CHARS": "8000",
                "RESEARCH_WARBAND_REVIEWER_AUTHORITY_ID": (
                    "gemma-semantic-review-pass-v1"
                ),
                "RESEARCH_WARBAND_TRUSTED_REVIEWER_IDS": (
                    "gemma-semantic-review-pass-v1"
                ),
                "RESEARCH_WARBAND_SNAPSHOT_ROOT": directory,
                "RESEARCH_WARBAND_NORMALIZER_ID": "research-eval-utf8-exact-v1",
            },
            clear=True,
        ), mock.patch.object(
            production_runner, "RoutedOpenAIModelClient", FakeClient
        ), mock.patch.object(
            production_runner, "load_runtime_contract", return_value=runtime_contract
        ), mock.patch.object(
            production_runner, "_validate_runtime_environment", return_value=None
        ), mock.patch.object(
            production_runner,
            "validate_runtime_dependencies",
            return_value=runtime_report,
        ):
            pipeline, store, observed_runtime = production_runner._build_pipeline(
                production_runner.EVALUATOR_PROFILE,
                {"source_gateway_url": "http://127.0.0.1:9999"},
            )
        self.assertIsInstance(pipeline, production_runner.ResearchPipeline)
        self.assertIsInstance(store, SnapshotStore)
        self.assertEqual(observed_runtime, runtime_report)
        self.assertEqual(2, len(calls))
        self.assertEqual(["gemma", "gemma"], [call["route"] for call in calls])
        self.assertEqual(["other", "other"], [call["priority"] for call in calls])
        self.assertNotIn("qwen", [call["route"] for call in calls])
        self.assertEqual(
            {"reader": 1024, "writer": 1024},
            calls[0]["role_max_tokens"],
        )
        self.assertEqual(
            {"reader_coverage": 1024, "semantic_verifier": 1280},
            calls[1]["role_max_tokens"],
        )

        self.assertTrue(
            all(call["base_url"] == "http://127.0.0.1:8079/v1" for call in calls)
        )
        self.assertTrue(all(call["model"] == "gemma.gguf" for call in calls))
        self.assertTrue(all(call["timeout_sec"] == 7200.0 for call in calls))
        self.assertTrue(all(call["attested_max_model_len"] == 6144 for call in calls))
        self.assertEqual(
            calls[0]["physical_model_identity"], calls[1]["physical_model_identity"]
        )
        author = pipeline.author_model
        review = pipeline.review_boundary.client
        self.assertEqual(author.runtime_model_identity, review.runtime_model_identity)
        self.assertEqual(author.independence_identity, review.independence_identity)
        self.assertNotEqual(author.stable_identity, review.stable_identity)
        self.assertEqual(
            production_runner.AUTHOR_CONTEXT_PASS_ID, author.context_pass_id
        )
        self.assertEqual(
            production_runner.REVIEW_CONTEXT_PASS_ID, review.context_pass_id
        )
        self.assertEqual(
            production_runner.REVIEW_MODEL_ROLES, review.allowed_roles
        )
        self.assertEqual(
            production_runner.REVIEW_ASSURANCE_MODE,
            pipeline.review_boundary.assurance_mode,
        )
        self.assertEqual(pipeline._reader_chunk_chars, 8000)

    def test_runtime_readiness_probe_preserves_the_supervisor_exact_contract(self) -> None:
        contract: dict[str, object] = {}
        with mock.patch.object(
            production_runner, "load_runtime_contract", return_value=contract
        ), mock.patch.object(
            production_runner, "_validate_runtime_environment"
        ), mock.patch.object(
            production_runner,
            "validate_runtime_dependencies",
            return_value={
                "attestation_sha256": "a" * 64,
                "review_pass": {
                    "assurance_mode": "same_model_context_isolated"
                },
            },
        ):
            self.assertEqual(
                {"ready": True, "attestation_sha256": "a" * 64},
                production_runner.runtime_readiness_probe(),
            )

    def test_runtime_environment_accepts_the_bounded_gemma_review_contract(self) -> None:
        runtime_contract_path = (
            BUILD_ROOT / "deploy" / "research-warband-model-runtime.31b-v2.json"
        )
        runtime_contract = json.loads(
            runtime_contract_path.read_text(encoding="utf-8")
        )
        with mock.patch.dict(
            os.environ,
            {
                "RESEARCH_WARBAND_PROFILE": production_runner.EVALUATOR_PROFILE,
                "RESEARCH_WARBAND_NORMALIZER_ID": "research-eval-utf8-exact-v1",
                "RESEARCH_WARBAND_LLM_BASE_URL": "http://127.0.0.1:8079/v1",
                "RESEARCH_WARBAND_LLM_MODEL": runtime_contract["dispatcher"]["routes"][
                    "gemma"
                ]["model"],
                "RESEARCH_GEMMA_TIMEOUT_SEC": str(
                    runtime_contract["operator_profile"]["gemma_timeout_sec"]
                ),
                "RESEARCH_GEMMA_MAX_TOKENS": str(
                    runtime_contract["operator_profile"]["gemma_max_tokens"]
                ),
                "RESEARCH_GEMMA_MAX_CONTEXT_CHARS": str(
                    runtime_contract["operator_profile"]["gemma_max_context_chars"]
                ),
                "RESEARCH_READER_CHUNK_CHARS": str(
                    runtime_contract["operator_profile"]["reader_chunk_chars"]
                ),
                "RESEARCH_WARBAND_MODEL_RUNTIME_CONTRACT": str(
                    runtime_contract_path
                ),
                "RESEARCH_WARBAND_TRUSTED_CONTRACT_FILES": str(
                    runtime_contract_path
                ),
            },
            clear=True,
        ):
            production_runner._validate_runtime_environment(runtime_contract)

    def test_deployment_preflight_attests_classifier_and_separate_stores(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            classifier = root / "classifier.json"
            classifier.write_text(
                '{"version":1,"exact":{},"suffix":{}}', encoding="utf-8"
            )
            native_contract = root / "iskandar_directive.py"
            native_contract.write_text("# attested test contract\n", encoding="utf-8")
            web_tools = root / "EyeOfTerror" / "Services" / "Search" / "web_tools.py"
            web_tools.parent.mkdir(parents=True)
            web_tools.write_text("# attested test search source\n", encoding="utf-8")
            runtime_contract = root / "runtime.json"
            runtime_contract.write_text(
                (
                    BUILD_ROOT
                    / "deploy"
                    / "research-warband-model-runtime.31b-v2.json"
                ).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            env = {
                "RESEARCH_WARBAND_PROFILE": production_runner.PRODUCTION_PROFILE,
                "RESEARCH_WARBAND_RUNNER": (
                    "EyeOfTerror.Scriptorium.ResearchWarband.production_runner:run_mission"
                ),
                "RESEARCH_WARBAND_READINESS_PROBE": (
                    "EyeOfTerror.Scriptorium.ResearchWarband.production_runner:runtime_readiness_probe"
                ),
                "RESEARCH_WARBAND_PORT": "7201",
                "RESEARCH_WARBAND_HOST": "127.0.0.1",
                "RESEARCH_WARBAND_STANDALONE_TEST_MODE": "0",
                "RESEARCH_WARBAND_BEARER_TOKEN": "0123456789abcdef" * 3,
                "RESEARCH_WARBAND_MAX_ACTIVE": "1",
                "RESEARCH_WARBAND_ATTEMPT_TIMEOUT_SECONDS": "604800",
                "RESEARCH_GEMMA_TIMEOUT_SEC": "7200",
                "RESEARCH_GEMMA_MAX_TOKENS": "2048",
                "RESEARCH_GEMMA_MAX_CONTEXT_CHARS": "24000",
                "RESEARCH_READER_CHUNK_CHARS": "8000",
                "SHUSHUNYA_SEARCH_MAX_WEB_BYTES": "200000",
                "SHUSHUNYA_SEARCH_BRAVE_API_KEY": "",
                "SHUSHUNYA_SEARCH_SEARXNG_URL": "",
                "SHUSHUNYA_SEARCH_PROVIDERS": "searxng,marginalia,duckduckgo,wikipedia,brave",
                "SHUSHUNYA_SEARCH_WEB_USER_AGENT": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                "SHUSHUNYA_SEARCH_WEB_ACCEPT_LANGUAGE": "ru,en;q=0.9",
                "RESEARCH_WARBAND_LLM_BASE_URL": "http://127.0.0.1:8079/v1",
                "RESEARCH_WARBAND_LLM_MODEL": "gemma-4-12b-it-UD-Q5_K_XL.gguf",
                "RESEARCH_WARBAND_TRUSTED_REVIEWER_IDS": (
                    "gemma-semantic-review-pass-v1"
                ),
                "RESEARCH_WARBAND_REVIEWER_AUTHORITY_ID": (
                    "gemma-semantic-review-pass-v1"
                ),
                "RESEARCH_WARBAND_NORMALIZER_ID": "research-warband-pinned-fetch-v2",
                "RESEARCH_WARBAND_MISSION_ROOT": str(root / "prod-missions"),
                "RESEARCH_WARBAND_SNAPSHOT_ROOT": str(root / "prod-cas"),
                "RESEARCH_WARBAND_PEER_MISSION_ROOT": str(root / "eval-missions"),
                "RESEARCH_WARBAND_PEER_SNAPSHOT_ROOT": str(root / "eval-cas"),
                "RESEARCH_SOURCE_CLASSIFIER_JSON": str(classifier),
                "RESEARCH_WARBAND_MODEL_RUNTIME_CONTRACT": str(runtime_contract),
                "RESEARCH_WARBAND_TRUSTED_SOURCE_FILES": str(web_tools),
                "RESEARCH_WARBAND_TRUSTED_CONTRACT_FILES": os.pathsep.join(
                    (str(native_contract), str(classifier), str(runtime_contract))
                ),
            }
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
                sys, "pycache_prefix", "/dev/null"
            ), mock.patch.object(
                sys, "dont_write_bytecode", True
            ), mock.patch.object(
                deployment_profile,
                "validate_runtime_dependencies",
                return_value={"attestation_sha256": "c" * 64},
            ):
                report = validate_deployment_profile(
                    production_runner.PRODUCTION_PROFILE
                )
                self.assertEqual(report["port"], 7201)
                self.assertEqual(report["trusted_source_file_count"], 1)
                self.assertEqual("gemma", report["review_pass"]["route"])
                self.assertEqual(1280, report["review_pass"]["semantic_max_tokens"])
                self.assertFalse(report["review_pass"]["separate_physical_model"])
                self.assertFalse(
                    report["review_pass"]["epistemic_independence_claimed"]
                )
                self.assertFalse(
                    any(
                        name.startswith("RESEARCH_QWEN")
                        for name in deployment_guard.BOUND_ENVIRONMENT
                    )
                )
                self.assertNotIn(
                    "RESEARCH_WARBAND_VERIFIER_MODEL",
                    deployment_guard.BOUND_ENVIRONMENT,
                )
                self.assertNotIn(
                    "RESEARCH_WARBAND_VERIFIER_BASE_URL",
                    deployment_guard.BOUND_ENVIRONMENT,
                )
                os.environ["RESEARCH_WARBAND_TRUSTED_CONTRACT_FILES"] = str(
                    native_contract
                )
                with self.assertRaisesRegex(
                    DeploymentProfileError, "source classifier must be included"
                ):
                    validate_deployment_profile(production_runner.PRODUCTION_PROFILE)
                os.environ["RESEARCH_WARBAND_TRUSTED_CONTRACT_FILES"] = os.pathsep.join(
                    (str(native_contract), str(classifier), str(runtime_contract))
                )
                os.environ["RESEARCH_WARBAND_PEER_SNAPSHOT_ROOT"] = str(
                    root / "eval-missions" / "nested-cas"
                )
                with self.assertRaisesRegex(DeploymentProfileError, "roots overlap"):
                    validate_deployment_profile(production_runner.PRODUCTION_PROFILE)
                os.environ["RESEARCH_WARBAND_PEER_SNAPSHOT_ROOT"] = str(
                    root / "eval-cas"
                )
                os.environ["RESEARCH_WARBAND_NORMALIZER_ID"] = "forged-normalizer"
                with self.assertRaisesRegex(
                    DeploymentProfileError, "normalizer identity"
                ):
                    validate_deployment_profile(production_runner.PRODUCTION_PROFILE)
                os.environ["RESEARCH_WARBAND_NORMALIZER_ID"] = (
                    "research-warband-pinned-fetch-v2"
                )
                os.environ["SHUSHUNYA_SEARCH_PROVIDERS"] = "brave"
                with self.assertRaisesRegex(DeploymentProfileError, "search providers"):
                    validate_deployment_profile(production_runner.PRODUCTION_PROFILE)

    def test_runtime_attestation_binds_only_gemma_and_ignores_qwen_health(self) -> None:
        contract = json.loads(
            (
                BUILD_ROOT
                / "deploy"
                / "research-warband-model-runtime.31b-v2.json"
            ).read_text(encoding="utf-8")
        )
        active = {"value": 0}
        forged_root = {"value": False}
        forged_gemma_tokens = {"value": False}
        forged_qwen_timeout = {"value": False}

        def response(
            client: LoopbackJSONClient,
            method: str,
            path: str,
            *,
            payload: object = None,
            timeout_sec: float,
        ) -> dict[str, object]:
            del timeout_sec
            if client.base_url.endswith(":8079"):
                self.assertEqual(method, "GET")
                self.assertIsNone(payload)
                self.assertEqual(path, "/dispatcher/health")
                return {
                    "ok": True,
                    "service": "llm-priority-dispatcher",
                    "version": 2,
                    "routes": {
                        "gemma": {
                            "model": "gemma-4-12b-it-UD-Q5_K_XL.gguf",
                            "upstream": "http://127.0.0.1:8080",
                            "capacity": 4,
                            "upstream_timeout_sec": 600.0,
                            "queue_timeout_sec": 300.0,
                            "active": active["value"],
                            "free": 4 - active["value"],
                        },
                        "qwen": {
                            "model": "Qwen3-Coder-Next-Q6_K-00001-of-00004.gguf",
                            "upstream": "http://127.0.0.1:8081",
                            "capacity": 1,
                            "upstream_timeout_sec": (
                                89999.0 if forged_qwen_timeout["value"] else 90000.0
                            ),
                            "queue_timeout_sec": 0.0,
                            "active": 0,
                        },
                    },
                }
            if client.base_url.endswith(":8080"):
                if path == "/tokenize":
                    self.assertEqual(method, "POST")
                    self.assertEqual(
                        payload["chat_template_kwargs"], {"enable_thinking": False}
                    )
                    tokens = [
                        2, 105, 9731, 107, 26917, 29661, 5163, 92875, 190878,
                        566, 236770, 236761, 236743, 106, 107, 105, 2364, 107,
                        123540, 236787, 99776, 82358, 33443, 163543, 236761, 9657,
                        7121, 16119, 236761, 106, 107, 105, 4368, 107, 100, 45518,
                        107, 101,
                    ]
                    if forged_gemma_tokens["value"]:
                        tokens[-1] += 1
                    return {
                        "count": len(tokens),
                        "max_model_len": 6144,
                        "tokens": tokens,
                        "token_strs": None,
                    }
                self.assertEqual(method, "GET")
                self.assertEqual(path, "/v1/models")
                root = (
                    "forged/root"
                    if forged_root["value"]
                    else "CoreOfMadness/models/google-gemma-4-31B-it-qat-w4a16-ct"
                )
                common = {
                    "object": "model",
                    "owned_by": "vllm",
                    "root": root,
                    "max_model_len": 6144,
                }
                return {
                    "object": "list",
                    "data": [
                        {"id": "gemma-4-12b-it-UD-Q5_K_XL.gguf", **common},
                        {"id": "google/gemma-4-31B-it-qat-w4a16-ct", **common},
                    ],
                }
            if client.base_url.endswith(":8081"):
                raise AssertionError("Iskandar runtime attestation must not probe Qwen")
            raise AssertionError(client.base_url)

        with mock.patch.object(
            runtime_dependencies.LoopbackJSONClient,
            "request_json",
            autospec=True,
            side_effect=response,
        ):
            first = runtime_dependencies.validate_runtime_dependencies(contract)
            active["value"] = 3
            second = runtime_dependencies.validate_runtime_dependencies(contract)
            self.assertEqual(
                first["attestation_sha256"], second["attestation_sha256"]
            )
            self.assertEqual({"gemma"}, set(first["dispatcher"]["routes"]))
            self.assertNotIn("qwen", first)
            self.assertEqual(
                "same_model_context_isolated",
                first["review_pass"]["assurance_mode"],
            )
            self.assertEqual(1280, first["review_pass"]["semantic_max_tokens"])
            changed_operator = json.loads(json.dumps(contract))
            changed_operator["operator_profile"]["research_max_active"] = 4
            third = runtime_dependencies.validate_runtime_dependencies(changed_operator)
            self.assertEqual(
                first["attestation_sha256"], third["attestation_sha256"]
            )
            forged_qwen_timeout["value"] = True
            qwen_changed = runtime_dependencies.validate_runtime_dependencies(contract)
            self.assertEqual(
                first["attestation_sha256"], qwen_changed["attestation_sha256"]
            )
            forged_qwen_timeout["value"] = False
            forged_root["value"] = True
            with self.assertRaisesRegex(
                runtime_dependencies.RuntimeDependencyError, "root/owner/context"
            ):
                runtime_dependencies.validate_runtime_dependencies(contract)
            forged_root["value"] = False
            forged_gemma_tokens["value"] = True
            with self.assertRaisesRegex(
                runtime_dependencies.RuntimeDependencyError, "tokenizer/template canary"
            ):
                runtime_dependencies.validate_runtime_dependencies(contract)

    def test_legacy_or_crafted_runtime_contract_is_rejected_before_any_probe(self) -> None:
        contract = {"version": 1}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(
                runtime_dependencies.RuntimeDependencyError, "missing or unknown"
            ):
                runtime_dependencies.load_runtime_contract(path)
        with mock.patch.object(
            runtime_dependencies.LoopbackJSONClient,
            "request_json",
            side_effect=AssertionError("malformed contracts must not reach the network"),
        ), self.assertRaisesRegex(
            runtime_dependencies.RuntimeDependencyError, "Gemma-only v2"
        ):
            runtime_dependencies.validate_runtime_dependencies(contract)

    def test_model_guard_checks_same_runtime_before_and_after_operation(self) -> None:
        class Inner:
            stable_identity = "model-inner"
            independence_identity = "physical-inner"

            def __init__(self) -> None:
                self.calls = 0

            def preflight(self, _role: str, _payload: object) -> None:
                self.calls += 1

            def decide(self, _role: str, _payload: object) -> dict[str, object]:
                self.calls += 1
                return {"ok": True}

        expected = "d" * 64
        inner = Inner()
        guard = production_runner.RuntimeGuardedModelClient(
            inner,
            expected_attestation_sha256=expected,
            context_pass_id=production_runner.AUTHOR_CONTEXT_PASS_ID,
            allowed_roles=production_runner.AUTHOR_MODEL_ROLES,
        )
        with mock.patch.object(
            production_runner,
            "validate_runtime_dependencies",
            side_effect=[
                {"attestation_sha256": expected},
                {"attestation_sha256": expected},
            ],
        ):
            guard.preflight("planner", {})
        self.assertEqual(inner.calls, 1)
        with mock.patch.object(
            production_runner,
            "validate_runtime_dependencies",
            side_effect=[
                {"attestation_sha256": expected},
                {"attestation_sha256": "e" * 64},
            ],
        ):
            with self.assertRaisesRegex(
                production_runner.ModelClientError, "runtime changed"
            ):
                guard.decide("planner", {})
        self.assertEqual(inner.calls, 2)
        self.assertEqual("physical-inner", guard.runtime_model_identity)
        self.assertEqual("physical-inner", guard.independence_identity)
        with self.assertRaisesRegex(
            production_runner.ModelClientError, "not authorized"
        ):
            guard.preflight("semantic_verifier", {})
        self.assertEqual(inner.calls, 2)


if __name__ == "__main__":
    unittest.main()
