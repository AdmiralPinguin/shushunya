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
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "ok.py").write_text("VALUE = 1\n", encoding="utf-8")
        planned = verification_adapter.run_verification_commands(["python -m py_compile ok.py"], str(repo), execute=False)
        if planned["status"] != "planned" or planned["results"][0]["status"] != "planned":
            raise AssertionError(f"planned verification should pass as planned: {planned}")
        executed = verification_adapter.run_verification_commands(["python -m py_compile ok.py"], str(repo), execute=True)
        if executed["status"] != "passed" or executed["results"][0]["returncode"] != 0:
            raise AssertionError(f"py_compile should execute successfully: {executed}")
        git_diff = verification_adapter.run_verification_commands(["git diff --check"], str(repo), execute=False)
        if git_diff["status"] != "planned" or git_diff["results"][0]["status"] != "planned":
            raise AssertionError(f"git diff --check should be allowlisted as planned: {git_diff}")
        blocked = verification_adapter.run_verification_commands(["rm -rf ."], str(repo), execute=True)
        if blocked["status"] != "blocked" or not blocked["blockers"]:
            raise AssertionError(f"unsafe command should be blocked: {blocked}")
        outside_path = verification_adapter.run_verification_commands(["python -m py_compile ../outside.py"], str(repo), execute=True)
        if outside_path["status"] != "blocked" or "unsafe path token" not in outside_path["results"][0]["stderr"]:
            raise AssertionError(f"allowlisted command with traversal path should be blocked: {outside_path}")
        absolute_path = verification_adapter.run_verification_commands(["pytest /tmp/test_api.py"], str(repo), execute=False)
        if absolute_path["status"] != "blocked" or "unsafe path token" not in absolute_path["results"][0]["stderr"]:
            raise AssertionError(f"allowlisted command with absolute path should be blocked: {absolute_path}")
        option_path = verification_adapter.run_verification_commands(["pytest --rootdir=/tmp"], str(repo), execute=False)
        if option_path["status"] != "blocked" or "unsafe path token" not in option_path["results"][0]["stderr"]:
            raise AssertionError(f"allowlisted command with absolute option path should be blocked: {option_path}")
    print("[ok] Ceraxia CodeBrigade verification adapter")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
