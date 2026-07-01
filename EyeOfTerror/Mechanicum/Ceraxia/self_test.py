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
            (repo / "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
            (repo / "test_app.py").write_text("from app import app\n\ndef test_app():\n    assert app()\n", encoding="utf-8")
            runs = Path(tmp) / "runs"
            result = run_ceraxia(
                CeraxiaInput(
                    task="почини security bug: token auth можно обойти через path traversal, добавь pytest negative tests",
                    repo_path=str(repo),
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
                "artifact_manifest.json",
                "run_audit.json",
            ]
            for name in expected:
                self.assertTrue((run_dir / name).exists(), name)
            brief = json.loads((run_dir / "implementation_brief.json").read_text(encoding="utf-8"))
            self.assertEqual(brief["contract_version"], "eye-mechanicum.v1")
            self.assertEqual(brief["target"], "CodeBrigade")
            self.assertEqual(brief["risk_level"], "high")
            self.assertIn("hardcoded one-off behavior", brief["forbidden_approaches"])
            self.assertIn("negative boundary test or explicit blocker is present", brief["quality_bar"]["must_have_evidence"])
            self.assertEqual(brief["code_brigade_handoff"]["target"], "CodeBrigade")
            self.assertIn("prove_negative_boundary", [step["step"] for step in brief["code_brigade_handoff"]["steps"]])
            self.assertIn("app.py", brief["repo_survey_evidence"]["candidate_files"])
            self.assertTrue(any(command.startswith("python -m pytest test_app.py") for command in brief["suggested_verification_commands"]))
            verification = json.loads((run_dir / "verification_report.json").read_text(encoding="utf-8"))
            self.assertIn("untrusted input is rejected", verification["negative_tests_required"])
            self.assertTrue(any(command.startswith("python -m pytest test_app.py") for command in verification["commands_planned"]))
            survey = json.loads((run_dir / "repo_survey.json").read_text(encoding="utf-8"))
            self.assertEqual(survey["status"], "surveyed")
            self.assertIn("app.py", survey["candidate_files"])
            self.assertIn("test_app.py", survey["test_files"])
            app_symbols = next(item for item in survey["python_symbols"] if item["path"] == "app.py")
            self.assertIn("app", app_symbols["functions"])
            audit = json.loads((run_dir / "run_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["decision"], "passed")
            self.assertTrue(audit["manifest_complete"])
            readiness = json.loads((run_dir / "execution_readiness.json").read_text(encoding="utf-8"))
            self.assertEqual(readiness["decision"], "blocked")
            self.assertIn("real CodeBrigade execution is not wired in this controller yet", readiness["blockers"])
            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["contract_version"], "eye-mechanicum.v1")
            self.assertEqual(summary["execution_readiness"], "blocked")
            self.assertTrue(summary["package_ok"])
            self.assertFalse(summary["ready_for_execution"])
            self.assertEqual(summary["review_decision"], "dry_run_ready")
            self.assertIn("security", summary["task_kinds"])

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

    def test_planning_validation_blocks_weak_contract_fields(self) -> None:
        packet = build_planning_packet({"task": "repo-grade migration API compatibility", "repo_path": "."})
        packet["contract_version"] = "old"
        packet["repo_survey_request"]["read_only"] = False
        packet["design_options"]["options"] = []
        packet["verification_strategy"]["targeted_commands"] = []
        packet["risk_register"]["acceptance_gates"] = []
        packet["quality_bar"]["must_have_evidence"] = []
        packet["code_brigade_handoff"]["steps"] = []
        problems = validate_planning_packet(packet)
        self.assertTrue(any("contract_version" in problem for problem in problems), problems)
        self.assertTrue(any("read-only" in problem for problem in problems), problems)
        self.assertTrue(any("reject hardcode" in problem for problem in problems), problems)
        self.assertTrue(any("targeted_commands" in problem for problem in problems), problems)
        self.assertTrue(any("acceptance_gates" in problem for problem in problems), problems)
        self.assertTrue(any("quality bar" in problem for problem in problems), problems)
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


if __name__ == "__main__":
    unittest.main()
