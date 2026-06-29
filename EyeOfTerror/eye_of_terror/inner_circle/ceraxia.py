from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..contracts import TaskContract, build_code_task_contract, validate_task_contract_payload
from ..pipeline import build_dispatch_packets, pipeline_status, write_pipeline_run
from ..registry import worker_by_name


REPO_ROOT = Path(__file__).resolve().parents[3]


def executable_client_action(task_id: str, action: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(action, dict) or not action:
        return {}
    method = str(action.get("method") or "").upper()
    endpoint = str(action.get("endpoint") or "")
    endpoint_method = ""
    path = endpoint
    if " " in endpoint:
        endpoint_method, path = endpoint.split(" ", 1)
        endpoint_method = endpoint_method.upper()
    method = method or endpoint_method
    if "{task_id}" in path:
        path = path.replace("{task_id}", quote(task_id, safe=""))
    body = action.get("body") if isinstance(action.get("body"), dict) else {}
    return {
        "kind": str(action.get("kind") or ""),
        "method": method,
        "path": path,
        "body": body,
        "reason": str(action.get("reason") or ""),
    }


def worker_metadata(path: str) -> dict[str, Any]:
    metadata_path = REPO_ROOT / path / "worker.json"
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def downstream_step_ids(contract: TaskContract, step_id: str) -> list[str]:
    downstream: list[str] = []
    seen: set[str] = set()
    changed = True
    while changed:
        changed = False
        for step in contract.worker_plan:
            if step.step_id in seen:
                continue
            if step_id in step.depends_on or any(dependency in seen for dependency in step.depends_on):
                seen.add(step.step_id)
                downstream.append(step.step_id)
                changed = True
    return downstream


def step_quality_checks(step_id: str) -> list[str]:
    checks_by_step = {
        "repository_survey": [
            "repository survey records dominant languages, likely entry points, and test surfaces",
            "survey excludes local runtime, model, cache, and generated artifact directories",
        ],
        "change_planning": [
            "change plan is scoped to the task goal and repository evidence",
            "plan records assumptions, risks, and candidate files before implementation",
        ],
        "implementation": [
            "patch manifest is auditable and separates intended edits from unresolved blockers",
            "implementation package must not silently mutate unrelated files",
        ],
        "verification": [
            "verification report names commands to run or explains why they are blocked",
            "test gaps are explicit and cannot be treated as success",
        ],
        "code_review": [
            "code review checks scope control, test evidence, and unsafe assumptions",
            "review returns ready, needs_revision, or blocked with focused revision steps",
        ],
        "finalize": [
            "final manifest lists deliverables, review status, blockers, and next safe action",
            "final package is ready only when review passes or blockers are explicit",
        ],
    }
    return checks_by_step.get(step_id, ["expected artifacts exist and satisfy the step purpose"])


def step_quality_matrix(contract: TaskContract) -> list[dict[str, Any]]:
    final_steps = ["code_review", "finalize"]
    matrix: list[dict[str, Any]] = []
    for step in contract.worker_plan:
        downstream = downstream_step_ids(contract, step.step_id)
        rerun_targets = [step.step_id] + [item for item in downstream if item not in final_steps] + final_steps
        deduped_targets: list[str] = []
        for target in rerun_targets:
            if target not in deduped_targets:
                deduped_targets.append(target)
        matrix.append(
            {
                "step_id": step.step_id,
                "worker": step.worker,
                "required_inputs": [
                    artifact
                    for dependency in step.depends_on
                    for artifact in next((candidate.expected_artifacts for candidate in contract.worker_plan if candidate.step_id == dependency), [])
                ],
                "expected_artifacts": step.expected_artifacts,
                "checks": step_quality_checks(step.step_id),
                "blockers": [
                    "missing expected artifact",
                    "artifact contradicts the task contract",
                    "worker hides test gaps or unsafe assumptions",
                ],
                "revision_targets": deduped_targets,
            }
        )
    return matrix


def oversight_plan(contract: TaskContract) -> dict[str, Any]:
    planned_step_ids = [step.step_id for step in contract.worker_plan]
    artifacts_by_role = {
        "survey": [artifact for artifact in contract.required_artifacts if artifact.endswith("/repo_survey.json")],
        "plan": [artifact for artifact in contract.required_artifacts if artifact.endswith("/change_plan.md")],
        "patch": [artifact for artifact in contract.required_artifacts if artifact.endswith("/patch_manifest.json")],
        "verification": [artifact for artifact in contract.required_artifacts if artifact.endswith("/verification_report.json")],
        "review": [artifact for artifact in contract.required_artifacts if artifact.endswith("/code_review.json")],
        "final": [artifact for artifact in contract.required_artifacts if artifact.endswith("/final_manifest.json")],
    }
    handoffs = [
        {
            "from_step": step.step_id,
            "to_steps": [candidate.step_id for candidate in contract.worker_plan if step.step_id in candidate.depends_on],
            "artifacts": step.expected_artifacts,
        }
        for step in contract.worker_plan
    ]
    return {
        "governor": contract.assigned_governor,
        "kind": "code_task_oversight",
        "quality_gates": contract.quality_gates,
        "completion_criteria": contract.completion_criteria,
        "non_goals": contract.non_goals,
        "artifact_roles": artifacts_by_role,
        "handoffs": handoffs,
        "step_quality_matrix": step_quality_matrix(contract),
        "final_review": {
            "critic_step": "code_review",
            "final_step": "finalize",
            "final_artifact": artifacts_by_role["final"][0] if artifacts_by_role["final"] else "",
            "deliverable_role": "final",
            "requires_critic_approval_or_blockers": True,
            "requires_gap_disclosure": True,
            "requires_evidence_trace": True,
        },
        "revision_policy": {
            "source_step": "code_review",
            "final_steps": ["code_review", "finalize"],
            "allowed_steps": planned_step_ids,
            "requires_downstream_rerun": True,
            "requires_focused_context": True,
            "requires_gap_disclosure": True,
        },
        "iteration_policy": {
            "controller": "WarmasterGateway",
            "recommended_endpoint": "POST /runs/{task_id}/start_research_loop_http",
            "max_revision_cycles": 3,
            "poll_endpoint": "GET /runs/{task_id}/orchestration?events_after=0",
            "auto_revision_triggers": [
                "code_review status is needs_revision or blocked",
                "verification_report has missing commands or failed checks",
                "patch_manifest is not auditable",
            ],
            "stop_conditions": [
                "final_manifest status is ready",
                "revision_plan is invalid",
                "revision plan fingerprint repeats without progress",
                "max_revision_cycles is reached",
                "external implementation authority or human input is required",
            ],
            "final_readiness_checks": [
                "review approval or explicit blockers are present",
                "verification commands or blockers are disclosed",
                "patch intent is traceable to task scope",
                "final package files exist and are readable",
            ],
        },
    }


def plan_actions(contract: dict[str, Any], ok: bool, errors: list[str], missing_workers: list[str], unavailable_workers: list[dict[str, Any]]) -> dict[str, Any]:
    actions = {
        "can_prepare_run": ok,
        "can_inspect_capabilities": True,
    }
    if ok:
        next_action = {
            "kind": "prepare_run",
            "method": "POST",
            "endpoint": "POST /prepare_run",
            "body": {
                "task": str(contract.get("goal") or ""),
                "task_id": str(contract.get("task_id") or ""),
            },
            "reason": "governor plan is valid and required workers are available",
        }
    else:
        reason = "governor plan failed validation"
        if missing_workers or unavailable_workers:
            reason = "required Mechanicum workers are missing or unavailable"
        elif errors:
            reason = "task contract failed validation"
        next_action = {
            "kind": "inspect_capabilities",
            "method": "GET",
            "endpoint": "GET /capabilities",
            "body": {},
            "reason": reason,
        }
    actions["next_action"] = next_action
    return actions


def payload_with_plan_view(payload: dict[str, Any]) -> dict[str, Any]:
    actions = payload.get("actions") if isinstance(payload.get("actions"), dict) else {}
    next_action = actions.get("next_action") if isinstance(actions.get("next_action"), dict) else {}
    contract = payload.get("contract") if isinstance(payload.get("contract"), dict) else {}
    pipeline = payload.get("pipeline") if isinstance(payload.get("pipeline"), dict) else {}
    task_id = str(contract.get("task_id") or "")
    ok = bool(payload.get("ok"))
    enriched = dict(payload)
    enriched.update(
        {
            "phase": "plan_ready" if ok else "plan_blocked",
            "decision": {
                "can_prepare_run": bool(actions.get("can_prepare_run")),
                "can_inspect_capabilities": bool(actions.get("can_inspect_capabilities")),
                "recommended_kind": str(next_action.get("kind") or ""),
                "recommended_endpoint": str(next_action.get("endpoint") or ""),
            },
            "display": {
                "headline": "Code plan is ready" if ok else "Code plan needs attention",
                "detail": str(next_action.get("reason") or "Ceraxia can prepare the run"),
                "severity": "info" if ok else "warning",
                "task_id": task_id,
                "step_count": int(pipeline.get("step_count") or 0),
            },
            "next_action": next_action,
            "client_action": executable_client_action(task_id, next_action),
        }
    )
    return enriched


@dataclass
class CeraxiaPlan:
    contract: TaskContract

    def to_dict(self) -> dict[str, Any]:
        contract = self.contract.to_dict()
        validation_errors = validate_task_contract_payload(contract)
        missing_workers: list[str] = []
        unavailable_workers: list[dict[str, Any]] = []
        resolved_workers: dict[str, Any] = {}
        for step in self.contract.worker_plan:
            worker = worker_by_name(step.worker)
            if worker is None:
                missing_workers.append(step.worker)
                continue
            worker_payload = worker.to_dict()
            metadata = worker_metadata(worker.path)
            if metadata:
                worker_payload["status"] = metadata.get("status", "")
                worker_payload["capabilities"] = metadata.get("capabilities", [])
            resolved_workers[step.worker] = worker_payload
            if metadata.get("status") == "planned" and step.worker not in {item.get("name") for item in unavailable_workers}:
                unavailable_workers.append(
                    {
                        "name": step.worker,
                        "status": "planned",
                        "port": worker.port,
                        "role": worker.role,
                        "path": worker.path,
                    }
                )
        ok = not missing_workers and not unavailable_workers and not validation_errors
        return {
            "ok": ok,
            "governor": "Ceraxia",
            "contract": contract,
            "validation": {"ok": not validation_errors, "errors": validation_errors},
            "pipeline": pipeline_status(self.contract, build_dispatch_packets(self.contract)),
            "resolved_workers": resolved_workers,
            "missing_workers": missing_workers,
            "unavailable_workers": unavailable_workers,
            "oversight": oversight_plan(self.contract),
            "actions": plan_actions(contract, ok, validation_errors, missing_workers, unavailable_workers),
        }


def plan_code_task(user_task: str, task_id: str | None = None) -> CeraxiaPlan:
    return CeraxiaPlan(contract=build_code_task_contract(user_task, task_id=task_id))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build a Ceraxia code task plan.")
    parser.add_argument("task", help="User task text")
    parser.add_argument("--task-id", default="", help="Stable task id")
    parser.add_argument("--run-dir", default="", help="Write contract and dispatch packets to this directory")
    args = parser.parse_args()
    plan = plan_code_task(args.task, task_id=args.task_id or None)
    if args.run_dir:
        status = write_pipeline_run(plan.contract, Path(args.run_dir), oversight=oversight_plan(plan.contract))
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
