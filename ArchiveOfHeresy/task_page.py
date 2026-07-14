"""Canonical per-task memory for ArchiveOfHeresy.

The database is the source of truth.  Markdown is a bounded rendering of the
current structured snapshot, never a second mutable copy in the shared wiki.
One durable ``task_memory_id`` may have many attempt/run aliases so switching
tasks acts like switching compacted conversations instead of losing context.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from archive_config import (
    LEGACY_SHARED_MEMORY_NAMESPACES,
    SHARED_MEMORY_NAMESPACE,
    TASK_MEMORY_BUSY_TIMEOUT_MS,
    TASK_MEMORY_MAX_EVENT_BYTES,
    TASK_MEMORY_MAX_LIST_ITEMS,
    TASK_MEMORY_MAX_SNAPSHOT_BYTES,
    TASK_MEMORY_MAX_TEXT_CHARS,
    TASK_MEMORY_CONTEXT_CHARS,
    TASK_MEMORY_SQLITE_PATH,
)


SCHEMA_VERSION = 1
_TASK_MEMORY_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_RECENT_EVENT_LIMIT = min(24, TASK_MEMORY_MAX_LIST_ITEMS)

_IMMUTABLE_FIELDS = {
    "schema_version",
    "task_memory_id",
    "root_task_id",
    "goal_verbatim",
    "created_at",
}
_TEXT_FIELDS = {
    "desired_outcome",
    "state",
    "current_strategy",
    "source_cursor",
    "updated_by",
    "legacy_body",
}
_LIST_FIELDS = {
    "success_conditions",
    "constraints",
    "decisions",
    "completed_work",
    "failed_approaches",
    "working_set",
    "next_actions",
    "open_requirements",
    "journal",
    "recent_events",
    "aliases",
}
_MUTABLE_FIELDS = _TEXT_FIELDS | _LIST_FIELDS | {"attempt"}
_SNAPSHOT_FIELDS = _IMMUTABLE_FIELDS | _MUTABLE_FIELDS | {"updated_at"}


class TaskPageError(RuntimeError):
    code = "task_page_error"
    status = 400

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}

    def response(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": False,
            "error": str(self),
            "code": self.code,
        }
        if self.details:
            result["details"] = self.details
        return result


class TaskPageValidationError(TaskPageError):
    code = "task_page_invalid"
    status = 400


class TaskPageNotFound(TaskPageError):
    code = "task_page_not_found"
    status = 404


class TaskPageConflict(TaskPageError):
    code = "task_page_conflict"
    status = 409


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _namespace(value: str | None) -> str:
    raw = str(value or SHARED_MEMORY_NAMESPACE).strip().lower()
    safe = "".join(char if char.isalnum() or char in ("-", "_") else "-" for char in raw)
    safe = safe.strip("-_")[:64] or "default"
    if safe in LEGACY_SHARED_MEMORY_NAMESPACES:
        shared = str(SHARED_MEMORY_NAMESPACE or "shushunya").strip().lower()
        safe = "".join(char if char.isalnum() or char in ("-", "_") else "-" for char in shared)
        safe = safe.strip("-_")[:64] or "shushunya"
    return safe


def _bounded_identifier(value: Any, field: str, *, max_chars: int = 256) -> str:
    text = str(value or "").strip()
    if not text:
        raise TaskPageValidationError(f"{field} is required")
    if len(text) > max_chars or _CONTROL_RE.search(text):
        raise TaskPageValidationError(
            f"{field} must contain 1-{max_chars} printable characters"
        )
    return text


def _task_memory_id(value: Any) -> str:
    result = _bounded_identifier(value, "task_memory_id", max_chars=128)
    if not _TASK_MEMORY_ID_RE.fullmatch(result):
        raise TaskPageValidationError(
            "task_memory_id must use only ASCII letters, digits, '.', '_' or '-'"
        )
    return result


def _derived_task_memory_id(namespace: str, root_task_id: str) -> str:
    digest = hashlib.sha256(f"{namespace}\0{root_task_id}".encode("utf-8")).hexdigest()[:32]
    return f"tm_{digest}"


def _json_blob(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TaskPageValidationError(f"value is not canonical JSON: {exc}") from exc


def _validate_json_value(value: Any, path: str, *, depth: int = 0) -> None:
    if depth > 5:
        raise TaskPageValidationError(f"{path} is nested too deeply")
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and len(value) > TASK_MEMORY_MAX_TEXT_CHARS:
            raise TaskPageValidationError(
                f"{path} exceeds {TASK_MEMORY_MAX_TEXT_CHARS} characters"
            )
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            raise TaskPageValidationError(f"{path} must be finite")
        return
    if isinstance(value, list):
        if len(value) > TASK_MEMORY_MAX_LIST_ITEMS:
            raise TaskPageValidationError(
                f"{path} exceeds {TASK_MEMORY_MAX_LIST_ITEMS} items"
            )
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]", depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > TASK_MEMORY_MAX_LIST_ITEMS:
            raise TaskPageValidationError(
                f"{path} exceeds {TASK_MEMORY_MAX_LIST_ITEMS} keys"
            )
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128 or _CONTROL_RE.search(key):
                raise TaskPageValidationError(f"{path} contains an invalid key")
            _validate_json_value(item, f"{path}.{key}", depth=depth + 1)
        return
    raise TaskPageValidationError(f"{path} contains unsupported type {type(value).__name__}")


def _normalise_aliases(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple, set)):
        raise TaskPageValidationError("aliases must be a list")
    aliases = sorted({_bounded_identifier(item, "alias") for item in values})
    if len(aliases) > TASK_MEMORY_MAX_LIST_ITEMS:
        raise TaskPageValidationError(
            f"aliases exceeds {TASK_MEMORY_MAX_LIST_ITEMS} items"
        )
    return aliases


def _alias_projection(root_task_id: str, *groups: Any) -> list[str]:
    """Keep root plus the newest aliases in the bounded rendered snapshot.

    The complete alias history lives in ``task_aliases`` and is never truncated.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for alias in list(group or []):
            clean = _bounded_identifier(alias, "alias")
            if clean not in seen:
                seen.add(clean)
                ordered.append(clean)
    root = _bounded_identifier(root_task_id, "root_task_id")
    newest = [alias for alias in ordered if alias != root]
    if TASK_MEMORY_MAX_LIST_ITEMS <= 1:
        return [root]
    return sorted([root, *newest[-(TASK_MEMORY_MAX_LIST_ITEMS - 1) :]])


