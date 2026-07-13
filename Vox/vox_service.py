#!/usr/bin/env python3
"""Vox — Shushunya's working memory of things not yet said to the owner.

Not a notification queue: intents to speak. Each intent is classified by the
Vox brain (LLM) into a speech class, carries a model-written announce line for
the phone, and lives through a judged lifecycle:

    open -> mentioned -> conveyed / closed

"Conveyed" means it actually sounded in the dialogue (judged by the Librarian
after the turn), never that some transport downloaded it. Nothing expires by
timer: an intent dies only meaningfully — conveyed, superseded by a newer
intent with the same dedupe key, or closed by its source.

Speech classes drive behaviour:
    срочно   — announce to the phone immediately, always on the tongue
    важно    — on the tongue at the next contact, badge only
    к слову  — on the tongue only when semantically close to the conversation
    фон      — only on explicit "расскажи, что накопилось"
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("VOX_DB_PATH", ROOT / "runtime" / "vox.sqlite3"))
DEFAULT_PORT = int(os.environ.get("VOX_PORT", "7400"))
LLM_BASE_URL = os.environ.get("VOX_LLM_BASE_URL", "http://127.0.0.1:8079").rstrip("/")
LLM_MODEL = os.environ.get("VOX_LLM_MODEL", os.environ.get("ARCHIVE_DEFAULT_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf"))
EMBED_BASE_URL = os.environ.get("VOX_EMBED_BASE_URL", "http://127.0.0.1:8181").rstrip("/")
EMBED_MODEL = os.environ.get("VOX_EMBED_MODEL", "multilingual-e5-large")
WARMASTER_BASE_URL = os.environ.get("VOX_WARMASTER_BASE_URL", "http://127.0.0.1:7000").rstrip("/")
FCM_SERVICE_ACCOUNT = os.environ.get("VOX_FCM_SERVICE_ACCOUNT", str(ROOT / "firebase-service-account.json"))
RELEVANCE_MIN = float(os.environ.get("VOX_RELEVANCE_MIN", "0.78"))
PUSH_LEASE_SECONDS = max(10, int(os.environ.get("VOX_PUSH_LEASE_SECONDS", "60")))
PUSH_RETRY_BASE_SECONDS = max(1, int(os.environ.get("VOX_PUSH_RETRY_BASE_SECONDS", "5")))
PUSH_RETRY_MAX_SECONDS = max(PUSH_RETRY_BASE_SECONDS, int(os.environ.get("VOX_PUSH_RETRY_MAX_SECONDS", "300")))
PUSH_POLL_SECONDS = max(0.2, float(os.environ.get("VOX_PUSH_POLL_SECONDS", "1")))
_FCM_TOKEN_CACHE = {"access_token": "", "exp": 0.0}
_FCM_LOCK = threading.Lock()
import re

OWNER_REQUEST_RE = re.compile(r"Исходный запрос пользователя:\s*(.+?)(?:\n\n|$)", re.S)
STATE_LABELS = {
    "running": "в работе",
    "queued": "в очереди",
    "blocked": "остановлена на внутренней проверке",
    "needs_user": "ждёт твоего ответа на конкретный вопрос",
    "failed": "провалена",
    "completed": "готова",
    "cancelled": "отменена",
    "interrupted": "прервана",
    # mission-protocol phases (mission_status is more precise than run status)
    "created": "принята, ещё НЕ выполняется",
    "assigned": "принята, ещё НЕ выполняется",
    "planning": "планируется, работа ещё НЕ идёт",
    "plan_review": "план готов, ждёт запуска — работа ещё НЕ идёт",
    "executing": "в работе",
    "governor_review": "бригадир проверяет результат",
    "warmaster_acceptance": "на финальной внутренней проверке",
    "revision": "на внутренней доработке",
}
CLASSES = ("срочно", "важно", "к слову", "фон", "unclassified")
STATES = ("open", "mentioned", "conveyed", "closed")
_LOCK = threading.Lock()

BRAIN_INSTRUCTIONS = (
    "Ты Вокс — голос Шушуни (злобного демона-помощника, мужской род) в разговоре с близким человеком. "
    "Они общаются на равных, по-братски и прямо. Панибратство означает близость, а не хамство, презрение "
    "или отмахивание. Не называй человека владельцем, хозяином, мастером или господином. "
    "Тебе дают факт, который Шушуня хочет сообщить. Верни один строгий JSON: "
    '{"class":"срочно|важно|к слову|фон",'
    '"topic":"короткая тема по-русски, конкретная",'
    '"announce_line":"одна живая фраза для push-уведомления от лица Шушуни (мужской род), конкретная, без общих слов"}. '
    "announce_line всегда пиши от первого лица. Не называй внутренние сервисы, исполнителей, HTTP-коды, "
    "идентификаторы, ключи запросов или диспетчерскую механику. "
    "Класс: 'срочно' — требуется решение человека или что-то сломалось/провалилось; "
    "'важно' — результат готов или значимое событие, скажем при следующем контакте; "
    "'к слову' — уместно ввернуть, когда разговор коснётся темы; "
    "'фон' — мелочь, только если собеседник сам спросит, что накопилось."
)

_PUSH_INTERNAL_RE = re.compile(
    r"\b(?:"
    r"Core|Warmaster|Abaddon|Skitarii|Ceraxia|Iskandar|Archive(?:OfHeresy)?|EyeOfTerror|Administratum|"
    r"Абаддон\w*|Вармастер\w*|Скитари\w*|Церакси\w*|Искандар\w*|"
    r"бригад\w*|варбанд\w*|губернатор\w*|бригадир\w*|"
    r"gateway|preflight|orchestration|client_action|next_action"
    r")\b",
    re.I,
)
_PUSH_PROTOCOL_RE = re.compile(
    r"\b(?:task|mission|run|effect|commitment)_id\b|\bidempotency\b|\bHTTP(?:\s+status)?\s*\d{3}\b",
    re.I,
)
_PUSH_HIERARCHY_RE = re.compile(r"\b(?:владел\w*|хозяин\w*|господин\w*|мой\s+мастер)\b", re.I)
_PUSH_HOSTILITY_RE = re.compile(r"\bне\s+трать\s+мо[её]\s+время\b", re.I)
_URGENT_CONTRACT_KINDS = {
    "decision_required",
    "task_completed",
    "task_failed",
    "task_stalled_internal",
}
_PUSH_WAKE = threading.Event()


def conversation_push_text(value: str, fallback: str) -> str:
    """Fail closed: only an already conversational line reaches FCM."""
    clean = " ".join(str(value or "").split()).strip()
    if (
        not clean
        or _PUSH_INTERNAL_RE.search(clean)
        or _PUSH_PROTOCOL_RE.search(clean)
        or _PUSH_HIERARCHY_RE.search(clean)
        or _PUSH_HOSTILITY_RE.search(clean)
    ):
        clean = fallback
    return " ".join(str(clean or fallback).split())[:240]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _push_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now().astimezone()
    return current.astimezone() if current.tzinfo else current.astimezone()


def _push_iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _post_json(url: str, payload: dict, timeout: float = 120.0) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS fcm_tokens (token TEXT PRIMARY KEY, updated_at TEXT NOT NULL)")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS intents (
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
            embedding_json TEXT NOT NULL DEFAULT '[]',
            push_state TEXT NOT NULL DEFAULT 'not_required',
            push_attempts INTEGER NOT NULL DEFAULT 0,
            push_next_at TEXT,
            push_error TEXT,
            pushed_at TEXT,
            push_lease_token TEXT
        )
        """
    )
    # Add the outbox without replaying historical urgent rows. SQLite applies
    # the DEFAULT to existing rows, so every pre-migration intent starts as
    # not_required; only a newly created/materially refreshed version queues.
    columns = {row["name"] for row in db.execute("PRAGMA table_info(intents)")}
    migrations = {
        "push_state": "TEXT NOT NULL DEFAULT 'not_required'",
        "push_attempts": "INTEGER NOT NULL DEFAULT 0",
        "push_next_at": "TEXT",
        "push_error": "TEXT",
        "pushed_at": "TEXT",
        "push_lease_token": "TEXT",
    }
    for name, definition in migrations.items():
        if name not in columns:
            db.execute(f"ALTER TABLE intents ADD COLUMN {name} {definition}")
    db.execute("UPDATE intents SET push_state = 'not_required' WHERE push_state IS NULL OR push_state = ''")
    db.execute("UPDATE intents SET push_attempts = 0 WHERE push_attempts IS NULL")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_intents_push_due "
        "ON intents (push_state, push_next_at, id)"
    )
    db.commit()
    return db


