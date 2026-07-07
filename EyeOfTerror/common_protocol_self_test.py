#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from EyeOfTerror.common_protocol import (
    ACCEPTANCE_STATUSES,
    GOVERNOR_REPORT_STATUSES,
    LIFECYCLE_STATUSES,
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
from EyeOfTerror.common_protocol.protocol import PROGRESS_PHASES, PROGRESS_STATUSES, WORKER_REPORT_STATUSES
from EyeOfTerror.common_protocol.validation import REQUIRED_FIELDS, ProtocolValidationError


ENVELOPE_FIELDS = {"type", "protocol_version", "mission_id", "created_at"}
SCHEMA_ENUM_FIELDS = {
    ("acceptance_review", "status"): ACCEPTANCE_STATUSES,
    ("governor_report", "status"): GOVERNOR_REPORT_STATUSES,
    ("mission_intake", "status"): LIFECYCLE_STATUSES,
    ("progress_event", "phase"): PROGRESS_PHASES,
    ("progress_event", "status"): PROGRESS_STATUSES,
    ("worker_report", "status"): WORKER_REPORT_STATUSES,
}


def assert_valid(payload: dict[str, object], payload_type: str) -> None:
    validate_protocol_payload(payload, expected_type=payload_type)


def assert_schema_runtime_alignment() -> None:
    schema_dir = PROJECT_ROOT / "EyeOfTerror" / "common_protocol" / "schemas"
    schema_names = {path.stem.removesuffix(".schema") for path in schema_dir.glob("*.schema.json")}
    if schema_names != set(REQUIRED_FIELDS):
        raise AssertionError(f"schema/runtime protocol type mismatch: schemas={schema_names} runtime={set(REQUIRED_FIELDS)}")
    for payload_type, runtime_required in REQUIRED_FIELDS.items():
        schema_path = schema_dir / f"{payload_type}.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        required = set(schema.get("required") if isinstance(schema.get("required"), list) else [])
        expected_required = ENVELOPE_FIELDS | runtime_required
        if required != expected_required:
            raise AssertionError(f"{payload_type} schema required drift: schema={required} runtime={expected_required}")
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        type_schema = properties.get("type") if isinstance(properties.get("type"), dict) else {}
        version_schema = properties.get("protocol_version") if isinstance(properties.get("protocol_version"), dict) else {}
        if type_schema.get("const") != payload_type or version_schema.get("const") != 1:
            raise AssertionError(f"{payload_type} schema envelope constants drift: {schema_path}")
    for (payload_type, field), expected_values in SCHEMA_ENUM_FIELDS.items():
        schema = json.loads((schema_dir / f"{payload_type}.schema.json").read_text(encoding="utf-8"))
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        field_schema = properties.get(field) if isinstance(properties.get(field), dict) else {}
        enum_values = set(field_schema.get("enum") if isinstance(field_schema.get("enum"), list) else [])
        if enum_values != set(expected_values):
            raise AssertionError(f"{payload_type}.{field} enum drift: schema={enum_values} runtime={set(expected_values)}")


def main() -> int:
    assert_schema_runtime_alignment()
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

    missing_created_at = worker_report(mission_id, "survey", "CorpusIngestor", "done", "Нет времени создания.")
    missing_created_at.pop("created_at", None)
    try:
        validate_protocol_payload(missing_created_at, expected_type="worker_report")
    except ProtocolValidationError:
        pass
    else:
        raise AssertionError("worker_report without created_at was accepted")

    with tempfile.TemporaryDirectory() as tmp:
        events_path = Path(tmp) / "progress_events.jsonl"
        append_progress_event(events_path, progress_event(mission_id, "Warmaster", "commander", "intake", "started", "Прием", "Миссия принята."))
        if not events_path.read_text(encoding="utf-8").strip():
            raise AssertionError("progress event was not appended")

    print("[ok] common command protocol")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
