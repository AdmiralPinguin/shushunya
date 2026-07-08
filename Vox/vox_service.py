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
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("VOX_DB_PATH", ROOT / "runtime" / "vox.sqlite3"))
DEFAULT_PORT = int(os.environ.get("VOX_PORT", "7400"))
LLM_BASE_URL = os.environ.get("VOX_LLM_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
LLM_MODEL = os.environ.get("VOX_LLM_MODEL", os.environ.get("ARCHIVE_DEFAULT_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf"))
EMBED_BASE_URL = os.environ.get("VOX_EMBED_BASE_URL", "http://127.0.0.1:8181").rstrip("/")
EMBED_MODEL = os.environ.get("VOX_EMBED_MODEL", "multilingual-e5-large")
RELEVANCE_MIN = float(os.environ.get("VOX_RELEVANCE_MIN", "0.78"))
CLASSES = ("срочно", "важно", "к слову", "фон", "unclassified")
STATES = ("open", "mentioned", "conveyed", "closed")
_LOCK = threading.Lock()

BRAIN_INSTRUCTIONS = (
    "Ты Вокс — канал связи Шушуни (злобного демона-помощника, мужской род) с владельцем. "
    "Тебе дают факт, который Шушуня хочет сообщить владельцу. Верни один строгий JSON: "
    '{"class":"срочно|важно|к слову|фон",'
    '"topic":"короткая тема по-русски, конкретная",'
    '"announce_line":"одна живая фраза для push-уведомления от лица Шушуни (мужской род), конкретная, без общих слов"}. '
    "Класс: 'срочно' — требуется решение владельца или что-то сломалось/провалилось; "
    "'важно' — результат готов или значимое событие, скажем при следующем контакте; "
    "'к слову' — уместно ввернуть, когда разговор коснётся темы; "
    "'фон' — мелочь, только если владелец сам спросит, что накопилось."
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _post_json(url: str, payload: dict, timeout: float = 120.0) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
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
            embedding_json TEXT NOT NULL DEFAULT '[]'
        )
        """
    )
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
            embedding = []
            try:
                embedding = embed_text(f"{brain['topic']} {row['body']}")
            except Exception:
                pass
            db.execute(
                "UPDATE intents SET speech_class = ?, topic = ?, announce_line = ?, embedding_json = ?, updated_at = ? WHERE id = ?",
                (
                    brain["speech_class"],
                    brain["topic"] or row["topic"],
                    brain["announce_line"],
                    json.dumps(embedding),
                    now_iso(),
                    row["id"],
                ),
            )
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
    try:
        brain = classify_intent(source, kind, body)
        speech_class = brain["speech_class"]
        topic = brain["topic"] or fallback_topic
        announce_line = brain["announce_line"]
    except Exception as exc:  # noqa: BLE001 - stored unclassified, classified lazily later
        print(f"Vox brain unavailable, intent deferred: {exc}", flush=True)
    try:
        embedding = embed_text(f"{topic} {body}")
    except Exception:
        embedding = []
    with _LOCK:
        with connect() as db:
            if dedupe_key:
                row = db.execute(
                    "SELECT id FROM intents WHERE dedupe_key = ? AND state IN ('open', 'mentioned')",
                    (dedupe_key,),
                ).fetchone()
                if row:
                    # Same news, newer version: refresh in place, no copies.
                    db.execute(
                        "UPDATE intents SET body = ?, topic = ?, announce_line = ?, speech_class = ?, embedding_json = ?, updated_at = ?, announced_at = NULL WHERE id = ?",
                        (body, topic, announce_line, speech_class, json.dumps(embedding), now_iso(), int(row["id"])),
                    )
                    return {"ok": True, "intent_id": int(row["id"]), "refreshed": True, "speech_class": speech_class}
            cursor = db.execute(
                "INSERT INTO intents (created_at, updated_at, source, kind, topic, body, announce_line, speech_class, dedupe_key, embedding_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now_iso(), now_iso(), source, kind, topic, body, announce_line, speech_class, dedupe_key, json.dumps(embedding)),
            )
            return {"ok": True, "intent_id": int(cursor.lastrowid), "speech_class": speech_class}


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
    print(f"Vox listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
