#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from cogitator_codewright import run


def role_policy(step_id: str) -> dict:
    policies = {
        "repository_survey": {"role": "repository_mapper", "authority": "read_only_repository_mapping", "may_mutate_source": False},
        "change_planning": {"role": "change_strategist", "authority": "scoped_plan_from_repository_evidence", "may_mutate_source": False},
        "implementation": {
            "role": "patchwright",
            "authority": "scoped_source_mutation_from_patch_contract_or_safe_inference",
            "may_mutate_source": True,
        },
        "verification": {"role": "verifier", "authority": "allowlisted_verification_and_narrow_repairs", "may_mutate_source": True},
        "code_review": {"role": "critic", "authority": "read_only_package_review_and_revision_ordering", "may_mutate_source": False},
        "finalize": {"role": "final_packager", "authority": "read_only_final_manifest_packaging", "may_mutate_source": False},
    }
    return policies[step_id]


def request(step_id: str, artifact: str, *, goal: str = "почини python приложение", target_repo_root: Path | None = None) -> dict:
    payload = {
        "task_id": f"ceraxia-test:{step_id}",
        "goal": goal,
        "step": {"step_id": step_id, "expected_artifacts": [artifact]},
        "quality_expectations": {
            "step_quality": {
                "step_id": step_id,
                "role_policy": role_policy(step_id),
            }
        },
    }
    if target_repo_root is not None:
        payload["target_repo_root"] = str(target_repo_root)
    return payload


def run_pipeline(root: Path, *, goal: str = "почини python приложение", target_repo_root: Path | None = None) -> dict:
    steps = [
        ("repository_survey", "/work/code/repo_survey.json"),
        ("change_planning", "/work/code/change_plan.md"),
        ("implementation", "/work/code/patch_manifest.json"),
        ("verification", "/work/code/verification_report.json"),
        ("code_review", "/work/code/code_review.json"),
        ("finalize", "/work/code/final_manifest.json"),
    ]
    for step_id, artifact in steps:
        result = run(request(step_id, artifact, goal=goal, target_repo_root=target_repo_root), root)
        if not result.get("ok") and result.get("status") not in {"blocked", "needs_revision", "passed_with_warnings"}:
            raise AssertionError(f"{step_id} failed: {result}")
        if not (root / artifact.removeprefix("/work/")).exists():
            raise AssertionError(f"{step_id} did not write {artifact}")
    return json.loads((root / "code" / "final_manifest.json").read_text(encoding="utf-8"))


def explicit_patch_goal() -> str:
    return """почини python приложение

CERAXIA_PATCH:
{
  "operations": [
    {
      "type": "replace",
      "path": "sample.py",
      "old": "return 1",
      "new": "return 2"
    }
  ],
  "verification_commands": ["python -m py_compile sample.py"]
}
"""


def forbidden_verify_goal() -> str:
    return """почини python приложение

CERAXIA_PATCH:
{
  "operations": [
    {
      "type": "replace",
      "path": "sample.py",
      "old": "return 1",
      "new": "return 2"
    }
  ],
  "verification_commands": ["bash -lc echo-nope"]
}
"""


def inferred_replace_goal() -> str:
    return """почини python приложение: в файле `sample.py` замени `return 1` на `return 2`.
Проверь `python -m py_compile sample.py`.
"""


def inferred_add_function_goal() -> str:
    return """почини python приложение: в файле `sample.py` добавь функцию `value`, возвращающую `42`.
Проверь `python -m unittest test_sample.py`.
"""


def test_inferred_missing_function_goal() -> str:
    return "почини тест `test_sample.py`."


def test_inferred_arithmetic_goal() -> str:
    return "почини тест `test_calc.py`."


def test_inferred_delegated_arithmetic_goal() -> str:
    return "кодовая задача: python тесты падают, источник ошибки не указан. Найди причину и исправь реализацию."


def partial_failure_goal() -> str:
    return """проверь что частично сломанный патч не оставляет мусор

CERAXIA_PATCH:
{
  "operations": [
    {
      "type": "write_file",
      "path": "created_before_failure.py",
      "content": "def value():\\n    return 1\\n"
    },
    {
      "type": "replace",
      "path": "missing.py",
      "old": "return 1",
      "new": "return 2"
    }
  ],
  "verification_commands": ["python -m py_compile created_before_failure.py"]
}
"""


def dirty_worktree_goal() -> str:
    return """кодовая задача: не перетирай пользовательские изменения

CERAXIA_PATCH:
{
  "operations": [
    {
      "type": "replace",
      "path": "settings.py",
      "old": "return 30",
      "new": "return 60"
    }
  ],
  "verification_commands": ["python -m py_compile settings.py"]
}
"""


def ambiguous_goal() -> str:
    return "кодовая задача: улучши обработку ошибок в python приложении, требования и формат ошибки не заданы. Если вариантов несколько, не угадывай."


def create_file_goal() -> str:
    return """создай python файл

CERAXIA_CREATE_FILE: generated.py
CERAXIA_FILE_CONTENT:
def generated_value():
    return 42

CERAXIA_VERIFY: python -m py_compile generated.py
"""


def multi_file_goal() -> str:
    return """создай несколько python файлов и проверь их вместе

CERAXIA_FILES:
{
  "files": [
    {
      "path": "calc.py",
      "content": "def add(left, right):\\n    return left + right\\n"
    },
    {
      "path": "test_calc.py",
      "content": "import unittest\\nfrom calc import add\\n\\nclass CalcTest(unittest.TestCase):\\n    def test_add(self):\\n        self.assertEqual(add(2, 3), 5)\\n\\nif __name__ == '__main__':\\n    unittest.main()\\n"
    }
  ],
  "verification_commands": ["python -m unittest test_calc.py"]
}
"""


