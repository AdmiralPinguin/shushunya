#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ceraxia import (
    CeraxiaInput,
    LIFECYCLE,
    build_implementation_brief,
    build_repo_survey,
    build_survey_quality_gate,
    audit_run_package,
    review_gate,
    run_ceraxia,
    validate_planning_packet,
)

import sys

PLANNING_PATH = str(Path(__file__).resolve().parents[1] / "PlanningBrigade")
if PLANNING_PATH not in sys.path:
    sys.path.insert(0, PLANNING_PATH)

from planning_brigade import build_planning_packet  # noqa: E402


class CeraxiaLifecycleTests(unittest.TestCase):
    def test_full_dry_run_pipeline_writes_required_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("from util import enabled\n\ndef app():\n    return enabled()\n", encoding="utf-8")
            (repo / "util.py").write_text("def enabled():\n    return True\n", encoding="utf-8")
            (repo / "api.ts").write_text("export function api() { return true; }\n", encoding="utf-8")
            (repo / "client.ts").write_text("import { api } from './api';\nexport function client() { return api(); }\n", encoding="utf-8")
            (repo / "client.spec.ts").write_text("import { client } from './client';\ntest('client', () => client());\n", encoding="utf-8")
            (repo / "test_app.py").write_text("from app import app\n\ndef test_app():\n    assert app()\n", encoding="utf-8")
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
                "implementation_brief.json",
                "worker_report.json",
                "verification_report.json",
                "review_gate.json",
                "status.json",
                "final_report.md",
                "execution_readiness.json",
                "run_summary.json",
                "evidence_matrix.json",
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
            self.assertEqual(brief["risk_level"], "high")
            self.assertIn("hardcoded one-off behavior", brief["forbidden_approaches"])
            self.assertIn("negative boundary test or explicit blocker is present", brief["quality_bar"]["must_have_evidence"])
            self.assertEqual(brief["code_brigade_handoff"]["target"], "CodeBrigade")
            self.assertIn("prove_negative_boundary", [step["step"] for step in brief["code_brigade_handoff"]["steps"]])
            self.assertEqual(brief["planning_review_gate"]["decision"], "ready_for_ceraxia_review")
            self.assertGreaterEqual(brief["planning_review_gate"]["score"], 80)
            self.assertGreaterEqual(len(brief["work_breakdown"]["phases"]), 6)
            self.assertEqual(brief["impact_analysis"]["highest_risk_surface"], "security_boundary")
            self.assertTrue(brief["impact_analysis"]["requires_cross_surface_review"])
            self.assertEqual(brief["execution_forecast"]["complexity"], "high")
            self.assertGreaterEqual(brief["execution_forecast"]["expected_code_brigade_iterations"], 4)
            self.assertTrue(any(item["path"] == "client.ts" and item["language"] == "typescript" for item in brief["repo_survey_evidence"]["source_summaries"]))
            self.assertTrue(brief["surface_verification_matrix"]["complete"])
            self.assertTrue(any(row["surface"] == "security_boundary" for row in brief["surface_verification_matrix"]["rows"]))
            self.assertEqual(brief["survey_quality_gate"]["decision"], "passed")
            self.assertIn("app.py", brief["repo_survey_evidence"]["candidate_files"])
            self.assertEqual(brief["repo_survey_evidence"]["existing_path_hints"], ["app.py", "test_app.py"])
            self.assertEqual(brief["repo_survey_evidence"]["missing_path_hints"], [])
            self.assertEqual(brief["repo_survey_evidence"]["unsafe_path_hints"], [])
            self.assertFalse(brief["repo_survey_evidence"]["survey_truncated"])
            self.assertFalse(brief["repo_survey_evidence"]["python_symbols_truncated"])
            self.assertTrue(any(edge["source"] == "app.py" and edge["target"] == "util.py" for edge in brief["repo_survey_evidence"]["local_import_edges"]))
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
            self.assertTrue(any(item["path"] == "client.ts" and "client" in item["symbols"] for item in implementation_plan["source_summaries_to_consider"]))
            self.assertTrue(implementation_plan["surface_verification_complete"])
            self.assertTrue(any(row["surface"] == "security_boundary" for row in implementation_plan["surface_verification_rows"]))
            self.assertEqual(implementation_plan["survey_quality_decision"], "passed")
            self.assertTrue(any(edge["source"] == "app.py" and edge["target"] == "util.py" for edge in implementation_plan["dependency_edges_to_check"]))
            self.assertTrue(any(edge["source"] == "client.ts" and edge["target"] == "api.ts" for edge in implementation_plan["dependency_edges_to_check"]))
            self.assertTrue(any(edge["source"] == "client.ts" and edge["target"] == "api.ts" for edge in implementation_plan["generic_dependency_edges_to_check"]))
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
            self.assertTrue(any(edge["source"] == "client.ts" and edge["target"] == "api.ts" for edge in survey["local_import_edges"]))
            self.assertTrue(any(edge["source"] == "client.ts" and edge["target"] == "api.ts" for edge in survey["generic_import_edges"]))
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
            self.assertEqual(review["verification_sufficiency"]["status"], "planned_only")
            self.assertEqual(review["surface_verification_sufficiency"]["status"], "planned_only")
            self.assertGreaterEqual(review["surface_verification_sufficiency"]["surface_count"], 1)
            self.assertGreaterEqual(review["verification_sufficiency"]["commands_planned_count"], 1)
            readiness = json.loads((run_dir / "execution_readiness.json").read_text(encoding="utf-8"))
            self.assertEqual(readiness["decision"], "blocked")
            self.assertIn("real CodeBrigade execution is not wired in this controller yet", readiness["blockers"])
            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["contract_version"], "eye-mechanicum.v1")
            self.assertEqual(summary["execution_readiness"], "blocked")
            self.assertTrue(summary["package_ok"])
            self.assertTrue(summary["package_lifecycle_finalized"])
            self.assertEqual(summary["package_audit_decision"], "pending_until_run_audit")
            self.assertFalse(summary["ready_for_execution"])
            self.assertEqual(summary["review_decision"], "dry_run_ready")
            self.assertEqual(summary["planning_review_decision"], "ready_for_ceraxia_review")
            self.assertGreaterEqual(summary["planning_review_score"], 80)
            self.assertGreaterEqual(summary["planning_work_phase_count"], 6)
            self.assertEqual(summary["survey_quality_decision"], "passed")
            self.assertEqual(summary["survey_quality_warning_count"], 0)
            self.assertEqual(summary["surface_verification_status"], "planned_only")
            self.assertGreaterEqual(summary["surface_verification_surface_count"], 1)
            self.assertEqual(summary["worker_status"], "dry_run_handoff_ready")
            self.assertEqual(summary["code_brigade_execution_policy_status"], "blocked_until_adapter_is_wired")
            self.assertEqual(summary["code_brigade_execution_result_status"], "")
            self.assertIsNone(summary["code_brigade_execution_preflight_ok"])
            self.assertIn("security", summary["task_kinds"])
            self.assertGreaterEqual(summary["evidence"]["required_count"], 1)
            self.assertGreaterEqual(summary["evidence"]["planned_count"], 1)
            evidence_matrix = json.loads((run_dir / "evidence_matrix.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence_matrix["kind"], "ceraxia_evidence_matrix")
            self.assertGreaterEqual(evidence_matrix["required_evidence_count"], 1)
            self.assertTrue(any(row["requirement"] == "candidate files are chosen from repository evidence" for row in evidence_matrix["rows"]))
            self.assertIn("app.py", evidence_matrix["implementation_plan_sources"]["target_files_to_inspect"])
            self.assertEqual(evidence_matrix["implementation_plan_sources"]["recommended_read_order"][0]["path"], "app.py")
            final_report = (run_dir / "final_report.md").read_text(encoding="utf-8")
            self.assertIn("Execution readiness: blocked", final_report)
            self.assertIn("- evidence_matrix.json", final_report)
            self.assertIn("BLOCKER: real CodeBrigade execution is not wired in this controller yet", final_report)
            self.assertIn("Verification commands planned:", final_report)
            self.assertIn("Verification commands executed: 0", final_report)
            self.assertIn("Planning review decision: ready_for_ceraxia_review", final_report)
            self.assertIn("Planning review score:", final_report)
            self.assertIn("Planning work phases:", final_report)
            self.assertIn("Survey quality decision: passed", final_report)
            self.assertIn("Surface verification status: planned_only", final_report)
            self.assertIn("Worker status: dry_run_handoff_ready", final_report)
            self.assertIn("Execution policy status: blocked_until_adapter_is_wired", final_report)
            self.assertIn("Execution preflight ok: n/a", final_report)
            self.assertIn("WARNING: broad verification is planned but not executed", final_report)
            self.assertIn("- repository survey partial: false", final_report)
            self.assertIn("- python symbol survey partial: false", final_report)
            self.assertIn("- source summary survey partial: false", final_report)
            self.assertIn("- max files scanned:", final_report)
            self.assertIn("- max python symbol files:", final_report)
            self.assertIn("- max source summary files:", final_report)

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
            review = json.loads((run_dir / "review_gate.json").read_text(encoding="utf-8"))
            self.assertEqual(review["surface_verification_sufficiency"]["status"], "partial")
            self.assertGreaterEqual(review["verification_sufficiency"]["meaningful_commands_executed_count"], 1)
            surface_evidence = review["surface_verification_sufficiency"]["surface_evidence"]
            self.assertTrue(any(row["surface"] == "source_behavior" and row["status"] == "executed" for row in surface_evidence))
            self.assertTrue(any(row["status"] == "partial" for row in surface_evidence))
            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["surface_verification_status"], "partial")

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
            self.assertIn("return True", (repo / "app.py").read_text(encoding="utf-8"))
            run_dir = Path(result["run_dir"])
            worker_report = json.loads((run_dir / "worker_report.json").read_text(encoding="utf-8"))
            self.assertEqual(worker_report["status"], "implemented")
            self.assertEqual(worker_report["execution_policy_status"], "real_execution_adapter_active")
            self.assertEqual(worker_report["execution_result"]["status"], "implemented")
            self.assertEqual(worker_report["execution_result"]["operation_results"][0]["status"], "applied")
            verification = json.loads((run_dir / "verification_report.json").read_text(encoding="utf-8"))
            self.assertEqual(verification["status"], "passed")
            self.assertTrue(verification["commands_executed"])
            review = json.loads((run_dir / "review_gate.json").read_text(encoding="utf-8"))
            self.assertEqual(review["decision"], "ready")
            self.assertEqual(review["surface_verification_sufficiency"]["status"], "partial")
            readiness = json.loads((run_dir / "execution_readiness.json").read_text(encoding="utf-8"))
            self.assertEqual(readiness["decision"], "ready_for_real_execution")
            audit = json.loads((run_dir / "run_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["decision"], "passed")

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
        }
        review = review_gate(packet, brief, worker_report, verification_report)
        self.assertEqual(review["surface_verification_sufficiency"]["status"], "failed")

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
        }
        review = review_gate(packet, brief, worker_report, verification_report)
        self.assertEqual(review["surface_verification_sufficiency"]["status"], "partial")
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
            self.assertTrue(any("real CodeBrigade execution adapter is not configured" in note for note in worker_report["notes"]))
            self.assertEqual(worker_report["execution_result"]["status"], "blocked")
            self.assertTrue(worker_report["execution_result"]["blockers"])
            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["worker_status"], "blocked")
            self.assertEqual(summary["code_brigade_execution_result_status"], "blocked")
            self.assertTrue(summary["code_brigade_execution_preflight_ok"])
            final_report = (run_dir / "final_report.md").read_text(encoding="utf-8")
            self.assertIn("Execution result status: blocked", final_report)
            self.assertIn("Execution preflight ok: True", final_report)

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


if __name__ == "__main__":
    unittest.main()
