#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from pathlib import Path

import verification_adapter


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "ok.py").write_text("VALUE = 1\n", encoding="utf-8")
        planned = verification_adapter.run_verification_commands(["python -m py_compile ok.py"], str(repo), execute=False)
        if planned["status"] != "passed" or planned["results"][0]["status"] != "planned":
            raise AssertionError(f"planned verification should pass as planned: {planned}")
        executed = verification_adapter.run_verification_commands(["python -m py_compile ok.py"], str(repo), execute=True)
        if executed["status"] != "passed" or executed["results"][0]["returncode"] != 0:
            raise AssertionError(f"py_compile should execute successfully: {executed}")
        blocked = verification_adapter.run_verification_commands(["rm -rf ."], str(repo), execute=True)
        if blocked["status"] != "blocked" or not blocked["blockers"]:
            raise AssertionError(f"unsafe command should be blocked: {blocked}")
    print("[ok] Ceraxia CodeBrigade verification adapter")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
