"""Per-task wiki memory.

Every brigade task gets its own wiki page in the Archive: goal, plan, a running
journal the fighter writes as it works, and the final outcome. Two wins at once:
- the fighter gets working memory outside its context window (read/append the page);
- Shushunya gains full knowledge of everything happening on the machine — every
  task and how it ended — because these pages live in his shared wiki.
"""
from __future__ import annotations

from datetime import datetime, timezone

import archive_state
from archive_util import shared_memory_namespace, wiki_bookshelf_for_namespace

TASK_PAGE_MAX_LINES = 400


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _title(task_id: str) -> str:
    return f"Задача: {task_id}"


def _ns(namespace: str | None) -> str:
    return namespace or shared_memory_namespace()


def read_task_page(task_id: str, namespace: str | None = None) -> str:
    """Full markdown body of the task page, or '' if it does not exist yet."""
    bookshelf = wiki_bookshelf_for_namespace(_ns(namespace))
    index = bookshelf.load_index()
    page = bookshelf.find_page(index, title=_title(task_id))
    if not page:
        return ""
    content = bookshelf.read_page(page)
    if content.startswith("---"):
        parts = content.split("---", 2)
        content = parts[2] if len(parts) == 3 else content
    return content.strip()


def write_task_page(task_id: str, body: str, *, kind: str = "task",
                    importance: int = 2, namespace: str | None = None) -> None:
    """Create or replace the whole task page body."""
    bookshelf = wiki_bookshelf_for_namespace(_ns(namespace))
    with archive_state.MAINTENANCE_LOCK:
        index = bookshelf.load_index()
        page = bookshelf.find_page(index, title=_title(task_id))
        bookshelf.upsert_page(
            index,
            {
                "id": page.get("id") if page else None,
                "title": _title(task_id),
                "kind": kind,
                "importance": importance,
                "body": body,
            },
            {"turn_id": f"taskpage-{task_id}"},
        )
        bookshelf.save_index(index)


def append_task_note(task_id: str, note: str, *, namespace: str | None = None) -> None:
    """Append one timestamped line to the task page's running journal."""
    bookshelf = wiki_bookshelf_for_namespace(_ns(namespace))
    with archive_state.MAINTENANCE_LOCK:
        index = bookshelf.load_index()
        page = bookshelf.find_page(index, title=_title(task_id))
        body = ""
        if page:
            content = bookshelf.read_page(page)
            if content.startswith("---"):
                parts = content.split("---", 2)
                content = parts[2] if len(parts) == 3 else content
            body = content.strip()
        # the bookshelf prepends the title heading itself on every render; strip any
        # accumulated markdown headings so the body stays a clean journal
        lines = [ln for ln in body.splitlines() if not ln.strip().startswith("#")]
        if not any(ln.strip() for ln in lines):
            lines = ["## Журнал"]
        lines.append(f"- {_now_iso()} — {note}")
        # keep header + trailing journal within a bound
        if len(lines) > TASK_PAGE_MAX_LINES:
            head = lines[:8]
            tail = lines[-(TASK_PAGE_MAX_LINES - 8):]
            lines = head + ["- …(старые записи усечены)…"] + tail
        bookshelf.upsert_page(
            index,
            {
                "id": page.get("id") if page else None,
                "title": _title(task_id),
                "kind": "task",
                "importance": 2,
                "body": "\n".join(lines),
            },
            {"turn_id": f"taskpage-{task_id}"},
        )
        bookshelf.save_index(index)
