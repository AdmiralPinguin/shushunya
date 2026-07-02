"""Pure oversight and revision validation for the Warmaster trust boundary.

Warmaster does not blindly execute a governor's plan: before a run starts it
checks the governor-supplied oversight and any revision plan for internal
consistency against the task contract and run steps. These checks are pure
(dict in, error list / summary out) so they can be unit-tested in isolation.
The gateway keeps the thin run-package IO wrappers that load a run directory
and call into this module.
"""
from __future__ import annotations

from typing import Any


def downstream_revision_steps(step_id: str, dependencies_by_step: dict[str, list[str]], final_steps: set[str]) -> list[str]:
    downstream: list[str] = []
    seen: set[str] = set()
    changed = True
    while changed:
        changed = False
        for candidate_step_id, dependencies in dependencies_by_step.items():
            if candidate_step_id in seen or candidate_step_id in final_steps:
                continue
            if step_id in dependencies or any(dependency in seen for dependency in dependencies):
                seen.add(candidate_step_id)
                downstream.append(candidate_step_id)
                changed = True
    return downstream


def compact_oversight_summary(oversight: dict[str, Any]) -> dict[str, Any]:
    artifact_roles = oversight.get("artifact_roles") if isinstance(oversight.get("artifact_roles"), dict) else {}
    final_review = oversight.get("final_review") if isinstance(oversight.get("final_review"), dict) else {}
    revision_policy = oversight.get("revision_policy") if isinstance(oversight.get("revision_policy"), dict) else {}
    iteration_policy = oversight.get("iteration_policy") if isinstance(oversight.get("iteration_policy"), dict) else {}
    task_profile = oversight.get("task_profile") if isinstance(oversight.get("task_profile"), dict) else {}
    worker_briefs = oversight.get("worker_specialization_briefs") if isinstance(oversight.get("worker_specialization_briefs"), list) else []
    quality_gates = oversight.get("quality_gates") if isinstance(oversight.get("quality_gates"), list) else []
    completion_criteria = oversight.get("completion_criteria") if isinstance(oversight.get("completion_criteria"), list) else []
    handoffs = oversight.get("handoffs") if isinstance(oversight.get("handoffs"), list) else []
    step_quality_matrix = oversight.get("step_quality_matrix") if isinstance(oversight.get("step_quality_matrix"), list) else []
    return {
        "kind": str(oversight.get("kind") or ""),
        "governor": str(oversight.get("governor") or ""),
        "quality_gate_count": len(quality_gates),
        "completion_criteria_count": len(completion_criteria),
        "handoff_count": len(handoffs),
        "step_quality_check_count": sum(
            len(item.get("checks") if isinstance(item.get("checks"), list) else [])
            for item in step_quality_matrix
            if isinstance(item, dict)
        ),
        "step_quality_matrix_count": len(step_quality_matrix),
        "task_profile": {
            "kinds": task_profile.get("kinds", []) if isinstance(task_profile.get("kinds"), list) else [],
            "complexity": str(task_profile.get("complexity") or ""),
            "risk_flags": task_profile.get("risk_flags", []) if isinstance(task_profile.get("risk_flags"), list) else [],
        },
        "worker_specialization_brief_count": len(worker_briefs),
        "artifact_roles": {
            "draft": artifact_roles.get("draft", []),
            "critic": artifact_roles.get("critic", []),
            "final": artifact_roles.get("final", []),
        },
        "final_review": {
            "critic_step": str(final_review.get("critic_step") or ""),
            "final_step": str(final_review.get("final_step") or ""),
            "final_artifact": str(final_review.get("final_artifact") or ""),
            "requires_critic_approval_or_blockers": bool(final_review.get("requires_critic_approval_or_blockers")),
            "requires_gap_disclosure": bool(final_review.get("requires_gap_disclosure")),
            "requires_evidence_trace": bool(final_review.get("requires_evidence_trace")),
        },
        "revision_policy": {
            "source_step": str(revision_policy.get("source_step") or ""),
            "final_steps": revision_policy.get("final_steps", []) if isinstance(revision_policy.get("final_steps"), list) else [],
            "allowed_steps": revision_policy.get("allowed_steps", []) if isinstance(revision_policy.get("allowed_steps"), list) else [],
            "requires_downstream_rerun": bool(revision_policy.get("requires_downstream_rerun")),
            "requires_focused_context": bool(revision_policy.get("requires_focused_context")),
            "requires_gap_disclosure": bool(revision_policy.get("requires_gap_disclosure")),
        },
        "iteration_policy": {
            "controller": str(iteration_policy.get("controller") or ""),
            "recommended_endpoint": str(iteration_policy.get("recommended_endpoint") or ""),
            "max_revision_cycles": int(iteration_policy.get("max_revision_cycles") or 0),
            "auto_revision_trigger_count": len(iteration_policy.get("auto_revision_triggers") if isinstance(iteration_policy.get("auto_revision_triggers"), list) else []),
            "stop_condition_count": len(iteration_policy.get("stop_conditions") if isinstance(iteration_policy.get("stop_conditions"), list) else []),
            "final_readiness_check_count": len(iteration_policy.get("final_readiness_checks") if isinstance(iteration_policy.get("final_readiness_checks"), list) else []),
        },
    }


