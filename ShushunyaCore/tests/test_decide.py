from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ShushunyaCore.authority import Authority
from ShushunyaCore.config import Settings
from ShushunyaCore.decide import DecisionEngine
from ShushunyaCore.identity import Identity
from ShushunyaCore.ledger import Ledger
from ShushunyaCore.organs import Organs
from ShushunyaCore.preferences import Preferences
from ShushunyaCore.relationship import Relationship
from ShushunyaCore.schema import TurnContext, TurnEnvelope
from ShushunyaCore.situation import SituationAssembler


def settings(root: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=7600,
        db_path=root / "store.sqlite3",
        lock_path=root / "core.lock",
        llm_base_url="http://127.0.0.1:9/v1",
        llm_model="fake",
        llm_timeout_sec=1,
        abaddon_base_url="http://127.0.0.1:9",
        archive_base_url="http://127.0.0.1:9",
        administratum_base_url="http://127.0.0.1:9",
        vox_base_url="http://127.0.0.1:9",
        warpwails_base_url="http://127.0.0.1:9",
        steward_interval_sec=15,
        organ_health_ttl_sec=15,
        effect_lease_sec=60,
        context_char_budget=12_000,
    )


CAPABILITIES = {
    "capabilities": [
        {"action": "answer_in_chat", "available": True},
        {"action": "ask_clarification", "available": True},
        {"action": "request_warmaster_mission", "available": True},
        {"action": "create_administratum_task", "available": True},
        {"action": "deliver_pending_reports", "available": True},
    ]
}


class FakeDecisionEngine(DecisionEngine):
    def __init__(self, *args, replies, **kwargs):
        super().__init__(*args, **kwargs)
        self.replies = list(replies)
        self.calls = 0

    async def _model_call(self, envelope, situation, repair=""):
        self.calls += 1
        return self.replies.pop(0), {"fake": True, "repair": repair}


class DecisionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.settings = settings(root)
        self.ledger = Ledger(self.settings.db_path)
        self.ledger.initialize()
        identity = Identity(self.ledger)
        relationship = Relationship(self.ledger)
        identity.seed()
        relationship.seed()
        preferences = Preferences(self.ledger)
        organs = Organs(self.settings)
        situation = SituationAssembler(self.settings, self.ledger, identity, relationship, preferences, organs)
        self.args = (self.settings, self.ledger, situation, Authority(preferences))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def envelope(self, key="turn-1", text="что думаешь?"):
        return TurnEnvelope(
            idempotency_key=key,
            text=text,
            source="test",
            recent_history=[],
            capability_manifest=CAPABILITIES,
            context=TurnContext(persona="говори прямо"),
        )

    async def test_plain_chat_is_answered_by_core_and_cached(self):
        engine = FakeDecisionEngine(
            *self.args,
            replies=[{
                "action": "answer_in_chat",
                "reply": "Конкретный ответ.",
                "confidence": 0.9,
                "rationale_summary": "Это вопрос, не поручение.",
            }],
        )
        first = await engine.resolve(self.envelope())
        second = await engine.resolve(self.envelope())
        self.assertEqual(first["decision"]["reply"], "Конкретный ответ.")
        self.assertEqual(second, first)
        self.assertEqual(engine.calls, 1)

    async def test_invalid_first_contract_is_repaired_once(self):
        engine = FakeDecisionEngine(
            *self.args,
            replies=[
                {"action": "answer_in_chat", "reply": ""},
                {"action": "ask_clarification", "reply": "Какой репозиторий?", "confidence": 1.0},
            ],
        )
        result = await engine.resolve(self.envelope(key="repair", text="почини"))
        self.assertEqual(result["decision"]["action"], "ask_clarification")
        self.assertEqual(engine.calls, 2)
        self.assertTrue(result["core"]["repair_error"])

    async def test_abaddon_action_creates_durable_effect_not_fake_speech(self):
        engine = FakeDecisionEngine(
            *self.args,
            replies=[{
                "action": "request_warmaster_mission",
                "reply": "я уже сделал",
                "warmaster_request": {
                    "user_request": "исправить проект",
                    "capability_area": "code",
                    "why_warmaster_needed": "нужно исполнение",
                    "expected_outcome": "исправленный проект",
                    "success_conditions": ["тест проходит"],
                },
                "confidence": 0.95,
            }],
        )
        result = await engine.resolve(self.envelope(key="mission", text="исправь проект"))
        self.assertEqual(result["decision"]["action"], "request_warmaster_mission")
        self.assertEqual(result["decision"]["reply"], "")
        # The model's fake completion claim was structurally discarded; Archive
        # receives only the typed effect and the durable truth exists now.
        self.assertEqual(result["effect"]["destination"], "abaddon")
        self.assertEqual(self.ledger.list_commitments()[0]["state"], "queued")

    def test_identity_proposal_decision_is_single_assignment(self):
        identity = Identity(self.ledger)
        proposal = identity.propose("custom_trait", {"value": 1}, "test", [])
        rejected = identity.decide_proposal(proposal["id"], approved=False)
        self.assertEqual(rejected["state"], "rejected")

        repeated = identity.decide_proposal(proposal["id"], approved=True)
        self.assertEqual(repeated["state"], "rejected")
        self.assertTrue(repeated["already_decided"])
        self.assertIsNone(self.ledger.projection_get("identity", "custom_trait"))


if __name__ == "__main__":
    unittest.main()
