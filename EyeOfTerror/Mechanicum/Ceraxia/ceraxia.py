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
from diagnostic_repair_contract import execute_diagnostic_repair_request  # noqa: E402
from engineering_memory import build_engineering_memory_update  # noqa: E402
from execution_adapter import can_infer_guarded_natural_language_patch  # noqa: E402
from planning_department import build_planning_department_package  # noqa: E402
from verification_adapter import run_verification_commands  # noqa: E402
from repo_survey import survey_repository  # noqa: E402


CONTRACT_VERSION = "eye-mechanicum.v1"
EXECUTION_MODES = {"dry_run", "guarded_patch", "repo_engineer", "review_only"}


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
    "planning_department.json",
    "implementation_brief.json",
    "worker_report.json",
    "verification_report.json",
    "review_gate.json",
    "diagnostic_repair_request.json",
    "planning_feedback_request.json",
    "status.json",
    "final_report.md",
    "execution_readiness.json",
    "run_summary.json",
    "evidence_matrix.json",
    "engineering_memory_update.json",
]


@dataclass(frozen=True)
class CeraxiaInput:
    task: str
    repo_path: str
    execution_mode: str = ""
    dry_run: bool = True
    execute_verification: bool = False
    execute_diagnostic_repair: bool = False
    constraints: tuple[str, ...] = ()
    verification_commands: tuple[str, ...] = ()
    runs_root: Path = RUNS_ROOT


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def task_slug(task: str, repo_path: str = "") -> str:
    words = re.findall(r"[a-zA-Z0-9а-яА-ЯёЁ]+", task.lower())
    slug = "-".join(words[:6]) or "task"
    digest = hashlib.sha1(f"{task}\n{repo_path}".encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def allocate_run_dir(runs_root: Path, base_run_id: str) -> tuple[str, Path]:
    run_id = base_run_id
    run_dir = runs_root / run_id
    counter = 2
    while run_dir.exists():
        run_id = f"{base_run_id}-{counter}"
        run_dir = runs_root / run_id
        counter += 1
    return run_id, run_dir


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
            "reverse_dependency_index": survey.get("reverse_dependency_index", {}),
            "test_coverage_links": survey.get("test_coverage_links", []),
            "caller_candidates": survey.get("caller_candidates", []),
            "contract_surface_candidates": survey.get("contract_surface_candidates", []),
            "package_manifest_candidates": survey.get("package_manifest_candidates", []),
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
        "multi_pass_investigation_status": planning_department.get("multi_pass_repo_investigation", {}).get("status", ""),
    }
    enriched["code_brigade_handoff"] = handoff
    return enriched


