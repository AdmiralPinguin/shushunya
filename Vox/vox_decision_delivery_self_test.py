#!/usr/bin/env python3
"""Focused durable-delivery contract for typed urgent notifications."""
from __future__ import annotations

import inspect
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import vox_service


def _create_legacy_database(path: Path) -> None:
    """Build the exact pre-outbox shape to prove migration does not replay it."""
    with sqlite3.connect(path) as db:
        db.execute(
            """
            CREATE TABLE intents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                topic TEXT NOT NULL,
                body TEXT NOT NULL,
                announce_line TEXT NOT NULL DEFAULT '',
                speech_class TEXT NOT NULL DEFAULT 'unclassified',
                state TEXT NOT NULL DEFAULT 'open',
                announced_at TEXT,
                conveyed_at TEXT,
                dedupe_key TEXT,
                embedding_json TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        db.execute(
            "INSERT INTO intents (created_at, updated_at, source, kind, topic, body, announce_line, "
            "speech_class, dedupe_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "2026-07-01T00:00:00+09:00",
                "2026-07-01T00:00:00+09:00",
                "warmaster",
                "decision_required",
                "старый вопрос",
                "Старый вопрос",
                "Мне нужен старый выбор",
                "срочно",
                "legacy:decision",
            ),
        )


def _intent(intent_id: int) -> dict:
    with vox_service.connect() as db:
        row = db.execute("SELECT * FROM intents WHERE id = ?", (intent_id,)).fetchone()
    if row is None:
        raise AssertionError(f"intent {intent_id} disappeared")
    return dict(row)


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        original_db = vox_service.DB_PATH
        original_push = vox_service.push_fcm
        original_embed = vox_service.embed_text
        pushes: list[tuple[str, str]] = []
        push_results: list[dict | Exception] = [
            {"ok": True, "sent": 0, "reason": "simulated zero delivery"},
            {"ok": True, "sent": 1},
        ]

        def fake_push(title: str, body: str) -> dict:
            pushes.append((title, body))
            result = push_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        try:
            legacy_path = Path(temp) / "legacy.sqlite3"
            _create_legacy_database(legacy_path)
            vox_service.DB_PATH = legacy_path
            with vox_service.connect() as db:
                legacy = dict(db.execute("SELECT * FROM intents").fetchone())
            required_columns = {
                "push_state",
                "push_attempts",
                "push_next_at",
                "push_error",
                "pushed_at",
            }
            if not required_columns.issubset(legacy):
                raise AssertionError(f"outbox migration is incomplete: {legacy.keys()}")
            if legacy["push_state"] != "not_required":
                raise AssertionError(f"legacy urgent intent was queued for replay: {legacy}")
            if vox_service.deliver_pending_push_once().get("claimed") is not False:
                raise AssertionError("legacy urgent intent was claimable after migration")

            vox_service.DB_PATH = Path(temp) / "vox.sqlite3"
            vox_service.push_fcm = fake_push
            vox_service.embed_text = lambda _text: []
            payload = {
                "source": "warmaster",
                "kind": "decision_required",
                "topic": "нужен выбор",
                "body": "Мне нужен твой выбор.\nGodot или Canvas?",
                "dedupe_key": "decision:t-1",
            }
            first = vox_service.create_intent(payload)
            duplicate = vox_service.create_intent(payload)
            first_id = int(first["intent_id"])
            queued = _intent(first_id)
            if queued["push_state"] != "pending" or queued["push_attempts"] != 0:
                raise AssertionError(f"new urgent intent was not transactionally queued: {queued}")

            started = datetime.now().astimezone().replace(microsecond=0)
            failed = vox_service.deliver_pending_push_once(now=started)
            after_failure = _intent(first_id)
            if failed.get("ok") is not False or after_failure["push_state"] != "retry_wait":
                raise AssertionError(f"sent=0 was not checkpointed as retryable failure: {failed}, {after_failure}")
            if after_failure["push_attempts"] != 1 or "sent=0" not in str(after_failure["push_error"]):
                raise AssertionError(f"failure diagnostics/attempt checkpoint missing: {after_failure}")

            # Conversational lifecycle and transport lifecycle are independent:
            # retry the owed push even after the same intent was conveyed.
            vox_service.mark_conveyed({"conveyed_ids": [first_id]})
            retried_at = started + timedelta(seconds=vox_service.PUSH_RETRY_BASE_SECONDS + 1)
            retried = vox_service.deliver_pending_push_once(now=retried_at)
            after_success = _intent(first_id)
            if retried.get("ok") is not True or after_success["push_state"] != "sent":
                raise AssertionError(f"retry did not reach sent checkpoint: {retried}, {after_success}")
            if after_success["push_attempts"] != 2 or not after_success["pushed_at"]:
                raise AssertionError(f"successful checkpoint is incomplete: {after_success}")
            if after_success["state"] != "conveyed":
                raise AssertionError(f"push outbox mutated conveyed intent lifecycle: {after_success}")

            changed = vox_service.create_intent({**payload, "body": "Мне нужен твой выбор.\nGodot или Unity?"})
            refreshed = _intent(first_id)
            if refreshed["push_state"] != "pending" or refreshed["push_attempts"] != 0:
                raise AssertionError(f"material urgent refresh did not create a new owed push: {refreshed}")

            push_results.append(RuntimeError("simulated transport crash"))
            exception_failure = vox_service.deliver_pending_push_once(now=retried_at + timedelta(seconds=1))
            after_exception = _intent(first_id)
            if exception_failure.get("ok") is not False or "exception" not in str(after_exception["push_error"]).casefold():
                raise AssertionError(f"FCM exception was not made retryable: {exception_failure}, {after_exception}")

            stalled = vox_service.create_intent(
                {
                    "source": "warmaster",
                    "kind": "task_stalled_internal",
                    "topic": "работа остановилась",
                    "body": "Я пока не могу продолжить задачу: внутренняя проверка не приняла результат.",
                    "dedupe_key": "stalled:t-2",
                }
            )
            stalled_row = _intent(int(stalled["intent_id"]))
            completed = vox_service.create_intent(
                {
                    "source": "warmaster",
                    "kind": "task_completed",
                    "topic": "задача завершена",
                    "body": "Я закончил задачу. Результат уже лежит в общем чате.",
                    "dedupe_key": "completed:t-3",
                }
            )
            completed_row = _intent(int(completed["intent_id"]))
        finally:
            vox_service.DB_PATH = original_db
            vox_service.push_fcm = original_push
            vox_service.embed_text = original_embed

    if first.get("speech_class") != "срочно":
        raise AssertionError(f"typed decision was not forced urgent: {first}")
    if duplicate.get("duplicate") is not True or duplicate.get("intent_id") != first.get("intent_id"):
        raise AssertionError(f"identical decision was not deduplicated: {duplicate}")
    if changed.get("refreshed") is not True or changed.get("intent_id") != first.get("intent_id"):
        raise AssertionError(f"updated decision was not refreshed in place: {changed}")
    if stalled.get("speech_class") != "срочно" or stalled_row["push_state"] != "pending":
        raise AssertionError(f"typed internal stop was not durably queued: {stalled}, {stalled_row}")
    if completed.get("speech_class") != "срочно" or completed_row["push_state"] != "pending":
        raise AssertionError(f"typed completion was not durably queued: {completed}, {completed_row}")
    if "закончил" not in str(completed_row.get("announce_line", "")).casefold():
        raise AssertionError(f"typed completion did not keep a conversational result: {completed_row}")
    if len(pushes) != 3:
        raise AssertionError(f"create_intent pushed outside the outbox or retry count changed: {pushes}")
    fallback = "У меня есть обновление. Открой чат."
    for raw in (
        "Абаддона остановил HTTP 409, task_id=secret",
        "Цераксия ждёт ответ бригады",
        "Владелец, не трать моё время",
    ):
        projected = vox_service.conversation_push_text(raw, fallback)
        if projected != fallback:
            raise AssertionError(f"unsafe push did not fail closed: {raw!r} -> {projected!r}")
    brain_lower = vox_service.BRAIN_INSTRUCTIONS.casefold()
    if "с владельцем" in brain_lower or "сообщить владельцу" in brain_lower or "решение владельца" in brain_lower:
        raise AssertionError("Vox personality still frames the peer as an owner")
    if "панибратство" not in brain_lower or "первого лица" not in brain_lower:
        raise AssertionError("Vox personality lost the peer/first-person boundary")
    push_source = inspect.getsource(vox_service.push_fcm)
    if '"conversation_title"' not in push_source or '"conversation_body"' not in push_source:
        raise AssertionError("FCM transport does not publish trusted conversation fields")
    if "target=push_fcm" in inspect.getsource(vox_service.create_intent):
        raise AssertionError("create_intent still uses fire-and-forget FCM")
    if "push_delivery_loop" not in inspect.getsource(vox_service.main):
        raise AssertionError("Vox main does not start the durable outbox worker")
    print("vox durable decision delivery self-test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
