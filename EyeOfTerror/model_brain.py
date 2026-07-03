from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_MODEL = "gemma-4-12b-it-UD-Q5_K_XL.gguf"


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def model_settings() -> dict[str, Any]:
    base_url = (
        os.environ.get("EYE_MODEL_BASE_URL")
        or os.environ.get("ARCHIVE_LLM_BASE_URL")
        or os.environ.get("LLM_BASE_URL")
        or DEFAULT_BASE_URL
    ).rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    return {
        "enabled": True,
        "base_url": base_url,
        "model": os.environ.get("EYE_MODEL_NAME") or os.environ.get("ARCHIVE_DEFAULT_MODEL") or os.environ.get("LLM_MODEL") or DEFAULT_MODEL,
        "timeout_sec": _float_env("EYE_MODEL_TIMEOUT_SEC", 45.0, 1.0, 600.0),
        "max_tokens": _int_env("EYE_MODEL_MAX_TOKENS", 512, 32, 4096),
        "max_context_chars": _int_env("EYE_MODEL_MAX_CONTEXT_CHARS", 12000, 1000, 120000),
    }


def model_contract(owner: str, role: str, *, layer: str = "worker") -> dict[str, Any]:
    settings = model_settings()
    return {
        "kind": "eye_of_terror_model_brain",
        "contract_version": 1,
        "owner": owner,
        "role": role,
        "layer": layer,
        "required_for_autonomous_mode": True,
        "provider": "openai_compatible_chat_completions",
        "mode": "active",
        "base_url": settings["base_url"],
        "model": settings["model"],
        "request_fields": ["task_id", "contract", "step", "input_artifacts", "quality_expectations", "revision_context"],
        "response_field": "model_brain",
        "thinking_mode": "disabled_for_command_outputs",
        "failure_policy": "model_answer_required_for_autonomous_worker_execution",
    }


def compact_json(value: Any, max_chars: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        text = repr(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 32] + "...<truncated>"


def _goal_from_request(request: dict[str, Any]) -> str:
    contract = request.get("contract") if isinstance(request.get("contract"), dict) else {}
    step = request.get("step") if isinstance(request.get("step"), dict) else {}
    for value in (
        request.get("task"),
        request.get("message"),
        contract.get("goal"),
        contract.get("task"),
        step.get("description"),
        step.get("goal"),
        step.get("step_id"),
        request.get("task_id"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "No explicit task text was supplied."


def _messages(owner: str, role: str, request: dict[str, Any], instructions: str, max_context_chars: int) -> list[dict[str, str]]:
    task_id = str(request.get("task_id") or "")
    context = compact_json(request, max_context_chars)
    return [
        {
            "role": "system",
            "content": (
                "You are a concrete EyeOfTerror autonomous agent component. "
                "Work only inside your assigned role. Return concise actionable JSON-like guidance, "
                "not prose, not hidden reasoning."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Owner: {owner}\nRole: {role}\nTask ID: {task_id}\n"
                f"Primary goal: {_goal_from_request(request)}\n"
                f"Role instructions: {instructions}\n"
                "Request context follows as compact JSON. Decide what this component should do next, "
                "what evidence it must use, and what risks/blockers must be recorded.\n"
                f"{context}"
            ),
        },
    ]


def request_model_decision(
    owner: str,
    role: str,
    request: dict[str, Any],
    *,
    layer: str = "worker",
    instructions: str = "Follow the worker contract and produce role-scoped guidance.",
) -> dict[str, Any]:
    settings = model_settings()
    started = time.monotonic()
    contract = model_contract(owner, role, layer=layer)
    payload = {
        "model": settings["model"],
        "messages": _messages(owner, role, request, instructions, int(settings["max_context_chars"])),
        "temperature": 0,
        "max_tokens": int(settings["max_tokens"]),
        "chat_template_kwargs": {"enable_thinking": False},
    }
    url = f"{settings['base_url']}/chat/completions"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    http_request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    api_key = os.environ.get("EYE_MODEL_API_KEY") or os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if api_key:
        http_request.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(http_request, timeout=float(settings["timeout_sec"])) as response:
            raw = response.read().decode("utf-8")
        parsed = json.loads(raw)
        choice = (parsed.get("choices") or [{}])[0] if isinstance(parsed, dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        content = str(message.get("content") or message.get("reasoning_content") or "").strip()
        if not content:
            content = compact_json(parsed, 2000)
        return {
            **contract,
            "ok": True,
            "status": "answered",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "content": content,
            "finish_reason": str(choice.get("finish_reason") or ""),
            "error": "",
        }
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {
            **contract,
            "ok": False,
            "status": "unavailable" if isinstance(exc, (OSError, TimeoutError, urllib.error.URLError)) else "error",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "content": "",
            "error": str(exc),
        }


def attach_model_brain(payload: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["model_brain"] = decision
    return enriched
