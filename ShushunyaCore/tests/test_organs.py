from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ShushunyaCore.organs import OrganError, Organs


class FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.content = b"json"
        self.text = "raw response must never reach the conversation contract"

    def json(self):
        return self._body


class FakeClient:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.last_json = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *_args, **_kwargs):
        self.last_json = _kwargs.get("json")
        return self.response


class AbaddonOutcomeTests(unittest.IsolatedAsyncioTestCase):
    def organs(self) -> Organs:
        return Organs(SimpleNamespace(abaddon_base_url="http://127.0.0.1:7000"))

    def payload(self) -> dict:
        return {
            "message": "Собери Galaga для Android",
            "task_id": "core-galaga",
            "idempotency_key": "effect-galaga",
            "warmaster_request": {
                "user_request": "Собери Galaga для Android",
                "expected_outcome": "Рабочий APK",
                "known_missing_inputs": [
                    "Предпочтительный движок (Unity, Godot или Native)",
                    "Графический стиль (пиксель-арт или современный)",
                ],
            },
        }

    async def test_structured_clarification_becomes_typed_non_retryable_decision(self):
        response = FakeResponse(
            409,
            {
                "ok": False,
                "client_action": {
                    "kind": "inspect_preflight",
                    "method": "POST",
                    "path": "/task_preflight",
                    "reason": "ceraxia_delegation_not_authorized",
                },
                "prepare": {
                    "task": {
                        "error_code": "ceraxia_delegation_not_authorized",
                        "leadership_directive": {
                            "decision": "needs_clarification",
                            "mission_intent": "Build a working Android Galaga APK.",
                            "escalation_conditions": ["Есть обязательный движок, или мне выбрать самому?"],
                        },
                    }
                },
            },
        )
        with patch("ShushunyaCore.organs.httpx.AsyncClient", return_value=FakeClient(response)):
            with self.assertRaises(OrganError) as caught:
                await self.organs().dispatch_abaddon(self.payload())

        error = caught.exception
        self.assertEqual(error.code, "clarification_required")
        self.assertFalse(error.retryable)
        self.assertEqual(error.evidence["outcome_type"], "needs_user_decision")
        request = error.evidence["decision_request"]
        self.assertEqual(request["task_id"], "core-galaga")
        self.assertIn("движок", request["question"])
        self.assertEqual(request["recommended_option"], "use_reasonable_defaults")
        self.assertEqual(request["options"][0]["id"], "use_reasonable_defaults")
        self.assertEqual(request["resume"]["kind"], "retry_preflight_with_answer")
        self.assertEqual(request["resume"]["path"], "/orchestrate_run")
        self.assertEqual(request["resume"]["body"]["task_id"], "core-galaga")
        self.assertEqual(request["resume"]["body"]["message"], self.payload()["message"])
        self.assertNotIn("response", error.evidence)
        self.assertNotIn("HTTP 409", error.explanation)

    async def test_missing_preferences_without_exact_question_do_not_interrupt_user(self):
        response = FakeResponse(
            409,
            {
                "ok": False,
                "next_action": {
                    "kind": "inspect_preflight",
                    "method": "POST",
                    "path": "/task_preflight",
                },
                "leadership_directive": {
                    "decision": "needs_clarification",
                    "mission_intent": "Build a working Android Galaga APK.",
                    "escalation_conditions": ["engine and visual style were not selected"],
                },
            },
        )
        with patch("ShushunyaCore.organs.httpx.AsyncClient", return_value=FakeClient(response)):
            with self.assertRaises(OrganError) as caught:
                await self.organs().dispatch_abaddon(self.payload())

        error = caught.exception
        self.assertEqual(error.code, "abaddon_repair_required")
        self.assertEqual(error.evidence["outcome_type"], "repair_required")
        self.assertNotIn("decision_request", error.evidence)

    async def test_published_preflight_action_is_repair_not_blind_retry(self):
        response = FakeResponse(
            409,
            {
                "ok": False,
                "next_action": {
                    "kind": "inspect_preflight",
                    "method": "POST",
                    "path": "/task_preflight",
                    "reason": "ceraxia_delegation_not_authorized",
                },
            },
        )
        with patch("ShushunyaCore.organs.httpx.AsyncClient", return_value=FakeClient(response)):
            with self.assertRaises(OrganError) as caught:
                await self.organs().dispatch_abaddon(self.payload())

        error = caught.exception
        self.assertEqual(error.code, "abaddon_repair_required")
        self.assertFalse(error.retryable)
        self.assertEqual(error.evidence["outcome_type"], "repair_required")
        self.assertEqual(error.evidence["repair_action"]["path"], "/task_preflight")

    async def test_transient_server_failure_remains_retryable_without_raw_body(self):
        response = FakeResponse(503, {"ok": False, "error_code": "temporarily_unavailable"})
        with patch("ShushunyaCore.organs.httpx.AsyncClient", return_value=FakeClient(response)):
            with self.assertRaises(OrganError) as caught:
                await self.organs().dispatch_abaddon(self.payload())

        error = caught.exception
        self.assertEqual(error.code, "abaddon_rejected")
        self.assertTrue(error.retryable)
        self.assertEqual(error.evidence["outcome_type"], "transient_failure")
        self.assertNotIn("response", error.evidence)

    async def test_continuation_creates_new_run_linked_to_immutable_parent(self):
        payload = self.payload()
        payload["task_id"] = "core-galaga-continuation"
        payload["parent_task_id"] = "core-galaga-failed"
        payload["continuation_of"] = "core-galaga-failed"
        response = FakeResponse(
            202,
            {
                "ok": True,
                "task_id": "core-galaga-continuation",
                "phase": "started",
                "next_action": {"kind": "poll"},
            },
        )
        client = FakeClient(response)
        with patch("ShushunyaCore.organs.httpx.AsyncClient", return_value=client):
            result = await self.organs().dispatch_abaddon(payload)

        self.assertTrue(result["ok"])
        self.assertEqual(result["delegate_ref"], "core-galaga-continuation")
        self.assertNotEqual(client.last_json["task_id"], client.last_json["parent_task_id"])
        self.assertEqual(client.last_json["parent_task_id"], "core-galaga-failed")
        self.assertEqual(client.last_json["continuation_of"], "core-galaga-failed")

    async def test_continuation_cannot_reuse_terminal_parent_identity(self):
        payload = self.payload()
        payload["parent_task_id"] = payload["task_id"]
        with self.assertRaises(OrganError) as caught:
            await self.organs().dispatch_abaddon(payload)
        self.assertEqual(caught.exception.code, "invalid_abaddon_continuation")


if __name__ == "__main__":
    unittest.main()
