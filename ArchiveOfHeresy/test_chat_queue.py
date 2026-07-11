import threading
import time
import unittest

from archive_state import TimedChatQueueLock, TimedSessionLocks


class ChatQueueConcurrencyTest(unittest.TestCase):
    def test_exactly_four_pipelines_can_enter_and_fifth_waits(self):
        gate = TimedChatQueueLock(timeout_sec=1, concurrency=4)
        release = threading.Event()
        all_four = threading.Event()
        state_lock = threading.Lock()
        entered = 0

        def holder():
            nonlocal entered
            with gate:
                with state_lock:
                    entered += 1
                    if entered == 4:
                        all_four.set()
                release.wait(timeout=2)

        holders = [threading.Thread(target=holder) for _ in range(4)]
        for thread in holders:
            thread.start()
        self.assertTrue(all_four.wait(timeout=1))
        self.assertEqual(gate.snapshot()["active"], 4)

        fifth_entered = threading.Event()

        def fifth():
            with gate:
                fifth_entered.set()

        fifth_thread = threading.Thread(target=fifth)
        fifth_thread.start()
        time.sleep(0.03)
        self.assertFalse(fifth_entered.is_set())

        release.set()
        self.assertTrue(fifth_entered.wait(timeout=1))
        for thread in holders:
            thread.join(timeout=1)
        fifth_thread.join(timeout=1)
        self.assertEqual(gate.snapshot()["active"], 0)

    def test_nested_pipeline_entry_reuses_the_same_thread_slot(self):
        gate = TimedChatQueueLock(timeout_sec=0.05, concurrency=1)
        with gate:
            self.assertEqual(gate.snapshot()["active"], 1)
            with gate:
                self.assertEqual(gate.snapshot()["active"], 1)
        self.assertEqual(gate.snapshot()["active"], 0)
        self.assertEqual(gate.snapshot()["admitted_total"], 1)

    def test_same_session_is_serial_but_different_sessions_are_parallel(self):
        sessions = TimedSessionLocks(timeout_sec=1)
        release_first = threading.Event()
        first_entered = threading.Event()
        same_entered = threading.Event()
        other_entered = threading.Event()

        def first():
            with sessions.hold("same"):
                first_entered.set()
                release_first.wait(timeout=2)

        def same():
            with sessions.hold("same"):
                same_entered.set()

        def other():
            with sessions.hold("other"):
                other_entered.set()

        first_thread = threading.Thread(target=first)
        same_thread = threading.Thread(target=same)
        other_thread = threading.Thread(target=other)
        first_thread.start()
        self.assertTrue(first_entered.wait(timeout=1))
        same_thread.start()
        other_thread.start()
        self.assertTrue(other_entered.wait(timeout=1))
        time.sleep(0.03)
        self.assertFalse(same_entered.is_set())

        release_first.set()
        self.assertTrue(same_entered.wait(timeout=1))
        first_thread.join(timeout=1)
        same_thread.join(timeout=1)
        other_thread.join(timeout=1)
        self.assertEqual(sessions.snapshot()["sessions"], 0)


if __name__ == "__main__":
    unittest.main()
