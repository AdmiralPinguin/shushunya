#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from ceraxia import CeraxiaInput, run_ceraxia


def write_repo(repo: Path, files: dict[str, str]) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    for rel_path, content in files.items():
        path = repo / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def require(condition: bool, message: str, payload: Any = None) -> None:
    if not condition:
        suffix = f": {payload}" if payload is not None else ""
        raise AssertionError(message + suffix)


def load_run_artifacts(run_dir: Path) -> dict[str, Any]:
    return {
        "brief": json.loads((run_dir / "implementation_brief.json").read_text(encoding="utf-8")),
        "worker": json.loads((run_dir / "worker_report.json").read_text(encoding="utf-8")),
        "summary": json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8")),
        "readiness": json.loads((run_dir / "execution_readiness.json").read_text(encoding="utf-8")),
        "review": json.loads((run_dir / "review_gate.json").read_text(encoding="utf-8")),
        "audit": json.loads((run_dir / "run_audit.json").read_text(encoding="utf-8")),
    }


def run_dry_security_trial(root: Path) -> dict[str, Any]:
    repo = root / "dry-security-repo"
    write_repo(
        repo,
        {
            "archive_paths.py": "from pathlib import PurePosixPath\n\ndef normalize(path):\n    return str(PurePosixPath(path))\n",
            "tests/test_archive_paths.py": "from archive_paths import normalize\n\ndef test_normalize():\n    assert normalize('a/b') == 'a/b'\n",
        },
    )
    result = run_ceraxia(
        CeraxiaInput(
            task="почини security path traversal в `archive_paths.py`, сохрани API compatibility и добавь negative tests",
            repo_path=str(repo),
            runs_root=root / "runs",
            dry_run=True,
        )
    )
    artifacts = load_run_artifacts(Path(result["run_dir"]))
    require(result["ok"], "dry security planning handoff should produce an auditable package", result)
    require(not result["ready_for_execution"], "dry security handoff must not claim real execution readiness", result)
    require(artifacts["worker"]["execution_intent"]["mode"] == "planning_handoff_only", "dry security worker must expose planning-only intent", artifacts["worker"])
    require(artifacts["summary"]["code_brigade_execution_intent_mode"] == "planning_handoff_only", "summary must preserve planning-only intent", artifacts["summary"])
    require(artifacts["summary"]["maturity"] == "dry_run_controller_with_code_brigade_handoff_adapter", "dry handoff maturity should remain honest", artifacts["summary"])
    require(artifacts["brief"]["risk_level"] == "high", "security trial should be high risk", artifacts["brief"])
    require(len(artifacts["brief"]["implementation_work_packages"]["packages"]) >= 5, "security trial should create cross-surface work packages", artifacts["brief"])
    require(artifacts["brief"]["constraint_trace_matrix"]["complete"], "dry security brief must preserve constraint trace", artifacts["brief"])
    require(artifacts["worker"]["implementation_plan"]["constraint_trace_complete"], "dry security worker plan must preserve constraint trace", artifacts["worker"])
    require(artifacts["summary"]["constraint_trace_status"] == "complete", "dry security summary must expose constraint trace status", artifacts["summary"])
    require(artifacts["review"]["constraint_trace_sufficiency"]["status"] == "complete", "dry security review must audit constraint trace", artifacts["review"])
    require(artifacts["audit"]["decision"] == "passed", "dry security run package audit should pass", artifacts["audit"])
    return {"id": "dry-security-planning-handoff", "result": result, "intent": artifacts["worker"]["execution_intent"]}


