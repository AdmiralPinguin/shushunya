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


CERAXIA_ROOT = Path(__file__).resolve().parent
MECHANICUM_ROOT = CERAXIA_ROOT.parent
EYE_ROOT = MECHANICUM_ROOT.parent
PROJECT_ROOT = EYE_ROOT.parent
RUNS_ROOT = CERAXIA_ROOT / "runs"

import sys

PLANNING_PATH = str(MECHANICUM_ROOT / "PlanningBrigade")
if PLANNING_PATH not in sys.path:
    sys.path.insert(0, PLANNING_PATH)
CODE_BRIGADE_PATH = str(MECHANICUM_ROOT / "CodeBrigade")
if CODE_BRIGADE_PATH not in sys.path:
    sys.path.insert(0, CODE_BRIGADE_PATH)

from planning_brigade import build_planning_packet  # noqa: E402
from planning_packet_contract import validate_planning_packet as validate_planning_packet_contract  # noqa: E402
from code_brigade_adapter import build_worker_report  # noqa: E402
from verification_adapter import run_verification_commands  # noqa: E402
from repo_survey import survey_repository  # noqa: E402


CONTRACT_VERSION = "eye-mechanicum.v1"


LIFECYCLE = [
    "received",
    "planned",
    "surveyed",
    "implementation_ready",
    "implemented",
    "verified",
    "reviewed",
    "finalized",
]

REQUIRED_RUN_ARTIFACTS = [
    "task.json",
    "planning_packet.json",
    "repo_survey.json",
    "implementation_brief.json",
    "worker_report.json",
    "verification_report.json",
    "review_gate.json",
    "status.json",
    "final_report.md",
    "execution_readiness.json",
    "run_summary.json",
    "evidence_matrix.json",
]


