#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from eye_of_terror.warmaster_gateway import prepare_task, research_loop_run


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        target_repo = temp_root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("def value():\n    return 1\n", encoding="utf-8")
        task = f"""почини python приложение
CERAXIA_TARGET_REPO: {target_repo}
CERAXIA_PATCH:
{{
  "operations": [
    {{"type": "replace", "path": "sample.py", "old": "return 1", "new": "return 2"}}
  ],
  "verification_commands": ["python -m py_compile sample.py"]
}}
"""
        run_root = temp_root / "runs"
        task_id = "ceraxia-explicit-patch-pipeline"
        prepared = prepare_task(task, task_id, run_root, governor_transport="local")
        if not prepared.get("ok") or prepared.get("governor") != "Ceraxia":
            raise AssertionError(f"Ceraxia patch task did not prepare correctly: {prepared}")
        workers = [step.get("worker") for step in prepared.get("status", {}).get("steps", [])]
        if workers != ["LogisRepository", "MagosStrategos", "FerrumPatchwright", "OrdinatusVerifier", "JudicatorCodicis", "SealwrightFinalis"]:
            raise AssertionError(f"Ceraxia pipeline workers drifted: {workers}")
        result = research_loop_run(run_root, task_id, run_mode="local", timeout_sec=120, max_revision_cycles=1)
        if not result.get("ok") or result.get("phase") != "completed":
            raise AssertionError(f"Ceraxia patch pipeline did not complete: {result}")
        manifests = list((run_root / task_id / "work").rglob("final_manifest.json"))
        if len(manifests) != 1:
            raise AssertionError(f"expected one final manifest, found {manifests}")
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        if manifest.get("status") != "ready" or manifest.get("next_safe_action") != "inspect_final_package":
            raise AssertionError(f"Ceraxia patch final manifest should be ready: {manifest}")
        if sample.read_text(encoding="utf-8") != "def value():\n    return 2\n":
            raise AssertionError("Ceraxia patch pipeline did not mutate the target file")
        changed = manifest.get("changed_files", [])
        if not changed or changed[0].get("path") != "sample.py" or not changed[0].get("changed"):
            raise AssertionError(f"Ceraxia final manifest lacks changed file evidence: {manifest}")
        if manifest.get("verification_summary", {}).get("executed_count", 0) < 2:
            raise AssertionError(f"Ceraxia final manifest lacks verification evidence: {manifest}")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        target_repo = temp_root / "repo"
        target_repo.mkdir()
        task = f"""создай python файл
CERAXIA_TARGET_REPO: {target_repo}
CERAXIA_CREATE_FILE: generated.py
CERAXIA_FILE_CONTENT:
def generated_value():
    return 42

CERAXIA_VERIFY: python -m py_compile generated.py
"""
        run_root = temp_root / "runs"
        task_id = "ceraxia-marker-create-pipeline"
        prepared = prepare_task(task, task_id, run_root, governor_transport="local")
        if not prepared.get("ok") or prepared.get("governor") != "Ceraxia":
            raise AssertionError(f"Ceraxia marker task did not prepare correctly: {prepared}")
        result = research_loop_run(run_root, task_id, run_mode="local", timeout_sec=120, max_revision_cycles=1)
        if not result.get("ok") or result.get("phase") != "completed":
            raise AssertionError(f"Ceraxia marker pipeline did not complete: {result}")
        manifests = list((run_root / task_id / "work").rglob("final_manifest.json"))
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        generated = target_repo / "generated.py"
        if manifest.get("status") != "ready" or not generated.exists():
            raise AssertionError(f"Ceraxia marker final manifest should be ready: {manifest}")
        if "return 42" not in generated.read_text(encoding="utf-8"):
            raise AssertionError("Ceraxia marker pipeline wrote wrong file content")
        if manifest.get("verification_summary", {}).get("executed_count", 0) < 2:
            raise AssertionError(f"Ceraxia marker final manifest lacks verification evidence: {manifest}")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        target_repo = temp_root / "repo"
        target_repo.mkdir()
        task = f"""создай несколько python файлов и проверь их вместе
CERAXIA_TARGET_REPO: {target_repo}
CERAXIA_FILES:
{{
  "files": [
    {{
      "path": "calc.py",
      "content": "def add(left, right):\\n    return left + right\\n"
    }},
    {{
      "path": "test_calc.py",
      "content": "import unittest\\nfrom calc import add\\n\\nclass CalcTest(unittest.TestCase):\\n    def test_add(self):\\n        self.assertEqual(add(2, 3), 5)\\n\\nif __name__ == '__main__':\\n    unittest.main()\\n"
    }}
  ],
  "verification_commands": ["python -m unittest test_calc.py"]
}}
"""
        run_root = temp_root / "runs"
        task_id = "ceraxia-multi-file-marker-pipeline"
        prepared = prepare_task(task, task_id, run_root, governor_transport="local")
        if not prepared.get("ok") or prepared.get("governor") != "Ceraxia":
            raise AssertionError(f"Ceraxia multi-file task did not prepare correctly: {prepared}")
        result = research_loop_run(run_root, task_id, run_mode="local", timeout_sec=120, max_revision_cycles=1)
        if not result.get("ok") or result.get("phase") != "completed":
            raise AssertionError(f"Ceraxia multi-file pipeline did not complete: {result}")
        manifest_path = next((run_root / task_id / "work").rglob("final_manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        changed_paths = {item.get("path") for item in manifest.get("changed_files", []) if isinstance(item, dict)}
        if manifest.get("status") != "ready" or changed_paths != {"calc.py", "test_calc.py"}:
            raise AssertionError(f"Ceraxia multi-file final manifest should be ready with both files: {manifest}")
        if not (target_repo / "calc.py").exists() or not (target_repo / "test_calc.py").exists():
            raise AssertionError("Ceraxia multi-file pipeline did not write both target files")
        if manifest.get("verification_summary", {}).get("executed_count", 0) < 2:
            raise AssertionError(f"Ceraxia multi-file manifest lacks verification evidence: {manifest}")
        repeated_task_id = "ceraxia-multi-file-marker-pipeline-repeat"
        repeated_prepared = prepare_task(task, repeated_task_id, run_root, governor_transport="local")
        if not repeated_prepared.get("ok") or repeated_prepared.get("governor") != "Ceraxia":
            raise AssertionError(f"Ceraxia repeated multi-file task did not prepare correctly: {repeated_prepared}")
        repeated_result = research_loop_run(run_root, repeated_task_id, run_mode="local", timeout_sec=120, max_revision_cycles=1)
        if not repeated_result.get("ok") or repeated_result.get("phase") != "completed":
            raise AssertionError(f"Ceraxia repeated multi-file pipeline did not complete: {repeated_result}")
        repeated_manifest_path = next((run_root / repeated_task_id / "work").rglob("final_manifest.json"))
        repeated_manifest = json.loads(repeated_manifest_path.read_text(encoding="utf-8"))
        repeated_files = repeated_manifest.get("changed_files", [])
        if repeated_manifest.get("status") != "ready" or not repeated_files:
            raise AssertionError(f"Ceraxia repeated multi-file manifest should remain ready: {repeated_manifest}")
        if not all(item.get("idempotent") for item in repeated_files if isinstance(item, dict)):
            raise AssertionError(f"Ceraxia repeated multi-file manifest should report idempotent writes: {repeated_manifest}")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        target_repo = temp_root / "repo"
        target_repo.mkdir()
        task = f"""создай python файл и исправь если py_compile найдет простую синтаксическую ошибку
CERAXIA_TARGET_REPO: {target_repo}
CERAXIA_CREATE_FILE: repair_me.py
CERAXIA_FILE_CONTENT:
def repaired_value()
    return 42

CERAXIA_VERIFY: python -m py_compile repair_me.py
"""
        run_root = temp_root / "runs"
        task_id = "ceraxia-repair-pipeline"
        prepared = prepare_task(task, task_id, run_root, governor_transport="local")
        if not prepared.get("ok") or prepared.get("governor") != "Ceraxia":
            raise AssertionError(f"Ceraxia repair task did not prepare correctly: {prepared}")
        result = research_loop_run(run_root, task_id, run_mode="local", timeout_sec=120, max_revision_cycles=1)
        if not result.get("ok") or result.get("phase") != "completed":
            raise AssertionError(f"Ceraxia repair pipeline did not complete: {result}")
        manifest_path = next((run_root / task_id / "work").rglob("final_manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        repaired = target_repo / "repair_me.py"
        if manifest.get("status") != "ready" or manifest.get("verification_summary", {}).get("repair_count") != 1:
            raise AssertionError(f"Ceraxia repair final manifest should be ready with repair evidence: {manifest}")
        if "def repaired_value():\n" not in repaired.read_text(encoding="utf-8"):
            raise AssertionError("Ceraxia repair pipeline did not update the target file")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        target_repo = temp_root / "repo"
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
        task = f"""создай python файл и исправь по unittest если тест покажет ожидаемое значение
CERAXIA_TARGET_REPO: {target_repo}
CERAXIA_CREATE_FILE: sample.py
CERAXIA_FILE_CONTENT:
def value():
    return 1

CERAXIA_VERIFY: python -m unittest test_sample.py
"""
        run_root = temp_root / "runs"
        task_id = "ceraxia-assertion-repair-pipeline"
        prepared = prepare_task(task, task_id, run_root, governor_transport="local")
        if not prepared.get("ok") or prepared.get("governor") != "Ceraxia":
            raise AssertionError(f"Ceraxia assertion repair task did not prepare correctly: {prepared}")
        result = research_loop_run(run_root, task_id, run_mode="local", timeout_sec=120, max_revision_cycles=1)
        if not result.get("ok") or result.get("phase") != "completed":
            raise AssertionError(f"Ceraxia assertion repair pipeline did not complete: {result}")
        manifest_path = next((run_root / task_id / "work").rglob("final_manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sample = target_repo / "sample.py"
        if manifest.get("status") != "ready" or manifest.get("verification_summary", {}).get("repair_count") != 1:
            raise AssertionError(f"Ceraxia assertion repair manifest should be ready with repair evidence: {manifest}")
        if "return 2" not in sample.read_text(encoding="utf-8"):
            raise AssertionError("Ceraxia assertion repair pipeline did not update the target file")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        target_repo = temp_root / "repo"
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
        task = f"""создай python файл и исправь NameError если тест показывает ожидаемый literal
CERAXIA_TARGET_REPO: {target_repo}
CERAXIA_CREATE_FILE: sample.py
CERAXIA_FILE_CONTENT:
def value():
    return answer

CERAXIA_VERIFY: python -m unittest test_sample.py
"""
        run_root = temp_root / "runs"
        task_id = "ceraxia-name-error-repair-pipeline"
        prepared = prepare_task(task, task_id, run_root, governor_transport="local")
        if not prepared.get("ok") or prepared.get("governor") != "Ceraxia":
            raise AssertionError(f"Ceraxia NameError repair task did not prepare correctly: {prepared}")
        result = research_loop_run(run_root, task_id, run_mode="local", timeout_sec=120, max_revision_cycles=1)
        if not result.get("ok") or result.get("phase") != "completed":
            raise AssertionError(f"Ceraxia NameError repair pipeline did not complete: {result}")
        manifest_path = next((run_root / task_id / "work").rglob("final_manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sample = target_repo / "sample.py"
        if manifest.get("status") != "ready" or manifest.get("verification_summary", {}).get("repair_count") != 1:
            raise AssertionError(f"Ceraxia NameError repair manifest should be ready with repair evidence: {manifest}")
        if "return 42" not in sample.read_text(encoding="utf-8"):
            raise AssertionError("Ceraxia NameError repair pipeline did not update the target file")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        target_repo = temp_root / "repo"
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
        task = f"""создай модуль и исправь missing import если тест показывает ожидаемый literal
CERAXIA_TARGET_REPO: {target_repo}
CERAXIA_CREATE_FILE: sample.py
CERAXIA_FILE_CONTENT:

CERAXIA_VERIFY: python -m unittest test_sample.py
"""
        run_root = temp_root / "runs"
        task_id = "ceraxia-import-error-repair-pipeline"
        prepared = prepare_task(task, task_id, run_root, governor_transport="local")
        if not prepared.get("ok") or prepared.get("governor") != "Ceraxia":
            raise AssertionError(f"Ceraxia ImportError repair task did not prepare correctly: {prepared}")
        result = research_loop_run(run_root, task_id, run_mode="local", timeout_sec=120, max_revision_cycles=1)
        if not result.get("ok") or result.get("phase") != "completed":
            raise AssertionError(f"Ceraxia ImportError repair pipeline did not complete: {result}")
        manifest_path = next((run_root / task_id / "work").rglob("final_manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sample = target_repo / "sample.py"
        if manifest.get("status") != "ready" or manifest.get("verification_summary", {}).get("repair_count") != 1:
            raise AssertionError(f"Ceraxia ImportError repair manifest should be ready with repair evidence: {manifest}")
        if "def value():\n    return 42\n" not in sample.read_text(encoding="utf-8"):
            raise AssertionError("Ceraxia ImportError repair pipeline did not add the missing function")
    print("[ok] Ceraxia explicit patch pipeline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