def changed_file_paths_from_worker(worker_report: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    values = worker_report.get("changed_files") if isinstance(worker_report.get("changed_files"), list) else []
    for item in values:
        if isinstance(item, str) and item:
            paths.append(item)
        elif isinstance(item, dict) and isinstance(item.get("path"), str) and item.get("path"):
            paths.append(item["path"])
    return paths


def verification_after_mutation_evidence(
    brief: dict[str, Any],
    worker_report: dict[str, Any],
    commands_executed: list[dict[str, Any]],
) -> dict[str, Any]:
    if worker_report.get("status") != "implemented":
        return {
            "kind": "ceraxia_verification_after_mutation_evidence",
            "status": "not_required",
            "reason": "worker did not report source mutation",
            "changed_files": [],
            "commands_executed_count": len(commands_executed),
            "blockers": [],
        }
    repo = Path(str(brief.get("repo_path") or ""))
    changed_files = changed_file_paths_from_worker(worker_report)
    blockers: list[str] = []
    rows: list[dict[str, Any]] = []
    for rel_path in changed_files:
        row: dict[str, Any] = {"path": rel_path}
        try:
            path = (repo / rel_path).resolve()
            path.relative_to(repo.resolve())
        except ValueError:
            row.update({"status": "blocked", "reason": "path escapes repo"})
            blockers.append(f"changed file escapes repo: {rel_path}")
            rows.append(row)
            continue
        if not path.exists() or not path.is_file() or path.is_symlink():
            row.update({"status": "blocked", "reason": "changed file is missing, not a file, or a symlink"})
            blockers.append(f"changed file cannot be observed before verification: {rel_path}")
            rows.append(row)
            continue
        data = path.read_bytes()
        row.update(
            {
                "status": "observed_after_worker",
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "mtime_ns": path.stat().st_mtime_ns,
            }
        )
        rows.append(row)
    if not changed_files:
        blockers.append("implemented worker report has no changed_files to bind to verification")
    if not commands_executed:
        blockers.append("implemented worker report has no executed verification commands after mutation")
    return {
        "kind": "ceraxia_verification_after_mutation_evidence",
        "status": "complete" if not blockers else "blocked",
        "repo_path": str(repo),
        "changed_files": rows,
        "changed_file_count": len(changed_files),
        "commands_executed_count": len(commands_executed),
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
    commands_executed = [item for item in execution.get("results", []) if item.get("status") != "planned"]
    after_mutation_evidence = verification_after_mutation_evidence(brief, worker_report, commands_executed)
    return {
        "kind": "ceraxia_verification_report",
        "status": status,
        "commands_planned": commands,
        "commands_executable": executable_commands,
        "commands_executed": commands_executed,
        "output_summary": summarize_verification_output(commands_executed),
        "verification_after_mutation_evidence": after_mutation_evidence,
        "verification_execution": execution,
        "negative_tests_required": strategy.get("negative_tests", []),
        "broad_verification_required": bool(strategy.get("broad_verification_required")),
        "blockers": execution.get("blockers", []) if execution.get("blockers") else (brief.get("blockers", []) if blocked else []),
        "dry_run": dry_run,
        "execute_verification": execute_verification,
    }


def output_signal_for_result(result: dict[str, Any]) -> str:
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    combined = f"{stdout}\n{stderr}".lower()
    if "traceback" in combined:
        return "traceback"
    if result.get("status") == "failed" or "failed" in combined or " error" in combined or "errors" in combined:
        return "failure_text"
    if "passed" in combined or combined.strip().endswith("ok"):
        return "pass_text"
    if stdout or stderr:
        return "output_present"
    return "output_empty"


def summarize_verification_output(commands: list[Any]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for item in commands:
        if not isinstance(item, dict):
            continue
        stdout = str(item.get("stdout") or "")
        stderr = str(item.get("stderr") or "")
        diagnostics = item.get("diagnostics") if isinstance(item.get("diagnostics"), dict) else {}
        summary.append(
            {
                "command": str(item.get("command") or ""),
                "status": str(item.get("status") or ""),
                "returncode": item.get("returncode"),
                "stdout_nonempty": bool(stdout),
                "stderr_nonempty": bool(stderr),
                "output_signal": output_signal_for_result(item),
                "has_traceback": bool(diagnostics.get("has_traceback")),
                "has_assertion_failure": bool(diagnostics.get("has_assertion_failure")),
                "has_syntax_error": bool(diagnostics.get("has_syntax_error")),
                "has_no_tests_ran": bool(diagnostics.get("has_no_tests_ran")),
                "traceback_files": diagnostics.get("traceback_files", []) if isinstance(diagnostics.get("traceback_files"), list) else [],
                "missing_imports": diagnostics.get("missing_imports", []) if isinstance(diagnostics.get("missing_imports"), list) else [],
            }
        )
    return summary


def output_signal_counts_from_summary(output_summary: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in output_summary:
        if not isinstance(item, dict):
            continue
        signal = str(item.get("output_signal") or "unknown")
        counts[signal] = counts.get(signal, 0) + 1
    return counts


def output_diagnostic_counts_from_summary(output_summary: list[Any]) -> dict[str, int]:
    return {
        "traceback": sum(1 for item in output_summary if isinstance(item, dict) and item.get("has_traceback")),
        "assertion_failure": sum(1 for item in output_summary if isinstance(item, dict) and item.get("has_assertion_failure")),
        "syntax_error": sum(1 for item in output_summary if isinstance(item, dict) and item.get("has_syntax_error")),
        "no_tests_ran": sum(1 for item in output_summary if isinstance(item, dict) and item.get("has_no_tests_ran")),
    }


def diagnostic_signals_from_summary_item(item: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    for key, label in [
        ("has_traceback", "traceback"),
        ("has_assertion_failure", "assertion_failure"),
        ("has_syntax_error", "syntax_error"),
        ("has_no_tests_ran", "no_tests_ran"),
    ]:
        if item.get(key):
            signals.append(label)
    if item.get("missing_imports"):
        signals.append("missing_import")
    if not signals and str(item.get("status") or "") in {"failed", "blocked"}:
        signals.append("failed_command")
    return signals


def failure_classification_from_repair_item(command_status: str, signals: list[str]) -> dict[str, Any]:
    if "syntax_error" in signals:
        failure_type = "syntax_error"
        severity = "high"
    elif "missing_import" in signals:
        failure_type = "missing_dependency_or_import"
        severity = "high"
    elif "assertion_failure" in signals:
        failure_type = "behavior_regression_or_unmet_acceptance"
        severity = "high"
    elif "no_tests_ran" in signals:
        failure_type = "verification_command_mismatch"
        severity = "normal"
    elif "traceback" in signals:
        failure_type = "runtime_exception"
        severity = "high"
    elif command_status == "blocked":
        failure_type = "blocked_verification_command"
        severity = "normal"
    else:
        failure_type = "failed_verification_command"
        severity = "normal"
    return {
        "type": failure_type,
        "severity": severity,
        "signals": signals,
        "command_status": command_status,
    }


def repair_hypotheses_from_failure(
    failure_classification: dict[str, Any],
    source_candidates: list[str],
    missing_imports: list[str],
) -> list[dict[str, Any]]:
    failure_type = str(failure_classification.get("type") or "")
    primary = source_candidates[0] if source_candidates else ""
    if failure_type == "missing_dependency_or_import":
        return [
            {
                "hypothesis": "map missing import to an existing or explicitly planned source module before editing",
                "source_candidates": source_candidates,
                "missing_imports": missing_imports,
                "mutation_allowed_after": "missing import maps to candidate source or allowed create path",
            }
        ]
    if failure_type == "syntax_error":
        return [
            {
                "hypothesis": "repair syntax in the traceback source without changing tests",
                "source_candidates": source_candidates,
                "mutation_allowed_after": "traceback file is read and syntax failure is reproduced",
            }
        ]
    if failure_type == "behavior_regression_or_unmet_acceptance":
        return [
            {
                "hypothesis": "repair behavior in candidate source while preserving the test oracle",
                "primary_candidate": primary,
                "source_candidates": source_candidates,
                "mutation_allowed_after": "source candidate and related caller/test evidence are read",
            }
        ]
    if failure_type == "verification_command_mismatch":
        return [
            {
                "hypothesis": "classify zero-test or runner mismatch before source mutation",
                "source_candidates": source_candidates,
                "mutation_allowed_after": "verification command is corrected or explicitly blocked",
            }
        ]
    return [
        {
            "hypothesis": "inspect diagnostic source candidates before attempting a narrow repair",
            "source_candidates": source_candidates,
            "mutation_allowed_after": "failure is mapped to a repo-local source surface",
        }
    ]


def build_diagnostic_repair_queue(
    brief: dict[str, Any],
    verification_report: dict[str, Any],
    worker_report: dict[str, Any],
) -> dict[str, Any]:
    repair_plan = brief.get("diagnostic_repair_plan") if isinstance(brief.get("diagnostic_repair_plan"), dict) else {}
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    output_summary = verification_report.get("output_summary") if isinstance(verification_report.get("output_summary"), list) else []
    read_before_repair = repair_plan.get("read_before_repair") if isinstance(repair_plan.get("read_before_repair"), list) else []
    stop_conditions = repair_plan.get("stop_conditions") if isinstance(repair_plan.get("stop_conditions"), list) else []
    repair_evidence = repair_plan.get("repair_evidence_required") if isinstance(repair_plan.get("repair_evidence_required"), list) else []
    default_targets = implementation_plan.get("target_files_to_inspect") if isinstance(implementation_plan.get("target_files_to_inspect"), list) else []
    if not default_targets:
        repo_evidence = brief.get("repo_survey_evidence") if isinstance(brief.get("repo_survey_evidence"), dict) else {}
        default_targets = repo_evidence.get("candidate_files") if isinstance(repo_evidence.get("candidate_files"), list) else []
    surface_matrix = brief.get("surface_verification_matrix") if isinstance(brief.get("surface_verification_matrix"), dict) else {}
    surface_rows = surface_matrix.get("rows") if isinstance(surface_matrix.get("rows"), list) else []
    surface_evidence = surface_evidence_rows(surface_rows, verification_report)
    package_matrix = brief.get("surface_package_matrix") if isinstance(brief.get("surface_package_matrix"), dict) else {}
    package_rows = package_matrix.get("rows") if isinstance(package_matrix.get("rows"), list) else []
    items: list[dict[str, Any]] = []
    for item in output_summary:
        if not isinstance(item, dict):
            continue
        command_status = str(item.get("status") or "")
        signals = diagnostic_signals_from_summary_item(item)
        if command_status not in {"failed", "blocked"} and not signals:
            continue
        traceback_files = item.get("traceback_files") if isinstance(item.get("traceback_files"), list) else []
        missing_imports = item.get("missing_imports") if isinstance(item.get("missing_imports"), list) else []
        concrete_read_targets = [str(path) for path in traceback_files if isinstance(path, str)]
        if not concrete_read_targets:
            concrete_read_targets = [str(path) for path in default_targets if isinstance(path, str)]
        source_candidates = [
            str(path).split(":", 1)[0]
            for path in concrete_read_targets
            if isinstance(path, str) and path
        ]
        command = str(item.get("command") or "")
        impacted_surfaces = [
            str(row.get("surface") or "")
            for row in surface_evidence
            if isinstance(row, dict) and command in (row.get("matched_commands") if isinstance(row.get("matched_commands"), list) else [])
        ]
        if not impacted_surfaces and command_status in {"failed", "blocked"}:
            impacted_surfaces = [
                str(row.get("surface") or "")
                for row in surface_evidence
                if isinstance(row, dict) and row.get("status") in {"failed", "blocked"}
            ]
        package_ids = sorted(
            {
                str(package_id)
                for row in package_rows
                if isinstance(row, dict) and str(row.get("surface") or "") in impacted_surfaces
                for package_id in (row.get("package_ids") if isinstance(row.get("package_ids"), list) else [])
                if isinstance(package_id, str)
            }
        )
        failure_classification = failure_classification_from_repair_item(command_status, signals)
        items.append(
            {
                "command": command,
                "status": command_status,
                "priority": "high" if "traceback" in signals or "syntax_error" in signals else "normal",
                "failure_classification": failure_classification,
                "diagnostic_signals": signals,
                "impacted_surfaces": impacted_surfaces,
                "package_ids": package_ids,
                "traceback_files": traceback_files,
                "missing_imports": missing_imports,
                "source_candidates": source_candidates,
                "repair_hypotheses": repair_hypotheses_from_failure(failure_classification, source_candidates, [str(item) for item in missing_imports if isinstance(item, str)]),
                "read_before_repair": read_before_repair,
                "concrete_read_targets": concrete_read_targets,
                "stop_conditions": stop_conditions,
                "repair_evidence_required": repair_evidence,
                "max_repair_attempts": repair_plan.get("max_repair_attempts", 3),
            }
        )
    return {
        "status": "queued" if items else "empty",
        "item_count": len(items),
        "items": items,
        "source": "verification_output_diagnostics",
        "plan_present": bool(repair_plan),
    }


def build_diagnostic_repair_request(
    run_id: str,
    brief: dict[str, Any],
    worker_report: dict[str, Any],
    verification_report: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    queue = review.get("diagnostic_repair_queue") if isinstance(review.get("diagnostic_repair_queue"), dict) else {}
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    return {
        "kind": "ceraxia_code_brigade_diagnostic_repair_request",
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "status": "required" if queue.get("item_count", 0) else "not_required",
        "target": "CodeBrigade",
        "repo_path": brief.get("repo_path", ""),
        "task": brief.get("task", ""),
        "verification_status": verification_report.get("status", ""),
        "review_decision": review.get("decision", ""),
        "diagnostic_repair_plan": brief.get("diagnostic_repair_plan", {}) if isinstance(brief.get("diagnostic_repair_plan"), dict) else {},
        "diagnostic_repair_queue": queue,
        "suggested_code_brigade_command": [
            "python3",
            "EyeOfTerror/Mechanicum/CodeBrigade/diagnostic_repair_contract.py",
            "--execute",
            "diagnostic_repair_request.json",
        ],
        "target_files_to_inspect": implementation_plan.get("target_files_to_inspect", []) if isinstance(implementation_plan.get("target_files_to_inspect"), list) else [],
        "test_files_to_preserve": implementation_plan.get("test_files_to_preserve", []) if isinstance(implementation_plan.get("test_files_to_preserve"), list) else [],
        "reverse_dependency_index": implementation_plan.get("reverse_dependency_index", {}) if isinstance(implementation_plan.get("reverse_dependency_index"), dict) else {},
        "scope_budget": implementation_plan.get("scope_budget", {}) if isinstance(implementation_plan.get("scope_budget"), dict) else {},
        "return_contract": [
            "worker_report.json with changed files, package statuses, and residual blockers",
            "verification_report.json after rerunning relevant failed commands",
            "diagnostic_summary mapped to repaired queue items",
        ],
    }


def build_planning_feedback_request(
    run_id: str,
    packet: dict[str, Any],
    brief: dict[str, Any],
    worker_report: dict[str, Any],
    verification_report: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    findings = review.get("findings") if isinstance(review.get("findings"), list) else []
    feedback_findings = [
        item
        for item in findings
        if isinstance(item, dict)
        and any(
            needle in str(item.get("finding") or "")
            for needle in [
                "planning packet",
                "surface verification matrix",
                "surface package matrix",
                "investigation playbook",
                "change control plan",
                "acceptance trace matrix",
                "constraint trace matrix",
                "assumption register",
                "worker output contract",
                "planning department",
            ]
        )
    ]
    worker_output_sufficiency = review.get("worker_output_contract_sufficiency") if isinstance(review.get("worker_output_contract_sufficiency"), dict) else {}
    planning_department_sufficiency = review.get("planning_department_sufficiency") if isinstance(review.get("planning_department_sufficiency"), dict) else {}
    return {
        "kind": "ceraxia_planning_feedback_request",
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "status": "required" if feedback_findings else "not_required",
        "target": "PlanningBrigade",
        "source": "Ceraxia.review_gate",
        "repo_path": brief.get("repo_path", ""),
        "task": brief.get("task", packet.get("task", "")),
        "review_decision": review.get("decision", ""),
        "worker_status": worker_report.get("status", ""),
        "verification_status": verification_report.get("status", ""),
        "planning_review_decision": brief.get("planning_review_gate", {}).get("decision", "") if isinstance(brief.get("planning_review_gate"), dict) else "",
        "feedback_findings": feedback_findings,
        "worker_output_contract_sufficiency": worker_output_sufficiency,
        "planning_department_sufficiency": planning_department_sufficiency,
        "replan_focus": [
            "repair planning packet contract drift",
            "repair planning department RFC, multi-pass investigation, and CodeBrigade handoff",
            "refresh implementation brief sections before CodeBrigade mutation",
            "tighten worker-output contract when package statuses or evidence sources are missing",
            "return a new planning_packet.json and implementation_brief.json candidate",
        ],
        "required_return_artifacts": [
            "planning_packet.json",
            "planning_department.json",
            "implementation_brief.json",
            "worker_output_contract",
            "planning_review_gate",
        ],
        "suggested_planning_command": [
            "python3",
            "EyeOfTerror/Mechanicum/PlanningBrigade/planning_brigade.py",
            "--validate",
            "--input-json",
            "task.json",
        ],
    }


def output_consistency_findings(verification_report: dict[str, Any], output_summary: list[Any]) -> list[str]:
    problems: list[str] = []
    report_status = str(verification_report.get("status") or "")
    for item in output_summary:
        if not isinstance(item, dict):
            continue
        signal = str(item.get("output_signal") or "")
        command = str(item.get("command") or "<unknown command>")
        command_status = str(item.get("status") or "")
        if signal in {"failure_text", "traceback"} and command_status == "passed":
            problems.append(f"verification command reported passed but output contains {signal}: {command}")
        if signal in {"failure_text", "traceback"} and report_status == "passed":
            problems.append(f"verification report is passed but output contains {signal}: {command}")
    return problems


def meaningful_executed_commands(verification_report: dict[str, Any]) -> list[dict[str, Any]]:
    commands = verification_report.get("commands_executed") if isinstance(verification_report.get("commands_executed"), list) else []
    return [item for item in commands if isinstance(item, dict) and item.get("status") in {"passed", "failed", "blocked"}]


def command_texts(commands: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("command", "")) for item in commands if isinstance(item, dict)]


def commands_matching_surface(surface: str, commands: list[dict[str, Any]], negative_tests: list[Any]) -> list[str]:
    texts = command_texts(commands)
    if surface == "source_behavior":
        return [command for command in texts if "py_compile" in command or "pytest" in command or "test" in command or "unittest" in command]
    if surface == "test_surface":
        return [command for command in texts if "pytest" in command or "test" in command or "unittest" in command]
    if surface in {"public_api_contract", "security_boundary", "data_compatibility", "concurrency_runtime", "runtime_configuration"} and negative_tests:
        return [command for command in texts if "pytest" in command or "test" in command or "unittest" in command]
    return []


def output_summary_for_commands(output_summary: list[Any], commands: list[str]) -> list[dict[str, Any]]:
    command_set = set(commands)
    return [
        item
        for item in output_summary
        if isinstance(item, dict) and str(item.get("command") or "") in command_set
    ]


def output_signal_counts_for_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        signal = str(row.get("output_signal") or "unknown")
        counts[signal] = counts.get(signal, 0) + 1
    return counts


def output_diagnostic_counts_for_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "traceback": sum(1 for row in rows if row.get("has_traceback")),
        "assertion_failure": sum(1 for row in rows if row.get("has_assertion_failure")),
        "syntax_error": sum(1 for row in rows if row.get("has_syntax_error")),
        "no_tests_ran": sum(1 for row in rows if row.get("has_no_tests_ran")),
        "missing_import": sum(1 for row in rows if row.get("missing_imports")),
    }


def has_test_command(commands: list[dict[str, Any]]) -> bool:
    return any("pytest" in command or "test" in command or "unittest" in command for command in command_texts(commands))


def has_source_command(commands: list[dict[str, Any]]) -> bool:
    return any("py_compile" in command or "pytest" in command or "test" in command or "unittest" in command for command in command_texts(commands))


def surface_evidence_rows(surface_rows: list[Any], verification_report: dict[str, Any]) -> list[dict[str, Any]]:
    planned_commands = verification_report.get("commands_planned") if isinstance(verification_report.get("commands_planned"), list) else []
    executed_commands = meaningful_executed_commands(verification_report)
    output_summary = verification_report.get("output_summary") if isinstance(verification_report.get("output_summary"), list) else []
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
            matched_commands: list[str] = []
        elif verification_status in {"failed", "blocked"}:
            status = verification_status
            reason = f"verification report status is {verification_status}"
            matched_commands = commands_matching_surface(surface, executed_commands, negative_tests)
        elif not planned_commands:
            status = "missing"
            reason = "no verification command is planned"
            matched_commands = []
        elif not executed_commands:
            status = "planned_only"
            reason = "verification is planned but not executed"
            matched_commands = []
        elif surface == "source_behavior" and has_source_command(executed_commands):
            status = "executed"
            reason = "source command executed"
            matched_commands = commands_matching_surface(surface, executed_commands, negative_tests)
        elif surface == "test_surface" and has_test_command(executed_commands):
            status = "executed"
            reason = "test command executed"
            matched_commands = commands_matching_surface(surface, executed_commands, negative_tests)
        elif surface in {"public_api_contract", "security_boundary", "data_compatibility", "concurrency_runtime", "runtime_configuration"}:
            if negative_tests and has_test_command(executed_commands):
                status = "executed"
                reason = "negative or compatibility test command executed"
                matched_commands = commands_matching_surface(surface, executed_commands, negative_tests)
            else:
                status = "partial"
                reason = "executed commands do not directly prove this high-risk surface"
                matched_commands = []
        elif executed_commands:
            status = "partial"
            reason = "some verification executed, but this surface has no direct evidence"
            matched_commands = []
        else:
            status = "planned_only"
            reason = "verification is planned but not executed"
            matched_commands = []
        matched_output_rows = output_summary_for_commands(output_summary, matched_commands)
        rows.append(
            {
                "surface": surface,
                "status": status,
                "reason": reason,
                "matched_commands": matched_commands,
                "matched_output_signal_counts": output_signal_counts_for_rows(matched_output_rows),
                "matched_output_diagnostic_counts": output_diagnostic_counts_for_rows(matched_output_rows),
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


def investigation_sufficiency_from_worker(worker_report: dict[str, Any]) -> dict[str, Any]:
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    read_stages = implementation_plan.get("investigation_read_stages") if isinstance(implementation_plan.get("investigation_read_stages"), list) else []
    evidence_questions = (
        implementation_plan.get("investigation_evidence_questions")
        if isinstance(implementation_plan.get("investigation_evidence_questions"), list)
        else []
    )
    mutation_blockers = (
        implementation_plan.get("investigation_mutation_blockers")
        if isinstance(implementation_plan.get("investigation_mutation_blockers"), list)
        else []
    )
    replan_triggers = (
        implementation_plan.get("investigation_replan_triggers")
        if isinstance(implementation_plan.get("investigation_replan_triggers"), list)
        else []
    )
    blockers: list[str] = []
    if len(read_stages) < 5:
        blockers.append("investigation playbook has fewer than five read stages")
    if not all(isinstance(stage, dict) and stage.get("stage") and stage.get("must_collect") for stage in read_stages):
        blockers.append("investigation playbook read stages are incomplete")
    if len(evidence_questions) < 4:
        blockers.append("investigation playbook has too few evidence questions")
    if len(mutation_blockers) < 3:
        blockers.append("investigation playbook has too few mutation blockers")
    if len(replan_triggers) < 3:
        blockers.append("investigation playbook has too few replan triggers")
    return {
        "status": "complete" if not blockers else "blocked",
        "read_stage_count": len(read_stages),
        "evidence_question_count": len(evidence_questions),
        "mutation_blocker_count": len(mutation_blockers),
        "replan_trigger_count": len(replan_triggers),
        "first_stage": str(read_stages[0].get("stage") or "") if read_stages and isinstance(read_stages[0], dict) else "",
        "blockers": blockers,
    }


def change_control_sufficiency_from_worker(worker_report: dict[str, Any]) -> dict[str, Any]:
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    allowed_intents = implementation_plan.get("change_allowed_intents") if isinstance(implementation_plan.get("change_allowed_intents"), list) else []
    protected_invariants = implementation_plan.get("change_protected_invariants") if isinstance(implementation_plan.get("change_protected_invariants"), list) else []
    mutation_requires = implementation_plan.get("change_mutation_requires") if isinstance(implementation_plan.get("change_mutation_requires"), list) else []
    diff_questions = implementation_plan.get("change_diff_review_questions") if isinstance(implementation_plan.get("change_diff_review_questions"), list) else []
    rollback_triggers = implementation_plan.get("change_rollback_triggers") if isinstance(implementation_plan.get("change_rollback_triggers"), list) else []
    post_change_proofs = implementation_plan.get("change_post_change_proofs") if isinstance(implementation_plan.get("change_post_change_proofs"), list) else []
    blockers: list[str] = []
    for label, items, minimum in [
        ("allowed change intents", allowed_intents, 3),
        ("protected invariants", protected_invariants, 3),
        ("mutation requirements", mutation_requires, 4),
        ("diff review questions", diff_questions, 3),
        ("rollback triggers", rollback_triggers, 3),
        ("post-change proofs", post_change_proofs, 3),
    ]:
        if len(items) < minimum:
            blockers.append(f"change control plan has too few {label}")
    return {
        "status": "complete" if not blockers else "blocked",
        "allowed_intent_count": len(allowed_intents),
        "protected_invariant_count": len(protected_invariants),
        "mutation_requirement_count": len(mutation_requires),
        "diff_review_question_count": len(diff_questions),
        "rollback_trigger_count": len(rollback_triggers),
        "post_change_proof_count": len(post_change_proofs),
        "blockers": blockers,
    }


def acceptance_trace_sufficiency_from_worker(worker_report: dict[str, Any]) -> dict[str, Any]:
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    rows = implementation_plan.get("acceptance_trace_rows") if isinstance(implementation_plan.get("acceptance_trace_rows"), list) else []
    complete = implementation_plan.get("acceptance_trace_complete") is True
    definition_of_done_complete = implementation_plan.get("definition_of_done_trace_complete") is True
    definition_of_done_count = implementation_plan.get("definition_of_done_count")
    traced_definition_of_done_count = implementation_plan.get("traced_definition_of_done_count")
    missing_definition_of_done = implementation_plan.get("missing_definition_of_done") if isinstance(implementation_plan.get("missing_definition_of_done"), list) else []
    blockers: list[str] = []
    if not rows:
        blockers.append("acceptance trace matrix has no rows")
    if not complete:
        blockers.append("acceptance trace matrix is not complete")
    if not definition_of_done_complete:
        blockers.append("definition_of_done trace is not complete")
    if not isinstance(definition_of_done_count, int) or definition_of_done_count < 1:
        blockers.append("definition_of_done_count is missing")
        definition_of_done_count = 0
    if not isinstance(traced_definition_of_done_count, int):
        blockers.append("traced_definition_of_done_count is missing")
        traced_definition_of_done_count = 0
    if traced_definition_of_done_count < definition_of_done_count:
        blockers.append("not every definition_of_done item is traced")
    if missing_definition_of_done:
        blockers.append("missing definition_of_done items: " + ", ".join(str(item) for item in missing_definition_of_done))
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            blockers.append(f"acceptance trace row {index} is not an object")
            continue
        if not row.get("requirement"):
            blockers.append(f"acceptance trace row {index} has no requirement")
        if not isinstance(row.get("planned_evidence"), list) or not row.get("planned_evidence"):
            blockers.append(f"acceptance trace row {index} has no planned evidence")
        if not isinstance(row.get("package_ids"), list) or not row.get("package_ids"):
            blockers.append(f"acceptance trace row {index} has no package ids")
    return {
        "status": "complete" if not blockers else "blocked",
        "row_count": len(rows),
        "blocked_row_count": sum(1 for row in rows if isinstance(row, dict) and row.get("status") == "blocked"),
        "complete": complete,
        "definition_of_done_complete": definition_of_done_complete,
        "definition_of_done_count": definition_of_done_count,
        "traced_definition_of_done_count": traced_definition_of_done_count,
        "missing_definition_of_done": missing_definition_of_done,
        "blockers": blockers,
    }


def constraint_trace_sufficiency_from_worker(worker_report: dict[str, Any]) -> dict[str, Any]:
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    rows = implementation_plan.get("constraint_trace_rows") if isinstance(implementation_plan.get("constraint_trace_rows"), list) else []
    complete = implementation_plan.get("constraint_trace_complete") is True
    blockers: list[str] = []
    if not rows:
        blockers.append("constraint trace matrix has no rows")
    if not complete:
        blockers.append("constraint trace matrix is not complete")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            blockers.append(f"constraint trace row {index} is not an object")
            continue
        if not row.get("constraint"):
            blockers.append(f"constraint trace row {index} has no constraint")
        if not isinstance(row.get("planned_evidence"), list) or not row.get("planned_evidence"):
            blockers.append(f"constraint trace row {index} has no planned evidence")
        if not isinstance(row.get("package_ids"), list) or not row.get("package_ids"):
            blockers.append(f"constraint trace row {index} has no package ids")
    return {
        "status": "complete" if not blockers else "blocked",
        "row_count": len(rows),
        "blocked_row_count": sum(1 for row in rows if isinstance(row, dict) and row.get("status") == "blocked"),
        "complete": complete,
        "blockers": blockers,
    }


def assumption_sufficiency_from_worker(worker_report: dict[str, Any]) -> dict[str, Any]:
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    rows = implementation_plan.get("assumption_rows") if isinstance(implementation_plan.get("assumption_rows"), list) else []
    replan_triggers = implementation_plan.get("assumption_replan_triggers") if isinstance(implementation_plan.get("assumption_replan_triggers"), list) else []
    blockers: list[str] = []
    if len(rows) < 3:
        blockers.append("assumption register has fewer than three assumptions")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            blockers.append(f"assumption row {index} is not an object")
            continue
        for key in ("id", "assumption", "risk_if_false", "validation_source", "blocks_when_false", "owner"):
            if key not in row:
                blockers.append(f"assumption row {index} missing {key}")
    if len(replan_triggers) < 3:
        blockers.append("assumption register has too few replan triggers")
    return {
        "status": "complete" if not blockers else "blocked",
        "assumption_count": len(rows),
        "replan_trigger_count": len(replan_triggers),
        "blockers": blockers,
    }


def worker_output_contract_sufficiency_from_worker(worker_report: dict[str, Any]) -> dict[str, Any]:
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    output_contract = implementation_plan.get("worker_output_contract") if isinstance(implementation_plan.get("worker_output_contract"), dict) else {}
    package_statuses = worker_report.get("work_package_statuses") if isinstance(worker_report.get("work_package_statuses"), list) else []
    status_by_package = {
        str(row.get("package_id") or ""): row
        for row in package_statuses
        if isinstance(row, dict) and row.get("package_id")
    }
    required_packages = output_contract.get("required_package_statuses") if isinstance(output_contract.get("required_package_statuses"), list) else []
    contract_rows = output_contract.get("package_result_contract") if isinstance(output_contract.get("package_result_contract"), list) else []
    allowed_statuses = {"planned", "implemented", "blocked"}
    blockers: list[str] = []
    if not output_contract:
        blockers.append("worker output contract is missing from implementation plan")
    if len(output_contract.get("required_reports", []) if isinstance(output_contract.get("required_reports"), list) else []) < 3:
        blockers.append("worker output contract does not name required reports")
    for package_id in [str(item) for item in required_packages if str(item)]:
        status_row = status_by_package.get(package_id)
        if not status_row:
            blockers.append(f"worker output contract required package status is missing: {package_id}")
            continue
        if status_row.get("status") not in allowed_statuses:
            blockers.append(f"worker output contract package has invalid status: {package_id}")
        if not status_row.get("evidence_source"):
            blockers.append(f"worker output contract package has no evidence source: {package_id}")
        if status_row.get("status") == "blocked" and not worker_report.get("notes"):
            blockers.append(f"blocked package has no worker_report notes: {package_id}")
    contract_package_ids = [
        str(row.get("package_id") or "")
        for row in contract_rows
        if isinstance(row, dict) and row.get("package_id")
    ]
    rows_with_acceptance_requirements = 0
    for row in contract_rows:
        if not isinstance(row, dict):
            blockers.append("worker output contract package row is not an object")
            continue
        package_id = str(row.get("package_id") or "<unknown>")
        requirements = row.get("acceptance_requirements")
        if isinstance(requirements, list) and requirements:
            rows_with_acceptance_requirements += 1
        else:
            blockers.append(f"worker output contract package has no acceptance requirements: {package_id}")
        if not isinstance(row.get("acceptance_evidence"), list) or not row.get("acceptance_evidence"):
            blockers.append(f"worker output contract package has no acceptance evidence: {package_id}")
    if sorted(contract_package_ids) != sorted(str(item) for item in required_packages if str(item)):
        blockers.append("worker output contract package rows do not match required package statuses")
    return {
        "status": "complete" if not blockers else "blocked",
        "required_package_count": len(required_packages),
        "reported_package_count": len(package_statuses),
        "contract_row_count": len(contract_rows),
        "rows_with_acceptance_requirements": rows_with_acceptance_requirements,
        "blockers": blockers,
    }


def planning_department_sufficiency_from_worker(worker_report: dict[str, Any]) -> dict[str, Any]:
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    roles = implementation_plan.get("planning_department_roles") if isinstance(implementation_plan.get("planning_department_roles"), list) else []
    phases = implementation_plan.get("multi_pass_investigation_phases") if isinstance(implementation_plan.get("multi_pass_investigation_phases"), list) else []
    handoff = (
        implementation_plan.get("planning_department_work_package_handoff")
        if isinstance(implementation_plan.get("planning_department_work_package_handoff"), dict)
        else {}
    )
    required_phase_ids = {
        "project_map",
        "dependency_public_api_map",
        "test_ci_manifest_map",
        "targeted_pre_mutation_reads",
    }
    phase_ids = {
        str(phase.get("id") or "")
        for phase in phases
        if isinstance(phase, dict)
    }
    packages = handoff.get("packages") if isinstance(handoff.get("packages"), list) else []
    blockers: list[str] = []
    if implementation_plan.get("planning_department_status") != "ready_for_code_brigade":
        blockers.append("planning department is not ready for CodeBrigade")
    if len(roles) < 5:
        blockers.append("planning department has fewer than five specialist roles")
    if implementation_plan.get("engineering_rfc_status") != "accepted_for_code_brigade_handoff":
        blockers.append("engineering RFC/ADR is not accepted for CodeBrigade handoff")
    missing_phases = sorted(required_phase_ids - phase_ids)
    if implementation_plan.get("multi_pass_investigation_status") != "complete":
        blockers.append("multi-pass repository investigation is not complete")
    if missing_phases:
        blockers.append("multi-pass repository investigation is missing phases: " + ", ".join(missing_phases))
    for phase in phases:
        if not isinstance(phase, dict):
            blockers.append("multi-pass repository investigation phase is not an object")
            continue
        if not phase.get("required_before_mutation"):
            blockers.append(f"multi-pass phase is not required before mutation: {phase.get('id')}")
    if handoff.get("status") != "ready":
        blockers.append("planning department CodeBrigade work package handoff is not ready")
    if not packages:
        blockers.append("planning department handoff has no work packages")
    for package in packages:
        if not isinstance(package, dict):
            blockers.append("planning department handoff package is not an object")
            continue
        package_id = str(package.get("id") or "<unknown>")
        if not package.get("acceptance_requirements"):
            blockers.append(f"planning department package has no acceptance requirements: {package_id}")
        if not isinstance(package.get("depends_on"), list):
            blockers.append(f"planning department package has no dependency list: {package_id}")
    return {
        "status": "complete" if not blockers else "blocked",
        "role_count": len(roles),
        "phase_count": len(phases),
        "package_count": len(packages),
        "missing_phase_ids": missing_phases,
        "blockers": blockers,
    }


def pre_mutation_read_sufficiency_from_worker(worker_report: dict[str, Any]) -> dict[str, Any]:
    evidence = worker_report.get("pre_mutation_read_evidence") if isinstance(worker_report.get("pre_mutation_read_evidence"), dict) else {}
    blockers: list[str] = []
    if worker_report.get("status") == "implemented":
        if not evidence:
            blockers.append("implemented worker report lacks pre_mutation_read_evidence")
        elif evidence.get("status") != "complete":
            blockers.extend(
                str(item)
                for item in evidence.get("blockers", [])
                if isinstance(item, str)
            )
            if not blockers:
                blockers.append("pre_mutation_read_evidence is not complete")
        elif int(evidence.get("recorded_read_count") or 0) <= 0 and int(evidence.get("planned_new_file_count") or 0) <= 0:
            blockers.append("pre_mutation_read_evidence has no read or planned-new-file rows")
    return {
        "status": "complete" if not blockers else "blocked",
        "evidence_status": evidence.get("status", "missing") if evidence else "missing",
        "recorded_read_count": int(evidence.get("recorded_read_count") or 0) if evidence else 0,
        "planned_new_file_count": int(evidence.get("planned_new_file_count") or 0) if evidence else 0,
        "required_read_count": int(evidence.get("required_read_count") or 0) if evidence else 0,
        "blockers": blockers,
    }


def source_mutation_scope_sufficiency_from_worker(worker_report: dict[str, Any]) -> dict[str, Any]:
    edit_plan = worker_report.get("edit_plan") if isinstance(worker_report.get("edit_plan"), dict) else {}
    implementation_plan = worker_report.get("implementation_plan") if isinstance(worker_report.get("implementation_plan"), dict) else {}
    changed_files_raw = worker_report.get("changed_files") if isinstance(worker_report.get("changed_files"), list) else []
    changed_files: list[str] = []
    blockers: list[str] = []
    for item in changed_files_raw:
        if isinstance(item, str):
            path = item
        elif isinstance(item, dict) and isinstance(item.get("path"), str):
            path = item["path"]
        else:
            blockers.append("changed_files contains a non-path entry")
            continue
        if path:
            changed_files.append(path)
    allowed_files: set[str] = set()
    for key in ("target_files", "allowed_new_files"):
        values = edit_plan.get(key)
        if isinstance(values, list):
            allowed_files.update(str(item) for item in values if isinstance(item, str) and item)
    for key in ("target_files_to_inspect", "missing_path_hints"):
        values = implementation_plan.get(key)
        if isinstance(values, list):
            allowed_files.update(str(item) for item in values if isinstance(item, str) and item)
    scope_budget = implementation_plan.get("scope_budget") if isinstance(implementation_plan.get("scope_budget"), dict) else {}
    test_edit_budget = int(scope_budget.get("max_test_files_to_edit_without_explicit_user_request") or 0)
    if test_edit_budget > 0:
        values = edit_plan.get("test_files")
        if isinstance(values, list):
            allowed_files.update(str(item) for item in values if isinstance(item, str) and item)
        values = implementation_plan.get("test_files_to_preserve")
        if isinstance(values, list):
            allowed_files.update(str(item) for item in values if isinstance(item, str) and item)
    task_text = str((worker_report.get("autonomous_execution_request") or {}).get("task") or "").lower()
    explicit_test_edit_requested = bool(
        re.search(r"\b(update|change|edit|add|repair|tighten)\b.{0,80}\b(test|tests|self-test|self-tests|self test|self tests)\b", task_text)
        or re.search(r"\b(test|tests|self-test|self-tests|self test|self tests)\b.{0,80}\b(update|change|edit|add|repair|tighten)\b", task_text)
        or re.search(r"\b(drift|prove)\b", task_text)
        or re.search(r"(обнов|измени|добав|исправ).{0,80}тест", task_text)
        or re.search(r"тест.{0,80}(обнов|измени|добав|исправ|доказ)", task_text)
    )
    if explicit_test_edit_requested:
        explicit_existing = set()
        values = implementation_plan.get("existing_path_hints")
        if isinstance(values, list):
            explicit_existing.update(str(item) for item in values if isinstance(item, str) and item)
        for key in ("test_files",):
            values = edit_plan.get(key)
            if isinstance(values, list):
                allowed_files.update(str(item) for item in values if isinstance(item, str) and item in explicit_existing)
        values = implementation_plan.get("test_files_to_preserve")
        if isinstance(values, list):
            allowed_files.update(str(item) for item in values if isinstance(item, str) and item in explicit_existing)
    escaping_files = [
        path for path in changed_files
        if path.startswith("/") or path == ".." or path.startswith("../") or "/../" in path
    ]
    unexpected_files = sorted({path for path in changed_files if path not in allowed_files})
    if worker_report.get("status") == "implemented":
        if not changed_files:
            blockers.append("implemented worker report has no changed_files")
        if not allowed_files:
            blockers.append("implemented worker report has no planned mutation scope")
        if escaping_files:
            blockers.append("changed_files contains paths outside the repository: " + ", ".join(escaping_files[:8]))
        if unexpected_files:
            blockers.append("changed_files includes paths outside edit_plan scope: " + ", ".join(unexpected_files[:8]))
    return {
        "status": "complete" if not blockers else "blocked",
        "changed_file_count": len(changed_files),
        "allowed_file_count": len(allowed_files),
        "changed_files": changed_files,
        "allowed_files": sorted(allowed_files),
        "unexpected_files": unexpected_files,
        "escaping_files": escaping_files,
        "test_edit_budget": test_edit_budget,
        "blockers": blockers,
    }


def verification_after_mutation_sufficiency(
    worker_report: dict[str, Any],
    verification_report: dict[str, Any],
) -> dict[str, Any]:
    evidence = (
        verification_report.get("verification_after_mutation_evidence")
        if isinstance(verification_report.get("verification_after_mutation_evidence"), dict)
        else {}
    )
    commands_executed = verification_report.get("commands_executed") if isinstance(verification_report.get("commands_executed"), list) else []
    blockers: list[str] = []
    if worker_report.get("status") == "implemented":
        if not evidence:
            blockers.append("verification_after_mutation_evidence is missing")
        elif evidence.get("status") != "complete":
            blockers.extend(str(item) for item in evidence.get("blockers", []) if isinstance(item, str))
            if not blockers:
                blockers.append("verification_after_mutation_evidence is not complete")
        if not commands_executed:
            blockers.append("implemented worker report has no executed verification commands")
    return {
        "status": "complete" if not blockers else "blocked",
        "evidence_status": evidence.get("status", "missing") if evidence else "missing",
        "changed_file_count": int(evidence.get("changed_file_count") or 0) if evidence else 0,
        "commands_executed_count": len(commands_executed),
        "blockers": blockers,
    }


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
    output_summary = verification_report.get("output_summary") if isinstance(verification_report.get("output_summary"), list) else []
    output_signal_counts = output_signal_counts_from_summary(output_summary)
    output_diagnostic_counts = output_diagnostic_counts_from_summary(output_summary)
    negative_tests = verification_report.get("negative_tests_required", [])
    verification_sufficiency = {
        "risk_level": brief.get("risk_level", "high"),
        "status": "executed" if meaningful_commands_executed else ("planned_only" if commands_planned else "missing"),
        "commands_planned_count": len(commands_planned),
        "commands_executed_count": len(commands_executed),
        "meaningful_commands_executed_count": len(meaningful_commands_executed),
        "output_summary_count": len(output_summary),
        "output_signal_counts": output_signal_counts,
        "output_diagnostic_counts": output_diagnostic_counts,
        "negative_tests_required_count": len(negative_tests) if isinstance(negative_tests, list) else 0,
        "broad_verification_required": bool(verification_report.get("broad_verification_required")),
    }
    surface_matrix = brief.get("surface_verification_matrix") if isinstance(brief.get("surface_verification_matrix"), dict) else {}
    surface_rows = surface_matrix.get("rows") if isinstance(surface_matrix.get("rows"), list) else []
    surface_blockers = surface_matrix.get("blockers") if isinstance(surface_matrix.get("blockers"), list) else []
    surface_evidence = surface_evidence_rows(surface_rows, verification_report)
    surface_evidence_status_counts = {
        status: sum(1 for row in surface_evidence if row.get("status") == status)
        for status in ("executed", "partial", "planned_only", "missing", "failed", "blocked")
    }
    surface_status = "blocked" if surface_blockers else surface_status_from_rows(surface_evidence)
    surface_verification_sufficiency = {
        "planned_complete": surface_matrix.get("complete") is True,
        "status": surface_status,
        "surface_count": len(surface_rows),
        "status_counts": surface_evidence_status_counts,
        "executed_surface_count": surface_evidence_status_counts["executed"],
        "partial_surface_count": surface_evidence_status_counts["partial"],
        "missing_surface_count": surface_evidence_status_counts["missing"],
        "blocked_surface_count": surface_evidence_status_counts["blocked"],
        "blocker_count": len(surface_blockers),
        "executed_evidence": bool(meaningful_commands_executed),
        "surface_evidence": surface_evidence,
    }
    package_statuses = worker_report.get("work_package_statuses") if isinstance(worker_report.get("work_package_statuses"), list) else []
    package_status_counts = {
        status: sum(1 for item in package_statuses if isinstance(item, dict) and item.get("status") == status)
        for status in ("planned", "implemented", "blocked")
    }
    package_status_missing_evidence = [
        str(item.get("package_id") or "<unknown>")
        for item in package_statuses
        if isinstance(item, dict) and not item.get("evidence_source")
    ]
    package_status_sufficiency = {
        "package_count": len(package_statuses),
        "status_counts": package_status_counts,
        "blocked_package_ids": [
            str(item.get("package_id") or "")
            for item in package_statuses
            if isinstance(item, dict) and item.get("status") == "blocked"
        ],
        "missing_evidence_source_package_ids": package_status_missing_evidence,
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
    investigation_sufficiency = investigation_sufficiency_from_worker(worker_report)
    change_control_sufficiency = change_control_sufficiency_from_worker(worker_report)
    acceptance_trace_sufficiency = acceptance_trace_sufficiency_from_worker(worker_report)
    constraint_trace_sufficiency = constraint_trace_sufficiency_from_worker(worker_report)
    assumption_sufficiency = assumption_sufficiency_from_worker(worker_report)
    worker_output_contract_sufficiency = worker_output_contract_sufficiency_from_worker(worker_report)
    planning_department_sufficiency = planning_department_sufficiency_from_worker(worker_report)
    pre_mutation_read_sufficiency = pre_mutation_read_sufficiency_from_worker(worker_report)
    source_mutation_scope_sufficiency = source_mutation_scope_sufficiency_from_worker(worker_report)
    verification_after_mutation = verification_after_mutation_sufficiency(worker_report, verification_report)
    diagnostic_repair_queue = build_diagnostic_repair_queue(brief, verification_report, worker_report)
    for problem in validate_planning_packet(packet):
        findings.append({"severity": "blocker", "finding": problem})
    if not worker_report.get("implementation_brief_acknowledged", False):
        findings.append({"severity": "blocker", "finding": "implementation brief was not acknowledged"})
    if worker_report["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "worker report is blocked"})
    if package_status_sufficiency["blocked_package_ids"]:
        findings.append({"severity": "blocker", "finding": "work packages are blocked: " + ", ".join(package_status_sufficiency["blocked_package_ids"])})
    if package_status_sufficiency["missing_evidence_source_package_ids"]:
        findings.append({"severity": "blocker", "finding": "work packages lack evidence_source: " + ", ".join(package_status_sufficiency["missing_evidence_source_package_ids"])})
    if surface_package_matrix.get("complete") is False:
        findings.append({"severity": "blocker", "finding": "surface package matrix has blockers"})
    if surface_package_sufficiency["missing_status_package_ids"]:
        findings.append({"severity": "blocker", "finding": "surface package matrix references packages without worker status: " + ", ".join(surface_package_sufficiency["missing_status_package_ids"])})
    if investigation_sufficiency["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "investigation playbook is incomplete: " + "; ".join(investigation_sufficiency["blockers"])})
    if change_control_sufficiency["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "change control plan is incomplete: " + "; ".join(change_control_sufficiency["blockers"])})
    if acceptance_trace_sufficiency["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "acceptance trace matrix is incomplete: " + "; ".join(acceptance_trace_sufficiency["blockers"])})
    if constraint_trace_sufficiency["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "constraint trace matrix is incomplete: " + "; ".join(constraint_trace_sufficiency["blockers"])})
    if assumption_sufficiency["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "assumption register is incomplete: " + "; ".join(assumption_sufficiency["blockers"])})
    if worker_output_contract_sufficiency["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "worker output contract is incomplete: " + "; ".join(worker_output_contract_sufficiency["blockers"])})
    if planning_department_sufficiency["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "planning department handoff is incomplete: " + "; ".join(planning_department_sufficiency["blockers"])})
    if pre_mutation_read_sufficiency["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "pre-mutation read evidence is incomplete: " + "; ".join(pre_mutation_read_sufficiency["blockers"])})
    if source_mutation_scope_sufficiency["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "source mutation scope is incomplete: " + "; ".join(source_mutation_scope_sufficiency["blockers"])})
    if verification_after_mutation["status"] == "blocked":
        findings.append({"severity": "blocker", "finding": "verification-after-mutation evidence is incomplete: " + "; ".join(verification_after_mutation["blockers"])})
    if worker_report["dry_run"] and package_status_counts["planned"]:
        warnings.append({"severity": "warning", "finding": "work packages are planned but not implemented"})
    if negative_tests and verification_report["status"] not in {"planned_only", "requires_execution", "passed"}:
        findings.append({"severity": "blocker", "finding": "negative tests are missing or not planned"})
    if verification_report.get("status") in {"failed", "blocked"}:
        findings.append({"severity": "blocker", "finding": f"verification report status is {verification_report.get('status')}"})
    for problem in output_consistency_findings(verification_report, output_summary):
        findings.append({"severity": "blocker", "finding": problem})
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
        "investigation_sufficiency": investigation_sufficiency,
        "change_control_sufficiency": change_control_sufficiency,
        "acceptance_trace_sufficiency": acceptance_trace_sufficiency,
        "constraint_trace_sufficiency": constraint_trace_sufficiency,
        "assumption_sufficiency": assumption_sufficiency,
        "worker_output_contract_sufficiency": worker_output_contract_sufficiency,
        "planning_department_sufficiency": planning_department_sufficiency,
        "pre_mutation_read_sufficiency": pre_mutation_read_sufficiency,
        "source_mutation_scope_sufficiency": source_mutation_scope_sufficiency,
        "verification_after_mutation_sufficiency": verification_after_mutation,
        "diagnostic_repair_queue": diagnostic_repair_queue,
        "checked_against": [
            "planning packet completeness",
            "strategy approval",
            "scope control",
            "verification strategy",
            "surface verification coverage",
            "surface package ownership",
            "work package status coverage",
            "investigation playbook coverage",
            "change control plan coverage",
            "acceptance traceability coverage",
            "constraint traceability coverage",
            "assumption register coverage",
            "worker output contract coverage",
            "planning department RFC and multi-pass handoff coverage",
            "pre-mutation read evidence",
            "source mutation scope",
            "verification after final mutation",
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
    engineering_memory = artifacts.get("engineering_memory_update", {}) if isinstance(artifacts.get("engineering_memory_update"), dict) else {}
    planning_department = artifacts.get("planning_department", {}) if isinstance(artifacts.get("planning_department"), dict) else {}
    planning_department_rfc = planning_department.get("engineering_rfc") if isinstance(planning_department.get("engineering_rfc"), dict) else {}
    planning_department_investigation = planning_department.get("multi_pass_repo_investigation") if isinstance(planning_department.get("multi_pass_repo_investigation"), dict) else {}
    planning_department_handoff = planning_department.get("code_brigade_work_package_handoff") if isinstance(planning_department.get("code_brigade_work_package_handoff"), dict) else {}
    execution_result = worker_report.get("execution_result") if isinstance(worker_report.get("execution_result"), dict) else {}
    preflight = execution_result.get("preflight") if isinstance(execution_result.get("preflight"), dict) else {}
    autonomous_request = worker_report.get("autonomous_execution_request") if isinstance(worker_report.get("autonomous_execution_request"), dict) else {}
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
    forecast = brief.get("execution_forecast") if isinstance(brief.get("execution_forecast"), dict) else {}
    scope_budget = forecast.get("scope_budget") if isinstance(forecast.get("scope_budget"), dict) else {}
    investigation_sufficiency = review.get("investigation_sufficiency") if isinstance(review.get("investigation_sufficiency"), dict) else {}
    change_control_sufficiency = review.get("change_control_sufficiency") if isinstance(review.get("change_control_sufficiency"), dict) else {}
    acceptance_trace_sufficiency = review.get("acceptance_trace_sufficiency") if isinstance(review.get("acceptance_trace_sufficiency"), dict) else {}
    constraint_trace_sufficiency = review.get("constraint_trace_sufficiency") if isinstance(review.get("constraint_trace_sufficiency"), dict) else {}
    assumption_sufficiency = review.get("assumption_sufficiency") if isinstance(review.get("assumption_sufficiency"), dict) else {}
    worker_output_contract_sufficiency = review.get("worker_output_contract_sufficiency") if isinstance(review.get("worker_output_contract_sufficiency"), dict) else {}
    planning_department_sufficiency = review.get("planning_department_sufficiency") if isinstance(review.get("planning_department_sufficiency"), dict) else {}
    work_phases = work_breakdown.get("phases") if isinstance(work_breakdown.get("phases"), list) else []
    blockers = readiness.get("blockers", [])
    warnings = review.get("warnings", [])
    commands_executed = verification.get("commands_executed", [])
    commands_planned = verification.get("commands_planned", [])
    output_summary = verification.get("output_summary", []) if isinstance(verification.get("output_summary"), list) else []
    output_signal_counts = output_signal_counts_from_summary(output_summary)
    output_diagnostic_counts = output_diagnostic_counts_from_summary(output_summary)
    repair_queue = review.get("diagnostic_repair_queue") if isinstance(review.get("diagnostic_repair_queue"), dict) else {}
    repo_evidence = brief.get("repo_survey_evidence", {}) if isinstance(brief.get("repo_survey_evidence"), dict) else {}
    work_packages = brief.get("implementation_work_packages", {}) if isinstance(brief.get("implementation_work_packages"), dict) else {}
    packages = work_packages.get("packages") if isinstance(work_packages.get("packages"), list) else []
    package_graph = work_packages.get("package_dependency_graph") if isinstance(work_packages.get("package_dependency_graph"), dict) else {}
    package_graph_rows = package_graph.get("rows") if isinstance(package_graph.get("rows"), list) else []
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
        f"Planning department status: {planning_department.get('status', '')}",
        f"Engineering RFC status: {planning_department_rfc.get('status', '')}",
        f"Multi-pass investigation status: {planning_department_investigation.get('status', '')}",
        f"Multi-pass investigation phases: {len(planning_department_investigation.get('phases', [])) if isinstance(planning_department_investigation.get('phases'), list) else 0}",
        f"CodeBrigade package handoff: {planning_department_handoff.get('status', '')}",
        f"Planning department review status: {planning_department_sufficiency.get('status', '')}",
        f"Planning work phases: {len(work_phases)}",
        f"Implementation work packages: {len(packages)}",
        f"Work package covered surfaces: {len(covered_package_surfaces)}",
        f"Work package statuses: planned={package_status_counts['planned']} implemented={package_status_counts['implemented']} blocked={package_status_counts['blocked']}",
        f"Work package dependency graph complete: {str(package_graph.get('complete') is True).lower()}",
        f"Work package dependency rows: {len(package_graph_rows)}",
        f"Work package dependency roots: {len(package_graph.get('root_packages', [])) if isinstance(package_graph.get('root_packages'), list) else 0}",
        f"Work package dependency terminals: {len(package_graph.get('terminal_packages', [])) if isinstance(package_graph.get('terminal_packages'), list) else 0}",
        f"Investigation playbook status: {investigation_sufficiency.get('status', '')}",
        f"Investigation read stages: {investigation_sufficiency.get('read_stage_count', 0)}",
        f"Change control status: {change_control_sufficiency.get('status', '')}",
        f"Protected invariants: {change_control_sufficiency.get('protected_invariant_count', 0)}",
        f"Acceptance trace status: {acceptance_trace_sufficiency.get('status', '')}",
        f"Acceptance trace rows: {acceptance_trace_sufficiency.get('row_count', 0)}",
        f"Definition-of-done trace complete: {str(acceptance_trace_sufficiency.get('definition_of_done_complete') is True).lower()}",
        f"Definition-of-done traced: {acceptance_trace_sufficiency.get('traced_definition_of_done_count', 0)}/{acceptance_trace_sufficiency.get('definition_of_done_count', 0)}",
        f"Constraint trace status: {constraint_trace_sufficiency.get('status', '')}",
        f"Constraint trace rows: {constraint_trace_sufficiency.get('row_count', 0)}",
        f"Assumption register status: {assumption_sufficiency.get('status', '')}",
        f"Assumptions tracked: {assumption_sufficiency.get('assumption_count', 0)}",
        f"Survey quality decision: {survey_quality.get('decision', '')}",
        f"Verification status: {verification['status']}",
        f"Surface verification status: {review.get('surface_verification_sufficiency', {}).get('status', '')}",
        f"Verification commands planned: {len(commands_planned)}",
        f"Verification commands executed: {len(commands_executed)}",
        f"Verification output summary rows: {len(output_summary)}",
        f"Verification output signals: {json.dumps(output_signal_counts, ensure_ascii=False, sort_keys=True)}",
        f"Verification output diagnostics: {json.dumps(output_diagnostic_counts, ensure_ascii=False, sort_keys=True)}",
        f"Diagnostic repair queue: {repair_queue.get('status', 'empty')} ({repair_queue.get('item_count', 0)} item(s))",
        f"Worker status: {worker_report.get('status', '')}",
        f"Execution policy status: {worker_report.get('execution_policy_status', '')}",
        f"Execution result status: {execution_result.get('status', '')}",
        f"Execution preflight ok: {preflight.get('ok') if preflight else 'n/a'}",
        f"Autonomous execution request: {autonomous_request.get('status', '')}",
        f"Execution intent: {execution_intent.get('mode', '')}",
        f"Execution adapter capability: {execution_intent.get('adapter_capability', '')}",
        f"Scope budget source files: {scope_budget.get('max_source_files_to_edit', 0)}",
        f"Scope budget unrequested test edits: {scope_budget.get('max_test_files_to_edit_without_explicit_user_request', 0)}",
        f"Engineering memory status: {engineering_memory.get('status', '')}",
        f"Engineering memory reusable patterns: {len(engineering_memory.get('reusable_patterns', [])) if isinstance(engineering_memory.get('reusable_patterns'), list) else 0}",
        f"Engineering memory false-success guards: {len(engineering_memory.get('false_success_guards', [])) if isinstance(engineering_memory.get('false_success_guards'), list) else 0}",
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
            f"- reverse dependency targets: {len(repo_evidence.get('reverse_dependency_index', {})) if isinstance(repo_evidence.get('reverse_dependency_index'), dict) else 0}",
            f"- test coverage links: {len(repo_evidence.get('test_coverage_links', [])) if isinstance(repo_evidence.get('test_coverage_links'), list) else 0}",
            f"- caller candidate rows: {len(repo_evidence.get('caller_candidates', [])) if isinstance(repo_evidence.get('caller_candidates'), list) else 0}",
            f"- contract surface candidates: {len(repo_evidence.get('contract_surface_candidates', [])) if isinstance(repo_evidence.get('contract_surface_candidates'), list) else 0}",
            f"- package manifest candidates: {len(repo_evidence.get('package_manifest_candidates', [])) if isinstance(repo_evidence.get('package_manifest_candidates'), list) else 0}",
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
            "- planning_department.json",
            "- implementation_brief.json",
            "- worker_report.json",
            "- verification_report.json",
            "- review_gate.json",
            "- diagnostic_repair_request.json",
            "- planning_feedback_request.json",
            "- status.json",
            "- final_report.md",
            "- execution_readiness.json",
            "- run_summary.json",
            "- evidence_matrix.json",
            "- engineering_memory_update.json",
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
        planning_feedback_request = json.loads((run_dir / "planning_feedback_request.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append({"severity": "blocker", "finding": f"planning_feedback_request.json is unreadable: {exc}"})
        planning_feedback_request = {}
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
    if summary.get("surface_verification_status_counts", {}) != surface_sufficiency.get("status_counts", {}):
        findings.append({"severity": "blocker", "finding": "run_summary surface_verification_status_counts disagrees with review_gate.json"})
    if summary.get("surface_verification_executed_count", 0) != surface_sufficiency.get("executed_surface_count", 0):
        findings.append({"severity": "blocker", "finding": "run_summary surface_verification_executed_count disagrees with review_gate.json"})
    if summary.get("surface_verification_partial_count", 0) != surface_sufficiency.get("partial_surface_count", 0):
        findings.append({"severity": "blocker", "finding": "run_summary surface_verification_partial_count disagrees with review_gate.json"})
    verification_sufficiency = review.get("verification_sufficiency") if isinstance(review.get("verification_sufficiency"), dict) else {}
    if summary.get("verification_output_summary_count", 0) != verification_sufficiency.get("output_summary_count", 0):
        findings.append({"severity": "blocker", "finding": "run_summary verification_output_summary_count disagrees with review_gate.json"})
    if summary.get("verification_output_signal_counts", {}) != verification_sufficiency.get("output_signal_counts", {}):
        findings.append({"severity": "blocker", "finding": "run_summary verification_output_signal_counts disagrees with review_gate.json"})
    if summary.get("verification_output_diagnostic_counts", {}) != verification_sufficiency.get("output_diagnostic_counts", {}):
        findings.append({"severity": "blocker", "finding": "run_summary verification_output_diagnostic_counts disagrees with review_gate.json"})
    repair_queue = review.get("diagnostic_repair_queue") if isinstance(review.get("diagnostic_repair_queue"), dict) else {}
    if summary.get("diagnostic_repair_queue_status", "empty") != repair_queue.get("status", "empty"):
        findings.append({"severity": "blocker", "finding": "run_summary diagnostic_repair_queue_status disagrees with review_gate.json"})
    if summary.get("diagnostic_repair_queue_item_count", 0) != repair_queue.get("item_count", 0):
        findings.append({"severity": "blocker", "finding": "run_summary diagnostic_repair_queue_item_count disagrees with review_gate.json"})
    investigation_sufficiency = review.get("investigation_sufficiency") if isinstance(review.get("investigation_sufficiency"), dict) else {}
    if summary.get("investigation_playbook_status", "") != investigation_sufficiency.get("status", ""):
        findings.append({"severity": "blocker", "finding": "run_summary investigation_playbook_status disagrees with review_gate.json"})
    if summary.get("investigation_read_stage_count", 0) != investigation_sufficiency.get("read_stage_count", 0):
        findings.append({"severity": "blocker", "finding": "run_summary investigation_read_stage_count disagrees with review_gate.json"})
    change_control_sufficiency = review.get("change_control_sufficiency") if isinstance(review.get("change_control_sufficiency"), dict) else {}
    if summary.get("change_control_status", "") != change_control_sufficiency.get("status", ""):
        findings.append({"severity": "blocker", "finding": "run_summary change_control_status disagrees with review_gate.json"})
    if summary.get("change_control_protected_invariant_count", 0) != change_control_sufficiency.get("protected_invariant_count", 0):
        findings.append({"severity": "blocker", "finding": "run_summary change_control_protected_invariant_count disagrees with review_gate.json"})
    acceptance_trace_sufficiency = review.get("acceptance_trace_sufficiency") if isinstance(review.get("acceptance_trace_sufficiency"), dict) else {}
    if summary.get("acceptance_trace_status", "") != acceptance_trace_sufficiency.get("status", ""):
        findings.append({"severity": "blocker", "finding": "run_summary acceptance_trace_status disagrees with review_gate.json"})
    if summary.get("acceptance_trace_row_count", 0) != acceptance_trace_sufficiency.get("row_count", 0):
        findings.append({"severity": "blocker", "finding": "run_summary acceptance_trace_row_count disagrees with review_gate.json"})
    if summary.get("definition_of_done_trace_complete") != acceptance_trace_sufficiency.get("definition_of_done_complete"):
        findings.append({"severity": "blocker", "finding": "run_summary definition_of_done_trace_complete disagrees with review_gate.json"})
    if summary.get("definition_of_done_count", 0) != acceptance_trace_sufficiency.get("definition_of_done_count", 0):
        findings.append({"severity": "blocker", "finding": "run_summary definition_of_done_count disagrees with review_gate.json"})
    if summary.get("traced_definition_of_done_count", 0) != acceptance_trace_sufficiency.get("traced_definition_of_done_count", 0):
        findings.append({"severity": "blocker", "finding": "run_summary traced_definition_of_done_count disagrees with review_gate.json"})
    constraint_trace_sufficiency = review.get("constraint_trace_sufficiency") if isinstance(review.get("constraint_trace_sufficiency"), dict) else {}
    if summary.get("constraint_trace_status", "") != constraint_trace_sufficiency.get("status", ""):
        findings.append({"severity": "blocker", "finding": "run_summary constraint_trace_status disagrees with review_gate.json"})
    if summary.get("constraint_trace_row_count", 0) != constraint_trace_sufficiency.get("row_count", 0):
        findings.append({"severity": "blocker", "finding": "run_summary constraint_trace_row_count disagrees with review_gate.json"})
    assumption_sufficiency = review.get("assumption_sufficiency") if isinstance(review.get("assumption_sufficiency"), dict) else {}
    if summary.get("assumption_register_status", "") != assumption_sufficiency.get("status", ""):
        findings.append({"severity": "blocker", "finding": "run_summary assumption_register_status disagrees with review_gate.json"})
    if summary.get("assumption_count", 0) != assumption_sufficiency.get("assumption_count", 0):
        findings.append({"severity": "blocker", "finding": "run_summary assumption_count disagrees with review_gate.json"})
    worker_output_contract_sufficiency = review.get("worker_output_contract_sufficiency") if isinstance(review.get("worker_output_contract_sufficiency"), dict) else {}
    if summary.get("worker_output_contract_status", "") != worker_output_contract_sufficiency.get("status", ""):
        findings.append({"severity": "blocker", "finding": "run_summary worker_output_contract_status disagrees with review_gate.json"})
    if summary.get("worker_output_required_package_count", 0) != worker_output_contract_sufficiency.get("required_package_count", 0):
        findings.append({"severity": "blocker", "finding": "run_summary worker_output_required_package_count disagrees with review_gate.json"})
    if summary.get("worker_output_reported_package_count", 0) != worker_output_contract_sufficiency.get("reported_package_count", 0):
        findings.append({"severity": "blocker", "finding": "run_summary worker_output_reported_package_count disagrees with review_gate.json"})
    if summary.get("worker_output_contract_row_count", 0) != worker_output_contract_sufficiency.get("contract_row_count", 0):
        findings.append({"severity": "blocker", "finding": "run_summary worker_output_contract_row_count disagrees with review_gate.json"})
    if summary.get("worker_output_acceptance_requirement_row_count", 0) != worker_output_contract_sufficiency.get("rows_with_acceptance_requirements", 0):
        findings.append({"severity": "blocker", "finding": "run_summary worker_output_acceptance_requirement_row_count disagrees with review_gate.json"})
    if planning_feedback_request.get("kind") != "ceraxia_planning_feedback_request":
        findings.append({"severity": "blocker", "finding": "planning_feedback_request.json has invalid kind"})
    if planning_feedback_request.get("target") != "PlanningBrigade":
        findings.append({"severity": "blocker", "finding": "planning_feedback_request.json has invalid target"})
    if planning_feedback_request.get("status") not in {"required", "not_required"}:
        findings.append({"severity": "blocker", "finding": "planning_feedback_request.json has invalid status"})
    planning_feedback_findings = planning_feedback_request.get("feedback_findings") if isinstance(planning_feedback_request.get("feedback_findings"), list) else []
    expected_feedback_status = "required" if planning_feedback_findings else "not_required"
    if planning_feedback_request.get("status") != expected_feedback_status:
        findings.append({"severity": "blocker", "finding": "planning_feedback_request status disagrees with feedback_findings"})
    if summary.get("planning_feedback_request_status", "") != planning_feedback_request.get("status", ""):
        findings.append({"severity": "blocker", "finding": "run_summary planning_feedback_request_status disagrees with planning_feedback_request.json"})
    if summary.get("planning_feedback_finding_count", 0) != len(planning_feedback_findings):
        findings.append({"severity": "blocker", "finding": "run_summary planning_feedback_finding_count disagrees with planning_feedback_request.json"})
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
    autonomous_request = worker_report.get("autonomous_execution_request") if isinstance(worker_report.get("autonomous_execution_request"), dict) else {}
    if summary.get("code_brigade_autonomous_execution_request_status", "") != autonomous_request.get("status", ""):
        findings.append({"severity": "blocker", "finding": "run_summary code_brigade_autonomous_execution_request_status disagrees with worker_report.json"})
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
    package_graph = work_packages.get("package_dependency_graph") if isinstance(work_packages.get("package_dependency_graph"), dict) else {}
    package_graph_rows = package_graph.get("rows") if isinstance(package_graph.get("rows"), list) else []
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
    if summary.get("implementation_work_package_dependency_graph_complete") != (package_graph.get("complete") is True):
        findings.append({"severity": "blocker", "finding": "run_summary implementation_work_package_dependency_graph_complete disagrees with implementation_brief.json"})
    if summary.get("implementation_work_package_dependency_row_count", 0) != len(package_graph_rows):
        findings.append({"severity": "blocker", "finding": "run_summary implementation_work_package_dependency_row_count disagrees with implementation_brief.json"})
    if summary.get("implementation_work_package_dependency_root_count", 0) != len(package_graph.get("root_packages", []) if isinstance(package_graph.get("root_packages"), list) else []):
        findings.append({"severity": "blocker", "finding": "run_summary implementation_work_package_dependency_root_count disagrees with implementation_brief.json"})
    if summary.get("implementation_work_package_dependency_terminal_count", 0) != len(package_graph.get("terminal_packages", []) if isinstance(package_graph.get("terminal_packages"), list) else []):
        findings.append({"severity": "blocker", "finding": "run_summary implementation_work_package_dependency_terminal_count disagrees with implementation_brief.json"})
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
    plan_sources = evidence_matrix.get("implementation_plan_sources") if isinstance(evidence_matrix.get("implementation_plan_sources"), dict) else {}
    investigation_read_stages = plan_sources.get("investigation_read_stages") if isinstance(plan_sources.get("investigation_read_stages"), list) else []
    investigation_questions = plan_sources.get("investigation_evidence_questions") if isinstance(plan_sources.get("investigation_evidence_questions"), list) else []
    investigation_blockers = plan_sources.get("investigation_mutation_blockers") if isinstance(plan_sources.get("investigation_mutation_blockers"), list) else []
    investigation_replan = plan_sources.get("investigation_replan_triggers") if isinstance(plan_sources.get("investigation_replan_triggers"), list) else []
    if summary.get("investigation_read_stage_count", 0) != len(investigation_read_stages):
        findings.append({"severity": "blocker", "finding": "run_summary investigation_read_stage_count disagrees with evidence_matrix.json"})
    if summary.get("investigation_evidence_question_count", 0) != len(investigation_questions):
        findings.append({"severity": "blocker", "finding": "run_summary investigation_evidence_question_count disagrees with evidence_matrix.json"})
    if summary.get("investigation_mutation_blocker_count", 0) != len(investigation_blockers):
        findings.append({"severity": "blocker", "finding": "run_summary investigation_mutation_blocker_count disagrees with evidence_matrix.json"})
    if summary.get("investigation_replan_trigger_count", 0) != len(investigation_replan):
        findings.append({"severity": "blocker", "finding": "run_summary investigation_replan_trigger_count disagrees with evidence_matrix.json"})
    change_invariants = plan_sources.get("change_protected_invariants") if isinstance(plan_sources.get("change_protected_invariants"), list) else []
    change_post_proofs = plan_sources.get("change_post_change_proofs") if isinstance(plan_sources.get("change_post_change_proofs"), list) else []
    if summary.get("change_control_protected_invariant_count", 0) != len(change_invariants):
        findings.append({"severity": "blocker", "finding": "run_summary change_control_protected_invariant_count disagrees with evidence_matrix.json"})
    if summary.get("change_control_post_change_proof_count", 0) != len(change_post_proofs):
        findings.append({"severity": "blocker", "finding": "run_summary change_control_post_change_proof_count disagrees with evidence_matrix.json"})
    acceptance_trace_rows = plan_sources.get("acceptance_trace_rows") if isinstance(plan_sources.get("acceptance_trace_rows"), list) else []
    if summary.get("acceptance_trace_row_count", 0) != len(acceptance_trace_rows):
        findings.append({"severity": "blocker", "finding": "run_summary acceptance_trace_row_count disagrees with evidence_matrix.json"})
    if summary.get("definition_of_done_trace_complete") != plan_sources.get("definition_of_done_trace_complete"):
        findings.append({"severity": "blocker", "finding": "run_summary definition_of_done_trace_complete disagrees with evidence_matrix.json"})
    if summary.get("definition_of_done_count", 0) != plan_sources.get("definition_of_done_count", 0):
        findings.append({"severity": "blocker", "finding": "run_summary definition_of_done_count disagrees with evidence_matrix.json"})
    if summary.get("traced_definition_of_done_count", 0) != plan_sources.get("traced_definition_of_done_count", 0):
        findings.append({"severity": "blocker", "finding": "run_summary traced_definition_of_done_count disagrees with evidence_matrix.json"})
    constraint_trace_rows = plan_sources.get("constraint_trace_rows") if isinstance(plan_sources.get("constraint_trace_rows"), list) else []
    if summary.get("constraint_trace_row_count", 0) != len(constraint_trace_rows):
        findings.append({"severity": "blocker", "finding": "run_summary constraint_trace_row_count disagrees with evidence_matrix.json"})
    assumption_rows = plan_sources.get("assumption_rows") if isinstance(plan_sources.get("assumption_rows"), list) else []
    if summary.get("assumption_count", 0) != len(assumption_rows):
        findings.append({"severity": "blocker", "finding": "run_summary assumption_count disagrees with evidence_matrix.json"})
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
    worker_report: dict[str, Any],
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
    repair_queue = review.get("diagnostic_repair_queue") if isinstance(review.get("diagnostic_repair_queue"), dict) else {}
    if repair_queue.get("item_count", 0):
        blockers.append("diagnostic repair request must be handled before execution readiness")
    if worker_report.get("status") == "review_only_ready":
        blockers.append("review_only requested; source execution was intentionally skipped")
    elif dry_run:
        blockers.append("dry run requested; real CodeBrigade execution was intentionally skipped")
    intent = worker_report.get("execution_intent") if isinstance(worker_report.get("execution_intent"), dict) else {}
    if not dry_run and intent.get("real_execution_supported") is False:
        blockers.append("real CodeBrigade execution requires autonomous unshaped source-edit adapter")
    return {
        "kind": "ceraxia_execution_readiness",
        "contract_version": CONTRACT_VERSION,
        "decision": "blocked" if blockers else "ready_for_real_execution",
        "dry_run": dry_run,
        "ready_conditions": ready_conditions,
        "blockers": blockers,
        "next_capability_to_wire": "CodeBrigade diagnostic repair adapter"
        if repair_queue.get("item_count", 0)
        else ("CodeBrigade real execution adapter" if dry_run and worker_report.get("status") != "review_only_ready" else ""),
    }


def build_final_next_action(status: dict[str, Any], worker_report: dict[str, Any], dry_run: bool) -> str:
    if status.get("state") != "finalized":
        return "inspect blockers"
    if worker_report.get("status") == "review_only_ready":
        return "inspect review package or rerun with guarded_patch/repo_engineer when mutation is intended"
    if dry_run:
        return "execute with CodeBrigade only when mutation is explicitly intended"
    execution_result = worker_report.get("execution_result") if isinstance(worker_report.get("execution_result"), dict) else {}
    if worker_report.get("status") == "implemented" and execution_result.get("status") == "implemented":
        return "expand CodeBrigade from guarded execution toward diagnostic autonomous source edits"
    return "inspect CodeBrigade execution blockers"


def build_maturity_label(worker_report: dict[str, Any], readiness: dict[str, Any]) -> str:
    intent = worker_report.get("execution_intent") if isinstance(worker_report.get("execution_intent"), dict) else {}
    edit_plan = worker_report.get("edit_plan") if isinstance(worker_report.get("edit_plan"), dict) else {}
    if readiness.get("decision") == "ready_for_real_execution" and worker_report.get("status") == "implemented":
        if edit_plan.get("controller_execution_mode") == "repo_engineer":
            return "repo_engineer_controller_with_guarded_code_brigade_execution"
        if intent.get("mode") == "guarded_inferred_patch_execution":
            return "guarded_inferred_patch_execution_controller"
        return "explicit_patch_execution_controller"
    if worker_report.get("status") == "dry_run_handoff_ready":
        return "dry_run_controller_with_code_brigade_handoff_adapter"
    if worker_report.get("status") == "review_only_ready":
        return "review_only_controller_without_code_brigade_execution"
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
    engineering_memory: dict[str, Any] | None = None,
    planning_feedback_request: dict[str, Any] | None = None,
    package_audit_decision: str = "pending_until_run_audit",
) -> dict[str, Any]:
    execution_result = worker_report.get("execution_result") if isinstance(worker_report.get("execution_result"), dict) else {}
    preflight = execution_result.get("preflight") if isinstance(execution_result.get("preflight"), dict) else {}
    execution_intent = worker_report.get("execution_intent") if isinstance(worker_report.get("execution_intent"), dict) else {}
    autonomous_request = worker_report.get("autonomous_execution_request") if isinstance(worker_report.get("autonomous_execution_request"), dict) else {}
    forecast = brief.get("execution_forecast") if isinstance(brief.get("execution_forecast"), dict) else {}
    scope_budget = forecast.get("scope_budget") if isinstance(forecast.get("scope_budget"), dict) else {}
    planning_review = brief.get("planning_review_gate") if isinstance(brief.get("planning_review_gate"), dict) else {}
    planning_department = brief.get("planning_department") if isinstance(brief.get("planning_department"), dict) else {}
    planning_department_rfc = planning_department.get("engineering_rfc") if isinstance(planning_department.get("engineering_rfc"), dict) else {}
    planning_department_investigation = planning_department.get("multi_pass_repo_investigation") if isinstance(planning_department.get("multi_pass_repo_investigation"), dict) else {}
    planning_department_handoff = planning_department.get("code_brigade_work_package_handoff") if isinstance(planning_department.get("code_brigade_work_package_handoff"), dict) else {}
    survey_quality = brief.get("survey_quality_gate") if isinstance(brief.get("survey_quality_gate"), dict) else {}
    work_breakdown = brief.get("work_breakdown") if isinstance(brief.get("work_breakdown"), dict) else {}
    work_phases = work_breakdown.get("phases") if isinstance(work_breakdown.get("phases"), list) else []
    verification_sufficiency = review.get("verification_sufficiency") if isinstance(review.get("verification_sufficiency"), dict) else {}
    surface_sufficiency = review.get("surface_verification_sufficiency") if isinstance(review.get("surface_verification_sufficiency"), dict) else {}
    repair_queue = review.get("diagnostic_repair_queue") if isinstance(review.get("diagnostic_repair_queue"), dict) else {}
    investigation_sufficiency = review.get("investigation_sufficiency") if isinstance(review.get("investigation_sufficiency"), dict) else {}
    change_control_sufficiency = review.get("change_control_sufficiency") if isinstance(review.get("change_control_sufficiency"), dict) else {}
    acceptance_trace_sufficiency = review.get("acceptance_trace_sufficiency") if isinstance(review.get("acceptance_trace_sufficiency"), dict) else {}
    constraint_trace_sufficiency = review.get("constraint_trace_sufficiency") if isinstance(review.get("constraint_trace_sufficiency"), dict) else {}
    assumption_sufficiency = review.get("assumption_sufficiency") if isinstance(review.get("assumption_sufficiency"), dict) else {}
    worker_output_contract_sufficiency = review.get("worker_output_contract_sufficiency") if isinstance(review.get("worker_output_contract_sufficiency"), dict) else {}
    planning_department_sufficiency = review.get("planning_department_sufficiency") if isinstance(review.get("planning_department_sufficiency"), dict) else {}
    planning_feedback = planning_feedback_request if isinstance(planning_feedback_request, dict) else {}
    memory = engineering_memory if isinstance(engineering_memory, dict) else {}
    planning_feedback_findings = planning_feedback.get("feedback_findings") if isinstance(planning_feedback.get("feedback_findings"), list) else []
    work_packages = brief.get("implementation_work_packages") if isinstance(brief.get("implementation_work_packages"), dict) else {}
    expert_plan = brief.get("expert_quality_plan") if isinstance(brief.get("expert_quality_plan"), dict) else {}
    packages = work_packages.get("packages") if isinstance(work_packages.get("packages"), list) else []
    package_graph = work_packages.get("package_dependency_graph") if isinstance(work_packages.get("package_dependency_graph"), dict) else {}
    package_graph_rows = package_graph.get("rows") if isinstance(package_graph.get("rows"), list) else []
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
        "planning_department_status": planning_department.get("status", ""),
        "engineering_rfc_status": planning_department_rfc.get("status", ""),
        "engineering_rfc_design_option_count": len(planning_department_rfc.get("design_options", [])) if isinstance(planning_department_rfc.get("design_options"), list) else 0,
        "multi_pass_investigation_status": planning_department_investigation.get("status", ""),
        "multi_pass_investigation_phase_count": len(planning_department_investigation.get("phases", [])) if isinstance(planning_department_investigation.get("phases"), list) else 0,
        "code_brigade_work_package_handoff_status": planning_department_handoff.get("status", ""),
        "planning_department_review_status": planning_department_sufficiency.get("status", ""),
        "planning_department_review_role_count": planning_department_sufficiency.get("role_count", 0),
        "planning_department_review_phase_count": planning_department_sufficiency.get("phase_count", 0),
        "planning_department_review_package_count": planning_department_sufficiency.get("package_count", 0),
        "planning_work_phase_count": len(work_phases),
        "implementation_work_package_count": len(packages),
        "implementation_work_package_surface_count": len(package_surfaces),
        "implementation_work_package_review_order": work_packages.get("review_order", []) if isinstance(work_packages.get("review_order"), list) else [],
        "implementation_work_package_dependency_graph_complete": package_graph.get("complete") is True,
        "implementation_work_package_dependency_row_count": len(package_graph_rows),
        "implementation_work_package_dependency_root_count": len(package_graph.get("root_packages", [])) if isinstance(package_graph.get("root_packages"), list) else 0,
        "implementation_work_package_dependency_terminal_count": len(package_graph.get("terminal_packages", [])) if isinstance(package_graph.get("terminal_packages"), list) else 0,
        "work_package_status_counts": package_status_counts,
        "survey_quality_decision": survey_quality.get("decision", ""),
        "survey_quality_warning_count": len(survey_quality.get("warnings", [])) if isinstance(survey_quality.get("warnings"), list) else 0,
        "surface_verification_status": surface_sufficiency.get("status", ""),
        "surface_verification_surface_count": surface_sufficiency.get("surface_count", 0),
        "surface_verification_status_counts": surface_sufficiency.get("status_counts", {}),
        "surface_verification_executed_count": surface_sufficiency.get("executed_surface_count", 0),
        "surface_verification_partial_count": surface_sufficiency.get("partial_surface_count", 0),
        "verification_output_summary_count": verification_sufficiency.get("output_summary_count", 0),
        "verification_output_signal_counts": verification_sufficiency.get("output_signal_counts", {}),
        "verification_output_diagnostic_counts": verification_sufficiency.get("output_diagnostic_counts", {}),
        "diagnostic_repair_queue_status": repair_queue.get("status", "empty"),
        "diagnostic_repair_queue_item_count": repair_queue.get("item_count", 0),
        "investigation_playbook_status": investigation_sufficiency.get("status", ""),
        "investigation_read_stage_count": investigation_sufficiency.get("read_stage_count", 0),
        "investigation_evidence_question_count": investigation_sufficiency.get("evidence_question_count", 0),
        "investigation_mutation_blocker_count": investigation_sufficiency.get("mutation_blocker_count", 0),
        "investigation_replan_trigger_count": investigation_sufficiency.get("replan_trigger_count", 0),
        "change_control_status": change_control_sufficiency.get("status", ""),
        "change_control_allowed_intent_count": change_control_sufficiency.get("allowed_intent_count", 0),
        "change_control_protected_invariant_count": change_control_sufficiency.get("protected_invariant_count", 0),
        "change_control_mutation_requirement_count": change_control_sufficiency.get("mutation_requirement_count", 0),
        "change_control_diff_review_question_count": change_control_sufficiency.get("diff_review_question_count", 0),
        "change_control_rollback_trigger_count": change_control_sufficiency.get("rollback_trigger_count", 0),
        "change_control_post_change_proof_count": change_control_sufficiency.get("post_change_proof_count", 0),
        "acceptance_trace_status": acceptance_trace_sufficiency.get("status", ""),
        "acceptance_trace_row_count": acceptance_trace_sufficiency.get("row_count", 0),
        "acceptance_trace_blocked_row_count": acceptance_trace_sufficiency.get("blocked_row_count", 0),
        "definition_of_done_trace_complete": acceptance_trace_sufficiency.get("definition_of_done_complete", False),
        "definition_of_done_count": acceptance_trace_sufficiency.get("definition_of_done_count", 0),
        "traced_definition_of_done_count": acceptance_trace_sufficiency.get("traced_definition_of_done_count", 0),
        "missing_definition_of_done_count": len(acceptance_trace_sufficiency.get("missing_definition_of_done", [])) if isinstance(acceptance_trace_sufficiency.get("missing_definition_of_done"), list) else 0,
        "constraint_trace_status": constraint_trace_sufficiency.get("status", ""),
        "constraint_trace_row_count": constraint_trace_sufficiency.get("row_count", 0),
        "constraint_trace_blocked_row_count": constraint_trace_sufficiency.get("blocked_row_count", 0),
        "assumption_register_status": assumption_sufficiency.get("status", ""),
        "assumption_count": assumption_sufficiency.get("assumption_count", 0),
        "assumption_replan_trigger_count": assumption_sufficiency.get("replan_trigger_count", 0),
        "worker_output_contract_status": worker_output_contract_sufficiency.get("status", ""),
        "worker_output_required_package_count": worker_output_contract_sufficiency.get("required_package_count", 0),
        "worker_output_reported_package_count": worker_output_contract_sufficiency.get("reported_package_count", 0),
        "worker_output_contract_row_count": worker_output_contract_sufficiency.get("contract_row_count", 0),
        "worker_output_acceptance_requirement_row_count": worker_output_contract_sufficiency.get("rows_with_acceptance_requirements", 0),
        "planning_feedback_request_status": planning_feedback.get("status", ""),
        "planning_feedback_finding_count": len(planning_feedback_findings),
        "execution_readiness": readiness.get("decision"),
        "worker_status": worker_report.get("status"),
        "code_brigade_execution_policy_status": worker_report.get("execution_policy_status"),
        "code_brigade_execution_intent_mode": execution_intent.get("mode", ""),
        "code_brigade_execution_real_supported": bool(execution_intent.get("real_execution_supported")),
        "code_brigade_autonomous_execution_request_status": autonomous_request.get("status", ""),
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
        "scope_budget_max_source_files_to_edit": scope_budget.get("max_source_files_to_edit", 0),
        "scope_budget_max_unrequested_test_files_to_edit": scope_budget.get("max_test_files_to_edit_without_explicit_user_request", 0),
        "scope_budget_replan_trigger_count": len(scope_budget.get("requires_ceraxia_replan_when", [])) if isinstance(scope_budget.get("requires_ceraxia_replan_when"), list) else 0,
        "engineering_memory_status": memory.get("status", ""),
        "engineering_memory_failure_pattern_count": len(memory.get("observed_failure_patterns", [])) if isinstance(memory.get("observed_failure_patterns"), list) else 0,
        "engineering_memory_reusable_pattern_count": len(memory.get("reusable_patterns", [])) if isinstance(memory.get("reusable_patterns"), list) else 0,
        "engineering_memory_false_success_guard_count": len(memory.get("false_success_guards", [])) if isinstance(memory.get("false_success_guards"), list) else 0,
        "engineering_memory_dangerous_module_count": len(memory.get("dangerous_modules", [])) if isinstance(memory.get("dangerous_modules"), list) else 0,
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
    autonomous_request = worker_report.get("autonomous_execution_request") if isinstance(worker_report.get("autonomous_execution_request"), dict) else {}
    work_packages = implementation_plan.get("implementation_work_packages") if isinstance(implementation_plan.get("implementation_work_packages"), list) else []
    work_package_review_order = implementation_plan.get("work_package_review_order") if isinstance(implementation_plan.get("work_package_review_order"), list) else []
    work_package_dependency_graph = implementation_plan.get("work_package_dependency_graph") if isinstance(implementation_plan.get("work_package_dependency_graph"), dict) else {}
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
            "investigation_read_stages": implementation_plan.get("investigation_read_stages", []),
            "investigation_evidence_questions": implementation_plan.get("investigation_evidence_questions", []),
            "investigation_mutation_blockers": implementation_plan.get("investigation_mutation_blockers", []),
            "investigation_replan_triggers": implementation_plan.get("investigation_replan_triggers", []),
            "change_allowed_intents": implementation_plan.get("change_allowed_intents", []),
            "change_protected_invariants": implementation_plan.get("change_protected_invariants", []),
            "change_mutation_requires": implementation_plan.get("change_mutation_requires", []),
            "change_diff_review_questions": implementation_plan.get("change_diff_review_questions", []),
            "change_rollback_triggers": implementation_plan.get("change_rollback_triggers", []),
            "change_post_change_proofs": implementation_plan.get("change_post_change_proofs", []),
            "acceptance_trace_rows": implementation_plan.get("acceptance_trace_rows", []),
            "acceptance_trace_complete": implementation_plan.get("acceptance_trace_complete", False),
            "definition_of_done_trace_complete": implementation_plan.get("definition_of_done_trace_complete", False),
            "definition_of_done_count": implementation_plan.get("definition_of_done_count", 0),
            "traced_definition_of_done_count": implementation_plan.get("traced_definition_of_done_count", 0),
            "missing_definition_of_done": implementation_plan.get("missing_definition_of_done", []),
            "constraint_trace_rows": implementation_plan.get("constraint_trace_rows", []),
            "constraint_trace_complete": implementation_plan.get("constraint_trace_complete", False),
            "assumption_rows": implementation_plan.get("assumption_rows", []),
            "assumption_replan_triggers": implementation_plan.get("assumption_replan_triggers", []),
            "verification_commands": implementation_plan.get("verification_commands", []),
            "scope_budget": implementation_plan.get("scope_budget", {}),
            "reverse_dependency_index": implementation_plan.get("reverse_dependency_index", {}),
            "test_coverage_links": implementation_plan.get("test_coverage_links", []),
            "caller_candidates": implementation_plan.get("caller_candidates", []),
            "contract_surface_candidates": implementation_plan.get("contract_surface_candidates", []),
            "package_manifest_candidates": implementation_plan.get("package_manifest_candidates", []),
        },
        "autonomous_execution_request": autonomous_request,
        "implementation_work_package_summary": {
            "package_count": len(work_packages),
            "review_order": work_package_review_order,
            "dependency_graph": work_package_dependency_graph,
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
