#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]


def require_file(path: Path) -> None:
    if not path.is_file():
        raise AssertionError(f"required file is missing: {path.relative_to(PROJECT_ROOT)}")


def require_dir(path: Path) -> None:
    if not path.is_dir():
        raise AssertionError(f"required directory is missing: {path.relative_to(PROJECT_ROOT)}")


def assert_brigade_structure() -> None:
    require_file(ROOT / "architecture_contract.json")
    for dirname in ["Ceraxia", "CodeBrigade", "PlanningBrigade"]:
        require_dir(ROOT / dirname)
        require_file(ROOT / dirname / "README.md")
    require_file(ROOT / "Ceraxia" / "ceraxia.py")
    require_file(ROOT / "Ceraxia" / "self_test.py")
    require_file(ROOT / "CodeBrigade" / "code_brigade_contract.schema.json")
    require_file(ROOT / "PlanningBrigade" / "planning_brigade.py")
    for role in ["TaskTriage", "RepoSurveyor", "DesignStrategos", "VerificationArchitect", "RiskScribe"]:
        require_file(ROOT / "PlanningBrigade" / role / "README.md")


def assert_architecture_contract() -> None:
    payload = json.loads((ROOT / "architecture_contract.json").read_text(encoding="utf-8"))
    if payload.get("kind") != "eye_mechanicum_architecture_contract":
        raise AssertionError("architecture_contract.json kind drifted")
    if payload.get("contract_version") != "eye-mechanicum.v1":
        raise AssertionError("architecture_contract.json contract_version drifted")
    if payload.get("governance_root") != "EyeOfTerror/Mechanicum":
        raise AssertionError("EyeOfTerror/Mechanicum must remain the governance root")
    if payload.get("legacy_runtime_root") != "Mechanicum":
        raise AssertionError("top-level Mechanicum must remain marked as legacy/shared runtime")
    expected_ownership = {
        "EyeOfTerror/Mechanicum/Ceraxia",
        "EyeOfTerror/Mechanicum/PlanningBrigade",
        "EyeOfTerror/Mechanicum/CodeBrigade",
    }
    ownership = payload.get("ownership")
    if not isinstance(ownership, dict):
        raise AssertionError("architecture_contract.json ownership must be an object")
    actual_ownership = set(ownership)
    if actual_ownership != expected_ownership:
        raise AssertionError(
            "architecture_contract.json ownership drifted: "
            f"expected={sorted(expected_ownership)} actual={sorted(actual_ownership)}"
        )
    missing_owned_paths = [
        owned_path for owned_path in sorted(expected_ownership) if not (PROJECT_ROOT / owned_path).is_dir()
    ]
    if missing_owned_paths:
        raise AssertionError(f"architecture_contract.json points at missing owned paths: {missing_owned_paths}")
    rules = payload.get("rules")
    if not isinstance(rules, list) or not any("must not import top-level Mechanicum" in str(rule) for rule in rules):
        raise AssertionError("architecture_contract.json must document the root Mechanicum import boundary")


def assert_no_reverse_runtime_dependency() -> None:
    offenders: list[str] = []
    for path in ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("import Mechanicum") or stripped.startswith("from Mechanicum"):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {stripped}")
    if offenders:
        raise AssertionError("EyeOfTerror/Mechanicum must not import root Mechanicum runtime directly: " + "; ".join(offenders))


def assert_runtime_artifacts_ignored() -> None:
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    required_patterns = [
        "EyeOfTerror/Mechanicum/Ceraxia/runs/",
        "__pycache__/",
    ]
    missing = [pattern for pattern in required_patterns if pattern not in gitignore]
    if missing:
        raise AssertionError(f"runtime ignore patterns missing: {missing}")


def main() -> int:
    assert_brigade_structure()
    assert_architecture_contract()
    assert_no_reverse_runtime_dependency()
    assert_runtime_artifacts_ignored()
    print("[ok] EyeOfTerror Mechanicum boundary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
