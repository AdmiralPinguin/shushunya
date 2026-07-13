#!/usr/bin/env python3
"""Focused contracts for the ordinary-chat projection and decision resume."""
from __future__ import annotations

import tempfile
import sqlite3
from pathlib import Path

import archive_ops
import decision_requests
import task_journal
from turn_protocol import turn_capability_manifest


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def test_projection_hides_anatomy():
    request = decision_requests.normalize_decision_request(
        {
            "problem": "Abaddon вернул HTTP 409 для task_id=abc; Core исчерпал idempotency key=xyz",
            "what_tried": ["Skitarii повторил запрос"],
            "options": [{"id": "a", "label": "Взять вариант A", "effect": "быстрее"}],
            "recommendation": "вариант A",
            "question": "Берём A?",
        },
        task_id="abc",
    )
    rendered = decision_requests.render_decision_request(request)
    for forbidden in ("Abaddon", "HTTP", "task_id", "Core", "idempotency", "Skitarii"):
        require(forbidden.lower() not in rendered.lower(), f"ordinary chat leaked {forbidden}: {rendered}")
    require("Берём A?" in rendered and "Я бы выбрал" in rendered, "typed question lost useful content")


def test_projection_hides_inflected_anatomy_and_uses_first_person():
    request = decision_requests.normalize_decision_request(
        {
            "problem": (
                "Ответ Абаддона: HTTP 409 для task_id=abc; Цераксия ждёт работу бригады, "
                "а Core исчерпал idempotency key=xyz"
            ),
            "what_tried": ["Скитарии передали результат Искандару"],
            "options": [{"id": "a", "label": "Взять вариант A", "effect": "быстрее"}],
            "recommendation": "вариант A",
            "question": "Берём A?",
        },
        task_id="abc",
    )
    rendered = decision_requests.render_decision_request(request)
    for forbidden in (
        "abaddon",
        "абаддон",
        "http",
        "task_id",
        "core",
        "idempotency",
        "skitarii",
        "скитари",
        "церакси",
        "искандар",
        "бригад",
        "варбанд",
    ):
        require(forbidden not in rendered.lower(), f"ordinary chat leaked {forbidden}: {rendered}")
    require("Берём A?" in rendered and "Я бы выбрал" in rendered, "typed question lost useful content")
    require("У меня возникла проблема" in rendered, "decision problem is not voiced in first person")
    require("Я уже попробовал" in rendered, "attempts are not voiced in first person")

    document = decision_requests.conversational_document(
        "Ответ Абаддона передан Цераксии и бригаде; run_id=run-secret."
    )
    for forbidden in ("абаддон", "церакси", "бригад", "run_id", "run-secret"):
        require(forbidden not in document.lower(), f"document projection leaked {forbidden}: {document}")


def test_retry_projection_does_not_invent_autorepair():
    rendered = decision_requests.render_dispatch_retry(
        "Абаддон вернул HTTP 409; Core завершил effect_id=dead"
    )
    require("сам повторю" not in rendered.lower(), "failed effect invented an automatic retry")
    require("я не считаю её начатой" in rendered.lower(), "retry projection lost honest launch state")


def test_blocked_is_not_user_decision():
    original = task_journal.fetch_orchestration
    task_journal.fetch_orchestration = lambda _task_id: {
        "summary": {
            "status": "blocked",
            "result": {"needs_user": False, "reason": "snapshot failed"},
            "mission_protocol": {
                "worker_reports": [
                    {
                        "needs_user": True,
                        "question": "Старый вопрос из уже закрытого этапа?",
                    }
                ]
            },
        }
    }
    try:
        facts = task_journal.escalation_facts(
            "t-1",
            {"task_id": "t-1", "status": "blocked", "goal": "Сделай игру"},
        )
    finally:
        task_journal.fetch_orchestration = original
    require(facts["needs_user"] is False, "coarse blocked status escalated to user")
    require(facts["decision_request"] is None, "invented a decision request")


