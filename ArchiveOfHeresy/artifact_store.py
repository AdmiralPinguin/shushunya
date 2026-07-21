"""Durable, path-free artifact registry and content-addressed blob store.

Only trusted in-process callers and the local operator CLI may import content.
HTTP clients can address an already registered artifact by opaque id, but no
HTTP route accepts a host filesystem path.
"""
from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import os
import re
import sqlite3
import stat
import unicodedata
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable

from archive_config import (
    ARTIFACT_MAX_BYTES,
    ARTIFACT_STREAM_CHUNK_BYTES,
    ARTIFACT_TOTAL_QUOTA_BYTES,
    ARTIFACTS_ROOT,
    SQLITE_PATH,
)


ARTIFACT_ID_RE = re.compile(r"art_[0-9a-f]{32}\Z")
MEDIA_TYPE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*\Z")
SCOPE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


class ArtifactError(ValueError):
    """A caller-visible artifact validation or storage-policy failure."""


class ArtifactTooLarge(ArtifactError):
    pass


class ArtifactQuotaExceeded(ArtifactError):
    pass


class ArtifactRangeError(ArtifactError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        os.chmod(path, 0o700)


@contextmanager
def _storage_lock():
    """Serialize CAS/catalog mutations across Archive processes."""
    _private_dir(ARTIFACTS_ROOT)
    lock_path = ARTIFACTS_ROOT / ".storage.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(lock_path, flags, 0o600)
    locked = False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ArtifactError("artifact storage lock is not a regular file")
        if os.name == "nt":
            import msvcrt

            if info.st_size < 1:
                os.write(fd, b"\0")
                os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _fsync_regular_file(path: Path) -> None:
    access = os.O_RDWR if os.name == "nt" else os.O_RDONLY
    flags = access | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ArtifactError(f"CAS path is not a regular file: {path}")
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_installed_blob(path: Path) -> None:
    """Make the CAS inode and every newly relevant directory durable first."""
    _fsync_regular_file(path)
    current = path.parent
    while True:
        _fsync_directory(current)
        if current == ARTIFACTS_ROOT:
            break
        try:
            current.relative_to(ARTIFACTS_ROOT)
        except ValueError as exc:
            raise ArtifactError("CAS path escaped the configured artifact root") from exc
        current = current.parent


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(SQLITE_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def _normalize_search_text(*values: Any) -> str:
    text = " ".join(str(value or "") for value in values)
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()[:32_768]


def _artifact_search_text(item: sqlite3.Row | dict[str, Any]) -> str:
    row = dict(item)
    return _normalize_search_text(
        row.get("artifact_id"),
        row.get("filename"),
        row.get("logical_path"),
        row.get("task_id"),
        row.get("mission_id"),
        row.get("source"),
        row.get("metadata_json"),
    )


def init_artifact_storage() -> None:
    """Create the catalog independently so the local publisher can run alone."""
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _private_dir(ARTIFACTS_ROOT)
    _private_dir(ARTIFACTS_ROOT / "blobs" / "sha256")
    _private_dir(ARTIFACTS_ROOT / "tmp")
    with _storage_lock(), closing(_connect()) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_blobs (
                sha256 TEXT PRIMARY KEY,
                size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
                relpath TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
                media_type TEXT NOT NULL,
                filename TEXT NOT NULL,
                source TEXT NOT NULL,
                audience_source TEXT NOT NULL DEFAULT '*',
                session_id TEXT NOT NULL,
                task_id TEXT,
                mission_id TEXT,
                logical_path TEXT,
                state TEXT NOT NULL DEFAULT 'ready',
                dedupe_key TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                search_text TEXT NOT NULL DEFAULT '',
                search_version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(sha256) REFERENCES artifact_blobs(sha256)
            )
            """
        )
        artifact_columns = {row[1] for row in db.execute("PRAGMA table_info(artifacts)")}
        if "search_text" not in artifact_columns:
            db.execute("ALTER TABLE artifacts ADD COLUMN search_text TEXT NOT NULL DEFAULT ''")
        if "search_version" not in artifact_columns:
            db.execute("ALTER TABLE artifacts ADD COLUMN search_version INTEGER NOT NULL DEFAULT 0")
        for row in db.execute(
            "SELECT * FROM artifacts WHERE search_version<2 OR search_text='' OR search_text IS NULL"
        ).fetchall():
            db.execute(
                "UPDATE artifacts SET search_text=?,search_version=2 WHERE artifact_id=?",
                (_artifact_search_text(row), row["artifact_id"]),
            )
        db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_artifacts_scoped_dedupe
            ON artifacts(session_id, source, dedupe_key)
            WHERE dedupe_key IS NOT NULL
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_artifacts_session_created
            ON artifacts(session_id, state, created_at DESC, artifact_id DESC)
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_sha256 ON artifacts(sha256)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_search_version ON artifacts(search_version)")
        db.commit()


def _safe_scope(value: Any, *, field: str, wildcard: bool = False) -> str:
    text = str(value or "").strip().lower()
    if wildcard and text == "*":
        return text
    if not SCOPE_RE.fullmatch(text):
        raise ArtifactError(f"{field} must be a simple 1-128 character identifier")
    return text


def _safe_optional_text(value: Any, *, field: str, limit: int = 160) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) > limit or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ArtifactError(f"{field} is invalid or longer than {limit} characters")
    return text


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = str(value or "").encode("utf-8")
    if len(encoded) <= max_bytes:
        return str(value or "")
    return encoded[: max(0, int(max_bytes))].decode("utf-8", errors="ignore")


def safe_artifact_filename(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).replace("\\", "/").split("/")[-1]
    text = "".join("_" if unicodedata.category(char).startswith("C") else char for char in text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        text = "artifact.bin"
    if len(text.encode("utf-8")) > 240:
        raw_suffix = Path(text).suffix
        suffix = _truncate_utf8(raw_suffix, 48) if raw_suffix else ""
        stem_source = text[: -len(raw_suffix)] if raw_suffix else text
        stem_budget = max(1, 240 - len(suffix.encode("utf-8")))
        stem = _truncate_utf8(stem_source, stem_budget).rstrip(" .")
        text = (stem or _truncate_utf8("artifact", stem_budget) or "a") + suffix
        text = _truncate_utf8(text, 240).strip(" .") or "artifact.bin"
    return text


def safe_media_type(value: Any) -> str:
    media_type = str(value or "application/octet-stream").split(";", 1)[0].strip().lower()
    return media_type if MEDIA_TYPE_RE.fullmatch(media_type) else "application/octet-stream"


def _safe_logical_path(value: Any) -> str | None:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return None
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ArtifactError("logical_path must be a relative normalized path")
    normalized = path.as_posix()
    if len(normalized) > 512 or any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise ArtifactError("logical_path is invalid or longer than 512 characters")
    return normalized


def _safe_dedupe_key(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) > 240 or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ArtifactError("dedupe_key is invalid or longer than 240 characters")
    return text


def _safe_metadata(value: Any) -> str:
    payload = value if isinstance(value, dict) else {}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 16_384:
        raise ArtifactError("artifact metadata exceeds 16384 bytes")
    return encoded


def _public_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    return {
        "artifact_id": item["artifact_id"],
        "filename": item["filename"],
        "media_type": item["media_type"],
        "size_bytes": int(item["size_bytes"]),
        "sha256": item["sha256"],
        "source": item["source"],
        "audience_source": item["audience_source"],
        "session_id": item["session_id"],
        "task_id": item.get("task_id"),
        "mission_id": item.get("mission_id"),
        "logical_path": item.get("logical_path"),
        "state": item["state"],
        "created_at": item["created_at"],
    }


def _artifact_row(artifact_id: Any) -> sqlite3.Row | None:
    token = str(artifact_id or "").strip().lower()
    if not ARTIFACT_ID_RE.fullmatch(token):
        return None
    with closing(_connect()) as db:
        return db.execute("SELECT * FROM artifacts WHERE artifact_id=?", (token,)).fetchone()


def artifact_metadata(
    artifact_id: Any,
    *,
    session_id: Any | None = None,
    audience_source: Any | None = None,
) -> dict[str, Any] | None:
    row = _artifact_row(artifact_id)
    if row is None or row["state"] != "ready":
        return None
    if session_id is not None and row["session_id"] != _safe_scope(session_id, field="session_id"):
        return None
    if audience_source is not None:
        source = _safe_scope(audience_source, field="audience_source", wildcard=True)
        if source != "*" and row["audience_source"] not in {"*", source}:
            return None
    return _public_row(row)


def list_artifacts(
    *,
    session_id: Any | None = None,
    producer_source: Any | None = None,
    audience_source: Any | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Internal catalog/list API. It never returns CAS or host paths."""
    clauses = ["state='ready'"]
    params: list[Any] = []
    if session_id is not None:
        clauses.append("session_id=?")
        params.append(_safe_scope(session_id, field="session_id"))
    if producer_source is not None:
        clauses.append("source=?")
        params.append(_safe_scope(producer_source, field="source"))
    if audience_source is not None:
        audience = _safe_scope(audience_source, field="audience_source", wildcard=True)
        if audience != "*":
            clauses.append("audience_source IN ('*', ?)")
            params.append(audience)
    try:
        safe_limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        safe_limit = 100
    params.append(safe_limit)
    with closing(_connect()) as db:
        rows = db.execute(
            f"SELECT * FROM artifacts WHERE {' AND '.join(clauses)} ORDER BY created_at DESC, artifact_id DESC LIMIT ?",
            params,
        ).fetchall()
    return [_public_row(row) for row in rows]


def recent_artifact_catalog(session_id: Any, *, audience_source: Any, limit: int = 10) -> list[dict[str, Any]]:
    """Compact, session- and transport-source-scoped catalog for Core."""
    try:
        items = list_artifacts(
            session_id=session_id,
            audience_source=audience_source,
            limit=max(1, min(int(limit), 12)),
        )
    except ArtifactError:
        return []
    return [
        {
            "artifact_id": item["artifact_id"],
            "filename": item["filename"],
            "media_type": item["media_type"],
            "size_bytes": item["size_bytes"],
            "created_at": item["created_at"],
            "source": item["source"],
        }
        for item in items
    ]


_ARTIFACT_QUERY_STOPWORDS = {
    "a", "an", "the", "file", "send", "show", "download", "me", "please",
    "дай", "мне", "файл", "файлы", "пришли", "пришлите", "скинь", "скиньте",
    "отправь", "отправьте", "покажи", "покажите", "скачать", "этот", "тот", "его",
}


# The owner writes tech terms in Cyrillic («скинь апк»), artifact filenames are
# Latin (Galaga.apk). Without these aliases the lexical match scores zero, the
# catalog falls back to pure recency, and the turn model confidently attaches
# whatever happened to be registered last (it once sent a zip instead of the apk
# registered 44 ms earlier).
_ARTIFACT_TERM_ALIASES = {
    "апк": "apk", "апка": "apk", "апкшка": "apk",
    "зип": "zip", "зипка": "zip", "зипку": "zip", "архив": "zip",
    "патч": "patch", "дифф": "diff", "пдф": "pdf",
    "исходники": "project", "проект": "project",
    "картинка": "png", "картинку": "png", "фотка": "jpg",
}


def _artifact_query_terms(query: Any) -> list[str]:
    normalized = _normalize_search_text(query)
    terms: list[str] = []

    def _add(term: str) -> None:
        if len(term) >= 2 and term not in _ARTIFACT_QUERY_STOPWORDS and term not in terms:
            terms.append(term)

    for raw in re.findall(r"[\w][\w.-]{1,63}", normalized, flags=re.UNICODE):
        term = raw.strip("._-")
        _add(term)
        alias = _ARTIFACT_TERM_ALIASES.get(term)
        if alias:
            _add(alias)
        if len(terms) >= 8:
            break
    return terms


def artifact_catalog_for_query(
    session_id: Any,
    *,
    audience_source: Any,
    query: Any,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return older lexical matches first, then fill remaining slots by recency."""
    session = _safe_scope(session_id, field="session_id")
    audience = _safe_scope(audience_source, field="audience_source", wildcard=True)
    try:
        safe_limit = max(1, min(int(limit), 12))
    except (TypeError, ValueError):
        safe_limit = 10
    terms = _artifact_query_terms(query)
    recent = list_artifacts(session_id=session, audience_source=audience, limit=safe_limit)
    if not terms:
        return recent_artifact_catalog(session, audience_source=audience, limit=safe_limit)

    predicates = " OR ".join("instr(search_text, ?) > 0" for _term in terms)
    visibility = "" if audience == "*" else "AND audience_source IN ('*', ?)"
    params: list[Any] = [session]
    if audience != "*":
        params.append(audience)
    params.extend(terms)
    with closing(_connect()) as db:
        rows = db.execute(
            f"""
            SELECT * FROM artifacts
            WHERE state='ready' AND session_id=? {visibility}
              AND ({predicates})
            ORDER BY created_at DESC, artifact_id DESC
            LIMIT 5000
            """,
            params,
        ).fetchall()

    def relevance(row: sqlite3.Row) -> tuple[int, str, str]:
        filename = _normalize_search_text(row["filename"])
        logical = _normalize_search_text(row["logical_path"])
        haystack = str(row["search_text"] or "")
        score = 0
        for term in terms:
            if term in filename:
                score += 80
            elif term in logical:
                score += 35
            elif term in haystack:
                score += 10
        if all(term in filename for term in terms):
            score += 200
        return score, str(row["created_at"]), str(row["artifact_id"])

    ranked = sorted(rows, key=relevance, reverse=True)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in [*ranked, *recent]:
        item = _public_row(row)
        if item["artifact_id"] in seen:
            continue
        seen.add(item["artifact_id"])
        selected.append(
            {
                "artifact_id": item["artifact_id"],
                "filename": item["filename"],
                "media_type": item["media_type"],
                "size_bytes": item["size_bytes"],
                "created_at": item["created_at"],
                "source": item["source"],
            }
        )
        if len(selected) >= safe_limit:
            break
    return selected


def _check_source_permissions(path: Path, info: os.stat_result, *, kind: str) -> None:
    if kind == "root" and not stat.S_ISDIR(info.st_mode):
        raise ArtifactError(f"trusted root is not a directory: {path}")
    if kind == "file" and not stat.S_ISREG(info.st_mode):
        raise ArtifactError(f"artifact source is not a regular file: {path}")
    if os.name != "nt":
        if info.st_mode & stat.S_IWOTH:
            raise ArtifactError(f"{kind} is writable by other users: {path}")
        if info.st_uid not in {0, os.geteuid()}:
            raise ArtifactError(f"{kind} is owned by another user: {path}")
        if kind == "file" and info.st_nlink != 1:
            raise ArtifactError(f"artifact source must have exactly one hard link: {path}")


def _select_trusted_root(source: Path, allowed_roots: Iterable[Path | str]) -> tuple[Path, Path]:
    roots = [Path(root).expanduser() for root in allowed_roots]
    if not roots:
        raise ArtifactError("trusted path import requires at least one allowed root")
    expanded_source = source.expanduser()
    if expanded_source.is_symlink():
        raise ArtifactError(f"artifact source may not be a symlink: {expanded_source}")
    absolute_source = Path(os.path.abspath(expanded_source))
    for supplied_root in roots:
        if supplied_root.is_symlink():
            raise ArtifactError(f"trusted root may not be a symlink: {supplied_root}")
        absolute_root = Path(os.path.abspath(supplied_root.expanduser()))
        root = supplied_root.resolve(strict=True)
        if absolute_root != root:
            raise ArtifactError(f"trusted root may not cross a symlink boundary: {supplied_root}")
        _check_source_permissions(root, root.stat(), kind="root")
        try:
            relative = absolute_source.relative_to(root)
        except ValueError:
            continue
        if not relative.parts:
            continue
        return root, relative
    raise ArtifactError("artifact source is outside all allowed roots")


@contextmanager
def _open_regular_beneath(source: Path, allowed_roots: Iterable[Path | str]):
    root, relative = _select_trusted_root(source, allowed_roots)
    file_fd: int | None = None
    directory_fds: list[int] = []
    try:
        try:
            if os.name != "nt" and os.open in os.supports_dir_fd:
                directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
                directory_fds.append(os.open(root, directory_flags))
                for part in relative.parts[:-1]:
                    directory_fds.append(os.open(part, directory_flags, dir_fd=directory_fds[-1]))
                file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                file_fd = os.open(relative.parts[-1], file_flags, dir_fd=directory_fds[-1])
            else:
                resolved = (root / relative).resolve(strict=True)
                if resolved.relative_to(root) != relative:
                    raise ArtifactError("artifact source changed or crossed a symlink boundary")
                file_fd = os.open(resolved, os.O_RDONLY | getattr(os, "O_BINARY", 0))
            before = os.fstat(file_fd)
        except OSError as exc:
            raise ArtifactError(f"could not safely open artifact source: {exc}") from exc
        _check_source_permissions(root / relative, before, kind="file")
        with os.fdopen(file_fd, "rb", closefd=True) as stream:
            file_fd = None
            yield stream, before, relative
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for fd in reversed(directory_fds):
            os.close(fd)


def _stage_reader(reader: BinaryIO, *, expected_size: int | None = None) -> tuple[Path, str, int]:
    if expected_size is not None and expected_size > ARTIFACT_MAX_BYTES:
        raise ArtifactTooLarge(
            f"artifact is {expected_size} bytes; configured single-file limit is {ARTIFACT_MAX_BYTES} bytes"
        )
    _private_dir(ARTIFACTS_ROOT / "tmp")
    staged = ARTIFACTS_ROOT / "tmp" / f"{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    total = 0
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(staged, flags, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=True) as target:
            fd = -1
            while True:
                chunk = reader.read(ARTIFACT_STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise ArtifactError("artifact stream returned non-binary data")
                chunk = bytes(chunk)
                total += len(chunk)
                if total > ARTIFACT_MAX_BYTES:
                    raise ArtifactTooLarge(
                        f"artifact exceeds configured single-file limit of {ARTIFACT_MAX_BYTES} bytes"
                    )
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if expected_size is not None and total != expected_size:
            raise ArtifactError(f"artifact source changed while reading: expected {expected_size} bytes, read {total}")
        return staged, digest.hexdigest(), total
    except Exception:
        if fd >= 0:
            os.close(fd)
        staged.unlink(missing_ok=True)
        raise


def _blob_relpath(sha256: str) -> str:
    return f"blobs/sha256/{sha256[:2]}/{sha256}"


def _install_blob(staged: Path, sha256: str, size_bytes: int) -> tuple[str, bool]:
    relpath = _blob_relpath(sha256)
    destination = ARTIFACTS_ROOT / PurePosixPath(relpath)
    _private_dir(destination.parent)
    created = False
    try:
        os.link(staged, destination)
        created = True
        if os.name != "nt":
            os.chmod(destination, 0o600)
    except FileExistsError:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        digest = hashlib.sha256()
        try:
            fd = os.open(destination, flags)
            with os.fdopen(fd, "rb", closefd=True) as existing:
                info = os.fstat(existing.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size != size_bytes:
                    raise ArtifactError(f"CAS collision or corrupt blob for sha256 {sha256}")
                while True:
                    chunk = existing.read(ARTIFACT_STREAM_CHUNK_BYTES)
                    if not chunk:
                        break
                    digest.update(chunk)
        except OSError as exc:
            raise ArtifactError(f"CAS collision or corrupt blob for sha256 {sha256}: {exc}") from exc
        if digest.hexdigest() != sha256:
            raise ArtifactError(f"CAS collision or corrupt blob for sha256 {sha256}")
    _fsync_installed_blob(destination)
    return relpath, created


def _orphan_blob_paths(db: sqlite3.Connection, *, limit: int) -> list[Path]:
    """Return canonical CAS files that have no durable blob catalog row."""
    base = ARTIFACTS_ROOT / "blobs" / "sha256"
    if not base.exists():
        return []
    catalogued = {
        str(row[0])
        for row in db.execute("SELECT relpath FROM artifact_blobs").fetchall()
    }
    orphaned: list[Path] = []
    for prefix in sorted(base.iterdir(), key=lambda item: item.name):
        try:
            prefix_info = prefix.lstat()
        except FileNotFoundError:
            continue
        if not re.fullmatch(r"[0-9a-f]{2}", prefix.name) or not stat.S_ISDIR(prefix_info.st_mode):
            continue
        for path in sorted(prefix.iterdir(), key=lambda item: item.name):
            if len(orphaned) >= limit:
                return orphaned
            name = path.name
            if not re.fullmatch(r"[0-9a-f]{64}", name) or not name.startswith(prefix.name):
                continue
            relpath = _blob_relpath(name)
            if relpath not in catalogued:
                orphaned.append(path)
    return orphaned


def _verify_blob_record(blob: sqlite3.Row | None, sha256: str, size_bytes: int) -> str:
    if blob is None or int(blob["size_bytes"]) != size_bytes:
        raise ArtifactError(f"catalog contains an invalid CAS record for {sha256}")
    relpath = str(blob["relpath"])
    if relpath != _blob_relpath(sha256):
        raise ArtifactError(f"catalog contains an invalid CAS path for {sha256}")
    destination = ARTIFACTS_ROOT / PurePosixPath(relpath)
    try:
        info = destination.lstat()
    except FileNotFoundError as exc:
        raise ArtifactError(f"catalog references a missing CAS blob {sha256}") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_size != size_bytes:
        raise ArtifactError(f"catalog references a corrupt CAS blob {sha256}")
    return relpath


def _publication_fields(
    *,
    filename: Any,
    media_type: Any,
    source: Any,
    audience_source: Any,
    session_id: Any,
    task_id: Any,
    mission_id: Any,
    logical_path: Any,
    dedupe_key: Any,
    metadata: Any,
) -> dict[str, Any]:
    fields = {
        "filename": safe_artifact_filename(filename),
        "media_type": safe_media_type(media_type),
        "source": _safe_scope(source, field="source"),
        "audience_source": _safe_scope(audience_source or "*", field="audience_source", wildcard=True),
        "session_id": _safe_scope(session_id, field="session_id"),
        "task_id": _safe_optional_text(task_id, field="task_id"),
        "mission_id": _safe_optional_text(mission_id, field="mission_id"),
        "logical_path": _safe_logical_path(logical_path),
        "dedupe_key": _safe_dedupe_key(dedupe_key),
        "metadata_json": _safe_metadata(metadata),
    }
    fields["search_text"] = _artifact_search_text(fields)
    return fields


def _existing_publication(db: sqlite3.Connection, fields: dict[str, Any]) -> sqlite3.Row | None:
    if fields["dedupe_key"] is None:
        return None
    return db.execute(
        "SELECT * FROM artifacts WHERE session_id=? AND source=? AND dedupe_key=?",
        (fields["session_id"], fields["source"], fields["dedupe_key"]),
    ).fetchone()


def _same_publication(row: sqlite3.Row, fields: dict[str, Any], sha256: str, size_bytes: int) -> bool:
    expected = {
        "sha256": sha256,
        "size_bytes": size_bytes,
        "filename": fields["filename"],
        "media_type": fields["media_type"],
        "audience_source": fields["audience_source"],
        "task_id": fields["task_id"],
        "mission_id": fields["mission_id"],
        "logical_path": fields["logical_path"],
        "metadata_json": fields["metadata_json"],
    }
    return all(row[key] == value for key, value in expected.items())


def _register_staged_under_lock(
    staged: Path,
    sha256: str,
    size_bytes: int,
    fields: dict[str, Any],
) -> dict[str, Any]:
    db = _connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        existing = _existing_publication(db, fields)
        if existing is not None:
            if not _same_publication(existing, fields, sha256, size_bytes):
                raise ArtifactError("dedupe_key was reused for different artifact content or metadata")
            if existing["state"] != "ready":
                raise ArtifactError("deduplicated artifact is not in ready state")
            _verify_blob_record(
                db.execute("SELECT * FROM artifact_blobs WHERE sha256=?", (sha256,)).fetchone(),
                sha256,
                size_bytes,
            )
            db.rollback()
            return _public_row(existing)

        blob = db.execute("SELECT * FROM artifact_blobs WHERE sha256=?", (sha256,)).fetchone()
        if blob is not None and int(blob["size_bytes"]) != size_bytes:
            raise ArtifactError(f"catalog contains an invalid size for CAS blob {sha256}")
        if blob is None:
            used = int(db.execute("SELECT COALESCE(SUM(size_bytes),0) FROM artifact_blobs").fetchone()[0])
            if used + size_bytes > ARTIFACT_TOTAL_QUOTA_BYTES:
                raise ArtifactQuotaExceeded(
                    f"artifact store quota would be exceeded: {used} + {size_bytes} > {ARTIFACT_TOTAL_QUOTA_BYTES} bytes"
                )
            relpath, _created_blob = _install_blob(staged, sha256, size_bytes)
            db.execute(
                "INSERT INTO artifact_blobs(sha256,size_bytes,relpath,created_at) VALUES (?,?,?,?)",
                (sha256, size_bytes, relpath, _utc_now()),
            )
        else:
            relpath = _verify_blob_record(blob, sha256, size_bytes)
            _fsync_installed_blob(ARTIFACTS_ROOT / PurePosixPath(relpath))

        artifact_id = f"art_{uuid.uuid4().hex}"
        publication_search_text = _normalize_search_text(artifact_id, fields["search_text"])
        created_at = _utc_now()
        db.execute(
            """
            INSERT INTO artifacts(
                artifact_id,sha256,size_bytes,media_type,filename,source,audience_source,
                session_id,task_id,mission_id,logical_path,state,dedupe_key,metadata_json,
                search_text,search_version,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                artifact_id,
                sha256,
                size_bytes,
                fields["media_type"],
                fields["filename"],
                fields["source"],
                fields["audience_source"],
                fields["session_id"],
                fields["task_id"],
                fields["mission_id"],
                fields["logical_path"],
                "ready",
                fields["dedupe_key"],
                fields["metadata_json"],
                publication_search_text,
                2,
                created_at,
            ),
        )
        row = db.execute("SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        if row is None:
            raise ArtifactError("artifact catalog insert was not observable before commit")
        public = _public_row(row)
        db.commit()
        return public
    except Exception:
        db.rollback()
        # A linked CAS blob is deliberately left as an orphan on transaction
        # failure. Deleting it here is unsafe because a commit error can be
        # ambiguous; cleanup can reclaim the orphan, while deleting a
        # blob after a successful commit would corrupt a durable catalog row.
        raise
    finally:
        db.close()


def _register_staged(staged: Path, sha256: str, size_bytes: int, fields: dict[str, Any]) -> dict[str, Any]:
    try:
        with _storage_lock():
            return _register_staged_under_lock(staged, sha256, size_bytes, fields)
    finally:
        staged.unlink(missing_ok=True)


def trusted_import_stream(
    reader: BinaryIO,
    *,
    filename: Any,
    media_type: Any = "application/octet-stream",
    source: Any,
    session_id: Any,
    audience_source: Any = "*",
    task_id: Any = None,
    mission_id: Any = None,
    logical_path: Any = None,
    dedupe_key: Any = None,
    metadata: Any = None,
    expected_size: int | None = None,
) -> dict[str, Any]:
    """Snapshot a trusted binary stream into CAS without buffering it in RAM."""
    init_artifact_storage()
    fields = _publication_fields(
        filename=filename,
        media_type=media_type,
        source=source,
        audience_source=audience_source,
        session_id=session_id,
        task_id=task_id,
        mission_id=mission_id,
        logical_path=logical_path,
        dedupe_key=dedupe_key,
        metadata=metadata,
    )
    staged, sha256, size_bytes = _stage_reader(reader, expected_size=expected_size)
    return _register_staged(staged, sha256, size_bytes, fields)


def trusted_import_bytes(data: bytes, **kwargs: Any) -> dict[str, Any]:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ArtifactError("trusted_import_bytes requires bytes")
    payload = bytes(data)
    return trusted_import_stream(io.BytesIO(payload), expected_size=len(payload), **kwargs)


def trusted_import_path(
    path: Path | str,
    *,
    allowed_roots: Iterable[Path | str],
    filename: Any = None,
    media_type: Any = None,
    source: Any,
    session_id: Any,
    audience_source: Any = "*",
    task_id: Any = None,
    mission_id: Any = None,
    logical_path: Any = None,
    dedupe_key: Any = None,
    metadata: Any = None,
) -> dict[str, Any]:
    """Race-resistant snapshot import beneath an explicit trusted root list."""
    init_artifact_storage()
    source_path = Path(path).expanduser()
    with _open_regular_beneath(source_path, allowed_roots) as (reader, before, relative):
        guessed_type = mimetypes.guess_type(str(filename or source_path.name))[0] or "application/octet-stream"
        fields = _publication_fields(
            filename=filename or source_path.name,
            media_type=media_type or guessed_type,
            source=source,
            audience_source=audience_source,
            session_id=session_id,
            task_id=task_id,
            mission_id=mission_id,
            logical_path=logical_path or relative.as_posix(),
            dedupe_key=dedupe_key,
            metadata=metadata,
        )
        staged, sha256, size_bytes = _stage_reader(reader, expected_size=int(before.st_size))
        after = os.fstat(reader.fileno())
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        if any(getattr(before, field, None) != getattr(after, field, None) for field in stable_fields):
            staged.unlink(missing_ok=True)
            raise ArtifactError("artifact source changed while its snapshot was being imported")
        return _register_staged(staged, sha256, size_bytes, fields)


def canonical_artifact_caption(metadata: sqlite3.Row | dict[str, Any]) -> str:
    """One factual caption; model-provided prose is never delivery authority."""
    item = dict(metadata)
    return f"Файл приложен: {safe_artifact_filename(item.get('filename'))}"


def attach_artifact_to_chat(
    artifact_id: Any,
    *,
    session_id: Any,
    audience_source: Any,
    effect_id: Any,
    client_request_id: Any = None,
) -> dict[str, Any] | None:
    """Atomically validate/open a CAS blob and persist its chat reference."""
    init_artifact_storage()
    token = str(artifact_id or "").strip().lower()
    if not ARTIFACT_ID_RE.fullmatch(token):
        return None
    session = _safe_scope(session_id, field="session_id")
    audience = _safe_scope(audience_source, field="audience_source", wildcard=True)
    safe_effect_id = _safe_dedupe_key(effect_id)
    if not safe_effect_id:
        raise ArtifactError("effect_id is required for artifact delivery")
    request_id = "".join(
        char for char in str(client_request_id or "").strip() if char.isalnum() or char in "-_.:"
    )[:160] or None
    dedupe_key = f"core-effect:{safe_effect_id}:artifact"
    if len(dedupe_key) > 240:
        raise ArtifactError("effect_id is too long for artifact delivery")

    fd: int | None = None
    with _storage_lock():
        db = _connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM artifacts WHERE artifact_id=?", (token,)).fetchone()
            if (
                row is None
                or row["state"] != "ready"
                or row["session_id"] != session
                or (audience != "*" and row["audience_source"] not in {"*", audience})
            ):
                db.rollback()
                return None
            blob = db.execute("SELECT * FROM artifact_blobs WHERE sha256=?", (row["sha256"],)).fetchone()
            relpath = _verify_blob_record(blob, row["sha256"], int(row["size_bytes"]))
            path = ARTIFACTS_ROOT / PurePosixPath(relpath)
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            fd = os.open(path, flags)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size != int(row["size_bytes"]):
                raise ArtifactError("artifact blob failed its atomic delivery integrity check")

            caption = canonical_artifact_caption(row)
            existing = db.execute(
                "SELECT * FROM mobile_chat_messages WHERE dedupe_key=?",
                (dedupe_key,),
            ).fetchone()
            if existing is not None:
                expected = {
                    "session_id": session,
                    "role": "assistant",
                    "content": caption,
                    "artifact_id": token,
                    "client_request_id": request_id,
                    "source": "shushunya-core",
                }
                if any(existing[key] != value for key, value in expected.items()):
                    raise ArtifactError("artifact delivery dedupe key refers to a different chat message")
                message_id = int(existing["id"])
            else:
                now = _utc_now()
                db.execute(
                    """
                    INSERT INTO mobile_chat_sessions(id,created_at,updated_at) VALUES (?,?,?)
                    ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at
                    """,
                    (session, now, now),
                )
                cursor = db.execute(
                    """
                    INSERT INTO mobile_chat_messages(
                        session_id,role,content,created_at,asset_id,artifact_id,
                        client_request_id,source,dedupe_key
                    ) VALUES (?,?,?,?,NULL,?,?,?,?)
                    """,
                    (session, "assistant", caption, now, token, request_id, "shushunya-core", dedupe_key),
                )
                message_id = int(cursor.lastrowid)
            public = _public_row(row)
            db.commit()
            return {"artifact": public, "message_id": message_id, "caption": caption}
        except Exception:
            db.rollback()
            raise
        finally:
            if fd is not None:
                os.close(fd)
            db.close()


@contextmanager
def open_artifact_content(artifact_id: Any, *, session_id: Any, audience_source: Any | None = None):
    """Open a registered CAS blob after catalog and session validation."""
    row = _artifact_row(artifact_id)
    if row is None or row["state"] != "ready" or row["session_id"] != _safe_scope(session_id, field="session_id"):
        raise FileNotFoundError("artifact not found")
    if audience_source is not None:
        audience = _safe_scope(audience_source, field="audience_source", wildcard=True)
        if audience != "*" and row["audience_source"] not in {"*", audience}:
            raise FileNotFoundError("artifact not found")
    expected_relpath = _blob_relpath(row["sha256"])
    with closing(_connect()) as db:
        blob = db.execute("SELECT * FROM artifact_blobs WHERE sha256=?", (row["sha256"],)).fetchone()
    _verify_blob_record(blob, row["sha256"], int(row["size_bytes"]))
    path = ARTIFACTS_ROOT / PurePosixPath(expected_relpath)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ArtifactError(f"artifact blob cannot be opened safely: {exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size != int(row["size_bytes"]):
            raise ArtifactError("artifact blob failed its catalog integrity check")
        with os.fdopen(fd, "rb", closefd=True) as stream:
            fd = -1
            yield _public_row(row), stream
    finally:
        if fd >= 0:
            os.close(fd)


def parse_single_byte_range(value: Any, size_bytes: int) -> tuple[int, int] | None:
    """Parse one RFC 7233 byte range. Multiple ranges are intentionally refused."""
    text = str(value or "").strip()
    if not text:
        return None
    if not text.lower().startswith("bytes=") or "," in text:
        raise ArtifactRangeError("only one bytes range is supported")
    spec = text[6:].strip()
    if "-" not in spec or size_bytes < 0:
        raise ArtifactRangeError("invalid byte range")
    first, last = (part.strip() for part in spec.split("-", 1))
    if not first:
        try:
            suffix = int(last)
        except ValueError as exc:
            raise ArtifactRangeError("invalid suffix byte range") from exc
        if suffix <= 0 or size_bytes == 0:
            raise ArtifactRangeError("unsatisfiable suffix byte range")
        start = max(0, size_bytes - suffix)
        return start, size_bytes - 1
    try:
        start = int(first)
        end = int(last) if last else size_bytes - 1
    except ValueError as exc:
        raise ArtifactRangeError("invalid byte range") from exc
    if start < 0 or end < start or start >= size_bytes:
        raise ArtifactRangeError("unsatisfiable byte range")
    return start, min(end, size_bytes - 1)


def artifact_store_stats() -> dict[str, Any]:
    init_artifact_storage()
    with closing(_connect()) as db:
        artifacts = int(db.execute("SELECT COUNT(*) FROM artifacts WHERE state='ready'").fetchone()[0])
        blobs, used = db.execute("SELECT COUNT(*),COALESCE(SUM(size_bytes),0) FROM artifact_blobs").fetchone()
    return {
        "artifacts": artifacts,
        "blobs": int(blobs),
        "used_bytes": int(used),
        "quota_bytes": ARTIFACT_TOTAL_QUOTA_BYTES,
        "max_artifact_bytes": ARTIFACT_MAX_BYTES,
    }


def _cleanup_unreferenced_artifacts_under_lock(
    *,
    older_than_days: int = 30,
    limit: int = 200,
    dry_run: bool = True,
) -> dict[str, Any]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(0, int(older_than_days)))).isoformat()
    safe_limit = max(1, min(int(limit), 2_000))
    db = _connect()
    blob_paths: list[Path] = []
    orphan_paths: list[Path] = []
    try:
        has_chat = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mobile_chat_messages'"
        ).fetchone() is not None
        if has_chat:
            has_chat = "artifact_id" in {
                row[1] for row in db.execute("PRAGMA table_info(mobile_chat_messages)").fetchall()
            }
        reference_clause = (
            "AND NOT EXISTS (SELECT 1 FROM mobile_chat_messages m WHERE m.artifact_id=a.artifact_id)"
            if has_chat
            else ""
        )
        if not dry_run:
            db.execute("BEGIN IMMEDIATE")
        orphan_paths = _orphan_blob_paths(db, limit=safe_limit)
        rows = db.execute(
            f"""
            SELECT a.* FROM artifacts a
            WHERE a.created_at < ? {reference_clause}
            ORDER BY a.created_at ASC LIMIT ?
            """,
            (cutoff, safe_limit),
        ).fetchall()
        candidates = [_public_row(row) for row in rows]
        if dry_run:
            return {
                "dry_run": True,
                "count": len(candidates),
                "artifacts": candidates,
                "orphan_blob_count": len(orphan_paths),
                "orphan_blobs": [path.name for path in orphan_paths],
            }
        # These files had no catalog row while the write lock was held, so no
        # concurrent importer can adopt them between this check and unlink.
        for path in orphan_paths:
            path.unlink(missing_ok=True)
        if not rows:
            db.commit()
            return {
                "dry_run": False,
                "count": 0,
                "artifacts": [],
                "orphan_blob_count": len(orphan_paths),
                "orphan_blobs": [path.name for path in orphan_paths],
            }
        for row in rows:
            db.execute("DELETE FROM artifacts WHERE artifact_id=?", (row["artifact_id"],))
        for sha256 in sorted({row["sha256"] for row in rows}):
            if db.execute("SELECT 1 FROM artifacts WHERE sha256=? LIMIT 1", (sha256,)).fetchone() is None:
                blob = db.execute("SELECT relpath FROM artifact_blobs WHERE sha256=?", (sha256,)).fetchone()
                if blob is not None and blob["relpath"] == _blob_relpath(sha256):
                    blob_paths.append(ARTIFACTS_ROOT / PurePosixPath(blob["relpath"]))
                db.execute("DELETE FROM artifact_blobs WHERE sha256=?", (sha256,))
        db.commit()
        for path in blob_paths:
            path.unlink(missing_ok=True)
        return {
            "dry_run": False,
            "count": len(candidates),
            "artifacts": candidates,
            "orphan_blob_count": len(orphan_paths),
            "orphan_blobs": [path.name for path in orphan_paths],
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def cleanup_unreferenced_artifacts(
    *,
    older_than_days: int = 30,
    limit: int = 200,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Internal cleanup seam; chat-linked artifacts are never selected."""
    init_artifact_storage()
    with _storage_lock():
        return _cleanup_unreferenced_artifacts_under_lock(
            older_than_days=older_than_days,
            limit=limit,
            dry_run=dry_run,
        )
