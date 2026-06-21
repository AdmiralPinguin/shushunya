from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Protocol

from .utils import compact_json_value


class JournalConfig(Protocol):
    task_id: str


AGENT_ROOT = Path(__file__).resolve().parents[1]
TASK_JOURNAL_DIR = Path(os.environ.get("SHUSHUNYA_AGENT_TASK_JOURNAL_DIR", str(AGENT_ROOT / "runtime" / "task-journals")))
TASK_JOURNAL_MAX_FILES = int(os.environ.get("SHUSHUNYA_AGENT_TASK_JOURNAL_MAX_FILES", "500"))
TASK_JOURNAL_MAX_BYTES = int(os.environ.get("SHUSHUNYA_AGENT_TASK_JOURNAL_MAX_BYTES", "10485760"))


def utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_task_id(raw: str | None = None) -> str:
    text = str(raw or "").strip()
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in text)
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    if cleaned:
        return cleaned[:96]
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def task_journal_path(task_id: str) -> Path:
    return TASK_JOURNAL_DIR / f"{safe_task_id(task_id)}.jsonl"


def write_task_journal(config: JournalConfig, event_type: str, payload: dict[str, Any]) -> None:
    if not config.task_id:
        return
    try:
        TASK_JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        path = task_journal_path(config.task_id)
        max_bytes = max(1024, int(TASK_JOURNAL_MAX_BYTES))
        if path.is_file() and path.stat().st_size > max_bytes:
            rotation_record = {
                "ts": utc_now_iso(),
                "task_id": config.task_id,
                "type": "journal_rotated",
                "previous_size": path.stat().st_size,
                "max_bytes": max_bytes,
            }
            path.write_text(json.dumps(rotation_record, ensure_ascii=False) + "\n", encoding="utf-8")
        record = {
            "ts": utc_now_iso(),
            "task_id": config.task_id,
            "type": event_type,
            **compact_json_value(payload, string_limit=6000, list_limit=80),
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        latest = TASK_JOURNAL_DIR / "latest"
        tmp_latest = TASK_JOURNAL_DIR / ".latest.tmp"
        tmp_latest.write_text(config.task_id + "\n", encoding="utf-8")
        tmp_latest.replace(latest)
        if event_type == "final":
            prune_task_journals(TASK_JOURNAL_MAX_FILES)
    except OSError:
        return


def prune_task_journals(max_files: int) -> None:
    try:
        max_files = max(1, int(max_files))
        journals = sorted(
            (path for path in TASK_JOURNAL_DIR.glob("*.jsonl") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
        )
        for path in journals[:-max_files]:
            path.unlink(missing_ok=True)
    except OSError:
        return


def read_task_journal(task_id: str | None = None, limit: int = 80) -> dict[str, Any]:
    try:
        if not task_id:
            task_id = (TASK_JOURNAL_DIR / "latest").read_text(encoding="utf-8").strip()
        safe_id = safe_task_id(task_id)
        path = task_journal_path(safe_id)
        if not path.is_file():
            return {"ok": False, "error": "task journal not found", "task_id": safe_id}
        safe_limit = max(1, min(limit, 500))
        tail: deque[str] = deque(maxlen=safe_limit)
        event_count = 0
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                clean = line.strip()
                if not clean:
                    continue
                event_count += 1
                tail.append(clean)
        records = [json.loads(line) for line in tail]
        return {"ok": True, "task_id": safe_id, "path": str(path), "events": records, "event_count": event_count}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "task_id": task_id or ""}


def compact_resume_events(events: list[Any], max_chars: int = 20000) -> list[Any]:
    selected: list[Any] = []
    total = 2
    for event in reversed(events):
        compacted = compact_json_value(event, string_limit=1200, list_limit=20)
        text = json.dumps(compacted, ensure_ascii=False, separators=(",", ":"))
        if selected and total + len(text) + 1 > max_chars:
            break
        selected.append(compacted)
        total += len(text) + 1
    return list(reversed(selected))
