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
        "output_fields": [*outputs, "execution_packet", "revision_packet"],
    }


def execution_packet(
    *,
    worker: str,
    step: str,
    produced_artifacts: list[str],
    next_steps: list[str] | None = None,
    blockers: list[dict[str, Any]] | None = None,
    handoff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active_blockers = blockers or []
    return {
        "kind": "pictorium_worker_execution_packet",
        "api_version": API_VERSION,
        "worker": worker,
        "step": step,
        "status": "blocked" if active_blockers else "ready",
        "produced_artifacts": produced_artifacts,
        "next_steps": next_steps or [],
        "blockers": active_blockers,
        "handoff": handoff or {},
    }


def revision_packet(
    *,
    worker: str,
    source_step: str,
    blockers: list[dict[str, Any]],
    default_target_worker: str,
    default_target_step: str,
    action: str,
) -> dict[str, Any]:
    issues = []
    for index, blocker in enumerate(blockers, start=1):
        issues.append(
            {
                "id": f"{source_step}_issue_{index:02d}",
                "severity": blocker.get("severity") or "blocking",
                "code": blocker.get("code") or "unknown_blocker",
                "message": blocker.get("message") or "",
                "target_worker": blocker.get("target_worker") or default_target_worker,
                "target_step": blocker.get("target_step") or default_target_step,
                "requested_change": blocker.get("requested_change") or action,
                "details": blocker.get("details") or {},
            }
        )
    return {
        "kind": "pictorium_revision_packet",
        "api_version": API_VERSION,
        "source_worker": worker,
        "source_step": source_step,
        "required": bool(issues),
        "action": action,
        "issues": issues,
    }
