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
    build_planning_feedback_request,
    build_repo_survey,
    build_verification_report,
    review_gate,
    run_ceraxia,
)
from code_brigade_adapter import build_worker_report  # noqa: E402
from code_brigade_adapter import build_blocked_execution_result  # noqa: E402
from execution_adapter import execute_implementation_brief  # noqa: E402
from planning_brigade import build_planning_packet  # noqa: E402
from planning_feedback_contract import build_planning_feedback_intake  # noqa: E402


def load_schema(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"schema must be object: {path}")
    return payload


def matches_type(value: object, schema_type: object) -> bool:
    if isinstance(schema_type, list):
        return any(matches_type(value, item) for item in schema_type)
    if not isinstance(schema_type, str):
        return True
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return True


def assert_schema_subset(schema: dict, payload: object, label: str) -> None:
    if "type" in schema and not matches_type(payload, schema["type"]):
        raise AssertionError(f"{label} expected type {schema['type']}: {payload}")
    if "const" in schema and payload != schema["const"]:
        raise AssertionError(f"{label} expected const {schema['const']}: {payload}")
    if "enum" in schema and payload not in schema["enum"]:
        raise AssertionError(f"{label} expected one of {schema['enum']}: {payload}")
    if isinstance(payload, list):
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(payload) < min_items:
            raise AssertionError(f"{label} expected at least {min_items} items: {payload}")
        prefix_items = schema.get("prefixItems")
        if isinstance(prefix_items, list):
            for index, item_schema in enumerate(prefix_items):
                if index >= len(payload):
                    raise AssertionError(f"{label} missing prefix item {index}: {payload}")
                if isinstance(item_schema, dict):
                    assert_schema_subset(item_schema, payload[index], f"{label}[{index}]")
    if not isinstance(payload, dict):
        if isinstance(payload, list) and "items" in schema:
            for index, item in enumerate(payload):
                assert_schema_subset(schema["items"], item, f"{label}[{index}]")
        return
    missing = [field for field in schema.get("required", []) if field not in payload]
    if missing:
        raise AssertionError(f"{label} missing schema required fields {missing}: {payload}")
    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        for field, field_schema in properties.items():
            if field in payload and isinstance(field_schema, dict):
                assert_schema_subset(field_schema, payload[field], f"{label}.{field}")


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


def assert_execution_policy_matches_result_schema() -> None:
    policy = json.loads((ROOT / "CodeBrigade" / "execution_policy.json").read_text(encoding="utf-8"))
    assert_schema_subset(
        load_schema(ROOT / "CodeBrigade" / "execution_policy.schema.json"),
        policy,
        "execution policy",
    )
    schema = load_schema(ROOT / "CodeBrigade" / "execution_result.schema.json")
    if policy.get("contract_version") != schema.get("properties", {}).get("contract_version", {}).get("const"):
        raise AssertionError(f"execution policy version drifted from result schema: {policy}")
    expected_outputs = sorted(
        field
        for field in schema.get("required", [])
        if field not in {"kind", "contract_version", "status"}
    )
    policy_outputs = sorted(policy.get("future_adapter_outputs", []))
    if policy_outputs != expected_outputs:
        raise AssertionError(
            "execution policy future_adapter_outputs drifted from execution_result required fields: "
            f"policy={policy_outputs} schema={expected_outputs}"
        )


def assert_contract_version_consts() -> None:
    schema_paths = [
        ROOT / "PlanningBrigade" / "planning_contract.schema.json",
        ROOT / "PlanningBrigade" / "planning_feedback_intake.schema.json",
        ROOT / "Ceraxia" / "contracts" / "implementation_brief.schema.json",
        ROOT / "Ceraxia" / "contracts" / "diagnostic_repair_request.schema.json",
        ROOT / "Ceraxia" / "contracts" / "planning_feedback_request.schema.json",
        ROOT / "Ceraxia" / "contracts" / "evidence_matrix.schema.json",
        ROOT / "Ceraxia" / "contracts" / "run_summary.schema.json",
        ROOT / "CodeBrigade" / "code_brigade_contract.schema.json",
        ROOT / "CodeBrigade" / "execution_policy.schema.json",
        ROOT / "CodeBrigade" / "execution_result.schema.json",
        ROOT / "CodeBrigade" / "verification_policy.schema.json",
        ROOT / "CodeBrigade" / "verification_execution.schema.json",
    ]
    for schema_path in schema_paths:
        schema = load_schema(schema_path)
        version_schema = schema.get("properties", {}).get("contract_version")
        if not isinstance(version_schema, dict) or version_schema.get("const") != "eye-mechanicum.v1":
            raise AssertionError(f"schema contract_version drifted in {schema_path.relative_to(ROOT)}")