def test_explicit_decision_contract_is_preserved():
    original = task_journal.fetch_orchestration
    task_journal.fetch_orchestration = lambda _task_id: {
        "summary": {
            "status": "needs_user",
            "result": {
                "needs_user": True,
                "decision_request": {
                    "problem": "Нужен выбор движка",
                    "what_tried": ["Проверил оба доступных варианта"],
                    "options": ["Godot", "нативный Canvas"],
                    "recommendation": "Godot",
                    "question": "Какой движок выбираешь?",
                    "resume": {"method": "POST", "path": "/runs/t-2/clarification"},
                },
            },
            "mission_protocol": {},
        }
    }
    try:
        facts = task_journal.escalation_facts(
            "t-2",
            {"task_id": "t-2", "status": "needs_user", "goal": "Сделай игру"},
        )
    finally:
        task_journal.fetch_orchestration = original
    require(facts["needs_user"] is True, "explicit typed question was discarded")
    request = facts["decision_request"]
    require(request["question"] == "Какой движок выбираешь?", "question changed")
    require(len(request["options"]) == 2 and request["recommendation"] == "Godot", "options/recommendation changed")


def test_manifest_binds_resume_to_existing_request():
    pending = [
        {"task_id": "t-1", "question": "First?"},
        {"task_id": "t-2", "question": "Second?"},
        {"task_id": "t-3", "question": "A or B?", "decision_id": "decision-3"},
        {"task_id": "t-4", "question": "Last?"},
    ]
    manifest = turn_capability_manifest(pending_decisions=pending)
    action = next(item for item in manifest["capabilities"] if item["action"] == "answer_pending_decision")
    require(action["available"] is True, "resume action is not available")
    require(manifest["pending_decision_task_id"] == "t-4", "resume lost its latest task binding")
    require(action["question"] == "Last?", "resume lost its latest conversational question")
    require(
        [(item["task_id"], item["question"]) for item in action["pending_decisions"]]
        == [("t-2", "Second?"), ("t-3", "A or B?"), ("t-4", "Last?")],
        "bounded pending-decision identities/questions changed or kept the wrong window",
    )
    require(
        action["pending_decisions"][1]["decision_id"] == "decision-3",
        "exact decision version was not published to Core",
    )


def test_decision_is_proactively_delivered_once():
    with tempfile.TemporaryDirectory() as temp:
        original_path = decision_requests.STORE_PATH
        original_fetch = task_journal.fetch_orchestration
        original_enqueue = task_journal.enqueue_report
        original_mark = task_journal.mark_delivered
        original_append = archive_ops.append_chat_message
        decision_requests.STORE_PATH = Path(temp) / "pending.json"
        messages = []
        marked = []
        try:
            task_journal.fetch_orchestration = lambda _task_id: {
                "summary": {
                    "status": "needs_user",
                    "result": {
                        "needs_user": True,
                        "decision_request": {
                            "problem": "Нужно выбрать движок",
                            "options": ["Godot", "Canvas"],
                            "recommendation": "Godot",
                            "question": "Какой движок выбираешь?",
                        },
                    },
                    "mission_protocol": {},
                }
            }
            task_journal.enqueue_report = lambda *_args, **_kwargs: 23
            task_journal.mark_delivered = lambda ids: marked.extend(ids) or len(ids)
            archive_ops.append_chat_message = lambda *_args, **kwargs: messages.append(kwargs) or 91
            delivered = task_journal.deliver_escalation_to_chat(
                "t-5",
                {"task_id": "t-5", "status": "needs_user", "goal": "Сделай игру"},
                "task_blocked",
            )
        finally:
            decision_requests.STORE_PATH = original_path
            task_journal.fetch_orchestration = original_fetch
            task_journal.enqueue_report = original_enqueue
            task_journal.mark_delivered = original_mark
            archive_ops.append_chat_message = original_append
    require(delivered["ok"] is True and len(messages) == 1, "decision was not proactively appended once")
    require(marked == [23], "proactively visible intent was left pending in Vox")
    require(
        messages[0]["dedupe_key"].startswith("decision:")
        and messages[0]["dedupe_key"].endswith(":chat"),
        "chat delivery is not fingerprint-idempotent",
    )


