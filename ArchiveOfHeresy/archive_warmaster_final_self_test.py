#!/usr/bin/env python3
from __future__ import annotations

from archive_handler import ArchiveHandler
import task_journal
from task_journal import deliver_final_to_chat, final_message_from_orchestration


def final_message(payload: dict[str, object]) -> str:
    return ArchiveHandler.warmaster_final_message(None, payload)


def main() -> int:
    accepted = final_message(
        {
            "status": "completed",
            "summary": {
                "status": "completed",
                "mission_protocol": {
                    "final_response": {
                        "type": "final_response",
                        "answer": "Принятый Вармастером финальный ответ.",
                    }
                },
            },
            "display": {"detail": "служебный completed detail"},
            "final": {"deliverable": "fallback deliverable"},
        }
    )
    if accepted != "Принятый Вармастером финальный ответ.":
        raise AssertionError(f"accepted final_response was not preferred: {accepted!r}")
    legacy_fallback = final_message(
        {
            "status": "completed",
            "summary": {"status": "completed", "mission_protocol": {}},
            "display": {"detail": "служебный completed detail"},
            "final": {"deliverable": "legacy deliverable must not reach chat"},
        }
    )
    if legacy_fallback:
        raise AssertionError(f"legacy final payload bypassed final_response gate: {legacy_fallback!r}")
    journal_accepted = final_message_from_orchestration(
        {
            "status": "completed",
            "snapshot": {
                "summary": {
                    "status": "completed",
                    "mission_protocol": {
                        "final_response": {
                            "type": "final_response",
                            "answer": "Финал для доставки из фонового журнала.",
                        }
                    },
                }
            },
            "display": {"detail": "служебный completed detail"},
            "final": {"deliverable": "fallback deliverable"},
        }
    )
    if journal_accepted != "Финал для доставки из фонового журнала.":
        raise AssertionError(f"journal final_response was not preferred: {journal_accepted!r}")
    delivered = []
    original_fetch = task_journal.fetch_orchestration
    original_append = task_journal.append_chat_message
    task_journal.fetch_orchestration = lambda task_id: {
        "status": "completed",
        "snapshot": {
            "summary": {
                "status": "completed",
                "mission_protocol": {
                    "final_response": {
                        "type": "final_response",
                        "answer": f"Доставленный финал {task_id}.",
                    }
                },
            }
        },
    }
    task_journal.append_chat_message = lambda *args, **kwargs: delivered.append({"args": args, "kwargs": kwargs})
    try:
        if not deliver_final_to_chat("task-final-delivery"):
            raise AssertionError("deliver_final_to_chat returned false for completed final_response")
    finally:
        task_journal.fetch_orchestration = original_fetch
        task_journal.append_chat_message = original_append
    if len(delivered) != 1:
        raise AssertionError(f"final delivery wrote unexpected messages: {delivered}")
    if delivered[0]["args"][1:3] != ("assistant", "Доставленный финал task-final-delivery."):
        raise AssertionError(f"final delivery wrote wrong chat payload: {delivered}")
    if delivered[0]["kwargs"].get("dedupe_key") != "warmaster:task-final-delivery:final":
        raise AssertionError(f"final delivery did not use stable dedupe key: {delivered}")
    delivered.clear()
    task_journal.fetch_orchestration = lambda task_id: {
        "status": "completed",
        "snapshot": {
            "summary": {
                "status": "completed",
                "mission_protocol": {},
            }
        },
        "final": {"deliverable": f"Недопустимый legacy финал {task_id}."},
    }
    task_journal.append_chat_message = lambda *args, **kwargs: delivered.append({"args": args, "kwargs": kwargs})
    try:
        if deliver_final_to_chat("task-without-final-response"):
            raise AssertionError("deliver_final_to_chat accepted completed run without protocol final_response")
    finally:
        task_journal.fetch_orchestration = original_fetch
        task_journal.append_chat_message = original_append
    if delivered:
        raise AssertionError(f"legacy final payload was delivered to chat: {delivered}")
    revision = final_message(
        {
            "status": "revision",
            "summary": {"status": "revision"},
            "display": {"headline": "Финальный отчет: нужна ревизия", "detail": "needs_revision internal detail"},
            "final": {},
        }
    )
    if revision:
        raise AssertionError(f"internal revision leaked to chat final message: {revision!r}")
    blocked = final_message(
        {
            "status": "blocked",
            "summary": {"status": "blocked"},
            "display": {"headline": "Нужна эскалация", "detail": "blocked diagnostic detail"},
            "final": {},
        }
    )
    if blocked:
        raise AssertionError(f"blocked diagnostic leaked to chat final message: {blocked!r}")
    running = final_message(
        {
            "status": "running",
            "summary": {"status": "running"},
            "display": {"headline": "Run is active", "detail": "0/10 steps complete"},
            "final": {},
        }
    )
    if running:
        raise AssertionError(f"running diagnostic leaked to chat final message: {running!r}")
    print("[ok] Archive Warmaster final-message gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
