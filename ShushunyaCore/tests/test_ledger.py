from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from ShushunyaCore.ledger import (
    MAX_PERSISTED_EVIDENCE_BYTES,
    IdempotencyConflict,
    InvariantViolation,
    Ledger,
    canonical_json,
)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.tmp.name) / "core.sqlite3")
        self.ledger.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def request(text="сделай исследование"):
        return {"source": "test", "text": text, "correlation_id": "corr-1"}

    def create_effect(self):
        turn_id, cached = self.ledger.accept_turn("turn-key", self.request())
        self.assertIsNone(cached)
        resolution = {"ok": True, "turn_id": turn_id, "decision": {"action": "request_warmaster_mission"}}
        commitment = {
            "id": "commitment-1",
            "kind": "abaddon_mission",
            "goal": "проверенный результат",
            "spec": {"goal": "проверенный результат"},
            "state": "queued",
            "honest_status": "Ещё не отправлено.",
            "delegate_kind": "abaddon",
        }
        effect = {
            "id": "effect-1",
            "commitment_id": "commitment-1",
            "kind": "request_warmaster_mission",
            "destination": "abaddon",
            "payload": {"message": "x", "task_id": "core-1"},
        }
        self.ledger.save_turn_resolution(
            idempotency_key="turn-key",
            turn_id=turn_id,
            resolution=resolution,
            commitment=commitment,
            effect=effect,
        )
        return turn_id, resolution

    def test_turn_commitment_and_outbox_are_idempotent(self):
        _turn_id, expected = self.create_effect()
        turn_id, cached = self.ledger.accept_turn("turn-key", self.request())
        self.assertEqual(cached, expected)
        self.assertTrue(turn_id.startswith("turn-"))
        self.assertEqual(len(self.ledger.list_commitments()), 1)
        self.assertEqual(self.ledger.status()["pending_effects"], 1)

    def test_failed_delegate_attempt_remains_open_for_steward_recovery(self):
        self.create_effect()
        with self.ledger.write() as db:
            db.execute(
                "UPDATE commitments SET state='failed',diagnostic_json=? "
                "WHERE id='commitment-1'",
                ('{"code":"legacy_failed_attempt"}',),
            )
        open_items = self.ledger.list_commitments(include_terminal=False)
        self.assertEqual([item["id"] for item in open_items], ["commitment-1"])
        claim = self.ledger.claim_outbox("recovery-worker", 60, message_id="effect-1")
        self.ledger.finish_effect(
            effect_id="effect-1",
            lease_token=claim["lease_token"],
            ok=True,
            result={"delegate_ref": "recovery-run", "explanation": "accepted"},
        )
        recovered = self.ledger.list_commitments(include_terminal=False)[0]
        self.assertEqual(recovered["state"], "working")
        self.assertEqual(recovered["delegate_ref"], "recovery-run")

    def test_same_key_with_different_request_is_conflict(self):
        self.create_effect()
        with self.assertRaises(IdempotencyConflict):
            self.ledger.accept_turn("turn-key", self.request("другая задача"))

    def test_explicit_claim_honors_retry_backoff(self):
        self.create_effect()
        with self.ledger.write() as db:
            db.execute(
                "UPDATE outbox SET state='retry_wait',next_attempt_at='2999-01-01T00:00:00+00:00' "
                "WHERE message_id='effect-1'"
            )
            db.execute("UPDATE effects SET state='retry_wait' WHERE id='effect-1'")
        self.assertIsNone(self.ledger.claim_outbox("foreground", 60, message_id="effect-1"))
        with self.ledger.write() as db:
            db.execute(
                "UPDATE outbox SET next_attempt_at='2000-01-01T00:00:00+00:00' WHERE message_id='effect-1'"
            )
        self.assertIsNotNone(self.ledger.claim_outbox("foreground", 60, message_id="effect-1"))

    def test_stale_lease_cannot_finalize_and_valid_delivery_advances_commitment(self):
        self.create_effect()
        claim = self.ledger.claim_outbox("worker-a", 60, message_id="effect-1")
        self.assertEqual(self.ledger.get_effect("effect-1")["state"], "leased")
        with self.assertRaises(InvariantViolation):
            self.ledger.finish_effect(
                effect_id="effect-1",
                lease_token="wrong",
                ok=True,
                result={"delegate_ref": "mission-1", "explanation": "ok"},
            )
        effect = self.ledger.finish_effect(
            effect_id="effect-1",
            lease_token=claim["lease_token"],
            ok=True,
            result={"delegate_ref": "mission-1", "explanation": "accepted"},
        )
        self.assertEqual(effect["state"], "delivered")
        commitment = self.ledger.list_commitments()[0]
        self.assertEqual(commitment["state"], "working")
        self.assertEqual(commitment["delegate_ref"], "mission-1")

    def test_finish_effect_hard_bounds_result_across_every_persistence_path(self):
        self.create_effect()
        claim = self.ledger.claim_outbox("worker-a", 60, message_id="effect-1")
        self.ledger.finish_effect(
            effect_id="effect-1",
            lease_token=claim["lease_token"],
            ok=False,
            retryable=True,
            result={
                "code": "organ_failed",
                "explanation": "concrete failure",
                "required_action": "retry with corrected input",
                "evidence": {"recursive": "x" * 400_000},
            },
        )
        effect = self.ledger.get_effect("effect-1")
        commitment = self.ledger.list_commitments()[0]
        self.assertLessEqual(
            len(canonical_json(effect["result"]).encode("utf-8")),
            MAX_PERSISTED_EVIDENCE_BYTES,
        )
        self.assertLessEqual(
            len(canonical_json(commitment["result"]).encode("utf-8")),
            MAX_PERSISTED_EVIDENCE_BYTES,
        )
        self.assertEqual(commitment["diagnostic"]["code"], "organ_failed")
        with self.ledger.connect() as db:
            payloads = db.execute(
                "SELECT payload_json FROM events WHERE aggregate_id IN ('effect-1','commitment-1') "
                "ORDER BY seq DESC LIMIT 2"
            ).fetchall()
        self.assertEqual(len(payloads), 2)
        self.assertTrue(all(len(row[0].encode("utf-8")) <= 64 * 1024 for row in payloads))

    def test_reclaimed_lease_fences_first_worker(self):
        self.create_effect()
        first = self.ledger.claim_outbox("worker-a", 10, message_id="effect-1")
        with self.ledger.write() as db:
            db.execute("UPDATE outbox SET lease_until='2000-01-01T00:00:00+00:00' WHERE message_id='effect-1'")
        second = self.ledger.claim_outbox("worker-b", 10, message_id="effect-1")
        self.assertNotEqual(first["lease_token"], second["lease_token"])
        with self.assertRaises(InvariantViolation):
            self.ledger.finish_effect(
                effect_id="effect-1",
                lease_token=first["lease_token"],
                ok=True,
                result={"delegate_ref": "mission-1", "explanation": "stale"},
            )
        self.ledger.finish_effect(
            effect_id="effect-1",
            lease_token=second["lease_token"],
            ok=True,
            result={"delegate_ref": "mission-1", "explanation": "current"},
        )

    def test_events_are_physically_append_only_and_blocked_is_not_a_state(self):
        self.create_effect()
        with self.ledger.connect() as db, self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE events SET kind='lie' WHERE seq=1")
        with self.ledger.connect() as db, self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE commitments SET state='blocked' WHERE id='commitment-1'")

    def test_restart_recovers_unknown_delivery_ack(self):
        self.create_effect()
        self.ledger.claim_outbox("worker-a", 60, message_id="effect-1")
        restarted = Ledger(self.ledger.db_path)
        restarted.initialize()
        recovered = restarted.recover_after_restart()
        self.assertEqual(recovered["outbox"], 1)
        effect = restarted.get_effect("effect-1")
        self.assertEqual(effect["state"], "retry_wait")

    def test_exhausted_unknown_ack_is_quarantined_not_declared_failed(self):
        self.create_effect()
        with self.ledger.write() as db:
            db.execute("UPDATE outbox SET max_attempts=1 WHERE message_id='effect-1'")
        claim = self.ledger.claim_outbox("worker-a", 60, message_id="effect-1")
        self.ledger.finish_effect(
            effect_id="effect-1",
            lease_token=claim["lease_token"],
            ok=False,
            result={
                "code": "delivery_ack_unknown_after_restart",
                "explanation": "ack unknown",
            },
            retryable=True,
        )
        commitment = self.ledger.list_commitments()[0]
        self.assertEqual(commitment["state"], "quarantined")
        self.assertEqual(commitment["delegate_ref"], "core-1")
        with self.ledger.connect() as db:
            notices = db.execute(
                "SELECT id,payload_json,state FROM effects "
                "WHERE kind='notify_commitment_stalled'"
            ).fetchall()
            outbox = db.execute(
                "SELECT message_id,state FROM outbox "
                "WHERE operation='notify_commitment_stalled'"
            ).fetchall()
        self.assertEqual(len(notices), 1)
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0]["message_id"], notices[0]["id"])
        payload = __import__("json").loads(notices[0]["payload_json"])
        self.assertIs(payload["needs_user"], False)
        self.assertNotIn("question", payload)

        # Initialization is not a migration/backfill pass. The event-owned
        # notification remains exactly once after a restart.
        self.ledger.initialize()
        with self.ledger.connect() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM effects WHERE kind='notify_commitment_stalled'"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_preexisting_quarantine_is_not_backfilled_at_startup(self):
        self.create_effect()
        with self.ledger.write() as db:
            db.execute(
                "UPDATE commitments SET state='quarantined',diagnostic_json=? "
                "WHERE id='commitment-1'",
                ('{"code":"historical_quarantine"}',),
            )

        self.ledger.initialize()

        with self.ledger.connect() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM effects WHERE kind='notify_commitment_stalled'"
            ).fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
