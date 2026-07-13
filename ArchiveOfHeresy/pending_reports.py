"""Thin client over the Vox service — Shushunya's working memory of things not
yet said to the owner.

This module keeps its historical function names (call sites across the archive
still use them), but the state, the speech-class brain, the "к слову" relevance
and the conveyed/announced lifecycle all live in the Vox service now. Vox, not
the transport, decides what to say and when; this file only forwards.
"""
import json
import os
import urllib.request

from decision_requests import conversational_document, conversational_text

VOX_BASE_URL = os.environ.get("ARCHIVE_VOX_BASE_URL", "http://127.0.0.1:7400").rstrip("/")


def _get(path, timeout=30.0):
    request = urllib.request.Request(VOX_BASE_URL + path, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _post(path, payload, timeout=200.0):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(VOX_BASE_URL + path, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def enqueue_report(source, kind, topic, body, dedupe_key=None):
    """Register an intent to speak. Vox classifies it (срочно/важно/к слову/фон),
    writes the announce line, and embeds it for relevance — the source only
    supplies facts."""
    try:
        result = _post("/intent", {"source": source, "kind": kind, "topic": topic, "body": body, "dedupe_key": dedupe_key})
        return result.get("intent_id")
    except Exception as exc:  # noqa: BLE001 - a down Vox must not break the caller
        print(f"Vox enqueue failed: {exc}", flush=True)
        return None


def pending_reports(limit=20):
    """Everything open, full bodies — used when the owner asks to hear it all."""
    try:
        return _get("/deliverable").get("intents") or []
    except Exception as exc:  # noqa: BLE001
        print(f"Vox deliverable failed: {exc}", flush=True)
        return []


def pending_summary():
    """Indicator payload: count + server-composed announce line + topics."""
    try:
        summary = _get("/summary")
        return {"count": summary.get("count", 0), "announce": summary.get("announce", ""), "topics": summary.get("topics") or []}
    except Exception as exc:  # noqa: BLE001
        print(f"Vox summary failed: {exc}", flush=True)
        return {"count": 0, "announce": "", "topics": []}


def task_roster_note():
    """Live status of all brigade tasks, injected every chat turn so Shushunya
    answers task status from truth (Vox pulls it fresh from Abaddon) instead
    of confabulating from a stale ack line or focus note."""
    try:
        roster = _get("/roster")
    except Exception as exc:  # noqa: BLE001
        print(f"Vox roster failed: {exc}", flush=True)
        return None
    tasks = [t for t in (roster.get("tasks") or []) if t.get("state") not in ("completed", "cancelled")]
    if not tasks:
        return None
    lines = []
    for task in tasks[:10]:
        state = str(task.get("state") or "").lower()
        state_label = (
            "остановилась на внутренней проверке"
            if state == "blocked"
            else "ждёт моего ответа на отдельный вопрос"
            if state == "needs_user"
            else conversational_text(task.get("state_label"))
        )
        lines.append(f"- {conversational_text(task.get('goal'))} — {state_label}")
    return {
        "role": "system",
        "content": (
            "[Мои текущие дела — живой статус, авторитетнее старых реплик]\n"
            "Это твои дела и твоя ответственность. В обычном разговоре говори о них от первого лица: "
            "'я работаю', 'я закончил', 'мне нужен твой выбор'. Не раскрывай внутренние сервисы, исполнителей, "
            "идентификаторы или транспорт. Статус бери только из списка. Само слово 'остановлена' не означает, "
            "что нужен пользователь: спрашивай только когда рядом есть отдельный typed decision_request.\n"
            + "\n".join(lines)
        ),
    }


def continuable_tasks(limit=5):
    """Trusted failed/blocked mission identities exposed to Core for one turn.

    Vox owns the live roster, so a model cannot grant itself authority by
    printing a plausible task id.  ``needs_user`` is deliberately excluded:
    those runs must use the typed decision path instead of a generic retry.
    """
    try:
        roster = _get("/roster")
    except Exception as exc:  # noqa: BLE001
        print(f"Vox continuation roster failed: {exc}", flush=True)
        return []
    try:
        safe_limit = max(1, min(int(limit or 5), 12))
    except (TypeError, ValueError):
        safe_limit = 5
    result = []
    for item in roster.get("tasks") or []:
        if (
            not isinstance(item, dict)
            or item.get("active") is True
            or item.get("needs_user") is True
            or str(item.get("user_visible_state") or "").strip().lower()
            == "needs_user_decision"
        ):
            continue
        state = str(item.get("state") or "").strip().lower()
        if state == "needs_user" or state not in {"failed", "blocked", "quarantined"}:
            continue
        task_id = str(item.get("task_id") or "").strip()[:240]
        goal = str(item.get("goal") or "").strip()[:1_200]
        if not task_id or not goal:
            continue
        result.append(
            {
                "parent_task_id": task_id,
                "goal": goal,
                "state": state,
                "state_label": str(item.get("state_label") or state).strip()[:300],
                "failure_summary": str(item.get("state_label") or state).strip()[:1_200],
            }
        )
        if len(result) >= safe_limit:
            break
    return result


def register_push_token(token):
    """Forward the device's FCM token to Vox, which sends the real push."""
    try:
        return _post("/register-token", {"token": str(token or "").strip()}, timeout=15)
    except Exception as exc:  # noqa: BLE001
        print(f"Vox register-token failed: {exc}", flush=True)
        return {"ok": False, "error": str(exc)}


def phone_announce():
    """What the phone should buzz about right now. Vox decides which urgent
    intents are still unannounced and marks them announced server-side, so the
    phone keeps no state at all."""
    try:
        return _get("/announce")
    except Exception as exc:  # noqa: BLE001
        print(f"Vox announce failed: {exc}", flush=True)
        return {"ok": False, "notify": False, "notify_lines": [], "count": 0, "badge": ""}


def mark_delivered(report_ids):
    """The owner heard these: judged conveyed in the dialogue."""
    try:
        return _post("/conveyed", {"conveyed_ids": [int(i) for i in report_ids or []]}, timeout=30).get("conveyed", 0)
    except Exception as exc:  # noqa: BLE001
        print(f"Vox conveyed failed: {exc}", flush=True)
        return 0


def reports_event_text(reports):
    """Combined system-event text: Shushunya voices all open intents at once."""
    lines = [
        "[То, что я ещё не успел сказать]",
        "Изложи это своим голосом и от первого лица, по порядку, ничего не выдумывая сверх текста.",
        "Докладывай строго по-русски: если фрагменты доклада на другом языке, переведи их.",
        "Не называй внутренние сервисы, исполнителей, HTTP-коды, идентификаторы задач или ключи запросов.",
        "Не создавай из этого новые задачи.",
        "",
    ]
    for index, report in enumerate(reports, 1):
        lines.append(f"--- доклад {index} [{report.get('kind')}] от {report.get('created_at')}")
        lines.append(conversational_document(report.get("body") or ""))
        lines.append("")
    return "\n".join(lines).strip()


def pending_topics_note(context_text=""):
    """The "on the tongue" note for an ordinary turn. Instead of dumping every
    queued topic, it asks Vox what is on the tongue FOR THIS conversation:
    urgent/important always, 'к слову' only when semantically close."""
    try:
        result = _post("/on-tongue", {"context": context_text}, timeout=90)
    except Exception as exc:  # noqa: BLE001
        print(f"Vox on-tongue failed: {exc}", flush=True)
        return None
    intents = result.get("intents") or []
    if not intents:
        return None
    lines = []
    for intent in intents:
        speech_class = intent.get("class")
        if speech_class == "срочно":
            lines.append(
                f"- СРОЧНОЕ [{conversational_text(intent.get('topic'))}]: "
                f"{conversational_text(intent.get('body'))}"
            )
        else:
            lines.append(f"- [{speech_class}] {conversational_text(intent.get('topic'))}")
    urgent = any(intent.get("class") == "срочно" for intent in intents)
    guidance = (
        "У тебя есть что сказать собеседнику. Срочное изложи в этом ответе прямо, своими словами и от первого лица. "
        if urgent
        else (
            "У тебя есть что сказать собеседнику. Если это уместно по ходу разговора, ввернёшь одной фразой; "
            "если не к месту — промолчи, скажешь позже. Не пересказывай списком."
        )
    )
    return {
        "role": "system",
        "content": "[Vox — на языке у Шушуни]\n" + guidance + "\n" + "\n".join(lines),
        # Carried on the message so the background judge can mark which of these
        # actually sounded in the answer (conveyed); stripped before the prompt.
        "on_tongue": intents,
    }


def judge_conveyed(assistant_text, on_tongue):
    """After the answer, decide which on-tongue intents actually sounded in it,
    and mark those conveyed in Vox. One background LLM call — off the user's
    wait, alongside the librarian; conveyance is judged, never assumed."""
    if not assistant_text or not on_tongue:
        return
    import os as _os

    model = _os.environ.get("ARCHIVE_DEFAULT_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf")
    llm_base = _os.environ.get("ARCHIVE_LLM_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
    candidates = [{"id": i.get("id"), "topic": i.get("topic")} for i in on_tongue]
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты судья Vox. Дан ответ Шушуни владельцу и список тем, которые Шушуня держал 'на языке'. "
                    "Верни строгий JSON {\"conveyed_ids\":[...]} — id только тех тем, которые РЕАЛЬНО прозвучали в ответе "
                    "(были изложены или явно упомянуты владельцу). Если тема не прозвучала — не включай её."
                ),
            },
            {"role": "user", "content": json.dumps({"answer": assistant_text[:3000], "on_tongue": candidates}, ensure_ascii=False)},
        ],
        "temperature": 0,
        "max_tokens": 200,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(f"{llm_base}/v1/chat/completions", data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
        content = str(((result.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        if "{" in content:
            content = content[content.find("{") : content.rfind("}") + 1]
        conveyed_ids = [int(i) for i in (json.loads(content).get("conveyed_ids") or [])]
        if conveyed_ids:
            mark_delivered(conveyed_ids)
    except Exception as exc:  # noqa: BLE001 - a missed judgement just lets the intent linger
        print(f"Vox conveyance judge failed: {exc}", flush=True)
