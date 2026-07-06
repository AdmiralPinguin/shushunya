"""Semantic (e5) ranking for the wiki, focus, and graph memory layers.

The vector layer already embeds chat chunks; these other layers still ranked by
lexical token overlap, so Magos missed paraphrases. This reuses the same CPU e5
endpoint to rank candidates by cosine similarity, with a content-hash embedding
cache so unchanged pages/nodes are embedded once. Fails soft: if the embedder is
unavailable the caller keeps its lexical ranking.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path

from archivist_agent.vector_memory import (
    VECTOR_EMBEDDING_BASE_URL,
    cosine_dense,
    embed_openai_text,
)

SEMANTIC_MEMORY_ENABLED = os.environ.get("ARCHIVE_SEMANTIC_MEMORY_ENABLED", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
CACHE_PATH = Path(os.environ.get("ARCHIVE_SEMANTIC_CACHE_PATH", Path(__file__).resolve().parent / "semantic" / "cache.sqlite3"))
# Lexical stays the primary ranker (precise on exact terms and robust to the
# "hub" pages that whole-document e5 vectors produce). Semantic only ADDS recall:
# a candidate with no lexical overlap is surfaced when its cosine clears this
# (deliberately high) bar, ranked below any lexical match. Tunable.
SEMANTIC_MIN_SCORE = float(os.environ.get("ARCHIVE_SEMANTIC_MIN_SCORE", "0.78"))
_LOCK = threading.Lock()
_INIT = False


def _connect():
    global _INIT
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(CACHE_PATH, timeout=10)
    if not _INIT:
        db.execute("CREATE TABLE IF NOT EXISTS embed_cache (content_hash TEXT PRIMARY KEY, embedding_json TEXT NOT NULL)")
        db.commit()
        _INIT = True
    return db


def _hash(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()


def _embed_cached(text: str, db) -> list[float]:
    key = _hash(text)
    row = db.execute("SELECT embedding_json FROM embed_cache WHERE content_hash = ?", (key,)).fetchone()
    if row:
        return json.loads(row[0])
    vector = embed_openai_text(text)  # raises if the embedder is unavailable
    db.execute(
        "INSERT OR REPLACE INTO embed_cache (content_hash, embedding_json) VALUES (?, ?)",
        (key, json.dumps(vector)),
    )
    db.commit()
    return vector


def semantic_scores(query: str, items: list[tuple[str, str]]) -> dict[str, float] | None:
    """items: list of (id, text). Returns {id: cosine_score} or None if semantic
    ranking is disabled or the embedder is unavailable (caller falls back)."""
    if not SEMANTIC_MEMORY_ENABLED or not query or not items:
        return None
    with _LOCK:
        try:
            query_vector = embed_openai_text(query)
        except Exception:  # noqa: BLE001 - embedder unavailable -> caller keeps lexical ranking
            return None
        db = _connect()
        try:
            scores: dict[str, float] = {}
            for item_id, text in items:
                if not text:
                    continue
                try:
                    vector = _embed_cached(text, db)
                except Exception:  # noqa: BLE001 - skip a single failed item, keep the rest
                    continue
                scores[str(item_id)] = cosine_dense(query_vector, vector)
            return scores or None
        finally:
            db.close()
