from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

try:
    from . import mission_store, service
except ImportError:
    import mission_store  # type: ignore[no-redef]
    import service  # type: ignore[no-redef]


SERVICE_POLL_TIMEOUT_SECONDS = 15.0 if os.name == "nt" else 5.0


def accepted(answer: str = "ok", **extra) -> dict:
    return {
        "outcome": "accepted",
        "reason": "verified",
        "answer": answer,
        **extra,
    }


def runner_accepted(_payload, _mission):
    return accepted()


def runner_clarify(_payload, mission):
    if not mission.clarification_turns:
        return {"outcome": "clarify", "reason": "Which edition?", "answer": ""}
    return accepted(f"edition={mission.clarification_turns[-1]['answer']}")


def runner_cancel_wait(_payload, mission):
    mission.cancelled.wait(30)
    return accepted("late")


def runner_raise(_payload, _mission):
    raise RuntimeError("boom")


def readiness_ok():
    return {"ready": True, "attestation_sha256": "a" * 64}


def readiness_not_ready():
    return {"ready": False, "reason": "physical deployment mismatch"}


def readiness_malformed():
    return {"ready": "yes", "secret": "must not escape"}


def readiness_without_attestation():
    return {"ready": True}


def readiness_from_file():
    path = Path(os.environ["RESEARCH_TEST_READINESS_FILE"])
    return {"ready": True, "attestation_sha256": path.read_text(encoding="ascii")}


def runner_changes_readiness(_payload, _mission):
    path = Path(os.environ["RESEARCH_TEST_READINESS_FILE"])
    path.write_text("b" * 64, encoding="ascii")
    return accepted("must-not-commit")


def runner_unknown(_payload, _mission):
    return {"outcome": "done", "answer": "lie"}


