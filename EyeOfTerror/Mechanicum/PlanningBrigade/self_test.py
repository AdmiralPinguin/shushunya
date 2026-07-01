#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import planning_brigade


ROOT = Path(__file__).resolve().parent


def assert_packet_shape(packet: dict) -> None:
    schema = json.loads((ROOT / "planning_contract.schema.json").read_text(encoding="utf-8"))
    missing = [field for field in schema["required"] if field not in packet]
    if missing:
        raise AssertionError(f"planning packet missing required fields: {missing}")
    expected_roles = ["TaskTriage", "RepoSurveyor", "DesignStrategos", "VerificationArchitect", "RiskScribe"]
    if packet.get("roles_completed") != expected_roles:
        raise AssertionError(f"planning packet role order drifted: {packet}")
    if packet.get("contract_version") != "eye-mechanicum.v1":
        raise AssertionError(f"planning packet contract version drifted: {packet}")
    if packet.get("next_action", {}).get("owner") != "Ceraxia":
        raise AssertionError(f"PlanningBrigade must hand authority back to Ceraxia: {packet}")


def main() -> int:
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
        or "security_boundary" not in [surface["surface"] for surface in security_packet["impact_analysis"]["surfaces"]]
        or not security_packet["surface_verification_matrix"]["complete"]
        or "security_boundary" not in [row["surface"] for row in security_packet["surface_verification_matrix"]["rows"]]
        or security_packet["design_options"]["selected_strategy"] != "boundary_first_patch"
        or "untrusted input is rejected" not in security_packet["verification_strategy"]["negative_tests"]
        or not security_packet["verification_strategy"]["broad_verification_required"]
        or "negative boundary test or explicit blocker is present" not in security_packet["quality_bar"]["must_have_evidence"]
        or "required negative tests are present, executed, or explicitly blocked" not in security_packet["acceptance_contract"]["must_prove"]
        or security_packet["implementation_brief_blueprint"]["target"] != "CodeBrigade"
        or "execution preflight passes" not in security_packet["implementation_brief_blueprint"]["mutation_preconditions"]
        or security_packet["planning_review_gate"]["decision"] != "ready_for_ceraxia_review"
        or security_packet["planning_review_gate"]["score"] < 80
        or "prove_negative_boundary" not in [step["step"] for step in security_packet["code_brigade_handoff"]["steps"]]
    ):
        raise AssertionError(f"security planning packet is too weak: {security_packet}")
    if not any(item["risk"] == "missing_negative_boundary_test" for item in security_packet["risk_register"]["risks"]):
        raise AssertionError(f"risk register must reject missing negative tests: {security_packet}")

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
        or not migration_packet["surface_verification_matrix"]["complete"]
        or "data_compatibility" not in [row["surface"] for row in migration_packet["surface_verification_matrix"]["rows"]]
        or "dependency_critical_path" not in migration_packet["implementation_brief_blueprint"]
        or "work_phases" not in migration_packet["implementation_brief_blueprint"]
        or migration_packet["code_brigade_handoff"]["target"] != "CodeBrigade"
        or migration_packet["repo_survey_request"]["read_only"] is not True
    ):
        raise AssertionError(f"migration planning packet is incomplete: {migration_packet}")

    combined_packet = planning_brigade.build_planning_packet(
        {
            "task": "security API migration: path traversal ломает legacy schema compatibility",
            "repo_path": "/repo",
        }
    )
    review_depends_on = next(phase for phase in combined_packet["work_breakdown"]["phases"] if phase["id"] == "review_result")["depends_on"]
    if sorted(review_depends_on) != ["prove_boundary", "prove_compatibility"]:
        raise AssertionError(f"combined high-risk plan must wait for both proof phases: {combined_packet}")

    unclear_packet = planning_brigade.build_planning_packet({"task": "почини", "repo_path": "/repo"})
    if unclear_packet["planning_review_gate"]["decision"] != "blocked" or not unclear_packet["planning_review_gate"]["blockers"]:
        raise AssertionError(f"unclear task must be blocked by planning review: {unclear_packet}")

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
    print("[ok] Ceraxia PlanningBrigade")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
