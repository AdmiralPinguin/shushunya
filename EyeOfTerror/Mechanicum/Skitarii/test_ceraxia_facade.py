from __future__ import annotations

import json
import http.client
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
WARMMASTER_ROOT = REPO_ROOT / "EyeOfTerror" / "Warmaster"
for import_root in (REPO_ROOT, WARMMASTER_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from EyeOfTerror.common_protocol import commander_order
from eye_of_terror.inner_circle import ceraxia_service


class _WarbandHealthHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        model = lambda name: {"model": name, "base_url": "http://127.0.0.1:1/v1"}
        payload = json.dumps({
            "status": "ok",
            "service": "Skitarii",
            "vm_alive": True,
            "process_boundary_ready": True,
            "identity": {
                "source_sha256": ceraxia_service.expected_skitarii_source_sha256(),
                "instance_id": "test-instance",
                "held_out_required": True,
                "models": {
                    "planner": model("planner"),
                    "reviewer": model("reviewer"),
                    "spec": model("spec"),
                    "fighter": model("fighter"),
                    "held_out": model("held-out"),
                },
            },
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _request_json(
    url: str,
    payload: dict | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    request = urllib.request.Request(
        url,
        data=data,
        method="POST" if data is not None else "GET",
        headers=request_headers,
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - local test server
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise AssertionError(f"expected JSON object, got {result!r}")
    return result


def _command(task_id: str) -> dict:
    return commander_order(
        f"mission-{task_id}",
        to="Ceraxia",
        user_request="Почини Python приложение.",
        commander_intent="Подготовить совместимый кодовый запуск.",
        primary_goal="почини python приложение",
        success_conditions=["Ceraxia publishes one consistent execution contract"],
        constraints=["Preserve the Warmaster preflight contract."],
        escalate_to_user_if=["the requested behavior requires an irreversible product choice"],
    )


def _directive_answer(
    intent: str = "Deliver a verified repair without changing unrelated behavior",
    *,
    decision: str = "delegate",
) -> dict:
    return {
        "ok": True,
        "status": "answered",
        "content": json.dumps(
            {
                "decision": decision,
                "mission_intent": intent,
                "priorities": ["correctness", "preserve existing behavior"],
                "constraints": ["do not overwrite unrelated user changes"],
                "success_conditions": ["the requested behavior is verified"],
                "tradeoffs": ["prefer a narrow safe change over a broad rewrite"],
                "escalation_conditions": ["a product choice changes observable behavior"],
            },
            ensure_ascii=False,
        ),
    }


class TestCeraxiaFacade(unittest.TestCase):
    def setUp(self) -> None:
        self.health_server = ThreadingHTTPServer(("127.0.0.1", 0), _WarbandHealthHandler)
        self.health_thread = threading.Thread(target=self.health_server.serve_forever, daemon=True)
        self.health_thread.start()
        self.backend_url = f"http://127.0.0.1:{self.health_server.server_port}"

    def tearDown(self) -> None:
        self.health_server.shutdown()
        self.health_server.server_close()
        self.health_thread.join(timeout=5)

    def assert_compatibility_contract(self, payload: dict) -> None:
        legacy_workers = ceraxia_service.required_workers()
        self.assertEqual(len(legacy_workers), 6)
        self.assertNotIn("SkitariiWarband", legacy_workers)
        self.assertEqual(payload["api_version"], 2)
        self.assertEqual(payload["contract_mode"], "legacy_six_worker_compatibility_adapter")
        self.assertEqual(payload["required_workers"], legacy_workers)
        self.assertEqual(payload["pipeline"]["required_workers"], legacy_workers)
        self.assertEqual(payload["pipeline"]["mode"], "legacy_six_worker_compatibility_adapter")
        self.assertIs(payload["pipeline"]["authoritative"], False)
        self.assertEqual(payload["pipeline"]["purpose"], "registry_preflight_only")
        self.assertEqual(payload["pipeline"]["active_execution_backend"], "SkitariiWarband")
        self.assertEqual(payload["execution_contract"]["planning_and_preflight"], "six_worker_registry_compatibility_adapter")
        self.assertEqual(payload["execution_contract"]["execution"], "SkitariiWarband")
        self.assertEqual(
            payload["execution_contract"]["leadership_contract"],
            "native_ceraxia_directive_v1",
        )
        self.assertIs(
            payload["execution_contract"]["compatibility_plan_authoritative"],
            False,
        )
        self.assertTrue(payload["active_execution_backend"]["healthy"])
        self.assertEqual(payload["active_execution_backend"]["lifecycle"], "active")
        self.assertEqual(payload["active_execution_backend"]["endpoint"], self.backend_url)
        for worker in payload.get("worker_availability", {}).get("resolved_workers", {}).values():
            self.assertTrue(worker["compatibility_only"])
            self.assertFalse(worker["path_exists"])
            self.assertEqual(worker["status"], "compatibility_adapter")
            self.assertEqual(worker["execution_backend"], "SkitariiWarband")

    def test_backend_health_is_a_real_probe(self) -> None:
        with patch.dict(os.environ, {"SKITARII_URL": self.backend_url}):
            healthy = ceraxia_service.skitarii_backend_health(timeout_sec=1)
        self.assertTrue(healthy["healthy"])
        self.assertEqual(healthy["health"]["service"], "Skitarii")

        with patch.dict(os.environ, {"SKITARII_URL": "http://127.0.0.1:1"}):
            unavailable = ceraxia_service.skitarii_backend_health(timeout_sec=0.1)
        self.assertFalse(unavailable["healthy"])
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertTrue(unavailable["error"])

    def test_old_backend_without_identity_is_not_ready(self) -> None:
        class Response:
            status = 200

            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self):
                return json.dumps({"status": "ok", "service": "Skitarii", "vm_alive": True}).encode()

        with patch.object(ceraxia_service, "urlopen", return_value=Response()):
            health = ceraxia_service.skitarii_backend_health(timeout_sec=1)
        self.assertFalse(health["healthy"])
        self.assertIn("identity", health["error"])

    def test_backend_without_process_boundary_is_not_ready(self) -> None:
        with patch.dict(os.environ, {"SKITARII_URL": self.backend_url}):
            baseline = ceraxia_service.skitarii_backend_health(timeout_sec=1)["health"]
        payload = json.loads(json.dumps(baseline))
        payload["process_boundary_ready"] = False

        class Response:
            status = 200

            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return json.dumps(payload).encode("utf-8")

        with patch.object(ceraxia_service, "urlopen", return_value=Response()):
            health = ceraxia_service.skitarii_backend_health(timeout_sec=1)
        self.assertFalse(health["healthy"])
        self.assertIn("process boundary", health["error"])

    def test_backend_probe_sends_configured_bearer_token(self) -> None:
        with patch.dict(os.environ, {"SKITARII_URL": self.backend_url}):
            baseline = ceraxia_service.skitarii_backend_health(timeout_sec=1)["health"]
        seen = {}

        class Response:
            status = 200

            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return json.dumps(baseline).encode("utf-8")

        def open_request(request, **_kwargs):
            seen["authorization"] = request.get_header("Authorization")
            return Response()

        with (
            patch.dict(os.environ, {"SKITARII_BEARER_TOKEN": "facade-secret"}),
            patch.object(ceraxia_service, "urlopen", side_effect=open_request),
        ):
            health = ceraxia_service.skitarii_backend_health(timeout_sec=1)
        self.assertTrue(health["healthy"])
        self.assertEqual(seen["authorization"], "Bearer facade-secret")

    def test_capabilities_keep_registry_preflight_on_legacy_adapter(self) -> None:
        with patch.dict(os.environ, {"SKITARII_URL": self.backend_url}):
            payload = ceraxia_service.service_capabilities()
        self.assert_compatibility_contract(payload)
        self.assertEqual(payload["pipeline"]["kind"], "code_task")
        self.assertEqual(payload["pipeline"]["step_count"], 6)
        self.assertEqual(payload["worker_availability"]["scope"], "legacy_six_worker_compatibility_adapter")
        self.assertNotIn("SkitariiWarband", payload["worker_availability"]["resolved_workers"])

    def test_leader_answer_is_authoritative_but_cannot_contain_a_file_plan(self) -> None:
        decision = unittest.mock.Mock(return_value=_directive_answer("Use the compatibility-safe outcome"))
        with patch.object(ceraxia_service, "request_model_decision", decision):
            directive, _model = ceraxia_service.request_leadership_directive(
                "fix the application",
                "leader-contract",
                _command("leader-contract"),
            )
        self.assertEqual(directive["mission_intent"], "Use the compatibility-safe outcome")
        self.assertEqual(directive["constraints"][0], "Preserve the Warmaster preflight contract.")
        self.assertEqual(
            directive["success_conditions"][0],
            "Ceraxia publishes one consistent execution contract",
        )
        self.assertEqual(
            directive["escalation_conditions"][0],
            "the requested behavior requires an irreversible product choice",
        )
        request_payload = decision.call_args.args[2]
        self.assertIn("forbidden_detailed_plan_fields", request_payload)
        instructions = decision.call_args.kwargs["instructions"]
        self.assertIn("Skitarii owns repository exploration", instructions)
        self.assertIn("exactly these seven literal top-level keys", instructions)
        self.assertIn("Do not echo task_id or delegation_subject", instructions)

        detailed = _directive_answer()
        content = json.loads(detailed["content"])
        content["files"] = ["app.py"]
        detailed["content"] = json.dumps(content)
        with (
            patch.object(ceraxia_service, "request_model_decision", return_value=detailed),
            self.assertRaisesRegex(ceraxia_service.CeraxiaDirectiveError, "detailed planning"),
        ):
            ceraxia_service.request_leadership_directive(
                "fix the application",
                "leader-contract",
                _command("leader-contract"),
            )

        optional_order = _command("optional-boundaries")
        optional_order.pop("constraints")
        optional_order.pop("escalate_to_user_if")
        optional_order["success_conditions"] *= 2
        with patch.object(
            ceraxia_service,
            "request_model_decision",
            return_value=_directive_answer(),
        ):
            optional_directive, _model = ceraxia_service.request_leadership_directive(
                "fix the application",
                "optional-boundaries",
                optional_order,
            )
        self.assertEqual(
            optional_directive["success_conditions"].count(
                "Ceraxia publishes one consistent execution contract",
            ),
            1,
        )

    def test_gemma_fenced_directive_ignores_only_code_owned_echo_fields(self) -> None:
        answer = _directive_answer()
        content = json.loads(answer["content"])
        content["task_id"] = "model-echo-must-not-own-identity"
        content["delegation_subject"] = "model-echo-must-not-own-task"
        answer["content"] = "```json\n" + json.dumps(content) + "\n```"
        with patch.object(
            ceraxia_service,
            "request_model_decision",
            return_value=answer,
        ):
            directive, _model = ceraxia_service.request_leadership_directive(
                "fix the application",
                "code-owned-task-id",
                _command("code-owned-task-id"),
            )
        self.assertEqual(directive["task_id"], "code-owned-task-id")
        self.assertEqual(directive["mission_id"], "mission-code-owned-task-id")
        self.assertNotIn("delegation_subject", directive)

        for extra_field, expected_error in (
            ("leader", "unknown fields"),
            ("files", "detailed planning"),
        ):
            invalid = dict(content, **{extra_field: "not allowed"})
            fenced = dict(answer, content="```json\n" + json.dumps(invalid) + "\n```")
            with (
                patch.object(
                    ceraxia_service,
                    "request_model_decision",
                    return_value=fenced,
                ),
                self.assertRaisesRegex(
                    ceraxia_service.CeraxiaDirectiveError,
                    expected_error,
                ),
            ):
                ceraxia_service.request_leadership_directive(
                    "fix the application",
                    "code-owned-task-id",
                    _command("code-owned-task-id"),
                )

        prose = dict(answer, content="Here is the answer:\n" + answer["content"])
        with (
            patch.object(
                ceraxia_service,
                "request_model_decision",
                return_value=prose,
            ),
            self.assertRaisesRegex(
                ceraxia_service.CeraxiaDirectiveError,
                "without surrounding prose",
            ),
        ):
            ceraxia_service.request_leadership_directive(
                "fix the application",
                "code-owned-task-id",
                _command("code-owned-task-id"),
            )

    def test_non_delegation_leader_outcomes_do_not_create_a_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "runs"
            handler = ceraxia_service.make_handler(run_root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                for leader_decision in ("reject", "escalate"):
                    task_id = f"leader-{leader_decision}"
                    with (
                        self.subTest(decision=leader_decision),
                        patch.object(
                            ceraxia_service,
                            "request_model_decision",
                            return_value=_directive_answer(decision=leader_decision),
                        ),
                        self.assertRaises(urllib.error.HTTPError) as caught,
                    ):
                        _request_json(
                            base + "/prepare_run",
                            {"task_id": task_id, "commander_order": _command(task_id)},
                        )
                    self.assertEqual(caught.exception.code, 409)
                    response_payload = json.loads(caught.exception.read().decode("utf-8"))
                    caught.exception.close()
                    self.assertEqual(
                        response_payload["leadership_directive"]["decision"],
                        leader_decision,
                    )
                    self.assertFalse((run_root / task_id).exists())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_all_public_run_contract_routes_report_the_same_execution_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            handler = ceraxia_service.make_handler(root / "runs")
            service = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            service_thread = threading.Thread(target=service.serve_forever, daemon=True)
            service_thread.start()
            base = f"http://127.0.0.1:{service.server_port}"
            answered = _directive_answer()
            try:
                with (
                    patch.dict(os.environ, {"SKITARII_URL": self.backend_url}),
                    patch.object(
                        ceraxia_service,
                        "request_model_decision",
                        return_value=answered,
                    ) as model_call,
                ):
                    health = _request_json(base + "/health")
                    capabilities = _request_json(base + "/capabilities")
                    plan = _request_json(
                        base + "/plan",
                        {"task_id": "facade-run", "commander_order": _command("facade-run"),
                         "repo_path": str(ceraxia_service.REPO_ROOT)},
                    )
                    callable_payload = _request_json(
                        base + "/callable_contract",
                        {"task_id": "facade-callable", "commander_order": _command("facade-callable")},
                    )
                    prepared = _request_json(
                        base + "/prepare_run",
                        {"task_id": "facade-run", "commander_order": _command("facade-run"),
                         "repo_path": str(ceraxia_service.REPO_ROOT)},
                    )
                    persisted_directive = json.loads(
                        (root / "runs" / "facade-run" / "ceraxia_directive.json").read_text(
                            encoding="utf-8",
                        ),
                    )
            finally:
                service.shutdown()
                service.server_close()
                service_thread.join(timeout=5)

        self.assertTrue(health["ok"])
        self.assertEqual(model_call.call_count, 3)  # /plan, /callable, and authoritative /prepare.
        self.assertTrue(health["readiness"])
        self.assertTrue(health["backend"]["healthy"])
        for payload in (capabilities, plan, callable_payload, prepared):
            self.assert_compatibility_contract(payload)
        self.assert_compatibility_contract(callable_payload["plan"])
        self.assertEqual(callable_payload["input_contract"]["required"], ["commander_order"])
        self.assertEqual(callable_payload["final_package_schema"]["kind"], "skitarii_bridge_result")
        self.assertEqual(plan["leadership_directive"]["leader"], "Ceraxia")
        self.assertEqual(plan["leadership_directive"]["delegated_to"], "SkitariiWarband")
        self.assertEqual(plan["leadership_directive"]["mission_intent"],
                         "Deliver a verified repair without changing unrelated behavior")
        self.assertEqual(prepared["leadership_directive"], persisted_directive)
        self.assertEqual(prepared["execution_contract"]["leadership"], "Ceraxia")
        self.assertEqual(prepared["execution_contract"]["detailed_planning"], "SkitariiWarband")
        self.assertFalse(
            {"steps", "work_plan", "worker_plan", "files", "commands"}
            & set(plan["leadership_directive"]),
        )
        self.assertIsInstance(plan["next_action"]["body"]["commander_order"], dict)
        self.assertIsInstance(callable_payload["next_action"]["body"]["commander_order"], dict)
        self.assertNotIn("<commander_order>", json.dumps((plan, callable_payload)))
        self.assertNotIn("<optional-run-dir>", json.dumps((plan, callable_payload)))
        self.assertIn(
            "Warmaster /runs/{task_id}/apply_patch",
            [step["endpoint"] for step in callable_payload["execution_flow"]],
        )
        self.assertNotIn("CERAXIA_TARGET_REPO:", plan["contract"]["goal"])
        self.assertIn("CERAXIA_REPOSITORY_SCOPE:", plan["contract"]["goal"])

    def test_http_boundary_and_cors_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler = ceraxia_service.make_handler(Path(temp_dir) / "runs")
            service = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=service.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{service.server_port}"
            try:
                self.assertTrue(ceraxia_service._peer_allowed("127.0.0.1"))
                self.assertFalse(ceraxia_service._peer_allowed("192.0.2.10"))
                self.assertEqual(
                    ceraxia_service._validate_bind_host("127.0.0.1"), "127.0.0.1",
                )
                with self.assertRaisesRegex(ValueError, "off loopback"):
                    ceraxia_service._validate_bind_host("0.0.0.0")
                with self.assertRaisesRegex(ValueError, "literal loopback"):
                    ceraxia_service._validate_bind_host("localhost")

                connection = http.client.HTTPConnection("127.0.0.1", service.server_port, timeout=5)
                connection.putrequest("GET", "/health", skip_host=True)
                connection.putheader("Host", "evil.example")
                connection.endheaders()
                bad_host = connection.getresponse()
                self.assertEqual(bad_host.status, 421)
                self.assertNotEqual(bad_host.getheader("Access-Control-Allow-Origin"), "*")
                bad_host.read()
                connection.close()

                cross_preflight = urllib.request.Request(
                    base + "/prepare_run",
                    method="OPTIONS",
                    headers={
                        "Origin": "https://evil.example",
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": "content-type",
                    },
                )
                with self.assertRaises(urllib.error.HTTPError) as denied_preflight:
                    urllib.request.urlopen(cross_preflight, timeout=5)
                self.assertEqual(denied_preflight.exception.code, 403)
                self.assertIsNone(
                    denied_preflight.exception.headers.get("Access-Control-Allow-Origin"),
                )
                denied_preflight.exception.close()

                same_origin_preflight = urllib.request.Request(
                    base + "/prepare_run",
                    method="OPTIONS",
                    headers={
                        "Origin": base,
                        "Access-Control-Request-Method": "POST",
                    },
                )
                with urllib.request.urlopen(same_origin_preflight, timeout=5) as response:
                    self.assertEqual(response.status, 204)
                    self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), base)
                    self.assertNotEqual(response.headers.get("Access-Control-Allow-Origin"), "*")

                trusted = "https://ceraxia-ui.example"
                trusted_preflight = urllib.request.Request(
                    base + "/plan", method="OPTIONS", headers={"Origin": trusted},
                )
                with patch.dict(os.environ, {"CERAXIA_TRUSTED_ORIGINS": trusted}):
                    with urllib.request.urlopen(trusted_preflight, timeout=5) as response:
                        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), trusted)

                cross_post = urllib.request.Request(
                    base + "/plan",
                    data=b"{}",
                    method="POST",
                    headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
                )
                with self.assertRaises(urllib.error.HTTPError) as denied_post:
                    urllib.request.urlopen(cross_post, timeout=5)
                self.assertEqual(denied_post.exception.code, 403)
                denied_post.exception.close()
            finally:
                service.shutdown()
                service.server_close()
                thread.join(timeout=5)

    def test_post_body_limit_content_type_and_optional_bearer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler = ceraxia_service.make_handler(Path(temp_dir) / "runs")
            service = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=service.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{service.server_port}"
            payload = {"task_id": "auth-plan", "commander_order": _command("auth-plan")}
            try:
                wrong_type = urllib.request.Request(
                    base + "/plan", data=b"{}", method="POST",
                    headers={"Content-Type": "text/plain"},
                )
                with self.assertRaises(urllib.error.HTTPError) as wrong_type_error:
                    urllib.request.urlopen(wrong_type, timeout=5)
                self.assertEqual(wrong_type_error.exception.code, 415)
                wrong_type_error.exception.close()

                connection = http.client.HTTPConnection("127.0.0.1", service.server_port, timeout=5)
                connection.putrequest("POST", "/plan")
                connection.putheader("Content-Type", "application/json")
                connection.putheader(
                    "Content-Length", str(ceraxia_service.MAX_CERAXIA_REQUEST_BYTES + 1),
                )
                connection.endheaders()
                oversized = connection.getresponse()
                self.assertEqual(oversized.status, 400)
                self.assertIn("exceeds", oversized.read().decode("utf-8"))
                connection.close()

                answered = _directive_answer()
                with (
                    patch.dict(
                        os.environ,
                        {
                            "CERAXIA_BEARER_TOKEN": "facade-secret",
                            "SKITARII_URL": self.backend_url,
                        },
                    ),
                    patch.object(ceraxia_service, "request_model_decision", return_value=answered),
                ):
                    with self.assertRaises(urllib.error.HTTPError) as missing_auth:
                        _request_json(base + "/plan", payload)
                    self.assertEqual(missing_auth.exception.code, 401)
                    missing_auth.exception.close()
                    with self.assertRaises(urllib.error.HTTPError) as wrong_auth:
                        _request_json(
                            base + "/plan", payload,
                            headers={"Authorization": "Bearer wrong"},
                        )
                    self.assertEqual(wrong_auth.exception.code, 401)
                    wrong_auth.exception.close()
                    accepted = _request_json(
                        base + "/plan", payload,
                        headers={"Authorization": "Bearer facade-secret"},
                    )
                self.assertTrue(accepted["ok"])
                self.assertNotIn("facade-secret", json.dumps(accepted))
            finally:
                service.shutdown()
                service.server_close()
                thread.join(timeout=5)

    def test_warmaster_scopes_ceraxia_bearer_to_ceraxia_only(self) -> None:
        from eye_of_terror import task_prepare

        calls = []

        def fake_post(url, payload, timeout_sec=120.0, *, headers=None):
            calls.append({"url": url, "payload": payload, "headers": dict(headers or {})})
            return {"ok": True}

        with (
            patch.dict(os.environ, {"CERAXIA_BEARER_TOKEN": "scoped-secret"}),
            patch.object(task_prepare, "post_json", side_effect=fake_post),
        ):
            task_prepare._post_governor_json(
                "http://127.0.0.1:7104/plan", {"x": 1}, "Ceraxia",
            )
            task_prepare._post_governor_json(
                "http://127.0.0.1:7103/plan", {"x": 2}, "IskandarKhayon",
            )
        self.assertEqual(
            calls[0]["headers"].get("Authorization"), "Bearer scoped-secret",
        )
        self.assertNotIn("Authorization", calls[1]["headers"])
        self.assertNotIn("scoped-secret", json.dumps(calls[1]))

    def test_local_ceraxia_transport_cannot_bypass_leader_directive(self) -> None:
        from eye_of_terror import task_prepare

        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir)
            order = _command("local-bypass")
            prepared = task_prepare.prepare_task(
                "raw task must not bypass Ceraxia",
                "local-bypass",
                run_root,
                governor_transport="local",
                forced_governor="Ceraxia",
                commander_order=order,
            )
            preflight = task_prepare.preflight_task(
                "raw task must not bypass Ceraxia",
                "local-bypass",
                run_root,
                governor_transport="local",
                forced_governor="Ceraxia",
                commander_order=order,
            )
            self.assertFalse((run_root / "local-bypass").exists())
        self.assertEqual(prepared["error_code"], "ceraxia_leader_service_required")
        self.assertEqual(preflight["error_code"], "ceraxia_leader_service_required")

    def test_cleanup_refuses_run_root_and_symlink(self) -> None:
        from eye_of_terror import task_prepare

        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "runs"
            run_root.mkdir()
            sentinel = run_root / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")

            root_result = task_prepare.cleanup_unregistered_run_dir(run_root, run_root)
            self.assertFalse(root_result["attempted"])
            self.assertEqual(root_result["reason"], "run_dir is run_root")
            self.assertTrue(sentinel.exists())

            symlink = run_root / "forged-task"
            symlink.symlink_to(run_root, target_is_directory=True)
            symlink_result = task_prepare.cleanup_unregistered_run_dir(run_root, symlink)
            self.assertFalse(symlink_result["attempted"])
            self.assertEqual(symlink_result["reason"], "run_dir is a symlink")
            self.assertTrue(sentinel.exists())

    def test_http_preflight_cannot_ignore_a_reject_directive(self) -> None:
        from eye_of_terror import task_prepare

        task_id = "http-reject"
        order = _command(task_id)
        with patch.object(
            ceraxia_service,
            "request_model_decision",
            return_value=_directive_answer(decision="reject"),
        ):
            directive, _model = ceraxia_service.request_leadership_directive(
                "do not execute",
                task_id,
                order,
            )
        plan_payload = {
            "ok": True,
            "contract": {"task_id": task_id},
            "leadership_directive": directive,
        }
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(task_prepare, "_post_governor_json", return_value=plan_payload),
            patch.object(
                task_prepare,
                "attach_governor_plan_payload",
                side_effect=lambda payload, *_args, **_kwargs: payload,
            ),
            patch.object(task_prepare, "fetch_service_capabilities", return_value={"ok": False}),
        ):
            result = task_prepare.preflight_task(
                "do not execute",
                task_id,
                Path(temp_dir),
                governor_transport="http",
                forced_governor="Ceraxia",
                commander_order=order,
            )
        self.assertEqual(result["error_code"], "ceraxia_delegation_not_authorized")
        self.assertEqual(result["leadership_directive"]["decision"], "reject")

    def test_task_prepare_trusts_the_persisted_prepare_directive(self) -> None:
        from eye_of_terror import task_prepare

        directive = {
            "kind": "ceraxia_leadership_directive",
            "version": 1,
            "task_id": "prepared-directive",
            "mission_id": "mission-prepared-directive",
            "leader": "Ceraxia",
            "decision": "delegate",
            "delegated_to": "SkitariiWarband",
            "mission_intent": "Deliver the requested verified outcome",
            "priorities": ["correctness"],
            "constraints": ["preserve caller constraints"],
            "success_conditions": ["behavior is verified"],
            "tradeoffs": [],
            "escalation_conditions": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "ceraxia_directive.json").write_text(
                json.dumps(directive),
                encoding="utf-8",
            )
            accepted = task_prepare.validated_prepared_ceraxia_directive(
                run_dir,
                {"leadership_directive": directive},
                "prepared-directive",
                "mission-prepared-directive",
            )
            self.assertEqual(accepted, directive)
            with self.assertRaisesRegex(
                task_prepare.CeraxiaDirectiveError,
                "do not match",
            ):
                task_prepare.validated_prepared_ceraxia_directive(
                    run_dir,
                    {"leadership_directive": dict(directive, mission_intent="different")},
                    "prepared-directive",
                    "mission-prepared-directive",
                )

    def test_prepare_run_is_bound_to_exact_task_scoped_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            run_root = base_dir / "ceraxia-runs"
            handoff_root = base_dir / "warmaster-runs"
            run_root.mkdir()
            handoff_root.mkdir()
            task_id = "scoped-run"
            with patch.dict(os.environ, {"WARMMASTER_RUN_ROOT": str(handoff_root)}):
                self.assertEqual(
                    ceraxia_service.resolve_run_dir(run_root, "", task_id),
                    run_root / task_id,
                )
                self.assertEqual(
                    ceraxia_service.resolve_run_dir(
                        run_root, str(handoff_root / task_id), task_id,
                    ),
                    handoff_root / task_id,
                )
                for unsafe in (
                    run_root,
                    run_root / "another-run",
                    run_root / task_id / "nested",
                    base_dir / "outside" / task_id,
                ):
                    with self.subTest(unsafe=unsafe):
                        with self.assertRaisesRegex(ValueError, "exact task-scoped"):
                            ceraxia_service.resolve_run_dir(run_root, str(unsafe), task_id)

                outside = base_dir / "outside-target"
                outside.mkdir()
                linked = run_root / task_id
                linked.symlink_to(outside, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "symlink"):
                    ceraxia_service.resolve_run_dir(run_root, str(linked), task_id)
                linked.unlink()

                handler = ceraxia_service.make_handler(run_root)
                service = ThreadingHTTPServer(("127.0.0.1", 0), handler)
                thread = threading.Thread(target=service.serve_forever, daemon=True)
                thread.start()
                endpoint = f"http://127.0.0.1:{service.server_port}"
                answered = _directive_answer()
                decision = unittest.mock.Mock(return_value=answered)
                try:
                    with (
                        patch.dict(os.environ, {"SKITARII_URL": self.backend_url}),
                        patch.object(ceraxia_service, "request_model_decision", decision),
                    ):
                        with self.assertRaises(urllib.error.HTTPError) as cross_run:
                            _request_json(
                                endpoint + "/prepare_run",
                                {
                                    "task_id": task_id,
                                    "run_dir": str(run_root / "another-run"),
                                    "commander_order": _command(task_id),
                                },
                            )
                        self.assertEqual(cross_run.exception.code, 400)
                        cross_run.exception.close()
                        decision.assert_not_called()

                        prepared = _request_json(
                            endpoint + "/prepare_run",
                            {"task_id": task_id, "commander_order": _command(task_id)},
                        )
                        with self.assertRaises(urllib.error.HTTPError) as duplicate:
                            _request_json(
                                endpoint + "/prepare_run",
                                {"task_id": task_id, "commander_order": _command(task_id)},
                            )
                        self.assertEqual(duplicate.exception.code, 409)
                        duplicate.exception.close()
                    self.assertTrue(prepared["ok"])
                    self.assertTrue((run_root / task_id / "contract.json").is_file())
                    self.assertEqual(decision.call_count, 1)
                finally:
                    service.shutdown()
                    service.server_close()
                    thread.join(timeout=5)

    def test_prepare_failure_removes_only_its_atomic_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "runs"
            handler = ceraxia_service.make_handler(run_root)
            service = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=service.serve_forever, daemon=True)
            thread.start()
            endpoint = f"http://127.0.0.1:{service.server_port}"
            task_id = "rollback-run"
            try:
                with (
                    patch.object(
                        ceraxia_service, "request_model_decision",
                        return_value=_directive_answer(),
                    ),
                    patch.object(
                        ceraxia_service, "write_pipeline_run",
                        side_effect=RuntimeError("injected preparation failure"),
                    ),
                ):
                    with self.assertRaises(urllib.error.HTTPError) as failed:
                        _request_json(
                            endpoint + "/prepare_run",
                            {"task_id": task_id, "commander_order": _command(task_id)},
                        )
                self.assertEqual(failed.exception.code, 500)
                failed.exception.close()
                self.assertFalse(os.path.lexists(run_root / task_id))
            finally:
                service.shutdown()
                service.server_close()
                thread.join(timeout=5)

    def test_external_repo_path_is_rejected_instead_of_misrouted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler = ceraxia_service.make_handler(Path(temp_dir) / "runs")
            service = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=service.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(
                    ceraxia_service, "request_model_decision",
                    return_value=_directive_answer(),
                ):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        _request_json(
                            f"http://127.0.0.1:{service.server_port}/prepare_run",
                            {"task_id": "wrong-repo", "commander_order": _command("wrong-repo"),
                             "repo_path": str(Path(temp_dir) / "some-other-repo")},
                        )
                self.assertEqual(caught.exception.code, 400)
            finally:
                service.shutdown()
                service.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
