#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WARM_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(WARM_ROOT) not in sys.path:
    sys.path.insert(0, str(WARM_ROOT))

from EyeOfTerror.common_protocol import validate_protocol_payload
from eye_of_terror.inner_circle.iskandar import oversight_plan, plan_research_writing
from eye_of_terror.mission_control import (
    link_run_to_mission,
    mission_protocol_summary,
    record_worker_execution_started,
    record_worker_protocol_report,
    worker_report_from_payload,
)
from eye_of_terror.pipeline import write_pipeline_run
from eye_of_terror.run_validation import run_dispatch_package_errors


def read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"not a JSON object: {path}")
    return payload


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        run_dir = root / "run"
        mission_dir = root / "missions" / "mission-custom-worker"
        plan = plan_research_writing("Собери краткий отчет о локальных агентах.", task_id="worker-protocol")
        write_pipeline_run(plan.contract, run_dir, oversight=oversight_plan(plan.contract))
        dispatch = read_json(run_dir / "dispatch" / "source_discovery.json")
        order = dispatch.get("worker_order") if isinstance(dispatch.get("worker_order"), dict) else {}
        validate_protocol_payload(order, expected_type="worker_order")
        request = dispatch.get("request") if isinstance(dispatch.get("request"), dict) else {}
        request_order = request.get("worker_order") if isinstance(request.get("worker_order"), dict) else {}
        validate_protocol_payload(request_order, expected_type="worker_order")
        if request.get("task") != order.get("task") or request.get("expected_output") != order.get("expected_output"):
            raise AssertionError(f"legacy request was not normalized from worker_order: {request}")
        if order.get("mission_id") != "mission-worker-protocol":
            raise AssertionError(f"initial worker_order mission_id was not task-derived: {order}")
        status = read_json(run_dir / "status.json")
        broken = dict(dispatch)
        broken.pop("worker_order", None)
        broken_request = dict(broken.get("request") if isinstance(broken.get("request"), dict) else {})
        broken_request.pop("worker_order", None)
        broken["request"] = broken_request
        (run_dir / "dispatch" / "source_discovery.json").write_text(
            json.dumps(broken, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        broken_errors = run_dispatch_package_errors(run_dir, status)
        if (
            not any("dispatch worker_order missing for source_discovery" in error for error in broken_errors)
            or not any("dispatch request.worker_order missing for source_discovery" in error for error in broken_errors)
        ):
            raise AssertionError(f"dispatch validation did not require worker_order: {broken_errors}")
        (run_dir / "dispatch" / "source_discovery.json").write_text(
            json.dumps(dispatch, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        link_run_to_mission(
            run_dir,
            {
                "ok": True,
                "mission_id": mission_dir.name,
                "mission_dir": str(mission_dir),
                "commander_order": {"to": "IskandarKhayon"},
            },
        )
        governor_plan = read_json(mission_dir / "governor_plan.json")
        validate_protocol_payload(governor_plan, expected_type="governor_plan")
        if governor_plan.get("governor") != "IskandarKhayon" or not governor_plan.get("work_plan"):
            raise AssertionError(f"bad governor_plan: {governor_plan}")
        worker_order_paths = sorted((mission_dir / "worker_orders").glob("worker_order-*.json"))
        if len(worker_order_paths) != len(plan.contract.worker_plan):
            raise AssertionError(f"mission workspace did not record every worker_order: {worker_order_paths}")
        worker_order_by_step: dict[str, dict[str, object]] = {}
        for worker_order_path in worker_order_paths:
            stored_order = read_json(worker_order_path)
            validate_protocol_payload(stored_order, expected_type="worker_order")
            if stored_order.get("mission_id") != mission_dir.name:
                raise AssertionError(f"stored worker_order mission_id drifted: {stored_order}")
            worker_order_by_step[str(stored_order.get("step_id") or "")] = stored_order
        if (
            worker_order_by_step.get("corpus_ingestion", {}).get("to") != "CorpusIngestor"
            or worker_order_by_step.get("source_discovery", {}).get("to") != "Lexmechanic"
        ):
            raise AssertionError(f"mission worker_orders did not preserve step ownership: {worker_order_by_step}")
        protocol_summary = mission_protocol_summary(mission_dir)
        if (
            protocol_summary.get("worker_order_count") != len(plan.contract.worker_plan)
            or protocol_summary.get("has_governor_plan") is not True
            or protocol_summary.get("progress_event_roles", {}).get("governor", 0) < len(plan.contract.worker_plan)
        ):
            raise AssertionError(f"mission protocol summary did not expose worker orders: {protocol_summary}")
        synced = read_json(run_dir / "dispatch" / "source_discovery.json")
        synced_order = synced.get("worker_order") if isinstance(synced.get("worker_order"), dict) else {}
        if synced_order.get("mission_id") != mission_dir.name:
            raise AssertionError(f"worker_order mission_id was not synced: {synced_order}")
        synced_request = synced.get("request") if isinstance(synced.get("request"), dict) else {}
        synced_request_order = synced_request.get("worker_order") if isinstance(synced_request.get("worker_order"), dict) else {}
        validate_protocol_payload(synced_request_order, expected_type="worker_order")
        if synced_request_order.get("mission_id") != mission_dir.name:
            raise AssertionError(f"request.worker_order mission_id was not synced: {synced_request_order}")
        record_worker_execution_started(run_dir, synced)
        report = worker_report_from_payload(
            mission_dir.name,
            step_id="source_discovery",
            worker="CorpusIngestor",
            payload={"ok": True, "status": "completed", "summary": "Источники собраны.", "artifacts": ["/work/research/source_index.json"]},
            ok=True,
        )
        record_worker_protocol_report(run_dir, report)
        if not list((mission_dir / "worker_reports").glob("worker_report-source_discovery-*.json")):
            raise AssertionError("worker_report was not written to mission workspace")
        protocol_summary_after_report = mission_protocol_summary(mission_dir)
        if protocol_summary_after_report.get("worker_report_count") != 1:
            raise AssertionError(f"mission protocol summary did not expose worker report: {protocol_summary_after_report}")
        events = (mission_dir / "progress_events.jsonl").read_text(encoding="utf-8").strip().splitlines()
        if not any("source_discovery" in line for line in events):
            raise AssertionError("worker progress event was not appended")
        parsed_events = [json.loads(line) for line in events]
        if not any(
            item.get("role") == "worker"
            and item.get("phase") == "executing"
            and item.get("status") == "running"
            and item.get("actor") == synced_order.get("to")
            for item in parsed_events
        ):
            raise AssertionError(f"worker execution start progress_event was not appended: {parsed_events}")
    print("[ok] Warmaster worker protocol")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
