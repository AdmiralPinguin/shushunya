import unittest

from shushunya_desktop.companion import CompanionSnapshot
from shushunya_desktop.demo_state import (
    DEMO_STATES,
    demo_snapshot,
    demo_snapshot_by_index,
    demo_state_by_index,
    next_demo_state,
    previous_demo_state,
)


class DemoStateTests(unittest.TestCase):
    def test_cycle_has_the_nine_story_states_in_order(self) -> None:
        self.assertEqual(
            (
                "sleep",
                "attention",
                "thinking",
                "forging",
                "waiting",
                "speaking",
                "triumph",
                "wounded",
                "sealing",
            ),
            DEMO_STATES,
        )
        self.assertEqual(len(DEMO_STATES), len(set(DEMO_STATES)))

    def test_every_state_has_a_matching_standalone_snapshot(self) -> None:
        for state in DEMO_STATES:
            with self.subTest(state=state):
                snapshot = demo_snapshot(state)
                self.assertIsInstance(snapshot, CompanionSnapshot)
                self.assertEqual(state, snapshot.presence)
                self.assertEqual("Шушуня", snapshot.name)
                self.assertTrue(snapshot.utterance.strip())

    def test_quiet_states_do_not_claim_background_work(self) -> None:
        for state in ("sleep", "attention", "speaking"):
            with self.subTest(state=state):
                snapshot = demo_snapshot(state)
                self.assertEqual("", snapshot.current_activity)
                self.assertEqual("", snapshot.owner_request)
                self.assertFalse(snapshot.activities)

    def test_active_states_admit_that_work_is_not_finished(self) -> None:
        for state in ("thinking", "forging", "sealing"):
            with self.subTest(state=state):
                snapshot = demo_snapshot(state)
                self.assertTrue(snapshot.current_activity)
                self.assertTrue(snapshot.activities)
                self.assertEqual("now", snapshot.activities[0].phase)
                self.assertEqual("", snapshot.latest_result)

    def test_waiting_names_the_owner_decision_and_stops_work(self) -> None:
        snapshot = demo_snapshot("waiting")
        self.assertTrue(snapshot.owner_request)
        self.assertEqual("waiting", snapshot.activities[0].phase)
        self.assertIn("не продолжается", snapshot.current_activity)

    def test_terminal_states_report_their_real_outcome(self) -> None:
        triumph = demo_snapshot("triumph")
        wounded = demo_snapshot("wounded")
        self.assertEqual("done", triumph.results[0].phase)
        self.assertTrue(triumph.latest_result)
        self.assertEqual("failed", wounded.results[0].phase)
        self.assertTrue(wounded.latest_result)
        self.assertFalse(wounded.activities)

    def test_next_and_previous_wrap_and_accept_steps(self) -> None:
        self.assertEqual("attention", next_demo_state("sleep"))
        self.assertEqual("sleep", next_demo_state("sealing"))
        self.assertEqual("sealing", previous_demo_state("sleep"))
        self.assertEqual("sleep", previous_demo_state("attention"))
        self.assertEqual("forging", next_demo_state("sleep", step=3))
        self.assertEqual("wounded", previous_demo_state("sleep", step=2))
        self.assertEqual("sleep", next_demo_state("sleep", step=len(DEMO_STATES)))

    def test_state_and_snapshot_can_be_selected_by_cyclic_index(self) -> None:
        self.assertEqual("sleep", demo_state_by_index(0))
        self.assertEqual("sealing", demo_state_by_index(-1))
        self.assertEqual("attention", demo_state_by_index(len(DEMO_STATES) + 1))
        self.assertEqual("wounded", demo_snapshot_by_index(-2).presence)

    def test_invalid_state_and_index_fail_loudly(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown demo state"):
            demo_snapshot("idle")
        with self.assertRaisesRegex(ValueError, "unknown demo state"):
            next_demo_state("missing")
        with self.assertRaises(TypeError):
            demo_state_by_index(1.5)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            next_demo_state("sleep", True)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