def run_explicit_patch_trial(root: Path) -> dict[str, Any]:
    repo = root / "explicit-patch-repo"
    write_repo(repo, {"app.py": "def enabled():\n    return False\n"})
    patch = {
        "operations": [
            {
                "type": "replace_return_expression",
                "path": "app.py",
                "function_name": "enabled",
                "old_expression": "False",
                "new_expression": "True",
            }
        ]
    }
    result = run_ceraxia(
        CeraxiaInput(
            task="почини bug в `app.py`\nCERAXIA_PATCH:\n" + json.dumps(patch),
            repo_path=str(repo),
            runs_root=root / "runs",
            dry_run=False,
            execute_verification=True,
            verification_commands=("python -m py_compile app.py",),
        )
    )
    artifacts = load_run_artifacts(Path(result["run_dir"]))
    require(result["ok"], "explicit patch handoff should complete", result)
    require(result["ready_for_execution"], "explicit patch handoff should be ready after real adapter execution", result)
    require("return True" in (repo / "app.py").read_text(encoding="utf-8"), "explicit patch should mutate app.py")
    require(artifacts["worker"]["execution_intent"]["mode"] == "explicit_patch_execution", "worker must expose explicit patch intent", artifacts["worker"])
    require(artifacts["summary"]["code_brigade_execution_real_supported"] is True, "summary must preserve executable intent", artifacts["summary"])
    require(artifacts["summary"]["maturity"] == "explicit_patch_execution_controller", "explicit patch maturity should show real adapter execution", artifacts["summary"])
    require(artifacts["review"]["decision"] == "ready", "explicit patch review should be ready", artifacts["review"])
    require(artifacts["review"]["constraint_trace_sufficiency"]["status"] == "complete", "explicit patch review must audit constraint trace", artifacts["review"])
    return {"id": "explicit-patch-execution", "result": result, "intent": artifacts["worker"]["execution_intent"]}


def run_guarded_inferred_patch_trial(root: Path) -> dict[str, Any]:
    repo = root / "guarded-inferred-patch-repo"
    write_repo(repo, {"app.py": "def enabled():\n    return False\n"})
    result = run_ceraxia(
        CeraxiaInput(
            task="В файле `app.py` замени `return False` на `return True`.",
            repo_path=str(repo),
            runs_root=root / "runs",
            dry_run=False,
            execute_verification=True,
            verification_commands=("python -m py_compile app.py",),
        )
    )
    artifacts = load_run_artifacts(Path(result["run_dir"]))
    require(result["ok"], "guarded inferred patch handoff should complete", result)
    require(result["ready_for_execution"], "guarded inferred patch handoff should be ready after real adapter execution", result)
    require("return True" in (repo / "app.py").read_text(encoding="utf-8"), "guarded inferred patch should mutate app.py")
    require(artifacts["worker"]["execution_intent"]["mode"] == "guarded_inferred_patch_execution", "worker must expose guarded inferred intent", artifacts["worker"])
    require(artifacts["worker"]["autonomous_execution_request"]["status"] == "not_required", "guarded inferred execution should not request autonomous adapter", artifacts["worker"])
    require(artifacts["summary"]["maturity"] == "guarded_inferred_patch_execution_controller", "guarded inferred maturity should show real adapter execution", artifacts["summary"])
    require("natural_language_simple_replace" in artifacts["worker"]["execution_result"]["patch_summary"], "worker result should expose inferred patch source", artifacts["worker"])
    return {"id": "guarded-inferred-patch-execution", "result": result, "intent": artifacts["worker"]["execution_intent"]}


def run_guarded_inferred_create_file_trial(root: Path) -> dict[str, Any]:
    repo = root / "guarded-inferred-create-file-repo"
    write_repo(
        repo,
        {
            "test_helpers.py": (
                "import unittest\nfrom helpers import helper\n\n"
                "class HelperTest(unittest.TestCase):\n"
                "    def test_helper(self):\n"
                "        self.assertTrue(helper())\n"
            )
        },
    )
    result = run_ceraxia(
        CeraxiaInput(
            task="Создай файл `helpers.py` с содержимым `def helper():\n    return True\n`.",
            repo_path=str(repo),
            runs_root=root / "runs",
            dry_run=False,
            execute_verification=True,
            verification_commands=("python -m py_compile helpers.py", "python -m unittest test_helpers.py"),
        )
    )
    artifacts = load_run_artifacts(Path(result["run_dir"]))
    require(result["ok"], "guarded inferred create-file handoff should complete", result)
    require(result["ready_for_execution"], "guarded inferred create-file handoff should be ready after real adapter execution", result)
    require("def helper" in (repo / "helpers.py").read_text(encoding="utf-8"), "guarded inferred create-file should create helpers.py")
    require(artifacts["brief"]["repo_survey_evidence"]["candidate_files"] == [], "create-file trial should prove candidate-less source creation is supported", artifacts["brief"])
    require(artifacts["brief"]["survey_quality_gate"]["allowed_missing_create_path_hints"] == ["helpers.py"], "create-file path should be an allowed missing hint", artifacts["brief"])
    require(artifacts["worker"]["execution_intent"]["mode"] == "guarded_inferred_patch_execution", "worker must expose guarded inferred intent", artifacts["worker"])
    require("natural_language_create_file" in artifacts["worker"]["execution_result"]["patch_summary"], "worker result should expose inferred create-file source", artifacts["worker"])
    require(artifacts["summary"]["maturity"] == "guarded_inferred_patch_execution_controller", "guarded inferred create-file maturity should show real adapter execution", artifacts["summary"])
    return {"id": "guarded-inferred-create-file-execution", "result": result, "intent": artifacts["worker"]["execution_intent"]}