def _validate_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise TaskPageValidationError("snapshot must be an object")
    unknown = sorted(set(snapshot) - _SNAPSHOT_FIELDS)
    if unknown:
        raise TaskPageValidationError(f"unknown snapshot fields: {', '.join(unknown)}")
    missing = sorted((_IMMUTABLE_FIELDS | {"updated_at"}) - set(snapshot))
    if missing:
        raise TaskPageValidationError(f"snapshot is missing fields: {', '.join(missing)}")
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        raise TaskPageValidationError(f"schema_version must be {SCHEMA_VERSION}")
    _task_memory_id(snapshot.get("task_memory_id"))
    _bounded_identifier(snapshot.get("root_task_id"), "root_task_id")
    goal = snapshot.get("goal_verbatim")
    if not isinstance(goal, str) or not goal.strip():
        raise TaskPageValidationError("goal_verbatim must be a non-empty string")
    if len(goal) > TASK_MEMORY_MAX_TEXT_CHARS:
        raise TaskPageValidationError(
            f"goal_verbatim exceeds {TASK_MEMORY_MAX_TEXT_CHARS} characters"
        )
    for field in ("created_at", "updated_at"):
        if not isinstance(snapshot.get(field), str) or not snapshot[field]:
            raise TaskPageValidationError(f"{field} must be a non-empty string")
    for field in _TEXT_FIELDS:
        value = snapshot.get(field, "")
        if not isinstance(value, str):
            raise TaskPageValidationError(f"{field} must be a string")
    for field in _LIST_FIELDS:
        value = snapshot.get(field, [])
        if not isinstance(value, list):
            raise TaskPageValidationError(f"{field} must be a list")
    attempt = snapshot.get("attempt", 0)
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
        raise TaskPageValidationError("attempt must be a non-negative integer")
    aliases = _normalise_aliases(snapshot.get("aliases", []))
    if aliases != snapshot.get("aliases", []):
        raise TaskPageValidationError("aliases must be unique and sorted")
    _validate_json_value(snapshot, "snapshot")
    blob = _json_blob(snapshot)
    if len(blob) > TASK_MEMORY_MAX_SNAPSHOT_BYTES:
        raise TaskPageValidationError(
            f"snapshot exceeds {TASK_MEMORY_MAX_SNAPSHOT_BYTES} UTF-8 bytes"
        )
    return snapshot


def _snapshot_blob(snapshot: dict[str, Any]) -> tuple[str, str]:
    _validate_snapshot(snapshot)
    blob = _json_blob(snapshot)
    return blob.decode("utf-8"), hashlib.sha256(blob).hexdigest()


