#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import code_brigade_adapter
from diagnostic_repair_contract import execute_diagnostic_repair_loop, execute_diagnostic_repair_request
from greenfield_architect import build_greenfield_project_brief as architect_build_greenfield_project_brief
from greenfield_architect import greenfield_model_runtime_defaults
from greenfield_dependency_worker import dependency_manager_status
from greenfield_feature_worker import infer_acceptance_features
from greenfield_implementation_worker import execute_file_set_synthesis_contract, execute_module_synthesis_contracts, extract_json_object, forbidden_markers_found as implementation_forbidden_markers_found, generated_file_quality, task_behavior_markers
from greenfield_implementation_worker import build_implementation_trace as worker_build_implementation_trace
from greenfield_implementation_worker import build_implementation_worker_plan as worker_build_implementation_worker_plan
from greenfield_live_trial import allocate_live_trial_root, compact_greenfield_result
from greenfield_memory_worker import build_greenfield_memory_index, build_greenfield_memory_record, update_greenfield_memory_index
from greenfield_project import build_greenfield_project_brief, execute_greenfield_project_brief, forbidden_placeholder_markers_found, model_synthesis_blockers, reconcile_module_synthesis_with_file_set, run_dependency_worker, run_greenfield_verification_loop, validate_greenfield_project_brief
from greenfield_repair_live_trial import compact_repair_result, scenario_spec
from greenfield_review_worker import artifact_review_greenfield_project, python_source_semantic_status, review_greenfield_project
from greenfield_scenario_worker import review_greenfield_scenarios
from greenfield_scaffold_worker import greenfield_workspace_status, normalize_project_file_rows, scaffold_greenfield_files
from greenfield_verification_worker import repair_guidance_for_verification, verification_failure_signature
from greenfield_templates import available_templates
from self_test import valid_brief


