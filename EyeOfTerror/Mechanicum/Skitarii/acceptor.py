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


def accept(executor: Any, deliverables: list[str], checks: list[dict[str, Any]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for path in deliverables:
        r = executor.bash(f"test -s {path!r}")
        exists = r["returncode"] == 0
        results.append({"kind": "deliverable", "target": path, "ok": exists})
    for check in checks:
        results.append(run_check(executor, check))
    # A mission that proved NOTHING must not be accepted. Require at least one real
    # executable check (a deliverable-exists test alone is too weak — it says the file
    # is there, not that it works). Empty spec => blocked, never a false success.
    if not checks:
        return {"accepted": False, "results": results,
                "reason": "no executable success checks were produced — cannot confirm the work is correct"}
    ok = bool(results) and all(r["ok"] for r in results)
    return {"accepted": ok, "results": results}
