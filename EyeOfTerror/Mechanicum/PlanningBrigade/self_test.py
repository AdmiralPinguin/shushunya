#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import planning_brigade
from planning_packet_contract import REQUIRED_PACKET_OBJECTS


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[2]


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


def assert_packet_shape(packet: dict) -> None:
    schema = json.loads((ROOT / "planning_contract.schema.json").read_text(encoding="utf-8"))
    missing = [field for field in schema["required"] if field not in packet]
    if missing:
        raise AssertionError(f"planning packet missing required fields: {missing}")
    schema_required = set(schema["required"])
    schema_properties = schema.get("properties", {}) if isinstance(schema.get("properties"), dict) else {}
    missing_schema_required = [field for field in REQUIRED_PACKET_OBJECTS if field not in schema_required]
    missing_schema_properties = [field for field in REQUIRED_PACKET_OBJECTS if field not in schema_properties]
    if missing_schema_required:
        raise AssertionError(f"planning schema must require validator packet objects: {missing_schema_required}")
    if missing_schema_properties:
        raise AssertionError(f"planning schema must describe validator packet objects: {missing_schema_properties}")
    expected_roles = ["TaskTriage", "RepoSurveyor", "DesignStrategos", "VerificationArchitect", "RiskScribe"]
    if packet.get("roles_completed") != expected_roles:
        raise AssertionError(f"planning packet role order drifted: {packet}")
    if packet.get("contract_version") != "eye-mechanicum.v1":
        raise AssertionError(f"planning packet contract version drifted: {packet}")
    if packet.get("next_action", {}).get("owner") != "Ceraxia":
        raise AssertionError(f"PlanningBrigade must hand authority back to Ceraxia: {packet}")


def assert_role_contracts() -> None:
    contracts = json.loads((ROOT / "role_contracts.json").read_text(encoding="utf-8"))
    service_contracts = json.loads((ROOT / "service_contracts.json").read_text(encoding="utf-8"))
    if contracts.get("contract_version") != "eye-mechanicum.v1":
        raise AssertionError(f"role contract version drifted: {contracts}")
    if service_contracts.get("contract_version") != "eye-mechanicum.v1":
        raise AssertionError(f"service contract version drifted: {service_contracts}")
    roles = contracts.get("roles") if isinstance(contracts.get("roles"), list) else []
    names = [role.get("name") for role in roles if isinstance(role, dict)]
    if names != planning_brigade.ROLE_ORDER:
        raise AssertionError(f"role contracts must follow PlanningBrigade role order: {contracts}")
    services = service_contracts.get("services") if isinstance(service_contracts.get("services"), list) else []
    service_names = [service.get("name") for service in services if isinstance(service, dict)]
    if service_names != names:
        raise AssertionError(f"service contracts must follow role contract order: {service_contracts}")
    service_ports = [int(service.get("port") or 0) for service in services if isinstance(service, dict)]
    if service_ports != list(range(7111, 7116)):
        raise AssertionError(f"planning service ports must be stable reserved 7111-7115: {service_contracts}")
    active_ports = collect_active_ports()
    port_collisions = {port: active_ports[port] for port in service_ports if port in active_ports}
    if port_collisions:
        raise AssertionError(f"planning service ports collide with active registry ports: {port_collisions}")
    if service_contracts.get("port_policy", {}).get("active") is not False:
        raise AssertionError(f"planning service contracts should stay planned until split: {service_contracts}")
    externally_available = {"task", "constraints"}
    produced: set[str] = set(externally_available)
    for role in roles:
        if role.get("may_mutate_source") is not False:
            raise AssertionError(f"PlanningBrigade roles must be read-only: {role}")
        if not role.get("authority") or not role.get("outputs"):
            raise AssertionError(f"role contract must expose authority and outputs: {role}")
        if not isinstance(role.get("quality_gates"), list) or len(role.get("quality_gates", [])) < 3:
            raise AssertionError(f"role contract must expose at least three quality gates: {role}")
        missing_inputs = [item for item in role.get("inputs", []) if item not in produced]
        if missing_inputs:
            raise AssertionError(f"role contract inputs must be produced by earlier roles or external input: role={role['name']} missing={missing_inputs}")
        produced.update(str(item) for item in role.get("outputs", []))
    missing_packet_owners = [key for key in REQUIRED_PACKET_OBJECTS if key not in produced]
    if missing_packet_owners:
        raise AssertionError(f"required planning packet objects must be owned by role outputs: {missing_packet_owners}")
    sample_packet = planning_brigade.build_planning_packet(
        {
            "task": "почини security API migration pytest",
            "repo_path": "/repo",
        }
    )
    for role in roles:
        for output in role["outputs"]:
            if output not in sample_packet:
                raise AssertionError(f"role contract output is absent from planning packet: role={role['name']} output={output}")
    for service in services:
        if service.get("may_mutate_source") is not False:
            raise AssertionError(f"planning service interface must be read-only: {service}")
        matching_role = next(role for role in roles if role["name"] == service["name"])
        if service.get("output_artifacts") != matching_role.get("outputs"):
            raise AssertionError(f"service outputs must match role outputs: service={service} role={matching_role}")
        for output in service.get("output_artifacts", []):
            if output not in sample_packet:
                raise AssertionError(f"service output is absent from planning packet: service={service['name']} output={output}")


