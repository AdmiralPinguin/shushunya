"""Outbox for Shushunya's proactive reports.

Proactive sources (Administratum reminders/summaries, Warmaster escalations,
completed task finals) no longer barge into the shared chat. They are queued
here; the app shows a "she wants to say something" indicator, and the reports
are voiced only when the owner presses the button or asks in conversation
(detected as a deliver_reports intent). While reports are pending, the chat
prompt gets a topics-only note so Shushunya can mention that news exists
without spilling the content uninvited.
"""
import json
import sqlite3
from datetime import datetime

from archive_config import SQLITE_PATH
from archive_state import ARCHIVE_LOCK


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _connect():
    db = sqlite3.connect(SQLITE_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            source TEXT NOT NULL,
            kind TEXT NOT NULL,
            topic TEXT NOT NULL,
            body TEXT NOT NULL,
            dedupe_key TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            delivered_at TEXT
        )
        """
    )
    return db


def enqueue_report(source, kind, topic, body, dedupe_key=None):
    """Queue a proactive report instead of posting it into the chat."""
    source = str(source or "unknown").strip()[:80]
    kind = str(kind or "report").strip()[:80]
    topic = " ".join(str(topic or "").split())[:200] or kind
    body = str(body or "").strip()
    if not body:
        return None
    dedupe_key = str(dedupe_key or "").strip()[:160] or None
    with ARCHIVE_LOCK:
        with _connect() as db:
            if dedupe_key:
                row = db.execute("SELECT id FROM pending_reports WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
                if row:
                    return int(row["id"])
            cursor = db.execute(
                "INSERT INTO pending_reports (created_at, source, kind, topic, body, dedupe_key) VALUES (?, ?, ?, ?, ?, ?)",
                (now_iso(), source, kind, topic, body, dedupe_key),
            )
            return int(cursor.lastrowid)


def pending_reports(limit=20):
    with _connect() as db:
        rows = db.execute(
            "SELECT * FROM pending_reports WHERE status = 'pending' ORDER BY id LIMIT ?",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    return [dict(row) for row in rows]


def pending_summary():
    """Cheap indicator payload: count + topics only, no content."""
    reports = pending_reports()
    return {
        "count": len(reports),
        "topics": [{"id": r["id"], "kind": r["kind"], "topic": r["topic"], "created_at": r["created_at"]} for r in reports],
    }


def mark_delivered(report_ids):
    ids = [int(i) for i in report_ids or []]
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    with ARCHIVE_LOCK:
        with _connect() as db:
            cursor = db.execute(
                f"UPDATE pending_reports SET status = 'delivered', delivered_at = ? WHERE id IN ({placeholders}) AND status = 'pending'",
                (now_iso(), *ids),
            )
            return cursor.rowcount


def reports_event_text(reports):
    """Combined system-event text: Shushunya voices all queued reports at once."""
    lines = [
        "[Накопленные доклады для владельца]",
        "Владелец разрешил доложить. Изложи доклады своим голосом, по порядку, ничего не выдумывая сверх текста.",
        "Докладывай строго по-русски: если фрагменты доклада на английском или другом языке, переведи их.",
        "Не создавай из этого новые задачи.",
        "",
    ]
    for index, report in enumerate(reports, 1):
        lines.append(f"--- доклад {index} [{report['kind']}] от {report['created_at']}")
        lines.append(str(report["body"]))
        lines.append("")
    return "\n".join(lines).strip()


def pending_topics_note():
    """Topics-only system note for regular chat turns: she may mention that news
    exists, but must not spill the content until the owner asks."""
    summary = pending_summary()
    if not summary["count"]:
        return None
    topics = "; ".join(f"[{t['kind']}] {t['topic']}" for t in summary["topics"][:10])
    return {
        "role": "system",
        "content": (
            f"У Шушуни есть {summary['count']} недоставленных докладов (темы: {topics}). "
            "НЕ пересказывай их содержание. Если уместно, можешь одной короткой фразой упомянуть, "
            "что есть новости и предложить рассказать. Если владелец прямо спрашивает про новости/доклады, "
            "они будут доставлены отдельным сообщением — не выдумывай их содержание сам."
        ),
    }
