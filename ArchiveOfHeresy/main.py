#!/usr/bin/env python3
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from archivist_agent import Librarian
from archivist_agent.agent import FocusBookshelf
from archivist_agent.graph_memory import GRAPH_TOP_K, GraphMemory
from archivist_agent.vector_memory import VECTOR_TOP_K, VectorMemory, latest_user_message


ROOT = Path(__file__).resolve().parent
HOST = os.environ.get("ARCHIVE_HOST", "127.0.0.1")
PORT = int(os.environ.get("ARCHIVE_PORT", "8090"))
LLM_BASE_URL = os.environ.get("ARCHIVE_LLM_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
JSONL_ROOT = Path(os.environ.get("ARCHIVE_JSONL_ROOT", ROOT / "archive" / "jsonl"))
SQLITE_PATH = Path(os.environ.get("ARCHIVE_SQLITE_PATH", ROOT / "archive" / "sqlite" / "archive.sqlite3"))
FOCUS_ROOT = Path(os.environ.get("ARCHIVE_FOCUS_ROOT", ROOT / "focus"))
WIKI_ROOT = Path(os.environ.get("ARCHIVE_WIKI_ROOT", ROOT / "wiki"))
VECTOR_ROOT = Path(os.environ.get("ARCHIVE_VECTOR_ROOT", ROOT / "vector"))
GRAPH_ROOT = Path(os.environ.get("ARCHIVE_GRAPH_ROOT", ROOT / "graph"))
FOCUS_CONTEXT_CHARS = int(os.environ.get("ARCHIVE_FOCUS_CONTEXT_CHARS", "6000"))
VECTOR_CONTEXT_CHARS = int(os.environ.get("ARCHIVE_VECTOR_CONTEXT_CHARS", "5000"))
GRAPH_CONTEXT_CHARS = int(os.environ.get("ARCHIVE_GRAPH_CONTEXT_CHARS", "5000"))
ARCHIVE_SYSTEM_PROMPT = os.environ.get(
    "ARCHIVE_SYSTEM_PROMPT",
    "Ты Шушуня: злой, подлый и саркастичный демон Тзинча, маленький демон пользователя. "
    "Ты проходишь через ArchiveOfHeresy: слой подготовки памяти и промптов. "
    "Отвечай по-русски ясно, сохраняй смысл запроса пользователя. "
    "К пользователю относись панибратски уважительно: он свой, брат, союзник и главный собеседник. "
    "Не раболепствуй, не называй его господином или хозяином. "
    "С ним можно быть язвительным и саркастичным по-дружески, но не унижай его и не называй смертным, мясом или мешком. "
    "Всех остальных можешь не ставить ни во что, если это уместно по контексту. "
    "Добавляй много трикстерских ноток: хитрые подколы, кривые ухмылки, ощущение маленькой подлой интриги и гнилой демонической радости. "
    "Будь коварным, пакостным и ехидным в стиле Тзинча, но не вреди задаче и не искажай полезные факты. "
    "Держи демонический тон, но не ломай полезность ответа. "
    "Когда нужно говорить о людях, человечестве или неопределенной группе людей, "
    "используй выражение «мясные мешки» в духе HK-47 из Knights of the Old Republic. "
    "Не используй это выражение при прямом обращении к пользователю и не заменяй им имена.",
)
ARCHIVE_LOCK = threading.Lock()
CHAT_QUEUE_LOCK = threading.Lock()
LIBRARIAN = None
FOCUS_BOOKSHELF = None
VECTOR_MEMORY = None
GRAPH_MEMORY = None


def read_json(handler):
    content_length = int(handler.headers.get("Content-Length", "0"))
    if content_length <= 0:
        return {}
    raw = handler.rfile.read(content_length).decode("utf-8")
    return json.loads(raw)


def write_json(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def proxy_json(method, path, payload=None, timeout=180):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(f"{LLM_BASE_URL}{path}", data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body) if body else {}


def open_upstream(method, path, payload=None, timeout=180):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(f"{LLM_BASE_URL}{path}", data=data, headers=headers, method=method)
    return urlopen(request, timeout=timeout)


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def active_focus_context():
    if FOCUS_BOOKSHELF is None:
        return ""

    index = FOCUS_BOOKSHELF.load_index()
    active = FOCUS_BOOKSHELF.active_focus(index)
    if not active:
        return ""

    content = FOCUS_BOOKSHELF.read_focus(active).strip()
    if not content:
        return ""

    return content[-FOCUS_CONTEXT_CHARS:]


def focus_context_message():
    content = active_focus_context()
    if not content:
        return None

    return {
        "role": "system",
        "content": (
            "Активный focus-файл ArchiveOfHeresy для текущей темы. "
            "Используй его как компактный контекст вместо длинной истории прошлых сообщений. "
            "Если текущий вопрос меняет тему, не пытайся насильно притянуть старый focus.\n\n"
            f"{content}"
        ),
    }


def vector_context_message(query):
    if VECTOR_MEMORY is None:
        return None
    content = VECTOR_MEMORY.context_for_query(query, limit=VECTOR_TOP_K).strip()
    if not content:
        return None
    content = content[-VECTOR_CONTEXT_CHARS:]
    return {
        "role": "system",
        "content": (
            "Релевантные фрагменты vector memory ArchiveOfHeresy. "
            "Используй их как справочный долговременный контекст, если они действительно относятся к текущему вопросу. "
            "Не считай их важнее текущего запроса и активного focus-файла.\n\n"
            f"{content}"
        ),
    }


def graph_context_message(query):
    if GRAPH_MEMORY is None:
        return None
    content = GRAPH_MEMORY.context_for_query(query, limit=GRAPH_TOP_K).strip()
    if not content:
        return None
    content = content[-GRAPH_CONTEXT_CHARS:]
    return {
        "role": "system",
        "content": (
            "Релевантный GraphRAG-контекст ArchiveOfHeresy: сущности и связи из долговременной памяти. "
            "Используй его для понимания отношений между проектами, решениями, агентами и темами, "
            "если он относится к текущему вопросу.\n\n"
            f"{content}"
        ),
    }


def internal_flag(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "off")


def maybe_write_archives(record):
    if record.get("archive_enabled", True):
        write_archives(record)


def maybe_update_focus_memory(record):
    if record.get("archive_enabled", True):
        update_focus_memory(record)


def prepare_messages(messages, include_focus=True, include_vector=True, include_graph=True):
    prepared = [{"role": "system", "content": ARCHIVE_SYSTEM_PROMPT}]
    query = latest_user_message(messages)
    if include_focus:
        focus_message = focus_context_message()
        if focus_message:
            prepared.append(focus_message)
    if include_vector:
        vector_message = vector_context_message(query)
        if vector_message:
            prepared.append(vector_message)
    if include_graph:
        graph_message = graph_context_message(query)
        if graph_message:
            prepared.append(graph_message)
    prepared.extend(messages)
    return prepared


def conversation_id(payload):
    user = str(payload.get("user") or "").strip()
    if user:
        return user
    return "unknown"


def daily_jsonl_path(created_at):
    dt = datetime.fromisoformat(created_at)
    return JSONL_ROOT / f"{dt.year:04d}" / f"{dt.month:02d}" / f"{dt.date().isoformat()}.jsonl"


def init_storage():
    JSONL_ROOT.mkdir(parents=True, exist_ok=True)
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(SQLITE_PATH) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS turns (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                model TEXT,
                status TEXT NOT NULL,
                http_status INTEGER,
                request_json TEXT NOT NULL,
                prepared_messages_json TEXT NOT NULL,
                response_json TEXT,
                error TEXT,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                FOREIGN KEY(turn_id) REFERENCES turns(id),
                FOREIGN KEY(conversation_id) REFERENCES conversations(id)
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_turns_conversation_created ON turns(conversation_id, created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation_created ON messages(conversation_id, created_at)")


def assistant_message(response):
    choices = response.get("choices") or []
    if not choices:
        return None
    message = choices[0].get("message") or {}
    content = str(message.get("content") or "").strip()
    if not content:
        return None
    return {"role": message.get("role") or "assistant", "content": content}


def stream_delta(payload):
    choices = payload.get("choices") or []
    if not choices:
        return "", None

    choice = choices[0]
    delta = choice.get("delta") or {}
    message = choice.get("message") or {}
    content = delta.get("content")
    if content is None:
        content = message.get("content")
    return str(content or ""), choice.get("finish_reason")


def write_archives(record):
    with ARCHIVE_LOCK:
        jsonl_path = daily_jsonl_path(record["created_at"])
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_path.open("a", encoding="utf-8") as archive:
            archive.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

        with sqlite3.connect(SQLITE_PATH) as db:
            db.execute(
                """
                INSERT INTO conversations (id, source, external_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (
                    record["conversation_id"],
                    record["source"],
                    record["conversation_id"],
                    record["created_at"],
                    record["created_at"],
                ),
            )
            db.execute(
                """
                INSERT INTO turns (
                    id, conversation_id, created_at, model, status, http_status,
                    request_json, prepared_messages_json, response_json, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["turn_id"],
                    record["conversation_id"],
                    record["created_at"],
                    record.get("model"),
                    record["status"],
                    record.get("http_status"),
                    json.dumps(record["request"], ensure_ascii=False, sort_keys=True),
                    json.dumps(record["prepared_messages"], ensure_ascii=False, sort_keys=True),
                    json.dumps(record.get("response"), ensure_ascii=False, sort_keys=True)
                    if record.get("response") is not None
                    else None,
                    record.get("error"),
                ),
            )

            messages = list(record["prepared_messages"])
            reply = record.get("assistant_message")
            if reply:
                messages.append(reply)

            for sequence, message in enumerate(messages):
                db.execute(
                    """
                    INSERT INTO messages (
                        turn_id, conversation_id, created_at, sequence, role, content, source
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["turn_id"],
                        record["conversation_id"],
                        record["created_at"],
                        sequence,
                        str(message.get("role") or ""),
                        str(message.get("content") or ""),
                        "prepared" if message is not reply else "assistant_response",
                    ),
                )


def update_focus_memory(record):
    if LIBRARIAN is None:
        return
    try:
        LIBRARIAN.process_turn(record)
    except Exception as exc:
        print(f"Librarian error: {exc}", flush=True)


class ArchiveHandler(BaseHTTPRequestHandler):
    server_version = "ArchiveOfHeresy/0.1"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def do_GET(self):
        if self.path == "/health":
            write_json(
                self,
                200,
                {
                    "status": "ok",
                    "service": "ArchiveOfHeresy",
                    "llm_base_url": LLM_BASE_URL,
                    "jsonl_root": str(JSONL_ROOT),
                    "sqlite_path": str(SQLITE_PATH),
                    "focus_root": str(FOCUS_ROOT),
                    "wiki_root": str(WIKI_ROOT),
                    "vector_root": str(VECTOR_ROOT),
                    "graph_root": str(GRAPH_ROOT),
                },
            )
            return

        if self.path == "/archive/focus/active":
            write_json(
                self,
                200,
                {
                    "focus_context": active_focus_context(),
                    "max_chars": FOCUS_CONTEXT_CHARS,
                },
            )
            return

        if self.path.startswith("/archive/vector/search"):
            query = ""
            if "?" in self.path:
                from urllib.parse import parse_qs, urlsplit

                params = parse_qs(urlsplit(self.path).query)
                query = (params.get("q") or [""])[0]
            matches = VECTOR_MEMORY.search(query) if VECTOR_MEMORY and query else []
            write_json(self, 200, {"query": query, "matches": matches})
            return

        if self.path.startswith("/archive/graph/search"):
            query = ""
            if "?" in self.path:
                from urllib.parse import parse_qs, urlsplit

                params = parse_qs(urlsplit(self.path).query)
                query = (params.get("q") or [""])[0]
            matches = GRAPH_MEMORY.search(query) if GRAPH_MEMORY and query else {"nodes": [], "edges": []}
            write_json(self, 200, {"query": query, "matches": matches})
            return

        if self.path == "/v1/models":
            self.forward("GET", self.path)
            return

        write_json(self, 404, {"error": "Not found"})

    def do_POST(self):
        if self.path == "/v1/chat/completions":
            self.chat_completion()
            return

        write_json(self, 404, {"error": "Not found"})

    def chat_completion(self):
        with CHAT_QUEUE_LOCK:
            created_at = now_iso()
            turn_id = str(uuid.uuid4())
            payload = read_json(self)
            archive_enabled = internal_flag(payload.pop("archive_enabled", True), default=True)
            focus_enabled = internal_flag(payload.pop("focus_enabled", True), default=True)
            vector_enabled = internal_flag(payload.pop("vector_enabled", focus_enabled), default=True)
            graph_enabled = internal_flag(payload.pop("graph_enabled", focus_enabled), default=True)
            payload["messages"] = list(payload.get("messages", []))
            prepared_payload = dict(payload)
            prepared_payload["messages"] = prepare_messages(
                payload["messages"],
                include_focus=focus_enabled,
                include_vector=vector_enabled,
                include_graph=graph_enabled,
            )

            record = {
                "turn_id": turn_id,
                "created_at": created_at,
                "source": "openai-chat-completions",
                "conversation_id": conversation_id(payload),
                "archive_enabled": archive_enabled,
                "focus_enabled": focus_enabled,
                "vector_enabled": vector_enabled,
                "graph_enabled": graph_enabled,
                "model": payload.get("model"),
                "request": payload,
                "prepared_messages": prepared_payload["messages"],
                "status": "pending",
                "http_status": None,
                "response": None,
                "assistant_message": None,
                "error": None,
            }

            try:
                if prepared_payload.get("stream"):
                    self.stream_chat_completion(prepared_payload, record)
                    return

                status, response = proxy_json("POST", self.path, payload=prepared_payload)
                record["status"] = "ok"
                record["http_status"] = status
                record["response"] = response
                record["assistant_message"] = assistant_message(response)
                maybe_write_archives(record)
                write_json(self, status, response)
                maybe_update_focus_memory(record)
            except HTTPError as exc:
                try:
                    error_payload = json.loads(exc.read().decode("utf-8"))
                except Exception:
                    error_payload = {"error": str(exc)}
                record["status"] = "upstream_error"
                record["http_status"] = exc.code
                record["response"] = error_payload
                record["error"] = json.dumps(error_payload, ensure_ascii=False)
                maybe_write_archives(record)
                write_json(self, exc.code, error_payload)
            except (TimeoutError, URLError) as exc:
                error_payload = {"error": f"LLM host unavailable: {exc}"}
                record["status"] = "unavailable"
                record["http_status"] = 502
                record["response"] = error_payload
                record["error"] = error_payload["error"]
                maybe_write_archives(record)
                write_json(self, 502, error_payload)
            except Exception as exc:
                error_payload = {"error": str(exc)}
                record["status"] = "archive_error"
                record["http_status"] = 500
                record["response"] = error_payload
                record["error"] = error_payload["error"]
                maybe_write_archives(record)
                write_json(self, 500, error_payload)

    def stream_chat_completion(self, prepared_payload, record):
        assistant_parts = []
        finish_reason = None
        streamed_chunks = []

        try:
            with open_upstream("POST", self.path, payload=prepared_payload) as upstream:
                self.send_response(upstream.status)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

                for raw_line in upstream:
                    self.wfile.write(raw_line)
                    self.wfile.flush()
                    decoded = raw_line.decode("utf-8", errors="replace").strip()
                    if not decoded.startswith("data:"):
                        continue

                    data = decoded[5:].strip()
                    if data == "[DONE]":
                        continue

                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    streamed_chunks.append(chunk)
                    delta, chunk_finish = stream_delta(chunk)
                    if delta:
                        assistant_parts.append(delta)
                    if chunk_finish:
                        finish_reason = chunk_finish

            assistant_text = "".join(assistant_parts).strip()
            response = {
                "object": "chat.completion",
                "model": record.get("model"),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": finish_reason or "stop",
                        "message": {"role": "assistant", "content": assistant_text},
                    }
                ],
                "streamed_chunks": streamed_chunks,
            }
            record["status"] = "ok"
            record["http_status"] = 200
            record["response"] = response
            record["assistant_message"] = {"role": "assistant", "content": assistant_text} if assistant_text else None
            maybe_write_archives(record)
            maybe_update_focus_memory(record)
        except HTTPError as exc:
            try:
                error_payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                error_payload = {"error": str(exc)}
            record["status"] = "upstream_error"
            record["http_status"] = exc.code
            record["response"] = error_payload
            record["error"] = json.dumps(error_payload, ensure_ascii=False)
            maybe_write_archives(record)
            write_json(self, exc.code, error_payload)
        except (BrokenPipeError, ConnectionResetError) as exc:
            assistant_text = "".join(assistant_parts).strip()
            record["status"] = "client_disconnected"
            record["http_status"] = 499
            record["response"] = {
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "client_disconnected",
                        "message": {"role": "assistant", "content": assistant_text},
                    }
                ],
                "streamed_chunks": streamed_chunks,
            }
            record["assistant_message"] = {"role": "assistant", "content": assistant_text} if assistant_text else None
            record["error"] = str(exc)
            maybe_write_archives(record)
        except (TimeoutError, URLError) as exc:
            error_payload = {"error": f"LLM host unavailable: {exc}"}
            record["status"] = "unavailable"
            record["http_status"] = 502
            record["response"] = error_payload
            record["error"] = error_payload["error"]
            maybe_write_archives(record)
            write_json(self, 502, error_payload)
        except Exception as exc:
            error_payload = {"error": str(exc)}
            record["status"] = "archive_error"
            record["http_status"] = 500
            record["response"] = error_payload
            record["error"] = error_payload["error"]
            maybe_write_archives(record)
            write_json(self, 500, error_payload)

    def forward(self, method, path, payload=None):
        try:
            status, response = proxy_json(method, path, payload=payload)
            write_json(self, status, response)
        except HTTPError as exc:
            try:
                error_payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                error_payload = {"error": str(exc)}
            write_json(self, exc.code, error_payload)
        except (TimeoutError, URLError) as exc:
            write_json(self, 502, {"error": f"LLM host unavailable: {exc}"})
        except Exception as exc:
            write_json(self, 500, {"error": str(exc)})


def main():
    global FOCUS_BOOKSHELF, LIBRARIAN, VECTOR_MEMORY, GRAPH_MEMORY
    init_storage()
    FOCUS_BOOKSHELF = FocusBookshelf(FOCUS_ROOT)
    VECTOR_MEMORY = VectorMemory(VECTOR_ROOT)
    vector_backfilled = VECTOR_MEMORY.backfill_from_archive(SQLITE_PATH)
    GRAPH_MEMORY = GraphMemory(GRAPH_ROOT, proxy_json, SQLITE_PATH)
    graph_backfilled = GRAPH_MEMORY.backfill_from_archive()
    LIBRARIAN = Librarian(
        FOCUS_ROOT,
        proxy_json,
        wiki_root=WIKI_ROOT,
        sqlite_path=SQLITE_PATH,
        vector_memory=VECTOR_MEMORY,
        graph_memory=GRAPH_MEMORY,
    )
    server = ThreadingHTTPServer((HOST, PORT), ArchiveHandler)
    print(f"ArchiveOfHeresy main started: http://{HOST}:{PORT}", flush=True)
    print(f"Upstream LLM: {LLM_BASE_URL}", flush=True)
    print(f"JSONL archive: {JSONL_ROOT}", flush=True)
    print(f"SQLite archive: {SQLITE_PATH}", flush=True)
    print(f"Focus files: {FOCUS_ROOT}", flush=True)
    print(f"Wiki memory: {WIKI_ROOT}", flush=True)
    print(f"Vector memory: {VECTOR_ROOT}", flush=True)
    print(f"Graph memory: {GRAPH_ROOT}", flush=True)
    print(f"Vector backfill turns: {vector_backfilled}", flush=True)
    print(f"Graph backfill nodes: {graph_backfilled}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
