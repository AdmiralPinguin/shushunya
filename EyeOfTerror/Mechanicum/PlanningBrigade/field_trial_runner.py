#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from planning_brigade import CONTRACT_VERSION, build_planning_packet
from planning_packet_contract import validate_planning_packet


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[2]
SERVICE_CONTRACTS = json.loads((ROOT / "service_contracts.json").read_text(encoding="utf-8"))


def collect_active_ports() -> dict[int, str]:
    active_ports: dict[int, str] = {}
    registry = json.loads((REPO_ROOT / "EyeOfTerror" / "registry" / "ports.json").read_text(encoding="utf-8"))
    for section_name in ["eye_of_terror", "mechanicum"]:
        section = registry.get(section_name) if isinstance(registry.get(section_name), dict) else {}
        for port, metadata in section.items():
            name = metadata.get("name", "unknown") if isinstance(metadata, dict) else "unknown"
            active_ports[int(port)] = f"EyeOfTerror/registry/ports.json:{section_name}:{name}"

    worker_services = json.loads((REPO_ROOT / "Mechanicum" / "worker_services.json").read_text(encoding="utf-8"))
    for name, metadata in worker_services.items():
        if isinstance(metadata, dict) and isinstance(metadata.get("port"), int):
            active_ports[int(metadata["port"])] = f"Mechanicum/worker_services.json:{name}"
    return active_ports


def require_subset(expected: list[str], actual: list[str], label: str, trial_id: str) -> None:
    missing = [item for item in expected if item not in actual]
    if missing:
        raise AssertionError(f"{trial_id}: missing {label}: {missing}; actual={actual}")


def validate_service_contracts_against_packet(packet: dict[str, Any], trial_id: str) -> dict[str, Any]:
    if SERVICE_CONTRACTS.get("contract_version") != CONTRACT_VERSION:
        raise AssertionError(f"{trial_id}: service contract version drifted: {SERVICE_CONTRACTS}")
    port_policy = SERVICE_CONTRACTS.get("port_policy") if isinstance(SERVICE_CONTRACTS.get("port_policy"), dict) else {}
    if port_policy.get("active") is not False:
        raise AssertionError(f"{trial_id}: planning services must stay inactive until split gates pass: {SERVICE_CONTRACTS}")
    services = SERVICE_CONTRACTS.get("services") if isinstance(SERVICE_CONTRACTS.get("services"), list) else []
    ports = [int(service.get("port") or 0) for service in services if isinstance(service, dict)]
    if ports != list(range(7111, 7116)):
        raise AssertionError(f"{trial_id}: planning service ports must remain reserved 7111-7115: {SERVICE_CONTRACTS}")
    if len(set(ports)) != len(ports):
        raise AssertionError(f"{trial_id}: planning service ports must be unique: {SERVICE_CONTRACTS}")
    active_ports = collect_active_ports()
    port_collisions = {port: active_ports[port] for port in ports if port in active_ports}
    if port_collisions:
        raise AssertionError(f"{trial_id}: planning service ports collide with active registry ports: {port_collisions}")
    missing_outputs: dict[str, list[str]] = {}
    mutating_services: list[str] = []
    for service in services:
        if not isinstance(service, dict):
            raise AssertionError(f"{trial_id}: invalid planning service contract entry: {SERVICE_CONTRACTS}")
        service_name = str(service.get("name") or "")
        if service.get("may_mutate_source") is not False:
            mutating_services.append(service_name)
        missing = [
            output
            for output in service.get("output_artifacts", [])
            if isinstance(output, str) and output not in packet
        ]
        if missing:
            missing_outputs[service_name] = missing
    if mutating_services:
        raise AssertionError(f"{trial_id}: planning service contracts must stay read-only: {mutating_services}")
    if missing_outputs:
        raise AssertionError(f"{trial_id}: service outputs are absent from packet: {missing_outputs}; packet={packet}")
    split_gates = SERVICE_CONTRACTS.get("split_gates") if isinstance(SERVICE_CONTRACTS.get("split_gates"), list) else []
    if len(split_gates) < 5:
        raise AssertionError(f"{trial_id}: service split gates are too weak: {SERVICE_CONTRACTS}")
    return {
        "service_names": [str(service.get("name") or "") for service in services if isinstance(service, dict)],
        "service_ports": ports,
        "active_registry_port_count": len(active_ports),
        "service_port_collision_count": len(port_collisions),
        "service_split_gate_count": len(split_gates),
        "service_contract_active": port_policy.get("active"),
    }


