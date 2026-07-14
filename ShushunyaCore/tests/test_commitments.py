from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ShushunyaCore.commitments import Commitments
from ShushunyaCore.ledger import Ledger
from ShushunyaCore.organs import OrganError, Organs
from ShushunyaCore.steward import Steward


class FakeOrgans:
    executable_action = staticmethod(Organs.executable_action)
    _normalize_action = staticmethod(Organs._normalize_action)

    def __init__(
        self,
        snapshot,
        *,
        recovery_failures=0,
        recovery_error_retryable=True,
        recovery_error_evidence=None,
        inspect_error=None,
    ):
        self.snapshot = snapshot
        self.inspect_error = inspect_error
        self.actions = []
        self.recovery_requests = []
        self.recovery_failures = recovery_failures
        self.recovery_error_retryable = recovery_error_retryable
        self.recovery_error_evidence = recovery_error_evidence
        self.notifications = []

    async def inspect_abaddon(self, task_id):
        if self.inspect_error is not None:
            raise self.inspect_error
        return self.snapshot

    async def execute_abaddon_action(self, task_id, snapshot):
        self.actions.append((task_id, snapshot))
        return {"ok": True, "task_id": task_id, "status": "started"}

    async def dispatch_abaddon(self, payload):
        self.recovery_requests.append(dict(payload))
        if self.recovery_failures > 0:
            self.recovery_failures -= 1
            raise OrganError(
                "abaddon_unreachable",
                "lost recovery acknowledgement",
                retryable=self.recovery_error_retryable,
                evidence=(
                    self.recovery_error_evidence
                    if isinstance(self.recovery_error_evidence, dict)
                    else {"task_id": payload["task_id"]}
                ),
            )
        return {
            "ok": True,
            "delegate_ref": payload["task_id"],
            "status": "running",
        }

    async def dispatch_archive_notification_adapter(self, effect_id, payload):
        self.notifications.append((effect_id, payload))
        return {
            "ok": True,
            "delegate_ref": "chat-message-notification",
            "status": "delivered",
            "explanation": "Уведомление сохранено.",
        }


class CommitmentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.tmp.name) / "core.sqlite3")
        self.ledger.initialize()
        turn_id, _ = self.ledger.accept_turn("turn-1", {"source": "test", "text": "x"})
        self.ledger.save_turn_resolution(
            idempotency_key="turn-1",
            turn_id=turn_id,
            resolution={"ok": True},
            commitment={
                "id": "commitment-1",
                "kind": "abaddon_mission",
                "goal": "finish",
                "spec": {
                    "message": "original mission specification",
                    "task_id": "root-run-1",
                    "goal_id": "goal-1",
                    "task_memory_id": "goal-1",
                    "root_task_id": "root-run-1",
                },
                "state": "working",
                "delegate_kind": "abaddon",
                "delegate_ref": "mission-1",
                "max_attempts": 3,
                "honest_status": "working",
            },
        )

    def tearDown(self):
        self.tmp.cleanup()

    def item(self):
        return self.ledger.list_commitments()[0]

    async def test_nested_revision_overrides_outer_completed_and_dispatches_once(self):
        snapshot = {
            "task_id": "mission-1",
            "status": "completed",
            "phase": "revision_required",
            "mission_state": {},
            "client_action": {
                "kind": "execute_revision",
                "method": "POST",
                "path": "/runs/mission-1/start_revision_http",
                "body": {"step_ids": ["step-2"]},
            },
        }
        organs = FakeOrgans(snapshot)
        commitments = Commitments(self.ledger, organs)
        first = await commitments.reconcile_one(self.item())
        self.assertEqual(first["state"], "revising")
        self.assertEqual(len(organs.actions), 1)
        second = await commitments.reconcile_one(first)
        self.assertEqual(second["state"], "revising")
        self.assertEqual(len(organs.actions), 1)

    async def test_failed_attempt_ignores_stale_action_and_starts_new_immutable_run(self):
        snapshot = {
            "task_id": "mission-1",
            "status": "failed",
            "phase": "ready_to_start",
            "client_action": {
                "kind": "start",
                "method": "POST",
                "path": "/runs/mission-1/start_http",
                "body": {},
            },
        }
        organs = FakeOrgans(snapshot)
        current = await Commitments(self.ledger, organs).reconcile_one(self.item())

        self.assertEqual(current["state"], "working")
        self.assertNotEqual(current["delegate_ref"], "mission-1")
        self.assertEqual(current["delegate_ref"], organs.recovery_requests[0]["task_id"])
        recovery = organs.recovery_requests[0]
        self.assertEqual(recovery["parent_task_id"], "mission-1")
        self.assertEqual(recovery["continuation_of"], "mission-1")
        self.assertEqual(recovery["goal_id"], "goal-1")
        self.assertEqual(recovery["task_memory_id"], "goal-1")
        self.assertEqual(recovery["root_task_id"], "root-run-1")
        self.assertIn("отличающуюся стратегию", recovery["failure_guidance"]["required_action"])
        self.assertEqual(current["attempt_count"], 1)
        self.assertEqual(organs.actions, [])

    async def test_recovery_retry_reattaches_same_task_after_lost_ack(self):
        snapshot = {
            "task_id": "mission-1",
            "status": "preflight_failed",
            "phase": "completed",
            "summary": {"result": {"error": "preflight contract failed"}},
        }
        organs = FakeOrgans(snapshot, recovery_failures=1)
        commitments = Commitments(self.ledger, organs)

        first = await commitments.reconcile_one(self.item())
        self.assertEqual(first["state"], "retry_wait")
        self.assertNotEqual(first["state"], "waiting_user")
        self.assertEqual(len(organs.recovery_requests), 1)
        first_payload = organs.recovery_requests[0]

        with self.ledger.write() as db:
            db.execute(
                "UPDATE commitments SET next_attempt_at='2000-01-01T00:00:00+00:00' "
                "WHERE id='commitment-1'"
            )
        second = await commitments.reconcile_one(self.item())

        self.assertEqual(second["state"], "working")
        self.assertEqual(len(organs.recovery_requests), 2)
        second_payload = organs.recovery_requests[1]
        self.assertEqual(second_payload["task_id"], first_payload["task_id"])
        self.assertEqual(second_payload["idempotency_key"], first_payload["idempotency_key"])
        self.assertEqual(second["delegate_ref"], first_payload["task_id"])

    async def test_missing_initial_run_becomes_new_attempt_not_eternal_status_poll(self):
        error = OrganError(
            "abaddon_run_not_found",
            "run was never created",
            retryable=False,
            evidence={"task_id": "mission-1", "http_status": 404},
        )
        organs = FakeOrgans({}, inspect_error=error)
        current = await Commitments(self.ledger, organs).reconcile_one(self.item())
        self.assertEqual(current["state"], "working")
        self.assertNotEqual(current["delegate_ref"], "mission-1")
        self.assertEqual(len(organs.recovery_requests), 1)
        recovery = organs.recovery_requests[0]
        self.assertEqual(recovery["parent_task_id"], "mission-1")
        self.assertEqual(recovery["task_memory_id"], "goal-1")
        self.assertEqual(
            recovery["failure_guidance"]["code"],
            "abaddon_delegation_not_created",
        )

    async def test_permanent_recovery_rejection_changes_strategy_identity(self):
        snapshot = {
            "task_id": "mission-1",
            "status": "failed",
            "phase": "completed",
        }
        organs = FakeOrgans(
            snapshot,
            recovery_failures=2,
            recovery_error_retryable=False,
        )
        commitments = Commitments(self.ledger, organs)
        first = await commitments.reconcile_one(self.item())
        self.assertEqual(first["state"], "retry_wait")
        self.assertEqual(first["result"]["recovery_generation"], 1)
        with self.ledger.write() as db:
            db.execute(
                "UPDATE commitments SET next_attempt_at='2000-01-01T00:00:00+00:00' "
                "WHERE id='commitment-1'"
            )
        second = await commitments.reconcile_one(self.item())
        self.assertEqual(second["state"], "retry_wait")
        self.assertEqual(second["result"]["recovery_generation"], 2)
        self.assertNotEqual(
            organs.recovery_requests[0]["task_id"],
            organs.recovery_requests[1]["task_id"],
        )
        self.assertNotEqual(
            organs.recovery_requests[0]["idempotency_key"],
            organs.recovery_requests[1]["idempotency_key"],
        )

    async def test_lineage_rejection_retries_same_child_after_repair(self):
        snapshot = {
            "task_id": "mission-1",
            "status": "failed",
            "summary": {"reason": "old attempt failed"},
        }
        organs = FakeOrgans(
            snapshot,
            recovery_failures=2,
            recovery_error_retryable=False,
            recovery_error_evidence={
                "technical": {"error_code": "task_memory_parent_conflict"}
            },
        )
        commitments = Commitments(self.ledger, organs)

        first = await commitments.reconcile_one(self.item())
        second = await commitments.reconcile_one(first)

        self.assertEqual(first["state"], "waiting_external")
        self.assertEqual(
            first["diagnostic"]["code"],
            "task_memory_lineage_repair_required",
        )
        self.assertFalse(first["diagnostic"]["requires_user"])
        self.assertEqual(len(organs.recovery_requests), 2)
        self.assertEqual(
            organs.recovery_requests[0]["task_id"],
            organs.recovery_requests[1]["task_id"],
        )
        self.assertEqual(second["state"], "waiting_external")

    async def test_nested_question_becomes_waiting_user(self):
        snapshot = {
            "task_id": "mission-1",
            "status": "completed",
            "phase": "running",
            "summary": {"result": {"status": "needs_user", "needs_user": True, "question": "Какой репозиторий?"}},
        }
        current = await Commitments(self.ledger, FakeOrgans(snapshot)).reconcile_one(self.item())
        self.assertEqual(current["state"], "waiting_user")
        self.assertIn("репозиторий", current["diagnostic"]["explanation"])

    async def test_needs_user_flag_without_exact_question_stays_internal(self):
        snapshot = {
            "task_id": "mission-1",
            "status": "blocked",
            "phase": "running",
            "summary": {
                "result": {
                    "status": "needs_user",
                    "needs_user": True,
                    "action": {"reason": "internal preflight did not complete"},
                }
            },
        }
        organs = FakeOrgans(snapshot)
        current = await Commitments(self.ledger, organs).reconcile_one(self.item())
        self.assertEqual(current["state"], "working")
        self.assertNotEqual(current["state"], "waiting_user")
        self.assertEqual(len(organs.recovery_requests), 1)

    async def test_external_dependency_has_explanation_and_resume_condition(self):
        snapshot = {
            "task_id": "mission-1",
            "status": "completed",
            "phase": "inspect",
            "mission_state": {
                "external_dependency": "GitHub unavailable",
                "explanation": "Не отвечает GitHub",
                "required_action": "Дождаться GitHub",
                "resume_condition": "GitHub API отвечает",
            },
        }
        current = await Commitments(self.ledger, FakeOrgans(snapshot)).reconcile_one(self.item())
        self.assertEqual(current["state"], "waiting_external")
        self.assertEqual(current["diagnostic"]["resume_condition"], "GitHub API отвечает")

    async def test_unexplained_block_starts_new_linked_attempt_immediately(self):
        snapshot = {
            "task_id": "mission-1",
            "status": "blocked",
            "phase": "inspect",
            "mission_state": {},
        }
        organs = FakeOrgans(snapshot)
        commitments = Commitments(self.ledger, organs)
        current = await commitments.reconcile_one(self.item())
        self.assertEqual(current["state"], "working")
        self.assertEqual(current["attempt_count"], 1)
        self.assertNotEqual(current["delegate_ref"], "mission-1")
        self.assertEqual(len(organs.recovery_requests), 1)
        recovery = organs.recovery_requests[0]
        self.assertEqual(recovery["parent_task_id"], "mission-1")
        self.assertEqual(recovery["task_memory_id"], "goal-1")
        self.assertEqual(recovery["root_task_id"], "root-run-1")
        self.assertEqual(recovery["failure_guidance"]["code"], "abaddon_blocked_no_action")
        with self.ledger.connect() as db:
            rows = db.execute(
                "SELECT id,destination,payload_json,state FROM effects "
                "WHERE kind='notify_commitment_stalled'"
            ).fetchall()
        self.assertEqual(rows, [])
        self.assertEqual(self.item()["state"], "working")

    async def test_block_with_executable_resume_continues_same_attempt(self):
        snapshot = {
            "task_id": "mission-1",
            "status": "blocked",
            "phase": "resume_required",
            "client_action": {
                "kind": "resume",
                "method": "POST",
                "path": "/runs/mission-1/resume_http",
                "body": {"reason": "continue after repair"},
            },
        }
        organs = FakeOrgans(snapshot)
        current = await Commitments(self.ledger, organs).reconcile_one(self.item())
        self.assertEqual(current["state"], "working")
        self.assertEqual(current["delegate_ref"], "mission-1")
        self.assertEqual(len(organs.actions), 1)
        self.assertEqual(organs.recovery_requests, [])

    async def test_exhausted_continuation_budget_changes_attempt_not_waits_forever(self):
        snapshot = {
            "task_id": "mission-1",
            "status": "blocked",
            "phase": "resume_required",
            "client_action": {
                "kind": "resume",
                "method": "POST",
                "path": "/runs/mission-1/resume_http",
                "body": {"reason": "same repair again"},
            },
        }
        with self.ledger.write() as db:
            db.execute(
                "UPDATE commitments SET attempt_count=max_attempts WHERE id='commitment-1'"
            )
        organs = FakeOrgans(snapshot)
        current = await Commitments(self.ledger, organs).reconcile_one(self.item())
        self.assertEqual(current["state"], "working")
        self.assertNotEqual(current["delegate_ref"], "mission-1")
        self.assertEqual(organs.actions, [])
        self.assertEqual(len(organs.recovery_requests), 1)
        self.assertEqual(
            organs.recovery_requests[0]["failure_guidance"]["code"],
            "abaddon_continuation_budget_exhausted",
        )

    async def test_future_retry_is_not_polled_and_publication_stays_working(self):
        snapshot = {
            "task_id": "mission-1",
            "status": "publishing",
            "phase": "publishing",
            "mission_state": {},
        }
        organs = FakeOrgans(snapshot)
        commitments = Commitments(self.ledger, organs)
        with self.ledger.write() as db:
            db.execute(
                "UPDATE commitments SET state='retry_wait',next_attempt_at='2999-01-01T00:00:00+00:00',"
                "diagnostic_json='{}' WHERE id='commitment-1'"
            )
        summary = await commitments.reconcile_all()
        self.assertEqual(summary["checked"], 0)
        with self.ledger.write() as db:
            db.execute("UPDATE commitments SET state='working',next_attempt_at=NULL,diagnostic_json=NULL WHERE id='commitment-1'")
        current = await commitments.reconcile_one(self.item())
        self.assertEqual(current["state"], "working")

    async def test_waiting_user_is_reconciled_after_answer_resumes_mission(self):
        snapshot = {
            "task_id": "mission-1",
            "status": "running",
            "phase": "executing",
            "mission_state": {},
        }
        with self.ledger.write() as db:
            db.execute(
                "UPDATE commitments SET state='waiting_user',diagnostic_json='{}' "
                "WHERE id='commitment-1'"
            )

        summary = await Commitments(self.ledger, FakeOrgans(snapshot)).reconcile_all()

        self.assertEqual(summary["checked"], 1)
        self.assertEqual(self.item()["state"], "working")

    async def test_steward_cycles_are_serialized(self):
        class HealthProbe:
            def __init__(self):
                self.active = 0
                self.peak = 0

            async def refresh_health(self):
                self.active += 1
                self.peak = max(self.peak, self.active)
                await asyncio.sleep(0.01)
                self.active -= 1
                return {"ok": True}

        class CommitmentProbe:
            async def reconcile_all(self):
                await asyncio.sleep(0.01)
                return {"checked": 0, "changed": 0}

        organs = HealthProbe()
        steward = Steward(
            SimpleNamespace(effect_lease_sec=60, steward_interval_sec=15),
            self.ledger,
            organs,
            CommitmentProbe(),
        )
        await asyncio.gather(steward.cycle(), steward.cycle())
        self.assertEqual(organs.peak, 1)


if __name__ == "__main__":
    unittest.main()
