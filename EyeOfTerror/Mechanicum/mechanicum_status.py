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
            "run_report_self_test.py",
        ],
        "maturity": "controller_with_planning_quality_survey_gate_read_order_assumption_register_investigation_playbook_change_control_acceptance_trace_definition_of_done_trace_audit_caller_contract_package_manifest_evidence_generic_edges_per_surface_verification_counts_work_package_review_worker_output_acceptance_requirement_contract_audit_artifact_manifest_hash_drift_diagnostic_repair_requests_planning_feedback_requests_and_optional_guarded_repair_execution",
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
            "role_service.py",
            "role_service_self_test.py",
            "start_role_services.py",
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
        "maturity": "contracted_planning_department_with_http_ready_role_services_role_quality_gates_assumption_register_investigation_playbook_change_control_acceptance_trace_definition_of_done_trace_surface_output_evidence_implementation_brief_blueprint_field_trial_coverage_code_work_packages_dependency_execution_batches_worker_output_acceptance_requirement_contracts_and_ceraxia_feedback_intake",
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
            "greenfield_project.py",
            "greenfield_project_brief.schema.json",
            "implementation_brief_contract.py",
            "execution_policy.schema.json",
            "execution_result.schema.json",
            "execution_policy.json",
            "verification_adapter.py",
            "verification_policy.json",
            "verification_policy.schema.json",
            "verification_execution.schema.json",
            "focused_self_test.py",
            "self_test.py",
        ],
        "maturity": "handoff_with_read_order_assumption_register_investigation_playbook_change_control_acceptance_trace_worker_output_contract_preflight_explicit_text_ast_guarded_natural_language_test_inferred_literal_patch_adapter_greenfield_project_creation_adapter_package_statuses_allowlisted_verification_focused_contract_smoke_diagnostic_repair_intake_executor_support_classification_guarded_assertion_failed_command_traceback_missing_import_name_error_repair_executor_and_bounded_verified_repair_loop",
    },
}


ROADMAP = [
    {
        "priority": 1,
        "item": "expand CodeBrigade from guarded inference to diagnostic autonomous source edits",
        "reason": "PlanningBrigade now emits diagnostic repair and worker-output contracts, Ceraxia writes diagnostic repair request artifacts and audits worker-output sufficiency, and CodeBrigade validates repair intake, executes narrow guarded repairs, reruns allowlisted verification, records attempt history, and returns replan packets on repeated failed signatures. Broader source-edit adapters still need more diagnostic classes against repo evidence, verification feedback, scope budgets, and refusal controls.",
        "owner": "CodeBrigade",
    },
    {
        "priority": 2,
        "item": "deepen repository survey beyond shallow generic import edges",
        "reason": "Ceraxia now records path hints, read order, investigation playbook stages, caller candidates, contract surface candidates, package manifest candidates, Python import edges, source summaries, local JS/TS relative import edges, Go module import edges rooted in go.mod, Rust mod/crate edges, and a normalized repository_dependency_graph; cross-language call graphs and manifest-to-source impact analysis are still shallow.",
        "owner": "Ceraxia",
    },
    {
        "priority": 3,
        "item": "match per-surface verification to command output and artifact evidence",
        "reason": "Review now reports per-surface executed, partial, planned, failed, or blocked evidence, tracks work-package statuses, audits worker-output contract sufficiency, blocks high-risk partial execution, and run package audit checks saved artifact-manifest hashes; it still does not deeply inspect generated artifacts for surface-specific assertions.",
        "owner": "Ceraxia",
    },
    {
        "priority": 4,
        "item": "wire PlanningBrigade role services into Ceraxia runtime dispatch after more field trials",
        "reason": "PlanningBrigade now exposes HTTP-ready read-only role services for the five planning roles, plus a supervisor manifest/launcher for ports 7111-7115, and keeps the in-process packet builder for compatibility; the next step is runtime selection between in-process and HTTP role dispatch inside Ceraxia.",
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
