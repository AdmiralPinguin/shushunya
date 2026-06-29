from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..contracts import TaskContract, build_lore_reconstruction_contract, validate_task_contract_payload
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


def oversight_plan(contract: TaskContract) -> dict[str, Any]:
    artifacts_by_role = {
        "source_map": [artifact for artifact in contract.required_artifacts if artifact.endswith("/source_map.json")],
        "source_snapshots": [artifact for artifact in contract.required_artifacts if artifact.endswith("/source_snapshots.json")],
        "evidence_notes": [artifact for artifact in contract.required_artifacts if artifact.endswith("/direct_event_notes.json")],
        "timeline": [artifact for artifact in contract.required_artifacts if artifact.endswith("/timeline.json")],
        "draft": [artifact for artifact in contract.required_artifacts if artifact.endswith("/reconstruction_ru.md")],
        "coverage": [artifact for artifact in contract.required_artifacts if artifact.endswith("/coverage_report.md")],
        "critic": [artifact for artifact in contract.required_artifacts if artifact.endswith("/critic_report.json")],
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
        "kind": "lore_reconstruction_oversight",
        "quality_gates": contract.quality_gates,
        "completion_criteria": contract.completion_criteria,
        "non_goals": contract.non_goals,
        "artifact_roles": artifacts_by_role,
        "handoffs": handoffs,
        "final_review": {
            "critic_step": "critic_review",
            "final_step": "finalize",
            "final_artifact": artifacts_by_role["final"][0] if artifacts_by_role["final"] else "",
            "deliverable_role": "draft",
            "requires_critic_approval_or_blockers": True,
            "requires_gap_disclosure": True,
            "requires_evidence_trace": True,
        },
        "revision_policy": {
            "source_step": "critic_review",
            "final_steps": ["critic_review", "finalize"],
            "requires_downstream_rerun": True,
            "requires_focused_context": True,
            "requires_gap_disclosure": True,
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
    if ok:
        phase = "plan_ready"
        headline = "Plan is ready"
        detail = str(next_action.get("reason") or "Governor can prepare the run")
        severity = "info"
    else:
        phase = "plan_blocked"
        headline = "Plan needs attention"
        detail = str(next_action.get("reason") or "Governor plan is blocked")
        severity = "warning"
    enriched = dict(payload)
    enriched.update(
        {
            "phase": phase,
            "decision": {
                "can_prepare_run": bool(actions.get("can_prepare_run")),
                "can_inspect_capabilities": bool(actions.get("can_inspect_capabilities")),
                "recommended_kind": str(next_action.get("kind") or ""),
                "recommended_endpoint": str(next_action.get("endpoint") or ""),
            },
            "display": {
                "headline": headline,
                "detail": detail,
                "severity": severity,
                "task_id": task_id,
                "step_count": int(pipeline.get("step_count") or 0),
            },
            "next_action": next_action,
            "client_action": executable_client_action(task_id, next_action),
        }
    )
    return enriched


@dataclass
class IskandarPlan:
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
            else:
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
            "governor": "IskandarKhayon",
            "contract": contract,
            "validation": {"ok": not validation_errors, "errors": validation_errors},
            "pipeline": pipeline_status(self.contract, build_dispatch_packets(self.contract)),
            "resolved_workers": resolved_workers,
            "missing_workers": missing_workers,
            "unavailable_workers": unavailable_workers,
            "oversight": oversight_plan(self.contract),
            "actions": plan_actions(contract, ok, validation_errors, missing_workers, unavailable_workers),
        }


def plan_lore_reconstruction(user_task: str, task_id: str | None = None) -> IskandarPlan:
    return IskandarPlan(contract=build_lore_reconstruction_contract(user_task, task_id=task_id))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build an Iskandar Khayon lore reconstruction plan.")
    parser.add_argument("task", help="User task text")
    parser.add_argument("--task-id", default="", help="Stable task id")
    parser.add_argument("--run-dir", default="", help="Write contract and dispatch packets to this directory")
    args = parser.parse_args()
    plan = plan_lore_reconstruction(args.task, task_id=args.task_id or None)
    if args.run_dir:
        status = write_pipeline_run(plan.contract, Path(args.run_dir), oversight=oversight_plan(plan.contract))
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        payload = plan.to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
