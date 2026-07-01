#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


COMPONENTS = {
    "Ceraxia": {
        "kind": "governor_controller",
        "required_files": [
            "README.md",
            "ceraxia.py",
            "repo_survey.py",
            "self_test.py",
            "contracts/implementation_brief.schema.json",
            "contracts/diagnostic_repair_request.schema.json",
            "contracts/planning_feedback_request.schema.json",
            "contracts/evidence_matrix.schema.json",
            "contracts/run_artifacts.schema.json",
            "contracts/run_summary.schema.json",
        ],
        "maturity": "controller_with_planning_quality_survey_gate_read_order_assumption_register_investigation_playbook_change_control_acceptance_trace_definition_of_done_trace_audit_caller_contract_evidence_generic_edges_per_surface_verification_counts_work_package_review_worker_output_acceptance_requirement_contract_audit_diagnostic_repair_requests_planning_feedback_requests_and_optional_guarded_repair_execution",
    },
    "PlanningBrigade": {
        "kind": "advisory_planning_brigade",
        "required_files": [
            "README.md",
            "STATUS.md",
            "planning_brigade.py",
            "planning_feedback_contract.py",
            "planning_feedback_intake.schema.json",
            "planning_packet_contract.py",
            "planning_contract.schema.json",
            "role_contracts.json",
            "service_contracts.json",
            "field_trials.json",
            "field_trial_runner.py",
            "self_test.py",
            "TaskTriage/README.md",
            "RepoSurveyor/README.md",
            "DesignStrategos/README.md",
            "VerificationArchitect/README.md",
            "RiskScribe/README.md",
        ],
        "maturity": "contracted_planning_department_with_role_quality_gates_assumption_register_investigation_playbook_change_control_acceptance_trace_definition_of_done_trace_surface_output_evidence_implementation_brief_blueprint_field_trial_coverage_code_work_packages_dependency_execution_batches_worker_output_acceptance_requirement_contracts_and_ceraxia_feedback_intake",
    },
    "CodeBrigade": {
        "kind": "implementation_brigade_contract",
        "required_files": [
            "README.md",
            "code_brigade_contract.schema.json",
            "code_brigade_adapter.py",
            "diagnostic_repair_contract.py",
            "diagnostic_repair_intake.schema.json",
            "execution_adapter.py",
            "execution_contract.py",
            "execution_preflight.py",
            "execution_preflight.schema.json",
            "implementation_brief_contract.py",
            "execution_policy.schema.json",
            "execution_result.schema.json",
            "execution_policy.json",
            "verification_adapter.py",
            "verification_policy.json",
            "verification_policy.schema.json",
            "verification_execution.schema.json",
            "self_test.py",
        ],
        "maturity": "handoff_with_read_order_assumption_register_investigation_playbook_change_control_acceptance_trace_worker_output_contract_preflight_explicit_text_ast_guarded_natural_language_test_inferred_literal_patch_adapter_package_statuses_allowlisted_verification_diagnostic_repair_intake_executor_support_classification_and_guarded_assertion_failed_command_traceback_missing_import_repair_executor",
    },
}


ROADMAP = [
    {
        "priority": 1,
        "item": "expand CodeBrigade from guarded inference to diagnostic autonomous source edits",
        "reason": "PlanningBrigade now emits diagnostic repair and worker-output contracts, Ceraxia writes diagnostic repair request artifacts and audits worker-output sufficiency, and CodeBrigade validates repair intake plus executes narrow assertion-failure guarded repairs, but the broader source-edit adapter still needs to handle more diagnostic classes against repo evidence, verification feedback, scope budgets, and refusal controls.",
        "owner": "CodeBrigade",
    },
    {
        "priority": 2,
        "item": "deepen repository survey beyond shallow generic import edges",
        "reason": "Ceraxia now records path hints, read order, investigation playbook stages, caller candidates, contract surface candidates, Python import edges, source summaries, and local JS/TS relative import edges; cross-language call graphs and package-level dependency evidence are still shallow.",
        "owner": "Ceraxia",
    },
    {
        "priority": 3,
        "item": "match per-surface verification to command output and artifact evidence",
        "reason": "Review now reports per-surface executed, partial, planned, failed, or blocked evidence, tracks work-package statuses, audits worker-output contract sufficiency, and blocks high-risk partial execution; it still does not deeply inspect command stdout/stderr or generated artifacts for surface-specific assertions.",
        "owner": "Ceraxia",
    },
    {
        "priority": 4,
        "item": "split PlanningBrigade roles into callable services after more field trials",
        "reason": "PlanningBrigade now emits problem framing, path hints, assumption registers, investigation playbooks, change-control plans, diagnostic repair plans, acceptance trace matrices with explicit definition-of-done coverage, dependency maps, work breakdown, impact analysis, surface verification matrices, acceptance contracts, CodeBrigade brief blueprints, implementation work packages, and worker-output contracts; role services should wait until these contracts survive more real tasks.",
        "owner": "PlanningBrigade",
    },
]


def component_status(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    missing: list[str] = []
    present: list[str] = []
    for rel in spec["required_files"]:
        path = ROOT / name / rel
        if path.is_file():
            present.append(rel)
        else:
            missing.append(rel)
    return {
        "name": name,
        "kind": spec["kind"],
        "maturity": spec["maturity"],
        "status": "ready" if not missing else "incomplete",
        "present_files": present,
        "missing_files": missing,
    }


def build_status() -> dict[str, Any]:
    components = [component_status(name, spec) for name, spec in COMPONENTS.items()]
    incomplete = [item["name"] for item in components if item["status"] != "ready"]
    architecture_contract = json.loads((ROOT / "architecture_contract.json").read_text(encoding="utf-8"))
    return {
        "ok": not incomplete,
        "kind": "eye_mechanicum_status",
        "root": str(ROOT),
        "architecture_contract": architecture_contract,
        "components": components,
        "incomplete_components": incomplete,
        "roadmap": ROADMAP,
        "next_architecture_step": ROADMAP[0]["item"] if not incomplete else "repair incomplete component contracts",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Report EyeOfTerror/Mechanicum component status.")
    parser.add_argument("--json", action="store_true", help="Print full JSON status.")
    args = parser.parse_args()
    status = build_status()
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        suffix = "ready" if status["ok"] else f"incomplete: {', '.join(status['incomplete_components'])}"
        print(f"[ok] EyeOfTerror Mechanicum status: {suffix}" if status["ok"] else f"[fail] EyeOfTerror Mechanicum status: {suffix}")
    return 0 if status["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
