#!/usr/bin/env python3
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path


GRAPH_INTERVAL_MESSAGES = int(os.environ.get("ARCHIVE_GRAPH_INTERVAL_MESSAGES", "20"))
GRAPH_MAX_RECENT_TURNS = int(os.environ.get("ARCHIVE_GRAPH_MAX_RECENT_TURNS", "12"))
GRAPH_TOP_K = int(os.environ.get("ARCHIVE_GRAPH_TOP_K", "5"))
GRAPH_BACKFILL_ON_START = os.environ.get("ARCHIVE_GRAPH_BACKFILL_ON_START", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]+", re.UNICODE)
GRAPH_SYSTEM_PROMPT = os.environ.get(
    "ARCHIVE_GRAPH_SYSTEM_PROMPT",
    "Ты изолированный graph-архивариус ArchiveOfHeresy. "
    "Ты не Шушуня и не наследуешь стиль основного диалога. "
    "Твоя задача: обновлять GraphRAG-память как набор сущностей и связей. "
    "Отвечай только валидным JSON без markdown и пояснений.",
)
GRAPH_TASK_PROMPT = os.environ.get(
    "ARCHIVE_GRAPH_TASK_PROMPT",
    "Извлеки из свежих сообщений устойчивые сущности и связи для GraphRAG. "
    "Сущности: проекты, модули, агенты, пользователи, решения, компоненты, темы, предпочтения, статусы. "
    "Связи: uses, owns, updates, supersedes, depends_on, belongs_to, configures, stores, retrieves, blocks, relates_to. "
    "В агентном режиме Tool result описывает реальные действия и результаты инструмента; извлекай из него устойчивые "
    "сущности, статусы, зависимости, блокеры и связи, не превращая сырой вывод в граф дословно. "
    "Если новые сообщения меняют старое решение, обнови summary/status так, чтобы актуальная версия была ясна. "
    "Не копируй переписку подряд. Не добавляй фактов, которых нет во входных данных.",
)


def now_iso():
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


def trim_text(value, limit):
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n..."


def tokenize(text):
    return {token.lower() for token in TOKEN_RE.findall(str(text or "")) if len(token) > 1}


def extract_json(value):
    value = str(value or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?", "", value).strip()
        value = re.sub(r"```$", "", value).strip()
    start = value.find("{")
    end = value.rfind("}")
    if start >= 0 and end > start:
        value = value[start : end + 1]
    return json.loads(value)


def latest_user_message(messages):
    for message in reversed(messages or []):
        if message.get("role") == "user":
            return str(message.get("content") or "").strip()
    return ""


def response_assistant_message(response):
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "").strip()


def overlap_score(query_tokens, text):
    if not query_tokens:
        return 0.0
    target = tokenize(text)
    if not target:
        return 0.0
    return len(query_tokens & target) / max(1, min(len(query_tokens), len(target)))