def test_first_run_decision_retries_partial_chat_and_vox_delivery():
    originals = {
        "fetch_runs": task_journal.fetch_runs,
        "load_state": task_journal.load_state,
        "save_state": task_journal.save_state,
        "escalation_facts": task_journal.escalation_facts,
        "deliver": task_journal.deliver_escalation_to_chat,
        "mark": task_journal.mark_delivered,
    }
    current_state = {}
    attempts = []
    outcomes = [
        {
            "ok": False,
            "chat": False,
            "vox": True,
            "conveyed": False,
            "report_id": 71,
        },
        {
            "ok": False,
            "chat": True,
            "vox": False,
            "conveyed": False,
            "report_id": None,
        },
        {
            "ok": True,
            "chat": True,
            "vox": True,
            "conveyed": True,
            "report_id": 72,
        },
    ]
    facts = {
        "task_id": "t-retry",
        "goal": "Собрать игру",
        "status": "running",
        "needs_user": True,
        "decision_request": {
            "task_id": "t-retry",
            "question": "A или B?",
            "problem": "Нужен реальный выбор.",
            "options": [],
            "what_tried": [],
            "recommendation": "A",
            "resume": {},
        },
    }

    def save_state(value):
        nonlocal current_state
        import json

        current_state = json.loads(json.dumps(value))

    def deliver(*_args, **_kwargs):
        attempts.append(dict(_kwargs))
        return outcomes[len(attempts) - 1]

    try:
        task_journal.fetch_runs = lambda: [
            {"task_id": "t-retry", "status": "running", "goal": "Собрать игру"}
        ]
        task_journal.load_state = lambda: __import__("json").loads(
            __import__("json").dumps(current_state)
        )
        task_journal.save_state = save_state
        task_journal.escalation_facts = lambda *_args, **_kwargs: dict(facts)
        task_journal.deliver_escalation_to_chat = deliver
        task_journal.mark_delivered = lambda ids: len(ids)

        first = task_journal.poll_once()
        second = task_journal.poll_once()
        third = task_journal.poll_once()
        facts["decision_request"]["question"] = "B или C?"
        fourth = task_journal.poll_once()
    finally:
        task_journal.fetch_runs = originals["fetch_runs"]
        task_journal.load_state = originals["load_state"]
        task_journal.save_state = originals["save_state"]
        task_journal.escalation_facts = originals["escalation_facts"]
        task_journal.deliver_escalation_to_chat = originals["deliver"]
        task_journal.mark_delivered = originals["mark"]

    require(first["baseline"] is True, "first-run decision was not treated as a baseline")
    require(first["conversation_deliveries_pending"] == 1, "partial first delivery was not checkpointed")
    require(second["conversation_deliveries_pending"] == 0, "split chat/Vox success did not converge")
    require(third["conversation_deliveries_pending"] == 0, "completed checkpoint became pending again")
    require(fourth["conversation_deliveries_pending"] == 0, "changed decision did not deliver")
    require(len(attempts) == 3, "same-status retry or changed decision delivery count is wrong")
    require(
        attempts[0]["delivery_token"] == attempts[1]["delivery_token"],
        "retry changed the idempotency token",
    )
    require(
        attempts[2]["delivery_token"] != attempts[1]["delivery_token"],
        "changed decision reused the previous delivery token",
    )


def test_same_live_question_ignores_coarse_status_transition():
    base = {
        "task_id": "t-stable-question",
        "status": "running",
        "needs_user": True,
        "decision_request": {
            "task_id": "t-stable-question",
            "question": "A or B?",
            "problem": "A real choice",
            "options": ["A", "B"],
            "recommendation": "A",
            "resume": {"path": "/runs/t-stable-question/clarification"},
        },
    }
    running = task_journal.escalation_fingerprint(base, "decision_required")
    blocked = task_journal.escalation_fingerprint(
        {**base, "status": "blocked"},
        "decision_required",
    )
    require(running == blocked, "coarse running/blocked transition duplicated one live question")


def test_delivery_checkpoint_upgrade_does_not_replay_old_internal_blocks():
    originals = {
        "fetch_runs": task_journal.fetch_runs,
        "load_state": task_journal.load_state,
        "save_state": task_journal.save_state,
        "escalation_facts": task_journal.escalation_facts,
        "deliver": task_journal.deliver_escalation_to_chat,
        "clear": task_journal.clear_pending_decision,
    }
    current_state = {"legacy-block": "blocked"}
    attempts = []

    def save_state(value):
        nonlocal current_state
        import json

        current_state = json.loads(json.dumps(value))

    try:
        task_journal.fetch_runs = lambda: [
            {"task_id": "legacy-block", "status": "blocked", "goal": "Старая задача"}
        ]
        task_journal.load_state = lambda: __import__("json").loads(
            __import__("json").dumps(current_state)
        )
        task_journal.save_state = save_state
        task_journal.escalation_facts = lambda *_args, **_kwargs: {
            "task_id": "legacy-block",
            "goal": "Старая задача",
            "status": "blocked",
            "needs_user": False,
            "decision_request": None,
        }
        task_journal.deliver_escalation_to_chat = lambda *_args, **_kwargs: attempts.append(1)
        task_journal.clear_pending_decision = lambda *_args, **_kwargs: False

        first = task_journal.poll_once()
        second = task_journal.poll_once()
    finally:
        task_journal.fetch_runs = originals["fetch_runs"]
        task_journal.load_state = originals["load_state"]
        task_journal.save_state = originals["save_state"]
        task_journal.escalation_facts = originals["escalation_facts"]
        task_journal.deliver_escalation_to_chat = originals["deliver"]
        task_journal.clear_pending_decision = originals["clear"]

    require(first["baseline"] is False, "legacy task state was mistaken for an empty journal")
    require(second["conversation_deliveries_pending"] == 0, "suppressed baseline became pending")
    require(not attempts, "upgrade replayed an old internal block into chat/Vox")


