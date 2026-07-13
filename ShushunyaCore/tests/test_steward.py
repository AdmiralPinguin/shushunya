from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ShushunyaCore.commitments import Commitments
from ShushunyaCore.ledger import Ledger
from ShushunyaCore.organs import OrganError, Organs
from ShushunyaCore.steward import Steward


class FakeArtifactOrgans:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls: list[tuple[str, dict]] = []

    async def dispatch_archive_artifact_adapter(self, effect_id, payload):
        self.calls.append((effect_id, payload))
        if self.fail:
            raise OrganError(
                "archive_artifact_adapter_unreachable",
                "ack потерян",
                retryable=True,
                evidence={"artifact_id": payload["artifact_id"]},
            )
        return {
            "ok": True,
            "delegate_ref": "chat-message-41",
            "artifact_id": payload["artifact_id"],
            "status": "delivered",
            "explanation": "Archive сохранил карточку файла.",
        }

    async def dispatch_abaddon(self, _payload):  # pragma: no cover - wrong route guard
        raise AssertionError("artifact effect was routed to Abaddon")

    async def dispatch_archive_adapter(self, _effect_id, _payload):  # pragma: no cover
        raise AssertionError("artifact effect was routed to Administratum")


class ArchiveAdapterAuthTests(unittest.TestCase):
    def test_dedicated_core_archive_header_is_mandatory(self):
        configured = Organs(SimpleNamespace(archive_effect_key="core-test-key-0123456789abcdefghijkl"))
        self.assertEqual(
            configured._archive_effect_headers(),
            {"X-Shushunya-Core-Key": "core-test-key-0123456789abcdefghijkl"},
        )

        missing = Organs(SimpleNamespace(archive_effect_key=""))
        with self.assertRaises(OrganError) as caught:
            missing._archive_effect_headers()
        self.assertEqual(caught.exception.code, "archive_effect_auth_unconfigured")
        self.assertFalse(caught.exception.retryable)


class ArchiveAdapterAuthAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_secret_is_not_misreported_as_retryable_transport_failure(self):
        organs = Organs(
            SimpleNamespace(
                archive_effect_key="",
                archive_base_url="http://127.0.0.1:9",
                llm_timeout_sec=1,
            )
        )
        with self.assertRaises(OrganError) as administratum_error:
            await organs.dispatch_archive_adapter("effect-administratum", {"kind": "reminder"})
        self.assertEqual(administratum_error.exception.code, "archive_effect_auth_unconfigured")
        self.assertFalse(administratum_error.exception.retryable)

        with self.assertRaises(OrganError) as artifact_error:
            await organs.dispatch_archive_artifact_adapter(
                "effect-artifact",
                {
                    "artifact_id": "art_0123456789abcdef0123456789abcdef",
                    "session_id": "shushunya-main",
                },
            )
        self.assertEqual(artifact_error.exception.code, "archive_effect_auth_unconfigured")
        self.assertFalse(artifact_error.exception.retryable)


class StewardArtifactTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.tmp.name) / "core.sqlite3")
        self.ledger.initialize()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def create_effect(self, *, max_attempts: int = 3):
        turn_id, _cached = self.ledger.accept_turn(
            "artifact-turn", {"source": "test", "text": "скинь файл"},
        )
        payload = {
            "artifact_id": "artifact-known",
            "session_id": "shushunya-main",
            "source": "test",
            "client_request_id": "android-artifact-turn",
            "idempotency_key": "effect-artifact",
        }
        self.ledger.save_turn_resolution(
            idempotency_key="artifact-turn",
            turn_id=turn_id,
            resolution={"ok": True, "turn_id": turn_id},
            commitment={
                "id": "commitment-artifact",
                "kind": "artifact_delivery",
                "goal": "доставить файл",
                "spec": payload,
                "state": "queued",
                "delegate_kind": "archive_artifact_adapter",
                "max_attempts": max_attempts,
            },
            effect={
                "id": "effect-artifact",
                "commitment_id": "commitment-artifact",
                "kind": "deliver_artifact",
                "destination": "archive_artifact_adapter",
                "payload": payload,
                "idempotency_key": "effect-artifact",
                "max_attempts": max_attempts,
            },
        )

    async def test_artifact_effect_uses_its_adapter_and_succeeds_from_fact(self):
        self.create_effect()
        organs = FakeArtifactOrgans()
        steward = Steward(
            SimpleNamespace(effect_lease_sec=60),
            self.ledger,
            organs,
            Commitments(self.ledger, organs),
        )

        effect = await steward.dispatch_effect("effect-artifact")

        self.assertEqual(effect["state"], "delivered")
        self.assertEqual(organs.calls[0][0], "effect-artifact")
        self.assertEqual(organs.calls[0][1]["artifact_id"], "artifact-known")
        self.assertNotIn("caption", organs.calls[0][1])
        commitment = self.ledger.list_commitments()[0]
        self.assertEqual(commitment["state"], "succeeded")
        self.assertEqual(commitment["delegate_ref"], "chat-message-41")

    async def test_lost_artifact_ack_is_quarantined_not_declared_failed(self):
        self.create_effect(max_attempts=1)
        organs = FakeArtifactOrgans(fail=True)
        steward = Steward(
            SimpleNamespace(effect_lease_sec=60),
            self.ledger,
            organs,
            Commitments(self.ledger, organs),
        )

        effect = await steward.dispatch_effect("effect-artifact")

        self.assertEqual(effect["state"], "dead_letter")
        commitment = self.ledger.list_commitments()[0]
        self.assertEqual(commitment["state"], "quarantined")
        self.assertEqual(
            commitment["diagnostic"]["code"], "archive_artifact_adapter_unreachable",
        )


if __name__ == "__main__":
    unittest.main()