class GraphMemory:
    def __init__(self, root, proxy_json, sqlite_path, memory_namespace="default"):
        self.root = Path(root)
        self.db_path = self.root / "graph.sqlite3"
        self.proxy_json = proxy_json
        self.archive_sqlite_path = Path(sqlite_path)
        self.memory_namespace = str(memory_namespace or "default")
        self.root.mkdir(parents=True, exist_ok=True)
        self.init_storage()

    def init_storage(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    aliases_json TEXT NOT NULL,
                    importance INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    turn_id TEXT
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_edges (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    weight REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    turn_id TEXT,
                    UNIQUE(source_id, target_id, relation),
                    FOREIGN KEY(source_id) REFERENCES graph_nodes(id),
                    FOREIGN KEY(target_id) REFERENCES graph_nodes(id)
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_graph_nodes_name ON graph_nodes(name)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges(source_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_id)")

    def state_value(self, key, default=None):
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT value FROM graph_state WHERE key = ?", (key,)).fetchone()
        if not row:
            return default
        return row[0]

    def set_state_value(self, key, value):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO graph_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value) if value is not None else None),
            )

    def process_turn(self, record):
        if record.get("status") != "ok":
            return {"status": "skipped", "reason": "turn_not_ok"}
        if record.get("conversation_id") == "archive-librarian":
            return {"status": "skipped", "reason": "archive_librarian"}
        user_text = latest_user_message(record.get("request", {}).get("messages", []))
        assistant_text = str((record.get("assistant_message") or {}).get("content") or "").strip()
        message_count = int(bool(user_text)) + int(bool(assistant_text))
        if not message_count:
            return {"status": "skipped", "reason": "empty_exchange"}

        pending = int(self.state_value("pending_messages", "0") or 0) + message_count
        if pending < GRAPH_INTERVAL_MESSAGES:
            self.set_state_value("pending_messages", pending)
            return {"status": "pending", "pending_messages": pending}

        recent_turns = self.recent_turns(self.state_value("last_sync_at"))
        applied = {"nodes": 0, "edges": 0}
        if recent_turns:
            decision = self.agent_cycle(record, recent_turns)
            if not decision.get("nodes"):
                decision = self.fallback_decision(recent_turns)
            self.apply_decision(decision, record)
            applied = {"nodes": len(decision.get("nodes", [])), "edges": len(decision.get("edges", []))}

        self.set_state_value("pending_messages", 0)
        self.set_state_value("last_sync_at", record.get("created_at"))
        self.set_state_value("last_sync_turn_id", record.get("turn_id"))
        return {"status": "synced", "recent_turns": len(recent_turns), **applied}

    def backfill_from_archive(self, model=None):
        if not GRAPH_BACKFILL_ON_START or not self.archive_sqlite_path.exists() or self.node_count() > 0:
            return 0
        recent_turns = self.recent_turns(None)
        if not recent_turns:
            return 0
        record = {
            "turn_id": recent_turns[-1].get("turn_id"),
            "created_at": recent_turns[-1].get("created_at"),
            "model": model,
        }
        decision = self.agent_cycle(record, recent_turns)
        if not decision.get("nodes"):
            decision = self.fallback_decision(recent_turns)
        self.apply_decision(decision, record)
        self.set_state_value("pending_messages", 0)
        self.set_state_value("last_sync_at", record.get("created_at"))
        self.set_state_value("last_sync_turn_id", record.get("turn_id"))
        return len(decision.get("nodes", []))

    def node_count(self):
        with sqlite3.connect(self.db_path) as db:
            return int(db.execute("SELECT count(*) FROM graph_nodes").fetchone()[0])

    def recent_turns(self, last_sync_at):
        if not self.archive_sqlite_path.exists():
            return []

        sql = """
            SELECT id, conversation_id, created_at, request_json, response_json
            FROM turns
            WHERE status = 'ok'
        """
        params = []
        with sqlite3.connect(self.archive_sqlite_path) as db:
            turn_columns = {row[1] for row in db.execute("PRAGMA table_info(turns)")}
        if "memory_namespace" in turn_columns:
            sql += " AND memory_namespace = ?"
            params.append(self.memory_namespace)
        if last_sync_at:
            sql += " AND created_at > ?"
            params.append(last_sync_at)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(GRAPH_MAX_RECENT_TURNS)

        with sqlite3.connect(self.archive_sqlite_path) as db:
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute(sql, params)]
        rows.reverse()

        turns = []
        for row in rows:
            try:
                request = json.loads(row.get("request_json") or "{}")
            except json.JSONDecodeError:
                request = {}
            try:
                response = json.loads(row.get("response_json") or "{}")
            except json.JSONDecodeError:
                response = {}
            turns.append(
                {
                    "turn_id": row.get("id"),
                    "conversation_id": row.get("conversation_id"),
                    "created_at": row.get("created_at"),
                    "user": latest_user_message(request.get("messages", [])),
                    "assistant": trim_text(response_assistant_message(response), 1600),
                }
            )
        return turns

    def agent_cycle(self, record, recent_turns):
        seed_text = "\n".join([turn.get("user", "") + "\n" + turn.get("assistant", "") for turn in recent_turns])
        graph_context = self.context_for_query(seed_text, limit=GRAPH_TOP_K)
        messages = [
            {"role": "system", "content": GRAPH_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": GRAPH_TASK_PROMPT,
                        "existing_relevant_graph": graph_context,
                        "recent_turns": recent_turns,
                        "schema": {
                            "nodes": [
                                {
                                    "name": "stable entity name",
                                    "kind": "project|module|agent|decision|person|topic|preference|status|component",
                                    "summary": "current integrated summary",
                                    "aliases": ["optional"],
                                    "importance": "1..5",
                                    "status": "active|superseded|paused|unknown",
                                }
                            ],
                            "edges": [
                                {
                                    "source": "source node name",
                                    "target": "target node name",
                                    "relation": "uses|owns|updates|supersedes|depends_on|belongs_to|configures|stores|retrieves|blocks|relates_to",
                                    "summary": "why the relation exists",
                                    "weight": "0.1..1.0",
                                    "status": "active|superseded|unknown",
                                }
                            ],
                        },
                        "rules": [
                            "Return exactly one JSON object with nodes and edges arrays.",
                            "Prefer stable names that can be merged later.",
                            "Keep summaries short and current.",
                            "Do not imitate the conversation persona.",
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            payload = {
                "model": record.get("model")
                or os.environ.get(
                    "ARCHIVE_LIBRARIAN_MODEL",
                    os.environ.get("ARCHIVE_DEFAULT_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf"),
                ),
                "user": "archive-librarian",
                "messages": messages,
                "max_tokens": 1800,
                "temperature": 0.1,
            }
            _status, response = self.proxy_json("POST", "/v1/chat/completions", payload=payload, timeout=240)
            return self.normalize_decision(extract_json(response["choices"][0]["message"].get("content", "")))
        except Exception:
            return {"nodes": [], "edges": []}

    def apply_decision(self, decision, record):
        name_to_id = {}
        for raw in decision.get("nodes", []):
            node_id = self.upsert_node(raw, record)
            if node_id:
                name_to_id[str(raw.get("name") or "").strip()] = node_id

        for raw in decision.get("edges", []):
            source = str(raw.get("source") or "").strip()
            target = str(raw.get("target") or "").strip()
            if not source or not target:
                continue
            source_id = name_to_id.get(source) or self.ensure_node(source, record)
            target_id = name_to_id.get(target) or self.ensure_node(target, record)
            self.upsert_edge(source_id, target_id, raw, record)

    def upsert_node(self, raw, record):
        name = str(raw.get("name") or "").strip()[:160]
        if not name:
            return None
        now = now_iso()
        aliases = raw.get("aliases") if isinstance(raw.get("aliases"), list) else []
        values = {
            "kind": str(raw.get("kind") or "topic").strip()[:40] or "topic",
            "summary": trim_text(raw.get("summary"), 2000),
            "aliases_json": json.dumps([str(item)[:160] for item in aliases], ensure_ascii=False),
            "importance": max(1, min(5, int(raw.get("importance") or 3))),
            "status": str(raw.get("status") or "active").strip()[:40] or "active",
        }
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT id, importance FROM graph_nodes WHERE name = ?", (name,)).fetchone()
            if row:
                node_id = row[0]
                values["importance"] = max(values["importance"], int(row[1] or 3))
                db.execute(
                    """
                    UPDATE graph_nodes
                    SET kind = ?, summary = ?, aliases_json = ?, importance = ?, status = ?, updated_at = ?, turn_id = ?
                    WHERE id = ?
                    """,
                    (
                        values["kind"],
                        values["summary"],
                        values["aliases_json"],
                        values["importance"],
                        values["status"],
                        now,
                        record.get("turn_id"),
                        node_id,
                    ),
                )
                return node_id
            node_id = str(uuid.uuid4())
            db.execute(
                """
                INSERT INTO graph_nodes (
                    id, name, kind, summary, aliases_json, importance, status, created_at, updated_at, turn_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_id,
                    name,
                    values["kind"],
                    values["summary"],
                    values["aliases_json"],
                    values["importance"],
                    values["status"],
                    now,
                    now,
                    record.get("turn_id"),
                ),
            )
        return node_id

    def ensure_node(self, name, record):
        return self.upsert_node({"name": name, "kind": "topic", "summary": "", "importance": 2, "status": "active"}, record)

    def upsert_edge(self, source_id, target_id, raw, record):
        if not source_id or not target_id:
            return
        relation = str(raw.get("relation") or "relates_to").strip()[:80] or "relates_to"
        summary = trim_text(raw.get("summary"), 1600)
        try:
            weight = float(raw.get("weight") or 0.5)
        except (TypeError, ValueError):
            weight = 0.5
        weight = max(0.1, min(1.0, weight))
        status = str(raw.get("status") or "active").strip()[:40] or "active"
        now = now_iso()
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT id FROM graph_edges WHERE source_id = ? AND target_id = ? AND relation = ?",
                (source_id, target_id, relation),
            ).fetchone()
            if row:
                db.execute(
                    """
                    UPDATE graph_edges
                    SET summary = ?, weight = ?, status = ?, updated_at = ?, turn_id = ?
                    WHERE id = ?
                    """,
                    (summary, weight, status, now, record.get("turn_id"), row[0]),
                )
                return
            db.execute(
                """
                INSERT INTO graph_edges (
                    id, source_id, target_id, relation, summary, weight, status, created_at, updated_at, turn_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), source_id, target_id, relation, summary, weight, status, now, now, record.get("turn_id")),
            )

    def normalize_decision(self, decision):
        nodes = []
        for raw in decision.get("nodes") or []:
            name = str(raw.get("name") or "").strip()
            if name:
                nodes.append(raw)
        edges = []
        for raw in decision.get("edges") or []:
            if str(raw.get("source") or "").strip() and str(raw.get("target") or "").strip():
                edges.append(raw)
        return {"nodes": nodes, "edges": edges}

    def fallback_decision(self, recent_turns):
        text = "\n".join([turn.get("user", "") + "\n" + turn.get("assistant", "") for turn in recent_turns]).lower()
        nodes = []
        edges = []

        def add_node(name, kind, summary, importance=3, aliases=None):
            nodes.append(
                {
                    "name": name,
                    "kind": kind,
                    "summary": summary,
                    "aliases": aliases or [],
                    "importance": importance,
                    "status": "active",
                }
            )

        def add_edge(source, target, relation, summary, weight=0.7):
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "relation": relation,
                    "summary": summary,
                    "weight": weight,
                    "status": "active",
                }
            )

        if "archiveofheresy" in text or "архив" in text or "memory" in text or "памят" in text:
            add_node("ArchiveOfHeresy", "project", "Gateway that prepares prompts, archives turns, and hosts model memory layers.", 5)
        if "архивариус" in text or "librarian" in text or "библиотек" in text:
            add_node("Librarian", "agent", "Isolated archivist agent that maintains ArchiveOfHeresy memory layers after model answers.", 5, ["архивариус", "библиотекарь"])
        layers = [
            ("Focus Memory", "focus", "Current-topic compact memory injected into the model request."),
            ("Wiki Memory", "wiki", "Durable sorted memory for decisions, facts, statuses, and open questions."),
            ("Vector Memory", "vector", "Similarity retrieval layer over archived user and assistant chunks."),
            ("GraphRAG Memory", "graph", "Graph layer storing entities and relations for relationship-aware retrieval."),
        ]
        for name, keyword, summary in layers:
            if keyword in text or name.lower() in text:
                add_node(name, "component", summary, 4)
                if any(node["name"] == "ArchiveOfHeresy" for node in nodes):
                    add_edge(name, "ArchiveOfHeresy", "belongs_to", f"{name} is a memory layer inside ArchiveOfHeresy.", 0.8)
                if any(node["name"] == "Librarian" for node in nodes):
                    add_edge("Librarian", name, "updates", f"Librarian maintains {name}.", 0.9)

        return {"nodes": nodes, "edges": edges}

    def search(self, query, limit=GRAPH_TOP_K):
        query_tokens = tokenize(query)
        if not query_tokens:
            return {"nodes": [], "edges": []}

        scored_nodes = []
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            for row in db.execute("SELECT * FROM graph_nodes"):
                text = " ".join([row["name"], row["kind"], row["summary"], row["aliases_json"], row["status"]])
                score = overlap_score(query_tokens, text)
                if score > 0:
                    item = dict(row)
                    item["score"] = score
                    scored_nodes.append(item)
            scored_nodes.sort(key=lambda item: (-item["score"], -int(item["importance"] or 0), item["updated_at"]))
            nodes = scored_nodes[:limit]
            node_ids = {node["id"] for node in nodes}
            edges = []
            if node_ids:
                placeholders = ",".join("?" for _ in node_ids)
                for row in db.execute(
                    f"""
                    SELECT e.*, s.name AS source_name, t.name AS target_name
                    FROM graph_edges e
                    JOIN graph_nodes s ON s.id = e.source_id
                    JOIN graph_nodes t ON t.id = e.target_id
                    WHERE e.source_id IN ({placeholders}) OR e.target_id IN ({placeholders})
                    ORDER BY e.weight DESC, e.updated_at DESC
                    LIMIT ?
                    """,
                    [*node_ids, *node_ids, limit * 2],
                ):
                    edges.append(dict(row))
        return {"nodes": nodes, "edges": edges}

    def context_for_query(self, query, limit=GRAPH_TOP_K):
        result = self.search(query, limit=limit)
        if not result["nodes"] and not result["edges"]:
            return ""
        lines = ["# GraphRAG Memory", ""]
        if result["nodes"]:
            lines.append("## Nodes")
            for node in result["nodes"]:
                lines.append(
                    f"- {node['name']} ({node['kind']}, status={node['status']}, importance={node['importance']}): "
                    f"{trim_text(node['summary'], 500)}"
                )
            lines.append("")
        if result["edges"]:
            lines.append("## Relations")
            for edge in result["edges"]:
                lines.append(
                    f"- {edge['source_name']} --{edge['relation']}--> {edge['target_name']} "
                    f"(status={edge['status']}, weight={edge['weight']}): {trim_text(edge['summary'], 400)}"
                )
        return "\n".join(lines).strip()
