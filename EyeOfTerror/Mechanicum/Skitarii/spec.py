"""Postanovshchik: turns the user's goal into executable success checks.

One small LLM call. The checks are shell commands whose exit code 0 means pass —
they are the brigade's source of truth instead of paper review.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any


def _chat_json(prompt: str) -> dict[str, Any]:
    base = os.environ.get("SPEC_LLM_BASE_URL", "http://127.0.0.1:8081/v1").rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    payload = {
        "model": os.environ.get("SPEC_LLM_MODEL", "Qwen3-Coder-Next-Q6_K-00001-of-00004.gguf"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 900,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(f"{base}/chat/completions",
                                 data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as resp:
        content = str(((json.loads(resp.read()).get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    match = re.search(r"\{.*\}", content, re.DOTALL)
    return json.loads(match.group(0)) if match else {}


def build_spec(goal: str) -> dict[str, Any]:
    """Returns {"deliverables": [...], "checks": [...]}.

    A check is a STRUCTURED dict (never a hand-written shell one-liner — the model
    mangles quoting/substitution). The acceptor turns it into a correct command:
      {"cmd": "..."}                          -> pass iff exit code 0
      {"cmd": "...", "expect_stdout": "14"}   -> pass iff trimmed stdout == "14"
      {"cmd": "...", "oracle": "python3 -c 'print(3*(5+2)-8/4)'"}
                                              -> pass iff stdout == oracle's stdout
    """
    prompt = (
        "You prepare acceptance for a coding task. Return ONE strict JSON object and nothing else:\n"
        '{"deliverables": ["relative file paths the task must produce"],\n'
        ' "checks": [ {"cmd": "command that runs the program"},\n'
        '             {"cmd": "...", "expect_stdout": "literal expected output"},\n'
        '             {"cmd": "...", "oracle": "command that computes the correct answer"} ]}\n'
        "Rules for checks:\n"
        "- Each check is a JSON object, NOT a shell string. Do not write pipes/grep/test yourself.\n"
        "- 'cmd' is a single command that runs the deliverable (e.g. \"python3 calc.py '2+3*4'\").\n"
        "- NEVER put an answer you computed in your head into expect_stdout — you make arithmetic mistakes.\n"
        "  Use expect_stdout ONLY for values written literally in the task text.\n"
        "- For anything you'd have to compute, use 'oracle': a command (python3 -c ... / node -e ...) that\n"
        "  produces the correct answer, so the real interpreter is the source of truth, not you.\n"
        "- A bare {\"cmd\": ...} (no expect/oracle) passes on exit code 0 — use it ONLY for 'does it run / "
        "compile'. A compile/syntax check ALONE is NOT acceptance: wrong logic would pass it.\n"
        "- REQUIRED: include at least one BEHAVIOURAL check that verifies the program does the right thing "
        "(expect_stdout or oracle on real inputs, or a command that runs the project's tests). Cover the main "
        "case and at least one edge case. Never rely on compile-only.\n"
        "Keep 2-6 checks, runnable on bare Ubuntu with php, python3, node.\n\n"
        f"TASK:\n{goal}"
    )
    try:
        spec = _chat_json(prompt)
    except Exception:
        spec = {}
    deliverables = [str(d) for d in (spec.get("deliverables") or []) if isinstance(d, str)]
    checks: list[dict[str, Any]] = []
    for c in (spec.get("checks") or []):
        if isinstance(c, dict) and str(c.get("cmd") or "").strip():
            check = {"cmd": str(c["cmd"])}
            if isinstance(c.get("expect_stdout"), (str, int, float)):
                check["expect_stdout"] = str(c["expect_stdout"])
            if str(c.get("oracle") or "").strip():
                check["oracle"] = str(c["oracle"])
            checks.append(check)
        elif isinstance(c, str) and c.strip():
            checks.append({"cmd": c})  # tolerate a bare string as a plain exit-0 check
    # Fallback: the model gave files but no checks. Never return an empty check set
    # (that would let a mission be "accepted" having proven nothing). Synthesize a
    # minimum real check per deliverable — that it at least parses/compiles.
    if not checks and deliverables:
        _syntax = {".py": "python3 -m py_compile {p}", ".php": "php -l {p}",
                   ".js": "node --check {p}", ".sh": "bash -n {p}"}
        for d in deliverables:
            ext = "." + d.rsplit(".", 1)[-1] if "." in d else ""
            if ext in _syntax:
                checks.append({"cmd": _syntax[ext].format(p=d)})
    return {"deliverables": deliverables, "checks": checks}
