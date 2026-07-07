#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.request
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOCAL_ROOT = Path(__file__).resolve().parent
for path in (PROJECT_ROOT, LOCAL_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from EyeOfTerror.common_protocol import validate_protocol_payload, worker_order
import planning_brigade
from planning_packet_contract import REQUIRED_PACKET_OBJECTS, ROLE_ORDER
from role_service import make_handler, role_capabilities, run_role_plan
from start_role_services import build_supervisor_manifest


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        data = response.read().decode("utf-8")
    parsed = json.loads(data)
    if not isinstance(parsed, dict):
        raise AssertionError(f"HTTP response must be an object: {parsed}")
    return parsed


class PlanningRoleServiceTests(unittest.TestCase):
    def planning_order(self, role: str = "TaskTriage", step_id: str = "task_triage") -> dict[str, Any]:
        return worker_order(
            mission_id="mission-planning-role-worker-order-self-test",
            step_id=step_id,
            sender="Ceraxia",
            to=role,
            task="почини security API pytest в `app.py`",
            expected_output=f"{role} planning artifacts",
            revision_context={"repo_path": "/repo"},
            quality_requirements=["return a shared worker_report"],
        )

    def test_role_services_can_build_required_packet_objects_in_order(self) -> None:
        payload = {
            "task": "почини security API pytest в `app.py`",
            "repo_path": "/repo",
            "constraints": ["preserve public API response shape"],
        }
        context: dict[str, Any] = {"payload": payload}
        trace: list[dict[str, Any]] = []
        for role_name in ROLE_ORDER:
            result = run_role_plan(role_name, {"worker_order": self.planning_order(role_name, role_name.lower()), "payload": payload, "context": context})
            self.assertEqual(result["status"], "completed", result)
            self.assertTrue(result["read_only"], result)
            self.assertEqual(result["protocol_mode"], "worker_order", result)
            validate_protocol_payload(result["worker_report"], expected_type="worker_report")
            outputs = result["outputs"]
            context.update(outputs)
            trace.append({"role": role_name, "outputs": result["output_artifacts"]})
        missing = [name for name in REQUIRED_PACKET_OBJECTS if name not in context]
        self.assertEqual(missing, [])
        self.assertEqual([row["role"] for row in trace], ROLE_ORDER)
        packet = planning_brigade.build_planning_packet(payload)
        self.assertEqual(context["task_triage"], packet["task_triage"])
        self.assertEqual(context["repo_survey_request"], packet["repo_survey_request"])
        self.assertEqual(context["planning_review_gate"]["decision"], packet["planning_review_gate"]["decision"])

    def test_task_triage_http_service_exposes_work_endpoint(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler("TaskTriage"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            with self.assertRaises(HTTPError) as missing_order:
                post_json(f"http://{host}:{port}/work", {"payload": {"task": "raw bypass"}})
            self.assertEqual(missing_order.exception.code, 400)
            with self.assertRaises(HTTPError) as removed_plan:
                post_json(
                    f"http://{host}:{port}/plan",
                    {"worker_order": self.planning_order(), "payload": {"task": "добавь pytest для `app.py`", "repo_path": "/repo"}},
                )
            self.assertEqual(removed_plan.exception.code, 404)
            response = post_json(
                f"http://{host}:{port}/work",
                {"worker_order": self.planning_order(), "payload": {"repo_path": "/repo"}},
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual(response["role"], "TaskTriage")
        self.assertEqual(response["protocol_mode"], "worker_order")
        validate_protocol_payload(response["worker_report"], expected_type="worker_report")
        self.assertIn("task_triage", response["outputs"])
        self.assertIn("problem_statement", response["outputs"])

    def test_task_triage_work_endpoint_requires_worker_order_and_returns_worker_report(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler("TaskTriage"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            with self.assertRaises(HTTPError) as raised:
                post_json(f"http://{host}:{port}/work", {"payload": {"task": "raw bypass"}})
            self.assertEqual(raised.exception.code, 400)
            response = post_json(
                f"http://{host}:{port}/work",
                {"worker_order": self.planning_order(), "payload": {"repo_path": "/repo"}},
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual(response["protocol_mode"], "worker_order", response)
        validate_protocol_payload(response["worker_order"], expected_type="worker_order")
        validate_protocol_payload(response["worker_report"], expected_type="worker_report")
        self.assertEqual(response["worker_report"]["mission_id"], "mission-planning-role-worker-order-self-test")
        self.assertEqual(response["worker_report"]["step_id"], "task_triage")
        self.assertEqual(response["worker_report"]["worker"], "TaskTriage")
        self.assertIn("task_triage", response["outputs"])
        self.assertIn("security", response["outputs"]["task_triage"]["task_kinds"])

    def test_worker_order_task_is_authoritative_over_payload_task(self) -> None:
        order = self.planning_order()
        result = run_role_plan(
            "TaskTriage",
            {
                "worker_order": order,
                "payload": {
                    "task": "raw payload override must be ignored",
                    "goal": "raw goal override must be ignored",
                    "message": "raw message override must be ignored",
                    "repo_path": "/repo",
                },
            },
        )
        triage = result["outputs"]["task_triage"]
        self.assertIn("security", triage["task_kinds"])
        self.assertIn("api_compatibility", triage["task_kinds"])
        self.assertIn("test_repair", triage["task_kinds"])
        self.assertEqual(triage["risk_level"], "high")
        validate_protocol_payload(result["worker_report"], expected_type="worker_report")

    def test_capabilities_match_service_contract(self) -> None:
        capabilities = role_capabilities("RiskScribe")
        self.assertEqual(capabilities["role"], "RiskScribe")
        self.assertIn("POST /work", capabilities["endpoints"])
        self.assertNotIn("POST /plan", capabilities["endpoints"])
        self.assertEqual(capabilities["protocol"]["strict_endpoint"], "POST /work")
        self.assertNotIn("legacy_endpoint", capabilities["protocol"])
        self.assertEqual(capabilities["service_contract"]["port"], 7115)
        self.assertFalse(capabilities["service_contract"]["may_mutate_source"])

    def test_supervisor_manifest_starts_all_roles_on_reserved_ports(self) -> None:
        manifest = build_supervisor_manifest()
        self.assertEqual(manifest["kind"], "planning_brigade_role_service_supervisor_manifest")
        self.assertEqual(manifest["service_count"], 5)
        self.assertEqual(manifest["role_order"], ROLE_ORDER)
        self.assertEqual(manifest["ports"], [7111, 7112, 7113, 7114, 7115])
        self.assertTrue(manifest["ports_unique"])
        self.assertTrue(manifest["read_only"])
        by_role = {service["role"]: service for service in manifest["services"]}
        self.assertEqual(by_role["TaskTriage"]["work_url"], "http://127.0.0.1:7111/work")
        self.assertNotIn("plan_url", by_role["TaskTriage"])
        self.assertEqual(by_role["RiskScribe"]["handoff_to"], "Ceraxia")
        for role in ROLE_ORDER:
            self.assertIn("role_service.py", " ".join(by_role[role]["command"]))


if __name__ == "__main__":
    unittest.main()