def embed_text(text: str) -> list[float]:
    payload = {"model": EMBED_MODEL, "input": [f"query: {text[:600]}"]}
    response = _post_json(f"{EMBED_BASE_URL}/v1/embeddings", payload, timeout=60)
    return list((response.get("data") or [{}])[0].get("embedding") or [])


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _fcm_access_token() -> str:
    """Service-account -> OAuth2 access token for FCM v1 (cached ~55 min)."""
    with _FCM_LOCK:
        if _FCM_TOKEN_CACHE["access_token"] and _FCM_TOKEN_CACHE["exp"] > time.time() + 60:
            return _FCM_TOKEN_CACHE["access_token"]
        import jwt  # noqa: PLC0415

        with open(FCM_SERVICE_ACCOUNT, encoding="utf-8") as handle:
            sa = json.load(handle)
        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": sa["client_email"],
                "scope": "https://www.googleapis.com/auth/firebase.messaging",
                "aud": sa["token_uri"],
                "iat": now,
                "exp": now + 3600,
            },
            sa["private_key"],
            algorithm="RS256",
        )
        body = urllib.parse.urlencode(
            {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}
        ).encode("utf-8")
        request = urllib.request.Request(sa["token_uri"], data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        with urllib.request.urlopen(request, timeout=20) as response:
            token = json.loads(response.read().decode("utf-8"))
        _FCM_TOKEN_CACHE["access_token"] = token["access_token"]
        _FCM_TOKEN_CACHE["exp"] = time.time() + int(token.get("expires_in", 3600))
        return _FCM_TOKEN_CACHE["access_token"]


def _fcm_project_id() -> str:
    with open(FCM_SERVICE_ACCOUNT, encoding="utf-8") as handle:
        return json.load(handle)["project_id"]


def push_fcm(title: str, body: str) -> dict:
    """Send an FCM data+notification push to every registered device. Real push:
    the phone gets it with the app fully closed, no foreground service."""
    conversation_title = conversation_push_text(title, "Шушуня хочет что-то сказать")
    conversation_body = conversation_push_text(
        body,
        "У меня есть обновление. Открой чат — там скажу нормально.",
    )
    if not os.path.exists(FCM_SERVICE_ACCOUNT):
        return {"ok": False, "sent": 0, "error": "no service account"}
    with connect() as db:
        tokens = [row["token"] for row in db.execute("SELECT token FROM fcm_tokens")]
    if not tokens:
        return {"ok": False, "sent": 0, "error": "no registered devices"}
    try:
        access = _fcm_access_token()
        project = _fcm_project_id()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "sent": 0, "error": f"auth: {exc}"}
    url = f"https://fcm.googleapis.com/v1/projects/{project}/messages:send"
    sent, dead, errors = 0, [], []
    for token in tokens:
        message = {
            "message": {
                "token": token,
                "notification": {"title": conversation_title, "body": conversation_body},
                "data": {
                    "conversation_title": conversation_title,
                    "conversation_body": conversation_body,
                },
                "android": {"priority": "high", "notification": {"channel_id": "shushunya_answers"}},
            }
        }
        data = json.dumps(message, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Authorization": f"Bearer {access}"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=20):
                sent += 1
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 400):  # stale/invalid token -> forget it
                dead.append(token)
            else:
                errors.append(f"FCM HTTP {exc.code}")
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
    if dead:
        with connect() as db:
            db.executemany("DELETE FROM fcm_tokens WHERE token = ?", [(t,) for t in dead])
    result = {"ok": sent > 0, "sent": sent, "pruned": len(dead)}
    if sent == 0:
        result["error"] = "; ".join(errors[:3]) or "FCM accepted no messages"
    return result