def diagnostic_repair_request(repo: Path, signals: list[str] | None = None, max_attempts: int = 3) -> dict:
    return {
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
                    "command": "python -m unittest test_app.py",
                    "status": "failed",
                    "priority": "high",
                    "diagnostic_signals": signals or ["name_error"],
                    "impacted_surfaces": ["source_behavior"],
                    "package_ids": ["minimal_patch_package"],
                    "read_before_repair": ["traceback_files"],
                    "concrete_read_targets": ["app.py"],
                    "stop_conditions": ["same verification failure repeats"],
                    "repair_evidence_required": ["rerun failed command"],
                    "max_repair_attempts": max_attempts,
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


def project_creation_brief(repo: Path, task: str) -> dict:
    brief = valid_brief()
    brief["repo_path"] = str(repo)
    brief["risk_level"] = "low"
    brief["controller_execution_mode"] = "project_creation"
    brief["task"] = task
    brief["execution_intent"] = {
        "kind": "ceraxia_code_brigade_execution_intent",
        "contract_version": "eye-mechanicum.v1",
        "mode": "greenfield_project_creation",
        "adapter_capability": "greenfield_project_scaffold_adapter",
        "explicit_patch_present": False,
        "real_execution_supported": True,
        "dry_run_requested": False,
        "blockers": [],
        "required_next_adapter": "",
    }
    brief["greenfield_model_guidance_replay"] = {
        "kind": "code_brigade_greenfield_model_guidance_replay",
        "mode": "scaffold_files_as_model_output",
    }
    brief["repo_survey_evidence"]["candidate_files"] = []
    brief["repo_survey_evidence"]["test_files"] = []
    brief["repo_survey_evidence"]["recommended_read_order"] = []
    brief["suggested_verification_commands"] = []
    return brief


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

    def test_greenfield_project_creation_does_not_require_planning_handoff_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(repo, "Создай telegram bot `gate-demo` с TELEGRAM_BOT_TOKEN runtime config.")
            brief["risk_level"] = "high"
            brief.pop("planning_department", None)
            brief.pop("planning_department_handoff", None)
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["planning_handoff_gate"]["decision"], "passed", report["planning_handoff_gate"])
            self.assertFalse(report["planning_handoff_gate"]["required"])
            self.assertFalse(any("PlanningBrigade handoff blocked" in note for note in report.get("notes", [])))

    def test_name_error_diagnostic_repair_uses_guarded_source_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "app.py").write_text("", encoding="utf-8")
            (repo / "test_app.py").write_text(
                "import unittest\n"
                "import app\n\n"
                "class AppTests(unittest.TestCase):\n"
                "    def test_value(self):\n"
                "        self.assertEqual(app.value(), 7)\n",
                encoding="utf-8",
            )
            request = diagnostic_repair_request(repo)
            result = execute_diagnostic_repair_request(json.loads(json.dumps(request)))
            self.assertEqual(result["status"], "implemented", result)
            self.assertEqual(result["changed_files"], ["app.py"])
            self.assertIn("def value():\n    return 7\n", (repo / "app.py").read_text(encoding="utf-8"))

    def test_diagnostic_repair_loop_applies_patch_and_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "app.py").write_text("", encoding="utf-8")
            (repo / "test_app.py").write_text(
                "import unittest\n"
                "import app\n\n"
                "class AppTests(unittest.TestCase):\n"
                "    def test_value(self):\n"
                "        self.assertEqual(app.value(), 7)\n",
                encoding="utf-8",
            )
            result = execute_diagnostic_repair_loop(diagnostic_repair_request(repo), max_cycles=2)
            self.assertEqual(result["status"], "passed", result)
            self.assertEqual(result["cycle_count"], 1)
            self.assertEqual(result["attempts"][0]["execution_status"], "implemented")
            self.assertEqual(result["attempts"][0]["verification_status"], "passed")
            self.assertEqual(result["verification_results"][0]["status"], "passed")

    def test_diagnostic_repair_loop_requests_replan_after_failed_repeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "app.py").write_text("def value():\n    return 3\n", encoding="utf-8")
            (repo / "test_app.py").write_text(
                "import unittest\n"
                "import app\n\n"
                "class AppTests(unittest.TestCase):\n"
                "    def test_value(self):\n"
                "        self.assertEqual(app.value(), 7)\n"
                "    def test_value_type(self):\n"
                "        self.assertIsInstance(app.value(), str)\n",
                encoding="utf-8",
            )
            result = execute_diagnostic_repair_loop(diagnostic_repair_request(repo), max_cycles=2)
            self.assertEqual(result["status"], "replan_required", result)
            self.assertEqual(result["cycle_count"], 1)
            self.assertEqual(result["attempts"][0]["execution_status"], "implemented")
            self.assertEqual(result["attempts"][0]["verification_status"], "failed")
            self.assertTrue(result["replan_packet"]["new_hypothesis_required"])

    def test_project_creation_mode_creates_greenfield_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(
                repo,
                "Создай новый минимальный python проект. "
                "CERAXIA_PROJECT: "
                '{"summary":"demo app","files":['
                '{"path":".ceraxia_greenfield_workspace","content":"created-by=ceraxia-code-brigade\\n"},'
                '{"path":"app.py","content":"def main():\\n    return \\"ready\\"\\n"},'
                '{"path":"test_app.py","content":"import unittest\\nimport app\\n\\nclass AppTests(unittest.TestCase):\\n    def test_main(self):\\n        self.assertEqual(app.main(), \\"ready\\")\\n"}'
                '],"verification_commands":["python -m unittest test_app.py"]}'
            )
            brief["suggested_verification_commands"] = ["python -m unittest test_app.py"]
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["status"], "implemented", report)
            self.assertEqual(
                sorted(report["changed_files"]),
                [
                    ".ceraxia_greenfield_workspace",
                    "README.md",
                    "app.py",
                    "architecture_plan.json",
                    "file_tree_plan.json",
                    "greenfield_file_set_synthesis_report.json",
                    "greenfield_memory_index.json",
                    "greenfield_memory_record.json",
                    "greenfield_model_guidance_ledger.json",
                    "greenfield_module_synthesis_report.json",
                    "greenfield_project_brief.json",
                    "greenfield_run_report.json",
                    "implementation_trace.json",
                    "module_contracts.json",
                    "scenario_plan.json",
                    "test_app.py",
                    "verification_plan.json",
                ],
            )
            self.assertEqual(report["execution_result"]["greenfield_project"]["verification"]["status"], "passed")
            project_brief = report["execution_result"]["greenfield_project"]["greenfield_project_brief"]
            self.assertEqual(project_brief["kind"], "code_brigade_greenfield_project_brief")
            self.assertEqual(project_brief["implementation_plan"]["kind"], "code_brigade_greenfield_implementation_plan")
            self.assertEqual(project_brief["implementation_plan"]["role"], "GreenfieldImplementationWorker")
            self.assertEqual(project_brief["implementation_plan"]["synthesis_policy"]["mode"], "module_by_module_llm_contract")
            self.assertEqual(project_brief["implementation_feature_report"]["kind"], "code_brigade_greenfield_implementation_feature_report")
            self.assertEqual(project_brief["implementation_trace"]["kind"], "code_brigade_greenfield_implementation_trace")
            self.assertEqual(project_brief["scenario_plan"]["kind"], "code_brigade_greenfield_scenario_plan")
            self.assertGreater(project_brief["scenario_plan"]["scenario_count"], 0)
            self.assertGreater(project_brief["implementation_trace"]["requirement_trace_count"], 0)
            self.assertTrue(project_brief["implementation_plan"]["module_sequence"])
            first_module = project_brief["implementation_plan"]["module_sequence"][0]
            self.assertEqual(first_module["code_synthesis_contract"]["kind"], "code_brigade_greenfield_module_synthesis_contract")
            self.assertEqual(first_module["code_synthesis_contract"]["path"], first_module["path"])
            memory = report["execution_result"]["greenfield_project"]["greenfield_memory_record"]
            self.assertEqual(memory["kind"], "code_brigade_greenfield_memory_record")
            self.assertEqual(memory["template_id"], project_brief["template_id"])
            self.assertEqual(memory["semantic_review_status"], "passed")
            self.assertEqual(memory["scenario_review_status"], "passed")
            self.assertEqual(memory["definition_of_done_status"]["status"], "passed")
            self.assertTrue(memory["reusable_learnings"])
            self.assertTrue((repo / "greenfield_memory_record.json").exists())
            memory_index = report["execution_result"]["greenfield_project"]["greenfield_memory_index"]
            self.assertEqual(memory_index["kind"], "code_brigade_greenfield_memory_index")
            self.assertEqual(memory_index["record_count"], 1)
            self.assertEqual(memory_index["recent_runs"][0]["template_id"], project_brief["template_id"])
            self.assertTrue((repo / "greenfield_memory_index.json").exists())
            self.assertTrue((repo / "greenfield_model_guidance_ledger.json").exists())
            self.assertTrue((repo / "greenfield_file_set_synthesis_report.json").exists())
            self.assertTrue((repo / "greenfield_module_synthesis_report.json").exists())
            self.assertTrue((repo / "greenfield_run_report.json").exists())
            ledger = json.loads((repo / "greenfield_model_guidance_ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(ledger["kind"], "code_brigade_greenfield_model_guidance_ledger")
            self.assertIn("GreenfieldArchitect", ledger["roles"])
            self.assertIn("GreenfieldImplementationWorker", ledger["roles"])
            self.assertIn("GreenfieldReviewer", ledger["roles"])
            run_report = json.loads((repo / "greenfield_run_report.json").read_text(encoding="utf-8"))
            self.assertEqual(run_report["kind"], "code_brigade_greenfield_run_report")
            self.assertIn(run_report["file_set_synthesis_status"], {"applied", "model_unavailable", "skipped", "rejected"})
            self.assertIn(run_report["implementation_synthesis_status"], {"applied", "model_unavailable", "skipped"})
            self.assertEqual(run_report["definition_of_done_status"]["status"], "passed")
            self.assertEqual(run_report["scenario_review_status"], "passed")
            self.assertEqual(run_report["model_guidance_ledger_status"], ledger["status"])
            review = report["execution_result"]["greenfield_project"]["greenfield_review"]
            self.assertIn("model_guidance", review)
            self.assertEqual(review["semantic_review"]["status"], "passed")
            self.assertEqual(review["scenario_review"]["status"], "passed")
            self.assertEqual(review["semantic_review"]["implementation_trace_status"], "complete")

    def test_project_creation_blocks_placeholder_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(
                repo,
                "Создай новый минимальный python проект `demo`.\nCERAXIA_PROJECT:\n"
                + json.dumps(
                    {
                        "summary": "placeholder demo",
                        "files": [
                            {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                            {"path": "app.py", "content": "def main():\n    # TODO replace generated placeholder\n    return \"ready\"\n"},
                            {"path": "test_app.py", "content": "import unittest\nimport app\n\n\nclass AppTests(unittest.TestCase):\n    def test_main(self):\n        self.assertEqual(app.main(), \"ready\")\n"},
                        ],
                        "verification_commands": ["python -m unittest test_app.py"],
                    }
                ),
            )
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["status"], "blocked", report)
            review = report["execution_result"]["greenfield_project"]["greenfield_review"]
            self.assertEqual(review["semantic_review"]["status"], "blocked")
            self.assertTrue(any("placeholder marker" in item for item in review["semantic_review"]["blockers"]))

    def test_placeholder_marker_does_not_confuse_todo_domain_word(self) -> None:
        self.assertEqual(forbidden_placeholder_markers_found("function addTodo() { return true; }", ["TODO"]), [])
        self.assertEqual(forbidden_placeholder_markers_found("# TODO replace this generated placeholder", ["TODO", "placeholder"]), ["TODO", "placeholder"])
        self.assertEqual(implementation_forbidden_markers_found("export function TodoDashboard() { return 'ready'; }", ["TODO", "placeholder"]), [])
        self.assertEqual(implementation_forbidden_markers_found("// TODO replace this generated placeholder", ["TODO", "placeholder"]), ["TODO", "placeholder"])

    def test_greenfield_review_worker_scores_python_source_strength(self) -> None:
        self.assertEqual(python_source_semantic_status("VALUE = 1\n"), "weak")
        self.assertEqual(python_source_semantic_status("def run():\n    return 'ready'\n\nif True:\n    run()\n"), "ok")

    def test_greenfield_review_blocks_unproven_definition_of_done_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".ceraxia_greenfield_workspace").write_text("created-by=ceraxia-code-brigade\n", encoding="utf-8")
            (repo / "README.md").write_text("# demo\n\n```bash\npython app.py\n```\n", encoding="utf-8")
            (repo / "app.py").write_text("VALUE = 'ready'\n\n\ndef main():\n    return VALUE\n", encoding="utf-8")
            (repo / "test_app.py").write_text(
                "import unittest\n"
                "import app\n\n"
                "class AppTests(unittest.TestCase):\n"
                "    def test_main(self):\n"
                "        self.assertEqual(app.main(), 'ready')\n",
                encoding="utf-8",
            )
            project = {
                "project_name": "demo",
                "project_type": "cli_tool",
                "template_id": "python_cli_basic",
                "expected_files": [".ceraxia_greenfield_workspace", "README.md", "app.py", "test_app.py"],
                "run_commands": ["python app.py"],
                "verification_commands": ["python -m unittest test_app.py"],
                "entrypoints": [{"name": "app", "command": "python app.py", "path": "app.py"}],
                "artifact_contract": {"source_files": ["app.py"], "test_files": ["test_app.py"], "manifest_files": []},
                "definition_of_done": ["README documents commands"],
                "implementation_plan": {
                    "module_sequence": [
                        {"path": "app.py", "code_synthesis_contract": {"kind": "code_brigade_greenfield_module_synthesis_contract"}},
                        {"path": "test_app.py", "code_synthesis_contract": {"kind": "code_brigade_greenfield_module_synthesis_contract"}},
                    ],
                    "anti_stub_policy": {"forbidden_markers": ["TODO", "pass"]},
                },
                "implementation_trace": {
                    "rows": [
                        {
                            "requirement": "return ready",
                            "file": "app.py",
                            "verification_files": ["test_app.py"],
                            "synthesis_contract_kind": "code_brigade_greenfield_module_synthesis_contract",
                        }
                    ]
                },
                "module_contracts": [
                    {"module": "app", "path": "app.py", "responsibility": "ready behavior", "requirements": ["return ready"]},
                    {"module": "test_app", "path": "test_app.py", "responsibility": "test ready behavior", "requirements": ["prove ready behavior"]},
                ],
                "scenario_plan": {
                    "kind": "code_brigade_greenfield_scenario_plan",
                    "contract_version": "eye-mechanicum.v1",
                    "status": "planned",
                    "scenario_count": 1,
                    "rows": [
                        {
                            "id": "ready",
                            "description": "Ready path works",
                            "steps": ["call main"],
                            "required_markers": ["ready"],
                            "evidence_files": ["app.py", "test_app.py"],
                        }
                    ],
                },
            }
            review = review_greenfield_project(
                repo,
                project,
                {"status": "not_required", "blockers": [], "warnings": []},
                {"status": "passed", "results": [{"command": "python -m unittest test_app.py", "status": "passed"}]},
                lambda role, payload, instructions: {"ok": True, "status": "answered", "content": "{}"},
            )
            self.assertEqual(review["status"], "blocked", review)
            dod = review["definition_of_done_review"]
            self.assertEqual(dod["status"], "blocked")
            self.assertIn("README missing command: python -m unittest test_app.py", dod["rows"][0]["missing_evidence"])
            self.assertTrue(any("definition_of_done item is not proven" in blocker for blocker in review["blockers"]))

    def test_greenfield_review_consumes_structured_model_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".ceraxia_greenfield_workspace").write_text("created-by=ceraxia-code-brigade\n", encoding="utf-8")
            (repo / "README.md").write_text(
                "# demo\n\n```bash\npython app.py\n```\n\n```bash\npython -m unittest test_app.py\n```\n",
                encoding="utf-8",
            )
            (repo / "app.py").write_text("VALUE = 'ready'\n\n\ndef main():\n    return VALUE\n", encoding="utf-8")
            (repo / "test_app.py").write_text(
                "import unittest\n"
                "import app\n\n"
                "class AppTests(unittest.TestCase):\n"
                "    def test_main(self):\n"
                "        self.assertEqual(app.main(), 'ready')\n",
                encoding="utf-8",
            )
            project = {
                "project_name": "demo",
                "project_type": "cli_tool",
                "template_id": "python_cli_basic",
                "expected_files": [".ceraxia_greenfield_workspace", "README.md", "app.py", "test_app.py"],
                "run_commands": ["python app.py"],
                "verification_commands": ["python -m unittest test_app.py"],
                "entrypoints": [{"name": "app", "command": "python app.py", "path": "app.py"}],
                "artifact_contract": {"source_files": ["app.py"], "test_files": ["test_app.py"], "manifest_files": []},
                "definition_of_done": ["README documents commands", "behavior is tested"],
                "implementation_plan": {
                    "module_sequence": [
                        {"path": "app.py", "code_synthesis_contract": {"kind": "code_brigade_greenfield_module_synthesis_contract"}},
                        {"path": "test_app.py", "code_synthesis_contract": {"kind": "code_brigade_greenfield_module_synthesis_contract"}},
                    ],
                    "anti_stub_policy": {"forbidden_markers": ["TODO", "pass"]},
                },
                "implementation_trace": {
                    "rows": [
                        {
                            "requirement": "return ready",
                            "file": "app.py",
                            "verification_files": ["test_app.py"],
                            "synthesis_contract_kind": "code_brigade_greenfield_module_synthesis_contract",
                        }
                    ]
                },
                "module_contracts": [
                    {"module": "app", "path": "app.py", "responsibility": "ready behavior", "requirements": ["return ready"]},
                    {"module": "test_app", "path": "test_app.py", "responsibility": "test ready behavior", "requirements": ["prove ready behavior"]},
                ],
                "scenario_plan": {
                    "kind": "code_brigade_greenfield_scenario_plan",
                    "contract_version": "eye-mechanicum.v1",
                    "status": "planned",
                    "scenario_count": 1,
                    "rows": [
                        {
                            "id": "ready",
                            "description": "Ready path works",
                            "steps": ["call main"],
                            "required_markers": ["ready"],
                            "evidence_files": ["app.py", "test_app.py"],
                        }
                    ],
                },
            }
            review = review_greenfield_project(
                repo,
                project,
                {"status": "not_required", "blockers": [], "warnings": []},
                {"status": "passed", "results": [{"command": "python -m unittest test_app.py", "status": "passed"}]},
                lambda role, payload, instructions: {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps({"status": "blocked", "blockers": ["launch smoke is not meaningful"], "warnings": ["needs manual UX review"]}),
                },
            )
            self.assertEqual(review["status"], "blocked", review)
            self.assertEqual(review["model_findings"]["status"], "blocked")
            self.assertIn("GreenfieldReviewer model blocker: launch smoke is not meaningful", review["blockers"])
            self.assertIn("needs manual UX review", review["warnings"])

    def test_greenfield_review_blocks_missing_architecture_guidance_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".ceraxia_greenfield_workspace").write_text("created-by=ceraxia-code-brigade\n", encoding="utf-8")
            (repo / "README.md").write_text(
                "# demo\n\n```bash\npython app.py\n```\n\n```bash\npython -m unittest test_app.py\n```\n",
                encoding="utf-8",
            )
            (repo / "app.py").write_text("VALUE = 'ready'\n\n\ndef main():\n    return VALUE\n", encoding="utf-8")
            (repo / "test_app.py").write_text(
                "import unittest\n"
                "import app\n\n"
                "class AppTests(unittest.TestCase):\n"
                "    def test_main(self):\n"
                "        self.assertEqual(app.main(), 'ready')\n",
                encoding="utf-8",
            )
            project = {
                "project_name": "demo",
                "project_type": "cli_tool",
                "template_id": "python_cli_basic",
                "expected_files": [".ceraxia_greenfield_workspace", "README.md", "app.py", "test_app.py"],
                "run_commands": ["python app.py"],
                "verification_commands": ["python -m unittest test_app.py"],
                "entrypoints": [{"name": "app", "command": "python app.py", "path": "app.py"}],
                "artifact_contract": {"source_files": ["app.py"], "test_files": ["test_app.py"], "manifest_files": []},
                "definition_of_done": ["README documents commands", "behavior is tested"],
                "architecture_plan": {
                    "model_guidance": {
                        "ok": True,
                        "status": "answered",
                        "content": json.dumps({"evidence_required": ["app/models.py for domain model", "test_app.py for verification"]}),
                    }
                },
                "implementation_plan": {
                    "module_sequence": [
                        {"path": "app.py", "code_synthesis_contract": {"kind": "code_brigade_greenfield_module_synthesis_contract"}},
                        {"path": "test_app.py", "code_synthesis_contract": {"kind": "code_brigade_greenfield_module_synthesis_contract"}},
                    ],
                    "anti_stub_policy": {"forbidden_markers": ["TODO", "pass"]},
                },
                "implementation_trace": {
                    "rows": [
                        {
                            "requirement": "return ready",
                            "file": "app.py",
                            "verification_files": ["test_app.py"],
                            "synthesis_contract_kind": "code_brigade_greenfield_module_synthesis_contract",
                        }
                    ]
                },
                "module_contracts": [
                    {"module": "app", "path": "app.py", "responsibility": "ready behavior", "requirements": ["return ready"]},
                    {"module": "test_app", "path": "test_app.py", "responsibility": "test ready behavior", "requirements": ["prove ready behavior"]},
                ],
                "scenario_plan": {
                    "kind": "code_brigade_greenfield_scenario_plan",
                    "contract_version": "eye-mechanicum.v1",
                    "status": "planned",
                    "scenario_count": 1,
                    "rows": [
                        {
                            "id": "ready",
                            "description": "Ready path works",
                            "steps": ["call main"],
                            "required_markers": ["ready"],
                            "evidence_files": ["app.py", "test_app.py"],
                        }
                    ],
                },
            }
            review = review_greenfield_project(
                repo,
                project,
                {"status": "not_required", "blockers": [], "warnings": []},
                {"status": "passed", "results": [{"command": "python -m unittest test_app.py", "status": "passed"}]},
                lambda role, payload, instructions: {"ok": True, "status": "answered", "content": "{}"},
            )
            self.assertEqual(review["status"], "blocked", review)
            self.assertEqual(review["architecture_guidance_review"]["status"], "blocked")
            self.assertTrue(any("app/models.py" in blocker for blocker in review["blockers"]))

    def test_greenfield_architecture_guidance_resolves_expected_file_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            tests = repo / "tests"
            tests.mkdir()
            (repo / "game.js").write_text("requestAnimationFrame(gameLoop);\n", encoding="utf-8")
            (tests / "test_browser_game.py").write_text("def test_game():\n    assert True\n", encoding="utf-8")
            project = {
                "expected_files": ["game.js", "tests/test_browser_game.py"],
                "architecture_plan": {
                    "model_guidance": {
                        "content": json.dumps({"evidence_required": ["game.js loop structure", "test_browser_game.py execution plan"]})
                    }
                },
            }
            review = review_greenfield_project(
                repo,
                project,
                {"status": "not_required", "blockers": [], "warnings": []},
                {"status": "passed", "results": [{"command": "python -m unittest discover tests", "status": "passed"}]},
                lambda role, payload, instructions: {"ok": True, "status": "answered", "content": "{}"},
            )
            self.assertNotIn("architecture guidance required missing evidence file: test_browser_game.py", review["blockers"])
            arch_review = review["architecture_guidance_review"]
            self.assertEqual(arch_review["status"], "passed", arch_review)
            self.assertIn("tests/test_browser_game.py", {row["path"] for row in arch_review["rows"]})

    def test_greenfield_architecture_guidance_review_uses_model_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "domain.py").write_text("class Ledger: pass\n", encoding="utf-8")
            tests = repo / "tests"
            tests.mkdir()
            (tests / "test_domain.py").write_text("def test_domain():\n    assert True\n", encoding="utf-8")
            project = {
                "expected_files": ["domain.py", "tests/test_domain.py"],
                "architecture_plan": {
                    "model_guidance": {"content": "unstructured prose retained for humans"},
                    "model_constraints": {
                        "status": "parsed",
                        "evidence_required": ["domain.py ledger model", "test_domain.py domain verification"],
                    },
                },
            }
            review = review_greenfield_project(
                repo,
                project,
                {"status": "not_required", "blockers": [], "warnings": []},
                {"status": "passed", "results": [{"command": "python -m unittest discover tests", "status": "passed"}]},
                lambda role, payload, instructions: {"ok": True, "status": "answered", "content": "{}"},
            )
            arch_review = review["architecture_guidance_review"]
            self.assertEqual(arch_review["status"], "passed", arch_review)
            self.assertIn("tests/test_domain.py", {row["path"] for row in arch_review["rows"]})

    def test_greenfield_artifact_review_blocks_unwired_frontend_assets_and_weak_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "index.html").write_text("<main>ready</main><script src=\"app.js\"></script>\n", encoding="utf-8")
            (repo / "app.js").write_text("function render() { return true; }\n", encoding="utf-8")
            (repo / "state.js").write_text("function createState() { return {}; }\n", encoding="utf-8")
            tests = repo / "tests"
            tests.mkdir()
            (tests / "test_static_site.py").write_text("def test_page():\n    value = True\n", encoding="utf-8")
            project = {
                "template_id": "static_site",
                "artifact_contract": {
                    "source_files": ["index.html", "app.js", "state.js"],
                    "test_files": ["tests/test_static_site.py"],
                },
                "module_contracts": [
                    {"path": "state.js", "responsibility": "state module", "requirements": ["create state"]},
                ],
            }
            review = artifact_review_greenfield_project(repo, project)
            self.assertEqual(review["status"], "blocked", review)
            self.assertTrue(any("unreferenced static asset: state.js" in item for item in review["blockers"]))
            self.assertTrue(any("assertionless test file" in item for item in review["blockers"]))

    def test_greenfield_artifact_review_checks_local_agent_module_wiring(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            package = repo / "agent_demo"
            package.mkdir()
            (package / "registry.py").write_text("ACTION_REGISTRY = {}\n", encoding="utf-8")
            (package / "schema.py").write_text("def validate_payload(payload=None):\n    return {}\n", encoding="utf-8")
            (package / "session.py").write_text("class AgentSession:\n    pass\n", encoding="utf-8")
            (package / "runner.py").write_text("def run_action():\n    return {'status': 'ok'}\n", encoding="utf-8")
            (package / "contract.py").write_text("def build_tool_result():\n    return {'status': 'ok'}\n", encoding="utf-8")
            (package / "tool.py").write_text("def main():\n    print('ok')\n", encoding="utf-8")
            tests = repo / "tests"
            tests.mkdir()
            (tests / "test_contract.py").write_text("def test_contract():\n    assert True\n", encoding="utf-8")
            project = {
                "template_id": "local_agent_tool",
                "artifact_contract": {
                    "source_files": [
                        "agent_demo/registry.py",
                        "agent_demo/schema.py",
                        "agent_demo/session.py",
                        "agent_demo/runner.py",
                        "agent_demo/contract.py",
                        "agent_demo/tool.py",
                    ],
                    "test_files": ["tests/test_contract.py"],
                },
                "module_contracts": [],
            }
            review = artifact_review_greenfield_project(repo, project)
            self.assertEqual(review["status"], "blocked", review)
            self.assertTrue(any("runner missing import: .registry" in item for item in review["blockers"]))
            self.assertTrue(any("contract facade not wired to runner" in item for item in review["blockers"]))
            self.assertTrue(any("CLI not wired to runner" in item for item in review["blockers"]))

    def test_greenfield_artifact_review_blocks_fake_browser_game(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "index.html").write_text("<main>Score: 0</main><script src=\"game.js\"></script>\n", encoding="utf-8")
            (repo / "game.js").write_text("function renderGame() { return true; }\n", encoding="utf-8")
            (repo / "styles.css").write_text("body { margin: 0; }\n", encoding="utf-8")
            tests = repo / "tests"
            tests.mkdir()
            (tests / "test_browser_game.py").write_text("def test_game_contract():\n    assert True\n", encoding="utf-8")
            project = {
                "template_id": "static_browser_game",
                "artifact_contract": {
                    "source_files": ["index.html", "styles.css", "game.js"],
                    "test_files": ["tests/test_browser_game.py"],
                },
                "module_contracts": [
                    {"path": "index.html", "responsibility": "browser game entrypoint", "requirements": ["canvas", "score"]},
                    {"path": "game.js", "responsibility": "interactive game loop", "requirements": ["keyboard controls", "animation loop"]},
                ],
            }
            review = artifact_review_greenfield_project(repo, project)
            self.assertEqual(review["status"], "blocked", review)
            self.assertTrue(any("canvas#game" in item for item in review["blockers"]))
            self.assertTrue(any("animation loop" in item for item in review["blockers"]))
            self.assertTrue(any("keyboard input" in item for item in review["blockers"]))

    def test_greenfield_scenario_review_blocks_missing_behavior_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = architect_build_greenfield_project_brief("Создай CLI калькулятор `scenario-calc`.")
            for item in project["files"]:
                if not isinstance(item, dict) or not item.get("path"):
                    continue
                path = repo / str(item["path"])
                path.parent.mkdir(parents=True, exist_ok=True)
                content = str(item.get("content") or "")
                if str(item["path"]).endswith("core.py"):
                    content = content.replace("division by zero", "zero divisor")
                if str(item["path"]) == "tests/test_core.py":
                    content = content.replace("test_division_by_zero_is_rejected", "test_zero_rejection")
                    content = content.replace("division by zero", "zero divisor")
                path.write_text(content, encoding="utf-8")
            review = review_greenfield_scenarios(repo, project)
            self.assertEqual(review["status"], "blocked", review)
            self.assertTrue(any("calculator_error_handling" in blocker for blocker in review["blockers"]))

    def test_greenfield_feature_worker_detects_task_features(self) -> None:
        feature_ids = {feature["id"] for feature in infer_acceptance_features("notes api issue tracker operations dashboard todo calculator csv summary sales analytics pipeline local agent tool router telegram bot /start /help vite counter app text utils library")}
        self.assertEqual(feature_ids, {"calculator_operations", "todo_list", "notes_api", "issue_tracker_api", "operations_dashboard_api", "csv_summary", "sales_analytics_pipeline", "local_agent_command_router", "telegram_command_bot", "vite_counter_app", "python_text_utils_library"})
        kanban_feature_ids = {feature["id"] for feature in infer_acceptance_features("kanban project board with todo doing done columns and column counters")}
        self.assertEqual(kanban_feature_ids, {"kanban_board_frontend"})

    def test_greenfield_feature_worker_does_not_treat_todo_remaining_counter_as_vite_app(self) -> None:
        feature_ids = {feature["id"] for feature in infer_acceptance_features("Создай todo list со счетчиком оставшихся задач и фильтром active/completed.")}
        self.assertEqual(feature_ids, {"todo_list"})
        dashboard_feature_ids = {feature["id"] for feature in infer_acceptance_features("Создай dashboard с карточками задач, фильтрами active/done/all, счетчиком оставшихся задач, toggle done и localStorage.")}
        self.assertEqual(dashboard_feature_ids, {"todo_list"})

    def test_greenfield_architect_routes_csv_pipeline_before_api_keyword(self) -> None:
        project = architect_build_greenfield_project_brief("Создай data sales analytics pipeline `sales-demo` для CSV с CLI JSON API output.")
        self.assertEqual(project["template_id"], "data_processing_tool")
        self.assertTrue(any(feature["id"] == "sales_analytics_pipeline" for feature in project["acceptance_features"]))

    def test_greenfield_architect_owns_project_brief_and_plan(self) -> None:
        def guidance(role: str, payload: dict, instructions: str) -> dict:
            if role == "GreenfieldArchitect":
                return {
                    "ok": True,
                    "status": "answered",
                    "content": "```json\n"
                    + json.dumps(
                        {
                            "guidance": {
                                "missing_modules": ["CalculatorCore"],
                                "verification_gaps": ["CLI smoke command"],
                                "scaffold_risks": ["argument parsing drift"],
                                "next_steps": ["keep tests paired with core"],
                            },
                            "evidence_required": ["tests/test_core.py proves calculator behavior"],
                        }
                    )
                    + "\n```",
                }
            return {"ok": True, "status": "answered", "content": "{}"}

        project = architect_build_greenfield_project_brief("Создай CLI калькулятор `architect-calc`.", request_guidance=guidance)
        self.assertEqual(project["kind"], "code_brigade_greenfield_project_brief")
        self.assertEqual(project["architecture_plan"]["selected_template"], "python_cli_basic")
        constraints = project["architecture_plan"]["model_constraints"]
        self.assertEqual(constraints["status"], "parsed")
        self.assertEqual(constraints["missing_modules"], ["CalculatorCore"])
        self.assertEqual(constraints["verification_gaps"], ["CLI smoke command"])
        self.assertEqual(constraints["evidence_required"], ["tests/test_core.py proves calculator behavior"])
        self.assertEqual(project["implementation_plan"]["kind"], "code_brigade_greenfield_implementation_plan")
        self.assertEqual(project["implementation_plan"]["role"], "GreenfieldImplementationWorker")
        self.assertEqual(project["implementation_plan"]["synthesis_policy"]["mode"], "module_by_module_llm_contract")
        self.assertEqual(project["implementation_feature_report"]["kind"], "code_brigade_greenfield_implementation_feature_report")
        self.assertEqual(project["implementation_trace"]["kind"], "code_brigade_greenfield_implementation_trace")
        self.assertEqual(project["scenario_plan"]["kind"], "code_brigade_greenfield_scenario_plan")
        self.assertGreaterEqual(project["scenario_plan"]["scenario_count"], 2)
        self.assertGreater(project["implementation_trace"]["requirement_trace_count"], 0)
        self.assertIn("architecture_plan.json", project["expected_files"])
        self.assertIn("implementation_trace.json", project["expected_files"])
        self.assertIn("scenario_plan.json", project["expected_files"])
        self.assertTrue(project["implementation_plan"]["module_sequence"])
        self.assertTrue(all("code_synthesis_contract" in row for row in project["implementation_plan"]["module_sequence"]))

    def test_greenfield_dependency_worker_reports_manager_status(self) -> None:
        none_status = dependency_manager_status("none")
        self.assertFalse(none_status["required"])
        self.assertTrue(none_status["available"])
        pip_status = dependency_manager_status("pip")
        self.assertTrue(pip_status["required"])
        self.assertIn(pip_status["binary"], {"python", "python3", str(Path(pip_status["path"]))})
        self.assertTrue(pip_status["candidates"])

    def test_greenfield_dependency_worker_falls_back_to_python3_for_pip(self) -> None:
        def fake_which(binary: str) -> str | None:
            if binary == "python3":
                return "/usr/bin/python3"
            return None

        with patch("greenfield_dependency_worker.shutil.which", fake_which):
            pip_status = dependency_manager_status("pip")
        self.assertTrue(pip_status["required"])
        self.assertTrue(pip_status["available"])
        self.assertEqual(pip_status["binary"], "python3")
        self.assertEqual(pip_status["path"], "/usr/bin/python3")

    def test_greenfield_implementation_worker_owns_plan_and_trace(self) -> None:
        plan = worker_build_implementation_worker_plan(
            "Создай библиотеку.",
            "python_library",
            [
                {"module": "demo.core", "path": "demo/core.py", "responsibility": "library logic", "requirements": ["normalize whitespace"]},
                {"module": "tests.test_library", "path": "tests/test_library.py", "responsibility": "verification", "requirements": ["prove normalization"]},
            ],
            ["demo/core.py", "tests/test_library.py"],
        )
        self.assertEqual(plan["kind"], "code_brigade_greenfield_implementation_plan")
        self.assertEqual(plan["role"], "GreenfieldImplementationWorker")
        self.assertEqual(plan["model_guidance"]["status"], "not_requested")
        self.assertEqual(plan["synthesis_policy"]["mode"], "module_by_module_llm_contract")
        self.assertEqual(plan["module_sequence"][0]["paired_tests"], ["tests/test_library.py"])
        self.assertEqual(plan["module_sequence"][0]["code_synthesis_contract"]["kind"], "code_brigade_greenfield_module_synthesis_contract")
        self.assertEqual(plan["module_sequence"][0]["code_synthesis_contract"]["rollback_scope"]["allowed_source_files"], ["demo/core.py"])
        trace = worker_build_implementation_trace(plan)
        self.assertEqual(trace["kind"], "code_brigade_greenfield_implementation_trace")
        self.assertEqual(trace["status"], "complete")
        self.assertGreaterEqual(trace["requirement_trace_count"], 2)
        self.assertTrue(all(row["synthesis_contract_kind"] == "code_brigade_greenfield_module_synthesis_contract" for row in trace["rows"]))

    def test_greenfield_implementation_trace_uses_precise_workflow_tests(self) -> None:
        plan = worker_build_implementation_worker_plan(
            "Создай sales analytics pipeline.",
            "data_processing_tool",
            [
                {"module": "sales.loader", "path": "sales/loader.py", "responsibility": "load records", "requirements": ["parse CSV rows"]},
                {"module": "sales.analyzer", "path": "sales/analyzer.py", "responsibility": "analyze records", "requirements": ["group totals by region"]},
                {"module": "tests.test_sales_pipeline", "path": "tests/test_sales_pipeline.py", "responsibility": "workflow verification", "requirements": ["prove load filter group workflow"]},
            ],
            ["sales/loader.py", "sales/analyzer.py", "tests/test_processor.py", "tests/test_sales_pipeline.py"],
        )
        by_path = {row["path"]: row for row in plan["module_sequence"]}
        self.assertEqual(by_path["sales/loader.py"]["paired_tests"], ["tests/test_sales_pipeline.py"])
        self.assertEqual(by_path["sales/analyzer.py"]["paired_tests"], ["tests/test_sales_pipeline.py"])
        self.assertEqual(by_path["tests/test_sales_pipeline.py"]["paired_tests"], ["tests/test_sales_pipeline.py"])
        trace = worker_build_implementation_trace(plan)
        for row in trace["rows"]:
            if row["file"] in {"sales/loader.py", "sales/analyzer.py"}:
                self.assertEqual(row["verification_files"], ["tests/test_sales_pipeline.py"])

    def test_greenfield_model_runtime_defaults_allow_code_synthesis_latency_and_tokens(self) -> None:
        implementation_defaults = greenfield_model_runtime_defaults(
            "GreenfieldImplementationWorker",
            {"module_synthesis_contract": {"path": "demo/core.py"}},
        )
        self.assertGreaterEqual(int(implementation_defaults["EYE_MODEL_TIMEOUT_SEC"]), 120)
        self.assertGreaterEqual(int(implementation_defaults["EYE_MODEL_MAX_TOKENS"]), 4096)
        architect_defaults = greenfield_model_runtime_defaults("GreenfieldArchitect", {})
        self.assertGreaterEqual(int(architect_defaults["EYE_MODEL_TIMEOUT_SEC"]), 30)
        self.assertGreaterEqual(int(architect_defaults["EYE_MODEL_MAX_TOKENS"]), 1024)

    def test_greenfield_module_synthesis_applies_valid_model_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = architect_build_greenfield_project_brief("Создай CLI калькулятор `synthesis-calc`.")
            module = project["implementation_plan"]["module_sequence"][0]
            path = repo / module["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("def old():\n    return 'old'\n", encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                contract = payload["module_synthesis_contract"]
                is_test = "test" in Path(contract["path"]).name.lower() or "/tests/" in f"/{contract['path']}"
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "path": contract["path"],
                            "content": (
                                "import unittest\n\nclass GeneratedTests(unittest.TestCase):\n    def test_generated(self):\n        self.assertTrue(True)\n"
                                if is_test
                                else "def main():\n    return 'model-generated'\n"
                            ),
                            "requirements_satisfied": contract["requirements"],
                            "tests_to_update": contract["paired_tests"],
                            "notes": "valid module synthesis",
                        }
                    ),
                }

            report = execute_module_synthesis_contracts(repo, project, guidance)
            self.assertEqual(report["status"], "applied", report)
            self.assertEqual(report["applied_count"], len(project["implementation_plan"]["module_sequence"]))
            self.assertIn(module["path"], report["changed_files"])
            self.assertEqual(path.read_text(encoding="utf-8"), "def main():\n    return 'model-generated'\n")

    def test_greenfield_module_synthesis_reformats_invalid_model_json_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = architect_build_greenfield_project_brief("Создай CLI калькулятор `reformat-calc`.")
            module = project["implementation_plan"]["module_sequence"][0]
            path = repo / module["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("def old():\n    return 'old'\n", encoding="utf-8")
            calls: list[str] = []

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                contract = payload["module_synthesis_contract"]
                calls.append("reformat" if "invalid_model_content" in payload else "initial")
                if "invalid_model_content" in payload:
                    is_test = "test" in Path(contract["path"]).name.lower() or "/tests/" in f"/{contract['path']}"
                    return {
                        "ok": True,
                        "status": "answered",
                        "content": json.dumps(
                            {
                                "path": contract["path"],
                                "content": (
                                    "import unittest\n\nclass ReformattedTests(unittest.TestCase):\n    def test_reformatted(self):\n        self.assertTrue(True)\n"
                                    if is_test
                                    else "def main():\n    return 'reformatted'\n"
                                ),
                                "requirements_satisfied": contract["requirements"],
                                "tests_to_update": contract["paired_tests"],
                                "notes": "reformatted valid JSON",
                            }
                        ),
                    }
                return {
                    "ok": True,
                    "status": "answered",
                    "content": '{"path": "' + contract["path"] + '", "content": "const root = document.getElementById("root");"}',
                }

            report = execute_module_synthesis_contracts(repo, project, guidance)
            self.assertEqual(report["status"], "applied", report)
            self.assertIn("reformat", calls)
            self.assertEqual(path.read_text(encoding="utf-8"), "def main():\n    return 'reformatted'\n")
            first_row = report["rows"][0]
            self.assertEqual(first_row["status"], "applied", first_row)
            self.assertEqual(first_row["reformat_guidance_status"], "answered")
            self.assertIn("model output required JSON reformat retry", first_row["warnings"])

    def test_greenfield_file_set_synthesis_applies_source_and_tests_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = architect_build_greenfield_project_brief("Создай CLI калькулятор `file-set-calc`.")
            source_module = next(row for row in project["implementation_plan"]["module_sequence"] if row["path"] in project["implementation_plan"]["source_files"])
            test_module = next(row for row in project["implementation_plan"]["module_sequence"] if row["path"] in project["implementation_plan"]["test_files"])
            source_path = source_module["path"]
            test_path = test_module["path"]
            for rel_path in (source_path, test_path):
                path = repo / rel_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("old\n", encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                source_requirements = source_module["requirements"]
                test_requirements = test_module["requirements"]
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "files": [
                                {
                                    "path": source_path,
                                    "content": "def main():\n    return 'ready'\n",
                                    "requirements_satisfied": source_requirements,
                                    "notes": "source",
                                },
                                {
                                    "path": test_path,
                                    "content": "import unittest\n\nclass GeneratedTests(unittest.TestCase):\n    def test_ready(self):\n        self.assertTrue(True)\n",
                                    "requirements_satisfied": test_requirements,
                                    "notes": "tests",
                                },
                            ],
                            "notes": "coordinated source and tests",
                        }
                    ),
                }

            report = execute_file_set_synthesis_contract(repo, project, guidance)
            self.assertEqual(report["status"], "applied", report)
            self.assertEqual(set(report["changed_files"]), {source_path, test_path})
            self.assertIn("return 'ready'", (repo / source_path).read_text(encoding="utf-8"))
            self.assertIn("GeneratedTests", (repo / test_path).read_text(encoding="utf-8"))

    def test_greenfield_file_set_synthesis_rejects_source_without_paired_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = architect_build_greenfield_project_brief("Создай CLI проект `stale-test-calc`, который печатает model-ready.")
            source_module = next(row for row in project["implementation_plan"]["module_sequence"] if row["path"].endswith("/core.py"))
            source_path = source_module["path"]
            test_path = source_module["paired_tests"][0]
            for rel_path, content in (
                (source_path, "def run() -> str:\n    return 'ready'\n"),
                (test_path, "import unittest\n\nclass OldTests(unittest.TestCase):\n    def test_old(self):\n        self.assertTrue(True)\n"),
            ):
                path = repo / rel_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "files": [
                                {
                                    "path": source_path,
                                    "content": "def run() -> str:\n    return 'model-ready'\n",
                                    "requirements_satisfied": source_module["requirements"],
                                    "notes": "source only",
                                }
                            ],
                            "notes": "omitted paired tests",
                        }
                    ),
                }

            report = execute_file_set_synthesis_contract(repo, project, guidance)
            self.assertEqual(report["status"], "rejected", report)
            self.assertTrue(any("omitted paired tests" in blocker for blocker in report["blockers"]), report)
            self.assertIn("return 'ready'", (repo / source_path).read_text(encoding="utf-8"))
            self.assertIn("OldTests", (repo / test_path).read_text(encoding="utf-8"))

    def test_greenfield_module_synthesis_rejects_stale_test_behavior_marker(self) -> None:
        self.assertEqual(task_behavior_markers("Создай CLI проект `demo`, который печатает model-ready."), ["model-ready"])
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = architect_build_greenfield_project_brief("Создай CLI проект `marker-calc`, который печатает model-ready.")
            test_module = next(row for row in project["implementation_plan"]["module_sequence"] if row["path"] == "tests/test_core.py")
            test_path = repo / test_module["path"]
            test_path.parent.mkdir(parents=True, exist_ok=True)
            test_path.write_text("old\n", encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                contract = payload["module_synthesis_contract"]
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "path": contract["path"],
                            "content": (
                                "import unittest\n\n"
                                "from marker_calc.core import run\n\n\n"
                                "class CoreTests(unittest.TestCase):\n"
                                "    def test_run(self):\n"
                                "        self.assertEqual(run(), \"ready\")\n"
                            ),
                            "requirements_satisfied": contract["requirements"],
                            "tests_to_update": contract["paired_tests"],
                            "notes": "stale test behavior",
                        }
                    ),
                }

            report = execute_module_synthesis_contracts(repo, project, guidance)
            stale_row = next(row for row in report["rows"] if row["path"] == "tests/test_core.py")
            self.assertEqual(stale_row["status"], "rejected", report)
            self.assertTrue(any("task behavior markers" in blocker for blocker in stale_row["blockers"]), report)
            self.assertEqual(test_path.read_text(encoding="utf-8"), "old\n")

    def test_greenfield_module_synthesis_reconciles_file_set_covered_rejected_module(self) -> None:
        file_set_report = {
            "status": "applied",
            "changed_files": ["demo/report.py", "tests/test_report.py"],
            "semantic_quality_rows": [
                {"path": "demo/report.py", "status": "passed"},
                {"path": "tests/test_report.py", "status": "passed"},
            ],
        }
        module_report = {
            "status": "blocked",
            "applied_count": 1,
            "blocked_count": 1,
            "rows": [
                {"module": "demo.core", "path": "demo/core.py", "status": "applied", "blockers": []},
                {"module": "demo.report", "path": "demo/report.py", "status": "rejected", "blockers": ["model output is not valid JSON object"]},
            ],
        }
        reconciled = reconcile_module_synthesis_with_file_set(
            file_set_report,
            module_report,
            {"status": "passed"},
            {"status": "passed"},
        )
        self.assertEqual(reconciled["status"], "applied", reconciled)
        self.assertEqual(reconciled["file_set_reconciled_count"], 1)
        self.assertEqual(reconciled["blocked_count"], 0)
        self.assertEqual(model_synthesis_blockers(file_set_report, reconciled), [])
        report_row = next(row for row in reconciled["rows"] if row["path"] == "demo/report.py")
        self.assertEqual(report_row["status"], "covered_by_file_set")

    def test_greenfield_project_executor_uses_injected_model_for_full_synthesis_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            task = "Создай новый Python CLI проект `model-synth-demo`."
            brief = project_creation_brief(repo, task)

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                project_name = str(payload.get("project_name") or "model-synth-demo")
                package = project_name.replace("-", "_")
                core_path = f"{package}/core.py"
                cli_path = f"{package}/cli.py"
                test_path = "tests/test_core.py"
                module_sequence = payload.get("synthesis_contract", {}).get("module_sequence", [])
                requirements_by_path = {
                    str(row.get("path") or ""): [str(item) for item in row.get("requirements", []) if isinstance(item, str)]
                    for row in module_sequence
                    if isinstance(row, dict)
                }
                if role == "GreenfieldImplementationWorker" and isinstance(payload.get("synthesis_contract"), dict):
                    return {
                        "ok": True,
                        "status": "answered",
                        "content": json.dumps(
                            {
                                "files": [
                                    {
                                        "path": core_path,
                                        "content": "def run() -> str:\n    return \"model-ready\"\n",
                                        "requirements_satisfied": requirements_by_path.get(core_path, []),
                                        "notes": "model generated core behavior",
                                    },
                                    {
                                        "path": cli_path,
                                        "content": (
                                            "from .core import run\n\n\n"
                                            "def main() -> None:\n"
                                            "    print(run())\n\n\n"
                                            "if __name__ == \"__main__\":\n"
                                            "    main()\n"
                                        ),
                                        "requirements_satisfied": requirements_by_path.get(cli_path, []),
                                        "notes": "model generated CLI behavior",
                                    },
                                    {
                                        "path": test_path,
                                        "content": (
                                            "import unittest\n\n"
                                            f"from {package}.core import run\n\n\n"
                                            "class CoreTests(unittest.TestCase):\n"
                                            "    def test_run_uses_model_generated_behavior(self):\n"
                                            "        self.assertEqual(run(), \"model-ready\")\n"
                                        ),
                                        "requirements_satisfied": requirements_by_path.get(test_path, []),
                                        "notes": "model generated test behavior",
                                    },
                                ],
                                "notes": "coordinated model file-set synthesis",
                            }
                        ),
                    }
                if role == "GreenfieldImplementationWorker" and isinstance(payload.get("module_synthesis_contract"), dict):
                    contract = payload["module_synthesis_contract"]
                    rel_path = str(contract["path"])
                    if rel_path == core_path:
                        content = "def run() -> str:\n    return \"model-ready\"\n"
                    elif rel_path == cli_path:
                        content = (
                            "from .core import run\n\n\n"
                            "def main() -> None:\n"
                            "    print(run())\n\n\n"
                            "if __name__ == \"__main__\":\n"
                            "    main()\n"
                        )
                    else:
                        content = (
                            "import unittest\n\n"
                            f"from {package}.core import run\n\n\n"
                            "class CoreTests(unittest.TestCase):\n"
                            "    def test_run_uses_model_generated_behavior(self):\n"
                            "        self.assertEqual(run(), \"model-ready\")\n"
                        )
                    return {
                        "ok": True,
                        "status": "answered",
                        "content": json.dumps(
                            {
                                "path": rel_path,
                                "content": content,
                                "requirements_satisfied": contract["requirements"],
                                "tests_to_update": contract["paired_tests"],
                                "notes": "model generated module implementation",
                            }
                        ),
                    }
                return {"ok": True, "status": "answered", "content": "{}"}

            result = execute_greenfield_project_brief(brief, guidance)
            self.assertEqual(result["status"], "implemented", result)
            project = result["greenfield_project"]
            self.assertEqual(project["file_set_synthesis_report"]["status"], "applied")
            self.assertEqual(project["implementation_synthesis_report"]["status"], "applied")
            self.assertEqual(project["verification"]["status"], "passed")
            self.assertEqual(project["greenfield_review"]["status"], "passed")
            self.assertEqual(project["greenfield_run_report"]["model_guidance_ledger_status"], "complete")
            self.assertEqual((repo / f"model_synth_demo/core.py").read_text(encoding="utf-8"), "def run() -> str:\n    return \"model-ready\"\n")
            self.assertIn("model-ready", (repo / "tests/test_core.py").read_text(encoding="utf-8"))
            ledger = json.loads((repo / "greenfield_model_guidance_ledger.json").read_text(encoding="utf-8"))
            self.assertTrue(all(row["status"] != "missing" for row in ledger["entries"]))

    def test_greenfield_project_executor_blocks_when_model_synthesis_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(repo, "Создай новый Python CLI проект `blocked-model-demo`.")

            def unavailable_guidance(role: str, payload: dict, instructions: str) -> dict:
                return {"ok": False, "status": "unavailable", "error": "test model unavailable", "content": ""}

            result = execute_greenfield_project_brief(brief, unavailable_guidance)
            self.assertEqual(result["status"], "blocked", result)
            self.assertTrue(any("model synthesis" in blocker for blocker in result["blockers"]))
            project = result["greenfield_project"]
            self.assertEqual(project["file_set_synthesis_report"]["status"], "model_unavailable")
            self.assertEqual(project["implementation_synthesis_report"]["status"], "model_unavailable")

    def test_greenfield_file_set_synthesis_rejects_out_of_scope_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = architect_build_greenfield_project_brief("Создай CLI калькулятор `bad-file-set-calc`.")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "files": [
                                {
                                    "path": "outside.py",
                                    "content": "print('bad')\n",
                                    "requirements_satisfied": [],
                                    "notes": "outside",
                                }
                            ],
                            "notes": "bad",
                        }
                    ),
                }

            report = execute_file_set_synthesis_contract(repo, project, guidance)
            self.assertEqual(report["status"], "rejected", report)
            self.assertFalse((repo / "outside.py").exists())

    def test_greenfield_module_synthesis_rejects_bad_model_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = architect_build_greenfield_project_brief("Создай CLI калькулятор `bad-synthesis-calc`.")
            module = project["implementation_plan"]["module_sequence"][0]
            path = repo / module["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("def keep():\n    return 'safe'\n", encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "path": "../outside.py",
                            "content": "# TODO placeholder\n",
                            "requirements_satisfied": [],
                            "tests_to_update": [],
                            "notes": "bad module synthesis",
                        }
                    ),
                }

            report = execute_module_synthesis_contracts(repo, project, guidance)
            self.assertEqual(report["status"], "blocked", report)
            self.assertEqual(path.read_text(encoding="utf-8"), "def keep():\n    return 'safe'\n")
            self.assertTrue(any(row["status"] == "rejected" for row in report["rows"]))

    def test_greenfield_synthesis_quality_blocks_weak_source_and_assertionless_tests(self) -> None:
        self.assertEqual(generated_file_quality("app.py", "VALUE = 1\n", ["return ready"])["status"], "blocked")
        self.assertEqual(generated_file_quality("tests/test_app.py", "def test_ready():\n    pass\n", ["prove ready"])["status"], "blocked")
        narration_quality = generated_file_quality(
            "billing/invoice.py",
            "def build_invoice():\n    # The test oracle expects this exact summary.\n    return 'ready'\n",
            ["build invoice"],
        )
        self.assertEqual(narration_quality["status"], "blocked")
        self.assertTrue(any("test/repair narration" in item for item in narration_quality["blockers"]))
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = architect_build_greenfield_project_brief("Создай CLI калькулятор `quality-calc`.")
            source_module = next(row for row in project["implementation_plan"]["module_sequence"] if row["path"] in project["implementation_plan"]["source_files"])

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "path": source_module["path"],
                            "content": "VALUE = 1\n",
                            "requirements_satisfied": source_module["requirements"],
                            "tests_to_update": source_module["paired_tests"],
                            "notes": "weak",
                        }
                    ),
                }

            report = execute_module_synthesis_contracts(repo, project, guidance)
            self.assertEqual(report["status"], "blocked", report)
            self.assertTrue(any("semantic quality blocked" in "; ".join(row["blockers"]) for row in report["rows"] if row["blockers"]))

    def test_greenfield_domain_quality_blocks_bad_domain_outputs(self) -> None:
        self.assertEqual(
            generated_file_quality("app/main.py", "def run():\n    return True\n", ["health"], {"template_id": "python_fastapi_service"})["status"],
            "blocked",
        )
        self.assertEqual(
            generated_file_quality("bot_demo/bot.py", "def main():\n    print('ready')\n", ["bot"], {"template_id": "telegram_bot_python"})["status"],
            "blocked",
        )
        self.assertEqual(
            generated_file_quality("data_demo/processor.py", "def summarize_rows(text):\n    return {'rows': 0}\n", ["parse CSV"], {"template_id": "data_processing_tool"})["status"],
            "blocked",
        )
        self.assertEqual(
            generated_file_quality("agent_demo/contract.py", "def build_tool_result(task):\n    return 'ready'\n", ["structured result"], {"template_id": "local_agent_tool"})["status"],
            "blocked",
        )
        self.assertEqual(
            generated_file_quality("src/main.jsx", "const value = 1;\n", ["render ready"], {"template_id": "node_vite_app"})["status"],
            "blocked",
        )

    def test_greenfield_contract_requires_module_synthesis_contracts(self) -> None:
        project = architect_build_greenfield_project_brief("Создай CLI калькулятор `contract-calc`.")
        self.assertEqual(validate_greenfield_project_brief(project), [])
        broken_plan = json.loads(json.dumps(project, ensure_ascii=False))
        broken_plan["implementation_plan"]["module_sequence"][0].pop("code_synthesis_contract", None)
        self.assertTrue(any("module synthesis contract is required" in item for item in validate_greenfield_project_brief(broken_plan)))
        broken_trace = json.loads(json.dumps(project, ensure_ascii=False))
        broken_trace["implementation_trace"]["rows"][0].pop("synthesis_contract_kind", None)
        self.assertTrue(any("implementation_trace synthesis contract is required" in item for item in validate_greenfield_project_brief(broken_trace)))

    def test_greenfield_verification_worker_builds_stable_failure_signature(self) -> None:
        signature = verification_failure_signature(
            {
                "results": [
                    {"command": "python -m unittest", "status": "failed", "stderr": "x" * 700},
                    {"command": "python -m py_compile app.py", "status": "passed", "stderr": ""},
                ]
            }
        )
        self.assertIn("python -m unittest", signature)
        self.assertIn('"status": "failed"', signature)
        self.assertLess(len(signature), 800)

    def test_greenfield_repair_guidance_exposes_supported_bounded_operations(self) -> None:
        captured: dict[str, object] = {}

        def guidance(role: str, payload: dict, instructions: str) -> dict:
            captured["role"] = role
            captured["payload"] = payload
            captured["instructions"] = instructions
            return {"ok": True, "status": "answered", "content": "{}"}

        repair_guidance_for_verification(
            {"project_name": "demo", "template_id": "python_cli_basic", "template_contract": {"common_failure_fixes": ["repair tests"]}},
            {"status": "failed", "results": [{"command": "python -m unittest", "status": "failed"}]},
            "signature",
            guidance,
        )
        self.assertEqual(captured["role"], "GreenfieldRepairWorker")
        payload = captured["payload"]
        self.assertIsInstance(payload, dict)
        operation_types = {row["type"] for row in payload["supported_repair_operations"]}
        self.assertEqual(
            operation_types,
            {"remove_undefined_name_line", "replace_exact", "replace_return_expression", "replace_python_constant", "replace_function_body"},
        )
        self.assertIn("return JSON only", str(captured["instructions"]))
        self.assertIn("derive old_text", str(captured["instructions"]))
        self.assertIn("workspace_file_snapshots", str(captured["instructions"]))

    def test_greenfield_repair_guidance_includes_workspace_file_snapshots(self) -> None:
        captured: dict[str, object] = {}
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "calc.py").write_text("def add(left, right):\n    return left - right\n", encoding="utf-8")
            (repo / "test_calc.py").write_text("import calc\n", encoding="utf-8")
            project = {
                "project_name": "demo",
                "template_id": "python_cli_basic",
                "expected_files": ["calc.py", "test_calc.py", "README.md"],
                "template_contract": {},
            }

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                captured["payload"] = payload
                return {"ok": True, "status": "answered", "content": "{}"}

            repair_guidance_for_verification(project, {"status": "failed", "results": []}, "signature", guidance, repo)
        payload = captured["payload"]
        self.assertIsInstance(payload, dict)
        snapshots = payload["workspace_file_snapshots"]
        self.assertEqual([row["path"] for row in snapshots], ["calc.py", "test_calc.py"])
        self.assertIn("return left - right", snapshots[0]["content"])

    def test_greenfield_memory_worker_records_repair_learning(self) -> None:
        memory = build_greenfield_memory_record(
            {
                "project_name": "demo",
                "project_type": "cli_tool",
                "template_id": "python_cli_basic",
                "stack": {},
                "dependency_plan": {},
                "run_commands": ["python -m demo.cli"],
                "verification_commands": ["python -m unittest"],
                "acceptance_features": [{"id": "calculator_operations"}],
                "implementation_feature_report": {"implementation_strategy": "task-derived feature override"},
                "definition_of_done": ["tests pass", "README documents commands"],
            },
            {"status": "not_required", "blockers": [], "warnings": [], "manager_status": {}, "new_lockfiles": []},
            {
                "status": "passed",
                "stop_reason": "verification passed",
                "stop_condition_evidence": {"reason": "verification passed", "attempt_count": 1},
                "final_verification": {"results": [{"command": "python -m unittest", "status": "passed"}]},
                "attempts": [{"repair_execution": {"repaired_files": [{"path": "README.md"}]}}],
            },
            {"status": "passed", "blockers": [], "warnings": [], "semantic_review": {"status": "passed", "blockers": []}},
        )
        self.assertEqual(memory["kind"], "code_brigade_greenfield_memory_record")
        self.assertEqual(memory["repaired_files"], ["README.md"])
        self.assertEqual(memory["acceptance_feature_ids"], ["calculator_operations"])
        self.assertEqual(memory["acceptance_feature_coverage"]["status"], "covered")
        self.assertEqual(memory["definition_of_done_status"]["status"], "passed")
        self.assertEqual(memory["verification_results"], [{"command": "python -m unittest", "status": "passed"}])
        self.assertEqual(memory["verification_stop_condition_evidence"]["reason"], "verification passed")
        self.assertTrue(memory["reusable_learnings"])

    def test_greenfield_memory_worker_builds_reusable_index(self) -> None:
        records = [
            {
                "project_name": "api-demo",
                "project_type": "api_service",
                "template_id": "python_fastapi_service",
                "verification_status": "passed",
                "review_status": "passed",
                "scenario_review_status": "passed",
                "implementation_synthesis_status": "applied",
                "review_blockers": [],
                "dependency_blockers": [],
                "repaired_files": [],
                "reusable_learnings": ["keep README commands identical to run_commands and verification_commands"],
            },
            {
                "project_name": "game-demo",
                "project_type": "game",
                "template_id": "static_browser_game",
                "verification_status": "passed",
                "review_status": "blocked",
                "scenario_review_status": "blocked",
                "implementation_synthesis_status": "applied",
                "review_blockers": ["scenario browser_game_loop is missing behavior markers: score"],
                "dependency_blockers": [],
                "repaired_files": ["game.js"],
                "reusable_learnings": ["treat scenario_plan as the user-workflow contract and block review when source/test evidence misses required behavior markers"],
            },
        ]
        index = build_greenfield_memory_index(records)
        self.assertEqual(index["kind"], "code_brigade_greenfield_memory_index")
        self.assertEqual(index["record_count"], 2)
        self.assertEqual(index["templates_seen"]["python_fastapi_service"], 1)
        self.assertEqual(index["templates_seen"]["static_browser_game"], 1)
        self.assertEqual(index["status_counts"]["passed"], 1)
        self.assertEqual(index["status_counts"]["blocked"], 1)
        self.assertEqual(index["common_review_blockers"][0]["blocker"], "scenario browser_game_loop is missing behavior markers: score")
        self.assertTrue(index["reusable_learnings"])

    def test_greenfield_memory_worker_updates_workspace_index_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            first = {"project_name": "first", "project_type": "cli_tool", "template_id": "python_cli_basic", "verification_status": "passed", "review_status": "passed", "reusable_learnings": ["first learning"]}
            second = {"project_name": "second", "project_type": "game", "template_id": "static_browser_game", "verification_status": "passed", "review_status": "passed", "reusable_learnings": ["second learning"]}
            update_greenfield_memory_index(repo, first)
            index = update_greenfield_memory_index(repo, second)
            self.assertTrue((repo / "greenfield_memory_index.json").is_file())
            self.assertEqual(index["record_count"], 2)
            self.assertEqual([row["project_name"] for row in index["recent_runs"]], ["first", "second"])

    def test_greenfield_memory_worker_records_definition_of_done_evidence_matrix(self) -> None:
        memory = build_greenfield_memory_record(
            {
                "project_name": "demo",
                "project_type": "cli_tool",
                "template_id": "python_cli_basic",
                "stack": {},
                "dependency_plan": {},
                "run_commands": ["python app.py"],
                "verification_commands": ["python -m unittest test_app.py"],
                "definition_of_done": ["README documents commands"],
            },
            {"status": "not_required", "blockers": [], "warnings": [], "manager_status": {}, "new_lockfiles": []},
            {"status": "passed", "final_verification": {"results": []}, "attempts": []},
            {
                "status": "blocked",
                "definition_of_done_review": {
                    "status": "blocked",
                    "passed_count": 0,
                    "blocked_count": 1,
                    "rows": [
                        {
                            "item": "README documents commands",
                            "status": "blocked",
                            "evidence": ["README.md"],
                            "missing_evidence": ["README missing command: python -m unittest test_app.py"],
                        }
                    ],
                },
            },
        )
        dod = memory["definition_of_done_status"]
        self.assertEqual(dod["status"], "blocked")
        self.assertEqual(dod["blocked_count"], 1)
        self.assertEqual(dod["items"][0]["item"], "README documents commands")
        self.assertEqual(dod["items"][0]["missing_evidence"], ["README missing command: python -m unittest test_app.py"])

    def test_greenfield_scaffold_worker_writes_files_and_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            rows = normalize_project_file_rows(
                [
                    {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                    {"path": "pkg/app.py", "content": "def run():\n    return 'ready'\n"},
                    {"path": "tests/test_app.py", "content": "import unittest\n"},
                ]
            )
            report = scaffold_greenfield_files(repo, rows, greenfield_workspace_status(repo))
            self.assertEqual(report["status"], "implemented", report)
            self.assertTrue((repo / ".ceraxia_greenfield_workspace").exists())
            self.assertTrue((repo / "pkg/app.py").exists())
            self.assertIn("pkg/app.py", report["changed_files"])

    def test_greenfield_verification_loop_repairs_missing_template_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief("Создай новый CLI проект `repair-demo`.")
            for item in project["files"]:
                rel_path = item["path"]
                if rel_path == "repair_demo/core.py":
                    continue
                path = repo / rel_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")
            loop = run_greenfield_verification_loop(repo, project["verification_commands"], project, max_cycles=2)
            self.assertEqual(loop["status"], "passed", loop)
            self.assertEqual(loop["stop_condition_evidence"]["reason"], "verification passed")
            self.assertEqual(loop["stop_condition_evidence"]["attempt_count"], 2)
            self.assertFalse(loop["stop_condition_evidence"]["repeated_failure_signature"])
            self.assertEqual(len(loop["attempts"]), 2)
            repair = loop["attempts"][0]["repair_execution"]
            self.assertEqual(repair["status"], "applied")
            self.assertTrue(any(row["path"] == "repair_demo/core.py" for row in repair["repaired_files"]))
            self.assertTrue((repo / "repair_demo/core.py").exists())

    def test_greenfield_verification_loop_repairs_failed_module_with_synthesis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief(
                "Создай новый CLI проект `synthesis-repair-demo`.",
                {
                    "files": [
                        {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                        {"path": "app.py", "content": "def main():\n    return 'broken'\n"},
                        {
                            "path": "test_app.py",
                            "content": "import unittest\nimport app\n\nclass AppTests(unittest.TestCase):\n    def test_main(self):\n        self.assertEqual(app.main(), 'ready')\n",
                        },
                    ],
                    "verification_commands": ["python -m unittest test_app.py"],
                    "module_contracts": [{"module": "app", "path": "app.py", "responsibility": "return ready", "requirements": ["return ready"]}],
                },
            )
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                contract = payload.get("module_synthesis_contract", {})
                if not contract:
                    return {"ok": True, "status": "answered", "content": "{\"hypothesis\":\"repair app.py from failing assertion\"}"}
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "path": contract["path"],
                            "content": "def main():\n    return 'ready'\n",
                            "requirements_satisfied": contract["requirements"],
                            "tests_to_update": contract["paired_tests"],
                            "notes": "fixed from verification context",
                        }
                    ),
                }

            loop = run_greenfield_verification_loop(repo, project["verification_commands"], project, max_cycles=2, request_guidance=guidance)
            self.assertEqual(loop["status"], "passed", loop)
            self.assertEqual(loop["stop_condition_evidence"]["reason"], "verification passed")
            repair = loop["attempts"][0]["repair_execution"]
            self.assertEqual(repair["repair_strategy"], "module_synthesis_repair")
            self.assertEqual(repair["synthesis_repair_report"]["synthesis_stage"], "verification_repair")
            self.assertEqual((repo / "app.py").read_text(encoding="utf-8"), "def main():\n    return 'ready'\n")

    def test_greenfield_verification_repair_synthesis_preserves_test_oracle_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief(
                "Создай новый CLI проект `oracle-preserve-demo`.",
                {
                    "files": [
                        {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                        {"path": "app.py", "content": "def main():\n    return 'broken'\n"},
                        {
                            "path": "tests/test_app.py",
                            "content": "import unittest\nimport app\n\nclass AppTests(unittest.TestCase):\n    def test_main(self):\n        self.assertEqual(app.main(), 'ready')\n",
                        },
                    ],
                    "verification_commands": ["python -m unittest discover tests"],
                    "module_contracts": [
                        {"module": "app", "path": "app.py", "responsibility": "return ready", "requirements": ["return ready"]},
                        {"module": "tests.test_app", "path": "tests/test_app.py", "responsibility": "verify app behavior", "requirements": ["prove return ready"]},
                    ],
                },
            )
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")
            original_test = (repo / "tests/test_app.py").read_text(encoding="utf-8")
            requested_paths: list[str] = []

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                contract = payload["module_synthesis_contract"]
                requested_paths.append(contract["path"])
                self.assertNotIn("/tests/", f"/{contract['path']}")
                snapshots = payload.get("test_oracle_snapshots", [])
                self.assertEqual(len(snapshots), 1)
                self.assertEqual(snapshots[0]["path"], "tests/test_app.py")
                self.assertIn("self.assertEqual(app.main(), 'ready')", snapshots[0]["content"])
                self.assertIn("test_oracle_snapshots are read-only acceptance evidence", payload.get("repair_invariants", []))
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "path": contract["path"],
                            "content": "def main():\n    return 'ready'\n",
                            "requirements_satisfied": contract["requirements"],
                            "tests_to_update": contract["paired_tests"],
                            "notes": "fixed source while preserving test oracle",
                        }
                    ),
                }

            report = execute_module_synthesis_contracts(
                repo,
                project,
                guidance,
                synthesis_stage="verification_repair",
                verification_context={"status": "failed", "failure_signature": "ready assertion failed"},
            )
            self.assertEqual(report["status"], "applied", report)
            self.assertEqual(report["changed_files"], ["app.py"])
            self.assertEqual(requested_paths, ["app.py"])
            rows = {row["path"]: row for row in report["rows"]}
            self.assertEqual(rows["tests/test_app.py"]["status"], "skipped_test_oracle")
            self.assertEqual((repo / "tests/test_app.py").read_text(encoding="utf-8"), original_test)

    def test_greenfield_verification_loop_applies_model_guided_name_error_line_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief(
                "Создай local agent tool router `guided-repair-demo`.",
                {
                    "files": [
                        {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                        {"path": "demo.py", "content": "READY = 'ready'\nstray_symbol\n\ndef value():\n    return READY\n"},
                        {
                            "path": "test_demo.py",
                            "content": "import unittest\nimport demo\n\nclass DemoTests(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(demo.value(), 'ready')\n",
                        },
                    ],
                    "verification_commands": ["python -m unittest test_demo.py"],
                    "module_contracts": [{"module": "demo", "path": "demo.py", "responsibility": "return ready", "requirements": ["return ready"]}],
                },
            )
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                self.assertEqual(role, "GreenfieldRepairWorker")
                return {
                    "ok": True,
                    "status": "answered",
                    "content": "```json\n{\"repair_hypothesis\":{\"target_file\":\"demo.py\",\"target_line\":2,\"action\":\"Remove the stray undefined name line.\"}}\n```",
                }

            loop = run_greenfield_verification_loop(repo, project["verification_commands"], project, max_cycles=2, request_guidance=guidance)
            self.assertEqual(loop["status"], "passed", loop)
            repair = loop["attempts"][0]["repair_execution"]
            self.assertEqual(repair["status"], "applied")
            self.assertIn(
                {"path": "demo.py", "repair": "guided_remove_undefined_name_line", "status": "applied", "target_line": 2, "undefined_name": "stray_symbol"},
                repair["repaired_files"],
            )
            self.assertNotIn("stray_symbol", (repo / "demo.py").read_text(encoding="utf-8"))

    def test_greenfield_verification_loop_applies_model_guided_evidence_shape_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief(
                "Создай CLI проект `evidence-repair-demo`.",
                {
                    "files": [
                        {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                        {"path": "demo.py", "content": "READY = 'ready'\nn\n\ndef value():\n    return READY\n"},
                        {
                            "path": "test_demo.py",
                            "content": "import unittest\nimport demo\n\nclass DemoTests(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(demo.value(), 'ready')\n",
                        },
                    ],
                    "verification_commands": ["python -m unittest test_demo.py"],
                    "module_contracts": [{"module": "demo", "path": "demo.py", "responsibility": "return ready", "requirements": ["return ready"]}],
                },
            )
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "action": "propose_repair",
                            "hypothesis": "Remove the stray n character on line 2 of demo.py.",
                            "evidence": {"traceback_source": "demo.py", "line_number": 2, "error_type": "NameError"},
                            "scope_boundary": "demo.py",
                        }
                    ),
                }

            loop = run_greenfield_verification_loop(repo, project["verification_commands"], project, max_cycles=2, request_guidance=guidance)
            self.assertEqual(loop["status"], "passed", loop)
            repair = loop["attempts"][0]["repair_execution"]
            self.assertIn(
                {"path": "demo.py", "repair": "guided_remove_undefined_name_line", "status": "applied", "target_line": 2, "undefined_name": "n"},
                repair["repaired_files"],
            )

    def test_greenfield_verification_loop_applies_guided_semantic_exact_replace_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief(
                "Создай CLI проект `semantic-repair-demo`.",
                {
                    "files": [
                        {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                        {"path": "demo.py", "content": "def value():\n    return 'broken'\n"},
                        {
                            "path": "test_demo.py",
                            "content": "import unittest\nimport demo\n\nclass DemoTests(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(demo.value(), 'ready')\n",
                        },
                    ],
                    "verification_commands": ["python -m unittest test_demo.py"],
                    "module_contracts": [{"module": "demo", "path": "demo.py", "responsibility": "return ready", "requirements": ["return ready"]}],
                },
            )
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                self.assertEqual(role, "GreenfieldRepairWorker")
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "repair_hypothesis": "test assertion expects ready while demo.value returns broken",
                            "operations": [
                                {
                                    "type": "replace_exact",
                                    "path": "demo.py",
                                    "old_text": "return 'broken'",
                                    "new_text": "return 'ready'",
                                    "reason": "align implementation with failing assertion and module contract",
                                }
                            ],
                        }
                    ),
                }

            loop = run_greenfield_verification_loop(repo, project["verification_commands"], project, max_cycles=2, request_guidance=guidance)
            self.assertEqual(loop["status"], "passed", loop)
            repair = loop["attempts"][0]["repair_execution"]
            self.assertEqual(repair["status"], "applied", repair)
            self.assertIn(
                {"path": "demo.py", "repair": "guided_exact_replace", "status": "applied", "operation_index": 1},
                repair["repaired_files"],
            )
            self.assertEqual((repo / "demo.py").read_text(encoding="utf-8"), "def value():\n    return 'ready'\n")
            self.assertNotEqual(repair.get("repair_strategy"), "module_synthesis_repair")

    def test_greenfield_guided_exact_replace_blocks_ambiguous_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief(
                "Создай CLI проект `ambiguous-repair-demo`.",
                {
                    "files": [
                        {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                        {"path": "demo.py", "content": "def first():\n    return 'broken'\n\ndef second():\n    return 'broken'\n"},
                        {
                            "path": "test_demo.py",
                            "content": "import unittest\nimport demo\n\nclass DemoTests(unittest.TestCase):\n    def test_first(self):\n        self.assertEqual(demo.first(), 'ready')\n",
                        },
                    ],
                    "verification_commands": ["python -m unittest test_demo.py"],
                    "module_contracts": [{"module": "demo", "path": "demo.py", "responsibility": "return ready", "requirements": ["return ready"]}],
                },
            )
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "operations": [
                                {
                                    "type": "replace_exact",
                                    "path": "demo.py",
                                    "old_text": "return 'broken'",
                                    "new_text": "return 'ready'",
                                }
                            ]
                        }
                    ),
                }

            loop = run_greenfield_verification_loop(repo, project["verification_commands"], project, max_cycles=1, request_guidance=guidance)
            self.assertEqual(loop["status"], "blocked", loop)
            repair = loop["attempts"][0]["repair_execution"]
            self.assertIn("guided replace requires exactly one match", "; ".join(repair["blockers"]))
            self.assertIn("return 'broken'", (repo / "demo.py").read_text(encoding="utf-8"))

    def test_greenfield_verification_loop_applies_guided_ast_return_expression_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief(
                "Создай CLI проект `ast-repair-demo`.",
                {
                    "files": [
                        {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                        {"path": "calc.py", "content": "def add(left, right):\n    return left - right\n"},
                        {
                            "path": "test_calc.py",
                            "content": "import unittest\nimport calc\n\nclass CalcTests(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(calc.add(2, 3), 5)\n",
                        },
                    ],
                    "verification_commands": ["python -m unittest test_calc.py"],
                    "module_contracts": [{"module": "calc", "path": "calc.py", "responsibility": "add values", "requirements": ["add values"]}],
                },
            )
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "operations": [
                                {
                                    "type": "replace_return_expression",
                                    "path": "calc.py",
                                    "function_name": "add",
                                    "old_expression": "left - right",
                                    "new_expression": "left + right",
                                }
                            ]
                        }
                    ),
                }

            loop = run_greenfield_verification_loop(repo, project["verification_commands"], project, max_cycles=2, request_guidance=guidance)
            self.assertEqual(loop["status"], "passed", loop)
            repair = loop["attempts"][0]["repair_execution"]
            self.assertIn(
                {"path": "calc.py", "repair": "guided_replace_return_expression", "status": "applied", "operation_index": 1, "function_name": "add"},
                repair["repaired_files"],
            )
            self.assertEqual((repo / "calc.py").read_text(encoding="utf-8"), "def add(left, right):\n    return left + right\n")

    def test_greenfield_verification_loop_accepts_nested_repair_operation_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief(
                "Создай CLI проект `nested-repair-demo`.",
                {
                    "files": [
                        {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                        {"path": "calc.py", "content": "def add(left, right):\n    return left - right\n"},
                        {
                            "path": "test_calc.py",
                            "content": "import unittest\nimport calc\n\nclass CalcTests(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(calc.add(2, 3), 5)\n",
                        },
                    ],
                    "verification_commands": ["python -m unittest test_calc.py"],
                    "module_contracts": [{"module": "calc", "path": "calc.py", "responsibility": "add values", "requirements": ["add values"]}],
                },
            )
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "status": "success",
                            "repair_operation": {
                                "type": "replace_return_expression",
                                "operations": [
                                    {
                                        "type": "replace_return_expression",
                                        "path": "calc.py",
                                        "function_name": "add",
                                        "old_expression": "left - right",
                                        "new_expression": "left + right",
                                    }
                                ],
                            },
                        }
                    ),
                }

            loop = run_greenfield_verification_loop(repo, project["verification_commands"], project, max_cycles=2, request_guidance=guidance)
            self.assertEqual(loop["status"], "passed", loop)
            self.assertEqual((repo / "calc.py").read_text(encoding="utf-8"), "def add(left, right):\n    return left + right\n")

    def test_greenfield_verification_loop_inherits_nested_repair_operation_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief(
                "Repair generated settings module.",
                {
                    "files": [
                        {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                        {"path": "settings.py", "content": "FEATURE_ENABLED = False\n\n\ndef enabled():\n    return FEATURE_ENABLED\n"},
                        {"path": "test_settings.py", "content": "import unittest\nimport settings\n\nclass SettingsTests(unittest.TestCase):\n    def test_enabled(self):\n        self.assertTrue(settings.enabled())\n"},
                    ],
                    "verification_commands": ["python -m unittest test_settings.py"],
                    "module_contracts": [{"module": "settings", "path": "settings.py", "responsibility": "return enabled feature flag", "requirements": ["return enabled feature flag"]}],
                },
            )
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "status": "success",
                            "repair_operation": {
                                "type": "replace_python_constant",
                                "operations": [
                                    {
                                        "path": "settings.py",
                                        "symbol_name": "FEATURE_ENABLED",
                                        "old_literal": "False",
                                        "new_literal": "True",
                                    }
                                ],
                            },
                        }
                    ),
                }

            loop = run_greenfield_verification_loop(repo, project["verification_commands"], project, max_cycles=2, request_guidance=guidance)
            self.assertEqual(loop["status"], "passed", loop)
            self.assertIn("FEATURE_ENABLED = True", (repo / "settings.py").read_text(encoding="utf-8"))
            repair = loop["attempts"][0]["repair_execution"]["repaired_files"][0]
            self.assertEqual(repair["repair"], "guided_replace_python_constant")

    def test_greenfield_verification_loop_accepts_nested_line_repair_hypothesis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief(
                "Repair generated demo module.",
                {
                    "files": [
                        {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                        {"path": "demo.py", "content": "READY = 'ready'\nstray_symbol\n\ndef value():\n    return READY\n"},
                        {"path": "test_demo.py", "content": "import unittest\nimport demo\n\nclass DemoTests(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(demo.value(), 'ready')\n"},
                    ],
                    "verification_commands": ["python -m unittest test_demo.py"],
                    "module_contracts": [{"module": "demo", "path": "demo.py", "responsibility": "return ready", "requirements": ["return ready"]}],
                },
            )
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "status": "success",
                            "repair_operation": {
                                "type": "remove_undefined_name_line",
                                "repair_hypothesis": {
                                    "target_file": "demo.py",
                                    "target_line": 2,
                                    "action": "Remove the stray undefined name line.",
                                },
                            },
                        }
                    ),
                }

            loop = run_greenfield_verification_loop(repo, project["verification_commands"], project, max_cycles=2, request_guidance=guidance)
            self.assertEqual(loop["status"], "passed", loop)
            self.assertNotIn("stray_symbol", (repo / "demo.py").read_text(encoding="utf-8"))
            repair = loop["attempts"][0]["repair_execution"]["repaired_files"][0]
            self.assertEqual(repair["repair"], "guided_remove_undefined_name_line")

    def test_greenfield_guided_ast_return_expression_blocks_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief(
                "Создай CLI проект `ast-mismatch-demo`.",
                {
                    "files": [
                        {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                        {"path": "calc.py", "content": "def add(left, right):\n    return left - right\n"},
                        {
                            "path": "test_calc.py",
                            "content": "import unittest\nimport calc\n\nclass CalcTests(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(calc.add(2, 3), 5)\n",
                        },
                    ],
                    "verification_commands": ["python -m unittest test_calc.py"],
                    "module_contracts": [{"module": "calc", "path": "calc.py", "responsibility": "add values", "requirements": ["add values"]}],
                },
            )
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "operations": [
                                {
                                    "type": "replace_return_expression",
                                    "path": "calc.py",
                                    "function_name": "add",
                                    "old_expression": "left * right",
                                    "new_expression": "left + right",
                                }
                            ]
                        }
                    ),
                }

            loop = run_greenfield_verification_loop(repo, project["verification_commands"], project, max_cycles=1, request_guidance=guidance)
            self.assertEqual(loop["status"], "blocked", loop)
            repair = loop["attempts"][0]["repair_execution"]
            self.assertIn("replace_return_expression failed", "; ".join(repair["blockers"]))
            self.assertEqual((repo / "calc.py").read_text(encoding="utf-8"), "def add(left, right):\n    return left - right\n")

    def test_greenfield_verification_loop_applies_guided_ast_constant_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief(
                "Создай CLI проект `constant-repair-demo`.",
                {
                    "files": [
                        {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                        {"path": "settings.py", "content": "FEATURE_ENABLED = False\n\n\ndef enabled():\n    return FEATURE_ENABLED\n"},
                        {
                            "path": "test_settings.py",
                            "content": "import unittest\nimport settings\n\nclass SettingsTests(unittest.TestCase):\n    def test_enabled(self):\n        self.assertTrue(settings.enabled())\n",
                        },
                    ],
                    "verification_commands": ["python -m unittest test_settings.py"],
                    "module_contracts": [{"module": "settings", "path": "settings.py", "responsibility": "return enabled feature flag", "requirements": ["return enabled feature flag"]}],
                },
            )
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "operations": [
                                {
                                    "type": "replace_python_constant",
                                    "path": "settings.py",
                                    "symbol_name": "FEATURE_ENABLED",
                                    "old_literal": "False",
                                    "new_literal": "True",
                                }
                            ]
                        }
                    ),
                }

            loop = run_greenfield_verification_loop(repo, project["verification_commands"], project, max_cycles=2, request_guidance=guidance)
            self.assertEqual(loop["status"], "passed", loop)
            repair = loop["attempts"][0]["repair_execution"]
            self.assertIn(
                {"path": "settings.py", "repair": "guided_replace_python_constant", "status": "applied", "operation_index": 1, "symbol_name": "FEATURE_ENABLED"},
                repair["repaired_files"],
            )
            self.assertEqual((repo / "settings.py").read_text(encoding="utf-8"), "FEATURE_ENABLED = True\n\n\ndef enabled():\n    return FEATURE_ENABLED\n")

    def test_greenfield_guided_ast_constant_repair_blocks_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief(
                "Создай CLI проект `constant-mismatch-demo`.",
                {
                    "files": [
                        {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                        {"path": "settings.py", "content": "FEATURE_ENABLED = False\n\n\ndef enabled():\n    return FEATURE_ENABLED\n"},
                        {
                            "path": "test_settings.py",
                            "content": "import unittest\nimport settings\n\nclass SettingsTests(unittest.TestCase):\n    def test_enabled(self):\n        self.assertTrue(settings.enabled())\n",
                        },
                    ],
                    "verification_commands": ["python -m unittest test_settings.py"],
                    "module_contracts": [{"module": "settings", "path": "settings.py", "responsibility": "return enabled feature flag", "requirements": ["return enabled feature flag"]}],
                },
            )
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "operations": [
                                {
                                    "type": "replace_python_constant",
                                    "path": "settings.py",
                                    "symbol_name": "FEATURE_ENABLED",
                                    "old_literal": "True",
                                    "new_literal": "False",
                                }
                            ]
                        }
                    ),
                }

            loop = run_greenfield_verification_loop(repo, project["verification_commands"], project, max_cycles=1, request_guidance=guidance)
            self.assertEqual(loop["status"], "blocked", loop)
            repair = loop["attempts"][0]["repair_execution"]
            self.assertIn("replace_python_constant failed", "; ".join(repair["blockers"]))
            self.assertEqual((repo / "settings.py").read_text(encoding="utf-8"), "FEATURE_ENABLED = False\n\n\ndef enabled():\n    return FEATURE_ENABLED\n")

    def test_greenfield_verification_loop_applies_guided_ast_function_body_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief(
                "Создай CLI проект `body-repair-demo`.",
                {
                    "files": [
                        {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                        {"path": "grades.py", "content": "def grade(score):\n    if score >= 90:\n        return 'B'\n    return 'C'\n"},
                        {
                            "path": "test_grades.py",
                            "content": "import unittest\nimport grades\n\nclass GradeTests(unittest.TestCase):\n    def test_grade(self):\n        self.assertEqual(grades.grade(95), 'A')\n        self.assertEqual(grades.grade(70), 'C')\n",
                        },
                    ],
                    "verification_commands": ["python -m unittest test_grades.py"],
                    "module_contracts": [{"module": "grades", "path": "grades.py", "responsibility": "grade scores", "requirements": ["grade scores"]}],
                },
            )
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "operations": [
                                {
                                    "type": "replace_function_body",
                                    "path": "grades.py",
                                    "function_name": "grade",
                                    "old_body": "if score >= 90:\n    return 'B'\nreturn 'C'",
                                    "new_body": "if score >= 90:\n    return 'A'\nreturn 'C'",
                                }
                            ]
                        }
                    ),
                }

            loop = run_greenfield_verification_loop(repo, project["verification_commands"], project, max_cycles=2, request_guidance=guidance)
            self.assertEqual(loop["status"], "passed", loop)
            repair = loop["attempts"][0]["repair_execution"]
            self.assertIn(
                {"path": "grades.py", "repair": "guided_replace_function_body", "status": "applied", "operation_index": 1, "function_name": "grade"},
                repair["repaired_files"],
            )
            self.assertEqual((repo / "grades.py").read_text(encoding="utf-8"), "def grade(score):\n    if score >= 90:\n        return 'A'\n    return 'C'\n")

    def test_greenfield_verification_loop_retries_function_body_with_old_body_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief(
                "Создай CLI проект `body-retry-demo`.",
                {
                    "files": [
                        {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                        {"path": "grades.py", "content": "def grade(score):\n    return 'C'\n"},
                        {
                            "path": "test_grades.py",
                            "content": "import unittest\nimport grades\n\nclass GradeTests(unittest.TestCase):\n    def test_grade_bands(self):\n        self.assertEqual(grades.grade(95), 'A')\n        self.assertEqual(grades.grade(85), 'B')\n        self.assertEqual(grades.grade(70), 'C')\n\n    def test_negative_score_is_rejected(self):\n        with self.assertRaises(ValueError):\n            grades.grade(-1)\n",
                        },
                    ],
                    "verification_commands": ["python -m unittest test_grades.py"],
                    "module_contracts": [{"module": "grades", "path": "grades.py", "responsibility": "grade bands", "requirements": ["grade bands", "reject negative scores"]}],
                },
            )
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")
            calls: list[dict] = []

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                calls.append(payload)
                if "function_body_candidates" not in payload:
                    return {"ok": True, "status": "answered", "content": json.dumps({"status": "blocked", "blockers": ["replace_function_body needs old_body"]})}
                candidate = payload["function_body_candidates"][0]
                self.assertEqual(candidate["old_body"], "return 'C'")
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "operations": [
                                {
                                    "type": "replace_function_body",
                                    "path": candidate["path"],
                                    "function_name": candidate["function_name"],
                                    "old_body": candidate["old_body"],
                                    "new_body": "if score < 0:\n    raise ValueError('score must be non-negative')\nif score >= 90:\n    return 'A'\nif score >= 80:\n    return 'B'\nreturn 'C'",
                                }
                            ]
                        }
                    ),
                }

            loop = run_greenfield_verification_loop(repo, project["verification_commands"], project, max_cycles=2, request_guidance=guidance)
            self.assertEqual(loop["status"], "passed", loop)
            self.assertTrue(loop["attempts"][0]["repair_guidance_retry"])
            self.assertTrue(any("function_body_candidates" in payload for payload in calls))
            self.assertIn(
                {"path": "grades.py", "repair": "guided_replace_function_body", "status": "applied", "operation_index": 1, "function_name": "grade"},
                loop["attempts"][0]["repair_execution"]["repaired_files"],
            )

    def test_greenfield_verification_loop_accepts_singular_operation_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief(
                "Создай CLI проект `single-operation-demo`.",
                {
                    "files": [
                        {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                        {"path": "grades.py", "content": "def grade(score):\n    return 'C'\n"},
                        {"path": "test_grades.py", "content": "import unittest\nimport grades\n\nclass GradeTests(unittest.TestCase):\n    def test_grade(self):\n        self.assertEqual(grades.grade(95), 'A')\n"},
                    ],
                    "verification_commands": ["python -m unittest test_grades.py"],
                    "module_contracts": [{"module": "grades", "path": "grades.py", "responsibility": "grade score", "requirements": ["grade score"]}],
                },
            )
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "status": "replace_function_body",
                            "operation": {
                                "type": "replace_function_body",
                                "path": "grades.py",
                                "function_name": "grade",
                                "old_body": "return 'C'",
                                "new_body": "return 'A'",
                            },
                        }
                    ),
                }

            loop = run_greenfield_verification_loop(repo, project["verification_commands"], project, max_cycles=2, request_guidance=guidance)
            self.assertEqual(loop["status"], "passed", loop)
            self.assertIn(
                {"path": "grades.py", "repair": "guided_replace_function_body", "status": "applied", "operation_index": 1, "function_name": "grade"},
                loop["attempts"][0]["repair_execution"]["repaired_files"],
            )

    def test_greenfield_guided_ast_function_body_repair_blocks_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief(
                "Создай CLI проект `body-mismatch-demo`.",
                {
                    "files": [
                        {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                        {"path": "grades.py", "content": "def grade(score):\n    if score >= 90:\n        return 'B'\n    return 'C'\n"},
                        {
                            "path": "test_grades.py",
                            "content": "import unittest\nimport grades\n\nclass GradeTests(unittest.TestCase):\n    def test_grade(self):\n        self.assertEqual(grades.grade(95), 'A')\n",
                        },
                    ],
                    "verification_commands": ["python -m unittest test_grades.py"],
                    "module_contracts": [{"module": "grades", "path": "grades.py", "responsibility": "grade scores", "requirements": ["grade scores"]}],
                },
            )
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "operations": [
                                {
                                    "type": "replace_function_body",
                                    "path": "grades.py",
                                    "function_name": "grade",
                                    "old_body": "return 'B'",
                                    "new_body": "return 'A'",
                                }
                            ]
                        }
                    ),
                }

            loop = run_greenfield_verification_loop(repo, project["verification_commands"], project, max_cycles=1, request_guidance=guidance)
            self.assertEqual(loop["status"], "blocked", loop)
            repair = loop["attempts"][0]["repair_execution"]
            self.assertIn("replace_function_body failed", "; ".join(repair["blockers"]))
            self.assertEqual((repo / "grades.py").read_text(encoding="utf-8"), "def grade(score):\n    if score >= 90:\n        return 'B'\n    return 'C'\n")

    def test_greenfield_verification_loop_reruns_after_final_allowed_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief(
                "Создай новый CLI проект `final-repair-demo`.",
                {
                    "files": [
                        {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                        {"path": "app.py", "content": "def main():\n    return 'broken'\n"},
                        {
                            "path": "test_app.py",
                            "content": "import unittest\nimport app\n\nclass AppTests(unittest.TestCase):\n    def test_main(self):\n        self.assertEqual(app.main(), 'ready')\n",
                        },
                    ],
                    "verification_commands": ["python -m unittest test_app.py"],
                    "module_contracts": [{"module": "app", "path": "app.py", "responsibility": "return ready", "requirements": ["return ready"]}],
                },
            )
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")

            def guidance(role: str, payload: dict, instructions: str) -> dict:
                contract = payload.get("module_synthesis_contract", {})
                if not contract:
                    return {"ok": True, "status": "answered", "content": "{\"hypothesis\":\"repair final failed assertion\"}"}
                return {
                    "ok": True,
                    "status": "answered",
                    "content": json.dumps(
                        {
                            "path": contract["path"],
                            "content": "def main():\n    return 'ready'\n",
                            "requirements_satisfied": contract["requirements"],
                            "tests_to_update": contract["paired_tests"],
                            "notes": "fixed on final allowed repair",
                        }
                    ),
                }

            loop = run_greenfield_verification_loop(repo, project["verification_commands"], project, max_cycles=1, request_guidance=guidance)
            self.assertEqual(loop["status"], "passed", loop)
            self.assertEqual(loop["stop_reason"], "verification passed after final repair")
            self.assertTrue(loop["attempts"][-1]["post_repair_verification"])

    def test_greenfield_verification_loop_records_repeated_failure_stop_condition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "broken.py").write_text("def value():\n    return 1\n", encoding="utf-8")
            project = build_greenfield_project_brief(
                "Создай новый CLI проект `repeat-demo`.",
                {
                    "files": [
                        {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                        {"path": "broken.py", "content": "def value():\n    return 1\n"},
                    ],
                    "verification_commands": ["python missing_test.py"],
                    "module_contracts": [{"module": "broken", "path": "broken.py", "responsibility": "broken repeat demo", "requirements": ["run missing test"]}],
                },
            )
            loop = run_greenfield_verification_loop(repo, project["verification_commands"], project, max_cycles=2)
            self.assertEqual(loop["status"], "blocked", loop)
            self.assertEqual(loop["stop_reason"], "same verification failure repeats")
            self.assertTrue(loop["stop_condition_evidence"]["repeated_failure_signature"])
            self.assertEqual(loop["stop_condition_evidence"]["reason"], "same verification failure repeats")

    def test_greenfield_dependency_worker_records_node_strategy_without_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief("Создай Vite frontend web app `vite-demo`.")
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")
            report = run_dependency_worker(repo, project)
            self.assertEqual(report["status"], "manifest_recorded", report)
            self.assertEqual(report["package_manager"], "npm")
            self.assertEqual(report["manager_status"]["binary"], "npm")
            self.assertIn("package.json", [row["path"] for row in report["manifest_files"]])
            package_manifest = next(row for row in report["manifest_files"] if row["path"] == "package.json")
            self.assertEqual(package_manifest["status"], "present")
            self.assertEqual(package_manifest["ecosystem"], "node")
            self.assertTrue(package_manifest["sha256"])
            self.assertEqual(report["manifest_status"], "complete")
            self.assertEqual(report["manifest_count"], 1)
            self.assertEqual(report["dependency_strategy"]["install_default"], "record_manifest_only")
            self.assertFalse(report["install_policy_evidence"]["explicit_install_requested"])
            self.assertEqual(report["new_lockfiles"], [])
            self.assertEqual(report["lockfile_status"], "unchanged")

    def test_greenfield_dependency_worker_recognizes_python3_pip_stack_without_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief("Создай FastAPI service `api-demo`.")
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")
            report = run_dependency_worker(repo, project)
            self.assertEqual(report["status"], "manifest_recorded", report)
            self.assertEqual(report["package_manager"], "pip")
            self.assertTrue(report["manager_status"]["available"], report)
            self.assertNotIn("package manager is unavailable until install/run is requested: pip", report["warnings"])
            self.assertEqual(report["manifest_status"], "complete")

    def test_greenfield_dependency_worker_blocks_workspace_escape_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            project = build_greenfield_project_brief(
                "Создай новый CLI проект `unsafe-demo`.",
                {"install_commands": ["python -m pip install -r ../requirements.txt"]},
            )
            for item in project["files"]:
                path = repo / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")
            report = run_dependency_worker(repo, project)
            self.assertEqual(report["status"], "blocked", report)
            self.assertTrue(any("outside workspace" in item for item in report["blockers"]))
            self.assertEqual(report["install_policy_evidence"]["blocked_command_count"], 1)
            self.assertEqual(report["manifest_status"], "complete")

    def test_project_creation_mode_blocks_nonempty_unowned_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "important.py").write_text("VALUE = 1\n", encoding="utf-8")
            brief = project_creation_brief(repo, "Создай новый минимальный python проект.")
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["status"], "blocked", report)
            self.assertIn("empty directory", " ".join(report["execution_result"]["blockers"]))
            self.assertFalse((repo / "app.py").exists())

    def test_project_creation_inferred_cli_uses_real_project_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(repo, "Создай новый CLI проект `forge-tool`.")
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["status"], "implemented", report)
            changed = set(report["changed_files"])
            self.assertIn("forge_tool/core.py", changed)
            self.assertIn("forge_tool/cli.py", changed)
            self.assertIn("tests/test_core.py", changed)
            self.assertIn("greenfield_project_brief.json", changed)
            project = report["execution_result"]["greenfield_project"]["greenfield_project_brief"]
            self.assertEqual(project["project_type"], "cli_tool")
            self.assertEqual(project["template_id"], "python_cli_basic")
            self.assertGreaterEqual(len(project["module_contracts"]), 2)
            self.assertEqual(report["execution_result"]["greenfield_project"]["verification"]["status"], "passed")

    def test_project_creation_calculator_cli_implements_task_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(repo, "Создай CLI калькулятор `calc-tool` со сложением вычитанием умножением и делением.")
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["status"], "implemented", report)
            project = report["execution_result"]["greenfield_project"]["greenfield_project_brief"]
            self.assertEqual(project["template_id"], "python_cli_basic")
            self.assertTrue(any(feature["id"] == "calculator_operations" for feature in project["acceptance_features"]))
            self.assertIn("calculator_operations", project["implementation_feature_report"]["recognized_feature_ids"])
            self.assertIn("model_guidance", project["implementation_feature_report"])
            self.assertIn("reject division by zero", json.dumps(project["module_contracts"], ensure_ascii=False))
            self.assertIn("calculate", (repo / "calc_tool/core.py").read_text(encoding="utf-8"))
            self.assertIn("test_division_by_zero_is_rejected", (repo / "tests/test_core.py").read_text(encoding="utf-8"))
            verification = report["execution_result"]["greenfield_project"]["verification"]
            self.assertEqual(verification["status"], "passed", verification)
            self.assertEqual(report["execution_result"]["greenfield_project"]["greenfield_review"]["semantic_review"]["status"], "passed")

    def test_project_creation_static_todo_site_implements_task_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(repo, "Создай static frontend website todo list `todo-demo` со списком задач.")
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["status"], "implemented", report)
            project = report["execution_result"]["greenfield_project"]["greenfield_project_brief"]
            self.assertEqual(project["template_id"], "static_site")
            self.assertTrue(any(feature["id"] == "todo_list" for feature in project["acceptance_features"]))
            self.assertIn("todo_list", project["implementation_feature_report"]["recognized_feature_ids"])
            self.assertIn("persist tasks in localStorage", json.dumps(project["module_contracts"], ensure_ascii=False))
            self.assertIn("todo-input", (repo / "index.html").read_text(encoding="utf-8"))
            self.assertIn("function addTodo", (repo / "app.js").read_text(encoding="utf-8"))
            self.assertIn("function renderTodos", (repo / "app.js").read_text(encoding="utf-8"))
            self.assertIn("test_script_implements_todo_behaviors", (repo / "tests/test_static_site.py").read_text(encoding="utf-8"))
            verification = report["execution_result"]["greenfield_project"]["verification"]
            self.assertEqual(verification["status"], "passed", verification)
            review = report["execution_result"]["greenfield_project"]["greenfield_review"]
            self.assertEqual(review["semantic_review"]["status"], "passed", review)
            self.assertEqual(review["status"], "passed", review)

    def test_project_creation_kanban_board_frontend_implements_multi_workflow_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(repo, "Создай static frontend website kanban board `kanban-demo` с карточками, колонками, фильтрами и метриками.")
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["status"], "implemented", report)
            project = report["execution_result"]["greenfield_project"]["greenfield_project_brief"]
            self.assertEqual(project["template_id"], "static_site")
            self.assertTrue(any(feature["id"] == "kanban_board_frontend" for feature in project["acceptance_features"]))
            self.assertIn("kanban_board_frontend", project["implementation_feature_report"]["recognized_feature_ids"])
            self.assertGreaterEqual(len(project["module_contracts"]), 5)
            self.assertGreaterEqual(project["scenario_plan"]["scenario_count"], 3)
            expected = {"index.html", "state.js", "board.js", "app.js", "tests/test_kanban_board.py"}
            self.assertTrue(expected.issubset(set(project["expected_files"])))
            self.assertIn("kanban-form", (repo / "index.html").read_text(encoding="utf-8"))
            self.assertIn("function createCard", (repo / "state.js").read_text(encoding="utf-8"))
            self.assertIn("function moveCard", (repo / "state.js").read_text(encoding="utf-8"))
            self.assertIn("function filterCards", (repo / "state.js").read_text(encoding="utf-8"))
            self.assertIn("function renderBoard", (repo / "board.js").read_text(encoding="utf-8"))
            self.assertIn("function renderMetrics", (repo / "board.js").read_text(encoding="utf-8"))
            self.assertIn("addEventListener", (repo / "app.js").read_text(encoding="utf-8"))
            tests = (repo / "tests/test_kanban_board.py").read_text(encoding="utf-8")
            self.assertIn("test_state_module_owns_board_workflows", tests)
            self.assertIn("test_render_and_app_modules_wire_interactions", tests)
            module_rows = {row["path"]: row for row in project["implementation_plan"]["module_sequence"]}
            for module_path in ("state.js", "board.js", "app.js"):
                self.assertEqual(module_rows[module_path]["paired_tests"], ["tests/test_kanban_board.py"])
            trace_rows = project["implementation_trace"]["rows"]
            self.assertTrue(all(row["verification_files"] == ["tests/test_kanban_board.py"] for row in trace_rows if row["file"] in {"state.js", "board.js", "app.js"}))
            verification = report["execution_result"]["greenfield_project"]["verification"]
            self.assertEqual(verification["status"], "passed", verification)
            review = report["execution_result"]["greenfield_project"]["greenfield_review"]
            self.assertEqual(review["semantic_review"]["status"], "passed", review)
            self.assertEqual(review["scenario_review"]["status"], "passed", review)
            self.assertEqual(review["status"], "passed", review)

    def test_project_creation_vite_kanban_board_implements_multi_workflow_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(
                repo,
                "Создай Vite frontend web app `project-board-demo`: kanban-доска с колонками todo/doing/done, добавлением карточек, переносом карточек между колонками, фильтром по тексту, счетчиками по колонкам, localStorage persistence и тестами контракта.",
            )
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["status"], "implemented", report)
            project = report["execution_result"]["greenfield_project"]["greenfield_project_brief"]
            self.assertEqual(project["template_id"], "node_vite_app")
            self.assertTrue(any(feature["id"] == "kanban_board_frontend" for feature in project["acceptance_features"]))
            self.assertIn("kanban_board_frontend", project["implementation_feature_report"]["recognized_feature_ids"])
            self.assertGreaterEqual(project["scenario_plan"]["scenario_count"], 3)
            self.assertIn("src/main.jsx", project["expected_files"])
            source = (repo / "src/main.jsx").read_text(encoding="utf-8")
            for marker in ("function createCard", "function moveCard", "function filterCards", "function boardMetrics", "function renderMetrics", "function renderBoard"):
                self.assertIn(marker, source)
            for marker in ("localStorage", "loadBoard", "saveBoard", "addEventListener", "backlog", "doing", "done"):
                self.assertIn(marker, source)
            self.assertIn("test_kanban_workflow_markers", (repo / "tests/test_vite_contract.py").read_text(encoding="utf-8"))
            verification = report["execution_result"]["greenfield_project"]["verification"]
            self.assertEqual(verification["status"], "passed", verification)
            review = report["execution_result"]["greenfield_project"]["greenfield_review"]
            self.assertEqual(review["semantic_review"]["status"], "passed", review)
            self.assertEqual(review["scenario_review"]["status"], "passed", review)
            self.assertEqual(review["status"], "passed", review)

    def test_project_creation_notes_api_implements_task_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(repo, "Создай FastAPI notes API `notes-demo` для заметок.")
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["status"], "implemented", report)
            project = report["execution_result"]["greenfield_project"]["greenfield_project_brief"]
            self.assertEqual(project["template_id"], "python_fastapi_service")
            self.assertTrue(any(feature["id"] == "notes_api" for feature in project["acceptance_features"]))
            self.assertIn("notes_api", project["implementation_feature_report"]["recognized_feature_ids"])
            self.assertIn("reject empty note titles", json.dumps(project["module_contracts"], ensure_ascii=False))
            source = (repo / "app/main.py").read_text(encoding="utf-8")
            self.assertIn("def create_note", source)
            self.assertIn("@app.post('/notes')", source)
            self.assertIn("@app.delete('/notes/{note_id}')", source)
            tests = (repo / "tests/test_health.py").read_text(encoding="utf-8")
            self.assertIn("test_create_list_and_get_note", tests)
            self.assertIn("test_delete_note", tests)
            verification = report["execution_result"]["greenfield_project"]["verification"]
            self.assertEqual(verification["status"], "passed", verification)
            review = report["execution_result"]["greenfield_project"]["greenfield_review"]
            self.assertEqual(review["semantic_review"]["status"], "passed", review)
            self.assertEqual(review["status"], "passed", review)

    def test_project_creation_issue_tracker_api_implements_multi_workflow_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(repo, "Создай FastAPI issue tracker API `issue-demo` с назначением, статусами и фильтрами.")
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["status"], "implemented", report)
            project = report["execution_result"]["greenfield_project"]["greenfield_project_brief"]
            self.assertEqual(project["template_id"], "python_fastapi_service")
            self.assertTrue(any(feature["id"] == "issue_tracker_api" for feature in project["acceptance_features"]))
            self.assertIn("issue_tracker_api", project["implementation_feature_report"]["recognized_feature_ids"])
            self.assertGreaterEqual(len(project["module_contracts"]), 5)
            self.assertGreaterEqual(project["scenario_plan"]["scenario_count"], 3)
            expected = {"app/domain.py", "app/store.py", "app/routes.py", "app/main.py", "tests/test_issue_tracker.py"}
            self.assertTrue(expected.issubset(set(project["expected_files"])))
            self.assertIn("def create_issue", (repo / "app/domain.py").read_text(encoding="utf-8"))
            self.assertIn("class IssueStore", (repo / "app/store.py").read_text(encoding="utf-8"))
            self.assertIn("def create_issue_response", (repo / "app/routes.py").read_text(encoding="utf-8"))
            self.assertIn("include_router", (repo / "app/main.py").read_text(encoding="utf-8"))
            tests = (repo / "tests/test_issue_tracker.py").read_text(encoding="utf-8")
            self.assertIn("test_domain_create_assign_transition_workflow", tests)
            self.assertIn("test_store_filtering_workflow", tests)
            self.assertIn("test_route_adapter_workflow", tests)
            module_rows = {row["path"]: row for row in project["implementation_plan"]["module_sequence"]}
            for module_path in ("app/domain.py", "app/store.py", "app/routes.py"):
                self.assertEqual(module_rows[module_path]["paired_tests"], ["tests/test_issue_tracker.py"])
            self.assertIn("tests/test_issue_tracker.py", module_rows["app/main.py"]["paired_tests"])
            verification = report["execution_result"]["greenfield_project"]["verification"]
            self.assertEqual(verification["status"], "passed", verification)
            review = report["execution_result"]["greenfield_project"]["greenfield_review"]
            self.assertEqual(review["semantic_review"]["status"], "passed", review)
            self.assertEqual(review["scenario_review"]["status"], "passed", review)
            self.assertEqual(review["status"], "passed", review)

    def test_project_creation_inventory_ops_api_implements_multi_workflow_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(
                repo,
                "Создай FastAPI backend service `inventory-ops-demo`: inventory items CRUD, stock adjustment ledger, low-stock report endpoint, search/filter by sku/category/status, JSON error responses, tests without network.",
            )
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["status"], "implemented", report)
            project = report["execution_result"]["greenfield_project"]["greenfield_project_brief"]
            self.assertEqual(project["template_id"], "python_fastapi_service")
            self.assertTrue(any(feature["id"] == "inventory_ops_api" for feature in project["acceptance_features"]))
            self.assertIn("inventory_ops_api", project["implementation_feature_report"]["recognized_feature_ids"])
            self.assertGreaterEqual(len(project["module_contracts"]), 6)
            self.assertGreaterEqual(project["scenario_plan"]["scenario_count"], 3)
            expected = {"app/domain.py", "app/store.py", "app/reports.py", "app/routes.py", "app/main.py", "tests/test_inventory_ops.py"}
            self.assertTrue(expected.issubset(set(project["expected_files"])))
            self.assertIn("def create_item", (repo / "app/domain.py").read_text(encoding="utf-8"))
            self.assertIn("def adjust_stock", (repo / "app/domain.py").read_text(encoding="utf-8"))
            self.assertIn("class InventoryStore", (repo / "app/store.py").read_text(encoding="utf-8"))
            self.assertIn("def low_stock_report", (repo / "app/reports.py").read_text(encoding="utf-8"))
            self.assertIn("def create_item_response", (repo / "app/routes.py").read_text(encoding="utf-8"))
            self.assertIn("include_router", (repo / "app/main.py").read_text(encoding="utf-8"))
            tests = (repo / "tests/test_inventory_ops.py").read_text(encoding="utf-8")
            self.assertIn("test_inventory_crud_and_stock_adjustment_ledger", tests)
            self.assertIn("test_low_stock_report_and_filters", tests)
            self.assertIn("test_domain_validation_and_json_errors", tests)
            verification = report["execution_result"]["greenfield_project"]["verification"]
            self.assertEqual(verification["status"], "passed", verification)
            review = report["execution_result"]["greenfield_project"]["greenfield_review"]
            self.assertEqual(review["semantic_review"]["status"], "passed", review)
            self.assertEqual(review["scenario_review"]["status"], "passed", review)
            self.assertEqual(review["status"], "passed", review)

    def test_project_creation_operations_dashboard_api_implements_long_form_multi_workflow_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(repo, "Создай FastAPI operations dashboard API `ops-demo` для сервисов, инцидентов, метрик и event timeline.")
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["status"], "implemented", report)
            project = report["execution_result"]["greenfield_project"]["greenfield_project_brief"]
            self.assertEqual(project["template_id"], "python_fastapi_service")
            self.assertTrue(any(feature["id"] == "operations_dashboard_api" for feature in project["acceptance_features"]))
            self.assertIn("operations_dashboard_api", project["implementation_feature_report"]["recognized_feature_ids"])
            self.assertGreaterEqual(len(project["module_contracts"]), 7)
            self.assertGreaterEqual(project["scenario_plan"]["scenario_count"], 4)
            expected = {
                "app/domain.py",
                "app/store.py",
                "app/metrics.py",
                "app/events.py",
                "app/routes.py",
                "app/main.py",
                "tests/test_operations_dashboard.py",
            }
            self.assertTrue(expected.issubset(set(project["expected_files"])))
            self.assertIn("def register_service", (repo / "app/domain.py").read_text(encoding="utf-8"))
            self.assertIn("class OperationsStore", (repo / "app/store.py").read_text(encoding="utf-8"))
            self.assertIn("def build_dashboard_metrics", (repo / "app/metrics.py").read_text(encoding="utf-8"))
            self.assertIn("def build_event_timeline", (repo / "app/events.py").read_text(encoding="utf-8"))
            self.assertIn("def dashboard_response", (repo / "app/routes.py").read_text(encoding="utf-8"))
            self.assertIn("include_router", (repo / "app/main.py").read_text(encoding="utf-8"))
            tests = (repo / "tests/test_operations_dashboard.py").read_text(encoding="utf-8")
            self.assertIn("test_domain_service_incident_lifecycle", tests)
            self.assertIn("test_store_metrics_and_filters_workflow", tests)
            self.assertIn("test_events_and_route_adapters_workflow", tests)
            module_rows = {row["path"]: row for row in project["implementation_plan"]["module_sequence"]}
            for module_path in ("app/domain.py", "app/store.py", "app/metrics.py", "app/events.py", "app/routes.py", "app/main.py"):
                self.assertEqual(module_rows[module_path]["paired_tests"], ["tests/test_operations_dashboard.py"])
            trace_rows = project["implementation_trace"]["rows"]
            self.assertTrue(all(row["verification_files"] == ["tests/test_operations_dashboard.py"] for row in trace_rows if row["file"].startswith("app/") and row["file"] != "app/main.py"))
            verification = report["execution_result"]["greenfield_project"]["verification"]
            self.assertEqual(verification["status"], "passed", verification)
            review = report["execution_result"]["greenfield_project"]["greenfield_review"]
            self.assertEqual(review["semantic_review"]["status"], "passed", review)
            self.assertEqual(review["scenario_review"]["status"], "passed", review)
            self.assertEqual(review["artifact_review"]["status"], "passed", review)
            self.assertEqual(review["status"], "passed", review)

    def test_project_creation_csv_summary_tool_implements_task_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(repo, "Создай data csv summary tool `csv-demo` для сводки CSV.")
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["status"], "implemented", report)
            project = report["execution_result"]["greenfield_project"]["greenfield_project_brief"]
            self.assertEqual(project["template_id"], "data_processing_tool")
            self.assertTrue(any(feature["id"] == "csv_summary" for feature in project["acceptance_features"]))
            self.assertIn("csv_summary", project["implementation_feature_report"]["recognized_feature_ids"])
            self.assertIn("average numeric columns", json.dumps(project["module_contracts"], ensure_ascii=False))
            source = (repo / "csv_demo/processor.py").read_text(encoding="utf-8")
            self.assertIn("numeric_averages", source)
            self.assertIn("numeric_sums", source)
            tests = (repo / "tests/test_processor.py").read_text(encoding="utf-8")
            self.assertIn("test_counts_rows_columns_sums_and_averages", tests)
            verification = report["execution_result"]["greenfield_project"]["verification"]
            self.assertEqual(verification["status"], "passed", verification)
            review = report["execution_result"]["greenfield_project"]["greenfield_review"]
            self.assertEqual(review["semantic_review"]["status"], "passed", review)
            self.assertEqual(review["status"], "passed", review)

    def test_project_creation_sales_analytics_pipeline_implements_multi_workflow_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(repo, "Создай data sales analytics pipeline `sales-demo` для CSV с фильтрацией, группировкой и отчетом.")
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["status"], "implemented", report)
            project = report["execution_result"]["greenfield_project"]["greenfield_project_brief"]
            self.assertEqual(project["template_id"], "data_processing_tool")
            self.assertTrue(any(feature["id"] == "sales_analytics_pipeline" for feature in project["acceptance_features"]))
            self.assertIn("sales_analytics_pipeline", project["implementation_feature_report"]["recognized_feature_ids"])
            self.assertGreaterEqual(len(project["module_contracts"]), 5)
            self.assertGreaterEqual(project["scenario_plan"]["scenario_count"], 3)
            source_paths = {
                "sales_demo/loader.py",
                "sales_demo/analyzer.py",
                "sales_demo/report.py",
                "sales_demo/cli.py",
                "tests/test_sales_pipeline.py",
            }
            self.assertTrue(source_paths.issubset(set(project["expected_files"])))
            self.assertIn("def load_records", (repo / "sales_demo/loader.py").read_text(encoding="utf-8"))
            self.assertIn("def group_region_totals", (repo / "sales_demo/analyzer.py").read_text(encoding="utf-8"))
            self.assertIn("def render_markdown_report", (repo / "sales_demo/report.py").read_text(encoding="utf-8"))
            self.assertIn("def run_pipeline", (repo / "sales_demo/cli.py").read_text(encoding="utf-8"))
            tests = (repo / "tests/test_sales_pipeline.py").read_text(encoding="utf-8")
            self.assertIn("test_load_filter_and_group_workflow", tests)
            self.assertIn("test_summary_and_markdown_report_workflow", tests)
            self.assertIn("test_cli_json_output_workflow", tests)
            module_rows = {row["path"]: row for row in project["implementation_plan"]["module_sequence"]}
            for module_path in ("sales_demo/loader.py", "sales_demo/analyzer.py", "sales_demo/report.py", "sales_demo/cli.py"):
                self.assertEqual(module_rows[module_path]["paired_tests"], ["tests/test_sales_pipeline.py"])
            trace_rows = project["implementation_trace"]["rows"]
            self.assertTrue(all(row["verification_files"] == ["tests/test_sales_pipeline.py"] for row in trace_rows if row["file"].startswith("sales_demo/") and not row["file"].endswith("__init__.py")))
            verification = report["execution_result"]["greenfield_project"]["verification"]
            self.assertEqual(verification["status"], "passed", verification)
            review = report["execution_result"]["greenfield_project"]["greenfield_review"]
            self.assertEqual(review["semantic_review"]["status"], "passed", review)
            self.assertEqual(review["scenario_review"]["status"], "passed", review)
            self.assertEqual(review["status"], "passed", review)

    def test_project_creation_local_agent_tool_implements_command_router_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(repo, "Создай local agent tool router `agent-demo` для команд status echo summarize.")
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["status"], "implemented", report)
            project = report["execution_result"]["greenfield_project"]["greenfield_project_brief"]
            self.assertEqual(project["template_id"], "local_agent_tool")
            self.assertTrue(any(feature["id"] == "local_agent_command_router" for feature in project["acceptance_features"]))
            self.assertIn("local_agent_command_router", project["implementation_feature_report"]["recognized_feature_ids"])
            self.assertGreaterEqual(len(project["module_contracts"]), 7)
            self.assertGreaterEqual(project["scenario_plan"]["scenario_count"], 3)
            self.assertIn("reject unknown actions", json.dumps(project["module_contracts"], ensure_ascii=False))
            expected = {
                "agent_demo/registry.py",
                "agent_demo/schema.py",
                "agent_demo/session.py",
                "agent_demo/runner.py",
                "agent_demo/contract.py",
                "agent_demo/tool.py",
                "tests/test_contract.py",
            }
            self.assertTrue(expected.issubset(set(project["expected_files"])))
            registry = (repo / "agent_demo/registry.py").read_text(encoding="utf-8")
            self.assertIn("ACTION_REGISTRY", registry)
            self.assertIn("def available_actions", registry)
            self.assertIn("unsupported action", registry)
            schema = (repo / "agent_demo/schema.py").read_text(encoding="utf-8")
            self.assertIn("def validate_payload", schema)
            self.assertIn("payload must be a JSON object", schema)
            session = (repo / "agent_demo/session.py").read_text(encoding="utf-8")
            self.assertIn("class AgentSession", session)
            self.assertIn("def record_action", session)
            runner = (repo / "agent_demo/runner.py").read_text(encoding="utf-8")
            self.assertIn("def run_action", runner)
            self.assertIn("def run_sequence", runner)
            cli = (repo / "agent_demo/tool.py").read_text(encoding="utf-8")
            self.assertIn("json.loads", cli)
            self.assertIn("build_parser", cli)
            self.assertIn("--sequence", cli)
            tests = (repo / "tests/test_contract.py").read_text(encoding="utf-8")
            self.assertIn("test_unknown_action_is_rejected", tests)
            self.assertIn("test_cli_prints_json", tests)
            self.assertIn("test_session_records_cross_command_workflow", tests)
            self.assertIn("test_sequence_runner_preserves_session_order", tests)
            module_rows = {row["path"]: row for row in project["implementation_plan"]["module_sequence"]}
            for module_path in ("agent_demo/registry.py", "agent_demo/schema.py", "agent_demo/session.py", "agent_demo/runner.py", "agent_demo/contract.py", "agent_demo/tool.py"):
                self.assertEqual(module_rows[module_path]["paired_tests"], ["tests/test_contract.py"])
            verification = report["execution_result"]["greenfield_project"]["verification"]
            self.assertEqual(verification["status"], "passed", verification)
            review = report["execution_result"]["greenfield_project"]["greenfield_review"]
            self.assertEqual(review["semantic_review"]["status"], "passed", review)
            self.assertEqual(review["scenario_review"]["status"], "passed", review)
            self.assertEqual(review["status"], "passed", review)

    def test_project_creation_telegram_bot_implements_command_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(repo, "Создай telegram bot `bot-demo` с командами /start /help /status /echo.")
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["status"], "implemented", report)
            project = report["execution_result"]["greenfield_project"]["greenfield_project_brief"]
            self.assertEqual(project["template_id"], "telegram_bot_python")
            self.assertTrue(any(feature["id"] == "telegram_command_bot" for feature in project["acceptance_features"]))
            self.assertIn("telegram_command_bot", project["implementation_feature_report"]["recognized_feature_ids"])
            self.assertIn("handle echo command", json.dumps(project["module_contracts"], ensure_ascii=False))
            source = (repo / "bot_demo/bot.py").read_text(encoding="utf-8")
            self.assertIn("COMMANDS", source)
            self.assertIn("def command_list", source)
            self.assertIn("TELEGRAM_BOT_TOKEN is required", source)
            tests = (repo / "tests/test_bot.py").read_text(encoding="utf-8")
            self.assertIn("test_start_help_and_status", tests)
            self.assertIn("test_runtime_requires_token", tests)
            verification = report["execution_result"]["greenfield_project"]["verification"]
            self.assertEqual(verification["status"], "passed", verification)
            review = report["execution_result"]["greenfield_project"]["greenfield_review"]
            self.assertEqual(review["semantic_review"]["status"], "passed", review)
            self.assertEqual(review["status"], "passed", review)

    def test_project_creation_vite_counter_app_implements_task_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(repo, "Создай Vite React counter app `counter-demo` со счетчиком.")
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["status"], "implemented", report)
            project = report["execution_result"]["greenfield_project"]["greenfield_project_brief"]
            self.assertEqual(project["template_id"], "node_vite_app")
            self.assertTrue(any(feature["id"] == "vite_counter_app" for feature in project["acceptance_features"]))
            self.assertIn("vite_counter_app", project["implementation_feature_report"]["recognized_feature_ids"])
            self.assertIn("increment count", json.dumps(project["module_contracts"], ensure_ascii=False))
            source = (repo / "src/main.jsx").read_text(encoding="utf-8")
            self.assertIn("export function CounterApp", source)
            self.assertIn("const increment", source)
            self.assertIn("const decrement", source)
            self.assertIn("const reset", source)
            tests = (repo / "tests/test_vite_contract.py").read_text(encoding="utf-8")
            self.assertIn("test_counter_behaviors_are_implemented", tests)
            verification = report["execution_result"]["greenfield_project"]["verification"]
            self.assertEqual(verification["status"], "passed", verification)
            review = report["execution_result"]["greenfield_project"]["greenfield_review"]
            self.assertEqual(review["semantic_review"]["status"], "passed", review)
            self.assertEqual(review["status"], "passed", review)

    def test_project_creation_vite_todo_dashboard_does_not_collapse_to_counter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(
                repo,
                "Создай Vite frontend web app `dashboard-demo`: dashboard с карточками задач, фильтрами active/done/all, счетчиком оставшихся задач, кнопкой toggle done и localStorage.",
            )
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["status"], "implemented", report)
            project = report["execution_result"]["greenfield_project"]["greenfield_project_brief"]
            self.assertEqual(project["template_id"], "node_vite_app")
            self.assertTrue(any(feature["id"] == "todo_list" for feature in project["acceptance_features"]))
            self.assertFalse(any(feature["id"] == "vite_counter_app" for feature in project["acceptance_features"]))
            self.assertIn("todo_list", project["implementation_feature_report"]["recognized_feature_ids"])
            self.assertIn("filter all active done tasks", json.dumps(project["module_contracts"], ensure_ascii=False))
            source = (repo / "src/main.jsx").read_text(encoding="utf-8")
            self.assertIn("export function TodoDashboard", source)
            self.assertIn("function loadTodos", source)
            self.assertIn("filterTasks", source)
            self.assertIn("remainingTasks", source)
            self.assertIn("toggleDone", source)
            self.assertIn("localStorage", source)
            self.assertNotIn("CounterApp", source)
            tests = (repo / "tests/test_vite_contract.py").read_text(encoding="utf-8")
            self.assertIn("test_task_dashboard_behaviors_are_implemented", tests)
            self.assertIn("reject counter-app substitution", json.dumps(project["module_contracts"], ensure_ascii=False))
            verification = report["execution_result"]["greenfield_project"]["verification"]
            self.assertEqual(verification["status"], "passed", verification)
            review = report["execution_result"]["greenfield_project"]["greenfield_review"]
            self.assertEqual(review["scenario_review"]["status"], "passed", review)
            self.assertEqual(review["status"], "passed", review)

    def test_project_creation_text_utils_library_implements_task_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            brief = project_creation_brief(repo, "Создай python text utils library `text-demo` со slugify и word count.")
            report = code_brigade_adapter.build_worker_report(brief, dry_run=False)
            self.assertEqual(report["status"], "implemented", report)
            project = report["execution_result"]["greenfield_project"]["greenfield_project_brief"]
            self.assertEqual(project["template_id"], "python_library")
            self.assertTrue(any(feature["id"] == "python_text_utils_library" for feature in project["acceptance_features"]))
            self.assertIn("python_text_utils_library", project["implementation_feature_report"]["recognized_feature_ids"])
            self.assertIn("generate ascii slugs", json.dumps(project["module_contracts"], ensure_ascii=False))
            source = (repo / "text_demo/core.py").read_text(encoding="utf-8")
            self.assertIn("def normalize_text", source)
            self.assertIn("def slugify", source)
            self.assertIn("def word_count", source)
            self.assertIn("def summarize_text", source)
            tests = (repo / "tests/test_library.py").read_text(encoding="utf-8")
            self.assertIn("test_slugify_generates_ascii_slug", tests)
            self.assertIn("test_summarize_text", tests)
            verification = report["execution_result"]["greenfield_project"]["verification"]
            self.assertEqual(verification["status"], "passed", verification)
            review = report["execution_result"]["greenfield_project"]["greenfield_review"]
            self.assertEqual(review["semantic_review"]["status"], "passed", review)
            self.assertEqual(review["status"], "passed", review)

    def test_greenfield_project_brief_contract_and_templates(self) -> None:
        cli = build_greenfield_project_brief("Создай новый CLI проект `forge-tool`.")
        api = build_greenfield_project_brief("Создай FastAPI backend service `api-demo`.")
        static = build_greenfield_project_brief("Создай static frontend website `site-demo`.")
        library = build_greenfield_project_brief("Создай python library package `lib-demo`.")
        vite = build_greenfield_project_brief("Создай Vite frontend web app `vite-demo`.")
        game = build_greenfield_project_brief("Создай browser game `game-demo` with keyboard controls and score.")
        bot = build_greenfield_project_brief("Создай telegram bot `bot-demo`.")
        data_tool = build_greenfield_project_brief("Создай data csv processing tool `data-demo`.")
        agent_tool = build_greenfield_project_brief("Создай local agent tool `agent-demo`.")
        self.assertEqual(validate_greenfield_project_brief(cli), [])
        self.assertEqual(validate_greenfield_project_brief(api), [])
        self.assertEqual(validate_greenfield_project_brief(static), [])
        for project in (library, vite, game, bot, data_tool, agent_tool):
            self.assertEqual(validate_greenfield_project_brief(project), [])
            self.assertTrue(project["template_contract"]["common_failure_fixes"])
            self.assertEqual(project["template_contract"]["expected_tree"], project["expected_files"])
        self.assertEqual(cli["project_type"], "cli_tool")
        self.assertEqual(api["project_type"], "api_service")
        self.assertEqual(static["template_id"], "static_site")
        self.assertEqual(library["template_id"], "python_library")
        self.assertEqual(vite["template_id"], "node_vite_app")
        self.assertEqual(game["project_type"], "game")
        self.assertEqual(game["template_id"], "static_browser_game")
        game_scenarios = game["scenario_plan"]["rows"]
        self.assertEqual(game_scenarios[0]["id"], "browser_game_loop")
        self.assertIn("requestAnimationFrame", game_scenarios[0]["required_markers"])
        self.assertNotIn("ready", game_scenarios[0]["required_markers"])
        self.assertEqual(bot["template_id"], "telegram_bot_python")
        self.assertEqual(data_tool["template_id"], "data_processing_tool")
        self.assertEqual(agent_tool["template_id"], "local_agent_tool")
        self.assertTrue({"python_cli_basic", "python_fastapi_service", "python_library", "node_vite_app", "static_site", "static_browser_game", "telegram_bot_python", "data_processing_tool", "local_agent_tool"}.issubset(set(available_templates())))
        self.assertTrue(any(path.endswith("cli.py") for path in cli["expected_files"]))
        self.assertIn("requirements.txt", api["expected_files"])
        self.assertIn("architecture_plan.json", cli["expected_files"])
        self.assertIn("file_tree_plan.json", cli["expected_files"])
        self.assertIn("implementation_trace.json", cli["expected_files"])
        self.assertIn("module_contracts.json", cli["expected_files"])
        self.assertIn("verification_plan.json", cli["expected_files"])
        self.assertIn("package.json", vite["expected_files"])
        self.assertIn("game.js", game["expected_files"])
        self.assertIn("tests/test_browser_game.py", game["expected_files"])
        self.assertIn("requirements.txt", bot["expected_files"])
        self.assertIn("pyproject.toml", data_tool["expected_files"])
        self.assertIn("pyproject.toml", agent_tool["expected_files"])
        self.assertIn("tests/test_static_site.py", static["expected_files"])
        for project in (cli, api, static, library, vite, game, bot, data_tool, agent_tool):
            contract_paths = {str(row.get("path") or "") for row in project["module_contracts"]}
            plan_paths = {str(row.get("path") or "") for row in project["implementation_plan"]["module_sequence"]}
            for test_file in project["implementation_plan"]["test_files"]:
                self.assertIn(test_file, contract_paths, project["template_id"])
                self.assertIn(test_file, plan_paths, project["template_id"])
        todo = build_greenfield_project_brief("Создай static frontend website todo list `todo-demo`.")
        self.assertTrue(any(feature["id"] == "todo_list" for feature in todo["acceptance_features"]))
        self.assertGreaterEqual(len(todo["module_contracts"]), 3)
        kanban = build_greenfield_project_brief("Создай static frontend website kanban board `kanban-demo`.")
        self.assertTrue(any(feature["id"] == "kanban_board_frontend" for feature in kanban["acceptance_features"]))
        self.assertGreaterEqual(len(kanban["module_contracts"]), 5)
        self.assertGreaterEqual(kanban["scenario_plan"]["scenario_count"], 3)
        notes = build_greenfield_project_brief("Создай FastAPI notes API `notes-demo`.")
        self.assertTrue(any(feature["id"] == "notes_api" for feature in notes["acceptance_features"]))
        self.assertGreaterEqual(len(notes["module_contracts"]), 2)
        issue_tracker = build_greenfield_project_brief("Создай FastAPI issue tracker API `issue-demo`.")
        self.assertTrue(any(feature["id"] == "issue_tracker_api" for feature in issue_tracker["acceptance_features"]))
        self.assertGreaterEqual(len(issue_tracker["module_contracts"]), 5)
        self.assertGreaterEqual(issue_tracker["scenario_plan"]["scenario_count"], 3)
        maintenance = build_greenfield_project_brief("Создай FastAPI service `maintenance-demo` для заявок обслуживания оборудования: создать заявку, назначить техника, менять статус open/in_progress/resolved, фильтровать по статусу и технику, считать summary по статусам.")
        self.assertTrue(any(feature["id"] == "maintenance_work_orders_api" for feature in maintenance["acceptance_features"]))
        self.assertGreaterEqual(len(maintenance["module_contracts"]), 5)
        self.assertGreaterEqual(maintenance["scenario_plan"]["scenario_count"], 3)
        self.assertIn("app/store.py", maintenance["expected_files"])
        self.assertIn("tests/test_maintenance.py", maintenance["expected_files"])
        operations_dashboard = build_greenfield_project_brief("Создай FastAPI operations dashboard API `ops-demo`.")
        self.assertTrue(any(feature["id"] == "operations_dashboard_api" for feature in operations_dashboard["acceptance_features"]))
        self.assertGreaterEqual(len(operations_dashboard["module_contracts"]), 7)
        self.assertGreaterEqual(operations_dashboard["scenario_plan"]["scenario_count"], 4)
        csv_summary = build_greenfield_project_brief("Создай data csv summary tool `csv-demo`.")
        self.assertTrue(any(feature["id"] == "csv_summary" for feature in csv_summary["acceptance_features"]))
        self.assertGreaterEqual(len(csv_summary["module_contracts"]), 3)
        sales_pipeline = build_greenfield_project_brief("Создай data sales analytics pipeline `sales-demo` для CSV.")
        self.assertTrue(any(feature["id"] == "sales_analytics_pipeline" for feature in sales_pipeline["acceptance_features"]))
        self.assertGreaterEqual(len(sales_pipeline["module_contracts"]), 5)
        self.assertGreaterEqual(sales_pipeline["scenario_plan"]["scenario_count"], 3)
        agent_router = build_greenfield_project_brief("Создай local agent tool router `agent-demo`.")
        self.assertTrue(any(feature["id"] == "local_agent_command_router" for feature in agent_router["acceptance_features"]))
        self.assertGreaterEqual(len(agent_router["module_contracts"]), 7)
        self.assertGreaterEqual(agent_router["scenario_plan"]["scenario_count"], 3)
        command_bot = build_greenfield_project_brief("Создай telegram bot `bot-demo` с командами /start /help.")
        self.assertTrue(any(feature["id"] == "telegram_command_bot" for feature in command_bot["acceptance_features"]))
        self.assertGreaterEqual(len(command_bot["module_contracts"]), 2)
        counter_app = build_greenfield_project_brief("Создай Vite React counter app `counter-demo`.")
        self.assertTrue(any(feature["id"] == "vite_counter_app" for feature in counter_app["acceptance_features"]))
        self.assertGreaterEqual(len(counter_app["module_contracts"]), 3)
        vite_todo = build_greenfield_project_brief("Создай Vite dashboard с карточками задач, фильтрами active/done/all и localStorage `dash-demo`.")
        self.assertTrue(any(feature["id"] == "todo_list" for feature in vite_todo["acceptance_features"]))
        self.assertFalse(any(feature["id"] == "vite_counter_app" for feature in vite_todo["acceptance_features"]))
        self.assertGreaterEqual(len(vite_todo["module_contracts"]), 3)
        self.assertGreaterEqual(vite_todo["scenario_plan"]["scenario_count"], 2)
        text_utils = build_greenfield_project_brief("Создай python text utils library `text-demo`.")
        self.assertTrue(any(feature["id"] == "python_text_utils_library" for feature in text_utils["acceptance_features"]))
        self.assertGreaterEqual(len(text_utils["module_contracts"]), 3)

    def test_greenfield_project_brief_schema_tracks_runtime_contract(self) -> None:
        schema_path = Path(__file__).with_name("greenfield_project_brief.schema.json")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        required = set(schema["required"])
        self.assertIn("implementation_plan", required)
        self.assertIn("implementation_trace", required)
        self.assertIn("implementation_feature_report", required)
        self.assertIn("scenario_plan", required)
        implementation_required = set(schema["properties"]["implementation_plan"]["required"])
        self.assertTrue({"role", "synthesis_policy", "module_sequence"}.issubset(implementation_required))
        module_required = set(schema["properties"]["implementation_plan"]["properties"]["module_sequence"]["items"]["required"])
        self.assertIn("code_synthesis_contract", module_required)
        trace_required = set(schema["properties"]["implementation_trace"]["required"])
        self.assertTrue({"kind", "contract_version", "status", "requirement_trace_count", "module_count", "rows"}.issubset(trace_required))
        trace_row_required = set(schema["properties"]["implementation_trace"]["properties"]["rows"]["items"]["required"])
        self.assertIn("synthesis_contract_kind", trace_row_required)
        feature_required = set(schema["properties"]["implementation_feature_report"]["required"])
        self.assertTrue({"kind", "recognized_feature_ids", "changed_file_paths", "changed_module_contract_paths", "implementation_strategy"}.issubset(feature_required))
        scenario_required = set(schema["properties"]["scenario_plan"]["required"])
        self.assertTrue({"kind", "scenario_count", "rows"}.issubset(scenario_required))

    def test_greenfield_capability_audit_tracks_objective_scope(self) -> None:
        audit_path = Path(__file__).with_name("greenfield_capability_audit.json")
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        self.assertEqual(audit["kind"], "code_brigade_greenfield_capability_audit")
        self.assertEqual(audit["status"], "in_progress")
        rows = audit["requirements"]
        self.assertEqual({row["objective_item"] for row in rows}, set(range(1, 11)))
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id["implementation_worker"]["status"], "partial")
        self.assertEqual(by_id["model_integration"]["status"], "partial")
        self.assertTrue(all(row["evidence"] for row in rows))
        self.assertTrue(audit["next_recommended_work"])

    def test_greenfield_live_trial_compact_result_exposes_model_synthesis_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            (run_dir / "task.json").write_text(json.dumps({"task": "live trial"}), encoding="utf-8")
            worker_report = {
                "status": "blocked",
                "execution_result": {
                    "status": "blocked",
                    "blockers": ["greenfield module model synthesis did not produce accepted code: model_unavailable"],
                    "greenfield_project": {
                        "file_set_synthesis_report": {"status": "model_unavailable", "blockers": ["model down"]},
                        "implementation_synthesis_report": {
                            "status": "model_unavailable",
                            "applied_count": 0,
                            "model_unavailable_count": 2,
                            "blocked_count": 0,
                            "rows": [{"module": "demo.core", "path": "demo/core.py", "status": "model_unavailable", "model_guidance_status": "unavailable", "blockers": ["model down"]}],
                        },
                        "verification": {"status": "planned", "results": []},
                        "greenfield_review": {"status": "blocked", "blockers": ["verification did not pass"]},
                        "greenfield_model_guidance_ledger": {"status": "partial", "entries": [{"role": "GreenfieldImplementationWorker", "status": "model_unavailable"}]},
                        "greenfield_run_report": {"implementation_synthesis_status": "model_unavailable"},
                    },
                },
            }
            (run_dir / "worker_report.json").write_text(json.dumps(worker_report), encoding="utf-8")
            result = compact_greenfield_result({"ok": False, "package_ok": False, "ready_for_execution": False, "state": "failed", "review_decision": "blocked", "run_dir": str(run_dir)}, root / "workspace")
            self.assertEqual(result["kind"], "code_brigade_greenfield_live_model_trial_result")
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["module_synthesis_status"], "model_unavailable")
            self.assertEqual(result["module_synthesis_model_unavailable_count"], 2)
            self.assertEqual(result["model_guidance_ledger_status"], "partial")

    def test_greenfield_live_trial_allocator_avoids_parallel_workspace_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp)
            first = allocate_live_trial_root(run_root)
            second = allocate_live_trial_root(run_root)
            self.assertNotEqual(first, second)
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assertTrue(first.name.startswith("greenfield-live-"))
            self.assertTrue(second.name.startswith("greenfield-live-"))

    def test_greenfield_repair_live_trial_compact_result_requires_repair_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            loop = {
                "status": "passed",
                "stop_reason": "verification passed",
                "attempts": [
                    {
                        "repair_execution": {
                            "status": "applied",
                            "repaired_files": [
                                {"path": "calc.py", "repair": "guided_replace_return_expression", "status": "applied"}
                            ],
                            "blockers": [],
                        }
                    }
                ],
                "final_verification": {"status": "passed"},
                "stop_condition_evidence": {"reason": "verification passed"},
            }
            result = compact_repair_result("return_expression", workspace, {"verification_commands": ["python -m unittest test_calc.py"]}, loop)
            self.assertEqual(result["kind"], "code_brigade_greenfield_live_repair_trial_result")
            self.assertEqual(result["status"], "accepted")
            self.assertEqual(result["scenario"], "return_expression")
            self.assertEqual(result["repair_attempt_count"], 1)
            self.assertTrue(result["bounded_repair_applied"])
            self.assertFalse(result["module_synthesis_repair_applied"])
            self.assertEqual(result["repaired_files"][0]["repair"], "guided_replace_return_expression")
            blocked = compact_repair_result("return_expression", workspace, {"verification_commands": []}, {"status": "passed", "attempts": []})
            self.assertEqual(blocked["status"], "blocked")

    def test_greenfield_repair_live_trial_function_body_requires_multi_statement_fix(self) -> None:
        spec = scenario_spec("function_body")
        grades = next(item["content"] for item in spec["files"] if item["path"] == "grades.py")
        tests = next(item["content"] for item in spec["files"] if item["path"] == "test_grades.py")
        self.assertEqual(grades, "def grade(score):\n    return 'C'\n")
        self.assertIn("grades.grade(95), 'A'", tests)
        self.assertIn("grades.grade(85), 'B'", tests)
        self.assertIn("with self.assertRaises(ValueError)", tests)
        self.assertIn("replacing the grade() function body", spec["task"])

    def test_greenfield_repair_live_trial_compact_result_marks_module_synthesis_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            loop = {
                "status": "passed",
                "stop_reason": "verification passed",
                "attempts": [
                    {
                        "repair_execution": {
                            "status": "applied",
                            "repair_strategy": "module_synthesis_repair",
                            "repaired_files": [{"path": "grades.py", "repair": "verification_repair_module_synthesis"}],
                            "blockers": ["no bounded greenfield repair was applicable"],
                        }
                    }
                ],
                "final_verification": {"status": "passed"},
                "stop_condition_evidence": {"reason": "verification passed"},
            }
            result = compact_repair_result("function_body", workspace, {"verification_commands": ["python -m unittest test_grades.py"]}, loop)
            self.assertEqual(result["status"], "accepted")
            self.assertFalse(result["bounded_repair_applied"])
            self.assertTrue(result["module_synthesis_repair_applied"])
            self.assertFalse(result["multi_file_repair_applied"])

    def test_greenfield_repair_live_trial_compact_result_marks_multi_file_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            loop = {
                "status": "passed",
                "stop_reason": "verification passed",
                "attempts": [
                    {
                        "repair_execution": {
                            "status": "applied",
                            "repair_strategy": "module_synthesis_repair",
                            "repaired_files": [
                                {"path": "billing/pricing.py", "repair": "verification_repair_module_synthesis"},
                                {"path": "billing/invoice.py", "repair": "verification_repair_module_synthesis"},
                            ],
                            "blockers": [],
                        }
                    }
                ],
                "final_verification": {"status": "passed"},
                "stop_condition_evidence": {"reason": "verification passed"},
            }
            result = compact_repair_result("multi_file", workspace, {"verification_commands": ["python -m unittest discover tests"]}, loop)
            self.assertEqual(result["status"], "accepted")
            self.assertTrue(result["module_synthesis_repair_applied"])
            self.assertTrue(result["multi_file_repair_applied"])
            self.assertEqual(result["repaired_path_count"], 2)

    def test_greenfield_repair_live_trial_multi_file_scenario_requires_two_modules(self) -> None:
        spec = scenario_spec("multi_file")
        paths = {item["path"] for item in spec["files"]}
        contract_paths = {item["path"] for item in spec["module_contracts"]}
        self.assertIn("billing/pricing.py", paths)
        self.assertIn("billing/invoice.py", paths)
        self.assertIn("billing/pricing.py", contract_paths)
        self.assertIn("billing/invoice.py", contract_paths)
        tests = next(item["content"] for item in spec["files"] if item["path"] == "tests/test_invoice.py")
        self.assertIn("discounted_subtotal(ITEMS), 225", tests)
        self.assertIn("invoice['summary']", tests)

    def test_greenfield_model_json_parser_repairs_code_regex_escapes(self) -> None:
        payload = """```json
{"path":"src/main.jsx","content":"const compact = value.replace(/\\s+/g, ' ');\\nconst id = value.match(/\\d+/)?.[0];","requirements_satisfied":["render board"],"tests_to_update":["tests/test_vite_contract.py"],"notes":"ok"}
```"""
        output = extract_json_object(payload)
        self.assertEqual(output["path"], "src/main.jsx")
        self.assertIn(r"/\s+/g", output["content"])
        self.assertIn(r"/\d+/", output["content"])

    def test_greenfield_model_json_parser_repairs_literal_newlines_inside_code_content(self) -> None:
        payload = """{
  "path": "src/main.jsx",
  "content": "function createCard(title) {
  return { title };
}",
  "requirements_satisfied": ["render board"],
  "tests_to_update": ["tests/test_vite_contract.py"],
  "notes": "ok"
}"""
        output = extract_json_object(payload)
        self.assertEqual(output["path"], "src/main.jsx")
        self.assertIn("function createCard", output["content"])
        self.assertIn("\n  return", output["content"])

    def test_greenfield_model_json_parser_recovers_unescaped_code_quotes(self) -> None:
        payload = """{
  "path": "src/main.jsx",
  "content": "function renderBoard() {
  const root = document.getElementById("root");
  root.textContent = "ready";
}",
  "requirements_satisfied": ["render board"],
  "tests_to_update": ["tests/test_vite_contract.py"],
  "notes": "ok"
}"""
        output = extract_json_object(payload)
        self.assertEqual(output["path"], "src/main.jsx")
        self.assertIn('document.getElementById("root")', output["content"])
        self.assertEqual(output["requirements_satisfied"], ["render board"])
        self.assertEqual(output["tests_to_update"], ["tests/test_vite_contract.py"])


if __name__ == "__main__":
    unittest.main()
