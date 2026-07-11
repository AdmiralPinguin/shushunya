from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import mission_store  # noqa: E402


class MissionStoreRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self._old_root = mission_store.STORE_ROOT
        self._old_missions = mission_store._MISSIONS
        self._limits = {
            name: getattr(mission_store, name)
            for name in (
                "MAX_PERSISTED_STATE_BYTES",
                "MAX_PERSISTED_PAYLOAD_BYTES",
                "MAX_PERSISTED_RESULT_BYTES",
                "MAX_MISSION_DURABLE_BYTES",
                "MAX_EVENT_FILE_BYTES",
                "MAX_EVENT_BYTES",
                "MAX_EVENTS_IN_MEMORY",
                "MAX_TERMINAL_MISSIONS",
                "TERMINAL_TTL_SECONDS",
                "MAX_TERMINAL_DURABLE_BYTES",
                "MAX_STORE_DURABLE_BYTES",
                "MAX_STORE_MISSIONS",
                "MAX_ACTIVE_MISSIONS",
            )
        }
        mission_store.STORE_ROOT = Path(self._temporary.name)
        mission_store._MISSIONS = {}

    def tearDown(self) -> None:
        mission_store.STORE_ROOT = self._old_root
        mission_store._MISSIONS = self._old_missions
        for name, value in self._limits.items():
            setattr(mission_store, name, value)
        self._temporary.cleanup()

    def test_canonical_request_hash_excludes_task_id_and_is_snapshotted(self) -> None:
        payload = {
            "task_id": "transport-only",
            "z": [3, {"юникод": "да"}],
            "a": {"two": 2, "one": 1},
        }
        expected_body = dict(payload)
        expected_body.pop("task_id")
        canonical = json.dumps(
            expected_body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        expected = hashlib.sha256(canonical).hexdigest()

        mission = mission_store.create("hash-case", "fix it")
        mission.set_payload(payload)

        state = json.loads((mission._dir() / "mission.json").read_text(encoding="utf-8"))
        self.assertEqual(mission.payload, payload)
        self.assertEqual(mission.request_sha256, expected)
        self.assertEqual(mission.snapshot()["request_sha256"], expected)
        self.assertEqual(state["request_sha256"], expected)
        self.assertIsNone(state["payload"])
        self.assertEqual(state["payload_ref"]["path"], "payload.json")
        self.assertEqual(
            json.loads((mission._dir() / "payload.json").read_text(encoding="utf-8")),
            payload,
        )

        changed_transport_id = dict(payload, task_id="another-id")
        self.assertEqual(mission_store.request_sha256(changed_transport_id), expected)

    def test_active_and_still_executing_missions_are_never_pruned(self) -> None:
        mission_store.MAX_ACTIVE_MISSIONS = len(mission_store.ACTIVE_STATUSES) + 1
        mission_store.MAX_TERMINAL_MISSIONS = 0
        mission_store.TERMINAL_TTL_SECONDS = 0
        now = time.time()
        expected: set[str] = set()
        for index, status in enumerate(sorted(mission_store.ACTIVE_STATUSES)):
            mission = mission_store.create(f"active-{index}", "goal")
            with mission._lock:
                mission.status = status
                mission.updated = now - 100_000
                mission._persist(raise_errors=True)
            expected.add(mission.id)

        # Cancellation is terminal to the API, but GC must wait until the worker
        # has really exited before deleting its persisted request.
        executing = mission_store.create("cancelled-but-executing", "goal")
        with executing._lock:
            executing.status = "cancelled"
            executing.updated = now - 100_000
            executing._worker_active = True
            executing._persist(raise_errors=True)
        expected.add(executing.id)

        removed = mission_store.prune(now=now)
        self.assertEqual(removed, [])
        self.assertEqual(set(mission_store._MISSIONS), expected)
        for mission_id in expected:
            self.assertTrue((mission_store.STORE_ROOT / mission_id / "mission.json").is_file())

    def test_terminal_retention_is_bounded_by_count(self) -> None:
        mission_store.MAX_TERMINAL_MISSIONS = 2
        mission_store.TERMINAL_TTL_SECONDS = 10**12
        missions = []
        for index in range(5):
            mission = mission_store.create(f"terminal-{index}", "goal")
            mission.set_status("done")
            with mission._lock:
                mission.updated = 100.0 + index
                mission._persist(raise_errors=True)
            missions.append(mission)

        mission_store.prune(now=1_000.0)
        self.assertEqual(set(mission_store._MISSIONS), {"terminal-3", "terminal-4"})
        self.assertFalse((mission_store.STORE_ROOT / "terminal-0").exists())
        self.assertFalse((mission_store.STORE_ROOT / "terminal-2").exists())
        self.assertTrue((mission_store.STORE_ROOT / "terminal-3" / "mission.json").is_file())
        self.assertTrue((mission_store.STORE_ROOT / "terminal-4" / "mission.json").is_file())

    def test_oversized_state_rehydrates_as_blocked_metadata(self) -> None:
        mission_store.MAX_PERSISTED_STATE_BYTES = 512
        directory = mission_store.STORE_ROOT / "oversized-state"
        directory.mkdir(parents=True)
        (directory / "mission.json").write_text(
            json.dumps(
                {
                    "id": "oversized-state",
                    "goal": "goal",
                    "status": "running",
                    "payload": {"blob": "x" * 2_000},
                }
            ),
            encoding="utf-8",
        )

        mission_store._MISSIONS = {}
        mission_store._rehydrate()
        restored = mission_store.get("oversized-state")

        self.assertIsNotNone(restored)
        self.assertEqual(restored.status, "blocked")
        self.assertFalse(restored.result["accepted"])
        self.assertIn("exceeds 512 bytes", restored.result["error"])
        self.assertFalse(mission_store.resume("oversized-state", lambda _m: {"status": "done"}))

    def test_payload_limit_rejects_before_mutating_persisted_request(self) -> None:
        mission = mission_store.create("payload-limit", "goal")
        mission_store.MAX_PERSISTED_PAYLOAD_BYTES = 32
        with self.assertRaises(mission_store.PayloadTooLargeError):
            mission.set_payload({"blob": "x" * 100})
        state = json.loads((mission._dir() / "mission.json").read_text(encoding="utf-8"))
        self.assertIsNone(mission.payload)
        self.assertIsNone(state["payload"])
        self.assertIsNone(state["payload_ref"])
        self.assertIsNone(state["request_sha256"])

    def test_payload_size_boundary_accepts_max_and_rejects_max_plus_one(self) -> None:
        empty_size = len(
            json.dumps(
                {"blob": ""}, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        )
        mission_store.MAX_PERSISTED_PAYLOAD_BYTES = empty_size + 64
        exact_max = {"blob": "x" * 64}
        over_max = {"blob": "x" * 65}

        accepted = mission_store.create("payload-at-max", "goal", payload=exact_max)
        self.assertEqual(accepted.payload, exact_max)
        with self.assertRaises(mission_store.PayloadTooLargeError):
            mission_store.create("payload-over-max", "goal", payload=over_max)
        self.assertIsNone(mission_store.get("payload-over-max"))
        self.assertFalse((mission_store.STORE_ROOT / "payload-over-max").exists())

    def test_large_accepted_patch_result_is_persisted_and_rehydrated_exactly(self) -> None:
        payload = {"task_id": "large-result", "goal": "fix"}
        patch = "diff --git a/a b/a\n" + ("+x\n" * 750_000)
        verdict = {
            "status": "done",
            "accepted": True,
            "patch_bundle": patch,
            "files": {"a": "x"},
            "checks": [{"ok": True, "target": "unit"}],
        }
        self.assertGreater(len(json.dumps(verdict).encode("utf-8")), 2_000_000)
        mission = mission_store.create("large-result", "fix", payload=payload)
        mission_store.run_async(mission, lambda _mission: verdict)
        for _ in range(300):
            if not mission.snapshot()["inflight"]:
                break
            time.sleep(0.01)

        snapshot = mission.snapshot()
        self.assertEqual(snapshot["status"], "done")
        self.assertTrue(snapshot["result"]["accepted"])
        self.assertEqual(snapshot["result"], verdict)
        result_path = mission._dir() / "result.json"
        expected_result_bytes = json.dumps(
            verdict, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.assertEqual(result_path.read_bytes(), expected_result_bytes)
        self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), verdict)
        state = json.loads((mission._dir() / "mission.json").read_text(encoding="utf-8"))
        self.assertIsNone(state["result"])
        self.assertEqual(state["result_ref"]["sha256"], hashlib.sha256(result_path.read_bytes()).hexdigest())

        mission_store._MISSIONS = {}
        mission_store._rehydrate()
        restored = mission_store.get("large-result")
        self.assertEqual(restored.result, verdict)
        self.assertTrue(restored.result["accepted"])

    def test_max_payload_and_max_result_coexist_without_compaction(self) -> None:
        payload_empty_size = len(
            json.dumps({"blob": ""}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        result_empty_size = len(
            json.dumps(
                {"status": "done", "accepted": True, "patch_bundle": ""},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        mission_store.MAX_PERSISTED_PAYLOAD_BYTES = payload_empty_size + 256
        mission_store.MAX_PERSISTED_RESULT_BYTES = result_empty_size + 384
        mission_store.MAX_MISSION_DURABLE_BYTES = 20_000
        payload = {"blob": "p" * 256}
        verdict = {
            "status": "done",
            "accepted": True,
            "patch_bundle": "r" * 384,
        }

        mission = mission_store.create("both-at-max", "goal", payload=payload)
        self.assertEqual(mission.complete_result(verdict), "done")
        self.assertEqual(mission.payload, payload)
        self.assertEqual(mission.result, verdict)
        self.assertEqual(
            json.loads((mission._dir() / "payload.json").read_text(encoding="utf-8")),
            payload,
        )
        self.assertEqual(
            json.loads((mission._dir() / "result.json").read_text(encoding="utf-8")),
            verdict,
        )

        too_large = dict(verdict, patch_bundle="r" * 385)
        blocked = mission_store.create("result-over-max", "goal", payload={"ok": True})
        self.assertEqual(blocked.complete_result(too_large), "blocked")
        self.assertFalse(blocked.result["accepted"])
        self.assertIn("could not be persisted", blocked.result["error"])

    def test_atomic_create_with_payload_is_immediately_hashed(self) -> None:
        payload = {"task_id": "atomic", "goal": "fix", "mode": "patch"}
        mission = mission_store.create("atomic-create", "fix", payload=payload)
        self.assertIs(mission_store.get("atomic-create"), mission)
        self.assertEqual(
            mission.snapshot()["request_sha256"],
            mission_store.request_sha256(payload),
        )
        state = json.loads((mission._dir() / "mission.json").read_text(encoding="utf-8"))
        self.assertIsNone(state["payload"])
        self.assertEqual(state["payload_ref"]["path"], "payload.json")
        self.assertEqual(
            json.loads((mission._dir() / "payload.json").read_text(encoding="utf-8")),
            payload,
        )
        self.assertEqual(state["request_sha256"], mission_store.request_sha256(payload))

    def test_create_and_start_is_not_visible_before_inflight_persist(self) -> None:
        entered_start = threading.Event()
        release_start = threading.Event()
        getter_done = threading.Event()
        real_run_async = mission_store.run_async
        seen: dict[str, object] = {}

        def gated_run(mission, fn):
            entered_start.set()
            self.assertTrue(release_start.wait(timeout=2))
            return real_run_async(mission, fn)

        def creator():
            seen["mission"] = mission_store.create_and_run(
                "atomic-start", "goal", {"goal": "goal"},
                lambda _mission: {"status": "done", "accepted": True},
            )

        def getter():
            mission = mission_store.get("atomic-start")
            seen["observed"] = mission.snapshot(include_result=False) if mission else None
            getter_done.set()

        with mock.patch.object(mission_store, "run_async", side_effect=gated_run):
            create_thread = threading.Thread(target=creator)
            create_thread.start()
            self.assertTrue(entered_start.wait(timeout=2))
            get_thread = threading.Thread(target=getter)
            get_thread.start()
            self.assertFalse(getter_done.wait(timeout=0.05))
            release_start.set()
            create_thread.join(timeout=2)
            get_thread.join(timeout=2)

        observed = seen["observed"]
        self.assertTrue(observed["inflight"] or observed["cleanup_complete"])
        self.assertFalse(create_thread.is_alive())
        self.assertFalse(get_thread.is_alive())

    def test_atomic_create_rolls_back_oversized_payload_and_id(self) -> None:
        mission_store.MAX_PERSISTED_PAYLOAD_BYTES = 32
        with self.assertRaises(mission_store.PayloadTooLargeError):
            mission_store.create(
                "atomic-oversize",
                "goal",
                payload={"blob": "x" * 100},
            )
        self.assertIsNone(mission_store.get("atomic-oversize"))
        self.assertFalse((mission_store.STORE_ROOT / "atomic-oversize").exists())

        # The rollback releases the durable reservation, so a valid retry can
        # claim the exact same id.
        mission_store.MAX_PERSISTED_PAYLOAD_BYTES = self._limits["MAX_PERSISTED_PAYLOAD_BYTES"]
        retried = mission_store.create("atomic-oversize", "goal", payload={"ok": True})
        self.assertIs(mission_store.get("atomic-oversize"), retried)

    def test_concurrent_get_cannot_observe_half_persisted_create(self) -> None:
        payload = {"task_id": "concurrent", "goal": "fix"}
        entered_persist = threading.Event()
        release_persist = threading.Event()
        getter_done = threading.Event()
        created: dict[str, object] = {}
        real_persist = mission_store.Mission._persist

        def gated_persist(instance, *args, **kwargs):
            if instance.id == "concurrent-create":
                entered_persist.set()
                self.assertTrue(release_persist.wait(timeout=2))
            return real_persist(instance, *args, **kwargs)

        def creator() -> None:
            created["mission"] = mission_store.create(
                "concurrent-create", "fix", payload=payload
            )

        def getter() -> None:
            mission = mission_store.get("concurrent-create")
            created["snapshot"] = mission.snapshot() if mission else None
            getter_done.set()

        with mock.patch.object(mission_store.Mission, "_persist", gated_persist):
            create_thread = threading.Thread(target=creator)
            create_thread.start()
            self.assertTrue(entered_persist.wait(timeout=2))
            get_thread = threading.Thread(target=getter)
            get_thread.start()
            self.assertFalse(getter_done.wait(timeout=0.05))
            release_persist.set()
            create_thread.join(timeout=2)
            get_thread.join(timeout=2)

        self.assertFalse(create_thread.is_alive())
        self.assertFalse(get_thread.is_alive())
        self.assertEqual(
            created["snapshot"]["request_sha256"],
            mission_store.request_sha256(payload),
        )

    def test_events_are_bounded_in_memory_and_on_disk(self) -> None:
        mission_store.MAX_EVENTS_IN_MEMORY = 3
        mission_store.MAX_EVENT_BYTES = 160
        mission_store.MAX_EVENT_FILE_BYTES = 360
        mission = mission_store.create("bounded-events", "goal")
        for index in range(20):
            mission.record("noisy", {"index": index, "blob": "z" * 2_000})
        events_path = mission._dir() / "events.jsonl"
        self.assertLessEqual(len(mission.events), 3)
        self.assertLessEqual(events_path.stat().st_size, 360)
        for event in mission.events:
            encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.assertLessEqual(len(encoded), 160)

    def test_cancel_stays_cancelling_until_worker_cleanup_returns(self) -> None:
        mission_store.MAX_TERMINAL_MISSIONS = 0
        mission_store.TERMINAL_TTL_SECONDS = 0
        started = threading.Event()
        release_cleanup = threading.Event()
        mission = mission_store.create(
            "cancel-lifecycle", "goal", payload={"goal": "goal"}
        )

        def worker(_mission):
            started.set()
            self.assertTrue(release_cleanup.wait(timeout=2))
            return {"status": "done", "accepted": True}

        mission_store.run_async(mission, worker)
        self.assertTrue(started.wait(timeout=2))
        self.assertTrue(mission_store.cancel(mission.id))
        cancelling = mission.snapshot()
        self.assertEqual(cancelling["status"], "cancelling")
        self.assertTrue(cancelling["inflight"])
        self.assertFalse(cancelling["cleanup_complete"])
        self.assertEqual(mission_store.prune(), [])
        self.assertIs(mission_store.get(mission.id), mission)

        # Retain the just-finished terminal record long enough to inspect the
        # final state, then prove it becomes GC-eligible.
        mission_store.MAX_TERMINAL_MISSIONS = 1
        mission_store.TERMINAL_TTL_SECONDS = 10**12
        release_cleanup.set()
        for _ in range(100):
            if not mission.snapshot()["inflight"]:
                break
            time.sleep(0.01)
        finished = mission.snapshot()
        self.assertEqual(finished["status"], "cancelled")
        self.assertFalse(finished["inflight"])
        self.assertTrue(finished["cleanup_complete"])

        state = json.loads((mission._dir() / "mission.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "cancelled")
        self.assertFalse(state["inflight"])
        self.assertTrue(state["cleanup_complete"])

        mission_store.MAX_TERMINAL_MISSIONS = 0
        mission_store.TERMINAL_TTL_SECONDS = 0
        self.assertEqual(mission_store.prune(), [mission.id])
        self.assertIsNone(mission_store.get(mission.id))

    def test_cancel_does_not_mask_quarantined_cleanup_failure(self) -> None:
        started = threading.Event()
        release = threading.Event()
        mission = mission_store.create(
            "cancel-cleanup-failed", "goal", payload={"goal": "goal"}
        )

        def worker(_mission):
            started.set()
            self.assertTrue(release.wait(timeout=2))
            return {
                "status": "blocked",
                "accepted": False,
                "cleanup_complete": False,
                "boundary_quarantined": True,
                "error": "surviving process",
            }

        mission_store.run_async(mission, worker)
        self.assertTrue(started.wait(timeout=2))
        self.assertTrue(mission_store.cancel(mission.id))
        self.assertEqual(mission.status, "cancelling")
        release.set()
        for _ in range(100):
            if not mission.snapshot()["inflight"]:
                break
            time.sleep(0.01)
        snapshot = mission.snapshot()
        self.assertEqual(snapshot["status"], "blocked")
        self.assertFalse(snapshot["result"]["accepted"])
        self.assertTrue(snapshot["result"]["boundary_quarantined"])
        self.assertFalse(snapshot["cleanup_complete"])
        self.assertEqual(mission_store.prune(), [])

    def test_cancel_expected_identity_prevents_aba(self) -> None:
        old = mission_store.create("cancel-aba", "goal")
        old.set_status("done")
        replacement = mission_store.Mission("cancel-aba", "new goal")
        replacement.status = "running"
        replacement.inflight = True
        replacement.cleanup_complete = False
        mission_store._MISSIONS["cancel-aba"] = replacement
        self.assertFalse(mission_store.cancel("cancel-aba", expected=old))
        self.assertFalse(replacement.cancelled.is_set())
        self.assertEqual(replacement.status, "running")

    def test_lifecycle_persistence_failure_is_blocked_in_memory(self) -> None:
        mission = mission_store.create("persist-fail", "goal", payload={"goal": "goal"})
        with mock.patch.object(
            mission_store.Mission, "_atomic_write", side_effect=OSError("disk full")
        ):
            with self.assertRaises(mission_store.MissionPersistenceError):
                mission.set_status("done")
        snapshot = mission.snapshot()
        self.assertEqual(snapshot["status"], "blocked")
        self.assertFalse(snapshot["result"]["accepted"])
        self.assertFalse(snapshot["cleanup_complete"])
        self.assertTrue(mission._resume_disabled)

    def test_corrupt_lazy_payload_cannot_resume_with_goal_fallback(self) -> None:
        payload = {"goal": "exact", "workspace_files": {"a.py": "x=1\n"}}
        mission = mission_store.create("corrupt-resume", "goal", payload=payload)
        mission.set_status("failed")
        mission_store._MISSIONS = {}
        mission_store._rehydrate()
        restored = mission_store.get("corrupt-resume")
        self.assertFalse(restored._payload_loaded)
        (mission_store.STORE_ROOT / "corrupt-resume" / "payload.json").write_text(
            '{"goal":"tampered"}', encoding="utf-8"
        )
        called = []
        self.assertFalse(mission_store.resume(
            "corrupt-resume", lambda _mission: called.append(True) or {"status": "done"},
            expected=restored, require_payload=True,
        ))
        self.assertEqual(called, [])
        self.assertEqual(restored.status, "blocked")
        self.assertTrue(restored._resume_disabled)

    def test_rehydrate_enumeration_stops_at_configured_count(self) -> None:
        mission_store.MAX_STORE_MISSIONS = 2
        for name in ("one", "two", "three"):
            directory = mission_store.STORE_ROOT / name
            directory.mkdir(mode=0o700)
        mission_store._MISSIONS = {}
        with self.assertRaisesRegex(RuntimeError, "more than 2 entries"):
            mission_store._rehydrate()

    def test_event_and_hash_reads_do_not_load_large_lazy_result(self) -> None:
        mission = mission_store.create("lazy-events", "goal", payload={"goal": "goal"})
        mission.complete_result({
            "status": "done", "accepted": True, "patch_bundle": "x" * 200_000,
        })
        mission_store._MISSIONS = {}
        mission_store._rehydrate()
        restored = mission_store.get("lazy-events")
        self.assertFalse(restored._result_loaded)
        self.assertEqual(restored.events_snapshot(), restored.events)
        self.assertEqual(restored.request_sha256, mission_store.request_sha256({"goal": "goal"}))
        self.assertFalse(restored._result_loaded)

    def test_restart_blocks_terminal_result_whose_cleanup_never_completed(self) -> None:
        mission = mission_store.create(
            "crashed-after-result", "goal", payload={"goal": "goal"}
        )
        with mission._lock:
            mission.result = {"status": "done", "accepted": True, "patch_bundle": "patch"}
            mission.status = "done"
            mission.inflight = True
            mission.cleanup_complete = False
            mission._persist(raise_errors=True)
        self.assertFalse(
            mission_store.resume(
                mission.id, lambda _mission: {"status": "done", "accepted": True}
            )
        )

        mission_store._MISSIONS = {}
        mission_store._rehydrate()
        restored = mission_store.get("crashed-after-result")
        snapshot = restored.snapshot()
        self.assertEqual(snapshot["status"], "blocked")
        self.assertFalse(snapshot["result"]["accepted"])
        self.assertFalse(snapshot["inflight"])
        self.assertFalse(snapshot["cleanup_complete"])
        self.assertEqual(restored.payload, {"goal": "goal"})
        self.assertEqual(
            snapshot["request_sha256"],
            mission_store.request_sha256({"goal": "goal"}),
        )

    def test_restarted_unclean_records_are_nonresumable_but_retention_bounded(self) -> None:
        mission_store.MAX_ACTIVE_MISSIONS = 4
        mission_store.MAX_TERMINAL_MISSIONS = 1
        mission_store.TERMINAL_TTL_SECONDS = 10**12
        for index in range(3):
            mission = mission_store.create(
                f"crash-bounded-{index}", "goal", payload={"goal": str(index)}
            )
            with mission._lock:
                mission.status = "running"
                mission.inflight = True
                mission.cleanup_complete = False
                mission._persist(raise_errors=True)

        mission_store._MISSIONS = {}
        mission_store._rehydrate()
        self.assertEqual(len(mission_store._MISSIONS), 1)
        restored = next(iter(mission_store._MISSIONS.values()))
        self.assertEqual(restored.status, "blocked")
        self.assertFalse(restored.cleanup_complete)
        self.assertFalse(mission_store.resume(restored.id, lambda _m: {"status": "done"}))

    def test_active_admission_is_one_and_reopens_after_terminal_cleanup(self) -> None:
        mission_store.MAX_ACTIVE_MISSIONS = 1
        first = mission_store.create("admission-first", "goal", payload={"goal": "one"})
        with self.assertRaises(mission_store.MissionCapacityError):
            mission_store.create("admission-second", "goal", payload={"goal": "two"})
        self.assertFalse((mission_store.STORE_ROOT / "admission-second").exists())
        first.set_status("done")
        second = mission_store.create("admission-second", "goal", payload={"goal": "two"})
        self.assertIs(mission_store.get(second.id), second)

    def test_global_store_count_and_byte_admission_fail_closed(self) -> None:
        mission_store.MAX_TERMINAL_MISSIONS = 10
        mission_store.TERMINAL_TTL_SECONDS = 10**12
        first = mission_store.create("capacity-first", "goal", payload={"blob": "x" * 256})
        first.set_status("done")
        mission_store.MAX_STORE_MISSIONS = 1
        with self.assertRaises(mission_store.MissionCapacityError):
            mission_store.create("capacity-count", "goal", payload={"ok": True})

        mission_store.MAX_STORE_MISSIONS = 10
        mission_store.MAX_STORE_DURABLE_BYTES = mission_store._store_durable_bytes() + 64
        with self.assertRaises(mission_store.MissionCapacityError):
            mission_store.create("capacity-bytes", "goal", payload={"blob": "z" * 512})
        self.assertFalse((mission_store.STORE_ROOT / "capacity-bytes").exists())

    def test_terminal_byte_budget_deletes_oldest_only(self) -> None:
        mission_store.MAX_TERMINAL_MISSIONS = 10
        mission_store.TERMINAL_TTL_SECONDS = 10**12
        first = mission_store.create("bytes-old", "goal", payload={"blob": "a" * 1024})
        first.set_status("done")
        with first._lock:
            first.updated = 10
            first._persist(raise_errors=True)
        second = mission_store.create("bytes-new", "goal", payload={"blob": "b" * 1024})
        second.set_status("done")
        with second._lock:
            second.updated = 20
            second._persist(raise_errors=True)
        mission_store.MAX_TERMINAL_DURABLE_BYTES = (
            mission_store._tree_durable_bytes(mission_store.STORE_ROOT / second.id) + 128
        )
        removed = mission_store.prune(now=30)
        self.assertIn(first.id, removed)
        self.assertNotIn(second.id, removed)
        self.assertIsNone(mission_store.get(first.id))
        self.assertIs(mission_store.get(second.id), second)

    def test_rehydrate_keeps_large_blobs_lazy_until_requested(self) -> None:
        payload = {"blob": "p" * 20_000}
        result = {"status": "done", "accepted": True, "patch_bundle": "r" * 20_000}
        mission = mission_store.create("lazy-blobs", "goal", payload=payload)
        self.assertEqual(mission.complete_result(result), "done")
        mission_store._MISSIONS = {}
        mission_store._rehydrate()
        restored = mission_store.get("lazy-blobs")
        self.assertFalse(restored._payload_loaded)
        self.assertFalse(restored._result_loaded)
        self.assertEqual(restored.snapshot()["result"], result)
        self.assertFalse(restored._payload_loaded)
        self.assertEqual(restored.payload, payload)

    def test_modes_are_private_even_under_umask_022_and_answer_is_durable(self) -> None:
        previous_umask = os.umask(0o022)
        try:
            mission = mission_store.create("private-modes", "goal", payload={"goal": "x"})
            mission.set_status("needs_user")
            self.assertTrue(mission.provide_answer("durable answer"))
            mission.record("note", {"text": "event"})
            mission.complete_result({"status": "done", "accepted": True})
        finally:
            os.umask(previous_umask)
        directory = mission_store.STORE_ROOT / mission.id
        self.assertEqual(stat.S_IMODE(mission_store.STORE_ROOT.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for name in ("mission.json", "payload.json", "result.json", "events.jsonl"):
            self.assertEqual(stat.S_IMODE((directory / name).stat().st_mode), 0o600)
        state = json.loads((directory / "mission.json").read_text(encoding="utf-8"))
        self.assertEqual(state["answer"], "durable answer")

    def test_symlink_store_and_foreign_owned_entries_are_rejected(self) -> None:
        real = Path(self._temporary.name) / "real-store"
        real.mkdir()
        linked = Path(self._temporary.name) / "linked-store"
        try:
            linked.symlink_to(real, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        mission_store.STORE_ROOT = linked
        with self.assertRaises(ValueError):
            mission_store._ensure_store_root()

        mission_store.STORE_ROOT = real
        real.chmod(0o700)
        with mock.patch.object(mission_store, "_owned_by_service", return_value=False):
            with self.assertRaises(PermissionError):
                mission_store._secure_directory(real, fix_mode=True)

    def test_existing_directory_is_a_duplicate_even_without_memory_entry(self) -> None:
        mission_store.create("reserved-id", "goal")
        mission_store._MISSIONS = {}
        with self.assertRaises(mission_store.MissionExistsError):
            mission_store.create("reserved-id", "another goal")


if __name__ == "__main__":
    unittest.main()