def runner_large(_payload, _mission):
    return accepted("x" * 5000)


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "missions"
        self.server = None
        self.thread = None
        self.store = None
        self.runtime = None
        self.mission_ids: dict[str, str] = {}
        self.bearer = ""
        self.start_server(runner_accepted, standalone=True)

    def tearDown(self) -> None:
        self.stop_server()
        self.mission_ids = {}
        self.temp.cleanup()

    def start_server(
        self,
        runner,
        *,
        standalone: bool,
        readiness_probe=None,
        bearer: str | None = None,
        max_request: int = 1_000_000,
        max_response: int = 2_000_000,
    ) -> None:
        self.stop_server()
        self.store = mission_store.MissionStore(
            self.root,
            max_active=4,
            max_missions=32,
            max_store_bytes=30_000_000,
            max_payload_bytes=2_000_000,
            max_result_bytes=5_000_000,
            max_events_bytes=1_000_000,
            max_event_bytes=100_000,
            max_state_bytes=100_000,
            max_attempts=8,
            attempt_timeout_seconds=5,
            cancel_grace_seconds=0.05,
            terminate_grace_seconds=0.2,
        )
        self.runtime = service.ResearchServiceRuntime(
            store=self.store,
            runner=runner,
            standalone_test_mode=standalone,
            readiness_probe=(
                readiness_probe
                if readiness_probe is not None or standalone
                else readiness_ok
            ),
        )
        self.bearer = ("" if standalone else "production-secret") if bearer is None else bearer
        self.server = service.build_server(
            self.runtime,
            bearer_token=self.bearer,
            max_request_bytes=max_request,
            max_response_bytes=max_response,
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop_server(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)
        if self.store is not None:
            self.store.wait_for_idle(timeout=SERVICE_POLL_TIMEOUT_SECONDS)
        if self.runtime is not None:
            self.runtime.close()
        self.server = None
        self.thread = None

    def test_store_singleton_lease_blocks_double_start_and_releases_when_idle(self) -> None:
        assert self.server is not None
        second_store = mission_store.MissionStore(self.root)
        second_runtime = service.ResearchServiceRuntime(
            store=second_store,
            runner=runner_accepted,
            standalone_test_mode=True,
        )
        with self.assertRaises(mission_store.MissionStoreError):
            service.build_server(second_runtime)
        self.stop_server()
        replacement = service.build_server(second_runtime)
        replacement.server_close()

    def request(
        self,
        method: str,
        path: str,
        value=None,
        *,
        raw: bytes | None = None,
        host: str | None = "default",
        headers: list[tuple[str, str]] | None = None,
        content_type: str | None = "application/json",
        authenticated: bool = True,
    ) -> tuple[int, dict, dict[str, str]]:
        path_parts = path.split("/")
        if len(path_parts) >= 3 and path_parts[1] == "missions":
            path_parts[2] = self.mission_ids.get(path_parts[2], path_parts[2])
            path = "/".join(path_parts)
        if raw is None and value is not None:
            raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host == "default":
            conn.putheader("Host", f"127.0.0.1:{self.port}")
        elif host is not None:
            conn.putheader("Host", host)
        if content_type is not None and raw is not None:
            conn.putheader("Content-Type", content_type)
        if raw is not None:
            conn.putheader("Content-Length", str(len(raw)))
        supplied_headers = list(headers or [])
        if (
            authenticated
            and self.bearer
            and not any(name.lower() == "authorization" for name, _value in supplied_headers)
        ):
            supplied_headers.append(("Authorization", f"Bearer {self.bearer}"))
        for name, header_value in supplied_headers:
            conn.putheader(name, header_value)
        conn.endheaders(raw)
        response = conn.getresponse()
        data = response.read()
        response_headers = {name.lower(): val for name, val in response.getheaders()}
        conn.close()
        body = json.loads(data)
        if (
            method == "POST"
            and path == "/missions"
            and isinstance(value, dict)
            and isinstance(body, dict)
            and type(value.get("task_id")) is str
            and type(body.get("mission_id")) is str
        ):
            self.mission_ids[value["task_id"]] = body["mission_id"]
        return response.status, body, response_headers

    @staticmethod
    def standalone_payload(mission_id: str = "http-1", goal: str = "public research task") -> dict:
        return {
            "goal": goal,
            "task_id": mission_id,
            "max_wall_sec": 5,
            "standalone_test": True,
            "output_contract_version": "research-result/v1",
            "source_gateway_url": "http://127.0.0.1:1",
        }

    def poll(
        self,
        mission_id: str,
        expected: set[str],
        timeout: float = SERVICE_POLL_TIMEOUT_SECONDS,
    ) -> dict:
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            code, body, _headers = self.request("GET", f"/missions/{mission_id}")
            self.assertEqual(code, 200)
            last = body
            if body["status"] in expected:
                return body
            time.sleep(0.01)
        raise AssertionError(f"mission did not reach {expected}: {last}")

    def test_health_and_capabilities_attest_identity_models_and_recovery(self) -> None:
        code, body, headers = self.request("GET", "/health")
        self.assertEqual(code, 200)
        self.assertEqual(body["service"], "ResearchWarband")
        identity = body["identity"]
        self.assertRegex(identity["source_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(identity["instance_id"], r"^[0-9a-f]{32}$")
        self.assertIn("research", identity["models"])
        self.assertTrue(identity["store_recovery"]["safe"])
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertNotIn("access-control-allow-origin", headers)

        code, body, _ = self.request("GET", "/capabilities")
        self.assertEqual(code, 200)
        self.assertTrue(body["asynchronous"])
        self.assertTrue(body["startup_adoption"])
        self.assertTrue(body["exact_request_idempotency"])

    def test_model_identity_separates_legacy_alias_from_physical_31b(self) -> None:
        contract = Path(self.temp.name) / "runtime.json"
        contract.write_text(
            json.dumps(
                {
                    "gemma": {
                        "canonical_model_id": "google/gemma-4-31B-it",
                        "root": "models/gemma-4-31B-it",
                        "max_model_len": 6144,
                    },
                    "operator_profile": {"writer_max_tokens": 1024},
                }
            ),
            encoding="utf-8",
        )
        with mock.patch.dict(
            os.environ,
            {
                "RESEARCH_WARBAND_LLM_MODEL": "legacy-12b-served-alias",
                "RESEARCH_WARBAND_MODEL_RUNTIME_CONTRACT": str(contract),
            },
            clear=False,
        ):
            identity = service._public_model_runtime_identity()
        self.assertEqual("legacy-12b-served-alias", identity["served_alias"])
        self.assertEqual("google/gemma-4-31B-it", identity["canonical_model_id"])
        self.assertEqual("models/gemma-4-31B-it", identity["physical_model_root"])
        self.assertEqual(6144, identity["max_model_len"])
        self.assertEqual(1024, identity["writer_max_tokens"])

    def test_post_start_dependency_tamper_degrades_health_and_rejects_mutations(self) -> None:
        self.stop_server()
        trusted = Path(self.temp.name) / "trusted_dependency.py"
        trusted.write_text("VALUE = 1\n", encoding="utf-8")
        with mock.patch.dict(
            os.environ,
            {"RESEARCH_WARBAND_TRUSTED_SOURCE_FILES": str(trusted)},
            clear=False,
        ):
            self.start_server(runner_clarify, standalone=True)
            self.assertEqual(
                self.request("POST", "/missions", self.standalone_payload("tamper-answer"))[0],
                202,
            )
            self.poll("tamper-answer", {"needs_user"})
            trusted.write_text("VALUE = 2\n", encoding="utf-8")

            code, body, _ = self.request("GET", "/health")
            self.assertEqual(code, 503)
            self.assertFalse(body["ok"])
            self.assertEqual(body["status"], "degraded")
            self.assertIsNone(body["identity"]["source_sha256"])
            self.assertFalse(
                body["identity"]["readiness"]["deployment_integrity"]["ok"]
            )
            self.assertNotIn("VALUE = 2", json.dumps(body))
            code, capabilities, _ = self.request("GET", "/capabilities")
            self.assertEqual(code, 200)
            self.assertFalse(capabilities["ready"])

            code, _body, _ = self.request(
                "POST", "/missions/tamper-answer/answer", {"answer": "second"}
            )
            self.assertEqual(code, 503)
            mission = self.store.get(self.mission_ids["tamper-answer"])
            assert mission is not None
            self.assertEqual(mission.question, "Which edition?")
            self.assertEqual(mission.clarification_turns, [])
            self.assertEqual(
                self.request(
                    "POST", "/missions", self.standalone_payload("tamper-create")
                )[0],
                503,
            )
            self.stop_server()

    def test_attested_runner_readiness_controls_health_and_scheduling(self) -> None:
        self.stop_server()
        self.start_server(
            runner_accepted,
            standalone=True,
            readiness_probe=readiness_ok,
        )
        code, healthy, _ = self.request("GET", "/health")
        self.assertEqual(code, 200)
        self.assertEqual(
            healthy["identity"]["readiness"]["runner_deployment"][
                "attestation_sha256"
            ],
            "a" * 64,
        )

        self.start_server(
            runner_accepted,
            standalone=True,
            readiness_probe=readiness_not_ready,
        )
        code, degraded, _ = self.request("GET", "/health")
        self.assertEqual(code, 503)
        self.assertFalse(degraded["ok"])
        serialized = json.dumps(degraded)
        self.assertNotIn("physical deployment mismatch", serialized)
        self.assertEqual(
            self.request("POST", "/missions", self.standalone_payload("not-ready"))[0],
            503,
        )

        self.start_server(
            runner_accepted,
            standalone=True,
            readiness_probe=readiness_malformed,
        )
        code, malformed, _ = self.request("GET", "/health")
        self.assertEqual(code, 503)
        self.assertNotIn("must not escape", json.dumps(malformed))

        self.start_server(
            runner_accepted,
            standalone=False,
            readiness_probe=readiness_without_attestation,
        )
        self.assertEqual(self.request("GET", "/health")[0], 503)
        self.assertEqual(
            self.request("POST", "/missions", {
                "mission_id": "missing-attestation",
                "task_id": "task",
                "leadership_directive": {},
                "commander_order": {},
            })[0],
            503,
        )

    def test_readiness_attestation_is_pinned_across_attempt(self) -> None:
        self.stop_server()
        attestation = Path(self.temp.name) / "runtime-attestation.txt"
        attestation.write_text("a" * 64, encoding="ascii")
        with mock.patch.dict(
            os.environ,
            {"RESEARCH_TEST_READINESS_FILE": str(attestation)},
            clear=False,
        ):
            self.start_server(
                runner_changes_readiness,
                standalone=True,
                readiness_probe=readiness_from_file,
            )
            self.assertEqual(
                self.request(
                    "POST", "/missions", self.standalone_payload("attestation-race")
                )[0],
                202,
            )
            final = self.poll("attestation-race", {"blocked"}, timeout=5)
            self.assertIsNone(final["result"])
            self.assertIn("attestation changed", final["storage_error"])

    def test_async_create_get_events_and_exact_result(self) -> None:
        code, created, _ = self.request("POST", "/missions", self.standalone_payload())
        self.assertEqual(code, 202)
        self.assertRegex(created["request_sha256"], r"^[0-9a-f]{64}$")
        final = self.poll("http-1", {"done"})
        self.assertEqual(final["result"], accepted())
        self.assertFalse(final["inflight"])
        self.assertTrue(final["cleanup_complete"])
        code, events, _ = self.request("GET", "/missions/http-1/events")
        self.assertEqual(code, 200)
        self.assertEqual(events["events"][-1]["type"], "pipeline_result")

    def test_same_request_is_idempotent_different_request_conflicts(self) -> None:
        self.start_server(runner_accepted, standalone=False)
        payload = {
            "mission_id": "idem-http",
            "task_id": "idem-task",
            "leadership_directive": {
                "mission_id": "idem-http",
                "task_id": "idem-task",
            },
            "commander_order": {"mission_id": "idem-http", "goal": "one"},
        }
        self.assertEqual(self.request("POST", "/missions", payload)[0], 202)
        self.assertEqual(self.request("POST", "/missions", payload)[0], 200)
        different = json.loads(json.dumps(payload))
        different["commander_order"]["goal"] = "two"
        code, body, _ = self.request(
            "POST", "/missions", different
        )
        self.assertEqual(code, 409)
        self.assertIn("different request_sha256", body["error"])
        self.poll("idem-http", {"done"})
        assert self.store is not None
        mission = self.store.get("idem-http")
        assert mission is not None
        self.assertEqual(mission.attempt, 1)

    def test_strict_production_envelope_and_double_standalone_gate(self) -> None:
        self.start_server(runner_accepted, standalone=False)
        code, body, _ = self.request("POST", "/missions", self.standalone_payload("no-test"))
        self.assertEqual(code, 400)
        self.assertIn("STANDALONE_TEST_MODE", body["error"])

        production = {
            "mission_id": "prod-1",
            "task_id": "task-prod",
            "leadership_directive": {
                "mission_id": "prod-1",
                "task_id": "task-prod",
            },
            "commander_order": {"mission_id": "prod-1"},
        }
        self.assertEqual(self.request("POST", "/missions", production)[0], 202)
        self.poll("prod-1", {"done"})
        code, body, _ = self.request("POST", "/missions", {**production, "unknown": 1})
        self.assertEqual(code, 400)
        self.assertIn("unknown", body["error"])
        wrong = json.loads(json.dumps(production))
        wrong["mission_id"] = "prod-2"
        code, body, _ = self.request("POST", "/missions", wrong)
        self.assertEqual(code, 400)
        self.assertIn("does not match", body["error"])

    def test_public_evaluator_envelope_is_exact_and_derives_mission_id(self) -> None:
        payload = self.standalone_payload("eval-public-smoke")
        code, body, _ = self.request("POST", "/missions", payload)
        self.assertEqual(code, 202)
        mission_id = body["mission_id"]
        self.assertTrue(mission_id.startswith("eval-public-smoke-"))
        self.poll(mission_id, {"done"})
        code, repeat_body, _ = self.request("POST", "/missions", payload)
        self.assertEqual(code, 200)
        self.assertEqual(repeat_body["mission_id"], mission_id)

        # A new fixture server port is a new exact request, not a 409 collision
        # with the previous durable evaluator mission.
        rerun = dict(payload)
        rerun["source_gateway_url"] = "http://127.0.0.1:2"
        code, rerun_body, _ = self.request("POST", "/missions", rerun)
        self.assertEqual(code, 202)
        self.assertNotEqual(rerun_body["mission_id"], mission_id)

        private = self.standalone_payload("eval-private")
        private["oracle"] = {"required_facts": ["must never cross"]}
        code, body, _ = self.request("POST", "/missions", private)
        self.assertEqual(code, 400)
        self.assertIn("unknown", body["error"])
        assert self.store is not None
        self.assertIsNone(self.store.get("eval-private"))

        production = {
            "mission_id": "forged-production",
            "task_id": "forged-task",
            "leadership_directive": {},
            "commander_order": {},
        }
        code, body, _ = self.request("POST", "/missions", production)
        self.assertEqual(code, 400)
        self.assertIn("tokenless standalone", body["error"])

    def test_evaluator_envelope_rejected_when_daemon_standalone_disabled(self) -> None:
        self.start_server(runner_accepted, standalone=False)
        code, body, _ = self.request(
            "POST", "/missions", self.standalone_payload("eval-disabled")
        )
        self.assertEqual(code, 400)
        self.assertIn("STANDALONE_TEST_MODE", body["error"])

    def test_production_startup_requires_bearer(self) -> None:
        other_store = mission_store.MissionStore(self.root.parent / "production-auth")
        runtime = service.ResearchServiceRuntime(
            store=other_store,
            runner=runner_accepted,
            standalone_test_mode=False,
            readiness_probe=readiness_ok,
        )
        with self.assertRaisesRegex(RuntimeError, "requires a bearer"):
            service.build_server(runtime, bearer_token="")

    def test_production_startup_requires_attested_readiness_probe(self) -> None:
        store = mission_store.MissionStore(self.root.parent / "production-readiness")
        with self.assertRaisesRegex(RuntimeError, "attested readiness probe"):
            service.ResearchServiceRuntime(
                store=store,
                runner=runner_accepted,
                standalone_test_mode=False,
                readiness_probe=None,
            )

    def test_deployed_profile_requires_bytecode_sink_mode(self) -> None:
        for index, (prefix, dont_write) in enumerate(
            ((None, True), ("/dev/null", False))
        ):
            store = mission_store.MissionStore(
                self.root.parent / f"deployed-bytecode-{index}"
            )
            with self.subTest(prefix=prefix, dont_write=dont_write), mock.patch.dict(
                os.environ,
                {"RESEARCH_WARBAND_PROFILE": "external-evaluator"},
                clear=False,
            ), mock.patch.object(
                service.sys, "pycache_prefix", prefix
            ), mock.patch.object(
                service.sys, "dont_write_bytecode", dont_write
            ), self.assertRaisesRegex(
                RuntimeError, "PYTHONPYCACHEPREFIX=/dev/null"
            ):
                service.ResearchServiceRuntime(
                    store=store,
                    runner=runner_accepted,
                    standalone_test_mode=True,
                    readiness_probe=readiness_ok,
                )

        ready_store = mission_store.MissionStore(
            self.root.parent / "deployed-bytecode-ready"
        )
        with mock.patch.dict(
            os.environ,
            {"RESEARCH_WARBAND_PROFILE": "external-evaluator"},
            clear=False,
        ), mock.patch.object(
            service.sys, "pycache_prefix", "/dev/null"
        ), mock.patch.object(
            service.sys, "dont_write_bytecode", True
        ), mock.patch.object(
            mission_store, "verify_linux_cgroup_delegation"
        ):
            runtime = service.ResearchServiceRuntime(
                store=ready_store,
                runner=runner_accepted,
                standalone_test_mode=True,
                readiness_probe=readiness_ok,
            )
        self.assertTrue(runtime.require_readiness_attestation)

    def test_tokenless_evaluator_refuses_store_with_production_missions(self) -> None:
        store = mission_store.MissionStore(self.root.parent / "mixed-auth-store")
        store.create_or_get(
            "production-history",
            {
                "mission_id": "production-history",
                "task_id": "task",
                "leadership_directive": {},
                "commander_order": {},
            },
        )
        runtime = service.ResearchServiceRuntime(
            store=store,
            runner=runner_accepted,
            standalone_test_mode=True,
        )
        with self.assertRaisesRegex(RuntimeError, "dedicated evaluator store"):
            service.build_server(runtime, bearer_token="")

    def test_health_fingerprint_binds_actual_runner_and_rejects_tamper(self) -> None:
        first_store = mission_store.MissionStore(self.root.parent / "identity-one")
        second_store = mission_store.MissionStore(self.root.parent / "identity-two")
        first = service.ResearchServiceRuntime(
            store=first_store, runner=runner_accepted, standalone_test_mode=True
        )
        second = service.ResearchServiceRuntime(
            store=second_store, runner=runner_unknown, standalone_test_mode=True
        )
        self.assertNotEqual(first.source_sha256, second.source_sha256)
        spec = mission_store.attest_runner(runner_accepted)
        tampered = mission_store.RunnerSpec(
            target=spec.target,
            module_path=spec.module_path,
            module_sha256="f" * 64,
            callable_sha256=spec.callable_sha256,
        )
        with self.assertRaises(RuntimeError):
            service.ResearchServiceRuntime(
                store=mission_store.MissionStore(self.root.parent / "identity-tamper"),
                runner=tampered,
                standalone_test_mode=True,
            )

    def test_source_manifest_discovers_new_production_module(self) -> None:
        package = self.root.parent / "source-manifest"
        package.mkdir()
        (package / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
        before = service._source_sha256(package)
        (package / "test_ignored.py").write_text("PRIVATE = 1\n", encoding="utf-8")
        self.assertEqual(service._source_sha256(package), before)
        (package / "future_production.py").write_text("VALUE = 2\n", encoding="utf-8")
        self.assertNotEqual(service._source_sha256(package), before)

    def test_client_cannot_inject_reviewer_normalizer_or_verifier_authority(self) -> None:
        self.start_server(runner_accepted, standalone=False)
        base = {
            "mission_id": "authority-1",
            "task_id": "authority-task",
            "leadership_directive": {
                "mission_id": "authority-1",
                "task_id": "authority-task",
            },
            "commander_order": {"mission_id": "authority-1"},
        }
        forbidden = (
            "trusted_reviewer_ids",
            "attestations",
            "normalizer_callback",
            "normalizer_id",
            "normalizer_registry",
            "verifier_config",
        )
        for index, field in enumerate(forbidden):
            with self.subTest(field=field):
                payload = json.loads(json.dumps(base))
                payload["mission_id"] = f"authority-{index + 2}"
                payload["leadership_directive"]["mission_id"] = payload["mission_id"]
                payload["commander_order"]["mission_id"] = payload["mission_id"]
                payload["commander_order"]["nested"] = {field: {"client": "supplied"}}
                code, body, _ = self.request("POST", "/missions", payload)
                self.assertEqual(code, 400)
                self.assertIn("trusted authority object", body["error"])
        assert self.store is not None
        self.assertEqual(self.store.missions, {})

    def test_malformed_oversized_and_wrong_content_type_are_rejected(self) -> None:
        code, _body, _ = self.request("POST", "/missions", raw=b"{bad")
        self.assertEqual(code, 400)
        code, _body, _ = self.request(
            "POST", "/missions", self.standalone_payload("ctype"), content_type="text/plain"
        )
        self.assertEqual(code, 415)
        self.start_server(runner_accepted, standalone=True, max_request=128)
        oversized = self.standalone_payload("oversized")
        oversized["padding"] = "x" * 1000
        try:
            code, body, _ = self.request("POST", "/missions", oversized)
        except (ConnectionAbortedError, ConnectionResetError):
            if os.name != "nt":
                raise
            # Windows may reset a connection closed with a deliberately unread
            # oversized body; that is still a fail-closed rejection.
        else:
            self.assertEqual(code, 413)
            self.assertIn("exceeds", body["error"])

    def test_auth_literal_host_and_origin_policy(self) -> None:
        self.start_server(runner_accepted, standalone=True, bearer="secret")
        self.assertEqual(self.request("GET", "/health", authenticated=False)[0], 401)
        auth = [("Authorization", "Bearer secret")]
        self.assertEqual(self.request("GET", "/health", headers=auth)[0], 200)
        self.assertEqual(
            self.request("GET", "/health", host="evil.example", headers=auth)[0], 421
        )
        self.assertEqual(self.request("GET", "/health", host=None, headers=auth)[0], 421)
        self.assertEqual(
            self.request(
                "GET",
                "/health",
                headers=[*auth, ("Origin", "http://evil.example")],
            )[0],
            403,
        )
        same_origin = f"http://127.0.0.1:{self.port}"
        self.assertEqual(
            self.request("GET", "/health", headers=[*auth, ("Origin", same_origin)])[0],
            200,
        )

    def test_cancel_endpoint_wins_against_runner(self) -> None:
        self.start_server(runner_cancel_wait, standalone=True)
        self.assertEqual(
            self.request("POST", "/missions", self.standalone_payload("cancel-http"))[0],
            202,
        )
        self.poll("cancel-http", {"running"})
        code, body, _ = self.request("POST", "/missions/cancel-http/cancel", {})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        final = self.poll("cancel-http", {"cancelled"})
        self.assertIsNone(final["result"])

    def test_answer_endpoint_persists_answer_and_relaunches(self) -> None:
        self.start_server(runner_clarify, standalone=True)
        self.assertEqual(
            self.request("POST", "/missions", self.standalone_payload("answer-http"))[0],
            202,
        )
        self.poll("answer-http", {"needs_user"})
        code, body, _ = self.request(
            "POST", "/missions/answer-http/answer", {"answer": "second"}
        )
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        final = self.poll("answer-http", {"done"})
        self.assertEqual(final["result"]["answer"], "edition=second")

    def test_runner_failure_and_unknown_outcome_never_report_done(self) -> None:
        for mission_id, runner in (
            ("raise-http", runner_raise),
            ("unknown-http", runner_unknown),
        ):
            with self.subTest(mission_id=mission_id):
                self.start_server(runner, standalone=True)
                self.assertEqual(
                    self.request("POST", "/missions", self.standalone_payload(mission_id))[0],
                    202,
                )
                final = self.poll(mission_id, {"blocked"})
                self.assertNotEqual(final["status"], "done")

    def test_create_persistence_failure_returns_507_and_leaves_no_visible_mission(self) -> None:
        assert self.store is not None
        with mock.patch.object(self.store, "_atomic_write", side_effect=OSError("disk full")):
            code, body, _ = self.request(
                "POST", "/missions", self.standalone_payload("persist-http")
            )
        self.assertEqual(code, 507)
        self.assertIn("disk full", body["error"])
        self.assertEqual(self.store.missions, {})

    def test_response_limit_fails_closed_without_truncating_stored_result(self) -> None:
        self.start_server(
            runner_large,
            standalone=True,
            max_response=1000,
        )
        self.assertEqual(
            self.request("POST", "/missions", self.standalone_payload("large-http"))[0],
            202,
        )
        assert self.store is not None
        self.assertTrue(self.store.wait_for_idle())
        code, body, _ = self.request("GET", "/missions/large-http")
        self.assertEqual(code, 507)
        self.assertIn("response exceeds", body["error"])
        mission = self.store.get(self.mission_ids["large-http"])
        assert mission is not None
        self.assertEqual(mission.result, runner_large({}, None))

    def test_server_and_worker_threads_cleanup(self) -> None:
        self.assertEqual(
            self.request("POST", "/missions", self.standalone_payload("cleanup-http"))[0],
            202,
        )
        self.poll("cleanup-http", {"done"})
        assert self.store is not None
        self.assertTrue(self.store.wait_for_idle())
        self.assertEqual(self.store.active_worker_count(), 0)
        mission = self.store.get(self.mission_ids["cleanup-http"])
        assert mission is not None
        self.assertIsNone(mission._thread)


if __name__ == "__main__":
    unittest.main()
