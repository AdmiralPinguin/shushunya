#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from planning_packet_contract import CONTRACT_VERSION


REQUIRED_REQUEST_FIELDS = [
    "kind",
    "contract_version",
    "run_id",
    "status",
    "target",
    "source",
    "repo_path",
    "task",
    "review_decision",
    "worker_status",
    "verification_status",
    "planning_review_decision",
    "feedback_findings",
    "worker_output_contract_sufficiency",
    "replan_focus",
    "required_return_artifacts",
    "suggested_planning_command",
]


def string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def validate_planning_feedback_request(request: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    missing = [field for field in REQUIRED_REQUEST_FIELDS if field not in request]
    if missing:
        problems.append(f"planning feedback request missing fields: {missing}")
    if request.get("kind") != "ceraxia_planning_feedback_request":
        problems.append("planning feedback request kind must be ceraxia_planning_feedback_request")
    if request.get("contract_version") != CONTRACT_VERSION:
        problems.append(f"planning feedback request contract_version must be {CONTRACT_VERSION}")
    if request.get("target") != "PlanningBrigade":
        problems.append("planning feedback request target must be PlanningBrigade")
    if request.get("source") != "Ceraxia.review_gate":
        problems.append("planning feedback request source must be Ceraxia.review_gate")
    findings = request.get("feedback_findings")
    if not isinstance(findings, list):
        problems.append("planning feedback request feedback_findings must be a list")
        findings = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict) or not str(finding.get("finding") or "").strip():
            problems.append(f"planning feedback request feedback_findings[{index}] must include finding text")
        if isinstance(finding, dict) and not str(finding.get("severity") or "").strip():
            problems.append(f"planning feedback request feedback_findings[{index}] must include severity")
    expected_status = "required" if findings else "not_required"
    if request.get("status") not in {"required", "not_required"}:
        problems.append("planning feedback request status must be required or not_required")
    elif request.get("status") != expected_status:
        problems.append("planning feedback request status must match feedback_findings")
    required_artifacts = string_list(request.get("required_return_artifacts"))
    for artifact in ["planning_packet.json", "implementation_brief.json", "worker_output_contract", "planning_review_gate"]:
        if artifact not in required_artifacts:
            problems.append(f"planning feedback request must require return artifact: {artifact}")
    if not string_list(request.get("replan_focus")):
        problems.append("planning feedback request replan_focus is required")
    command = request.get("suggested_planning_command")
    if not isinstance(command, list) or command[:2] != ["python3", "EyeOfTerror/Mechanicum/PlanningBrigade/planning_brigade.py"]:
        problems.append("planning feedback request suggested_planning_command must target PlanningBrigade CLI")
    worker_output = request.get("worker_output_contract_sufficiency")
    if not isinstance(worker_output, dict) or not str(worker_output.get("status") or "").strip():
        problems.append("planning feedback request worker_output_contract_sufficiency.status is required")
    if not str(request.get("task") or "").strip():
        problems.append("planning feedback request task is required")
    return problems


def build_replan_payload(request: dict[str, Any]) -> dict[str, Any]:
    findings = request.get("feedback_findings") if isinstance(request.get("feedback_findings"), list) else []
    finding_text = [
        str(item.get("finding") or "").strip()
        for item in findings
        if isinstance(item, dict) and str(item.get("finding") or "").strip()
    ]
    replan_focus = string_list(request.get("replan_focus"))
    required_artifacts = string_list(request.get("required_return_artifacts"))
    constraints = [
        "treat Ceraxia planning feedback as authoritative replan input",
        "preserve the original user task intent while repairing planning and handoff defects",
        "return authority to Ceraxia after rebuilding the planning packet and implementation brief",
    ]
    constraints.extend(f"feedback finding: {item}" for item in finding_text)
    constraints.extend(f"replan focus: {item}" for item in replan_focus)
    constraints.extend(f"required return artifact: {item}" for item in required_artifacts)
    return {
        "task": str(request.get("task") or ""),
        "repo_path": str(request.get("repo_path") or ""),
        "constraints": constraints,
        "requirements": replan_focus,
        "source_run_id": str(request.get("run_id") or ""),
        "source_feedback_status": str(request.get("status") or ""),
    }


def build_planning_feedback_intake(request: dict[str, Any]) -> dict[str, Any]:
    problems = validate_planning_feedback_request(request)
    findings = request.get("feedback_findings") if isinstance(request.get("feedback_findings"), list) else []
    replan_focus = string_list(request.get("replan_focus"))
    required_artifacts = string_list(request.get("required_return_artifacts"))
    replan_payload = build_replan_payload(request)
    return {
        "kind": "planning_brigade_feedback_intake",
        "contract_version": CONTRACT_VERSION,
        "run_id": str(request.get("run_id") or ""),
        "status": "blocked_invalid_request" if problems else "replan_required" if findings else "no_replan_required",
        "target": "PlanningBrigade",
        "source": "planning_feedback_request.json",
        "task": str(request.get("task") or ""),
        "repo_path": str(request.get("repo_path") or ""),
        "validation_problems": problems,
        "feedback_finding_count": len(findings),
        "replan_focus": replan_focus,
        "required_return_artifacts": required_artifacts,
        "replan_payload": replan_payload,
        "recommended_planning_actions": [
            {
                "action": "rebuild_planning_packet",
                "reason": "Ceraxia review found planning or handoff contract findings",
                "required_when": "feedback_finding_count > 0",
            },
            {
                "action": "refresh_implementation_brief",
                "reason": "CodeBrigade handoff must reflect the repaired planning packet",
                "required_when": "implementation_brief.json is in required_return_artifacts",
            },
            {
                "action": "rerun_planning_review_gate",
                "reason": "PlanningBrigade must prove the revised packet before Ceraxia accepts it",
                "required_when": "planning_review_gate is in required_return_artifacts",
            },
        ],
        "handoff_back_to": "Ceraxia",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Ceraxia planning feedback and build PlanningBrigade replan intake.")
    parser.add_argument("request", help="Path to planning_feedback_request.json")
    args = parser.parse_args()
    payload = json.loads(Path(args.request).read_text(encoding="utf-8"))
    intake = build_planning_feedback_intake(payload if isinstance(payload, dict) else {})
    print(json.dumps(intake, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if intake["status"] == "blocked_invalid_request" else 0


if __name__ == "__main__":
    raise SystemExit(main())
