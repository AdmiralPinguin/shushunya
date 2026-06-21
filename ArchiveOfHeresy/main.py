#!/usr/bin/env python3
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from archivist_agent import Librarian
from archivist_agent.agent import FocusBookshelf, WikiBookshelf
from archivist_agent.graph_memory import GRAPH_TOP_K, GraphMemory
from archivist_agent.magos_agent import Magos
from archivist_agent.vector_memory import VECTOR_TOP_K, VectorMemory, latest_user_message


ROOT = Path(__file__).resolve().parent
HOST = os.environ.get("ARCHIVE_HOST", "127.0.0.1")
PORT = int(os.environ.get("ARCHIVE_PORT", "8090"))
ARCHIVE_API_KEY = os.environ.get("ARCHIVE_API_KEY", "").strip()
LLM_BASE_URL = os.environ.get("ARCHIVE_LLM_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
JSONL_ROOT = Path(os.environ.get("ARCHIVE_JSONL_ROOT", ROOT / "archive" / "jsonl"))
MEMORY_EVENTS_ROOT = Path(os.environ.get("ARCHIVE_MEMORY_EVENTS_ROOT", ROOT / "archive" / "memory_events"))
SQLITE_PATH = Path(os.environ.get("ARCHIVE_SQLITE_PATH", ROOT / "archive" / "sqlite" / "archive.sqlite3"))
FOCUS_ROOT = Path(os.environ.get("ARCHIVE_FOCUS_ROOT", ROOT / "focus"))
WIKI_ROOT = Path(os.environ.get("ARCHIVE_WIKI_ROOT", ROOT / "wiki"))
VECTOR_ROOT = Path(os.environ.get("ARCHIVE_VECTOR_ROOT", ROOT / "vector"))
GRAPH_ROOT = Path(os.environ.get("ARCHIVE_GRAPH_ROOT", ROOT / "graph"))
FOCUS_CONTEXT_CHARS = int(os.environ.get("ARCHIVE_FOCUS_CONTEXT_CHARS", "6000"))
VECTOR_CONTEXT_CHARS = int(os.environ.get("ARCHIVE_VECTOR_CONTEXT_CHARS", "5000"))
GRAPH_CONTEXT_CHARS = int(os.environ.get("ARCHIVE_GRAPH_CONTEXT_CHARS", "5000"))
VECTOR_INJECTION_ENABLED = os.environ.get("ARCHIVE_VECTOR_INJECTION_ENABLED", "0").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
GRAPH_INJECTION_ENABLED = os.environ.get("ARCHIVE_GRAPH_INJECTION_ENABLED", "0").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
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
MAGOS = None
FOCUS_BOOKSHELF = None
FOCUS_COMPONENTS = {}
GRAPH_COMPONENTS = {}
VECTOR_MEMORY = None
GRAPH_MEMORY = None
TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]+", re.UNICODE)


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


def authorized(handler):
    if not ARCHIVE_API_KEY:
        return True

    auth = handler.headers.get("Authorization", "").strip()
    expected = f"Bearer {ARCHIVE_API_KEY}"
    return auth == expected


def require_auth(handler):
    if authorized(handler):
        return True
    write_json(
        handler,
        401,
        {
            "error": {
                "message": "Missing or invalid API key",
                "type": "authentication_error",
            }
        },
    )
    return False


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


def safe_memory_namespace(value):
    raw = str(value or "default").strip().lower()
    safe = "".join(char if char.isalnum() or char in ("-", "_") else "-" for char in raw).strip("-_")
    return safe[:64] or "default"


def focus_root_for_namespace(namespace):
    namespace = safe_memory_namespace(namespace)
    if namespace == "default":
        return FOCUS_ROOT
    return FOCUS_ROOT / "namespaces" / namespace


def graph_root_for_namespace(namespace):
    namespace = safe_memory_namespace(namespace)
    if namespace == "default":
        return GRAPH_ROOT
    return GRAPH_ROOT / "namespaces" / namespace


def wiki_root_for_namespace(namespace):
    namespace = safe_memory_namespace(namespace)
    if namespace == "default":
        return WIKI_ROOT
    return WIKI_ROOT / "namespaces" / namespace


def existing_child_namespaces(root):
    namespace_root = Path(root) / "namespaces"
    if not namespace_root.exists():
        return set()
    return {
        safe_memory_namespace(path.name)
        for path in namespace_root.iterdir()
        if path.is_dir() and safe_memory_namespace(path.name) != "default"
    }


