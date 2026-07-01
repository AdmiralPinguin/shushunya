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
    build_repo_survey_stub,
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
            runs = Path(tmp) / "runs"
            result = run_ceraxia(
                CeraxiaInput(
                    task="почини security bug: token auth можно обойти через path traversal, добавь pytest negative tests",
                    repo_path=str(repo),
                    runs_root=runs,
                )
            )
            self.assertTrue(result["ok"], result)
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
                "artifact_manifest.json",
                "run_audit.json",
            ]
            for name in expected:
                self.assertTrue((run_dir / name).exists(), name)
            brief = json.loads((run_dir / "implementation_brief.json").read_text(encoding="utf-8"))
            self.assertEqual(brief["target"], "CodeBrigade")
            self.assertEqual(brief["risk_level"], "high")
            self.assertIn("hardcoded one-off behavior", brief["forbidden_approaches"])
            verification = json.loads((run_dir / "verification_report.json").read_text(encoding="utf-8"))
            self.assertIn("untrusted input is rejected", verification["negative_tests_required"])
            audit = json.loads((run_dir / "run_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["decision"], "passed")
            self.assertTrue(audit["manifest_complete"])

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
            self.assertEqual(result["state"], "failed")
            run_dir = Path(result["run_dir"])
            brief = json.loads((run_dir / "implementation_brief.json").read_text(encoding="utf-8"))
            self.assertTrue(brief["blocked"])
            status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
            self.assertIn("failed", status["lifecycle"])

    def test_review_gate_rejects_incomplete_planning_packet(self) -> None:
        packet = build_planning_packet({"task": "почини pytest для public API schema", "repo_path": "."})
        packet.pop("verification_strategy")
        problems = validate_planning_packet(packet)
        self.assertTrue(any("verification_strategy" in problem for problem in problems))
        packet["verification_strategy"] = {}
        survey = build_repo_survey_stub(packet)
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
