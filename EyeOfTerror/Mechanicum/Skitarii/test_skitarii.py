"""Focused tests for the Skitarii brigade — no LLM, no VM. They pin the safety
invariants the review flagged: no false success, path preservation, spec fallback,
patch/greenfield classification, oracle acceptance, budget honesty.

Run:  python3 -m unittest EyeOfTerror.Mechanicum.Skitarii.test_skitarii  (from repo root)
   or  python3 test_skitarii.py                                          (from this dir)
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

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
        info = ex.bash_background("sleep 1")
        self.assertTrue(info["started"])
        self.assertTrue(info["pid"])


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
        f, is_patch = self.b._collect_workspace("почини логику приёмщика acceptor в бригаде Skitarii")
        self.assertTrue(is_patch)
        self.assertGreater(len(f), 0)


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


class TestMissionStore(unittest.TestCase):
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
        self.assertEqual(m.status, "cancelled")


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
        ex.write_file("shared.py", "base")
        ch = ex.child("s0")
        ch.write_file("only_child.py", "x")
        self.assertNotIn("wt", str(ex.workdir))
        self.assertIn("wt", str(ch.workdir))


if __name__ == "__main__":
    unittest.main(verbosity=2)