def feature_goal() -> str:
    return """добавь multi-file feature с source, tests, docs, caller

CERAXIA_FEATURE:
{
  "module_path": "billing/discounts.py",
  "function_name": "apply_discount",
  "arguments": ["price", "percent"],
  "return_expression": "price - (price * percent / 100)",
  "test_path": "tests/test_discounts.py",
  "test_cases": [
    {"inputs": [200, 25], "expected": 150.0},
    {"inputs": [80, 10], "expected": 72.0}
  ],
  "docs_path": "docs/discounts.md",
  "docs_title": "Discount helpers",
  "caller_path": "billing/api.py",
  "caller_function": "discounted_total",
  "verification_commands": ["python -m unittest tests.test_discounts"]
}
"""


def config_runtime_goal() -> str:
    return """согласуй JSON config, Python loader и shell entrypoint

CERAXIA_CONFIG_RUNTIME:
{
  "config_path": "app/settings.json",
  "loader_path": "app/config_loader.py",
  "entrypoint_path": "bin/run-app.sh",
  "test_path": "tests/test_config_loader.py",
  "setting_key": "service_url",
  "env_var": "SERVICE_URL",
  "default_value": "http://localhost:8080",
  "verification_commands": ["python -m unittest tests.test_config_loader", "python -m py_compile app/config_loader.py"]
}
"""


def refactor_goal() -> str:
    return """вынеси дублированный расчет в общий helper, не меняя публичные функции

CERAXIA_REFACTOR:
{
  "helper_path": "common/calculations.py",
  "helper_function": "net_amount",
  "arguments": ["gross", "fee"],
  "return_expression": "gross - fee",
  "baseline_verification_commands": ["python -m unittest discover"],
  "replacements": [
    {
      "path": "orders.py",
      "public_function": "order_total",
      "old": "def order_total(gross, fee):\\n    return gross - fee\\n",
      "new": "from common.calculations import net_amount\\n\\n\\ndef order_total(gross, fee):\\n    return net_amount(gross, fee)\\n"
    },
    {
      "path": "refunds.py",
      "public_function": "refund_total",
      "old": "def refund_total(gross, fee):\\n    return gross - fee\\n",
      "new": "from common.calculations import net_amount\\n\\n\\ndef refund_total(gross, fee):\\n    return net_amount(gross, fee)\\n"
    }
  ],
  "verification_commands": ["python -m unittest discover", "python -m py_compile orders.py refunds.py common/calculations.py"]
}
"""


def edge_fix_goal() -> str:
    return """исправь функцию так, чтобы были проверены happy path и негативные edge cases

CERAXIA_EDGE_FIX:
{
  "source_path": "retry_policy.py",
  "function_name": "parse_retry_count",
  "arguments": ["raw"],
  "body_lines": [
    "value = int(raw)",
    "if value < 0 or value > 10:",
    "    raise ValueError('retry count must be between 0 and 10')",
    "return value"
  ],
  "test_path": "test_retry_policy.py",
  "positive_cases": [
    {"inputs": ["0"], "expected": 0},
    {"inputs": ["3"], "expected": 3},
    {"inputs": ["10"], "expected": 10}
  ],
  "negative_cases": [
    {"inputs": ["-1"], "exception": "ValueError"},
    {"inputs": ["11"], "exception": "ValueError"},
    {"inputs": ["bad"], "exception": "ValueError"}
  ],
  "verification_commands": ["python -m unittest test_retry_policy", "python -m py_compile retry_policy.py"]
}
"""


def repair_colon_goal() -> str:
    return """создай python файл и исправь если проверка найдет синтаксис

CERAXIA_CREATE_FILE: repair_me.py
CERAXIA_FILE_CONTENT:
def repaired_value()
    return 42

CERAXIA_VERIFY: python -m py_compile repair_me.py
"""


def repair_assertion_goal() -> str:
    return """создай python файл и исправь по unittest если тест покажет ожидаемое значение

CERAXIA_CREATE_FILE: sample.py
CERAXIA_FILE_CONTENT:
def value():
    return 1

CERAXIA_VERIFY: python -m unittest test_sample.py
"""


def repair_name_error_goal() -> str:
    return """создай python файл и исправь NameError если тест показывает ожидаемый literal

CERAXIA_CREATE_FILE: sample.py
CERAXIA_FILE_CONTENT:
def value():
    return answer

CERAXIA_VERIFY: python -m unittest test_sample.py
"""


