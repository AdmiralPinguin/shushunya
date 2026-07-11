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


_CODE_SUFFIX = ("py", "php", "js", "ts", "go", "java", "rb", "rs", "c", "h", "cpp", "sh", "json")


def _full_working_copy(
    executor: Any, workspace: dict[str, str], inventory: list[str] | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Enumerate the ACTUAL working copy in the sandbox (not just the caller's preloaded
    slice), so recon reasons over the whole tree. Returns (all_files, heads) where heads
    holds file contents for the preloaded files plus a bounded number of newly-found ones."""
    all_files: list[str] = [str(path) for path in (inventory or [])]
    heads = dict(workspace)
    if executor is None:
        return sorted(heads.keys()), heads
    try:
        out = executor.bash("find . -type f -not -path './.git/*' -not -path './.bg/*' 2>/dev/null | sort",
                            timeout=30).get("stdout") or ""
    except Exception:  # noqa: BLE001
        return sorted(heads.keys()), heads
    for line in out.splitlines():
        rel = line[2:] if line.startswith("./") else line.strip()
        if rel:
            all_files.append(rel)
    # read heads for code files we don't already have content for, bounded to keep it fast
    budget = 40 - len(heads)
    for rel in all_files:
        if budget <= 0:
            break
        if rel in heads or rel.rsplit(".", 1)[-1] not in _CODE_SUFFIX:
            continue
        try:
            heads[rel] = executor.read_file(rel, max_bytes=4000)
            budget -= 1
        except Exception:  # noqa: BLE001
            pass
    return (sorted(set(all_files) | set(heads.keys())), heads)


def explore(
    goal: str,
    workspace: dict[str, str] | None,
    executor: Any = None,
    *,
    inventory: list[str] | None = None,
) -> dict[str, Any]:
    """Return {target_files, related_files, tests, invariants, risks}. Empty-ish for a
    greenfield task (no workspace) — the fighter just builds from scratch. When an
    executor is given, recon sees the FULL working copy in the sandbox, not just the
    caller's preloaded slice."""
    if not workspace and not inventory and executor is None:
        return {"target_files": [], "related_files": [], "tests": [], "invariants": [], "risks": []}
    all_files, heads = _full_working_copy(executor, workspace or {}, inventory)
    if not heads and not all_files:
        return {"target_files": [], "related_files": [], "tests": [], "invariants": [], "risks": []}
    # Give recon the complete inventory. Contents stay bounded, but silently hiding the
    # 401st path made target selection depend on alphabetical luck in larger repos.
    goal_terms = {t.lower() for t in re.findall(r"[\w.-]{3,}", goal)}
    ranked_heads = sorted(
        heads.items(),
        key=lambda item: (-sum(term in item[0].lower() for term in goal_terms), item[0]),
    )
    listing = []
    for path, content in ranked_heads[:40]:
        head = "\n".join((content or "").splitlines()[:25])
        listing.append(f"### {path}\n{head}")
    tree = "\n".join(all_files)
    prompt = (
        "You are the recon head of a coding warband. Given the TASK, the full file tree of the "
        "working copy, and the heads of the key files, work out where the change goes. Return ONE "
        "strict JSON object and nothing else:\n"
        '{"target_files": ["files to edit"], "related_files": ["files to read/respect but likely not edit"], '
        '"tests": ["existing test files that cover this"], "invariants": ["behaviours that must NOT break"], '
        '"risks": ["what could go wrong"]}\n'
        "Only use paths that appear in the tree below. Be concise.\n\n"
        f"TASK:\n{goal}\n\nFILE TREE:\n{tree}\n\nKEY FILE HEADS:\n" + "\n\n".join(listing)
    )
    try:
        parsed = _extract_json(_chat(prompt))
    except Exception:
        parsed = {}
    known = set(all_files) | set(heads.keys())
    def _keep(xs: Any) -> list[str]:
        return [str(x) for x in xs if isinstance(x, str)][:12] if isinstance(xs, list) else []
    return {
        "target_files": [f for f in _keep(parsed.get("target_files")) if f in known],
        "related_files": [f for f in _keep(parsed.get("related_files")) if f in known],
        "tests": [f for f in _keep(parsed.get("tests")) if f in known],
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
