#!/usr/bin/env python3
from __future__ import annotations

from typing import Any


CONTRACT_VERSION = "eye-mechanicum.v1"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _strings(value: Any) -> list[str]:
    return [str(item) for item in _list(value) if str(item)]


def _first_strings(*values: Any) -> list[str]:
    for value in values:
        rows = _strings(value)
        if rows:
            return rows
    return []


def build_engineering_rfc(packet: dict[str, Any], survey: dict[str, Any], brief: dict[str, Any]) -> dict[str, Any]:
    design_options = _dict(packet.get("design_options"))
    selected = str(design_options.get("selected_strategy") or brief.get("selected_strategy") or "")
    risk_register = _dict(packet.get("risk_register"))
    expert_plan = _dict(brief.get("expert_quality_plan"))
    change_control = _dict(brief.get("change_control_plan"))
    impact = _dict(brief.get("impact_analysis"))
    verification = _dict(brief.get("required_verification"))
    alternatives = _list(design_options.get("alternatives"))
    if not alternatives and selected:
        alternatives = [
            {"name": selected, "decision": "selected", "reason": "selected by PlanningBrigade"},
            {"name": "minimal_patch", "decision": "rejected", "reason": "insufficient without impact and verification evidence"},
        ]
    return {
        "kind": "ceraxia_engineering_rfc",
        "contract_version": CONTRACT_VERSION,
        "status": "accepted_for_code_brigade_handoff" if not brief.get("blocked") else "blocked",
        "problem": _dict(packet.get("problem_statement")),
        "selected_option": selected,
        "design_options": alternatives,
        "tradeoffs": _list(expert_plan.get("tradeoff_register")),
        "risk_register": risk_register,
        "impact_map": {
            "surfaces": _list(impact.get("surfaces")),
            "highest_risk_surface": str(impact.get("highest_risk_surface") or ""),
            "requires_cross_surface_review": bool(impact.get("requires_cross_surface_review")),
            "candidate_files": _list(survey.get("candidate_files")),
            "test_files": _list(survey.get("test_files")),
            "contract_surfaces": _list(survey.get("contract_surface_candidates")),
        },
        "rollback_plan": {
            "rollback_strategy": _first_strings(expert_plan.get("rollback_strategy"), change_control.get("rollback_triggers")),
            "rollback_triggers": _strings(change_control.get("rollback_triggers")),
            "protected_invariants": _strings(change_control.get("protected_invariants")),
        },
        "test_strategy": {
            "targeted_commands": _strings(verification.get("targeted_commands")),
            "negative_tests": _strings(verification.get("negative_tests")),
            "broad_verification_required": bool(verification.get("broad_verification_required")),
            "post_change_proofs": _strings(change_control.get("post_change_proofs")),
        },
        "decision_blockers": _strings(brief.get("blockers")),
    }


def build_multi_pass_investigation(packet: dict[str, Any], survey: dict[str, Any], brief: dict[str, Any]) -> dict[str, Any]:
    triage = _dict(packet.get("task_triage"))
    risk_level = str(brief.get("risk_level") or triage.get("risk_level") or "high")
    complex_task = risk_level == "high"
    phases = [
        {
            "id": "project_map",
            "owner": "Repository Cartographer",
            "required_before_mutation": True,
            "evidence_sources": ["repo_survey.json:candidate_files", "repo_survey.json:entrypoint_candidates"],
            "evidence_count": len(_list(survey.get("candidate_files"))) + len(_list(survey.get("entrypoint_candidates"))),
        },
        {
            "id": "dependency_public_api_map",
            "owner": "Repository Cartographer",
            "required_before_mutation": True,
            "evidence_sources": [
                "repo_survey.json:local_import_edges",
                "repo_survey.json:generic_import_edges",
                "repo_survey.json:contract_surface_candidates",
            ],
            "evidence_count": len(_list(survey.get("local_import_edges")))
            + len(_list(survey.get("generic_import_edges")))
            + len(_list(survey.get("contract_surface_candidates"))),
        },
        {
            "id": "test_ci_manifest_map",
            "owner": "Verification Planner",
            "required_before_mutation": True,
            "evidence_sources": [
                "repo_survey.json:test_files",
                "repo_survey.json:test_coverage_links",
                "repo_survey.json:package_manifest_candidates",
            ],
            "evidence_count": len(_list(survey.get("test_files")))
            + len(_list(survey.get("test_coverage_links")))
            + len(_list(survey.get("package_manifest_candidates"))),
        },
        {
            "id": "targeted_pre_mutation_reads",
            "owner": "Implementation Planner",
            "required_before_mutation": True,
            "evidence_sources": ["repo_survey.json:recommended_read_order"],
            "evidence_count": len(_list(survey.get("recommended_read_order"))),
        },
    ]
    blockers: list[str] = []
    if complex_task:
        for phase in phases:
            if int(phase["evidence_count"]) <= 0:
                blockers.append(f"{phase['id']} lacks repository evidence")
    return {
        "kind": "ceraxia_multi_pass_repo_investigation",
        "contract_version": CONTRACT_VERSION,
        "status": "complete" if not blockers else "blocked",
        "complex_task": complex_task,
        "mutation_policy": "all phases must be complete before source mutation for medium/high risk work",
        "phases": phases,
        "blockers": blockers,
    }


