import hashlib
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import task_page


class TaskPageStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "task-memory.sqlite3"
        self.store = task_page.TaskPageStore(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def assert_document(self, document):
        for field in ("task_memory_id", "revision", "sha256", "snapshot", "content"):
            self.assertIn(field, document)
        blob = json.dumps(
            document["snapshot"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.assertEqual(document["sha256"], hashlib.sha256(blob).hexdigest())

    def test_init_alias_lookup_markdown_and_restart(self):
        created = self.store.init(
            root_task_id="goal-42",
            task_id="attempt-1",
            goal_verbatim="Собрать автономную бригаду",
            aliases=["mission-1"],
            snapshot={
                "desired_outcome": "Рабочий результат",
                "success_conditions": ["Проверено исполнением"],
                "next_actions": ["Запустить"],
            },
            actor="abaddon",
        )
        self.assert_document(created)
        self.assertEqual(created["revision"], 1)
        self.assertIn("Собрать автономную бригаду", created["content"])
        self.assertNotIn("WikiBookshelf", created["content"])

        by_attempt = self.store.get(task_id="attempt-1")
        by_mission = self.store.get(task_id="mission-1")
        self.assertEqual(by_attempt["task_memory_id"], created["task_memory_id"])
        self.assertEqual(by_mission["task_memory_id"], created["task_memory_id"])

        restarted = task_page.TaskPageStore(self.db_path)
        after_restart = restarted.get(task_id="goal-42")
        self.assertEqual(after_restart["sha256"], created["sha256"])
        self.assertEqual(after_restart["snapshot"], created["snapshot"])

    def test_checkpoint_uses_cas_and_identity_is_immutable(self):
        created = self.store.init(
            root_task_id="root",
            task_id="attempt-a",
            goal_verbatim="Не потерять исходную цель",
        )
        updated = self.store.checkpoint(
            task_id="attempt-a",
            expected_revision=created["revision"],
            idempotency_key="checkpoint-1",
            actor="ceraxia",
            patch={"state": "working", "current_strategy": "Проверить живой путь"},
        )
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["snapshot"]["state"], "working")

        with self.assertRaises(task_page.TaskPageConflict) as stale:
            self.store.checkpoint(
                task_id="attempt-a",
                expected_revision=1,
                idempotency_key="checkpoint-stale",
                patch={"state": "wrong"},
            )
        self.assertEqual(stale.exception.details["current_revision"], 2)

        with self.assertRaises(task_page.TaskPageConflict):
            self.store.checkpoint(
                task_id="attempt-a",
                expected_revision=2,
                idempotency_key="rewrite-goal",
                patch={"goal_verbatim": "Другая цель"},
            )
        self.assertEqual(self.store.get(task_id="attempt-a")["snapshot"]["goal_verbatim"], "Не потерять исходную цель")

    def test_event_is_idempotent_and_keeps_full_event_log(self):
        created = self.store.init(
            root_task_id="root",
            task_id="attempt-a",
            goal_verbatim="Довести задачу",
        )
        first = self.store.event(
            task_id="attempt-a",
            expected_revision=created["revision"],
            idempotency_key="worker-event-7",
            actor="skitarii",
            kind="test_finished",
            payload={"summary": "Тест завершён", "exit_code": 0},
            patch={"completed_work": ["targeted test"]},
        )
        replay = self.store.event(
            task_id="attempt-a",
            expected_revision=created["revision"],
            idempotency_key="worker-event-7",
            actor="skitarii",
            kind="test_finished",
            payload={"summary": "Тест завершён", "exit_code": 0},
            patch={"completed_work": ["targeted test"]},
        )
        self.assertEqual(first["revision"], 2)
        self.assertEqual(replay["revision"], 2)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual([item["kind"] for item in self.store.events(task_id="attempt-a")], ["init", "test_finished"])

        with self.assertRaises(task_page.TaskPageConflict):
            self.store.event(
                task_id="attempt-a",
                expected_revision=2,
                idempotency_key="worker-event-7",
                actor="skitarii",
                kind="test_finished",
                payload={"summary": "Другой результат", "exit_code": 1},
            )

    def test_legacy_body_and_note_return_the_new_envelope(self):
        body = task_page.handle_task_page_post(
            {"task_id": "legacy-run", "body": "Старая страница"},
            store=self.store,
        )
        self.assert_document(body)
        self.assertIn("Старая страница", body["content"])

        noted = task_page.handle_task_page_post(
            {
                "task_id": "legacy-run",
                "note": "Найден живой блокер",
                "idempotency_key": "legacy-note-1",
            },
            store=self.store,
        )
        replay = task_page.handle_task_page_post(
            {
                "task_id": "legacy-run",
                "note": "Найден живой блокер",
                "idempotency_key": "legacy-note-1",
            },
            store=self.store,
        )
        self.assert_document(noted)
        self.assertEqual(replay["revision"], noted["revision"])
        self.assertEqual(len(noted["snapshot"]["journal"]), 1)
        self.assertIn("Найден живой блокер", task_page.read_task_page("legacy-run", store=self.store))

    def test_bounds_unknown_fields_and_alias_collisions_are_rejected(self):
        created = self.store.init(
            root_task_id="one",
            goal_verbatim="Первая задача",
            aliases=["shared-attempt"],
        )
        with self.assertRaises(task_page.TaskPageValidationError):
            self.store.checkpoint(
                task_memory_id=created["task_memory_id"],
                expected_revision=1,
                idempotency_key="unknown-field",
                patch={"unbounded_blob": "no"},
            )
        with self.assertRaises(task_page.TaskPageValidationError):
            self.store.event(
                task_memory_id=created["task_memory_id"],
                expected_revision=1,
                idempotency_key="huge-event",
                kind="huge",
                payload={"summary": "x" * (task_page.TASK_MEMORY_MAX_TEXT_CHARS + 1)},
            )
        with self.assertRaises(task_page.TaskPageConflict):
            self.store.init(
                root_task_id="two",
                goal_verbatim="Вторая задача",
                aliases=["shared-attempt"],
            )
        protected = self.store.init(
            root_task_id="protected-root",
            task_memory_id="protected-memory-id",
            goal_verbatim="Страница с явным стабильным ID",
        )
        self.assertEqual(protected["task_memory_id"], "protected-memory-id")
        with self.assertRaises(task_page.TaskPageConflict):
            self.store.checkpoint(
                task_memory_id=created["task_memory_id"],
                expected_revision=created["revision"],
                idempotency_key="alias-memory-collision",
                patch={"aliases": ["protected-memory-id"]},
            )
        self.store.init(
            root_task_id="alias-owner",
            goal_verbatim="Сначала это алиас",
            aliases=["future-memory-id"],
        )
        with self.assertRaises(task_page.TaskPageConflict):
            self.store.init(
                root_task_id="future-root",
                task_memory_id="future-memory-id",
                goal_verbatim="Нельзя затенить старый алиас",
            )

    def test_both_references_must_resolve_to_the_same_page(self):
        first = self.store.init(
            root_task_id="first-root",
            task_id="first-run",
            task_memory_id="first-memory",
            goal_verbatim="Первая",
        )
        self.store.init(
            root_task_id="second-root",
            task_id="second-run",
            task_memory_id="second-memory",
            goal_verbatim="Вторая",
        )
        with self.assertRaises(task_page.TaskPageConflict):
            self.store.checkpoint(
                task_memory_id=first["task_memory_id"],
                task_id="second-run",
                expected_revision=first["revision"],
                idempotency_key="cross-page-write",
                patch={"state": "wrong-page"},
            )
        self.assertEqual(
            self.store.get(task_memory_id="first-memory")["snapshot"]["state"],
            "created",
        )

    def test_alias_table_keeps_more_attempts_than_snapshot_projection(self):
        created = self.store.init(
            root_task_id="long-goal",
            task_id="attempt-000",
            goal_verbatim="Очень долгая задача",
        )
        for number in range(1, task_page.TASK_MEMORY_MAX_LIST_ITEMS + 5):
            self.store.init(
                root_task_id="long-goal",
                task_id=f"attempt-{number:03d}",
                task_memory_id=created["task_memory_id"],
                goal_verbatim="Очень долгая задача",
            )
        final = self.store.get(task_memory_id=created["task_memory_id"])
        self.assertLessEqual(
            len(final["snapshot"]["aliases"]),
            task_page.TASK_MEMORY_MAX_LIST_ITEMS,
        )
        self.assertEqual(
            self.store.get(task_id="attempt-001")["task_memory_id"],
            created["task_memory_id"],
        )
        self.assertEqual(
            self.store.get(task_id=f"attempt-{task_page.TASK_MEMORY_MAX_LIST_ITEMS + 4:03d}")["task_memory_id"],
            created["task_memory_id"],
        )

    def test_concurrent_cas_allows_only_one_writer(self):
        created = self.store.init(root_task_id="race", goal_verbatim="Одна истина")
        barrier = threading.Barrier(3)
        results = []
        result_lock = threading.Lock()

        def writer(number):
            barrier.wait()
            try:
                self.store.checkpoint(
                    task_memory_id=created["task_memory_id"],
                    expected_revision=1,
                    idempotency_key=f"race-{number}",
                    patch={"state": f"writer-{number}"},
                )
                result = "ok"
            except task_page.TaskPageConflict:
                result = "conflict"
            with result_lock:
                results.append(result)

        threads = [threading.Thread(target=writer, args=(number,)) for number in (1, 2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(sorted(results), ["conflict", "ok"])
        self.assertEqual(self.store.get(task_memory_id=created["task_memory_id"])["revision"], 2)


if __name__ == "__main__":
    unittest.main()
