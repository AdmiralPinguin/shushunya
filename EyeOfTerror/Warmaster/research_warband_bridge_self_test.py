from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eye_of_terror.ledger import TaskLedger
from eye_of_terror import research_warband_bridge as bridge


TASK_ID = "research-bridge-test"
MISSION_ID = "mission-research-bridge-test"
ENVELOPE = {
    "mission_id": MISSION_ID,
    "task_id": TASK_ID,
    "leadership_directive": {"mission_id": MISSION_ID, "task_id": TASK_ID},
    "commander_order": {"mission_id": MISSION_ID},
}


def accepted_result() -> dict:
    return {
        "outcome": "accepted",
        "reason": "accepted",
        "external_evaluator_result": {
            "status": "accepted",
            "accepted": True,
            "final_text": "Проверенный исследовательский ответ.",
        },
        "pipeline_audit": {
            "verification_report": {
                "accepted": True,
                "integrity_ok": True,
                "issues": [],
            }
        },
    }


class ResearchWarbandBridgeTest(unittest.TestCase):
    def test_bearer_transport_is_proxyless_exact_url_and_strict_json(self) -> None:
        class FakeResponse:
            status = 200

            def __init__(self, raw: bytes, *, url: str, content_type: str) -> None:
                self.raw = raw
                self.url = url
                self.headers = {
                    "Content-Type": content_type,
                    "Content-Length": str(len(raw)),
                }

            def geturl(self) -> str:
                return self.url

            def read(self, _limit: int = -1) -> bytes:
                return self.raw

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

        target = bridge.DEFAULT_RESEARCH_WARBAND_URL + "/health"
        proxy_handlers = [
            item
            for item in bridge._PRIVATE_OPENER.handlers
            if isinstance(item, bridge.urllib.request.ProxyHandler)
        ]
        self.assertEqual(proxy_handlers, [])
        self.assertTrue(
            any(isinstance(item, bridge._NoRedirect) for item in bridge._PRIVATE_OPENER.handlers)
        )

        with (
            patch.dict(
                os.environ,
                {
                    "RESEARCH_WARBAND_BEARER_TOKEN": (
                        "research-warband-transport-test-0123456789abcdef"
                    )
                },
            ),
            patch.object(
                bridge._PRIVATE_OPENER,
                "open",
                return_value=FakeResponse(
                    b'{"ok":true}', url=target, content_type="application/json; charset=utf-8"
                ),
            ) as private_open,
            patch.object(
                bridge.urllib.request,
                "urlopen",
                side_effect=AssertionError("global urlopen must not be used"),
            ),
        ):
            status, payload = bridge._json_request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True})
        private_open.assert_called_once()

        invalid_responses = (
            FakeResponse(b'{"ok":true}', url=target + "/redirected", content_type="application/json"),
            FakeResponse(b'{"ok":true}', url=target, content_type="text/plain"),
            FakeResponse(b'{"ok":true,"ok":false}', url=target, content_type="application/json"),
        )
        with patch.dict(
            os.environ,
            {
                "RESEARCH_WARBAND_BEARER_TOKEN": (
                    "research-warband-transport-test-0123456789abcdef"
                )
            },
        ):
            for response in invalid_responses:
                with self.subTest(response=response.raw, url=response.url):
                    with patch.object(
                        bridge._PRIVATE_OPENER, "open", return_value=response
                    ):
                        with self.assertRaises(bridge.ResearchWarbandBridgeError):
                            bridge._json_request("GET", "/health")

    def make_run(self, root: Path) -> Path:
        run_dir = root / TASK_ID
        run_dir.mkdir()
        ledger = TaskLedger.create(
            run_dir / "task_ledger.json", TASK_ID, "Исследуй вопрос", "IskandarKhayon"
        )
        ledger.set_status("running")
        return run_dir

    def test_runtime_inspection_uses_exact_authenticated_client_and_identity(self) -> None:
        request_hash = bridge._request_sha256(ENVELOPE)
        snapshot = {
            "id": MISSION_ID,
            "request_sha256": request_hash,
            "status": "running",
            "inflight": True,
            "cleanup_complete": False,
        }
        with patch.object(
            bridge, "_json_request", return_value=(200, snapshot),
        ) as request:
            inspected = bridge.inspect_research_warband_mission(
                MISSION_ID, request_hash, timeout_sec=1.25,
            )
        self.assertEqual(inspected, snapshot)
        request.assert_called_once_with(
            "GET", f"/missions/{MISSION_ID}", timeout=1.25,
        )

        foreign = dict(snapshot)
        foreign["id"] = "foreign-mission"
        with (
            patch.object(bridge, "_json_request", return_value=(200, foreign)),
            self.assertRaisesRegex(
                bridge.ResearchWarbandBridgeError,
                "mission identity changed",
            ),
        ):
            bridge.inspect_research_warband_mission(
                MISSION_ID, request_hash, timeout_sec=1.25,
            )

    def test_happy_path_is_adoptable_and_finishes_only_after_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = self.make_run(Path(raw))
            TaskLedger.load(run_dir / "task_ledger.json").force_status("created")
            request_hash = bridge._request_sha256(ENVELOPE)
            calls: list[tuple[str, str]] = []

            def fake_request(method: str, path: str, **_kwargs):
                calls.append((method, path))
                if calls == [("GET", f"/missions/{MISSION_ID}")]:
                    return 404, {"error": "mission not found"}
                if method == "POST" and path == "/missions":
                    return 202, {
                        "mission_id": MISSION_ID,
                        "status": "queued",
                        "request_sha256": request_hash,
                        "idempotent": False,
                    }
                if len(calls) == 3:
                    return 200, {
                        "id": MISSION_ID,
                        "request_sha256": request_hash,
                        "status": "running",
                        "attempt": 1,
                        "inflight": True,
                        "cleanup_complete": False,
                    }
                return 200, {
                    "id": MISSION_ID,
                    "request_sha256": request_hash,
                    "status": "done",
                    "attempt": 1,
                    "result": accepted_result(),
                    "inflight": False,
                    "cleanup_complete": True,
                }

            with (
                patch.object(
                    bridge, "load_research_warband_envelope", return_value=ENVELOPE
                ),
                patch.object(bridge, "_json_request", side_effect=fake_request),
                patch.object(bridge, "_finalize_protocol") as finalize,
            ):
                result = bridge.run_via_research_warband(
                    run_dir, TASK_ID, timeout_sec=5
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["summary"], "Проверенный исследовательский ответ.")
            ledger = TaskLedger.load(run_dir / "task_ledger.json").to_dict()
            self.assertEqual(ledger["status"], "completed")
            self.assertEqual(
                ledger["research_warband_mission"]["request_sha256"], request_hash
            )
            self.assertTrue(
                ledger["research_warband_mission"]["bridge_activity_announced"]
            )
            finalize.assert_called_once()

    def test_remote_activity_transitions_created_ledger_once(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = self.make_run(Path(raw))
            TaskLedger.load(run_dir / "task_ledger.json").force_status("created")
            with patch.object(
                bridge,
                "_mission_dir",
                side_effect=bridge.ResearchWarbandBridgeError("no protocol fixture"),
            ):
                bridge._record_remote_activity(
                    run_dir, TASK_ID, MISSION_ID, "running"
                )
                first = TaskLedger.load(run_dir / "task_ledger.json").to_dict()
                bridge._record_remote_activity(
                    run_dir, TASK_ID, MISSION_ID, "running"
                )
                second = TaskLedger.load(run_dir / "task_ledger.json").to_dict()
            self.assertEqual(first["status"], "running")
            self.assertTrue(
                first["research_warband_mission"]["bridge_activity_announced"]
            )
            self.assertEqual(
                sum(
                    event.get("type") == "research_warband_execution_started"
                    for event in second.get("events", [])
                    if isinstance(event, dict)
                ),
                1,
            )

    def test_internal_contract_error_fails_after_exact_cleanup_is_proven(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = self.make_run(Path(raw))
            request_hash = bridge._request_sha256(ENVELOPE)
            poll_count = 0
            cancelled = False

            def fake_request(method: str, path: str, **_kwargs):
                nonlocal poll_count, cancelled
                if method == "POST" and path.endswith("/cancel"):
                    cancelled = True
                    return 200, {"ok": True, "status": "cancelling"}
                if method == "GET":
                    poll_count += 1
                    if poll_count == 1:
                        return 404, {"error": "mission not found"}
                    if poll_count == 2:
                        return 200, {
                            "id": MISSION_ID,
                            "request_sha256": request_hash,
                            "status": "running",
                            "inflight": True,
                            "cleanup_complete": False,
                        }
                    if poll_count == 3:
                        return 200, {
                            "id": MISSION_ID,
                            "request_sha256": request_hash,
                            "status": "protocol-corruption",
                        }
                    if cancelled:
                        return 200, {
                            "id": MISSION_ID,
                            "request_sha256": request_hash,
                            "status": "cancelled",
                            "result": None,
                            "inflight": False,
                            "cleanup_complete": True,
                        }
                    return 200, {
                        "id": MISSION_ID,
                        "request_sha256": request_hash,
                        "status": "running",
                        "inflight": True,
                        "cleanup_complete": False,
                    }
                if method == "POST" and path == "/missions":
                    return 202, {
                        "mission_id": MISSION_ID,
                        "status": "queued",
                        "request_sha256": request_hash,
                        "idempotent": False,
                    }
                raise AssertionError((method, path))

            with (
                patch.object(
                    bridge, "load_research_warband_envelope", return_value=ENVELOPE
                ),
                patch.object(bridge, "_json_request", side_effect=fake_request),
                patch.object(bridge, "_finalize_protocol"),
                patch.object(bridge, "POLL_INTERVAL_SECONDS", 0),
            ):
                result = bridge.run_via_research_warband(
                    run_dir, TASK_ID, timeout_sec=5
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "failed")
            self.assertEqual(
                result["review_findings"][0]["code"],
                "research_bridge_internal_failure",
            )
            self.assertTrue(cancelled)
            self.assertEqual(
                result["research_warband_cleanup"],
                {
                    "required": True,
                    "requested": True,
                    "proven": True,
                    "status": "cancelled",
                    "inflight": False,
                    "cleanup_complete": True,
                },
            )
            durable = TaskLedger.load(run_dir / "task_ledger.json").to_dict()
            self.assertTrue(
                durable["research_warband_mission"]["bridge_cleanup_proven"]
            )
            self.assertEqual(durable["status"], "failed")

    def test_needs_user_progress_is_waiting_not_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = self.make_run(Path(raw))
            with (
                patch.object(bridge, "_mission_dir", return_value=run_dir),
                patch.object(bridge, "_append_protocol_progress") as progress,
            ):
                result = bridge._record_waiting(
                    run_dir, TASK_ID, MISSION_ID, "Which period?"
                )
            self.assertEqual(result["status"], "needs_user")
            self.assertTrue(result["needs_user"])
            self.assertEqual(progress.call_args.kwargs["phase"], "needs_user")
            self.assertEqual(progress.call_args.kwargs["status"], "waiting")

    def test_lost_create_response_is_adopted_by_hash_then_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = self.make_run(Path(raw))
            request_hash = bridge._request_sha256(ENVELOPE)
            state = {"initial_get": True, "created": False, "cancelled": False}

            def fake_request(method: str, path: str, **_kwargs):
                if method == "GET" and state["initial_get"]:
                    state["initial_get"] = False
                    return 404, {"error": "mission not found"}
                if method == "POST" and path == "/missions":
                    state["created"] = True
                    raise bridge.ResearchWarbandBridgeError(
                        "connection lost after request body was sent"
                    )
                if method == "POST" and path.endswith("/cancel"):
                    self.assertTrue(state["created"])
                    state["cancelled"] = True
                    return 200, {"ok": True, "status": "cancelling"}
                if method == "GET":
                    return 200, {
                        "id": MISSION_ID,
                        "request_sha256": request_hash,
                        "status": "cancelled" if state["cancelled"] else "running",
                        "result": None,
                        "inflight": not state["cancelled"],
                        "cleanup_complete": state["cancelled"],
                    }
                raise AssertionError((method, path))

            with (
                patch.object(
                    bridge, "load_research_warband_envelope", return_value=ENVELOPE
                ),
                patch.object(bridge, "_json_request", side_effect=fake_request),
                patch.object(bridge, "_finalize_protocol"),
                patch.object(bridge, "POLL_INTERVAL_SECONDS", 0),
            ):
                result = bridge.run_via_research_warband(
                    run_dir, TASK_ID, timeout_sec=5
                )
            self.assertTrue(state["created"])
            self.assertTrue(state["cancelled"])
            self.assertFalse(result["ok"])
            self.assertTrue(result["research_warband_cleanup"]["proven"])
            self.assertEqual(
                result["research_warband_cleanup"]["status"], "cancelled"
            )

    def test_unproven_cleanup_is_explicit_in_result_and_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = self.make_run(Path(raw))
            request_hash = bridge._request_sha256(ENVELOPE)
            monotonic = iter((0.0, 0.0, 1.0, 2.0, 2.0, 3.0))

            def fake_request(method: str, path: str, **_kwargs):
                if method == "POST" and path.endswith("/cancel"):
                    return 200, {"ok": True, "status": "cancelling"}
                return 200, {
                    "id": MISSION_ID,
                    "request_sha256": request_hash,
                    "status": "running",
                    "inflight": True,
                    "cleanup_complete": False,
                }

            with (
                patch.object(
                    bridge, "load_research_warband_envelope", return_value=ENVELOPE
                ),
                patch.object(bridge, "_json_request", side_effect=fake_request),
                patch.object(bridge, "_finalize_protocol"),
                patch.object(bridge, "POLL_INTERVAL_SECONDS", 0),
                patch.object(bridge, "ERROR_CLEANUP_TIMEOUT_SECONDS", 0.01),
                patch.object(bridge.time, "monotonic", side_effect=lambda: next(monotonic)),
            ):
                result = bridge.run_via_research_warband(
                    run_dir, TASK_ID, timeout_sec=1
                )
            self.assertFalse(result["research_warband_cleanup"]["proven"])
            self.assertEqual(result["status"], "interrupted")
            self.assertIn("CLEANUP IS PENDING AND UNPROVEN", result["summary"])
            durable = TaskLedger.load(run_dir / "task_ledger.json").to_dict()
            self.assertFalse(
                durable["research_warband_mission"]["bridge_cleanup_proven"]
            )
            self.assertTrue(
                durable["research_warband_mission"]["bridge_cleanup_pending"]
            )
            self.assertEqual(durable["status"], "interrupted")

    def test_existing_matching_mission_is_adopted_without_duplicate_post(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = self.make_run(Path(raw))
            request_hash = bridge._request_sha256(ENVELOPE)
            calls: list[tuple[str, str]] = []

            def fake_request(method: str, path: str, **_kwargs):
                calls.append((method, path))
                return 200, {
                    "id": MISSION_ID,
                    "request_sha256": request_hash,
                    "status": "done",
                    "attempt": 1,
                    "result": accepted_result(),
                    "inflight": False,
                    "cleanup_complete": True,
                }

            with (
                patch.object(
                    bridge, "load_research_warband_envelope", return_value=ENVELOPE
                ),
                patch.object(bridge, "_json_request", side_effect=fake_request),
                patch.object(bridge, "_finalize_protocol"),
            ):
                result = bridge.run_via_research_warband(
                    run_dir, TASK_ID, timeout_sec=5
                )
            self.assertTrue(result["ok"])
            self.assertNotIn(("POST", "/missions"), calls)

    def test_mission_directory_must_be_direct_child_of_authority_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            authority = root / "missions"
            authority.mkdir()
            mission_dir = authority / MISSION_ID
            mission_dir.mkdir()
            (mission_dir / "mission.json").write_text(
                '{"mission_id":"' + MISSION_ID + '"}', encoding="utf-8"
            )
            runs = root / "runs"
            runs.mkdir()
            run_dir = self.make_run(runs)
            (run_dir / "mission_ref.json").write_text(
                '{"mission_id":"'
                + MISSION_ID
                + '","mission_dir":"'
                + str(mission_dir).replace("\\", "\\\\")
                + '"}',
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ", {"WARMMASTER_MISSIONS_ROOT": str(authority)}
            ):
                self.assertEqual(bridge._mission_dir(run_dir, MISSION_ID), mission_dir)
                outside = root / "outside" / MISSION_ID
                outside.mkdir(parents=True)
                (outside / "mission.json").write_text(
                    '{"mission_id":"' + MISSION_ID + '"}', encoding="utf-8"
                )
                (run_dir / "mission_ref.json").write_text(
                    '{"mission_id":"'
                    + MISSION_ID
                    + '","mission_dir":"'
                    + str(outside).replace("\\", "\\\\")
                    + '"}',
                    encoding="utf-8",
                )
                with self.assertRaises(bridge.ResearchWarbandBridgeError):
                    bridge._mission_dir(run_dir, MISSION_ID)

    def test_service_rejects_native_mission_id_not_supported_by_port_7201(self) -> None:
        for mission_id in ("mission:colon", "m" * 129, ".."):
            with self.subTest(mission_id=mission_id):
                with self.assertRaises(bridge.ResearchWarbandBridgeError):
                    bridge._validate_service_mission_id(mission_id)

    def test_existing_service_mission_with_different_hash_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = self.make_run(Path(raw))

            def fake_request(_method: str, _path: str, **_kwargs):
                return 200, {
                    "id": MISSION_ID,
                    "request_sha256": "0" * 64,
                    "status": "running",
                }

            with (
                patch.object(
                    bridge, "load_research_warband_envelope", return_value=ENVELOPE
                ),
                patch.object(bridge, "_json_request", side_effect=fake_request),
                patch.object(bridge, "_finalize_protocol"),
            ):
                result = bridge.run_via_research_warband(
                    run_dir, TASK_ID, timeout_sec=1
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "blocked")
            self.assertIn("request hash", result["summary"])

    def test_answer_is_forwarded_only_to_bound_waiting_mission(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = self.make_run(Path(raw))
            request_hash = bridge._request_sha256(ENVELOPE)
            ledger = TaskLedger.load(run_dir / "task_ledger.json")
            ledger.data["research_warband_mission"] = {
                "id": MISSION_ID,
                "request_sha256": request_hash,
                "status": "needs_user",
                "service": bridge.DEFAULT_RESEARCH_WARBAND_URL,
            }
            ledger.data["result"] = {
                "status": "needs_user",
                "needs_user": True,
                "question": "Какой период?",
            }
            ledger.save()

            def fake_request(method: str, path: str, **_kwargs):
                if method == "GET":
                    return 200, {
                        "id": MISSION_ID,
                        "request_sha256": request_hash,
                        "status": "needs_user",
                    }
                self.assertEqual(path, f"/missions/{MISSION_ID}/answer")
                return 200, {"ok": True, "status": "queued"}

            with patch.object(bridge, "_json_request", side_effect=fake_request):
                result = bridge.answer_research_warband_mission(
                    run_dir, TASK_ID, "2020–2024"
                )
            self.assertTrue(result["ok"])
            resumed = TaskLedger.load(run_dir / "task_ledger.json").to_dict()["result"]
            self.assertEqual(resumed["status"], "running")
            self.assertFalse(resumed["needs_user"])

    def test_cancel_requires_service_cleanup_proof(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = self.make_run(Path(raw))
            request_hash = bridge._request_sha256(ENVELOPE)
            ledger = TaskLedger.load(run_dir / "task_ledger.json")
            ledger.data["research_warband_mission"] = {
                "id": MISSION_ID,
                "request_sha256": request_hash,
                "status": "running",
                "service": bridge.DEFAULT_RESEARCH_WARBAND_URL,
            }
            ledger.save()

            def fake_request(method: str, _path: str, **_kwargs):
                if method == "POST":
                    return 200, {"ok": True, "status": "cancelling"}
                return 200, {
                    "id": MISSION_ID,
                    "request_sha256": request_hash,
                    "status": "cancelled",
                    "result": {},
                    "inflight": False,
                    "cleanup_complete": True,
                }

            with (
                patch.object(bridge, "_json_request", side_effect=fake_request),
                patch.object(bridge, "_finalize_protocol") as finalize,
            ):
                result = bridge.cancel_research_warband_mission_for_run(
                    run_dir, TASK_ID, timeout_sec=2
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "cancelled")
            self.assertEqual(
                TaskLedger.load(run_dir / "task_ledger.json").to_dict()["status"],
                "cancelled",
            )
            finalize.assert_called_once()


if __name__ == "__main__":
    unittest.main()
