import unittest

from shushunya_desktop.companion import (
    CoreCompanionProvider,
    DemoCompanionProvider,
    build_snapshot,
    idle_snapshot,
)


class CompanionStateTests(unittest.TestCase):
    def test_empty_core_is_quiet_presence(self) -> None:
        snapshot = build_snapshot(
            name="Шушуня",
            commitments_payload={"commitments": []},
            agenda_payload={"items": [], "next_useful": None},
        )
        self.assertEqual("idle", snapshot.presence)
        self.assertEqual("Я здесь.", snapshot.utterance)
        self.assertFalse(snapshot.activities)
        self.assertFalse(snapshot.agenda)

    def test_commitment_becomes_human_activity_without_machine_status(self) -> None:
        snapshot = build_snapshot(
            name="Шушуня",
            commitments_payload={
                "commitments": [
                    {
                        "id": "commitment-1",
                        "goal": "доделать Галагу",
                        "state": "working",
                        "honest_status": "Абаддон подтвердил task_id=internal-7 на порту 7000",
                    }
                ]
            },
            agenda_payload={"items": []},
        )
        visible = " ".join(item.text + " " + item.detail for item in snapshot.activities)
        self.assertIn("Сейчас занимаюсь: доделать Галагу", visible)
        self.assertNotIn("Абаддон", visible)
        self.assertNotIn("task_id", visible)
        self.assertNotIn("7000", visible)

    def test_waiting_user_exposes_only_required_action(self) -> None:
        snapshot = build_snapshot(
            name="Шушуня",
            commitments_payload={
                "commitments": [
                    {
                        "id": "commitment-2",
                        "goal": "выбрать финальный стиль",
                        "state": "waiting_user",
                        "diagnostic": {
                            "required_action": "Скажи, какой из двух вариантов оставить.",
                            "evidence": {"delegate_ref": "secret", "port": 7000},
                        },
                    }
                ]
            },
            agenda_payload={"items": []},
        )
        self.assertEqual("waiting", snapshot.presence)
        self.assertEqual("Скажи, какой из двух вариантов оставить.", snapshot.owner_request)
        self.assertNotIn("secret", snapshot.owner_request)

    def test_quarantine_is_internal_and_never_asks_the_user(self) -> None:
        snapshot = build_snapshot(
            name="Shushunya",
            commitments_payload={
                "commitments": [
                    {
                        "id": "commitment-quarantined",
                        "goal": "finish the mission",
                        "state": "quarantined",
                        "diagnostic": {
                            "required_action": "Abaddon must publish POST /internal",
                        },
                    }
                ]
            },
            agenda_payload={"items": []},
        )
        self.assertEqual("idle", snapshot.presence)
        self.assertEqual("", snapshot.owner_request)
        self.assertEqual("recovering", snapshot.activities[0].phase)
        visible = " ".join(item.text + " " + item.detail for item in snapshot.activities)
        self.assertNotIn("Abaddon", visible)
        self.assertNotIn("POST", visible)

    def test_internal_waits_are_not_rendered_as_waiting_for_user(self) -> None:
        for state in ("waiting_external", "retry_wait"):
            with self.subTest(state=state):
                snapshot = build_snapshot(
                    name="Shushunya",
                    commitments_payload={
                        "commitments": [
                            {
                                "id": f"commitment-{state}",
                                "goal": "finish the mission",
                                "state": state,
                            }
                        ]
                    },
                    agenda_payload={"items": []},
                )
                self.assertEqual("idle", snapshot.presence)
                self.assertEqual("", snapshot.owner_request)
                self.assertEqual("recovering", snapshot.activities[0].phase)

    def test_agenda_is_intention_not_execution_claim(self) -> None:
        snapshot = build_snapshot(
            name="Шушуня",
            commitments_payload={"commitments": []},
            agenda_payload={
                "items": [
                    {"id": "a1", "title": "разобрать накопившиеся заметки", "state": "queued"},
                    {"id": "a2", "title": "собрать единый визуальный стиль", "state": "queued"},
                ],
                "next_useful": {"id": "a2"},
            },
        )
        self.assertEqual("thinking", snapshot.presence)
        self.assertEqual("later", snapshot.agenda[0].phase)
        self.assertEqual("next", snapshot.agenda[1].phase)

    def test_results_use_allowlist_and_never_dump_raw_json(self) -> None:
        snapshot = build_snapshot(
            name="Шушуня",
            commitments_payload={
                "commitments": [
                    {
                        "id": "commitment-3",
                        "goal": "собрать справку",
                        "state": "succeeded",
                        "result": {
                            "summary": {"answer": "Вот итоговая справка."},
                            "delegate_ref": "internal-worker",
                            "status_code": 200,
                        },
                    }
                ]
            },
            agenda_payload={"items": []},
        )
        visible = snapshot.results[0].text + " " + snapshot.results[0].detail
        self.assertIn("Вот итоговая справка.", visible)
        self.assertNotIn("internal-worker", visible)
        self.assertNotIn("status_code", visible)

    def test_smoke_and_verification_sources_are_not_public(self) -> None:
        self.assertFalse(CoreCompanionProvider._source_is_public("verification"))
        self.assertFalse(CoreCompanionProvider._source_is_public("codex-live-smoke"))
        self.assertTrue(CoreCompanionProvider._source_is_public("app-chat-session"))

    def test_old_utterance_does_not_leave_presence_speaking_forever(self) -> None:
        snapshot = build_snapshot(
            name="Шушуня",
            commitments_payload={"commitments": []},
            agenda_payload={"items": []},
            utterance="Это моя последняя реплика.",
            utterance_recent=False,
        )
        self.assertEqual("idle", snapshot.presence)
        self.assertEqual("Это моя последняя реплика.", snapshot.utterance)

    def test_idle_fallback_contains_no_fake_work(self) -> None:
        snapshot = idle_snapshot()
        self.assertEqual("Я здесь.", snapshot.utterance)
        self.assertFalse(snapshot.activities)
        self.assertFalse(snapshot.agenda)
        self.assertFalse(snapshot.results)

    def test_empty_preview_is_honestly_empty(self) -> None:
        snapshot = DemoCompanionProvider("empty").fetch()
        self.assertEqual("idle", snapshot.presence)
        self.assertFalse(snapshot.activities)
        self.assertFalse(snapshot.agenda)
        self.assertFalse(snapshot.results)

    def test_stress_preview_exercises_every_list(self) -> None:
        snapshot = DemoCompanionProvider("stress").fetch()
        self.assertEqual(4, len(snapshot.activities))
        self.assertEqual(5, len(snapshot.agenda))
        self.assertEqual(4, len(snapshot.results))
        self.assertTrue(snapshot.owner_request)


if __name__ == "__main__":
    unittest.main()