@dataclass(frozen=True)
class CeraxiaInput:
    task: str
    repo_path: str
    dry_run: bool = True
    execute_verification: bool = False
    constraints: tuple[str, ...] = ()
    verification_commands: tuple[str, ...] = ()
    runs_root: Path = RUNS_ROOT


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def task_slug(task: str) -> str:
    words = re.findall(r"[a-zA-Z0-9а-яА-ЯёЁ]+", task.lower())
    slug = "-".join(words[:6]) or "task"
    digest = hashlib.sha1(task.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def validate_planning_packet(packet: dict[str, Any]) -> list[str]:
    return validate_planning_packet_contract(packet)


def build_repo_survey(packet: dict[str, Any]) -> dict[str, Any]:
    survey_request = packet["repo_survey_request"]
    return survey_repository(
        str(survey_request.get("repo_path") or PROJECT_ROOT),
        survey_request.get("focus", []) if isinstance(survey_request.get("focus"), list) else [],
        survey_request.get("exclude_patterns", []) if isinstance(survey_request.get("exclude_patterns"), list) else [],
        survey_request.get("path_hints", []) if isinstance(survey_request.get("path_hints"), list) else [],
    )


def build_survey_quality_gate(packet: dict[str, Any], survey: dict[str, Any]) -> dict[str, Any]:
    triage = packet.get("task_triage") if isinstance(packet.get("task_triage"), dict) else {}
    task_kinds = set(triage.get("task_kinds", []) if isinstance(triage.get("task_kinds"), list) else [])
    risk_level = triage.get("risk_level", "high")
    candidate_files = survey.get("candidate_files") if isinstance(survey.get("candidate_files"), list) else []
    test_files = survey.get("test_files") if isinstance(survey.get("test_files"), list) else []
    missing_path_hints = survey.get("missing_path_hints") if isinstance(survey.get("missing_path_hints"), list) else []
    unsafe_path_hints = survey.get("unsafe_path_hints") if isinstance(survey.get("unsafe_path_hints"), list) else []
    blockers: list[str] = []
    warnings: list[str] = []
    if not survey.get("repo_exists"):
        blockers.append("repository does not exist")
    if unsafe_path_hints:
        blockers.append("unsafe explicit path hints: " + ", ".join(str(item) for item in unsafe_path_hints))
    if missing_path_hints:
        blockers.append("explicit path hints were not found: " + ", ".join(str(item) for item in missing_path_hints))
    if not candidate_files:
        blockers.append("repository survey found no candidate source/config/documentation files")
    if risk_level == "high" and not test_files:
        blockers.append("high-risk task has no discovered test surface")
    elif not test_files:
        warnings.append("repository survey found no test files")
    if survey.get("truncated"):
        warnings.append("repository survey reached file limit")
    if survey.get("python_symbols_truncated"):
        warnings.append("python symbol survey reached file limit")
    if survey.get("source_summaries_truncated"):
        warnings.append("source summary survey reached file limit")
    return {
        "kind": "ceraxia_survey_quality_gate",
        "decision": "blocked" if blockers else "passed",
        "risk_level": risk_level,
        "task_kinds": sorted(task_kinds),
        "candidate_file_count": len(candidate_files),
        "test_file_count": len(test_files),
        "missing_path_hints": missing_path_hints,
        "unsafe_path_hints": unsafe_path_hints,
        "blockers": blockers,
        "warnings": warnings,
    }


def build_execution_intent(packet: dict[str, Any], dry_run: bool | None = None) -> dict[str, Any]:
    task = str(packet.get("task") or "")
    has_explicit_patch = "CERAXIA_PATCH:" in task
    mode = "explicit_patch_execution" if has_explicit_patch else "planning_handoff_only"
    blockers: list[str] = []
    if dry_run is True:
        blockers.append("dry run requested; source mutation is intentionally skipped")
    if not has_explicit_patch:
        blockers.append("unshaped source mutation requires a future CodeBrigade autonomous execution adapter")
    return {
        "kind": "ceraxia_code_brigade_execution_intent",
        "contract_version": CONTRACT_VERSION,
        "mode": mode,
        "adapter_capability": "explicit_patch_adapter_only",
        "explicit_patch_present": has_explicit_patch,
        "real_execution_supported": has_explicit_patch,
        "dry_run_requested": bool(dry_run) if dry_run is not None else False,
        "blockers": blockers,
        "required_next_adapter": "" if has_explicit_patch else "autonomous CodeBrigade source-edit adapter",
    }


def build_implementation_brief(packet: dict[str, Any], survey: dict[str, Any]) -> dict[str, Any]:
    triage = packet.get("task_triage") if isinstance(packet.get("task_triage"), dict) else {}
    verification = packet.get("verification_strategy") if isinstance(packet.get("verification_strategy"), dict) else {}
    risks = packet.get("risk_register") if isinstance(packet.get("risk_register"), dict) else {}
    quality = packet.get("quality_bar") if isinstance(packet.get("quality_bar"), dict) else {}
    handoff = packet.get("code_brigade_handoff") if isinstance(packet.get("code_brigade_handoff"), dict) else {}
    planning_problems = validate_planning_packet(packet)
    planning_review = packet.get("planning_review_gate") if isinstance(packet.get("planning_review_gate"), dict) else {}
    survey_quality = build_survey_quality_gate(packet, survey)
    blocked = bool(planning_problems) or not survey["repo_exists"] or planning_review.get("decision") == "blocked" or survey_quality["decision"] == "blocked"
    blockers = [f"planning validation failed: {problem}" for problem in planning_problems]
    if not survey["repo_exists"]:
        blockers.append("repo survey or planning validation is incomplete")
    blockers.extend(str(item) for item in survey_quality["blockers"])
    return {
        "kind": "ceraxia_code_brigade_implementation_brief",
        "contract_version": CONTRACT_VERSION,
        "owner": "Ceraxia",
        "target": "CodeBrigade",
        "task": str(packet.get("task") or ""),
        "repo_path": survey["repo_path"],
        "task_kinds": triage.get("task_kinds") if isinstance(triage.get("task_kinds"), list) else [],
        "risk_level": triage.get("risk_level") if triage.get("risk_level") in {"low", "medium", "high"} else "high",
        "selected_strategy": packet.get("design_options", {}).get("selected_strategy", ""),
        "allowed_scope": [
            "candidate files identified by repository survey",
            "tests directly covering the requested behavior",
            "documentation only when needed to preserve the contract",
        ],
        "forbidden_approaches": [
            "hardcoded one-off behavior",
            "broad rewrite without repo evidence",
            "editing tests to fit a broken patch",
            "claiming verification without command output or explicit blocker",
        ],
        "expected_artifacts": [
            "worker_report.json",
            "verification_report.json",
            "final_report.md",
        ],
        "required_verification": verification,
        "surface_verification_matrix": packet.get("surface_verification_matrix", {}),
        "surface_package_matrix": packet.get("surface_package_matrix", {}),
        "survey_quality_gate": survey_quality,
        "acceptance_gates": risks.get("acceptance_gates") if isinstance(risks.get("acceptance_gates"), list) else [],
        "quality_bar": quality,
        "acceptance_contract": packet.get("acceptance_contract", {}),
        "expert_quality_plan": packet.get("expert_quality_plan", {}),
        "implementation_brief_blueprint": packet.get("implementation_brief_blueprint", {}),
        "implementation_work_packages": packet.get("implementation_work_packages", {}),
        "planning_review_gate": planning_review,
        "planning_dependency_map": packet.get("dependency_map", {}),
        "work_breakdown": packet.get("work_breakdown", {}),
        "impact_analysis": packet.get("impact_analysis", {}),
        "execution_forecast": packet.get("execution_forecast", {}),
        "execution_intent": build_execution_intent(packet),
        "code_brigade_handoff": handoff,
        "repo_survey_evidence": {
            "candidate_files": survey.get("candidate_files", []),
            "test_files": survey.get("test_files", []),
            "path_hints": survey.get("path_hints", []),
            "existing_path_hints": survey.get("existing_path_hints", []),
            "missing_path_hints": survey.get("missing_path_hints", []),
            "unsafe_path_hints": survey.get("unsafe_path_hints", []),
            "entrypoint_candidates": survey.get("entrypoint_candidates", []),
            "python_symbols": survey.get("python_symbols", []),
            "source_summaries": survey.get("source_summaries", []),
            "local_import_edges": survey.get("local_import_edges", []),
            "generic_import_edges": survey.get("generic_import_edges", []),
            "recommended_read_order": survey.get("recommended_read_order", []),
            "survey_truncated": bool(survey.get("truncated")),
            "max_files_scanned": survey.get("max_files_scanned", 0),
            "python_symbols_truncated": bool(survey.get("python_symbols_truncated")),
            "max_python_symbol_files": survey.get("max_python_symbol_files", 0),
            "source_summaries_truncated": bool(survey.get("source_summaries_truncated")),
            "max_source_summary_files": survey.get("max_source_summary_files", 0),
        },
        "suggested_verification_commands": survey.get("suggested_verification_commands", []),
        "blocked": blocked,
        "blockers": blockers,
    }


def build_verification_report(brief: dict[str, Any], worker_report: dict[str, Any], execute_verification: bool = False) -> dict[str, Any]:
    strategy = brief["required_verification"]
    commands = list(strategy.get("targeted_commands", []))
    for command in brief.get("suggested_verification_commands", []):
        if command not in commands:
            commands.append(command)
    executable_commands = [
        command
        for command in commands
        if not command.startswith("rerun ") and "<" not in command and ">" not in command
    ]
    dry_run = worker_report["dry_run"]
    blocked = brief["blocked"] or worker_report["status"] == "blocked"
    execution = run_verification_commands(executable_commands, brief.get("repo_path", ""), execute=execute_verification) if executable_commands and not blocked else {
        "kind": "code_brigade_verification_execution",
        "contract_version": CONTRACT_VERSION,
        "status": "blocked" if blocked else "passed",
        "execute": execute_verification,
        "repo_path": brief.get("repo_path", ""),
        "results": [],
        "blockers": brief.get("blockers", []) if blocked else [],
    }
    if blocked:
        status = "blocked"
    elif execute_verification:
        status = execution["status"] if executable_commands else "requires_execution"
    else:
        status = "planned_only" if dry_run else "requires_execution"
    return {
        "kind": "ceraxia_verification_report",
        "status": status,
        "commands_planned": commands,
        "commands_executable": executable_commands,
        "commands_executed": [item for item in execution.get("results", []) if item.get("status") != "planned"],
        "verification_execution": execution,
        "negative_tests_required": strategy.get("negative_tests", []),
        "broad_verification_required": bool(strategy.get("broad_verification_required")),
        "blockers": execution.get("blockers", []) if execution.get("blockers") else (brief.get("blockers", []) if blocked else []),
        "dry_run": dry_run,
        "execute_verification": execute_verification,
    }


def meaningful_executed_commands(verification_report: dict[str, Any]) -> list[dict[str, Any]]:
    commands = verification_report.get("commands_executed") if isinstance(verification_report.get("commands_executed"), list) else []
    return [item for item in commands if isinstance(item, dict) and item.get("status") in {"passed", "failed", "blocked"}]


def command_texts(commands: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("command", "")) for item in commands if isinstance(item, dict)]


def has_test_command(commands: list[dict[str, Any]]) -> bool:
    return any("pytest" in command or "test" in command or "unittest" in command for command in command_texts(commands))


def has_source_command(commands: list[dict[str, Any]]) -> bool:
    return any("py_compile" in command or "pytest" in command or "test" in command or "unittest" in command for command in command_texts(commands))


