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
from eye_of_terror.mission_control import link_run_to_mission, record_worker_protocol_report, worker_report_from_payload
from eye_of_terror.pipeline import write_pipeline_run


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
        if order.get("mission_id") != "mission-worker-protocol":
            raise AssertionError(f"initial worker_order mission_id was not task-derived: {order}")
        link_run_to_mission(
            run_dir,
            {
                "ok": True,
                "mission_id": mission_dir.name,
                "mission_dir": str(mission_dir),
                "commander_order": {"to": "IskandarKhayon"},
            },
        )
        synced = read_json(run_dir / "dispatch" / "source_discovery.json")
        synced_order = synced.get("worker_order") if isinstance(synced.get("worker_order"), dict) else {}
        if synced_order.get("mission_id") != mission_dir.name:
            raise AssertionError(f"worker_order mission_id was not synced: {synced_order}")
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
        events = (mission_dir / "progress_events.jsonl").read_text(encoding="utf-8").strip().splitlines()
        if not any("source_discovery" in line for line in events):
            raise AssertionError("worker progress event was not appended")
    print("[ok] Warmaster worker protocol")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
