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


def summarize_task_events(task_id: str, events: list[Any]) -> dict[str, Any]:
    start_event = next((event for event in events if isinstance(event, dict) and event.get("type") == "start"), {})
    final_event = next((event for event in reversed(events) if isinstance(event, dict) and event.get("type") == "final"), {})
    actions = []
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "action":
            continue
        action = event.get("action")
        if isinstance(action, dict):
            action_name = str(action.get("action") or "")
            summary = action.get("path") or action.get("query") or action.get("url") or action.get("kind") or ""
        else:
            action_name = str(action or "")
            summary = str(event.get("summary") or "")
        if action_name:
            actions.append({"action": action_name, "summary": str(summary)[:240]})
    return {
        "ok": bool(final_event),
        "task_id": task_id,
        "task": presentable_task_text(str(start_event.get("task") or "")),
        "final": str(final_event.get("message") or "").strip(),
        "cancelled": bool(final_event.get("cancelled", False)),
        "success": bool(final_event.get("ok", False)),
        "duration_sec": final_event.get("duration_sec"),
        "actions": actions[-20:],
    }


def presentable_task_text(task: str) -> str:
    text = str(task or "").strip()
    for marker in (
        "\n\nAuthoritative previous agent task context:",
        "\n\nResume context from previous agent task journal ",
    ):
        index = text.find(marker)
        if index >= 0:
            text = text[:index].strip()
    return text


def is_meta_or_status_task(task: str) -> bool:
    text = " ".join(str(task or "").lower().split())
    if not text:
        return True
    if is_contextless_task_reference(text):
        return True
    previous_markers = ("прошл", "предыдущ", "последн")
    unfinished_markers = ("незакончен", "не закончен", "невыполн", "не выполн", "недодел", "не додел", "незаверш", "не заверш")
    task_markers = ("задач", "таск", "task")
    memory_markers = ("помни", "вспом", "что делал", "что была", "что было")
    command_markers = ("начни", "запусти", "продолж", "повтори", "возобнов", "сделай", "заново", "сначала")
    if any(marker in text for marker in previous_markers) and any(marker in text for marker in task_markers) and (
        any(marker in text for marker in memory_markers + command_markers) or "?" in text
    ):
        return True
    if any(marker in text for marker in unfinished_markers) and any(marker in text for marker in task_markers) and (
        any(marker in text for marker in memory_markers + command_markers) or "?" in text
    ):
        return True
    status_phrases = (
        "живой",
        "ты жив",
        "ты тут",
        "работаешь",
        "проверь статус",
        "status",
        "state",
        "health",
    )
    compact = text.strip(" ?.!")
    if len(compact) <= 80 and compact in {"еще раз", "ещё раз", "повтори", "повтори еще раз", "повтори ещё раз"}:
        return True
    return len(compact) <= 80 and any(phrase in compact for phrase in status_phrases)


def is_contextless_task_reference(task: str) -> bool:
    compact = " ".join(str(task or "").lower().split()).strip(" ?.!")
    if not compact or len(compact) > 140:
        return False
    exact_phrases = {
        "продолжи",
        "продолжи работу",
        "продолжай",
        "продолжай работу",
        "закончи",
        "закончи задачу",
        "доделай",
        "доделай задачу",
        "доделай работу",
        "начни сначала",
        "начни заново",
        "начни сначала ее",
        "начни сначала её",
        "начни заново ее",
        "начни заново её",
    }
    if compact in exact_phrases:
        return True
    reference_markers = ("эту задач", "этой задач", "ее", "её", "продолжением")
    action_markers = ("продолж", "закончи", "додел", "займись", "возобнов", "начни")
    return any(marker in compact for marker in reference_markers) and any(marker in compact for marker in action_markers)


def latest_completed_task_summary(exclude_task_id: str | None = None) -> dict[str, Any]:
    exclude = safe_task_id(exclude_task_id) if exclude_task_id else ""
    try:
        journals = sorted(
            (path for path in TASK_JOURNAL_DIR.glob("*.jsonl") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in journals:
            task_id = safe_task_id(path.stem)
            if exclude and task_id == exclude:
                continue
            payload = read_task_journal(task_id, limit=500)
            if not payload.get("ok"):
                continue
            events = payload.get("events", [])
            summary = summarize_task_events(task_id, events if isinstance(events, list) else [])
            if is_meta_or_status_task(str(summary.get("task") or "")):
                continue
            if summary.get("ok"):
                return summary
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": "completed task journal not found"}


def recent_task_summaries(limit: int = 20, prefix: str | None = None) -> dict[str, Any]:
    try:
        safe_limit = max(1, min(int(limit or 20), 100))
    except (TypeError, ValueError):
        safe_limit = 20
    safe_prefix = safe_task_id(prefix) if prefix else ""
    try:
        journals = sorted(
            (path for path in TASK_JOURNAL_DIR.glob("*.jsonl") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        tasks: list[dict[str, Any]] = []
        for path in journals:
            task_id = safe_task_id(path.stem)
            if safe_prefix and not task_id.startswith(safe_prefix):
                continue
            payload = read_task_journal(task_id, limit=500)
            if not payload.get("ok"):
                continue
            events = payload.get("events", [])
            if not isinstance(events, list):
                events = []
            summary = summarize_task_events(task_id, events)
            start_event = next((event for event in events if isinstance(event, dict) and event.get("type") == "start"), {})
            final_event = next((event for event in reversed(events) if isinstance(event, dict) and event.get("type") == "final"), {})
            last_event = next((event for event in reversed(events) if isinstance(event, dict)), {})
            summary.update(
                {
                    "created_at": start_event.get("ts") or "",
                    "updated_at": last_event.get("ts") or "",
                    "running": False,
                    "event_count": payload.get("event_count", len(events)),
                    "last_event_type": last_event.get("type") or "",
                }
            )
            tasks.append(summary)
            if len(tasks) >= safe_limit:
                break
        return {"ok": True, "tasks": tasks, "limit": safe_limit, "prefix": safe_prefix}
    except OSError as exc:
        return {"ok": False, "error": str(exc), "tasks": []}


def compact_resume_events(events: list[Any], max_chars: int = 6000) -> list[Any]:
    selected: list[Any] = []
    total = 2
    start_event = next((event for event in events if isinstance(event, dict) and event.get("type") == "start"), None)
    if start_event is not None:
        compacted_start = compact_json_value(start_event, string_limit=1800, list_limit=30)
        text = json.dumps(compacted_start, ensure_ascii=False, separators=(",", ":"))
        if len(text) + 3 <= max_chars:
            selected.append(compacted_start)
            total += len(text) + 1
    for event in reversed(events):
        if event is start_event:
            continue
        compacted = compact_json_value(event, string_limit=500, list_limit=8)
        text = json.dumps(compacted, ensure_ascii=False, separators=(",", ":"))
        if selected and total + len(text) + 1 > max_chars:
            break
        selected.append(compacted)
        total += len(text) + 1
    if selected and start_event is not None and selected[0].get("type") == "start":
        return [selected[0], *reversed(selected[1:])]
    return list(reversed(selected))
