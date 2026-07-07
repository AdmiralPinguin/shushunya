from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[5]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from EyeOfTerror.common_protocol import validate_protocol_payload  # noqa: E402


def strict_worker_request_from_payload(payload: dict[str, Any], expected_worker: str = "") -> dict[str, Any]:
    order = payload.get("worker_order") if isinstance(payload.get("worker_order"), dict) else {}
    request = payload.get("request") if isinstance(payload.get("request"), dict) else payload
    if not order and isinstance(request, dict):
        order = request.get("worker_order") if isinstance(request.get("worker_order"), dict) else {}
    if not order:
        raise ValueError("worker_order is required for CodeBrigade worker CLI execution")
    validate_protocol_payload(order, expected_type="worker_order")
    worker = str(order.get("to") or "")
    if expected_worker and worker != expected_worker:
        raise ValueError(f"worker_order.to={worker!r} cannot be handled by {expected_worker}")
    request_order = request.get("worker_order") if isinstance(request, dict) and isinstance(request.get("worker_order"), dict) else {}
    if request_order:
        validate_protocol_payload(request_order, expected_type="worker_order")
        if request_order != order:
            raise ValueError("request.worker_order must match dispatch worker_order")
    normalized = dict(request if isinstance(request, dict) else {})
    normalized["worker_order"] = order
    normalized.setdefault("task", str(order.get("task") or ""))
    normalized.setdefault("expected_output", str(order.get("expected_output") or ""))
    normalized.setdefault("input_artifacts", list(order.get("input_artifacts") if isinstance(order.get("input_artifacts"), list) else []))
    normalized.setdefault("quality_requirements", list(order.get("quality_requirements") if isinstance(order.get("quality_requirements"), list) else []))
    normalized.setdefault("revision_context", dict(order.get("revision_context") if isinstance(order.get("revision_context"), dict) else {}))
    return normalized