def test_resume_clears_only_after_acceptance():
    with tempfile.TemporaryDirectory() as temp:
        original_path = decision_requests.STORE_PATH
        original_proxy = archive_ops.proxy_json_url
        original_mark = archive_ops.mark_delivered
        decision_requests.STORE_PATH = Path(temp) / "pending.json"
        marked = []
        calls = []
        try:
            decision_requests.upsert_pending(
                {
                    "task_id": "t-4",
                    "question": "A или B?",
                    "vox_intent_id": 17,
                    "resume": {
                        "kind": "retry_preflight_with_answer",
                        "method": "POST",
                        "path": "/orchestrate_run",
                        "body": {"task_id": "t-4", "message": "Собери игру"},
                    },
                }
            )
            def accepted(*args, **kwargs):
                calls.append((args, kwargs))
                return 200, {"ok": True, "status": "running"}

            archive_ops.proxy_json_url = accepted
            archive_ops.mark_delivered = lambda ids: marked.extend(ids)
            result = archive_ops.resume_pending_decision("t-4", "A")
            require(result["ok"] is True, "accepted answer did not resume")
            require(decision_requests.find_pending("t-4") is None, "accepted decision stayed pending")
            require(marked == [17], "Vox intent was not closed")
            require(calls[0][0][1].endswith("/orchestrate_run"), "preflight answer used a missing run endpoint")
            sent = calls[0][1]["payload"]
            require(sent["task_id"] == "t-4", "preflight resume changed task identity")
            require("Собери игру" in sent["message"] and sent["message"].endswith("A"), "answer was not bound to the original request")
        finally:
            decision_requests.STORE_PATH = original_path
            archive_ops.proxy_json_url = original_proxy
            archive_ops.mark_delivered = original_mark


def test_preflight_answer_replaces_pending_question():
    with tempfile.TemporaryDirectory() as temp:
        original_path = decision_requests.STORE_PATH
        original_proxy = archive_ops.proxy_json_url
        original_mark = archive_ops.mark_delivered
        decision_requests.STORE_PATH = Path(temp) / "pending.json"
        calls = []
        marked = []
        try:
            decision_requests.upsert_pending(
                {
                    "task_id": "t-multistep",
                    "question": "A или B?",
                    "vox_intent_id": 23,
                    "resume": {
                        "kind": "retry_preflight_with_answer",
                        "method": "POST",
                        "path": "/orchestrate_run",
                        "body": {"task_id": "t-multistep", "message": "Собери игру"},
                    },
                }
            )

            def ask_second(*args, **kwargs):
                calls.append((args, kwargs))
                return 409, {
                    "ok": False,
                    "needs_user": True,
                    "decision_request": {
                        "task_id": "t-multistep",
                        "problem": "Нужно выбрать формат",
                        "question": "APK или исходники?",
                        "options": ["APK", "исходники"],
                    },
                }

            archive_ops.proxy_json_url = ask_second
            archive_ops.mark_delivered = lambda ids: marked.extend(ids)
            result = archive_ops.resume_pending_decision(
                "t-multistep",
                "A",
                request_id="answer-A-request",
            )
            pending = decision_requests.find_pending("t-multistep")
            require(result["status"] == "needs_another_decision", "second preflight question was hidden")
            require(pending["question"] == "APK или исходники?", "old pending question was not replaced")
            require(pending["resume"]["kind"] == "retry_preflight_with_answer", "replacement lost preflight route")
            require(pending["resume"]["body"]["message"].endswith("A"), "first answer was not accumulated")
            require(marked == [23], "old decision notification stayed open after replacement")

            calls_before_replay = len(calls)
            replay = archive_ops.resume_pending_decision(
                "t-multistep",
                "A",
                request_id="answer-A-request",
            )
            require(replay.get("idempotent_replay") is True, "replayed answer did not use its durable receipt")
            require(replay.get("status") == "needs_another_decision", "replayed A consumed question B")
            require(len(calls) == calls_before_replay, "replayed answer reached the backend again")
            require(
                decision_requests.find_pending("t-multistep")["question"] == "APK или исходники?",
                "replayed answer replaced or cleared question B",
            )

            def accepted(*args, **kwargs):
                calls.append((args, kwargs))
                return 200, {"ok": True, "task_id": "t-multistep", "status": "running"}

            archive_ops.proxy_json_url = accepted
            finished = archive_ops.resume_pending_decision(
                "t-multistep",
                "APK",
                request_id="answer-B-request",
            )
            require(finished["ok"] is True, "second preflight answer did not resume")
            require(decision_requests.find_pending("t-multistep") is None, "replacement stayed pending")
            final_message = calls[-1][1]["payload"]["message"]
            require("A" in final_message and final_message.endswith("APK"), "multi-step answers were not accumulated")
        finally:
            decision_requests.STORE_PATH = original_path
            archive_ops.proxy_json_url = original_proxy
            archive_ops.mark_delivered = original_mark


