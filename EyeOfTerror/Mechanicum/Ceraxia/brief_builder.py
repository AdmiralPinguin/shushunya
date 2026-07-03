from __future__ import annotations

"""Repo survey, quality gate, execution intent, and implementation-brief builders."""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ceraxia_common import (  # noqa: F401
    CONTRACT_VERSION,
    DIAGNOSTIC_REPAIR_MAX_ATTEMPTS,
    EXECUTION_MODES,
    LIFECYCLE,
    PROJECT_ROOT,
    REQUIRED_RUN_ARTIFACTS,
    RUNS_ROOT,
    CeraxiaInput,
)

from planning_brigade import build_planning_packet  # noqa: E402,F401
from planning_packet_contract import validate_planning_packet as validate_planning_packet_contract  # noqa: E402,F401
from code_brigade_adapter import build_worker_report  # noqa: E402,F401
from diagnostic_repair_contract import execute_diagnostic_repair_request  # noqa: E402,F401
from engineering_memory import build_engineering_memory_update  # noqa: E402,F401
from execution_adapter import can_infer_guarded_natural_language_patch  # noqa: E402,F401
from planning_department import build_planning_department_package  # noqa: E402,F401
from verification_adapter import run_verification_commands  # noqa: E402,F401
from repo_survey import survey_repository  # noqa: E402,F401


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


def planned_create_paths_from_task(task: str) -> list[str]:
    paths: list[str] = []
    for match in re.finditer(
        r"создай\s+файл\s+`(?P<path>[^`]+)`\s+с\s+содержимым\s+`(?P<content>[^`]+)`",
        task,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        paths.append(match.group("path").strip())
    marker = "CERAXIA_PATCH:"
    if marker in task:
        raw = task.split(marker, 1)[1].strip()
        try:
            payload, _ = json.JSONDecoder().raw_decode(raw)
        except json.JSONDecodeError:
            payload = {}
        operations = payload.get("operations") if isinstance(payload, dict) else []
        if isinstance(operations, list):
            for operation in operations:
                if isinstance(operation, dict) and operation.get("type") == "create_file":
                    paths.append(str(operation.get("path") or "").strip())
    return [path for path in paths if path]


def build_survey_quality_gate(packet: dict[str, Any], survey: dict[str, Any]) -> dict[str, Any]:
    triage = packet.get("task_triage") if isinstance(packet.get("task_triage"), dict) else {}
    task_kinds = set(triage.get("task_kinds", []) if isinstance(triage.get("task_kinds"), list) else [])
    risk_level = triage.get("risk_level", "high")
    candidate_files = survey.get("candidate_files") if isinstance(survey.get("candidate_files"), list) else []
    test_files = survey.get("test_files") if isinstance(survey.get("test_files"), list) else []
    missing_path_hints = survey.get("missing_path_hints") if isinstance(survey.get("missing_path_hints"), list) else []
    unsafe_path_hints = survey.get("unsafe_path_hints") if isinstance(survey.get("unsafe_path_hints"), list) else []
    planned_create_paths = planned_create_paths_from_task(str(packet.get("task") or ""))
    missing_blockers = [str(item) for item in missing_path_hints if str(item) not in planned_create_paths]
    allowed_missing_create_path_hints = [str(item) for item in missing_path_hints if str(item) in planned_create_paths]
    blockers: list[str] = []
    warnings: list[str] = []
    if not survey.get("repo_exists"):
        blockers.append("repository does not exist")
    if unsafe_path_hints:
        blockers.append("unsafe explicit path hints: " + ", ".join(str(item) for item in unsafe_path_hints))
    if missing_blockers:
        blockers.append("explicit path hints were not found: " + ", ".join(missing_blockers))
    if not candidate_files and not allowed_missing_create_path_hints:
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
        "allowed_missing_create_path_hints": allowed_missing_create_path_hints,
        "unsafe_path_hints": unsafe_path_hints,
        "blockers": blockers,
        "warnings": warnings,
    }


