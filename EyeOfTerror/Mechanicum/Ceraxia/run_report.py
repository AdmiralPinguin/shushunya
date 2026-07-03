from __future__ import annotations

"""Final report, artifact manifest, audit, readiness, run summary, and evidence matrix."""

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

from brief_builder import validate_planning_packet
from verification_report import output_diagnostic_counts_from_summary, output_signal_counts_from_summary


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


def artifact_manifest_drift_findings(run_dir: Path, current_manifest: dict[str, Any]) -> list[dict[str, str]]:
    manifest_path = run_dir / "artifact_manifest.json"
    try:
        stored_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        return [{"severity": "blocker", "finding": f"artifact_manifest.json is unreadable: {exc}"}]
    findings: list[dict[str, str]] = []
    if stored_manifest.get("kind") != "ceraxia_run_artifact_manifest":
        findings.append({"severity": "blocker", "finding": "artifact_manifest.json has invalid kind"})
    if stored_manifest.get("required_artifacts") != current_manifest.get("required_artifacts"):
        findings.append({"severity": "blocker", "finding": "artifact_manifest.json required_artifacts drifted from runtime contract"})
    stored_entries = {
        str(entry.get("path") or ""): entry
        for entry in stored_manifest.get("entries", [])
        if isinstance(entry, dict) and entry.get("path")
    }
    current_entries = {
        str(entry.get("path") or ""): entry
        for entry in current_manifest.get("entries", [])
        if isinstance(entry, dict) and entry.get("path")
    }
    for path, current_entry in current_entries.items():
        stored_entry = stored_entries.get(path)
        if not stored_entry:
            findings.append({"severity": "blocker", "finding": f"artifact_manifest.json missing entry for {path}"})
            continue
        for key in ("exists", "size_bytes", "sha256"):
            if stored_entry.get(key) != current_entry.get(key):
                findings.append({"severity": "blocker", "finding": f"artifact_manifest.json {path} {key} disagrees with current artifact"})
    extra_entries = sorted(set(stored_entries) - set(current_entries))
    if extra_entries:
        findings.append({"severity": "blocker", "finding": "artifact_manifest.json has unknown entries: " + ", ".join(extra_entries[:8])})
    return findings


def audit_run_package(run_dir: Path) -> dict[str, Any]:
    manifest = build_artifact_manifest(run_dir)
    findings: list[dict[str, str]] = []
    if manifest["missing"]:
        findings.append({"severity": "blocker", "finding": f"missing artifacts: {', '.join(manifest['missing'])}"})
    findings.extend(artifact_manifest_drift_findings(run_dir, manifest))
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
        "diagnostic_repair_queue_requires_attempt_history": bool(repair_queue.get("requires_attempt_history")),
        "diagnostic_repair_queue_max_attempts": repair_queue.get("max_attempts_per_item", 0),
        "diagnostic_repair_queue_replan_trigger_count": repair_queue.get("replan_trigger_count", 0),
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
        "engineering_memory_reuse_plan_count": len(memory.get("reuse_plan", [])) if isinstance(memory.get("reuse_plan"), list) else 0,
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
            "repository_cartography": implementation_plan.get("repository_cartography", {}),
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