def main() -> int:
    assert_contract_version_consts()
    packet = build_planning_packet(
        {
            "task": "почини security API migration compatibility pytest",
            "repo_path": str(ROOT),
        }
    )
    assert_schema_subset(load_schema(ROOT / "PlanningBrigade" / "planning_contract.schema.json"), packet, "planning packet")
    survey = build_repo_survey(packet)
    brief = build_implementation_brief(packet, survey)
    assert_schema_subset(load_schema(ROOT / "Ceraxia" / "contracts" / "implementation_brief.schema.json"), brief, "implementation brief")
    worker_report = build_worker_report(brief, dry_run=True)
    code_schema = ROOT / "CodeBrigade" / "code_brigade_contract.schema.json"
    assert_schema_subset(load_schema(code_schema), worker_report, "worker report")
    assert_nested_required(code_schema, worker_report, "implementation_plan", "worker report")
    blocked_execution_result = build_blocked_execution_result(["real CodeBrigade execution adapter is not configured"])
    assert_schema_subset(
        load_schema(ROOT / "CodeBrigade" / "execution_result.schema.json"),
        blocked_execution_result,
        "execution result",
    )
    preflight_execution_result = execute_implementation_brief(brief)
    assert_schema_subset(
        load_schema(ROOT / "CodeBrigade" / "execution_result.schema.json"),
        preflight_execution_result,
        "execution result with preflight",
    )
    if "preflight" not in preflight_execution_result:
        raise AssertionError(f"execution adapter should expose preflight evidence: {preflight_execution_result}")
    assert_schema_subset(
        load_schema(ROOT / "CodeBrigade" / "execution_preflight.schema.json"),
        preflight_execution_result["preflight"],
        "execution preflight",
    )
    assert_execution_policy_matches_result_schema()
    verification = build_verification_report(brief, worker_report)
    verification_policy = json.loads((ROOT / "CodeBrigade" / "verification_policy.json").read_text(encoding="utf-8"))
    assert_schema_subset(
        load_schema(ROOT / "CodeBrigade" / "verification_policy.schema.json"),
        verification_policy,
        "verification policy",
    )
    output_contract = verification_policy.get("output_contract")
    if output_contract != "verification_execution.schema.json" or not (ROOT / "CodeBrigade" / str(output_contract)).is_file():
        raise AssertionError(f"verification policy output_contract is invalid: {verification_policy}")
    assert_schema_subset(
        load_schema(ROOT / "CodeBrigade" / "verification_execution.schema.json"),
        verification["verification_execution"],
        "verification execution",
    )
    review = review_gate(packet, brief, worker_report, verification)
    planning_feedback_request = build_planning_feedback_request("contracts-smoke", packet, brief, worker_report, verification, review)
    assert_schema_subset(
        load_schema(ROOT / "Ceraxia" / "contracts" / "planning_feedback_request.schema.json"),
        planning_feedback_request,
        "planning feedback request",
    )
    planning_feedback_intake = build_planning_feedback_intake(planning_feedback_request)
    assert_schema_subset(
        load_schema(ROOT / "PlanningBrigade" / "planning_feedback_intake.schema.json"),
        planning_feedback_intake,
        "planning feedback intake",
    )
    status = {"state": "finalized"}
    readiness = build_execution_readiness(status, brief, worker_report, verification, review, dry_run=True)
    evidence_matrix = build_evidence_matrix(brief, worker_report, verification, readiness)
    evidence_schema = ROOT / "Ceraxia" / "contracts" / "evidence_matrix.schema.json"
    assert_schema_subset(load_schema(evidence_schema), evidence_matrix, "evidence matrix")
    assert_nested_required(evidence_schema, evidence_matrix, "implementation_plan_sources", "evidence matrix")
    assert_array_item_required(evidence_schema, evidence_matrix, "rows", "evidence matrix")

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("def app():\n    return True\n", encoding="utf-8")
        (repo / "test_app.py").write_text("from app import app\n\ndef test_app():\n    assert app()\n", encoding="utf-8")
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
        summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
        assert_schema_subset(load_schema(ROOT / "Ceraxia" / "contracts" / "run_summary.schema.json"), summary, "run summary")
        repair_request = json.loads((run_dir / "diagnostic_repair_request.json").read_text(encoding="utf-8"))
        assert_schema_subset(
            load_schema(ROOT / "Ceraxia" / "contracts" / "diagnostic_repair_request.schema.json"),
            repair_request,
            "diagnostic repair request",
        )
        planning_feedback_request = json.loads((run_dir / "planning_feedback_request.json").read_text(encoding="utf-8"))
        assert_schema_subset(
            load_schema(ROOT / "Ceraxia" / "contracts" / "planning_feedback_request.schema.json"),
            planning_feedback_request,
            "planning feedback request",
        )
    print("[ok] EyeOfTerror Mechanicum contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
