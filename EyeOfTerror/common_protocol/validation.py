from __future__ import annotations

from typing import Any

from .protocol import (
    ACCEPTANCE_STATUSES,
    GOVERNOR_REPORT_STATUSES,
    LIFECYCLE_STATUSES,
    PROGRESS_PHASES,
    PROGRESS_STATUSES,
    PROTOCOL_VERSION,
    WORKER_REPORT_STATUSES,
)


class ProtocolValidationError(ValueError):
    pass


REQUIRED_FIELDS: dict[str, set[str]] = {
    "mission_intake": {"mission_id", "user_request", "source_channel", "status"},
    "commander_order": {"mission_id", "from", "to", "user_request", "commander_intent", "primary_goal", "success_conditions", "reporting_policy"},
    "governor_plan": {"mission_id", "governor", "understanding", "work_plan", "quality_gates", "expected_deliverables"},
    "worker_order": {"mission_id", "step_id", "from", "to", "task", "expected_output", "input_artifacts", "quality_requirements", "revision_context"},
    "progress_event": {"mission_id", "actor", "role", "phase", "status", "title", "body", "visible_to_user"},
    "worker_report": {"mission_id", "step_id", "worker", "status", "summary", "artifacts", "problems", "next_recommended_action"},
    "governor_report": {"mission_id", "governor", "status", "summary", "deliverables", "quality_review", "revision_plan", "user_facing_answer"},
    "acceptance_review": {"mission_id", "reviewer", "status", "accepted", "reason", "required_revision", "escalate_to_user"},
    "revision_order": {"mission_id", "from", "to", "reason", "order", "required_steps"},
    "final_response": {"mission_id", "status", "accepted_by", "answer", "artifacts"},
}


def _require_string(payload: dict[str, Any], field: str) -> None:
    if not isinstance(payload.get(field), str) or not str(payload.get(field)).strip():
        raise ProtocolValidationError(f"{payload.get('type')}.{field} must be a non-empty string")


def _require_list(payload: dict[str, Any], field: str) -> None:
    if not isinstance(payload.get(field), list):
        raise ProtocolValidationError(f"{payload.get('type')}.{field} must be a list")


def validate_protocol_payload(payload: dict[str, Any], expected_type: str | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProtocolValidationError("protocol payload must be a JSON object")
    payload_type = str(payload.get("type") or "")
    if expected_type and payload_type != expected_type:
        raise ProtocolValidationError(f"expected {expected_type}, got {payload_type or '<missing>'}")
    if payload_type not in REQUIRED_FIELDS:
        raise ProtocolValidationError(f"unknown protocol type: {payload_type or '<missing>'}")
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolValidationError(f"{payload_type}.protocol_version must be {PROTOCOL_VERSION}")
    missing = sorted(field for field in REQUIRED_FIELDS[payload_type] if field not in payload)
    if missing:
        raise ProtocolValidationError(f"{payload_type} missing fields: {missing}")
    _require_string(payload, "mission_id")

    if payload_type == "mission_intake":
        _require_string(payload, "user_request")
        if payload.get("status") not in LIFECYCLE_STATUSES:
            raise ProtocolValidationError("mission_intake.status is not a lifecycle status")
    elif payload_type == "commander_order":
        for field in ("from", "to", "user_request", "commander_intent", "primary_goal"):
            _require_string(payload, field)
        _require_list(payload, "success_conditions")
        policy = payload.get("reporting_policy")
        if not isinstance(policy, dict) or policy.get("revision_is_internal") is not True:
            raise ProtocolValidationError("commander_order.reporting_policy.revision_is_internal must be true")
    elif payload_type == "governor_plan":
        for field in ("governor", "understanding"):
            _require_string(payload, field)
        _require_list(payload, "work_plan")
        _require_list(payload, "quality_gates")
    elif payload_type == "worker_order":
        for field in ("step_id", "from", "to", "task", "expected_output"):
            _require_string(payload, field)
        _require_list(payload, "input_artifacts")
        _require_list(payload, "quality_requirements")
        if not isinstance(payload.get("revision_context"), dict):
            raise ProtocolValidationError("worker_order.revision_context must be an object")
    elif payload_type == "progress_event":
        for field in ("actor", "role", "title", "body"):
            _require_string(payload, field)
        if payload.get("phase") not in PROGRESS_PHASES:
            raise ProtocolValidationError("progress_event.phase is invalid")
        if payload.get("status") not in PROGRESS_STATUSES:
            raise ProtocolValidationError("progress_event.status is invalid")
        if not isinstance(payload.get("visible_to_user"), bool):
            raise ProtocolValidationError("progress_event.visible_to_user must be boolean")
    elif payload_type == "worker_report":
        for field in ("step_id", "worker", "summary"):
            _require_string(payload, field)
        if payload.get("status") not in WORKER_REPORT_STATUSES:
            raise ProtocolValidationError("worker_report.status is invalid")
    elif payload_type == "governor_report":
        for field in ("governor", "summary"):
            _require_string(payload, field)
        if payload.get("status") not in GOVERNOR_REPORT_STATUSES:
            raise ProtocolValidationError("governor_report.status is invalid")
        if not isinstance(payload.get("quality_review"), dict):
            raise ProtocolValidationError("governor_report.quality_review must be an object")
        if not isinstance(payload.get("revision_plan"), dict):
            raise ProtocolValidationError("governor_report.revision_plan must be an object")
    elif payload_type == "acceptance_review":
        for field in ("reviewer", "reason"):
            _require_string(payload, field)
        if payload.get("status") not in ACCEPTANCE_STATUSES:
            raise ProtocolValidationError("acceptance_review.status is invalid")
        if not isinstance(payload.get("accepted"), bool):
            raise ProtocolValidationError("acceptance_review.accepted must be boolean")
        if not isinstance(payload.get("required_revision"), dict):
            raise ProtocolValidationError("acceptance_review.required_revision must be an object")
        if not isinstance(payload.get("escalate_to_user"), bool):
            raise ProtocolValidationError("acceptance_review.escalate_to_user must be boolean")
    elif payload_type == "revision_order":
        for field in ("from", "to", "reason", "order"):
            _require_string(payload, field)
        _require_list(payload, "required_steps")
    elif payload_type == "final_response":
        for field in ("status", "accepted_by", "answer"):
            _require_string(payload, field)
        _require_list(payload, "artifacts")
    return payload
