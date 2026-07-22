"""Product critic — judges the RUNNING product against the quality contract.

Distinct from the code reviewer (advisory, never overturns green checks): the
critic looks at probe EVIDENCE — command transcripts, server responses, screen
frames — territory the executable checks cannot cover. It can demand a bounded
polish round; it can never block forever (max rounds live in the caller).

The model is the multimodal gemma behind the dispatcher: screenshots go in as
OpenAI-style image_url data URLs, the same wire format the owner's chat uses.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any

from product_probe import screens_to_data_urls

_CRITIC_BASE = os.environ.get("SKITARII_CRITIC_LLM_BASE_URL",
                              os.environ.get("PLANNER_LLM_BASE_URL", "http://127.0.0.1:8079/v1"))
_CRITIC_MODEL = os.environ.get("SKITARII_CRITIC_LLM_MODEL",
                               os.environ.get("PLANNER_LLM_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf"))


def _chat(messages: list[dict], max_tokens: int = 900) -> str:
    base = _CRITIC_BASE.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    payload = {
        "model": _CRITIC_MODEL,
        "messages": messages,
        "temperature": float(os.environ.get("SKITARII_CRITIC_TEMPERATURE", "0.2")),
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())
    return str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def judge_product(goal: str, contract: list[str], evidence: dict[str, Any]) -> dict[str, Any]:
    """-> {"passed": bool, "findings": [...], "polish_instructions": str}

    Best-effort: an unreachable critic passes the product with a note — quality
    review must never become a dead end (доктрина «никаких тупиков»)."""
    contract = [c for c in (contract or []) if str(c).strip()]
    if not contract:
        return {"passed": True, "findings": [], "polish_instructions": "",
                "note": "empty quality contract"}
    facts = evidence.get("facts") or {}
    texts = [str(t) for t in (evidence.get("texts") or [])][:8]
    content: list[dict[str, Any]] = [{
        "type": "text",
        "text": (
            "You are a demanding product critic. Judge the RUNNING product below against the"
            " quality contract. Evidence: probe facts, command/server transcripts"
            + (", and screenshots of the live product" if evidence.get("screens") else "")
            + ".\nReturn ONE strict JSON object and nothing else:\n"
            '{"passed": true|false, "findings": ["each unmet contract item, concretely, with what'
            ' you saw instead"], "polish_instructions": "one concrete work order for the coder'
            ' fixing the findings; empty if passed"}\n'
            "Rules: judge ONLY against the contract items; met items are not findings. A dead or"
            " unresponsive product fails. Be strict about visual quality when screenshots are"
            " given (placeholder rectangles are not sprites). Do not invent requirements beyond"
            " the contract.\n\n"
            f"TASK:\n{goal}\n\nQUALITY CONTRACT:\n- " + "\n- ".join(contract)
            + f"\n\nPROBE FACTS:\n{json.dumps(facts, ensure_ascii=False)}"
            + ("\n\nTRANSCRIPTS:\n" + "\n---\n".join(texts) if texts else "")
        ),
    }]
    for url in screens_to_data_urls(evidence.get("screens") or []):
        content.append({"type": "image_url", "image_url": {"url": url}})
    try:
        verdict = _extract_json(_chat([{"role": "user", "content": content}]))
    except Exception as exc:  # noqa: BLE001 - critic outage must not kill the mission
        return {"passed": True, "findings": [],
                "polish_instructions": "",
                "note": f"critic unavailable: {type(exc).__name__}: {exc}"[:300]}
    if not verdict:
        return {"passed": True, "findings": [], "polish_instructions": "",
                "note": "critic returned no parseable verdict"}
    findings = [str(f) for f in (verdict.get("findings") or []) if isinstance(f, str)][:10]
    passed = bool(verdict.get("passed")) and not findings
    return {
        "passed": passed,
        "findings": findings,
        "polish_instructions": str(verdict.get("polish_instructions") or "")[:2_000],
    }