def run_test_inferred_missing_function_trial(root: Path) -> dict[str, Any]:
    repo = root / "test-inferred-missing-function-repo"
    write_repo(
        repo,
        {
            "app.py": "",
            "test_app.py": (
                "import unittest\nimport app\n\n"
                "class ValueTest(unittest.TestCase):\n"
                "    def test_value(self):\n"
                "        self.assertEqual(app.value(), 42)\n\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n"
            ),
        },
    )
    result = run_ceraxia(
        CeraxiaInput(
            task="почини app.py чтобы тест проходил",
            repo_path=str(repo),
            runs_root=root / "runs",
            dry_run=False,
            execute_verification=True,
            verification_commands=("python -m unittest test_app.py", "python -m py_compile app.py"),
        )
    )
    artifacts = load_run_artifacts(Path(result["run_dir"]))
    require(result["ok"], "test-inferred missing-function handoff should complete", result)
    require(result["ready_for_execution"], "test-inferred missing-function handoff should be ready after real adapter execution", result)
    require("def value():\n    return 42\n" in (repo / "app.py").read_text(encoding="utf-8"), "test-inferred patch should mutate app.py")
    require(artifacts["worker"]["execution_intent"]["mode"] == "guarded_inferred_patch_execution", "worker must expose guarded inferred intent", artifacts["worker"])
    require("test_inferred_missing_function" in artifacts["worker"]["execution_result"]["patch_summary"], "worker result should expose test-inferred patch source", artifacts["worker"])
    require(artifacts["summary"]["surface_verification_status"] == "executed", "missing-function trial should execute source and test verification", artifacts["summary"])
    return {"id": "test-inferred-missing-function-execution", "result": result, "intent": artifacts["worker"]["execution_intent"]}


def run_test_inferred_return_mismatch_trial(root: Path) -> dict[str, Any]:
    repo = root / "test-inferred-return-mismatch-repo"
    write_repo(
        repo,
        {
            "app.py": "def value():\n    return 1\n",
            "test_app.py": (
                "import unittest\nfrom app import value\n\n"
                "class ValueTest(unittest.TestCase):\n"
                "    def test_value(self):\n"
                "        self.assertEqual(value(), 42)\n\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n"
            ),
        },
    )
    result = run_ceraxia(
        CeraxiaInput(
            task="почини app.py чтобы тест проходил",
            repo_path=str(repo),
            runs_root=root / "runs",
            dry_run=False,
            execute_verification=True,
            verification_commands=("python -m unittest test_app.py", "python -m py_compile app.py"),
        )
    )
    artifacts = load_run_artifacts(Path(result["run_dir"]))
    require(result["ok"], "test-inferred return-mismatch handoff should complete", result)
    require(result["ready_for_execution"], "test-inferred return-mismatch handoff should be ready after real adapter execution", result)
    require("return 42" in (repo / "app.py").read_text(encoding="utf-8"), "test-inferred return mismatch should mutate app.py")
    require("runtime_diagnostic_return_mismatch" in artifacts["worker"]["execution_result"]["patch_summary"], "worker result should expose return-mismatch patch source", artifacts["worker"])
    require(artifacts["summary"]["surface_verification_status"] == "executed", "return-mismatch trial should execute source and test verification", artifacts["summary"])
    return {"id": "test-inferred-return-mismatch-execution", "result": result, "intent": artifacts["worker"]["execution_intent"]}


