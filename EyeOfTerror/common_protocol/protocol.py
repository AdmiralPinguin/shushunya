from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

PROTOCOL_VERSION = 1

LIFECYCLE_STATUSES = {
    "created",
    "intake",
    "assigned",
    "planning",
    "plan_review",
    "executing",
    "governor_review",
    "warmaster_acceptance",
    "revision",
    "blocked",
    "completed",
    "failed",
    "cancelled",
}

PROGRESS_PHASES = {
    "intake",
    "assigned",
    "planning",
    "executing",
    "reviewing",
    "revising",
    "finalizing",
    "blocked",
    "completed",
    "failed",
}

PROGRESS_STATUSES = {"started", "running", "done", "blocked", "failed"}
WORKER_REPORT_STATUSES = {"done", "blocked", "needs_revision", "failed"}
GOVERNOR_REPORT_STATUSES = {"ready", "needs_revision", "blocked", "failed"}
ACCEPTANCE_STATUSES = {"accepted", "revision_required", "blocked", "failed"}


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def protocol_envelope(payload_type: str, mission_id: str) -> dict[str, Any]:
    return {
        "type": payload_type,
        "protocol_version": PROTOCOL_VERSION,
        "mission_id": mission_id,
        "created_at": utc_now(),
    }


def _strings(values: list[str] | None) -> list[str]:
    return [item.strip() for item in values or [] if isinstance(item, str) and item.strip()]


def mission_intake(
    mission_id: str,
    user_request: str,
    source_channel: str = "main_chat",
    user_id: str = "",
) -> dict[str, Any]:
    return {
        **protocol_envelope("mission_intake", mission_id),
        "source_channel": source_channel,
        "user_id": user_id,
        "user_request": user_request,
        "status": "intake",
    }


def commander_order(
    mission_id: str,
    to: str,
    user_request: str,
    commander_intent: str,
    primary_goal: str,
    success_conditions: list[str],
    constraints: list[str] | None = None,
    escalate_to_user_if: list[str] | None = None,
    supporting_governors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        **protocol_envelope("commander_order", mission_id),
        "from": "Warmaster",
        "to": to,
        "supporting_governors": _strings(supporting_governors),
        "user_request": user_request,
        "commander_intent": commander_intent,
        "primary_goal": primary_goal,
        "success_conditions": _strings(success_conditions),
        "constraints": _strings(constraints),
        "escalate_to_user_if": _strings(escalate_to_user_if),
        "reporting_policy": {
            "progress_events_required": True,
            "final_report_required": True,
            "revision_is_internal": True,
        },
    }


def governor_plan(
    mission_id: str,
    governor: str,
    understanding: str,
    work_plan: list[dict[str, Any]],
    quality_gates: list[str],
    expected_deliverables: list[str] | None = None,
) -> dict[str, Any]:
    return {
        **protocol_envelope("governor_plan", mission_id),
        "governor": governor,
        "understanding": understanding,
        "work_plan": work_plan,
        "quality_gates": _strings(quality_gates),
        "expected_deliverables": _strings(expected_deliverables),
    }


def governor_plan_from_contract(mission_id: str, contract: dict[str, Any]) -> dict[str, Any]:
    worker_plan = contract.get("worker_plan") if isinstance(contract.get("worker_plan"), list) else []
    work_plan: list[dict[str, Any]] = []
    for step in worker_plan:
        if not isinstance(step, dict):
            continue
        work_plan.append(
            {
                "step_id": str(step.get("step_id") or "").strip(),
                "worker": str(step.get("worker") or "").strip(),
                "goal": str(step.get("purpose") or step.get("goal") or "").strip(),
                "depends_on": _strings(step.get("depends_on") if isinstance(step.get("depends_on"), list) else []),
                "expected_artifacts": _strings(step.get("expected_artifacts") if isinstance(step.get("expected_artifacts"), list) else []),
            }
        )
    return governor_plan(
        mission_id,
        governor=str(contract.get("assigned_governor") or "").strip(),
        understanding=str(contract.get("goal") or "").strip(),
        work_plan=work_plan,
        quality_gates=_strings(contract.get("quality_gates") if isinstance(contract.get("quality_gates"), list) else []),
        expected_deliverables=_strings(contract.get("required_artifacts") if isinstance(contract.get("required_artifacts"), list) else []),
    )


def worker_order(
    mission_id: str,
    step_id: str,
    sender: str,
    to: str,
    task: str,
    expected_output: str,
    input_artifacts: list[str] | None = None,
    quality_requirements: list[str] | None = None,
    revision_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        **protocol_envelope("worker_order", mission_id),
        "step_id": step_id,
        "from": sender,
        "to": to,
        "task": task,
        "input_artifacts": _strings(input_artifacts),
        "expected_output": expected_output,
        "quality_requirements": _strings(quality_requirements),
        "revision_context": revision_context or {},
    }


def worker_report(
    mission_id: str,
    step_id: str,
    worker: str,
    status: str,
    summary: str,
    artifacts: list[str] | None = None,
    problems: list[str] | None = None,
    next_recommended_action: str = "",
) -> dict[str, Any]:
    return {
        **protocol_envelope("worker_report", mission_id),
        "step_id": step_id,
        "worker": worker,
        "status": status,
        "summary": summary,
        "artifacts": _strings(artifacts),
        "problems": _strings(problems),
        "next_recommended_action": next_recommended_action,
    }


def governor_report(
    mission_id: str,
    governor: str,
    status: str,
    summary: str,
    deliverables: list[str] | None = None,
    quality_review: dict[str, Any] | None = None,
    revision_plan: dict[str, Any] | None = None,
    user_facing_answer: str = "",
) -> dict[str, Any]:
    return {
        **protocol_envelope("governor_report", mission_id),
        "governor": governor,
        "status": status,
        "summary": summary,
        "deliverables": _strings(deliverables),
        "quality_review": quality_review or {"passed": False, "checks": []},
        "revision_plan": revision_plan or {"required": False, "reason": "", "steps": []},
        "user_facing_answer": user_facing_answer,
    }


def revision_order(
    mission_id: str,
    to: str,
    reason: str,
    order: str,
    required_steps: list[str] | None = None,
) -> dict[str, Any]:
    return {
        **protocol_envelope("revision_order", mission_id),
        "from": "Warmaster",
        "to": to,
        "reason": reason,
        "order": order,
        "required_steps": _strings(required_steps),
    }


def acceptance_review(
    mission_id: str,
    accepted: bool,
    reason: str,
    required_revision: dict[str, Any] | None = None,
    escalate_to_user: bool = False,
    reviewer: str = "Warmaster",
) -> dict[str, Any]:
    if accepted:
        status = "accepted"
    elif escalate_to_user:
        status = "blocked"
    else:
        status = "revision_required"
    return {
        **protocol_envelope("acceptance_review", mission_id),
        "reviewer": reviewer,
        "status": status,
        "accepted": accepted,
        "reason": reason,
        "required_revision": required_revision or {"to": "", "order": ""},
        "escalate_to_user": escalate_to_user,
    }


def final_response(
    mission_id: str,
    status: str,
    answer: str,
    accepted_by: str = "Warmaster",
    artifacts: list[str] | None = None,
) -> dict[str, Any]:
    return {
        **protocol_envelope("final_response", mission_id),
        "status": status,
        "accepted_by": accepted_by,
        "answer": answer,
        "artifacts": _strings(artifacts),
    }
