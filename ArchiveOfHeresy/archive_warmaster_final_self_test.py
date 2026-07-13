#!/usr/bin/env python3
from __future__ import annotations

import io
import inspect
import json
from urllib.error import HTTPError

import archive_handler
import archive_ops
from archive_handler import ArchiveHandler
import task_journal
from task_journal import deliver_final_to_chat, final_message_from_orchestration

MISSION_ID = "mission-warmaster-final-self-test"
STATIC_TASK_ID = "warmaster-final-self-test"


def final_message(payload: dict[str, object]) -> str:
    return ArchiveHandler.warmaster_final_message(None, payload)


def accepted_review() -> dict[str, object]:
    return {
        "type": "acceptance_review",
        "mission_id": MISSION_ID,
        "reviewer": "Warmaster",
        "accepted": True,
        "status": "accepted",
    }


def accepted_protocol(answer: str) -> dict[str, object]:
    return {
        "mission": {"mission_id": MISSION_ID},
        "commander_order": {"mission_id": MISSION_ID},
        "acceptance_review": accepted_review(),
        "final_response": {
            "type": "final_response",
            "mission_id": MISSION_ID,
            "status": "completed",
            "accepted_by": "Warmaster",
            "answer": answer,
        },
    }


def accepted_protocol_history(answer: str) -> dict[str, object]:
    return {
        "mission": {"mission_id": MISSION_ID},
        "commander_order": {"mission_id": MISSION_ID},
        "acceptance_reviews": [accepted_review()],
        "final_response": {
            "type": "final_response",
            "mission_id": MISSION_ID,
            "status": "completed",
            "accepted_by": "Warmaster",
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

    def warmaster_acceptance_message(self, task_id):
        return ArchiveHandler.warmaster_acceptance_message(self, task_id)

    def warmaster_start_outcome_message(self, task, task_id, accepted, *outcomes):
        return ArchiveHandler.warmaster_start_outcome_message(self, task, task_id, accepted, *outcomes)

    def warmaster_http_error_response(self, exc):
        return ArchiveHandler.warmaster_http_error_response(self, exc)

    def warmaster_orchestrate(self, payload):
        return ArchiveHandler.warmaster_orchestrate(self, payload)

    def warmaster_start_response_status(self, accepted, initial_status=0, loop_status=0):
        return ArchiveHandler.warmaster_start_response_status(accepted, initial_status, loop_status)

    def warmaster_core_effect_start_ack(self, core_effect, dispatched, fallback_task_id=""):
        return ArchiveHandler.warmaster_core_effect_start_ack(self, core_effect, dispatched, fallback_task_id)

    def warmaster_run_as_agent_task(self, run, active=False, final_text="", activity=None):
        return ArchiveHandler.warmaster_run_as_agent_task(self, run, active=active, final_text=final_text, activity=activity)


def main() -> int:
    committed_decisions = []
    original_find_pending = archive_ops.find_pending_decision
    original_decision_proxy = archive_ops.proxy_json_url
    original_commit_answer = archive_ops.commit_answer_result
    original_mark_delivered = archive_ops.mark_delivered
    archive_ops.find_pending_decision = lambda _task_id: {
        "task_id": "lost-clarification-ack",
        "question": "Which option?",
    }
    archive_ops.commit_answer_result = lambda request_id, **kwargs: committed_decisions.append(
        {"request_id": request_id, **kwargs}
    ) or True
    archive_ops.mark_delivered = lambda _report_ids: None

    def lost_ack_proxy(method, url, **_kwargs):
        if method == "POST":
            raise RuntimeError("response was lost")
        return 200, {"status": "running", "snapshot": {"summary": {"status": "running"}}}

    archive_ops.proxy_json_url = lost_ack_proxy
    try:
        reconciled_answer = archive_ops.resume_pending_decision("lost-clarification-ack", "option A")
    finally:
        archive_ops.find_pending_decision = original_find_pending
        archive_ops.proxy_json_url = original_decision_proxy
        archive_ops.commit_answer_result = original_commit_answer
        archive_ops.mark_delivered = original_mark_delivered
    if (
        reconciled_answer.get("status") != "resumed_reconciled"
        or len(committed_decisions) != 1
        or committed_decisions[0].get("task_id") != "lost-clarification-ack"
        or committed_decisions[0].get("clear_pending") is not True
    ):
        raise AssertionError(f"lost clarification ACK was not reconciled from durable run state: {reconciled_answer!r}")

    start_ack = ArchiveHandler.warmaster_loop_started_or_active
    if start_ack(None, 200, {"ok": False, "task_id": "strict"}, "strict"):
        raise AssertionError("HTTP 200 with ok=false was accepted as a started run")
    if start_ack(None, 202, {"ok": True, "task_id": "other", "status": "started"}, "strict"):
        raise AssertionError("start acknowledgement for another task was accepted")
    if not start_ack(None, 202, {"ok": True, "task_id": "strict", "status": "started"}, "strict"):
        raise AssertionError("strict start acknowledgement was rejected")
    if not start_ack(None, 409, {"ok": False, "task_id": "strict", "error": "run already active"}, "strict"):
        raise AssertionError("identity-bound already-active acknowledgement was rejected")
    if start_ack(None, 202, {"ok": True, "active": True}, "strict"):
        raise AssertionError("active acknowledgement without task identity was accepted")
    if start_ack(None, 202, {"ok": True, "core_owned": True, "auto_start": True}, "strict"):
        raise AssertionError("Core-owned acknowledgement without task identity/state was accepted")
    if not start_ack(
        None,
        202,
        {"ok": True, "core_owned": True, "auto_start": True, "task_id": "strict", "status": "started"},
        "strict",
    ):
        raise AssertionError("identity-bound Core-owned start acknowledgement was rejected")

    core_effect = {"id": "effect-1", "payload": {"task_id": "strict"}}
    wrong_core_dispatch = {
        "effect": {
            "state": "delivered",
            "result": {
                "ok": True,
                "delegate_ref": "wrong",
                "status": "failed",
                "evidence": {"http_status": 202},
            },
        },
    }
    expected_id, core_status, core_ack = ArchiveHandler.warmaster_core_effect_start_ack(
        None,
        core_effect,
        wrong_core_dispatch,
        "fallback",
    )
    if expected_id != "strict" or start_ack(None, core_status, core_ack, expected_id):
        raise AssertionError(f"Core result for another failed task was fabricated as started: {core_ack!r}")
    valid_core_dispatch = {
        "effect": {
            "state": "delivered",
            "result": {
                "ok": True,
                "delegate_ref": "strict",
                "status": "running",
                "evidence": {"http_status": 202},
            },
        },
    }
    expected_id, core_status, core_ack = ArchiveHandler.warmaster_core_effect_start_ack(
        None,
        core_effect,
        valid_core_dispatch,
        "fallback",
    )
    if not start_ack(None, core_status, core_ack, expected_id):
        raise AssertionError(f"actual identity-bound Core acknowledgement was rejected: {core_ack!r}")

    response_status = ArchiveHandler.warmaster_start_response_status
    if response_status(True, 503, 0) != 202:
        raise AssertionError("accepted start did not map to HTTP 202")
    if response_status(False, 503, 0) != 503 or response_status(False, 200, 0) != 409:
        raise AssertionError("upstream failure status was masked or ambiguous success was not rejected")

    outcome_handler = FakeArchiveHandler("/")
    original_proxy = archive_handler.proxy_json_url
    original_upsert = archive_handler.upsert_pending_decision
    stored_preflight = []
    preflight_payload = {
        "ok": False,
        "needs_user": True,
        "task_id": "preflight-choice",
        "decision_request": {
            "kind": "decision_request",
            "task_id": "preflight-choice",
            "problem": "Нужно выбрать формат результата",
            "question": "APK или исходники?",
        },
    }

    def preflight_conflict(*_args, **_kwargs):
        body = json.dumps(preflight_payload, ensure_ascii=False).encode("utf-8")
        raise HTTPError("http://warmaster/orchestrate_run", 409, "Conflict", {}, io.BytesIO(body))

    try:
        archive_handler.proxy_json_url = preflight_conflict
        archive_handler.upsert_pending_decision = lambda request: stored_preflight.append(request) or True
        preflight_status, preflight_response = outcome_handler.warmaster_orchestrate({"task_id": "preflight-choice"})
        preflight_message = outcome_handler.warmaster_start_outcome_message(
            "build the app",
            "preflight-choice",
            False,
            preflight_response,
        )
    finally:
        archive_handler.proxy_json_url = original_proxy
        archive_handler.upsert_pending_decision = original_upsert
    if preflight_status != 409 or not stored_preflight or "APK или исходники?" not in preflight_message:
        raise AssertionError(
            f"initial preflight 409 bypassed decision storage/rendering: "
            f"{preflight_status}, {preflight_response!r}, {preflight_message!r}"
        )
    preflight_resume = stored_preflight[-1].get("resume") if isinstance(stored_preflight[-1], dict) else {}
    if (
        preflight_resume.get("kind") != "retry_preflight_with_answer"
        or preflight_resume.get("path") != "/orchestrate_run"
        or (preflight_resume.get("body") or {}).get("task_id") != "preflight-choice"
        or (preflight_resume.get("body") or {}).get("message") != "build the app"
    ):
        raise AssertionError(f"preflight decision has no executable same-run resume contract: {preflight_resume!r}")
    synthetic_sources = (
        inspect.getsource(ArchiveHandler.run_mobile_warmaster_payload),
        inspect.getsource(archive_ops.run_mobile_chat_payload),
    )
    if any('"state": "retry_wait"' in source for source in synthetic_sources):
        raise AssertionError("an unconfirmed Core transport exception still fabricates retry_wait")

    rejected_start = outcome_handler.warmaster_start_outcome_message(
        "build the app",
        "rejected-start",
        False,
        {"error": "strict start acknowledgement is missing"},
    )
    if "Работа запущена" in rejected_start or rejected_start.startswith("Принял"):
        raise AssertionError(f"rejected start was reported as accepted: {rejected_start!r}")
    accepted_start = outcome_handler.warmaster_start_outcome_message(
        "build the app",
        "accepted-start",
        True,
        {"ok": True},
    )
    if not accepted_start.startswith("Принял"):
        raise AssertionError(f"accepted start lost its acknowledgement: {accepted_start!r}")
    acceptance = ArchiveHandler.warmaster_acceptance_message(None, "public-name-test")
    forbidden = ("Абаддон", "Вармастер", "Warmaster", "task_id", "public-name-test")
    if any(item in acceptance for item in forbidden) or not acceptance.startswith("Принял"):
        raise AssertionError(f"acceptance message leaked internal anatomy: {acceptance!r}")
    accepted = final_message(
        {
            "task_id": STATIC_TASK_ID,
            "status": "completed",
            "summary": {
                "task_id": STATIC_TASK_ID,
                "status": "completed",
                "mission_ref": {"mission_id": MISSION_ID},
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
            "task_id": STATIC_TASK_ID,
            "status": "completed",
            "snapshot": {
                "summary": {
                    "task_id": STATIC_TASK_ID,
                    "status": "completed",
                    "mission_ref": {"mission_id": MISSION_ID},
                    "mission_protocol": accepted_protocol_history("Финал для доставки из фонового журнала."),
                }
            },
            "display": {"detail": "служебный completed detail"},
            "final": {"deliverable": "fallback deliverable"},
        }
    )
    if journal_accepted != "Финал для доставки из фонового журнала.":
        raise AssertionError(f"journal final_response was not preferred: {journal_accepted!r}")
    queued_reports = []
    final_chat_messages = []
    conveyed_reports = []
    original_fetch = task_journal.fetch_orchestration
    original_enqueue = task_journal.enqueue_report
    original_mark = task_journal.mark_delivered
    original_append = archive_ops.append_chat_message
    task_journal.fetch_orchestration = lambda task_id: {
        "task_id": task_id,
        "status": "completed",
            "snapshot": {
                "summary": {
                    "task_id": task_id,
                    "status": "completed",
                    "mission_ref": {"mission_id": MISSION_ID},
                    "mission_protocol": accepted_protocol(f"Доставленный финал {task_id}."),
                }
            },
    }
    task_journal.enqueue_report = lambda *args, **kwargs: queued_reports.append({"args": args, "kwargs": kwargs}) or 777
    task_journal.mark_delivered = lambda ids: conveyed_reports.extend(ids) or len(ids)
    archive_ops.append_chat_message = lambda *args, **kwargs: final_chat_messages.append(
        {"args": args, "kwargs": kwargs}
    ) or 991
    try:
        if not deliver_final_to_chat("task-final-delivery"):
            raise AssertionError("deliver_final_to_chat returned false for completed final_response")
    finally:
        task_journal.fetch_orchestration = original_fetch
        task_journal.enqueue_report = original_enqueue
        task_journal.mark_delivered = original_mark
        archive_ops.append_chat_message = original_append
    if len(queued_reports) != 1:
        raise AssertionError(f"final delivery queued unexpected reports: {queued_reports}")
    if queued_reports[0]["args"][:3] != ("warmaster", "task_completed", "готово: task-final-delivery"):
        raise AssertionError(f"final delivery queued wrong report header: {queued_reports}")
    if "Доставленный финал task-final-delivery." not in queued_reports[0]["args"][3]:
        raise AssertionError(f"final delivery queued wrong report body: {queued_reports}")
    if "Я закончил задачу" not in queued_reports[0]["args"][3] or any(
        item in queued_reports[0]["args"][3]
        for item in ("Абаддон", "Warmaster", "task_id", "idempotency")
    ):
        raise AssertionError(f"final delivery report leaked internal anatomy: {queued_reports}")
    if queued_reports[0]["kwargs"].get("dedupe_key") != "warmaster:task-final-delivery:final":
        raise AssertionError(f"final delivery did not use stable dedupe key: {queued_reports}")
    if (
        len(final_chat_messages) != 1
        or final_chat_messages[0]["args"][:2] != (task_journal.SHARED_CHAT_SESSION_ID, "assistant")
        or final_chat_messages[0]["kwargs"].get("dedupe_key")
        != "warmaster:task-final-delivery:final:chat"
    ):
        raise AssertionError(f"accepted final was not appended idempotently to shared chat: {final_chat_messages}")
    if conveyed_reports != [777]:
        raise AssertionError(f"visible final report was not marked conveyed: {conveyed_reports}")
    queued_reports.clear()
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
    task_journal.enqueue_report = lambda *args, **kwargs: queued_reports.append({"args": args, "kwargs": kwargs}) or 777
    try:
        if deliver_final_to_chat("task-without-final-response"):
            raise AssertionError("deliver_final_to_chat accepted completed run without protocol final_response")
    finally:
        task_journal.fetch_orchestration = original_fetch
        task_journal.enqueue_report = original_enqueue
    if queued_reports:
        raise AssertionError(f"legacy final payload was queued for chat: {queued_reports}")
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
    delivered = []
    original_proxy = archive_handler.proxy_json_url
    original_write = archive_handler.write_json
    original_archive_append = archive_handler.append_chat_message
    archive_handler.proxy_json_url = lambda *_args, **_kwargs: (
        200,
        {
            "task_id": "mobile-final-event",
            "active": False,
            "status": "completed",
            "snapshot": {
                "summary": {
                    "status": "completed",
                    "task_id": "mobile-final-event",
                    "mission_ref": {"mission_id": MISSION_ID},
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
    print("[ok] Archive Abaddon final-message gate and Warmaster protocol compatibility")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
