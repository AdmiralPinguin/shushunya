#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import code_brigade_adapter
from diagnostic_repair_contract import execute_diagnostic_repair_loop, execute_diagnostic_repair_request
from greenfield_project import build_greenfield_project_brief, forbidden_placeholder_markers_found, run_dependency_worker, run_greenfield_verification_loop, validate_greenfield_project_brief
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
                    "greenfield_project_brief.json",
                    "module_contracts.json",
                    "test_app.py",
                    "verification_plan.json",
                ],
            )
            self.assertEqual(report["execution_result"]["greenfield_project"]["verification"]["status"], "passed")
            project_brief = report["execution_result"]["greenfield_project"]["greenfield_project_brief"]
            self.assertEqual(project_brief["kind"], "code_brigade_greenfield_project_brief")
            self.assertEqual(project_brief["implementation_plan"]["kind"], "code_brigade_greenfield_implementation_plan")
            self.assertTrue(project_brief["implementation_plan"]["module_sequence"])
            memory = report["execution_result"]["greenfield_project"]["greenfield_memory_record"]
            self.assertEqual(memory["kind"], "code_brigade_greenfield_memory_record")
            self.assertEqual(memory["template_id"], project_brief["template_id"])
            self.assertEqual(memory["semantic_review_status"], "passed")
            self.assertTrue(memory["reusable_learnings"])
            review = report["execution_result"]["greenfield_project"]["greenfield_review"]
            self.assertIn("model_guidance", review)
            self.assertEqual(review["semantic_review"]["status"], "passed")

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
            self.assertEqual(len(loop["attempts"]), 2)
            repair = loop["attempts"][0]["repair_execution"]
            self.assertEqual(repair["status"], "applied")
            self.assertTrue(any(row["path"] == "repair_demo/core.py" for row in repair["repaired_files"]))
            self.assertTrue((repo / "repair_demo/core.py").exists())

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
            self.assertEqual(report["dependency_strategy"]["install_default"], "record_manifest_only")
            self.assertEqual(report["new_lockfiles"], [])

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


if __name__ == "__main__":
    unittest.main()
