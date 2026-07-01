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

from planning_brigade import ROLE_ORDER, build_planning_packet  # noqa: E402
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
    problems: list[str] = []
    required = [
        "task_triage",
        "repo_survey_request",
        "design_options",
        "verification_strategy",
        "risk_register",
        "quality_bar",
        "code_brigade_handoff",
    ]
    if packet.get("roles_completed") != ROLE_ORDER:
        problems.append("planning packet must include all five planning roles in order")
    if packet.get("contract_version") != CONTRACT_VERSION:
        problems.append("planning packet contract_version is unsupported")
    for key in required:
        if not isinstance(packet.get(key), dict):
            problems.append(f"planning packet missing object: {key}")
    if not packet.get("ok"):
        problems.append("planning packet is not ok")
    if packet.get("design_options", {}).get("requires_ceraxia_approval") is not True:
        problems.append("planning packet must require Ceraxia strategy approval")
    triage = packet.get("task_triage") if isinstance(packet.get("task_triage"), dict) else {}
    if not isinstance(triage.get("task_kinds"), list) or not triage.get("task_kinds"):
        problems.append("task triage must include task_kinds")
    if triage.get("risk_level") not in {"low", "medium", "high"}:
        problems.append("task triage must include a valid risk_level")
    if triage.get("handoff_to") != "RepoSurveyor":
        problems.append("task triage must hand off to RepoSurveyor")
    survey = packet.get("repo_survey_request") if isinstance(packet.get("repo_survey_request"), dict) else {}
    if survey.get("read_only") is not True:
        problems.append("repo survey request must be read-only")
    if not isinstance(survey.get("focus"), list) or not survey.get("focus"):
        problems.append("repo survey request must include focus areas")
    if survey.get("handoff_to") != "DesignStrategos":
        problems.append("repo survey request must hand off to DesignStrategos")
    design = packet.get("design_options") if isinstance(packet.get("design_options"), dict) else {}
    if not isinstance(design.get("selected_strategy"), str) or not design.get("selected_strategy"):
        problems.append("design options must include selected_strategy")
    options = design.get("options") if isinstance(design.get("options"), list) else []
    if not any(item.get("name") == "hardcode" and item.get("decision") == "reject" for item in options if isinstance(item, dict)):
        problems.append("design options must reject hardcode")
    if not any(item.get("name") == "broad_rewrite" and item.get("decision") == "reject" for item in options if isinstance(item, dict)):
        problems.append("design options must reject broad_rewrite")
    if design.get("handoff_to") != "VerificationArchitect":
        problems.append("design options must hand off to VerificationArchitect")
    verification = packet.get("verification_strategy") if isinstance(packet.get("verification_strategy"), dict) else {}
    if not isinstance(verification.get("targeted_commands"), list) or not verification.get("targeted_commands"):
        problems.append("verification strategy must include targeted_commands")
    if not isinstance(verification.get("checks"), list) or not verification.get("checks"):
        problems.append("verification strategy must include checks")
    if not isinstance(verification.get("negative_tests"), list):
        problems.append("verification strategy must include negative_tests list")
    if not isinstance(verification.get("broad_verification_required"), bool):
        problems.append("verification strategy must include broad_verification_required boolean")
    if verification.get("handoff_to") != "RiskScribe":
        problems.append("verification strategy must hand off to RiskScribe")
    risks = packet.get("risk_register") if isinstance(packet.get("risk_register"), dict) else {}
    if not isinstance(risks.get("risks"), list) or not risks.get("risks"):
        problems.append("risk register must include risks")
    if not isinstance(risks.get("acceptance_gates"), list) or not risks.get("acceptance_gates"):
        problems.append("risk register must include acceptance_gates")
    if risks.get("handoff_to") != "Ceraxia":
        problems.append("risk register must hand authority back to Ceraxia")
    quality = packet.get("quality_bar") if isinstance(packet.get("quality_bar"), dict) else {}
    if not isinstance(quality.get("must_have_evidence"), list) or not quality.get("must_have_evidence"):
        problems.append("quality bar must include must_have_evidence")
    if not isinstance(quality.get("forbidden_shortcuts"), list) or not quality.get("forbidden_shortcuts"):
        problems.append("quality bar must include forbidden_shortcuts")
    if not isinstance(quality.get("success_definition"), str) or not quality.get("success_definition"):
        problems.append("quality bar must include success_definition")
    handoff = packet.get("code_brigade_handoff") if isinstance(packet.get("code_brigade_handoff"), dict) else {}
    if handoff.get("target") != "CodeBrigade":
        problems.append("code brigade handoff must target CodeBrigade")
    if not isinstance(handoff.get("steps"), list) or not handoff.get("steps"):
        problems.append("code brigade handoff must include steps")
    if packet.get("next_action", {}).get("owner") != "Ceraxia":
        problems.append("next action must be owned by Ceraxia")
    return problems


