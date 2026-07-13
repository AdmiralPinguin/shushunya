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


if __name__ == "__main__":
    unittest.main()