def test_lost_answer_ack_distinguishes_failure_from_active_run():
    with tempfile.TemporaryDirectory() as temp:
        original_path = decision_requests.STORE_PATH
        original_proxy = archive_ops.proxy_json_url
        decision_requests.STORE_PATH = Path(temp) / "pending.json"
        try:
            decision_requests.upsert_pending({"task_id": "t-failed-ack", "question": "A или B?"})

            def failed_after_post(method, _url, **_kwargs):
                if method == "POST":
                    raise RuntimeError("lost response")
                return 200, {"status": "failed", "snapshot": {"summary": {"status": "failed"}}}

            archive_ops.proxy_json_url = failed_after_post
            result = archive_ops.resume_pending_decision("t-failed-ack", "A")
            require(result["ok"] is False, "failed run was falsely reconciled as resumed")
            require(result["status"] == "resume_terminal", "failed run was not classified as terminal")
            require(decision_requests.find_pending("t-failed-ack") is None, "stale terminal question stayed pending")

            decision_requests.upsert_pending({"task_id": "t-unknown-ack", "question": "A или B?"})
            archive_ops.proxy_json_url = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline"))
            unknown = archive_ops.resume_pending_decision("t-unknown-ack", "B")
            require(unknown["ok"] is False, "unreadable run was falsely reconciled")
            require(decision_requests.find_pending("t-unknown-ack") is not None, "unconfirmed question was discarded")
            require("я повтор" not in unknown["message"].lower(), "response promised a retry that is not durable")
        finally:
            decision_requests.STORE_PATH = original_path
            archive_ops.proxy_json_url = original_proxy


def test_lost_ack_advances_to_authoritative_next_question_once():
    with tempfile.TemporaryDirectory() as temp:
        original_path = decision_requests.STORE_PATH
        original_proxy = archive_ops.proxy_json_url
        decision_requests.STORE_PATH = Path(temp) / "pending.json"
        posts = []
        try:
            decision_requests.upsert_pending(
                {
                    "task_id": "t-lost-b",
                    "question": "A?",
                    "resume": {
                        "kind": "retry_preflight_with_answer",
                        "method": "POST",
                        "path": "/orchestrate_run",
                        "body": {"task_id": "t-lost-b", "message": "Собери игру"},
                    },
                }
            )

            def lost_b_ack(method, _url, **_kwargs):
                if method == "POST":
                    posts.append(1)
                    raise RuntimeError("answer A ACK was lost")
                return 200, {
                    "status": "running",
                    "mission_state": {
                        "status": "needs_user",
                        "needs_user": True,
                        "user_visible_state": "needs_user_decision",
                    },
                    "snapshot": {
                        "summary": {
                            "status": "running",
                            "result": {
                                "status": "needs_user",
                                "needs_user": True,
                                "decision_request": {
                                    "kind": "decision_request",
                                    "task_id": "t-lost-b",
                                    "problem": "Нужен следующий выбор",
                                    "question": "B?",
                                },
                            },
                        }
                    },
                }

            archive_ops.proxy_json_url = lost_b_ack
            first = archive_ops.resume_pending_decision(
                "t-lost-b",
                "answer-A",
                request_id="lost-A-request",
            )
            pending = decision_requests.find_pending("t-lost-b")
            receipt = decision_requests.find_answer_receipt("lost-A-request", "t-lost-b")
            require(first.get("status") == "needs_another_decision", "authoritative B was not recovered")
            require(pending and pending.get("question") == "B?", "pending A was not atomically replaced by B")
            require(receipt is not None, "lost A ACK did not checkpoint its replay receipt")
            require(len(posts) == 1, "first answer was posted more than once")

            replay = archive_ops.resume_pending_decision(
                "t-lost-b",
                "answer-A",
                request_id="lost-A-request",
            )
            require(replay.get("idempotent_replay") is True, "lost-ACK replay missed its receipt")
            require(replay.get("status") == "needs_another_decision", "replayed A consumed B")
            require(len(posts) == 1, "replayed A reached the backend")
            require(decision_requests.find_pending("t-lost-b").get("question") == "B?", "replay changed B")
        finally:
            decision_requests.STORE_PATH = original_path
            archive_ops.proxy_json_url = original_proxy


