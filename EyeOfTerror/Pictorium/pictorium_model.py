from __future__ import annotations

import json
import re
from typing import Any

from EyeOfTerror.model_brain import model_contract, request_model_decision


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1)
    elif "{" in cleaned and "}" in cleaned:
        cleaned = cleaned[cleaned.find("{") : cleaned.rfind("}") + 1]
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("model brain response must be a JSON object")
    return parsed


def pictorium_model_contract(worker: str, role: str) -> dict[str, Any]:
    contract = model_contract(worker, role, layer="pictorium_worker")
    contract["response_shape"] = {
        "type": "object",
        "required": ["decision", "confidence", "risks"],
        "properties": {
            "decision": "role-scoped structured decision for this worker",
            "confidence": "low|medium|high",
            "risks": "list of blockers or quality risks",
        },
    }
    return contract


def request_pictorium_model_guidance(
    worker: str,
    role: str,
    payload: dict[str, Any],
    *,
    instructions: str,
) -> dict[str, Any]:
    embedded = payload.get("model_brain") if isinstance(payload.get("model_brain"), dict) else {}
    if embedded.get("ok") and str(embedded.get("content") or "").strip():
        raw_decision = dict(embedded)
        raw_decision.setdefault("source", "request.model_brain")
    else:
        raw_decision = request_model_decision(
            worker,
            role,
            payload,
            layer="pictorium_worker",
            instructions=(
                f"{instructions}\n"
                "Return only one JSON object. Include at least: decision, confidence, risks. "
                "Do not return prose outside JSON."
            ),
        )
    guidance = {
        "kind": "pictorium_worker_model_guidance",
        "contract_version": 1,
        "worker": worker,
        "role": role,
        "required": True,
        "ok": False,
        "status": str(raw_decision.get("status") or "unknown"),
        "decision": {},
        "parse_error": "",
        "raw_model_brain": raw_decision,
    }
    if raw_decision.get("status") != "answered":
        guidance["status"] = "unavailable"
        guidance["parse_error"] = str(raw_decision.get("error") or "model brain did not answer")
        return guidance
    try:
        decision = _extract_json_object(str(raw_decision.get("content") or ""))
    except (json.JSONDecodeError, ValueError) as exc:
        guidance["status"] = "invalid_json"
        guidance["parse_error"] = str(exc)
        return guidance
    guidance["ok"] = True
    guidance["status"] = "answered"
    guidance["decision"] = decision
    return guidance


def model_guidance_blockers(guidance: dict[str, Any], *, target_worker: str, target_step: str) -> list[dict[str, Any]]:
    if guidance.get("ok"):
        return []
    return [
        {
            "code": "model_brain_unavailable",
            "severity": "blocking",
            "message": str(guidance.get("parse_error") or guidance.get("status") or "model brain unavailable"),
            "target_worker": target_worker,
            "target_step": target_step,
            "requested_change": "obtain a live structured model-brain JSON answer before executing this worker",
        }
    ]


def attach_model_guidance(payload: dict[str, Any], guidance: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["model_guidance"] = guidance
    enriched["model_brain"] = guidance.get("raw_model_brain", {})
    return enriched
