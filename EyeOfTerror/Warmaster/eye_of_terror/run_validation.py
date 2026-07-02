"""Run-package validation adapters: load a run dir and check its
governor oversight, dispatch packets, and revision plan against the
contract using the pure checks in oversight_guard."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .brigade import contract_summary
from .local_executor import ordered_dispatch_paths
from .oversight_guard import compact_oversight_summary, downstream_revision_steps, validate_oversight_payload
from .run_package import load_json_file, load_json_object, run_contract, run_dispatch_packets, run_oversight


def plan_oversight_errors(contract: dict[str, Any], plan_payload: dict[str, Any]) -> list[str]:
    oversight = plan_payload.get("oversight") if isinstance(plan_payload.get("oversight"), dict) else {}
    if not oversight:
        return ["governor plan did not include oversight"]
    return validate_oversight_payload(contract, oversight, {"steps": contract_summary(contract).get("steps", [])})


def verify_prepared_run_package(run_dir: Path, planned_contract: dict[str, Any], planned_oversight: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    contract_payload = run_contract(run_dir)
    if not contract_payload.get("ok"):
        errors.append(str(contract_payload.get("error") or "contract unavailable"))
    elif contract_payload.get("contract") != planned_contract:
        errors.append("prepared contract does not match governor plan")
    status, status_error = load_json_object(run_dir / "status.json", "status")
    if status_error:
        errors.append(status_error)
    oversight_payload = run_oversight(run_dir)
    if not oversight_payload.get("ok"):
        errors.append(str(oversight_payload.get("error") or "oversight unavailable"))
    else:
        prepared_oversight = oversight_payload.get("oversight") if isinstance(oversight_payload.get("oversight"), dict) else {}
        if prepared_oversight != planned_oversight:
            errors.append("prepared oversight does not match governor plan")
        if not status_error:
            errors.extend(validate_oversight_against_run(run_dir, prepared_oversight, status))
    if not status_error:
        errors.extend(run_dispatch_package_errors(run_dir, status))
    return errors


def dispatch_workers_by_step(run_dir: Path) -> dict[str, str]:
    workers: dict[str, str] = {}
    for dispatch_path in ordered_dispatch_paths(run_dir):
        packet = load_json_file(dispatch_path)
        step_id = str(packet.get("step_id") or dispatch_path.stem)
        worker = str(packet.get("worker") or "")
        if step_id:
            workers[step_id] = worker
    return workers


def dispatch_dependencies_by_step(run_dir: Path) -> dict[str, list[str]]:
    dependencies: dict[str, list[str]] = {}
    for dispatch_path in ordered_dispatch_paths(run_dir):
        packet = load_json_file(dispatch_path)
        step_id = str(packet.get("step_id") or dispatch_path.stem)
        depends_on = packet.get("depends_on") if isinstance(packet.get("depends_on"), list) else []
        dependencies[step_id] = [str(dependency) for dependency in depends_on if isinstance(dependency, str) and dependency]
    return dependencies


def validate_revision_plan(run_dir: Path, revision_plan: dict[str, Any]) -> list[str]:
    if not revision_plan.get("required"):
        return []
    raw_steps = revision_plan.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return ["revision_plan.steps must be a non-empty list when required"]
    try:
        workers_by_step = dispatch_workers_by_step(run_dir)
    except Exception as exc:  # noqa: BLE001 - summaries should report invalid run packages instead of crashing.
        return [f"revision dispatch unavailable: {exc}"]
    try:
        dependencies_by_step = dispatch_dependencies_by_step(run_dir)
    except Exception as exc:  # noqa: BLE001 - summaries should report invalid run packages instead of crashing.
        return [f"revision dispatch dependencies unavailable: {exc}"]
    allowed_steps: set[str] = set(workers_by_step)
    final_steps: set[str] = set()
    requires_downstream_rerun = False
    oversight_payload = run_oversight(run_dir)
    if oversight_payload.get("ok"):
        oversight = oversight_payload.get("oversight") if isinstance(oversight_payload.get("oversight"), dict) else {}
        revision_policy = oversight.get("revision_policy") if isinstance(oversight.get("revision_policy"), dict) else {}
        policy_allowed_steps = revision_policy.get("allowed_steps") if isinstance(revision_policy.get("allowed_steps"), list) else []
        if policy_allowed_steps:
            allowed_steps = {str(step_id) for step_id in policy_allowed_steps if isinstance(step_id, str) and step_id}
        policy_final_steps = revision_policy.get("final_steps") if isinstance(revision_policy.get("final_steps"), list) else []
        final_steps = {str(step_id) for step_id in policy_final_steps if isinstance(step_id, str) and step_id}
        requires_downstream_rerun = bool(revision_policy.get("requires_downstream_rerun"))
    errors: list[str] = []
    seen: set[str] = set()
    requested: set[str] = set()
    for index, item in enumerate(raw_steps):
        if not isinstance(item, dict):
            errors.append(f"revision_plan.steps[{index}] must be an object")
            continue
        step_id = str(item.get("step_id") or "").strip()
        worker = str(item.get("worker") or "").strip()
        if not step_id:
            errors.append(f"revision_plan.steps[{index}].step_id must be a non-empty string")
            continue
        if step_id in seen:
            errors.append(f"revision_plan references duplicate step: {step_id}")
        seen.add(step_id)
        requested.add(step_id)
        expected_worker = workers_by_step.get(step_id)
        if expected_worker is None:
            errors.append(f"revision_plan references unknown dispatch step: {step_id}")
        elif step_id not in allowed_steps:
            errors.append(f"revision_plan step is not allowed by oversight revision_policy: {step_id}")
        if not worker:
            errors.append(f"revision_plan.steps[{index}].worker must be a non-empty string")
        elif expected_worker is not None and worker != expected_worker:
            errors.append(f"revision_plan worker mismatch for {step_id}: expected {expected_worker}, got {worker}")
        for field_name in ("reason", "source", "priority"):
            if field_name in item and not isinstance(item.get(field_name), str):
                errors.append(f"revision_plan.steps[{index}].{field_name} must be a string")
    if requires_downstream_rerun:
        for step_id in sorted(requested):
            missing_downstream = [
                downstream_step_id
                for downstream_step_id in downstream_revision_steps(step_id, dependencies_by_step, final_steps)
                if downstream_step_id not in requested
            ]
            if missing_downstream:
                errors.append(f"revision_plan step {step_id} is missing downstream rerun steps: {missing_downstream}")
    return errors


def revision_plan_summary(revision_plan: dict[str, Any], revision_plan_errors: list[str] | None = None) -> dict[str, Any]:
    raw_steps = revision_plan.get("steps") if isinstance(revision_plan.get("steps"), list) else []
    steps = [item for item in raw_steps if isinstance(item, dict)]
    step_ids: list[str] = []
    workers: list[str] = []
    reasons: list[str] = []
    for item in steps:
        step_id = str(item.get("step_id") or "").strip()
        worker = str(item.get("worker") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if step_id and step_id not in step_ids:
            step_ids.append(step_id)
        if worker and worker not in workers:
            workers.append(worker)
        if reason and reason not in reasons:
            reasons.append(reason)
    errors = revision_plan_errors or []
    return {
        "required": bool(revision_plan.get("required")),
        "valid": not errors,
        "step_count": len(step_ids),
        "worker_count": len(workers),
        "step_ids": step_ids,
        "workers": workers,
        "reasons": reasons[:5],
        "errors": errors,
    }


def run_package_action_errors(run_dir: Path) -> list[str]:
    if not (run_dir / "status.json").exists() or not (run_dir / "contract.json").exists():
        return []
    errors: list[str] = []
    status, status_error = load_json_object(run_dir / "status.json", "status")
    if status_error:
        errors.append(status_error)
    contract_payload = run_contract(run_dir)
    if not contract_payload.get("ok"):
        errors.append(str(contract_payload.get("error") or "contract unavailable"))
    if not status_error:
        errors.extend(run_dispatch_package_errors(run_dir, status))
    return errors


def run_oversight_diagnostics(run_dir: Path) -> dict[str, Any]:
    payload = run_oversight(run_dir)
    if not payload.get("ok"):
        return payload
    oversight = payload.get("oversight") if isinstance(payload.get("oversight"), dict) else {}
    status, status_error = load_json_object(run_dir / "status.json", "status")
    validation_errors = [status_error] if status_error else validate_oversight_against_run(run_dir, oversight, status)
    return {
        **payload,
        "summary": compact_oversight_summary(oversight),
        "validation": {"ok": not validation_errors, "errors": validation_errors},
    }


def run_package_diagnostics(run_dir: Path) -> dict[str, Any]:
    status, status_error = load_json_object(run_dir / "status.json", "status")
    contract_payload = run_contract(run_dir)
    oversight_payload = run_oversight_diagnostics(run_dir)
    dispatch_payload = run_dispatch_packets(run_dir)
    errors: list[str] = []
    if status_error:
        errors.append(status_error)
    if not contract_payload.get("ok"):
        errors.append(str(contract_payload.get("error") or "contract unavailable"))
    if not oversight_payload.get("ok"):
        errors.append(str(oversight_payload.get("error") or "oversight unavailable"))
    else:
        errors.extend(oversight_payload.get("validation", {}).get("errors", []) if isinstance(oversight_payload.get("validation"), dict) else [])
    if not status_error:
        errors.extend(run_dispatch_package_errors(run_dir, status))
    dispatch_items = dispatch_payload.get("dispatch") if isinstance(dispatch_payload.get("dispatch"), list) else []
    contract = contract_payload.get("contract") if isinstance(contract_payload.get("contract"), dict) else {}
    return {
        "ok": not errors,
        "task_id": run_dir.name,
        "run_dir": str(run_dir),
        "validation": {"ok": not errors, "errors": errors},
        "files": {
            "contract": (run_dir / "contract.json").exists(),
            "oversight": (run_dir / "oversight.json").exists(),
            "status": (run_dir / "status.json").exists(),
            "dispatch_dir": (run_dir / "dispatch").exists(),
        },
        "contract_summary": contract_summary(contract) if contract else {},
        "oversight_summary": oversight_payload.get("summary", {}) if isinstance(oversight_payload.get("summary"), dict) else {},
        "dispatch_count": len(dispatch_items),
    }


def run_oversight_summary(run_dir: Path) -> dict[str, Any]:
    payload = run_oversight(run_dir)
    if not payload.get("ok"):
        return {}
    oversight = payload.get("oversight") if isinstance(payload.get("oversight"), dict) else {}
    return compact_oversight_summary(oversight) if oversight else {}


def run_oversight_validation_errors(run_dir: Path, status: dict[str, Any]) -> list[str]:
    if not (run_dir / "status.json").exists() or not (run_dir / "contract.json").exists():
        return []
    payload = run_oversight(run_dir)
    if not payload.get("ok"):
        return [str(payload.get("error") or "oversight unavailable")]
    oversight = payload.get("oversight") if isinstance(payload.get("oversight"), dict) else {}
    return validate_oversight_against_run(run_dir, oversight, status)


def validate_oversight_against_run(run_dir: Path, oversight: dict[str, Any], status: dict[str, Any]) -> list[str]:
    contract_payload = run_contract(run_dir)
    contract = contract_payload.get("contract") if isinstance(contract_payload.get("contract"), dict) else {}
    if not contract_payload.get("ok"):
        return [str(contract_payload.get("error") or "contract unavailable")]
    return validate_oversight_payload(contract, oversight, status)


def run_dispatch_package_errors(run_dir: Path, status: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    dispatch_dir = run_dir / "dispatch"
    if not dispatch_dir.exists():
        return ["dispatch directory not found"]
    steps = status.get("steps") if isinstance(status.get("steps"), list) else []
    task_id = str(status.get("task_id") or run_dir.name)
    step_by_id = {
        str(step.get("step_id") or ""): step
        for step in steps
        if isinstance(step, dict) and str(step.get("step_id") or "")
    }
    expected_names = {
        f"{str(step.get('step_id') or '')}.json"
        for step in steps
        if isinstance(step, dict) and str(step.get("step_id") or "")
    }
    actual_names = {path.name for path in dispatch_dir.glob("*.json")}
    for name in sorted(expected_names - actual_names):
        errors.append(f"dispatch packet missing: {name}")
    for name in sorted(actual_names - expected_names):
        errors.append(f"unexpected dispatch packet: {name}")
    dispatch_payload = run_dispatch_packets(run_dir)
    if not dispatch_payload.get("ok"):
        errors.append(str(dispatch_payload.get("error") or "dispatch unavailable"))
        return errors
    dispatch_items = dispatch_payload.get("dispatch") if isinstance(dispatch_payload.get("dispatch"), list) else []
    for item in dispatch_items:
        if not isinstance(item, dict):
            continue
        if not item.get("ok"):
            path = str(item.get("path") or "")
            detail = str(item.get("error") or "dispatch packet is not valid")
            errors.append(f"{path}: {detail}" if path else detail)
            continue
        path = Path(str(item.get("path") or ""))
        packet = item.get("packet") if isinstance(item.get("packet"), dict) else {}
        packet_step_id = str(packet.get("step_id") or "")
        expected_step_id = path.stem
        if packet_step_id != expected_step_id:
            errors.append(f"dispatch step_id mismatch for {path.name}: expected {expected_step_id}, got {packet_step_id or 'missing'}")
        expected_worker = str(step_by_id.get(expected_step_id, {}).get("worker") or "")
        packet_worker = str(packet.get("worker") or "")
        if expected_worker and packet_worker != expected_worker:
            errors.append(f"dispatch worker mismatch for {expected_step_id}: expected {expected_worker}, got {packet_worker or 'missing'}")
        packet_task_id = str(packet.get("task_id") or "")
        if packet_task_id != task_id:
            errors.append(f"dispatch task_id mismatch for {expected_step_id}: expected {task_id}, got {packet_task_id or 'missing'}")
        request = packet.get("request") if isinstance(packet.get("request"), dict) else {}
        request_task_id = str(request.get("task_id") or "")
        if not request_task_id:
            errors.append(f"dispatch request.task_id missing for {expected_step_id}")
    return errors
