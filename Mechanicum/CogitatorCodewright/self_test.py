#!/usr/bin/env python3
from __future__ import annotations

import json
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
        role_policies = final.get("role_policies", {})
        if (
            role_policies.get("implementation", {}).get("authority")
            != "scoped_source_mutation_from_patch_contract_or_safe_inference"
            or role_policies.get("verification", {}).get("authority") != "allowlisted_verification_and_narrow_repairs"
            or role_policies.get("finalize", {}).get("may_mutate_source") is not False
        ):
            raise AssertionError(f"final manifest should preserve role policy evidence: {final}")
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
        if "role_policy forbids source mutation repair" not in report.get("blockers", []):
            raise AssertionError(f"read-only verification policy should block repair: {report}")
        if broken.read_text(encoding="utf-8") != "def value()\n    return 42\n":
            raise AssertionError("read-only verification policy allowed repair mutation")
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
        survey = json.loads((root / "work" / "code" / "repo_survey.json").read_text(encoding="utf-8"))
        if survey.get("role_policy", {}).get("authority") != "read_only_repository_mapping":
            raise AssertionError(f"repository survey should preserve its read-only role policy: {survey}")
        symbol_paths = {item.get("path") for item in survey.get("python_symbols", []) if isinstance(item, dict)}
        if not {"sample.py", "test_sample.py"}.issubset(symbol_paths):
            raise AssertionError(f"repository survey should include Python symbol summaries: {survey}")
        if "python -m unittest discover" not in survey.get("suggested_verification_commands", []):
            raise AssertionError(f"repository survey should suggest Python unittest discovery: {survey}")
        plan_text = (root / "work" / "code" / "change_plan.md").read_text(encoding="utf-8")
        if "## Python Symbol Surface" not in plan_text or "## Suggested Verification" not in plan_text:
            raise AssertionError(f"change plan should include symbol and verification sections: {plan_text}")
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
        if sample.read_text(encoding="utf-8") != "def value():\n    return 42\n":
            raise AssertionError("test-inferred return mismatch task did not update the return value")
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
