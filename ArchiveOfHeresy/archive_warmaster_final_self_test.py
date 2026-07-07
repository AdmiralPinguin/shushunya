#!/usr/bin/env python3
from __future__ import annotations

import archive_handler
from archive_handler import ArchiveHandler
import task_journal
from task_journal import deliver_final_to_chat, final_message_from_orchestration


def final_message(payload: dict[str, object]) -> str:
    return ArchiveHandler.warmaster_final_message(None, payload)


def accepted_review() -> dict[str, object]:
    return {
        "type": "acceptance_review",
        "reviewer": "Warmaster",
        "accepted": True,
        "status": "accepted",
    }


def accepted_protocol(answer: str) -> dict[str, object]:
    return {
        "acceptance_review": accepted_review(),
        "final_response": {
            "type": "final_response",
            "answer": answer,
        },
    }


def accepted_protocol_history(answer: str) -> dict[str, object]:
    return {
        "acceptance_reviews": [accepted_review()],
        "final_response": {
            "type": "final_response",
            "answer": answer,
        },
    }


class FakeArchiveHandler:
    def __init__(self, path: str) -> None:
        self.path = path

    def warmaster_activity_from_payload(self, payload):
        return ArchiveHandler.warmaster_activity_from_payload(self, payload)

    def warmaster_activity_entry_as_agent_event(self, entry, index, total):
        return ArchiveHandler.warmaster_activity_entry_as_agent_event(self, entry, index, total)

    def warmaster_event_as_agent_event(self, event, index, total):
        return ArchiveHandler.warmaster_event_as_agent_event(self, event, index, total)

    def warmaster_final_message(self, orchestration):
        return ArchiveHandler.warmaster_final_message(self, orchestration)

    def warmaster_run_as_agent_task(self, run, active=False, final_text="", activity=None):
        return ArchiveHandler.warmaster_run_as_agent_task(self, run, active=active, final_text=final_text, activity=activity)


