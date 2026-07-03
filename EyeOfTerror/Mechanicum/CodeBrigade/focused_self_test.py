#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import code_brigade_adapter
from diagnostic_repair_contract import execute_diagnostic_repair_loop, execute_diagnostic_repair_request
from greenfield_architect import build_greenfield_project_brief as architect_build_greenfield_project_brief
from greenfield_dependency_worker import dependency_manager_status
from greenfield_feature_worker import infer_acceptance_features
from greenfield_implementation_worker import execute_file_set_synthesis_contract, execute_module_synthesis_contracts, generated_file_quality
from greenfield_implementation_worker import build_implementation_trace as worker_build_implementation_trace
from greenfield_implementation_worker import build_implementation_worker_plan as worker_build_implementation_worker_plan
from greenfield_memory_worker import build_greenfield_memory_record
from greenfield_project import build_greenfield_project_brief, forbidden_placeholder_markers_found, run_dependency_worker, run_greenfield_verification_loop, validate_greenfield_project_brief
from greenfield_review_worker import python_source_semantic_status
from greenfield_scenario_worker import review_greenfield_scenarios
from greenfield_scaffold_worker import greenfield_workspace_status, normalize_project_file_rows, scaffold_greenfield_files
from greenfield_verification_worker import verification_failure_signature
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
                    "greenfield_module_synthesis_report.json",
                    "greenfield_project_brief.json",
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

    def test_greenfield_review_worker_scores_python_source_strength(self) -> None:
        self.assertEqual(python_source_semantic_status("VALUE = 1\n"), "weak")
        self.assertEqual(python_source_semantic_status("def run():\n    return 'ready'\n\nif True:\n    run()\n"), "ok")

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
        feature_ids = {feature["id"] for feature in infer_acceptance_features("notes api issue tracker todo calculator csv summary sales analytics pipeline local agent tool router telegram bot /start /help vite counter app text utils library")}
        self.assertEqual(feature_ids, {"calculator_operations", "todo_list", "notes_api", "issue_tracker_api", "csv_summary", "sales_analytics_pipeline", "local_agent_command_router", "telegram_command_bot", "vite_counter_app", "python_text_utils_library"})

    def test_greenfield_architect_owns_project_brief_and_plan(self) -> None:
        project = architect_build_greenfield_project_brief("Создай CLI калькулятор `architect-calc`.")
        self.assertEqual(project["kind"], "code_brigade_greenfield_project_brief")
        self.assertEqual(project["architecture_plan"]["selected_template"], "python_cli_basic")
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
        self.assertEqual(pip_status["binary"], "python")

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
            self.assertIn("reject unknown actions", json.dumps(project["module_contracts"], ensure_ascii=False))
            source = (repo / "agent_demo/contract.py").read_text(encoding="utf-8")
            self.assertIn("ACTION_REGISTRY", source)
            self.assertIn("def available_actions", source)
            self.assertIn("unsupported action", source)
            cli = (repo / "agent_demo/tool.py").read_text(encoding="utf-8")
            self.assertIn("json.loads", cli)
            self.assertIn("build_parser", cli)
            tests = (repo / "tests/test_contract.py").read_text(encoding="utf-8")
            self.assertIn("test_unknown_action_is_rejected", tests)
            self.assertIn("test_cli_prints_json", tests)
            verification = report["execution_result"]["greenfield_project"]["verification"]
            self.assertEqual(verification["status"], "passed", verification)
            review = report["execution_result"]["greenfield_project"]["greenfield_review"]
            self.assertEqual(review["semantic_review"]["status"], "passed", review)
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
        bot = build_greenfield_project_brief("Создай telegram bot `bot-demo`.")
        data_tool = build_greenfield_project_brief("Создай data csv processing tool `data-demo`.")
        agent_tool = build_greenfield_project_brief("Создай local agent tool `agent-demo`.")
        self.assertEqual(validate_greenfield_project_brief(cli), [])
        self.assertEqual(validate_greenfield_project_brief(api), [])
        self.assertEqual(validate_greenfield_project_brief(static), [])
        for project in (library, vite, bot, data_tool, agent_tool):
            self.assertEqual(validate_greenfield_project_brief(project), [])
            self.assertTrue(project["template_contract"]["common_failure_fixes"])
            self.assertEqual(project["template_contract"]["expected_tree"], project["expected_files"])
        self.assertEqual(cli["project_type"], "cli_tool")
        self.assertEqual(api["project_type"], "api_service")
        self.assertEqual(static["template_id"], "static_site")
        self.assertEqual(library["template_id"], "python_library")
        self.assertEqual(vite["template_id"], "node_vite_app")
        self.assertEqual(bot["template_id"], "telegram_bot_python")
        self.assertEqual(data_tool["template_id"], "data_processing_tool")
        self.assertEqual(agent_tool["template_id"], "local_agent_tool")
        self.assertTrue({"python_cli_basic", "python_fastapi_service", "python_library", "node_vite_app", "static_site", "telegram_bot_python", "data_processing_tool", "local_agent_tool"}.issubset(set(available_templates())))
        self.assertTrue(any(path.endswith("cli.py") for path in cli["expected_files"]))
        self.assertIn("requirements.txt", api["expected_files"])
        self.assertIn("architecture_plan.json", cli["expected_files"])
        self.assertIn("file_tree_plan.json", cli["expected_files"])
        self.assertIn("implementation_trace.json", cli["expected_files"])
        self.assertIn("module_contracts.json", cli["expected_files"])
        self.assertIn("verification_plan.json", cli["expected_files"])
        self.assertIn("package.json", vite["expected_files"])
        self.assertIn("requirements.txt", bot["expected_files"])
        self.assertIn("pyproject.toml", data_tool["expected_files"])
        self.assertIn("pyproject.toml", agent_tool["expected_files"])
        self.assertIn("tests/test_static_site.py", static["expected_files"])
        todo = build_greenfield_project_brief("Создай static frontend website todo list `todo-demo`.")
        self.assertTrue(any(feature["id"] == "todo_list" for feature in todo["acceptance_features"]))
        self.assertGreaterEqual(len(todo["module_contracts"]), 3)
        notes = build_greenfield_project_brief("Создай FastAPI notes API `notes-demo`.")
        self.assertTrue(any(feature["id"] == "notes_api" for feature in notes["acceptance_features"]))
        self.assertGreaterEqual(len(notes["module_contracts"]), 2)
        issue_tracker = build_greenfield_project_brief("Создай FastAPI issue tracker API `issue-demo`.")
        self.assertTrue(any(feature["id"] == "issue_tracker_api" for feature in issue_tracker["acceptance_features"]))
        self.assertGreaterEqual(len(issue_tracker["module_contracts"]), 5)
        self.assertGreaterEqual(issue_tracker["scenario_plan"]["scenario_count"], 3)
        csv_summary = build_greenfield_project_brief("Создай data csv summary tool `csv-demo`.")
        self.assertTrue(any(feature["id"] == "csv_summary" for feature in csv_summary["acceptance_features"]))
        self.assertGreaterEqual(len(csv_summary["module_contracts"]), 3)
        sales_pipeline = build_greenfield_project_brief("Создай data sales analytics pipeline `sales-demo` для CSV.")
        self.assertTrue(any(feature["id"] == "sales_analytics_pipeline" for feature in sales_pipeline["acceptance_features"]))
        self.assertGreaterEqual(len(sales_pipeline["module_contracts"]), 5)
        self.assertGreaterEqual(sales_pipeline["scenario_plan"]["scenario_count"], 3)
        agent_router = build_greenfield_project_brief("Создай local agent tool router `agent-demo`.")
        self.assertTrue(any(feature["id"] == "local_agent_command_router" for feature in agent_router["acceptance_features"]))
        self.assertGreaterEqual(len(agent_router["module_contracts"]), 3)
        command_bot = build_greenfield_project_brief("Создай telegram bot `bot-demo` с командами /start /help.")
        self.assertTrue(any(feature["id"] == "telegram_command_bot" for feature in command_bot["acceptance_features"]))
        self.assertGreaterEqual(len(command_bot["module_contracts"]), 2)
        counter_app = build_greenfield_project_brief("Создай Vite React counter app `counter-demo`.")
        self.assertTrue(any(feature["id"] == "vite_counter_app" for feature in counter_app["acceptance_features"]))
        self.assertGreaterEqual(len(counter_app["module_contracts"]), 3)
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


if __name__ == "__main__":
    unittest.main()
