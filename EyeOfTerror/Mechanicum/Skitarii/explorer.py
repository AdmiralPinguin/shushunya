"""Explorer — the head's recon pass before the fighter touches anything.

Given the goal and the loaded project slice, it works out WHERE the change goes:
which files to edit, which to respect, which tests exist, what invariants must not
break, and the risks. A compact JSON that steers the fighter — so it doesn't grep
blindly or rewrite the wrong file. Runs on the planner (non-coder) model.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any


def _chat(prompt: str, max_tokens: int = 900) -> str:
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
    with urllib.request.urlopen(req, timeout=240) as resp:
        return str(((json.loads(resp.read()).get("choices") or [{}])[0].get("message") or {}).get("content") or "")


def _extract_json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


def explore(goal: str, workspace: dict[str, str] | None) -> dict[str, Any]:
    """Return {target_files, related_files, tests, invariants, risks}. Empty-ish for a
    greenfield task (no workspace) — the fighter just builds from scratch."""
    if not workspace:
        return {"target_files": [], "related_files": [], "tests": [], "invariants": [], "risks": []}
    # keep the prompt bounded: list files + short heads
    listing = []
    for path, content in list(workspace.items())[:40]:
        head = "\n".join(content.splitlines()[:25])
        listing.append(f"### {path}\n{head}")
    prompt = (
        "You are the recon head of a coding warband. Given the TASK and the loaded project files, "
        "work out where the change goes. Return ONE strict JSON object and nothing else:\n"
        '{"target_files": ["files to edit"], "related_files": ["files to read/respect but likely not edit"], '
        '"tests": ["existing test files that cover this"], "invariants": ["behaviours that must NOT break"], '
        '"risks": ["what could go wrong"]}\n'
        "Only use paths that appear in the files below. Be concise.\n\n"
        f"TASK:\n{goal}\n\nPROJECT FILES:\n" + "\n\n".join(listing)
    )
    try:
        parsed = _extract_json(_chat(prompt))
    except Exception:
        parsed = {}
    known = set(workspace.keys())
    def _keep(xs: Any) -> list[str]:
        return [str(x) for x in xs if isinstance(x, str)][:12] if isinstance(xs, list) else []
    return {
        "target_files": [f for f in _keep(parsed.get("target_files")) if f in known] or _keep(parsed.get("target_files")),
        "related_files": _keep(parsed.get("related_files")),
        "tests": _keep(parsed.get("tests")),
        "invariants": _keep(parsed.get("invariants")),
        "risks": _keep(parsed.get("risks")),
    }


def brief_for_fighter(exp: dict[str, Any]) -> str:
    """Render the exploration as a short brief appended to the fighter's goal."""
    if not any(exp.get(k) for k in ("target_files", "invariants", "tests")):
        return ""
    parts = ["\n\n--- Recon (Explorer) ---"]
    if exp.get("target_files"):
        parts.append("Скорее всего править эти файлы: " + ", ".join(exp["target_files"]))
    if exp.get("related_files"):
        parts.append("Учесть (не обязательно менять): " + ", ".join(exp["related_files"]))
    if exp.get("tests"):
        parts.append("Существующие тесты (не ломай, прогони их): " + ", ".join(exp["tests"]))
    if exp.get("invariants"):
        parts.append("НЕ сломать: " + "; ".join(exp["invariants"]))
    if exp.get("risks"):
        parts.append("Риски: " + "; ".join(exp["risks"]))
    return "\n".join(parts)
