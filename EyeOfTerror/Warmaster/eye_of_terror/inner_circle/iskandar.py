from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..contracts import (
    TaskContract,
    build_lore_reconstruction_contract,
    build_research_writing_contract,
    classify_research_intent,
    validate_task_contract_payload,
)
from EyeOfTerror.common_protocol import governor_plan_from_contract, validate_protocol_payload
from ..pipeline import build_dispatch_packets, pipeline_status, write_pipeline_run
from ..registry import worker_by_name


REPO_ROOT = Path(__file__).resolve().parents[4]


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
        "corpus_ingestion": [
            "local corpus index exists and records whether user-provided primary texts are available",
            "supported local files are classified separately from web-discovered sources",
            "absence of local primary text is exposed as a coverage gap rather than hidden",
        ],
        "source_discovery": [
            "source map exists and contains classified source candidates",
            "source map distinguishes primary, official, wiki, community, unavailable, and uncertain sources",
            "direct-event usefulness is labeled before downstream extraction",
        ],
        "source_acquisition": [
            "snapshot artifact records fetched, blocked, binary, and render-required sources separately",
            "blocked or render-required sources remain visible as coverage gaps",
        ],
        "fact_extraction": [
            "research corpus exists and includes claims, events, arguments, evidence excerpts, confidence, and gaps",
            "direct event notes remain available for compatibility when events are present",
            "claims and events include confidence labels and source references",
            "interpretation and synthesis are separated from directly extracted evidence",
        ],
        "timeline": [
            "timeline orders direct events before consequences and interpretations",
            "contradictions and missing links are preserved as explicit uncertainty",
        ],
        "structure_mapping": [
            "structure_map exists and records timeline, source order, argument flow, or topic structure as appropriate",
            "timeline is populated for event tasks and preserved as compatibility artifact",
            "analytical tasks expose source_order and argument_flow instead of forcing an event chronology",
        ],
        "synthesis_planning": [
            "synthesis_plan exists and declares output_mode, sections, source requirements, and evidence trace",
            "sections requiring evidence list claim refs or are marked unsupported",
            "book tasks include book_outline and chapter_plan before drafting",
        ],
        "draft_reconstruction": [
            "draft uses research_corpus, synthesis_plan, output_mode, and structure/timeline artifacts as inputs",
            "coverage report names gaps and source limitations",
            "unsupported narrative invention is treated as a blocker",
        ],
        "critic_review": [
            "critic compares draft against contract, extracted facts, timeline, and coverage report",
            "critic returns pass, warnings, blockers, and focused revision steps",
            "critic must not approve when required direct-event artifacts are absent",
        ],
        "finalize": [
            "final manifest includes deliverable, package files, critic status, warnings, and blockers",
            "final package is ready only when critic passes or blockers are explicitly disclosed",
        ],
    }
    return checks_by_step.get(step_id, ["expected artifacts exist and satisfy the step purpose"])


def step_quality_matrix(contract: TaskContract) -> list[dict[str, Any]]:
    final_review_steps = ["critic_review", "finalize"]
    matrix: list[dict[str, Any]] = []
    for step in contract.worker_plan:
        downstream = downstream_step_ids(contract, step.step_id)
        rerun_targets = [step.step_id] + [item for item in downstream if item not in final_review_steps] + final_review_steps
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
                    "worker hides uncertainty required by the contract",
                ],
                "revision_targets": deduped_targets,
            }
        )
    return matrix