def main() -> int:
    accepted = final_message(
        {
            "status": "completed",
            "summary": {
                "status": "completed",
                "mission_protocol": accepted_protocol("Принятый Вармастером финальный ответ."),
            },
            "display": {"detail": "служебный completed detail"},
            "final": {"deliverable": "fallback deliverable"},
        }
    )
    if accepted != "Принятый Вармастером финальный ответ.":
        raise AssertionError(f"accepted final_response was not preferred: {accepted!r}")
    unaccepted_final = final_message(
        {
            "status": "completed",
            "summary": {
                "status": "completed",
                "mission_protocol": {
                    "final_response": {
                        "type": "final_response",
                        "answer": "Финал без приемки не должен уйти в чат.",
                    }
                },
            },
        }
    )
    if unaccepted_final:
        raise AssertionError(f"final_response without accepted acceptance_review reached chat: {unaccepted_final!r}")
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
    activity_only = final_message(
        {
            "status": "completed",
            "summary": {"status": "completed", "mission_protocol": {}},
            "governor_activity": {
                "chat_independent": True,
                "progress_events": [
                    {
                        "type": "progress_event",
                        "actor": "IskandarKhayon",
                        "role": "governor",
                        "phase": "executing",
                        "status": "running",
                        "title": "Проверяю источники",
                        "body": "Это отчет для вкладки бригады, не финал чата.",
                    }
                ],
                "activity_cards": [
                    {
                        "kind": "progress_event",
                        "headline": "Проверяю источники",
                        "detail": "Это отчет для вкладки бригады, не финал чата.",
                    }
                ],
            },
        }
    )
    if activity_only:
        raise AssertionError(f"brigade activity leaked to chat final message: {activity_only!r}")
    activity_payload = {
        "chat_independent": True,
        "progress_events": [
            {
                "type": "progress_event",
                "actor": "Ceraxia",
                "role": "governor",
                "phase": "planning",
                "status": "running",
                "title": "Собираю план работ",
                "body": "Карточка для вкладки Цераксии.",
                "created_at": "2026-07-07T00:00:00Z",
            }
        ],
        "protocol_activity_cards": [
            {
                "kind": "progress_event",
                "headline": "Собираю план работ",
                "detail": "Карточка для вкладки Цераксии.",
            }
        ],
        "activity_cards": [
            {
                "kind": "progress_event",
                "headline": "Собираю план работ",
                "detail": "Карточка для вкладки Цераксии.",
            }
        ],
        "brigade_tabs": [
            {
                "key": "ceraxia",
                "label": "Цераксия",
                "governor": "Ceraxia",
                "status": "running",
                "active": True,
                "activity_cards": [
                    {
                        "kind": "progress_event",
                        "headline": "Собираю план работ",
                        "detail": "Карточка для вкладки Цераксии.",
                    }
                ],
            }
        ],
    }
    agent_task = ArchiveHandler.warmaster_run_as_agent_task(
        None,
        {"task_id": "activity-separation", "status": "running", "governor": "Ceraxia", "goal": "проверка вкладок"},
        active=True,
        activity=activity_payload,
    )
    if (
        agent_task.get("final")
        or agent_task.get("activity_log")
        or agent_task.get("progress_events") != activity_payload["progress_events"]
        or agent_task.get("activity_cards") != activity_payload["activity_cards"]
        or agent_task.get("protocol_activity_cards") != activity_payload["protocol_activity_cards"]
        or agent_task.get("brigade_tabs") != activity_payload["brigade_tabs"]
    ):
        raise AssertionError(f"Warmaster activity was not kept as brigade-tab payload: {agent_task}")
    journal_accepted = final_message_from_orchestration(
        {
            "status": "completed",
            "snapshot": {
                "summary": {
                    "status": "completed",
                    "mission_protocol": accepted_protocol_history("Финал для доставки из фонового журнала."),
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
                    "mission_protocol": accepted_protocol(f"Доставленный финал {task_id}."),
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
    captured = []
    delivered.clear()
    original_proxy = archive_handler.proxy_json_url
    original_write = archive_handler.write_json
    original_archive_append = archive_handler.append_chat_message
    archive_handler.proxy_json_url = lambda *_args, **_kwargs: (
        200,
        {
            "active": False,
            "snapshot": {
                "summary": {
                    "status": "completed",
                    "task_id": "mobile-final-event",
                    "goal": "проверить разделение финала и активности",
                    "mission_protocol": accepted_protocol("Мобильный финал из final_response."),
                }
            },
            "governor_activity": activity_payload,
        },
    )
    archive_handler.write_json = lambda _handler, status, payload: captured.append({"status": status, "payload": payload})
    archive_handler.append_chat_message = lambda *args, **kwargs: delivered.append({"args": args, "kwargs": kwargs})
    fake_handler = FakeArchiveHandler("/archive/mobile/agent/task?task_id=mobile-final-event")
    try:
        ArchiveHandler.mobile_agent_task(fake_handler)
    finally:
        archive_handler.proxy_json_url = original_proxy
        archive_handler.write_json = original_write
        archive_handler.append_chat_message = original_archive_append
    if len(captured) != 1:
        raise AssertionError(f"mobile_agent_task did not write exactly one payload: {captured}")
    mobile_payload = captured[0]["payload"]
    if mobile_payload.get("final") != "Мобильный финал из final_response.":
        raise AssertionError(f"mobile final text was not preserved: {mobile_payload}")
    final_event = mobile_payload.get("final_event") if isinstance(mobile_payload.get("final_event"), dict) else {}
    if (
        final_event.get("type") != "final"
        or final_event.get("ok") is not True
        or final_event.get("message") != "Мобильный финал из final_response."
    ):
        raise AssertionError(f"mobile final_event was not preserved as structured terminal event: {mobile_payload}")
    if (
        mobile_payload.get("progress_events") != activity_payload["progress_events"]
        or mobile_payload.get("activity_cards") != activity_payload["activity_cards"]
        or mobile_payload.get("protocol_activity_cards") != activity_payload["protocol_activity_cards"]
        or mobile_payload.get("brigade_tabs") != activity_payload["brigade_tabs"]
        or mobile_payload.get("activity_log")
    ):
        raise AssertionError(f"mobile brigade activity was not preserved separately: {mobile_payload}")
    if len(delivered) != 1 or delivered[0]["args"][1:3] != ("assistant", "Мобильный финал из final_response."):
        raise AssertionError(f"mobile final delivery did not append the accepted final once: {delivered}")
    captured.clear()
    delivered.clear()
    archive_handler.proxy_json_url = lambda *_args, **_kwargs: (
        200,
        {
            "active": False,
            "snapshot": {
                "summary": {
                    "status": "completed",
                    "task_id": "mobile-no-protocol-final",
                    "goal": "completed без protocol final_response",
                    "mission_protocol": {},
                }
            },
            "governor_activity": activity_payload,
            "final": {"deliverable": "legacy fallback must not become final"},
        },
    )
    archive_handler.write_json = lambda _handler, status, payload: captured.append({"status": status, "payload": payload})
    archive_handler.append_chat_message = lambda *args, **kwargs: delivered.append({"args": args, "kwargs": kwargs})
    fake_handler = FakeArchiveHandler("/archive/mobile/agent/task?task_id=mobile-no-protocol-final")
    try:
        ArchiveHandler.mobile_agent_task(fake_handler)
    finally:
        archive_handler.proxy_json_url = original_proxy
        archive_handler.write_json = original_write
        archive_handler.append_chat_message = original_archive_append
    no_final_payload = captured[0]["payload"]
    no_final_event = no_final_payload.get("final_event") if isinstance(no_final_payload.get("final_event"), dict) else {}
    if no_final_payload.get("final") or no_final_event.get("ok") is not False or no_final_event.get("message"):
        raise AssertionError(f"mobile completed run without final_response looked like user final: {no_final_payload}")
    if delivered:
        raise AssertionError(f"mobile completed run without final_response was delivered to chat: {delivered}")
    print("[ok] Archive Warmaster final-message gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