def build_execution_batches(rows: list[Any]) -> dict[str, Any]:
    dependencies_by_package: dict[str, list[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        package_id = str(row.get("package_id") or "")
        if not package_id:
            continue
        dependencies_by_package[package_id] = [
            dependency
            for dependency in _strings(row.get("depends_on"))
            if dependency and dependency != package_id
        ]
    batches: list[list[str]] = []
    emitted: set[str] = set()
    remaining = set(dependencies_by_package)
    while remaining:
        ready = sorted(
            package_id
            for package_id in remaining
            if all(dependency in emitted or dependency not in dependencies_by_package for dependency in dependencies_by_package[package_id])
        )
        if not ready:
            break
        batches.append(ready)
        emitted.update(ready)
        remaining.difference_update(ready)
    return {
        "complete": not remaining,
        "batches": batches,
        "unresolved_packages": sorted(remaining),
    }


def build_work_package_handoff(brief: dict[str, Any]) -> dict[str, Any]:
    work_packages = _dict(brief.get("implementation_work_packages"))
    output_contract = _dict(brief.get("worker_output_contract"))
    packages = _list(work_packages.get("packages"))
    graph = _dict(work_packages.get("package_dependency_graph"))
    rows = _list(graph.get("rows"))
    contract_rows = {
        str(row.get("package_id") or ""): row
        for row in _list(output_contract.get("package_result_contract"))
        if isinstance(row, dict) and row.get("package_id")
    }
    dependencies_by_package = {
        str(row.get("package_id") or ""): _strings(row.get("depends_on"))
        for row in rows
        if isinstance(row, dict) and row.get("package_id")
    }
    execution_batches = build_execution_batches(rows)
    package_rows = []
    for package in packages:
        if not isinstance(package, dict):
            continue
        package_id = str(package.get("id") or "")
        contract_row = contract_rows.get(package_id, {})
        package_rows.append(
            {
                "id": package_id,
                "owner": "CodeBrigade",
                "depends_on": dependencies_by_package.get(package_id, _strings(package.get("depends_on"))),
                "impact_surfaces": _strings(package.get("impact_surfaces")),
                "acceptance_requirements": _first_strings(package.get("acceptance_requirements"), contract_row.get("acceptance_requirements")),
                "acceptance_evidence": _strings(contract_row.get("acceptance_evidence")),
                "blocking_policy": _strings(package.get("blocking_policy")),
            }
        )
    return {
        "kind": "ceraxia_code_brigade_work_package_handoff",
        "contract_version": CONTRACT_VERSION,
        "target": "CodeBrigade",
        "status": "ready" if package_rows and graph.get("complete") is True and execution_batches["complete"] else "blocked",
        "review_order": _strings(work_packages.get("review_order")),
        "dependency_graph": graph,
        "execution_batches": execution_batches,
        "packages": package_rows,
        "handoff_criteria": _strings(work_packages.get("global_handoff_criteria")),
    }


def build_brigade_handoff_contract(brief: dict[str, Any], handoff: dict[str, Any]) -> dict[str, Any]:
    packages = _list(handoff.get("packages"))
    package_ids = [str(package.get("id") or "") for package in packages if isinstance(package, dict) and package.get("id")]
    roles = [
        {
            "name": "Ceraxia",
            "authority": "own coding task strategy and decide whether execution evidence is acceptable",
            "inputs": ["planning_packet.json", "repo_survey.json"],
            "outputs": ["implementation_brief.json", "planning_department.json"],
            "may_mutate_source": False,
            "handoff_to": "PlanningBrigade",
            "acceptance_gate": "implementation brief has RFC, work packages, verification, rollback, and acceptance trace",
        },
        {
            "name": "PlanningBrigade",
            "authority": "turn task intent into dependency-aware execution contract",
            "inputs": ["task", "constraints", "repo_survey_request"],
            "outputs": ["planning_packet.json", "worker_output_contract", "code_brigade_handoff"],
            "may_mutate_source": False,
            "handoff_to": "RepoSurveyor",
            "acceptance_gate": "definition of done, risks, dependencies, and handoff contracts are complete",
        },
        {
            "name": "RepoSurveyor",
            "authority": "produce read-only repository evidence before any mutation",
            "inputs": ["repo_survey_request", "path_hints"],
            "outputs": ["repo_survey.json", "candidate files", "dependency map", "test coverage links"],
            "may_mutate_source": False,
            "handoff_to": "CodeBrigade",
            "acceptance_gate": "candidate files, tests, dependencies, and blockers are explicit",
        },
        {
            "name": "CodeBrigade",
            "authority": "apply only the scoped implementation packages",
            "inputs": ["implementation_brief.json", "planning_department.json", "repo_survey.json"],
            "outputs": ["worker_report.json", "changed_files", "pre_mutation_read_evidence"],
            "may_mutate_source": True,
            "handoff_to": "Verifier",
            "acceptance_gate": "changed files map to planned packages and pre-mutation reads are recorded",
        },
        {
            "name": "Verifier",
            "authority": "prove behavior with focused and broad verification evidence",
            "inputs": ["worker_report.json", "worker_output_contract", "verification commands"],
            "outputs": ["verification_report.json", "verification_contract_trace"],
            "may_mutate_source": False,
            "handoff_to": "Reviewer",
            "acceptance_gate": "syntax-only checks cannot satisfy behavior acceptance requirements",
        },
        {
            "name": "Reviewer",
            "authority": "block completion unless task, contract, and evidence agree",
            "inputs": ["worker_report.json", "verification_report.json", "review_gate.json"],
            "outputs": ["review_gate.json", "run_audit.json"],
            "may_mutate_source": False,
            "handoff_to": "RepairStrategist",
            "acceptance_gate": "no false success, missing evidence, or contract drift remains",
        },
        {
            "name": "RepairStrategist",
            "authority": "convert failed verification into a new hypothesis or PlanningBrigade replan",
            "inputs": ["diagnostic_repair_queue", "attempt_history", "verification_report.json"],
            "outputs": ["diagnostic_repair_request.json", "replan_packet"],
            "may_mutate_source": False,
            "handoff_to": "PlanningBrigade",
            "acceptance_gate": "repeat fixes are blocked without new evidence and preserved attempt history",
        },
    ]
    blockers: list[str] = []
    if not package_ids:
        blockers.append("handoff contract has no implementation package ids")
    if handoff.get("status") != "ready":
        blockers.append("handoff contract cannot activate because work package handoff is not ready")
    if any(not role["outputs"] or not role["inputs"] for role in roles):
        blockers.append("handoff role contract is missing inputs or outputs")
    if [role["name"] for role in roles][-1] != "RepairStrategist":
        blockers.append("repair strategist must close the role chain")
    return {
        "kind": "ceraxia_brigade_handoff_contract",
        "contract_version": CONTRACT_VERSION,
        "status": "ready" if not blockers else "blocked",
        "package_ids": package_ids,
        "roles": roles,
        "role_order": [role["name"] for role in roles],
        "required_execution_artifacts": [
            "planning_packet.json",
            "planning_department.json",
            "implementation_brief.json",
            "worker_report.json",
            "verification_report.json",
            "review_gate.json",
            "run_audit.json",
        ],
        "loop_policy": {
            "failure_route": "Reviewer -> RepairStrategist -> PlanningBrigade",
            "must_preserve": ["attempt_history", "blocked repair signatures", "verification output"],
            "must_change_before_retry": ["failure hypothesis", "source evidence", "acceptance proof"],
        },
        "blockers": blockers,
    }


def build_planning_department_package(packet: dict[str, Any], survey: dict[str, Any], brief: dict[str, Any]) -> dict[str, Any]:
    rfc = build_engineering_rfc(packet, survey, brief)
    investigation = build_multi_pass_investigation(packet, survey, brief)
    handoff = build_work_package_handoff(brief)
    brigade_handoff = build_brigade_handoff_contract(brief, handoff)
    blockers = [*rfc.get("decision_blockers", []), *investigation.get("blockers", [])]
    if handoff.get("status") != "ready":
        blockers.append("CodeBrigade work package handoff is incomplete")
    if brigade_handoff.get("status") != "ready":
        blockers.extend(_strings(brigade_handoff.get("blockers")))
    return {
        "kind": "ceraxia_planning_department_package",
        "contract_version": CONTRACT_VERSION,
        "owner": "Ceraxia",
        "status": "ready_for_code_brigade" if not blockers else "blocked",
        "roles": [
            {"name": "Principal Planner", "responsibility": "own RFC/ADR decision quality and tradeoffs"},
            {"name": "Repository Cartographer", "responsibility": "prove repo map, dependencies, public API, and call surfaces"},
            {"name": "Implementation Planner", "responsibility": "split work into dependency-aware CodeBrigade packages"},
            {"name": "Verification Planner", "responsibility": "bind tests, CI commands, negative checks, and post-change proofs"},
            {"name": "Risk Reviewer", "responsibility": "block weak evidence, broad rewrites, and mutation without rollback"},
        ],
        "engineering_rfc": rfc,
        "multi_pass_repo_investigation": investigation,
        "code_brigade_work_package_handoff": handoff,
        "brigade_handoff_contract": brigade_handoff,
        "blockers": blockers,
    }
