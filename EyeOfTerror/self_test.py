#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from eye_of_terror.contracts import (
    TASK_CONTRACT_FIELDS,
    TASK_CONTRACT_REQUIRED_FIELDS,
    TASK_KINDS,
    WORKER_STEP_FIELDS,
    WORKER_STEP_REQUIRED_FIELDS,
    build_lore_reconstruction_contract,
    validate_task_contract_payload,
)
from eye_of_terror.inner_circle.iskandar import plan_lore_reconstruction
from eye_of_terror.pipeline import build_dispatch_packets, write_pipeline_run
from eye_of_terror.registry import worker_refs


def main() -> int:
    schema = json.loads((Path(__file__).resolve().parent / "contracts" / "task_contract.schema.json").read_text(encoding="utf-8"))
    schema_required = set(schema.get("required", []))
    schema_fields = set(schema.get("properties", {}))
    schema_kinds = set(schema.get("properties", {}).get("kind", {}).get("enum", []))
    step_schema = schema.get("properties", {}).get("worker_plan", {}).get("items", {})
    step_required = set(step_schema.get("required", []))
    step_fields = set(step_schema.get("properties", {}))
    if schema_required != TASK_CONTRACT_REQUIRED_FIELDS or schema_fields != TASK_CONTRACT_FIELDS or schema_kinds != TASK_KINDS:
        raise AssertionError("task contract schema and runtime validator constants disagree")
    if step_required != WORKER_STEP_REQUIRED_FIELDS or step_fields != WORKER_STEP_FIELDS:
        raise AssertionError("worker step schema and runtime validator constants disagree")
    print("[ok] task contract schema")

    workers = worker_refs()
    ports = [worker.port for worker in workers]
    if ports != sorted(ports) or min(ports) != 7001:
        raise AssertionError(f"worker ports are not stable 7001+ order: {ports}")
    names = {worker.name for worker in workers}
    required = {
        "ShushunyaAgent",
        "Lexmechanic",
        "AuspexBrowser",
        "NoosphericExtractor",
        "Chronologis",
        "ScriptoriumDaemon",
        "ReductorVerifier",
        "FabricatorFinalis",
        "ForgeRelay",
    }
    if not required.issubset(names):
        raise AssertionError(f"worker registry missing expected workers: {required - names}")
    print("[ok] worker registry")

    task = "Собери все известное о событиях Скалатракса и сделай реконструкцию."
    contract = build_lore_reconstruction_contract(task, task_id="test-skalathrax")
    payload = contract.to_dict()
    if payload["assigned_governor"] != "IskandarKhayon" or payload["kind"] != "research":
        raise AssertionError(f"bad lore contract routing: {payload}")
    validation_errors = validate_task_contract_payload(payload)
    if validation_errors:
        raise AssertionError(f"valid lore contract failed validation: {validation_errors}")
    broken_payload = json.loads(json.dumps(payload))
    broken_payload["worker_plan"][0]["depends_on"] = ["missing-step"]
    if not validate_task_contract_payload(broken_payload):
        raise AssertionError("broken lore contract should fail validation")
    if "/work/skalathrax/source_map.json" not in payload["required_artifacts"]:
        raise AssertionError(f"skalathrax artifacts not derived: {payload['required_artifacts']}")
    if "/work/skalathrax/source_snapshots.json" not in payload["required_artifacts"]:
        raise AssertionError(f"source snapshots missing: {payload['required_artifacts']}")
    step_workers = [step["worker"] for step in payload["worker_plan"]]
    if not all(step.get("step_id") for step in payload["worker_plan"]):
        raise AssertionError(f"worker steps must expose stable step_id: {payload['worker_plan']}")
    expected_order = [
        "Lexmechanic",
        "AuspexBrowser",
        "NoosphericExtractor",
        "Chronologis",
        "ScriptoriumDaemon",
        "ReductorVerifier",
        "FabricatorFinalis",
    ]
    if step_workers != expected_order:
        raise AssertionError(f"wrong Iskandar worker order: {step_workers}")
    print("[ok] lore reconstruction contract")

    plan = plan_lore_reconstruction(task, task_id="test-skalathrax").to_dict()
    if not plan["ok"] or plan["missing_workers"]:
        raise AssertionError(f"Iskandar plan did not resolve workers: {json.dumps(plan, ensure_ascii=False)}")
    if not plan.get("validation", {}).get("ok"):
        raise AssertionError(f"Iskandar plan failed contract validation: {plan.get('validation')}")
    if "Do not deliver a shallow wiki summary" not in " ".join(plan["contract"]["non_goals"]):
        raise AssertionError("Iskandar contract does not guard against shallow wiki summaries")
    print("[ok] Iskandar worker plan")

    packets = build_dispatch_packets(contract)
    if [packet.step_id for packet in packets] != [
        "source_discovery",
        "source_acquisition",
        "fact_extraction",
        "timeline",
        "draft_reconstruction",
        "critic_review",
        "finalize",
    ]:
        raise AssertionError(f"wrong dispatch packet sequence: {[packet.step_id for packet in packets]}")
    if packets[0].port != 7002 or packets[-1].port != 7007:
        raise AssertionError(f"dispatch packets target wrong ports: {[packet.port for packet in packets]}")
    if packets[2].request["task_id"] != "test-skalathrax:fact_extraction":
        raise AssertionError(f"dispatch task id is not stable: {packets[2].request}")
    if packets[2].request["input_artifacts"] != ["/work/skalathrax/source_snapshots.json"]:
        raise AssertionError(f"dependency input artifacts were not propagated: {packets[2].request}")
    expected_draft_inputs = [
        "/work/skalathrax/source_map.json",
        "/work/skalathrax/direct_event_notes.json",
        "/work/skalathrax/timeline.json",
    ]
    if packets[4].input_artifacts != expected_draft_inputs:
        raise AssertionError(f"multi-dependency input artifacts were not propagated: {packets[4].to_dict()}")
    print("[ok] Iskandar dispatch packets")

    with tempfile.TemporaryDirectory() as temp_dir:
        status = write_pipeline_run(contract, Path(temp_dir))
        if not status["ok"]:
            raise AssertionError(f"pipeline status failed: {status}")
        expected_files = [
            "contract.json",
            "status.json",
            "dispatch/source_discovery.json",
            "dispatch/source_acquisition.json",
            "dispatch/fact_extraction.json",
            "dispatch/timeline.json",
            "dispatch/draft_reconstruction.json",
            "dispatch/critic_review.json",
            "dispatch/finalize.json",
        ]
        missing = [name for name in expected_files if not (Path(temp_dir) / name).exists()]
        if missing:
            raise AssertionError(f"pipeline run did not write expected files: {missing}")
        leftovers = list(Path(temp_dir).glob("**/*.tmp"))
        if leftovers:
            raise AssertionError(f"pipeline run left atomic temp files: {leftovers}")
    print("[ok] Iskandar pipeline run package")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
