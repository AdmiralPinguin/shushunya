#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


CODE_BRIGADE_ROOT = Path(__file__).resolve().parent
MECHANICUM_ROOT = CODE_BRIGADE_ROOT.parent
EYE_ROOT = MECHANICUM_ROOT.parent
PROJECT_ROOT = EYE_ROOT.parent
for path in reversed((CODE_BRIGADE_ROOT, PROJECT_ROOT)):
    path_text = str(path)
    while path_text in sys.path:
        sys.path.remove(path_text)
    sys.path.insert(0, path_text)

from EyeOfTerror.model_brain import model_settings  # noqa: E402
from greenfield_live_trial import allocate_live_trial_root  # noqa: E402
from greenfield_project import build_greenfield_project_brief  # noqa: E402
from greenfield_verification_worker import run_greenfield_verification_loop  # noqa: E402


DEFAULT_RUN_ROOT = EYE_ROOT / "live_runs" / "greenfield_repair_trials"


def scenario_spec(scenario: str) -> dict[str, Any]:
    if scenario == "return_expression":
        return {
            "task": "Repair a generated math module so add(left, right) returns the sum required by the tests.",
            "files": [
                {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                {"path": "calc.py", "content": "def add(left, right):\n    return left - right\n"},
                {"path": "test_calc.py", "content": "import unittest\nimport calc\n\nclass CalcTests(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(calc.add(2, 3), 5)\n"},
            ],
            "verification_commands": ["python -m unittest test_calc.py"],
            "module_contracts": [{"module": "calc", "path": "calc.py", "responsibility": "add values", "requirements": ["add values"]}],
        }
    if scenario == "constant":
        return {
            "task": "Repair a generated settings module so enabled() returns the enabled feature flag required by the tests.",
            "files": [
                {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                {"path": "settings.py", "content": "FEATURE_ENABLED = False\n\n\ndef enabled():\n    return FEATURE_ENABLED\n"},
                {"path": "test_settings.py", "content": "import unittest\nimport settings\n\nclass SettingsTests(unittest.TestCase):\n    def test_enabled(self):\n        self.assertTrue(settings.enabled())\n"},
            ],
            "verification_commands": ["python -m unittest test_settings.py"],
            "module_contracts": [{"module": "settings", "path": "settings.py", "responsibility": "return enabled feature flag", "requirements": ["return enabled feature flag"]}],
        }
    if scenario == "function_body":
        return {
            "task": "Repair a generated grading module so grade(95) returns A and grade(70) returns C.",
            "files": [
                {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                {"path": "grades.py", "content": "def grade(score):\n    if score >= 90:\n        return 'B'\n    return 'C'\n"},
                {"path": "test_grades.py", "content": "import unittest\nimport grades\n\nclass GradeTests(unittest.TestCase):\n    def test_grade(self):\n        self.assertEqual(grades.grade(95), 'A')\n        self.assertEqual(grades.grade(70), 'C')\n"},
            ],
            "verification_commands": ["python -m unittest test_grades.py"],
            "module_contracts": [{"module": "grades", "path": "grades.py", "responsibility": "grade scores", "requirements": ["grade scores"]}],
        }
    if scenario == "exact_replace":
        return {
            "task": "Repair a generated demo module so value() returns ready as required by the tests.",
            "files": [
                {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                {"path": "demo.py", "content": "def value():\n    return 'broken'\n"},
                {"path": "test_demo.py", "content": "import unittest\nimport demo\n\nclass DemoTests(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(demo.value(), 'ready')\n"},
            ],
            "verification_commands": ["python -m unittest test_demo.py"],
            "module_contracts": [{"module": "demo", "path": "demo.py", "responsibility": "return ready", "requirements": ["return ready"]}],
        }
    if scenario == "name_error":
        return {
            "task": "Repair a generated module by removing the stray undefined name line while preserving value().",
            "files": [
                {"path": ".ceraxia_greenfield_workspace", "content": "created-by=ceraxia-code-brigade\n"},
                {"path": "demo.py", "content": "READY = 'ready'\nstray_symbol\n\ndef value():\n    return READY\n"},
                {"path": "test_demo.py", "content": "import unittest\nimport demo\n\nclass DemoTests(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(demo.value(), 'ready')\n"},
            ],
            "verification_commands": ["python -m unittest test_demo.py"],
            "module_contracts": [{"module": "demo", "path": "demo.py", "responsibility": "return ready", "requirements": ["return ready"]}],
        }
    raise ValueError(f"unsupported repair scenario: {scenario}")


def write_project(workspace: Path, project: dict[str, Any]) -> None:
    for item in project.get("files", []):
        if not isinstance(item, dict):
            continue
        path = workspace / str(item.get("path") or "")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(item.get("content") or ""), encoding="utf-8")


def compact_repair_result(scenario: str, workspace: Path, project: dict[str, Any], loop: dict[str, Any]) -> dict[str, Any]:
    attempts = loop.get("attempts", []) if isinstance(loop.get("attempts"), list) else []
    repair_attempts = [
        attempt.get("repair_execution")
        for attempt in attempts
        if isinstance(attempt, dict) and isinstance(attempt.get("repair_execution"), dict)
    ]
    repaired_files: list[dict[str, Any]] = []
    repair_strategies: list[str] = []
    blockers: list[str] = []
    for repair in repair_attempts:
        if not isinstance(repair, dict):
            continue
        repair_strategy = str(repair.get("repair_strategy") or "")
        if repair_strategy:
            repair_strategies.append(repair_strategy)
        repaired_files.extend(row for row in repair.get("repaired_files", []) if isinstance(row, dict))
        blockers.extend(str(item) for item in repair.get("blockers", []) if isinstance(item, str))
    return {
        "kind": "code_brigade_greenfield_live_repair_trial_result",
        "contract_version": "eye-mechanicum.v1",
        "scenario": scenario,
        "status": "accepted" if loop.get("status") == "passed" and repaired_files else "blocked",
        "model_settings": model_settings(),
        "workspace": str(workspace),
        "verification_commands": project.get("verification_commands", []),
        "loop_status": str(loop.get("status") or ""),
        "stop_reason": str(loop.get("stop_reason") or ""),
        "attempt_count": len(attempts),
        "repair_attempt_count": len(repair_attempts),
        "repaired_files": repaired_files,
        "repair_strategies": repair_strategies,
        "blockers": blockers,
        "final_verification_status": str((loop.get("final_verification") or {}).get("status") or "") if isinstance(loop.get("final_verification"), dict) else "",
        "stop_condition_evidence": loop.get("stop_condition_evidence", {}) if isinstance(loop.get("stop_condition_evidence"), dict) else {},
    }


def run_live_repair_trial(scenario: str, run_root: Path) -> dict[str, Any]:
    trial_root = allocate_live_trial_root(run_root)
    workspace = trial_root / "workspace"
    workspace.mkdir(parents=True, exist_ok=False)
    spec = scenario_spec(scenario)
    project = build_greenfield_project_brief(spec["task"], spec)
    write_project(workspace, project)
    loop = run_greenfield_verification_loop(workspace, project["verification_commands"], project, max_cycles=2)
    result = compact_repair_result(scenario, workspace, project, loop)
    (trial_root / "live_greenfield_repair_trial_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (trial_root / "greenfield_repair_loop.json").write_text(json.dumps(loop, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a live-model GreenfieldRepairWorker verification repair trial.")
    parser.add_argument("--scenario", choices=["name_error", "exact_replace", "return_expression", "constant", "function_body"], default="return_expression")
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--require-accepted", action="store_true")
    args = parser.parse_args()
    result = run_live_repair_trial(args.scenario, args.run_root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if args.require_accepted and result["status"] != "accepted":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
