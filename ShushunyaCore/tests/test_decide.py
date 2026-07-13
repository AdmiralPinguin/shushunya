from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
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
        archive_effect_key="test-core-archive-key-0123456789abcdef",
        administratum_base_url="http://127.0.0.1:9",
        vox_base_url="http://127.0.0.1:9",
        warpwails_base_url="http://127.0.0.1:9",
        steward_interval_sec=15,
        organ_health_ttl_sec=15,
        effect_lease_sec=60,
        context_char_budget=12_000,
    )


ARTIFACT_ID = "artifact-a1b2c3d4"


CAPABILITIES = {
    "capabilities": [
        {"action": "answer_in_chat", "available": True},
        {"action": "ask_clarification", "available": True},
        {"action": "request_warmaster_mission", "available": True},
        {"action": "create_administratum_task", "available": True},
        {"action": "deliver_pending_reports", "available": True},
        {
            "action": "deliver_artifact",
            "available": True,
            "artifacts": [
                {
                    "artifact_id": ARTIFACT_ID,
                    "filename": "shushunya.apk",
                    "mime_type": "application/vnd.android.package-archive",
                    "size_bytes": 12_345_678,
                    "created_at": "2026-07-13T12:00:00+00:00",
                }
            ],
        },
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
        self.root = root
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
        self.identity = identity
        self.relationship = relationship
        self.preferences = preferences
        self.organs = organs
        self.situation = situation
        self.args = (self.settings, self.ledger, situation, Authority(preferences))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def envelope(self, key="turn-1", text="что думаешь?", correlation_id=""):
        return TurnEnvelope(
            idempotency_key=key,
            correlation_id=correlation_id,
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

    async def test_pending_decision_binds_trusted_task_and_exact_user_text(self):
        manifest = json.loads(json.dumps(CAPABILITIES))
        manifest["capabilities"].append(
            {
                "action": "answer_pending_decision",
                "available": True,
                "pending_decisions": [
                    {"task_id": "task-real", "question": "Какой движок выбрать?"},
                ],
            }
        )
        engine = FakeDecisionEngine(
            *self.args,
            replies=[{
                "action": "answer_pending_decision",
                "reply": "я уже передал ответ",
                "pending_decision": {"task_id": "task-invented", "answer": "переписанный ответ"},
                "confidence": 0.95,
            }],
        )
        envelope = self.envelope(key="pending-decision", text="Выбирай сам, главное собери APK")
        envelope.capability_manifest = manifest

        result = await engine.resolve(envelope)

        self.assertEqual(result["decision"]["action"], "answer_pending_decision")
        self.assertEqual(
            result["decision"]["pending_decision"],
            {"task_id": "task-real", "answer": "Выбирай сам, главное собери APK"},
        )
        self.assertEqual(result["decision"]["pending_decision_task_id"], "task-real")
        self.assertEqual(result["decision"]["reply"], "")
        self.assertIsNone(result["effect"])

    async def test_pending_decision_prefers_explicit_trusted_older_task(self):
        manifest = json.loads(json.dumps(CAPABILITIES))
        manifest["pending_decision_task_id"] = "task-new"
        manifest["capabilities"].append(
            {
                "action": "answer_pending_decision",
                "available": True,
                "pending_decisions": [
                    {"task_id": "task-old", "question": "Old question?"},
                    {"task_id": "task-new", "question": "New question?"},
                ],
            }
        )
        engine = FakeDecisionEngine(
            *self.args,
            replies=[{
                "action": "answer_pending_decision",
                "pending_decision_task_id": "task-old",
                "confidence": 0.99,
            }],
        )
        envelope = self.envelope(
            key="pending-decision-explicit-old",
            text="For task-old choose option A",
        )
        envelope.capability_manifest = manifest

        result = await engine.resolve(envelope)

        self.assertEqual(result["decision"]["action"], "answer_pending_decision")
        self.assertEqual(result["decision"]["pending_decision_task_id"], "task-old")
        self.assertEqual(
            result["decision"]["pending_decision"],
            {"task_id": "task-old", "answer": "For task-old choose option A"},
        )

    async def test_registered_artifact_creates_one_durable_no_speech_effect(self):
        engine = FakeDecisionEngine(
            *self.args,
            replies=[{
                "action": "deliver_artifact",
                "reply": "уже отправил",
                "artifact_delivery": {
                    "artifact_id": ARTIFACT_ID,
                    "caption": "Модель не имеет права писать эту подпись.",
                    "path": "C:/secrets/ignored",
                },
                "confidence": 0.99,
            }],
        )
        envelope = self.envelope(
            key="archive-turn:client-artifact-1",
            text="скинь свежий apk",
            correlation_id="android-client-artifact-1",
        )
        first = await engine.resolve(envelope)
        second = await engine.resolve(envelope)

        self.assertEqual(second, first)
        self.assertEqual(engine.calls, 1)
        self.assertEqual(first["decision"]["reply"], "")
        self.assertEqual(first["effect"]["destination"], "archive_artifact_adapter")
        self.assertEqual(first["effect"]["kind"], "deliver_artifact")
        self.assertEqual(
            set(first["effect"]["payload"]),
            {"artifact_id", "session_id", "source", "client_request_id", "idempotency_key"},
        )
        self.assertEqual(first["decision"]["artifact_delivery"], {"artifact_id": ARTIFACT_ID})
        self.assertEqual(first["effect"]["payload"]["artifact_id"], ARTIFACT_ID)
        self.assertEqual(first["effect"]["payload"]["client_request_id"], "android-client-artifact-1")
        commitment = self.ledger.list_commitments()[0]
        self.assertEqual(commitment["kind"], "artifact_delivery")
        self.assertEqual(commitment["state"], "queued")

    async def test_unregistered_artifact_is_clarified_without_effect(self):
        engine = FakeDecisionEngine(
            *self.args,
            replies=[{
                "action": "deliver_artifact",
                "artifact_delivery": {"artifact_id": "artifact-invented"},
                "confidence": 0.99,
            }],
        )
        result = await engine.resolve(self.envelope(key="bad-artifact", text="скинь секрет"))

        self.assertEqual(result["decision"]["action"], "ask_clarification")
        self.assertEqual(result["decision"]["reason"], "artifact_not_in_capability")
        self.assertIn("shushunya.apk", result["decision"]["reply"])
        self.assertIn("нет среди доступных мне вложений", result["decision"]["reply"])
        self.assertIn("после этого я смогу его отправить", result["decision"]["reply"])
        self.assertNotIn("разрешение", result["decision"]["reply"])
        self.assertNotIn("artifact_id", result["decision"]["reply"])
        self.assertIsNone(result["effect"])
        self.assertEqual(self.ledger.list_commitments(), [])

    async def test_missing_artifact_id_reaches_factual_authority_explanation(self):
        engine = FakeDecisionEngine(
            *self.args,
            replies=[{
                "action": "deliver_artifact",
                "artifact_delivery": {},
                "confidence": 0.9,
            }],
        )
        result = await engine.resolve(self.envelope(key="missing-artifact", text="скинь файл"))

        self.assertEqual(result["decision"]["action"], "ask_clarification")
        self.assertEqual(result["decision"]["reason"], "incomplete_artifact_delivery")
        self.assertIn("shushunya.apk", result["decision"]["reply"])
        self.assertIn("нет среди доступных мне вложений", result["decision"]["reply"])
        self.assertNotIn("разрешение", result["decision"]["reply"])
        self.assertNotIn("artifact_id", result["decision"]["reply"])
        self.assertIsNone(result["effect"])
        self.assertEqual(self.ledger.list_commitments(), [])

    def test_available_artifact_id_survives_hard_situation_compaction(self):
        compact_settings = replace(self.settings, context_char_budget=2_800)
        assembler = SituationAssembler(
            compact_settings,
            self.ledger,
            self.identity,
            self.relationship,
            self.preferences,
            self.organs,
        )
        manifest = json.loads(json.dumps(CAPABILITIES))
        for capability in manifest["capabilities"]:
            capability["description"] = "очень длинное описание " * 300
        envelope = TurnEnvelope(
            idempotency_key="compact-artifact",
            text="скинь apk",
            source="test",
            recent_history=[{"role": "user", "content": "история " * 500}],
            capability_manifest=manifest,
            context=TurnContext(
                persona="личность " * 500,
                recalled_memory="память " * 500,
                live_roster="статус " * 500,
            ),
        )

        situation = assembler.assemble(envelope)

        self.assertLessEqual(len(json.dumps(situation, ensure_ascii=False, separators=(",", ":"))), 2_800)
        self.assertEqual(situation["available_artifacts"][0]["artifact_id"], ARTIFACT_ID)

    def test_pending_decision_binding_survives_hard_situation_compaction(self):
        compact_settings = replace(self.settings, context_char_budget=2_800)
        assembler = SituationAssembler(
            compact_settings,
            self.ledger,
            self.identity,
            self.relationship,
            self.preferences,
            self.organs,
        )
        manifest = json.loads(json.dumps(CAPABILITIES))
        manifest["capabilities"].append(
            {
                "action": "answer_pending_decision",
                "available": True,
                "pending_decisions": [
                    {"task_id": "task-pending", "question": "Какой движок выбрать? " * 100},
                ],
            }
        )
        envelope = TurnEnvelope(
            idempotency_key="compact-pending-decision",
            text="выбирай сам",
            source="test",
            recent_history=[{"role": "user", "content": "история " * 500}],
            capability_manifest=manifest,
            context=TurnContext(
                persona="личность " * 500,
                recalled_memory="память " * 500,
                live_roster="статус " * 500,
            ),
        )

        situation = assembler.assemble(envelope)

        self.assertLessEqual(len(json.dumps(situation, ensure_ascii=False, separators=(",", ":"))), 2_800)
        self.assertEqual(situation["pending_decisions"][0]["task_id"], "task-pending")

    def test_relationship_migrates_legacy_contract_without_losing_corrections(self):
        ledger = Ledger(self.root / "legacy-relationship.sqlite3")
        ledger.initialize()
        ledger.projection_put(
            "relationship",
            "owner_contract",
            {"language": "ru", "directness": "custom-high"},
            actor="legacy-test",
        )
        relationship = Relationship(ledger)

        relationship.seed()
        snapshot = relationship.snapshot()

        self.assertNotIn("owner_contract", snapshot)
        contract = snapshot["conversation_contract"]
        self.assertEqual(contract["directness"], "custom-high")
        self.assertEqual(contract["relationship"], "peer_brotherly")
        self.assertIn("владелец", contract["forbidden_hierarchy_terms"])
        self.assertIn("Панибратство не равно хамству", contract["panibrat_boundary"])

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