def run_trial(trial: dict[str, Any]) -> dict[str, Any]:
    packet = build_planning_packet({"task": trial["task"], "repo_path": trial.get("repo_path", "")})
    trial_id = str(trial["id"])
    validation_problems = validate_planning_packet(packet)
    service_contract_status = validate_service_contracts_against_packet(packet, trial_id)
    expected_decision = trial.get("expected_decision", "ready_for_ceraxia_review")
    if expected_decision != "blocked" and validation_problems:
        raise AssertionError(f"{trial_id}: generated packet failed contract validation: {validation_problems}; packet={packet}")
    require_subset(trial.get("expected_kinds", []), packet["task_triage"]["task_kinds"], "task kinds", trial_id)
    phases = [phase["id"] for phase in packet["work_breakdown"]["phases"]]
    require_subset(trial.get("expected_phases", []), phases, "work phases", trial_id)
    work_packages = packet["implementation_work_packages"]["packages"]
    work_package_ids = [package["id"] for package in work_packages]
    require_subset(trial.get("expected_work_packages", []), work_package_ids, "implementation work packages", trial_id)
    playbook = packet.get("investigation_playbook") if isinstance(packet.get("investigation_playbook"), dict) else {}
    read_stages = playbook.get("read_stages") if isinstance(playbook.get("read_stages"), list) else []
    if len(read_stages) < 5:
        raise AssertionError(f"{trial_id}: investigation playbook must include ordered read stages: {packet}")
    if not isinstance(playbook.get("evidence_questions"), list) or len(playbook.get("evidence_questions", [])) < 4:
        raise AssertionError(f"{trial_id}: investigation playbook must include evidence questions: {packet}")
    if not isinstance(playbook.get("mutation_blockers"), list) or len(playbook.get("mutation_blockers", [])) < 3:
        raise AssertionError(f"{trial_id}: investigation playbook must include mutation blockers: {packet}")
    change_control = packet.get("change_control_plan") if isinstance(packet.get("change_control_plan"), dict) else {}
    if change_control.get("target") != "CodeBrigade":
        raise AssertionError(f"{trial_id}: change control plan must target CodeBrigade: {packet}")
    for key, minimum in [
        ("allowed_change_intents", 3),
        ("protected_invariants", 3),
        ("mutation_requires", 4),
        ("diff_review_questions", 3),
        ("rollback_triggers", 3),
        ("post_change_proofs", 3),
    ]:
        if not isinstance(change_control.get(key), list) or len(change_control.get(key, [])) < minimum:
            raise AssertionError(f"{trial_id}: change control plan missing {key}: {packet}")
    missing_blocking_policy = [package.get("id", "<unknown>") for package in work_packages if not package.get("blocking_policy")]
    if missing_blocking_policy:
        raise AssertionError(f"{trial_id}: work packages missing blocking_policy: {missing_blocking_policy}; packet={packet}")
    if packet["implementation_work_packages"]["review_order"] != work_package_ids:
        raise AssertionError(f"{trial_id}: work package review_order must match package order: {packet}")
    package_graph = packet["implementation_work_packages"].get("package_dependency_graph") if isinstance(packet["implementation_work_packages"].get("package_dependency_graph"), dict) else {}
    dependency_rows = package_graph.get("rows") if isinstance(package_graph.get("rows"), list) else []
    graph_package_ids = [row.get("package_id") for row in dependency_rows if isinstance(row, dict)]
    if sorted(graph_package_ids) != sorted(work_package_ids):
        raise AssertionError(f"{trial_id}: package dependency graph must cover every package: {packet}")
    if "evidence_survey_package" not in package_graph.get("root_packages", []):
        raise AssertionError(f"{trial_id}: package dependency graph must root at evidence survey: {packet}")
    if "verification_evidence_package" not in package_graph.get("terminal_packages", []):
        raise AssertionError(f"{trial_id}: package dependency graph must terminate at verification evidence: {packet}")
    verification_row = next((row for row in dependency_rows if isinstance(row, dict) and row.get("package_id") == "verification_evidence_package"), {})
    verification_dependencies = verification_row.get("depends_on") if isinstance(verification_row.get("depends_on"), list) else []
    missing_verification_dependencies = sorted(package_id for package_id in work_package_ids if package_id != "verification_evidence_package" and package_id not in verification_dependencies)
    if missing_verification_dependencies:
        raise AssertionError(f"{trial_id}: verification package must depend on every earlier package: {missing_verification_dependencies}")
    surfaces = [surface["surface"] for surface in packet["impact_analysis"]["surfaces"]]
    covered_surfaces = {
        surface
        for package in work_packages
        for surface in package.get("impact_surfaces", [])
        if isinstance(surface, str)
    }
    missing_surface_packages = sorted(surface for surface in surfaces if surface not in covered_surfaces)
    if missing_surface_packages:
        raise AssertionError(f"{trial_id}: impact surfaces lack implementation work packages: {missing_surface_packages}; packet={packet}")
    package_matrix_surfaces = {
        row.get("surface")
        for row in packet["surface_package_matrix"]["rows"]
        if isinstance(row, dict) and row.get("surface") and row.get("package_ids")
    }
    require_subset(surfaces, package_matrix_surfaces, "surface package matrix", trial_id)
    require_subset(trial.get("expected_surfaces", []), surfaces, "impact surfaces", trial_id)
    expected_highest_risk_surface = trial.get("expected_highest_risk_surface")
    if expected_highest_risk_surface and packet["impact_analysis"]["highest_risk_surface"] != expected_highest_risk_surface:
        raise AssertionError(
            f"{trial_id}: expected highest risk surface {expected_highest_risk_surface}, "
            f"got {packet['impact_analysis']['highest_risk_surface']}: {packet}"
        )
    if not packet["surface_verification_matrix"]["complete"]:
        raise AssertionError(f"{trial_id}: surface verification matrix should be complete: {packet}")
    matrix_surfaces = [row["surface"] for row in packet["surface_verification_matrix"]["rows"]]
    require_subset(trial.get("expected_surfaces", []), matrix_surfaces, "surface verification rows", trial_id)
    require_subset(
        trial.get("expected_negative_tests", []),
        packet["verification_strategy"]["negative_tests"],
        "negative tests",
        trial_id,
    )
    acceptance_trace = packet.get("acceptance_trace_matrix") if isinstance(packet.get("acceptance_trace_matrix"), dict) else {}
    trace_rows = acceptance_trace.get("rows") if isinstance(acceptance_trace.get("rows"), list) else []
    if acceptance_trace.get("complete") is not True or not trace_rows:
        raise AssertionError(f"{trial_id}: acceptance trace matrix must be complete: {packet}")
    if acceptance_trace.get("definition_of_done_complete") is not True:
        raise AssertionError(f"{trial_id}: acceptance trace matrix must cover every definition_of_done item: {packet}")
    if acceptance_trace.get("definition_of_done_count") != len(packet["problem_statement"]["definition_of_done"]):
        raise AssertionError(f"{trial_id}: definition_of_done trace count must match the problem statement: {packet}")
    if acceptance_trace.get("traced_definition_of_done_count") != acceptance_trace.get("definition_of_done_count"):
        raise AssertionError(f"{trial_id}: every definition_of_done item must be traced: {packet}")
    if acceptance_trace.get("missing_definition_of_done"):
        raise AssertionError(f"{trial_id}: definition_of_done trace must not have missing items: {packet}")
    constraint_trace = packet.get("constraint_trace_matrix") if isinstance(packet.get("constraint_trace_matrix"), dict) else {}
    constraint_rows = constraint_trace.get("rows") if isinstance(constraint_trace.get("rows"), list) else []
    if constraint_trace.get("complete") is not True or not constraint_rows:
        raise AssertionError(f"{trial_id}: constraint trace matrix must be complete: {packet}")
    handoff = packet.get("code_brigade_handoff") if isinstance(packet.get("code_brigade_handoff"), dict) else {}
    if handoff.get("package_review_order") != packet["implementation_work_packages"]["review_order"]:
        raise AssertionError(f"{trial_id}: CodeBrigade handoff must carry package review order: {packet}")
    if handoff.get("package_dependency_graph") != package_graph:
        raise AssertionError(f"{trial_id}: CodeBrigade handoff must carry package dependency graph: {packet}")
    if handoff.get("global_handoff_criteria") != packet["implementation_work_packages"]["global_handoff_criteria"]:
        raise AssertionError(f"{trial_id}: CodeBrigade handoff must carry global handoff criteria: {packet}")
    if handoff.get("acceptance_trace_required") is not True:
        raise AssertionError(f"{trial_id}: CodeBrigade handoff must require acceptance trace: {packet}")
    if handoff.get("acceptance_trace_row_count") != acceptance_trace.get("row_count"):
        raise AssertionError(f"{trial_id}: CodeBrigade handoff acceptance row count drifted: {packet}")
    if handoff.get("definition_of_done_trace_required") is not True:
        raise AssertionError(f"{trial_id}: CodeBrigade handoff must require definition_of_done trace: {packet}")
    if handoff.get("definition_of_done_count") != acceptance_trace.get("definition_of_done_count"):
        raise AssertionError(f"{trial_id}: CodeBrigade handoff definition_of_done count drifted: {packet}")
    if handoff.get("traced_definition_of_done_count") != acceptance_trace.get("traced_definition_of_done_count"):
        raise AssertionError(f"{trial_id}: CodeBrigade handoff traced definition_of_done count drifted: {packet}")
    output_contract = packet.get("worker_output_contract") if isinstance(packet.get("worker_output_contract"), dict) else {}
    if output_contract.get("target") != "CodeBrigade":
        raise AssertionError(f"{trial_id}: worker output contract must target CodeBrigade: {packet}")
    if output_contract.get("required_package_statuses") != work_package_ids:
        raise AssertionError(f"{trial_id}: worker output contract must track work package order: {packet}")
    output_rows = output_contract.get("package_result_contract") if isinstance(output_contract.get("package_result_contract"), list) else []
    output_row_package_ids = [
        row.get("package_id")
        for row in output_rows
        if isinstance(row, dict) and isinstance(row.get("package_id"), str) and row.get("package_id")
    ]
    if sorted(output_row_package_ids) != sorted(work_package_ids):
        raise AssertionError(f"{trial_id}: worker output contract rows must cover every work package: {packet}")
    for row in output_rows:
        if not isinstance(row, dict):
            raise AssertionError(f"{trial_id}: worker output contract row must be an object: {packet}")
        if not row.get("required_status_field") or not row.get("required_evidence_source"):
            raise AssertionError(f"{trial_id}: worker output contract row must require status and evidence source: {packet}")
        if not isinstance(row.get("acceptance_evidence"), list) or not row.get("acceptance_evidence"):
            raise AssertionError(f"{trial_id}: worker output contract row must include acceptance evidence: {packet}")
        if len(row.get("blocker_contract", []) if isinstance(row.get("blocker_contract"), list) else []) < 3:
            raise AssertionError(f"{trial_id}: worker output contract row must include blocker contract: {packet}")
    if handoff.get("worker_output_contract") != output_contract:
        raise AssertionError(f"{trial_id}: CodeBrigade handoff must carry worker output contract: {packet}")
    blueprint = packet.get("implementation_brief_blueprint") if isinstance(packet.get("implementation_brief_blueprint"), dict) else {}
    brief_required_sections = blueprint.get("required_sections") if isinstance(blueprint.get("required_sections"), list) else []
    brief_mutation_preconditions = blueprint.get("mutation_preconditions") if isinstance(blueprint.get("mutation_preconditions"), list) else []
    if len(brief_required_sections) < 20:
        raise AssertionError(f"{trial_id}: implementation brief blueprint must require the full CodeBrigade handoff surface: {packet}")
    if len(brief_mutation_preconditions) < 6:
        raise AssertionError(f"{trial_id}: implementation brief blueprint must include mutation preconditions: {packet}")
    surface_matrix = packet.get("surface_verification_matrix") if isinstance(packet.get("surface_verification_matrix"), dict) else {}
    surface_rows = surface_matrix.get("rows") if isinstance(surface_matrix.get("rows"), list) else []
    surface_output_evidence_required = [
        str(item)
        for row in surface_rows
        if isinstance(row, dict)
        for item in (row.get("output_evidence_required") if isinstance(row.get("output_evidence_required"), list) else [])
        if isinstance(item, str) and item
    ]
    if not surface_output_evidence_required:
        raise AssertionError(f"{trial_id}: surface verification rows must require output evidence: {packet}")
    assumptions = packet.get("assumption_register") if isinstance(packet.get("assumption_register"), dict) else {}
    assumption_rows = assumptions.get("assumptions") if isinstance(assumptions.get("assumptions"), list) else []
    if len(assumption_rows) < 3:
        raise AssertionError(f"{trial_id}: assumption register must include task, repo, and verification assumptions: {packet}")
    decision = packet["planning_review_gate"]["decision"]
    if decision != expected_decision:
        raise AssertionError(f"{trial_id}: expected decision {expected_decision}, got {decision}: {packet}")
    minimum_score = int(trial.get("minimum_score", 0))
    if packet["planning_review_gate"]["score"] < minimum_score:
        raise AssertionError(f"{trial_id}: planning score below {minimum_score}: {packet}")
    return {
        "id": trial_id,
        "decision": decision,
        "score": packet["planning_review_gate"]["score"],
        "task_kinds": packet["task_triage"]["task_kinds"],
        "phases": phases,
        "work_packages": work_package_ids,
        "package_dependency_graph_packages": graph_package_ids,
        "package_dependency_graph_roots": package_graph.get("root_packages", []) if isinstance(package_graph.get("root_packages"), list) else [],
        "package_dependency_graph_terminals": package_graph.get("terminal_packages", []) if isinstance(package_graph.get("terminal_packages"), list) else [],
        "package_dependency_graph_parallelizable_after_survey": package_graph.get("parallelizable_after_survey", []) if isinstance(package_graph.get("parallelizable_after_survey"), list) else [],
        "package_dependency_graph_complete": package_graph.get("complete"),
        "surfaces": surfaces,
        "highest_risk_surface": packet["impact_analysis"]["highest_risk_surface"],
        "negative_tests": packet["verification_strategy"]["negative_tests"],
        "change_protected_invariants": change_control["protected_invariants"],
        "change_post_change_proofs": change_control["post_change_proofs"],
        "change_rollback_triggers": change_control["rollback_triggers"],
        "acceptance_trace_requirements": [row["requirement"] for row in trace_rows if isinstance(row, dict) and row.get("requirement")],
        "acceptance_trace_package_ids": [
            package_id
            for row in trace_rows
            if isinstance(row, dict)
            for package_id in row.get("package_ids", [])
            if isinstance(package_id, str)
        ],
        "acceptance_trace_row_count": len(trace_rows),
        "definition_of_done_count": acceptance_trace.get("definition_of_done_count"),
        "traced_definition_of_done_count": acceptance_trace.get("traced_definition_of_done_count"),
        "definition_of_done_complete": acceptance_trace.get("definition_of_done_complete"),
        "handoff_package_review_order": handoff.get("package_review_order", []) if isinstance(handoff.get("package_review_order"), list) else [],
        "handoff_acceptance_trace_required": handoff.get("acceptance_trace_required"),
        "handoff_acceptance_trace_row_count": handoff.get("acceptance_trace_row_count"),
        "handoff_definition_of_done_trace_required": handoff.get("definition_of_done_trace_required"),
        "handoff_definition_of_done_count": handoff.get("definition_of_done_count"),
        "handoff_traced_definition_of_done_count": handoff.get("traced_definition_of_done_count"),
        "brief_required_sections": [str(item) for item in brief_required_sections if isinstance(item, str)],
        "brief_mutation_preconditions": [str(item) for item in brief_mutation_preconditions if isinstance(item, str)],
        "brief_required_section_count": len(brief_required_sections),
        "brief_mutation_precondition_count": len(brief_mutation_preconditions),
        "worker_output_required_package_statuses": output_contract.get("required_package_statuses", []) if isinstance(output_contract.get("required_package_statuses"), list) else [],
        "worker_output_package_result_ids": output_row_package_ids,
        "worker_output_required_report_count": len(output_contract.get("required_reports", [])) if isinstance(output_contract.get("required_reports"), list) else 0,
        "worker_output_final_review_input_count": len(output_contract.get("final_review_inputs", [])) if isinstance(output_contract.get("final_review_inputs"), list) else 0,
        "worker_output_failure_contract_count": len(output_contract.get("failure_contract", [])) if isinstance(output_contract.get("failure_contract"), list) else 0,
        "surface_output_evidence_required": surface_output_evidence_required,
        "surface_output_evidence_required_count": len(surface_output_evidence_required),
        "constraint_trace_constraints": [row["constraint"] for row in constraint_rows if isinstance(row, dict) and row.get("constraint")],
        "constraint_trace_package_ids": [
            package_id
            for row in constraint_rows
            if isinstance(row, dict)
            for package_id in row.get("package_ids", [])
            if isinstance(package_id, str)
        ],
        "constraint_trace_row_count": len(constraint_rows),
        "assumption_ids": [row["id"] for row in assumption_rows if isinstance(row, dict) and row.get("id")],
        "assumption_replan_triggers": assumptions.get("replan_when_false", []) if isinstance(assumptions.get("replan_when_false"), list) else [],
        "assumption_count": len(assumption_rows),
        "planning_service_names": service_contract_status["service_names"],
        "planning_service_ports": service_contract_status["service_ports"],
        "planning_service_active_registry_port_count": service_contract_status["active_registry_port_count"],
        "planning_service_port_collision_count": service_contract_status["service_port_collision_count"],
        "planning_service_split_gate_count": service_contract_status["service_split_gate_count"],
        "planning_service_contract_active": service_contract_status["service_contract_active"],
        "change_control_counts": {
            "allowed_change_intents": len(change_control["allowed_change_intents"]),
            "protected_invariants": len(change_control["protected_invariants"]),
            "mutation_requires": len(change_control["mutation_requires"]),
            "diff_review_questions": len(change_control["diff_review_questions"]),
            "rollback_triggers": len(change_control["rollback_triggers"]),
            "post_change_proofs": len(change_control["post_change_proofs"]),
        },
        "validation_problem_count": len(validation_problems),
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    kinds: Counter[str] = Counter()
    phases: Counter[str] = Counter()
    work_packages: Counter[str] = Counter()
    graph_packages: Counter[str] = Counter()
    graph_roots: Counter[str] = Counter()
    graph_terminals: Counter[str] = Counter()
    graph_parallelizable_after_survey: Counter[str] = Counter()
    graph_complete_values: Counter[str] = Counter()
    surfaces: Counter[str] = Counter()
    highest_risk_surfaces: Counter[str] = Counter()
    decisions: Counter[str] = Counter()
    negative_tests: Counter[str] = Counter()
    change_invariants: Counter[str] = Counter()
    change_post_proofs: Counter[str] = Counter()
    change_rollback_triggers: Counter[str] = Counter()
    acceptance_trace_packages: Counter[str] = Counter()
    acceptance_trace_requirements: Counter[str] = Counter()
    acceptance_trace_row_counts: list[int] = []
    definition_of_done_counts: list[int] = []
    traced_definition_of_done_counts: list[int] = []
    definition_of_done_complete_values: Counter[str] = Counter()
    handoff_package_review_orders: list[list[str]] = []
    handoff_acceptance_trace_required: Counter[str] = Counter()
    handoff_acceptance_trace_row_counts: list[int] = []
    handoff_definition_of_done_trace_required: Counter[str] = Counter()
    handoff_definition_of_done_counts: list[int] = []
    handoff_traced_definition_of_done_counts: list[int] = []
    brief_required_sections: Counter[str] = Counter()
    brief_mutation_preconditions: Counter[str] = Counter()
    brief_required_section_counts: list[int] = []
    brief_mutation_precondition_counts: list[int] = []
    worker_output_required_packages: Counter[str] = Counter()
    worker_output_result_packages: Counter[str] = Counter()
    worker_output_required_report_counts: list[int] = []
    worker_output_final_review_input_counts: list[int] = []
    worker_output_failure_contract_counts: list[int] = []
    surface_output_evidence_required: Counter[str] = Counter()
    surface_output_evidence_required_counts: list[int] = []
    constraint_trace_constraints: Counter[str] = Counter()
    constraint_trace_packages: Counter[str] = Counter()
    constraint_trace_row_counts: list[int] = []
    assumption_ids: Counter[str] = Counter()
    assumption_replan_triggers: Counter[str] = Counter()
    assumption_counts: list[int] = []
    planning_service_names: Counter[str] = Counter()
    planning_service_ports: Counter[str] = Counter()
    planning_service_active_registry_port_counts: list[int] = []
    planning_service_port_collision_counts: list[int] = []
    planning_service_split_gate_counts: list[int] = []
    planning_service_active_values: Counter[str] = Counter()
    minimum_change_control_counts: dict[str, int] = {}
    scores: list[int] = []
    for result in results:
        kinds.update(str(item) for item in result.get("task_kinds", []))
        phases.update(str(item) for item in result.get("phases", []))
        work_packages.update(str(item) for item in result.get("work_packages", []))
        graph_packages.update(str(item) for item in result.get("package_dependency_graph_packages", []))
        graph_roots.update(str(item) for item in result.get("package_dependency_graph_roots", []))
        graph_terminals.update(str(item) for item in result.get("package_dependency_graph_terminals", []))
        graph_parallelizable_after_survey.update(str(item) for item in result.get("package_dependency_graph_parallelizable_after_survey", []))
        graph_complete_values.update([str(result.get("package_dependency_graph_complete"))])
        surfaces.update(str(item) for item in result.get("surfaces", []))
        if result.get("highest_risk_surface"):
            highest_risk_surfaces.update([str(result["highest_risk_surface"])])
        decisions.update([str(result.get("decision", ""))])
        negative_tests.update(str(item) for item in result.get("negative_tests", []))
        change_invariants.update(str(item) for item in result.get("change_protected_invariants", []))
        change_post_proofs.update(str(item) for item in result.get("change_post_change_proofs", []))
        change_rollback_triggers.update(str(item) for item in result.get("change_rollback_triggers", []))
        acceptance_trace_packages.update(str(item) for item in result.get("acceptance_trace_package_ids", []))
        acceptance_trace_requirements.update(str(item) for item in result.get("acceptance_trace_requirements", []))
        if isinstance(result.get("acceptance_trace_row_count"), int):
            acceptance_trace_row_counts.append(result["acceptance_trace_row_count"])
        if isinstance(result.get("definition_of_done_count"), int):
            definition_of_done_counts.append(result["definition_of_done_count"])
        if isinstance(result.get("traced_definition_of_done_count"), int):
            traced_definition_of_done_counts.append(result["traced_definition_of_done_count"])
        definition_of_done_complete_values.update([str(result.get("definition_of_done_complete"))])
        handoff_order = result.get("handoff_package_review_order")
        if isinstance(handoff_order, list):
            handoff_package_review_orders.append([str(item) for item in handoff_order])
        handoff_acceptance_trace_required.update([str(result.get("handoff_acceptance_trace_required"))])
        if isinstance(result.get("handoff_acceptance_trace_row_count"), int):
            handoff_acceptance_trace_row_counts.append(result["handoff_acceptance_trace_row_count"])
        handoff_definition_of_done_trace_required.update([str(result.get("handoff_definition_of_done_trace_required"))])
        if isinstance(result.get("handoff_definition_of_done_count"), int):
            handoff_definition_of_done_counts.append(result["handoff_definition_of_done_count"])
        if isinstance(result.get("handoff_traced_definition_of_done_count"), int):
            handoff_traced_definition_of_done_counts.append(result["handoff_traced_definition_of_done_count"])
        brief_required_sections.update(str(item) for item in result.get("brief_required_sections", []))
        brief_mutation_preconditions.update(str(item) for item in result.get("brief_mutation_preconditions", []))
        if isinstance(result.get("brief_required_section_count"), int):
            brief_required_section_counts.append(result["brief_required_section_count"])
        if isinstance(result.get("brief_mutation_precondition_count"), int):
            brief_mutation_precondition_counts.append(result["brief_mutation_precondition_count"])
        worker_output_required_packages.update(str(item) for item in result.get("worker_output_required_package_statuses", []))
        worker_output_result_packages.update(str(item) for item in result.get("worker_output_package_result_ids", []))
        if isinstance(result.get("worker_output_required_report_count"), int):
            worker_output_required_report_counts.append(result["worker_output_required_report_count"])
        if isinstance(result.get("worker_output_final_review_input_count"), int):
            worker_output_final_review_input_counts.append(result["worker_output_final_review_input_count"])
        if isinstance(result.get("worker_output_failure_contract_count"), int):
            worker_output_failure_contract_counts.append(result["worker_output_failure_contract_count"])
        surface_output_evidence_required.update(str(item) for item in result.get("surface_output_evidence_required", []))
        if isinstance(result.get("surface_output_evidence_required_count"), int):
            surface_output_evidence_required_counts.append(result["surface_output_evidence_required_count"])
        constraint_trace_constraints.update(str(item) for item in result.get("constraint_trace_constraints", []))
        constraint_trace_packages.update(str(item) for item in result.get("constraint_trace_package_ids", []))
        if isinstance(result.get("constraint_trace_row_count"), int):
            constraint_trace_row_counts.append(result["constraint_trace_row_count"])
        assumption_ids.update(str(item) for item in result.get("assumption_ids", []))
        assumption_replan_triggers.update(str(item) for item in result.get("assumption_replan_triggers", []))
        if isinstance(result.get("assumption_count"), int):
            assumption_counts.append(result["assumption_count"])
        planning_service_names.update(str(item) for item in result.get("planning_service_names", []))
        planning_service_ports.update(str(item) for item in result.get("planning_service_ports", []))
        if isinstance(result.get("planning_service_active_registry_port_count"), int):
            planning_service_active_registry_port_counts.append(result["planning_service_active_registry_port_count"])
        if isinstance(result.get("planning_service_port_collision_count"), int):
            planning_service_port_collision_counts.append(result["planning_service_port_collision_count"])
        if isinstance(result.get("planning_service_split_gate_count"), int):
            planning_service_split_gate_counts.append(result["planning_service_split_gate_count"])
        planning_service_active_values.update([str(result.get("planning_service_contract_active"))])
        counts = result.get("change_control_counts") if isinstance(result.get("change_control_counts"), dict) else {}
        for key, value in counts.items():
            if isinstance(value, int):
                minimum_change_control_counts[key] = min(minimum_change_control_counts.get(key, value), value)
        if isinstance(result.get("score"), int):
            scores.append(result["score"])
    return {
        "trial_count": len(results),
        "decision_counts": dict(sorted(decisions.items())),
        "task_kind_counts": dict(sorted(kinds.items())),
        "phase_counts": dict(sorted(phases.items())),
        "work_package_counts": dict(sorted(work_packages.items())),
        "package_dependency_graph_package_counts": dict(sorted(graph_packages.items())),
        "package_dependency_graph_root_counts": dict(sorted(graph_roots.items())),
        "package_dependency_graph_terminal_counts": dict(sorted(graph_terminals.items())),
        "package_dependency_graph_parallelizable_after_survey_counts": dict(sorted(graph_parallelizable_after_survey.items())),
        "package_dependency_graph_complete_value_counts": dict(sorted(graph_complete_values.items())),
        "surface_counts": dict(sorted(surfaces.items())),
        "highest_risk_surface_counts": dict(sorted(highest_risk_surfaces.items())),
        "negative_test_counts": dict(sorted(negative_tests.items())),
        "change_invariant_counts": dict(sorted(change_invariants.items())),
        "change_post_proof_counts": dict(sorted(change_post_proofs.items())),
        "change_rollback_trigger_counts": dict(sorted(change_rollback_triggers.items())),
        "acceptance_trace_package_counts": dict(sorted(acceptance_trace_packages.items())),
        "acceptance_trace_requirement_counts": dict(sorted(acceptance_trace_requirements.items())),
        "minimum_acceptance_trace_row_count": min(acceptance_trace_row_counts) if acceptance_trace_row_counts else 0,
        "minimum_definition_of_done_count": min(definition_of_done_counts) if definition_of_done_counts else 0,
        "minimum_traced_definition_of_done_count": min(traced_definition_of_done_counts) if traced_definition_of_done_counts else 0,
        "definition_of_done_complete_value_counts": dict(sorted(definition_of_done_complete_values.items())),
        "handoff_package_review_orders": handoff_package_review_orders,
        "handoff_acceptance_trace_required_counts": dict(sorted(handoff_acceptance_trace_required.items())),
        "minimum_handoff_acceptance_trace_row_count": min(handoff_acceptance_trace_row_counts) if handoff_acceptance_trace_row_counts else 0,
        "handoff_definition_of_done_trace_required_counts": dict(sorted(handoff_definition_of_done_trace_required.items())),
        "minimum_handoff_definition_of_done_count": min(handoff_definition_of_done_counts) if handoff_definition_of_done_counts else 0,
        "minimum_handoff_traced_definition_of_done_count": min(handoff_traced_definition_of_done_counts) if handoff_traced_definition_of_done_counts else 0,
        "brief_required_section_counts": dict(sorted(brief_required_sections.items())),
        "brief_mutation_precondition_counts": dict(sorted(brief_mutation_preconditions.items())),
        "minimum_brief_required_section_count": min(brief_required_section_counts) if brief_required_section_counts else 0,
        "minimum_brief_mutation_precondition_count": min(brief_mutation_precondition_counts) if brief_mutation_precondition_counts else 0,
        "worker_output_required_package_counts": dict(sorted(worker_output_required_packages.items())),
        "worker_output_result_package_counts": dict(sorted(worker_output_result_packages.items())),
        "minimum_worker_output_required_report_count": min(worker_output_required_report_counts) if worker_output_required_report_counts else 0,
        "minimum_worker_output_final_review_input_count": min(worker_output_final_review_input_counts) if worker_output_final_review_input_counts else 0,
        "minimum_worker_output_failure_contract_count": min(worker_output_failure_contract_counts) if worker_output_failure_contract_counts else 0,
        "surface_output_evidence_required_counts": dict(sorted(surface_output_evidence_required.items())),
        "minimum_surface_output_evidence_required_count": min(surface_output_evidence_required_counts) if surface_output_evidence_required_counts else 0,
        "constraint_trace_constraint_counts": dict(sorted(constraint_trace_constraints.items())),
        "constraint_trace_package_counts": dict(sorted(constraint_trace_packages.items())),
        "minimum_constraint_trace_row_count": min(constraint_trace_row_counts) if constraint_trace_row_counts else 0,
        "assumption_id_counts": dict(sorted(assumption_ids.items())),
        "assumption_replan_trigger_counts": dict(sorted(assumption_replan_triggers.items())),
        "minimum_assumption_count": min(assumption_counts) if assumption_counts else 0,
        "planning_service_name_counts": dict(sorted(planning_service_names.items())),
        "planning_service_port_counts": dict(sorted(planning_service_ports.items())),
        "minimum_planning_service_active_registry_port_count": min(planning_service_active_registry_port_counts) if planning_service_active_registry_port_counts else 0,
        "maximum_planning_service_port_collision_count": max(planning_service_port_collision_counts) if planning_service_port_collision_counts else 0,
        "minimum_planning_service_split_gate_count": min(planning_service_split_gate_counts) if planning_service_split_gate_counts else 0,
        "planning_service_active_value_counts": dict(sorted(planning_service_active_values.items())),
        "minimum_change_control_counts": dict(sorted(minimum_change_control_counts.items())),
        "minimum_score": min(scores) if scores else 0,
        "average_score": round(sum(scores) / len(scores), 2) if scores else 0,
    }


def assert_coverage(summary: dict[str, Any]) -> None:
    required_kinds = {
        "api_compatibility",
        "bugfix",
        "concurrency",
        "config_runtime",
        "migration",
        "refactor",
        "security",
        "test_repair",
    }
    required_surfaces = {
        "concurrency_runtime",
        "data_compatibility",
        "internal_architecture",
        "public_api_contract",
        "runtime_configuration",
        "security_boundary",
        "source_behavior",
        "test_surface",
    }
    required_work_packages = {
        "compatibility_package",
        "architecture_refactor_package",
        "concurrency_runtime_package",
        "evidence_survey_package",
        "minimal_patch_package",
        "runtime_configuration_package",
        "security_boundary_package",
        "verification_evidence_package",
    }
    kind_counts = summary.get("task_kind_counts") if isinstance(summary.get("task_kind_counts"), dict) else {}
    surface_counts = summary.get("surface_counts") if isinstance(summary.get("surface_counts"), dict) else {}
    work_package_counts = summary.get("work_package_counts") if isinstance(summary.get("work_package_counts"), dict) else {}
    invariant_counts = summary.get("change_invariant_counts") if isinstance(summary.get("change_invariant_counts"), dict) else {}
    post_proof_counts = summary.get("change_post_proof_counts") if isinstance(summary.get("change_post_proof_counts"), dict) else {}
    rollback_counts = summary.get("change_rollback_trigger_counts") if isinstance(summary.get("change_rollback_trigger_counts"), dict) else {}
    acceptance_trace_package_counts = summary.get("acceptance_trace_package_counts") if isinstance(summary.get("acceptance_trace_package_counts"), dict) else {}
    handoff_acceptance_required_counts = summary.get("handoff_acceptance_trace_required_counts") if isinstance(summary.get("handoff_acceptance_trace_required_counts"), dict) else {}
    definition_of_done_complete_counts = summary.get("definition_of_done_complete_value_counts") if isinstance(summary.get("definition_of_done_complete_value_counts"), dict) else {}
    handoff_definition_of_done_required_counts = summary.get("handoff_definition_of_done_trace_required_counts") if isinstance(summary.get("handoff_definition_of_done_trace_required_counts"), dict) else {}
    brief_required_section_counts = summary.get("brief_required_section_counts") if isinstance(summary.get("brief_required_section_counts"), dict) else {}
    brief_mutation_precondition_counts = summary.get("brief_mutation_precondition_counts") if isinstance(summary.get("brief_mutation_precondition_counts"), dict) else {}
    worker_output_required_package_counts = summary.get("worker_output_required_package_counts") if isinstance(summary.get("worker_output_required_package_counts"), dict) else {}
    worker_output_result_package_counts = summary.get("worker_output_result_package_counts") if isinstance(summary.get("worker_output_result_package_counts"), dict) else {}
    surface_output_evidence_counts = summary.get("surface_output_evidence_required_counts") if isinstance(summary.get("surface_output_evidence_required_counts"), dict) else {}
    constraint_trace_package_counts = summary.get("constraint_trace_package_counts") if isinstance(summary.get("constraint_trace_package_counts"), dict) else {}
    assumption_id_counts = summary.get("assumption_id_counts") if isinstance(summary.get("assumption_id_counts"), dict) else {}
    planning_service_name_counts = summary.get("planning_service_name_counts") if isinstance(summary.get("planning_service_name_counts"), dict) else {}
    planning_service_port_counts = summary.get("planning_service_port_counts") if isinstance(summary.get("planning_service_port_counts"), dict) else {}
    planning_service_active_values = summary.get("planning_service_active_value_counts") if isinstance(summary.get("planning_service_active_value_counts"), dict) else {}
    minimum_change_counts = summary.get("minimum_change_control_counts") if isinstance(summary.get("minimum_change_control_counts"), dict) else {}
    missing_kinds = sorted(kind for kind in required_kinds if kind not in kind_counts)
    missing_surfaces = sorted(surface for surface in required_surfaces if surface not in surface_counts)
    missing_work_packages = sorted(package for package in required_work_packages if package not in work_package_counts)
    graph_package_counts = summary.get("package_dependency_graph_package_counts") if isinstance(summary.get("package_dependency_graph_package_counts"), dict) else {}
    graph_root_counts = summary.get("package_dependency_graph_root_counts") if isinstance(summary.get("package_dependency_graph_root_counts"), dict) else {}
    graph_terminal_counts = summary.get("package_dependency_graph_terminal_counts") if isinstance(summary.get("package_dependency_graph_terminal_counts"), dict) else {}
    graph_complete_counts = summary.get("package_dependency_graph_complete_value_counts") if isinstance(summary.get("package_dependency_graph_complete_value_counts"), dict) else {}
    missing_graph_packages = sorted(package for package in required_work_packages if package not in graph_package_counts)
    if missing_kinds:
        raise AssertionError(f"field trials are missing task kind coverage: {missing_kinds}")
    if missing_surfaces:
        raise AssertionError(f"field trials are missing surface coverage: {missing_surfaces}")
    if missing_work_packages:
        raise AssertionError(f"field trials are missing implementation work package coverage: {missing_work_packages}")
    if missing_graph_packages:
        raise AssertionError(f"field trials are missing package dependency graph coverage: {missing_graph_packages}")
    if "evidence_survey_package" not in graph_root_counts:
        raise AssertionError(f"field trials must root package dependency graphs at evidence_survey_package: {summary}")
    if "verification_evidence_package" not in graph_terminal_counts:
        raise AssertionError(f"field trials must terminate package dependency graphs at verification_evidence_package: {summary}")
    if graph_complete_counts != {"True": summary["trial_count"]}:
        raise AssertionError(f"field trials must prove package dependency graphs are complete: {summary}")
    decision_counts = summary.get("decision_counts") if isinstance(summary.get("decision_counts"), dict) else {}
    if "blocked" not in decision_counts or "ready_for_ceraxia_review" not in decision_counts:
        raise AssertionError(f"field trials must cover blocked and ready decisions: {summary}")
    required_invariant_fragments = [
        "negative security boundary",
        "old callers, old data",
        "parallel, retry, cache",
        "public entrypoints and dependency edges",
    ]
    missing_invariant_fragments = [
        fragment
        for fragment in required_invariant_fragments
        if not any(fragment in invariant for invariant in invariant_counts)
    ]
    if missing_invariant_fragments:
        raise AssertionError(f"field trials are missing change-control invariant coverage: {missing_invariant_fragments}")
    required_post_proof_fragments = [
        "negative boundary evidence",
        "compatibility evidence",
        "remaining race risk",
        "dependency-edge review",
    ]
    missing_post_proof_fragments = [
        fragment
        for fragment in required_post_proof_fragments
        if not any(fragment in proof for proof in post_proof_counts)
    ]
    if missing_post_proof_fragments:
        raise AssertionError(f"field trials are missing change-control proof coverage: {missing_post_proof_fragments}")
    for key, minimum in {
        "allowed_change_intents": 3,
        "protected_invariants": 3,
        "mutation_requires": 4,
        "diff_review_questions": 3,
        "rollback_triggers": 3,
        "post_change_proofs": 3,
    }.items():
        if minimum_change_counts.get(key, 0) < minimum:
            raise AssertionError(f"field trials have weak minimum change-control count for {key}: {summary}")
    if "verification cannot prove the changed behavior" not in rollback_counts:
        raise AssertionError(f"field trials must preserve verification rollback trigger coverage: {summary}")
    for package in required_work_packages:
        if package not in acceptance_trace_package_counts:
            raise AssertionError(f"field trials are missing acceptance trace package coverage for {package}: {summary}")
    if int(summary.get("minimum_acceptance_trace_row_count") or 0) < 3:
        raise AssertionError(f"field trials have too few acceptance trace rows: {summary}")
    if definition_of_done_complete_counts != {"True": summary["trial_count"]}:
        raise AssertionError(f"field trials must prove definition_of_done trace completeness: {summary}")
    if int(summary.get("minimum_definition_of_done_count") or 0) < 3:
        raise AssertionError(f"field trials have too few definition_of_done items: {summary}")
    if int(summary.get("minimum_traced_definition_of_done_count") or 0) < int(summary.get("minimum_definition_of_done_count") or 0):
        raise AssertionError(f"field trials show untraced definition_of_done items: {summary}")
    if handoff_acceptance_required_counts != {"True": summary["trial_count"]}:
        raise AssertionError(f"field trials must prove handoff requires acceptance trace: {summary}")
    if int(summary.get("minimum_handoff_acceptance_trace_row_count") or 0) < int(summary.get("minimum_acceptance_trace_row_count") or 0):
        raise AssertionError(f"field trials show handoff acceptance trace row count drift: {summary}")
    if handoff_definition_of_done_required_counts != {"True": summary["trial_count"]}:
        raise AssertionError(f"field trials must prove handoff requires definition_of_done trace: {summary}")
    if int(summary.get("minimum_handoff_definition_of_done_count") or 0) < int(summary.get("minimum_definition_of_done_count") or 0):
        raise AssertionError(f"field trials show handoff definition_of_done count drift: {summary}")
    if int(summary.get("minimum_handoff_traced_definition_of_done_count") or 0) < int(summary.get("minimum_traced_definition_of_done_count") or 0):
        raise AssertionError(f"field trials show handoff traced definition_of_done count drift: {summary}")
    required_brief_sections = {
        "surface_verification_matrix",
        "surface_package_matrix",
        "investigation_playbook",
        "acceptance_trace_matrix",
        "constraint_trace_matrix",
        "assumption_register",
        "implementation_work_packages",
        "worker_output_contract",
        "planning_review_gate",
        "change_control_plan",
    }
    missing_brief_sections = sorted(section for section in required_brief_sections if section not in brief_required_section_counts)
    if missing_brief_sections:
        raise AssertionError(f"field trials are missing implementation brief section coverage: {missing_brief_sections}")
    if int(summary.get("minimum_brief_required_section_count") or 0) < 20:
        raise AssertionError(f"field trials have too few implementation brief required sections: {summary}")
    required_precondition_fragments = [
        "implementation brief validates",
        "investigation playbook",
        "change control plan",
        "execution preflight passes",
        "candidate files are repo-relative",
        "verification plan is attached",
    ]
    missing_precondition_fragments = [
        fragment
        for fragment in required_precondition_fragments
        if not any(fragment in precondition for precondition in brief_mutation_precondition_counts)
    ]
    if missing_precondition_fragments:
        raise AssertionError(f"field trials are missing implementation brief mutation precondition coverage: {missing_precondition_fragments}")
    if int(summary.get("minimum_brief_mutation_precondition_count") or 0) < 6:
        raise AssertionError(f"field trials have too few implementation brief mutation preconditions: {summary}")
    missing_worker_output_required_packages = sorted(package for package in required_work_packages if package not in worker_output_required_package_counts)
    missing_worker_output_result_packages = sorted(package for package in required_work_packages if package not in worker_output_result_package_counts)
    if missing_worker_output_required_packages:
        raise AssertionError(f"field trials are missing worker-output required package coverage: {missing_worker_output_required_packages}")
    if missing_worker_output_result_packages:
        raise AssertionError(f"field trials are missing worker-output result package coverage: {missing_worker_output_result_packages}")
    if int(summary.get("minimum_worker_output_required_report_count") or 0) < 3:
        raise AssertionError(f"field trials have too few worker-output required reports: {summary}")
    if int(summary.get("minimum_worker_output_final_review_input_count") or 0) < 4:
        raise AssertionError(f"field trials have too few worker-output final review inputs: {summary}")
    if int(summary.get("minimum_worker_output_failure_contract_count") or 0) < 3:
        raise AssertionError(f"field trials have too few worker-output failure contract rows: {summary}")
    if int(summary.get("minimum_surface_output_evidence_required_count") or 0) < 2:
        raise AssertionError(f"field trials have too few surface output evidence requirements: {summary}")
    required_surface_output_fragments = [
        "command status is recorded",
        "output signal is classified",
        "negative boundary output",
        "compatibility output",
        "runtime configuration output",
        "parallel or retry output",
        "dependency or behavior output",
    ]
    missing_surface_output_fragments = [
        fragment
        for fragment in required_surface_output_fragments
        if not any(fragment in evidence for evidence in surface_output_evidence_counts)
    ]
    if missing_surface_output_fragments:
        raise AssertionError(f"field trials are missing surface output evidence coverage: {missing_surface_output_fragments}")
    if int(summary.get("minimum_constraint_trace_row_count") or 0) < 3:
        raise AssertionError(f"field trials have too few constraint trace rows: {summary}")
    for package in {"evidence_survey_package", "minimal_patch_package", "verification_evidence_package"}:
        if package not in constraint_trace_package_counts:
            raise AssertionError(f"field trials are missing constraint trace package coverage for {package}: {summary}")
    required_assumptions = {
        "task_contract_is_sufficient",
        "repo_survey_can_find_relevant_surface",
        "verification_can_prove_user_visible_behavior",
        "security_boundary_is_traceable",
        "compatibility_expectation_is_known",
        "state_transition_risk_is_bounded",
    }
    missing_assumptions = sorted(assumption for assumption in required_assumptions if assumption not in assumption_id_counts)
    if missing_assumptions:
        raise AssertionError(f"field trials are missing assumption coverage: {missing_assumptions}")
    if int(summary.get("minimum_assumption_count") or 0) < 3:
        raise AssertionError(f"field trials have too few assumptions: {summary}")
    required_service_names = {"TaskTriage", "RepoSurveyor", "DesignStrategos", "VerificationArchitect", "RiskScribe"}
    required_service_ports = {"7111", "7112", "7113", "7114", "7115"}
    missing_service_names = sorted(name for name in required_service_names if name not in planning_service_name_counts)
    missing_service_ports = sorted(port for port in required_service_ports if port not in planning_service_port_counts)
    if missing_service_names:
        raise AssertionError(f"field trials are missing planning service contract coverage: {missing_service_names}")
    if missing_service_ports:
        raise AssertionError(f"field trials are missing planning service port coverage: {missing_service_ports}")
    if int(summary.get("minimum_planning_service_active_registry_port_count") or 0) < 1:
        raise AssertionError(f"field trials must compare planning service ports with the active registry: {summary}")
    if int(summary.get("maximum_planning_service_port_collision_count") or 0) != 0:
        raise AssertionError(f"field trials found planning service port collisions: {summary}")
    if planning_service_active_values != {"False": summary["trial_count"]}:
        raise AssertionError(f"field trials must prove planning service contracts stay inactive: {summary}")
    if int(summary.get("minimum_planning_service_split_gate_count") or 0) < 5:
        raise AssertionError(f"field trials have too few planning service split gates: {summary}")


def main() -> int:
    spec = json.loads((ROOT / "field_trials.json").read_text(encoding="utf-8"))
    if spec.get("contract_version") != CONTRACT_VERSION:
        raise AssertionError(f"field trial contract version drifted: {spec}")
    results = [run_trial(trial) for trial in spec["trials"]]
    summary = summarize_results(results)
    assert_coverage(summary)
    print(json.dumps({"ok": True, "summary": summary, "trials": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
