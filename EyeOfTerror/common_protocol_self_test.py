#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from EyeOfTerror.common_protocol import (
    acceptance_review,
    append_progress_event,
    commander_order,
    final_response,
    governor_plan,
    governor_report,
    mission_intake,
    progress_event,
    revision_order,
    validate_protocol_payload,
    worker_order,
    worker_report,
)
from EyeOfTerror.common_protocol.validation import ProtocolValidationError


def assert_valid(payload: dict[str, object], payload_type: str) -> None:
    validate_protocol_payload(payload, expected_type=payload_type)


def main() -> int:
    mission_id = "mission-protocol-smoke"
    assert_valid(mission_intake(mission_id, "Сделай сложную задачу."), "mission_intake")
    assert_valid(
        commander_order(
            mission_id,
            to="IskandarKhayon",
            user_request="Сделай сложную задачу.",
            commander_intent="Назначить профильного бригадира и удержать ревизии внутри командования.",
            primary_goal="Получить проверенный финал.",
            success_conditions=["Финал принят Вармастером."],
            constraints=["Не отправлять needs_revision пользователю как финал."],
            escalate_to_user_if=["Нужен выбор пользователя."],
        ),
        "commander_order",
    )
    assert_valid(
        governor_plan(
            mission_id,
            governor="IskandarKhayon",
            understanding="Нужна доменная декомпозиция.",
            work_plan=[{"step_id": "survey", "worker": "CorpusIngestor", "goal": "найти источники"}],
            quality_gates=["проверить полноту"],
        ),
        "governor_plan",
    )
    assert_valid(
        worker_order(
            mission_id,
            step_id="survey",
            sender="IskandarKhayon",
            to="CorpusIngestor",
            task="Найти источники.",
            expected_output="Список источников.",
        ),
        "worker_order",
    )
    assert_valid(progress_event(mission_id, "Warmaster", "commander", "assigned", "done", "Назначение", "Бригадир назначен."), "progress_event")
    assert_valid(worker_report(mission_id, "survey", "CorpusIngestor", "done", "Источники найдены."), "worker_report")
    assert_valid(governor_report(mission_id, "IskandarKhayon", "ready", "Работа готова."), "governor_report")
    assert_valid(acceptance_review(mission_id, accepted=False, reason="Нужна доработка.", required_revision={"to": "IskandarKhayon", "order": "Уточнить источники."}), "acceptance_review")
    assert_valid(revision_order(mission_id, "IskandarKhayon", "Недостаточно источников.", "Расширить корпус."), "revision_order")
    assert_valid(final_response(mission_id, "completed", "Финальный ответ."), "final_response")

    bad = worker_report(mission_id, "survey", "CorpusIngestor", "maybe", "Неверный статус.")
    try:
        validate_protocol_payload(bad, expected_type="worker_report")
    except ProtocolValidationError:
        pass
    else:
        raise AssertionError("invalid worker_report status was accepted")

    with tempfile.TemporaryDirectory() as tmp:
        events_path = Path(tmp) / "progress_events.jsonl"
        append_progress_event(events_path, progress_event(mission_id, "Warmaster", "commander", "intake", "started", "Прием", "Миссия принята."))
        if not events_path.read_text(encoding="utf-8").strip():
            raise AssertionError("progress event was not appended")

    print("[ok] common command protocol")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
