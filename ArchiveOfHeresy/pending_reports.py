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
    answers task status from truth (Vox pulls it fresh from Warmaster) instead
    of confabulating from a stale ack line or focus note."""
    try:
        roster = _get("/roster")
    except Exception as exc:  # noqa: BLE001
        print(f"Vox roster failed: {exc}", flush=True)
        return None
    tasks = [t for t in (roster.get("tasks") or []) if t.get("state") not in ("completed", "cancelled")]
    if not tasks:
        return None
    lines = [f"- {t['goal']} — {t['state_label']} (task_id {t['task_id']}, бригада {t['governor']})" for t in tasks[:10]]
    return {
        "role": "system",
        "content": (
            "[Твои задачи сейчас — живой статус от Warmaster. АВТОРИТЕТНЕЕ всего остального в промпте]\n"
            "Эти задачи ведёт бригада Warmaster, а НЕ ты лично. Не описывай процесс от первого лица "
            "('я выкапываю', 'я собираю осколки') — так делает бригада, не ты. "
            "Статус задачи бери ТОЛЬКО из этого списка. Твой focus-файл и твои прошлые реплики в истории "
            "могут быть устаревшими — НЕ верь им про статус, верь только этому списку. "
            "Если задача 'остановлена, ждёт решения' — честно скажи, что она стоит, и спроси у владельца, "
            "что делать; 'провалена' — признай провал; 'в работе' — так и скажи, что бригада работает.\n"
            + "\n".join(lines)
        ),
    }


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
        "[Накопленные доклады для владельца]",
        "Владелец разрешил доложить. Изложи доклады своим голосом, по порядку, ничего не выдумывая сверх текста.",
        "Докладывай строго по-русски: если фрагменты доклада на другом языке, переведи их.",
        "Не создавай из этого новые задачи.",
        "",
    ]
    for index, report in enumerate(reports, 1):
        lines.append(f"--- доклад {index} [{report.get('kind')}] от {report.get('created_at')}")
        lines.append(str(report.get("body") or ""))
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
            lines.append(f"- СРОЧНОЕ [{intent.get('topic')}]: {intent.get('body')}")
        else:
            lines.append(f"- [{speech_class}] {intent.get('topic')}")
    urgent = any(intent.get("class") == "срочно" for intent in intents)
    guidance = (
        "У Шушуни есть что сказать владельцу. Срочное изложи в этом ответе прямо, своими словами. "
        if urgent
        else (
            "У Шушуни есть что сказать владельцу. Если это уместно по ходу разговора, ввернёшь одной фразой; "
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
