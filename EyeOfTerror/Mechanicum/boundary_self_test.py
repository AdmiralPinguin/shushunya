#!/usr/bin/env python3
from __future__ import annotations

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
    for dirname in ["Ceraxia", "CodeBrigade", "PlanningBrigade"]:
        require_dir(ROOT / dirname)
        require_file(ROOT / dirname / "README.md")
    require_file(ROOT / "Ceraxia" / "ceraxia.py")
    require_file(ROOT / "Ceraxia" / "self_test.py")
    require_file(ROOT / "CodeBrigade" / "code_brigade_contract.schema.json")
    require_file(ROOT / "PlanningBrigade" / "planning_brigade.py")
    for role in ["TaskTriage", "RepoSurveyor", "DesignStrategos", "VerificationArchitect", "RiskScribe"]:
        require_file(ROOT / "PlanningBrigade" / role / "README.md")


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
    assert_no_reverse_runtime_dependency()
    assert_runtime_artifacts_ignored()
    print("[ok] EyeOfTerror Mechanicum boundary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