def _claim_pending_push(now: datetime | None = None) -> dict | None:
    """Atomically lease one due outbox row; expired leases are recoverable."""
    current = _push_now(now)
    current_iso = _push_iso(current)
    lease_until = _push_iso(current + timedelta(seconds=PUSH_LEASE_SECONDS))
    lease_token = uuid4().hex
    with _LOCK:
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT * FROM intents
                WHERE (
                    push_state IN ('pending', 'retry_wait')
                    AND (push_next_at IS NULL OR push_next_at <= ?)
                ) OR (
                    push_state = 'leased'
                    AND push_next_at IS NOT NULL
                    AND push_next_at <= ?
                )
                ORDER BY id
                LIMIT 1
                """,
                (current_iso, current_iso),
            ).fetchone()
            if row is None:
                return None
            changed = db.execute(
                "UPDATE intents SET push_state = 'leased', push_next_at = ?, "
                "push_lease_token = ?, updated_at = ? WHERE id = ?",
                (lease_until, lease_token, current_iso, int(row["id"])),
            ).rowcount
            if changed != 1:
                return None
            claim = dict(row)
            claim["push_lease_token"] = lease_token
            claim["push_next_at"] = lease_until
            return claim


def _finish_push_claim(
    claim: dict,
    *,
    sent: bool,
    error: str = "",
    now: datetime | None = None,
) -> dict:
    current = _push_now(now)
    current_iso = _push_iso(current)
    attempts = int(claim.get("push_attempts") or 0) + 1
    if sent:
        state = "sent"
        next_at = None
        pushed_at = current_iso
        stored_error = None
    else:
        state = "retry_wait"
        delay = min(PUSH_RETRY_MAX_SECONDS, PUSH_RETRY_BASE_SECONDS * (2 ** min(attempts - 1, 8)))
        next_at = _push_iso(current + timedelta(seconds=delay))
        pushed_at = None
        stored_error = " ".join(str(error or "unknown FCM failure").split())[:1000]
    with _LOCK:
        with connect() as db:
            changed = db.execute(
                "UPDATE intents SET push_state = ?, push_attempts = ?, push_next_at = ?, "
                "push_error = ?, pushed_at = ?, push_lease_token = NULL, updated_at = ? "
                "WHERE id = ? AND push_state = 'leased' AND push_lease_token = ?",
                (
                    state,
                    attempts,
                    next_at,
                    stored_error,
                    pushed_at,
                    current_iso,
                    int(claim["id"]),
                    claim["push_lease_token"],
                ),
            ).rowcount
    return {
        "checkpointed": changed == 1,
        "attempts": attempts,
        "push_state": state,
        "next_at": next_at,
    }


def deliver_pending_push_once(now: datetime | None = None) -> dict:
    """Claim and deliver one urgent FCM outbox row.

    This is deliberately independent of the conversational intent state:
    conveyed/closed answers can still finish a push that was already owed.
    """
    claim = _claim_pending_push(now)
    if claim is None:
        return {"ok": True, "claimed": False}
    response: dict = {}
    failure = ""
    try:
        raw = push_fcm("Шушуня хочет что-то сказать", str(claim.get("announce_line") or claim.get("topic") or ""))
        response = raw if isinstance(raw, dict) else {}
        sent_count = int(response.get("sent") or 0)
        delivered = response.get("ok") is True and sent_count > 0
        if not delivered:
            failure = str(response.get("error") or response.get("reason") or f"FCM sent={sent_count}")
            if sent_count == 0 and "sent=0" not in failure:
                failure = f"FCM sent=0: {failure}"
    except Exception as exc:  # noqa: BLE001 - durable retry is the error boundary
        delivered = False
        sent_count = 0
        failure = f"FCM exception: {exc}"
    checkpoint = _finish_push_claim(claim, sent=delivered, error=failure, now=now)
    if not checkpoint["checkpointed"]:
        return {"ok": False, "claimed": True, "intent_id": int(claim["id"]), "error": "push lease was lost"}
    if delivered:
        return {
            "ok": True,
            "claimed": True,
            "intent_id": int(claim["id"]),
            "sent": sent_count,
            "attempts": checkpoint["attempts"],
        }
    return {
        "ok": False,
        "claimed": True,
        "intent_id": int(claim["id"]),
        "error": failure,
        "attempts": checkpoint["attempts"],
        "retry_at": checkpoint["next_at"],
    }


def push_delivery_loop(stop_event: threading.Event) -> None:
    """Continuously drain due pushes; a crash only leaves an expiring lease."""
    while not stop_event.is_set():
        processed = 0
        try:
            while processed < 16 and not stop_event.is_set():
                outcome = deliver_pending_push_once()
                if not outcome.get("claimed"):
                    break
                processed += 1
        except Exception as exc:  # noqa: BLE001 - keep the outbox worker alive
            print(f"Vox push outbox error: {exc}", flush=True)
        _PUSH_WAKE.wait(PUSH_POLL_SECONDS)
        _PUSH_WAKE.clear()


def register_fcm_token(token: str) -> dict:
    token = str(token or "").strip()
    if not token:
        return {"ok": False, "error": "empty token"}
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO fcm_tokens (token, updated_at) VALUES (?, ?)", (token, now_iso()))
    _PUSH_WAKE.set()
    return {"ok": True}


def classify_intent(source: str, kind: str, body: str) -> dict:
    """The Vox brain: the source supplies facts, Vox decides how to speak."""
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": BRAIN_INSTRUCTIONS},
            {"role": "user", "content": json.dumps({"source": source, "kind": kind, "body": body[:2000]}, ensure_ascii=False)},
        ],
        "temperature": 0,
        "max_tokens": 300,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    response = _post_json(f"{LLM_BASE_URL}/v1/chat/completions", payload, timeout=180)
    content = str(((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    if "{" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    parsed = json.loads(content)
    speech_class = str(parsed.get("class") or "").strip()
    if speech_class not in CLASSES:
        speech_class = "важно"
    return {
        "speech_class": speech_class,
        "topic": " ".join(str(parsed.get("topic") or "").split())[:200],
        "announce_line": " ".join(str(parsed.get("announce_line") or "").split())[:300],
    }


def classify_pending(db: sqlite3.Connection) -> None:
    """Intents stored while the model was unavailable get classified lazily —
    deferral, not a mechanical default decision."""
    rows = db.execute("SELECT * FROM intents WHERE speech_class = 'unclassified' AND state = 'open' LIMIT 5").fetchall()
    for row in rows:
        try:
            brain = classify_intent(row["source"], row["kind"], row["body"])
            announce_line = conversation_push_text(
                brain["announce_line"],
                "У меня есть обновление. Открой чат — там скажу нормально.",
            )
            embedding = []
            try:
                embedding = embed_text(f"{brain['topic']} {row['body']}")
            except Exception:
                pass
            was_newly_deferred = row["push_state"] == "awaiting_classification"
            queue_push = was_newly_deferred and brain["speech_class"] == "срочно" and bool(announce_line)
            next_push_state = "pending" if queue_push else ("not_required" if was_newly_deferred else row["push_state"])
            db.execute(
                "UPDATE intents SET speech_class = ?, topic = ?, announce_line = ?, embedding_json = ?, "
                "push_state = ?, push_attempts = CASE WHEN ? THEN 0 ELSE push_attempts END, "
                "push_next_at = CASE WHEN ? THEN NULL ELSE push_next_at END, "
                "push_error = CASE WHEN ? THEN NULL ELSE push_error END, "
                "pushed_at = CASE WHEN ? THEN NULL ELSE pushed_at END, "
                "push_lease_token = CASE WHEN ? THEN NULL ELSE push_lease_token END, updated_at = ? WHERE id = ?",
                (
                    brain["speech_class"],
                    brain["topic"] or row["topic"],
                    announce_line,
                    json.dumps(embedding),
                    next_push_state,
                    was_newly_deferred,
                    was_newly_deferred,
                    was_newly_deferred,
                    was_newly_deferred,
                    was_newly_deferred,
                    now_iso(),
                    row["id"],
                ),
            )
            if queue_push:
                _PUSH_WAKE.set()
        except Exception:
            break  # model still down: stay unclassified, try next time


def create_intent(payload: dict) -> dict:
    source = str(payload.get("source") or "unknown").strip()[:80]
    kind = str(payload.get("kind") or "report").strip()[:80]
    body = str(payload.get("body") or "").strip()
    if not body:
        return {"ok": False, "error": "body is required"}
    fallback_topic = " ".join(str(payload.get("topic") or "").split())[:200] or kind
    dedupe_key = str(payload.get("dedupe_key") or "").strip()[:160] or None
    speech_class = "unclassified"
    topic = fallback_topic
    announce_line = ""
    embedding: list[float] = []
    if kind in _URGENT_CONTRACT_KINDS:
        # A typed question or confirmed stop is urgent by contract, even when
        # the LLM classifier is down. Vox guarantees immediate transport; the
        # source already owns the factual classification.
        speech_class = "срочно"
        if kind == "decision_required":
            question = next((line.strip() for line in reversed(body.splitlines()) if line.strip()), "нужен твой выбор")
            announce_line = conversation_push_text(
                f"Мне нужен твой выбор: {question}",
                "Мне нужен твой выбор. Открой чат — там конкретный вопрос.",
            )
        elif kind == "task_completed":
            announce_line = conversation_push_text(
                body,
                "Я закончил задачу. Открой чат — там уже лежит результат.",
            )
        else:
            announce_line = conversation_push_text(
                body,
                "Я остановился на внутренней проверке. Открой чат — там объясню, что произошло.",
            )
    else:
        try:
            brain = classify_intent(source, kind, body)
            speech_class = brain["speech_class"]
            topic = brain["topic"] or fallback_topic
            announce_line = conversation_push_text(
                brain["announce_line"],
                "У меня есть обновление. Открой чат — там скажу нормально.",
            )
        except Exception as exc:  # noqa: BLE001 - stored unclassified, classified lazily later
            print(f"Vox brain unavailable, intent deferred: {exc}", flush=True)
    try:
        embedding = embed_text(f"{topic} {body}")
    except Exception:
        embedding = []
    if speech_class == "срочно" and announce_line:
        next_push_state = "pending"
    elif speech_class == "unclassified":
        # This marks only versions created after the migration. When lazy
        # classification later decides "срочно", it may safely queue them;
        # historical unclassified rows remain not_required and never replay.
        next_push_state = "awaiting_classification"
    else:
        next_push_state = "not_required"
    result = None
    queued_push = False
    with _LOCK:
        with connect() as db:
            if dedupe_key:
                row = db.execute(
                    "SELECT * FROM intents WHERE dedupe_key = ? ORDER BY id DESC LIMIT 1",
                    (dedupe_key,),
                ).fetchone()
                if row:
                    unchanged = (
                        row["body"] == body
                        and row["topic"] == topic
                        and row["announce_line"] == announce_line
                        and row["speech_class"] == speech_class
                    )
                    if unchanged:
                        result = {
                            "ok": True,
                            "intent_id": int(row["id"]),
                            "duplicate": True,
                            "speech_class": speech_class,
                        }
                    else:
                        # Same subject, materially newer news: reopen and push
                        # once for this new version, without making a copy.
                        db.execute(
                            "UPDATE intents SET body = ?, topic = ?, announce_line = ?, speech_class = ?, "
                            "embedding_json = ?, updated_at = ?, announced_at = NULL, conveyed_at = NULL, state = 'open', "
                            "push_state = ?, push_attempts = 0, push_next_at = NULL, push_error = NULL, "
                            "pushed_at = NULL, push_lease_token = NULL WHERE id = ?",
                            (
                                body,
                                topic,
                                announce_line,
                                speech_class,
                                json.dumps(embedding),
                                now_iso(),
                                next_push_state,
                                int(row["id"]),
                            ),
                        )
                        result = {"ok": True, "intent_id": int(row["id"]), "refreshed": True, "speech_class": speech_class}
                        queued_push = next_push_state == "pending"
            if result is None:
                cursor = db.execute(
                    "INSERT INTO intents (created_at, updated_at, source, kind, topic, body, announce_line, "
                    "speech_class, dedupe_key, embedding_json, push_state) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        now_iso(),
                        now_iso(),
                        source,
                        kind,
                        topic,
                        body,
                        announce_line,
                        speech_class,
                        dedupe_key,
                        json.dumps(embedding),
                        next_push_state,
                    ),
                )
                result = {"ok": True, "intent_id": int(cursor.lastrowid), "speech_class": speech_class}
                queued_push = next_push_state == "pending"
    if queued_push:
        _PUSH_WAKE.set()
    return result


def open_intents(db: sqlite3.Connection) -> list[dict]:
    classify_pending(db)
    rows = db.execute("SELECT * FROM intents WHERE state IN ('open', 'mentioned') ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def on_tongue(context_text: str, limit: int = 6) -> dict:
    """What Shushunya has on the tongue for this turn: urgent and important
    always; 'к слову' only when semantically close to the conversation."""
    with connect() as db:
        intents = open_intents(db)
    context_embedding: list[float] = []
    if context_text:
        try:
            context_embedding = embed_text(context_text)
        except Exception:
            context_embedding = []
    picked = []
    for intent in intents:
        speech_class = intent["speech_class"]
        relevance = 0.0
        if speech_class in ("срочно", "важно", "unclassified"):
            include = True
        elif speech_class == "к слову" and context_embedding:
            try:
                relevance = cosine(context_embedding, json.loads(intent["embedding_json"]))
            except (TypeError, ValueError):
                relevance = 0.0
            include = relevance >= RELEVANCE_MIN
        else:
            include = False
        if include:
            picked.append(
                {
                    "id": intent["id"],
                    "class": speech_class,
                    "topic": intent["topic"],
                    "body": intent["body"][:1200],
                    "relevance": round(relevance, 3),
                }
            )
    picked = picked[: max(1, min(limit, 12))]
    return {"ok": True, "intents": picked, "open_total": len(intents)}


def deliverable_intents() -> dict:
    """Everything open, full bodies — the owner explicitly asked to hear it."""
    with connect() as db:
        intents = open_intents(db)
    return {
        "ok": True,
        "intents": [
            {"id": i["id"], "class": i["speech_class"], "kind": i["kind"], "topic": i["topic"], "body": i["body"], "created_at": i["created_at"]}
            for i in intents
        ],
    }


def summary() -> dict:
    with connect() as db:
        intents = open_intents(db)
    announce = ""
    if intents:
        newest = intents[-1]
        announce = newest["announce_line"] or newest["topic"]
        if len(intents) > 1:
            announce += f" (и ещё {len(intents) - 1})"
    return {
        "ok": True,
        "count": len(intents),
        "announce": announce,
        "topics": [{"id": i["id"], "kind": i["kind"], "class": i["speech_class"], "topic": i["topic"], "created_at": i["created_at"]} for i in intents],
    }


def announce_for_phone() -> dict:
    """Vox decides what the phone should buzz about: urgent intents not yet
    announced. Marking happens here, server-side — the phone stays stateless."""
    with _LOCK:
        with connect() as db:
            intents = open_intents(db)
            fresh = [i for i in intents if i["speech_class"] == "срочно" and not i["announced_at"]]
            lines = [i["announce_line"] or i["topic"] for i in fresh]
            if fresh:
                marks = [i["id"] for i in fresh]
                placeholders = ",".join("?" for _ in marks)
                db.execute(f"UPDATE intents SET announced_at = ? WHERE id IN ({placeholders})", (now_iso(), *marks))
    return {
        "ok": True,
        "count": len(intents),
        "notify": bool(lines),
        "notify_lines": lines,
        "badge": summary()["announce"],
    }


def _get_json(url: str, timeout: float = 15.0) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _human_goal(run: dict) -> str:
    goal = str(run.get("goal") or "")
    match = OWNER_REQUEST_RE.search(goal)
    return " ".join((match.group(1) if match else goal).split())[:160]


def task_roster(limit: int = 10) -> dict:
    """Live roster of all of Shushunya's brigade tasks, pulled fresh from
    Warmaster (never stored — a roster can't go stale if it isn't cached).
    This is what Shushunya always has at hand, so task status is answered from
    truth instead of from a frozen focus note or an old acknowledgement."""
    try:
        response = _get_json(f"{WARMASTER_BASE_URL}/runs?limit={max(1, min(limit, 30))}")
    except Exception as exc:  # noqa: BLE001 - Warmaster down: empty roster, not a lie
        return {"ok": False, "error": str(exc), "tasks": []}
    runs = response.get("runs") if isinstance(response.get("runs"), list) else []
    active_ids = set(response.get("process_active_runs") or [])
    tasks = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        # Warmaster's nested mission_state is the canonical lifecycle view.
        # In particular, a blocked mission may either need an internal repair
        # or a concrete user decision; flattening both to "blocked" makes the
        # conversation layer treat a pending question as autonomous work.
        mission_state = run.get("mission_state") if isinstance(run.get("mission_state"), dict) else {}
        needs_user = mission_state.get("needs_user") is True
        status = str(
            mission_state.get("status")
            or run.get("mission_status")
            or run.get("lifecycle_status")
            or run.get("status")
            or ""
        ).lower()
        if needs_user:
            status = "needs_user"
        tasks.append(
            {
                "task_id": str(run.get("task_id") or ""),
                "goal": _human_goal(run),
                "governor": str(run.get("governor") or ""),
                "state": status,
                "state_label": STATE_LABELS.get(status, status or "неизвестно"),
                "needs_user": needs_user,
                "user_visible_state": str(mission_state.get("user_visible_state") or ""),
                "next_owner": str(mission_state.get("next_owner") or ""),
                "active": str(run.get("task_id") or "") in active_ids or status in {"running", "queued", "executing"},
            }
        )
    return {"ok": True, "tasks": tasks}


def mark_conveyed(payload: dict) -> dict:
    conveyed = [int(i) for i in payload.get("conveyed_ids") or []]
    mentioned = [int(i) for i in payload.get("mentioned_ids") or []]
    closed = [int(i) for i in payload.get("closed_ids") or []]
    with _LOCK:
        with connect() as db:
            for ids, state in ((conveyed, "conveyed"), (mentioned, "mentioned"), (closed, "closed")):
                if not ids:
                    continue
                placeholders = ",".join("?" for _ in ids)
                db.execute(
                    f"UPDATE intents SET state = ?, conveyed_at = ?, updated_at = ? WHERE id IN ({placeholders}) AND state IN ('open', 'mentioned')",
                    (state, now_iso() if state == "conveyed" else None, now_iso(), *ids),
                )
    return {"ok": True, "conveyed": len(conveyed), "mentioned": len(mentioned), "closed": len(closed)}


class VoxHandler(BaseHTTPRequestHandler):
    server_version = "Vox/0.1"

    def log_message(self, fmt, *args):  # noqa: A003
        return

    def _reply(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _payload(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/health":
                self._reply(200, {"ok": True, "service": "Vox", "port": DEFAULT_PORT})
            elif parsed.path == "/summary":
                self._reply(200, summary())
            elif parsed.path == "/announce":
                self._reply(200, announce_for_phone())
            elif parsed.path == "/deliverable":
                self._reply(200, deliverable_intents())
            elif parsed.path == "/roster":
                self._reply(200, task_roster())
            elif parsed.path == "/intents":
                with connect() as db:
                    rows = db.execute("SELECT * FROM intents ORDER BY id DESC LIMIT 50").fetchall()
                self._reply(200, {"ok": True, "intents": [dict(r) for r in rows]})
            else:
                self._reply(404, {"ok": False, "error": "not found"})
        except Exception as exc:  # noqa: BLE001
            self._reply(500, {"ok": False, "error": str(exc)})

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = self._payload()
            if parsed.path == "/intent":
                self._reply(201, create_intent(payload))
            elif parsed.path == "/register-token":
                self._reply(200, register_fcm_token(payload.get("token")))
            elif parsed.path == "/test-push":
                self._reply(200, push_fcm("Шушуня (тест)", str(payload.get("body") or "Проверка пуша")))
            elif parsed.path == "/on-tongue":
                self._reply(200, on_tongue(str(payload.get("context") or ""), int(payload.get("limit") or 6)))
            elif parsed.path == "/conveyed":
                self._reply(200, mark_conveyed(payload))
            else:
                self._reply(404, {"ok": False, "error": "not found"})
        except Exception as exc:  # noqa: BLE001
            self._reply(500, {"ok": False, "error": str(exc)})


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Vox intent service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    connect().close()
    server = ThreadingHTTPServer((args.host, args.port), VoxHandler)
    push_stop = threading.Event()
    push_thread = threading.Thread(
        target=push_delivery_loop,
        args=(push_stop,),
        daemon=True,
        name="vox-fcm-outbox",
    )
    push_thread.start()
    print(f"Vox listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        push_stop.set()
        _PUSH_WAKE.set()
        server.server_close()
        push_thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
