from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ShushunyaCore.commitments import Commitments
from ShushunyaCore.ledger import Ledger
from ShushunyaCore.organs import Organs
from ShushunyaCore.steward import Steward


class FakeOrgans:
    executable_action = staticmethod(Organs.executable_action)
    _normalize_action = staticmethod(Organs._normalize_action)

    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.actions = []

    async def inspect_abaddon(self, task_id):
        return self.snapshot

    async def execute_abaddon_action(self, task_id, snapshot):
        self.actions.append((task_id, snapshot))
        return {"ok": True, "task_id": task_id, "status": "started"}


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
                "spec": {},
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
        current = await Commitments(self.ledger, FakeOrgans(snapshot)).reconcile_one(self.item())
        self.assertEqual(current["state"], "retry_wait")
        self.assertNotEqual(current["state"], "waiting_user")

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

    async def test_unexplained_block_is_nonterminal_then_quarantined(self):
        snapshot = {
            "task_id": "mission-1",
            "status": "blocked",
            "phase": "inspect",
            "mission_state": {},
        }
        commitments = Commitments(self.ledger, FakeOrgans(snapshot))
        current = await commitments.reconcile_one(self.item())
        self.assertEqual(current["state"], "retry_wait")
        for _ in range(2):
            with self.ledger.write() as db:
                db.execute("UPDATE commitments SET next_attempt_at='2000-01-01T00:00:00+00:00' WHERE id='commitment-1'")
            current = await commitments.reconcile_one(self.item())
        self.assertEqual(current["state"], "quarantined")
        self.assertIsNotNone(current["diagnostic"])

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