def known_memory_namespaces():
    namespaces = {"default"}
    namespaces.update(FOCUS_COMPONENTS.keys())
    namespaces.update(GRAPH_COMPONENTS.keys())
    namespaces.update(existing_child_namespaces(FOCUS_ROOT))
    namespaces.update(existing_child_namespaces(WIKI_ROOT))
    namespaces.update(existing_child_namespaces(GRAPH_ROOT))
    return sorted(namespaces)


def wiki_bookshelf_for_namespace(namespace):
    return WikiBookshelf(wiki_root_for_namespace(namespace))


def find_focus(index, focus_id=None, active=False):
    target_id = index.get("active_id") if active else focus_id
    for focus in index.get("files", []):
        if focus.get("id") == target_id:
            return focus
    return None


def vector_stats(memory_namespace):
    if VECTOR_MEMORY is None or not VECTOR_MEMORY.db_path.exists():
        return {"chunks": 0, "turns": 0}
    with sqlite3.connect(VECTOR_MEMORY.db_path) as db:
        row = db.execute(
            """
            SELECT count(*) AS chunks, count(DISTINCT turn_id) AS turns
            FROM vector_chunks
            WHERE memory_namespace = ?
            """,
            (memory_namespace,),
        ).fetchone()
    return {"chunks": int(row[0] or 0), "turns": int(row[1] or 0)}


def graph_stats(memory_namespace):
    graph_memory = graph_memory_for_namespace(memory_namespace)
    if graph_memory is None or not graph_memory.db_path.exists():
        return {"nodes": 0, "edges": 0}
    with sqlite3.connect(graph_memory.db_path) as db:
        nodes = int(db.execute("SELECT count(*) FROM graph_nodes").fetchone()[0] or 0)
        edges = int(db.execute("SELECT count(*) FROM graph_edges").fetchone()[0] or 0)
    return {"nodes": nodes, "edges": edges}


def memory_tokens(value):
    return {token.lower() for token in TOKEN_RE.findall(str(value or "")) if len(token) > 1}


def memory_overlap_score(query_tokens, value):
    if not query_tokens:
        return 0.0
    target = memory_tokens(value)
    if not target:
        return 0.0
    return len(query_tokens & target) / max(1, min(len(query_tokens), len(target)))


def trim_memory_text(value, limit=1200):
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n..."


def wiki_search(memory_namespace, query, limit=5):
    query_tokens = memory_tokens(query)
    if not query_tokens:
        return []
    bookshelf = wiki_bookshelf_for_namespace(memory_namespace)
    index = bookshelf.load_index()
    matches = []
    for page in index.get("pages", []):
        content = bookshelf.read_page(page)
        score = memory_overlap_score(
            query_tokens,
            " ".join([str(page.get("title") or ""), str(page.get("kind") or ""), content]),
        )
        if score <= 0:
            continue
        matches.append(
            {
                "score": score,
                "id": page.get("id"),
                "title": page.get("title"),
                "kind": page.get("kind"),
                "importance": page.get("importance"),
                "updated_at": page.get("updated_at"),
                "excerpt": trim_memory_text(content, 1400),
            }
        )
    matches.sort(key=lambda item: (-item["score"], -int(item.get("importance") or 0), item.get("updated_at") or ""))
    return matches[:limit]


def focus_search(memory_namespace, query, limit=5):
    query_tokens = memory_tokens(query)
    if not query_tokens:
        return []
    bookshelf = focus_components(memory_namespace)["bookshelf"]
    index = bookshelf.load_index()
    matches = []
    for focus in index.get("files", []):
        content = bookshelf.read_focus(focus)
        score = memory_overlap_score(
            query_tokens,
            " ".join([str(focus.get("title") or ""), str(focus.get("status") or ""), content]),
        )
        if score <= 0 and focus.get("id") != index.get("active_id"):
            continue
        matches.append(
            {
                "score": score,
                "id": focus.get("id"),
                "title": focus.get("title"),
                "status": focus.get("status"),
                "importance": focus.get("importance"),
                "updated_at": focus.get("updated_at"),
                "active": focus.get("id") == index.get("active_id"),
                "excerpt": trim_memory_text(content, 1400),
            }
        )
    matches.sort(key=lambda item: (not item["active"], -item["score"], -int(item.get("importance") or 0), item.get("updated_at") or ""))
    return matches[:limit]


