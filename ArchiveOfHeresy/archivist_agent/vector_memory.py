#!/usr/bin/env python3
import hashlib
import json
import math
import os
import re
import sqlite3
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


VECTOR_DIMENSIONS = int(os.environ.get("ARCHIVE_VECTOR_DIMENSIONS", "384"))
SPARSE_EMBEDDING_VERSION = os.environ.get("ARCHIVE_SPARSE_EMBEDDING_VERSION", "hashed-token-chargram-v2")
VECTOR_EMBEDDING_BACKEND = os.environ.get("ARCHIVE_VECTOR_EMBEDDING_BACKEND", "openai").strip().lower() or "openai"
VECTOR_EMBEDDING_FALLBACK = os.environ.get("ARCHIVE_VECTOR_EMBEDDING_FALLBACK", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
VECTOR_EMBEDDING_BASE_URL = os.environ.get("ARCHIVE_EMBEDDING_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
VECTOR_EMBEDDING_MODEL = os.environ.get(
    "ARCHIVE_EMBEDDING_MODEL",
    "gemma-4-12b-it-UD-Q5_K_XL.gguf",
)
VECTOR_CHUNK_CHARS = int(os.environ.get("ARCHIVE_VECTOR_CHUNK_CHARS", "1200"))
VECTOR_TOP_K = int(os.environ.get("ARCHIVE_VECTOR_TOP_K", "5"))
VECTOR_MIN_SCORE = float(os.environ.get("ARCHIVE_VECTOR_MIN_SCORE", "0.18"))
VECTOR_BACKFILL_ON_START = os.environ.get("ARCHIVE_VECTOR_BACKFILL_ON_START", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
VECTOR_BACKFILL_MAX_TURNS = int(os.environ.get("ARCHIVE_VECTOR_BACKFILL_MAX_TURNS", "200"))
TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]+", re.UNICODE)


def trim_text(value, limit):
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n..."


def tokenize(text):
    return [token.lower() for token in TOKEN_RE.findall(str(text or "")) if len(token) > 1]


def chargrams(token, size=3):
    token = str(token or "").lower()
    if len(token) < size + 1:
        return []
    return [token[index : index + size] for index in range(0, len(token) - size + 1)]


def embed_features(text):
    features = []
    for token in tokenize(text):
        features.append((f"tok:{token}", 1.0))
        for gram in chargrams(token):
            features.append((f"chr:{gram}", 0.35))
    return features


def stable_hash(value):
    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=False)


def sparse_embedding_version():
    return f"sparse:{SPARSE_EMBEDDING_VERSION}:{VECTOR_DIMENSIONS}"


def openai_embedding_version(model=VECTOR_EMBEDDING_MODEL):
    return f"openai:{model}"


def embed_sparse_text(text, dimensions=VECTOR_DIMENSIONS):
    vector = {}
    for feature, weight in embed_features(text):
        hashed = stable_hash(feature)
        index = hashed % dimensions
        sign = -1.0 if (hashed >> 63) else 1.0
        vector[index] = vector.get(index, 0.0) + sign * weight

    norm = math.sqrt(sum(value * value for value in vector.values()))
    if norm <= 0:
        return {}
    return {str(index): value / norm for index, value in vector.items()}


def normalize_dense(values):
    dense = [float(value) for value in values]
    norm = math.sqrt(sum(value * value for value in dense))
    if norm <= 0:
        return []
    return [value / norm for value in dense]


def embed_openai_text(text, base_url=VECTOR_EMBEDDING_BASE_URL, model=VECTOR_EMBEDDING_MODEL, timeout=60):
    payload = {"model": model, "input": str(text or "")}
    request = Request(
        f"{base_url}/v1/embeddings",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8") or "{}")
    data = body.get("data") or []
    if not data:
        raise RuntimeError("embedding response did not include data")
    embedding = data[0].get("embedding")
    if not isinstance(embedding, list):
        raise RuntimeError("embedding response did not include a dense vector")
    normalized = normalize_dense(embedding)
    if not normalized:
        raise RuntimeError("embedding response vector was empty")
    return normalized


def embed_text(text, backend=VECTOR_EMBEDDING_BACKEND):
    if backend == "sparse":
        return embed_sparse_text(text), sparse_embedding_version(), "sparse"
    if backend == "openai":
        try:
            return embed_openai_text(text), openai_embedding_version(), "openai"
        except (HTTPError, URLError, TimeoutError, RuntimeError, json.JSONDecodeError, OSError) as exc:
            if not VECTOR_EMBEDDING_FALLBACK:
                raise
            return embed_sparse_text(text), sparse_embedding_version(), f"sparse_fallback:{exc}"
    return embed_sparse_text(text), sparse_embedding_version(), "sparse"


def cosine_sparse(left, right):
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(index, 0.0) for index, value in left.items())


def cosine_dense(left, right):
    if not left or not right:
        return 0.0
    count = min(len(left), len(right))
    return sum(float(left[index]) * float(right[index]) for index in range(count))


def cosine_embedding(left, right):
    if isinstance(left, list) and isinstance(right, list):
        return cosine_dense(left, right)
    if isinstance(left, dict) and isinstance(right, dict):
        return cosine_sparse(left, right)
    return 0.0


def split_chunks(text, limit=VECTOR_CHUNK_CHARS):
    text = str(text or "").strip()
    if not text:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    chunks = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > limit:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(paragraph), limit):
                chunks.append(paragraph[start : start + limit].strip())
            continue
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) > limit and current:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def latest_user_message(messages):
    for message in reversed(messages or []):
        if message.get("role") == "user":
            return str(message.get("content") or "").strip()
    return ""


