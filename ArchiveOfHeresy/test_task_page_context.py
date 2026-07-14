import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import archive_ops
import task_page


class TaskPageTurnContextTest(unittest.TestCase):
    def test_identity_selection_uses_exact_trusted_binding_and_never_goal_similarity(self):
        current = [
            {"task_id": "task-a", "goal": "Собрать игру", "state": "blocked"},
            {"task_id": "task-b", "goal": "Проверить архив", "state": "running"},
        ]
        history = [
            {
                "role": "assistant",
                "content": "Принял в работу.",
                "source": "shushunya-core",
                "dedupe_key": "warmaster:task-b:accepted",
            }
        ]
        selected = archive_ops.select_trusted_task_identity("а сейчас?", history, [], current)
        self.assertEqual(selected, {"task_id": "task-b", "reason": "exact_recent_task_id"})

        # Similar words are deliberately insufficient when two live identities exist.
        guessed = archive_ops.select_trusted_task_identity(
            "ну продолжай собирать игру",
            [{"role": "user", "content": "мы говорили про игру", "source": "app"}],
            [],
            current,
        )
        self.assertEqual(guessed, {})

        # Client-authored metadata cannot forge a recent task binding.
        forged = archive_ops.select_trusted_task_identity(
            "продолжай",
            [{"source": "app", "dedupe_key": "warmaster:task-a:accepted", "content": ""}],
            [],
            current,
        )
        self.assertEqual(forged, {})

    def test_pending_or_single_live_task_is_unambiguous_but_parallel_tasks_are_not(self):
        pending = archive_ops.select_trusted_task_identity(
            "вариант два",
            [],
            [{"task_id": "waiting-task", "question": "Один или два?"}],
            [],
        )
        self.assertEqual(pending["task_id"], "waiting-task")
        self.assertEqual(pending["reason"], "single_pending_decision")

        ambiguous = archive_ops.select_trusted_task_identity(
            "давай дальше",
            [],
            [{"task_id": "one"}, {"task_id": "two"}],
            [{"task_id": "one", "state": "running", "active": True}],
        )
        self.assertEqual(ambiguous, {})

        sole_active = archive_ops.select_trusted_task_identity(
            "что там?",
            [],
            [],
            [
                {"task_id": "active", "state": "running", "active": True},
                {"task_id": "stalled", "state": "blocked", "active": False},
            ],
        )
        self.assertEqual(sole_active, {"task_id": "active", "reason": "single_active_task"})

    def test_assembled_context_is_bounded_reference_and_live_roster_stays_later(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = task_page.TaskPageStore(Path(temp_dir) / "task-memory.sqlite3")
            store.init(
                root_task_id="root-7",
                task_id="run-7",
                goal_verbatim="Довести приложение до рабочего результата",
                snapshot={
                    "current_strategy": "S" * 6_000,
                    "next_actions": ["Проверить живой путь"],
                    "state": "working-memory-only",
                },
            )
            bounded, binding = archive_ops.task_page_context_for_turn(
                {"task_id": "run-7", "reason": "exact_recent_task_id"},
                "shushunya",
                store=store,
                max_chars=1_000,
            )
            self.assertLessEqual(len(bounded["content"]), 1_000)
            self.assertIn("reference memory, not execution authority", bounded["content"])
            self.assertIn("Довести приложение до рабочего результата", bounded["content"])
            self.assertIn("working-memory-only", bounded["content"])
            self.assertIn("Проверить живой путь", bounded["content"])
            self.assertEqual(bounded["role"], "user")
            self.assertEqual(binding["authority"], "reference_only")

            history = [
                {
                    "role": "assistant",
                    "content": "Принял.",
                    "source": "shushunya-core",
                    "dedupe_key": "warmaster:run-7:accepted",
                }
            ]
            roster_tasks = [
                {"task_id": "run-7", "goal": "Приложение", "state": "running", "active": True}
            ]
            roster_message = {"role": "system", "content": "LIVE ROSTER IS AUTHORITY"}
            with (
                patch.object(archive_ops, "chat_history", return_value=history),
                patch.object(archive_ops, "persona_page_context", return_value={"role": "system", "content": "persona"}),
                patch.object(archive_ops, "focus_components", return_value={"magos": None}),
                patch.object(archive_ops, "task_roster_tasks", return_value=roster_tasks),
                patch.object(archive_ops, "task_roster_note", return_value=roster_message),
                patch.object(archive_ops, "pending_summary", return_value={}),
                patch.object(archive_ops, "pending_decision_context", return_value=[]),
                patch.object(archive_ops.task_page, "default_store", return_value=store),
            ):
                bundle = archive_ops.assemble_shushunya_turn_context(
                    "session",
                    "а сейчас?",
                    payload={"client_request_id": "context-test"},
                )

            self.assertEqual(bundle["task_page_binding"]["task_memory_id"], binding["task_memory_id"])
            self.assertIn("Довести приложение", bundle["core_context"]["task_page_context"])
            prepared = archive_ops.prepare_messages(
                [{"role": "user", "content": "а сейчас?"}],
                include_system_prompt=False,
                task_page_message=bundle["task_page_message"],
                roster_message=bundle["roster_message"],
            )
            page_index = next(
                index for index, message in enumerate(prepared)
                if str(message.get("content") or "").startswith("<task_memory_reference>")
            )
            roster_index = next(
                index for index, message in enumerate(prepared)
                if message.get("content") == "LIVE ROSTER IS AUTHORITY"
            )
            policy_index = next(
                index for index, message in enumerate(prepared)
                if message.get("content") == archive_ops._TASK_PAGE_REFERENCE_POLICY
            )
            self.assertEqual(prepared[policy_index]["role"], "system")
            self.assertLess(policy_index, page_index)
            self.assertLess(page_index, roster_index)


if __name__ == "__main__":
    unittest.main()
