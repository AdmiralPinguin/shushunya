from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

try:
    from . import mission_store, production_runner
except ImportError:
    import mission_store  # type: ignore[no-redef]
    import production_runner  # type: ignore[no-redef]


def accepted(answer: str = "ok") -> dict:
    return {
        "outcome": "accepted",
        "reason": "verified",
        "answer": answer,
        "ledger": {"claims": []},
    }


def runner_accepted(payload, _mission):
    return accepted(str(payload.get("runner_answer", "ok")))


def runner_clarify(_payload, mission):
    if mission.clarification_turns:
        return accepted(str(mission.clarification_turns[-1]["answer"]))
    return {"outcome": "clarify", "reason": "Which jurisdiction?", "answer": ""}


def runner_two_clarifications(_payload, mission):
    turns = [dict(turn) for turn in mission.clarification_turns]
    if not turns:
        return {"outcome": "clarify", "reason": "Which edition?", "answer": ""}
    if len(turns) == 1:
        return {"outcome": "clarify", "reason": "Which jurisdiction?", "answer": ""}
    return {
        **accepted("paired"),
        "clarification_turns_seen": turns,
    }


def runner_cancel_wait(_payload, mission):
    mission.cancelled.wait(30)
    return accepted("too late")


def runner_unknown(_payload, _mission):
    return {"outcome": "search_more", "reason": "leaked internal state", "x": [1]}


def runner_invalid_clarification(_payload, _mission):
    return {"outcome": "clarify", "reason": "   ", "answer": ""}


def runner_oversized_clarification(_payload, _mission):
    return {"outcome": "clarify", "reason": "x" * 128, "answer": ""}


def retryable_finding() -> dict:
    return {
        "code": "source_strategy_incomplete",
        "entity_kind": "research_mission",
        "entity_id": "source-strategy",
        "what_failed": "The first source strategy did not establish the answer.",
        "evidence": "The bounded first attempt ended without accepted evidence.",
        "expected": "A grounded answer or a proven scoped absence.",
        "remediation": "Broaden the source strategy without repeating the failed query.",
        "revision_owner": "scout",
        "retryable": True,
    }


def runner_needs_revision(_payload, mission):
    if mission.revision_turns:
        return {
            **accepted("revised"),
            "revision_turns_seen": [dict(turn) for turn in mission.revision_turns],
        }
    return {
        "outcome": "needs_revision",
        "reason": "choose another bounded approach",
        "review_findings": [retryable_finding()],
    }


def runner_always_needs_revision(_payload, _mission):
    return {
        "outcome": "needs_revision",
        "reason": "the current bounded approach still needs correction",
        "review_findings": [retryable_finding()],
    }


def production_revision_result(*, valid_finding: bool = True) -> dict:
    finding = retryable_finding()
    ledger = {field: [] for field in production_runner.EXTERNAL_LEDGER_FIELDS}
    ledger["claims"] = [{"id": "claim-preserved"}]
    ledger["final_claim_refs"] = [{"claim_id": "claim-preserved"}]
    return {
        "runner_contract_version": production_runner.RUNNER_CONTRACT_VERSION,
        "outcome": "needs_revision",
        "reason": "the evidence pass needs a different source strategy",
        "external_evaluator_result": {
            "contract_version": production_runner.EXTERNAL_CONTRACT_VERSION,
            "mission_id": "external-mission-preserved",
            "status": "needs_revision",
            "accepted": False,
            "final_text": "unaccepted draft must not escape",
            "question": "",
            "ledger": ledger,
            "search_log": [{"query": "preserved query"}],
        },
        "pipeline_audit": {
            "review_findings": [finding] if valid_finding else [],
        },
    }


def runner_production_needs_revision(_payload, _mission):
    return production_revision_result(valid_finding=True)


def runner_production_invalid_revision(_payload, _mission):
    return production_revision_result(valid_finding=False)


