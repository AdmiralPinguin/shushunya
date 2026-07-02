#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ceraxia import (
    CeraxiaInput,
    LIFECYCLE,
    allocate_run_dir,
    attach_planning_department_to_brief,
    build_execution_readiness,
    build_implementation_brief,
    build_planning_feedback_request,
    build_repo_survey,
    build_survey_quality_gate,
    audit_run_package,
    review_gate,
    run_ceraxia,
    source_mutation_scope_sufficiency_from_worker,
    validate_planning_packet,
)
from planning_department import build_planning_department_package
import repo_survey as repo_survey_module

import sys

PLANNING_PATH = str(Path(__file__).resolve().parents[1] / "PlanningBrigade")
if PLANNING_PATH not in sys.path:
    sys.path.insert(0, PLANNING_PATH)
CODE_BRIGADE_PATH = str(Path(__file__).resolve().parents[1] / "CodeBrigade")
if CODE_BRIGADE_PATH not in sys.path:
    sys.path.insert(0, CODE_BRIGADE_PATH)

import code_brigade_adapter  # noqa: E402
from planning_brigade import build_planning_packet  # noqa: E402
from planning_feedback_contract import build_planning_feedback_intake  # noqa: E402
from planning_packet_contract import validate_planning_packet  # noqa: E402


