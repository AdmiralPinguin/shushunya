#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WARM_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(WARM_ROOT) not in sys.path:
    sys.path.insert(0, str(WARM_ROOT))

from EyeOfTerror.common_protocol import commander_order, progress_event, worker_order
from eye_of_terror.run_state import governor_activity_report


def main() -> int:
    command = commander_order(
        "mission-tabs-self-test",
        to="IskandarKhayon",
        user_request="Собери реконструкцию события.",
        commander_intent="Проверить вкладки бригад.",
        primary_goal="События должны быть видны как карточки бригад.",
        success_conditions=["worker events stay in the governor tab"],
    )
    lex_order = worker_order(
        "mission-tabs-self-test",
        step_id="source_discovery",
        sender="IskandarKhayon",
        to="Lexmechanic",
        task="Найти источники.",
        expected_output="source_map.json",
    )
    events = [
        progress_event(
            "mission-tabs-self-test",
            actor="Warmaster",
            role="commander",
            phase="assigned",
            status="done",
            title="Назначен Искандар",
            body="Командный приказ сформирован.",
        ),
        progress_event(
            "mission-tabs-self-test",
            actor="IskandarKhayon",
            role="governor",
            phase="planning",
            status="done",
            title="Составляю план источников",
            body="Бригадир разбил задачу на поисковый и проверочный этапы.",
        ),
        progress_event(
            "mission-tabs-self-test",
            actor="Lexmechanic",
            role="worker",
            phase="executing",
            status="running",
            title="Ищу источники",
            body="Воркер проверяет карту источников.",
        ),
    ]
    summary = {
        "task_id": "tabs-self-test",
        "governor": "IskandarKhayon",
        "status": "running",
        "created_at": "2026-07-07T00:00:00Z",
        "updated_at": "2026-07-07T00:01:00Z",
        "mission_protocol": {
            "commander_order": command,
            "governor_plan": {
                "type": "governor_plan",
                "mission_id": "mission-tabs-self-test",
                "governor": "IskandarKhayon",
                "understanding": "Проверить вкладки бригад.",
                "work_plan": [{"step_id": "source_discovery", "worker": "Lexmechanic", "goal": "Найти источники."}],
                "quality_gates": ["source map is auditable"],
                "expected_deliverables": ["source_map.json"],
            },
            "worker_orders": [lex_order],
        },
        "mission_progress_events": events,
        "progress": {"step_states": []},
        "result": {},
        "revision_plan": {"required": False, "steps": []},
        "revision_plan_summary": {},
        "final_manifest_summary": {},
    }
    ledger = {
        "task_id": "tabs-self-test",
        "governor": "IskandarKhayon",
        "status": "running",
        "goal": "Проверить вкладки бригад.",
        "created_at": "2026-07-07T00:00:00Z",
        "updated_at": "2026-07-07T00:01:00Z",
    }
    activity = governor_activity_report(summary, ledger)
    tabs = activity.get("brigade_tabs") if isinstance(activity.get("brigade_tabs"), list) else []
    by_key = {str(tab.get("key") or ""): tab for tab in tabs if isinstance(tab, dict)}
    if "iskandar" not in by_key:
        raise AssertionError(f"Iskandar tab missing: {activity}")
    iskandar = by_key["iskandar"]
    if iskandar.get("label") != "Искандар" or iskandar.get("governor") != "IskandarKhayon":
        raise AssertionError(f"bad Iskandar tab header: {iskandar}")
    iskandar_cards = iskandar.get("activity_cards") if isinstance(iskandar.get("activity_cards"), list) else []
    if not any(card.get("actor") == "Lexmechanic" and card.get("role") == "worker" for card in iskandar_cards if isinstance(card, dict)):
        raise AssertionError(f"worker progress card was not grouped into Iskandar tab: {iskandar}")
    if not any(card.get("card_title") == "Ищу источники" and card.get("card_body") == "Воркер проверяет карту источников." for card in iskandar_cards if isinstance(card, dict)):
        raise AssertionError(f"card title/body were not exposed for UI rendering: {iskandar}")
    if any(card.get("source") != "mission_protocol" for card in iskandar_cards if isinstance(card, dict)):
        raise AssertionError(f"brigade tab mixed non-protocol diagnostic cards into UI activity: {iskandar_cards}")
    activity_cards = activity.get("activity_cards") if isinstance(activity.get("activity_cards"), list) else []
    if (
        len(activity_cards) != len(events)
        or any(card.get("source") != "mission_protocol" for card in activity_cards if isinstance(card, dict))
        or any(card.get("source") == "run_summary" for card in activity_cards if isinstance(card, dict))
    ):
        raise AssertionError(f"activity_cards must be progress_event-only: {activity}")
    summary_cards = activity.get("summary_activity_cards") if isinstance(activity.get("summary_activity_cards"), list) else []
    if not summary_cards or not all(card.get("source") == "run_summary" for card in summary_cards if isinstance(card, dict)):
        raise AssertionError(f"run-summary diagnostics should remain separate from brigade activity: {activity}")
    if not iskandar.get("active"):
        raise AssertionError(f"running worker event should mark brigade tab active: {iskandar}")
    if activity.get("log_text"):
        raise AssertionError(f"brigade activity should stay structured, not log_text: {activity}")
    print("[ok] Warmaster brigade tabs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
