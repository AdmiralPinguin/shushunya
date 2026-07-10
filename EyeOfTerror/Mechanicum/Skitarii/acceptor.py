"""Priyomshchik: independent acceptance. Re-runs the spec's checks itself and
judges by real execution — not by reading anyone's report. Anti-confabulation gate.

A check is a structured dict from spec.build_spec:
  {"cmd": ...}                    -> pass iff exit code 0
  {"cmd": ..., "expect_stdout": s} -> pass iff trimmed stdout == s
  {"cmd": ..., "oracle": ...}      -> pass iff cmd stdout == oracle stdout (both run for real)
The comparison command is assembled HERE in code, so quoting/substitution is always
correct — the model never hand-writes shell.
"""
from __future__ import annotations

import re
from typing import Any


def _norm(s: str) -> str:
    return "\n".join(line.rstrip() for line in (s or "").strip().splitlines()).strip()


def run_check(executor: Any, check: dict[str, Any]) -> dict[str, Any]:
    cmd = str(check.get("cmd") or "")
    got = executor.bash(cmd, timeout=180)
    record = {"kind": "check", "target": cmd, "exit": got["returncode"],
              "stdout": (got["stdout"] or "")[-400:], "stderr": (got["stderr"] or "")[-400:]}
    if got["returncode"] != 0:
        return {**record, "ok": False, "why": f"exit {got['returncode']}: {(got['stderr'] or '')[-200:]}"}
    if "oracle" in check:
        ora = executor.bash(str(check["oracle"]), timeout=180)
        if ora["returncode"] != 0:
            return {**record, "ok": False, "why": f"oracle failed: {(ora['stderr'] or '')[-200:]}"}
        ok = _norm(got["stdout"]) == _norm(ora["stdout"])
        return {**record, "ok": ok, "expected": _norm(ora["stdout"])[:200],
                "why": "" if ok else f"got {_norm(got['stdout'])[:80]!r} != oracle {_norm(ora['stdout'])[:80]!r}"}
    if "expect_stdout" in check:
        ok = _norm(got["stdout"]) == _norm(str(check["expect_stdout"]))
        return {**record, "ok": ok, "expected": str(check["expect_stdout"])[:200],
                "why": "" if ok else f"got {_norm(got['stdout'])[:80]!r} != expected {str(check['expect_stdout'])[:80]!r}"}
    return {**record, "ok": True}


_COMPILE_RE = re.compile(r"py_compile|php\s+-l|node\s+--check|tsc\s|bash\s+-n|-fsyntax-only|gofmt|javac\b")
_TEST_RE = re.compile(r"pytest|unittest|\bnose\b|jest|mocha|go\s+test|phpunit|\brspec\b|(^|/)test_|_test\.")


def check_kind(check: dict[str, Any]) -> str:
    """behavior = verifies output (expect_stdout/oracle); test = runs a test runner;
    compile = syntax/compile only; run = bare command (weakly behavioural)."""
    if "expect_stdout" in check or "oracle" in check:
        return "behavior"
    cmd = str(check.get("cmd") or "")
    if _TEST_RE.search(cmd):
        return "test"
    if _COMPILE_RE.search(cmd):
        return "compile"
    return "run"


def accept(executor: Any, deliverables: list[str], checks: list[dict[str, Any]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for path in deliverables:
        r = executor.bash(f"test -s {path!r}")
        exists = r["returncode"] == 0
        results.append({"kind": "deliverable", "target": path, "ok": exists})
    for check in checks:
        res = run_check(executor, check)
        res["check_kind"] = check_kind(check)
        results.append(res)
    # A mission that proved NOTHING must not be accepted.
    if not checks:
        return {"accepted": False, "results": results,
                "reason": "no executable success checks were produced — cannot confirm the work is correct"}
    # STRUCTURAL gate (not just a prompt hint): compile/syntax alone can't catch wrong
    # logic. Require at least one BEHAVIOURAL or TEST check, else BLOCKED.
    kinds = {check_kind(c) for c in checks}
    if not (kinds & {"behavior", "test"}):
        return {"accepted": False, "results": results,
                "reason": "checks are compile/run-only — no behavioural or functional test, "
                          "so wrong logic could pass. Add an expect_stdout/oracle or a test run."}
    ok = bool(results) and all(r["ok"] for r in results)
    return {"accepted": ok, "results": results}