class CeraxiaLifecycleTests(unittest.TestCase):
    def test_run_dir_allocator_avoids_same_second_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = "ceraxia-20260701-000000-same-task"
            first_id, first_dir = allocate_run_dir(root, base)
            self.assertEqual(first_id, base)
            first_dir.mkdir(parents=True)
            second_id, second_dir = allocate_run_dir(root, base)
            self.assertEqual(second_id, f"{base}-2")
            self.assertNotEqual(first_dir, second_dir)

    def test_full_dry_run_pipeline_writes_required_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("from util import enabled\n\ndef app():\n    return enabled()\n", encoding="utf-8")
            (repo / "util.py").write_text("def enabled():\n    return True\n", encoding="utf-8")
            (repo / "pkg").mkdir()
            (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
            (repo / "pkg" / "service.py").write_text("from .util import enabled\n\ndef service():\n    return enabled()\n", encoding="utf-8")
            (repo / "pkg" / "util.py").write_text("def enabled():\n    return True\n", encoding="utf-8")
            (repo / "api.ts").write_text("export function api() { return true; }\n", encoding="utf-8")
            (repo / "setup.ts").write_text("export const ready = true;\n", encoding="utf-8")
            (repo / "barrel.ts").write_text("export { api } from './api';\n", encoding="utf-8")
            (repo / "client.ts").write_text("import { api } from './api';\nexport function client() { return api(); }\n", encoding="utf-8")
            (repo / "client.spec.ts").write_text("import './setup';\nimport { client } from './client';\ntest('client', () => client());\n", encoding="utf-8")
            (repo / "package.json").write_text(
                json.dumps({"name": "demo", "dependencies": {"axios": "^1.0.0"}, "devDependencies": {"vitest": "^1.0.0"}, "scripts": {"test": "vitest"}}),
                encoding="utf-8",
            )
            (repo / "pyproject.toml").write_text(
                "[project]\nname = \"demo-py\"\ndependencies = [\"requests\"]\n[project.optional-dependencies]\ntest = [\"pytest\"]\n",
                encoding="utf-8",
            )
            (repo / "test_app.py").write_text(
                "import unittest\nfrom app import app\n\n"
                "class AppTest(unittest.TestCase):\n"
                "    def test_app(self):\n"
                "        self.assertTrue(app())\n",
                encoding="utf-8",
            )
            runs = Path(tmp) / "runs"
            result = run_ceraxia(
                CeraxiaInput(
                    task="почини security bug в `app.py`: token auth можно обойти через path traversal, добавь pytest negative tests для `test_app.py`",
                    repo_path=str(repo),
                    constraints=("preserve mobile API response shape",),
                    verification_commands=("python -m py_compile app.py",),
                    runs_root=runs,
                )
            )
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["package_ok"], result)
            self.assertFalse(result["ready_for_execution"], result)
            self.assertEqual(result["state"], "finalized")
            self.assertEqual(result["lifecycle"], LIFECYCLE)
            run_dir = Path(result["run_dir"])
            expected = [
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
                "artifact_manifest.json",
                "run_audit.json",
            ]
            for name in expected:
                self.assertTrue((run_dir / name).exists(), name)
            packet = json.loads((run_dir / "planning_packet.json").read_text(encoding="utf-8"))
            self.assertIn("preserve mobile API response shape", packet["problem_statement"]["known_constraints"])
            self.assertIn("python -m py_compile app.py", packet["verification_strategy"]["targeted_commands"])
            brief = json.loads((run_dir / "implementation_brief.json").read_text(encoding="utf-8"))
            self.assertEqual(brief["contract_version"], "eye-mechanicum.v1")
            self.assertEqual(brief["target"], "CodeBrigade")
            planning_department = json.loads((run_dir / "planning_department.json").read_text(encoding="utf-8"))
            self.assertEqual(planning_department["kind"], "ceraxia_planning_department_package")
            self.assertEqual(planning_department["status"], "ready_for_code_brigade")
            self.assertGreaterEqual(len(planning_department["roles"]), 5)
            self.assertEqual(planning_department["engineering_rfc"]["status"], "accepted_for_code_brigade_handoff")
            self.assertGreaterEqual(len(planning_department["engineering_rfc"]["design_options"]), 2)
            self.assertTrue(planning_department["engineering_rfc"]["rollback_plan"]["rollback_strategy"])
            self.assertTrue(planning_department["engineering_rfc"]["test_strategy"]["targeted_commands"])
            self.assertEqual(planning_department["multi_pass_repo_investigation"]["status"], "complete")
            self.assertEqual(
                [phase["id"] for phase in planning_department["multi_pass_repo_investigation"]["phases"]],
                [
                    "project_map",
                    "dependency_public_api_map",
                    "test_ci_manifest_map",
                    "targeted_pre_mutation_reads",
                ],
            )
            self.assertTrue(all(phase["required_before_mutation"] for phase in planning_department["multi_pass_repo_investigation"]["phases"]))
            self.assertEqual(planning_department["code_brigade_work_package_handoff"]["status"], "ready")
            self.assertIn("security_boundary_package", [package["id"] for package in planning_department["code_brigade_work_package_handoff"]["packages"]])
            planning_execution_batches = planning_department["code_brigade_work_package_handoff"]["execution_batches"]
            self.assertTrue(planning_execution_batches["complete"])
            self.assertEqual(planning_execution_batches["batches"][0], ["evidence_survey_package"])
            self.assertEqual(planning_execution_batches["batches"][-1], ["verification_evidence_package"])
            self.assertEqual(planning_execution_batches["unresolved_packages"], [])
            self.assertEqual(brief["planning_department"]["status"], "ready_for_code_brigade")
            self.assertEqual(brief["planning_department_handoff"]["target"], "CodeBrigade")
            self.assertEqual(brief["code_brigade_handoff"]["planning_department_package"]["artifact"], "planning_department.json")
            self.assertEqual(brief["risk_level"], "high")
            self.assertIn("hardcoded one-off behavior", brief["forbidden_approaches"])
            self.assertIn("negative boundary test or explicit blocker is present", brief["quality_bar"]["must_have_evidence"])
            self.assertEqual(brief["code_brigade_handoff"]["target"], "CodeBrigade")
            self.assertIn("prove_negative_boundary", [step["step"] for step in brief["code_brigade_handoff"]["steps"]])
            self.assertEqual(brief["planning_review_gate"]["decision"], "ready_for_ceraxia_review")
            self.assertGreaterEqual(brief["planning_review_gate"]["score"], 80)
            self.assertTrue(any(item["id"] == "security_boundary_is_traceable" for item in brief["assumption_register"]["assumptions"]))
            self.assertIn("security_boundary_package", brief["implementation_work_packages"]["review_order"])
            self.assertTrue(any(package["id"] == "security_boundary_package" for package in brief["implementation_work_packages"]["packages"]))
            self.assertGreaterEqual(len(brief["work_breakdown"]["phases"]), 6)
            self.assertEqual(brief["impact_analysis"]["highest_risk_surface"], "security_boundary")
            self.assertTrue(brief["impact_analysis"]["requires_cross_surface_review"])
            self.assertEqual(brief["execution_forecast"]["complexity"], "high")
            self.assertGreaterEqual(brief["execution_forecast"]["expected_code_brigade_iterations"], 4)
            self.assertEqual(brief["expert_quality_plan"]["level"], "expert")
            self.assertTrue(brief["expert_quality_plan"]["required_for_expert_gate"])
            self.assertGreaterEqual(len(brief["expert_quality_plan"]["tradeoff_register"]), 3)
            self.assertGreaterEqual(len(brief["expert_quality_plan"]["review_checklist"]), 5)
            self.assertEqual(brief["investigation_playbook"]["target"], "CodeBrigade")
            self.assertEqual(brief["investigation_playbook"]["read_stages"][0]["stage"], "entrypoints_first")
            self.assertIn("security_boundary_trace", [stage["stage"] for stage in brief["investigation_playbook"]["read_stages"]])
            self.assertEqual(brief["change_control_plan"]["target"], "CodeBrigade")
            self.assertIn("negative security boundary remains closed for bypass inputs", brief["change_control_plan"]["protected_invariants"])
            self.assertIn("negative boundary evidence is executed or blocked with a concrete reason", brief["change_control_plan"]["post_change_proofs"])
            self.assertTrue(brief["acceptance_trace_matrix"]["complete"])
            self.assertTrue(any("security_boundary_package" in row["package_ids"] for row in brief["acceptance_trace_matrix"]["rows"]))
            self.assertTrue(brief["constraint_trace_matrix"]["complete"])
            self.assertTrue(any("verification_evidence_package" in row["package_ids"] for row in brief["constraint_trace_matrix"]["rows"]))
            self.assertTrue(any(item["path"] == "client.ts" and item["language"] == "typescript" for item in brief["repo_survey_evidence"]["source_summaries"]))
            self.assertTrue(brief["surface_verification_matrix"]["complete"])
            self.assertTrue(any(row["surface"] == "security_boundary" for row in brief["surface_verification_matrix"]["rows"]))
            security_surface_row = next(row for row in brief["surface_verification_matrix"]["rows"] if row["surface"] == "security_boundary")
            self.assertIn("negative boundary output or explicit blocker is linked to this surface", security_surface_row["output_evidence_required"])
            self.assertTrue(brief["surface_package_matrix"]["complete"])
            self.assertTrue(any(row["surface"] == "security_boundary" and "security_boundary_package" in row["package_ids"] for row in brief["surface_package_matrix"]["rows"]))
            self.assertEqual(brief["diagnostic_repair_plan"]["target"], "CodeBrigade")
            self.assertEqual(brief["diagnostic_repair_plan"]["max_repair_attempts"], 3)
            self.assertIn("traceback_files", brief["diagnostic_repair_plan"]["read_before_repair"])
            self.assertTrue(any("same verification failure repeats" in item for item in brief["diagnostic_repair_plan"]["stop_conditions"]))
            self.assertEqual(brief["worker_output_contract"]["target"], "CodeBrigade")
            self.assertIn("worker_report.json", brief["worker_output_contract"]["required_reports"])
            self.assertEqual(sorted(brief["worker_output_contract"]["required_package_statuses"]), sorted(brief["implementation_work_packages"]["review_order"]))
            self.assertEqual(brief["code_brigade_handoff"]["worker_output_contract"], brief["worker_output_contract"])
            self.assertEqual(brief["survey_quality_gate"]["decision"], "passed")
            self.assertIn("app.py", brief["repo_survey_evidence"]["candidate_files"])
            self.assertEqual(brief["repo_survey_evidence"]["existing_path_hints"], ["app.py", "test_app.py"])
            self.assertEqual(brief["repo_survey_evidence"]["missing_path_hints"], [])
            self.assertEqual(brief["repo_survey_evidence"]["unsafe_path_hints"], [])
            self.assertFalse(brief["repo_survey_evidence"]["survey_truncated"])
            self.assertFalse(brief["repo_survey_evidence"]["python_symbols_truncated"])
            self.assertTrue(any(edge["source"] == "app.py" and edge["target"] == "util.py" for edge in brief["repo_survey_evidence"]["local_import_edges"]))
            self.assertTrue(any(edge["source"] == "pkg/service.py" and edge["target"] == "pkg/util.py" for edge in brief["repo_survey_evidence"]["local_import_edges"]))
            self.assertIn("test_app.py", brief["repo_survey_evidence"]["reverse_dependency_index"]["app.py"])
            self.assertTrue(any(link["test"] == "test_app.py" and link["target"] == "app.py" for link in brief["repo_survey_evidence"]["test_coverage_links"]))
            self.assertTrue(any(row["target"] == "app.py" and "test_app.py" in row["callers"] for row in brief["repo_survey_evidence"]["caller_candidates"]))
            self.assertTrue(any(row["path"] == "api.ts" for row in brief["repo_survey_evidence"]["contract_surface_candidates"]))
            self.assertTrue(any(row["path"] == "package.json" and row["ecosystem"] == "node" and row["dependency_count"] == 1 for row in brief["repo_survey_evidence"]["package_manifest_candidates"]))
            self.assertTrue(any(row["path"] == "pyproject.toml" and row["ecosystem"] == "python" and row["dependency_count"] == 1 for row in brief["repo_survey_evidence"]["package_manifest_candidates"]))
            self.assertTrue(any(command.startswith("python -m pytest test_app.py") for command in brief["suggested_verification_commands"]))
            verification = json.loads((run_dir / "verification_report.json").read_text(encoding="utf-8"))
            self.assertIn("untrusted input is rejected", verification["negative_tests_required"])
            self.assertTrue(any(command.startswith("python -m pytest test_app.py") for command in verification["commands_planned"]))
            worker_report = json.loads((run_dir / "worker_report.json").read_text(encoding="utf-8"))
            self.assertEqual(worker_report["execution_policy_status"], "blocked_until_adapter_is_wired")
            implementation_plan = worker_report["implementation_plan"]
            self.assertIn("app.py", implementation_plan["target_files_to_inspect"])
            self.assertIn("test_app.py", implementation_plan["test_files_to_preserve"])
            self.assertEqual(implementation_plan["existing_path_hints"], ["app.py", "test_app.py"])
            self.assertEqual(implementation_plan["recommended_read_order"][0]["path"], "app.py")
            self.assertEqual(implementation_plan["recommended_read_order"][1]["path"], "test_app.py")
            self.assertEqual(implementation_plan["highest_risk_surface"], "security_boundary")
            self.assertTrue(implementation_plan["requires_cross_surface_review"])
            self.assertEqual(implementation_plan["execution_complexity"], "high")
            self.assertGreaterEqual(implementation_plan["expected_code_brigade_iterations"], 4)
            self.assertEqual(implementation_plan["diagnostic_repair_plan"], brief["diagnostic_repair_plan"])
            self.assertEqual(implementation_plan["worker_output_contract"], brief["worker_output_contract"])
            self.assertEqual(implementation_plan["planning_department_status"], "ready_for_code_brigade")
            self.assertEqual(implementation_plan["engineering_rfc_status"], "accepted_for_code_brigade_handoff")
            self.assertEqual(implementation_plan["multi_pass_investigation_status"], "complete")

            self.assertEqual(len(implementation_plan["multi_pass_investigation_phases"]), 4)
            self.assertEqual(implementation_plan["planning_department_work_package_handoff"]["status"], "ready")
            self.assertEqual(implementation_plan["expert_quality_level"], "expert")
            self.assertTrue(implementation_plan["expert_quality_required"])
            self.assertTrue(any(item["decision"] == "boundary_patch_vs_feature_shortcut" for item in implementation_plan["expert_tradeoff_register"]))
            self.assertTrue(any("negative boundary evidence" in item for item in implementation_plan["expert_review_checklist"]))
            self.assertEqual(implementation_plan["investigation_read_stages"][0]["stage"], "entrypoints_first")
            self.assertIn("Which callers, entrypoints, or schemas could break if the patch is too narrow?", implementation_plan["investigation_evidence_questions"])
            self.assertIn("public caller or test surface is unknown for medium/high risk work", implementation_plan["investigation_mutation_blockers"])
            self.assertIn("negative security boundary remains closed for bypass inputs", implementation_plan["change_protected_invariants"])
            self.assertIn("rollback trigger is known before source mutation", implementation_plan["change_mutation_requires"])
            self.assertIn("negative boundary evidence is executed or blocked with a concrete reason", implementation_plan["change_post_change_proofs"])
            self.assertTrue(implementation_plan["acceptance_trace_complete"])
            self.assertTrue(implementation_plan["definition_of_done_trace_complete"])
            self.assertEqual(implementation_plan["definition_of_done_count"], implementation_plan["traced_definition_of_done_count"])
            self.assertEqual(implementation_plan["missing_definition_of_done"], [])
            self.assertTrue(any("security_boundary_package" in row["package_ids"] for row in implementation_plan["acceptance_trace_rows"]))
            self.assertTrue(implementation_plan["constraint_trace_complete"])
            self.assertTrue(any("verification_evidence_package" in row["package_ids"] for row in implementation_plan["constraint_trace_rows"]))
            self.assertIn("security_boundary_package", implementation_plan["work_package_review_order"])
            self.assertTrue(any(package["id"] == "security_boundary_package" for package in implementation_plan["implementation_work_packages"]))
            self.assertIn("final report answers the original task rather than only package-local success", implementation_plan["work_package_handoff_criteria"])
            self.assertTrue(any(item["path"] == "client.ts" and "client" in item["symbols"] for item in implementation_plan["source_summaries_to_consider"]))
            self.assertTrue(implementation_plan["surface_verification_complete"])
            self.assertTrue(any(row["surface"] == "security_boundary" for row in implementation_plan["surface_verification_rows"]))
            implementation_security_surface = next(row for row in implementation_plan["surface_verification_rows"] if row["surface"] == "security_boundary")
            self.assertIn("negative boundary output or explicit blocker is linked to this surface", implementation_security_surface["output_evidence_required"])
            self.assertEqual(implementation_plan["survey_quality_decision"], "passed")
            self.assertTrue(any(item["id"] == "security_boundary_is_traceable" for item in implementation_plan["assumption_rows"]))
            self.assertIn("negative boundary work package blocks handoff", implementation_plan["assumption_replan_triggers"])
            self.assertTrue(any(edge["source"] == "app.py" and edge["target"] == "util.py" for edge in implementation_plan["dependency_edges_to_check"]))
            self.assertTrue(any(edge["source"] == "pkg/service.py" and edge["target"] == "pkg/util.py" for edge in implementation_plan["dependency_edges_to_check"]))
            self.assertTrue(any(edge["source"] == "client.ts" and edge["target"] == "api.ts" for edge in implementation_plan["dependency_edges_to_check"]))
            self.assertTrue(any(edge["source"] == "barrel.ts" and edge["target"] == "api.ts" for edge in implementation_plan["dependency_edges_to_check"]))
            self.assertTrue(any(edge["source"] == "client.spec.ts" and edge["target"] == "setup.ts" for edge in implementation_plan["dependency_edges_to_check"]))
            self.assertTrue(any(edge["source"] == "client.ts" and edge["target"] == "api.ts" for edge in implementation_plan["generic_dependency_edges_to_check"]))
            self.assertTrue(any(edge["source"] == "barrel.ts" and edge["target"] == "api.ts" for edge in implementation_plan["generic_dependency_edges_to_check"]))
            self.assertTrue(any(edge["source"] == "client.spec.ts" and edge["target"] == "setup.ts" for edge in implementation_plan["generic_dependency_edges_to_check"]))
            self.assertIn("test_app.py", implementation_plan["reverse_dependency_index"]["app.py"])
            self.assertTrue(any(link["test"] == "test_app.py" and link["target"] == "app.py" for link in implementation_plan["test_coverage_links"]))
            self.assertTrue(any(row["target"] == "app.py" and "test_app.py" in row["callers"] for row in implementation_plan["caller_candidates"]))
            self.assertTrue(any(row["path"] == "api.ts" for row in implementation_plan["contract_surface_candidates"]))
            self.assertTrue(any(row["path"] == "package.json" for row in implementation_plan["package_manifest_candidates"]))
            self.assertEqual(implementation_plan["repository_cartography"]["kind"], "ceraxia_repository_cartography")
            self.assertIn("app.py", implementation_plan["repository_cartography"]["entrypoints"])
            self.assertTrue(any(row["path"] == "api.ts" for row in implementation_plan["repository_cartography"]["contract_surfaces"]))
            self.assertTrue(any(row["path"] == "app.py" for row in implementation_plan["repository_cartography"]["risky_modules"]))
            self.assertFalse(implementation_plan["survey_truncated"])
            self.assertFalse(implementation_plan["python_symbols_truncated"])
            self.assertTrue(any(command.startswith("python -m pytest test_app.py") for command in implementation_plan["verification_commands"]))
            survey = json.loads((run_dir / "repo_survey.json").read_text(encoding="utf-8"))
            self.assertEqual(survey["status"], "surveyed")
            self.assertIn("app.py", survey["candidate_files"])
            self.assertNotIn("test_app.py", survey["candidate_files"])
            self.assertNotIn("client.spec.ts", survey["candidate_files"])
            self.assertIn("test_app.py", survey["test_files"])
            self.assertIn("client.spec.ts", survey["test_files"])
            self.assertEqual(survey["existing_path_hints"], ["app.py", "test_app.py"])
            self.assertEqual(survey["recommended_read_order"][0]["path"], "app.py")
            self.assertEqual(survey["recommended_read_order"][1]["path"], "test_app.py")
            self.assertFalse(survey["truncated"])
            self.assertFalse(survey["python_symbols_truncated"])
            self.assertTrue(any(edge["source"] == "app.py" and edge["target"] == "util.py" for edge in survey["local_import_edges"]))
            self.assertTrue(any(edge["source"] == "pkg/service.py" and edge["target"] == "pkg/util.py" for edge in survey["local_import_edges"]))
            self.assertTrue(any(edge["source"] == "client.ts" and edge["target"] == "api.ts" for edge in survey["local_import_edges"]))
            self.assertTrue(any(edge["source"] == "barrel.ts" and edge["target"] == "api.ts" for edge in survey["local_import_edges"]))
            self.assertTrue(any(edge["source"] == "client.spec.ts" and edge["target"] == "setup.ts" for edge in survey["local_import_edges"]))
            self.assertTrue(any(edge["source"] == "client.ts" and edge["target"] == "api.ts" for edge in survey["generic_import_edges"]))
            self.assertTrue(any(edge["source"] == "barrel.ts" and edge["target"] == "api.ts" for edge in survey["generic_import_edges"]))
            self.assertTrue(any(edge["source"] == "client.spec.ts" and edge["target"] == "setup.ts" for edge in survey["generic_import_edges"]))
            self.assertIn("test_app.py", survey["reverse_dependency_index"]["app.py"])
            self.assertTrue(any(link["test"] == "test_app.py" and link["target"] == "app.py" for link in survey["test_coverage_links"]))
            self.assertTrue(any(row["target"] == "app.py" and "test_app.py" in row["callers"] for row in survey["caller_candidates"]))
            self.assertTrue(any(row["path"] == "api.ts" for row in survey["contract_surface_candidates"]))
            self.assertTrue(any(row["path"] == "package.json" and row["script_count"] == 1 for row in survey["package_manifest_candidates"]))
            self.assertTrue(any(row["path"] == "pyproject.toml" and row["dev_dependency_count"] == 1 for row in survey["package_manifest_candidates"]))
            cartography = survey["repository_cartography"]
            self.assertEqual(cartography["kind"], "ceraxia_repository_cartography")
            self.assertIn("app.py", cartography["entrypoints"])
            self.assertIn("test_app.py", cartography["test_inventory"])
            self.assertTrue(any(row["path"] == "api.ts" for row in cartography["contract_surfaces"]))
            self.assertTrue(any(row["path"] == "package.json" for row in cartography["package_manifests"]))
            self.assertTrue(any(row["path"] == "app.py" and "entrypoint" in row["reasons"] for row in cartography["risky_modules"]))
            self.assertGreaterEqual(cartography["summary"]["risky_module_count"], 1)
            app_symbols = next(item for item in survey["python_symbols"] if item["path"] == "app.py")
            self.assertIn("app", app_symbols["functions"])
            client_summary = next(item for item in survey["source_summaries"] if item["path"] == "client.ts")
            self.assertEqual(client_summary["language"], "typescript")
            self.assertIn("client", client_summary["symbols"])
            audit = json.loads((run_dir / "run_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["decision"], "passed")
            self.assertTrue(audit["manifest_complete"])
            review = json.loads((run_dir / "review_gate.json").read_text(encoding="utf-8"))
            self.assertTrue(any("broad verification is planned" in item["finding"] for item in review["warnings"]))
            self.assertTrue(any("high-risk task has no executed verification evidence yet" in item["finding"] for item in review["warnings"]))
            self.assertTrue(any("work packages are planned but not implemented" in item["finding"] for item in review["warnings"]))
            self.assertEqual(review["verification_sufficiency"]["status"], "planned_only")
            self.assertEqual(review["surface_verification_sufficiency"]["status"], "planned_only")
            self.assertGreaterEqual(review["package_status_sufficiency"]["status_counts"]["planned"], 1)
            self.assertEqual(review["planning_department_sufficiency"]["status"], "complete")
            self.assertEqual(review["planning_department_sufficiency"]["phase_count"], 4)
            self.assertGreaterEqual(review["planning_department_sufficiency"]["role_count"], 5)
            self.assertGreaterEqual(review["surface_package_sufficiency"]["surface_count"], 1)
            self.assertFalse(review["surface_package_sufficiency"]["missing_status_package_ids"])
            self.assertGreaterEqual(review["surface_verification_sufficiency"]["surface_count"], 1)
            self.assertGreaterEqual(review["verification_sufficiency"]["commands_planned_count"], 1)
            repair_request = json.loads((run_dir / "diagnostic_repair_request.json").read_text(encoding="utf-8"))
            self.assertEqual(repair_request["target"], "CodeBrigade")
            self.assertEqual(repair_request["status"], "not_required")
            self.assertEqual(repair_request["diagnostic_repair_queue"], review["diagnostic_repair_queue"])
            self.assertEqual(
                repair_request["suggested_code_brigade_command"],
                [
                    "python3",
                    "EyeOfTerror/Mechanicum/CodeBrigade/diagnostic_repair_contract.py",
                    "--execute",
                    "diagnostic_repair_request.json",
                ],
            )
            planning_feedback = json.loads((run_dir / "planning_feedback_request.json").read_text(encoding="utf-8"))
            self.assertEqual(planning_feedback["kind"], "ceraxia_planning_feedback_request")
            self.assertEqual(planning_feedback["target"], "PlanningBrigade")
            self.assertEqual(planning_feedback["status"], "not_required")
            self.assertEqual(planning_feedback["source"], "Ceraxia.review_gate")
            self.assertEqual(planning_feedback["feedback_findings"], [])
            self.assertIn("implementation_brief.json", planning_feedback["required_return_artifacts"])
            self.assertIn("planning_department.json", planning_feedback["required_return_artifacts"])
            readiness = json.loads((run_dir / "execution_readiness.json").read_text(encoding="utf-8"))
            self.assertEqual(readiness["decision"], "blocked")
            self.assertIn("dry run requested; real CodeBrigade execution was intentionally skipped", readiness["blockers"])
            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            summary_schema = json.loads((Path(__file__).resolve().parent / "contracts" / "run_summary.schema.json").read_text(encoding="utf-8"))
            missing_summary_fields = [field for field in summary_schema["required"] if field not in summary]
            self.assertEqual(missing_summary_fields, [])
            self.assertEqual(summary["contract_version"], "eye-mechanicum.v1")
            self.assertEqual(summary["execution_readiness"], "blocked")
            self.assertTrue(summary["package_ok"])
            self.assertTrue(summary["package_lifecycle_finalized"])
            self.assertEqual(summary["package_audit_decision"], "passed")
            self.assertFalse(summary["ready_for_execution"])
            self.assertEqual(summary["review_decision"], "dry_run_ready")
            self.assertEqual(summary["planning_review_decision"], "ready_for_ceraxia_review")
            self.assertGreaterEqual(summary["planning_review_score"], 80)
            self.assertEqual(summary["planning_department_status"], "ready_for_code_brigade")
            self.assertEqual(summary["engineering_rfc_status"], "accepted_for_code_brigade_handoff")
            self.assertGreaterEqual(summary["engineering_rfc_design_option_count"], 2)
            self.assertEqual(summary["multi_pass_investigation_status"], "complete")
            self.assertEqual(summary["multi_pass_investigation_phase_count"], 4)
            self.assertEqual(summary["code_brigade_work_package_handoff_status"], "ready")
            self.assertEqual(summary["planning_department_review_status"], "complete")
            self.assertGreaterEqual(summary["planning_department_review_role_count"], 5)
            self.assertEqual(summary["planning_department_review_phase_count"], 4)
            self.assertGreaterEqual(summary["planning_department_review_package_count"], 1)
            self.assertGreaterEqual(summary["planning_work_phase_count"], 6)
            self.assertEqual(summary["survey_quality_decision"], "passed")
            self.assertEqual(summary["survey_quality_warning_count"], 0)
            self.assertEqual(summary["surface_verification_status"], "planned_only")
            self.assertGreaterEqual(summary["surface_verification_surface_count"], 1)
            self.assertEqual(summary["surface_verification_status_counts"], review["surface_verification_sufficiency"]["status_counts"])
            self.assertEqual(summary["surface_verification_executed_count"], 0)
            self.assertEqual(summary["surface_verification_partial_count"], 0)
            self.assertEqual(summary["investigation_playbook_status"], "complete")
            self.assertGreaterEqual(summary["investigation_read_stage_count"], 5)
            self.assertGreaterEqual(summary["investigation_evidence_question_count"], 4)
            self.assertGreaterEqual(summary["investigation_mutation_blocker_count"], 3)
            self.assertGreaterEqual(summary["investigation_replan_trigger_count"], 3)
            self.assertEqual(summary["change_control_status"], "complete")
            self.assertGreaterEqual(summary["change_control_protected_invariant_count"], 4)
            self.assertGreaterEqual(summary["change_control_post_change_proof_count"], 4)
            self.assertEqual(summary["acceptance_trace_status"], "complete")
            self.assertGreaterEqual(summary["acceptance_trace_row_count"], 4)
            self.assertEqual(summary["acceptance_trace_blocked_row_count"], 0)
            self.assertTrue(summary["definition_of_done_trace_complete"])
            self.assertGreaterEqual(summary["definition_of_done_count"], 3)
            self.assertEqual(summary["definition_of_done_count"], summary["traced_definition_of_done_count"])
            self.assertEqual(summary["missing_definition_of_done_count"], 0)
            self.assertEqual(summary["constraint_trace_status"], "complete")
            self.assertGreaterEqual(summary["constraint_trace_row_count"], 3)
            self.assertEqual(summary["constraint_trace_blocked_row_count"], 0)
            self.assertEqual(summary["assumption_register_status"], "complete")
            self.assertGreaterEqual(summary["assumption_count"], 4)
            self.assertGreaterEqual(summary["assumption_replan_trigger_count"], 4)
            self.assertEqual(summary["worker_output_contract_status"], "complete")
            self.assertEqual(summary["worker_output_required_package_count"], summary["implementation_work_package_count"])
            self.assertEqual(summary["worker_output_reported_package_count"], summary["implementation_work_package_count"])
            self.assertEqual(summary["worker_output_contract_row_count"], summary["implementation_work_package_count"])
            self.assertEqual(summary["worker_output_acceptance_requirement_row_count"], summary["implementation_work_package_count"])
            self.assertEqual(summary["planning_feedback_request_status"], "not_required")
            self.assertEqual(summary["planning_feedback_finding_count"], 0)
            self.assertEqual(summary["worker_status"], "dry_run_handoff_ready")
            self.assertEqual(summary["code_brigade_execution_policy_status"], "blocked_until_adapter_is_wired")
            self.assertEqual(summary["code_brigade_autonomous_execution_request_status"], "required")
            self.assertEqual(summary["code_brigade_execution_result_status"], "")
            self.assertIsNone(summary["code_brigade_execution_preflight_ok"])
            self.assertEqual(summary["expert_quality_level"], "expert")
            self.assertTrue(summary["expert_quality_required"])
            self.assertGreaterEqual(summary["expert_tradeoff_count"], 3)
            self.assertGreaterEqual(summary["expert_review_checklist_count"], 5)
            self.assertGreaterEqual(summary["scope_budget_max_source_files_to_edit"], 1)
            self.assertEqual(summary["scope_budget_max_unrequested_test_files_to_edit"], 0)
            self.assertGreaterEqual(summary["scope_budget_replan_trigger_count"], 1)
            self.assertIn("security", summary["task_kinds"])
            self.assertIn("security_boundary_package", summary["implementation_work_package_review_order"])
            self.assertGreaterEqual(summary["implementation_work_package_count"], 1)
            self.assertGreaterEqual(summary["implementation_work_package_surface_count"], 1)
            self.assertTrue(summary["implementation_work_package_dependency_graph_complete"])
            self.assertGreaterEqual(summary["implementation_work_package_dependency_row_count"], summary["implementation_work_package_count"])
            self.assertGreaterEqual(summary["implementation_work_package_dependency_root_count"], 1)
            self.assertGreaterEqual(summary["implementation_work_package_dependency_terminal_count"], 1)
            self.assertGreaterEqual(summary["work_package_status_counts"]["planned"], 1)
            self.assertEqual(summary["work_package_status_counts"]["implemented"], 0)
            self.assertEqual(summary["engineering_memory_status"], "recorded")
            self.assertEqual(summary["engineering_memory_failure_pattern_count"], 0)
            self.assertGreaterEqual(summary["engineering_memory_reusable_pattern_count"], 3)
            self.assertGreaterEqual(summary["engineering_memory_false_success_guard_count"], 4)
            self.assertGreaterEqual(summary["engineering_memory_reuse_plan_count"], 1)
            self.assertGreaterEqual(summary["engineering_memory_dangerous_module_count"], 1)
            self.assertGreaterEqual(summary["evidence"]["required_count"], 1)
            self.assertGreaterEqual(summary["evidence"]["planned_count"], 1)
            engineering_memory = json.loads((run_dir / "engineering_memory_update.json").read_text(encoding="utf-8"))
            self.assertEqual(engineering_memory["kind"], "ceraxia_engineering_memory_update")
            self.assertEqual(engineering_memory["status"], "recorded")
            self.assertIn("security", engineering_memory["task_kinds"])
            self.assertIn("boundary", " ".join(engineering_memory["mandatory_checks_by_task_kind"]["security"]))
            self.assertIn("review_gate.decision", " ".join(engineering_memory["false_success_guards"]))
            self.assertTrue(any(row["task_kind"] == "security" for row in engineering_memory["reuse_plan"]))
            self.assertTrue(any("negative boundary" in " ".join(row["mandatory_checks"]) for row in engineering_memory["reuse_plan"]))
            self.assertIn("app.py", engineering_memory["dangerous_modules"])
            evidence_matrix = json.loads((run_dir / "evidence_matrix.json").read_text(encoding="utf-8"))
            evidence_schema = json.loads((Path(__file__).resolve().parent / "contracts" / "evidence_matrix.schema.json").read_text(encoding="utf-8"))
            missing_evidence_fields = [field for field in evidence_schema["required"] if field not in evidence_matrix]
            self.assertEqual(missing_evidence_fields, [])
            self.assertEqual(evidence_matrix["kind"], "ceraxia_evidence_matrix")
            self.assertGreaterEqual(evidence_matrix["required_evidence_count"], 1)
            self.assertTrue(any(row["requirement"] == "candidate files are chosen from repository evidence" for row in evidence_matrix["rows"]))
            self.assertIn("app.py", evidence_matrix["implementation_plan_sources"]["target_files_to_inspect"])
            self.assertEqual(evidence_matrix["implementation_plan_sources"]["recommended_read_order"][0]["path"], "app.py")
            self.assertEqual(evidence_matrix["implementation_plan_sources"]["investigation_read_stages"][0]["stage"], "entrypoints_first")
            self.assertIn("public caller or test surface is unknown for medium/high risk work", evidence_matrix["implementation_plan_sources"]["investigation_mutation_blockers"])
            self.assertIn("negative security boundary remains closed for bypass inputs", evidence_matrix["implementation_plan_sources"]["change_protected_invariants"])
            self.assertIn("negative boundary evidence is executed or blocked with a concrete reason", evidence_matrix["implementation_plan_sources"]["change_post_change_proofs"])
            self.assertTrue(evidence_matrix["implementation_plan_sources"]["acceptance_trace_complete"])
            self.assertTrue(evidence_matrix["implementation_plan_sources"]["definition_of_done_trace_complete"])
            self.assertEqual(
                evidence_matrix["implementation_plan_sources"]["definition_of_done_count"],
                evidence_matrix["implementation_plan_sources"]["traced_definition_of_done_count"],
            )
            self.assertEqual(evidence_matrix["implementation_plan_sources"]["missing_definition_of_done"], [])
            self.assertTrue(any("security_boundary_package" in row["package_ids"] for row in evidence_matrix["implementation_plan_sources"]["acceptance_trace_rows"]))
            self.assertTrue(evidence_matrix["implementation_plan_sources"]["constraint_trace_complete"])
            self.assertTrue(any("verification_evidence_package" in row["package_ids"] for row in evidence_matrix["implementation_plan_sources"]["constraint_trace_rows"]))
            self.assertTrue(any(item["id"] == "security_boundary_is_traceable" for item in evidence_matrix["implementation_plan_sources"]["assumption_rows"]))
            self.assertEqual(evidence_matrix["implementation_plan_sources"]["scope_budget"]["max_test_files_to_edit_without_explicit_user_request"], 0)
            self.assertIn("test_app.py", evidence_matrix["implementation_plan_sources"]["reverse_dependency_index"]["app.py"])
            self.assertTrue(any(link["test"] == "test_app.py" and link["target"] == "app.py" for link in evidence_matrix["implementation_plan_sources"]["test_coverage_links"]))
            self.assertTrue(any(row["target"] == "app.py" and "test_app.py" in row["callers"] for row in evidence_matrix["implementation_plan_sources"]["caller_candidates"]))
            self.assertTrue(any(row["path"] == "api.ts" for row in evidence_matrix["implementation_plan_sources"]["contract_surface_candidates"]))
            self.assertTrue(any(row["path"] == "package.json" for row in evidence_matrix["implementation_plan_sources"]["package_manifest_candidates"]))
            self.assertEqual(evidence_matrix["implementation_plan_sources"]["repository_cartography"]["kind"], "ceraxia_repository_cartography")
            self.assertEqual(evidence_matrix["autonomous_execution_request"]["status"], "required")
            self.assertEqual(evidence_matrix["autonomous_execution_request"]["target_adapter"], "autonomous CodeBrigade source-edit adapter")
            self.assertIn("security_boundary_package", evidence_matrix["implementation_work_package_summary"]["review_order"])
            self.assertTrue(evidence_matrix["implementation_work_package_summary"]["dependency_graph"]["complete"])
            self.assertIn("verification_evidence_package", evidence_matrix["implementation_work_package_summary"]["dependency_graph"]["terminal_packages"])
            self.assertEqual(evidence_matrix["implementation_work_package_summary"]["dependency_graph"]["execution_batches"][0], ["evidence_survey_package"])
            self.assertEqual(evidence_matrix["implementation_work_package_summary"]["dependency_graph"]["execution_batches"][-1], ["verification_evidence_package"])
            self.assertIn("security_boundary", evidence_matrix["implementation_work_package_summary"]["covered_surfaces"])
            self.assertEqual(summary["implementation_work_package_count"], evidence_matrix["implementation_work_package_summary"]["package_count"])
            self.assertEqual(summary["implementation_work_package_surface_count"], evidence_matrix["implementation_work_package_summary"]["covered_surface_count"])
            self.assertEqual(summary["work_package_status_counts"], evidence_matrix["implementation_work_package_summary"]["status_counts"])
            self.assertEqual(evidence_matrix["expert_quality_summary"]["level"], "expert")
            self.assertTrue(evidence_matrix["expert_quality_summary"]["required_for_expert_gate"])
            self.assertEqual(summary["expert_tradeoff_count"], evidence_matrix["expert_quality_summary"]["tradeoff_count"])
            self.assertEqual(summary["expert_review_checklist_count"], evidence_matrix["expert_quality_summary"]["review_checklist_count"])
            self.assertTrue(all(item["status"] == "planned" for item in evidence_matrix["implementation_work_package_summary"]["statuses"]))
            self.assertTrue(any(row["surface"] == "security_boundary" and "security_boundary_package" in row["package_ids"] for row in evidence_matrix["surface_package_summary"]["rows"]))
            final_report = (run_dir / "final_report.md").read_text(encoding="utf-8")
            self.assertIn("Execution readiness: blocked", final_report)
            self.assertIn("Investigation playbook status: complete", final_report)
            self.assertIn("Investigation read stages:", final_report)
            self.assertIn("Change control status: complete", final_report)
            self.assertIn("Protected invariants:", final_report)
            self.assertIn("Acceptance trace status: complete", final_report)
            self.assertIn("Acceptance trace rows:", final_report)
            self.assertIn("Constraint trace status: complete", final_report)
            self.assertIn("Constraint trace rows:", final_report)
            self.assertIn("Assumption register status: complete", final_report)
            self.assertIn("Assumptions tracked:", final_report)
            self.assertIn("- planning_department.json", final_report)
            self.assertIn("- evidence_matrix.json", final_report)
            self.assertIn("- engineering_memory_update.json", final_report)
            self.assertIn("BLOCKER: dry run requested; real CodeBrigade execution was intentionally skipped", final_report)
            self.assertIn("Verification commands planned:", final_report)
            self.assertIn("Verification commands executed: 0", final_report)
            self.assertIn("Planning review decision: ready_for_ceraxia_review", final_report)
            self.assertIn("Planning review score:", final_report)
            self.assertIn("Planning department status: ready_for_code_brigade", final_report)
            self.assertIn("Engineering RFC status: accepted_for_code_brigade_handoff", final_report)
            self.assertIn("Multi-pass investigation status: complete", final_report)
            self.assertIn("CodeBrigade package handoff: ready", final_report)
            self.assertIn("Planning department review status: complete", final_report)
            self.assertIn("Expert quality level: expert", final_report)
            self.assertIn("Expert quality required: true", final_report)
            self.assertIn("Planning work phases:", final_report)
            self.assertIn("Implementation work packages:", final_report)
            self.assertIn("Work package covered surfaces:", final_report)
            self.assertIn("Work package statuses: planned=", final_report)
            self.assertIn("Work package dependency graph complete: true", final_report)
            self.assertIn("Work package dependency rows:", final_report)
            self.assertIn("Work package dependency roots:", final_report)
            self.assertIn("Work package dependency terminals:", final_report)
            self.assertIn("Survey quality decision: passed", final_report)
            self.assertIn("Surface verification status: planned_only", final_report)
            self.assertIn("Worker status: dry_run_handoff_ready", final_report)
            self.assertIn("Execution policy status: blocked_until_adapter_is_wired", final_report)
            self.assertIn("Execution preflight ok: n/a", final_report)
            self.assertIn("Autonomous execution request: required", final_report)
            self.assertIn("Execution intent: planning_handoff_only", final_report)
            self.assertIn("Execution adapter capability: explicit_patch_adapter_only", final_report)
            self.assertIn("Scope budget source files:", final_report)
            self.assertIn("Scope budget unrequested test edits: 0", final_report)
            self.assertIn("Engineering memory status: recorded", final_report)
            self.assertIn("Engineering memory reusable patterns:", final_report)
            self.assertIn("Engineering memory false-success guards:", final_report)
            self.assertIn("WARNING: broad verification is planned but not executed", final_report)
            self.assertIn("- repository survey partial: false", final_report)
            self.assertIn("- python symbol survey partial: false", final_report)
            self.assertIn("- source summary survey partial: false", final_report)
            self.assertIn("- max files scanned:", final_report)
            self.assertIn("- max python symbol files:", final_report)
            self.assertIn("- max source summary files:", final_report)
            self.assertIn("- reverse dependency targets:", final_report)
            self.assertIn("- test coverage links:", final_report)
            self.assertIn("- caller candidate rows:", final_report)
            self.assertIn("- contract surface candidates:", final_report)
            self.assertIn("- package manifest candidates:", final_report)
            self.assertIn("- expert tradeoffs:", final_report)
            self.assertIn("- expert review checklist items:", final_report)

    def test_repo_survey_directly_checks_explicit_path_hints_past_scan_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "first.py").write_text("def first():\n    return True\n", encoding="utf-8")
            nested = repo / "deep" / "target"
            nested.mkdir(parents=True)
            (nested / "README.md").write_text("# Target\n", encoding="utf-8")
            old_limit = repo_survey_module.MAX_SURVEY_FILES
            try:
                repo_survey_module.MAX_SURVEY_FILES = 1
                packet = build_planning_packet(
                    {
                        "task": "обнови `deep/target/README.md`",
                        "repo_path": str(repo),
                    }
                )
                survey = build_repo_survey(packet)
            finally:
                repo_survey_module.MAX_SURVEY_FILES = old_limit
            self.assertIn("deep/target/README.md", survey["existing_path_hints"])
            self.assertNotIn("deep/target/README.md", survey["missing_path_hints"])
            self.assertIn("deep/target/README.md", [row["path"] for row in survey["recommended_read_order"]])

    def test_missing_repo_blocks_before_claiming_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_ceraxia(
                CeraxiaInput(
                    task="добавь feature в API и сохрани compatibility",
                    repo_path=str(Path(tmp) / "missing"),
                    runs_root=Path(tmp) / "runs",
                )
            )
            self.assertFalse(result["ok"])
            self.assertFalse(result["package_ok"])
            self.assertFalse(result["ready_for_execution"])
            self.assertEqual(result["state"], "failed")
            run_dir = Path(result["run_dir"])
            brief = json.loads((run_dir / "implementation_brief.json").read_text(encoding="utf-8"))
            self.assertTrue(brief["blocked"])
            readiness = json.loads((run_dir / "execution_readiness.json").read_text(encoding="utf-8"))
            self.assertEqual(readiness["decision"], "blocked")
            status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
            self.assertIn("failed", status["lifecycle"])

    def test_missing_explicit_path_hint_blocks_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
            result = run_ceraxia(
                CeraxiaInput(
                    task="почини `missing.py` без изменения API",
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                )
            )
            self.assertFalse(result["ok"], result)
            run_dir = Path(result["run_dir"])
            brief = json.loads((run_dir / "implementation_brief.json").read_text(encoding="utf-8"))
            self.assertTrue(brief["blocked"])
            self.assertEqual(brief["survey_quality_gate"]["decision"], "blocked")
            self.assertIn("missing.py", brief["survey_quality_gate"]["missing_path_hints"])

    def test_survey_quality_reports_source_summary_truncation(self) -> None:
        packet = build_planning_packet({"task": "добавь helper", "repo_path": "."})
        survey = {
            "repo_exists": True,
            "candidate_files": ["app.ts"],
            "test_files": ["app.spec.ts"],
            "missing_path_hints": [],
            "unsafe_path_hints": [],
            "truncated": False,
            "python_symbols_truncated": False,
            "source_summaries_truncated": True,
        }
        quality = build_survey_quality_gate(packet, survey)
        self.assertEqual(quality["decision"], "passed")
        self.assertIn("source summary survey reached file limit", quality["warnings"])

    def test_unsafe_explicit_path_hint_blocks_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
            result = run_ceraxia(
                CeraxiaInput(
                    task="почини `/tmp/outside.py` без изменения API",
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                )
            )
            self.assertFalse(result["ok"], result)
            run_dir = Path(result["run_dir"])
            brief = json.loads((run_dir / "implementation_brief.json").read_text(encoding="utf-8"))
            self.assertTrue(brief["blocked"])
            self.assertEqual(brief["survey_quality_gate"]["decision"], "blocked")
            self.assertIn("/tmp/outside.py", brief["survey_quality_gate"]["unsafe_path_hints"])

    def test_execute_verification_runs_allowlisted_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
            result = run_ceraxia(
                CeraxiaInput(
                    task="добавь API helper",
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                    execute_verification=True,
                )
            )
            self.assertTrue(result["package_ok"], result)
            run_dir = Path(result["run_dir"])
            verification = json.loads((run_dir / "verification_report.json").read_text(encoding="utf-8"))
            self.assertEqual(verification["status"], "passed")
            self.assertTrue(verification["commands_executed"])
            self.assertTrue(verification["output_summary"])
            self.assertTrue(all(row.get("output_signal") for row in verification["output_summary"]))
            review = json.loads((run_dir / "review_gate.json").read_text(encoding="utf-8"))
            self.assertEqual(review["surface_verification_sufficiency"]["status"], "partial")
            self.assertGreaterEqual(review["verification_sufficiency"]["meaningful_commands_executed_count"], 1)
            self.assertGreaterEqual(review["verification_sufficiency"]["output_summary_count"], 1)
            self.assertTrue(review["verification_sufficiency"]["output_signal_counts"])
            surface_evidence = review["surface_verification_sufficiency"]["surface_evidence"]
            source_surface = next(row for row in surface_evidence if row["surface"] == "source_behavior")
            self.assertEqual(source_surface["status"], "executed")
            self.assertTrue(source_surface["matched_commands"])
            self.assertTrue(source_surface["matched_output_signal_counts"])
            self.assertTrue(any(row["status"] == "partial" for row in surface_evidence))
            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["surface_verification_status"], "partial")
            self.assertGreaterEqual(summary["verification_output_summary_count"], 1)
            self.assertEqual(summary["verification_output_signal_counts"], review["verification_sufficiency"]["output_signal_counts"])
            self.assertEqual(summary["verification_output_diagnostic_counts"], review["verification_sufficiency"]["output_diagnostic_counts"])
            self.assertEqual(summary["surface_verification_status_counts"], review["surface_verification_sufficiency"]["status_counts"])
            self.assertGreaterEqual(summary["surface_verification_executed_count"] + summary["surface_verification_partial_count"], 1)

    def test_execute_diagnostic_repair_writes_execution_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
            (repo / "test_app.py").write_text(
                "import unittest\nimport app\n\n\nclass AppTest(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(app.value(), 2)\n\n\nif __name__ == '__main__':\n    unittest.main()\n",
                encoding="utf-8",
            )
            result = run_ceraxia(
                CeraxiaInput(
                    task="почини failing unittest в `app.py`",
                    repo_path=str(repo),
                    execute_verification=True,
                    execute_diagnostic_repair=True,
                    verification_commands=("python -m unittest test_app.py",),
                    runs_root=Path(tmp) / "runs",
                )
            )
            run_dir = Path(result["run_dir"])
            repair_execution = json.loads((run_dir / "diagnostic_repair_execution_result.json").read_text(encoding="utf-8"))
            self.assertEqual(repair_execution["status"], "implemented")
            self.assertEqual(repair_execution["changed_files"], ["app.py"])
            self.assertIn("return 2", (repo / "app.py").read_text(encoding="utf-8"))

    def test_real_explicit_patch_pipeline_reaches_execution_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def app():\n    return False\n", encoding="utf-8")
            patch = {
                "operations": [
                    {
                        "type": "replace_return_expression",
                        "path": "app.py",
                        "function_name": "app",
                        "old_expression": "False",
                        "new_expression": "True",
                    }
                ]
            }
            result = run_ceraxia(
                CeraxiaInput(
                    task="почини bug в `app.py`\nCERAXIA_PATCH:\n" + json.dumps(patch),
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                    dry_run=False,
                    execute_verification=True,
                    verification_commands=("python -m py_compile app.py",),
                )
            )
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["package_ok"], result)
            self.assertTrue(result["ready_for_execution"], result)
            self.assertEqual(result["execution_mode"], "guarded_patch")
            self.assertIn("return True", (repo / "app.py").read_text(encoding="utf-8"))
            run_dir = Path(result["run_dir"])
            task_json = json.loads((run_dir / "task.json").read_text(encoding="utf-8"))
            self.assertEqual(task_json["execution_mode"], "guarded_patch")
            worker_report = json.loads((run_dir / "worker_report.json").read_text(encoding="utf-8"))
            self.assertEqual(worker_report["status"], "implemented")
            self.assertEqual(worker_report["execution_intent"]["mode"], "explicit_patch_execution")
            self.assertEqual(worker_report["edit_plan"]["controller_execution_mode"], "guarded_patch")
            self.assertIn("app.py", worker_report["edit_plan"]["target_files"])
            self.assertIn("python -m py_compile app.py", worker_report["edit_plan"]["verification_commands"])
            self.assertTrue(worker_report["edit_plan"]["acceptance_criteria"])
            read_evidence = worker_report["pre_mutation_read_evidence"]
            self.assertEqual(read_evidence["status"], "complete")
            self.assertTrue(any(row["path"] == "app.py" and row["status"] == "read" for row in read_evidence["rows"]))
            self.assertTrue(worker_report["execution_intent"]["real_execution_supported"])
            self.assertEqual(worker_report["autonomous_execution_request"]["status"], "not_required")
            self.assertTrue(all(item["status"] == "implemented" for item in worker_report["work_package_statuses"]))
            self.assertTrue(all(item["evidence_source"] == "execution_result" for item in worker_report["work_package_statuses"]))
            self.assertEqual(worker_report["execution_policy_status"], "real_execution_adapter_active")
            self.assertEqual(worker_report["execution_result"]["status"], "implemented")
            self.assertEqual(worker_report["execution_result"]["operation_results"][0]["status"], "applied")
            verification = json.loads((run_dir / "verification_report.json").read_text(encoding="utf-8"))
            self.assertEqual(verification["status"], "passed")
            self.assertTrue(verification["commands_executed"])
            self.assertEqual(verification["verification_after_mutation_evidence"]["status"], "complete")
            self.assertEqual(verification["verification_after_mutation_evidence"]["changed_file_count"], 1)
            review = json.loads((run_dir / "review_gate.json").read_text(encoding="utf-8"))
            self.assertEqual(review["decision"], "ready")
            self.assertEqual(review["verification_after_mutation_sufficiency"]["status"], "complete")
            self.assertEqual(review["surface_verification_sufficiency"]["status"], "partial")
            self.assertGreaterEqual(review["package_status_sufficiency"]["status_counts"]["implemented"], 1)
            readiness = json.loads((run_dir / "execution_readiness.json").read_text(encoding="utf-8"))
            self.assertEqual(readiness["decision"], "ready_for_real_execution")
            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["code_brigade_execution_intent_mode"], "explicit_patch_execution")
            self.assertTrue(summary["code_brigade_execution_real_supported"])
            self.assertEqual(summary["code_brigade_autonomous_execution_request_status"], "not_required")
            audit = json.loads((run_dir / "run_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["decision"], "passed")

    def test_review_only_mode_builds_review_package_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def app():\n    return False\n", encoding="utf-8")
            patch = {
                "operations": [
                    {
                        "type": "replace_return_expression",
                        "path": "app.py",
                        "function_name": "app",
                        "old_expression": "False",
                        "new_expression": "True",
                    }
                ]
            }
            result = run_ceraxia(
                CeraxiaInput(
                    task="review only for `app.py`\nCERAXIA_PATCH:\n" + json.dumps(patch),
                    repo_path=str(repo),
                    execution_mode="review_only",
                    runs_root=Path(tmp) / "runs",
                    execute_verification=True,
                    verification_commands=("python -m py_compile app.py",),
                )
            )
            self.assertTrue(result["ok"], result)
            self.assertFalse(result["ready_for_execution"], result)
            self.assertIn("return False", (repo / "app.py").read_text(encoding="utf-8"))
            run_dir = Path(result["run_dir"])
            task_json = json.loads((run_dir / "task.json").read_text(encoding="utf-8"))
            self.assertEqual(task_json["execution_mode"], "review_only")
            worker_report = json.loads((run_dir / "worker_report.json").read_text(encoding="utf-8"))
            self.assertEqual(worker_report["status"], "review_only_ready")
            self.assertEqual(worker_report["edit_plan"]["controller_execution_mode"], "review_only")
            self.assertEqual(worker_report["execution_policy_status"], "review_only_no_source_execution")
            self.assertNotIn("execution_result", worker_report)
            review = json.loads((run_dir / "review_gate.json").read_text(encoding="utf-8"))
            self.assertEqual(review["decision"], "dry_run_ready")
            readiness = json.loads((run_dir / "execution_readiness.json").read_text(encoding="utf-8"))
            self.assertIn("review_only requested", " ".join(readiness["blockers"]))
            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["maturity"], "review_only_controller_without_code_brigade_execution")
            self.assertEqual(summary["code_brigade_execution_policy_status"], "review_only_no_source_execution")

    def test_review_gate_blocks_blocked_work_packages(self) -> None:
        packet = build_planning_packet({"task": "почини pytest для public API schema", "repo_path": "."})
        survey = {
            "repo_exists": True,
            "repo_path": ".",
            "candidate_files": ["app.py"],
            "test_files": ["test_app.py"],
            "entrypoint_candidates": [],
            "python_symbols": [],
            "source_summaries": [],
            "local_import_edges": [],
            "generic_import_edges": [],
            "recommended_read_order": [{"path": "app.py", "reason": "ranked source/config candidate"}],
            "suggested_verification_commands": [],
            "truncated": False,
            "python_symbols_truncated": False,
            "source_summaries_truncated": False,
            "max_files_scanned": 1,
            "max_python_symbol_files": 1,
            "max_source_summary_files": 1,
        }
        brief = build_implementation_brief(packet, survey)
        worker_report = {
            "status": "dry_run_handoff_ready",
            "dry_run": True,
            "changed_files": [],
            "implementation_brief_acknowledged": True,
            "work_package_statuses": [
                {
                    "package_id": "minimal_patch_package",
                    "owner": "CodeBrigade",
                    "impact_surfaces": ["source_behavior"],
                    "status": "blocked",
                    "evidence_source": "blockers",
                }
            ],
        }
        verification_report = {
            "status": "planned_only",
            "negative_tests_required": [],
            "broad_verification_required": False,
            "commands_planned": ["python -m py_compile app.py"],
        }
        review = review_gate(packet, brief, worker_report, verification_report)
        self.assertEqual(review["decision"], "blocked")
        self.assertEqual(review["package_status_sufficiency"]["blocked_package_ids"], ["minimal_patch_package"])
        self.assertIn("evidence_survey_package", review["surface_package_sufficiency"]["missing_status_package_ids"])
        self.assertEqual(review["worker_output_contract_sufficiency"]["status"], "blocked")
        self.assertTrue(any("work packages are blocked" in item["finding"] for item in review["findings"]))
        self.assertTrue(any("surface package matrix references packages without worker status" in item["finding"] for item in review["findings"]))
        self.assertTrue(any("worker output contract is incomplete" in item["finding"] for item in review["findings"]))
        feedback = build_planning_feedback_request("run-1", packet, brief, worker_report, verification_report, review)
        self.assertEqual(feedback["status"], "required")
        self.assertEqual(feedback["target"], "PlanningBrigade")
        self.assertEqual(feedback["source"], "Ceraxia.review_gate")
        self.assertEqual(feedback["worker_output_contract_sufficiency"], review["worker_output_contract_sufficiency"])
        self.assertTrue(any("worker output contract" in item["finding"] for item in feedback["feedback_findings"]))
        self.assertIn("worker-output contract", " ".join(feedback["replan_focus"]))
        self.assertIn("planning_packet.json", feedback["required_return_artifacts"])
        feedback_intake = build_planning_feedback_intake(feedback)
        self.assertEqual(feedback_intake["status"], "replan_required")
        self.assertEqual(feedback_intake["handoff_back_to"], "Ceraxia")
        replan_packet = build_planning_packet(feedback_intake["replan_payload"])
        self.assertEqual(validate_planning_packet(replan_packet), [])
        self.assertTrue(any("feedback finding:" in item for item in replan_packet["problem_statement"]["known_constraints"]))

    def test_review_gate_blocks_missing_planning_department_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("from util import allowed\n\ndef app():\n    return allowed()\n", encoding="utf-8")
            (repo / "util.py").write_text("def allowed():\n    return True\n", encoding="utf-8")
            (repo / "test_app.py").write_text("from app import app\n\ndef test_app():\n    assert app()\n", encoding="utf-8")
            (repo / "api.ts").write_text("export function api() { return true; }\n", encoding="utf-8")
            (repo / "package.json").write_text(json.dumps({"scripts": {"test": "pytest"}}), encoding="utf-8")
            packet = build_planning_packet({"task": "почини security bug в `app.py` и проверь тестами", "repo_path": str(repo)})
            survey = build_repo_survey(packet)
            brief = build_implementation_brief(packet, survey)
            planning_department = build_planning_department_package(packet, survey, brief)
            brief = attach_planning_department_to_brief(brief, planning_department)
            worker_report = code_brigade_adapter.build_worker_report(brief, dry_run=True)
            plan = worker_report["implementation_plan"]
            plan["planning_department_status"] = ""
            plan["engineering_rfc_status"] = ""
            plan["multi_pass_investigation_status"] = ""
            plan["multi_pass_investigation_phases"] = []
            plan["planning_department_work_package_handoff"] = {}
            verification_report = {
                "status": "planned_only",
                "negative_tests_required": ["untrusted input is rejected"],
                "broad_verification_required": True,
                "commands_planned": ["python -m pytest test_app.py"],
                "commands_executed": [],
                "output_summary": [],
            }
            review = review_gate(packet, brief, worker_report, verification_report)
            self.assertEqual(review["decision"], "blocked")
            self.assertEqual(review["planning_department_sufficiency"]["status"], "blocked")
            self.assertTrue(any("planning department handoff is incomplete" in item["finding"] for item in review["findings"]))
            feedback = build_planning_feedback_request("run-1", packet, brief, worker_report, verification_report, review)
            self.assertEqual(feedback["status"], "required")
            self.assertEqual(feedback["planning_department_sufficiency"], review["planning_department_sufficiency"])
            self.assertTrue(any("planning department" in item["finding"] for item in feedback["feedback_findings"]))
            self.assertIn("planning_department.json", feedback["required_return_artifacts"])

    def test_review_gate_blocks_implemented_worker_without_read_evidence(self) -> None:
        packet = build_planning_packet({"task": "почини pytest для public API schema", "repo_path": "."})
        survey = {
            "repo_exists": True,
            "repo_path": ".",
            "candidate_files": ["app.py"],
            "test_files": ["test_app.py"],
            "entrypoint_candidates": [],
            "python_symbols": [],
            "source_summaries": [],
            "local_import_edges": [],
            "generic_import_edges": [],
            "recommended_read_order": [{"path": "app.py", "reason": "ranked source/config candidate"}],
            "suggested_verification_commands": ["python -m py_compile app.py"],
            "truncated": False,
            "python_symbols_truncated": False,
            "source_summaries_truncated": False,
            "max_files_scanned": 1,
            "max_python_symbol_files": 1,
            "max_source_summary_files": 1,
        }
        brief = build_implementation_brief(packet, survey)
        worker_report = {
            "status": "implemented",
            "dry_run": False,
            "changed_files": ["app.py"],
            "implementation_brief_acknowledged": True,
            "work_package_statuses": [
                {
                    "package_id": "evidence_survey_package",
                    "owner": "CodeBrigade",
                    "impact_surfaces": ["source_behavior", "test_surface"],
                    "status": "implemented",
                    "evidence_source": "worker_report",
                },
                {
                    "package_id": "minimal_patch_package",
                    "owner": "CodeBrigade",
                    "impact_surfaces": ["source_behavior"],
                    "status": "implemented",
                    "evidence_source": "worker_report",
                },
                {
                    "package_id": "verification_evidence_package",
                    "owner": "CodeBrigade",
                    "impact_surfaces": ["source_behavior", "test_surface"],
                    "status": "implemented",
                    "evidence_source": "verification_report",
                },
            ],
        }
        verification_report = {
            "status": "passed",
            "negative_tests_required": [],
            "broad_verification_required": False,
            "commands_planned": ["python -m py_compile app.py"],
            "commands_executed": [{"command": "python -m py_compile app.py", "returncode": 0}],
            "output_summary": [{"command": "python -m py_compile app.py", "returncode": 0, "signals": ["output_empty"], "diagnostics": []}],
        }
        review = review_gate(packet, brief, worker_report, verification_report)
        self.assertEqual(review["decision"], "blocked")
        self.assertEqual(review["pre_mutation_read_sufficiency"]["status"], "blocked")
        self.assertTrue(any("pre-mutation read evidence" in item["finding"] for item in review["findings"]))

    def test_review_gate_blocks_package_status_without_evidence_source(self) -> None:
        packet = build_planning_packet({"task": "В файле `app.py` замени `return False` на `return True`.", "repo_path": "."})
        survey = {
            "repo_exists": True,
            "repo_path": ".",
            "candidate_files": ["app.py"],
            "test_files": ["test_app.py"],
            "entrypoint_candidates": [],
            "python_symbols": [],
            "source_summaries": [],
            "local_import_edges": [],
            "generic_import_edges": [],
            "recommended_read_order": [{"path": "app.py", "reason": "ranked source/config candidate"}],
            "suggested_verification_commands": ["python -m py_compile app.py"],
            "truncated": False,
            "python_symbols_truncated": False,
            "source_summaries_truncated": False,
            "max_files_scanned": 1,
            "max_python_symbol_files": 1,
            "max_source_summary_files": 1,
        }
        brief = build_implementation_brief(packet, survey)
        worker_report = {
            "status": "dry_run_handoff_ready",
            "dry_run": True,
            "implementation_brief_acknowledged": True,
            "implementation_plan": code_brigade_adapter.build_implementation_plan(brief),
            "work_package_statuses": [
                {
                    "package_id": "minimal_patch_package",
                    "owner": "CodeBrigade",
                    "impact_surfaces": ["source_behavior"],
                    "status": "planned",
                }
            ],
        }
        verification_report = {
            "status": "planned_only",
            "negative_tests_required": [],
            "broad_verification_required": False,
            "commands_planned": ["python -m py_compile app.py"],
            "commands_executed": [],
            "output_summary": [],
        }
        review = review_gate(packet, brief, worker_report, verification_report)
        self.assertEqual(review["decision"], "blocked")
        self.assertEqual(review["package_status_sufficiency"]["missing_evidence_source_package_ids"], ["minimal_patch_package"])
        self.assertTrue(any("work packages lack evidence_source" in item["finding"] for item in review["findings"]))

    def test_review_gate_blocks_implemented_worker_with_unplanned_changed_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def app():\n    return False\n", encoding="utf-8")
            packet = build_planning_packet(
                {
                    "task": "В файле `app.py` замени `return False` на `return True`.",
                    "repo_path": str(repo),
                }
            )
            survey = build_repo_survey(packet)
            brief = build_implementation_brief(packet, survey)
            worker_report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(worker_report["status"], "implemented", worker_report)
            worker_report["changed_files"] = ["unexpected.py"]
            verification_report = {
                "status": "passed",
                "negative_tests_required": [],
                "broad_verification_required": False,
                "commands_planned": ["python -m py_compile app.py"],
                "commands_executable": ["python -m py_compile app.py"],
                "commands_executed": [{"command": "python -m py_compile app.py", "returncode": 0}],
                "output_summary": [
                    {
                        "command": "python -m py_compile app.py",
                        "returncode": 0,
                        "signals": ["output_empty"],
                        "diagnostics": [],
                    }
                ],
            }
            review = review_gate(packet, brief, worker_report, verification_report)
            self.assertEqual(review["decision"], "blocked")
            self.assertEqual(review["source_mutation_scope_sufficiency"]["status"], "blocked")
            self.assertEqual(review["source_mutation_scope_sufficiency"]["unexpected_files"], ["unexpected.py"])
            self.assertTrue(any("source mutation scope" in item["finding"] for item in review["findings"]))

    def test_review_scope_accepts_explicit_requested_test_edit(self) -> None:
        worker_report = {
            "status": "implemented",
            "changed_files": ["test_app.py"],
            "edit_plan": {
                "target_files": [],
                "allowed_new_files": [],
                "test_files": ["test_app.py"],
            },
            "implementation_plan": {
                "target_files_to_inspect": [],
                "missing_path_hints": [],
                "existing_path_hints": ["test_app.py"],
                "test_files_to_preserve": ["test_app.py"],
                "scope_budget": {
                    "max_test_files_to_edit_without_explicit_user_request": 0,
                },
            },
            "autonomous_execution_request": {
                "task": "Update `test_app.py` self-test to prove docs contract drift is caught.",
            },
        }
        scope = source_mutation_scope_sufficiency_from_worker(worker_report)
        self.assertEqual(scope["status"], "complete", scope)
        self.assertEqual(scope["unexpected_files"], [])
        self.assertIn("test_app.py", scope["allowed_files"])

    def test_review_gate_blocks_implemented_worker_without_after_mutation_verification_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def app():\n    return False\n", encoding="utf-8")
            packet = build_planning_packet(
                {
                    "task": "В файле `app.py` замени `return False` на `return True`.",
                    "repo_path": str(repo),
                }
            )
            survey = build_repo_survey(packet)
            brief = build_implementation_brief(packet, survey)
            worker_report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(worker_report["status"], "implemented", worker_report)
            verification_report = {
                "status": "passed",
                "negative_tests_required": [],
                "broad_verification_required": False,
                "commands_planned": ["python -m py_compile app.py"],
                "commands_executable": ["python -m py_compile app.py"],
                "commands_executed": [{"command": "python -m py_compile app.py", "returncode": 0}],
                "output_summary": [
                    {
                        "command": "python -m py_compile app.py",
                        "returncode": 0,
                        "signals": ["output_empty"],
                        "diagnostics": [],
                    }
                ],
            }
            review = review_gate(packet, brief, worker_report, verification_report)
            self.assertEqual(review["decision"], "blocked")
            self.assertEqual(review["source_mutation_scope_sufficiency"]["status"], "complete")
            self.assertEqual(review["verification_after_mutation_sufficiency"]["status"], "blocked")
            self.assertTrue(any("verification-after-mutation evidence" in item["finding"] for item in review["findings"]))

    def test_review_gate_blocks_missing_investigation_playbook(self) -> None:
        packet = build_planning_packet({"task": "почини pytest для public API schema", "repo_path": "."})
        survey = {
            "repo_exists": True,
            "repo_path": ".",
            "candidate_files": ["app.py"],
            "test_files": ["test_app.py"],
            "entrypoint_candidates": [],
            "python_symbols": [],
            "source_summaries": [],
            "local_import_edges": [],
            "generic_import_edges": [],
            "recommended_read_order": [{"path": "app.py", "reason": "ranked source/config candidate"}],
            "suggested_verification_commands": [],
            "truncated": False,
            "python_symbols_truncated": False,
            "source_summaries_truncated": False,
            "max_files_scanned": 1,
            "max_python_symbol_files": 1,
            "max_source_summary_files": 1,
        }
        brief = build_implementation_brief(packet, survey)
        packages = brief["implementation_work_packages"]["packages"]
        worker_report = {
            "status": "dry_run_handoff_ready",
            "dry_run": True,
            "changed_files": [],
            "implementation_brief_acknowledged": True,
            "implementation_plan": {
                "investigation_read_stages": [],
                "investigation_evidence_questions": [],
                "investigation_mutation_blockers": [],
                "investigation_replan_triggers": [],
            },
            "work_package_statuses": [
                {
                    "package_id": package["id"],
                    "owner": "CodeBrigade",
                    "impact_surfaces": package["impact_surfaces"],
                    "status": "planned",
                    "evidence_source": "implementation_plan",
                }
                for package in packages
            ],
        }
        verification_report = {
            "status": "planned_only",
            "negative_tests_required": [],
            "broad_verification_required": False,
            "commands_planned": ["python -m py_compile app.py"],
            "commands_executed": [],
        }
        review = review_gate(packet, brief, worker_report, verification_report)
        self.assertEqual(review["decision"], "blocked")
        self.assertEqual(review["investigation_sufficiency"]["status"], "blocked")
        self.assertTrue(any("investigation playbook is incomplete" in item["finding"] for item in review["findings"]))

    def test_review_gate_blocks_missing_change_control_plan(self) -> None:
        packet = build_planning_packet({"task": "почини pytest для public API schema", "repo_path": "."})
        survey = {
            "repo_exists": True,
            "repo_path": ".",
            "candidate_files": ["app.py"],
            "test_files": ["test_app.py"],
            "entrypoint_candidates": [],
            "python_symbols": [],
            "source_summaries": [],
            "local_import_edges": [],
            "generic_import_edges": [],
            "recommended_read_order": [{"path": "app.py", "reason": "ranked source/config candidate"}],
            "suggested_verification_commands": [],
            "truncated": False,
            "python_symbols_truncated": False,
            "source_summaries_truncated": False,
            "max_files_scanned": 1,
            "max_python_symbol_files": 1,
            "max_source_summary_files": 1,
        }
        brief = build_implementation_brief(packet, survey)
        packages = brief["implementation_work_packages"]["packages"]
        playbook = brief["investigation_playbook"]
        worker_report = {
            "status": "dry_run_handoff_ready",
            "dry_run": True,
            "changed_files": [],
            "implementation_brief_acknowledged": True,
            "implementation_plan": {
                "investigation_read_stages": playbook["read_stages"],
                "investigation_evidence_questions": playbook["evidence_questions"],
                "investigation_mutation_blockers": playbook["mutation_blockers"],
                "investigation_replan_triggers": playbook["replan_triggers"],
                "change_allowed_intents": [],
                "change_protected_invariants": [],
                "change_mutation_requires": [],
                "change_diff_review_questions": [],
                "change_rollback_triggers": [],
                "change_post_change_proofs": [],
            },
            "work_package_statuses": [
                {
                    "package_id": package["id"],
                    "owner": "CodeBrigade",
                    "impact_surfaces": package["impact_surfaces"],
                    "status": "planned",
                    "evidence_source": "implementation_plan",
                }
                for package in packages
            ],
        }
        verification_report = {
            "status": "planned_only",
            "negative_tests_required": [],
            "broad_verification_required": False,
            "commands_planned": ["python -m py_compile app.py"],
            "commands_executed": [],
        }
        review = review_gate(packet, brief, worker_report, verification_report)
        self.assertEqual(review["decision"], "blocked")
        self.assertEqual(review["change_control_sufficiency"]["status"], "blocked")
        self.assertTrue(any("change control plan is incomplete" in item["finding"] for item in review["findings"]))

    def test_review_gate_blocks_missing_constraint_trace(self) -> None:
        packet = build_planning_packet({"task": "почини pytest для public API schema", "repo_path": "."})
        survey = {
            "repo_exists": True,
            "repo_path": ".",
            "candidate_files": ["app.py"],
            "test_files": ["test_app.py"],
            "entrypoint_candidates": [],
            "python_symbols": [],
            "source_summaries": [],
            "local_import_edges": [],
            "generic_import_edges": [],
            "recommended_read_order": [{"path": "app.py", "reason": "ranked source/config candidate"}],
            "suggested_verification_commands": [],
            "truncated": False,
            "python_symbols_truncated": False,
            "source_summaries_truncated": False,
            "max_files_scanned": 1,
            "max_python_symbol_files": 1,
            "max_source_summary_files": 1,
        }
        brief = build_implementation_brief(packet, survey)
        packages = brief["implementation_work_packages"]["packages"]
        playbook = brief["investigation_playbook"]
        change = brief["change_control_plan"]
        assumptions = brief["assumption_register"]
        acceptance_trace = brief["acceptance_trace_matrix"]
        worker_report = {
            "status": "dry_run_handoff_ready",
            "dry_run": True,
            "changed_files": [],
            "implementation_brief_acknowledged": True,
            "implementation_plan": {
                "investigation_read_stages": playbook["read_stages"],
                "investigation_evidence_questions": playbook["evidence_questions"],
                "investigation_mutation_blockers": playbook["mutation_blockers"],
                "investigation_replan_triggers": playbook["replan_triggers"],
                "change_allowed_intents": change["allowed_change_intents"],
                "change_protected_invariants": change["protected_invariants"],
                "change_mutation_requires": change["mutation_requires"],
                "change_diff_review_questions": change["diff_review_questions"],
                "change_rollback_triggers": change["rollback_triggers"],
                "change_post_change_proofs": change["post_change_proofs"],
                "acceptance_trace_rows": acceptance_trace["rows"],
                "acceptance_trace_complete": True,
                "definition_of_done_trace_complete": acceptance_trace["definition_of_done_complete"],
                "definition_of_done_count": acceptance_trace["definition_of_done_count"],
                "traced_definition_of_done_count": acceptance_trace["traced_definition_of_done_count"],
                "missing_definition_of_done": acceptance_trace["missing_definition_of_done"],
                "constraint_trace_rows": [],
                "constraint_trace_complete": False,
                "assumption_rows": assumptions["assumptions"],
                "assumption_replan_triggers": assumptions["replan_when_false"],
            },
            "work_package_statuses": [
                {
                    "package_id": package["id"],
                    "owner": "CodeBrigade",
                    "impact_surfaces": package["impact_surfaces"],
                    "status": "planned",
                    "evidence_source": "implementation_plan",
                }
                for package in packages
            ],
        }
        verification_report = {
            "status": "planned_only",
            "negative_tests_required": [],
            "broad_verification_required": False,
            "commands_planned": ["python -m py_compile app.py"],
            "commands_executed": [],
        }
        review = review_gate(packet, brief, worker_report, verification_report)
        self.assertEqual(review["decision"], "blocked")
        self.assertEqual(review["constraint_trace_sufficiency"]["status"], "blocked")
        self.assertTrue(any("constraint trace matrix is incomplete" in item["finding"] for item in review["findings"]))

    def test_review_gate_marks_failed_surface_verification(self) -> None:
        packet = build_planning_packet({"task": "почини pytest для public API schema", "repo_path": "."})
        survey = {
            "repo_exists": True,
            "repo_path": ".",
            "candidate_files": ["app.py"],
            "test_files": ["test_app.py"],
            "entrypoint_candidates": [],
            "python_symbols": [],
            "source_summaries": [],
            "local_import_edges": [],
            "suggested_verification_commands": [],
            "truncated": False,
            "python_symbols_truncated": False,
            "source_summaries_truncated": False,
            "max_files_scanned": 1,
            "max_python_symbol_files": 1,
            "max_source_summary_files": 1,
        }
        brief = build_implementation_brief(packet, survey)
        worker_report = {
            "status": "dry_run_handoff_ready",
            "dry_run": True,
            "changed_files": [],
            "implementation_brief_acknowledged": True,
        }
        verification_report = {
            "status": "failed",
            "negative_tests_required": [],
            "broad_verification_required": False,
            "commands_planned": ["python -m pytest"],
            "commands_executable": ["python -m pytest"],
            "commands_executed": [{"command": "python -m pytest", "status": "failed"}],
            "output_summary": [
                {
                    "command": "python -m pytest",
                    "status": "failed",
                    "returncode": 1,
                    "stdout_nonempty": True,
                    "stderr_nonempty": False,
                    "output_signal": "failure_text",
                    "has_assertion_failure": True,
                }
            ],
        }
        review = review_gate(packet, brief, worker_report, verification_report)
        self.assertEqual(review["surface_verification_sufficiency"]["status"], "failed")
        self.assertEqual(review["verification_sufficiency"]["output_signal_counts"]["failure_text"], 1)
        self.assertEqual(review["verification_sufficiency"]["output_diagnostic_counts"]["assertion_failure"], 1)
        failed_source_surface = next(row for row in review["surface_verification_sufficiency"]["surface_evidence"] if row["surface"] == "source_behavior")
        self.assertEqual(failed_source_surface["matched_output_signal_counts"]["failure_text"], 1)
        self.assertEqual(failed_source_surface["matched_output_diagnostic_counts"]["assertion_failure"], 1)
        self.assertEqual(review["diagnostic_repair_queue"]["status"], "queued")
        self.assertEqual(review["diagnostic_repair_queue"]["item_count"], 1)
        repair_item = review["diagnostic_repair_queue"]["items"][0]
        self.assertIn("assertion_failure", repair_item["diagnostic_signals"])
        self.assertEqual(repair_item["failure_classification"]["type"], "behavior_regression_or_unmet_acceptance")
        self.assertEqual(repair_item["failure_classification"]["severity"], "high")
        self.assertEqual(repair_item["source_candidates"], ["app.py"])
        self.assertTrue(repair_item["repair_hypotheses"])
        self.assertIn("test oracle", repair_item["repair_hypotheses"][0]["hypothesis"])
        self.assertIn("source_behavior", repair_item["impacted_surfaces"])
        self.assertIn("minimal_patch_package", repair_item["package_ids"])
        self.assertEqual(repair_item["max_repair_attempts"], brief["diagnostic_repair_plan"]["max_repair_attempts"])
        self.assertEqual(repair_item["stop_conditions"], brief["diagnostic_repair_plan"]["stop_conditions"])
        readiness = build_execution_readiness({"state": "failed"}, brief, worker_report, verification_report, review, dry_run=False)
        self.assertIn("diagnostic repair request must be handled before execution readiness", readiness["blockers"])
        self.assertEqual(readiness["next_capability_to_wire"], "CodeBrigade diagnostic repair adapter")

    def test_review_gate_blocks_passed_report_with_failure_output(self) -> None:
        packet = build_planning_packet({"task": "почини pytest для public API schema", "repo_path": "."})
        survey = {
            "repo_exists": True,
            "repo_path": ".",
            "candidate_files": ["app.py"],
            "test_files": ["test_app.py"],
            "entrypoint_candidates": [],
            "python_symbols": [],
            "source_summaries": [],
            "local_import_edges": [],
            "suggested_verification_commands": [],
            "truncated": False,
            "python_symbols_truncated": False,
            "source_summaries_truncated": False,
            "max_files_scanned": 1,
            "max_python_symbol_files": 1,
            "max_source_summary_files": 1,
        }
        brief = build_implementation_brief(packet, survey)
        worker_report = {
            "status": "dry_run_handoff_ready",
            "dry_run": True,
            "changed_files": [],
            "implementation_brief_acknowledged": True,
        }
        verification_report = {
            "status": "passed",
            "negative_tests_required": [],
            "broad_verification_required": False,
            "commands_planned": ["python -m pytest"],
            "commands_executable": ["python -m pytest"],
            "commands_executed": [{"command": "python -m pytest", "status": "passed"}],
            "output_summary": [
                {
                    "command": "python -m pytest",
                    "status": "passed",
                    "returncode": 0,
                    "stdout_nonempty": True,
                    "stderr_nonempty": False,
                    "output_signal": "failure_text",
                }
            ],
        }
        review = review_gate(packet, brief, worker_report, verification_report)
        self.assertEqual(review["decision"], "blocked")
        self.assertTrue(any("output contains failure_text" in item["finding"] for item in review["findings"]))

    def test_review_gate_blocks_high_risk_partial_surface_execution(self) -> None:
        packet = build_planning_packet(
            {
                "task": "почини security API bug: token boundary ломает public schema compatibility",
                "repo_path": ".",
            }
        )
        survey = {
            "repo_exists": True,
            "repo_path": ".",
            "candidate_files": ["app.py"],
            "test_files": ["test_app.py"],
            "entrypoint_candidates": [],
            "python_symbols": [],
            "source_summaries": [],
            "local_import_edges": [],
            "generic_import_edges": [],
            "recommended_read_order": [{"path": "app.py", "reason": "ranked source/config candidate"}],
            "suggested_verification_commands": [],
            "truncated": False,
            "python_symbols_truncated": False,
            "source_summaries_truncated": False,
            "max_files_scanned": 1,
            "max_python_symbol_files": 1,
            "max_source_summary_files": 1,
        }
        brief = build_implementation_brief(packet, survey)
        worker_report = {
            "status": "dry_run_handoff_ready",
            "dry_run": True,
            "changed_files": [],
            "implementation_brief_acknowledged": True,
        }
        verification_report = {
            "status": "passed",
            "negative_tests_required": ["untrusted input is rejected", "old and new API shape compatibility"],
            "broad_verification_required": True,
            "commands_planned": ["python -m py_compile app.py", "python -m pytest test_app.py"],
            "commands_executable": ["python -m py_compile app.py", "python -m pytest test_app.py"],
            "commands_executed": [{"command": "python -m py_compile app.py", "status": "passed"}],
            "output_summary": [
                {
                    "command": "python -m py_compile app.py",
                    "status": "passed",
                    "returncode": 0,
                    "stdout_nonempty": False,
                    "stderr_nonempty": False,
                    "output_signal": "output_empty",
                }
            ],
        }
        review = review_gate(packet, brief, worker_report, verification_report)
        self.assertEqual(review["surface_verification_sufficiency"]["status"], "partial")
        self.assertEqual(review["verification_sufficiency"]["output_signal_counts"]["output_empty"], 1)
        surface_evidence = review["surface_verification_sufficiency"]["surface_evidence"]
        self.assertTrue(any(row["surface"] == "source_behavior" and row["matched_commands"] == ["python -m py_compile app.py"] for row in surface_evidence))
        self.assertTrue(any(row["surface"] == "security_boundary" and row["status"] == "partial" and not row["matched_commands"] for row in surface_evidence))
        self.assertEqual(review["decision"], "blocked")
        self.assertTrue(any("partial executed surface evidence" in item["finding"] for item in review["findings"]))

    def test_non_dry_run_blocks_until_real_code_brigade_execution_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
            result = run_ceraxia(
                CeraxiaInput(
                    task="добавь API helper",
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                    dry_run=False,
                )
            )
            self.assertFalse(result["ok"], result)
            self.assertFalse(result["package_ok"], result)
            self.assertFalse(result["ready_for_execution"], result)
            self.assertEqual(result["state"], "failed")
            run_dir = Path(result["run_dir"])
            worker_report = json.loads((run_dir / "worker_report.json").read_text(encoding="utf-8"))
            self.assertEqual(worker_report["status"], "blocked")
            self.assertEqual(worker_report["execution_intent"]["mode"], "planning_handoff_only")
            self.assertFalse(worker_report["execution_intent"]["real_execution_supported"])
            self.assertTrue(any("future CodeBrigade autonomous execution adapter" in note for note in worker_report["notes"]))
            self.assertEqual(worker_report["execution_result"]["status"], "blocked")
            self.assertTrue(worker_report["execution_result"]["blockers"])
            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["worker_status"], "blocked")
            self.assertEqual(summary["code_brigade_execution_intent_mode"], "planning_handoff_only")
            self.assertFalse(summary["code_brigade_execution_real_supported"])
            self.assertEqual(summary["code_brigade_execution_result_status"], "blocked")
            self.assertTrue(summary["code_brigade_execution_preflight_ok"])
            final_report = (run_dir / "final_report.md").read_text(encoding="utf-8")
            self.assertIn("Execution result status: blocked", final_report)
            self.assertIn("Execution preflight ok: True", final_report)

    def test_non_dry_guarded_inferred_patch_has_supported_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def app():\n    return False\n", encoding="utf-8")
            result = run_ceraxia(
                CeraxiaInput(
                    task="В файле `app.py` замени `return False` на `return True`.",
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                    dry_run=False,
                    execute_verification=True,
                    verification_commands=("python -m py_compile app.py",),
                )
            )
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["ready_for_execution"], result)
            self.assertIn("return True", (repo / "app.py").read_text(encoding="utf-8"))
            run_dir = Path(result["run_dir"])
            brief = json.loads((run_dir / "implementation_brief.json").read_text(encoding="utf-8"))
            self.assertEqual(brief["execution_intent"]["mode"], "guarded_inferred_patch_execution")
            self.assertTrue(brief["execution_intent"]["real_execution_supported"])
            self.assertEqual(brief["execution_intent"]["blockers"], [])
            worker_report = json.loads((run_dir / "worker_report.json").read_text(encoding="utf-8"))
            self.assertEqual(worker_report["execution_intent"]["mode"], "guarded_inferred_patch_execution")
            self.assertTrue(worker_report["execution_intent"]["real_execution_supported"])
            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["maturity"], "guarded_inferred_patch_execution_controller")
            self.assertEqual(summary["code_brigade_execution_intent_mode"], "guarded_inferred_patch_execution")

    def test_repo_engineer_mode_has_distinct_controller_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def app():\n    return False\n", encoding="utf-8")
            result = run_ceraxia(
                CeraxiaInput(
                    task="В файле `app.py` замени `return False` на `return True`.",
                    repo_path=str(repo),
                    execution_mode="repo_engineer",
                    runs_root=Path(tmp) / "runs",
                    execute_verification=True,
                    verification_commands=("python -m py_compile app.py",),
                )
            )
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["ready_for_execution"], result)
            self.assertEqual(result["execution_mode"], "repo_engineer")
            self.assertIn("return True", (repo / "app.py").read_text(encoding="utf-8"))
            run_dir = Path(result["run_dir"])
            task_json = json.loads((run_dir / "task.json").read_text(encoding="utf-8"))
            self.assertEqual(task_json["execution_mode"], "repo_engineer")
            self.assertFalse(task_json["dry_run"])
            worker_report = json.loads((run_dir / "worker_report.json").read_text(encoding="utf-8"))
            self.assertEqual(worker_report["status"], "implemented")
            self.assertEqual(worker_report["edit_plan"]["controller_execution_mode"], "repo_engineer")
            self.assertEqual(worker_report["execution_policy_status"], "real_execution_adapter_active")
            self.assertEqual(worker_report["pre_mutation_read_evidence"]["status"], "complete")
            review = json.loads((run_dir / "review_gate.json").read_text(encoding="utf-8"))
            self.assertEqual(review["decision"], "ready")
            self.assertEqual(review["verification_after_mutation_sufficiency"]["status"], "complete")
            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["maturity"], "repo_engineer_controller_with_guarded_code_brigade_execution")
            self.assertEqual(summary["code_brigade_execution_intent_mode"], "guarded_inferred_patch_execution")

    def test_non_dry_guarded_inferred_create_file_allows_missing_path_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
            result = run_ceraxia(
                CeraxiaInput(
                    task="Создай файл `helpers.py` с содержимым `def helper():\n    return True\n`.",
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                    dry_run=False,
                    execute_verification=True,
                    verification_commands=("python -m py_compile helpers.py",),
                )
            )
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["ready_for_execution"], result)
            self.assertIn("def helper", (repo / "helpers.py").read_text(encoding="utf-8"))
            run_dir = Path(result["run_dir"])
            brief = json.loads((run_dir / "implementation_brief.json").read_text(encoding="utf-8"))
            self.assertEqual(brief["survey_quality_gate"]["decision"], "passed")
            self.assertEqual(brief["survey_quality_gate"]["missing_path_hints"], ["helpers.py"])
            self.assertEqual(brief["survey_quality_gate"]["allowed_missing_create_path_hints"], ["helpers.py"])
            worker_report = json.loads((run_dir / "worker_report.json").read_text(encoding="utf-8"))
            self.assertEqual(worker_report["status"], "implemented")
            self.assertIn("natural_language_create_file", worker_report["execution_result"]["patch_summary"])
            self.assertEqual(worker_report["changed_files"], ["helpers.py"])
            read_evidence = worker_report["pre_mutation_read_evidence"]
            self.assertEqual(read_evidence["status"], "complete")
            self.assertTrue(any(row["path"] == "helpers.py" and row["status"] == "planned_new_file" for row in read_evidence["rows"]))
            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["maturity"], "guarded_inferred_patch_execution_controller")
            self.assertEqual(summary["code_brigade_execution_result_status"], "implemented")

    def test_non_dry_guarded_inferred_create_file_can_be_only_source_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "test_app.py").write_text(
                "import unittest\nfrom app import app\n\n"
                "class AppTest(unittest.TestCase):\n"
                "    def test_app(self):\n"
                "        self.assertTrue(app())\n",
                encoding="utf-8",
            )
            result = run_ceraxia(
                CeraxiaInput(
                    task="Создай файл `app.py` с содержимым `def app():\n    return True\n`.",
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                    dry_run=False,
                    execute_verification=True,
                    verification_commands=("python -m py_compile app.py", "python -m unittest test_app.py"),
                )
            )
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["ready_for_execution"], result)
            self.assertIn("def app", (repo / "app.py").read_text(encoding="utf-8"))
            run_dir = Path(result["run_dir"])
            brief = json.loads((run_dir / "implementation_brief.json").read_text(encoding="utf-8"))
            self.assertEqual(brief["survey_quality_gate"]["decision"], "passed")
            self.assertEqual(brief["repo_survey_evidence"]["candidate_files"], [])
            self.assertEqual(brief["survey_quality_gate"]["allowed_missing_create_path_hints"], ["app.py"])
            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["surface_verification_status"], "executed")

    def test_review_gate_rejects_incomplete_planning_packet(self) -> None:
        packet = build_planning_packet({"task": "почини pytest для public API schema", "repo_path": "."})
        packet.pop("verification_strategy")
        problems = validate_planning_packet(packet)
        self.assertTrue(any("verification_strategy" in problem for problem in problems))
        packet["verification_strategy"] = {}
        survey = build_repo_survey(packet)
        brief = build_implementation_brief(packet, survey)
        worker_report = {
            "status": "dry_run_handoff_ready",
            "dry_run": True,
            "changed_files": [],
            "implementation_brief_acknowledged": True,
        }
        verification_report = {
            "status": "planned_only",
            "negative_tests_required": [],
            "broad_verification_required": False,
            "commands_planned": [],
        }
        packet.pop("risk_register")
        review = review_gate(packet, brief, worker_report, verification_report)
        self.assertEqual(review["decision"], "blocked")

    def test_review_gate_warns_on_truncated_survey(self) -> None:
        packet = build_planning_packet({"task": "почини pytest для public API schema", "repo_path": "."})
        survey = build_repo_survey(packet)
        brief = build_implementation_brief(packet, survey)
        brief["repo_survey_evidence"]["survey_truncated"] = True
        worker_report = {
            "status": "dry_run_handoff_ready",
            "dry_run": True,
            "changed_files": [],
            "implementation_brief_acknowledged": True,
        }
        verification_report = {
            "status": "planned_only",
            "negative_tests_required": [],
            "broad_verification_required": False,
            "commands_planned": ["python -m pytest"],
            "commands_executable": [],
            "commands_executed": [],
        }
        review = review_gate(packet, brief, worker_report, verification_report)
        self.assertTrue(any("coverage is partial" in item["finding"] for item in review["warnings"]))

    def test_review_gate_warns_on_truncated_python_symbols(self) -> None:
        packet = build_planning_packet({"task": "почини pytest для public API schema", "repo_path": "."})
        survey = build_repo_survey(packet)
        brief = build_implementation_brief(packet, survey)
        brief["repo_survey_evidence"]["python_symbols_truncated"] = True
        worker_report = {
            "status": "dry_run_handoff_ready",
            "dry_run": True,
            "changed_files": [],
            "implementation_brief_acknowledged": True,
        }
        verification_report = {
            "status": "planned_only",
            "negative_tests_required": [],
            "broad_verification_required": False,
            "commands_planned": ["python -m pytest"],
            "commands_executable": [],
            "commands_executed": [],
        }
        review = review_gate(packet, brief, worker_report, verification_report)
        self.assertTrue(any("dependency evidence is partial" in item["finding"] for item in review["warnings"]))

    def test_planning_validation_blocks_weak_contract_fields(self) -> None:
        packet = build_planning_packet({"task": "repo-grade migration API compatibility", "repo_path": "."})
        packet["contract_version"] = "old"
        packet["repo_survey_request"]["read_only"] = False
        packet["problem_statement"]["definition_of_done"] = []
        packet["dependency_map"]["critical_path"] = ["task_contract", "implementation_brief"]
        packet["work_breakdown"]["phases"] = []
        packet["impact_analysis"]["surfaces"] = []
        packet["execution_forecast"] = {"complexity": "broken", "expected_code_brigade_iterations": 0}
        packet["design_options"]["options"] = []
        packet["verification_strategy"]["targeted_commands"] = []
        packet["surface_verification_matrix"] = {"rows": [], "complete": "no"}
        packet["surface_package_matrix"] = {"rows": [], "complete": "no"}
        packet["risk_register"]["acceptance_gates"] = []
        packet["quality_bar"]["must_have_evidence"] = []
        packet["acceptance_contract"]["must_prove"] = []
        packet["implementation_brief_blueprint"]["mutation_preconditions"] = []
        packet["planning_review_gate"] = {"decision": "broken", "score": -1}
        packet["code_brigade_handoff"]["steps"] = []
        problems = validate_planning_packet(packet)
        self.assertTrue(any("contract_version" in problem for problem in problems), problems)
        self.assertTrue(any("read-only" in problem for problem in problems), problems)
        self.assertTrue(any("definition_of_done" in problem for problem in problems), problems)
        self.assertTrue(any("critical path" in problem for problem in problems), problems)
        self.assertTrue(any("work breakdown missing phase" in problem for problem in problems), problems)
        self.assertTrue(any("impact analysis" in problem for problem in problems), problems)
        self.assertTrue(any("execution forecast" in problem for problem in problems), problems)
        self.assertTrue(any("reject hardcode" in problem for problem in problems), problems)
        self.assertTrue(any("targeted_commands" in problem for problem in problems), problems)
        self.assertTrue(any("surface verification matrix" in problem for problem in problems), problems)
        self.assertTrue(any("acceptance_gates" in problem for problem in problems), problems)
        self.assertTrue(any("quality bar" in problem for problem in problems), problems)
        self.assertTrue(any("acceptance contract" in problem for problem in problems), problems)
        self.assertTrue(any("mutation preconditions" in problem for problem in problems), problems)
        self.assertTrue(any("planning review gate" in problem for problem in problems), problems)
        self.assertTrue(any("code brigade handoff" in problem for problem in problems), problems)
        survey = build_repo_survey(packet)
        brief = build_implementation_brief(packet, survey)
        self.assertTrue(brief["blocked"])
        self.assertTrue(any("planning validation failed" in item for item in brief["blockers"]))

    def test_run_audit_blocks_missing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            result = run_ceraxia(
                CeraxiaInput(
                    task="почини pytest для public API schema",
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                )
            )
            run_dir = Path(result["run_dir"])
            (run_dir / "worker_report.json").unlink()
            audit = audit_run_package(run_dir)
            self.assertEqual(audit["decision"], "blocked")
            self.assertFalse(audit["manifest_complete"])

    def test_run_audit_blocks_corrupt_planning_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
            result = run_ceraxia(
                CeraxiaInput(
                    task="добавь helper в `app.py`",
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                )
            )
            run_dir = Path(result["run_dir"])
            packet_path = run_dir / "planning_packet.json"
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            packet.pop("surface_verification_matrix")
            packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            audit = audit_run_package(run_dir)
            self.assertEqual(audit["decision"], "blocked")
            self.assertTrue(any("planning packet audit failed" in item["finding"] for item in audit["findings"]))

    def test_run_audit_blocks_summary_readiness_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            result = run_ceraxia(
                CeraxiaInput(
                    task="почини pytest для public API schema",
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                )
            )
            run_dir = Path(result["run_dir"])
            summary_path = run_dir / "run_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["ready_for_execution"] = True
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            audit = audit_run_package(run_dir)
            self.assertEqual(audit["decision"], "blocked")
            self.assertTrue(any("ready_for_execution disagrees" in item["finding"] for item in audit["findings"]))

    def test_run_audit_blocks_worker_summary_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            result = run_ceraxia(
                CeraxiaInput(
                    task="почини pytest для public API schema",
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                )
            )
            run_dir = Path(result["run_dir"])
            summary_path = run_dir / "run_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["worker_status"] = "implemented"
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            audit = audit_run_package(run_dir)
            self.assertEqual(audit["decision"], "blocked")
            self.assertTrue(any("worker_status disagrees" in item["finding"] for item in audit["findings"]))

    def test_run_audit_blocks_planning_summary_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            result = run_ceraxia(
                CeraxiaInput(
                    task="почини pytest для public API schema",
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                )
            )
            run_dir = Path(result["run_dir"])
            summary_path = run_dir / "run_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["planning_review_decision"] = "blocked"
            summary["planning_review_score"] = 0
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            audit = audit_run_package(run_dir)
            self.assertEqual(audit["decision"], "blocked")
            self.assertTrue(any("planning_review_decision disagrees" in item["finding"] for item in audit["findings"]))
            self.assertTrue(any("planning_review_score disagrees" in item["finding"] for item in audit["findings"]))

    def test_run_audit_blocks_survey_quality_summary_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
            result = run_ceraxia(
                CeraxiaInput(
                    task="добавь helper в `app.py`",
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                )
            )
            run_dir = Path(result["run_dir"])
            summary_path = run_dir / "run_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["survey_quality_decision"] = "blocked"
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            audit = audit_run_package(run_dir)
            self.assertEqual(audit["decision"], "blocked")
            self.assertTrue(any("survey_quality_decision disagrees" in item["finding"] for item in audit["findings"]))

    def test_run_audit_blocks_surface_verification_summary_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
            result = run_ceraxia(
                CeraxiaInput(
                    task="добавь helper в `app.py`",
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                )
            )
            run_dir = Path(result["run_dir"])
            summary_path = run_dir / "run_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["surface_verification_status"] = "executed"
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            audit = audit_run_package(run_dir)
            self.assertEqual(audit["decision"], "blocked")
            self.assertTrue(any("surface_verification_status disagrees" in item["finding"] for item in audit["findings"]))

    def test_run_audit_blocks_worker_output_contract_summary_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
            result = run_ceraxia(
                CeraxiaInput(
                    task="добавь helper в `app.py`",
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                )
            )
            run_dir = Path(result["run_dir"])
            summary_path = run_dir / "run_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["worker_output_contract_status"] = "broken"
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            audit = audit_run_package(run_dir)
            self.assertEqual(audit["decision"], "blocked")
            self.assertTrue(any("worker_output_contract_status disagrees" in item["finding"] for item in audit["findings"]))
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["worker_output_contract_status"] = "complete"
            summary["worker_output_acceptance_requirement_row_count"] = 0
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            audit = audit_run_package(run_dir)
            self.assertEqual(audit["decision"], "blocked")
            self.assertTrue(any("worker_output_acceptance_requirement_row_count disagrees" in item["finding"] for item in audit["findings"]))

    def test_run_audit_blocks_surface_status_count_summary_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
            result = run_ceraxia(
                CeraxiaInput(
                    task="добавь helper в `app.py`",
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                )
            )
            run_dir = Path(result["run_dir"])
            summary_path = run_dir / "run_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["surface_verification_status_counts"] = {"executed": 99}
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            audit = audit_run_package(run_dir)
            self.assertEqual(audit["decision"], "blocked")
            self.assertTrue(any("surface_verification_status_counts disagrees" in item["finding"] for item in audit["findings"]))

    def test_run_audit_blocks_planning_feedback_summary_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
            result = run_ceraxia(
                CeraxiaInput(
                    task="добавь helper в `app.py`",
                    repo_path=str(repo),
                    runs_root=Path(tmp) / "runs",
                )
            )
            run_dir = Path(result["run_dir"])
            summary_path = run_dir / "run_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["planning_feedback_request_status"] = "required"
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            audit = audit_run_package(run_dir)
            self.assertEqual(audit["decision"], "blocked")
            self.assertTrue(any("planning_feedback_request_status disagrees" in item["finding"] for item in audit["findings"]))


if __name__ == "__main__":
    unittest.main()
