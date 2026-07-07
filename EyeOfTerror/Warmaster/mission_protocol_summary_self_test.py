#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
WARM_ROOT = ROOT / "Warmaster"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(WARM_ROOT) not in sys.path:
    sys.path.insert(0, str(WARM_ROOT))

from EyeOfTerror.common_protocol import append_progress_event, commander_order, governor_plan, progress_event, worker_order
from eye_of_terror.mission_control import mission_protocol_audit, mission_protocol_summary, mission_state, record_mission_state


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    with TemporaryDirectory() as tmp:
        mission_dir = Path(tmp) / "missions" / "mission-summary-self-test"
        mission_dir.mkdir(parents=True)
        write_json(
            mission_dir / "mission.json",
            {
                "mission_id": "mission-summary-self-test",
                "task_id": "summary-self-test",
                "status": "plan_review",
                "assigned_governor": "IskandarKhayon",
                "source_channel": "self_test",
            },
        )
        write_json(
            mission_dir / "mission_intake.json",
            {
                "type": "mission_intake",
                "protocol_version": 1,
                "mission_id": "mission-summary-self-test",
                "created_at": "2026-01-01T00:00:00Z",
                "source_channel": "self_test",
                "user_id": "",
                "user_request": "Собери источники.",
                "status": "intake",
            },
        )
        write_json(
            mission_dir / "commander_order.json",
            commander_order(
                "mission-summary-self-test",
                to="IskandarKhayon",
                user_request="Собери источники.",
                commander_intent="Проверить сводку протокола.",
                primary_goal="Получить диагностируемый progress stream.",
                success_conditions=["progress events are visible"],
            ),
        )
        write_json(
            mission_dir / "governor_plan.json",
            governor_plan(
                "mission-summary-self-test",
                governor="IskandarKhayon",
                understanding="Получить диагностируемый progress stream.",
                work_plan=[{"step_id": "source_discovery", "worker": "Lexmechanic", "goal": "Найти источники."}],
                quality_gates=["progress stream is auditable"],
                expected_deliverables=["source_map.json"],
            ),
        )
        write_json(
            mission_dir / "worker_orders" / "worker_order-source_discovery.json",
            worker_order(
                "mission-summary-self-test",
                step_id="source_discovery",
                sender="IskandarKhayon",
                to="Lexmechanic",
                task="Найти источники.",
                expected_output="source_map.json",
            ),
        )
        durable = record_mission_state(mission_dir, "plan_review")
        if durable.get("status") != "plan_review" or durable.get("source") != "mission_protocol":
            raise AssertionError(f"durable mission_state was not recorded: {durable}")
        events_path = mission_dir / "progress_events.jsonl"
        append_progress_event(
            events_path,
            progress_event(
                "mission-summary-self-test",
                actor="Warmaster",
                role="commander",
                phase="assigned",
                status="done",
                title="Назначен бригадир",
                body="Приказ сформирован.",
            ),
        )
        append_progress_event(
            events_path,
            progress_event(
                "mission-summary-self-test",
                actor="IskandarKhayon",
                role="governor",
                phase="planning",
                status="done",
                title="Бригадир составил план",
                body="План готов.",
            ),
        )
        summary = mission_protocol_summary(mission_dir)
        if summary.get("has_mission_state") is not True:
            raise AssertionError(f"mission_state.json is not reflected in protocol summary: {summary}")
        if summary.get("protocol_audit_ok") is not True:
            raise AssertionError(f"protocol audit should pass for a complete plan_review mission: {summary.get('protocol_audit')}")
        if summary.get("progress_event_count") != 2:
            raise AssertionError(f"progress event count missing: {summary}")
        if summary.get("progress_event_roles") != {"commander": 1, "governor": 1}:
            raise AssertionError(f"role counts are wrong: {summary}")
        if summary.get("progress_event_phases") != {"assigned": 1, "planning": 1}:
            raise AssertionError(f"phase counts are wrong: {summary}")
        latest = summary.get("latest_progress_event") if isinstance(summary.get("latest_progress_event"), dict) else {}
        if latest.get("actor") != "IskandarKhayon":
            raise AssertionError(f"latest progress event is wrong: {latest}")
        state = mission_state(Path(tmp), "mission-summary-self-test")
        canonical = state.get("mission_state") if isinstance(state.get("mission_state"), dict) else {}
        if (
            canonical.get("status") != "plan_review"
            or canonical.get("assigned_governor") != "IskandarKhayon"
            or canonical.get("user_visible_state") != "accepted"
            or canonical.get("revision_is_internal") is not True
            or state.get("durable_mission_state", {}).get("status") != "plan_review"
        ):
            raise AssertionError(f"mission_state projection is wrong: {canonical}")
        audit = state.get("protocol_audit") if isinstance(state.get("protocol_audit"), dict) else {}
        if audit.get("ok") is not True or audit.get("counts", {}).get("worker_orders") != 1:
            raise AssertionError(f"mission_state endpoint did not expose protocol audit: {audit}")
        broken_dir = Path(tmp) / "missions" / "mission-broken-direct-routing"
        broken_dir.mkdir(parents=True)
        write_json(
            broken_dir / "mission.json",
            {
                "mission_id": "mission-broken-direct-routing",
                "task_id": "broken-direct-routing",
                "status": "assigned",
                "assigned_governor": "IskandarKhayon",
            },
        )
        write_json(
            broken_dir / "mission_intake.json",
            {
                "type": "mission_intake",
                "protocol_version": 1,
                "mission_id": "mission-broken-direct-routing",
                "created_at": "2026-01-01T00:00:00Z",
                "source_channel": "self_test",
                "user_id": "",
                "user_request": "Сырой обход без приказа.",
                "status": "intake",
            },
        )
        record_mission_state(broken_dir, "assigned")
        broken_audit = mission_protocol_audit(broken_dir)
        if broken_audit.get("ok") is not False or not any("commander_order.json is missing" in error for error in broken_audit.get("errors", [])):
            raise AssertionError(f"protocol audit did not catch missing commander_order: {broken_audit}")
    print("[ok] Warmaster mission protocol summary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