def validate_oversight_payload(contract: dict[str, Any], oversight: dict[str, Any], status: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    governor = str(oversight.get("governor") or "")
    if governor != str(contract.get("assigned_governor") or ""):
        errors.append("oversight governor does not match contract assigned_governor")
    required_artifacts = set(contract.get("required_artifacts") if isinstance(contract.get("required_artifacts"), list) else [])
    steps = status.get("steps") if isinstance(status.get("steps"), list) else []
    steps_by_id = {str(step.get("step_id") or ""): step for step in steps if isinstance(step, dict) and step.get("step_id")}
    final_review = oversight.get("final_review") if isinstance(oversight.get("final_review"), dict) else {}
    for field_name in ("critic_step", "final_step"):
        step_id = str(final_review.get(field_name) or "")
        if not step_id:
            errors.append(f"oversight final_review.{field_name} is required")
        elif step_id not in steps_by_id:
            errors.append(f"oversight final_review.{field_name} references unknown step: {step_id}")
    final_artifact = str(final_review.get("final_artifact") or "")
    final_step = str(final_review.get("final_step") or "")
    final_expected = steps_by_id.get(final_step, {}).get("expected_artifacts", []) if final_step in steps_by_id else []
    if not final_artifact:
        errors.append("oversight final_review.final_artifact is required")
    elif final_artifact not in required_artifacts:
        errors.append(f"oversight final artifact is not required by contract: {final_artifact}")
    elif final_artifact not in final_expected:
        errors.append(f"oversight final artifact is not produced by final step: {final_artifact}")
    revision_policy = oversight.get("revision_policy") if isinstance(oversight.get("revision_policy"), dict) else {}
    if not revision_policy:
        errors.append("oversight revision_policy is required")
    else:
        source_step = str(revision_policy.get("source_step") or "")
        if not source_step:
            errors.append("oversight revision_policy.source_step is required")
        elif source_step not in steps_by_id:
            errors.append(f"oversight revision_policy.source_step references unknown step: {source_step}")
        final_steps = revision_policy.get("final_steps")
        if not isinstance(final_steps, list) or not final_steps:
            errors.append("oversight revision_policy.final_steps must be a non-empty list")
        else:
            normalized_final_steps: list[str] = []
            for index, step_id in enumerate(final_steps):
                if not isinstance(step_id, str) or not step_id:
                    errors.append(f"oversight revision_policy.final_steps[{index}] must be a non-empty string")
                    continue
                normalized_final_steps.append(step_id)
                if step_id not in steps_by_id:
                    errors.append(f"oversight revision_policy.final_steps[{index}] references unknown step: {step_id}")
            for required_step in (str(final_review.get("critic_step") or ""), str(final_review.get("final_step") or "")):
                if required_step and required_step not in normalized_final_steps:
                    errors.append(f"oversight revision_policy.final_steps must include final_review step: {required_step}")
        allowed_steps = revision_policy.get("allowed_steps")
        if not isinstance(allowed_steps, list) or not allowed_steps:
            errors.append("oversight revision_policy.allowed_steps must be a non-empty list")
        else:
            normalized_allowed_steps: list[str] = []
            for index, step_id in enumerate(allowed_steps):
                if not isinstance(step_id, str) or not step_id:
                    errors.append(f"oversight revision_policy.allowed_steps[{index}] must be a non-empty string")
                    continue
                if step_id in normalized_allowed_steps:
                    errors.append(f"oversight revision_policy.allowed_steps has duplicate step: {step_id}")
                normalized_allowed_steps.append(step_id)
                if step_id not in steps_by_id:
                    errors.append(f"oversight revision_policy.allowed_steps[{index}] references unknown step: {step_id}")
            for required_step in (str(final_review.get("critic_step") or ""), str(final_review.get("final_step") or "")):
                if required_step and required_step not in normalized_allowed_steps:
                    errors.append(f"oversight revision_policy.allowed_steps must include final_review step: {required_step}")
        for field_name in ("requires_downstream_rerun", "requires_focused_context", "requires_gap_disclosure"):
            if not isinstance(revision_policy.get(field_name), bool):
                errors.append(f"oversight revision_policy.{field_name} must be a boolean")
    handoffs = oversight.get("handoffs") if isinstance(oversight.get("handoffs"), list) else []
    for index, handoff in enumerate(handoffs):
        if not isinstance(handoff, dict):
            errors.append(f"oversight handoffs[{index}] must be an object")
            continue
        from_step = str(handoff.get("from_step") or "")
        if from_step not in steps_by_id:
            errors.append(f"oversight handoffs[{index}].from_step references unknown step: {from_step}")
        to_steps = handoff.get("to_steps") if isinstance(handoff.get("to_steps"), list) else []
        for to_step in to_steps:
            if str(to_step) not in steps_by_id:
                errors.append(f"oversight handoffs[{index}].to_steps references unknown step: {to_step}")
    matrix = oversight.get("step_quality_matrix") if isinstance(oversight.get("step_quality_matrix"), list) else []
    if not matrix:
        errors.append("oversight step_quality_matrix must be a non-empty list")
    else:
        matrix_step_ids: set[str] = set()
        known_artifacts = {
            str(artifact)
            for step in steps
            if isinstance(step, dict)
            for artifact in (step.get("expected_artifacts") if isinstance(step.get("expected_artifacts"), list) else [])
        }
        for index, item in enumerate(matrix):
            if not isinstance(item, dict):
                errors.append(f"oversight step_quality_matrix[{index}] must be an object")
                continue
            step_id = str(item.get("step_id") or "")
            if not step_id:
                errors.append(f"oversight step_quality_matrix[{index}].step_id is required")
                continue
            if step_id in matrix_step_ids:
                errors.append(f"oversight step_quality_matrix has duplicate step_id: {step_id}")
            matrix_step_ids.add(step_id)
            step = steps_by_id.get(step_id)
            if not step:
                errors.append(f"oversight step_quality_matrix[{index}].step_id references unknown step: {step_id}")
                continue
            worker = str(item.get("worker") or "")
            if worker != str(step.get("worker") or ""):
                errors.append(f"oversight step_quality_matrix[{index}].worker does not match run step: {step_id}")
            expected_artifacts = item.get("expected_artifacts")
            if expected_artifacts != (step.get("expected_artifacts") if isinstance(step.get("expected_artifacts"), list) else []):
                errors.append(f"oversight step_quality_matrix[{index}].expected_artifacts does not match run step: {step_id}")
            required_inputs = item.get("required_inputs") if isinstance(item.get("required_inputs"), list) else []
            for artifact in required_inputs:
                if str(artifact) not in known_artifacts:
                    errors.append(f"oversight step_quality_matrix[{index}].required_inputs references unknown artifact: {artifact}")
            checks = item.get("checks")
            if not isinstance(checks, list) or not checks or any(not isinstance(check, str) or not check for check in checks):
                errors.append(f"oversight step_quality_matrix[{index}].checks must be non-empty strings")
            role_policy = item.get("role_policy")
            if role_policy is not None:
                if not isinstance(role_policy, dict):
                    errors.append(f"oversight step_quality_matrix[{index}].role_policy must be an object")
                    role_policy = {}
                if not isinstance(role_policy.get("authority"), str) or not role_policy.get("authority"):
                    errors.append(f"oversight step_quality_matrix[{index}].role_policy.authority is required")
                if not isinstance(role_policy.get("may_mutate_source"), bool):
                    errors.append(f"oversight step_quality_matrix[{index}].role_policy.may_mutate_source must be a boolean")
            blockers = item.get("blockers")
            if not isinstance(blockers, list) or not blockers or any(not isinstance(blocker, str) or not blocker for blocker in blockers):
                errors.append(f"oversight step_quality_matrix[{index}].blockers must be non-empty strings")
            revision_targets = item.get("revision_targets")
            if not isinstance(revision_targets, list) or not revision_targets:
                errors.append(f"oversight step_quality_matrix[{index}].revision_targets must be a non-empty list")
            else:
                for target in revision_targets:
                    if str(target) not in steps_by_id:
                        errors.append(f"oversight step_quality_matrix[{index}].revision_targets references unknown step: {target}")
        missing_matrix_steps = sorted(set(steps_by_id) - matrix_step_ids)
        if missing_matrix_steps:
            errors.append(f"oversight step_quality_matrix missing steps: {missing_matrix_steps}")
    return errors