def build_repo_survey(packet: dict[str, Any]) -> dict[str, Any]:
    survey_request = packet["repo_survey_request"]
    return survey_repository(
        str(survey_request.get("repo_path") or PROJECT_ROOT),
        survey_request.get("focus", []) if isinstance(survey_request.get("focus"), list) else [],
        survey_request.get("exclude_patterns", []) if isinstance(survey_request.get("exclude_patterns"), list) else [],
    )


def build_implementation_brief(packet: dict[str, Any], survey: dict[str, Any]) -> dict[str, Any]:
    triage = packet.get("task_triage") if isinstance(packet.get("task_triage"), dict) else {}
    verification = packet.get("verification_strategy") if isinstance(packet.get("verification_strategy"), dict) else {}
    risks = packet.get("risk_register") if isinstance(packet.get("risk_register"), dict) else {}
    quality = packet.get("quality_bar") if isinstance(packet.get("quality_bar"), dict) else {}
    handoff = packet.get("code_brigade_handoff") if isinstance(packet.get("code_brigade_handoff"), dict) else {}
    planning_problems = validate_planning_packet(packet)
    blocked = bool(planning_problems) or not survey["repo_exists"]
    blockers = [f"planning validation failed: {problem}" for problem in planning_problems]
    if not survey["repo_exists"]:
        blockers.append("repo survey or planning validation is incomplete")
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
        "acceptance_gates": risks.get("acceptance_gates") if isinstance(risks.get("acceptance_gates"), list) else [],
        "quality_bar": quality,
        "code_brigade_handoff": handoff,
        "repo_survey_evidence": {
            "candidate_files": survey.get("candidate_files", []),
            "test_files": survey.get("test_files", []),
            "entrypoint_candidates": survey.get("entrypoint_candidates", []),
            "python_symbols": survey.get("python_symbols", []),
            "local_import_edges": survey.get("local_import_edges", []),
            "survey_truncated": bool(survey.get("truncated")),
            "max_files_scanned": survey.get("max_files_scanned", 0),
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


def review_gate(
    packet: dict[str, Any],
    brief: dict[str, Any],
    worker_report: dict[str, Any],
    verification_report: dict[str, Any],
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    for problem in validate_planning_packet(packet):
        findings.append({"severity": "blocker", "finding": problem})
    if not worker_report.get("implementation_brief_acknowledged", False):
        findings.append({"severity": "blocker", "finding": "implementation brief was not acknowledged"})
    if worker_report["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "worker report is blocked"})
    negative_tests = verification_report.get("negative_tests_required", [])
    if negative_tests and verification_report["status"] not in {"planned_only", "requires_execution", "passed"}:
        findings.append({"severity": "blocker", "finding": "negative tests are missing or not planned"})
    if verification_report.get("broad_verification_required") and not verification_report.get("commands_planned"):
        findings.append({"severity": "blocker", "finding": "broad verification is required but no commands are planned"})
    if verification_report.get("broad_verification_required") and verification_report.get("status") == "planned_only":
        warnings.append({"severity": "warning", "finding": "broad verification is planned but not executed"})
    if verification_report.get("commands_executable") and not verification_report.get("commands_executed"):
        warnings.append({"severity": "warning", "finding": "executable verification commands exist but were not run"})
    repo_evidence = brief.get("repo_survey_evidence") if isinstance(brief.get("repo_survey_evidence"), dict) else {}
    if repo_evidence.get("survey_truncated"):
        warnings.append({"severity": "warning", "finding": "repository survey reached file limit; coverage is partial"})
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
        "checked_against": [
            "planning packet completeness",
            "strategy approval",
            "scope control",
            "verification strategy",
            "worker report honesty",
        ],
    }


def final_report_markdown(run_id: str, artifacts: dict[str, dict[str, Any]]) -> str:
    packet = artifacts["planning_packet"]
    brief = artifacts["implementation_brief"]
    review = artifacts["review_gate"]
    verification = artifacts["verification_report"]
    readiness = artifacts["execution_readiness"]
    blockers = readiness.get("blockers", [])
    warnings = review.get("warnings", [])
    commands_executed = verification.get("commands_executed", [])
    commands_planned = verification.get("commands_planned", [])
    lines = [
        f"# Ceraxia Run {run_id}",
        "",
        f"Task: {packet['task']}",
        f"Lifecycle status: {artifacts['status']['state']}",
        f"Package status: {'complete' if artifacts['status']['state'] == 'finalized' else 'incomplete'}",
        f"Execution readiness: {readiness['decision']}",
        f"Risk: {brief['risk_level']}",
        f"Strategy: {brief['selected_strategy']}",
        f"Review decision: {review['decision']}",
        f"Verification status: {verification['status']}",
        f"Verification commands planned: {len(commands_planned)}",
        f"Verification commands executed: {len(commands_executed)}",
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
            f"- candidate files: {len(brief.get('repo_survey_evidence', {}).get('candidate_files', []))}",
            f"- test files: {len(brief.get('repo_survey_evidence', {}).get('test_files', []))}",
            f"- python symbol reports: {len(brief.get('repo_survey_evidence', {}).get('python_symbols', []))}",
            f"- code brigade handoff steps: {len(brief.get('code_brigade_handoff', {}).get('steps', []))}",
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
    if summary.get("execution_readiness") != readiness.get("decision"):
        findings.append({"severity": "blocker", "finding": "run_summary execution_readiness disagrees with execution_readiness.json"})
    if summary.get("ready_for_execution") != (readiness.get("decision") == "ready_for_real_execution"):
        findings.append({"severity": "blocker", "finding": "run_summary ready_for_execution disagrees with execution_readiness.json"})
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
        blockers.append("real CodeBrigade execution is not wired in this controller yet")
    return {
        "kind": "ceraxia_execution_readiness",
        "contract_version": CONTRACT_VERSION,
        "decision": "blocked" if blockers else "ready_for_real_execution",
        "dry_run": dry_run,
        "ready_conditions": ready_conditions,
        "blockers": blockers,
        "next_capability_to_wire": "CodeBrigade real execution adapter" if dry_run else "",
    }


def build_run_summary(
    run_id: str,
    run_dir: Path,
    status: dict[str, Any],
    brief: dict[str, Any],
    review: dict[str, Any],
    readiness: dict[str, Any],
    evidence_matrix: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": "ceraxia_run_summary",
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "state": status.get("state"),
        "package_ok": status.get("state") == "finalized",
        "package_lifecycle_finalized": status.get("state") == "finalized",
        "package_audit_decision": "pending_until_run_audit",
        "ready_for_execution": readiness.get("decision") == "ready_for_real_execution",
        "review_decision": review.get("decision"),
        "execution_readiness": readiness.get("decision"),
        "risk_level": brief.get("risk_level"),
        "task_kinds": brief.get("task_kinds", []),
        "selected_strategy": brief.get("selected_strategy"),
        "evidence": {
            "required_count": evidence_matrix.get("required_evidence_count", 0),
            "present_count": evidence_matrix.get("present_count", 0),
            "planned_count": evidence_matrix.get("planned_count", 0),
            "blocked_count": evidence_matrix.get("blocked_count", 0),
        },
        "blockers": readiness.get("blockers", []),
        "next_action": status.get("next_action"),
        "maturity": "dry_run_controller_with_code_brigade_handoff_adapter",
    }


def build_evidence_matrix(
    brief: dict[str, Any],
    worker_report: dict[str, Any],
    verification_report: dict[str, Any],
    readiness: dict[str, Any],
) -> dict[str, Any]:
    quality = brief.get("quality_bar") if isinstance(brief.get("quality_bar"), dict) else {}
    required_evidence = quality.get("must_have_evidence") if isinstance(quality.get("must_have_evidence"), list) else []
    repo_evidence = brief.get("repo_survey_evidence") if isinstance(brief.get("repo_survey_evidence"), dict) else {}
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
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
            "verification_commands": implementation_plan.get("verification_commands", []),
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
    status["next_action"] = "real CodeBrigade execution can replace dry-run handoff" if status["state"] == "finalized" else "inspect blockers"
    artifacts = {
        "status": status,
        "planning_packet": packet,
        "implementation_brief": brief,
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
    summary = build_run_summary(run_id, run_dir, status, brief, review, readiness, evidence_matrix)
    write_json(run_dir / "run_summary.json", summary)
    manifest = build_artifact_manifest(run_dir)
    write_json(run_dir / "artifact_manifest.json", manifest)
    audit = audit_run_package(run_dir)
    write_json(run_dir / "run_audit.json", audit)
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
    args = parser.parse_args()
    result = run_ceraxia(
        CeraxiaInput(
            task=args.task,
            repo_path=args.repo_path,
            dry_run=not args.execute,
            execute_verification=args.execute_verification,
            runs_root=args.runs_root,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