def memory_search(memory_namespace, query, limit=5):
    namespace = safe_memory_namespace(memory_namespace)
    query = str(query or "").strip()
    try:
        safe_limit = max(1, min(int(limit or 5), 20))
    except (TypeError, ValueError):
        safe_limit = 5
    vector_matches = VECTOR_MEMORY.search(query, limit=safe_limit, memory_namespace=namespace) if VECTOR_MEMORY and query else []
    graph_memory = graph_memory_for_namespace(namespace)
    graph_matches = graph_memory.search(query, limit=safe_limit) if graph_memory and query else {"nodes": [], "edges": []}
    return {
        "ok": True,
        "memory_namespace": namespace,
        "query": query,
        "limit": safe_limit,
        "warning": "Gateway search is reference memory only. Treat current task/tool results as fresher than memory.",
        "focus": focus_search(namespace, query, safe_limit),
        "wiki": wiki_search(namespace, query, safe_limit),
        "vector": vector_matches,
        "graph": graph_matches,
    }


def memory_catalog(memory_namespace):
    namespace = safe_memory_namespace(memory_namespace)
    bookshelf = focus_components(namespace)["bookshelf"]
    focus_index = bookshelf.load_index()
    wiki_bookshelf = wiki_bookshelf_for_namespace(namespace)
    wiki_index = wiki_bookshelf.load_index()
    return {
        "memory_namespace": namespace,
        "gateway": {
            "read_endpoints": [
                "/archive/memory/catalog",
                "/archive/memory/focus",
                "/archive/memory/wiki",
                "/archive/memory/search",
                "/archive/vector/search",
                "/archive/graph/search",
                "/archive/memory/events",
            ],
            "write_endpoint": "/archive/memory/propose-change",
            "write_policy": "Agents propose changes; ArchiveOfHeresy records the proposal and the librarian decides how to update memory.",
        },
        "focus": bookshelf.catalog(focus_index),
        "wiki": wiki_bookshelf.catalog(wiki_index),
        "vector": vector_stats(namespace),
        "graph": graph_stats(namespace),
        "recent_events": recent_memory_events(limit=5, memory_namespace=namespace),
    }


def graph_memory_for_namespace(namespace):
    namespace = safe_memory_namespace(namespace)
    cached = GRAPH_COMPONENTS.get(namespace)
    if cached is not None:
        return cached
    graph_memory = GraphMemory(
        graph_root_for_namespace(namespace),
        proxy_json,
        SQLITE_PATH,
        memory_namespace=namespace,
    )
    GRAPH_COMPONENTS[namespace] = graph_memory
    return graph_memory


def focus_components(namespace):
    namespace = safe_memory_namespace(namespace)
    cached = FOCUS_COMPONENTS.get(namespace)
    if cached is not None:
        return cached
    root = focus_root_for_namespace(namespace)
    bookshelf = FocusBookshelf(root)
    librarian = Librarian(
        root,
        proxy_json,
        wiki_root=wiki_root_for_namespace(namespace),
        sqlite_path=SQLITE_PATH,
        vector_memory=VECTOR_MEMORY,
        graph_memory=graph_memory_for_namespace(namespace),
        memory_namespace=namespace,
    )
    magos = Magos(
        root,
        wiki_root_for_namespace(namespace),
        proxy_json,
        vector_memory=VECTOR_MEMORY,
        graph_memory=graph_memory_for_namespace(namespace),
    )
    cached = {"bookshelf": bookshelf, "librarian": librarian, "magos": magos, "root": root}
    FOCUS_COMPONENTS[namespace] = cached
    return cached


def active_focus_context(namespace="default"):
    bookshelf = focus_components(namespace)["bookshelf"]
    if bookshelf is None:
        return ""

    index = bookshelf.load_index()
    active = bookshelf.active_focus(index)
    if not active:
        return ""

    content = bookshelf.read_focus(active).strip()
    if not content:
        return ""

    return content[-FOCUS_CONTEXT_CHARS:]


def focus_context_message(namespace="default"):
    content = active_focus_context(namespace)
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


def vector_context_message(query, memory_namespace="default"):
    if not VECTOR_INJECTION_ENABLED:
        return None
    if VECTOR_MEMORY is None:
        return None
    content = VECTOR_MEMORY.context_for_query(query, limit=VECTOR_TOP_K, memory_namespace=memory_namespace).strip()
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


