from __future__ import annotations

"""Post-mutation verification evidence and command-output summarization."""

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
    worker_output_contract = brief.get("worker_output_contract") if isinstance(brief.get("worker_output_contract"), dict) else {}
    package_rows = worker_output_contract.get("package_result_contract") if isinstance(worker_output_contract.get("package_result_contract"), list) else []
    acceptance_requirements = sorted(
        {
            str(requirement)
            for row in package_rows
            if isinstance(row, dict)
            for requirement in (row.get("acceptance_requirements") if isinstance(row.get("acceptance_requirements"), list) else [])
            if str(requirement)
        }
    )
    execution = run_verification_commands(executable_commands, brief.get("repo_path", ""), execute=execute_verification, acceptance_requirements=acceptance_requirements) if executable_commands and not blocked else {
        "kind": "code_brigade_verification_execution",
        "contract_version": CONTRACT_VERSION,
        "status": "blocked" if blocked else "passed",
        "execute": execute_verification,
        "repo_path": brief.get("repo_path", ""),
        "results": [],
        "blockers": brief.get("blockers", []) if blocked else [],
        "contract_trace": {
            "kind": "code_brigade_verification_contract_trace",
            "contract_version": CONTRACT_VERSION,
            "requirement_count": len(acceptance_requirements),
            "status": "incomplete" if acceptance_requirements else "proven",
            "status_counts": {},
            "focused_evidence_count": 0,
            "behavior_evidence_count": 0,
            "broad_evidence_count": 0,
            "blocking_requirement_count": len(acceptance_requirements),
            "rows": [],
        },
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
        "contract_trace": execution.get("contract_trace", {}),
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
