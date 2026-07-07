from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .schema import DB_PATH, SCHEMA_SQL, VALID_TASK_KINDS, VALID_TASK_STATUSES, VALID_WATCH_STATUSES
from .timeutil import DEFAULT_TZ, next_run_after, now_iso, parse_datetime


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    return db


def init_db(db_path: Path = DB_PATH) -> None:
    with connect(db_path) as db:
        db.executescript(SCHEMA_SQL)


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def rows_dict(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def json_text(value: Any) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def create_task(payload: dict[str, Any], db_path: Path = DB_PATH) -> dict[str, Any]:
    init_db(db_path)
    kind = str(payload.get("kind") or "reminder").strip().lower()
    if kind not in VALID_TASK_KINDS:
        raise ValueError(f"invalid task kind: {kind}")
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    timezone_name = str(payload.get("timezone") or DEFAULT_TZ).strip() or DEFAULT_TZ
    due_at = payload.get("due_at")
    interval = payload.get("interval")
    next_run = payload.get("next_run") or due_at
    if kind == "routine" and not next_run:
        next_run = next_run_after(now_iso(timezone_name), interval or "1d", timezone_name)
    if next_run:
        next_run = parse_datetime(str(next_run), timezone_name).replace(microsecond=0).isoformat()
    if due_at:
        due_at = parse_datetime(str(due_at), timezone_name).replace(microsecond=0).isoformat()
    # Empty string must not survive to the DB: claim_due_tasks compares strings,
    # and '' <= now is always true, so an unscheduled task would fire instantly.
    next_run = next_run or None
    due_at = due_at or None
    task_id = str(payload.get("id") or uuid.uuid4()).strip()
    created_at = now_iso(timezone_name)
    values = {
        "id": task_id,
        "kind": kind,
        "title": title,
        "body": str(payload.get("body") or ""),
        "due_at": due_at,
        "interval": str(interval or ""),
        "status": str(payload.get("status") or "active").strip().lower(),
        "next_run": next_run,
        "timezone": timezone_name,
        "created_by": str(payload.get("created_by") or "unknown"),
        "created_from_session": str(payload.get("created_from_session") or ""),
        "created_from_message_id": str(payload.get("created_from_message_id") or ""),
        "dedupe_key": str(payload.get("dedupe_key") or "").strip() or None,
        "payload_json": json_text(payload.get("payload_json") or payload.get("payload") or {}),
        "last_error": "",
        "created_at": created_at,
        "updated_at": created_at,
    }
    if values["status"] not in VALID_TASK_STATUSES:
        raise ValueError(f"invalid task status: {values['status']}")
    existing = get_task_by_dedupe(values["dedupe_key"], db_path)
    if existing:
        with connect(db_path) as db:
            db.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (created_at, existing["id"]))
        return get_task(existing["id"], db_path) or existing
    with connect(db_path) as db:
        db.execute(
            """
            INSERT INTO tasks (
                id, kind, title, body, due_at, interval, status, next_run, timezone,
                created_by, created_from_session, created_from_message_id, dedupe_key,
                payload_json, last_error, created_at, updated_at
            )
            VALUES (
                :id, :kind, :title, :body, :due_at, :interval, :status, :next_run, :timezone,
                :created_by, :created_from_session, :created_from_message_id, :dedupe_key,
                :payload_json, :last_error, :created_at, :updated_at
            )
            """,
            values,
        )
    return get_task(task_id, db_path) or get_task_by_dedupe(values["dedupe_key"], db_path) or values