def surface_evidence_rows(surface_rows: list[Any], verification_report: dict[str, Any]) -> list[dict[str, Any]]:
    planned_commands = verification_report.get("commands_planned") if isinstance(verification_report.get("commands_planned"), list) else []
    executed_commands = meaningful_executed_commands(verification_report)
    negative_tests = verification_report.get("negative_tests_required") if isinstance(verification_report.get("negative_tests_required"), list) else []
    verification_status = str(verification_report.get("status", ""))
    rows: list[dict[str, Any]] = []
    for surface_row in surface_rows:
        if not isinstance(surface_row, dict):
            continue
        surface = str(surface_row.get("surface", ""))
        blockers = surface_row.get("blockers") if isinstance(surface_row.get("blockers"), list) else []
        if blockers:
            status = "blocked"
            reason = "surface planning row has blockers"
        elif verification_status in {"failed", "blocked"}:
            status = verification_status
            reason = f"verification report status is {verification_status}"
        elif not planned_commands:
            status = "missing"
            reason = "no verification command is planned"
        elif not executed_commands:
            status = "planned_only"
            reason = "verification is planned but not executed"
        elif surface == "source_behavior" and has_source_command(executed_commands):
            status = "executed"
            reason = "source command executed"
        elif surface == "test_surface" and has_test_command(executed_commands):
            status = "executed"
            reason = "test command executed"
        elif surface in {"public_api_contract", "security_boundary", "data_compatibility", "concurrency_runtime", "runtime_configuration"}:
            if negative_tests and has_test_command(executed_commands):
                status = "executed"
                reason = "negative or compatibility test command executed"
            else:
                status = "partial"
                reason = "executed commands do not directly prove this high-risk surface"
        elif executed_commands:
            status = "partial"
            reason = "some verification executed, but this surface has no direct evidence"
        else:
            status = "planned_only"
            reason = "verification is planned but not executed"
        rows.append(
            {
                "surface": surface,
                "status": status,
                "reason": reason,
                "covered_by": surface_row.get("covered_by", []) if isinstance(surface_row.get("covered_by"), list) else [],
                "evidence_needed": surface_row.get("evidence_needed", []) if isinstance(surface_row.get("evidence_needed"), list) else [],
            }
        )
    return rows


def surface_status_from_rows(rows: list[dict[str, Any]]) -> str:
    statuses = {row.get("status") for row in rows}
    if not rows:
        return "missing"
    if "blocked" in statuses:
        return "blocked"
    if "failed" in statuses:
        return "failed"
    if statuses == {"executed"}:
        return "executed"
    if "executed" in statuses or "partial" in statuses:
        return "partial"
    if "missing" in statuses:
        return "missing"
    return "planned_only"