def oversight_plan(contract: TaskContract) -> dict[str, Any]:
    planned_step_ids = [step.step_id for step in contract.worker_plan]
    research_intent = classify_research_intent(contract.goal)
    required_workers: list[str] = []
    for step in contract.worker_plan:
        if step.worker not in required_workers:
            required_workers.append(step.worker)
    artifacts_by_role = {
        "corpus_index": [artifact for artifact in contract.required_artifacts if artifact.endswith("/corpus_index.json")],
        "source_map": [artifact for artifact in contract.required_artifacts if artifact.endswith("/source_map.json")],
        "source_snapshots": [artifact for artifact in contract.required_artifacts if artifact.endswith("/source_snapshots.json")],
        "research_corpus": [artifact for artifact in contract.required_artifacts if artifact.endswith("/research_corpus.json")],
        "evidence_notes": [artifact for artifact in contract.required_artifacts if artifact.endswith("/direct_event_notes.json")],
        "timeline": [artifact for artifact in contract.required_artifacts if artifact.endswith("/timeline.json")],
        "structure_map": [artifact for artifact in contract.required_artifacts if artifact.endswith("/structure_map.json")],
        "synthesis_plan": [artifact for artifact in contract.required_artifacts if artifact.endswith("/synthesis_plan.json")],
        "book_outline": [artifact for artifact in contract.required_artifacts if artifact.endswith("/book_outline.json")],
        "chapter_plan": [artifact for artifact in contract.required_artifacts if artifact.endswith("/chapter_plan.json")],
        "draft": [artifact for artifact in contract.required_artifacts if artifact.endswith("/reconstruction_ru.md")],
        "manuscript": [artifact for artifact in contract.required_artifacts if artifact.endswith("/manuscript_ru.md")],
        "fb2": [artifact for artifact in contract.required_artifacts if artifact.endswith("/manuscript.fb2")],
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
        "kind": "lore_reconstruction_oversight"
        if any("shallow wiki summary" in item for item in contract.non_goals)
        else "research_writing_oversight",
        "research_intent": research_intent,
        "pipeline_plan": {
            "intent": research_intent["intent"],
            "output_mode": research_intent["output_mode"],
            "required_depth": research_intent["required_depth"],
            "source_policy": research_intent["source_policy"],
            "needs_timeline": research_intent["needs_timeline"],
            "needs_chapters": research_intent["needs_chapters"],
            "chapter_count": research_intent.get("chapter_count", 0),
            "required_workers": required_workers,
            "steps": planned_step_ids,
        },
        "quality_gates": contract.quality_gates,
        "completion_criteria": contract.completion_criteria,
        "non_goals": contract.non_goals,
        "artifact_roles": artifacts_by_role,
        "handoffs": handoffs,
        "step_quality_matrix": step_quality_matrix(contract),
        "final_review": {
            "critic_step": "critic_review",
            "final_step": "finalize",
            "final_artifact": artifacts_by_role["final"][0] if artifacts_by_role["final"] else "",
            "deliverable_role": "fb2" if research_intent["output_mode"] in {"book_manuscript", "book_manuscript_with_timeline"} else "draft",
            "deliverable_artifacts": artifacts_by_role["fb2"]
            if research_intent["output_mode"] in {"book_manuscript", "book_manuscript_with_timeline"}
            else artifacts_by_role["draft"],
            "requires_critic_approval_or_blockers": True,
            "requires_gap_disclosure": True,
            "requires_evidence_trace": True,
            "output_mode": research_intent["output_mode"],
        },
        "revision_policy": {
            "source_step": "critic_review",
            "final_steps": ["critic_review", "finalize"],
            "allowed_steps": planned_step_ids,
            "section_rerun_targets": [
                step_id
                for step_id in [
                    "source_discovery",
                    "source_acquisition",
                    "source_rendering",
                    "fact_extraction",
                    "structure_mapping",
                    "timeline",
                    "synthesis_planning",
                    "draft_reconstruction",
                    "critic_review",
                ]
                if step_id in planned_step_ids
            ],
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
                "critic_report contains blockers or needs_revision",
                "final_manifest status is blocked or needs_revision",
                "corpus_requirements.required is true",
                "required event evidence is missing",
                "primary evidence minimum is not met for comprehensive tasks",
            ],
            "stop_conditions": [
                "final_manifest status is ready",
                "revision_plan is invalid",
                "revision plan fingerprint repeats without progress",
                "max_revision_cycles is reached",
                "external input or missing local corpus text is required",
            ],
            "final_readiness_checks": [
                "critic approval or explicit blockers are present",
                "source coverage and corpus requirements are disclosed",
                "event evidence trace is present",
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
        protocol_plan = governor_plan_from_contract(f"mission-{self.contract.task_id}", contract)
        validate_protocol_payload(protocol_plan, expected_type="governor_plan")
        return {
            "ok": ok,
            "governor": "IskandarKhayon",
            "contract": contract,
            "governor_plan": protocol_plan,
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


def plan_research_writing(user_task: str, task_id: str | None = None) -> IskandarPlan:
    return IskandarPlan(contract=build_research_writing_contract(user_task, task_id=task_id))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build an Iskandar Khayon research/writing plan.")
    parser.add_argument("task", help="User task text")
    parser.add_argument("--task-id", default="", help="Stable task id")
    parser.add_argument("--run-dir", default="", help="Write contract and dispatch packets to this directory")
    args = parser.parse_args()
    plan = plan_research_writing(args.task, task_id=args.task_id or None)
    if args.run_dir:
        status = write_pipeline_run(plan.contract, Path(args.run_dir), oversight=oversight_plan(plan.contract))
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        payload = plan.to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
