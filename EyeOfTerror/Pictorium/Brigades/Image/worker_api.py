from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from EyeOfTerror.common_protocol import validate_protocol_payload, worker_report
from EyeOfTerror.Pictorium.pictorium_model import (
    attach_model_guidance,
    model_guidance_blockers,
    pictorium_model_contract,
    request_pictorium_model_guidance,
)


API_VERSION = 1
_PAYLOAD_STACK: ContextVar[tuple[dict[str, Any], ...]] = ContextVar("pictorium_worker_payload_stack", default=())


def _push_payload(payload: dict[str, Any]) -> None:
    _PAYLOAD_STACK.set((*_PAYLOAD_STACK.get(), payload))


def _pop_payload() -> dict[str, Any] | None:
    stack = _PAYLOAD_STACK.get()
    if not stack:
        return None
    payload = stack[-1]
    _PAYLOAD_STACK.set(stack[:-1])
    return payload


def response(worker: str, payload: dict[str, Any], *, ok: bool = True) -> dict[str, Any]:
    result = {
        "ok": ok,
        "worker": worker,
        "api_version": API_VERSION,
        **payload,
    }
    request_payload = _pop_payload()
    if isinstance(request_payload, dict):
        order = worker_order_from_payload(request_payload)
        if order:
            if str(order.get("to") or "").strip() != worker:
                raise ValueError(f"worker_order.to={order.get('to')!r} cannot be handled by {worker}")
            result.setdefault("protocol_mode", "worker_order")
            result.setdefault("worker_order", order)
            result.setdefault("worker_report", worker_report_from_response(worker, order, result, ok=ok))
        else:
            raise ValueError("worker_order is required for Pictorium worker execution")
    return result


def require_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        raise ValueError("worker_order is required for Pictorium worker execution")
    if not isinstance(payload, dict):
        raise TypeError("worker payload must be a JSON object")
    if not worker_order_from_payload(payload):
        raise ValueError("worker_order is required for Pictorium worker execution")
    _push_payload(payload)
    return payload


def worker_order_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    order = payload.get("worker_order") if isinstance(payload.get("worker_order"), dict) else {}
    if order:
        validate_protocol_payload(order, expected_type="worker_order")
    return order


def task_text(payload: dict[str, Any]) -> str:
    order = worker_order_from_payload(payload)
    if order:
        return str(order.get("task") or "").strip()
    raise ValueError("worker_order is required for Pictorium worker execution")


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
        "input_fields": ["worker_order", *inputs],
        "output_fields": [*outputs, "worker_report", "protocol_mode", "execution_packet", "revision_packet", "model_guidance"],
        "model_brain": pictorium_model_contract(name, role),
    }


def _blocker_messages(blockers: Any) -> list[str]:
    if not isinstance(blockers, list):
        return []
    messages = []
    for blocker in blockers:
        if isinstance(blocker, dict):
            message = str(blocker.get("message") or blocker.get("code") or "").strip()
        else:
            message = str(blocker or "").strip()
        if message:
            messages.append(message)
    return messages


def _next_action(packet: dict[str, Any]) -> str:
    next_steps = packet.get("next_steps")
    if isinstance(next_steps, list):
        return ", ".join(str(item).strip() for item in next_steps if str(item).strip())
    return str(next_steps or "").strip()


def worker_report_from_response(
    worker: str,
    order: dict[str, Any],
    result: dict[str, Any],
    *,
    ok: bool,
) -> dict[str, Any]:
    packet = result.get("execution_packet") if isinstance(result.get("execution_packet"), dict) else {}
    blockers = packet.get("blockers") if isinstance(packet.get("blockers"), list) else result.get("blockers")
    artifacts = packet.get("produced_artifacts") if isinstance(packet.get("produced_artifacts"), list) else []
    status = "done" if ok else "failed"
    if blockers:
        status = "blocked"
    summary = str(
        result.get("summary")
        or packet.get("step")
        or result.get("artifact")
        or order.get("expected_output")
        or order.get("task")
        or f"{worker} completed work"
    ).strip()
    report = worker_report(
        mission_id=str(order.get("mission_id") or ""),
        step_id=str(order.get("step_id") or ""),
        worker=worker,
        status=status,
        summary=summary,
        artifacts=[str(item) for item in artifacts if str(item).strip()],
        problems=_blocker_messages(blockers),
        next_recommended_action=_next_action(packet),
    )
    validate_protocol_payload(report, expected_type="worker_report")
    return report


def worker_model_guidance(worker: str, role: str, payload: dict[str, Any], instructions: str) -> dict[str, Any]:
    return request_pictorium_model_guidance(worker, role, payload, instructions=instructions)


def guidance_blockers(guidance: dict[str, Any], *, worker: str, step: str) -> list[dict[str, Any]]:
    return model_guidance_blockers(guidance, target_worker=worker, target_step=step)


def with_model_guidance(payload: dict[str, Any], guidance: dict[str, Any]) -> dict[str, Any]:
    return attach_model_guidance(payload, guidance)


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


def persist_expected_artifacts(request, workspace_root, result):
    """Write each declared expected artifact to the sandbox so the next step's
    input-artifact preflight passes. Image workers pass their real data through
    the dispatch packet, but the gateway still gates each step on the artifact
    FILE existing — so the step output is materialised here from the worker's
    result. Uses the order's /work/<slug>/... paths, not a hardcoded path."""
    import json as _json
    from pathlib import Path as _Path

    if workspace_root is None or not isinstance(request, dict) or not isinstance(result, dict):
        return
    # The launcher hands run() the nested packet["request"]; expected artifacts
    # live under step.expected_artifacts there (top-level only on the raw packet).
    expected = request.get("expected_artifacts")
    if not isinstance(expected, list) or not expected:
        step = request.get("step") if isinstance(request.get("step"), dict) else {}
        expected = step.get("expected_artifacts")
    if not isinstance(expected, list) or not expected:
        worker_order = request.get("worker_order") if isinstance(request.get("worker_order"), dict) else {}
        single = worker_order.get("expected_output")
        expected = [single] if isinstance(single, str) and single else []
    if not isinstance(expected, list) or not expected:
        return
    root = _Path(workspace_root).resolve()
    payload = result.get("worker_report") if isinstance(result.get("worker_report"), dict) else result
    for artifact in expected:
        if not isinstance(artifact, str) or not artifact.startswith("/work/"):
            continue
        host_path = (root / artifact.removeprefix("/work/")).resolve()
        if not host_path.is_relative_to(root):
            continue
        host_path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "artifact": artifact,
            "step_id": request.get("step_id"),
            "worker": request.get("worker"),
            "produced_by": request.get("worker"),
            "result": payload,
        }
        host_path.write_text(_json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
