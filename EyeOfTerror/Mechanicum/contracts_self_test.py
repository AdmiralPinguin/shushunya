#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import sys


ROOT = Path(__file__).resolve().parent
CERAXIA_PATH = str(ROOT / "Ceraxia")
PLANNING_PATH = str(ROOT / "PlanningBrigade")
CODE_BRIGADE_PATH = str(ROOT / "CodeBrigade")
for path in [CERAXIA_PATH, PLANNING_PATH, CODE_BRIGADE_PATH]:
    if path not in sys.path:
        sys.path.insert(0, path)

from ceraxia import CeraxiaInput, build_implementation_brief, build_repo_survey_stub, run_ceraxia  # noqa: E402
from code_brigade_adapter import build_worker_report  # noqa: E402
from planning_brigade import build_planning_packet  # noqa: E402


def load_schema(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"schema must be object: {path}")
    return payload


def assert_required(schema_path: Path, payload: dict, label: str) -> None:
    schema = load_schema(schema_path)
    missing = [field for field in schema.get("required", []) if field not in payload]
    if missing:
        raise AssertionError(f"{label} missing schema required fields {missing}: {payload}")


def main() -> int:
    packet = build_planning_packet(
        {
            "task": "почини security API migration compatibility pytest",
            "repo_path": str(ROOT),
        }
    )
    assert_required(ROOT / "PlanningBrigade" / "planning_contract.schema.json", packet, "planning packet")
    survey = build_repo_survey_stub(packet)
    brief = build_implementation_brief(packet, survey)
    assert_required(ROOT / "Ceraxia" / "contracts" / "implementation_brief.schema.json", brief, "implementation brief")
    worker_report = build_worker_report(brief, dry_run=True)
    assert_required(ROOT / "CodeBrigade" / "code_brigade_contract.schema.json", worker_report, "worker report")

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        result = run_ceraxia(
            CeraxiaInput(
                task="почини API compatibility pytest",
                repo_path=str(repo),
                runs_root=Path(tmp) / "runs",
            )
        )
        if not result["ok"]:
            raise AssertionError(f"Ceraxia smoke run should pass audit: {result}")
        run_dir = Path(result["run_dir"])
        artifact_schema = load_schema(ROOT / "Ceraxia" / "contracts" / "run_artifacts.schema.json")
        missing_artifacts = [name for name in artifact_schema.get("required", []) if not (run_dir / name).exists()]
        if missing_artifacts:
            raise AssertionError(f"run artifact schema drifted from generated files: {missing_artifacts}")
    print("[ok] EyeOfTerror Mechanicum contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