def graph_context_message(query, memory_namespace="default"):
    if not GRAPH_INJECTION_ENABLED:
        return None
    graph_memory = graph_memory_for_namespace(memory_namespace)
    if graph_memory is None:
        return None
    content = graph_memory.context_for_query(query, limit=GRAPH_TOP_K).strip()
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


def text_from_content(content):
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return str(content or "").strip()

    parts = []
    image_count = 0
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "text":
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
        elif item_type == "image_url":
            image_count += 1
    if image_count:
        parts.append(f"[Пользователь приложил изображение: {image_count} шт. Содержимое изображения доступно Шушуне, но не анализируется Magos/памятью.]")
    return "\n".join(parts).strip()


def sanitize_messages_for_memory(messages):
    sanitized = []
    for message in messages or []:
        copy = dict(message)
        copy["content"] = text_from_content(copy.get("content"))
        sanitized.append(copy)
    return sanitized


def maybe_write_archives(record):
    if record.get("archive_enabled", True):
        write_archives(record)


def maybe_update_focus_memory(record):
    if record.get("archive_enabled", True):
        update_focus_memory(record)


def prepare_messages(
    messages,
    include_focus=True,
    include_vector=True,
    include_graph=True,
    magos_message=None,
    query_messages=None,
    memory_namespace="default",
):
    prepared = [{"role": "system", "content": ARCHIVE_SYSTEM_PROMPT}]
    query = latest_user_message(query_messages if query_messages is not None else messages)
    if include_focus:
        focus_message = focus_context_message(memory_namespace)
        if focus_message:
            prepared.append(focus_message)
    if magos_message:
        prepared.append(magos_message)
    if include_vector:
        vector_message = vector_context_message(query, memory_namespace=memory_namespace)
        if vector_message:
            prepared.append(vector_message)
    if include_graph:
        graph_message = graph_context_message(query, memory_namespace=memory_namespace)
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


def daily_memory_events_path(created_at):
    dt = datetime.fromisoformat(created_at)
    return MEMORY_EVENTS_ROOT / f"{dt.year:04d}" / f"{dt.month:02d}" / f"{dt.date().isoformat()}.jsonl"


