#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import code_brigade_adapter
from diagnostic_repair_contract import execute_diagnostic_repair_request
from self_test import valid_brief


class CodeBrigadeFocusedTests(unittest.TestCase):
    def test_real_mutation_requires_planning_handoff(self) -> None:
        brief = valid_brief()
        brief.pop("planning_department")
        brief.pop("planning_department_handoff")
        report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
        self.assertEqual(report["planning_handoff_gate"]["decision"], "blocked")
        self.assertTrue(
            any("planning_department" in blocker for blocker in report["planning_handoff_gate"]["blockers"]),
            report,
        )

    def test_name_error_diagnostic_repair_uses_guarded_source_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "app.py").write_text("", encoding="utf-8")
            (repo / "test_app.py").write_text(
                "import app\n\n"
                "def test_value():\n"
                "    assert app.value() == 7\n",
                encoding="utf-8",
            )
            request = {
                "kind": "ceraxia_code_brigade_diagnostic_repair_request",
                "contract_version": "eye-mechanicum.v1",
                "run_id": "focused-name-error",
                "status": "required",
                "target": "CodeBrigade",
                "repo_path": str(repo),
                "task": "repair NameError in app.py",
                "verification_status": "failed",
                "review_decision": "blocked",
                "diagnostic_repair_queue": {
                    "status": "queued",
                    "item_count": 1,
                    "items": [
                        {
                            "command": "python -m pytest test_app.py",
                            "status": "failed",
                            "priority": "high",
                            "diagnostic_signals": ["name_error"],
                            "impacted_surfaces": ["source_behavior"],
                            "package_ids": ["minimal_patch_package"],
                            "read_before_repair": ["traceback_files"],
                            "concrete_read_targets": ["app.py"],
                            "stop_conditions": ["same verification failure repeats"],
                            "repair_evidence_required": ["rerun failed command"],
                            "max_repair_attempts": 3,
                            "missing_imports": [],
                        }
                    ],
                },
                "target_files_to_inspect": ["app.py"],
                "test_files_to_preserve": ["test_app.py"],
                "reverse_dependency_index": {"app.py": ["test_app.py"]},
                "scope_budget": {
                    "max_source_files_to_edit": 1,
                    "max_test_files_to_edit_without_explicit_user_request": 0,
                    "requires_ceraxia_replan_when": ["repair exceeds source scope"],
                },
                "attempt_history": [],
                "return_contract": ["worker_report.json", "verification_report.json"],
            }
            result = execute_diagnostic_repair_request(json.loads(json.dumps(request)))
            self.assertEqual(result["status"], "implemented", result)
            self.assertEqual(result["changed_files"], ["app.py"])
            self.assertIn("def value():\n    return 7\n", (repo / "app.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