def build_execution_intent(packet: dict[str, Any], dry_run: bool | None = None) -> dict[str, Any]:
    task = str(packet.get("task") or "")
    has_explicit_patch = "CERAXIA_PATCH:" in task
    has_guarded_inferred_patch = False if has_explicit_patch else can_infer_guarded_natural_language_patch(task)
    mode = "explicit_patch_execution" if has_explicit_patch else ("guarded_inferred_patch_execution" if has_guarded_inferred_patch else "planning_handoff_only")
    blockers: list[str] = []
    if dry_run is True:
        blockers.append("dry run requested; source mutation is intentionally skipped")
    if not has_explicit_patch and not has_guarded_inferred_patch:
        blockers.append("unshaped source mutation requires a future CodeBrigade autonomous execution adapter")
    return {
        "kind": "ceraxia_code_brigade_execution_intent",
        "contract_version": CONTRACT_VERSION,
        "mode": mode,
        "adapter_capability": "explicit_or_guarded_inference_adapter" if has_explicit_patch or has_guarded_inferred_patch else "explicit_patch_adapter_only",
        "explicit_patch_present": has_explicit_patch,
        "real_execution_supported": has_explicit_patch or has_guarded_inferred_patch,
        "dry_run_requested": bool(dry_run) if dry_run is not None else False,
        "blockers": blockers,
        "required_next_adapter": "" if has_explicit_patch or has_guarded_inferred_patch else "autonomous CodeBrigade source-edit adapter",
    }


def normalize_execution_mode(task_input: CeraxiaInput) -> str:
    if task_input.execution_mode in EXECUTION_MODES:
        return task_input.execution_mode
    return "dry_run" if task_input.dry_run else "guarded_patch"


def execution_mode_dry_run(mode: str) -> bool:
    return mode in {"dry_run", "review_only"}


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
        "assumption_register": packet.get("assumption_register", {}),
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
        "diagnostic_repair_plan": packet.get("diagnostic_repair_plan", {}),
        "surface_verification_matrix": packet.get("surface_verification_matrix", {}),
        "surface_package_matrix": packet.get("surface_package_matrix", {}),
        "survey_quality_gate": survey_quality,
        "acceptance_gates": risks.get("acceptance_gates") if isinstance(risks.get("acceptance_gates"), list) else [],
        "quality_bar": quality,
        "acceptance_contract": packet.get("acceptance_contract", {}),
        "acceptance_trace_matrix": packet.get("acceptance_trace_matrix", {}),
        "constraint_trace_matrix": packet.get("constraint_trace_matrix", {}),
        "expert_quality_plan": packet.get("expert_quality_plan", {}),
        "change_control_plan": packet.get("change_control_plan", {}),
        "investigation_playbook": packet.get("investigation_playbook", {}),
        "implementation_brief_blueprint": packet.get("implementation_brief_blueprint", {}),
        "implementation_work_packages": packet.get("implementation_work_packages", {}),
        "worker_output_contract": packet.get("worker_output_contract", {}),
        "planning_review_gate": planning_review,
        "planning_role_execution_trace": packet.get("role_execution_trace", []) if isinstance(packet.get("role_execution_trace"), list) else [],
        "planning_dependency_map": packet.get("dependency_map", {}),
        "work_breakdown": packet.get("work_breakdown", {}),
        "impact_analysis": packet.get("impact_analysis", {}),
        "execution_forecast": packet.get("execution_forecast", {}),
        "execution_intent": build_execution_intent(packet),
        "controller_execution_mode": packet.get("execution_mode", "dry_run"),
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
            "repository_dependency_graph": survey.get("repository_dependency_graph", {}),
            "reverse_dependency_index": survey.get("reverse_dependency_index", {}),
            "test_coverage_links": survey.get("test_coverage_links", []),
            "missing_python_import_hints": survey.get("missing_python_import_hints", []),
            "caller_candidates": survey.get("caller_candidates", []),
            "contract_surface_candidates": survey.get("contract_surface_candidates", []),
            "package_manifest_candidates": survey.get("package_manifest_candidates", []),
            "recommended_read_order": survey.get("recommended_read_order", []),
            "repository_cartography": survey.get("repository_cartography", {}),
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


def attach_planning_department_to_brief(brief: dict[str, Any], planning_department: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(brief)
    enriched["planning_department"] = planning_department
    enriched["planning_department_handoff"] = planning_department.get("code_brigade_work_package_handoff", {})
    handoff = dict(enriched.get("code_brigade_handoff", {}) if isinstance(enriched.get("code_brigade_handoff"), dict) else {})
    handoff["planning_department_package"] = {
        "artifact": "planning_department.json",
        "status": planning_department.get("status", ""),
        "required_before_code_brigade_execution": True,
        "work_package_handoff_status": planning_department.get("code_brigade_work_package_handoff", {}).get("status", ""),
        "brigade_handoff_contract_status": planning_department.get("brigade_handoff_contract", {}).get("status", ""),
        "multi_pass_investigation_status": planning_department.get("multi_pass_repo_investigation", {}).get("status", ""),
    }
    enriched["code_brigade_handoff"] = handoff
    return enriched
