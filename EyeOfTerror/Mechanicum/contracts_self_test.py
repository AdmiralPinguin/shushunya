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

from ceraxia import (  # noqa: E402
    CeraxiaInput,
    build_evidence_matrix,
    build_execution_readiness,
    build_implementation_brief,
    build_repo_survey,
    build_verification_report,
    review_gate,
    run_ceraxia,
)
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


def assert_nested_required(schema_path: Path, payload: dict, field: str, label: str) -> None:
    schema = load_schema(schema_path)
    field_schema = schema.get("properties", {}).get(field, {})
    nested_payload = payload.get(field)
    if not isinstance(nested_payload, dict):
        raise AssertionError(f"{label} missing nested object {field}: {payload}")
    missing = [nested for nested in field_schema.get("required", []) if nested not in nested_payload]
    if missing:
        raise AssertionError(f"{label}.{field} missing schema required fields {missing}: {nested_payload}")


def assert_array_item_required(schema_path: Path, payload: dict, field: str, label: str) -> None:
    schema = load_schema(schema_path)
    item_required = schema.get("properties", {}).get(field, {}).get("items", {}).get("required", [])
    items = payload.get(field)
    if not isinstance(items, list):
        raise AssertionError(f"{label} missing array {field}: {payload}")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise AssertionError(f"{label}.{field}[{index}] must be object: {item}")
        missing = [required for required in item_required if required not in item]
        if missing:
            raise AssertionError(f"{label}.{field}[{index}] missing schema required fields {missing}: {item}")


def main() -> int:
    packet = build_planning_packet(
        {
            "task": "почини security API migration compatibility pytest",
            "repo_path": str(ROOT),
        }
    )
    assert_required(ROOT / "PlanningBrigade" / "planning_contract.schema.json", packet, "planning packet")
    survey = build_repo_survey(packet)
    brief = build_implementation_brief(packet, survey)
    assert_required(ROOT / "Ceraxia" / "contracts" / "implementation_brief.schema.json", brief, "implementation brief")
    worker_report = build_worker_report(brief, dry_run=True)
    code_schema = ROOT / "CodeBrigade" / "code_brigade_contract.schema.json"
    assert_required(code_schema, worker_report, "worker report")
    assert_nested_required(code_schema, worker_report, "implementation_plan", "worker report")
    verification = build_verification_report(brief, worker_report)
    review = review_gate(packet, brief, worker_report, verification)
    status = {"state": "finalized"}
    readiness = build_execution_readiness(status, brief, verification, review, dry_run=True)
    evidence_matrix = build_evidence_matrix(brief, worker_report, verification, readiness)
    evidence_schema = ROOT / "Ceraxia" / "contracts" / "evidence_matrix.schema.json"
    assert_required(evidence_schema, evidence_matrix, "evidence matrix")
    assert_nested_required(evidence_schema, evidence_matrix, "implementation_plan_sources", "evidence matrix")
    assert_array_item_required(evidence_schema, evidence_matrix, "rows", "evidence matrix")

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
