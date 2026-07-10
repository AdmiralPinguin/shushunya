"""Reviewer — the independent second head. It gets ONLY the goal, the diff and the
test results — never the fighter's own success claim — and hunts for regressions and
missing checks. If it finds problems, they go back to the fighter as review notes.
This is the anti-confabulation layer on top of the acceptor's factual re-run.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any


def _chat(prompt: str, max_tokens: int = 700) -> str:
    base = os.environ.get("PLANNER_LLM_BASE_URL", "http://127.0.0.1:8079/v1").rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    payload = {
        "model": os.environ.get("REVIEWER_LLM_MODEL", os.environ.get("PLANNER_LLM_MODEL",
                                 "gemma-4-12b-it-UD-Q5_K_XL.gguf")),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(f"{base}/chat/completions",
                                 data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=240) as resp:
        return str(((json.loads(resp.read()).get("choices") or [{}])[0].get("message") or {}).get("content") or "")


def _extract_json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


def review(goal: str, unified_diff: str, acceptance: dict[str, Any],
           invariants: list[str] | None = None) -> dict[str, Any]:
    """Return {approved: bool, issues: [str]}. Approves only if the diff plausibly does
    the task without regressions or weakened tests. Empty diff / no change → not approved."""
    if not (unified_diff or "").strip():
        return {"approved": False, "issues": ["Изменений нет — задача не выполнена."]}
    results = acceptance.get("results", []) if isinstance(acceptance, dict) else []
    fails = [r for r in results if not r.get("ok")]
    inv = "\n".join(f"- {i}" for i in (invariants or [])) or "(не заданы)"
    prompt = (
        "You are an independent code reviewer. You do NOT trust the author. Given the TASK, the unified "
        "diff, the invariants that must hold, and the acceptance results, decide if the change is correct "
        "and safe. Look for: wrong logic, regressions, removed/weakened tests, unrelated edits, missing "
        "edge cases. Return ONE strict JSON object: "
        '{"approved": true|false, "issues": ["concrete problem the author must fix"]}\n'
        "Approve only if the diff actually does the task and breaks nothing. If acceptance has failures, "
        "approved must be false.\n\n"
        f"TASK:\n{goal}\n\nINVARIANTS:\n{inv}\n\n"
        f"ACCEPTANCE FAILURES: {json.dumps(fails, ensure_ascii=False)[:1500]}\n\n"
        f"UNIFIED DIFF:\n{unified_diff[:12000]}"
    )
    try:
        parsed = _extract_json(_chat(prompt))
    except Exception:
        parsed = {}
    issues = [str(x) for x in (parsed.get("issues") or []) if isinstance(x, str)][:8]
    approved = bool(parsed.get("approved")) and not fails
    if not approved and not issues:
        issues = ["Ревьюер не одобрил (проверки не прошли или изменение сомнительно)."]
    return {"approved": approved, "issues": issues}