def _render_item(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def render_task_page(snapshot: dict[str, Any], *, revision: int, sha256: str) -> str:
    """Render the current compact snapshot as deterministic Markdown."""
    lines = [
        f"# Задача: {snapshot['root_task_id']}",
        "",
        f"> task_memory_id: `{snapshot['task_memory_id']}` · revision: `{revision}` · sha256: `{sha256}`",
        "",
        "## Цель",
        "",
        snapshot["goal_verbatim"],
    ]
    sections = (
        ("Желаемый результат", "desired_outcome"),
        ("Критерии успеха", "success_conditions"),
        ("Ограничения", "constraints"),
        ("Состояние", "state"),
        ("Текущая стратегия", "current_strategy"),
        ("Решения", "decisions"),
        ("Сделано", "completed_work"),
        ("Неудачные подходы", "failed_approaches"),
        ("Рабочий набор", "working_set"),
        ("Следующие действия", "next_actions"),
        ("Что требуется", "open_requirements"),
        ("Алиасы", "aliases"),
        ("Журнал", "journal"),
        ("Недавние события", "recent_events"),
        ("Legacy body", "legacy_body"),
    )
    for heading, field in sections:
        value = snapshot.get(field)
        if value in (None, "", []):
            continue
        lines.extend(["", f"## {heading}", ""])
        if isinstance(value, list):
            lines.extend(f"- {_render_item(item)}" for item in value)
        else:
            lines.append(str(value))
    lines.extend(["", f"_Обновлено: {snapshot['updated_at']}; попытка: {snapshot.get('attempt', 0)}._"])
    return "\n".join(lines).strip()


def _clip_context_text(value: Any, limit: int) -> str:
    text = _render_item(value).strip()
    if len(text) <= max(0, limit):
        return text
    return text[: max(0, limit - 1)].rstrip() + ("…" if limit else "")


def _render_context_section(
    heading: str,
    value: Any,
    budget: int,
    *,
    newest_first: bool = False,
) -> str:
    prefix = f"## {heading}\n"
    if value in (None, "", []):
        return ""
    available = max(0, budget - len(prefix))
    if available < 8:
        return ""
    if not isinstance(value, list):
        return prefix + _clip_context_text(value, available)
    candidates = list(value)
    if newest_first:
        candidates.reverse()
    selected: list[str] = []
    used = 0
    for item in candidates:
        remaining = available - used - 2
        if remaining < 4:
            break
        rendered = _clip_context_text(item, min(400, remaining))
        if not rendered:
            continue
        line = f"- {rendered}"
        selected.append(line)
        used += len(line) + 1
    if newest_first:
        selected.reverse()
    return prefix + "\n".join(selected)


def render_task_context(
    snapshot: dict[str, Any],
    *,
    max_chars: int = TASK_MEMORY_CONTEXT_CHARS,
) -> str:
    """Render a priority-aware compact handoff without prefix/tail truncation."""
    safe_limit = max(400, min(int(max_chars), 50_000))
    identity = (
        f"task_memory_id={snapshot.get('task_memory_id') or ''}\n"
        f"root_task_id={snapshot.get('root_task_id') or ''}\n"
        f"attempt={snapshot.get('attempt', 0)}"
    )
    sections = [
        ("Цель", snapshot.get("goal_verbatim"), 20, False),
        ("Желаемый результат", snapshot.get("desired_outcome"), 6, False),
        ("Состояние в памяти (не live-статус)", snapshot.get("state"), 5, False),
        ("Критерии успеха", snapshot.get("success_conditions"), 10, False),
        ("Ограничения", snapshot.get("constraints"), 7, False),
        ("Текущая стратегия", snapshot.get("current_strategy"), 9, False),
        ("Решения", snapshot.get("decisions"), 10, True),
        ("Следующие действия", snapshot.get("next_actions"), 10, True),
        ("Что требуется", snapshot.get("open_requirements"), 5, True),
        ("Сделано", snapshot.get("completed_work"), 7, True),
        ("Неудачные подходы", snapshot.get("failed_approaches"), 5, True),
        ("Рабочий набор", snapshot.get("working_set"), 4, True),
        ("Журнал", snapshot.get("journal"), 1, True),
        ("Недавние события", snapshot.get("recent_events"), 1, True),
    ]
    active = [section for section in sections if section[1] not in (None, "", [])]
    header = "[Сжатая память задачи]\n" + identity
    available = max(0, safe_limit - len(header) - 2)
    total_weight = sum(section[2] for section in active) or 1
    rendered = [header]
    used_budget = 0
    for index, (heading, value, weight, newest_first) in enumerate(active):
        if index == len(active) - 1:
            budget = available - used_budget
        else:
            budget = max(32, (available * weight) // total_weight)
            budget = min(budget, available - used_budget)
        section = _render_context_section(
            heading,
            value,
            budget,
            newest_first=newest_first,
        )
        if section:
            rendered.append(section)
        used_budget += budget
    result = "\n\n".join(rendered)
    return result if len(result) <= safe_limit else result[:safe_limit]


class TaskPageStore:
    """SQLite-backed task pages with CAS revisions and idempotent events."""

    def __init__(self, db_path: str | Path = TASK_MEMORY_SQLITE_PATH):
        self.db_path = Path(db_path)
        self._schema_lock = threading.Lock()
        self._schema_ready = False

    def _raw_connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(self.db_path),
            timeout=TASK_MEMORY_BUSY_TIMEOUT_MS / 1000.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {TASK_MEMORY_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with closing(self._raw_connect()) as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = NORMAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS task_pages (
                        task_memory_id TEXT PRIMARY KEY,
                        namespace TEXT NOT NULL,
                        root_task_id TEXT NOT NULL,
                        schema_version INTEGER NOT NULL,
                        revision INTEGER NOT NULL CHECK (revision >= 1),
                        snapshot_json TEXT NOT NULL,
                        snapshot_sha256 TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(namespace, root_task_id)
                    );
                    CREATE TABLE IF NOT EXISTS task_aliases (
                        namespace TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        task_memory_id TEXT NOT NULL,
                        relation TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(namespace, task_id),
                        FOREIGN KEY(task_memory_id) REFERENCES task_pages(task_memory_id) ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS task_aliases_memory_idx
                        ON task_aliases(task_memory_id);
                    CREATE TABLE IF NOT EXISTS task_events (
                        task_memory_id TEXT NOT NULL,
                        seq INTEGER NOT NULL,
                        idempotency_key TEXT NOT NULL,
                        actor TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        payload_sha256 TEXT NOT NULL,
                        result_revision INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(task_memory_id, seq),
                        UNIQUE(task_memory_id, idempotency_key),
                        FOREIGN KEY(task_memory_id) REFERENCES task_pages(task_memory_id) ON DELETE CASCADE
                    );
                    """
                )
            self._schema_ready = True

    def _connect(self) -> sqlite3.Connection:
        self._ensure_schema()
        return self._raw_connect()

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _resolve_row(
        connection: sqlite3.Connection,
        *,
        namespace: str,
        task_id: str | None = None,
        task_memory_id: str | None = None,
    ) -> sqlite3.Row | None:
        memory_row = None
        if task_memory_id:
            memory_row = connection.execute(
                "SELECT * FROM task_pages WHERE task_memory_id = ? AND namespace = ?",
                (task_memory_id, namespace),
            ).fetchone()
        if not task_id:
            return memory_row
        task_row = connection.execute(
            "SELECT * FROM task_pages WHERE task_memory_id = ? AND namespace = ?",
            (task_id, namespace),
        ).fetchone()
        if not task_row:
            task_row = connection.execute(
                """
                SELECT p.*
                  FROM task_aliases a
                  JOIN task_pages p ON p.task_memory_id = a.task_memory_id
                 WHERE a.namespace = ? AND a.task_id = ?
                """,
                (namespace, task_id),
            ).fetchone()
        if task_memory_id:
            if not memory_row or not task_row:
                raise TaskPageConflict(
                    "task_id and task_memory_id must both resolve to the same page"
                )
            if memory_row["task_memory_id"] != task_row["task_memory_id"]:
                raise TaskPageConflict(
                    "task_id and task_memory_id identify different pages",
                    details={
                        "task_id": task_id,
                        "task_memory_id": task_memory_id,
                    },
                )
            return memory_row
        return task_row

    @staticmethod
    def _document(row: sqlite3.Row | dict[str, Any], *, task_id: str | None = None) -> dict[str, Any]:
        snapshot = json.loads(row["snapshot_json"])
        revision = int(row["revision"])
        sha256 = str(row["snapshot_sha256"])
        return {
            "ok": True,
            "task_id": task_id or row["root_task_id"],
            "task_memory_id": row["task_memory_id"],
            "namespace": row["namespace"],
            "root_task_id": row["root_task_id"],
            "revision": revision,
            "sha256": sha256,
            "snapshot": snapshot,
            "content": render_task_page(snapshot, revision=revision, sha256=sha256),
            "context": render_task_context(snapshot),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def lookup(
        self,
        *,
        task_id: str | None = None,
        task_memory_id: str | None = None,
        namespace: str | None = None,
    ) -> dict[str, Any] | None:
        ns = _namespace(namespace)
        clean_task_id = _bounded_identifier(task_id, "task_id") if task_id else None
        clean_memory_id = _task_memory_id(task_memory_id) if task_memory_id else None
        if not clean_task_id and not clean_memory_id:
            raise TaskPageValidationError("task_id or task_memory_id is required")
        with self._connection() as connection:
            row = self._resolve_row(
                connection,
                namespace=ns,
                task_id=clean_task_id,
                task_memory_id=clean_memory_id,
            )
            return self._document(row, task_id=clean_task_id) if row else None

    def get(self, **references: Any) -> dict[str, Any]:
        document = self.lookup(**references)
        if document is None:
            raise TaskPageNotFound("task page does not exist")
        return document

    @staticmethod
    def _insert_aliases(
        connection: sqlite3.Connection,
        *,
        namespace: str,
        task_memory_id: str,
        root_task_id: str,
        aliases: list[str],
        created_at: str,
    ) -> None:
        for alias in aliases:
            page_with_that_id = connection.execute(
                "SELECT task_memory_id FROM task_pages WHERE task_memory_id = ? AND namespace = ?",
                (alias, namespace),
            ).fetchone()
            if page_with_that_id and page_with_that_id["task_memory_id"] != task_memory_id:
                raise TaskPageConflict(
                    f"alias collides with another task_memory_id: {alias}",
                    details={"alias": alias, "task_memory_id": page_with_that_id["task_memory_id"]},
                )
            existing = connection.execute(
                "SELECT task_memory_id FROM task_aliases WHERE namespace = ? AND task_id = ?",
                (namespace, alias),
            ).fetchone()
            if existing and existing["task_memory_id"] != task_memory_id:
                raise TaskPageConflict(
                    f"alias already belongs to another task page: {alias}",
                    details={"alias": alias, "task_memory_id": existing["task_memory_id"]},
                )
            relation = "root" if alias == root_task_id else "alias"
            connection.execute(
                """
                INSERT OR IGNORE INTO task_aliases
                    (namespace, task_id, task_memory_id, relation, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (namespace, alias, task_memory_id, relation, created_at),
            )

    def init(
        self,
        *,
        root_task_id: str,
        goal_verbatim: str,
        task_id: str | None = None,
        task_memory_id: str | None = None,
        namespace: str | None = None,
        snapshot: dict[str, Any] | None = None,
        aliases: list[str] | None = None,
        actor: str = "archive",
    ) -> dict[str, Any]:
        ns = _namespace(namespace)
        root = _bounded_identifier(root_task_id, "root_task_id")
        goal = str(goal_verbatim or "").strip()
        if not goal:
            raise TaskPageValidationError("goal_verbatim is required")
        if len(goal) > TASK_MEMORY_MAX_TEXT_CHARS:
            raise TaskPageValidationError(
                f"goal_verbatim exceeds {TASK_MEMORY_MAX_TEXT_CHARS} characters"
            )
        memory_id = _task_memory_id(task_memory_id) if task_memory_id else _derived_task_memory_id(ns, root)
        requested_task_id = _bounded_identifier(task_id, "task_id") if task_id else root
        requested_aliases = _normalise_aliases(aliases or [])
        alias_list = _normalise_aliases([root, requested_task_id, *requested_aliases])
        initial = copy.deepcopy(snapshot or {})
        if not isinstance(initial, dict):
            raise TaskPageValidationError("snapshot must be an object")
        unknown = sorted(set(initial) - _MUTABLE_FIELDS - _IMMUTABLE_FIELDS - {"updated_at"})
        if unknown:
            raise TaskPageValidationError(f"unknown snapshot fields: {', '.join(unknown)}")
        for field, expected in {
            "schema_version": SCHEMA_VERSION,
            "task_memory_id": memory_id,
            "root_task_id": root,
            "goal_verbatim": goal,
        }.items():
            if field in initial and initial[field] != expected:
                raise TaskPageConflict(f"snapshot {field} does not match init identity")
        initial_aliases = _normalise_aliases(initial.pop("aliases", []))
        alias_list = _normalise_aliases([*alias_list, *initial_aliases])
        now = _now_iso()
        values: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "task_memory_id": memory_id,
            "root_task_id": root,
            "goal_verbatim": goal,
            "created_at": now,
            "updated_at": now,
            "desired_outcome": "",
            "state": "created",
            "current_strategy": "",
            "source_cursor": "",
            "updated_by": str(actor or "archive")[:128],
            "legacy_body": "",
            "attempt": 0,
            "success_conditions": [],
            "constraints": [],
            "decisions": [],
            "completed_work": [],
            "failed_approaches": [],
            "working_set": [],
            "next_actions": [],
            "open_requirements": [],
            "journal": [],
            "recent_events": [],
            "aliases": alias_list,
        }
        for field, value in initial.items():
            if field not in _IMMUTABLE_FIELDS | {"updated_at"}:
                values[field] = copy.deepcopy(value)
        snapshot_json, snapshot_sha256 = _snapshot_blob(values)
        actor_text = _bounded_identifier(actor or "archive", "actor", max_chars=128)

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                memory_id_alias = connection.execute(
                    "SELECT task_memory_id FROM task_aliases WHERE namespace = ? AND task_id = ?",
                    (ns, memory_id),
                ).fetchone()
                if memory_id_alias and memory_id_alias["task_memory_id"] != memory_id:
                    raise TaskPageConflict(
                        "task_memory_id is already used as an alias of another page",
                        details={
                            "alias": memory_id,
                            "task_memory_id": memory_id_alias["task_memory_id"],
                        },
                    )
                existing = connection.execute(
                    "SELECT * FROM task_pages WHERE task_memory_id = ?",
                    (memory_id,),
                ).fetchone()
                by_root = connection.execute(
                    "SELECT * FROM task_pages WHERE namespace = ? AND root_task_id = ?",
                    (ns, root),
                ).fetchone()
                if existing and existing["namespace"] != ns:
                    raise TaskPageConflict("task_memory_id already exists in another namespace")
                if existing and by_root and existing["task_memory_id"] != by_root["task_memory_id"]:
                    raise TaskPageConflict("root task and task_memory_id identify different pages")
                current = existing or by_root
                if current:
                    current_snapshot = json.loads(current["snapshot_json"])
                    if current["root_task_id"] != root or current_snapshot.get("goal_verbatim") != goal:
                        raise TaskPageConflict(
                            "root_task_id and goal_verbatim are immutable",
                            details={"task_memory_id": current["task_memory_id"]},
                        )
                    if task_memory_id and current["task_memory_id"] != memory_id:
                        raise TaskPageConflict("requested task_memory_id does not match existing root task")
                    for field, value in initial.items():
                        if field not in _IMMUTABLE_FIELDS | {"updated_at", "aliases"} and current_snapshot.get(field) != value:
                            raise TaskPageConflict(f"existing task page has different initial field: {field}")
                    merged_aliases = _alias_projection(
                        root,
                        current_snapshot.get("aliases", []),
                        alias_list,
                    )
                    self._insert_aliases(
                        connection,
                        namespace=ns,
                        task_memory_id=current["task_memory_id"],
                        root_task_id=root,
                        aliases=alias_list,
                        created_at=now,
                    )
                    if merged_aliases == current_snapshot.get("aliases", []):
                        connection.commit()
                        document = self._document(current, task_id=requested_task_id)
                        document["idempotent_replay"] = True
                        return document
                    current_snapshot["aliases"] = merged_aliases
                    current_snapshot["updated_at"] = now
                    current_snapshot["updated_by"] = actor_text
                    new_json, new_sha = _snapshot_blob(current_snapshot)
                    new_revision = int(current["revision"]) + 1
                    connection.execute(
                        """
                        UPDATE task_pages
                           SET revision = ?, snapshot_json = ?, snapshot_sha256 = ?, updated_at = ?
                         WHERE task_memory_id = ?
                        """,
                        (new_revision, new_json, new_sha, now, current["task_memory_id"]),
                    )
                    payload = {"aliases": merged_aliases}
                    payload_blob = _json_blob(payload)
                    seq = connection.execute(
                        "SELECT COALESCE(MAX(seq), 0) + 1 FROM task_events WHERE task_memory_id = ?",
                        (current["task_memory_id"],),
                    ).fetchone()[0]
                    connection.execute(
                        """
                        INSERT INTO task_events
                            (task_memory_id, seq, idempotency_key, actor, kind, payload_json,
                             payload_sha256, result_revision, created_at)
                        VALUES (?, ?, ?, ?, 'aliases_added', ?, ?, ?, ?)
                        """,
                        (
                            current["task_memory_id"],
                            seq,
                            f"init-aliases:{hashlib.sha256(payload_blob).hexdigest()}",
                            actor_text,
                            payload_blob.decode("utf-8"),
                            hashlib.sha256(payload_blob).hexdigest(),
                            new_revision,
                            now,
                        ),
                    )
                    connection.commit()
                    return self.get(task_memory_id=current["task_memory_id"], namespace=ns)

                connection.execute(
                    """
                    INSERT INTO task_pages
                        (task_memory_id, namespace, root_task_id, schema_version, revision,
                         snapshot_json, snapshot_sha256, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
                    """,
                    (memory_id, ns, root, SCHEMA_VERSION, snapshot_json, snapshot_sha256, now, now),
                )
                self._insert_aliases(
                    connection,
                    namespace=ns,
                    task_memory_id=memory_id,
                    root_task_id=root,
                    aliases=alias_list,
                    created_at=now,
                )
                event_payload = {"root_task_id": root, "goal_verbatim": goal, "aliases": alias_list}
                event_blob = _json_blob(event_payload)
                connection.execute(
                    """
                    INSERT INTO task_events
                        (task_memory_id, seq, idempotency_key, actor, kind, payload_json,
                         payload_sha256, result_revision, created_at)
                    VALUES (?, 1, ?, ?, 'init', ?, ?, 1, ?)
                    """,
                    (
                        memory_id,
                        f"init:{memory_id}",
                        actor_text,
                        event_blob.decode("utf-8"),
                        hashlib.sha256(event_blob).hexdigest(),
                        now,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get(task_memory_id=memory_id, namespace=ns)

    @staticmethod
    def _apply_patch(snapshot: dict[str, Any], patch: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        if not isinstance(patch, dict):
            raise TaskPageValidationError("patch must be an object")
        immutable = sorted(set(patch) & (_IMMUTABLE_FIELDS | {"updated_at"}))
        if immutable:
            raise TaskPageConflict(
                f"immutable snapshot fields cannot be changed: {', '.join(immutable)}"
            )
        unknown = sorted(set(patch) - _MUTABLE_FIELDS)
        if unknown:
            raise TaskPageValidationError(f"unknown snapshot fields: {', '.join(unknown)}")
        result = copy.deepcopy(snapshot)
        alias_additions: list[str] = []
        for field, value in patch.items():
            if field == "aliases":
                alias_additions = _normalise_aliases(value)
                result["aliases"] = _alias_projection(
                    str(result.get("root_task_id") or ""),
                    result.get("aliases", []),
                    alias_additions,
                )
            else:
                result[field] = copy.deepcopy(value)
        return result, alias_additions

    def _mutate(
        self,
        *,
        task_id: str | None,
        task_memory_id: str | None,
        namespace: str | None,
        expected_revision: int,
        idempotency_key: str,
        actor: str,
        kind: str,
        event_payload: dict[str, Any],
        transform: Callable[[dict[str, Any]], tuple[dict[str, Any], list[str]]],
    ) -> dict[str, Any]:
        ns = _namespace(namespace)
        clean_task_id = _bounded_identifier(task_id, "task_id") if task_id else None
        clean_memory_id = _task_memory_id(task_memory_id) if task_memory_id else None
        if not clean_task_id and not clean_memory_id:
            raise TaskPageValidationError("task_id or task_memory_id is required")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise TaskPageValidationError("expected_revision must be a positive integer")
        key = _bounded_identifier(idempotency_key, "idempotency_key", max_chars=200)
        actor_text = _bounded_identifier(actor or "unknown", "actor", max_chars=128)
        kind_text = _bounded_identifier(kind or "event", "kind", max_chars=128)
        if not isinstance(event_payload, dict):
            raise TaskPageValidationError("event payload must be an object")
        _validate_json_value(event_payload, "event")
        payload_blob = _json_blob(event_payload)
        if len(payload_blob) > TASK_MEMORY_MAX_EVENT_BYTES:
            raise TaskPageValidationError(
                f"event exceeds {TASK_MEMORY_MAX_EVENT_BYTES} UTF-8 bytes"
            )
        payload_sha = hashlib.sha256(payload_blob).hexdigest()
        now = _now_iso()

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._resolve_row(
                    connection,
                    namespace=ns,
                    task_id=clean_task_id,
                    task_memory_id=clean_memory_id,
                )
                if not row:
                    raise TaskPageNotFound("task page does not exist")
                prior = connection.execute(
                    """
                    SELECT actor, kind, payload_sha256
                      FROM task_events
                     WHERE task_memory_id = ? AND idempotency_key = ?
                    """,
                    (row["task_memory_id"], key),
                ).fetchone()
                if prior:
                    if (
                        prior["actor"] != actor_text
                        or prior["kind"] != kind_text
                        or prior["payload_sha256"] != payload_sha
                    ):
                        raise TaskPageConflict(
                            "idempotency_key was already used with a different payload",
                            details={"idempotency_key": key},
                        )
                    connection.commit()
                    current = self.get(task_memory_id=row["task_memory_id"], namespace=ns)
                    current["idempotent_replay"] = True
                    return current
                current_revision = int(row["revision"])
                if current_revision != expected_revision:
                    raise TaskPageConflict(
                        "stale task page revision",
                        details={
                            "expected_revision": expected_revision,
                            "current_revision": current_revision,
                            "task_memory_id": row["task_memory_id"],
                        },
                    )
                snapshot = json.loads(row["snapshot_json"])
                updated, alias_additions = transform(snapshot)
                updated["updated_at"] = now
                updated["updated_by"] = actor_text
                summary_source = (
                    event_payload.get("summary")
                    or event_payload.get("note")
                    or event_payload.get("message")
                    or payload_blob.decode("utf-8")
                )
                summary = str(summary_source)[:1000]
                recent = list(updated.get("recent_events", []))
                recent.append(
                    {
                        "revision": current_revision + 1,
                        "at": now,
                        "actor": actor_text,
                        "kind": kind_text,
                        "summary": summary,
                    }
                )
                updated["recent_events"] = recent[-_RECENT_EVENT_LIMIT:]
                if kind_text in {"note", "legacy_note"} and event_payload.get("note") is not None:
                    journal = list(updated.get("journal", []))
                    journal.append(
                        {
                            "at": now,
                            "actor": actor_text,
                            "note": str(event_payload.get("note"))[:TASK_MEMORY_MAX_TEXT_CHARS],
                        }
                    )
                    updated["journal"] = journal[-TASK_MEMORY_MAX_LIST_ITEMS:]
                new_json, new_sha = _snapshot_blob(updated)
                new_revision = current_revision + 1
                for alias in alias_additions:
                    self._insert_aliases(
                        connection,
                        namespace=ns,
                        task_memory_id=row["task_memory_id"],
                        root_task_id=row["root_task_id"],
                        aliases=[alias],
                        created_at=now,
                    )
                changed = connection.execute(
                    """
                    UPDATE task_pages
                       SET revision = ?, snapshot_json = ?, snapshot_sha256 = ?, updated_at = ?
                     WHERE task_memory_id = ? AND revision = ?
                    """,
                    (new_revision, new_json, new_sha, now, row["task_memory_id"], current_revision),
                ).rowcount
                if changed != 1:
                    raise TaskPageConflict("task page changed during CAS update")
                seq = connection.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM task_events WHERE task_memory_id = ?",
                    (row["task_memory_id"],),
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO task_events
                        (task_memory_id, seq, idempotency_key, actor, kind, payload_json,
                         payload_sha256, result_revision, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["task_memory_id"],
                        seq,
                        key,
                        actor_text,
                        kind_text,
                        payload_blob.decode("utf-8"),
                        payload_sha,
                        new_revision,
                        now,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get(task_memory_id=row["task_memory_id"], namespace=ns)

    def checkpoint(
        self,
        *,
        patch: dict[str, Any],
        expected_revision: int,
        idempotency_key: str,
        task_id: str | None = None,
        task_memory_id: str | None = None,
        namespace: str | None = None,
        actor: str = "unknown",
    ) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise TaskPageValidationError("patch must be an object")
        patch_copy = copy.deepcopy(patch)
        return self._mutate(
            task_id=task_id,
            task_memory_id=task_memory_id,
            namespace=namespace,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            actor=actor,
            kind="checkpoint",
            event_payload={"patch": patch_copy, "summary": f"checkpoint: {', '.join(sorted(patch_copy))}"},
            transform=lambda snapshot: self._apply_patch(snapshot, patch_copy),
        )

    def event(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        expected_revision: int,
        idempotency_key: str,
        task_id: str | None = None,
        task_memory_id: str | None = None,
        namespace: str | None = None,
        actor: str = "unknown",
        patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TaskPageValidationError("event payload must be an object")
        if patch is not None and not isinstance(patch, dict):
            raise TaskPageValidationError("patch must be an object")
        patch_copy = copy.deepcopy(patch or {})
        stored_payload = copy.deepcopy(payload)
        if patch_copy:
            stored_payload["snapshot_patch"] = patch_copy
        return self._mutate(
            task_id=task_id,
            task_memory_id=task_memory_id,
            namespace=namespace,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            actor=actor,
            kind=kind,
            event_payload=stored_payload,
            transform=lambda snapshot: self._apply_patch(snapshot, patch_copy),
        )

    def events(
        self,
        *,
        task_id: str | None = None,
        task_memory_id: str | None = None,
        namespace: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        document = self.get(
            task_id=task_id,
            task_memory_id=task_memory_id,
            namespace=namespace,
        )
        bounded_limit = max(1, min(int(limit), TASK_MEMORY_MAX_LIST_ITEMS))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT seq, idempotency_key, actor, kind, payload_json,
                       payload_sha256, result_revision, created_at
                  FROM task_events
                 WHERE task_memory_id = ?
                 ORDER BY seq DESC
                 LIMIT ?
                """,
                (document["task_memory_id"], bounded_limit),
            ).fetchall()
        return [
            {
                "seq": row["seq"],
                "idempotency_key": row["idempotency_key"],
                "actor": row["actor"],
                "kind": row["kind"],
                "payload": json.loads(row["payload_json"]),
                "payload_sha256": row["payload_sha256"],
                "result_revision": row["result_revision"],
                "created_at": row["created_at"],
            }
            for row in reversed(rows)
        ]


_DEFAULT_STORE: TaskPageStore | None = None
_DEFAULT_STORE_LOCK = threading.Lock()


def default_store() -> TaskPageStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        with _DEFAULT_STORE_LOCK:
            if _DEFAULT_STORE is None:
                _DEFAULT_STORE = TaskPageStore()
    return _DEFAULT_STORE


def empty_task_page_document(task_id: str = "") -> dict[str, Any]:
    return {
        "ok": True,
        "task_id": task_id,
        "task_memory_id": None,
        "revision": 0,
        "sha256": "",
        "snapshot": {},
        "content": "",
    }


def read_task_page(task_id: str, namespace: str | None = None, *, store: TaskPageStore | None = None) -> str:
    """Backward-compatible Markdown read; returns ``''`` when absent."""
    document = (store or default_store()).lookup(task_id=task_id, namespace=namespace)
    return document["content"] if document else ""


def _legacy_goal(task_id: str, body: str = "") -> str:
    for line in str(body).splitlines():
        candidate = line.strip().lstrip("#").strip()
        if candidate and not candidate.lower().startswith("задача:"):
            return candidate[:TASK_MEMORY_MAX_TEXT_CHARS]
    return f"Task {task_id}"


def write_task_page(
    task_id: str,
    body: str,
    *,
    kind: str = "task",
    importance: int = 2,
    namespace: str | None = None,
    idempotency_key: str | None = None,
    store: TaskPageStore | None = None,
) -> dict[str, Any]:
    """Backward-compatible body write, now stored as a revisioned legacy field."""
    del kind, importance
    target = store or default_store()
    body_text = str(body)
    if len(body_text) > TASK_MEMORY_MAX_TEXT_CHARS:
        raise TaskPageValidationError(
            f"body exceeds {TASK_MEMORY_MAX_TEXT_CHARS} characters"
        )
    current = target.lookup(task_id=task_id, namespace=namespace)
    if current is None:
        return target.init(
            task_id=task_id,
            root_task_id=task_id,
            goal_verbatim=_legacy_goal(task_id, body_text),
            namespace=namespace,
            snapshot={"legacy_body": body_text, "state": "active"},
            actor="legacy_api",
        )
    key = idempotency_key or f"legacy-body:{uuid.uuid4().hex}"
    for _ in range(3):
        try:
            return target.checkpoint(
                task_id=task_id,
                namespace=namespace,
                expected_revision=current["revision"],
                idempotency_key=key,
                actor="legacy_api",
                patch={"legacy_body": body_text},
            )
        except TaskPageConflict as exc:
            if exc.details.get("current_revision") is None:
                raise
            current = target.get(task_id=task_id, namespace=namespace)
    raise TaskPageConflict("task page kept changing during legacy body write")


def append_task_note(
    task_id: str,
    note: str,
    *,
    namespace: str | None = None,
    idempotency_key: str | None = None,
    store: TaskPageStore | None = None,
) -> dict[str, Any]:
    """Backward-compatible journal append with optional retry idempotency."""
    target = store or default_store()
    note_text = str(note).strip()
    if not note_text:
        raise TaskPageValidationError("note must be non-empty")
    if len(note_text) > TASK_MEMORY_MAX_TEXT_CHARS:
        raise TaskPageValidationError(
            f"note exceeds {TASK_MEMORY_MAX_TEXT_CHARS} characters"
        )
    current = target.lookup(task_id=task_id, namespace=namespace)
    if current is None:
        current = target.init(
            task_id=task_id,
            root_task_id=task_id,
            goal_verbatim=_legacy_goal(task_id),
            namespace=namespace,
            snapshot={"state": "active"},
            actor="legacy_api",
        )
    key = idempotency_key or f"legacy-note:{uuid.uuid4().hex}"
    for _ in range(3):
        try:
            return target.event(
                task_id=task_id,
                namespace=namespace,
                expected_revision=current["revision"],
                idempotency_key=key,
                actor="legacy_api",
                kind="legacy_note",
                payload={"note": note_text, "summary": note_text},
            )
        except TaskPageConflict as exc:
            if exc.details.get("current_revision") is None:
                raise
            current = target.get(task_id=task_id, namespace=namespace)
    raise TaskPageConflict("task page kept changing during legacy note append")


def handle_task_page_post(payload: dict[str, Any], *, store: TaskPageStore | None = None) -> dict[str, Any]:
    """Pure dispatcher used by HTTP and targeted tests (no server required)."""
    if not isinstance(payload, dict):
        raise TaskPageValidationError("request body must be an object")
    target = store or default_store()
    action = str(payload.get("action") or "").strip().lower()
    if not action:
        if "note" in payload:
            action = "note"
        elif "body" in payload:
            action = "body"
        else:
            action = "init"
    task_id = str(payload.get("task_id") or "").strip() or None
    task_memory_id = str(payload.get("task_memory_id") or "").strip() or None
    namespace = payload.get("namespace")

    if action == "init":
        root_task_id = str(payload.get("root_task_id") or task_id or "").strip()
        goal = payload.get("goal_verbatim", payload.get("goal"))
        return target.init(
            task_id=task_id,
            task_memory_id=task_memory_id,
            root_task_id=root_task_id,
            goal_verbatim=str(goal or ""),
            namespace=namespace,
            snapshot=payload.get("snapshot") or {},
            aliases=payload.get("aliases") or [],
            actor=str(payload.get("actor") or "archive_http"),
        )
    if action == "event":
        return target.event(
            task_id=task_id,
            task_memory_id=task_memory_id,
            namespace=namespace,
            expected_revision=payload.get("expected_revision"),
            idempotency_key=str(payload.get("idempotency_key") or ""),
            actor=str(payload.get("actor") or "unknown"),
            kind=str(payload.get("kind") or "event"),
            payload=payload.get("event", payload.get("payload", {})),
            patch=payload.get("patch") or {},
        )
    if action == "checkpoint":
        return target.checkpoint(
            task_id=task_id,
            task_memory_id=task_memory_id,
            namespace=namespace,
            expected_revision=payload.get("expected_revision"),
            idempotency_key=str(payload.get("idempotency_key") or ""),
            actor=str(payload.get("actor") or "unknown"),
            patch=payload.get("patch", payload.get("snapshot", {})),
        )
    if action == "note":
        if not task_id:
            raise TaskPageValidationError("task_id is required for legacy note")
        return append_task_note(
            task_id,
            str(payload.get("note") or ""),
            namespace=namespace,
            idempotency_key=str(payload.get("idempotency_key") or "") or None,
            store=target,
        )
    if action == "body":
        if not task_id:
            raise TaskPageValidationError("task_id is required for legacy body")
        return write_task_page(
            task_id,
            str(payload.get("body") or ""),
            namespace=namespace,
            idempotency_key=str(payload.get("idempotency_key") or "") or None,
            store=target,
        )
    raise TaskPageValidationError(f"unsupported task-page action: {action}")
