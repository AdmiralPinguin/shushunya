"""Focused tests for the fighter's durable context controller (no live LLM/VM)."""
from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness


def _settings(*, compact_at: int = 50) -> dict:
    return {
        "base_url": "http://llm.invalid/v1",
        "model": "fake",
        "timeout_sec": 1,
        "max_tokens": 100,
        "context_window": 200,
        "compact_at_tokens": compact_at,
        "checkpoint_max_tokens": 80,
    }


def _tool_call(name: str, arguments: dict, call_id: str = "call-1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


class HarnessContextTests(unittest.TestCase):
    def test_background_command_flushes_workspace_wal(self) -> None:
        replies = [
            {"choices": [{"message": {
                "content": "",
                "tool_calls": [_tool_call(
                    "bash_background", {"command": "python worker.py"}, "bg-1",
                )],
            }}]},
            {"choices": [{"message": {
                "content": "",
                "tool_calls": [_tool_call(
                    "done", {"summary": "ready", "artifacts": []}, "done-bg",
                )],
            }}]},
        ]
        wal = Mock()
        with (
            patch.object(
                harness, "_llm_settings", return_value=_settings(compact_at=10_000),
            ),
            patch.object(harness, "_chat", side_effect=replies),
            patch.object(harness, "_dispatch_tool", return_value='{"pid": 7}'),
        ):
            result = harness.run_fighter(
                "run worker", [], object(), task_id="attempt-bg-wal", max_steps=2,
                durable_checkpoint_fn=wal,
            )

        self.assertTrue(result["ok"])
        self.assertGreaterEqual(wal.call_count, 2)
        self.assertEqual(
            wal.call_args_list[0].kwargs["boundary"], "tool:bash_background",
        )

    def test_mutating_tool_and_handoff_flush_workspace_wal(self) -> None:
        replies = [
            {"choices": [{"message": {
                "content": "",
                "tool_calls": [_tool_call(
                    "write_file", {"path": "app.py", "content": "print(1)\n"},
                )],
            }}]},
            {"choices": [{"message": {
                "content": "",
                "tool_calls": [_tool_call(
                    "done", {"summary": "ready", "artifacts": ["app.py"]}, "done-1",
                )],
            }}]},
        ]
        wal = Mock()
        with (
            patch.object(
                harness, "_llm_settings", return_value=_settings(compact_at=10_000),
            ),
            patch.object(harness, "_chat", side_effect=replies),
            patch.object(harness, "_dispatch_tool", return_value="written"),
        ):
            result = harness.run_fighter(
                "write app", [], object(), task_id="attempt-wal", max_steps=2,
                durable_checkpoint_fn=wal,
            )

        self.assertTrue(result["ok"])
        self.assertGreaterEqual(wal.call_count, 2)
        self.assertEqual(wal.call_args_list[0].kwargs["boundary"], "tool:write_file")
        self.assertTrue(
            wal.call_args_list[-1].kwargs["boundary"].startswith("lifecycle:")
        )

    def test_run_id_never_falls_back_to_task_memory(self) -> None:
        reply = {"choices": [{"message": {
            "content": "",
            "tool_calls": [_tool_call("done", {"summary": "done", "artifacts": []})],
        }}]}
        with (
            patch.object(harness, "_llm_settings", return_value=_settings()),
            patch.object(harness, "_chat", return_value=reply),
            patch.object(harness, "_memory_read") as memory_read,
            patch.object(harness, "_memory_note") as memory_note,
            patch.object(harness, "_structured_memory_checkpoint") as structured,
        ):
            result = harness.run_fighter(
                "small task", [], object(), task_id="attempt-17", max_steps=1,
            )

        self.assertTrue(result["ok"])
        memory_read.assert_not_called()
        memory_note.assert_not_called()
        structured.assert_not_called()

    def test_memory_task_id_is_loaded_and_used_instead_of_run_id(self) -> None:
        old_checkpoint = {
            "version": 1,
            "current_state": "existing durable task state",
            "completed": ["old work"],
            "decisions": [],
            "files_changed": [],
            "checks": [],
            "failures": [],
            "next_actions": ["continue"],
        }
        page = "journal\n" + harness.CHECKPOINT_PREFIX + json.dumps(old_checkpoint)
        model_calls: list[tuple[list[dict], dict]] = []
        main_calls = 0

        def fake_chat(messages, settings):
            nonlocal main_calls
            model_calls.append((deepcopy(messages), dict(settings)))
            if settings.get("tools") == []:
                return {"choices": [{"message": {"content": json.dumps({
                    "current_state": "edited the implementation",
                    "completed": ["inspected target"],
                    "decisions": ["keep the existing API"],
                    "files_changed": ["app.py: implementation"],
                    "checks": ["python app.py: passed"],
                    "failures": [],
                    "next_actions": ["finish integration"],
                })}}]}
            main_calls += 1
            if main_calls == 1:
                return {
                    "choices": [{"message": {
                        "content": "",
                        "tool_calls": [_tool_call("bash", {"command": "true"})],
                    }}],
                    "usage": {"total_tokens": 90},
                }
            return {"choices": [{"message": {
                "content": "",
                "tool_calls": [_tool_call("done", {
                    "summary": "finished", "artifacts": ["app.py"],
                }, "done-1")],
            }}]}

        memory_read = Mock(return_value=page)
        memory_note = Mock(return_value="noted")
        structured_checkpoint = Mock(return_value={"revision": 3})
        with (
            patch.object(harness, "_llm_settings", return_value=_settings()),
            patch.object(harness, "_chat", side_effect=fake_chat),
            patch.object(harness, "_memory_read", memory_read),
            patch.object(harness, "_memory_note", memory_note),
            patch.object(
                harness, "_structured_memory_checkpoint", structured_checkpoint,
            ),
            patch.object(harness, "_dispatch_tool", return_value="ok") as dispatch,
        ):
            result = harness.run_fighter(
                "change app", [], object(), task_id="run-attempt-7",
                memory_task_id="commitment-galaga", max_steps=4,
            )

        self.assertTrue(result["ok"])
        memory_read.assert_called_once_with("commitment-galaga")
        memory_note.assert_not_called()
        self.assertGreaterEqual(structured_checkpoint.call_count, 2)
        self.assertTrue(all(
            call.args[0] == "commitment-galaga"
            for call in structured_checkpoint.call_args_list
        ))
        self.assertEqual(dispatch.call_args.kwargs["task_id"], "run-attempt-7")
        self.assertEqual(dispatch.call_args.kwargs["memory_task_id"], "commitment-galaga")

        first_main = model_calls[0][0]
        self.assertEqual([m["role"] for m in first_main], ["system", "user", "user"])
        self.assertIn("existing durable task state", first_main[-1]["content"])

        second_main = model_calls[-1][0]
        self.assertEqual([m["role"] for m in second_main], ["system", "user", "user"])
        self.assertIn("edited the implementation", second_main[-1]["content"])
        self.assertNotIn("tool_call_id", json.dumps(second_main))
        self.assertTrue(any(e.get("event") == "context_compacted" for e in result["transcript"]))

    def test_context_overflow_recovers_from_controller_checkpoint(self) -> None:
        calls = 0
        seen: list[tuple[list[dict], dict]] = []

        def fake_chat(messages, settings):
            nonlocal calls
            calls += 1
            seen.append((deepcopy(messages), dict(settings)))
            if calls == 1:
                raise harness.LLMRequestError(
                    status=400,
                    body="request exceeds the available context size",
                    retryable=True,
                    context_overflow=True,
                )
            if settings.get("tools") == []:
                return {"choices": [{"message": {"content": json.dumps({
                    "current_state": "resume after context reset",
                    "completed": [],
                    "decisions": [],
                    "files_changed": [],
                    "checks": [],
                    "failures": ["backend rejected the oversized request"],
                    "next_actions": ["inspect the unchanged workspace"],
                })}}]}
            return {"choices": [{"message": {
                "content": "",
                "tool_calls": [_tool_call("done", {
                    "summary": "resumed", "artifacts": [],
                }, "done")],
            }}]}

        memory_note = Mock(return_value="noted")
        structured_checkpoint = Mock(return_value={"revision": 2})
        with (
            patch.object(harness, "_llm_settings", return_value=_settings(compact_at=10_000)),
            patch.object(harness, "_chat", side_effect=fake_chat),
            patch.object(harness, "_memory_read", return_value=""),
            patch.object(harness, "_memory_note", memory_note),
            patch.object(
                harness, "_structured_memory_checkpoint", structured_checkpoint,
            ),
        ):
            result = harness.run_fighter(
                "resume safely", [], object(), task_id="run-1",
                memory_task_id="durable-1", max_steps=3,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(calls, 3)
        self.assertEqual(seen[1][1].get("tools"), [])
        self.assertIn("BOUNDED SESSION SNAPSHOT", seen[1][0][1]["content"])
        memory_note.assert_not_called()
        self.assertGreaterEqual(structured_checkpoint.call_count, 2)
        self.assertIn(
            "resume after context reset",
            structured_checkpoint.call_args_list[0].args[1]["current_state"],
        )

    def test_http_error_body_is_preserved_and_context_overflow_is_typed(self) -> None:
        body = b'{"error":{"message":"request (33180 tokens) exceeds the available context size (32768 tokens)"}}'
        error = urllib.error.HTTPError(
            "http://llm.invalid/v1/chat/completions", 400, "Bad Request", {}, io.BytesIO(body),
        )
        with patch.object(harness.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(harness.LLMRequestError) as caught:
                harness._chat([{"role": "user", "content": "x"}], _settings())

        exc = caught.exception
        self.assertEqual(exc.status, 400)
        self.assertEqual(exc.body, body.decode())
        self.assertTrue(exc.context_overflow)
        self.assertTrue(exc.retryable)

    def test_non_context_bad_request_is_not_retryable_but_503_is(self) -> None:
        for status, retryable in ((400, False), (503, True)):
            with self.subTest(status=status):
                error = urllib.error.HTTPError(
                    "http://llm.invalid/v1/chat/completions", status, "error", {},
                    io.BytesIO(b'{"error":"invalid request"}'),
                )
                with patch.object(harness.urllib.request, "urlopen", side_effect=error):
                    with self.assertRaises(harness.LLMRequestError) as caught:
                        harness._chat([{"role": "user", "content": "x"}], _settings())
                self.assertFalse(caught.exception.context_overflow)
                self.assertEqual(caught.exception.retryable, retryable)

    def test_structured_checkpoint_retries_one_cas_conflict(self) -> None:
        conflict = urllib.error.HTTPError(
            "http://archive.invalid/archive/task-page/checkpoint",
            409,
            "Conflict",
            {},
            io.BytesIO(b'{"code":"task_page_conflict"}'),
        )
        post = Mock(side_effect=[conflict, {"revision": 6}])
        checkpoint = harness._normalize_checkpoint({
            "current_state": "editing parser",
            "decisions": ["keep public API"],
            "completed_work": ["read parser.py"],
            "failed_approaches": ["regex was ambiguous"],
            "working_set": ["parser.py"],
            "checks": ["python -m unittest: passed"],
            "next_actions": ["finish edge case"],
        }, "fallback")
        with (
            patch.object(
                harness, "_task_page_document",
                side_effect=[
                    {"revision": 4, "snapshot": {
                        "decisions": ["sibling A"],
                        "completed_work": ["sibling file A"],
                        "working_set": ["sibling-a.py"],
                        "journal": [],
                    }},
                    {"revision": 5, "snapshot": {
                        "decisions": ["sibling A", "sibling B"],
                        "completed_work": ["sibling file A", "sibling file B"],
                        "working_set": ["sibling-a.py", "sibling-b.py"],
                        "journal": [],
                    }},
                ],
            ),
            patch.object(harness, "_post_task_page", post),
        ):
            result = harness._structured_memory_checkpoint(
                "stable-task", checkpoint, idempotency_key="checkpoint-key",
            )

        self.assertEqual(result["revision"], 6)
        self.assertEqual(post.call_count, 2)
        first, second = (call.args[0] for call in post.call_args_list)
        self.assertEqual((first["expected_revision"], second["expected_revision"]), (4, 5))
        self.assertNotEqual(first["idempotency_key"], second["idempotency_key"])
        self.assertTrue(first["idempotency_key"].startswith("checkpoint-key-"))
        self.assertTrue(second["idempotency_key"].startswith("checkpoint-key-"))
        self.assertEqual(first["task_memory_id"], "stable-task")
        self.assertNotIn("state", first["patch"])
        self.assertNotIn("next_actions", first["patch"])
        self.assertNotIn("decisions", second["patch"])
        self.assertNotIn("completed_work", second["patch"])
        self.assertEqual(
            second["patch"]["journal"][-1]["kind"],
            "unverified_fighter_context",
        )
        self.assertEqual(
            second["patch"]["journal"][-1]["claimed_completed_work"],
            ["read parser.py"],
        )
        self.assertEqual(
            second["patch"]["journal"][-1]["decisions"],
            ["keep public API"],
        )
        self.assertEqual(
            first["patch"]["journal"][-1]["checks"],
            ["python -m unittest: passed"],
        )

    def test_lost_checkpoint_ack_replays_identical_idempotent_payload(self) -> None:
        checkpoint = harness._normalize_checkpoint({
            "current_state": "working",
            "completed_work": ["changed app.py"],
            "checks": ["python app.py: passed"],
        }, "fallback")
        lost_ack = urllib.error.URLError("connection closed after commit")
        post = Mock(side_effect=[lost_ack, {
            "revision": 8,
            "idempotent_replay": True,
        }])
        with (
            patch.object(
                harness, "_task_page_document",
                side_effect=[
                    {"revision": 7, "snapshot": {"journal": []}},
                    {"revision": 8, "snapshot": {
                        "completed_work": ["changed app.py"],
                        "journal": [{
                            "actor": "SkitariiContextController",
                            "kind": "context_checks",
                            "checks": ["python app.py: passed"],
                        }],
                    }},
                ],
            ),
            patch.object(harness, "_post_task_page", post),
        ):
            result = harness._structured_memory_checkpoint(
                "stable-task", checkpoint, idempotency_key="lost-ack-key",
            )

        self.assertTrue(result["idempotent_replay"])
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0].args[0], post.call_args_list[1].args[0])

    def test_missing_structured_page_is_explicit_and_never_creates_legacy_memory(self) -> None:
        checkpoint = harness._normalize_checkpoint(
            {"current_state": "resume later"}, "fallback",
        )
        with (
            patch.object(
                harness, "_structured_memory_checkpoint",
                side_effect=OSError("archive version is old"),
            ),
            patch.object(harness, "_memory_note", return_value="noted") as note,
        ):
            with self.assertRaisesRegex(OSError, "archive version is old"):
                harness._persist_checkpoint("stable-task", checkpoint)

        note.assert_not_called()

    def test_archive_key_is_sent_on_task_page_get_and_post(self) -> None:
        response = Mock()
        response.read.return_value = json.dumps({
            "task_memory_id": "stable-task",
            "revision": 1,
            "snapshot": {},
        }).encode("utf-8")
        with (
            patch.dict(
                harness.os.environ,
                {"SKITARII_ARCHIVE_API_KEY": "archive-secret"},
                clear=False,
            ),
            patch.object(harness.urllib.request, "urlopen", return_value=response) as open_url,
        ):
            harness._task_page_document("stable-task")
            harness._post_task_page({"action": "checkpoint"})

        self.assertEqual(open_url.call_count, 2)
        for call in open_url.call_args_list:
            request = call.args[0]
            self.assertEqual(
                request.get_header("Authorization"), "Bearer archive-secret",
            )

        with patch.dict(
            harness.os.environ,
            {"SKITARII_ARCHIVE_API_KEY": "bad\r\nInjected: value"},
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "line break"):
                harness._archive_headers()

    def test_memory_note_requires_existing_structured_page(self) -> None:
        with (
            patch.object(
                harness, "_task_page_document",
                return_value={"revision": 0, "task_memory_id": None},
            ),
            patch.object(harness, "_post_task_page") as post,
        ):
            with self.assertRaisesRegex(RuntimeError, "not initialized"):
                harness._memory_note("stable-task", "progress")

        post.assert_not_called()

    def test_memory_read_prioritizes_structured_snapshot_over_markdown_tail(self) -> None:
        document = {
            "task_memory_id": "stable-task",
            "root_task_id": "root-task",
            "content": "obsolete journal " + ("x" * 50_000),
            "snapshot": {
                "goal_verbatim": "build the durable feature",
                "desired_outcome": "feature works after task switching",
                "state": "implementation is half complete",
                "decisions": ["keep the stable task identity"],
                "completed_work": ["added checkpoint transport"],
                "failed_approaches": ["attempt id as memory key"],
                "working_set": ["service.py", "harness.py"],
                "next_actions": ["finish service finalization"],
            },
        }
        with patch.object(harness, "_task_page_document", return_value=document):
            rendered = harness._memory_read("stable-task")

        self.assertLessEqual(len(rendered), harness.MAX_WIKI_CONTEXT_CHARS)
        self.assertIn("implementation is half complete", rendered)
        self.assertIn("keep the stable task identity", rendered)
        self.assertIn("finish service finalization", rendered)
        self.assertNotIn("obsolete journal", rendered)

    def test_memory_read_prefers_archive_canonical_context(self) -> None:
        document = {
            "context": "Canonical compact task context",
            "content": "obsolete markdown tail",
            "snapshot": {"state": "duplicate local rendering"},
        }
        with patch.object(harness, "_task_page_document", return_value=document):
            rendered = harness._memory_read("stable-task")

        self.assertEqual(rendered, "Canonical compact task context")

    def test_only_authoritative_checkpoint_replaces_state_and_next_actions(self) -> None:
        snapshot = {
            "state": "leader state",
            "next_actions": ["leader action"],
            "decisions": ["leader decision"],
        }
        fighter = harness._normalize_checkpoint({
            "current_state": "fighter local state",
            "decisions": ["fighter evidence"],
            "next_actions": ["fighter local action"],
        }, "fighter")
        contribution = harness._checkpoint_patch(fighter, snapshot)
        authoritative = harness._checkpoint_patch(
            fighter, snapshot, authoritative=True,
        )

        self.assertNotIn("state", contribution)
        self.assertNotIn("next_actions", contribution)
        self.assertNotIn("completed_work", contribution)
        self.assertNotIn("failed_approaches", contribution)
        self.assertNotIn("decisions", contribution)
        self.assertEqual(
            contribution["journal"][-1]["decisions"],
            ["fighter evidence"],
        )
        self.assertEqual(authoritative["state"], "fighter local state")
        self.assertEqual(authoritative["next_actions"], ["fighter local action"])
        self.assertEqual(
            authoritative["decisions"],
            ["leader decision", "fighter evidence"],
        )

    def test_persist_checkpoint_uses_deterministic_logical_key(self) -> None:
        checkpoint = harness._normalize_checkpoint({
            "current_state": "same state",
            "working_set": ["app.py"],
        }, "fallback")
        with patch.object(
            harness, "_structured_memory_checkpoint", return_value={},
        ) as write:
            harness._persist_checkpoint("stable-task", checkpoint)
            harness._persist_checkpoint("stable-task", checkpoint)

        first = write.call_args_list[0].kwargs["idempotency_key"]
        second = write.call_args_list[1].kwargs["idempotency_key"]
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("skitarii-context-"))

    def test_short_done_path_writes_lifecycle_checkpoint(self) -> None:
        reply = {"choices": [{"message": {
            "content": "",
            "tool_calls": [_tool_call("done", {
                "summary": "short mission complete", "artifacts": [],
            })],
        }}]}
        with (
            patch.object(harness, "_llm_settings", return_value=_settings(compact_at=10_000)),
            patch.object(harness, "_chat", return_value=reply),
            patch.object(harness, "_memory_read", return_value=""),
            patch.object(harness, "_structured_memory_checkpoint", return_value={}) as write,
        ):
            result = harness.run_fighter(
                "small task", [], object(), task_id="attempt-short",
                memory_task_id="stable-short", max_steps=1,
            )

        self.assertTrue(result["ok"])
        write.assert_called_once()
        self.assertEqual(write.call_args.args[0], "stable-short")
        self.assertIn(
            "handed its candidate",
            write.call_args.args[1]["current_state"],
        )
        self.assertNotIn(
            "short mission complete",
            write.call_args.args[1]["completed_work"],
        )
        self.assertIn(
            "Unverified fighter handoff: short mission complete",
            write.call_args.args[1]["decisions"],
        )
        self.assertTrue(any(
            event.get("event") == "lifecycle_checkpoint"
            for event in result["transcript"]
        ))

    def test_transcript_has_hard_entry_and_byte_bounds(self) -> None:
        transcript: list[dict] = []
        with (
            patch.object(harness, "MAX_TRANSCRIPT_ENTRIES", 3),
            patch.object(harness, "MAX_TRANSCRIPT_BYTES", 900),
        ):
            for step in range(20):
                harness._append_transcript(transcript, {
                    "step": step,
                    "tool": "bash",
                    "args": {"nested": "x" * 5_000},
                    "result": "y" * 5_000,
                })
            size = harness._transcript_size(transcript)

        self.assertLessEqual(len(transcript), 3)
        self.assertLessEqual(size, 900)


if __name__ == "__main__":
    unittest.main()