def run_test_inferred_constant_trial(root: Path) -> dict[str, Any]:
    repo = root / "test-inferred-constant-repo"
    write_repo(
        repo,
        {
            "app.py": "",
            "test_app.py": (
                "import unittest\nfrom app import ANSWER\n\n"
                "class AnswerTest(unittest.TestCase):\n"
                "    def test_answer(self):\n"
                "        self.assertEqual(ANSWER, 42)\n\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n"
            ),
        },
    )
    result = run_ceraxia(
        CeraxiaInput(
            task="почини app.py чтобы тест проходил",
            repo_path=str(repo),
            runs_root=root / "runs",
            dry_run=False,
            execute_verification=True,
            verification_commands=("python -m unittest test_app.py", "python -m py_compile app.py"),
        )
    )
    artifacts = load_run_artifacts(Path(result["run_dir"]))
    require(result["ok"], "test-inferred constant handoff should complete", result)
    require(result["ready_for_execution"], "test-inferred constant handoff should be ready after real adapter execution", result)
    require("ANSWER = 42\n" in (repo / "app.py").read_text(encoding="utf-8"), "test-inferred constant patch should mutate app.py")
    require(artifacts["worker"]["execution_intent"]["mode"] == "guarded_inferred_patch_execution", "worker must expose guarded inferred intent", artifacts["worker"])
    require("test_inferred_missing_constant" in artifacts["worker"]["execution_result"]["patch_summary"], "worker result should expose constant patch source", artifacts["worker"])
    require(artifacts["summary"]["surface_verification_status"] == "executed", "constant trial should execute source and test verification", artifacts["summary"])
    return {"id": "test-inferred-constant-execution", "result": result, "intent": artifacts["worker"]["execution_intent"]}


def run_missing_path_trial(root: Path) -> dict[str, Any]:
    repo = root / "missing-path-repo"
    write_repo(repo, {"app.py": "def app():\n    return True\n"})
    result = run_ceraxia(
        CeraxiaInput(
            task="почини `missing.py` без изменения public API",
            repo_path=str(repo),
            runs_root=root / "runs",
            dry_run=True,
        )
    )
    artifacts = load_run_artifacts(Path(result["run_dir"]))
    require(not result["ok"], "missing explicit path should block handoff", result)
    require(artifacts["brief"]["blocked"], "missing explicit path should block implementation brief", artifacts["brief"])
    require("missing.py" in artifacts["brief"]["survey_quality_gate"]["missing_path_hints"], "missing path hint must be recorded", artifacts["brief"])
    return {"id": "missing-path-blocker", "result": result, "blockers": artifacts["brief"]["blockers"]}


def run_unshaped_real_execution_trial(root: Path) -> dict[str, Any]:
    repo = root / "unshaped-real-repo"
    write_repo(repo, {"app.py": "def app():\n    return True\n"})
    result = run_ceraxia(
        CeraxiaInput(
            task="добавь API helper в `app.py` без structured patch payload",
            repo_path=str(repo),
            runs_root=root / "runs",
            dry_run=False,
        )
    )
    artifacts = load_run_artifacts(Path(result["run_dir"]))
    require(not result["ok"], "unshaped real execution should block until autonomous adapter exists", result)
    require(artifacts["worker"]["execution_intent"]["mode"] == "planning_handoff_only", "unshaped execution must preserve planning-only intent", artifacts["worker"])
    require(not artifacts["worker"]["execution_intent"]["real_execution_supported"], "unshaped execution must not claim real support", artifacts["worker"])
    require(artifacts["summary"]["maturity"] == "blocked_controller_with_audited_handoff", "blocked unshaped maturity should be explicit", artifacts["summary"])
    require(any("autonomous execution adapter" in item for item in artifacts["worker"]["notes"]), "worker blocker should name missing autonomous adapter", artifacts["worker"])
    return {"id": "unshaped-real-execution-blocker", "result": result, "intent": artifacts["worker"]["execution_intent"]}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        trials = [
            run_dry_security_trial(root),
            run_explicit_patch_trial(root),
            run_guarded_inferred_patch_trial(root),
            run_guarded_inferred_create_file_trial(root),
            run_test_inferred_missing_function_trial(root),
            run_test_inferred_return_mismatch_trial(root),
            run_test_inferred_constant_trial(root),
            run_missing_path_trial(root),
            run_unshaped_real_execution_trial(root),
        ]
    print(json.dumps({"ok": True, "trial_count": len(trials), "trials": trials}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