def repair_import_error_goal() -> str:
    return """создай модуль и исправь missing import если тест показывает ожидаемый literal

CERAXIA_CREATE_FILE: sample.py
CERAXIA_FILE_CONTENT:

CERAXIA_VERIFY: python -m unittest test_sample.py
"""


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        final = run_pipeline(root)
        if final.get("status") != "blocked" or final.get("next_safe_action") != "handoff_to_patch_worker":
            raise AssertionError(f"final manifest should refuse code completion without source mutation: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("def value():\n    return 1\n", encoding="utf-8")
        (target_repo / "helper.py").write_text("from sample import value\n\ndef doubled():\n    return value() * 2\n", encoding="utf-8")
        (target_repo / "test_sample.py").write_text(
            "import unittest\nfrom sample import value\n\n"
            "class ValueTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(value(), 2)\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=explicit_patch_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready" or final.get("next_safe_action") != "inspect_final_package":
            raise AssertionError(f"final manifest should be ready after explicit patch verification: {final}")
        if sample.read_text(encoding="utf-8") != "def value():\n    return 2\n":
            raise AssertionError("explicit replace patch did not mutate the target file")
        changed = final.get("changed_files", [])
        if not changed or changed[0].get("path") != "sample.py" or not changed[0].get("changed"):
            raise AssertionError(f"final manifest should preserve changed file metadata: {final}")
        if final.get("verification_summary", {}).get("executed_count", 0) < 2:
            raise AssertionError(f"final manifest should preserve verification evidence: {final}")
        scope = final.get("patch_scope_evidence", {})
        if "sample.py" not in scope.get("changed_files_in_repo_map", []):
            raise AssertionError(f"final manifest should preserve patch scope evidence: {final}")
        scope_review = final.get("patch_scope_review", {})
        if (
            scope_review.get("status") != "covered"
            or scope_review.get("mapped_changed_file_count") != 1
            or scope_review.get("source_without_linked_tests")
        ):
            raise AssertionError(f"final manifest should preserve patch scope review: {final}")
        repair_state = final.get("repair_loop_state", {})
        if (
            repair_state.get("status") != "passed"
            or repair_state.get("next_action") != "continue_to_code_review"
            or repair_state.get("commands_executed_count", 0) < 2
        ):
            raise AssertionError(f"final manifest should preserve repair loop state: {final}")
        role_policies = final.get("role_policies", {})
        if (
            role_policies.get("implementation", {}).get("authority")
            != "scoped_source_mutation_from_patch_contract_or_safe_inference"
            or role_policies.get("verification", {}).get("authority") != "allowlisted_verification_and_narrow_repairs"
            or role_policies.get("finalize", {}).get("may_mutate_source") is not False
        ):
            raise AssertionError(f"final manifest should preserve role policy evidence: {final}")
        decision_record = final.get("review_decision_record", [])
        if (
            len(decision_record) < 4
            or not any(
                item.get("check") == "diagnostic_linkage" and item.get("status") == "pass"
                for item in decision_record
                if isinstance(item, dict)
            )
        ):
            raise AssertionError(f"final manifest should preserve review decision record: {final}")
        investigation = final.get("engineering_investigation", {})
        readiness = final.get("engineering_readiness", {})
        dependency_edges = investigation.get("dependency_graph", {}).get("edges", [])
        if not any(edge.get("from") == "helper.py" and edge.get("to") == "sample.py" for edge in dependency_edges):
            raise AssertionError(f"engineering investigation should include import dependency graph: {final}")
        targeted = investigation.get("targeted_reading_plan", [])
        if not any(item.get("path") == "sample.py" and item.get("dependent_count", 0) >= 1 for item in targeted):
            raise AssertionError(f"engineering investigation should include targeted reading with dependents: {final}")
        if not investigation.get("hypotheses"):
            raise AssertionError(f"engineering investigation should preserve hypothesis log: {final}")
        plan_text = (root / "work" / "code" / "change_plan.md").read_text(encoding="utf-8")
        if (
            "## Hypothesis Log" not in plan_text
            or "## Targeted Reading Plan" not in plan_text
            or "## File Impact Matrix" not in plan_text
            or "## Acceptance Criteria" not in plan_text
            or "## Test Strategy" not in plan_text
        ):
            raise AssertionError(f"change plan should include engineering investigation sections: {plan_text}")
        if (
            len(readiness.get("acceptance_criteria", [])) < 5
            or not readiness.get("test_strategy", {}).get("fallback_checks")
            or not readiness.get("impact_matrix")
            or final.get("engineering_readiness_review", {}).get("acceptance_criteria_count", 0) < 5
        ):
            raise AssertionError(f"final manifest should preserve engineering readiness model: {final}")
        if not any(item.get("check") == "readiness_model_present" and item.get("status") == "pass" for item in decision_record):
            raise AssertionError(f"review decision record should gate engineering readiness: {final}")
        patch_manifest = json.loads((root / "work" / "code" / "patch_manifest.json").read_text(encoding="utf-8"))
        candidates = patch_manifest.get("patch_candidates", [])
        if (
            not candidates
            or candidates[0].get("source") != "explicit_json_patch"
            or candidates[0].get("status") != "selected"
            or patch_manifest.get("selected_patch_candidate", {}).get("source") != "explicit_json_patch"
        ):
            raise AssertionError(f"implementation should preserve selected patch candidate journal: {patch_manifest}")
        source_excerpts = patch_manifest.get("source_excerpt_pack", [])
        if not any(item.get("path") == "sample.py" and item.get("status") == "read" for item in source_excerpts):
            raise AssertionError(f"implementation should read targeted source excerpts: {patch_manifest}")
        implementation_record = final.get("implementation_decision_record", [])
        if (
            not any(item.get("check") == "source_evidence_loaded" and item.get("status") == "pass" for item in implementation_record)
            or final.get("selected_patch_candidate", {}).get("source") != "explicit_json_patch"
            or final.get("execution_report", {}).get("patch_candidate_count", 0) < 1
            or final.get("execution_report", {}).get("source_excerpt_count", 0) < 1
        ):
            raise AssertionError(f"final manifest should preserve implementation decision evidence: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("def value():\n    return 1\n", encoding="utf-8")
        payload = request("implementation", "/work/code/patch_manifest.json", goal=explicit_patch_goal(), target_repo_root=target_repo)
        payload["quality_expectations"]["step_quality"]["role_policy"] = {
            "role": "read_only_test",
            "authority": "read_only_repository_mapping",
            "may_mutate_source": False,
        }
        result = run(payload, root / "work")
        if not result.get("ok"):
            raise AssertionError(f"read-only implementation policy should produce an auditable blocker: {result}")
        manifest = json.loads((root / "work" / "code" / "patch_manifest.json").read_text(encoding="utf-8"))
        if manifest.get("status") != "handoff_required" or "role_policy forbids source mutation for this step" not in manifest.get("blockers", []):
            raise AssertionError(f"read-only implementation policy should block mutation: {manifest}")
        if sample.read_text(encoding="utf-8") != "def value():\n    return 1\n":
            raise AssertionError("read-only implementation policy allowed source mutation")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        broken = target_repo / "repair_me.py"
        broken.write_text("def value()\n    return 42\n", encoding="utf-8")
        work = root / "work"
        (work / "code").mkdir(parents=True)
        (work / "code" / "patch_manifest.json").write_text(
            json.dumps(
                {
                    "status": "applied",
                    "changed_files": [{"path": "repair_me.py", "changed": True}],
                    "verification_commands": [],
                    "warnings": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        payload = request("verification", "/work/code/verification_report.json", target_repo_root=target_repo)
        payload["quality_expectations"]["step_quality"]["role_policy"] = {
            "role": "read_only_test",
            "authority": "read_only_verification_without_repair",
            "may_mutate_source": False,
        }
        result = run(payload, work)
        if not result.get("ok"):
            raise AssertionError(f"read-only verification policy should write a blocked report: {result}")
        report = json.loads((work / "code" / "verification_report.json").read_text(encoding="utf-8"))
        repair_state = json.loads((work / "code" / "repair_loop_state.json").read_text(encoding="utf-8"))
        if "role_policy forbids source mutation repair" not in report.get("blockers", []):
            raise AssertionError(f"read-only verification policy should block repair: {report}")
        if not repair_state.get("blocked_repairs") or repair_state.get("repairs_allowed") is not False:
            raise AssertionError(f"read-only verification policy should preserve blocked repair state: {repair_state}")
        if "repair_me.py" not in repair_state.get("candidate_source_paths", []):
            raise AssertionError(f"verification repair state should expose traceback source candidates: {repair_state}")
        if broken.read_text(encoding="utf-8") != "def value()\n    return 42\n":
            raise AssertionError("read-only verification policy allowed repair mutation")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "sample.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        work = root / "work"
        (work / "code").mkdir(parents=True)
        (work / "code" / "repo_survey.json").write_text(
            json.dumps({"repo_map": {"ranked_files": [{"path": "sample.py", "score": 10}, {"path": "test_sample.py", "score": 4}]}}) + "\n",
            encoding="utf-8",
        )
        (work / "code" / "patch_manifest.json").write_text(
            json.dumps(
                {
                    "status": "applied",
                    "changed_files": [{"path": "sample.py", "changed": True}],
                    "verification_commands": ["python -m py_compile missing_file.py"],
                    "warnings": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        payload = request("verification", "/work/code/verification_report.json", target_repo_root=target_repo)
        result = run(payload, work)
        if not result.get("ok"):
            raise AssertionError(f"verification with repo-map fallback should write a report: {result}")
        repair_state = json.loads((work / "code" / "repair_loop_state.json").read_text(encoding="utf-8"))
        if repair_state.get("candidate_source_paths") != ["sample.py"]:
            raise AssertionError(f"verification should fall back to repo-map source candidates: {repair_state}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "sample.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        work = root / "work"
        (work / "code").mkdir(parents=True)
        (work / "code" / "patch_manifest.json").write_text(
            json.dumps(
                {
                    "status": "applied",
                    "changed_files": [{"path": "unexpected.py", "changed": True}],
                    "patch_scope_evidence": {
                        "changed_files_in_repo_map": [],
                        "changed_files_outside_repo_map": ["unexpected.py"],
                        "evidence": [{"path": "unexpected.py", "in_repo_map": False}],
                    },
                    "verification_commands": [],
                    "warnings": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (work / "code" / "verification_report.json").write_text(
            json.dumps({"status": "passed", "blockers": [], "warnings": [], "executed": []}) + "\n",
            encoding="utf-8",
        )
        (work / "code" / "repair_loop_state.json").write_text(
            json.dumps({"status": "passed", "next_action": "continue_to_code_review"}) + "\n",
            encoding="utf-8",
        )
        payload = request("code_review", "/work/code/code_review.json", target_repo_root=target_repo)
        result = run(payload, work)
        if not result.get("ok"):
            raise AssertionError(f"scope-aware review should write a report: {result}")
        review = json.loads((work / "code" / "code_review.json").read_text(encoding="utf-8"))
        if review.get("patch_scope_review", {}).get("status") != "needs_attention":
            raise AssertionError(f"code review should flag unmapped changed files: {review}")
        warning_text = "\n".join(str(item.get("message", "")) for item in review.get("warnings", []) if isinstance(item, dict))
        if "unexpected.py" not in warning_text:
            raise AssertionError(f"code review should explain scope drift warning: {review}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("def value():\n    return 1\n", encoding="utf-8")
        final = run_pipeline(root / "work", goal=forbidden_verify_goal(), target_repo_root=target_repo)
        if final.get("status") != "blocked" or final.get("approved"):
            raise AssertionError(f"forbidden verification command should block final readiness: {final}")
        if final.get("verification_summary", {}).get("blocker_count", 0) < 1:
            raise AssertionError(f"blocked final manifest should preserve verification blockers: {final}")
        focused_context = final.get("revision_plan", {}).get("focused_context", {})
        if (
            focused_context.get("patch_source") != "explicit_json_patch"
            or "sample.py" not in focused_context.get("changed_files", [])
        ):
            raise AssertionError(f"blocked final manifest should preserve focused revision context: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("def value():\n    return 1\n", encoding="utf-8")
        (target_repo / "test_sample.py").write_text(
            "import unittest\nfrom sample import value\n\n"
            "class ValueTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(value(), 2)\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=inferred_replace_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready":
            raise AssertionError(f"inferred replace task should be ready: {final}")
        if final.get("patch_source") != "natural_language_simple_replace" or final.get("operation_count") != 1:
            raise AssertionError(f"inferred replace final manifest should expose patch audit fields: {final}")
        if sample.read_text(encoding="utf-8") != "def value():\n    return 2\n":
            raise AssertionError("inferred replace task did not mutate the target file")
        if final.get("verification_summary", {}).get("executed_count", 0) < 2:
            raise AssertionError(f"inferred replace final manifest should preserve verification evidence: {final}")
        source_test_links = final.get("patch_scope_evidence", {}).get("changed_sources_with_linked_tests", [])
        if not any(
            item.get("path") == "sample.py" and "test_sample.py" in item.get("tests", [])
            for item in source_test_links
            if isinstance(item, dict)
        ):
            raise AssertionError(f"final manifest should link changed source files to tests: {final}")
        if final.get("patch_scope_review", {}).get("status") != "covered":
            raise AssertionError(f"linked source/test patch should have covered scope review: {final}")
        survey = json.loads((root / "work" / "code" / "repo_survey.json").read_text(encoding="utf-8"))
        if survey.get("role_policy", {}).get("authority") != "read_only_repository_mapping":
            raise AssertionError(f"repository survey should preserve its read-only role policy: {survey}")
        symbol_paths = {item.get("path") for item in survey.get("python_symbols", []) if isinstance(item, dict)}
        if not {"sample.py", "test_sample.py"}.issubset(symbol_paths):
            raise AssertionError(f"repository survey should include Python symbol summaries: {survey}")
        if "python -m unittest discover" not in survey.get("suggested_verification_commands", []):
            raise AssertionError(f"repository survey should suggest Python unittest discovery: {survey}")
        repo_map = survey.get("repo_map", {})
        links = repo_map.get("test_source_links", [])
        if not any(
            item.get("test_path") == "test_sample.py" and "sample.py" in item.get("source_paths", [])
            for item in links
            if isinstance(item, dict)
        ):
            raise AssertionError(f"repository survey should link tests to imported source files: {survey}")
        ranked_paths = [item.get("path") for item in repo_map.get("ranked_files", []) if isinstance(item, dict)]
        if "sample.py" not in ranked_paths[:3]:
            raise AssertionError(f"repository survey should rank imported source files near the top: {survey}")
        if repo_map.get("recommended_read_order", [])[0].get("path") != "sample.py":
            raise AssertionError(f"repository survey should expose recommended read order: {survey}")
        plan_text = (root / "work" / "code" / "change_plan.md").read_text(encoding="utf-8")
        if (
            "## Python Symbol Surface" not in plan_text
            or "## Suggested Verification" not in plan_text
            or "## Ranked Repo Map" not in plan_text
            or "## Test Source Links" not in plan_text
            or "## Recommended Read Order" not in plan_text
        ):
            raise AssertionError(f"change plan should include repo-map, symbol, and verification sections: {plan_text}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("", encoding="utf-8")
        (target_repo / "test_sample.py").write_text(
            "import unittest\nfrom sample import value\n\n"
            "class ValueTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(value(), 42)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=inferred_add_function_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready":
            raise AssertionError(f"inferred add-function task should be ready: {final}")
        if final.get("patch_source") != "natural_language_add_function" or final.get("operation_count") != 1:
            raise AssertionError(f"inferred add-function final manifest should expose patch audit fields: {final}")
        if "def value():\n    return 42\n" not in sample.read_text(encoding="utf-8"):
            raise AssertionError("inferred add-function task did not append the target function")
        if final.get("verification_summary", {}).get("executed_count", 0) < 2:
            raise AssertionError(f"inferred add-function final manifest should preserve verification evidence: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("def value():\n    return 1\n", encoding="utf-8")
        final = run_pipeline(root / "work", goal=inferred_add_function_goal(), target_repo_root=target_repo)
        if final.get("status") != "blocked" or final.get("approved"):
            raise AssertionError(f"inferred add-function duplicate should block final readiness: {final}")
        if sample.read_text(encoding="utf-8").count("def value(") != 1:
            raise AssertionError("inferred add-function duplicate guard allowed duplicate function")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("", encoding="utf-8")
        (target_repo / "test_sample.py").write_text(
            "import unittest\nfrom sample import value\n\n"
            "class ValueTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(value(), 42)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=test_inferred_missing_function_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready":
            raise AssertionError(f"test-inferred missing function task should be ready: {final}")
        if final.get("patch_source") != "test_inferred_missing_function" or final.get("operation_count") != 1:
            raise AssertionError(f"test-inferred missing function should expose patch audit fields: {final}")
        if final.get("diagnostics", {}).get("function_name") != "value" or final.get("diagnostics", {}).get("test_path") != "test_sample.py":
            raise AssertionError(f"test-inferred missing function should expose diagnostics: {final}")
        if "def value():\n    return 42\n" not in sample.read_text(encoding="utf-8"):
            raise AssertionError("test-inferred missing function task did not append the target function")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("def value():\n    return 1\n", encoding="utf-8")
        (target_repo / "test_sample.py").write_text(
            "import unittest\nfrom sample import value\n\n"
            "class ValueTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(value(), 42)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=test_inferred_missing_function_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready":
            raise AssertionError(f"test-inferred return mismatch task should be ready: {final}")
        if final.get("patch_source") != "test_inferred_return_mismatch" or final.get("operation_count") != 1:
            raise AssertionError(f"test-inferred return mismatch should expose patch audit fields: {final}")
        if final.get("diagnostics", {}).get("actual") != "1" or final.get("diagnostics", {}).get("expected") != "42":
            raise AssertionError(f"test-inferred return mismatch should expose diagnostics: {final}")
        if final.get("recommended_read_order", [])[0].get("path") != "sample.py":
            raise AssertionError(f"test-inferred return mismatch should preserve recommended read order: {final}")
        if sample.read_text(encoding="utf-8") != "def value():\n    return 42\n":
            raise AssertionError("test-inferred return mismatch task did not update the return value")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        calc = target_repo / "calc.py"
        calc.write_text("def add(left, right):\n    return left - right\n", encoding="utf-8")
        (target_repo / "test_calc.py").write_text(
            "import unittest\nfrom calc import add\n\n"
            "class CalcTest(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(2, 3), 5)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=test_inferred_arithmetic_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready":
            raise AssertionError(f"test-inferred arithmetic task should be ready: {final}")
        if final.get("patch_source") != "test_inferred_arithmetic_return" or final.get("operation_count") != 1:
            raise AssertionError(f"test-inferred arithmetic should expose patch audit fields: {final}")
        if (
            final.get("selected_patch_candidate", {}).get("source") != "test_inferred_arithmetic_return"
            or final.get("execution_report", {}).get("patch_candidate_count", 0) < 5
        ):
            raise AssertionError(f"test-inferred arithmetic should preserve candidate resolution chain: {final}")
        if final.get("diagnostics", {}).get("replacement_expression") != "left + right":
            raise AssertionError(f"test-inferred arithmetic diagnostics should explain replacement: {final}")
        if "return left + right" not in calc.read_text(encoding="utf-8"):
            raise AssertionError("test-inferred arithmetic did not update the return expression")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        pricing = target_repo / "pricing.py"
        pricing.write_text("def discounted_price(price, percent):\n    return price - percent\n", encoding="utf-8")
        (target_repo / "checkout.py").write_text(
            "from pricing import discounted_price\n\n"
            "def total_after_discount(price, percent):\n"
            "    return discounted_price(price, percent)\n",
            encoding="utf-8",
        )
        (target_repo / "test_checkout.py").write_text(
            "import unittest\nfrom checkout import total_after_discount\n\n"
            "class CheckoutTest(unittest.TestCase):\n"
            "    def test_percentage_discount(self):\n"
            "        self.assertEqual(total_after_discount(200, 25), 150)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=test_inferred_delegated_arithmetic_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready" or final.get("patch_source") != "test_inferred_arithmetic_return":
            raise AssertionError(f"delegated arithmetic task should be ready: {final}")
        diagnostics = final.get("diagnostics", {})
        if (
            diagnostics.get("module_path") != "pricing.py"
            or diagnostics.get("delegated_from", {}).get("module_path") != "checkout.py"
            or diagnostics.get("replacement_expression") != "price - (price * percent / 100)"
        ):
            raise AssertionError(f"delegated arithmetic diagnostics should explain wrapper traversal: {final}")
        if "return price - (price * percent / 100)" not in pricing.read_text(encoding="utf-8"):
            raise AssertionError("delegated arithmetic did not update the implementation source")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        final = run_pipeline(root / "work", goal=partial_failure_goal(), target_repo_root=target_repo)
        if final.get("status") != "blocked" or final.get("approved"):
            raise AssertionError(f"partial patch failure should block final readiness: {final}")
        if (target_repo / "created_before_failure.py").exists():
            raise AssertionError("partial patch failure left a created file behind")
        patch_manifest = json.loads((root / "work" / "code" / "patch_manifest.json").read_text(encoding="utf-8"))
        rollback = patch_manifest.get("rollback", {})
        if not rollback.get("applied") or rollback.get("files", [{}])[0].get("path") != "created_before_failure.py":
            raise AssertionError(f"partial patch failure should record rollback evidence: {patch_manifest}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        settings = target_repo / "settings.py"
        settings.write_text("def timeout_seconds():\n    return 30\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=target_repo, check=True)
        subprocess.run(["git", "config", "user.email", "ceraxia-test@example.invalid"], cwd=target_repo, check=True)
        subprocess.run(["git", "config", "user.name", "Ceraxia Test"], cwd=target_repo, check=True)
        subprocess.run(["git", "add", "settings.py"], cwd=target_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=target_repo, check=True)
        settings.write_text(
            "def timeout_seconds():\n"
            "    # user local experiment, must not be overwritten\n"
            "    return 45\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=dirty_worktree_goal(), target_repo_root=target_repo)
        if final.get("status") != "blocked" or not any("uncommitted user changes" in item for item in final.get("blockers", [])):
            raise AssertionError(f"dirty worktree target should block mutation with safety evidence: {final}")
        if "return 45" not in settings.read_text(encoding="utf-8"):
            raise AssertionError("dirty worktree guard overwrote user changes")
        dirty = final.get("dirty_worktree", {})
        if not dirty.get("dirty_targets") or dirty.get("dirty_targets", [{}])[0].get("path") != "settings.py":
            raise AssertionError(f"dirty worktree evidence missing target path: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "parser.py").write_text("def parse_amount(raw):\n    return int(raw)\n", encoding="utf-8")
        (target_repo / "api.py").write_text(
            "from parser import parse_amount\n\n"
            "def handle_payload(payload):\n"
            "    return {'amount': parse_amount(payload['amount'])}\n",
            encoding="utf-8",
        )
        (target_repo / "test_api.py").write_text(
            "import unittest\nfrom api import handle_payload\n\n"
            "class ApiTest(unittest.TestCase):\n"
            "    def test_valid_amount(self):\n"
            "        self.assertEqual(handle_payload({'amount': '12'}), {'amount': 12})\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=ambiguous_goal(), target_repo_root=target_repo)
        ambiguity = final.get("ambiguity_analysis", {})
        if final.get("status") != "blocked" or not any("Ambiguous code task" in item for item in final.get("blockers", [])):
            raise AssertionError(f"ambiguous task should block with clarification request: {final}")
        if (
            ambiguity.get("status") != "ambiguous"
            or len(ambiguity.get("candidate_interpretations", [])) < 2
            or "expected behavior" not in ambiguity.get("safe_next_question", "")
        ):
            raise AssertionError(f"ambiguous task should preserve candidate interpretations: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        final = run_pipeline(root / "work", goal=create_file_goal(), target_repo_root=target_repo)
        generated = target_repo / "generated.py"
        if final.get("status") != "ready" or not generated.exists():
            raise AssertionError(f"marker-synthesized create file task should be ready: {final}")
        if "return 42" not in generated.read_text(encoding="utf-8"):
            raise AssertionError("marker-synthesized create file task wrote wrong content")
        if final.get("verification_summary", {}).get("executed_count", 0) < 2:
            raise AssertionError(f"marker final manifest should preserve verification evidence: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        final = run_pipeline(root / "work", goal=multi_file_goal(), target_repo_root=target_repo)
        calc = target_repo / "calc.py"
        test_calc = target_repo / "test_calc.py"
        if final.get("status") != "ready" or not calc.exists() or not test_calc.exists():
            raise AssertionError(f"multi-file marker task should be ready: {final}")
        changed_paths = {item.get("path") for item in final.get("changed_files", []) if isinstance(item, dict)}
        if changed_paths != {"calc.py", "test_calc.py"}:
            raise AssertionError(f"multi-file marker should preserve both changed files: {final}")
        if final.get("verification_summary", {}).get("executed_count", 0) < 2:
            raise AssertionError(f"multi-file final manifest should preserve verification evidence: {final}")
        repeated = run_pipeline(root / "work_repeat", goal=multi_file_goal(), target_repo_root=target_repo)
        if repeated.get("status") != "ready":
            raise AssertionError(f"repeated multi-file marker task should remain ready: {repeated}")
        repeated_files = repeated.get("changed_files", [])
        if not repeated_files or not all(item.get("idempotent") for item in repeated_files if isinstance(item, dict)):
            raise AssertionError(f"repeated multi-file marker should report idempotent writes: {repeated}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        final = run_pipeline(root / "work", goal=feature_goal(), target_repo_root=target_repo)
        expected_paths = {"billing/discounts.py", "tests/test_discounts.py", "docs/discounts.md", "billing/api.py"}
        changed_paths = {item.get("path") for item in final.get("changed_files", []) if isinstance(item, dict)}
        if final.get("status") != "ready" or final.get("patch_source") != "feature_marker_synthesis":
            raise AssertionError(f"feature marker task should be ready: {final}")
        if changed_paths != expected_paths:
            raise AssertionError(f"feature marker should write source/test/docs/caller: {final}")
        if "def apply_discount" not in (target_repo / "billing" / "discounts.py").read_text(encoding="utf-8"):
            raise AssertionError("feature marker did not write source function")
        if "Discount helpers" not in (target_repo / "docs" / "discounts.md").read_text(encoding="utf-8"):
            raise AssertionError("feature marker did not write docs")
        if final.get("verification_summary", {}).get("executed_count", 0) < 2:
            raise AssertionError(f"feature marker final manifest should preserve verification evidence: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        final = run_pipeline(root / "work", goal=config_runtime_goal(), target_repo_root=target_repo)
        expected_paths = {"app/settings.json", "app/config_loader.py", "bin/run-app.sh", "tests/test_config_loader.py"}
        changed_paths = {item.get("path") for item in final.get("changed_files", []) if isinstance(item, dict)}
        if final.get("status") != "ready" or final.get("patch_source") != "config_runtime_marker_synthesis":
            raise AssertionError(f"config/runtime marker task should be ready: {final}")
        if changed_paths != expected_paths:
            raise AssertionError(f"config/runtime marker should write config/loader/entrypoint/test: {final}")
        if "SERVICE_URL" not in (target_repo / "bin" / "run-app.sh").read_text(encoding="utf-8"):
            raise AssertionError("config/runtime marker did not write entrypoint env var")
        if '"service_url"' not in (target_repo / "app" / "settings.json").read_text(encoding="utf-8"):
            raise AssertionError("config/runtime marker did not write JSON config")
        if final.get("verification_summary", {}).get("executed_count", 0) < 3:
            raise AssertionError(f"config/runtime final manifest should preserve verification evidence: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "orders.py").write_text(
            "def order_total(gross, fee):\n"
            "    return gross - fee\n",
            encoding="utf-8",
        )
        (target_repo / "refunds.py").write_text(
            "def refund_total(gross, fee):\n"
            "    return gross - fee\n",
            encoding="utf-8",
        )
        (target_repo / "test_totals.py").write_text(
            "import unittest\nfrom orders import order_total\nfrom refunds import refund_total\n\n"
            "class TotalsTest(unittest.TestCase):\n"
            "    def test_order_total(self):\n"
            "        self.assertEqual(order_total(100, 15), 85)\n\n"
            "    def test_refund_total(self):\n"
            "        self.assertEqual(refund_total(80, 5), 75)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=refactor_goal(), target_repo_root=target_repo)
        expected_paths = {"common/calculations.py", "orders.py", "refunds.py"}
        changed_paths = {item.get("path") for item in final.get("changed_files", []) if isinstance(item, dict)}
        if final.get("status") != "ready" or final.get("patch_source") != "refactor_marker_synthesis":
            raise AssertionError(f"refactor marker task should be ready: {final}")
        if changed_paths != expected_paths:
            raise AssertionError(f"refactor marker should touch helper and duplicate modules only: {final}")
        if "def net_amount" not in (target_repo / "common" / "calculations.py").read_text(encoding="utf-8"):
            raise AssertionError("refactor marker did not write helper function")
        if "order_total" not in final.get("diagnostics", {}).get("public_functions", []):
            raise AssertionError(f"refactor marker should preserve public function evidence: {final}")
        if final.get("verification_summary", {}).get("executed_count", 0) < 3:
            raise AssertionError(f"refactor final manifest should preserve verification evidence: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "retry_policy.py").write_text(
            "def parse_retry_count(raw):\n"
            "    return int(raw)\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=edge_fix_goal(), target_repo_root=target_repo)
        expected_paths = {"retry_policy.py", "test_retry_policy.py"}
        changed_paths = {item.get("path") for item in final.get("changed_files", []) if isinstance(item, dict)}
        diagnostics = final.get("diagnostics", {})
        if final.get("status") != "ready" or final.get("patch_source") != "edge_fix_marker_synthesis":
            raise AssertionError(f"edge-fix marker task should be ready: {final}")
        if changed_paths != expected_paths:
            raise AssertionError(f"edge-fix marker should touch source and test only: {final}")
        if diagnostics.get("positive_case_count") != 3 or diagnostics.get("negative_case_count") != 3:
            raise AssertionError(f"edge-fix marker should preserve positive/negative evidence: {final}")
        if "assertRaises" not in (target_repo / "test_retry_policy.py").read_text(encoding="utf-8"):
            raise AssertionError("edge-fix marker did not write negative tests")
        if final.get("verification_summary", {}).get("executed_count", 0) < 3:
            raise AssertionError(f"edge-fix final manifest should preserve verification evidence: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        final = run_pipeline(root / "work", goal=repair_colon_goal(), target_repo_root=target_repo)
        repaired = target_repo / "repair_me.py"
        if final.get("status") != "ready" or final.get("verification_summary", {}).get("repair_count") != 1:
            raise AssertionError(f"expected-colon repair should produce ready final manifest: {final}")
        if "def repaired_value():\n" not in repaired.read_text(encoding="utf-8"):
            raise AssertionError("expected-colon repair did not update the source file")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "test_sample.py").write_text(
            "import unittest\nfrom sample import value\n\n"
            "class ValueTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(value(), 2)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=repair_assertion_goal(), target_repo_root=target_repo)
        sample = target_repo / "sample.py"
        if final.get("status") != "ready" or final.get("verification_summary", {}).get("repair_count") != 1:
            raise AssertionError(f"assertion repair should produce ready final manifest: {final}")
        if "return 2" not in sample.read_text(encoding="utf-8"):
            raise AssertionError("assertion repair did not update the return value")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "test_sample.py").write_text(
            "import unittest\nfrom sample import value\n\n"
            "class ValueTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(value(), 42)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=repair_name_error_goal(), target_repo_root=target_repo)
        sample = target_repo / "sample.py"
        if final.get("status") != "ready" or final.get("verification_summary", {}).get("repair_count") != 1:
            raise AssertionError(f"NameError repair should produce ready final manifest: {final}")
        if "return 42" not in sample.read_text(encoding="utf-8"):
            raise AssertionError("NameError repair did not update the undefined return")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        (target_repo / "test_sample.py").write_text(
            "import unittest\nfrom sample import value\n\n"
            "class ValueTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(value(), 42)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        final = run_pipeline(root / "work", goal=repair_import_error_goal(), target_repo_root=target_repo)
        sample = target_repo / "sample.py"
        if final.get("status") != "ready" or final.get("verification_summary", {}).get("repair_count") != 1:
            raise AssertionError(f"ImportError repair should produce ready final manifest: {final}")
        if "def value():\n    return 42\n" not in sample.read_text(encoding="utf-8"):
            raise AssertionError("ImportError repair did not add the missing function")
    print("[ok] CogitatorCodewright code artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
