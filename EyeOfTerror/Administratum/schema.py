from __future__ import annotations

from pathlib import Path


DEPARTMENT = "Administratum"
GOVERNOR = "AshurKai"
DEFAULT_PORT = 7300
ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = ROOT / "runtime"
DB_PATH = RUNTIME_ROOT / "administratum.sqlite3"
VALID_TASK_KINDS = {"reminder", "todo", "routine", "watch"}
VALID_TASK_STATUSES = {"active", "pending", "running", "done", "cancelled", "failed"}
VALID_WATCH_STATUSES = {"active", "paused", "running", "cancelled", "failed"}


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    due_at TEXT,
    interval TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    next_run TEXT,
    timezone TEXT NOT NULL DEFAULT 'Asia/Seoul',
    created_by TEXT NOT NULL DEFAULT 'unknown',
    created_from_session TEXT NOT NULL DEFAULT '',
    created_from_message_id TEXT NOT NULL DEFAULT '',
    dedupe_key TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_dedupe ON tasks(dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_next_run ON tasks(status, next_run);

CREATE TABLE IF NOT EXISTS watches (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    watch_type TEXT NOT NULL,
    target TEXT NOT NULL,
    condition_json TEXT NOT NULL DEFAULT '{}',
    last_value_json TEXT NOT NULL DEFAULT '{}',
    next_check TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_watches_next_check ON watches(status, next_check);

CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    watch_id TEXT,
    event_kind TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_journal_created ON journal(created_at);
"""
