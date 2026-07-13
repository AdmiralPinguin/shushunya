from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ShushunyaCore.authority import Authority
from ShushunyaCore.ledger import Ledger
from ShushunyaCore.preferences import Preferences
from ShushunyaCore.schema import PreferenceEvidence


MANIFEST = {
    "capabilities": [
        {"action": "request_warmaster_mission", "available": True},
        {"action": "create_administratum_task", "available": True},
        {
            "action": "deliver_artifact",
            "available": True,
            "artifacts": [{"artifact_id": "artifact-known", "filename": "result.zip"}],
        },
    ]
}


def decision(area="code"):
    return {
        "warmaster_request": {
            "user_request": "Сделай задачу",
            "expected_outcome": "Проверенный результат",
            "capability_area": area,
        }
    }


def artifact_decision(artifact_id="artifact-known"):
    return {"artifact_delivery": {"artifact_id": artifact_id}}


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.tmp.name) / "core.sqlite3")
        self.ledger.initialize()
        self.preferences = Preferences(self.ledger)
        self.authority = Authority(self.preferences)

    def tearDown(self):
        self.tmp.cleanup()

    def test_model_scope_cannot_bypass_owner_restriction(self):
        self.preferences.record(
            PreferenceEvidence(
                action_kind="request_warmaster_mission",
                target_scope="code",
                verdict="never_auto",
                evidence="owner rule",
            )
        )
        verdict = self.authority.authorize("request_warmaster_mission", decision("mixed"), MANIFEST)
        self.assertEqual(verdict.verdict, "ask")

    def test_global_never_auto_beats_old_specific_auto(self):
        self.preferences.record(
            PreferenceEvidence(
                action_kind="request_warmaster_mission",
                target_scope="code",
                verdict="delegate_future",
                evidence="old allow",
            )
        )
        self.preferences.record(
            PreferenceEvidence(
                action_kind="request_warmaster_mission",
                target_scope="*",
                verdict="never_auto",
                evidence="new stop",
            )
        )
        verdict = self.authority.authorize("request_warmaster_mission", decision("code"), MANIFEST)
        self.assertEqual(verdict.verdict, "ask")

    def test_registered_artifact_id_is_authorized(self):
        verdict = self.authority.authorize("deliver_artifact", artifact_decision(), MANIFEST)
        self.assertEqual(verdict.verdict, "auto")

    def test_model_cannot_invent_artifact_id(self):
        verdict = self.authority.authorize(
            "deliver_artifact", artifact_decision("artifact-invented"), MANIFEST,
        )
        self.assertEqual(verdict.verdict, "deny")
        self.assertEqual(verdict.code, "artifact_not_in_capability")
        self.assertIn("зарегистрирован", verdict.explanation)
        self.assertIn("видим", verdict.explanation)
        self.assertIn("result.zip", verdict.explanation)
        self.assertIn("после этого я смогу прислать", verdict.explanation)
        self.assertNotIn("разрешение", verdict.explanation)

    def test_available_flag_without_catalog_does_not_grant_file_access(self):
        manifest = {"capabilities": [{"action": "deliver_artifact", "available": True}]}
        verdict = self.authority.authorize("deliver_artifact", artifact_decision(), manifest)
        self.assertEqual(verdict.verdict, "deny")
        self.assertEqual(verdict.code, "artifact_not_in_capability")
        self.assertIn("ни одного доступного файла", verdict.explanation)

    def test_missing_artifact_id_lists_visible_filenames_without_permission_question(self):
        verdict = self.authority.authorize(
            "deliver_artifact", {"artifact_delivery": {}}, MANIFEST,
        )
        self.assertEqual(verdict.verdict, "deny")
        self.assertEqual(verdict.code, "incomplete_artifact_delivery")
        self.assertIn("result.zip", verdict.explanation)
        self.assertIn("локальный издатель", verdict.explanation)
        self.assertNotIn("разрешение", verdict.explanation)

    def test_unavailable_artifact_catalog_explains_registration_and_visibility(self):
        manifest = {"capabilities": [{"action": "deliver_artifact", "available": False}]}
        verdict = self.authority.authorize("deliver_artifact", artifact_decision(), manifest)
        self.assertEqual(verdict.verdict, "deny")
        self.assertEqual(verdict.code, "artifact_catalog_unavailable")
        self.assertIn("зарегистрирован", verdict.explanation)
        self.assertIn("после этого я смогу прислать", verdict.explanation)
        self.assertIn("session/source", verdict.explanation)
        self.assertIn("ни одного доступного файла", verdict.explanation)


if __name__ == "__main__":
    unittest.main()
