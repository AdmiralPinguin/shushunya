from __future__ import annotations

from typing import Any


API_VERSION = 1


def response(worker: str, payload: dict[str, Any], *, ok: bool = True) -> dict[str, Any]:
    return {
        "ok": ok,
        "worker": worker,
        "api_version": API_VERSION,
        **payload,
    }


def require_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise TypeError("worker payload must be a JSON object")
    return payload


def task_text(payload: dict[str, Any]) -> str:
    text = str(payload.get("request") or payload.get("task") or "").strip()
    if text:
        return text
    contract = payload.get("contract") if isinstance(payload.get("contract"), dict) else {}
    return str(contract.get("goal") or "").strip()


def model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [model_dump(item) for item in value]
    if isinstance(value, dict):
        return {key: model_dump(item) for key, item in value.items()}
    return value


def worker_contract(
    *,
    name: str,
    role: str,
    capabilities: list[str],
    inputs: list[str],
    outputs: list[str],
) -> dict[str, Any]:
    return {
        "name": name,
        "api_version": API_VERSION,
        "role": role,
        "callable": "handle(payload: dict) -> dict",
        "capabilities": capabilities,
        "input_fields": inputs,
        "output_fields": outputs,
    }