def test_lost_ack_and_unreadable_state_reserves_before_replay():
    with tempfile.TemporaryDirectory() as temp:
        original_path = decision_requests.STORE_PATH
        original_proxy = archive_ops.proxy_json_url
        decision_requests.STORE_PATH = Path(temp) / "pending.json"
        posts = []
        gets = []
        state = {"readable": False}
        try:
            decision_requests.upsert_pending(
                {
                    "task_id": "t-double-loss",
                    "question": "A?",
                    "resume": {
                        "kind": "retry_preflight_with_answer",
                        "method": "POST",
                        "path": "/orchestrate_run",
                        "body": {"task_id": "t-double-loss", "message": "Собери игру"},
                    },
                }
            )

            def double_loss_then_b(method, _url, **_kwargs):
                if method == "POST":
                    posts.append(_kwargs.get("payload"))
                    if len(posts) > 1:
                        raise AssertionError("reserved answer reached backend twice")
                    raise RuntimeError("answer A ACK was lost")
                gets.append(1)
                if not state["readable"]:
                    raise RuntimeError("authoritative state is temporarily unreadable")
                return 200, {
                    "status": "running",
                    "mission_state": {
                        "status": "needs_user",
                        "needs_user": True,
                        "user_visible_state": "needs_user_decision",
                    },
                    "snapshot": {
                        "summary": {
                            "status": "running",
                            "result": {
                                "status": "needs_user",
                                "needs_user": True,
                                "decision_request": {
                                    "kind": "decision_request",
                                    "task_id": "t-double-loss",
                                    "problem": "Нужен следующий выбор",
                                    "question": "B?",
                                },
                            },
                        }
                    },
                }

            archive_ops.proxy_json_url = double_loss_then_b
            first = archive_ops.resume_pending_decision(
                "t-double-loss",
                "answer-A",
                request_id="double-loss-request",
            )
            receipt = decision_requests.find_answer_receipt(
                "double-loss-request", "t-double-loss"
            )
            require(first.get("status") == "answer_reconcile_pending", "double loss claimed answer success")
            require(first.get("retryable") is True, "ambiguous answer is not honestly retryable")
            require(receipt and receipt.get("state") == "reconcile_pending", "pre-send reservation was lost")
            require(decision_requests.find_pending("t-double-loss").get("question") == "A?", "unknown state consumed A")
            require(len(posts) == 1, "first answer was posted more than once")

            state["readable"] = True
            replay = archive_ops.resume_pending_decision(
                "t-double-loss",
                "answer-A",
                request_id="double-loss-request",
            )
            completed_receipt = decision_requests.find_answer_receipt(
                "double-loss-request", "t-double-loss"
            )
            require(replay.get("status") == "needs_another_decision", "reconcile did not recover B")
            require(replay.get("idempotent_replay") is True, "reconcile was not marked idempotent")
            require(len(posts) == 1, "same request id posted A a second time")
            require(decision_requests.find_pending("t-double-loss").get("question") == "B?", "B was not installed")
            require(completed_receipt.get("state") == "completed", "reconcile did not finish receipt")

            gets_before_final_replay = len(gets)
            final_replay = archive_ops.resume_pending_decision(
                "t-double-loss",
                "answer-A",
                request_id="double-loss-request",
            )
            require(final_replay.get("idempotent_replay") is True, "completed receipt did not replay")
            require(len(posts) == 1, "completed receipt reached backend")
            require(len(gets) == gets_before_final_replay, "completed receipt reconciled unnecessarily")
        finally:
            decision_requests.STORE_PATH = original_path
            archive_ops.proxy_json_url = original_proxy


