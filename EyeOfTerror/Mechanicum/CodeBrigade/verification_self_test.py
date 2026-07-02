#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import verification_adapter


def main() -> int:
    policy = json.loads((Path(__file__).resolve().parent / "verification_policy.json").read_text(encoding="utf-8"))
    if policy["allowed_prefixes"] != verification_adapter.ALLOWED_PREFIXES:
        raise AssertionError(f"verification policy allowlist drifted from runtime: {policy}")
    if "block absolute path tokens" not in policy["path_token_guards"]:
        raise AssertionError(f"verification policy should document path token guards: {policy}")
    if "py_compile" not in policy.get("acceptance_trace_policy", ""):
        raise AssertionError(f"verification policy should document syntax-only acceptance trace limits: {policy}")
    schema = json.loads((Path(__file__).resolve().parent / "verification_execution.schema.json").read_text(encoding="utf-8"))
    if "contract_trace" not in schema.get("required", []):
        raise AssertionError(f"verification schema must require contract_trace: {schema}")
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "ok.py").write_text("VALUE = 1\n", encoding="utf-8")
        planned = verification_adapter.run_verification_commands(["python -m py_compile ok.py"], str(repo), execute=False)
        if planned["status"] != "planned" or planned["results"][0]["status"] != "planned":
            raise AssertionError(f"planned verification should pass as planned: {planned}")
        executed = verification_adapter.run_verification_commands(["python -m py_compile ok.py"], str(repo), execute=True)
        if executed["status"] != "passed" or executed["results"][0]["returncode"] != 0:
            raise AssertionError(f"py_compile should execute successfully: {executed}")
        syntax_trace = verification_adapter.run_verification_commands(
            ["python -m py_compile ok.py"],
            str(repo),
            execute=True,
            acceptance_requirements=["changed behavior is covered by targeted verification"],
        )
        if syntax_trace["contract_trace"]["rows"][0]["status"] != "syntax_only":
            raise AssertionError(f"syntax-only verification must not prove behavior acceptance: {syntax_trace}")
        (repo / "test_ok.py").write_text(
            "import unittest\n\nclass OkTest(unittest.TestCase):\n    def test_ok(self):\n        self.assertEqual(1, 1)\n",
            encoding="utf-8",
        )
        unittest_executed = verification_adapter.run_verification_commands(["python -m unittest test_ok.py"], str(repo), execute=True)
        if unittest_executed["status"] != "passed" or unittest_executed["results"][0]["returncode"] != 0:
            raise AssertionError(f"unittest should execute successfully: {unittest_executed}")
        behavior_trace = verification_adapter.run_verification_commands(
            ["python -m unittest test_ok.py", "python -m py_compile ok.py"],
            str(repo),
            execute=True,
            acceptance_requirements=["changed behavior is covered by targeted verification"],
        )
        if behavior_trace["contract_trace"]["status"] != "proven" or behavior_trace["contract_trace"]["behavior_evidence_count"] < 1:
            raise AssertionError(f"behavior command should prove behavior acceptance: {behavior_trace}")
        (repo / "test_fail.py").write_text(
            "import unittest\n\nclass FailTest(unittest.TestCase):\n    def test_fail(self):\n        self.assertEqual(1, 2)\n",
            encoding="utf-8",
        )
        failing_unittest = verification_adapter.run_verification_commands(["python -m unittest test_fail.py"], str(repo), execute=True)
        diagnostics = failing_unittest["results"][0]["diagnostics"]
        if failing_unittest["status"] != "failed" or not diagnostics["has_assertion_failure"]:
            raise AssertionError(f"failing unittest should expose assertion diagnostics: {failing_unittest}")
        if not any(item.startswith("test_fail.py:") for item in diagnostics["traceback_files"]):
            raise AssertionError(f"failing unittest should expose repo-relative traceback files: {failing_unittest}")
        (repo / "test_plain_function.py").write_text("def test_plain():\n    assert True\n", encoding="utf-8")
        no_tests_ran = verification_adapter.run_verification_commands(["python -m unittest test_plain_function.py"], str(repo), execute=True)
        if not no_tests_ran["results"][0]["diagnostics"]["has_no_tests_ran"]:
            raise AssertionError(f"unittest zero-test diagnostic should be explicit: {no_tests_ran}")
        git_diff = verification_adapter.run_verification_commands(["git diff --check"], str(repo), execute=False)
        if git_diff["status"] != "planned" or git_diff["results"][0]["status"] != "planned":
            raise AssertionError(f"git diff --check should be allowlisted as planned: {git_diff}")
        blocked = verification_adapter.run_verification_commands(["rm -rf ."], str(repo), execute=True)
        if blocked["status"] != "blocked" or not blocked["blockers"]:
            raise AssertionError(f"unsafe command should be blocked: {blocked}")
        blocked_trace = verification_adapter.run_verification_commands(
            ["rm -rf ."],
            str(repo),
            execute=True,
            acceptance_requirements=["changed behavior is covered by targeted verification"],
        )
        if blocked_trace["contract_trace"]["rows"][0]["status"] != "blocked":
            raise AssertionError(f"blocked verification should block acceptance trace: {blocked_trace}")
        outside_path = verification_adapter.run_verification_commands(["python -m py_compile ../outside.py"], str(repo), execute=True)
        if outside_path["status"] != "blocked" or "unsafe path token" not in outside_path["results"][0]["stderr"]:
            raise AssertionError(f"allowlisted command with traversal path should be blocked: {outside_path}")
        absolute_path = verification_adapter.run_verification_commands(["pytest /tmp/test_api.py"], str(repo), execute=False)
        if absolute_path["status"] != "blocked" or "unsafe path token" not in absolute_path["results"][0]["stderr"]:
            raise AssertionError(f"allowlisted command with absolute path should be blocked: {absolute_path}")
        option_path = verification_adapter.run_verification_commands(["pytest --rootdir=/tmp"], str(repo), execute=False)
        if option_path["status"] != "blocked" or "unsafe path token" not in option_path["results"][0]["stderr"]:
            raise AssertionError(f"allowlisted command with absolute option path should be blocked: {option_path}")
        home_path = verification_adapter.run_verification_commands(["python -m py_compile ~/.ssh/config"], str(repo), execute=False)
        if home_path["status"] != "blocked" or "unsafe path token" not in home_path["results"][0]["stderr"]:
            raise AssertionError(f"allowlisted command with home-relative path should be blocked: {home_path}")
        home_option_path = verification_adapter.run_verification_commands(["pytest --rootdir=~/project"], str(repo), execute=False)
        if home_option_path["status"] != "blocked" or "unsafe path token" not in home_option_path["results"][0]["stderr"]:
            raise AssertionError(f"allowlisted command with home-relative option path should be blocked: {home_option_path}")
        pytest_unavailable = verification_adapter.run_verification_commands(
            ["python -m py_compile ok.py", "python -m pytest test_missing.py"],
            str(repo),
            execute=True,
        )
        pytest_result = pytest_unavailable["results"][1]
        if pytest_result["status"] not in {"skipped", "failed"}:
            raise AssertionError(f"pytest availability fallback should produce a clear status: {pytest_unavailable}")
        if pytest_result["status"] == "skipped" and pytest_unavailable["status"] != "passed":
            raise AssertionError(f"skipped unavailable pytest should not fail when another verification passed: {pytest_unavailable}")
        skipped_trace = verification_adapter.run_verification_commands(
            ["python -m pytest test_missing.py"],
            str(repo),
            execute=True,
            acceptance_requirements=["pytest behavior evidence is required"],
        )
        if skipped_trace["contract_trace"]["rows"][0]["status"] not in {"skipped", "failed"}:
            raise AssertionError(f"skipped or failed pytest should not prove acceptance: {skipped_trace}")
    print("[ok] Ceraxia CodeBrigade verification adapter")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
