"""Pre-flight ambiguity gate.

The eval proved the warband grinds on hopelessly vague goals ("add caching", "make it
faster") instead of asking — a real gap vs Claude/Codex. This gate runs ONCE before the
fighter starts: if the goal names no concrete deliverable and no checkable behaviour, it
returns the single most useful clarifying question. It is deliberately CONSERVATIVE — a
terse-but-clear task ("write fizzbuzz", "fix add() in calc.py") must pass through, or we
would nag on real work. Preloaded workspace files count as strong evidence the task is
grounded.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

_PROMPT = (
    "You triage a coding task BEFORE work starts. Decide if it is specific enough to "
    "implement AND verify, or too vague — then you must ask ONE clarifying question.\n"
    "READY if it names a concrete deliverable OR a checkable behaviour: a file to write, "
    "a function/bug to fix, an input→output, a test to pass. Terse is fine — 'write "
    "fizzbuzz', 'fix add() in calc.py', 'reverse the argument' are all READY.\n"
    "UNDERSPECIFIED only if you genuinely cannot tell WHAT to build or HOW anyone would "
    "check it — no object, no target, no observable behaviour (e.g. 'add caching', 'make "
    "it faster', 'export the data', 'tidy up the config', 'fix authorization' with no "
    "code given).\n"
    "When in doubt, prefer READY — do not nag on tasks a competent engineer could start.\n"
    'Return ONE strict JSON and nothing else: {"ready": true} OR '
    '{"ready": false, "question": "the single most important question"}.\n\n'
    "TASK:\n{goal}"
)


def _chat(prompt: str, max_tokens: int = 200) -> str:
    base = os.environ.get("PLANNER_LLM_BASE_URL", "http://127.0.0.1:8079/v1").rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    payload = {
        "model": os.environ.get("PLANNER_LLM_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(f"{base}/chat/completions",
                                 data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return str(((json.loads(resp.read()).get("choices") or [{}])[0].get("message") or {}).get("content") or "")


def needs_clarification(goal: str, has_workspace: bool = False) -> str:
    """Return the clarifying question if the goal is too vague to start, else "".
    Fails OPEN (returns "") on any error — never block real work on a flaky gate."""
    goal = (goal or "").strip()
    if not goal:
        return "Что именно нужно сделать? Задача пустая."
    # grounded by real files, or long/detailed enough → skip the LLM call entirely
    if has_workspace or len(goal) > 400:
        return ""
    try:
        # NB: replace, not .format — the prompt contains literal { } JSON braces
        m = re.search(r"\{.*\}", _chat(_PROMPT.replace("{goal}", goal)), re.DOTALL)
        parsed = json.loads(m.group(0)) if m else {}
    except Exception:
        return ""
    if parsed.get("ready") is False:
        q = str(parsed.get("question") or "").strip()
        return q or "Задача слишком общая. Уточни, что конкретно сделать и как проверить результат?"
    return ""