def test_resume_reconciliation_prefers_current_durable_state():
    original_proxy = archive_ops.proxy_json_url
    try:
        cases = (
            ({"status": "running", "mission_state": {"status": "failed"}}, "terminal"),
            ({"status": "running", "mission_state": {"status": "revision"}}, "internal"),
            ({"status": "running", "mission_state": {"status": "completed"}}, "completed"),
            ({"status": "accepted"}, "active"),
            ({"status": "corrupt"}, "terminal"),
            ({"status": "preflight_failed"}, "terminal"),
            ({"status": "interrupted"}, "internal"),
            (
                {
                    "status": "failed",
                    "needs_user": False,
                    "snapshot": {
                        "summary": {
                            "status": "failed",
                            "mission_protocol": {
                                "old_worker_report": {
                                    "needs_user": True,
                                    "question": "Старый исторический вопрос?",
                                }
                            },
                        }
                    },
                },
                "terminal",
            ),
        )
        for payload, expected in cases:
            archive_ops.proxy_json_url = lambda *_args, _payload=payload, **_kwargs: (200, _payload)
            actual = archive_ops._run_resume_state("t-authority")
            require(
                actual.get("kind") == expected,
                f"coarse/stale state overrode current durable state: {payload} -> {actual}",
            )
    finally:
        archive_ops.proxy_json_url = original_proxy


def test_failed_turn_job_reopens_same_identity():
    original_path = archive_ops.SQLITE_PATH
    with tempfile.TemporaryDirectory() as temp:
        archive_ops.SQLITE_PATH = Path(temp) / "archive.sqlite3"
        try:
            with sqlite3.connect(archive_ops.SQLITE_PATH) as db:
                db.execute(
                    "CREATE TABLE mobile_jobs ("
                    "id TEXT PRIMARY KEY,type TEXT,status TEXT,created_at TEXT,updated_at TEXT,"
                    "request_json TEXT,response_json TEXT,error TEXT)"
                )
            payload = {
                "client_request_id": "android-recovery-fixed",
                "session_id": "shushunya",
                "text": "ответь мне",
                "client_source": "app",
                "artifact_audience_source": "app",
            }
            job_id, created, status = archive_ops.create_mobile_turn_job_once(dict(payload))
            require(created is True and status == "queued", "initial turn was not created")
            archive_ops.update_mobile_job(job_id, "failed", error="temporary LLM 503")

            retried_id, retried, retried_status = archive_ops.create_mobile_turn_job_once(dict(payload))
            snapshot = archive_ops.mobile_job_snapshot(job_id)
            require(retried_id == job_id, "recovery changed the durable turn identity")
            require(retried is True and retried_status == "queued", "failed turn did not reopen")
            require(snapshot.get("status") == "queued", "reopened turn was not queued")
            require(not snapshot.get("error"), "reopened turn kept a terminal error")

            duplicate_id, duplicate_created, duplicate_status = archive_ops.create_mobile_turn_job_once(dict(payload))
            require(duplicate_id == job_id, "concurrent recovery changed identity")
            require(duplicate_created is False and duplicate_status == "queued", "queued recovery ran twice")
        finally:
            archive_ops.SQLITE_PATH = original_path


