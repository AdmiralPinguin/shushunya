#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from archivist_agent.graph_memory import GraphMemory
from archivist_agent.vector_memory import VectorMemory


def create_archive_db(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            """
            CREATE TABLE turns (
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
                error TEXT
            )
            """
        )
        rows = [
            ("agent-turn", "shushunya-agent", "agent", "2026-06-21T00:00:00+09:00", "agent memory check"),
            ("default-turn", "chat-user", "default", "2026-06-21T00:01:00+09:00", "default chat check"),
        ]
        for turn_id, conversation_id, namespace, created_at, user_text in rows:
            request = {"messages": [{"role": "user", "content": user_text}]}
            response = {"choices": [{"message": {"role": "assistant", "content": f"{namespace} reply"}}]}
            db.execute(
                """
                INSERT INTO turns (
                    id, conversation_id, memory_namespace, created_at, model, status, http_status,
                    request_json, prepared_messages_json, response_json, error
                )
                VALUES (?, ?, ?, ?, ?, 'ok', 200, ?, '[]', ?, NULL)
                """,
                (
                    turn_id,
                    conversation_id,
                    namespace,
                    created_at,
                    "test-model",
                    json.dumps(request, ensure_ascii=False),
                    json.dumps(response, ensure_ascii=False),
                ),
            )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        archive_db = tmp_path / "archive.sqlite3"
        create_archive_db(archive_db)

        vector = VectorMemory(tmp_path / "vector")
        indexed = vector.backfill_from_archive(archive_db)
        if indexed != 2:
            raise AssertionError(f"expected 2 indexed turns, got {indexed}")
        if not vector.search("agent memory", memory_namespace="agent"):
            raise AssertionError("agent vector search returned no matches")
        if vector.search("agent memory", memory_namespace="default"):
            raise AssertionError("default vector search leaked agent matches")

        graph_agent = GraphMemory(tmp_path / "graph-agent", lambda *_args, **_kwargs: None, archive_db, memory_namespace="agent")
        graph_default = GraphMemory(tmp_path / "graph-default", lambda *_args, **_kwargs: None, archive_db, memory_namespace="default")
        agent_turns = graph_agent.recent_turns(None)
        default_turns = graph_default.recent_turns(None)
        if [turn["turn_id"] for turn in agent_turns] != ["agent-turn"]:
            raise AssertionError(f"unexpected agent graph turns: {agent_turns}")
        if [turn["turn_id"] for turn in default_turns] != ["default-turn"]:
            raise AssertionError(f"unexpected default graph turns: {default_turns}")

    print("namespace smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
