from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from EyeOfTerror.model_brain import request_model_decision  # noqa: E402


def _runtime_defaults(role: str) -> dict[str, str]:
    if role == "ScriptoriumDaemon":
        return {
            "EYE_MODEL_TIMEOUT_SEC": "240",
            "EYE_MODEL_MAX_TOKENS": "4096",
            "EYE_MODEL_MAX_CONTEXT_CHARS": "60000",
        }
    if role == "ReductorVerifier":
        return {
            "EYE_MODEL_TIMEOUT_SEC": "180",
            "EYE_MODEL_MAX_TOKENS": "2048",
            "EYE_MODEL_MAX_CONTEXT_CHARS": "60000",
        }
    return {
        "EYE_MODEL_TIMEOUT_SEC": "180",
        "EYE_MODEL_MAX_TOKENS": "1024",
        "EYE_MODEL_MAX_CONTEXT_CHARS": "30000",
    }


def request_scriptorium_model_guidance(role: str, payload: dict[str, Any], instructions: str) -> dict[str, Any]:
    runtime_defaults = _runtime_defaults(role)
    previous_values = {key: os.environ.get(key) for key in runtime_defaults}
    for key, value in runtime_defaults.items():
        if previous_values[key] is None:
            os.environ[key] = value
    try:
        return request_model_decision(
            "Scriptorium",
            role,
            payload,
            layer="scriptorium_worker",
            instructions=instructions,
        )
    finally:
        for key, previous in previous_values.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def request_required_scriptorium_guidance(
    role: str,
    request: dict[str, Any],
    payload: dict[str, Any],
    instructions: str,
    request_guidance: Any = request_scriptorium_model_guidance,
) -> dict[str, Any]:
    embedded = request.get("model_brain") if isinstance(request.get("model_brain"), dict) else {}
    if embedded.get("ok") and str(embedded.get("content") or "").strip():
        guidance = dict(embedded)
        guidance.setdefault("role", role)
        guidance.setdefault("status", "answered")
        guidance["source"] = "request.model_brain"
        return guidance
    guidance = request_guidance(role, payload, instructions)
    if isinstance(guidance, dict):
        guidance.setdefault("role", role)
        return guidance
    return {
        "ok": False,
        "status": "error",
        "role": role,
        "content": "",
        "error": "model guidance callable returned a non-object result",
    }


def model_unavailable_payload(worker: str, task_id: Any, decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "worker": worker,
        "task_id": task_id,
        "status": "failed",
        "error_code": "model_brain_unavailable",
        "error": str(decision.get("error") or decision.get("status") or "model brain did not answer"),
        "summary": f"{worker} cannot run without a live model-brain answer.",
        "model_guidance": decision,
    }


def research_intent_from_worker_request(request: dict[str, Any]) -> dict[str, Any]:
    expectations = request.get("quality_expectations") if isinstance(request.get("quality_expectations"), dict) else {}
    intent = expectations.get("research_intent") if isinstance(expectations.get("research_intent"), dict) else {}
    if intent:
        return intent
    step_quality = expectations.get("step_quality") if isinstance(expectations.get("step_quality"), dict) else {}
    intent = step_quality.get("research_intent") if isinstance(step_quality.get("research_intent"), dict) else {}
    if intent:
        return intent
    contract = request.get("contract") if isinstance(request.get("contract"), dict) else {}
    gates = contract.get("quality_gates") if isinstance(contract.get("quality_gates"), list) else []
    required_artifacts = contract.get("required_artifacts") if isinstance(contract.get("required_artifacts"), list) else []
    profile: dict[str, Any] = {}
    for gate in gates:
        text = str(gate)
        if text.startswith("intent:"):
            profile["intent"] = text.split(":", 1)[1]
        elif text.startswith("output_mode:"):
            profile["output_mode"] = text.split(":", 1)[1]
    if not profile:
        return {}
    output_mode = str(profile.get("output_mode") or "")
    artifact_text = "\n".join(str(item) for item in required_artifacts)
    chapter_count = sum(1 for item in required_artifacts if "/chapters/" in str(item) and str(item).endswith(".md"))
    profile.setdefault("required_depth", "comprehensive" if profile.get("intent") in {"event_reconstruction", "book"} else "deep")
    profile.setdefault("source_policy", "contract_quality_gates")
    profile["needs_timeline"] = output_mode in {"event_reconstruction", "book_manuscript_with_timeline"} or any(str(item).endswith("/timeline.json") for item in required_artifacts)
    profile["needs_chapters"] = "chapter_plan.json" in artifact_text or chapter_count > 0
    profile["chapter_count"] = chapter_count if profile["needs_chapters"] else 0
    return profile


def parsed_model_content(decision: dict[str, Any]) -> dict[str, Any]:
    content = str(decision.get("content") or "").strip()
    if not content:
        return {}
    candidates = [content]
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if fence_match:
        candidates.insert(0, fence_match.group(1))
    object_match = re.search(r"(\{.*\})", content, re.DOTALL)
    if object_match:
        candidates.append(object_match.group(1))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}