def init_storage():
    JSONL_ROOT.mkdir(parents=True, exist_ok=True)
    MEMORY_EVENTS_ROOT.mkdir(parents=True, exist_ok=True)
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
                memory_namespace TEXT NOT NULL DEFAULT 'default',
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
        turn_columns = {row[1] for row in db.execute("PRAGMA table_info(turns)")}
        if "memory_namespace" not in turn_columns:
            db.execute("ALTER TABLE turns ADD COLUMN memory_namespace TEXT NOT NULL DEFAULT 'default'")
        db.execute(
            """
            UPDATE turns
            SET memory_namespace = 'agent'
            WHERE conversation_id = 'shushunya-agent' AND memory_namespace = 'default'
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
        db.execute("CREATE INDEX IF NOT EXISTS idx_turns_namespace_created ON turns(memory_namespace, created_at)")
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
                    id, conversation_id, memory_namespace, created_at, model, status, http_status,
                    request_json, prepared_messages_json, response_json, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["turn_id"],
                    record["conversation_id"],
                    record.get("memory_namespace") or "default",
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


def write_memory_event(record, event):
    payload = {
        "created_at": now_iso(),
        "turn_created_at": record.get("created_at"),
        "turn_id": record.get("turn_id"),
        "conversation_id": record.get("conversation_id"),
        "memory_namespace": record.get("memory_namespace") or "default",
        "event": event,
    }
    with ARCHIVE_LOCK:
        path = daily_memory_events_path(record["created_at"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as archive:
            archive.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def write_gateway_event(memory_namespace, action, requester=None, **details):
    namespace = safe_memory_namespace(memory_namespace)
    requester = str(requester or "unknown").strip()[:80] or "unknown"
    record = {
        "created_at": now_iso(),
        "turn_id": str(uuid.uuid4()),
        "conversation_id": f"memory-gateway:{requester}",
        "memory_namespace": namespace,
    }
    write_memory_event(
        record,
        {
            "component": "memory_gateway",
            "action": action,
            "requester": requester,
            **details,
        },
    )


def recent_memory_events(limit=50, memory_namespace=None):
    limit = max(1, min(int(limit or 50), 500))
    events = []
    paths = sorted(MEMORY_EVENTS_ROOT.glob("*/*/*.jsonl"), reverse=True)
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if memory_namespace and event.get("memory_namespace") != memory_namespace:
                continue
            events.append(event)
            if len(events) >= limit:
                return events
    return events


def update_focus_memory(record):
    namespace = record.get("memory_namespace") or "default"
    librarian = focus_components(namespace)["librarian"]
    if librarian is None:
        return
    try:
        event = librarian.process_turn(record)
        write_memory_event(record, {"component": "librarian", "result": event})
    except Exception as exc:
        write_memory_event(record, {"component": "librarian", "status": "error", "error": str(exc)})
        print(f"Librarian error namespace={namespace}: {exc}", flush=True)


def maybe_abandon_magos_focus(record):
    namespace = record.get("memory_namespace") or "default"
    magos = focus_components(namespace)["magos"]
    if magos is None:
        return
    if record.get("status") == "ok":
        return
    try:
        magos.abandon_created_focus(record.get("turn_id"), f"model request ended with status={record.get('status')}")
    except Exception as exc:
        print(f"Magos abandon error namespace={namespace}: {exc}", flush=True)


class ArchiveHandler(BaseHTTPRequestHandler):
    server_version = "ArchiveOfHeresy/0.1"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def do_GET(self):
        if self.path == "/health":
            namespaces = known_memory_namespaces()
            write_json(
                self,
                200,
                {
                    "status": "ok",
                    "service": "ArchiveOfHeresy",
                    "llm_base_url": LLM_BASE_URL,
                    "jsonl_root": str(JSONL_ROOT),
                    "memory_events_root": str(MEMORY_EVENTS_ROOT),
                    "sqlite_path": str(SQLITE_PATH),
                    "focus_root": str(FOCUS_ROOT),
                    "focus_namespaces": {
                        namespace: str(focus_root_for_namespace(namespace))
                        for namespace in namespaces
                    },
                    "wiki_root": str(WIKI_ROOT),
                    "wiki_namespaces": {
                        namespace: str(wiki_root_for_namespace(namespace))
                        for namespace in namespaces
                    },
                    "vector_root": str(VECTOR_ROOT),
                    "graph_root": str(GRAPH_ROOT),
                    "graph_namespaces": {
                        namespace: str(graph_root_for_namespace(namespace))
                        for namespace in namespaces
                    },
                },
            )
            return

        if not require_auth(self):
            return

        if self.path.startswith("/archive/focus/active"):
            namespace = "default"
            if "?" in self.path:
                params = parse_qs(urlsplit(self.path).query)
                namespace = safe_memory_namespace((params.get("namespace") or ["default"])[0])
            write_json(
                self,
                200,
                {
                    "memory_namespace": namespace,
                    "focus_context": active_focus_context(namespace),
                    "max_chars": FOCUS_CONTEXT_CHARS,
                },
            )
            return

        if self.path.startswith("/archive/vector/search"):
            query = ""
            namespace = "default"
            if "?" in self.path:
                params = parse_qs(urlsplit(self.path).query)
                query = (params.get("q") or [""])[0]
                namespace = safe_memory_namespace((params.get("namespace") or ["default"])[0])
            matches = VECTOR_MEMORY.search(query, memory_namespace=namespace) if VECTOR_MEMORY and query else []
            write_json(self, 200, {"query": query, "memory_namespace": namespace, "matches": matches})
            return

        if self.path.startswith("/archive/graph/search"):
            query = ""
            namespace = "default"
            if "?" in self.path:
                params = parse_qs(urlsplit(self.path).query)
                query = (params.get("q") or [""])[0]
                namespace = safe_memory_namespace((params.get("namespace") or ["default"])[0])
            graph_memory = graph_memory_for_namespace(namespace)
            matches = graph_memory.search(query) if graph_memory and query else {"nodes": [], "edges": []}
            write_json(self, 200, {"query": query, "memory_namespace": namespace, "matches": matches})
            return

        if self.path.startswith("/archive/memory/events"):
            namespace = None
            limit = 50
            if "?" in self.path:
                params = parse_qs(urlsplit(self.path).query)
                raw_namespace = (params.get("namespace") or [""])[0]
                namespace = safe_memory_namespace(raw_namespace) if raw_namespace else None
                try:
                    limit = int((params.get("limit") or ["50"])[0])
                except (TypeError, ValueError):
                    limit = 50
            write_json(
                self,
                200,
                {
                    "memory_namespace": namespace,
                    "limit": max(1, min(limit, 500)),
                    "events": recent_memory_events(limit=limit, memory_namespace=namespace),
                },
            )
            return

        if self.path.startswith("/archive/memory/catalog"):
            namespace = "default"
            requester = "unknown"
            if "?" in self.path:
                params = parse_qs(urlsplit(self.path).query)
                namespace = safe_memory_namespace((params.get("namespace") or ["default"])[0])
                requester = (params.get("requester") or ["unknown"])[0]
            payload = memory_catalog(namespace)
            write_gateway_event(
                namespace,
                "catalog",
                requester=requester,
                focus_books=len(payload.get("focus", {}).get("books", [])),
                wiki_pages=len(payload.get("wiki", {}).get("pages", [])),
            )
            write_json(self, 200, payload)
            return

        if self.path.startswith("/archive/memory/search"):
            namespace = "default"
            query = ""
            limit = 5
            requester = "unknown"
            if "?" in self.path:
                params = parse_qs(urlsplit(self.path).query)
                namespace = safe_memory_namespace((params.get("namespace") or ["default"])[0])
                query = (params.get("q") or [""])[0]
                requester = (params.get("requester") or ["unknown"])[0]
                try:
                    limit = int((params.get("limit") or ["5"])[0])
                except (TypeError, ValueError):
                    limit = 5
            if not query.strip():
                write_json(self, 400, {"error": "Missing required query parameter: q", "memory_namespace": namespace})
                return
            payload = memory_search(namespace, query, limit=limit)
            write_gateway_event(
                namespace,
                "search",
                requester=requester,
                query=trim_memory_text(query, 300),
                focus_matches=len(payload.get("focus", [])),
                wiki_matches=len(payload.get("wiki", [])),
                vector_matches=len(payload.get("vector", [])),
                graph_nodes=len(payload.get("graph", {}).get("nodes", [])),
            )
            write_json(self, 200, payload)
            return

        if self.path.startswith("/archive/memory/focus"):
            namespace = "default"
            focus_id = ""
            active = False
            requester = "unknown"
            if "?" in self.path:
                params = parse_qs(urlsplit(self.path).query)
                namespace = safe_memory_namespace((params.get("namespace") or ["default"])[0])
                focus_id = (params.get("id") or [""])[0]
                active = focus_id in ("", "active")
                requester = (params.get("requester") or ["unknown"])[0]
            bookshelf = focus_components(namespace)["bookshelf"]
            index = bookshelf.load_index()
            focus = find_focus(index, focus_id=focus_id, active=active)
            if not focus:
                write_json(self, 404, {"error": "Focus not found", "memory_namespace": namespace, "id": focus_id or "active"})
                return
            write_gateway_event(
                namespace,
                "read_focus",
                requester=requester,
                focus_id=focus.get("id"),
                title=focus.get("title"),
                active=focus.get("id") == index.get("active_id"),
            )
            write_json(
                self,
                200,
                {
                    "memory_namespace": namespace,
                    "focus": focus,
                    "content": bookshelf.read_focus(focus),
                },
            )
            return

        if self.path.startswith("/archive/memory/wiki"):
            namespace = "default"
            page_id = ""
            title = ""
            requester = "unknown"
            if "?" in self.path:
                params = parse_qs(urlsplit(self.path).query)
                namespace = safe_memory_namespace((params.get("namespace") or ["default"])[0])
                page_id = (params.get("id") or [""])[0]
                title = (params.get("title") or [""])[0]
                requester = (params.get("requester") or ["unknown"])[0]
            bookshelf = wiki_bookshelf_for_namespace(namespace)
            index = bookshelf.load_index()
            page = bookshelf.find_page(index, page_id=page_id or None, title=title or None)
            if not page:
                write_gateway_event(
                    namespace,
                    "read_wiki_miss",
                    requester=requester,
                    page_id=page_id,
                    title=title,
                )
                write_json(
                    self,
                    404,
                    {"error": "Wiki page not found", "memory_namespace": namespace, "id": page_id, "title": title},
                )
                return
            write_gateway_event(
                namespace,
                "read_wiki",
                requester=requester,
                page_id=page.get("id"),
                title=page.get("title"),
            )
            write_json(
                self,
                200,
                {
                    "memory_namespace": namespace,
                    "page": page,
                    "content": bookshelf.read_page(page),
                },
            )
            return

        if self.path == "/v1/models":
            self.forward("GET", self.path)
            return

        write_json(self, 404, {"error": "Not found"})

    def do_POST(self):
        if not require_auth(self):
            return

        if self.path == "/v1/chat/completions":
            self.chat_completion()
            return

        if self.path == "/archive/memory/propose-change":
            self.memory_propose_change()
            return

        write_json(self, 404, {"error": "Not found"})

    def memory_propose_change(self):
        with CHAT_QUEUE_LOCK:
            created_at = now_iso()
            turn_id = str(uuid.uuid4())
            try:
                payload = read_json(self)
            except json.JSONDecodeError as exc:
                write_json(self, 400, {"error": f"Invalid JSON: {exc}"})
                return

            namespace = safe_memory_namespace(payload.get("namespace") or payload.get("memory_namespace") or "default")
            requester = str(payload.get("requester") or "memory-gateway").strip()[:80] or "memory-gateway"
            proposal = str(payload.get("proposal") or "").strip()
            if not proposal:
                write_json(self, 400, {"error": "Missing required field: proposal", "memory_namespace": namespace})
                return

            target = str(payload.get("target") or "auto").strip().lower()[:40] or "auto"
            evidence = str(payload.get("evidence") or "").strip()
            importance = payload.get("importance", 3)
            try:
                importance = max(1, min(5, int(importance)))
            except (TypeError, ValueError):
                importance = 3

            proposal_payload = {
                "type": "memory_change_proposal",
                "requester": requester,
                "memory_namespace": namespace,
                "target": target,
                "importance": importance,
                "proposal": proposal,
                "evidence": evidence,
                "instruction": (
                    "This is a proposed memory update from an agent through Memory Gateway. "
                    "Do not apply it blindly. Evaluate it as a normal archived turn and let the librarian decide "
                    "what belongs in focus, wiki, vector, and graph memory."
                ),
            }
            request_payload = {
                "user": f"memory-gateway:{requester}",
                "memory_namespace": namespace,
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(proposal_payload, ensure_ascii=False, indent=2),
                    }
                ],
            }
            assistant_text = (
                "Memory Gateway accepted the proposal for ArchiveOfHeresy librarian review. "
                "The requester does not receive direct write access to memory files."
            )
            response = {
                "object": "archive.memory.proposal",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "accepted",
                        "message": {"role": "assistant", "content": assistant_text},
                    }
                ],
            }
            record = {
                "turn_id": turn_id,
                "created_at": created_at,
                "source": "memory-gateway-proposal",
                "conversation_id": f"memory-gateway:{requester}",
                "memory_namespace": namespace,
                "archive_enabled": True,
                "focus_enabled": True,
                "vector_enabled": True,
                "graph_enabled": True,
                "magos_enabled": False,
                "magos_result": None,
                "model": "archive-memory-gateway",
                "request": request_payload,
                "prepared_messages": request_payload["messages"],
                "status": "ok",
                "http_status": 202,
                "response": response,
                "assistant_message": {"role": "assistant", "content": assistant_text},
                "error": None,
            }

            maybe_write_archives(record)
            write_gateway_event(
                namespace,
                "proposal_accepted",
                requester=requester,
                target=target,
                importance=importance,
                turn_id=turn_id,
            )
            maybe_update_focus_memory(record)
            write_json(
                self,
                202,
                {
                    "ok": True,
                    "turn_id": turn_id,
                    "memory_namespace": namespace,
                    "requester": requester,
                    "target": target,
                    "message": "Proposal queued through ArchiveOfHeresy librarian cycle.",
                },
            )

    def chat_completion(self):
        with CHAT_QUEUE_LOCK:
            created_at = now_iso()
            turn_id = str(uuid.uuid4())
            payload = read_json(self)
            archive_enabled = internal_flag(payload.pop("archive_enabled", True), default=True)
            focus_enabled = internal_flag(payload.pop("focus_enabled", True), default=True)
            vector_enabled = internal_flag(payload.pop("vector_enabled", focus_enabled), default=True)
            graph_enabled = internal_flag(payload.pop("graph_enabled", focus_enabled), default=True)
            memory_namespace = safe_memory_namespace(payload.pop("memory_namespace", "default"))
            payload["messages"] = list(payload.get("messages", []))
            memory_messages = sanitize_messages_for_memory(payload["messages"])
            magos_message = None
            magos_result = None
            magos = focus_components(memory_namespace)["magos"]
            if focus_enabled and magos is not None:
                try:
                    magos_message = magos.prepare_request(
                        memory_messages,
                        model=payload.get("model"),
                        conversation_id=conversation_id(payload),
                        turn_id=turn_id,
                        memory_namespace=memory_namespace,
                    )
                    magos_result = magos.last_result
                except Exception as exc:
                    print(f"Magos hard fail-soft: {exc}", flush=True)
                    magos_message = None
                    magos_result = {"error": str(exc)}
            prepared_payload = dict(payload)
            prepared_payload["messages"] = prepare_messages(
                payload["messages"],
                include_focus=focus_enabled,
                include_vector=vector_enabled,
                include_graph=graph_enabled,
                magos_message=magos_message,
                query_messages=memory_messages,
                memory_namespace=memory_namespace,
            )
            sanitized_payload = dict(payload)
            sanitized_payload["messages"] = memory_messages
            archive_prepared_messages = prepare_messages(
                memory_messages,
                include_focus=focus_enabled,
                include_vector=vector_enabled,
                include_graph=graph_enabled,
                magos_message=magos_message,
                query_messages=memory_messages,
                memory_namespace=memory_namespace,
            )

            record = {
                "turn_id": turn_id,
                "created_at": created_at,
                "source": "openai-chat-completions",
                "conversation_id": conversation_id(payload),
                "memory_namespace": memory_namespace,
                "archive_enabled": archive_enabled,
                "focus_enabled": focus_enabled,
                "vector_enabled": vector_enabled,
                "graph_enabled": graph_enabled,
                "magos_enabled": bool(magos_message),
                "magos_result": magos_result,
                "model": payload.get("model"),
                "request": sanitized_payload,
                "prepared_messages": archive_prepared_messages,
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
                maybe_abandon_magos_focus(record)
                maybe_write_archives(record)
                write_json(self, exc.code, error_payload)
            except (TimeoutError, URLError) as exc:
                error_payload = {"error": f"LLM host unavailable: {exc}"}
                record["status"] = "unavailable"
                record["http_status"] = 502
                record["response"] = error_payload
                record["error"] = error_payload["error"]
                maybe_abandon_magos_focus(record)
                maybe_write_archives(record)
                write_json(self, 502, error_payload)
            except Exception as exc:
                error_payload = {"error": str(exc)}
                record["status"] = "archive_error"
                record["http_status"] = 500
                record["response"] = error_payload
                record["error"] = error_payload["error"]
                maybe_abandon_magos_focus(record)
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
            maybe_abandon_magos_focus(record)
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
            maybe_abandon_magos_focus(record)
            maybe_write_archives(record)
        except (TimeoutError, URLError) as exc:
            error_payload = {"error": f"LLM host unavailable: {exc}"}
            record["status"] = "unavailable"
            record["http_status"] = 502
            record["response"] = error_payload
            record["error"] = error_payload["error"]
            maybe_abandon_magos_focus(record)
            maybe_write_archives(record)
            write_json(self, 502, error_payload)
        except Exception as exc:
            error_payload = {"error": str(exc)}
            record["status"] = "archive_error"
            record["http_status"] = 500
            record["response"] = error_payload
            record["error"] = error_payload["error"]
            maybe_abandon_magos_focus(record)
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
    global FOCUS_BOOKSHELF, LIBRARIAN, MAGOS, VECTOR_MEMORY, GRAPH_MEMORY, FOCUS_COMPONENTS, GRAPH_COMPONENTS
    init_storage()
    FOCUS_COMPONENTS = {}
    GRAPH_COMPONENTS = {}
    VECTOR_MEMORY = VectorMemory(VECTOR_ROOT)
    vector_backfilled = VECTOR_MEMORY.backfill_from_archive(SQLITE_PATH)
    GRAPH_MEMORY = graph_memory_for_namespace("default")
    graph_backfilled = GRAPH_MEMORY.backfill_from_archive()
    default_components = focus_components("default")
    FOCUS_BOOKSHELF = default_components["bookshelf"]
    LIBRARIAN = default_components["librarian"]
    MAGOS = default_components["magos"]
    server = ThreadingHTTPServer((HOST, PORT), ArchiveHandler)
    print(f"ArchiveOfHeresy main started: http://{HOST}:{PORT}", flush=True)
    print(f"Upstream LLM: {LLM_BASE_URL}", flush=True)
    print(f"JSONL archive: {JSONL_ROOT}", flush=True)
    print(f"Memory events: {MEMORY_EVENTS_ROOT}", flush=True)
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