def get_task(task_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    init_db(db_path)
    with connect(db_path) as db:
        return row_dict(db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())


def get_task_by_dedupe(dedupe_key: str | None, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    if not dedupe_key:
        return None
    with connect(db_path) as db:
        return row_dict(db.execute("SELECT * FROM tasks WHERE dedupe_key = ?", (dedupe_key,)).fetchone())


def list_tasks(status: str | None = None, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    with connect(db_path) as db:
        if status:
            return rows_dict(db.execute("SELECT * FROM tasks WHERE status = ? ORDER BY next_run, created_at", (status,)).fetchall())
        return rows_dict(db.execute("SELECT * FROM tasks ORDER BY status, next_run, created_at").fetchall())


def set_task_status(task_id: str, status: str, db_path: Path = DB_PATH, *, last_error: str = "") -> dict[str, Any] | None:
    if status not in VALID_TASK_STATUSES:
        raise ValueError(f"invalid task status: {status}")
    with connect(db_path) as db:
        db.execute(
            "UPDATE tasks SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
            (status, last_error, now_iso(), task_id),
        )
    return get_task(task_id, db_path)


def snooze_task(task_id: str, next_run: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    task = get_task(task_id, db_path)
    if not task:
        return None
    parsed = parse_datetime(next_run, task.get("timezone") or DEFAULT_TZ).replace(microsecond=0).isoformat()
    with connect(db_path) as db:
        db.execute(
            "UPDATE tasks SET next_run = ?, status = 'active', updated_at = ? WHERE id = ?",
            (parsed, now_iso(task.get("timezone") or DEFAULT_TZ), task_id),
        )
    return get_task(task_id, db_path)


def claim_due_tasks(limit: int = 20, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    now = now_iso()
    with connect(db_path) as db:
        rows = db.execute(
            """
            SELECT * FROM tasks
            WHERE status = 'active' AND next_run IS NOT NULL AND next_run != '' AND next_run <= ?
            ORDER BY next_run ASC
            LIMIT ?
            """,
            (now, max(1, min(int(limit), 100))),
        ).fetchall()
        ids = [row["id"] for row in rows]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            db.execute(f"UPDATE tasks SET status = 'running', updated_at = ? WHERE id IN ({placeholders})", (now, *ids))
    return rows_dict(rows)


def complete_due_task(task: dict[str, Any], db_path: Path = DB_PATH, *, error: str = "") -> dict[str, Any] | None:
    status = "failed" if error else "done"
    next_run = None
    if not error and task.get("interval"):
        next_run = next_run_after(task.get("next_run"), task.get("interval"), task.get("timezone") or DEFAULT_TZ)
        status = "active" if next_run else "done"
    with connect(db_path) as db:
        db.execute(
            "UPDATE tasks SET status = ?, next_run = ?, last_error = ?, updated_at = ? WHERE id = ?",
            (status, next_run, error, now_iso(task.get("timezone") or DEFAULT_TZ), task["id"]),
        )
    return get_task(task["id"], db_path)


def create_watch(payload: dict[str, Any], db_path: Path = DB_PATH) -> dict[str, Any]:
    init_db(db_path)
    title = str(payload.get("title") or "").strip()
    target = str(payload.get("target") or payload.get("url") or payload.get("query") or "").strip()
    if not title or not target:
        raise ValueError("title and target are required")
    watch_id = str(payload.get("id") or uuid.uuid4()).strip()
    created_at = now_iso()
    values = {
        "id": watch_id,
        "title": title,
        "watch_type": str(payload.get("watch_type") or "generic").strip(),
        "target": target,
        "condition_json": json_text(payload.get("condition_json") or payload.get("condition") or {}),
        "last_value_json": json_text(payload.get("last_value_json") or {}),
        "next_check": str(payload.get("next_check") or now_iso()),
        "status": str(payload.get("status") or "active").strip().lower(),
        "created_at": created_at,
        "updated_at": created_at,
    }
    if values["status"] not in VALID_WATCH_STATUSES:
        raise ValueError(f"invalid watch status: {values['status']}")
    with connect(db_path) as db:
        db.execute(
            """
            INSERT INTO watches (id, title, watch_type, target, condition_json, last_value_json, next_check, status, created_at, updated_at)
            VALUES (:id, :title, :watch_type, :target, :condition_json, :last_value_json, :next_check, :status, :created_at, :updated_at)
            """,
            values,
        )
    return get_watch(watch_id, db_path) or values


def get_watch(watch_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    init_db(db_path)
    with connect(db_path) as db:
        return row_dict(db.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone())


def list_watches(db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    with connect(db_path) as db:
        return rows_dict(db.execute("SELECT * FROM watches ORDER BY status, created_at").fetchall())


def set_watch_status(watch_id: str, status: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    if status not in VALID_WATCH_STATUSES:
        raise ValueError(f"invalid watch status: {status}")
    with connect(db_path) as db:
        db.execute("UPDATE watches SET status = ?, updated_at = ? WHERE id = ?", (status, now_iso(), watch_id))
    return get_watch(watch_id, db_path)


def claim_due_watches(limit: int = 20, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    now = now_iso()
    with connect(db_path) as db:
        rows = db.execute(
            """
            SELECT * FROM watches
            WHERE status = 'active' AND (next_check IS NULL OR next_check = '' OR next_check <= ?)
            ORDER BY next_check ASC, created_at ASC
            LIMIT ?
            """,
            (now, max(1, min(int(limit), 100))),
        ).fetchall()
        ids = [row["id"] for row in rows]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            db.execute(f"UPDATE watches SET status = 'running', updated_at = ? WHERE id IN ({placeholders})", (now, *ids))
    return rows_dict(rows)


def complete_watch_check(
    watch: dict[str, Any],
    last_value: Any,
    next_check: str,
    db_path: Path = DB_PATH,
    *,
    status: str = "active",
    error: str = "",
) -> dict[str, Any] | None:
    if status not in VALID_WATCH_STATUSES:
        raise ValueError(f"invalid watch status: {status}")
    payload = dict(last_value or {})
    if error:
        payload["error"] = error
    with connect(db_path) as db:
        db.execute(
            """
            UPDATE watches
            SET last_value_json = ?, next_check = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (json_text(payload), next_check, status, now_iso(), watch["id"]),
        )
    return get_watch(watch["id"], db_path)


def add_journal(event_kind: str, message: str, db_path: Path = DB_PATH, *, task_id: str = "", watch_id: str = "", payload: Any = None) -> dict[str, Any]:
    init_db(db_path)
    created_at = now_iso()
    with connect(db_path) as db:
        cursor = db.execute(
            """
            INSERT INTO journal (task_id, watch_id, event_kind, message, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id or None, watch_id or None, event_kind, message, json_text(payload or {}), created_at),
        )
        journal_id = cursor.lastrowid
        return row_dict(db.execute("SELECT * FROM journal WHERE id = ?", (journal_id,)).fetchone()) or {}


def list_journal(limit: int = 100, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    with connect(db_path) as db:
        return rows_dict(db.execute("SELECT * FROM journal ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 500)),)).fetchall())
