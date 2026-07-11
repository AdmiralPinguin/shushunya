"""Focused tests for the Skitarii warband — no LLM, no VM. They pin the safety
invariants the review flagged: no false success, path preservation, spec fallback,
patch/greenfield classification, oracle acceptance, budget honesty.

Run:  python3 -m unittest EyeOfTerror.Mechanicum.Skitarii.test_skitarii  (from repo root)
   or  python3 test_skitarii.py                                          (from this dir)
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from executor import LocalExecutor
from acceptor import accept, run_check
from spec import build_spec


def _ex() -> LocalExecutor:
    return LocalExecutor(Path(tempfile.mkdtemp()))


class TestNoFalseSuccess(unittest.TestCase):
    def test_empty_checks_never_accepted(self):
        # the exact review bug: nothing proven must never be accepted
        v = accept(_ex(), [], [])
        self.assertFalse(v["accepted"])
        self.assertIn("reason", v)

    def test_deliverable_alone_is_not_enough(self):
        ex = _ex()
        ex.write_file("x.py", "print(1)")
        # a deliverable exists but there are no checks -> still not accepted
        v = accept(ex, ["x.py"], [])
        self.assertFalse(v["accepted"])

    def test_behavioural_check_is_accepted(self):
        ex = _ex()
        ex.write_file("x.py", "print('hi')")
        v = accept(ex, ["x.py"], [{"cmd": "python3 x.py", "expect_stdout": "hi"}])
        self.assertTrue(v["accepted"])

    def test_failing_check_is_rejected(self):
        ex = _ex()
        ex.write_file("x.py", "def broken(:")  # syntax error
        v = accept(ex, ["x.py"], [{"cmd": "python3 -m py_compile x.py"}])
        self.assertFalse(v["accepted"])

    def test_compile_only_is_blocked(self):
        # even if it PASSES, a syntax/compile-only check set must not be accepted —
        # wrong logic would slip through. This is the review's key gap, now structural.
        ex = _ex()
        ex.write_file("x.py", "def add(a,b): return a-b")  # compiles, logic wrong
        v = accept(ex, ["x.py"], [{"cmd": "python3 -m py_compile x.py"}])
        self.assertFalse(v["accepted"])
        self.assertIn("behavioural", v.get("reason", ""))


class TestOracleAndExpect(unittest.TestCase):
    def test_oracle_comparison(self):
        ex = _ex()
        ex.write_file("calc.py", "import sys; print(int(sys.argv[1]) * 2)")
        r = run_check(ex, {"cmd": "python3 calc.py 7", "oracle": "python3 -c 'print(7*2)'"})
        self.assertTrue(r["ok"])

    def test_expect_stdout_mismatch(self):
        ex = _ex()
        ex.write_file("hi.py", "print('nope')")
        r = run_check(ex, {"cmd": "python3 hi.py", "expect_stdout": "hello"})
        self.assertFalse(r["ok"])


class TestSpecFallback(unittest.TestCase):
    def test_model_json_parser_uses_one_complete_object_and_ignores_trailing_output(self):
        import spec

        content = (
            "analysis before JSON\n```json\n"
            '{"checks":[{"cmd":"python3 app.py","expect_stdout":"OK"}]}\n'
            "```\n"
            '{"unrelated":"trailing object"}'
        )
        self.assertEqual(spec._first_json_object(content), {
            "checks": [{"cmd": "python3 app.py", "expect_stdout": "OK"}],
        })

    def test_synthesizes_syntax_check_when_model_gives_none(self):
        # real test: stub the LLM so it returns files but no checks, and assert the
        # fallback synthesizes a per-file syntax check (never an empty check set).
        import spec
        orig = spec._chat_json
        spec._chat_json = lambda prompt: {"deliverables": ["a.py", "b.php"], "checks": []}
        try:
            out = spec.build_spec("сделай a.py и b.php")
        finally:
            spec._chat_json = orig
        self.assertTrue(out["checks"], "fallback must not leave checks empty")
        cmds = " ".join(c["cmd"] for c in out["checks"])
        self.assertIn("py_compile", cmds)
        self.assertIn("php -l", cmds)

    def test_malformed_llm_output_yields_no_false_success(self):
        import spec
        orig = spec._chat_json
        spec._chat_json = lambda prompt: {}  # model gave nothing usable
        try:
            out = spec.build_spec("что-то")
        finally:
            spec._chat_json = orig
        # no deliverables + no checks -> acceptor (tested above) will reject: no false success
        self.assertEqual(out["checks"], [])
        self.assertEqual(out["deliverables"], [])

    def test_private_verifier_keeps_only_behavioural_checks(self):
        import spec
        orig = spec._held_out_chat_json
        spec._held_out_chat_json = lambda prompt: {
            "checks": [
                {"cmd": "python3 -m py_compile app.py"},
                {"cmd": "python3 app.py edge", "expect_stdout": "EDGE"},
            ]
        }
        try:
            checks = spec.build_held_out_checks("write app.py")
        finally:
            spec._held_out_chat_json = orig
        self.assertEqual(checks, [{"cmd": "/usr/bin/python3 app.py edge", "expect_stdout": "EDGE"}])

    def test_private_verifier_rejects_fake_runner_and_constant_self_check(self):
        import spec
        orig = spec._held_out_chat_json
        spec._held_out_chat_json = lambda prompt: {
            "checks": [
                {"cmd": "echo pytest"},
                {"cmd": "python3 -c 'print(7)'", "expect_stdout": "7"},
                {"cmd": "python3 -m unittest discover -s tests"},
                {"cmd": "npm test"},
                {"cmd": "npm test", "expect_stdout": ""},
                {"cmd": "npm test && echo OK", "expect_stdout": "OK"},
                {"cmd": "python3 app.py edge", "oracle": "npm test"},
                {"cmd": "python3 app.py edge", "oracle": "python3 app.py edge"},
                {"cmd": "python3 app.py edge", "oracle": "python app.py edge"},
                {"cmd": "python3 app.py edge", "oracle": "bash -c 'python3 app.py edge'"},
                {"cmd": "python3 -c 'import os; print(7)'", "expect_stdout": "7"},
                {"cmd": "python3 -c 'import pytest; print(7)'", "expect_stdout": "7"},
                {"cmd": "python3 -c 'import numpy; print(7)'", "expect_stdout": "7"},
                {"cmd": "python3 -c 'print(\"OK\") # app.py'", "expect_stdout": "OK"},
                {"cmd": "python3 -c 'print(\"OK\")' app.py", "expect_stdout": "OK"},
                {"cmd": "printf OK app.py", "expect_stdout": "OKapp.py"},
                {"cmd": "echo OK app.py", "expect_stdout": "OK app.py"},
                {"cmd": "nice pytest app.py", "expect_stdout": "OK"},
                {"cmd": "stdbuf -o0 pytest app.py", "expect_stdout": "OK"},
                {"cmd": "setsid pytest app.py", "expect_stdout": "OK"},
                {"cmd": "nohup pytest app.py", "expect_stdout": "OK"},
                {"cmd": "bash -c 'pytest app.py && echo OK'", "expect_stdout": "OK"},
                {"cmd": "env pytest app.py", "expect_stdout": "OK"},
                {"cmd": "timeout 10 pytest app.py", "expect_stdout": "OK"},
                {"cmd": "command pytest app.py", "expect_stdout": "OK"},
                {"cmd": "python3 -c 'import subprocess; subprocess.run([\"pytest\", \"app.py\"]); print(\"OK\")'", "expect_stdout": "OK"},
                {"cmd": "node app.js x;printf EDGE", "expect_stdout": "EDGE"},
                {"cmd": "php app.php x;printf EDGE", "expect_stdout": "EDGE"},
                {"cmd": "python3 app.py x>/dev/null;cat app.py", "oracle": "python3 -c app;cat<app.py"},
                {
                    "cmd": "python3 -c 'import app; print(app.edge())'",
                    "oracle": "python3 -c 'import json; json.__builtins__[\"exec\"](json.__builtins__[\"compile\"](json.__builtins__[\"open\"](\"app.py\").read(),\"app.py\",\"exec\"),globals()); print(edge())'",
                },
                {"cmd": "python3 -c 'from app import edge; edge=lambda:\"EDGE\"; print(edge())'", "expect_stdout": "EDGE"},
                {"cmd": "python3 -c 'import app as target; import json as target; print(target.dumps({\"ok\":1}))'", "expect_stdout": "{\"ok\": 1}"},
                {"cmd": "python3 -c 'import app; print(app.edge())'", "expect_stdout": "EDGE"},
                {"cmd": "python3 app.py edge", "oracle": "python3 -c 'print(\"EDGE\")'"},
                {"cmd": "python3 app.py edge", "expect_stdout": "EDGE"},
            ]
        }
        try:
            plan = spec.build_held_out_plan("fix app.py")
        finally:
            spec._held_out_chat_json = orig
        self.assertEqual(plan["status"], "ok")
        self.assertEqual(plan["checks"], [
            {"cmd": "/usr/bin/python3 -c 'import app; print(app.edge())'", "expect_stdout": "EDGE"},
            {"cmd": "/usr/bin/python3 app.py edge", "oracle": "/usr/bin/python3 -I -S -c 'print(\"EDGE\")'"},
            {"cmd": "/usr/bin/python3 app.py edge", "expect_stdout": "EDGE"},
        ])

    def test_private_verifier_positive_grammar_is_path_exact(self):
        import spec

        rejected = [
            ({"cmd": "./python3 app.py edge", "expect_stdout": "EDGE"}, "fix app.py"),
            ({"cmd": "python3 ../../app.py edge", "expect_stdout": "EDGE"}, "fix app.py"),
            ({"cmd": "python3 app.py edge", "expect_stdout": "EDGE"}, "fix App.py"),
            ({"cmd": "python3 -c 'import app; print(app.edge())'", "expect_stdout": "EDGE"}, "fix src/app.py"),
            ({"cmd": "python3 -c 'import src.app; print(src.edge())'", "expect_stdout": "EDGE"}, "fix src/app.py"),
        ]
        for check, goal in rejected:
            with self.subTest(check=check, goal=goal):
                self.assertEqual(
                    spec._structured_checks([check], allow_bare=False, goal=goal),
                    [],
                )
        accepted = spec._structured_checks(
            [
                {"cmd": "python3 app.py 'a|b'", "expect_stdout": "EDGE"},
                {"cmd": "python3 app.py x;printf EDGE", "expect_stdout": "EDGE"},
                {
                    "cmd": "python3 -c 'import src.app as target; print(target.edge())'",
                    "expect_stdout": "EDGE",
                },
                {
                    "cmd": "python3 -c 'import src.app; print(src.app.edge())'",
                    "expect_stdout": "EDGE",
                },
            ],
            allow_bare=False,
            goal="fix app.py and src/app.py",
        )
        self.assertEqual(accepted, [
            {"cmd": "/usr/bin/python3 app.py 'a|b'", "expect_stdout": "EDGE"},
            {"cmd": "/usr/bin/python3 app.py 'x;printf' EDGE", "expect_stdout": "EDGE"},
            {
                "cmd": "/usr/bin/python3 -c 'import src.app as target; print(target.edge())'",
                "expect_stdout": "EDGE",
            },
            {
                "cmd": "/usr/bin/python3 -c 'import src.app; print(src.app.edge())'",
                "expect_stdout": "EDGE",
            },
        ])

    def test_private_oracle_allows_pure_generators_but_no_effectful_comprehensions(self):
        import spec

        self.assertTrue(spec._private_oracle(
            "python3 -c 'print(\" \".join(str(i * 2) for i in range(1, 4)))'"
        ))
        for command in (
            "python3 -c 'print(\" \".join(open(str(i)).read() for i in range(3)))'",
            "python3 -c 'print([__import__(\"os\") for i in range(3)])'",
            "python3 -c 'values=[]; print([values.append(i) for i in range(3)])'",
        ):
            with self.subTest(command=command):
                self.assertFalse(spec._private_oracle(command))

    def test_private_verifier_preserves_generator_failure(self):
        import spec
        orig = spec._held_out_chat_json
        spec._held_out_chat_json = lambda prompt: (_ for _ in ()).throw(TimeoutError("offline"))
        try:
            plan = spec.build_held_out_plan("fix app.py")
        finally:
            spec._held_out_chat_json = orig
        self.assertEqual(plan["status"], "generator_unavailable")
        self.assertEqual(plan["checks"], [])

    def test_private_verifier_retries_one_invalid_model_reply(self):
        import spec

        replies = iter([
            {"checks": [{"cmd": "echo invalid", "expect_stdout": "invalid"}]},
            {"checks": [{"cmd": "python3 app.py edge", "expect_stdout": "EDGE"}]},
        ])
        with patch.object(spec, "_held_out_chat_json", side_effect=lambda prompt: next(replies)) as chat:
            plan = spec.build_held_out_plan("fix app.py")
        self.assertEqual(plan, {
            "status": "ok",
            "checks": [{"cmd": "/usr/bin/python3 app.py edge", "expect_stdout": "EDGE"}],
            "error": "",
        })
        self.assertEqual(chat.call_count, 2)
        self.assertIn("REPAIR:", chat.call_args_list[1].args[0])


class TestPathPreservation(unittest.TestCase):
    def test_subdir_paths_do_not_collapse(self):
        def norm(p: str) -> str:
            p = str(p).lstrip("/").replace("\\", "/")
            return "/".join(x for x in p.split("/") if x not in ("", ".", ".."))
        self.assertNotEqual(norm("src/index.py"), norm("tests/index.py"))
        self.assertEqual(norm("../../etc/passwd"), "etc/passwd")  # traversal stripped


class TestExecutorTools(unittest.TestCase):
    def test_read_file_offsets(self):
        ex = _ex()
        ex.write_file("f.txt", "l0\nl1\nl2\nl3\nl4")
        self.assertEqual(ex.read_file("f.txt", offset=1, limit=2), "l1\nl2")

    def test_edit_file_semantics(self):
        ex = _ex()
        ex.write_file("f.py", "x = 1\ny = 2\n")
        text = ex.read_file("f.py")
        self.assertEqual(text.count("x = 1"), 1)

    def test_bash_background_returns_pid(self):
        ex = _ex()
        try:
            info = ex.bash_background("sleep 1")
            self.assertTrue(info["started"])
            self.assertTrue(info["pid"])
        finally:
            ex.close()

class TestBridge(unittest.TestCase):
    """Real tests of the Warmaster→Skitarii bridge (not stubs)."""

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[3]  # repo root
        sys.path.insert(0, str(root))
        sys.path.insert(0, str(root / "EyeOfTerror" / "Warmaster"))
        from eye_of_terror import skitarii_bridge
        cls.b = skitarii_bridge

    def test_traversal_refused(self):
        self.assertIsNone(self.b._safe_repo_file("../../etc/passwd"))
        self.assertIsNone(self.b._safe_repo_file("/etc/passwd"))
        self.assertIsNone(self.b._safe_repo_file("EyeOfTerror/../../etc/passwd"))

    def test_named_repo_file_is_loaded_as_patch(self):
        f, is_patch = self.b._collect_workspace("исправь EyeOfTerror/Mechanicum/Skitarii/acceptor.py")
        self.assertTrue(is_patch)
        self.assertTrue(any(k.endswith("acceptor.py") for k in f))

    def test_greenfield_loads_nothing(self):
        f, is_patch = self.b._collect_workspace("напиши новый скрипт hello.py с нуля")
        self.assertFalse(is_patch)
        self.assertEqual(f, {})

    def test_patch_without_named_files_pulls_a_slice(self):
        # a modify request with no explicit path must still load real source (a slice),
        # never an empty set that would trigger a greenfield rewrite
        f, is_patch = self.b._collect_workspace("почини логику приёмщика acceptor в варбанде Skitarii")
        self.assertTrue(is_patch)
        self.assertGreater(len(f), 0)

    def test_full_snapshot_contains_all_visible_text_and_skips_ignored_files(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "src").mkdir()
        (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (root / "README.md").write_text("docs\n", encoding="utf-8")
        (root / ".gitignore").write_text("secret.env\n", encoding="utf-8")
        (root / "secret.env").write_text("TOKEN=nope\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "src/a.py", "README.md", ".gitignore"], check=True)
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            snapshot = self.b._full_repo_snapshot()
        finally:
            self.b.REPO_ROOT = original
        self.assertIn("src/a.py", snapshot)
        self.assertIn("README.md", snapshot)
        self.assertNotIn("secret.env", snapshot)
        self.assertEqual(snapshot.modes["src/a.py"], "100644")

    def test_full_snapshot_refuses_truncation(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "a.py").write_text("a = 1\n", encoding="utf-8")
        (root / "b.py").write_text("b = 2\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "a.py", "b.py"], check=True)
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            with self.assertRaises(self.b.SnapshotError):
                self.b._full_repo_snapshot(max_files=1)
        finally:
            self.b.REPO_ROOT = original

    def test_snapshot_preserves_binary_unknown_text_and_large_clean_asset_manifest(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "test"], check=True)
        (root / "small.bin").write_bytes(b"\x00\xff\x10")
        (root / "BUILD.custom").write_text("rule = exact\n", encoding="utf-8")
        (root / "model.onnx").write_bytes(b"M" * 128)
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            snapshot = self.b._full_repo_snapshot(max_file_bytes=64)
        finally:
            self.b.REPO_ROOT = original
        self.assertEqual(base64.b64decode(snapshot.blobs["small.bin"]), b"\x00\xff\x10")
        self.assertEqual(snapshot["BUILD.custom"], "rule = exact\n")
        self.assertEqual(snapshot.external_assets["model.onnx"]["size"], 128)
        self.assertIn("model.onnx", snapshot.inventory)

    def test_snapshot_refuses_dirty_large_asset_and_unmerged_index(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "test"], check=True)
        (root / "large.bin").write_bytes(b"A" * 128)
        (root / "conflict.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            (root / "large.bin").write_bytes(b"B" * 128)
            with self.assertRaises(self.b.SnapshotError):
                self.b._full_repo_snapshot(max_file_bytes=64)
            subprocess.run(["git", "-C", str(root), "checkout", "--", "large.bin"], check=True)
            blob1 = subprocess.run(
                ["git", "-C", str(root), "hash-object", "-w", "--stdin"],
                input="one\n", text=True, capture_output=True, check=True,
            ).stdout.strip()
            blob2 = subprocess.run(
                ["git", "-C", str(root), "hash-object", "-w", "--stdin"],
                input="two\n", text=True, capture_output=True, check=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "-C", str(root), "update-index", "--index-info"],
                input=f"100644 {blob1} 1\tconflict.txt\n100644 {blob2} 2\tconflict.txt\n",
                text=True, check=True,
            )
            with self.assertRaises(self.b.SnapshotError):
                self.b._full_repo_snapshot(max_file_bytes=64)
        finally:
            self.b.REPO_ROOT = original

    def test_snapshot_carries_staged_deletion(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "test"], check=True)
        (root / "gone.txt").write_text("gone\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "gone.txt"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
        subprocess.run(["git", "-C", str(root), "rm", "-q", "gone.txt"], check=True)
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            snapshot = self.b._full_repo_snapshot()
        finally:
            self.b.REPO_ROOT = original
        self.assertIn("gone.txt", snapshot.deleted_paths)

    def test_external_assets_are_virtual_in_disposable_snapshot(self):
        payload = b"large immutable asset"
        metadata = {
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "mode": "100644",
            "materialized": False,
        }
        snapshot = self.b.WorkspaceSnapshot(
            {}, external_assets={"asset.bin": metadata},
        )
        destination = Path(tempfile.mkdtemp())
        self.b._materialize_snapshot(snapshot, destination)
        self.assertFalse((destination / "asset.bin").exists())
        self.assertEqual(
            self.b._git_visible_content_fingerprint(
                destination, allowed_large=snapshot.external_assets,
            ),
            self.b._snapshot_content_fingerprint(snapshot),
        )

    def test_patch_stage_is_mandatory(self):
        self.assertFalse(self.b._patch_stage_passed(None))
        self.assertFalse(self.b._patch_stage_passed({"applies_to_live": True, "tests_pass_in_worktree": False}))
        self.assertTrue(self.b._patch_stage_passed({"applies_to_live": True, "tests_pass_in_worktree": True}))
        stage = {"applies_to_live": True, "tests_pass_in_worktree": True, "applied_to_live": False}
        self.assertFalse(self.b._patch_stage_passed(stage, require_applied=True))
        stage["applied_to_live"] = True
        stage["post_apply_tests_passed"] = True
        self.assertTrue(self.b._patch_stage_passed(stage, require_applied=True))

    def test_bridge_failure_schema_is_complete(self):
        failure = self.b._bridge_failure(
            Path(tempfile.mkdtemp()), "task-x", "unreachable", phase="skitarii_error",
            error="boom",
        )
        for key in (
            "status", "summary", "artifacts", "patch_stage", "ready_to_apply",
            "next_action", "artifact_root", "final_step", "needs_user",
        ):
            self.assertIn(key, failure)
        self.assertEqual(failure["status"], "blocked")
        self.assertEqual(failure["phase"], "skitarii_error")

    def test_service_response_content_length_is_bounded_before_read(self):
        class OversizedResponse:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, *_args):
                raise AssertionError("oversized response body must not be read")

        OversizedResponse.headers = {
            "Content-Length": str(self.b.MAX_SKITARII_RESPONSE_BYTES + 1),
        }
        with patch.object(self.b.urllib.request, "urlopen", return_value=OversizedResponse()):
            with self.assertRaisesRegex(RuntimeError, "byte limit"):
                self.b._skitarii_json_request("GET", "/missions/test")

    def test_bridge_sends_configured_service_bearer_without_logging_it(self):
        class Response:
            status = 200
            headers = {}

            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, *_args): return b"{}"

        seen = {}

        def open_request(request, **_kwargs):
            seen["authorization"] = request.get_header("Authorization")
            return Response()

        with (
            patch.dict(os.environ, {"SKITARII_BEARER_TOKEN": "test-secret-token"}),
            patch.object(self.b.urllib.request, "urlopen", side_effect=open_request),
        ):
            payload = self.b._skitarii_json_request("GET", "/health")
        self.assertEqual(payload, {})
        self.assertEqual(seen["authorization"], "Bearer test-secret-token")

    def test_async_bridge_forwards_cancellation_to_same_service_mission(self):
        from eye_of_terror.ledger import TaskLedger

        run_dir = Path(tempfile.mkdtemp())
        ledger = TaskLedger.create(run_dir / "task_ledger.json", "cancel-bridge", "fix", "Ceraxia")
        ledger.set_status("running")
        ledger.request_cancel("user requested")
        calls = []
        body_payload = {"goal": "fix", "task_id": "cancel-bridge"}
        request_sha = self.b._service_request_sha256(body_payload)
        service_created = False
        cancel_acknowledged = False

        def fake_request(method, path, **kwargs):
            nonlocal service_created, cancel_acknowledged
            calls.append((method, path))
            if method == "GET" and path == "/missions/wm-cancel-bridge-fixed" and not service_created:
                return {"_http_status": 404, "error": "not found"}
            if path == "/missions":
                service_created = True
                return {"mission_id": "wm-cancel-bridge-fixed", "status": "queued",
                        "request_sha256": request_sha}
            if path.endswith("/cancel"):
                cancel_acknowledged = True
                return {"ok": True, "status": "cancelling"}
            if method == "GET" and cancel_acknowledged:
                return {
                    "status": "cancelled", "request_sha256": request_sha,
                    "inflight": False, "cleanup_complete": True,
                    "result": {"status": "cancelled", "accepted": False},
                }
            self.fail(f"unexpected request: {method} {path}")

        body = json.dumps(body_payload).encode("utf-8")
        with (
            patch.object(self.b, "_service_mission_id", return_value="wm-cancel-bridge-fixed"),
            patch.object(self.b, "_skitarii_json_request", side_effect=fake_request),
        ):
            verdict = self.b._await_async_skitarii_mission(
                body, run_dir, "cancel-bridge", ledger, 30,
            )
        self.assertEqual(verdict["status"], "cancelled")
        self.assertIn(("POST", "/missions/wm-cancel-bridge-fixed/cancel"), calls)

    def test_async_bridge_adopts_deterministic_mission_after_restart(self):
        from eye_of_terror.ledger import TaskLedger

        run_dir = Path(tempfile.mkdtemp())
        ledger = TaskLedger.create(run_dir / "task_ledger.json", "adopt-bridge", "fix", "Ceraxia")
        ledger.set_status("running")
        service_id = "wm-adopt-bridge-fixed"
        calls = []
        body_payload = {"goal": "fix", "task_id": "adopt-bridge"}
        request_sha = self.b._service_request_sha256(body_payload)
        snapshots = iter([
            {"id": service_id, "status": "running", "result": None,
             "request_sha256": request_sha},
            {"id": service_id, "status": "done", "request_sha256": request_sha,
             "inflight": True, "cleanup_complete": False, "result": {
                "status": "done", "accepted": True, "checks": [], "files": {},
            }},
            {"id": service_id, "status": "done", "request_sha256": request_sha,
             "inflight": False, "cleanup_complete": True, "result": {
                "status": "done", "accepted": True, "checks": [], "files": {},
            }},
        ])

        def fake_request(method, path, **kwargs):
            calls.append((method, path))
            if method == "GET" and path == f"/missions/{service_id}":
                return next(snapshots)
            self.fail(f"restart adoption must not create a second mission: {method} {path}")

        body = json.dumps(body_payload).encode("utf-8")
        with (
            patch.object(self.b, "_service_mission_id", return_value=service_id),
            patch.object(self.b, "_skitarii_json_request", side_effect=fake_request),
            patch.object(self.b, "SKITARII_POLL_INTERVAL_SEC", 0),
        ):
            verdict = self.b._await_async_skitarii_mission(
                body, run_dir, "adopt-bridge", ledger, 30,
            )
        self.assertTrue(verdict["accepted"])
        self.assertNotIn(("POST", "/missions"), calls)
        persisted = TaskLedger.load(run_dir / "task_ledger.json").to_dict()
        self.assertEqual(persisted["skitarii_mission"]["id"], service_id)
        self.assertEqual(persisted["skitarii_mission"]["status"], "done")
        self.assertTrue(any(event["type"] == "skitarii_mission_adopted"
                            for event in persisted["events"]))

    def test_terminal_attempt_is_not_reused_for_a_new_revision(self):
        from eye_of_terror.ledger import TaskLedger

        run_dir = Path(tempfile.mkdtemp())
        ledger = TaskLedger.create(run_dir / "task_ledger.json", "revision-bridge", "fix", "Ceraxia")
        ledger.set_status("running")
        body_payload = {"goal": "fix revision two", "task_id": "revision-bridge"}
        request_sha = self.b._service_request_sha256(body_payload)
        attempt_one = self.b._service_mission_id("revision-bridge", 1)
        attempt_two = self.b._service_mission_id("revision-bridge", 2)
        ledger.data["skitarii_mission"] = {
            "id": attempt_one,
            "attempt": 1,
            "request_sha256": request_sha,
            "status": "done",
            "service": self.b.SKITARII_URL,
        }
        ledger.save()
        calls = []
        second_poll = False

        def fake_request(method, path, **kwargs):
            nonlocal second_poll
            calls.append((method, path))
            if method == "GET" and path == f"/missions/{attempt_two}" and not second_poll:
                second_poll = True
                return {"_http_status": 404, "error": "not found"}
            if method == "POST" and path == "/missions":
                submitted = json.loads(kwargs["body"])
                self.assertEqual(submitted["task_id"], attempt_two)
                return {"mission_id": attempt_two, "status": "queued",
                        "request_sha256": request_sha}
            if method == "GET" and path == f"/missions/{attempt_two}":
                return {"id": attempt_two, "status": "done", "request_sha256": request_sha,
                        "inflight": False, "cleanup_complete": True,
                        "result": {"status": "done", "accepted": True, "checks": [], "files": {}}}
            self.fail(f"unexpected request: {method} {path}")

        with (
            patch.object(self.b, "_skitarii_json_request", side_effect=fake_request),
            patch.object(self.b, "SKITARII_POLL_INTERVAL_SEC", 0),
        ):
            verdict = self.b._await_async_skitarii_mission(
                json.dumps(body_payload).encode("utf-8"), run_dir, "revision-bridge", ledger, 30,
            )
        self.assertTrue(verdict["accepted"])
        self.assertNotIn(("GET", f"/missions/{attempt_one}"), calls)
        persisted = TaskLedger.load(run_dir / "task_ledger.json").to_dict()["skitarii_mission"]
        self.assertEqual(persisted["attempt"], 2)
        self.assertEqual(persisted["id"], attempt_two)

    def test_active_attempt_cannot_jump_to_a_different_service_endpoint(self):
        from eye_of_terror.ledger import TaskLedger

        run_dir = Path(tempfile.mkdtemp())
        ledger = TaskLedger.create(run_dir / "task_ledger.json", "endpoint-bridge", "fix", "Ceraxia")
        body_payload = {"goal": "fix", "task_id": "endpoint-bridge"}
        ledger.data["skitarii_mission"] = {
            "id": self.b._service_mission_id("endpoint-bridge", 1),
            "attempt": 1,
            "request_sha256": self.b._service_request_sha256(body_payload),
            "status": "running",
            "service": "http://127.0.0.1:7999",
        }
        ledger.save()
        with patch.object(self.b, "_skitarii_json_request") as request:
            with self.assertRaisesRegex(RuntimeError, "different service endpoint"):
                self.b._await_async_skitarii_mission(
                    json.dumps(body_payload).encode("utf-8"), run_dir, "endpoint-bridge", ledger, 30,
                )
        request.assert_not_called()

    def test_active_attempt_with_different_request_identity_blocks_new_attempt(self):
        from eye_of_terror.ledger import TaskLedger

        run_dir = Path(tempfile.mkdtemp())
        task_id = "active-identity"
        ledger = TaskLedger.create(run_dir / "task_ledger.json", task_id, "fix", "Ceraxia")
        ledger.data["skitarii_mission"] = {
            "id": self.b._service_mission_id(task_id, 1),
            "attempt": 1,
            "request_sha256": "0" * 64,
            "status": "running",
            "service": self.b.SKITARII_URL,
        }
        ledger.save()
        body = json.dumps({"goal": "changed request", "task_id": task_id}).encode("utf-8")
        with patch.object(self.b, "_skitarii_json_request") as request:
            for attempt in range(2):
                with self.subTest(retry=attempt), self.assertRaisesRegex(
                    RuntimeError,
                    "different request identity|unresolved",
                ):
                    self.b._await_async_skitarii_mission(
                        body,
                        run_dir,
                        task_id,
                        ledger,
                        30,
                    )
        request.assert_not_called()
        persisted = TaskLedger.load(run_dir / "task_ledger.json").to_dict()["skitarii_mission"]
        self.assertEqual(persisted["status"], "running")
        self.assertEqual(persisted["identity_error"], "request_identity_mismatch")

    def test_unproven_cancel_cleanup_cannot_start_a_new_attempt(self):
        from eye_of_terror.ledger import TaskLedger

        run_dir = Path(tempfile.mkdtemp())
        task_id = "cleanup-unproven"
        ledger = TaskLedger.create(run_dir / "task_ledger.json", task_id, "fix", "Ceraxia")
        ledger.data["skitarii_mission"] = {
            "id": self.b._service_mission_id(task_id, 1),
            "attempt": 1,
            "request_sha256": "1" * 64,
            "status": "cancel_cleanup_unproven",
            "cleanup_complete": False,
            "service": self.b.SKITARII_URL,
        }
        ledger.save()
        with patch.object(self.b, "_skitarii_json_request") as request:
            with self.assertRaisesRegex(RuntimeError, "cleanup is unresolved"):
                self.b._await_async_skitarii_mission(
                    json.dumps({"goal": "retry", "task_id": task_id}).encode("utf-8"),
                    run_dir,
                    task_id,
                    ledger,
                    30,
                )
        request.assert_not_called()

    def test_cancel_reconciles_late_cleanup_proof_without_new_attempt(self):
        from eye_of_terror.ledger import TaskLedger

        run_dir = Path(tempfile.mkdtemp())
        task_id = "late-cleanup"
        mission_id = self.b._service_mission_id(task_id, 1)
        request_sha = "2" * 64
        ledger = TaskLedger.create(run_dir / "task_ledger.json", task_id, "fix", "Ceraxia")
        ledger.request_cancel("test")
        ledger.data["skitarii_mission"] = {
            "id": mission_id,
            "attempt": 1,
            "request_sha256": request_sha,
            "status": "cancel_cleanup_unproven",
            "cleanup_complete": False,
            "service": self.b.SKITARII_URL,
        }
        ledger.save()
        with patch.object(
            self.b,
            "_skitarii_json_request",
            return_value={
                "status": "cancelled",
                "request_sha256": request_sha,
                "inflight": False,
                "cleanup_complete": True,
                "result": {"status": "cancelled", "accepted": False},
            },
        ) as request:
            result = self.b.cancel_skitarii_mission_for_run(run_dir, task_id)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(request.call_args.args[:2], ("GET", f"/missions/{mission_id}"))
        persisted = TaskLedger.load(run_dir / "task_ledger.json").to_dict()["skitarii_mission"]
        self.assertEqual(persisted["status"], "cancelled")
        self.assertTrue(persisted["cleanup_complete"])

    def test_answer_resumes_the_existing_service_mission(self):
        from eye_of_terror.ledger import TaskLedger

        run_dir = Path(tempfile.mkdtemp())
        ledger = TaskLedger.create(run_dir / "task_ledger.json", "clarify-bridge", "fix", "Ceraxia")
        ledger.set_status("running")
        mission_id = self.b._service_mission_id("clarify-bridge", 1)
        request_sha = "a" * 64
        ledger.data["skitarii_mission"] = {
            "id": mission_id,
            "attempt": 1,
            "request_sha256": request_sha,
            "status": "needs_user",
        }
        ledger.save()
        ledger.set_result({
            "ok": False,
            "status": "needs_user",
            "phase": "needs_user",
            "needs_user": True,
            "question": "which format?",
            "skitarii_mission_id": mission_id,
            "next_action": self.b._service_clarification_action(),
        })
        seen = {"calls": []}

        def fake_request(method, path, **kwargs):
            seen["calls"].append((method, path))
            if method == "GET":
                return {"id": mission_id, "status": "needs_user", "request_sha256": request_sha}
            seen.update({"method": method, "path": path, "body": kwargs.get("body")})
            return {"ok": True, "status": "running"}

        with patch.object(self.b, "_skitarii_json_request", side_effect=fake_request):
            answered = self.b.answer_skitarii_mission(
                run_dir, "clarify-bridge", "JSON, please",
            )
        self.assertTrue(answered["ok"])
        self.assertEqual(seen["path"], f"/missions/{mission_id}/answer")
        self.assertEqual(json.loads(seen["body"])["answer"], "JSON, please")
        stored = TaskLedger.load(run_dir / "task_ledger.json").to_dict()["result"]
        self.assertEqual(stored["status"], "running")
        self.assertFalse(stored["needs_user"])
        self.assertEqual(stored["next_action"], {})

    def test_durable_cancel_reaches_service_without_a_poll_thread(self):
        from eye_of_terror.ledger import TaskLedger

        run_dir = Path(tempfile.mkdtemp())
        task_id = "durable-cancel"
        mission_id = self.b._service_mission_id(task_id, 1)
        ledger = TaskLedger.create(run_dir / "task_ledger.json", task_id, "fix", "Ceraxia")
        ledger.data["skitarii_mission"] = {
            "id": mission_id,
            "attempt": 1,
            "request_sha256": "b" * 64,
            "status": "running",
            "service": self.b.SKITARII_URL,
        }
        ledger.save()
        def request_result(method, _path, **_kwargs):
            if method == "POST":
                return {"ok": True, "status": "cancelling"}
            return {
                "status": "cancelled", "request_sha256": "b" * 64,
                "inflight": False, "cleanup_complete": True,
                "result": {"status": "cancelled", "accepted": False},
            }

        with patch.object(
            self.b, "_skitarii_json_request", side_effect=request_result,
        ) as request:
            cancelled = self.b.cancel_skitarii_mission_for_run(run_dir, task_id)
        self.assertTrue(cancelled["ok"])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args[:2],
                         ("POST", f"/missions/{mission_id}/cancel"))
        self.assertEqual(request.call_args_list[1].args[:2],
                         ("GET", f"/missions/{mission_id}"))
        stored = TaskLedger.load(run_dir / "task_ledger.json").to_dict()
        self.assertEqual(stored["skitarii_mission"]["status"], "cancelled")

    def test_terminal_needs_user_action_never_creates_a_new_task(self):
        action = self.b._expired_clarification_action("which format?")
        self.assertNotEqual(action.get("endpoint"), "POST /task")
        self.assertEqual(action["kind"], "prompt_user")
        self.assertFalse(action["method"])
        self.assertEqual(self.b._service_clarification_action()["endpoint"],
                         "POST /runs/{task_id}/clarification")

    def test_patch_resource_bounds_reject_unbounded_git_forms(self):
        with self.assertRaises(self.b.SnapshotError):
            self.b._validate_patch_resource_bounds(
                "diff --git a/a.bin b/a.bin\nGIT binary patch\ndelta 12\n",
            )
        with self.assertRaises(self.b.SnapshotError):
            self.b._validate_patch_resource_bounds(
                "diff --git a/a.bin b/a.bin\nGIT binary patch\n"
                f"literal {self.b.MAX_PATCH_FILE_BYTES + 1}\n",
            )
        with self.assertRaises(self.b.SnapshotError):
            self.b._validate_patch_resource_bounds(
                "diff --git a/module b/module\nnew file mode 160000\n",
            )
        with self.assertRaises(self.b.SnapshotError):
            self.b._validate_patch_resource_bounds(
                "diff --git a/large.bin b/copy.bin\nsimilarity index 100%\n"
                "copy from large.bin\ncopy to copy.bin\n",
            )

    def test_post_patch_fingerprint_rejects_escaping_symlink(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "evil").symlink_to("../../outside")
        with self.assertRaises(self.b.SnapshotError):
            self.b._git_visible_content_fingerprint(root)

    def test_snapshot_rejects_symlink_chain_through_ignored_outside_target(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / ".gitignore").write_text("hidden\n", encoding="utf-8")
        (root / "hidden").symlink_to("/etc")
        (root / "visible-link").symlink_to("hidden")
        subprocess.run(["git", "-C", str(root), "add", ".gitignore", "visible-link"], check=True)
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            with self.assertRaisesRegex(self.b.SnapshotError, "target chain escapes"):
                self.b._full_repo_snapshot()
        finally:
            self.b.REPO_ROOT = original

    def test_snapshot_rejects_tracked_symlink_to_ignored_internal_file(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
        (root / "secret.txt").write_text("ignored runtime secret\n", encoding="utf-8")
        (root / "alias.txt").symlink_to("secret.txt")
        subprocess.run(["git", "-C", str(root), "add", ".gitignore", "alias.txt"], check=True)
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            with self.assertRaisesRegex(self.b.SnapshotError, "not Git-visible"):
                self.b._full_repo_snapshot()
        finally:
            self.b.REPO_ROOT = original

    def test_snapshot_rejects_literal_backslash_path_alias(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "a\\b.py").write_text("literal_backslash = 1\n", encoding="utf-8")
        (root / "a").mkdir()
        (root / "a" / "b.py").write_text("slash_path = 2\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "--", "a\\b.py", "a/b.py"], check=True)
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            with self.assertRaisesRegex(self.b.SnapshotError, "unsupported backslash"):
                self.b._full_repo_snapshot()
        finally:
            self.b.REPO_ROOT = original

    def test_snapshot_rejects_symlink_to_omitted_large_asset(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "test"], check=True)
        (root / "big.bin").write_bytes(b"large tracked asset\n")
        (root / "alias.bin").symlink_to("big.bin")
        subprocess.run(["git", "-C", str(root), "add", "big.bin", "alias.bin"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            with self.assertRaisesRegex(self.b.SnapshotError, "omitted external asset"):
                self.b._full_repo_snapshot(max_file_bytes=4)
        finally:
            self.b.REPO_ROOT = original

    def test_snapshot_rejects_tracked_file_reached_through_symlinked_parent(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "test"], check=True)
        (root / "cfg").mkdir()
        (root / "cfg" / "token.txt").write_text("public baseline\n", encoding="utf-8")
        (root / ".gitignore").write_text("hidden/\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", ".gitignore", "cfg/token.txt"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
        (root / "cfg" / "token.txt").unlink()
        (root / "cfg").rmdir()
        (root / "hidden").mkdir()
        (root / "hidden" / "token.txt").write_text("ignored secret\n", encoding="utf-8")
        try:
            (root / "cfg").symlink_to("hidden", target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            with self.assertRaisesRegex(self.b.SnapshotError, "not Git-visible|symlinked parent"):
                self.b._full_repo_snapshot()
        finally:
            self.b.REPO_ROOT = original

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO support is required")
    def test_snapshot_rejects_tracked_fifo_without_opening_it(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "channel").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "channel"], check=True)
        (root / "channel").unlink()
        os.mkfifo(root / "channel")
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            with self.assertRaisesRegex(self.b.SnapshotError, "non-regular"):
                self.b._full_repo_snapshot()
        finally:
            self.b.REPO_ROOT = original

    def test_deleted_tracked_inventory_counts_toward_snapshot_file_limit(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        for index in range(3):
            (root / f"gone-{index}.txt").write_text("gone\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        for index in range(3):
            (root / f"gone-{index}.txt").unlink()
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            with self.assertRaisesRegex(self.b.SnapshotError, "exceeds 2 files"):
                self.b._full_repo_snapshot(max_files=2)
        finally:
            self.b.REPO_ROOT = original

    def test_post_patch_fingerprint_rejects_git_ignored_created_file(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / ".gitignore").write_text("ignored-output\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", ".gitignore"], check=True)
        (root / "ignored-output").write_text("hidden patch output\n", encoding="utf-8")
        with self.assertRaisesRegex(self.b.SnapshotError, "Git-ignored"):
            self.b._git_visible_content_fingerprint(root, reject_ignored_nodes=True)

    def test_patch_stage_rejects_git_quoted_ignore_control_edit(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "test"], check=True)
        (root / ".gitignore").write_text("old\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", ".gitignore"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            snapshot = self.b._full_repo_snapshot()
            patch_text = (
                'diff --git "a/\\056gitignore" "b/\\056gitignore"\n'
                '--- "a/\\056gitignore"\n+++ "b/\\056gitignore"\n'
                "@@ -1 +1 @@\n-old\n+new\n"
            )
            verdict = {
                "patch_bundle": {"unified_diff": patch_text},
                "checks": [{"cmd": "grep -qx new .gitignore"}],
            }
            ledger = type("Ledger", (), {"record_event": lambda *_args: None})()
            result = self.b._verify_and_stage_patch(
                verdict, Path(tempfile.mkdtemp()), ledger, snapshot,
            )
        finally:
            self.b.REPO_ROOT = original
        self.assertFalse(result["tests_pass_in_worktree"])
        self.assertIn("ignore/submodule controls", result.get("error") or result.get("reason", ""))
        self.assertEqual((root / ".gitignore").read_text(encoding="utf-8"), "old\n")

    def test_bounded_sandbox_hides_host_env_and_stops_output_flood(self):
        root = Path(tempfile.mkdtemp())
        with patch.dict(os.environ, {"SKITARII_TEST_SECRET": "must-not-leak"}):
            clean = self.b._run_sandboxed_check("env", root, 30)
        self.assertEqual(clean.returncode, 0)
        self.assertNotIn("SKITARII_TEST_SECRET", clean.stdout)
        flooded = self.b._run_sandboxed_check("yes x | head -c 200000", root, 30)
        self.assertTrue(flooded.output_limit)
        self.assertEqual(flooded.returncode, 125)
        self.assertLessEqual(len(flooded.stdout.encode()), self.b.MAX_VERIFY_OUTPUT_BYTES)

    def test_repository_fingerprint_changes_with_dirty_worktree(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "test"], check=True)
        (root / "a.py").write_text("print(1)\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "a.py"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            before = self.b._full_repo_snapshot().fingerprint
            (root / "a.py").write_text("print(2)\n", encoding="utf-8")
            after = self.b._full_repo_snapshot().fingerprint
        finally:
            self.b.REPO_ROOT = original
        self.assertNotEqual(before, after)

    def test_stale_baseline_blocks_autoapply(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "test"], check=True)
        (root / "a.py").write_text("print(1)\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "a.py"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            snapshot = self.b._full_repo_snapshot()
            (root / "a.py").write_text("print(9)\n", encoding="utf-8")
            patch_text = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-print(1)
+print(2)
"""
            verdict = {
                "patch_bundle": {"unified_diff": patch_text},
                "checks": [{"cmd": "python3 a.py", "expect_stdout": "2"}],
            }
            ledger = type("Ledger", (), {"record_event": lambda *_args: None})()
            with patch.dict(os.environ, {"SKITARII_AUTOAPPLY": "1"}):
                result = self.b._verify_and_stage_patch(
                    verdict, Path(tempfile.mkdtemp()), ledger, snapshot,
                )
        finally:
            self.b.REPO_ROOT = original
        self.assertFalse(result["applies_to_live"])
        self.assertIn("stale_baseline", result["reason"])
        self.assertEqual((root / "a.py").read_text(encoding="utf-8"), "print(9)\n")

    def test_failed_post_apply_verification_rolls_back_exact_baseline(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "test"], check=True)
        (root / "a.py").write_text("print(1)\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "a.py"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            snapshot = self.b._full_repo_snapshot()
            patch_text = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-print(1)
+print(2)
"""
            verdict = {
                "patch_bundle": {"unified_diff": patch_text},
                "checks": [{"cmd": "python3 a.py", "expect_stdout": "2"}],
            }
            ledger = type("Ledger", (), {"record_event": lambda *_args: None})()
            with (
                patch.dict(os.environ, {"SKITARII_AUTOAPPLY": "1"}),
                patch.object(
                    self.b, "_run_check_set",
                    side_effect=[(True, [], ""), (False, [], "forced post failure")],
                ),
            ):
                result = self.b._verify_and_stage_patch(
                    verdict, Path(tempfile.mkdtemp()), ledger, snapshot,
                )
        finally:
            self.b.REPO_ROOT = original
        self.assertTrue(result["rolled_back"])
        self.assertFalse(result["applied_to_live"])
        self.assertFalse(result["post_apply_tests_passed"])
        self.assertEqual((root / "a.py").read_text(encoding="utf-8"), "print(1)\n")

    def test_post_snapshot_exception_also_rolls_back(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "test"], check=True)
        (root / "a.py").write_text("print(1)\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "a.py"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            snapshot = self.b._full_repo_snapshot()
            real_snapshot = self.b._full_repo_snapshot
            calls = {"count": 0}

            def fail_every_post_snapshot(*args, **kwargs):
                calls["count"] += 1
                if calls["count"] >= 2:
                    raise self.b.SnapshotError("persistent post snapshot failure")
                return real_snapshot(*args, **kwargs)

            patch_text = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-print(1)
+print(2)
"""
            verdict = {
                "patch_bundle": {"unified_diff": patch_text},
                "checks": [{"cmd": "python3 a.py", "expect_stdout": "2"}],
            }
            ledger = type("Ledger", (), {"record_event": lambda *_args: None})()
            with (
                patch.dict(os.environ, {"SKITARII_AUTOAPPLY": "1"}),
                patch.object(self.b, "_full_repo_snapshot", side_effect=fail_every_post_snapshot),
                patch.object(self.b, "_run_check_set", return_value=(True, [], "")),
            ):
                result = self.b._verify_and_stage_patch(
                    verdict, Path(tempfile.mkdtemp()), ledger, snapshot,
                )
        finally:
            self.b.REPO_ROOT = original
        self.assertTrue(result["rolled_back"])
        self.assertFalse(result["applied_to_live"])
        self.assertEqual((root / "a.py").read_text(encoding="utf-8"), "print(1)\n")

    def test_patch_verification_clone_has_git_metadata(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "test"], check=True)
        (root / "a.py").write_text("print(1)\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "a.py"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
        patch = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-print(1)
+print(2)
"""
        verdict = {
            "patch_bundle": {"unified_diff": patch},
            "checks": [{"cmd": "git rev-parse --git-dir >/dev/null && python3 a.py",
                        "expect_stdout": "2"}],
        }
        events = []
        ledger = type("Ledger", (), {"record_event": lambda _self, name, data: events.append((name, data))})()
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            run_dir = Path(tempfile.mkdtemp())
            workspace = self.b.WorkspaceSnapshot({"a.py": "print(1)\n"}, modes={"a.py": "100644"})
            result = self.b._verify_and_stage_patch(verdict, run_dir, ledger, workspace)
        finally:
            self.b.REPO_ROOT = original
        self.assertTrue(result["applies_to_live"])
        self.assertTrue(result["tests_pass_in_worktree"])
        self.assertEqual((root / "a.py").read_text(encoding="utf-8"), "print(1)\n")

    def test_patch_verification_uses_dirty_live_baseline_and_preserves_symlink(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "test"], check=True)
        (root / "a.py").write_text("print(0)\n", encoding="utf-8")
        (root / "target.txt").write_text("target\n", encoding="utf-8")
        (root / "link.txt").symlink_to("target.txt")
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
        (root / "a.py").write_text("print(1)\n", encoding="utf-8")  # existing WIP
        patch_text = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-print(1)
+print(2)
"""
        verdict = {
            "patch_bundle": {"unified_diff": patch_text},
            "checks": [{"cmd": "test -L link.txt && test \"$(readlink link.txt)\" = target.txt && python3 a.py",
                        "expect_stdout": "2"}],
        }
        ledger = type("Ledger", (), {"record_event": lambda *_args: None})()
        workspace = self.b.WorkspaceSnapshot(
            {"a.py": "print(1)\n", "target.txt": "target\n"},
            modes={"a.py": "100644", "target.txt": "100644", "link.txt": "120000"},
            symlinks={"link.txt": "target.txt"},
        )
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            result = self.b._verify_and_stage_patch(
                verdict, Path(tempfile.mkdtemp()), ledger, workspace,
            )
        finally:
            self.b.REPO_ROOT = original
        self.assertTrue(result["applies_to_live"])
        self.assertTrue(result["tests_pass_in_worktree"])
        self.assertTrue((root / "link.txt").is_symlink())
        self.assertEqual((root / "a.py").read_text(encoding="utf-8"), "print(1)\n")

    def _run_bridge_fixture(self, autoapply: bool, verdict_override=None):
        from eye_of_terror.ledger import TaskLedger

        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "test"], check=True)
        (root / "a.py").write_text("print(1)\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "a.py"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
        run_dir = Path(tempfile.mkdtemp())
        (run_dir / "contract.json").write_text(
            json.dumps({"kind": "code", "goal": "fix a.py so it prints 2"}), encoding="utf-8",
        )
        (run_dir / "mission_ref.json").write_text("{}", encoding="utf-8")
        (run_dir / "governor_plan.json").write_text(
            json.dumps({"mission_id": "mission-bridge-fixture"}),
            encoding="utf-8",
        )
        (run_dir / "ceraxia_directive.json").write_text(
            json.dumps({
                "kind": "ceraxia_leadership_directive",
                "version": 1,
                "task_id": "bridge-fixture",
                "mission_id": "mission-bridge-fixture",
                "leader": "Ceraxia",
                "decision": "delegate",
                "delegated_to": "SkitariiWarband",
                "mission_intent": "Repair the requested behavior safely",
                "priorities": ["correctness"],
                "constraints": ["preserve unrelated behavior"],
                "success_conditions": ["the requested behavior is verified"],
                "tradeoffs": [],
                "escalation_conditions": [],
            }),
            encoding="utf-8",
        )
        TaskLedger.create(run_dir / "task_ledger.json", "bridge-fixture", "fix", "Ceraxia")
        patch_text = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-print(1)
+print(2)
"""
        verdict = {
            "accepted": True,
            "status": "done",
            "summary": "fixed",
            "artifacts": ["a.py"],
            "files": {"a.py": "print(2)\n"},
            "checks": [{"cmd": "python3 a.py", "expect_stdout": "2"}],
            "rounds": [],
            "held_out_required": True,
            "held_out_check_count": 1,
            "held_out_status": "passed",
            "held_out_acceptance": {"accepted": True, "results": [{"ok": True}]},
            "patch_bundle": {"unified_diff": patch_text, "apply_gate": "accepted"},
        }
        if verdict_override is not None:
            verdict = verdict_override

        original_root = self.b.REPO_ROOT
        original_autoapply = os.environ.pop("SKITARII_AUTOAPPLY", None)
        if autoapply:
            os.environ["SKITARII_AUTOAPPLY"] = "1"
        self.b.REPO_ROOT = root
        try:
            with patch.object(self.b, "_await_async_skitarii_mission", return_value=verdict) as await_mission:
                result = self.b.run_via_skitarii(run_dir, "bridge-fixture", timeout_sec=30)
                self.last_bridge_request = json.loads(await_mission.call_args.args[0])
        finally:
            self.b.REPO_ROOT = original_root
            os.environ.pop("SKITARII_AUTOAPPLY", None)
            if original_autoapply is not None:
                os.environ["SKITARII_AUTOAPPLY"] = original_autoapply
        return root, run_dir, result

    def test_bridge_reports_ready_to_apply_without_claiming_completion(self):
        root, run_dir, result = self._run_bridge_fixture(autoapply=False)
        self.assertEqual(
            self.last_bridge_request["leadership_directive"]["leader"],
            "Ceraxia",
        )
        self.assertEqual(self.last_bridge_request["delegating_task_id"], "bridge-fixture")
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "ready_to_apply")
        self.assertEqual(result["status"], "ready_to_apply")
        self.assertTrue(result["ready_to_apply"])
        self.assertIn("work/skitarii.patch", result["artifacts"])
        self.assertEqual((root / "a.py").read_text(encoding="utf-8"), "print(1)\n")
        ledger = json.loads((run_dir / "task_ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["status"], "blocked")
        self.assertEqual(ledger["result"]["status"], "ready_to_apply")
        self.assertEqual(ledger["result"]["next_action"]["method"], "POST")
        self.assertEqual(
            ledger["result"]["next_action"]["endpoint"],
            "POST /runs/{task_id}/apply_patch",
        )
        body = ledger["result"]["next_action"]["body"]
        self.assertTrue(body["confirm_apply"])
        self.assertEqual(body["expected_patch_sha256"], ledger["result"]["patch_stage"]["patch_sha256"])
        self.assertEqual(body["expected_checks_sha256"], ledger["result"]["patch_stage"]["checks_sha256"])

    def test_bridge_blocks_before_snapshot_when_ceraxia_directive_is_missing(self):
        from eye_of_terror.ledger import TaskLedger

        run_dir = Path(tempfile.mkdtemp())
        (run_dir / "contract.json").write_text(
            json.dumps({"kind": "code", "goal": "fix a.py"}),
            encoding="utf-8",
        )
        TaskLedger.create(run_dir / "task_ledger.json", "missing-directive", "fix", "Ceraxia")
        with (
            patch.object(self.b, "_collect_workspace") as collect,
            patch.object(self.b, "_await_async_skitarii_mission") as dispatch,
        ):
            result = self.b.run_via_skitarii(run_dir, "missing-directive", timeout_sec=30)
        self.assertEqual(result["phase"], "ceraxia_directive_invalid")
        self.assertIn("missing", result["summary"])
        collect.assert_not_called()
        dispatch.assert_not_called()

    def test_bridge_blocks_malformed_or_mismatched_directives_before_http(self):
        from eye_of_terror.ledger import TaskLedger

        base_directive = {
            "kind": "ceraxia_leadership_directive",
            "version": 1,
            "task_id": "directive-check",
            "mission_id": "mission-directive-check",
            "leader": "Ceraxia",
            "decision": "delegate",
            "delegated_to": "SkitariiWarband",
            "mission_intent": "Deliver a verified repair",
            "priorities": ["correctness"],
            "constraints": [],
            "success_conditions": ["behavior is verified"],
            "tradeoffs": [],
            "escalation_conditions": [],
        }
        cases = {
            "unknown detailed field": ({**base_directive, "files": ["app.py"]}, True),
            "mismatched task": ({**base_directive, "task_id": "another-task"}, True),
            "mismatched mission": ({**base_directive, "mission_id": "another-mission"}, True),
            "missing governor plan": (base_directive, False),
        }
        for label, (directive, write_governor_plan) in cases.items():
            with self.subTest(case=label):
                run_dir = Path(tempfile.mkdtemp())
                (run_dir / "contract.json").write_text(
                    json.dumps({"kind": "code", "goal": "fix a.py"}),
                    encoding="utf-8",
                )
                (run_dir / "ceraxia_directive.json").write_text(
                    json.dumps(directive),
                    encoding="utf-8",
                )
                if write_governor_plan:
                    (run_dir / "governor_plan.json").write_text(
                        json.dumps({"mission_id": "mission-directive-check"}),
                        encoding="utf-8",
                    )
                TaskLedger.create(
                    run_dir / "task_ledger.json",
                    "directive-check",
                    "fix",
                    "Ceraxia",
                )
                with (
                    patch.object(self.b, "_collect_workspace") as collect,
                    patch.object(self.b, "_skitarii_json_request") as http,
                ):
                    result = self.b.run_via_skitarii(
                        run_dir,
                        "directive-check",
                        timeout_sec=30,
                    )
                self.assertEqual(result["phase"], "ceraxia_directive_invalid")
                collect.assert_not_called()
                http.assert_not_called()

    def test_bridge_rejects_malformed_or_contradictory_verdicts(self):
        for malformed, expected in (
            ({"accepted": "yes", "files": {}}, "accepted must be a boolean"),
            ({"accepted": True, "needs_user": True, "checks": [], "files": {}},
             "cannot both be true"),
            ({
                "accepted": True, "status": "done", "checks": [{"cmd": "true"}], "files": {},
                "held_out_required": True, "held_out_check_count": 1,
                "held_out_status": "candidate_failure",
                "held_out_acceptance": {"accepted": False},
                "patch_bundle": {"apply_gate": "accepted"},
            }, "private verifier status must be passed"),
            ({
                "accepted": True, "status": "failed", "checks": [{"cmd": "true"}], "files": {},
                "held_out_required": True, "held_out_check_count": 1,
                "held_out_status": "passed", "held_out_acceptance": {"accepted": True},
                "patch_bundle": {"apply_gate": "accepted"},
            }, "completed service status"),
            (["not", "an", "object"], "non-object"),
        ):
            with self.subTest(malformed=malformed):
                root, run_dir, result = self._run_bridge_fixture(
                    autoapply=True, verdict_override=malformed,
                )
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "blocked")
                self.assertIn(expected, result["summary"])
                self.assertEqual((root / "a.py").read_text(encoding="utf-8"), "print(1)\n")
                stored = json.loads((run_dir / "task_ledger.json").read_text(encoding="utf-8"))
                self.assertEqual(stored["status"], "blocked")

    def test_skitarii_final_package_and_patch_artifact_are_retrievable(self):
        _root, run_dir, _result = self._run_bridge_fixture(autoapply=False)
        from eye_of_terror.artifacts import artifact_status, artifact_text, final_package

        ledger = json.loads((run_dir / "task_ledger.json").read_text(encoding="utf-8"))
        status = artifact_status(ledger)
        patch_item = next(item for item in status["artifacts"] if item["path"] == "work/skitarii.patch")
        self.assertTrue(patch_item["exists"])
        preview = artifact_text(ledger, "work/skitarii.patch")
        self.assertTrue(preview["ok"])
        self.assertIn("diff --git", preview["text"])
        with patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded read_bytes used")):
            bounded_preview = artifact_text(ledger, "work/skitarii.patch", max_bytes=7)
        self.assertTrue(bounded_preview["truncated"])
        self.assertLessEqual(len(bounded_preview["text"].encode("utf-8")), 7)
        with self.assertRaisesRegex(ValueError, "not recorded"):
            artifact_text(ledger, "work/.skitarii-verification-checks.json")
        with self.assertRaisesRegex(ValueError, "not recorded"):
            artifact_text(ledger, "task_ledger.json")
        package = final_package(ledger)
        self.assertEqual(package["kind"], "skitarii_bridge_result")
        self.assertEqual(package["status"], "ready_to_apply")
        self.assertTrue(package["ready_to_apply"])
        from eye_of_terror.warmaster_gateway import make_handler
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(run_dir.parent))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/runs/{run_dir.name}/final",
                timeout=30,
            ) as response:
                http_package = json.loads(response.read())
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/runs/{run_dir.name}/artifacts",
                timeout=30,
            ) as response:
                http_artifacts = json.loads(response.read())
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/runs/{run_dir.name}/artifact_text"
                "?path=work%2Fskitarii.patch",
                timeout=30,
            ) as response:
                http_patch = json.loads(response.read())
            with self.assertRaises(urllib.error.HTTPError) as unrecorded:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{server.server_port}/runs/{run_dir.name}/artifact_text"
                    "?path=task_ledger.json",
                    timeout=30,
                )
            self.assertEqual(unrecorded.exception.code, 400)
            unrecorded.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertEqual(http_package["kind"], "skitarii_bridge_result")
        self.assertEqual(http_package["status"], "ready_to_apply")
        self.assertEqual(http_package["client_action"]["method"], "POST")
        self.assertEqual(
            http_package["client_action"]["path"],
            f"/runs/{run_dir.name}/apply_patch",
        )
        self.assertTrue(http_artifacts["ok"])
        self.assertNotIn("artifact_root", http_artifacts)
        self.assertNotIn("workspace_root", http_artifacts)
        self.assertTrue(any(item["path"] == "work/skitarii.patch"
                            for item in http_artifacts["artifacts"]))
        self.assertTrue(all("host_path" not in item for item in http_artifacts["artifacts"]))
        self.assertTrue(http_patch["ok"])
        self.assertIn("diff --git", http_patch["text"])
        self.assertNotIn("host_path", http_patch)
        self.assertNotIn("artifact_root", http_package)

    def test_generic_artifact_http_api_allows_only_recorded_manifest_files(self):
        from eye_of_terror.warmaster_gateway import make_handler

        run_root = Path(tempfile.mkdtemp())
        run_dir = run_root / "generic-artifacts"
        run_dir.mkdir()
        workspace = Path(tempfile.mkdtemp())
        (workspace / "report.txt").write_text("public report\n", encoding="utf-8")
        (workspace / "secret.txt").write_text("private workspace file\n", encoding="utf-8")
        (workspace / ".git").mkdir()
        (workspace / ".git" / "config").write_text("credential = secret\n", encoding="utf-8")
        (workspace / "linked-config").symlink_to(Path(".git") / "config")
        os.link(workspace / ".git" / "config", workspace / "hardlinked-config")
        (workspace / "final_manifest.json").write_text(
            json.dumps({
                "status": "completed", "approved": True,
                "deliverable": "/work/report.txt",
                "files": [
                    {"path": "/work/report.txt"},
                    {"path": "/work/.git/config"},
                    {"path": "/work/linked-config"},
                    {"path": "/work/hardlinked-config"},
                ],
            }),
            encoding="utf-8",
        )
        ledger = {
            "task_id": "generic-artifacts",
            "status": "completed",
            "events": [],
            "steps": [],
            "result": {
                "ok": True,
                "status": "completed",
                "workspace_root": str(workspace),
                "artifacts": ["/work/final_manifest.json"],
            },
        }
        (run_dir / "task_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(run_root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/runs/generic-artifacts/artifact_text"
                "?path=%2Fwork%2Freport.txt",
                timeout=30,
            ) as response:
                report = json.loads(response.read())
            with self.assertRaises(urllib.error.HTTPError) as hidden:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{server.server_port}/runs/generic-artifacts/artifact_text"
                    "?path=%2Fwork%2Fsecret.txt",
                    timeout=30,
                )
            with self.assertRaises(urllib.error.HTTPError) as vcs_hidden:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{server.server_port}/runs/generic-artifacts/artifact_text"
                    "?path=%2Fwork%2F.git%2Fconfig",
                    timeout=30,
                )
            with self.assertRaises(urllib.error.HTTPError) as symlink_hidden:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{server.server_port}/runs/generic-artifacts/artifact_text"
                    "?path=%2Fwork%2Flinked-config",
                    timeout=30,
                )
            with self.assertRaises(urllib.error.HTTPError) as hardlink_hidden:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{server.server_port}/runs/generic-artifacts/artifact_text"
                    "?path=%2Fwork%2Fhardlinked-config",
                    timeout=30,
                )
            hidden.exception.close()
            vcs_hidden.exception.close()
            symlink_hidden.exception.close()
            hardlink_hidden.exception.close()
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/runs/generic-artifacts/final",
                timeout=30,
            ) as response:
                final = json.loads(response.read())
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/runs/generic-artifacts/ledger",
                timeout=30,
            ) as response:
                public_ledger = json.loads(response.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertTrue(report["ok"])
        self.assertEqual(hidden.exception.code, 400)
        self.assertEqual(vcs_hidden.exception.code, 400)
        self.assertEqual(symlink_hidden.exception.code, 400)
        self.assertEqual(hardlink_hidden.exception.code, 400)
        self.assertTrue(final["ok"])
        rendered = json.dumps({"final": final, "ledger": public_ledger})
        for forbidden in ("host_path", "workspace_root", "artifact_root", "patch_file"):
            self.assertNotIn(forbidden, rendered)

    def test_artifact_root_swap_to_symlink_fails_closed(self):
        from eye_of_terror.artifacts import artifact_text

        base = Path(tempfile.mkdtemp())
        workspace = base / "work"
        workspace.mkdir()
        (workspace / "secret.txt").write_text("recorded\n", encoding="utf-8")
        ledger = {
            "result": {
                "workspace_root": str(workspace),
                "artifacts": ["/work/secret.txt"],
            },
        }
        original = base / "original"
        workspace.rename(original)
        outside = Path(tempfile.mkdtemp())
        (outside / "secret.txt").write_text("OUTSIDE-SECRET\n", encoding="utf-8")
        workspace.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "unsafe component"):
            artifact_text(ledger, "/work/secret.txt")

    def test_gateway_run_resolution_and_origin_policy_are_fail_closed(self):
        from eye_of_terror import warmaster_gateway as gateway

        run_root = Path(tempfile.mkdtemp())
        outside = Path(tempfile.mkdtemp())
        (run_root / "linked-run").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "invalid task_id"):
            gateway._resolved_run_dir(run_root, "..")
        with self.assertRaisesRegex(ValueError, "symlink"):
            gateway._resolved_run_dir(run_root, "linked-run")
        self.assertTrue(gateway._is_loopback_origin("http://127.0.0.1:7000"))
        self.assertTrue(gateway._is_loopback_origin("http://[::1]:7000"))
        self.assertFalse(gateway._is_loopback_origin("http://evil.example:7000"))
        self.assertTrue(gateway._gateway_host_allowed("127.0.0.1:7000"))
        self.assertFalse(gateway._gateway_host_allowed("evil.example:7000"))
        self.assertTrue(gateway._gateway_peer_allowed("127.0.0.1"))
        self.assertFalse(gateway._gateway_peer_allowed("192.0.2.10"))
        self.assertEqual(gateway._validate_gateway_bind_host("127.0.0.1"), "127.0.0.1")
        with self.assertRaisesRegex(ValueError, "off loopback"):
            gateway._validate_gateway_bind_host("0.0.0.0")

    def test_gateway_request_body_has_a_hard_byte_limit(self):
        from eye_of_terror import gateway_util

        class Handler:
            headers = {"Content-Length": str(gateway_util.MAX_GATEWAY_REQUEST_BYTES + 1)}
            rfile = io.BytesIO(b"")

        with self.assertRaisesRegex(ValueError, "exceeds"):
            gateway_util.read_payload(Handler())

    def test_ready_patch_has_a_controlled_apply_action(self):
        from eye_of_terror.ledger import TaskLedger

        root, run_dir, _result = self._run_bridge_fixture(autoapply=False)
        ledger = TaskLedger.load(run_dir / "task_ledger.json")
        stage = ledger.to_dict()["result"]["patch_stage"]
        fingerprint = stage["baseline_fingerprint"]
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            applied = self.b.apply_staged_patch(
                run_dir,
                ledger,
                fingerprint,
                expected_patch_sha256=stage["patch_sha256"],
                expected_checks_sha256=stage["checks_sha256"],
            )
        finally:
            self.b.REPO_ROOT = original
        self.assertTrue(applied["ok"])
        self.assertEqual(applied["status"], "completed")
        self.assertEqual((root / "a.py").read_text(encoding="utf-8"), "print(2)\n")

    def test_controlled_apply_rejects_tampered_patch_artifact(self):
        from eye_of_terror.ledger import TaskLedger

        root, run_dir, _result = self._run_bridge_fixture(autoapply=False)
        ledger = TaskLedger.load(run_dir / "task_ledger.json")
        stage = ledger.to_dict()["result"]["patch_stage"]
        (run_dir / "work" / "skitarii.patch").write_text("tampered\n", encoding="utf-8")
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            applied = self.b.apply_staged_patch(
                run_dir,
                ledger,
                stage["baseline_fingerprint"],
                expected_patch_sha256=stage["patch_sha256"],
                expected_checks_sha256=stage["checks_sha256"],
            )
        finally:
            self.b.REPO_ROOT = original
        self.assertFalse(applied["ok"])
        self.assertIn("changed after verification", applied["error"])
        self.assertEqual((root / "a.py").read_text(encoding="utf-8"), "print(1)\n")

    def test_controlled_apply_completes_linked_durable_mission_state(self):
        from eye_of_terror.ledger import TaskLedger
        from EyeOfTerror.common_protocol import commander_order, governor_plan, mission_intake
        from eye_of_terror import mission_control

        root, run_dir, _result = self._run_bridge_fixture(autoapply=False)
        mission_dir = Path(tempfile.mkdtemp()) / "mission-bridge-fixture"
        mission_dir.mkdir()
        (mission_dir / "mission.json").write_text(
            json.dumps({
                "mission_id": "mission-bridge-fixture", "task_id": "bridge-fixture",
                "assigned_governor": "Ceraxia", "status": "blocked",
            }),
            encoding="utf-8",
        )
        protocol_files = {
            "mission_intake.json": mission_intake("mission-bridge-fixture", "fix a.py"),
            "commander_order.json": commander_order(
                "mission-bridge-fixture", "Ceraxia", "fix a.py", "fix code", "fix a.py",
                ["a.py prints 2"],
            ),
            "governor_plan.json": governor_plan(
                "mission-bridge-fixture", "Ceraxia", "fix a.py", [], ["verified patch"],
            ),
        }
        for name, payload in protocol_files.items():
            (mission_dir / name).write_text(json.dumps(payload), encoding="utf-8")
        (run_dir / "mission_ref.json").write_text(
            json.dumps({"mission_dir": str(mission_dir)}), encoding="utf-8",
        )
        ledger = TaskLedger.load(run_dir / "task_ledger.json")
        stage = ledger.to_dict()["result"]["patch_stage"]
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        try:
            applied = self.b.apply_staged_patch(
                run_dir,
                ledger,
                stage["baseline_fingerprint"],
                expected_patch_sha256=stage["patch_sha256"],
                expected_checks_sha256=stage["checks_sha256"],
            )
        finally:
            self.b.REPO_ROOT = original
        self.assertTrue(applied["ok"])
        mission = json.loads((mission_dir / "mission.json").read_text(encoding="utf-8"))
        state = json.loads((mission_dir / "mission_state.json").read_text(encoding="utf-8"))
        final = json.loads((mission_dir / "final_response.json").read_text(encoding="utf-8"))
        self.assertEqual(mission["status"], "completed")
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(final["status"], "completed")
        self.assertIn("completed", (mission_dir / "progress_events.jsonl").read_text(encoding="utf-8"))
        audit = mission_control.mission_protocol_audit(mission_dir)
        self.assertTrue(audit["ok"], audit["errors"])

    def test_protocol_finalize_failure_reconciles_without_reapplying_patch(self):
        from eye_of_terror.ledger import TaskLedger
        from EyeOfTerror.common_protocol import commander_order, governor_plan, mission_intake
        from eye_of_terror import mission_control

        root, run_dir, _result = self._run_bridge_fixture(autoapply=False)
        mission_dir = Path(tempfile.mkdtemp()) / "mission-reconcile-fixture"
        mission_dir.mkdir()
        (mission_dir / "mission.json").write_text(
            json.dumps({
                "mission_id": "mission-reconcile-fixture", "task_id": "bridge-fixture",
                "assigned_governor": "Ceraxia", "status": "blocked",
            }),
            encoding="utf-8",
        )
        for name, payload in {
            "mission_intake.json": mission_intake("mission-reconcile-fixture", "fix a.py"),
            "commander_order.json": commander_order(
                "mission-reconcile-fixture", "Ceraxia", "fix a.py", "fix code", "fix a.py",
                ["a.py prints 2"],
            ),
            "governor_plan.json": governor_plan(
                "mission-reconcile-fixture", "Ceraxia", "fix a.py", [], ["verified patch"],
            ),
        }.items():
            (mission_dir / name).write_text(json.dumps(payload), encoding="utf-8")
        (run_dir / "mission_ref.json").write_text(
            json.dumps({"mission_dir": str(mission_dir)}), encoding="utf-8",
        )
        staged = TaskLedger.load(run_dir / "task_ledger.json").to_dict()["result"]["patch_stage"]
        original_root = self.b.REPO_ROOT
        original_finalize = self.b._finalize_linked_completion
        finalizer_calls = 0
        pre_finalize_states = []

        def fail_once(*args, **kwargs):
            nonlocal finalizer_calls
            finalizer_calls += 1
            durable = TaskLedger.load(run_dir / "task_ledger.json").to_dict()
            pre_finalize_states.append((durable.get("status"), durable.get("result", {}).get("status")))
            if finalizer_calls == 1:
                raise RuntimeError("injected protocol write failure")
            return original_finalize(*args, **kwargs)

        self.b.REPO_ROOT = root
        try:
            with patch.object(self.b, "_finalize_linked_completion", side_effect=fail_once):
                first = self.b.apply_staged_patch(
                    run_dir,
                    TaskLedger.load(run_dir / "task_ledger.json"),
                    staged["baseline_fingerprint"],
                    expected_patch_sha256=staged["patch_sha256"],
                    expected_checks_sha256=staged["checks_sha256"],
                )
                self.assertFalse(first["ok"])
                self.assertEqual(first["status"], "protocol_finalize_pending")
                self.assertEqual((root / "a.py").read_text(encoding="utf-8"), "print(2)\n")
                pending = TaskLedger.load(run_dir / "task_ledger.json").to_dict()["result"]
                stage = pending["patch_stage"]
                reconciled = self.b.apply_staged_patch(
                    run_dir,
                    TaskLedger.load(run_dir / "task_ledger.json"),
                    stage["baseline_fingerprint"],
                    expected_patch_sha256=stage["patch_sha256"],
                    expected_checks_sha256=stage["checks_sha256"],
                )
        finally:
            self.b.REPO_ROOT = original_root
        self.assertTrue(reconciled["ok"])
        self.assertEqual(reconciled["status"], "completed")
        self.assertEqual(finalizer_calls, 2)
        self.assertEqual(pre_finalize_states[0], ("blocked", "protocol_finalize_pending"))
        self.assertEqual(pre_finalize_states[1], ("blocked", "protocol_finalize_pending"))
        self.assertEqual((root / "a.py").read_text(encoding="utf-8"), "print(2)\n")
        audit = mission_control.mission_protocol_audit(mission_dir)
        self.assertTrue(audit["ok"], audit["errors"])

    def test_gateway_apply_patch_endpoint_executes_ready_action(self):
        from eye_of_terror.ledger import TaskLedger
        from eye_of_terror.warmaster_gateway import make_handler

        root, run_dir, _result = self._run_bridge_fixture(autoapply=False)
        ledger = TaskLedger.load(run_dir / "task_ledger.json")
        stage = ledger.to_dict()["result"]["patch_stage"]
        fingerprint = stage["baseline_fingerprint"]
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(run_dir.parent))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/runs/{run_dir.name}/apply_patch",
                data=json.dumps({
                    "expected_repository_fingerprint": fingerprint,
                    "expected_patch_sha256": stage["patch_sha256"],
                    "expected_checks_sha256": stage["checks_sha256"],
                    "confirm_apply": True,
                }).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            self.b.REPO_ROOT = original
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "completed")
        self.assertEqual((root / "a.py").read_text(encoding="utf-8"), "print(2)\n")

    def test_gateway_apply_patch_rejects_cross_origin_request(self):
        from eye_of_terror.ledger import TaskLedger
        from eye_of_terror.warmaster_gateway import make_handler

        root, run_dir, _result = self._run_bridge_fixture(autoapply=False)
        ledger = TaskLedger.load(run_dir / "task_ledger.json")
        stage = ledger.to_dict()["result"]["patch_stage"]
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(run_dir.parent))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        original = self.b.REPO_ROOT
        self.b.REPO_ROOT = root
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/runs/{run_dir.name}/apply_patch",
                data=json.dumps({
                    "expected_repository_fingerprint": stage["baseline_fingerprint"],
                    "expected_patch_sha256": stage["patch_sha256"],
                    "expected_checks_sha256": stage["checks_sha256"],
                    "confirm_apply": True,
                }).encode("utf-8"),
                headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=30)
            self.assertEqual(caught.exception.code, 403)
            self.assertIsNone(caught.exception.headers.get("Access-Control-Allow-Origin"))
            caught.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            self.b.REPO_ROOT = original
        self.assertEqual((root / "a.py").read_text(encoding="utf-8"), "print(1)\n")

    def test_gateway_clarification_resumes_same_run_instead_of_creating_task(self):
        from eye_of_terror.ledger import TaskLedger
        from eye_of_terror import warmaster_gateway as gateway

        run_root = Path(tempfile.mkdtemp())
        run_dir = run_root / "clarify-route"
        run_dir.mkdir()
        ledger = TaskLedger.create(run_dir / "task_ledger.json", "clarify-route", "fix", "Ceraxia")
        ledger.set_status("running")
        ledger.set_result({
            "ok": False,
            "status": "needs_user",
            "phase": "needs_user",
            "needs_user": True,
            "skitarii_mission_id": "wm-clarify-route-fixed",
        })
        called = {}

        def answer_same_run(received_dir, task_id, answer):
            called.update({"run_dir": received_dir, "task_id": task_id, "answer": answer})
            return {"ok": True, "status": "running", "task_id": task_id}

        server = ThreadingHTTPServer(("127.0.0.1", 0), gateway.make_handler(run_root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(gateway, "answer_skitarii_mission", side_effect=answer_same_run):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/runs/clarify-route/clarification",
                    data=json.dumps({"answer": "JSON"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=30) as http_response:
                    payload = json.loads(http_response.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertTrue(payload["ok"])
        self.assertEqual(called["run_dir"], run_dir.resolve())
        self.assertEqual(called["task_id"], "clarify-route")
        self.assertEqual(called["answer"], "JSON")
        self.assertEqual(payload["client_action"]["path"], "/runs/clarify-route/snapshot")

    def test_gateway_cancel_fans_out_to_durable_skitarii_mission(self):
        from eye_of_terror.ledger import TaskLedger
        from eye_of_terror import warmaster_gateway as gateway

        run_root = Path(tempfile.mkdtemp())
        run_dir = run_root / "interrupted-cancel"
        run_dir.mkdir()
        ledger = TaskLedger.create(
            run_dir / "task_ledger.json", "interrupted-cancel", "fix", "Ceraxia",
        )
        ledger.force_status("interrupted", reason="simulated gateway restart")
        server = ThreadingHTTPServer(("127.0.0.1", 0), gateway.make_handler(run_root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def complete_direct_cancel(received_dir, received_task_id):
            durable = TaskLedger.load(received_dir / "task_ledger.json")
            durable.force_status("cancelled", reason="service cleanup proven")
            return {"ok": True, "status": "cancelled", "mission_id": "wm-existing"}

        try:
            with (
                patch.object(
                    gateway, "cancel_skitarii_mission_for_run",
                    side_effect=complete_direct_cancel,
                ) as cancel_service,
                patch.object(gateway, "cancel_http_worker_tasks", return_value=[]),
            ):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/runs/interrupted-cancel/cancel",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=30) as http_response:
                    payload = json.loads(http_response.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "cancelled")
        self.assertTrue(payload["skitarii_cancellation"]["ok"])
        cancel_service.assert_called_once_with(run_dir.resolve(), "interrupted-cancel")
        self.assertEqual(TaskLedger.load(run_dir / "task_ledger.json").to_dict()["status"], "cancelled")

    def test_bridge_completes_only_after_verified_live_apply(self):
        root, _run_dir, result = self._run_bridge_fixture(autoapply=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["phase"], "completed")
        self.assertTrue(result["patch_stage"]["applied_to_live"])
        self.assertEqual((root / "a.py").read_text(encoding="utf-8"), "print(2)\n")


class TestExplorerReviewer(unittest.TestCase):
    def test_explorer_greenfield_is_empty(self):
        import explorer
        exp = explorer.explore("напиши новый скрипт", None)
        self.assertEqual(exp["target_files"], [])

    def test_explorer_parses_and_scopes(self):
        import explorer
        orig = explorer._chat
        explorer._chat = lambda p, max_tokens=900: (
            '{"target_files":["a.py"],"related_files":["b.py"],"tests":["test_a.py"],'
            '"invariants":["mul stays correct"],"risks":["off-by-one"]}')
        try:
            exp = explorer.explore("fix a.py", {"a.py": "def add(): pass", "b.py": "x=1"})
        finally:
            explorer._chat = orig
        self.assertIn("a.py", exp["target_files"])
        self.assertIn("mul stays correct", exp["invariants"])
        self.assertIn("Recon", explorer.brief_for_fighter(exp))

    def test_explorer_sees_full_working_copy_via_executor(self):
        import explorer, tempfile
        from pathlib import Path
        from executor import LocalExecutor
        ex = LocalExecutor(Path(tempfile.mkdtemp(prefix="expl-")))
        ex.write_file("a.py", "def add(): pass\n")
        ex.write_file("helper.py", "def h(): pass\n")   # NOT in the preloaded slice
        seen = {}
        orig = explorer._chat
        def cap(p, max_tokens=900):
            seen["prompt"] = p
            return '{"target_files":["helper.py"],"related_files":[],"tests":[],"invariants":[],"risks":[]}'
        explorer._chat = cap
        try:
            exp = explorer.explore("fix helper", {"a.py": "def add(): pass\n"}, ex)
        finally:
            explorer._chat = orig
        self.assertIn("helper.py", seen["prompt"])          # recon saw the un-preloaded file
        self.assertIn("helper.py", exp["target_files"])     # and could target it

    def test_explorer_inventory_is_not_truncated_at_400_paths(self):
        import explorer
        workspace = {f"src/f{i:03}.py": "x = 1\n" for i in range(405)}
        seen = {}
        orig = explorer._chat
        explorer._chat = lambda prompt, max_tokens=900: (
            seen.setdefault("prompt", prompt) or
            '{"target_files":[],"related_files":[],"tests":[],"invariants":[],"risks":[]}'
        )
        try:
            explorer.explore("fix final module", workspace)
        finally:
            explorer._chat = orig
        self.assertIn("src/f404.py", seen["prompt"])

    def test_explorer_drops_hallucinated_paths_from_every_path_field(self):
        import explorer
        orig = explorer._chat
        explorer._chat = lambda *_args, **_kwargs: (
            '{"target_files":["ghost.py"],"related_files":["missing.py"],'
            '"tests":["test_ghost.py"],"invariants":[],"risks":[]}'
        )
        try:
            result = explorer.explore("fix app", {"app.py": "print(1)\n"})
        finally:
            explorer._chat = orig
        self.assertEqual(result["target_files"], [])
        self.assertEqual(result["related_files"], [])
        self.assertEqual(result["tests"], [])

    def test_reviewer_vetoes_empty_diff(self):
        import reviewer
        r = reviewer.review("fix bug", "", {"results": []})
        self.assertFalse(r["approved"])

    def test_reviewer_rejects_when_acceptance_failed(self):
        import reviewer
        orig = reviewer._chat
        reviewer._chat = lambda p, max_tokens=700: '{"approved": true, "issues": []}'
        try:
            # even if the model says approve, a real acceptance failure forces reject
            r = reviewer.review("fix", "diff --git a b\n+x", {"results": [{"ok": False, "target": "t"}]})
        finally:
            reviewer._chat = orig
        self.assertFalse(r["approved"])

    def test_reviewer_samples_the_whole_large_diff(self):
        import reviewer
        diff = "BEGIN\n" + ("a" * 7000) + "MIDDLE\n" + ("b" * 7000) + "END\n"
        sampled, truncated = reviewer._diff_sample(diff, limit=4000)
        self.assertTrue(truncated)
        self.assertIn("BEGIN", sampled)
        self.assertIn("END", sampled)
        self.assertIn("OMITTED DIFF REGION", sampled)


class TestMissionStore(unittest.TestCase):
    def setUp(self):
        import mission_store

        self.mission_store = mission_store
        self._old_store_root = mission_store.STORE_ROOT
        self._old_missions = mission_store._MISSIONS
        self._store_temp = tempfile.TemporaryDirectory()
        mission_store.STORE_ROOT = Path(self._store_temp.name)
        mission_store._MISSIONS = {}

    def tearDown(self):
        for worker in list(threading.enumerate()):
            if worker.name.startswith("mission-"):
                worker.join(timeout=1)
        self.mission_store.STORE_ROOT = self._old_store_root
        self.mission_store._MISSIONS = self._old_missions
        self._store_temp.cleanup()

    def test_async_lifecycle_and_result(self):
        import mission_store, time as _t
        m = mission_store.create("t-async-1", "goal")
        mission_store.run_async(m, lambda mm: {"status": "done", "accepted": True})
        for _ in range(50):
            if m.status in ("done", "failed"):
                break
            _t.sleep(0.05)
        self.assertEqual(m.status, "done")
        self.assertTrue(m.result["accepted"])

    def test_resume_reruns_a_failed_mission(self):
        import mission_store, time as _t
        m = mission_store.create("t-resume-1", "goal")
        mission_store.run_async(m, lambda mm: {"status": "failed", "accepted": False})
        for _ in range(50):
            if m.status == "failed":
                break
            _t.sleep(0.05)
        self.assertEqual(m.status, "failed")
        # can't resume an active mission; can resume a stopped one
        self.assertTrue(mission_store.resume("t-resume-1", lambda mm: {"status": "done", "accepted": True}))
        for _ in range(50):
            if m.status == "done":
                break
            _t.sleep(0.05)
        self.assertEqual(m.status, "done")
        self.assertTrue(m.result["accepted"])

    def test_resume_refuses_active_mission(self):
        import mission_store
        m = mission_store.create("t-resume-2", "goal")
        m.status = "running"
        self.assertFalse(mission_store.resume("t-resume-2", lambda mm: {"status": "done"}))

    def test_ask_user_blocks_then_answers(self):
        import mission_store, threading, time as _t
        m = mission_store.create("t-ask-1", "goal")
        out = {}
        def worker(mm):
            out["ans"] = mm.ask_user("which port?")
            return {"status": "done", "accepted": True}
        mission_store.run_async(m, worker)
        for _ in range(40):
            if m.status == "needs_user":
                break
            _t.sleep(0.05)
        self.assertEqual(m.status, "needs_user")
        self.assertEqual(m.question, "which port?")
        self.assertTrue(m.provide_answer("8099"))
        for _ in range(40):
            if m.status == "done":
                break
            _t.sleep(0.05)
        self.assertEqual(out.get("ans"), "8099")

    def test_cancel_unblocks_and_stops(self):
        import mission_store, time as _t
        m = mission_store.create("t-cancel-1", "goal")
        def worker(mm):
            mm.ask_user("waiting?")   # will block until cancel
            return {"status": "done", "accepted": True}
        mission_store.run_async(m, worker)
        for _ in range(40):
            if m.status == "needs_user":
                break
            _t.sleep(0.05)
        self.assertTrue(mission_store.cancel("t-cancel-1"))
        self.assertIn(m.status, {"cancelling", "cancelled"})
        for _ in range(40):
            if m.status == "cancelled" and not m.inflight:
                break
            _t.sleep(0.05)
        self.assertEqual(m.status, "cancelled")
        self.assertFalse(m.inflight)
        self.assertTrue(m.cleanup_complete)

    def test_invalid_and_duplicate_ids_fail_without_path_aliases(self):
        import mission_store

        for invalid in ("", ".", "..", "../x", "x/y", "x\\y", " x"):
            with self.assertRaises(ValueError, msg=invalid):
                mission_store.create(invalid, "goal")
            self.assertIsNone(mission_store.get(invalid))
        first = mission_store.create("same-id", "goal")
        self.assertEqual(first.id, "same-id")
        with self.assertRaises(mission_store.MissionExistsError):
            mission_store.create("same-id", "different goal")

    def test_rehydrate_preserves_exact_payload_and_skips_corrupt_event_line(self):
        import mission_store

        payload = {
            "goal": "fix",
            "mode": "patch",
            "workspace_files": {"src/a.py": "print(1)\n"},
            "workspace_deleted": ["old.py"],
            "workspace_modes": {"src/a.py": "100644"},
            "workspace_symlinks": {},
        }
        mission = mission_store.create("persisted-id", "fix")
        mission.payload = payload
        mission.set_status("failed")
        with open(mission._dir() / "events.jsonl", "a", encoding="utf-8") as handle:
            handle.write("{corrupt trailing event\n")
            handle.write(json.dumps({"type": "after-corrupt"}) + "\n")
        mission_store._MISSIONS = {}
        mission_store._rehydrate()
        restored = mission_store.get("persisted-id")
        self.assertIsNotNone(restored)
        self.assertEqual(restored.payload, payload)
        self.assertTrue(any(event.get("type") == "after-corrupt" for event in restored.events))

    def test_rehydrate_terminalizes_active_mission_without_losing_patch_payload(self):
        import mission_store

        mission = mission_store.create("active-id", "fix")
        mission.payload = {"goal": "fix", "mode": "patch", "workspace_files": {"a.py": "x=1\n"}}
        mission.set_status("running")
        mission_store._MISSIONS = {}
        mission_store._rehydrate()
        restored = mission_store.get("active-id")
        self.assertEqual(restored.status, "blocked")
        self.assertEqual(restored.payload["mode"], "patch")
        self.assertIn("workspace_files", restored.payload)


class TestParallel(unittest.TestCase):
    def test_dependency_waves(self):
        from planner import _dependency_waves
        subs = [{"title": "a", "depends_on": []}, {"title": "b", "depends_on": []},
                {"title": "c", "depends_on": [0, 1]}]
        w = _dependency_waves(subs)
        self.assertEqual([s["title"] for s in w[0]], ["a", "b"])   # independent → same wave
        self.assertEqual(w[1][0]["title"], "c")                     # dependent → later wave

    def test_broken_deps_do_not_deadlock(self):
        from planner import _dependency_waves
        subs = [{"title": "a", "depends_on": [5]}]  # invalid dep
        w = _dependency_waves(subs)
        self.assertEqual(sum(len(x) for x in w), 1)  # still scheduled, no infinite loop

    def test_child_executor_is_isolated(self):
        ex = _ex()
        ch = ex.child("s0")
        try:
            ex.write_file("shared.py", "base")
            ch.write_file("only_child.py", "x")
            self.assertNotEqual(ex.workdir, ch.workdir)
            self.assertEqual(ch.workdir.parent, ex.workdir.parent)
            self.assertRegex(ch.workdir.name, r"^mission-[0-9a-f]{16}$")
            self.assertFalse((ex.workdir / "only_child.py").exists())
        finally:
            ch.close()
            ex.close()


class TestToolRegistry(unittest.TestCase):
    def test_deny_list_removes_tool(self):
        import importlib, os, tools
        os.environ["SKITARII_TOOLS_DENY"] = "grep_symbol"
        importlib.reload(tools)
        names = [t.name for t in tools.enabled_extra_tools()]
        os.environ.pop("SKITARII_TOOLS_DENY", None); importlib.reload(tools)
        self.assertNotIn("grep_symbol", names)
        self.assertIn("git_diff", names)

    def test_find_files_lists_created_file(self):
        import tools
        ex = _ex(); ex.write_file("hello.py", "x=1")
        out = tools.dispatch_extra("find_files", {"pattern": "*.py"}, ex)
        self.assertIn("hello.py", out)

    def test_unknown_tool_returns_none(self):
        import tools
        self.assertIsNone(tools.dispatch_extra("does_not_exist", {}, _ex()))


class TestClarifyGate(unittest.TestCase):
    def test_vague_goal_asks(self):
        import clarify
        orig = clarify._chat
        clarify._chat = lambda p, max_tokens=200: '{"ready": false, "question": "Что кэшировать?"}'
        try:
            self.assertEqual(clarify.needs_clarification("Добавь кэш."), "Что кэшировать?")
        finally:
            clarify._chat = orig

    def test_clear_goal_passes(self):
        import clarify
        orig = clarify._chat
        clarify._chat = lambda p, max_tokens=200: '{"ready": true}'
        try:
            self.assertEqual(clarify.needs_clarification("Напиши fizzbuzz.py"), "")
        finally:
            clarify._chat = orig

    def test_workspace_short_circuits_without_llm(self):
        import clarify
        orig = clarify._chat
        def _boom(p, max_tokens=200):
            raise AssertionError("LLM must not be called when workspace is present")
        clarify._chat = _boom
        try:
            self.assertEqual(clarify.needs_clarification("anything", has_workspace=True), "")
        finally:
            clarify._chat = orig

    def test_gate_fails_open_on_error(self):
        import clarify
        orig = clarify._chat
        def _boom(p, max_tokens=200):
            raise RuntimeError("llm down")
        clarify._chat = _boom
        try:
            self.assertEqual(clarify.needs_clarification("Ускорь обработку."), "")
        finally:
            clarify._chat = orig


class TestWorktreeMerge(unittest.TestCase):
    """Parallel subtasks run in real git worktrees and merge back with real git — two
    subtasks touching different files both land; touching the SAME file is a flagged
    conflict, not a silent last-writer-wins."""

    def _base(self):
        import tempfile
        from pathlib import Path
        from executor import LocalExecutor
        d = tempfile.mkdtemp(prefix="wt-base-")
        return LocalExecutor(Path(d))

    def test_disjoint_merges_both_and_conflict_is_flagged(self):
        import planner
        base = self._base()
        base.write_file("seed.txt", "0\n")
        orig = planner._run_with_retry
        # each stub subtask writes its assigned file/content in its own worktree
        plan = {0: ("a.txt", "A\n"), 1: ("b.txt", "B\n")}

        def stub(goal, executor, task_id, *, top_goal, note, max_wall_sec, rounds=2, ask_fn=None, cancel_fn=None):
            rel, content = plan[int(goal)]
            executor.write_file(rel, content)
            return {"accepted": True, "status": "done"}

        planner._run_with_retry = stub
        notes = []
        try:
            wave = [{"goal": "0", "title": "s0"}, {"goal": "1", "title": "s1"}]
            planner._run_wave_parallel(wave, base, "wtT", "top", notes.append, 60, None, None)
            self.assertEqual(base.read_file("a.txt").strip(), "A")
            self.assertEqual(base.read_file("b.txt").strip(), "B")   # both disjoint files landed
            # now a conflicting wave: both subtasks rewrite seed.txt differently
            plan.clear(); plan.update({0: ("seed.txt", "X\n"), 1: ("seed.txt", "Y\n")})
            planner._run_wave_parallel(wave, base, "wtC", "top", notes.append, 60, None, None)
            self.assertTrue(any("КОНФЛИКТ" in n for n in notes), "merge conflict must be flagged")
        finally:
            planner._run_with_retry = orig


class TestEvalSuite(unittest.TestCase):
    def test_thirty_tasks_six_categories(self):
        import eval_suite
        self.assertEqual(len(eval_suite.TASKS), 30)
        self.assertEqual(eval_suite.categories(),
                         {"greenfield": 5, "fix_one": 5, "multi": 5,
                          "unspecified": 5, "regression": 5, "ambiguous": 5})

    def test_every_non_ambiguous_task_has_a_behavioural_check(self):
        # the eval is only meaningful if truth is checked by real behaviour, not compile
        import eval_suite, acceptor
        for t in eval_suite.TASKS:
            if t["category"] == "ambiguous":
                self.assertEqual(t["oracle_checks"], [])
                continue
            kinds = {acceptor.check_kind(c) for c in t["oracle_checks"]}
            self.assertTrue(kinds & {"behavior", "test"}, f"{t['id']} has no behavioural oracle")


if __name__ == "__main__":
    unittest.main(verbosity=2)
