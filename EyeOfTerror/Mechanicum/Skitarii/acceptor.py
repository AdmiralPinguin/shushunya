"""Priyomshchik: independent acceptance. Re-runs the spec's checks itself and
judges by real execution — not by reading anyone's report. Anti-confabulation gate.

A check is a structured dict from spec.build_spec:
  {"cmd": ...}                    -> pass iff exit code 0
  {"cmd": ..., "expect_stdout": s} -> pass iff trimmed stdout == s
  {"cmd": ..., "oracle": ...}      -> pass iff cmd stdout == oracle stdout (both run for real)
  {"kind": "file_bytes", "path": p, "expect_bytes": s}
                                      -> pass iff frozen artifact bytes equal UTF-8(s)
The comparison command is assembled HERE in code, so quoting/substitution is always
correct — the model never hand-writes shell.
"""
from __future__ import annotations

import base64
import re
from typing import Any


def _norm(s: str) -> str:
    return "\n".join(line.rstrip() for line in (s or "").strip().splitlines()).strip()


def run_check(executor: Any, check: dict[str, Any]) -> dict[str, Any]:
    if check.get("kind") == "file_bytes":
        path = str(check.get("path") or "")
        expected_value = check.get("expect_bytes")
        if not path or not isinstance(expected_value, str):
            return {
                "kind": "file_bytes", "target": path, "exit": 2,
                "stdout": "", "stderr": "invalid declarative file-bytes check",
                "ok": False, "why": "invalid declarative file-bytes check",
            }
        # The model supplies only inert data, and the path was already accepted by
        # spec's goal-linked positive grammar.  Read at most expected+1 bytes from
        # one no-follow regular-file fd so equality remains exact and bounded.
        expected_bytes = expected_value.encode("utf-8")
        reader = getattr(executor, "read_regular_artifact", None)
        if not callable(reader):
            return {
                "kind": "file_bytes", "target": path, "exit": 127,
                "stdout": "", "stderr": "atomic regular-file reader unavailable",
                "ok": False, "why": "atomic regular-file reader unavailable",
            }
        try:
            actual_bytes = reader(path, len(expected_bytes))
        except (FileNotFoundError, IsADirectoryError, ValueError) as exc:
            return {
                "kind": "file_bytes", "target": path, "exit": 1, "stdout": "",
                "stderr": str(exc)[-400:], "ok": False,
                "why": "artifact is missing, non-regular, or a symlink",
            }
        except OSError as exc:
            return {
                "kind": "file_bytes", "target": path, "exit": 255,
                "stdout": "", "stderr": str(exc)[-400:], "ok": False,
                "why": f"atomic frozen artifact read failed: {str(exc)[-200:]}",
            }
        if not isinstance(actual_bytes, bytes) or len(actual_bytes) > len(expected_bytes) + 1:
            return {
                "kind": "file_bytes", "target": path, "exit": 255,
                "stdout": "", "stderr": "atomic reader violated its byte contract",
                "ok": False, "why": "atomic reader violated its byte contract",
            }
        # Base64 is record-safe and keeps CRLF/trailing-newline evidence visible
        # without applying the normalization used by stdout checks.
        actual = base64.b64encode(actual_bytes).decode("ascii")
        expected = base64.b64encode(expected_bytes).decode("ascii")
        record = {
            "kind": "file_bytes", "target": path, "exit": 0,
            "stdout": actual[-400:], "stderr": "",
        }
        ok = actual_bytes == expected_bytes
        return {
            **record, "ok": ok, "expected": expected[:200],
            "why": "" if ok else "file bytes differ from the frozen expected value",
        }

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
    if check.get("kind") == "file_bytes":
        return "behavior"
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