class VectorMemory:
    def __init__(self, root):
        self.root = Path(root)
        self.db_path = self.root / "index.sqlite3"
        self.last_backend = None
        self.last_embedding_version = None
        self.resolved_embedding_version = None
        self.root.mkdir(parents=True, exist_ok=True)
        self.init_storage()

    def init_storage(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS vector_chunks (
                    id TEXT PRIMARY KEY,
                    turn_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    memory_namespace TEXT NOT NULL DEFAULT 'default',
                    created_at TEXT NOT NULL,
                    role TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    embedding_version TEXT NOT NULL DEFAULT 'legacy',
                    embedding_json TEXT NOT NULL
                )
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(vector_chunks)")}
            if "memory_namespace" not in columns:
                db.execute("ALTER TABLE vector_chunks ADD COLUMN memory_namespace TEXT NOT NULL DEFAULT 'default'")
            if "embedding_version" not in columns:
                db.execute("ALTER TABLE vector_chunks ADD COLUMN embedding_version TEXT NOT NULL DEFAULT 'legacy'")
            db.execute(
                """
                UPDATE vector_chunks
                SET memory_namespace = 'warmaster'
                WHERE conversation_id = 'warmaster' AND memory_namespace = 'default'
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_vector_chunks_created ON vector_chunks(created_at)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_vector_chunks_turn ON vector_chunks(turn_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_vector_chunks_conversation ON vector_chunks(conversation_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_vector_chunks_namespace_created ON vector_chunks(memory_namespace, created_at)")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS vector_indexed_turns (
                    turn_id TEXT PRIMARY KEY,
                    memory_namespace TEXT NOT NULL DEFAULT 'default',
                    indexed_at TEXT NOT NULL,
                    embedding_version TEXT NOT NULL DEFAULT 'legacy',
                    chunks INTEGER NOT NULL
                )
                """
            )
            indexed_columns = {row[1] for row in db.execute("PRAGMA table_info(vector_indexed_turns)")}
            if "embedding_version" not in indexed_columns:
                db.execute("ALTER TABLE vector_indexed_turns ADD COLUMN embedding_version TEXT NOT NULL DEFAULT 'legacy'")
            db.execute("CREATE INDEX IF NOT EXISTS idx_vector_indexed_namespace ON vector_indexed_turns(memory_namespace)")

    def index_turn(self, record):
        if record.get("status") != "ok":
            return 0
        turn_id = record.get("turn_id")
        conversation_id = record.get("conversation_id") or "unknown"
        memory_namespace = record.get("memory_namespace") or "default"
        created_at = record.get("created_at")
        if not turn_id or not created_at:
            return 0

        entries = []
        request = record.get("request", {})
        # Mobile chat-session records carry the user text in request["text"], not messages.
        user_text = latest_user_message(request.get("messages", [])) or str(request.get("text") or "").strip()
        assistant_text = str((record.get("assistant_message") or {}).get("content") or "").strip()
        if user_text:
            entries.append(("user", user_text))
        if assistant_text:
            entries.append(("assistant", assistant_text))

        rows = []
        active_version = None
        for role, text in entries:
            for chunk_index, chunk in enumerate(split_chunks(text)):
                embedding, embedding_version, backend = embed_text(chunk)
                if not embedding:
                    continue
                active_version = embedding_version
                self.last_backend = backend
                self.last_embedding_version = embedding_version
                chunk_id = f"{turn_id}:{role}:{chunk_index}"
                rows.append(
                    (
                        chunk_id,
                        turn_id,
                        conversation_id,
                        memory_namespace,
                        created_at,
                        role,
                        chunk_index,
                        chunk,
                        embedding_version,
                        json.dumps(embedding, ensure_ascii=False, sort_keys=True),
                    )
                )

        if not rows:
            return 0

        with sqlite3.connect(self.db_path) as db:
            db.executemany(
                """
                INSERT OR REPLACE INTO vector_chunks (
                    id, turn_id, conversation_id, memory_namespace, created_at, role, chunk_index, content, embedding_version, embedding_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            db.execute(
                """
                INSERT OR REPLACE INTO vector_indexed_turns (turn_id, memory_namespace, indexed_at, embedding_version, chunks)
                VALUES (?, ?, datetime('now'), ?, ?)
                """,
                (turn_id, memory_namespace, active_version or sparse_embedding_version(), len(rows)),
            )
        return len(rows)

    def indexed_turn_ids(self):
        if not self.db_path.exists():
            return set()
        version = self.resolve_embedding_version()
        with sqlite3.connect(self.db_path) as db:
            return {
                row[0]
                for row in db.execute(
                    "SELECT turn_id FROM vector_indexed_turns WHERE embedding_version = ?",
                    (version,),
                )
            }

    def current_embedding_version(self):
        if VECTOR_EMBEDDING_BACKEND == "sparse":
            return sparse_embedding_version()
        if VECTOR_EMBEDDING_BACKEND == "openai":
            return openai_embedding_version()
        return sparse_embedding_version()

    def resolve_embedding_version(self):
        if self.resolved_embedding_version:
            return self.resolved_embedding_version
        if VECTOR_EMBEDDING_BACKEND == "openai":
            try:
                embed_openai_text("ArchiveOfHeresy embedding backend probe", timeout=20)
                self.last_backend = "openai"
                self.resolved_embedding_version = openai_embedding_version()
            except Exception as exc:
                if not VECTOR_EMBEDDING_FALLBACK:
                    raise
                self.last_backend = f"sparse_fallback:{exc}"
                self.resolved_embedding_version = sparse_embedding_version()
            self.last_embedding_version = self.resolved_embedding_version
            return self.resolved_embedding_version
        self.last_backend = "sparse"
        self.resolved_embedding_version = sparse_embedding_version()
        self.last_embedding_version = self.resolved_embedding_version
        return self.resolved_embedding_version

    def embedding_status(self):
        versions = {}
        if self.db_path.exists():
            with sqlite3.connect(self.db_path) as db:
                try:
                    rows = db.execute(
                        "SELECT embedding_version, count(*) FROM vector_chunks GROUP BY embedding_version"
                    ).fetchall()
                    versions = {row[0]: int(row[1]) for row in rows}
                except sqlite3.Error:
                    versions = {}
        return {
            "backend": VECTOR_EMBEDDING_BACKEND,
            "fallback_enabled": VECTOR_EMBEDDING_FALLBACK,
            "base_url": VECTOR_EMBEDDING_BASE_URL,
            "model": VECTOR_EMBEDDING_MODEL,
            "current_version": self.current_embedding_version(),
            "resolved_version": self.resolved_embedding_version,
            "last_backend": self.last_backend,
            "last_embedding_version": self.last_embedding_version,
            "versions": versions,
        }

    def backfill_from_archive(self, archive_sqlite_path):
        archive_sqlite_path = Path(archive_sqlite_path)
        if not VECTOR_BACKFILL_ON_START or not archive_sqlite_path.exists():
            return 0

        indexed = 0
        known_turns = self.indexed_turn_ids()
        with sqlite3.connect(archive_sqlite_path) as archive_db:
            archive_db.row_factory = sqlite3.Row
            turn_columns = {row[1] for row in archive_db.execute("PRAGMA table_info(turns)")}
            namespace_select = "memory_namespace" if "memory_namespace" in turn_columns else "'default' AS memory_namespace"
            rows = list(
                archive_db.execute(
                    f"""
                    SELECT id, conversation_id, {namespace_select}, created_at, model, status, http_status, request_json, response_json, error
                    FROM turns
                    WHERE status = 'ok'
                    ORDER BY created_at
                    """
                )
            )

        pending_rows = [row for row in rows if row["id"] not in known_turns]
        if VECTOR_BACKFILL_MAX_TURNS > 0:
            pending_rows = pending_rows[:VECTOR_BACKFILL_MAX_TURNS]

        for row in pending_rows:
            try:
                request = json.loads(row["request_json"] or "{}")
            except json.JSONDecodeError:
                request = {}
            try:
                response = json.loads(row["response_json"] or "{}")
            except json.JSONDecodeError:
                response = {}
            record = {
                "turn_id": row["id"],
                "conversation_id": row["conversation_id"],
                "memory_namespace": row["memory_namespace"] if "memory_namespace" in row.keys() else "default",
                "created_at": row["created_at"],
                "model": row["model"],
                "status": row["status"],
                "http_status": row["http_status"],
                "request": request,
                "response": response,
                "assistant_message": self.response_assistant_message(response),
                "error": row["error"],
            }
            self.index_turn(record)
            indexed += 1
        return indexed

    def response_assistant_message(self, response):
        choices = response.get("choices") or []
        if not choices:
            return None
        message = (choices[0].get("message") or {})
        content = str(message.get("content") or "").strip()
        if not content:
            return None
        return {"role": message.get("role") or "assistant", "content": content}

    def search(self, query, limit=VECTOR_TOP_K, min_score=VECTOR_MIN_SCORE, exclude_turn_id=None, memory_namespace=None):
        query_embedding, query_version, backend = embed_text(query)
        self.last_backend = backend
        self.last_embedding_version = query_version
        if not query_embedding or not self.db_path.exists():
            return []

        results = []
        params = []
        where_parts = ["embedding_version = ?"]
        params.append(query_version)
        if memory_namespace:
            where_parts.append("memory_namespace = ?")
            params.append(str(memory_namespace))
        where = "WHERE " + " AND ".join(where_parts)
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            for row in db.execute(
                f"""
                SELECT id, turn_id, conversation_id, memory_namespace, created_at, role, chunk_index, content, embedding_json
                FROM vector_chunks
                {where}
                ORDER BY created_at DESC
                """,
                params,
            ):
                if exclude_turn_id and row["turn_id"] == exclude_turn_id:
                    continue
                try:
                    embedding = json.loads(row["embedding_json"])
                except json.JSONDecodeError:
                    continue
                score = cosine_embedding(query_embedding, embedding)
                if score < min_score:
                    continue
                results.append(
                    {
                        "score": score,
                        "embedding_version": query_version,
                        "embedding_backend": backend,
                        "turn_id": row["turn_id"],
                        "conversation_id": row["conversation_id"],
                        "memory_namespace": row["memory_namespace"],
                        "created_at": row["created_at"],
                        "role": row["role"],
                        "content": row["content"],
                    }
                )

        results.sort(key=lambda item: (-item["score"], item["created_at"]))
        return results[:limit]

    def context_for_query(self, query, limit=VECTOR_TOP_K, memory_namespace=None):
        matches = self.search(query, limit=limit, memory_namespace=memory_namespace)
        if not matches:
            return ""
        lines = ["# Vector Memory Matches", ""]
        for index, match in enumerate(matches, 1):
            score = match["score"]
            role = match["role"]
            created_at = match["created_at"]
            content = trim_text(match["content"], 900).replace("\n", "\n  ")
            lines.append(f"{index}. score={score:.3f}; role={role}; created_at={created_at}")
            lines.append(f"   {content}")
            lines.append("")
        return "\n".join(lines).strip()
