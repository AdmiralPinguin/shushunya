"""Fail-closed result serialization."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from typing import Any


class ResultWriteError(RuntimeError):
    pass


MAX_PUBLISHED_RESULT_BYTES = 128 * 1024 * 1024


def publication_safe_result(result: Any) -> dict[str, Any]:
    """Return strict bounded JSON, or a current fail-closed publication record."""

    try:
        if not isinstance(result, dict):
            raise TypeError("run result is not an object")
        compact = json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(compact) > MAX_PUBLISHED_RESULT_BYTES:
            raise ValueError("run result exceeds publication byte limit")
        restored = json.loads(compact)
        if not isinstance(restored, dict):
            raise TypeError("run result is not an object")
        return restored
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        return {
            "schema_version": 1,
            "run_valid": False,
            "run_passed": False,
            "publication_error": (
                "current evaluator result was rejected before publication: "
                + type(exc).__name__
            ),
        }


def render_result(result: dict[str, Any]) -> bytes:
    safe = publication_safe_result(result)
    return (
        json.dumps(
            safe,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def result_sha256(result: dict[str, Any]) -> str:
    return hashlib.sha256(render_result(result)).hexdigest()


def _is_link_like(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    is_junction = getattr(os.path, "isjunction", None)
    return bool(is_junction(path)) if is_junction is not None else False


def _reject_link_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    candidates = [absolute]
    candidates.extend(absolute.parents)
    for candidate in reversed(candidates):
        if _is_link_like(candidate):
            raise ResultWriteError(
                f"result path contains a symlink or junction: {candidate}"
            )


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_with_retry(source: Path, target: Path) -> None:
    """Tolerate only transient Windows sharing races between atomic writers."""

    attempts = 60 if os.name == "nt" else 1
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.01)


def write_result_atomic(result: dict[str, Any], target: str | Path) -> dict[str, Any]:
    """Publish the current outcome, including invalid runs, without stale passes.

    Each writer gets an exclusive temporary file in the destination directory.
    File bytes are fsynced before ``os.replace`` and the containing directory is
    fsynced on POSIX. Concurrent writers can replace one another only as whole
    documents; they cannot share or truncate a fixed temporary pathname.
    """

    path = Path(target)
    _reject_link_components(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_link_components(path.parent)
    if _is_link_like(path):
        raise ResultWriteError("refusing to replace a symlink or junction result")
    published = publication_safe_result(result)
    payload = render_result(published)
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary = Path(raw_temporary)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if _is_link_like(path):
            raise ResultWriteError("result target became a symlink or junction")
        _replace_with_retry(temporary, path)
        temporary = None
        _fsync_directory(path.parent)
    except ResultWriteError:
        raise
    except OSError as exc:
        raise ResultWriteError(f"cannot atomically publish result: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return published