def main() -> int:
    assert_role_contracts()
    security_packet = planning_brigade.build_planning_packet(
        {
            "task": "почини security bug: API token можно обойти через path traversal, добавь pytest negative tests",
            "repo_path": "/repo",
        }
    )
    assert_packet_shape(security_packet)
    if (
        security_packet["task_triage"]["risk_level"] != "high"
        or "security" not in security_packet["task_triage"]["task_kinds"]
        or "the original user-visible request is satisfied" not in security_packet["problem_statement"]["definition_of_done"]
        or security_packet["dependency_map"]["critical_path"][-1] != "implementation_brief"
        or "prove_boundary" not in [phase["id"] for phase in security_packet["work_breakdown"]["phases"]]
        or "prove_boundary" not in next(phase for phase in security_packet["work_breakdown"]["phases"] if phase["id"] == "review_result")["depends_on"]
        or security_packet["impact_analysis"]["highest_risk_surface"] != "security_boundary"
        or "security_boundary_is_traceable" not in [item["id"] for item in security_packet["assumption_register"]["assumptions"]]
        or security_packet["investigation_playbook"]["target"] != "CodeBrigade"
        or security_packet["investigation_playbook"]["read_stages"][0]["stage"] != "entrypoints_first"
        or "security_boundary_trace" not in [stage["stage"] for stage in security_packet["investigation_playbook"]["read_stages"]]
        or "verification would be syntax-only for a behavior, security, compatibility, migration, or concurrency task" not in security_packet["investigation_playbook"]["mutation_blockers"]
        or "security_boundary" not in [surface["surface"] for surface in security_packet["impact_analysis"]["surfaces"]]
        or not security_packet["surface_verification_matrix"]["complete"]
        or "security_boundary" not in [row["surface"] for row in security_packet["surface_verification_matrix"]["rows"]]
        or not security_packet["surface_package_matrix"]["complete"]
        or not any(row["surface"] == "security_boundary" and "security_boundary_package" in row["package_ids"] for row in security_packet["surface_package_matrix"]["rows"])
        or security_packet["execution_forecast"]["complexity"] != "high"
        or security_packet["execution_forecast"]["expected_code_brigade_iterations"] < 4
        or security_packet["execution_forecast"]["scope_budget"]["max_source_files_to_edit"] < 4
        or security_packet["execution_forecast"]["scope_budget"]["max_test_files_to_edit_without_explicit_user_request"] != 0
        or security_packet["expert_quality_plan"]["level"] != "expert"
        or security_packet["expert_quality_plan"]["required_for_expert_gate"] is not True
        or "negative boundary evidence proves the bypass is closed" not in security_packet["expert_quality_plan"]["review_checklist"]
        or security_packet["design_options"]["selected_strategy"] != "boundary_first_patch"
        or "untrusted input is rejected" not in security_packet["verification_strategy"]["negative_tests"]
        or not security_packet["verification_strategy"]["broad_verification_required"]
        or "negative boundary test or explicit blocker is present" not in security_packet["quality_bar"]["must_have_evidence"]
        or "required negative tests are present, executed, or explicitly blocked" not in security_packet["acceptance_contract"]["must_prove"]
        or security_packet["implementation_brief_blueprint"]["target"] != "CodeBrigade"
        or "execution preflight passes" not in security_packet["implementation_brief_blueprint"]["mutation_preconditions"]
        or security_packet["implementation_work_packages"]["package_count"] < 4
        or "security_boundary_package" not in security_packet["implementation_work_packages"]["review_order"]
        or not any(package["id"] == "security_boundary_package" and "negative_test_evidence.json or explicit blocker is returned" in package["handoff_criteria"] for package in security_packet["implementation_work_packages"]["packages"])
        or security_packet["planning_review_gate"]["decision"] != "ready_for_ceraxia_review"
        or security_packet["planning_review_gate"]["score"] < 80
        or "prove_negative_boundary" not in [step["step"] for step in security_packet["code_brigade_handoff"]["steps"]]
    ):
        raise AssertionError(f"security planning packet is too weak: {security_packet}")
    selected_security_option = next(option for option in security_packet["design_options"]["options"] if option["name"] == security_packet["design_options"]["selected_strategy"])
    if selected_security_option["decision"] != "prefer":
        raise AssertionError(f"selected security strategy must be preferred: {security_packet}")
    if not any(item["risk"] == "missing_negative_boundary_test" for item in security_packet["risk_register"]["risks"]):
        raise AssertionError(f"risk register must reject missing negative tests: {security_packet}")
    if "test edits are needed but were not explicitly requested by the user" not in security_packet["execution_forecast"]["scope_budget"]["requires_ceraxia_replan_when"]:
        raise AssertionError(f"scope budget must force replan before unrequested test edits: {security_packet}")
    if not security_packet["acceptance_trace_matrix"]["complete"] or not any(
        "negative boundary test" in row["requirement"] and "security_boundary_package" in row["package_ids"]
        for row in security_packet["acceptance_trace_matrix"]["rows"]
    ):
        raise AssertionError(f"security acceptance trace must map boundary evidence to security package: {security_packet}")

    migration_packet = planning_brigade.build_planning_packet(
        {
            "task": "repo-grade migration: сохрани legacy compatibility, API response schema и runtime config",
            "repo_path": "/repo",
        }
    )
    assert_packet_shape(migration_packet)
    if (
        "migration" not in migration_packet["task_triage"]["task_kinds"]
        or "api_compatibility" not in migration_packet["task_triage"]["task_kinds"]
        or "old, new, and mixed records round-trip correctly" not in migration_packet["verification_strategy"]["negative_tests"]
        or "backward compatibility evidence is present" not in migration_packet["quality_bar"]["must_have_evidence"]
        or not any(node["id"] == "compatibility_boundary" for node in migration_packet["dependency_map"]["nodes"])
        or "prove_compatibility" not in [phase["id"] for phase in migration_packet["work_breakdown"]["phases"]]
        or migration_packet["impact_analysis"]["highest_risk_surface"] != "data_compatibility"
        or "runtime_configuration" not in [surface["surface"] for surface in migration_packet["impact_analysis"]["surfaces"]]
        or "compatibility_expectation_is_known" not in [item["id"] for item in migration_packet["assumption_register"]["assumptions"]]
        or not migration_packet["surface_verification_matrix"]["complete"]
        or "data_compatibility" not in [row["surface"] for row in migration_packet["surface_verification_matrix"]["rows"]]
        or not any(row["surface"] == "runtime_configuration" and "runtime_configuration_package" in row["package_ids"] for row in migration_packet["surface_package_matrix"]["rows"])
        or migration_packet["execution_forecast"]["complexity"] != "high"
        or "compatibility_package" not in migration_packet["implementation_work_packages"]["review_order"]
        or "runtime_configuration_package" not in migration_packet["implementation_work_packages"]["review_order"]
        or not any(package["id"] == "compatibility_package" and "Protect old/new public or data shapes across callers, readers, and writers." == package["purpose"] for package in migration_packet["implementation_work_packages"]["packages"])
        or "dependency_critical_path" not in migration_packet["implementation_brief_blueprint"]
        or "investigation_playbook" not in migration_packet["implementation_brief_blueprint"]["required_sections"]
        or "compatibility_shape_trace" not in [stage["stage"] for stage in migration_packet["investigation_playbook"]["read_stages"]]
        or "work_phases" not in migration_packet["implementation_brief_blueprint"]
        or migration_packet["expert_quality_plan"]["level"] != "expert"
        or "strict_new_shape_vs_backward_compatibility" not in [item["decision"] for item in migration_packet["expert_quality_plan"]["tradeoff_register"]]
        or migration_packet["code_brigade_handoff"]["target"] != "CodeBrigade"
        or migration_packet["repo_survey_request"]["read_only"] is not True
    ):
        raise AssertionError(f"migration planning packet is incomplete: {migration_packet}")
    if "acceptance_trace_matrix" not in migration_packet["implementation_brief_blueprint"]["required_sections"]:
        raise AssertionError(f"migration brief blueprint must require acceptance trace matrix: {migration_packet}")
    if not migration_packet["acceptance_trace_matrix"]["complete"] or not any(
        row["requirement"] == "backward compatibility evidence is present" and "compatibility_package" in row["package_ids"]
        for row in migration_packet["acceptance_trace_matrix"]["rows"]
    ):
        raise AssertionError(f"migration acceptance trace must map compatibility evidence to compatibility package: {migration_packet}")
    required_brief_sections = migration_packet["implementation_brief_blueprint"]["required_sections"]
    for section in ["surface_verification_matrix", "survey_quality_gate", "execution_forecast", "expert_quality_plan", "implementation_work_packages", "planning_review_gate"]:
        if section not in required_brief_sections:
            raise AssertionError(f"implementation brief blueprint missing required section {section}: {migration_packet}")

    combined_packet = planning_brigade.build_planning_packet(
        {
            "task": "security API migration: path traversal ломает legacy schema compatibility",
            "repo_path": "/repo",
        }
    )
    review_depends_on = next(phase for phase in combined_packet["work_breakdown"]["phases"] if phase["id"] == "review_result")["depends_on"]
    if sorted(review_depends_on) != ["prove_boundary", "prove_compatibility"]:
        raise AssertionError(f"combined high-risk plan must wait for both proof phases: {combined_packet}")

    refactor_packet = planning_brigade.build_planning_packet(
        {
            "task": "refactor architecture: split planner and executor without changing public endpoint response contracts",
            "repo_path": "/repo",
        }
    )
    if "architecture_refactor_package" not in refactor_packet["implementation_work_packages"]["review_order"]:
        raise AssertionError(f"refactor planning must create architecture work package: {refactor_packet}")

    concurrency_packet = planning_brigade.build_planning_packet(
        {
            "task": "fix async retry race: parallel requests corrupt cache state under timeout",
            "repo_path": "/repo",
        }
    )
    if not any(item["risk"] == "nondeterministic_parallel_state_regression" for item in concurrency_packet["risk_register"]["risks"]):
        raise AssertionError(f"concurrency planning must expose nondeterministic state risk: {concurrency_packet}")
    if "concurrency_runtime_package" not in concurrency_packet["implementation_work_packages"]["review_order"]:
        raise AssertionError(f"concurrency planning must create runtime work package: {concurrency_packet}")
    if "runtime_configuration_package" not in concurrency_packet["implementation_work_packages"]["review_order"]:
        raise AssertionError(f"concurrency config/runtime planning must create config work package: {concurrency_packet}")
    if "deterministic_state_vs_fast_shared_cache" not in [item["decision"] for item in concurrency_packet["expert_quality_plan"]["tradeoff_register"]]:
        raise AssertionError(f"concurrency planning must include expert state tradeoff: {concurrency_packet}")

    unclear_packet = planning_brigade.build_planning_packet({"task": "почини", "repo_path": "/repo"})
    if unclear_packet["planning_review_gate"]["decision"] != "blocked" or not unclear_packet["planning_review_gate"]["blockers"]:
        raise AssertionError(f"unclear task must be blocked by planning review: {unclear_packet}")
    if unclear_packet["planning_review_gate"]["score"] > 40:
        raise AssertionError(f"unclear blocked plan should not keep a near-ready score: {unclear_packet}")

    blocked_review = planning_brigade.planning_review_gate(
        triage={"needs_clarification": False, "risk_level": "medium"},
        problem={"definition_of_done": ["a", "b", "c"]},
        survey={"repo_path": "/repo"},
        dependency={
            "critical_path": [
                "task_contract",
                "repo_evidence",
                "design_decision",
                "verification_contract",
                "implementation_brief",
            ]
        },
        breakdown={"phases": [{"id": str(index)} for index in range(6)]},
        verification={"targeted_commands": ["python -m py_compile app.py"], "negative_tests": []},
        surface_matrix={"complete": False, "blockers": ["no planned verification covers public_api_contract"]},
        acceptance={"must_prove": ["a"]},
    )
    if blocked_review["decision"] != "blocked" or "public_api_contract" not in " ".join(blocked_review["blockers"]):
        raise AssertionError(f"surface matrix blockers must block planning review: {blocked_review}")
    if blocked_review["score"] > 60:
        raise AssertionError(f"blocked planning review should have capped score: {blocked_review}")

    cli = subprocess.run(
        [
            sys.executable,
            str(ROOT / "planning_brigade.py"),
            "--task",
            "почини failing unittest без изменения тестов",
            "--repo-path",
            "/repo",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if cli.returncode != 0:
        raise AssertionError(f"planning CLI failed: {cli.stdout} {cli.stderr}")
    cli_packet = json.loads(cli.stdout)
    assert_packet_shape(cli_packet)
    if "test_repair" not in cli_packet["task_triage"]["task_kinds"] or "rerun failing test command" not in cli_packet["verification_strategy"]["targeted_commands"]:
        raise AssertionError(f"CLI packet should plan failing-test repair: {cli_packet}")
    cli_validated = subprocess.run(
        [
            sys.executable,
            str(ROOT / "planning_brigade.py"),
            "--task",
            "почини failing unittest без изменения тестов",
            "--repo-path",
            "/repo",
            "--validate",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if cli_validated.returncode != 0:
        raise AssertionError(f"valid planning CLI packet should pass validation: {cli_validated.stdout} {cli_validated.stderr}")
    cli_invalid = subprocess.run(
        [
            sys.executable,
            str(ROOT / "planning_brigade.py"),
            "--task",
            "почини",
            "--repo-path",
            "/repo",
            "--validate",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if cli_invalid.returncode != 2 or "validation_problems" not in cli_invalid.stderr:
        raise AssertionError(f"invalid planning CLI packet should fail validation: {cli_invalid.stdout} {cli_invalid.stderr}")

    path_hint_packet = planning_brigade.build_planning_packet(
        {
            "task": "почини `src/app.py` и tests/test_app.py без изменения public API",
            "repo_path": "/repo",
        }
    )
    if path_hint_packet["problem_statement"]["explicit_path_hints"] != ["src/app.py", "tests/test_app.py"]:
        raise AssertionError(f"planning packet should extract explicit path hints: {path_hint_packet}")
    if path_hint_packet["repo_survey_request"]["path_hints"] != ["src/app.py", "tests/test_app.py"]:
        raise AssertionError(f"survey request should preserve path hints: {path_hint_packet}")
    code_literal_packet = planning_brigade.build_planning_packet(
        {
            "task": "В файле `app.py` замени `return False` на `return True`.",
            "repo_path": "/repo",
        }
    )
    if code_literal_packet["problem_statement"]["explicit_path_hints"] != ["app.py"]:
        raise AssertionError(f"planning packet should not treat backtick code literals as paths: {code_literal_packet}")

    structured_packet = planning_brigade.build_planning_packet(
        {
            "task": "добавь feature",
            "repo_path": "/repo",
            "constraints": ["preserve CLI output"],
            "verification_commands": ["python -m pytest tests/test_cli.py"],
        }
    )
    if "preserve CLI output" not in structured_packet["problem_statement"]["known_constraints"]:
        raise AssertionError(f"structured constraints should be preserved: {structured_packet}")
    if "python -m pytest tests/test_cli.py" not in structured_packet["verification_strategy"]["targeted_commands"]:
        raise AssertionError(f"structured verification commands should be preserved: {structured_packet}")
    print("[ok] Ceraxia PlanningBrigade")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
