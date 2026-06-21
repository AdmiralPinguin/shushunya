#!/usr/bin/env python3
import hashlib
import json
import math
import os
import re
import sqlite3
from pathlib import Path


VECTOR_DIMENSIONS = int(os.environ.get("ARCHIVE_VECTOR_DIMENSIONS", "384"))
VECTOR_CHUNK_CHARS = int(os.environ.get("ARCHIVE_VECTOR_CHUNK_CHARS", "1200"))
VECTOR_TOP_K = int(os.environ.get("ARCHIVE_VECTOR_TOP_K", "5"))
VECTOR_MIN_SCORE = float(os.environ.get("ARCHIVE_VECTOR_MIN_SCORE", "0.18"))
VECTOR_BACKFILL_ON_START = os.environ.get("ARCHIVE_VECTOR_BACKFILL_ON_START", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]+", re.UNICODE)


def trim_text(value, limit):
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n..."


def tokenize(text):
    return [token.lower() for token in TOKEN_RE.findall(str(text or "")) if len(token) > 1]


def stable_hash(value):
    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=False)


def embed_text(text, dimensions=VECTOR_DIMENSIONS):
    vector = {}
    for token in tokenize(text):
        hashed = stable_hash(token)
        index = hashed % dimensions
        sign = -1.0 if (hashed >> 63) else 1.0
        vector[index] = vector.get(index, 0.0) + sign

    norm = math.sqrt(sum(value * value for value in vector.values()))
    if norm <= 0:
        return {}
    return {str(index): value / norm for index, value in vector.items()}


def cosine_sparse(left, right):
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(index, 0.0) for index, value in left.items())


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
                    embedding_json TEXT NOT NULL
                )
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(vector_chunks)")}
            if "memory_namespace" not in columns:
                db.execute("ALTER TABLE vector_chunks ADD COLUMN memory_namespace TEXT NOT NULL DEFAULT 'default'")
            db.execute(
                """
                UPDATE vector_chunks
                SET memory_namespace = 'agent'
                WHERE conversation_id = 'shushunya-agent' AND memory_namespace = 'default'
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_vector_chunks_created ON vector_chunks(created_at)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_vector_chunks_turn ON vector_chunks(turn_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_vector_chunks_conversation ON vector_chunks(conversation_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_vector_chunks_namespace_created ON vector_chunks(memory_namespace, created_at)")

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
        request_messages = record.get("request", {}).get("messages", [])
        user_text = latest_user_message(request_messages)
        assistant_text = str((record.get("assistant_message") or {}).get("content") or "").strip()
        if user_text:
            entries.append(("user", user_text))
        if assistant_text:
            entries.append(("assistant", assistant_text))

        rows = []
        for role, text in entries:
            for chunk_index, chunk in enumerate(split_chunks(text)):
                embedding = embed_text(chunk)
                if not embedding:
                    continue
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
                        json.dumps(embedding, ensure_ascii=False, sort_keys=True),
                    )
                )

        if not rows:
            return 0

        with sqlite3.connect(self.db_path) as db:
            db.executemany(
                """
                INSERT OR REPLACE INTO vector_chunks (
                    id, turn_id, conversation_id, memory_namespace, created_at, role, chunk_index, content, embedding_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def backfill_from_archive(self, archive_sqlite_path):
        archive_sqlite_path = Path(archive_sqlite_path)
        if not VECTOR_BACKFILL_ON_START or not archive_sqlite_path.exists():
            return 0

        indexed = 0
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

        for row in rows:
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
        query_embedding = embed_text(query)
        if not query_embedding or not self.db_path.exists():
            return []

        results = []
        params = []
        where = ""
        if memory_namespace:
            where = "WHERE memory_namespace = ?"
            params.append(str(memory_namespace))
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
                score = cosine_sparse(query_embedding, embedding)
                if score < min_score:
                    continue
                results.append(
                    {
                        "score": score,
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