def test_completed_delivery_retries_until_chat_and_vox_are_durable():
    originals = {
        "fetch_runs": task_journal.fetch_runs,
        "load_state": task_journal.load_state,
        "save_state": task_journal.save_state,
        "remember": task_journal.remember_entry,
        "deliver": task_journal._deliver_final_event,
        "publish": task_journal.publish_completed_artifacts,
        "clear": task_journal.clear_pending_decision,
        "mark": task_journal.mark_delivered,
    }
    state = {
        "completed-retry": "running",
        task_journal.ARTIFACT_PUBLICATIONS_STATE_KEY: {},
        task_journal.CONVERSATION_DELIVERIES_STATE_KEY: {},
    }
    attempts = []
    outcomes = [
        {"ok": False, "chat": True, "vox": False, "conveyed": False, "report_id": None},
        {"ok": True, "chat": True, "vox": True, "conveyed": True, "report_id": 88},
    ]

    def load_state():
        import json

        return json.loads(json.dumps(state))

    def save_state(value):
        nonlocal state
        import json

        state = json.loads(json.dumps(value))

    def deliver(*_args, **_kwargs):
        attempts.append(1)
        return outcomes[len(attempts) - 1]

    try:
        task_journal.fetch_runs = lambda: [
            {"task_id": "completed-retry", "status": "completed", "goal": "собрать результат"}
        ]
        task_journal.load_state = load_state
        task_journal.save_state = save_state
        task_journal.remember_entry = lambda *_args, **_kwargs: None
        task_journal._deliver_final_event = deliver
        task_journal.publish_completed_artifacts = lambda *_args, **_kwargs: {
            "changed": False,
            "bytes": 0,
            "attempted": 0,
            "published": 0,
            "complete": True,
            "errors": 0,
            "notices": [],
        }
        task_journal.clear_pending_decision = lambda *_args, **_kwargs: False
        task_journal.mark_delivered = lambda ids: len(ids)

        first = task_journal.poll_once()
        checkpoint_after_first = dict(
            state[task_journal.CONVERSATION_DELIVERIES_STATE_KEY]["completed-retry"]
        )
        second = task_journal.poll_once()
        checkpoint_after_second = state[task_journal.CONVERSATION_DELIVERIES_STATE_KEY]["completed-retry"]
    finally:
        task_journal.fetch_runs = originals["fetch_runs"]
        task_journal.load_state = originals["load_state"]
        task_journal.save_state = originals["save_state"]
        task_journal.remember_entry = originals["remember"]
        task_journal._deliver_final_event = originals["deliver"]
        task_journal.publish_completed_artifacts = originals["publish"]
        task_journal.clear_pending_decision = originals["clear"]
        task_journal.mark_delivered = originals["mark"]

    require(first["conversation_deliveries_pending"] == 1, "partial final delivery was lost")
    require(second["conversation_deliveries_pending"] == 0, "final delivery did not converge")
    require(len(attempts) == 2, "incomplete final was not retried exactly once")
    require(checkpoint_after_first["delivery_token"] == checkpoint_after_second["delivery_token"], "retry changed final identity")
    require(checkpoint_after_second["complete"] is True, "final checkpoint did not complete")


def test_escalation_detail_error_preserves_pending_decision():
    originals = {
        "fetch_runs": task_journal.fetch_runs,
        "load_state": task_journal.load_state,
        "save_state": task_journal.save_state,
        "facts": task_journal.escalation_facts,
        "clear": task_journal.clear_pending_decision,
        "deliver": task_journal.deliver_escalation_to_chat,
    }
    cleared = []
    state = {
        "detail-error": "blocked",
        task_journal.ARTIFACT_PUBLICATIONS_STATE_KEY: {},
        task_journal.CONVERSATION_DELIVERIES_STATE_KEY: {},
    }
    try:
        task_journal.fetch_runs = lambda: [
            {"task_id": "detail-error", "status": "blocked", "goal": "живая задача"}
        ]
        task_journal.load_state = lambda: __import__("json").loads(__import__("json").dumps(state))
        task_journal.save_state = lambda _value: None
        task_journal.escalation_facts = lambda *_args, **_kwargs: {
            "task_id": "detail-error",
            "goal": "живая задача",
            "status": "blocked",
            "detail_error": "temporary orchestration timeout",
        }
        task_journal.clear_pending_decision = lambda task_id: cleared.append(task_id) or True
        task_journal.deliver_escalation_to_chat = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unreadable escalation was delivered")
        )

        result = task_journal.poll_once()
    finally:
        task_journal.fetch_runs = originals["fetch_runs"]
        task_journal.load_state = originals["load_state"]
        task_journal.save_state = originals["save_state"]
        task_journal.escalation_facts = originals["facts"]
        task_journal.clear_pending_decision = originals["clear"]
        task_journal.deliver_escalation_to_chat = originals["deliver"]

    require(not cleared, "transient detail_error cleared the durable pending question")
    require(result["conversation_deliveries_pending"] == 1, "unreadable escalation was not kept retryable")


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"conversation projection self-test: ok ({len(tests)} tests)")


if __name__ == "__main__":
    main()
