import unittest

from PySide6.QtCore import QCoreApplication

from shushunya_desktop.backend import AppBackend
from shushunya_desktop.companion import DemoCompanionProvider


class DemoBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self) -> None:
        self.backend = AppBackend(
            DemoCompanionProvider(),
            demo_mode=True,
            initial_demo_state="sleep",
            demo_cycle=False,
        )

    def tearDown(self) -> None:
        if self.backend.demoCycleRunning:
            self.backend.toggleDemoCycle()
        self.backend.deleteLater()

    def test_initial_state_is_applied_without_polling_core(self) -> None:
        self.assertTrue(self.backend.demoMode)
        self.assertFalse(self.backend.demoCycleRunning)
        self.assertEqual("sleep", self.backend.visualState)
        self.assertEqual("sleep", self.backend.companion.presence)

    def test_manual_cycle_updates_both_visuals_and_fake_snapshot(self) -> None:
        self.backend.nextDemoState()
        self.assertEqual("attention", self.backend.visualState)
        self.assertEqual("attention", self.backend.companion.presence)

        self.backend.previousDemoState()
        self.assertEqual("sleep", self.backend.visualState)
        self.assertEqual("sleep", self.backend.companion.presence)

        self.backend.setDemoStateIndex(6)
        self.assertEqual("triumph", self.backend.visualState)
        self.assertTrue(self.backend.companion.hasResults)

    def test_pause_toggle_and_invalid_state_are_safe(self) -> None:
        self.backend.toggleDemoCycle()
        self.assertTrue(self.backend.demoCycleRunning)
        self.backend.toggleDemoCycle()
        self.assertFalse(self.backend.demoCycleRunning)

        self.backend.setDemoState("not-a-state")
        self.assertEqual("sleep", self.backend.visualState)


if __name__ == "__main__":
    unittest.main()