def review_gate(
    packet: dict[str, Any],
    brief: dict[str, Any],
    worker_report: dict[str, Any],
    verification_report: dict[str, Any],
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    commands_planned = verification_report.get("commands_planned") if isinstance(verification_report.get("commands_planned"), list) else []
    commands_executed = verification_report.get("commands_executed") if isinstance(verification_report.get("commands_executed"), list) else []
    meaningful_commands_executed = meaningful_executed_commands(verification_report)
    negative_tests = verification_report.get("negative_tests_required", [])
    verification_sufficiency = {
        "risk_level": brief.get("risk_level", "high"),
        "status": "executed" if meaningful_commands_executed else ("planned_only" if commands_planned else "missing"),
        "commands_planned_count": len(commands_planned),
        "commands_executed_count": len(commands_executed),
        "meaningful_commands_executed_count": len(meaningful_commands_executed),
        "negative_tests_required_count": len(negative_tests) if isinstance(negative_tests, list) else 0,
        "broad_verification_required": bool(verification_report.get("broad_verification_required")),
    }
    surface_matrix = brief.get("surface_verification_matrix") if isinstance(brief.get("surface_verification_matrix"), dict) else {}
    surface_rows = surface_matrix.get("rows") if isinstance(surface_matrix.get("rows"), list) else []
    surface_blockers = surface_matrix.get("blockers") if isinstance(surface_matrix.get("blockers"), list) else []
    surface_evidence = surface_evidence_rows(surface_rows, verification_report)
    surface_status = "blocked" if surface_blockers else surface_status_from_rows(surface_evidence)
    surface_verification_sufficiency = {
        "planned_complete": surface_matrix.get("complete") is True,
        "status": surface_status,
        "surface_count": len(surface_rows),
        "blocker_count": len(surface_blockers),
        "executed_evidence": bool(meaningful_commands_executed),
        "surface_evidence": surface_evidence,
    }
    package_statuses = worker_report.get("work_package_statuses") if isinstance(worker_report.get("work_package_statuses"), list) else []
    package_status_counts = {
        status: sum(1 for item in package_statuses if isinstance(item, dict) and item.get("status") == status)
        for status in ("planned", "implemented", "blocked")
    }
    package_status_sufficiency = {
        "package_count": len(package_statuses),
        "status_counts": package_status_counts,
        "blocked_package_ids": [
            str(item.get("package_id") or "")
            for item in package_statuses
            if isinstance(item, dict) and item.get("status") == "blocked"
        ],
    }
    surface_package_matrix = brief.get("surface_package_matrix") if isinstance(brief.get("surface_package_matrix"), dict) else {}
    surface_package_rows = surface_package_matrix.get("rows") if isinstance(surface_package_matrix.get("rows"), list) else []
    package_status_ids = {
        str(item.get("package_id") or "")
        for item in package_statuses
        if isinstance(item, dict) and item.get("package_id")
    }
    surface_package_sufficiency_rows: list[dict[str, Any]] = []
    missing_surface_package_statuses: list[str] = []
    for row in surface_package_rows:
        if not isinstance(row, dict):
            continue
        package_ids = [str(item) for item in row.get("package_ids", []) if isinstance(item, str)]
        missing_ids = [package_id for package_id in package_ids if package_id not in package_status_ids]
        missing_surface_package_statuses.extend(missing_ids)
        surface_package_sufficiency_rows.append(
            {
                "surface": str(row.get("surface") or ""),
                "package_ids": package_ids,
                "missing_status_package_ids": missing_ids,
                "blockers": row.get("blockers", []) if isinstance(row.get("blockers"), list) else [],
            }
        )
    surface_package_sufficiency = {
        "planned_complete": surface_package_matrix.get("complete") is True,
        "surface_count": len(surface_package_rows),
        "rows": surface_package_sufficiency_rows,
        "missing_status_package_ids": sorted(set(missing_surface_package_statuses)),
    }
    for problem in validate_planning_packet(packet):
        findings.append({"severity": "blocker", "finding": problem})
    if not worker_report.get("implementation_brief_acknowledged", False):
        findings.append({"severity": "blocker", "finding": "implementation brief was not acknowledged"})
    if worker_report["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "worker report is blocked"})
    if package_status_sufficiency["blocked_package_ids"]:
        findings.append({"severity": "blocker", "finding": "work packages are blocked: " + ", ".join(package_status_sufficiency["blocked_package_ids"])})
    if surface_package_matrix.get("complete") is False:
        findings.append({"severity": "blocker", "finding": "surface package matrix has blockers"})
    if surface_package_sufficiency["missing_status_package_ids"]:
        findings.append({"severity": "blocker", "finding": "surface package matrix references packages without worker status: " + ", ".join(surface_package_sufficiency["missing_status_package_ids"])})
    if worker_report["dry_run"] and package_status_counts["planned"]:
        warnings.append({"severity": "warning", "finding": "work packages are planned but not implemented"})
    if negative_tests and verification_report["status"] not in {"planned_only", "requires_execution", "passed"}:
        findings.append({"severity": "blocker", "finding": "negative tests are missing or not planned"})
    if verification_report.get("status") in {"failed", "blocked"}:
        findings.append({"severity": "blocker", "finding": f"verification report status is {verification_report.get('status')}"})
    if verification_report.get("broad_verification_required") and not verification_report.get("commands_planned"):
        findings.append({"severity": "blocker", "finding": "broad verification is required but no commands are planned"})
    if verification_report.get("broad_verification_required") and verification_report.get("status") == "planned_only":
        warnings.append({"severity": "warning", "finding": "broad verification is planned but not executed"})
    if surface_blockers:
        findings.append({"severity": "blocker", "finding": "surface verification matrix has blockers"})
    if surface_rows and not meaningful_commands_executed:
        warnings.append({"severity": "warning", "finding": "surface verification coverage is planned but not executed"})
    if surface_status == "partial":
        warnings.append({"severity": "warning", "finding": "surface verification has only partial executed evidence"})
    if brief.get("risk_level") == "high" and surface_status == "partial" and meaningful_commands_executed:
        findings.append({"severity": "blocker", "finding": "high-risk task has only partial executed surface evidence"})
    if verification_report.get("commands_executable") and not verification_report.get("commands_executed"):
        warnings.append({"severity": "warning", "finding": "executable verification commands exist but were not run"})
    if brief.get("risk_level") == "high" and not commands_executed:
        warnings.append({"severity": "warning", "finding": "high-risk task has no executed verification evidence yet"})
    repo_evidence = brief.get("repo_survey_evidence") if isinstance(brief.get("repo_survey_evidence"), dict) else {}
    if repo_evidence.get("survey_truncated"):
        warnings.append({"severity": "warning", "finding": "repository survey reached file limit; coverage is partial"})
    if repo_evidence.get("python_symbols_truncated"):
        warnings.append({"severity": "warning", "finding": "python symbol survey reached file limit; dependency evidence is partial"})
    if repo_evidence.get("source_summaries_truncated"):
        warnings.append({"severity": "warning", "finding": "source summary survey reached file limit; multi-language evidence is partial"})
    if any("hardcode" in approach for approach in brief.get("forbidden_approaches", [])):
        hardcode_rejected = any(
            option.get("name") == "hardcode" and option.get("decision") == "reject"
            for option in packet.get("design_options", {}).get("options", [])
        )
        if not hardcode_rejected:
            findings.append({"severity": "blocker", "finding": "hardcode rejection is missing"})
    decision = "blocked" if any(item["severity"] == "blocker" for item in findings) else "ready"
    if worker_report["dry_run"] and decision == "ready":
        decision = "dry_run_ready"
    return {
        "kind": "ceraxia_review_gate",
        "decision": decision,
        "findings": findings,
        "warnings": warnings,
        "verification_sufficiency": verification_sufficiency,
        "surface_verification_sufficiency": surface_verification_sufficiency,
        "package_status_sufficiency": package_status_sufficiency,
        "surface_package_sufficiency": surface_package_sufficiency,
        "checked_against": [
            "planning packet completeness",
            "strategy approval",
            "scope control",
            "verification strategy",
            "surface verification coverage",
            "surface package ownership",
            "work package status coverage",
            "worker report honesty",
        ],
    }


def final_report_markdown(run_id: str, artifacts: dict[str, dict[str, Any]]) -> str:
    packet = artifacts["planning_packet"]
    brief = artifacts["implementation_brief"]
    review = artifacts["review_gate"]
    verification = artifacts["verification_report"]
    readiness = artifacts["execution_readiness"]
    worker_report = artifacts.get("worker_report", {}) if isinstance(artifacts.get("worker_report"), dict) else {}
    execution_result = worker_report.get("execution_result") if isinstance(worker_report.get("execution_result"), dict) else {}
    preflight = execution_result.get("preflight") if isinstance(execution_result.get("preflight"), dict) else {}
    package_statuses = worker_report.get("work_package_statuses") if isinstance(worker_report.get("work_package_statuses"), list) else []
    package_status_counts = {
        status: sum(1 for item in package_statuses if isinstance(item, dict) and item.get("status") == status)
        for status in ("planned", "implemented", "blocked")
    }
    planning_review = brief.get("planning_review_gate") if isinstance(brief.get("planning_review_gate"), dict) else {}
    survey_quality = brief.get("survey_quality_gate") if isinstance(brief.get("survey_quality_gate"), dict) else {}
    work_breakdown = brief.get("work_breakdown") if isinstance(brief.get("work_breakdown"), dict) else {}
    expert_plan = brief.get("expert_quality_plan") if isinstance(brief.get("expert_quality_plan"), dict) else {}
    execution_intent = brief.get("execution_intent") if isinstance(brief.get("execution_intent"), dict) else {}
    work_phases = work_breakdown.get("phases") if isinstance(work_breakdown.get("phases"), list) else []
    blockers = readiness.get("blockers", [])
    warnings = review.get("warnings", [])
    commands_executed = verification.get("commands_executed", [])
    commands_planned = verification.get("commands_planned", [])
    repo_evidence = brief.get("repo_survey_evidence", {}) if isinstance(brief.get("repo_survey_evidence"), dict) else {}
    work_packages = brief.get("implementation_work_packages", {}) if isinstance(brief.get("implementation_work_packages"), dict) else {}
    packages = work_packages.get("packages") if isinstance(work_packages.get("packages"), list) else []
    covered_package_surfaces = sorted(
        {
            surface
            for package in packages
            if isinstance(package, dict)
            for surface in package.get("impact_surfaces", [])
            if isinstance(surface, str) and surface
        }
    )
    lines = [
        f"# Ceraxia Run {run_id}",
        "",
        f"Task: {packet['task']}",
        f"Lifecycle status: {artifacts['status']['state']}",
        f"Package status: {'complete' if artifacts['status']['state'] == 'finalized' else 'incomplete'}",
        f"Execution readiness: {readiness['decision']}",
        f"Risk: {brief['risk_level']}",
        f"Strategy: {brief['selected_strategy']}",
        f"Expert quality level: {expert_plan.get('level', '')}",
        f"Expert quality required: {str(bool(expert_plan.get('required_for_expert_gate'))).lower()}",
        f"Review decision: {review['decision']}",
        f"Planning review decision: {planning_review.get('decision', '')}",
        f"Planning review score: {planning_review.get('score', '')}",
        f"Planning work phases: {len(work_phases)}",
        f"Implementation work packages: {len(packages)}",
        f"Work package covered surfaces: {len(covered_package_surfaces)}",
        f"Work package statuses: planned={package_status_counts['planned']} implemented={package_status_counts['implemented']} blocked={package_status_counts['blocked']}",
        f"Survey quality decision: {survey_quality.get('decision', '')}",
        f"Verification status: {verification['status']}",
        f"Surface verification status: {review.get('surface_verification_sufficiency', {}).get('status', '')}",
        f"Verification commands planned: {len(commands_planned)}",
        f"Verification commands executed: {len(commands_executed)}",
        f"Worker status: {worker_report.get('status', '')}",
        f"Execution policy status: {worker_report.get('execution_policy_status', '')}",
        f"Execution result status: {execution_result.get('status', '')}",
        f"Execution preflight ok: {preflight.get('ok') if preflight else 'n/a'}",
        f"Execution intent: {execution_intent.get('mode', '')}",
        f"Execution adapter capability: {execution_intent.get('adapter_capability', '')}",
        "",
        "## Readiness",
        "",
    ]
    if blockers:
        lines.extend(f"- BLOCKER: {item}" for item in blockers)
    else:
        lines.append("- ready for real execution")
    if warnings:
        lines.extend(f"- WARNING: {item['finding']}" for item in warnings)
    lines.extend(
        [
            "",
            "## Evidence",
            "",
            f"- candidate files: {len(repo_evidence.get('candidate_files', []))}",
            f"- test files: {len(repo_evidence.get('test_files', []))}",
            f"- recommended read order entries: {len(repo_evidence.get('recommended_read_order', []))}",
            f"- python symbol reports: {len(repo_evidence.get('python_symbols', []))}",
            f"- source summaries: {len(repo_evidence.get('source_summaries', []))}",
            f"- repository survey partial: {str(bool(repo_evidence.get('survey_truncated'))).lower()}",
            f"- python symbol survey partial: {str(bool(repo_evidence.get('python_symbols_truncated'))).lower()}",
            f"- source summary survey partial: {str(bool(repo_evidence.get('source_summaries_truncated'))).lower()}",
            f"- max files scanned: {repo_evidence.get('max_files_scanned', 0)}",
            f"- max python symbol files: {repo_evidence.get('max_python_symbol_files', 0)}",
            f"- max source summary files: {repo_evidence.get('max_source_summary_files', 0)}",
            f"- code brigade handoff steps: {len(brief.get('code_brigade_handoff', {}).get('steps', []))}",
            f"- expert tradeoffs: {len(expert_plan.get('tradeoff_register', [])) if isinstance(expert_plan.get('tradeoff_register'), list) else 0}",
            f"- expert rollback requirements: {len(expert_plan.get('rollback_strategy', [])) if isinstance(expert_plan.get('rollback_strategy'), list) else 0}",
            f"- expert review checklist items: {len(expert_plan.get('review_checklist', [])) if isinstance(expert_plan.get('review_checklist'), list) else 0}",
            "",
        ]
    )
    lines.extend(
        [
            "## Artifacts",
            "",
            "- task.json",
            "- planning_packet.json",
            "- repo_survey.json",
            "- implementation_brief.json",
            "- worker_report.json",
            "- verification_report.json",
            "- review_gate.json",
            "- status.json",
            "- final_report.md",
            "- execution_readiness.json",
            "- run_summary.json",
            "- evidence_matrix.json",
            "- artifact_manifest.json",
            "- run_audit.json",
            "",
            "## Next Action",
            "",
            artifacts["status"]["next_action"],
            "",
        ]
    )
    return "\n".join(lines)


def build_artifact_manifest(run_dir: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    missing: list[str] = []
    for name in REQUIRED_RUN_ARTIFACTS:
        path = run_dir / name
        if not path.exists():
            missing.append(name)
            entries.append({"path": name, "exists": False, "size_bytes": 0, "sha256": ""})
            continue
        data = path.read_bytes()
        entries.append(
            {
                "path": name,
                "exists": True,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return {
        "kind": "ceraxia_run_artifact_manifest",
        "required_artifacts": REQUIRED_RUN_ARTIFACTS,
        "entries": entries,
        "missing": missing,
        "complete": not missing,
    }


def audit_run_package(run_dir: Path) -> dict[str, Any]:
    manifest = build_artifact_manifest(run_dir)
    findings: list[dict[str, str]] = []
    if manifest["missing"]:
        findings.append({"severity": "blocker", "finding": f"missing artifacts: {', '.join(manifest['missing'])}"})
    try:
        status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append({"severity": "blocker", "finding": f"status.json is unreadable: {exc}"})
        status = {}
    if status.get("state") == "finalized" and status.get("lifecycle") != LIFECYCLE:
        findings.append({"severity": "blocker", "finding": "finalized run has incomplete lifecycle"})
    try:
        review = json.loads((run_dir / "review_gate.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append({"severity": "blocker", "finding": f"review_gate.json is unreadable: {exc}"})
        review = {}
    if status.get("state") == "finalized" and review.get("decision") not in {"ready", "dry_run_ready"}:
        findings.append({"severity": "blocker", "finding": "finalized run lacks passing review gate"})
    try:
        readiness = json.loads((run_dir / "execution_readiness.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append({"severity": "blocker", "finding": f"execution_readiness.json is unreadable: {exc}"})
        readiness = {}
    try:
        summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append({"severity": "blocker", "finding": f"run_summary.json is unreadable: {exc}"})
        summary = {}
    try:
        worker_report = json.loads((run_dir / "worker_report.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append({"severity": "blocker", "finding": f"worker_report.json is unreadable: {exc}"})
        worker_report = {}
    try:
        planning_packet = json.loads((run_dir / "planning_packet.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append({"severity": "blocker", "finding": f"planning_packet.json is unreadable: {exc}"})
        planning_packet = {}
    for problem in validate_planning_packet(planning_packet):
        findings.append({"severity": "blocker", "finding": f"planning packet audit failed: {problem}"})
    try:
        brief = json.loads((run_dir / "implementation_brief.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append({"severity": "blocker", "finding": f"implementation_brief.json is unreadable: {exc}"})
        brief = {}
    if summary.get("execution_readiness") != readiness.get("decision"):
        findings.append({"severity": "blocker", "finding": "run_summary execution_readiness disagrees with execution_readiness.json"})
    surface_sufficiency = review.get("surface_verification_sufficiency") if isinstance(review.get("surface_verification_sufficiency"), dict) else {}
    if summary.get("surface_verification_status", "") != surface_sufficiency.get("status", ""):
        findings.append({"severity": "blocker", "finding": "run_summary surface_verification_status disagrees with review_gate.json"})
    if summary.get("ready_for_execution") != (readiness.get("decision") == "ready_for_real_execution"):
        findings.append({"severity": "blocker", "finding": "run_summary ready_for_execution disagrees with execution_readiness.json"})
    if summary.get("worker_status") != worker_report.get("status"):
        findings.append({"severity": "blocker", "finding": "run_summary worker_status disagrees with worker_report.json"})
    if summary.get("code_brigade_execution_policy_status") != worker_report.get("execution_policy_status"):
        findings.append({"severity": "blocker", "finding": "run_summary code_brigade_execution_policy_status disagrees with worker_report.json"})
    execution_result = worker_report.get("execution_result") if isinstance(worker_report.get("execution_result"), dict) else {}
    if summary.get("code_brigade_execution_result_status", "") != execution_result.get("status", ""):
        findings.append({"severity": "blocker", "finding": "run_summary code_brigade_execution_result_status disagrees with worker_report.json"})
    if summary.get("code_brigade_execution_intent_mode", "") != worker_report.get("execution_intent", {}).get("mode", ""):
        findings.append({"severity": "blocker", "finding": "run_summary code_brigade_execution_intent_mode disagrees with worker_report.json"})
    planning_review = brief.get("planning_review_gate") if isinstance(brief.get("planning_review_gate"), dict) else {}
    if summary.get("planning_review_decision", "") != planning_review.get("decision", ""):
        findings.append({"severity": "blocker", "finding": "run_summary planning_review_decision disagrees with implementation_brief.json"})
    if summary.get("planning_review_score", 0) != planning_review.get("score", 0):
        findings.append({"severity": "blocker", "finding": "run_summary planning_review_score disagrees with implementation_brief.json"})
    work_breakdown = brief.get("work_breakdown") if isinstance(brief.get("work_breakdown"), dict) else {}
    phases = work_breakdown.get("phases") if isinstance(work_breakdown.get("phases"), list) else []
    if summary.get("planning_work_phase_count", 0) != len(phases):
        findings.append({"severity": "blocker", "finding": "run_summary planning_work_phase_count disagrees with implementation_brief.json"})
    work_packages = brief.get("implementation_work_packages") if isinstance(brief.get("implementation_work_packages"), dict) else {}
    expert_plan = brief.get("expert_quality_plan") if isinstance(brief.get("expert_quality_plan"), dict) else {}
    packages = work_packages.get("packages") if isinstance(work_packages.get("packages"), list) else []
    package_statuses = worker_report.get("work_package_statuses") if isinstance(worker_report.get("work_package_statuses"), list) else []
    package_status_counts = {
        status: sum(1 for item in package_statuses if isinstance(item, dict) and item.get("status") == status)
        for status in ("planned", "implemented", "blocked")
    }
    package_review_order = work_packages.get("review_order") if isinstance(work_packages.get("review_order"), list) else []
    package_surfaces = sorted(
        {
            surface
            for package in packages
            if isinstance(package, dict)
            for surface in package.get("impact_surfaces", [])
            if isinstance(surface, str) and surface
        }
    )
    if summary.get("implementation_work_package_count", 0) != len(packages):
        findings.append({"severity": "blocker", "finding": "run_summary implementation_work_package_count disagrees with implementation_brief.json"})
    if summary.get("implementation_work_package_surface_count", 0) != len(package_surfaces):
        findings.append({"severity": "blocker", "finding": "run_summary implementation_work_package_surface_count disagrees with implementation_brief.json"})
    if summary.get("implementation_work_package_review_order", []) != package_review_order:
        findings.append({"severity": "blocker", "finding": "run_summary implementation_work_package_review_order disagrees with implementation_brief.json"})
    package_statuses = worker_report.get("work_package_statuses") if isinstance(worker_report.get("work_package_statuses"), list) else []
    package_status_counts = {
        status: sum(1 for item in package_statuses if isinstance(item, dict) and item.get("status") == status)
        for status in ("planned", "implemented", "blocked")
    }
    if summary.get("work_package_status_counts", {}) != package_status_counts:
        findings.append({"severity": "blocker", "finding": "run_summary work_package_status_counts disagrees with worker_report.json"})
    survey_quality = brief.get("survey_quality_gate") if isinstance(brief.get("survey_quality_gate"), dict) else {}
    if summary.get("survey_quality_decision", "") != survey_quality.get("decision", ""):
        findings.append({"severity": "blocker", "finding": "run_summary survey_quality_decision disagrees with implementation_brief.json"})
    survey_warnings = survey_quality.get("warnings") if isinstance(survey_quality.get("warnings"), list) else []
    if summary.get("survey_quality_warning_count", 0) != len(survey_warnings):
        findings.append({"severity": "blocker", "finding": "run_summary survey_quality_warning_count disagrees with implementation_brief.json"})
    try:
        evidence_matrix = json.loads((run_dir / "evidence_matrix.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append({"severity": "blocker", "finding": f"evidence_matrix.json is unreadable: {exc}"})
        evidence_matrix = {}
    evidence_summary = summary.get("evidence") if isinstance(summary.get("evidence"), dict) else {}
    if evidence_summary:
        for summary_key, matrix_key in [
            ("required_count", "required_evidence_count"),
            ("present_count", "present_count"),
            ("planned_count", "planned_count"),
            ("blocked_count", "blocked_count"),
        ]:
            if evidence_summary.get(summary_key) != evidence_matrix.get(matrix_key):
                findings.append({"severity": "blocker", "finding": f"run_summary evidence.{summary_key} disagrees with evidence_matrix.json"})
    package_summary = evidence_matrix.get("implementation_work_package_summary") if isinstance(evidence_matrix.get("implementation_work_package_summary"), dict) else {}
    if package_summary:
        if package_summary.get("package_count") != summary.get("implementation_work_package_count"):
            findings.append({"severity": "blocker", "finding": "evidence_matrix package_count disagrees with run_summary"})
        if package_summary.get("covered_surface_count") != summary.get("implementation_work_package_surface_count"):
            findings.append({"severity": "blocker", "finding": "evidence_matrix covered_surface_count disagrees with run_summary"})
        if package_summary.get("review_order") != summary.get("implementation_work_package_review_order"):
            findings.append({"severity": "blocker", "finding": "evidence_matrix review_order disagrees with run_summary"})
        if package_summary.get("status_counts") != summary.get("work_package_status_counts"):
            findings.append({"severity": "blocker", "finding": "evidence_matrix work package status_counts disagrees with run_summary"})
    expert_summary = evidence_matrix.get("expert_quality_summary") if isinstance(evidence_matrix.get("expert_quality_summary"), dict) else {}
    if expert_summary:
        if expert_summary.get("level") != summary.get("expert_quality_level"):
            findings.append({"severity": "blocker", "finding": "evidence_matrix expert quality level disagrees with run_summary"})
        if expert_summary.get("required_for_expert_gate") != summary.get("expert_quality_required"):
            findings.append({"severity": "blocker", "finding": "evidence_matrix expert quality requirement disagrees with run_summary"})
        if expert_summary.get("tradeoff_count") != summary.get("expert_tradeoff_count"):
            findings.append({"severity": "blocker", "finding": "evidence_matrix expert tradeoff_count disagrees with run_summary"})
        if expert_summary.get("review_checklist_count") != summary.get("expert_review_checklist_count"):
            findings.append({"severity": "blocker", "finding": "evidence_matrix expert review_checklist_count disagrees with run_summary"})
    decision = "passed" if not findings else "blocked"
    return {
        "kind": "ceraxia_run_package_audit",
        "decision": decision,
        "manifest_complete": manifest["complete"],
        "findings": findings,
    }


def build_execution_readiness(
    status: dict[str, Any],
    brief: dict[str, Any],
    verification_report: dict[str, Any],
    review: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any]:
    blockers: list[str] = []
    ready_conditions = [
        "planning packet passed contract validation",
        "implementation brief is not blocked",
        "verification strategy is planned",
        "review gate is ready or dry-run-ready",
        "run package audit passes",
    ]
    if status.get("state") != "finalized":
        blockers.append("run lifecycle is not finalized")
    if brief.get("blocked"):
        blockers.extend(str(item) for item in brief.get("blockers", []))
    if verification_report.get("status") == "blocked":
        blockers.extend(str(item) for item in verification_report.get("blockers", []))
    if review.get("decision") not in {"ready", "dry_run_ready"}:
        blockers.append("review gate did not approve the handoff")
    if dry_run:
        blockers.append("dry run requested; real CodeBrigade execution was intentionally skipped")
    intent = brief.get("execution_intent") if isinstance(brief.get("execution_intent"), dict) else {}
    if not dry_run and intent.get("real_execution_supported") is False:
        blockers.append("real CodeBrigade execution requires autonomous unshaped source-edit adapter")
    return {
        "kind": "ceraxia_execution_readiness",
        "contract_version": CONTRACT_VERSION,
        "decision": "blocked" if blockers else "ready_for_real_execution",
        "dry_run": dry_run,
        "ready_conditions": ready_conditions,
        "blockers": blockers,
        "next_capability_to_wire": "CodeBrigade real execution adapter" if dry_run else "",
    }


def build_final_next_action(status: dict[str, Any], worker_report: dict[str, Any], dry_run: bool) -> str:
    if status.get("state") != "finalized":
        return "inspect blockers"
    if dry_run:
        return "execute with CodeBrigade only when mutation is explicitly intended"
    execution_result = worker_report.get("execution_result") if isinstance(worker_report.get("execution_result"), dict) else {}
    if worker_report.get("status") == "implemented" and execution_result.get("status") == "implemented":
        return "expand CodeBrigade from explicit patch execution toward autonomous source edits"
    return "inspect CodeBrigade execution blockers"


def build_maturity_label(worker_report: dict[str, Any], readiness: dict[str, Any]) -> str:
    if readiness.get("decision") == "ready_for_real_execution" and worker_report.get("status") == "implemented":
        return "explicit_patch_execution_controller"
    if worker_report.get("status") == "dry_run_handoff_ready":
        return "dry_run_controller_with_code_brigade_handoff_adapter"
    return "blocked_controller_with_audited_handoff"


def build_run_summary(
    run_id: str,
    run_dir: Path,
    status: dict[str, Any],
    brief: dict[str, Any],
    worker_report: dict[str, Any],
    review: dict[str, Any],
    readiness: dict[str, Any],
    evidence_matrix: dict[str, Any],
    package_audit_decision: str = "pending_until_run_audit",
) -> dict[str, Any]:
    execution_result = worker_report.get("execution_result") if isinstance(worker_report.get("execution_result"), dict) else {}
    preflight = execution_result.get("preflight") if isinstance(execution_result.get("preflight"), dict) else {}
    execution_intent = worker_report.get("execution_intent") if isinstance(worker_report.get("execution_intent"), dict) else {}
    planning_review = brief.get("planning_review_gate") if isinstance(brief.get("planning_review_gate"), dict) else {}
    survey_quality = brief.get("survey_quality_gate") if isinstance(brief.get("survey_quality_gate"), dict) else {}
    work_breakdown = brief.get("work_breakdown") if isinstance(brief.get("work_breakdown"), dict) else {}
    work_phases = work_breakdown.get("phases") if isinstance(work_breakdown.get("phases"), list) else []
    surface_sufficiency = review.get("surface_verification_sufficiency") if isinstance(review.get("surface_verification_sufficiency"), dict) else {}
    work_packages = brief.get("implementation_work_packages") if isinstance(brief.get("implementation_work_packages"), dict) else {}
    expert_plan = brief.get("expert_quality_plan") if isinstance(brief.get("expert_quality_plan"), dict) else {}
    packages = work_packages.get("packages") if isinstance(work_packages.get("packages"), list) else []
    package_statuses = worker_report.get("work_package_statuses") if isinstance(worker_report.get("work_package_statuses"), list) else []
    package_status_counts = {
        status: sum(1 for item in package_statuses if isinstance(item, dict) and item.get("status") == status)
        for status in ("planned", "implemented", "blocked")
    }
    package_surfaces = sorted(
        {
            surface
            for package in packages
            if isinstance(package, dict)
            for surface in package.get("impact_surfaces", [])
            if isinstance(surface, str) and surface
        }
    )
    return {
        "kind": "ceraxia_run_summary",
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "state": status.get("state"),
        "package_ok": status.get("state") == "finalized",
        "package_lifecycle_finalized": status.get("state") == "finalized",
        "package_audit_decision": package_audit_decision,
        "ready_for_execution": readiness.get("decision") == "ready_for_real_execution",
        "review_decision": review.get("decision"),
        "planning_review_decision": planning_review.get("decision", ""),
        "planning_review_score": planning_review.get("score", 0),
        "planning_work_phase_count": len(work_phases),
        "implementation_work_package_count": len(packages),
        "implementation_work_package_surface_count": len(package_surfaces),
        "implementation_work_package_review_order": work_packages.get("review_order", []) if isinstance(work_packages.get("review_order"), list) else [],
        "work_package_status_counts": package_status_counts,
        "survey_quality_decision": survey_quality.get("decision", ""),
        "survey_quality_warning_count": len(survey_quality.get("warnings", [])) if isinstance(survey_quality.get("warnings"), list) else 0,
        "surface_verification_status": surface_sufficiency.get("status", ""),
        "surface_verification_surface_count": surface_sufficiency.get("surface_count", 0),
        "execution_readiness": readiness.get("decision"),
        "worker_status": worker_report.get("status"),
        "code_brigade_execution_policy_status": worker_report.get("execution_policy_status"),
        "code_brigade_execution_intent_mode": execution_intent.get("mode", ""),
        "code_brigade_execution_real_supported": bool(execution_intent.get("real_execution_supported")),
        "code_brigade_execution_result_status": execution_result.get("status", ""),
        "code_brigade_execution_preflight_ok": preflight.get("ok") if preflight else None,
        "code_brigade_execution_preflight_blocker_count": len(preflight.get("blockers", [])) if preflight else 0,
        "risk_level": brief.get("risk_level"),
        "task_kinds": brief.get("task_kinds", []),
        "selected_strategy": brief.get("selected_strategy"),
        "expert_quality_level": expert_plan.get("level", ""),
        "expert_quality_required": bool(expert_plan.get("required_for_expert_gate")),
        "expert_tradeoff_count": len(expert_plan.get("tradeoff_register", [])) if isinstance(expert_plan.get("tradeoff_register"), list) else 0,
        "expert_review_checklist_count": len(expert_plan.get("review_checklist", [])) if isinstance(expert_plan.get("review_checklist"), list) else 0,
        "evidence": {
            "required_count": evidence_matrix.get("required_evidence_count", 0),
            "present_count": evidence_matrix.get("present_count", 0),
            "planned_count": evidence_matrix.get("planned_count", 0),
            "blocked_count": evidence_matrix.get("blocked_count", 0),
        },
        "blockers": readiness.get("blockers", []),
        "next_action": status.get("next_action"),
        "maturity": build_maturity_label(worker_report, readiness),
    }


def build_evidence_matrix(
    brief: dict[str, Any],
    worker_report: dict[str, Any],
    verification_report: dict[str, Any],
    readiness: dict[str, Any],
) -> dict[str, Any]:
    quality = brief.get("quality_bar") if isinstance(brief.get("quality_bar"), dict) else {}
    expert_plan = brief.get("expert_quality_plan") if isinstance(brief.get("expert_quality_plan"), dict) else {}
    required_evidence = quality.get("must_have_evidence") if isinstance(quality.get("must_have_evidence"), list) else []
    repo_evidence = brief.get("repo_survey_evidence") if isinstance(brief.get("repo_survey_evidence"), dict) else {}
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    work_packages = implementation_plan.get("implementation_work_packages") if isinstance(implementation_plan.get("implementation_work_packages"), list) else []
    work_package_review_order = implementation_plan.get("work_package_review_order") if isinstance(implementation_plan.get("work_package_review_order"), list) else []
    surface_package_rows = implementation_plan.get("surface_package_matrix_rows") if isinstance(implementation_plan.get("surface_package_matrix_rows"), list) else []
    package_statuses = worker_report.get("work_package_statuses") if isinstance(worker_report.get("work_package_statuses"), list) else []
    package_status_counts = {
        status: sum(1 for item in package_statuses if isinstance(item, dict) and item.get("status") == status)
        for status in ("planned", "implemented", "blocked")
    }
    package_surfaces = sorted(
        {
            surface
            for package in work_packages
            if isinstance(package, dict)
            for surface in package.get("impact_surfaces", [])
            if isinstance(surface, str) and surface
        }
    )
    rows: list[dict[str, Any]] = []
    candidate_files = repo_evidence.get("candidate_files") if isinstance(repo_evidence.get("candidate_files"), list) else []
    test_files = repo_evidence.get("test_files") if isinstance(repo_evidence.get("test_files"), list) else []
    verification_commands = verification_report.get("commands_planned") if isinstance(verification_report.get("commands_planned"), list) else []
    commands_executed = verification_report.get("commands_executed") if isinstance(verification_report.get("commands_executed"), list) else []
    for requirement in required_evidence:
        status = "planned"
        sources: list[str] = []
        requirement_text = str(requirement)
        if "candidate files" in requirement_text and candidate_files:
            status = "present"
            sources.append("repo_survey.json:candidate_files")
        if "verification command" in requirement_text and verification_commands:
            status = "planned" if not commands_executed else "present"
            sources.append("verification_report.json:commands_planned")
        if "negative boundary" in requirement_text:
            negative_tests = verification_report.get("negative_tests_required")
            if isinstance(negative_tests, list) and negative_tests:
                status = "planned" if not commands_executed else "present"
                sources.append("verification_report.json:negative_tests_required")
        if "backward compatibility" in requirement_text and test_files:
            status = "planned" if not commands_executed else "present"
            sources.append("repo_survey.json:test_files")
        if readiness.get("decision") == "blocked" and not sources:
            status = "blocked"
        rows.append(
            {
                "requirement": requirement_text,
                "status": status,
                "sources": sources,
                "blocking_reason": "no concrete evidence source mapped yet" if not sources else "",
            }
        )
    return {
        "kind": "ceraxia_evidence_matrix",
        "contract_version": CONTRACT_VERSION,
        "decision": readiness.get("decision"),
        "required_evidence_count": len(required_evidence),
        "present_count": sum(1 for row in rows if row["status"] == "present"),
        "planned_count": sum(1 for row in rows if row["status"] == "planned"),
        "blocked_count": sum(1 for row in rows if row["status"] == "blocked"),
        "rows": rows,
        "implementation_plan_sources": {
            "target_files_to_inspect": implementation_plan.get("target_files_to_inspect", []),
            "test_files_to_preserve": implementation_plan.get("test_files_to_preserve", []),
            "recommended_read_order": implementation_plan.get("recommended_read_order", []),
            "verification_commands": implementation_plan.get("verification_commands", []),
        },
        "implementation_work_package_summary": {
            "package_count": len(work_packages),
            "review_order": work_package_review_order,
            "covered_surfaces": package_surfaces,
            "covered_surface_count": len(package_surfaces),
            "status_counts": package_status_counts,
            "statuses": package_statuses,
        },
        "surface_package_summary": {
            "surface_count": len(surface_package_rows),
            "rows": surface_package_rows,
        },
        "expert_quality_summary": {
            "level": expert_plan.get("level", ""),
            "required_for_expert_gate": bool(expert_plan.get("required_for_expert_gate")),
            "impact_surfaces": expert_plan.get("impact_surfaces", []) if isinstance(expert_plan.get("impact_surfaces"), list) else [],
            "tradeoff_count": len(expert_plan.get("tradeoff_register", [])) if isinstance(expert_plan.get("tradeoff_register"), list) else 0,
            "rollback_requirement_count": len(expert_plan.get("rollback_strategy", [])) if isinstance(expert_plan.get("rollback_strategy"), list) else 0,
            "observability_requirement_count": len(expert_plan.get("observability_plan", [])) if isinstance(expert_plan.get("observability_plan"), list) else 0,
            "review_checklist_count": len(expert_plan.get("review_checklist", [])) if isinstance(expert_plan.get("review_checklist"), list) else 0,
            "escalation_policy_count": len(expert_plan.get("escalation_policy", [])) if isinstance(expert_plan.get("escalation_policy"), list) else 0,
        },
    }


def run_ceraxia(task_input: CeraxiaInput) -> dict[str, Any]:
    run_id = f"ceraxia-{utc_stamp()}-{task_slug(task_input.task)}"
    run_dir = task_input.runs_root / run_id
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
        "dry_run": task_input.dry_run,
        "constraints": list(task_input.constraints),
        "verification_commands": list(task_input.verification_commands),
    }
    write_json(run_dir / "task.json", task_payload)

    packet = build_planning_packet(task_payload)
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
    status["state"] = "implementation_ready" if not brief["blocked"] else "failed"
    status["lifecycle"].append(status["state"])
    status["next_action"] = "handoff to CodeBrigade" if not brief["blocked"] else "fix blockers before implementation"
    write_json(run_dir / "implementation_brief.json", brief)

    worker_report = build_worker_report(brief, task_input.dry_run)
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

    status["state"] = "finalized" if status["state"] == "reviewed" else "failed"
    status["lifecycle"].append(status["state"])
    status["next_action"] = build_final_next_action(status, worker_report, task_input.dry_run)
    artifacts = {
        "status": status,
        "planning_packet": packet,
        "implementation_brief": brief,
        "worker_report": worker_report,
        "verification_report": verification_report,
        "review_gate": review,
    }
    write_json(run_dir / "status.json", status)
    readiness = build_execution_readiness(status, brief, verification_report, review, task_input.dry_run)
    artifacts["execution_readiness"] = readiness
    write_text(run_dir / "final_report.md", final_report_markdown(run_id, artifacts))
    write_json(run_dir / "execution_readiness.json", readiness)
    evidence_matrix = build_evidence_matrix(brief, worker_report, verification_report, readiness)
    write_json(run_dir / "evidence_matrix.json", evidence_matrix)
    summary = build_run_summary(run_id, run_dir, status, brief, worker_report, review, readiness, evidence_matrix)
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
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Ceraxia's planning-to-review smoke controller.")
    parser.add_argument("--task", required=True)
    parser.add_argument("--repo-path", default=str(PROJECT_ROOT))
    parser.add_argument("--runs-root", type=Path, default=RUNS_ROOT)
    parser.add_argument("--execute", action="store_true", help="Reserved for future real CodeBrigade execution.")
    parser.add_argument("--execute-verification", action="store_true", help="Run allowlisted verification commands while keeping source mutation dry-run.")
    parser.add_argument("--constraint", action="append", default=[], help="Structured planning constraint. Can be repeated.")
    parser.add_argument("--verification-command", action="append", default=[], help="Structured verification command. Can be repeated.")
    args = parser.parse_args()
    result = run_ceraxia(
        CeraxiaInput(
            task=args.task,
            repo_path=args.repo_path,
            dry_run=not args.execute,
            execute_verification=args.execute_verification,
            constraints=tuple(args.constraint),
            verification_commands=tuple(args.verification_command),
            runs_root=args.runs_root,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