def runner_production_failed(_payload, _mission):
    ledger = {field: [] for field in production_runner.EXTERNAL_LEDGER_FIELDS}
    return {
        "runner_contract_version": production_runner.RUNNER_CONTRACT_VERSION,
        "outcome": "failed",
        "reason": "A non-retryable internal invariant rejected the research result.",
        "external_evaluator_result": {
            "contract_version": production_runner.EXTERNAL_CONTRACT_VERSION,
            "mission_id": "external-failed-mission",
            "status": "failed",
            "accepted": False,
            "final_text": "",
            "question": "",
            "ledger": ledger,
            "search_log": [],
        },
        "pipeline_audit": {
            "review_findings": [{
                **retryable_finding(),
                "code": "non_retryable_internal_invariant",
                "retryable": False,
            }],
        },
    }


def runner_non_object(_payload, _mission):
    return "accepted"


def runner_raise(_payload, _mission):
    raise RuntimeError("boom")


def runner_hung(_payload, _mission):
    while True:
        time.sleep(1)


def runner_slow_accepted(_payload, _mission):
    time.sleep(0.5)
    return accepted("must not commit")


def runner_spawns_detached_session(payload, _mission):
    marker = str(payload["detached_pid_file"])
    code = (
        "import os,time;"
        f"open({marker!r},'w',encoding='ascii').write(str(os.getpid()));"
        "time.sleep(60)"
    )
    subprocess.Popen(
        [sys.executable, "-c", code],
        start_new_session=True,
        close_fds=True,
    )
    deadline = time.monotonic() + 3
    while not Path(marker).exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    return accepted("detached-child-started")


def runner_mutates_nested(payload, _mission):
    payload["nested"]["value"] = "mutated-in-child"
    return accepted("immutable")


class MissionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "missions"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def store(self, **overrides) -> mission_store.MissionStore:
        values = {
            "max_active": 4,
            "max_missions": 32,
            "max_store_bytes": 20_000_000,
            "max_payload_bytes": 1_000_000,
            "max_result_bytes": 1_000_000,
            "max_events_bytes": 1_000_000,
            "max_event_bytes": 100_000,
            "max_state_bytes": 100_000,
            "max_attempts": 8,
            "attempt_timeout_seconds": 5,
            "cancel_grace_seconds": 0.05,
            "terminate_grace_seconds": 0.2,
        }
        values.update(overrides)
        return mission_store.MissionStore(self.root, **values)

    @staticmethod
    def wait_status(mission: mission_store.Mission, statuses: set[str], timeout: float = 3) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with mission._lock:
                if mission.status in statuses:
                    return mission.status
            time.sleep(0.01)
        raise AssertionError(f"mission remained {mission.status!r}; expected {statuses}")

    def test_exact_payload_result_events_survive_restart(self) -> None:
        store = self.store()
        payload = {
            "mission_id": "exact-1",
            "task_id": "t",
            "unicode": "данные",
            "n": 7,
            "runner_answer": "точно",
        }
        mission, created = store.create_or_get("exact-1", payload)
        self.assertTrue(created)
        self.assertTrue(store.launch(mission, runner_accepted))
        self.assertTrue(store.wait_for_idle())
        self.assertEqual(mission.status, "done")
        self.assertFalse(mission.inflight)
        self.assertTrue(mission.cleanup_complete)
        events = mission.events_snapshot()
        request_hash = mission.request_sha256

        recovered = self.store()
        loaded = recovered.get("exact-1")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.payload, payload)
        self.assertEqual(loaded.request_sha256, request_hash)
        self.assertEqual(loaded.result, accepted("точно"))
        self.assertEqual(loaded.events_snapshot(), events)
        self.assertEqual(loaded.status, "done")
        result_path = self.root / "exact-1" / "result-000001.json"
        self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), accepted("точно"))

    def test_needs_revision_is_automatically_requeued_with_bound_feedback(self) -> None:
        store = self.store()
        mission, _created = store.create_or_get(
            "revision-1", {"mission_id": "revision-1", "task_id": "t"}
        )
        self.assertTrue(store.launch(mission, runner_needs_revision))
        self.assertTrue(store.wait_for_idle())
        self.assertEqual("done", mission.status)
        self.assertEqual(2, mission.attempt)
        self.assertEqual("accepted", mission.result["outcome"])
        self.assertEqual(1, len(mission.revision_turns))
        self.assertEqual(
            "Broaden the source strategy without repeating the failed query.",
            mission.revision_turns[0]["findings"][0]["remediation"],
        )
        self.assertEqual(2, len(mission.result_refs))
        recovered = self.store().get("revision-1")
        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual("done", recovered.status)
        self.assertEqual(2, recovered.attempt)
        self.assertEqual(1, len(recovered.revision_turns))
        self.assertEqual(
            recovered.revision_turns[0]["result_sha256"],
            recovered.result_refs[0]["sha256"],
        )

    def test_revision_attempt_exhaustion_fails_with_findings_not_blocked(self) -> None:
        store = self.store(max_attempts=1)
        mission, _created = store.create_or_get(
            "revision-exhausted", {"mission_id": "revision-exhausted", "task_id": "t"}
        )
        self.assertTrue(store.launch(mission, runner_needs_revision))
        self.assertTrue(store.wait_for_idle())
        self.assertEqual("failed", mission.status)
        self.assertTrue(mission.result["revision_exhausted"])
        self.assertEqual(
            "Broaden the source strategy without repeating the failed query.",
            mission.result["review_findings"][0]["remediation"],
        )

    def test_auto_revision_cap_is_two_without_consuming_sixteen_full_runs(self) -> None:
        store = self.store(max_attempts=16, max_auto_revisions=2)
        mission, _created = store.create_or_get(
            "revision-cap", {"mission_id": "revision-cap", "task_id": "t"}
        )
        self.assertTrue(store.launch(mission, runner_always_needs_revision))
        self.assertTrue(store.wait_for_idle())
        self.assertEqual("failed", mission.status)
        self.assertEqual(3, mission.attempt)
        self.assertEqual(2, len(mission.revision_turns))
        self.assertEqual(2, mission.result["auto_revision_limit"])

    def test_terminal_revision_conversion_keeps_production_contract_coherent(self) -> None:
        original_external = production_revision_result()["external_evaluator_result"]
        for mission_id, runner, expected_marker in (
            (
                "production-revision-exhausted",
                runner_production_needs_revision,
                "revision_exhausted",
            ),
            (
                "production-revision-invalid",
                runner_production_invalid_revision,
                "revision_protocol_error",
            ),
        ):
            with self.subTest(mission_id=mission_id):
                store = self.store(max_attempts=1)
                mission, _created = store.create_or_get(
                    mission_id, {"mission_id": mission_id, "task_id": "t"}
                )
                self.assertTrue(store.launch(mission, runner))
                self.assertTrue(store.wait_for_idle())
                self.assertEqual("failed", mission.status)
                self.assertEqual("failed", mission.result["outcome"])
                self.assertTrue(mission.result[expected_marker])
                self.assertTrue(mission.result["reason"].strip())
                external = production_runner.validate_external_evaluator_result(
                    mission.result["external_evaluator_result"]
                )
                self.assertEqual("failed", external["status"])
                self.assertFalse(external["accepted"])
                self.assertEqual("", external["final_text"])
                self.assertEqual("", external["question"])
                self.assertEqual(
                    original_external["mission_id"], external["mission_id"]
                )
                self.assertEqual(original_external["ledger"], external["ledger"])
                self.assertEqual(
                    original_external["search_log"], external["search_log"]
                )

    def test_same_id_same_request_is_idempotent_and_different_request_conflicts(self) -> None:
        store = self.store()
        payload = {"mission_id": "idem", "task_id": "t", "value": [1, 2]}
        first, created = store.create_or_get("idem", payload)
        self.assertTrue(created)
        second, created = store.create_or_get("idem", dict(payload))
        self.assertFalse(created)
        self.assertIs(first, second)
        with self.assertRaises(mission_store.MissionConflictError):
            store.create_or_get("idem", {**payload, "value": [2, 1]})

    def test_startup_adopts_running_mission_once(self) -> None:
        initial = self.store()
        mission, _ = initial.create_or_get("adopt-1", {"mission_id": "adopt-1", "task_id": "t"})
        with mission._lock:
            mission.status = "running"
            mission.inflight = True
            mission.cleanup_complete = False
            initial._append_event(mission, "simulated_crash", {"status": "running"})
            initial._persist(mission)

        recovered = self.store()
        recovered.bind_runner(runner_accepted)
        self.assertEqual(recovered.adopt_pending(), ["adopt-1"])
        self.assertEqual(recovered.adopt_pending(), [])
        self.assertTrue(recovered.wait_for_idle())
        loaded = recovered.get("adopt-1")
        assert loaded is not None
        self.assertEqual(loaded.attempt, 1)
        self.assertEqual(loaded.status, "done")
        self.assertEqual(loaded.adopted_count, 1)
        self.assertTrue(any(event["type"] == "adopted" for event in loaded.events))

    def test_restart_adopts_cancelling_as_clean_terminal_across_two_restarts(self) -> None:
        initial = self.store()
        mission, _ = initial.create_or_get(
            "cancel-restart", {"mission_id": "cancel-restart", "task_id": "t"}
        )
        with mission._lock:
            mission.status = "cancelling"
            mission.inflight = True
            mission.cleanup_complete = False
            initial._append_event(
                mission, "simulated_crash", {"status": "cancelling"}
            )
            initial._persist(mission)

        first_restart = self.store()
        self.assertEqual(first_restart.adopt_pending(runner_accepted), [])
        cancelled = first_restart.get("cancel-restart")
        assert cancelled is not None
        self.assertEqual(cancelled.status, "cancelled")
        self.assertFalse(cancelled.inflight)
        self.assertTrue(cancelled.cleanup_complete)

        second_restart = self.store()
        recovered = second_restart.get("cancel-restart")
        assert recovered is not None
        self.assertEqual(recovered.status, "cancelled")
        self.assertTrue(recovered.cleanup_complete)
        self.assertIsNone(recovered.storage_error)

    def test_needs_user_survives_restart_and_answer_relaunches(self) -> None:
        store = self.store()
        mission, _ = store.create_or_get("ask-1", {"mission_id": "ask-1", "task_id": "t"})
        self.assertTrue(store.launch(mission, runner_clarify))
        self.assertTrue(store.wait_for_idle())
        self.assertEqual(mission.status, "needs_user")

        recovered = self.store()
        loaded = recovered.get("ask-1")
        assert loaded is not None
        self.assertEqual(loaded.status, "needs_user")
        self.assertEqual(loaded.question, "Which jurisdiction?")

        self.assertTrue(recovered.provide_answer("ask-1", "Korea", runner_clarify))
        self.assertTrue(recovered.wait_for_idle())
        self.assertEqual(loaded.status, "done")
        self.assertEqual(loaded.result, accepted("Korea"))
        self.assertEqual(len(loaded.result_refs), 2)
        self.assertTrue((self.root / "ask-1" / "result-000001.json").is_file())
        self.assertTrue((self.root / "ask-1" / "result-000002.json").is_file())

    def test_ordered_question_answer_pairs_survive_multiple_restarts(self) -> None:
        store = self.store()
        mission, _ = store.create_or_get("ask-2", {"mission_id": "ask-2", "task_id": "t"})
        self.assertTrue(store.launch(mission, runner_two_clarifications))
        self.assertTrue(store.wait_for_idle())
        self.assertEqual(mission.question, "Which edition?")

        first_restart = self.store()
        self.assertTrue(first_restart.provide_answer("ask-2", "second", runner_two_clarifications))
        self.assertTrue(first_restart.wait_for_idle())
        after_first = first_restart.get("ask-2")
        assert after_first is not None
        self.assertEqual(
            after_first.clarification_turns,
            [{"question": "Which edition?", "answer": "second"}],
        )
        self.assertEqual(after_first.question, "Which jurisdiction?")

        second_restart = self.store()
        self.assertTrue(second_restart.provide_answer("ask-2", "Korea", runner_two_clarifications))
        self.assertTrue(second_restart.wait_for_idle())
        final = second_restart.get("ask-2")
        assert final is not None
        expected = [
            {"question": "Which edition?", "answer": "second"},
            {"question": "Which jurisdiction?", "answer": "Korea"},
        ]
        self.assertEqual(final.status, "done")
        self.assertEqual(final.clarification_turns, expected)
        self.assertEqual(final.result["clarification_turns_seen"], expected)
        persisted = json.loads(
            (self.root / "ask-2" / "mission.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("answers", persisted)
        self.assertEqual(persisted["clarification_turns"], expected)
        received = [event for event in final.events if event["type"] == "answer_received"]
        self.assertEqual([event["turn_index"] for event in received], [0, 1])
        self.assertEqual(
            received[0]["question_sha256"],
            hashlib.sha256(b"Which edition?").hexdigest(),
        )

    def test_cancel_wins_race_and_never_exposes_accepted_result(self) -> None:
        store = self.store()
        mission, _ = store.create_or_get("cancel-1", {"mission_id": "cancel-1", "task_id": "t"})
        self.assertTrue(store.launch(mission, runner_cancel_wait))
        self.wait_status(mission, {"running"})
        self.assertTrue(store.cancel("cancel-1", expected=mission))
        self.assertTrue(store.wait_for_idle())
        self.assertEqual(mission.status, "cancelled")
        self.assertIsNone(mission.result)
        self.assertFalse(store.cancel("cancel-1"))

    def test_capacity_rejects_second_active_mission(self) -> None:
        store = self.store(max_active=1)
        first, _ = store.create_or_get("cap-1", {"mission_id": "cap-1", "task_id": "t"})
        store.launch(first, runner_hung)
        self.wait_status(first, {"running"})
        with self.assertRaises(mission_store.MissionCapacityError):
            store.create_or_get("cap-2", {"mission_id": "cap-2", "task_id": "t"})
        store.cancel("cap-1")
        self.assertTrue(store.wait_for_idle())

    def test_capacity_prunes_old_terminal_but_never_active_mission(self) -> None:
        store = self.store(max_missions=1, max_terminal=1)
        first, _ = store.create_or_get("old", {"mission_id": "old", "task_id": "t"})
        store.launch(first, runner_accepted)
        self.assertTrue(store.wait_for_idle())
        second, created = store.create_or_get(
            "replacement", {"mission_id": "replacement", "task_id": "t"}
        )
        self.assertTrue(created)
        self.assertIsNone(store.get("old"))
        self.assertIs(store.get("replacement"), second)

    def test_persistence_failure_blocks_in_memory_and_cannot_report_done(self) -> None:
        store = self.store()
        mission, _ = store.create_or_get("disk-1", {"mission_id": "disk-1", "task_id": "t"})
        store.launch(mission, runner_slow_accepted)
        self.wait_status(mission, {"running"})
        with mock.patch.object(store, "_atomic_write", side_effect=OSError("disk full")):
            self.assertTrue(store.wait_for_idle())
        self.assertEqual(mission.status, "blocked")
        self.assertNotEqual(mission.status, "done")
        self.assertIn("persistence failed", mission.storage_error or "")
        self.assertFalse(mission.cleanup_complete)

    def test_unknown_outcome_fails_with_actionable_contract_diagnostic(self) -> None:
        store = self.store()
        mission, _ = store.create_or_get("bad-outcome", {"mission_id": "bad-outcome", "task_id": "t"})
        store.launch(mission, runner_unknown)
        self.assertTrue(store.wait_for_idle())
        self.assertEqual(mission.status, "failed")
        self.assertEqual(mission.result["outcome"], "failed")
        finding = mission.result["pipeline_audit"]["review_findings"][0]
        self.assertEqual(finding["code"], "invalid_pipeline_outcome")
        self.assertIn("search_more", finding["evidence"])

    def test_invalid_clarification_contract_fails_instead_of_blocking(self) -> None:
        for name, runner, limit, code in (
            ("missing-question", runner_invalid_clarification, 128, "invalid_clarification"),
            ("oversized-question", runner_oversized_clarification, 32, "oversized_clarification"),
        ):
            with self.subTest(name=name):
                store = self.store(max_question_bytes=limit)
                mission, _ = store.create_or_get(name, {"mission_id": name, "task_id": "t"})
                store.launch(mission, runner)
                self.assertTrue(store.wait_for_idle())
                self.assertEqual(mission.status, "failed")
                self.assertTrue(mission.cleanup_complete)
                self.assertEqual(
                    mission.result["pipeline_audit"]["review_findings"][0]["code"],
                    code,
                )

    def test_production_shaped_failed_outcome_is_terminal_failed(self) -> None:
        store = self.store()
        mission, _ = store.create_or_get(
            "production-failed", {"mission_id": "production-failed", "task_id": "t"}
        )
        store.launch(mission, runner_production_failed)
        self.assertTrue(store.wait_for_idle())
        self.assertEqual(mission.status, "failed")
        self.assertEqual(mission.result["outcome"], "failed")
        self.assertEqual(
            mission.result["external_evaluator_result"]["status"], "failed"
        )
        self.assertFalse(mission.result["external_evaluator_result"]["accepted"])

    def test_runner_defects_fail_with_diagnostics_without_becoming_blocked(self) -> None:
        for name, runner in (
            ("non-object", runner_non_object),
            ("exception", runner_raise),
        ):
            with self.subTest(name=name):
                store = self.store()
                mission, _ = store.create_or_get(name, {"mission_id": name, "task_id": "t"})
                store.launch(mission, runner)
                self.assertTrue(store.wait_for_idle())
                self.assertEqual(mission.status, "failed")
                self.assertEqual("failed", mission.result["outcome"])
                self.assertIn("runner_error", mission.result)
                self.assertTrue(
                    mission.result["pipeline_audit"]["review_findings"][0]["retryable"]
                )

    def test_symlink_root_and_mission_path_are_rejected(self) -> None:
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        link_root = Path(self.temp.name) / "root-link"
        try:
            os.symlink(outside, link_root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")
        with self.assertRaises(mission_store.UnsafeStoreError):
            mission_store.MissionStore(link_root)

        store = self.store()
        os.symlink(outside, self.root / "evil", target_is_directory=True)
        with self.assertRaises((mission_store.UnsafeStoreError, mission_store.MissionExistsError)):
            store.create_or_get("evil", {"mission_id": "evil", "task_id": "t"})

    def test_uncommitted_result_file_is_recovered_as_blocked_not_rerun(self) -> None:
        store = self.store()
        mission, _ = store.create_or_get("orphan", {"mission_id": "orphan", "task_id": "t"})
        (self.root / "orphan" / "result-000001.json").write_text(
            json.dumps(accepted()), encoding="utf-8"
        )
        recovered = self.store()
        loaded = recovered.get("orphan")
        assert loaded is not None
        self.assertEqual(loaded.status, "blocked")
        self.assertTrue(loaded._resume_disabled)
        self.assertIn("uncommitted", loaded.storage_error or "")

    def test_thread_reference_is_cleaned_after_completion(self) -> None:
        store = self.store()
        mission, _ = store.create_or_get("thread-1", {"mission_id": "thread-1", "task_id": "t"})
        store.launch(mission, runner_accepted)
        self.assertTrue(store.wait_for_idle())
        self.assertEqual(store.active_worker_count(), 0)
        self.assertIsNone(mission._thread)
        self.assertTrue(mission.cleanup_complete)

    def test_hung_runner_is_killed_at_hard_deadline_and_releases_capacity(self) -> None:
        store = self.store(
            max_active=1,
            attempt_timeout_seconds=0.25,
            cancel_grace_seconds=0.05,
            terminate_grace_seconds=0.2,
        )
        mission, _ = store.create_or_get("hung", {"mission_id": "hung", "task_id": "t"})
        started = time.monotonic()
        self.assertTrue(store.launch(mission, runner_hung))
        self.assertTrue(store.wait_for_idle(timeout=5))
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(mission.status, "failed")
        self.assertTrue(mission.cleanup_complete)
        self.assertIn("hard deadline", mission.storage_error or "")
        self.assertEqual(store.active_worker_count(), 0)

    @unittest.skipIf(os.name == "nt", "Linux cgroup-v2 regression")
    def test_delegated_cgroup_cleans_descendant_that_escapes_process_group(self) -> None:
        try:
            mission_store.verify_linux_cgroup_delegation()
        except Exception as exc:
            self.skipTest(f"delegated cgroup v2 is unavailable: {exc}")
        store = self.store(
            attempt_timeout_seconds=10,
            cancel_grace_seconds=0.05,
            terminate_grace_seconds=1,
        )
        store.bind_readiness_probe(None, require_linux_cgroup=True)
        marker = self.root / "detached.pid"
        mission, _ = store.create_or_get(
            "detached",
            {
                "mission_id": "detached",
                "task_id": "t",
                "detached_pid_file": str(marker),
            },
        )
        self.assertTrue(store.launch(mission, runner_spawns_detached_session))
        self.assertTrue(store.wait_for_idle(timeout=15))
        self.assertEqual(mission.status, "done")
        detached_pid = int(marker.read_text(encoding="ascii"))
        deadline = time.monotonic() + 3
        while Path(f"/proc/{detached_pid}").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(Path(f"/proc/{detached_pid}").exists())

    def test_lease_cannot_release_while_exiting_worker_schedules_queue(self) -> None:
        store = self.store()
        store.acquire_service_lease()
        scheduler_entered = threading.Event()
        scheduler_release = threading.Event()
        original = store._launch_waiting_locked

        def delayed_scheduler():
            scheduler_entered.set()
            scheduler_release.wait(2)
            original()

        store._launch_waiting_locked = delayed_scheduler  # type: ignore[method-assign]
        mission, _ = store.create_or_get("lease-race", {"mission_id": "lease-race", "task_id": "t"})
        self.assertTrue(store.launch(mission, runner_accepted))
        self.assertTrue(scheduler_entered.wait(3))
        released: list[bool] = []
        closer = threading.Thread(target=lambda: released.append(store.release_service_lease()))
        closer.start()
        time.sleep(0.05)
        self.assertTrue(closer.is_alive())
        self.assertIsNotNone(store._lease_handle)
        scheduler_release.set()
        closer.join(timeout=3)
        self.assertEqual(released, [True])
        self.assertTrue(store.wait_for_idle())

    def test_child_cannot_mutate_canonical_request_identity(self) -> None:
        store = self.store()
        payload = {
            "mission_id": "immutable",
            "task_id": "t",
            "nested": {"value": "original"},
        }
        mission, _ = store.create_or_get("immutable", payload)
        request_hash = mission.request_sha256
        self.assertTrue(store.launch(mission, runner_mutates_nested))
        self.assertTrue(store.wait_for_idle())
        self.assertEqual(mission.status, "done")
        self.assertEqual(mission.payload, payload)
        recovered = self.store()
        loaded = recovered.get("immutable")
        assert loaded is not None
        self.assertEqual(loaded.status, "done")
        self.assertEqual(loaded.payload, payload)
        self.assertEqual(loaded.request_sha256, request_hash)

    def test_runner_attestation_rejects_tamper_and_unimportable_callable(self) -> None:
        store = self.store()
        spec = mission_store.attest_runner(runner_accepted)
        tampered = mission_store.RunnerSpec(
            target=spec.target,
            module_path=spec.module_path,
            module_sha256="0" * 64,
            callable_sha256=spec.callable_sha256,
        )
        with self.assertRaises(RuntimeError):
            store.bind_runner(tampered)
        with self.assertRaises(TypeError):
            store.bind_runner(lambda _payload, _mission: accepted())


if __name__ == "__main__":
    unittest.main()
