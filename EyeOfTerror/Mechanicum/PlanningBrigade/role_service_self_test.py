#!/usr/bin/env python3
from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from typing import Any

import planning_brigade
from planning_packet_contract import REQUIRED_PACKET_OBJECTS, ROLE_ORDER
from role_service import make_handler, role_capabilities, run_role_plan


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
    def test_role_services_can_build_required_packet_objects_in_order(self) -> None:
        payload = {
            "task": "почини security API pytest в `app.py`",
            "repo_path": "/repo",
            "constraints": ["preserve public API response shape"],
        }
        context: dict[str, Any] = {"payload": payload}
        trace: list[dict[str, Any]] = []
        for role_name in ROLE_ORDER:
            result = run_role_plan(role_name, {"payload": payload, "context": context})
            self.assertEqual(result["status"], "completed", result)
            self.assertTrue(result["read_only"], result)
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

    def test_task_triage_http_service_exposes_plan_endpoint(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler("TaskTriage"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            response = post_json(
                f"http://{host}:{port}/plan",
                {"payload": {"task": "добавь pytest для `app.py`", "repo_path": "/repo"}},
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual(response["role"], "TaskTriage")
        self.assertIn("task_triage", response["outputs"])
        self.assertIn("problem_statement", response["outputs"])

    def test_capabilities_match_service_contract(self) -> None:
        capabilities = role_capabilities("RiskScribe")
        self.assertEqual(capabilities["role"], "RiskScribe")
        self.assertIn("POST /plan", capabilities["endpoints"])
        self.assertEqual(capabilities["service_contract"]["port"], 7115)
        self.assertFalse(capabilities["service_contract"]["may_mutate_source"])


if __name__ == "__main__":
    unittest.main()
