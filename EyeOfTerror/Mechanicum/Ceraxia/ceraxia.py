#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from ceraxia_common import *  # noqa: F401,F403

from planning_brigade import build_planning_packet  # noqa: E402
from planning_packet_contract import validate_planning_packet as validate_planning_packet_contract  # noqa: E402
from code_brigade_adapter import build_worker_report  # noqa: E402
from diagnostic_repair_contract import execute_diagnostic_repair_request  # noqa: E402
from engineering_memory import build_engineering_memory_update  # noqa: E402
from execution_adapter import can_infer_guarded_natural_language_patch  # noqa: E402
from planning_department import build_planning_department_package  # noqa: E402
from verification_adapter import run_verification_commands  # noqa: E402
from repo_survey import survey_repository  # noqa: E402

from ceraxia_io import *  # noqa: F401,F403,E402
from brief_builder import *  # noqa: F401,F403,E402
from verification_report import *  # noqa: F401,F403,E402
from review_gate import *  # noqa: F401,F403,E402
from run_report import *  # noqa: F401,F403,E402




def run_ceraxia(task_input: CeraxiaInput) -> dict[str, Any]:
    execution_mode = normalize_execution_mode(task_input)
    dry_run = execution_mode_dry_run(execution_mode)
    run_id, run_dir = allocate_run_dir(
        task_input.runs_root,
        f"ceraxia-{utc_stamp()}-{task_slug(task_input.task, task_input.repo_path)}",
    )
    status = {
        "run_id": run_id,
        "state": "received",
        "lifecycle": ["received"],
        "next_action": "build planning packet",
    }
    task_payload = {
        "kind": "ceraxia_task",
        "contract_version": CONTRACT_VERSION,
        "task": task_input.task,
        "repo_path": task_input.repo_path,
        "execution_mode": execution_mode,
        "dry_run": dry_run,
        "execute_diagnostic_repair": task_input.execute_diagnostic_repair,
        "constraints": list(task_input.constraints),
        "verification_commands": list(task_input.verification_commands),
    }
    if isinstance(task_input.greenfield_model_guidance_replay, dict):
        task_payload["greenfield_model_guidance_replay"] = task_input.greenfield_model_guidance_replay
    write_json(run_dir / "task.json", task_payload)

    packet = build_planning_packet(task_payload)
    packet["execution_mode"] = execution_mode
    planning_problems = validate_planning_packet(packet)
    status["state"] = "planned" if not planning_problems else "failed"
    status["lifecycle"].append(status["state"])
    status["next_action"] = "survey repository" if not planning_problems else "repair planning packet"
    write_json(run_dir / "planning_packet.json", packet)

    survey = build_repo_survey(packet)
    status["state"] = "surveyed" if survey["repo_exists"] else "failed"
    status["lifecycle"].append(status["state"])
    status["next_action"] = "build implementation brief" if survey["repo_exists"] else "provide existing repo path"
    write_json(run_dir / "repo_survey.json", survey)

    brief = build_implementation_brief(packet, survey)
    planning_department = build_planning_department_package(packet, survey, brief)
    write_json(run_dir / "planning_department.json", planning_department)
    brief = attach_planning_department_to_brief(brief, planning_department)
    status["state"] = "implementation_ready" if not brief["blocked"] else "failed"
    status["lifecycle"].append(status["state"])
    status["next_action"] = "handoff to CodeBrigade" if not brief["blocked"] else "fix blockers before implementation"
    write_json(run_dir / "implementation_brief.json", brief)

    worker_report = build_worker_report(brief, dry_run)
    status["state"] = "implemented" if worker_report["status"] != "blocked" else "failed"
    status["lifecycle"].append(status["state"])
    status["next_action"] = "verify worker output" if worker_report["status"] != "blocked" else "repair implementation blockers"
    write_json(run_dir / "worker_report.json", worker_report)

    verification_report = build_verification_report(brief, worker_report, execute_verification=task_input.execute_verification)
    status["state"] = "verified" if verification_report["status"] in {"planned_only", "requires_execution", "passed"} else "failed"
    status["lifecycle"].append(status["state"])
    status["next_action"] = "review gate" if status["state"] == "verified" else "repair verification blockers"
    write_json(run_dir / "verification_report.json", verification_report)

    review = review_gate(packet, brief, worker_report, verification_report)
    status["state"] = "reviewed" if review["decision"] in {"dry_run_ready", "ready"} else "failed"
    status["lifecycle"].append(status["state"])
    status["next_action"] = "finalize run package" if status["state"] == "reviewed" else "repair review findings"
    write_json(run_dir / "review_gate.json", review)
    repair_request = build_diagnostic_repair_request(run_id, brief, worker_report, verification_report, review)
    write_json(run_dir / "diagnostic_repair_request.json", repair_request)
    planning_feedback_request = build_planning_feedback_request(run_id, packet, brief, worker_report, verification_report, review)
    write_json(run_dir / "planning_feedback_request.json", planning_feedback_request)
    engineering_memory_update = build_engineering_memory_update(brief, worker_report, verification_report, review)
    write_json(run_dir / "engineering_memory_update.json", engineering_memory_update)
    repair_execution_result: dict[str, Any] = {
        "kind": "code_brigade_execution_result",
        "contract_version": CONTRACT_VERSION,
        "status": "not_requested",
        "changed_files": [],
        "patch_summary": "",
        "verification_commands_executed": [],
        "blockers": [],
        "rollback_notes": "",
        "operation_results": [],
    }
    if task_input.execute_diagnostic_repair:
        repair_execution_result = execute_diagnostic_repair_request(repair_request)
        write_json(run_dir / "diagnostic_repair_execution_result.json", repair_execution_result)

    status["state"] = "finalized" if status["state"] == "reviewed" else "failed"
    status["lifecycle"].append(status["state"])
    status["next_action"] = build_final_next_action(status, worker_report, dry_run)
    artifacts = {
        "status": status,
        "planning_packet": packet,
        "planning_department": planning_department,
        "implementation_brief": brief,
        "worker_report": worker_report,
        "verification_report": verification_report,
        "review_gate": review,
        "diagnostic_repair_request": repair_request,
        "planning_feedback_request": planning_feedback_request,
        "diagnostic_repair_execution_result": repair_execution_result,
        "engineering_memory_update": engineering_memory_update,
    }
    write_json(run_dir / "status.json", status)
    readiness = build_execution_readiness(status, brief, worker_report, verification_report, review, dry_run)
    artifacts["execution_readiness"] = readiness
    write_text(run_dir / "final_report.md", final_report_markdown(run_id, artifacts))
    write_json(run_dir / "execution_readiness.json", readiness)
    evidence_matrix = build_evidence_matrix(brief, worker_report, verification_report, readiness)
    write_json(run_dir / "evidence_matrix.json", evidence_matrix)
    summary = build_run_summary(
        run_id,
        run_dir,
        status,
        brief,
        worker_report,
        review,
        readiness,
        evidence_matrix,
        engineering_memory=engineering_memory_update,
        planning_feedback_request=planning_feedback_request,
    )
    write_json(run_dir / "run_summary.json", summary)
    manifest = build_artifact_manifest(run_dir)
    write_json(run_dir / "artifact_manifest.json", manifest)
    audit = audit_run_package(run_dir)
    write_json(run_dir / "run_audit.json", audit)
    summary = build_run_summary(
        run_id,
        run_dir,
        status,
        brief,
        worker_report,
        review,
        readiness,
        evidence_matrix,
        engineering_memory=engineering_memory_update,
        planning_feedback_request=planning_feedback_request,
        package_audit_decision=str(audit.get("decision") or "blocked"),
    )
    write_json(run_dir / "run_summary.json", summary)
    manifest = build_artifact_manifest(run_dir)
    write_json(run_dir / "artifact_manifest.json", manifest)
    return {
        "ok": status["state"] == "finalized" and audit["decision"] == "passed",
        "package_ok": status["state"] == "finalized" and audit["decision"] == "passed",
        "ready_for_execution": readiness["decision"] == "ready_for_real_execution",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "state": status["state"],
        "audit_decision": audit["decision"],
        "execution_readiness": readiness["decision"],
        "summary": summary,
        "lifecycle": status["lifecycle"],
        "review_decision": review["decision"],
        "next_action": status["next_action"],
        "execution_mode": execution_mode,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Ceraxia's planning-to-review smoke controller.")
    parser.add_argument("--task", required=True)
    parser.add_argument("--repo-path", default=str(PROJECT_ROOT))
    parser.add_argument("--runs-root", type=Path, default=RUNS_ROOT)
    parser.add_argument("--mode", choices=sorted(EXECUTION_MODES), default="", help="Ceraxia execution mode.")
    parser.add_argument("--execute", action="store_true", help="Compatibility alias for --mode guarded_patch.")
    parser.add_argument("--execute-verification", action="store_true", help="Run allowlisted verification commands while keeping source mutation dry-run.")
    parser.add_argument("--execute-diagnostic-repair", action="store_true", help="Run the narrow CodeBrigade diagnostic repair adapter when review creates a repair request.")
    parser.add_argument("--constraint", action="append", default=[], help="Structured planning constraint. Can be repeated.")
    parser.add_argument("--verification-command", action="append", default=[], help="Structured verification command. Can be repeated.")
    args = parser.parse_args()
    result = run_ceraxia(
        CeraxiaInput(
            task=args.task,
            repo_path=args.repo_path,
            execution_mode=args.mode or ("guarded_patch" if args.execute else "dry_run"),
            dry_run=not args.execute if not args.mode else execution_mode_dry_run(args.mode),
            execute_verification=args.execute_verification,
            execute_diagnostic_repair=args.execute_diagnostic_repair,
            constraints=tuple(args.constraint),
            verification_commands=tuple(args.verification_command),
            runs_root=args.runs_root,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
