"""Bridge from Warmaster's research loop to the Skitarii warband v2.

When a Ceraxia code mission runs, instead of the dead six-worker paper pipeline it
is handed to the Skitarii HTTP service, which does the whole thing (spec -> agentic
fighter loop -> real acceptance) inside the sandbox VM and returns an honest verdict.
Skitarii already re-runs the checks itself, so no Warmaster LLM acceptance is needed.
"""
from __future__ import annotations

import base64
import configparser
import hashlib
import json
import os
import posixpath
import re
import secrets
import selectors
import signal
import stat
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from EyeOfTerror.common_protocol.ceraxia_directive import (
    CeraxiaDirectiveError,
    validate_directive_for_commander,
    validate_ceraxia_directive,
)
from EyeOfTerror.common_protocol import review_finding
from EyeOfTerror.common_protocol.validation import (
    ProtocolValidationError,
    validate_protocol_payload,
    validate_review_findings,
)

SKITARII_URL = os.environ.get(
    "SKITARII_URL", os.environ.get("SKITARII_WARBAND_URL", "http://127.0.0.1:7200"),
)
REPO_ROOT = Path(os.environ.get("SHUSHUNYA_REPO_ROOT", "/media/shushunya/SHUSHUNYA/shushunya"))
WARMMASTER_MISSIONS_ROOT = Path(os.environ.get(
    "WARMMASTER_MISSIONS_ROOT",
    str(Path(__file__).resolve().parents[1] / "missions"),
))
MAX_VERIFY_CHECKS = 10
MAX_VERIFY_COMMAND_BYTES = 4096
MAX_VERIFY_OUTPUT_BYTES = 131_072
MAX_VERIFY_TOTAL_SECONDS = 600
MAX_PATCH_INPUT_BYTES = 20_000_000
MAX_PATCH_FILES = 1_000
MAX_PATCH_FILE_BYTES = 20_000_000
MAX_PATCH_EXPANDED_BYTES = 100_000_000
MAX_CANDIDATE_FILES = 5_000
MAX_CANDIDATE_TOTAL_BYTES = 200_000_000
MAX_EXTERNAL_ASSET_BYTES = 500_000_000
MAX_EXTERNAL_ASSET_TOTAL_BYTES = 1_000_000_000
MAX_EXTERNAL_ASSETS = 100
MAX_SKITARII_RESPONSE_BYTES = 32_000_000
ACCEPTANCE_SOURCE_TYPE = "commander_order_user_request"
MAX_ACCEPTANCE_SOURCE_BYTES = 131_072
MAX_ACCEPTANCE_METADATA_BYTES = 1_048_576
SKITARII_POLL_INTERVAL_SEC = 0.5
# A full bounded worker queue (HTTP 429) is transient backpressure, not a task
# failure.  We retry the mission POST with exponential backoff inside the mission
# wall budget instead of collapsing a retryable 429 into a dead "blocked" verdict.
_SKITARII_BACKPRESSURE_BASE_DELAY_SEC = 2.0
_SKITARII_BACKPRESSURE_MAX_DELAY_SEC = 30.0
_SKITARII_BACKPRESSURE_MAX_WAIT_SEC = 300
_MISSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_OWNED_GIT_LOCKS: dict[str, tuple[int, int, int]] = {}
_OWNED_GIT_GUARDS: dict[str, tuple[int, tuple[str, ...]]] = {}
_OWNED_PUBLICATION_GUARDS: dict[str, tuple[int, int]] = {}
_OWNED_RUN_APPLY_GUARDS: dict[str, tuple[int, int]] = {}


@dataclass
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    output_limit: bool = False
    limit_reason: str = ""
_MODIFY_MARKERS = ("исправ", "почин", "fix ", "измен", "рефактор", "в файле", "добавь в",
                   "поправ", "доработай", "bug", "рефактори", "оптимизир")


def _safe_repo_file(rel: str) -> Path | None:
    """Resolve rel under REPO_ROOT, refusing anything that escapes the repo (../,
    symlinks, absolute paths). Returns the real path or None."""
    raw = str(rel).replace("\\", "/")
    pure = PurePosixPath(raw)
    if (
        not raw
        or pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
        or (pure.parts and pure.parts[0].endswith(":"))
    ):
        return None
    try:
        root = REPO_ROOT.resolve()
        current = root
        for part in pure.parts:
            current = current / part
            metadata = os.lstat(current)
            if stat.S_ISLNK(metadata.st_mode):
                return None
    except OSError:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        return None
    return current


_SLICE_STOP = {"почини", "исправь", "измени", "добавь", "файле", "проект", "код", "нужно",
               "который", "которая", "please", "code", "file", "project", "function", "должна", "чтобы"}


_CODE_EXT = (".py", ".php", ".js", ".ts", ".go", ".java", ".rb", ".rs", ".c", ".h", ".cpp")
_TEXT_EXT = _CODE_EXT + (
    ".css", ".html", ".htm", ".json", ".jsonl", ".toml", ".yaml", ".yml",
    ".xml", ".md", ".rst", ".txt", ".ini", ".cfg", ".conf", ".gradle",
    ".properties", ".sql", ".sh", ".ps1", ".bat", ".cs", ".kt", ".kts",
    ".swift", ".scala", ".ex", ".exs", ".erl", ".hrl", ".vue", ".svelte",
)
_TEXT_NAMES = {
    "Dockerfile", "Makefile", "Rakefile", "Gemfile", "Procfile", "go.mod", "go.sum",
    "Cargo.toml", "Cargo.lock", "package.json", "package-lock.json", "pnpm-lock.yaml",
    "requirements.txt", "pyproject.toml", "tox.ini", ".gitignore", ".gitattributes",
}


class _SkitariiQueueBackpressure(RuntimeError):
    """The Skitarii worker queue stayed full for the whole retry budget.

    This is a retryable capacity signal, not a task failure: the mission is still
    queued and can be dispatched once the warband frees a worker slot.
    """


class SnapshotError(RuntimeError):
    """The live repository could not be represented without truncation."""


class WorkspaceSnapshot(dict):
    """Text workspace plus the filesystem metadata needed to reproduce its baseline."""

    def __init__(
        self,
        files: dict[str, str] | None = None,
        *,
        deleted_paths: list[str] | None = None,
        modes: dict[str, str] | None = None,
        symlinks: dict[str, str] | None = None,
        blobs: dict[str, str] | None = None,
        external_assets: dict[str, dict[str, Any]] | None = None,
        fingerprint: str = "",
        metadata_fingerprint: str = "",
    ) -> None:
        super().__init__(files or {})
        self.deleted_paths = deleted_paths or []
        self.modes = modes or {}
        self.symlinks = symlinks or {}
        self.blobs = blobs or {}
        self.external_assets = external_assets or {}
        self.fingerprint = fingerprint
        self.metadata_fingerprint = metadata_fingerprint

    @property
    def inventory(self) -> list[str]:
        return sorted(set(self) | set(self.blobs) | set(self.symlinks) | set(self.external_assets))


def _workspace_fingerprint(
    snapshot: WorkspaceSnapshot,
    root: Path,
    *,
    head: str | None = None,
    index: bytes = b"",
) -> str:
    """Hash the exact Git-visible baseline, including dirty index/worktree state."""
    if head is None:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
            timeout=30, check=True,
        ).stdout.strip()
    if not index:
        index = subprocess.run(
            ["git", "ls-files", "-s", "-z"], cwd=root, capture_output=True,
            timeout=60, check=True,
        ).stdout
    digest = hashlib.sha256()
    digest.update(b"skitarii-workspace-v1\0" + head.encode("ascii", errors="strict") + b"\0" + index)
    for path in sorted(snapshot.inventory):
        encoded_path = path.encode("utf-8", errors="strict")
        mode = str(snapshot.modes.get(path) or "").encode("ascii", errors="strict")
        if path in snapshot:
            kind = b"text"
            content_hash = hashlib.sha256(str(snapshot[path]).encode("utf-8")).digest()
        elif path in snapshot.blobs:
            kind = b"blob"
            content_hash = hashlib.sha256(
                base64.b64decode(str(snapshot.blobs[path]), validate=True),
            ).digest()
        elif path in snapshot.symlinks:
            kind = b"link"
            content_hash = hashlib.sha256(str(snapshot.symlinks[path]).encode("utf-8")).digest()
        else:
            kind = b"external"
            encoded = json.dumps(
                snapshot.external_assets[path], sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            content_hash = hashlib.sha256(encoded).digest()
        digest.update(kind + b"\0" + encoded_path + b"\0" + mode + b"\0" + content_hash)
    for path in sorted(snapshot.deleted_paths):
        digest.update(b"deleted\0" + path.encode("utf-8", errors="strict") + b"\0")
    return digest.hexdigest()


def _snapshot_content_fingerprint(
    snapshot: WorkspaceSnapshot,
    *,
    paths: Iterable[str] | None = None,
) -> str:
    """Hash only the current Git-visible filesystem state, independent of Git metadata."""
    digest = hashlib.sha256(b"skitarii-content-v1\0")
    selected = (
        sorted({_safe_relative_path(path) for path in paths})
        if paths is not None else sorted(snapshot.inventory)
    )
    for path in selected:
        mode = str(
            snapshot.modes.get(path)
            or (snapshot.external_assets.get(path) or {}).get("mode")
            or ("" if path not in snapshot.inventory else "100644")
        )
        if path in snapshot.symlinks:
            kind = "link"
            content_sha = hashlib.sha256(str(snapshot.symlinks[path]).encode("utf-8")).hexdigest()
        elif path in snapshot:
            kind = "file"
            content_sha = hashlib.sha256(str(snapshot[path]).encode("utf-8")).hexdigest()
        elif path in snapshot.blobs:
            kind = "file"
            content_sha = hashlib.sha256(
                base64.b64decode(str(snapshot.blobs[path]), validate=True),
            ).hexdigest()
        elif path in snapshot.external_assets:
            kind = "file"
            content_sha = str(snapshot.external_assets[path].get("sha256") or "")
        else:
            kind = "missing"
            mode = ""
            content_sha = ""
        digest.update(
            f"{kind}\0{path}\0{mode}\0{content_sha}\0".encode("utf-8", errors="strict"),
        )
    return digest.hexdigest()


def _validate_patch_resource_bounds(diff: str) -> dict[str, Any]:
    """Reject patch forms whose host-side expansion cannot be bounded in advance."""
    try:
        encoded_size = len(diff.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise SnapshotError("patch is not valid UTF-8 text") from exc
    if encoded_size > MAX_PATCH_INPUT_BYTES:
        raise SnapshotError(f"patch exceeds {MAX_PATCH_INPUT_BYTES} encoded bytes")
    if "\x00" in diff:
        raise SnapshotError("patch contains a NUL byte")
    file_count = 0
    literal_total = 0
    in_git_section = False
    old_header_seen = False
    new_header_seen = False
    binary_section = False
    hunk_old_remaining = 0
    hunk_new_remaining = 0
    for line in diff.splitlines():
        if hunk_old_remaining or hunk_new_remaining:
            if line.startswith("\\ No newline at end of file"):
                continue
            if not line or line[0] not in " +-":
                raise SnapshotError("patch has a malformed unified-diff hunk")
            if line[0] in " -":
                hunk_old_remaining -= 1
            if line[0] in " +":
                hunk_new_remaining -= 1
            if hunk_old_remaining < 0 or hunk_new_remaining < 0:
                raise SnapshotError("patch has a malformed unified-diff hunk")
            continue
        if line.startswith("diff --git "):
            file_count += 1
            if file_count > MAX_PATCH_FILES:
                raise SnapshotError(f"patch changes more than {MAX_PATCH_FILES} files")
            if not line.removeprefix("diff --git ").strip():
                raise SnapshotError("patch has a malformed Git file header")
            in_git_section = True
            old_header_seen = False
            new_header_seen = False
            binary_section = False
            continue
        if line.startswith("@@"):
            match = re.match(
                r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@(?: .*)?$", line,
            )
            if not match or not old_header_seen or not new_header_seen or binary_section:
                raise SnapshotError("patch has a malformed unified-diff hunk")
            try:
                hunk_old_remaining = int(match.group(1) or "1")
                hunk_new_remaining = int(match.group(2) or "1")
            except (ValueError, OverflowError) as exc:
                raise SnapshotError("patch has an oversized unified-diff hunk count") from exc
            continue
        if line == "GIT binary patch":
            if not in_git_section or old_header_seen or new_header_seen:
                raise SnapshotError("patch mixes unsupported patch section formats")
            binary_section = True
            continue
        if line.startswith("--- "):
            if not in_git_section or old_header_seen or binary_section:
                raise SnapshotError("patch contains a non-git or duplicate file section")
            old_header_seen = True
            continue
        if line.startswith("+++ "):
            if not old_header_seen or new_header_seen or binary_section:
                raise SnapshotError("patch contains a non-git or duplicate file section")
            new_header_seen = True
            continue
        if line.startswith("literal "):
            raw_size = line.removeprefix("literal ")
            if (
                not binary_section
                or not raw_size.isascii()
                or not raw_size.isdigit()
                or len(raw_size) > 12
            ):
                raise SnapshotError("git binary patch has an invalid literal size")
            try:
                declared = int(raw_size)
            except (ValueError, OverflowError) as exc:
                raise SnapshotError("git binary patch has an invalid literal size") from exc
            if declared > MAX_PATCH_FILE_BYTES:
                raise SnapshotError(
                    f"binary patch literal exceeds {MAX_PATCH_FILE_BYTES} bytes",
                )
            literal_total += declared
            if literal_total > MAX_PATCH_EXPANDED_BYTES:
                raise SnapshotError(
                    f"binary patch literals exceed {MAX_PATCH_EXPANDED_BYTES} bytes",
                )
        elif line.startswith("delta "):
            # A delta's final size cannot be established without inflating it against
            # host content. Fighters must emit a bounded literal instead.
            raise SnapshotError("git binary delta patches are not supported")
        elif line.startswith("copy from ") or line.startswith("copy to "):
            # A tiny copy patch can duplicate a very large clean tracked asset.
            raise SnapshotError("git copy patches are not supported")
        mode_match = re.fullmatch(
            r"(?:old mode|new mode|new file mode|deleted file mode) ([0-7]+)[\t ]*", line,
        )
        index_mode_match = re.fullmatch(
            r"index [0-9a-f]+\.\.[0-9a-f]+ ([0-7]+)[\t ]*", line,
        )
        raw_mode = (
            mode_match.group(1) if mode_match
            else (index_mode_match.group(1) if index_mode_match else "")
        )
        if raw_mode:
            try:
                parsed_mode = int(raw_mode, 8)
            except (ValueError, OverflowError) as exc:
                raise SnapshotError("patch has an invalid Git mode") from exc
            if parsed_mode == 0o120000:
                raise SnapshotError("patches may not create, delete, or modify symlinks")
            if parsed_mode == 0o160000:
                raise SnapshotError("Git submodule patches are not supported")
    if diff.strip() and file_count == 0:
        raise SnapshotError("patch has no git file headers")
    if hunk_old_remaining or hunk_new_remaining:
        raise SnapshotError("patch has a truncated unified-diff hunk")
    return {
        "input_bytes": encoded_size,
        "file_count": file_count,
        "declared_literal_bytes": literal_total,
    }


def _stable_stat_signature(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_size,
        metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _stable_regular_file_digest(path: Path, maximum: int) -> tuple[os.stat_result, str]:
    """Hash one pathname through O_NOFOLLOW and prove it still names that inode."""
    try:
        before = path.lstat()
        flags = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SnapshotError(f"patched repository file cannot be opened safely: {path}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise SnapshotError(f"unsupported patched repository node: {path}")
        if _stable_stat_signature(before) != _stable_stat_signature(opened):
            raise SnapshotError(f"patched repository file changed while opening: {path}")
        if opened.st_size > maximum:
            raise SnapshotError(f"patched file exceeds {maximum} bytes: {path}")
        digest = hashlib.sha256()
        read_bytes = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - read_bytes))
            if not chunk:
                break
            digest.update(chunk)
            read_bytes += len(chunk)
            if read_bytes > maximum:
                raise SnapshotError(f"patched file exceeds {maximum} bytes: {path}")
        after_fd = os.fstat(descriptor)
        after_path = path.lstat()
        signature = _stable_stat_signature(opened)
        if (
            read_bytes != opened.st_size
            or _stable_stat_signature(after_fd) != signature
            or _stable_stat_signature(after_path) != signature
        ):
            raise SnapshotError(f"patched repository file changed while hashing: {path}")
        return opened, digest.hexdigest()
    except OSError as exc:
        raise SnapshotError(f"patched repository file changed while hashing: {path}") from exc
    finally:
        os.close(descriptor)


def _stable_symlink_target(path: Path) -> tuple[os.stat_result, str]:
    try:
        before = path.lstat()
        if not stat.S_ISLNK(before.st_mode):
            raise SnapshotError(f"patched repository link changed before reading: {path}")
        target = os.readlink(path)
        after = path.lstat()
    except OSError as exc:
        raise SnapshotError(f"patched repository link changed while reading: {path}") from exc
    if _stable_stat_signature(before) != _stable_stat_signature(after):
        raise SnapshotError(f"patched repository link changed while reading: {path}")
    return before, target


def _stable_small_regular_file_bytes(path: Path, maximum: int, label: str) -> bytes:
    try:
        before = path.lstat()
        flags = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SnapshotError(f"{label} cannot be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _stable_stat_signature(before) != _stable_stat_signature(opened)
            or opened.st_size > maximum
        ):
            raise SnapshotError(f"{label} is not a stable bounded regular file")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(4096, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise SnapshotError(f"{label} is oversized")
        after_fd = os.fstat(descriptor)
        after_path = path.lstat()
        signature = _stable_stat_signature(opened)
        if (
            total != opened.st_size
            or _stable_stat_signature(after_fd) != signature
            or _stable_stat_signature(after_path) != signature
        ):
            raise SnapshotError(f"{label} changed while reading")
        return b"".join(chunks)
    except OSError as exc:
        raise SnapshotError(f"{label} changed while reading") from exc
    finally:
        os.close(descriptor)


def _scoped_path_generation(root: Path, paths: Iterable[str]) -> str:
    """Cheap no-content generation guard for a previously hashed target set."""
    root = root.resolve()
    digest = hashlib.sha256(b"skitarii-path-generation-v1\0")
    for rel in sorted({_safe_relative_path(path) for path in paths}):
        path = _materialize_path(root, rel)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            digest.update(f"missing\0{rel}\0".encode("utf-8", errors="strict"))
            continue
        except OSError as exc:
            raise SnapshotError(f"patch target generation is unreadable: {rel}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            metadata, target = _stable_symlink_target(path)
            extra = target.encode("utf-8", errors="strict")
            kind = b"link"
        elif stat.S_ISREG(metadata.st_mode):
            extra = b""
            kind = b"file"
        else:
            extra = b""
            kind = b"other"
        signature = _stable_stat_signature(metadata)
        digest.update(kind + b"\0" + rel.encode("utf-8", errors="strict") + b"\0")
        digest.update(json.dumps(signature, separators=(",", ":")).encode("ascii"))
        digest.update(b"\0" + extra + b"\0")
    return digest.hexdigest()


def _git_visible_content_fingerprint(
    root: Path,
    *,
    paths: Iterable[str] | None = None,
    allowed_large: dict[str, dict[str, Any]] | None = None,
    max_files: int = MAX_CANDIDATE_FILES,
    max_total_bytes: int = MAX_CANDIDATE_TOTAL_BYTES,
    max_file_bytes: int = MAX_PATCH_FILE_BYTES,
    reject_ignored_nodes: bool = False,
    project_missing_approved: bool = False,
    return_generation: bool = False,
) -> str | tuple[str, str]:
    """Hash and validate a post-patch tree without relying on snapshot inline limits."""
    root = root.resolve()
    approved = allowed_large or {}
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root, capture_output=True, timeout=60, check=True,
    ).stdout
    staged = subprocess.run(
        ["git", "ls-files", "-s", "-z"], cwd=root,
        capture_output=True, timeout=60, check=True,
    ).stdout
    modes: dict[str, str] = {}
    for entry in staged.split(b"\0"):
        if not entry or b"\t" not in entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        fields = metadata.split()
        if len(fields) >= 3 and fields[2] == b"0":
            rel = raw_path.decode("utf-8", errors="strict")
            mode = fields[0].decode("ascii")
            if mode == "160000":
                raise SnapshotError(f"Git submodules are not supported: {rel}")
            modes[rel] = mode
    digest = hashlib.sha256(b"skitarii-content-v1\0")
    raw_paths = sorted(part for part in listed.split(b"\0") if part)
    visible_paths = {
        _safe_relative_path(raw_path.decode("utf-8", errors="strict"))
        for raw_path in raw_paths
    }
    scoped = paths is not None
    fingerprint_paths = (
        {_safe_relative_path(path) for path in paths or ()}
        if scoped else visible_paths | set(approved)
    )
    if len(fingerprint_paths) > max_files:
        raise SnapshotError(f"patched repository exceeds {max_files} files")
    if reject_ignored_nodes:
        physical_nodes: set[str] = set()
        for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            if current_path == root and ".git" in dirnames:
                dirnames.remove(".git")
            elif ".git" in dirnames:
                raise SnapshotError("patched repository contains nested .git metadata")
            for name in list(dirnames):
                candidate = current_path / name
                if candidate.is_symlink():
                    rel = _safe_relative_path(candidate.relative_to(root).as_posix())
                    target = _safe_symlink_target(rel, os.readlink(candidate))
                    _validate_resolved_symlink(root, rel, target)
                    physical_nodes.add(rel)
                    dirnames.remove(name)
            for name in filenames:
                candidate = current_path / name
                rel = _safe_relative_path(candidate.relative_to(root).as_posix())
                node_mode = candidate.lstat().st_mode
                if stat.S_ISLNK(node_mode):
                    target = _safe_symlink_target(rel, os.readlink(candidate))
                    _validate_resolved_symlink(root, rel, target)
                elif not stat.S_ISREG(node_mode):
                    raise SnapshotError(f"unsupported patched repository node: {rel}")
                physical_nodes.add(rel)
        ignored_created = sorted(physical_nodes - visible_paths)
        if ignored_created:
            raise SnapshotError(
                f"patch created Git-ignored content: {ignored_created[0]}",
            )
    generation_before = _scoped_path_generation(root, fingerprint_paths)
    total = 0
    for rel in sorted(fingerprint_paths):
        path = _materialize_path(root, rel)
        current_parent = root
        for part in PurePosixPath(rel).parts[:-1]:
            current_parent /= part
            if current_parent.is_symlink():
                raise SnapshotError(f"patched repository parent is a symlink: {rel}")
            if os.path.lexists(current_parent) and not current_parent.is_dir():
                raise SnapshotError(f"patched repository parent is not a directory: {rel}")
        try:
            resolved_parent = path.parent.resolve()
        except OSError as exc:
            raise SnapshotError(f"patched repository parent cannot be resolved: {rel}") from exc
        if resolved_parent != root and root not in resolved_parent.parents:
            raise SnapshotError(f"patched repository path escapes through a symlink: {rel}")
        metadata = approved.get(rel) if isinstance(approved.get(rel), dict) else None
        try:
            node = path.lstat()
        except FileNotFoundError:
            if metadata is not None:
                if not project_missing_approved:
                    raise SnapshotError(f"approved large baseline asset disappeared: {rel}")
                mode = str(metadata.get("mode") or "100644")
                content_sha = str(metadata.get("sha256") or "")
                if (
                    int(metadata.get("size") or -1) < 0
                    or not re.fullmatch(r"[0-9a-f]{64}", content_sha)
                ):
                    raise SnapshotError(f"external baseline asset manifest is invalid: {rel}")
                digest.update(
                    f"file\0{rel}\0{mode}\0{content_sha}\0".encode("utf-8", errors="strict"),
                )
                continue
            if scoped:
                digest.update(f"missing\0{rel}\0\0\0".encode("utf-8", errors="strict"))
                continue
            if rel in modes:
                # A tracked path remains in the index after a worktree deletion.
                # Its absence is a legitimate post-patch state; the authoritative
                # changed-path fingerprint below records it explicitly as missing.
                continue
            raise SnapshotError(f"patched repository path disappeared: {rel}")
        except OSError as exc:
            raise SnapshotError(f"patched repository path cannot be inspected: {rel}") from exc
        if stat.S_ISLNK(node.st_mode):
            kind = "link"
            mode = "120000"
            _link_metadata, raw_target = _stable_symlink_target(path)
            target = _safe_symlink_target(rel, raw_target)
            _validate_resolved_symlink(root, rel, target)
            target_bytes = target.encode("utf-8", errors="strict")
            total += len(target_bytes)
            content_sha = hashlib.sha256(target_bytes).hexdigest()
        else:
            if not stat.S_ISREG(node.st_mode):
                raise SnapshotError(f"unsupported patched repository node: {rel}")
            maximum = int(metadata.get("size") or -1) if metadata else max_file_bytes
            if maximum < 0:
                raise SnapshotError(f"external baseline asset size is invalid: {rel}")
            stable, content_sha = _stable_regular_file_digest(path, maximum)
            kind = "file"
            # Git apply changes the worktree, not the index. Hash the actual executable
            # bit so mode-only patches and dirty index/worktree differences are visible.
            mode = "100755" if stable.st_mode & stat.S_IXUSR else "100644"
            size = stable.st_size
            if metadata:
                expected_mode = str(metadata.get("mode") or mode)
                if (
                    size != int(metadata.get("size") or -1)
                    or content_sha != str(metadata.get("sha256") or "")
                    or mode != expected_mode
                ):
                    raise SnapshotError(f"approved large baseline asset changed: {rel}")
            else:
                total += size
        if total > max_total_bytes:
            raise SnapshotError(f"patched repository exceeds {max_total_bytes} bytes")
        digest.update(
            f"{kind}\0{rel}\0{mode}\0{content_sha}\0".encode("utf-8", errors="strict"),
        )
    generation_after = _scoped_path_generation(root, fingerprint_paths)
    if generation_before != generation_after:
        raise SnapshotError("patch targets changed during content fingerprinting")
    fingerprint = digest.hexdigest()
    return (fingerprint, generation_after) if return_generation else fingerprint


def _git_index_flag_records(root: Path) -> bytes:
    """Return stable path/flag records without hashing the index stat cache.

    ``git ls-files --stage`` deliberately hides semantic index bits such as
    CE_INTENT_TO_ADD.  ``--debug`` exposes them, but also emits mutable ctime,
    mtime and inode data.  Parse only the path and flags so an ordinary index
    refresh cannot manufacture a conflict while a real semantic flag change
    cannot pass unnoticed.
    """
    raw = _bounded_command_stdout(
        ["git", "ls-files", "--debug", "-z"], root,
        timeout=60, max_bytes=40_000_000,
    )
    record = re.compile(
        rb"  ctime: [^\n]*\n"
        rb"  mtime: [^\n]*\n"
        rb"  dev: [^\n]*\n"
        rb"  uid: [^\n]*\n"
        rb"  size: [^\n]*\tflags: ([0-9a-fA-F]+)\n",
    )
    cursor = 0
    semantic = bytearray()
    while cursor < len(raw):
        nul = raw.find(b"\0", cursor)
        if nul < 0:
            raise SnapshotError("git returned a malformed index debug record")
        path = raw[cursor:nul]
        match = record.match(raw, nul + 1)
        if match is None:
            raise SnapshotError("git returned an unsupported index debug record")
        # Git's debug word also carries volatile in-memory bits such as
        # CE_FSMONITOR_VALID.  Bind only durable user-visible semantics:
        # assume-unchanged, intent-to-add and skip-worktree.  Entry mode/OID/
        # stage are already covered by ``ls-files --stage`` below.
        durable_flags = int(match.group(1), 16) & 0x60008000
        flags = f"{durable_flags:08x}".encode("ascii")
        semantic.extend(len(path).to_bytes(8, "big"))
        semantic.extend(path)
        semantic.extend(b"\0" + flags + b"\0")
        cursor = match.end()
    return bytes(semantic)


def _resolved_git_path(root: Path, name: str) -> Path:
    raw = _bounded_command_stdout(
        ["git", "rev-parse", "--git-path", name], root,
        timeout=30, max_bytes=4096,
    ).decode("utf-8", errors="strict").strip()
    path = Path(raw)
    return path if path.is_absolute() else root / path


def _git_mutation_lock_paths(root: Path) -> tuple[Path, ...]:
    names = [
        "index.lock", "HEAD.lock", "packed-refs.lock",
        "MERGE_HEAD.lock", "CHERRY_PICK_HEAD.lock", "REVERT_HEAD.lock",
        "REBASE_HEAD.lock", "AUTO_MERGE.lock",
    ]
    raw_common = _bounded_command_stdout(
        ["git", "rev-parse", "--git-common-dir"], root,
        timeout=30, max_bytes=4096,
    ).decode("utf-8", errors="strict").strip()
    common = Path(raw_common)
    if not common.is_absolute():
        common = root / common
    common = common.absolute()
    reftable_dir = common / "reftable"
    storage = subprocess.run(
        ["git", "config", "--get", "extensions.refStorage"], cwd=root,
        capture_output=True, timeout=30,
    )
    if storage.returncode not in {0, 1} or len(storage.stdout) > 1024:
        raise SnapshotError("git ref storage format could not be resolved")
    configured_storage = (
        storage.stdout.decode("ascii", errors="strict").strip().lower()
        if storage.returncode == 0 else ""
    )
    if configured_storage not in {"", "files", "reftable"}:
        raise SnapshotError("git ref storage format is unsupported")
    reported = subprocess.run(
        ["git", "rev-parse", "--show-ref-format"], cwd=root,
        capture_output=True, timeout=30,
    )
    reported_storage = (
        reported.stdout.decode("ascii", errors="strict").strip().lower()
        if reported.returncode == 0 and len(reported.stdout) <= 1024 else ""
    )
    uses_reftable = (
        configured_storage == "reftable"
        or reported_storage == "reftable"
        or (reftable_dir / "tables.list").is_file()
    )
    explicit_paths: list[Path] = (
        [reftable_dir / "tables.list.lock"] if uses_reftable else []
    )
    symbolic = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"], cwd=root,
        capture_output=True, timeout=30,
    )
    if symbolic.returncode == 0:
        ref = symbolic.stdout.decode("utf-8", errors="strict").strip()
        if not ref or "\0" in ref or ref.startswith("/") or ".." in PurePosixPath(ref).parts:
            raise SnapshotError("git symbolic HEAD lock path is invalid")
        if not uses_reftable:
            names.append(f"{ref}.lock")
    elif symbolic.returncode != 1:
        raise SnapshotError("git could not resolve symbolic HEAD for mutation guard")
    unique = {
        *(_resolved_git_path(root, name).absolute() for name in names),
        *(path.absolute() for path in explicit_paths),
    }
    return tuple(sorted(unique, key=lambda value: str(value)))


def _assert_git_mutation_guard(root: Path) -> None:
    guard_key = str(root.resolve())
    guarded = _OWNED_GIT_GUARDS.get(guard_key)
    if not guarded:
        return
    owner, paths = guarded
    if owner != threading.get_ident():
        raise SnapshotError("repository mutation guard is owned by another transaction")
    for raw_path in paths:
        expected = _OWNED_GIT_LOCKS.get(raw_path)
        path = Path(raw_path)
        try:
            current = path.lstat()
        except OSError as exc:
            raise SnapshotError("Git mutation guard was removed during apply") from exc
        if (
            expected is None
            or expected[0] != owner
            or (current.st_dev, current.st_ino) != expected[1:]
        ):
            raise SnapshotError("Git mutation guard changed during apply")


def _git_operation_state_active(root: Path) -> str:
    for name in (
        "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "REBASE_HEAD",
        "AUTO_MERGE", "BISECT_START", "rebase-apply", "rebase-merge", "sequencer",
    ):
        path = _resolved_git_path(root, name)
        if os.path.lexists(path):
            return name
    return ""


def _git_pseudoref(root: Path, name: str) -> bytes:
    path = _resolved_git_path(root, name)
    if not os.path.lexists(path):
        return b"ABSENT"
    raw = _stable_small_regular_file_bytes(path, 4096, f"git operation state {name}")
    object_ids = raw.splitlines()
    if not object_ids or any(
        not re.fullmatch(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})", oid)
        for oid in object_ids
    ):
        raise SnapshotError(f"git operation state {name} is malformed")
    return b"PRESENT\0" + b"\n".join(object_ids)


def _git_metadata_fingerprint(root: Path) -> str:
    """Hash branch, operation state and the semantic index, never worktree bytes."""
    root = root.resolve()
    symbolic = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"], cwd=root,
        capture_output=True, timeout=30,
    )
    if symbolic.returncode not in {0, 1} or len(symbolic.stdout) > 4096:
        raise SnapshotError("git could not resolve symbolic HEAD")
    symbolic_head = symbolic.stdout.strip() if symbolic.returncode == 0 else b"DETACHED"
    resolved_head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=root,
        capture_output=True, timeout=30,
    )
    if resolved_head.returncode == 0 and len(resolved_head.stdout) <= 1024:
        head = resolved_head.stdout.strip()
    else:
        repository = subprocess.run(
            ["git", "rev-parse", "--git-dir"], cwd=root,
            capture_output=True, timeout=30,
        )
        if repository.returncode != 0:
            raise SnapshotError("git could not resolve repository HEAD")
        if symbolic.returncode != 0:
            raise SnapshotError("git detached HEAD is malformed")
        loose_ref_raw = _bounded_command_stdout(
            ["git", "rev-parse", "--git-path", symbolic_head.decode("utf-8", errors="strict")],
            root, timeout=30, max_bytes=4096,
        ).decode("utf-8", errors="strict").strip()
        loose_ref = Path(loose_ref_raw)
        if not loose_ref.is_absolute():
            loose_ref = root / loose_ref
        if os.path.lexists(loose_ref):
            raise SnapshotError("git symbolic HEAD target is malformed")
        target = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", symbolic_head.decode("utf-8", errors="strict")],
            cwd=root, capture_output=True, timeout=30,
        )
        if target.returncode not in {0, 1}:
            raise SnapshotError("git could not validate symbolic HEAD target")
        if target.returncode == 0:
            raise SnapshotError("git symbolic HEAD target could not be resolved")
        head = b"UNBORN"
    index = _bounded_command_stdout(
        ["git", "ls-files", "--stage", "-v", "-z"], root,
        timeout=60, max_bytes=20_000_000,
    )
    index_flags = _git_index_flag_records(root)
    unmerged = _bounded_command_stdout(
        ["git", "ls-files", "--unmerged", "-z"], root,
        timeout=30, max_bytes=20_000_000,
    )
    resolve_undo = _bounded_command_stdout(
        ["git", "ls-files", "--resolve-undo", "-z"], root,
        timeout=30, max_bytes=20_000_000,
    )
    lock_path = _resolved_git_path(root, "index.lock").absolute()
    if os.path.lexists(lock_path):
        owned = _OWNED_GIT_LOCKS.get(str(lock_path))
        current = lock_path.lstat()
        if (
            owned is None
            or owned[0] != threading.get_ident()
            or (current.st_dev, current.st_ino) != owned[1:]
        ):
            raise SnapshotError("git index is locked by another operation")
    elif str(lock_path) in _OWNED_GIT_LOCKS:
        raise SnapshotError("owned Git index mutation guard disappeared")
    digest = hashlib.sha256(b"skitarii-git-metadata-v3\0")
    for label, value in (
        (b"head", head), (b"symbolic", symbolic_head),
        (b"index", index), (b"index_flags", index_flags),
        (b"unmerged", unmerged), (b"resolve_undo", resolve_undo),
        (b"merge_head", _git_pseudoref(root, "MERGE_HEAD")),
        (b"cherry_pick_head", _git_pseudoref(root, "CHERRY_PICK_HEAD")),
        (b"revert_head", _git_pseudoref(root, "REVERT_HEAD")),
        (b"rebase_head", _git_pseudoref(root, "REBASE_HEAD")),
        (b"auto_merge", _git_pseudoref(root, "AUTO_MERGE")),
    ):
        digest.update(label + b"\0" + value + b"\0")
    return digest.hexdigest()


def _stable_scoped_live_state(
    root: Path,
    paths: Iterable[str],
    *,
    allowed_large: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Read the composite Git/target state twice and reject a torn observation.

    Neither Git metadata nor worktree paths share one external lock.  The
    M/T/M/T sequence closes the long cross-component read windows while leaving
    only the unavoidable final-instruction race before ``git apply`` itself.
    """
    stable_paths = tuple(paths)
    first_metadata = _git_metadata_fingerprint(root)
    first_targets, first_generation = _git_visible_content_fingerprint(
        root, paths=stable_paths, allowed_large=allowed_large,
        return_generation=True,
    )
    second_metadata = _git_metadata_fingerprint(root)
    second_targets, second_generation = _git_visible_content_fingerprint(
        root, paths=stable_paths, allowed_large=allowed_large,
        return_generation=True,
    )
    third_metadata = _git_metadata_fingerprint(root)
    final_generation = _scoped_path_generation(root, stable_paths)
    _assert_git_mutation_guard(root)
    if (
        first_metadata != second_metadata
        or second_metadata != third_metadata
        or first_targets != second_targets
        or first_generation != second_generation
        or second_generation != final_generation
    ):
        raise SnapshotError("live patch targets or Git metadata changed during CAS read")
    return third_metadata, second_targets


def _safe_relative_path(raw: object) -> str:
    value = str(raw)
    if "\\" in value:
        raise SnapshotError(f"repository path contains an unsupported backslash: {value!r}")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise SnapshotError("repository path is not valid UTF-8") from exc
    path = PurePosixPath(value)
    if (
        not value
        or "\x00" in value
        or path.is_absolute()
        or not path.parts
        or any(part in ("..", ".git") for part in path.parts)
        or path.parts[0].endswith(":")
    ):
        raise SnapshotError(f"unsafe repository-relative path: {value!r}")
    normalized = path.as_posix()
    if normalized in ("", "."):
        raise SnapshotError("repository-relative path must name a file")
    return normalized


def _is_runner_control_path(raw: object) -> bool:
    rel = PurePosixPath(_safe_relative_path(raw))
    lowered = [part.lower() for part in rel.parts]
    dangerous_modules = {
        "sitecustomize", "usercustomize", "conftest", "pytest", "unittest", "runpy",
    }
    for part in lowered:
        stem = part
        while "." in stem:
            stem = stem.rsplit(".", 1)[0]
        if part in dangerous_modules or stem in dangerous_modules:
            return True
    return lowered[-1] in {"pytest.ini", ".pytest.ini", "tox.ini"}


def _runner_control_config_text(raw: object, text: str) -> bool:
    name = PurePosixPath(_safe_relative_path(raw)).name.lower()
    if name not in {"pyproject.toml", "setup.cfg"}:
        return False
    try:
        if name == "pyproject.toml":
            parsed = tomllib.loads(text)
            tool = parsed.get("tool") if isinstance(parsed, dict) else {}
            return isinstance(tool, dict) and any(str(key).lower() == "pytest" for key in tool)
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(text)
        return any(section.lower() in {"pytest", "tool:pytest"} for section in parser.sections())
    except (OSError, UnicodeError, ValueError, configparser.Error, tomllib.TOMLDecodeError):
        return True


def _has_runner_control_config(raw: object, path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        return True
    return _runner_control_config_text(raw, text)


def _materialize_path(root: Path, raw: object) -> Path:
    """Join lexically under root without following an existing symlink."""
    rel = PurePosixPath(_safe_relative_path(raw))
    return root.joinpath(*rel.parts)


def _safe_symlink_target(link_path: str, raw_target: object) -> str:
    target = str(raw_target)
    if "\\" in target:
        raise SnapshotError(f"unsafe symlink target: {link_path!r} -> {target!r}")
    if not target or "\x00" in target or posixpath.isabs(target):
        raise SnapshotError(f"unsafe symlink target: {link_path!r} -> {target!r}")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(link_path), target))
    parts = PurePosixPath(resolved).parts
    if resolved == ".." or resolved.startswith("../") or ".git" in parts:
        raise SnapshotError(f"symlink target escapes repository: {link_path!r} -> {target!r}")
    return target


def _validate_resolved_symlink(root: Path, link_path: str, target: str) -> None:
    """Reject an internal-looking link whose existing target chain escapes root."""
    root = root.resolve()
    link = _materialize_path(root, link_path)
    try:
        resolved = (link.parent / target).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise SnapshotError(f"repository symlink target cannot be resolved: {link_path}") from exc
    if resolved != root and root not in resolved.parents:
        raise SnapshotError(f"repository symlink target chain escapes: {link_path!r} -> {target!r}")
    try:
        relative_parts = resolved.relative_to(root).parts
    except ValueError as exc:
        raise SnapshotError(f"repository symlink target chain escapes: {link_path!r}") from exc
    if ".git" in relative_parts:
        raise SnapshotError(f"repository symlink target chain enters .git: {link_path!r}")


def _validate_snapshot_symlink_target(
    root: Path,
    link_path: str,
    target: str,
    visible_paths: set[str],
) -> None:
    """Require every direct symlink target to be represented by the snapshot.

    Merely staying inside the repository is insufficient: a tracked link can point
    at an ignored secret or at an untracked intermediate link.  The disposable
    verifier would omit that target while the live checkout follows it.  Requiring
    the normalized direct target to be Git-visible makes chains inductive: every
    tracked link in the chain is checked in turn, and links to ignored files or
    untracked directories fail closed.
    """
    _validate_resolved_symlink(root, link_path, target)
    normalized = posixpath.normpath(posixpath.join(posixpath.dirname(link_path), target))
    normalized = _safe_relative_path(normalized)
    if normalized not in visible_paths:
        raise SnapshotError(
            f"repository symlink target is not Git-visible: {link_path!r} -> {target!r}",
        )


def _add_file(files: dict[str, str], root: str, p: Path, limit: int) -> bool:
    """Add p to files (rel→content) if safe/small/new. Returns True if room remains."""
    try:
        rel = str(p.resolve().relative_to(root))
    except (OSError, ValueError):
        return len(files) < limit
    _JUNK = ("site-packages/", "dist-packages/", "/venv/", "/.venv/", "node_modules/",
             "/__pycache__/", "DemonsForge/DemonsForge/", "/lib/python")
    if rel in files or _safe_repo_file(rel) is None or any(j in "/" + rel for j in _JUNK):
        return len(files) < limit
    try:
        if p.stat().st_size < 100_000:
            files[rel] = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return len(files) < limit


def _repo_slice(goal: str, max_files: int = 40) -> dict[str, str]:
    """PATCH task named no files. Build a MODULE-level slice: grep the goal's keywords
    to find target files, then pull their directory neighbours and nearby tests so the
    fighter sees real context (siblings, tests, config) — not one isolated file, but
    also not the whole monorepo. Bounded and scoped to the repo."""
    import re
    import subprocess
    words = [w for w in re.findall(r"[A-Za-zА-Яа-я_][\w-]{3,}", goal) if w.lower() not in _SLICE_STOP]
    if not words:
        return {}
    root = str(REPO_ROOT.resolve())
    files: dict[str, str] = {}
    target_dirs: set[Path] = set()
    # 1) target files by keyword
    for kw in words[:6]:
        try:
            out = subprocess.run(
                ["grep", "-rliI"] + [f"--include=*{e}" for e in _CODE_EXT] +
                ["--exclude-dir=.git", "--exclude-dir=node_modules", "--exclude-dir=runtime",
                 "--exclude-dir=models", "--exclude-dir=vm-sandbox", "--exclude-dir=__pycache__",
                 "--exclude-dir=venv", "--exclude-dir=.venv", "--exclude-dir=site-packages",
                 "--exclude-dir=lib", "--exclude-dir=dist-packages", "--exclude-dir=DemonsForge",
                 kw, root],
                capture_output=True, text=True, timeout=25).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for line in out.splitlines():
            if not line.startswith(root):
                continue
            p = Path(line)
            target_dirs.add(p.parent)
            if not _add_file(files, root, p, max_files // 2):
                break
    # 2) directory neighbours + tests around the targets (module context)
    for d in list(target_dirs)[:8]:
        try:
            for p in sorted(d.iterdir()):
                if p.is_file() and p.suffix in _CODE_EXT:
                    if not _add_file(files, root, p, max_files):
                        return files
        except OSError:
            pass
    return files


def _bounded_command_stdout(
    args: list[str], cwd: Path, *, timeout: int, max_bytes: int,
) -> bytes:
    """Capture command stdout without allowing repository metadata to exhaust RAM."""
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert proc.stdout is not None and proc.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
    selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
    stdout = bytearray()
    stderr = bytearray()
    deadline = time.monotonic() + timeout
    failure = ""
    try:
        while selector.get_map() or proc.poll() is None:
            if time.monotonic() >= deadline:
                failure = "timed out"
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for key, _ in selector.select(timeout=0.1):
                chunk = os.read(key.fileobj.fileno(), 65_536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = stdout if key.data == "stdout" else stderr
                limit = max_bytes if key.data == "stdout" else min(max_bytes, 65_536)
                remaining = limit - len(target)
                if remaining > 0:
                    target.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    failure = f"exceeded {limit} output bytes"
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            if failure and proc.poll() is not None:
                break
        returncode = proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)
        failure = failure or "did not terminate"
        returncode = -signal.SIGKILL
    finally:
        selector.close()
        proc.stdout.close()
        proc.stderr.close()
    if failure:
        raise SnapshotError(f"{' '.join(args[:3])} {failure}")
    if returncode != 0:
        detail = stderr.decode("utf-8", errors="replace")[:300]
        raise SnapshotError(f"{' '.join(args[:3])} failed ({returncode}): {detail}")
    return bytes(stdout)


def _full_repo_snapshot(
    max_files: int = 5000,
    max_total_bytes: int = 50_000_000,
    max_file_bytes: int = 2_000_000,
    max_external_assets: int = MAX_EXTERNAL_ASSETS,
    max_external_file_bytes: int = MAX_EXTERNAL_ASSET_BYTES,
    max_external_total_bytes: int = MAX_EXTERNAL_ASSET_TOTAL_BYTES,
) -> WorkspaceSnapshot:
    """Return a bounded, explicit representation of every Git-visible path.

    Git is the security boundary: ignored secrets, runtimes, models, generated media and
    caches never enter the snapshot. Inline UTF-8 files remain text and inline binary
    files are base64 encoded. Any Git-visible asset above the inline cap is represented
    by an immutable hash/size manifest entry. This preserves an exact, conflict-checked
    baseline without letting an unrelated large dirty or untracked image stop the whole
    warband. Submodules still block because their contents are a separate repository.
    """
    root = REPO_ROOT.resolve()
    inventory_output_limit = max(1_000_000, max_files * 4096)
    try:
        listed = _bounded_command_stdout(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            root, timeout=60, max_bytes=inventory_output_limit,
        )
        try:
            head = _bounded_command_stdout(
                ["git", "rev-parse", "HEAD"], root, timeout=30, max_bytes=1024,
            ).decode("ascii", errors="strict").strip()
        except SnapshotError:
            head = "UNBORN"
        staged = _bounded_command_stdout(
            ["git", "ls-files", "-s", "-z"], cwd=root,
            timeout=60, max_bytes=inventory_output_limit,
        )
        unmerged = _bounded_command_stdout(
            ["git", "ls-files", "-u", "-z"], cwd=root,
            timeout=30, max_bytes=inventory_output_limit,
        )
    except (OSError, subprocess.SubprocessError, SnapshotError) as exc:
        raise SnapshotError(f"git could not enumerate the repository: {exc}") from exc
    if unmerged:
        raise SnapshotError("repository has unresolved index conflicts")
    git_modes: dict[str, str] = {}
    for entry in staged.split(b"\0"):
        if not entry or b"\t" not in entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        fields = metadata.split()
        if len(fields) >= 3 and fields[2] == b"0":
            path = raw_path.decode("utf-8", errors="surrogateescape").replace("\\", "/")
            git_modes[path] = fields[0].decode("ascii", errors="strict")
    files: dict[str, str] = {}
    blobs: dict[str, str] = {}
    external_assets: dict[str, dict[str, Any]] = {}
    modes: dict[str, str] = {}
    symlinks: dict[str, str] = {}
    inventory_entries = [raw_rel for raw_rel in listed.split(b"\0") if raw_rel]
    if len(inventory_entries) > max_files:
        raise SnapshotError(f"repository snapshot exceeds {max_files} files")
    inventory_paths = [
        _safe_relative_path(raw_rel.decode("utf-8", errors="surrogateescape"))
        for raw_rel in inventory_entries
    ]
    visible_paths = set(inventory_paths)
    inventory_path_bytes = sum(len(raw_rel) for raw_rel in inventory_entries)
    if inventory_path_bytes > max_total_bytes:
        raise SnapshotError(f"repository inventory exceeds {max_total_bytes} path bytes")
    total = inventory_path_bytes
    external_total = 0
    for rel in inventory_paths:
        indexed_mode = git_modes.get(rel, "")
        if indexed_mode == "160000":
            raise SnapshotError(f"Git submodules are not supported by exact snapshots: {rel}")
        raw_path = _materialize_path(root, rel)
        is_link = raw_path.is_symlink()
        if is_link:
            try:
                target = _safe_symlink_target(rel, os.readlink(raw_path))
                _validate_snapshot_symlink_target(root, rel, target, visible_paths)
            except OSError as exc:
                raise SnapshotError(f"repository symlink could not be read: {rel}") from exc
            encoded_target = target.encode("utf-8", errors="surrogateescape")
            if len(files) + len(blobs) + len(symlinks) + len(external_assets) >= max_files:
                raise SnapshotError(f"repository snapshot exceeds {max_files} files")
            if total + len(encoded_target) > max_total_bytes:
                raise SnapshotError(f"repository snapshot exceeds {max_total_bytes} bytes")
            symlinks[rel] = target
            modes[rel] = "120000"
            total += len(encoded_target)
            continue
        p = _safe_repo_file(rel)
        if p is None:
            if os.path.lexists(raw_path):
                raise SnapshotError(
                    f"repository path is non-regular or traverses a symlinked parent: {rel}",
                )
            if not indexed_mode:
                raise SnapshotError(f"untracked repository path disappeared during snapshot: {rel}")
            # Deleted tracked files are represented separately below.
            continue
        try:
            size = p.stat().st_size
            if size > max_file_bytes:
                if len(external_assets) >= max_external_assets:
                    raise SnapshotError(f"repository exceeds {max_external_assets} external assets")
                stable, content_sha = _stable_regular_file_digest(
                    p,
                    max_external_file_bytes,
                )
                size = stable.st_size
                if external_total + size > max_external_total_bytes:
                    raise SnapshotError(
                        f"tracked external assets exceed {max_external_total_bytes} bytes",
                    )
                worktree_clean = bool(indexed_mode) and subprocess.run(
                    ["git", "diff", "--quiet", "--", rel], cwd=root, timeout=30,
                ).returncode == 0
                index_clean = bool(indexed_mode) and subprocess.run(
                    ["git", "diff", "--cached", "--quiet", "--", rel], cwd=root, timeout=30,
                ).returncode == 0
                if len(files) + len(blobs) + len(symlinks) + len(external_assets) >= max_files:
                    raise SnapshotError(f"repository snapshot exceeds {max_files} files")
                external_assets[rel] = {
                    "size": size,
                    "sha256": content_sha,
                    "mode": "100755" if stable.st_mode & stat.S_IXUSR else "100644",
                    "materialized": False,
                    "source_state": (
                        "untracked" if not indexed_mode
                        else "tracked_clean" if worktree_clean and index_clean
                        else "tracked_dirty"
                    ),
                    "reason": "Git-visible asset exceeds inline snapshot limit and is immutable during this mission",
                }
                external_total += size
                continue
            data = p.read_bytes()
        except OSError as exc:
            raise SnapshotError(f"repository file could not be read: {rel}") from exc
        if len(files) + len(blobs) + len(symlinks) + len(external_assets) >= max_files:
            raise SnapshotError(f"repository snapshot exceeds {max_files} files")
        if total + len(data) > max_total_bytes:
            raise SnapshotError(f"repository snapshot exceeds {max_total_bytes} bytes")
        try:
            decoded = data.decode("utf-8")
        except UnicodeDecodeError:
            decoded = ""
        if decoded and "\x00" not in decoded:
            files[rel] = decoded
        elif not data:
            files[rel] = ""
        else:
            blobs[rel] = base64.b64encode(data).decode("ascii")
        modes[rel] = "100755" if p.stat().st_mode & stat.S_IXUSR else "100644"
        total += len(data)
    for link_path, target in symlinks.items():
        normalized_target = _safe_relative_path(
            posixpath.normpath(posixpath.join(posixpath.dirname(link_path), target)),
        )
        if normalized_target in external_assets:
            raise SnapshotError(
                "repository symlink targets an omitted external asset: "
                f"{link_path!r} -> {target!r}",
            )
    try:
        deleted_raw = _bounded_command_stdout(
            ["git", "ls-files", "-z", "--deleted"], root,
            timeout=30, max_bytes=inventory_output_limit,
        )
        staged_deleted_raw = _bounded_command_stdout(
            ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=D"],
            root, timeout=30, max_bytes=inventory_output_limit,
        )
    except (OSError, subprocess.SubprocessError, SnapshotError) as exc:
        raise SnapshotError(f"git could not enumerate deleted paths: {exc}") from exc
    deleted = sorted({
        _safe_relative_path(rel.decode("utf-8", errors="surrogateescape"))
        for rel in (deleted_raw + staged_deleted_raw).split(b"\0") if rel
    })
    snapshot = WorkspaceSnapshot(
        files, deleted_paths=deleted, modes=modes, symlinks=symlinks, blobs=blobs,
        external_assets=external_assets,
    )
    snapshot.fingerprint = _workspace_fingerprint(snapshot, root, head=head, index=staged)
    snapshot.metadata_fingerprint = _git_metadata_fingerprint(root)
    return snapshot


def _collect_workspace(goal: str) -> tuple[dict[str, str], bool]:
    """Load existing repo files this PATCH task references, so it edits real source
    instead of writing a blank greenfield file. Named files first, then a keyword
    slice (see _repo_slice). Traversal outside the repo is refused. Returns
    ({rel_path: content}, is_patch)."""
    import re
    files: dict[str, str] = {}
    for m in re.findall(r"[\w./-]+\.[A-Za-z0-9]{1,6}", goal):
        rel = m.lstrip("./")
        p = _safe_repo_file(rel)
        if p is None:
            continue
        try:
            if p.stat().st_size < 200_000:
                files[rel] = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        if len(files) >= 30:
            break
    is_patch = bool(files) or any(k in goal.lower() for k in _MODIFY_MARKERS)
    # A patch agent needs the complete repository, not a keyword-selected reality.
    # Snapshot failures propagate and block the mission: a partial fallback would make
    # Explorer reason about a repository that does not actually exist.
    if is_patch:
        files = _full_repo_snapshot()
    return files, is_patch


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_ceraxia_directive(run_dir: Path, task_id: str) -> dict[str, Any]:
    """Load the authoritative Ceraxia-to-Skitarii handoff and fail closed."""
    path = run_dir / "ceraxia_directive.json"
    if path.is_symlink() or not path.is_file():
        raise CeraxiaDirectiveError("ceraxia_directive.json is missing or not a regular file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CeraxiaDirectiveError(f"ceraxia_directive.json is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise CeraxiaDirectiveError("ceraxia_directive.json must contain an object")
    governor_plan_path = run_dir / "governor_plan.json"
    if governor_plan_path.is_symlink() or not governor_plan_path.is_file():
        raise CeraxiaDirectiveError("governor_plan.json is missing or not a regular file")
    try:
        governor_plan = json.loads(governor_plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CeraxiaDirectiveError(f"governor_plan.json is unreadable: {exc}") from exc
    if not isinstance(governor_plan, dict):
        raise CeraxiaDirectiveError("governor_plan.json must contain an object")
    expected_mission_id = governor_plan.get("mission_id")
    if (
        type(expected_mission_id) is not str
        or not _MISSION_ID_RE.fullmatch(expected_mission_id)
    ):
        raise CeraxiaDirectiveError("governor_plan.json does not bind a mission_id")
    return validate_ceraxia_directive(
        payload,
        expected_task_id=task_id,
        expected_mission_id=expected_mission_id,
        require_delegation=True,
    )


def _bound_mission_directory(raw: str, mission_id: str) -> Path:
    """Resolve one canonical mission directory under the configured authority root."""
    if not isinstance(raw, str) or not isinstance(mission_id, str):
        raise CeraxiaDirectiveError("linked mission identity must be textual")
    if not _MISSION_ID_RE.fullmatch(mission_id):
        raise CeraxiaDirectiveError("linked mission_id is invalid")
    candidate = Path(raw)
    try:
        root = WARMMASTER_MISSIONS_ROOT.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CeraxiaDirectiveError(f"linked mission directory is unavailable: {exc}") from exc
    if (
        not candidate.is_absolute()
        or candidate.is_symlink()
        or resolved != candidate
        or not resolved.is_dir()
        or resolved.parent != root
        or resolved.name != mission_id
    ):
        raise CeraxiaDirectiveError(
            "linked mission directory is outside the configured mission authority root"
        )
    return resolved


def _load_commander_order_acceptance_source(
    run_dir: Path,
    leadership_directive: dict[str, Any],
) -> dict[str, Any]:
    """Load the linked original request and bind it to this exact delegation.

    The fighter goal is Ceraxia's operational handoff.  Acceptance may also use
    the user's original literal requirements, but only when they come from the
    canonical commander_order for the same linked mission and the directive has
    preserved that order's command boundaries.
    """

    def bounded_text(value: object, maximum: int) -> bool:
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            return False
        try:
            return len(value.encode("utf-8")) <= maximum
        except UnicodeEncodeError:
            return False

    def strict_object(path: Path, label: str) -> dict[str, Any]:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(path, flags)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > MAX_ACCEPTANCE_METADATA_BYTES
            ):
                raise CeraxiaDirectiveError(
                    f"{label} is not a bounded regular file"
                )
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                raw = handle.read(MAX_ACCEPTANCE_METADATA_BYTES + 1)
            if len(raw) > MAX_ACCEPTANCE_METADATA_BYTES:
                raise CeraxiaDirectiveError(f"{label} exceeds the metadata bound")
            value = json.loads(raw.decode("utf-8"))
        except CeraxiaDirectiveError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CeraxiaDirectiveError(f"{label} is unreadable: {exc}") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if not isinstance(value, dict):
            raise CeraxiaDirectiveError(f"{label} must contain an object")
        return value

    mission_ref = strict_object(run_dir / "mission_ref.json", "mission_ref.json")
    expected_mission_id = str(leadership_directive.get("mission_id") or "")
    if mission_ref.get("mission_id") != expected_mission_id:
        raise CeraxiaDirectiveError(
            "mission_ref.json mission_id does not match the Ceraxia directive"
        )
    mission_dir_text = mission_ref.get("mission_dir")
    if not bounded_text(mission_dir_text, 4_096):
        raise CeraxiaDirectiveError("mission_ref.json does not bind a mission directory")
    mission_dir = _bound_mission_directory(mission_dir_text, expected_mission_id)
    mission = strict_object(mission_dir / "mission.json", "linked mission.json")
    if mission.get("mission_id") != expected_mission_id:
        raise CeraxiaDirectiveError(
            "linked mission.json mission_id does not match the Ceraxia directive"
        )
    if mission.get("task_id") != leadership_directive.get("task_id"):
        raise CeraxiaDirectiveError(
            "linked mission.json task_id does not match the Ceraxia directive"
        )
    command = strict_object(
        mission_dir / "commander_order.json", "linked commander_order.json",
    )
    try:
        validate_protocol_payload(command, expected_type="commander_order")
    except ValueError as exc:
        raise CeraxiaDirectiveError(f"linked commander_order.json is invalid: {exc}") from exc
    if type(command.get("protocol_version")) is not int:
        raise CeraxiaDirectiveError(
            "linked commander_order.protocol_version must be an integer"
        )
    if command.get("mission_id") != expected_mission_id:
        raise CeraxiaDirectiveError(
            "commander_order mission_id does not match the Ceraxia directive"
        )
    if command.get("from") != "Warmaster" or command.get("to") != "Ceraxia":
        raise CeraxiaDirectiveError(
            "commander_order authority must be Warmaster -> Ceraxia"
        )
    validate_directive_for_commander(
        leadership_directive,
        command,
        expected_task_id=str(leadership_directive.get("task_id") or ""),
        expected_mission_id=expected_mission_id,
        require_delegation=True,
    )
    user_request = command.get("user_request")
    if not bounded_text(user_request, MAX_ACCEPTANCE_SOURCE_BYTES):
        raise CeraxiaDirectiveError(
            "commander_order.user_request is empty, contains NUL, or exceeds the acceptance bound"
        )
    return {
        "type": ACCEPTANCE_SOURCE_TYPE,
        "protocol_version": command["protocol_version"],
        "mission_id": expected_mission_id,
        "delegating_task_id": str(leadership_directive.get("task_id") or ""),
        "from": "Warmaster",
        "to": "Ceraxia",
        "user_request": user_request,
    }


_TARGET_REPO_MARKER = re.compile(r"(?mi)^\s*CERAXIA_TARGET_REPO:\s*(.+?)\s*$")


def _normalize_goal_repo_scope(goal: str) -> str:
    """Accept only the configured host repo and never expose its host path to the VM."""
    for match in _TARGET_REPO_MARKER.finditer(goal):
        requested = Path(match.group(1).strip()).expanduser().resolve()
        if requested != REPO_ROOT.resolve():
            raise SnapshotError(f"mission requested an unsupported repository root: {requested}")
    normalized = _TARGET_REPO_MARKER.sub("", goal).strip()
    if "CERAXIA_REPOSITORY_SCOPE:" in normalized:
        normalized = re.sub(
            r"(?mi)^\s*CERAXIA_REPOSITORY_SCOPE:.*$",
            "CERAXIA_REPOSITORY_SCOPE: repository preloaded in current workdir",
            normalized,
        )
    return normalized


def _run_sandboxed_check(command: str, worktree: Path, timeout: int = 180) -> SandboxResult:
    """Run one check in a fresh, resource-bounded cgroup and bubblewrap workspace."""
    if len(command.encode("utf-8")) > MAX_VERIFY_COMMAND_BYTES:
        return SandboxResult(125, "", "verification command exceeds the size limit", limit_reason="command_size")
    bwrap = shutil.which("bwrap")
    systemd_run = shutil.which("systemd-run")
    systemctl = shutil.which("systemctl")
    if not bwrap or not systemd_run or not systemctl:
        raise RuntimeError("bubblewrap and systemd-run are required for host-side patch verification")
    sandbox = [
        bwrap, "--die-with-parent", "--new-session", "--unshare-all", "--clearenv",
    ]
    for path in ("/usr", "/bin", "/lib", "/lib64", "/etc"):
        if Path(path).exists():
            sandbox += ["--ro-bind", path, path]
    sandbox += [
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/work",
        "--ro-bind", str(worktree.resolve()), "/baseline",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "HOME", "/tmp",
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "LC_ALL", "C.UTF-8",
        "--setenv", "PYTHONNOUSERSITE", "1",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--chdir", "/work", "--", "/bin/bash", "-c",
        "cp -a /baseline/. /work/ && cd /work && " + command,
    ]
    unit = f"skitarii-check-{uuid.uuid4().hex}.service"
    args = [
        systemd_run, "--user", "--quiet", "--wait", "--collect", "--pipe",
        "--service-type=exec", f"--unit={unit}",
        "-p", "MemoryMax=2G", "-p", "MemorySwapMax=0", "-p", "TasksMax=128",
        "-p", "CPUQuota=200%", "-p", f"LimitCPU={max(1, timeout)}",
        "-p", "LimitFSIZE=67108864", "-p", "LimitNOFILE=256", "-p", "LimitCORE=0",
        "-p", f"RuntimeMaxSec={max(1, timeout + 5)}s", "-p", "KillMode=control-group",
        "-p", "TimeoutStopSec=3s", "--", *sandbox,
    ]
    outer_env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
        if key in os.environ
    }
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, env=outer_env,
    )
    selector = selectors.DefaultSelector()
    assert proc.stdout is not None and proc.stderr is not None
    selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
    selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    timed_out = False
    output_limit = False
    terminated = False
    terminated_at = 0.0

    def terminate() -> None:
        nonlocal terminated, terminated_at
        if terminated:
            return
        terminated = True
        terminated_at = time.monotonic()
        subprocess.run(
            [systemctl, "--user", "kill", "--kill-who=all", unit],
            capture_output=True, timeout=10, env=outer_env,
        )
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    try:
        while selector.get_map() or proc.poll() is None:
            if time.monotonic() >= deadline:
                timed_out = True
                terminate()
            for key, _ in selector.select(timeout=0.2):
                chunk = os.read(key.fileobj.fileno(), 65_536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = buffers[key.data]
                room = MAX_VERIFY_OUTPUT_BYTES - len(target)
                if room > 0:
                    target.extend(chunk[:room])
                if len(chunk) > room:
                    output_limit = True
                    terminate()
            if terminated and proc.poll() is None and time.monotonic() >= terminated_at + 3:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        try:
            returncode = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            returncode = proc.wait(timeout=5)
    finally:
        selector.close()
        proc.stdout.close()
        proc.stderr.close()
    if timed_out:
        returncode = 124
    elif output_limit:
        returncode = 125
    return SandboxResult(
        returncode,
        buffers["stdout"].decode("utf-8", errors="replace"),
        buffers["stderr"].decode("utf-8", errors="replace"),
        timed_out=timed_out,
        output_limit=output_limit,
        limit_reason="timeout" if timed_out else ("output" if output_limit else ""),
    )


def _patch_stage_passed(
    stage: dict[str, Any] | None,
    *,
    require_applied: bool = False,
    require_published: bool = False,
) -> bool:
    passed = bool(stage and stage.get("applies_to_live") is True and
                  stage.get("tests_pass_in_worktree") is True)
    if require_applied:
        passed = bool(
            passed and stage.get("applied_to_live") is True
            and stage.get("post_apply_tests_passed") is True
            and not stage.get("rolled_back")
        )
    if require_published:
        return bool(
            passed
            and stage.get("publication_status") == "pushed"
            and stage.get("publication_required") is True
            and stage.get("committed_to_main") is True
            and stage.get("pushed_to_origin") is True
            and re.fullmatch(r"[0-9a-f]{40,64}", str(stage.get("commit_sha") or ""))
            and re.fullmatch(r"[0-9a-f]{40,64}", str(stage.get("commit_tree_sha") or ""))
            and stage.get("commit_parent_sha") == stage.get("publication_base_head")
            and stage.get("published_target_fingerprint")
            == stage.get("patched_target_fingerprint")
            and stage.get("remote_contains_commit") is True
            and stage.get("remote_target_fingerprint")
            == stage.get("patched_target_fingerprint")
        )
    return bool(passed)


def _stage_conflict_manifest(stage: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build the canonical, caller-confirmed compare-and-set proof."""
    if not isinstance(stage, dict) or stage.get("conflict_scope_version") != 2:
        return None
    resource_bounds = stage.get("resource_bounds")
    raw_paths = resource_bounds.get("changed_paths") if isinstance(resource_bounds, dict) else None
    try:
        paths = [_safe_relative_path(path) for path in raw_paths] if isinstance(raw_paths, list) else []
    except SnapshotError:
        return None
    required_digests = (
        "baseline_metadata_fingerprint", "baseline_target_fingerprint",
        "patched_target_fingerprint", "patch_sha256", "checks_sha256",
    )
    if (
        not paths
        or len(paths) > MAX_PATCH_FILES
        or paths != sorted(set(paths))
        or any(
            not re.fullmatch(r"[0-9a-f]{64}", str(stage.get(name) or ""))
            for name in required_digests
        )
    ):
        return None
    return {
        "version": 2,
        "changed_paths": paths,
        **{name: str(stage[name]) for name in required_digests},
    }


def _stage_conflict_fingerprint(stage: dict[str, Any] | None) -> str:
    manifest = _stage_conflict_manifest(stage)
    if manifest is None:
        return ""
    encoded = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8", errors="strict")
    return hashlib.sha256(b"skitarii-conflict-manifest-v2\0" + encoded).hexdigest()


def _stage_conflict_paths(stage: dict[str, Any] | None) -> list[str]:
    manifest = _stage_conflict_manifest(stage)
    if manifest is None:
        return []
    recorded = str((stage or {}).get("baseline_fingerprint") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", recorded):
        return []
    if not secrets.compare_digest(recorded, _stage_conflict_fingerprint(stage)):
        return []
    return list(manifest["changed_paths"])


def _applied_stage_proof_valid(stage: dict[str, Any] | None) -> bool:
    return bool(
        _patch_stage_passed(stage, require_applied=True)
        and _stage_conflict_paths(stage)
        and stage.get("post_apply_target_fingerprint")
        == stage.get("patched_target_fingerprint")
        and stage.get("post_apply_metadata_fingerprint")
        == stage.get("baseline_metadata_fingerprint")
    )


def _stage_artifacts_match(run_dir: Path, stage: dict[str, Any]) -> bool:
    root = run_dir.resolve()
    for relative, digest_key, maximum in (
        ("work/skitarii.patch", "patch_sha256", MAX_PATCH_INPUT_BYTES),
        ("work/.skitarii-verification-checks.json", "checks_sha256", 1_000_000),
    ):
        raw = run_dir / relative
        try:
            resolved = raw.resolve(strict=True)
            if (
                raw.is_symlink()
                or root not in resolved.parents
                or not resolved.is_file()
                or resolved.stat().st_size > maximum
                or _sha256_file(resolved) != str(stage.get(digest_key) or "")
            ):
                return False
        except (OSError, RuntimeError):
            return False
    return True


@contextmanager
def _repo_lock(root: Path, timeout: int = 30):
    """Serialize Ceraxia and block native Git metadata writers during apply."""
    import fcntl

    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    runtime.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:20]
    lock_path = runtime / f"skitarii-repo-{name}.lock"
    handle = lock_path.open("a+b")
    started = time.monotonic()
    acquired: list[tuple[Path, int, tuple[int, int]]] = []
    owner = threading.get_ident()
    publication_key = str(root.resolve())
    publication_guard = _OWNED_PUBLICATION_GUARDS.get(publication_key)
    cooperative_owned = bool(publication_guard and publication_guard[0] == owner)

    def release_git_locks() -> str:
        errors: list[str] = []
        for path, descriptor, identity in reversed(acquired):
            recorded = _OWNED_GIT_LOCKS.get(str(path))
            if recorded is not None and recorded[0] == owner:
                _OWNED_GIT_LOCKS.pop(str(path), None)
            try:
                current = path.lstat()
                if (current.st_dev, current.st_ino) == identity:
                    path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                errors.append(f"{path}: {type(exc).__name__}: {str(exc)[:120]}")
            try:
                os.close(descriptor)
            except OSError as exc:
                errors.append(f"fd for {path}: {type(exc).__name__}: {str(exc)[:120]}")
        acquired.clear()
        return "; ".join(errors)

    try:
        if not cooperative_owned:
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() - started >= timeout:
                        raise TimeoutError("timed out waiting for the repository apply lock")
                    time.sleep(0.1)
        guard_paths = _git_mutation_lock_paths(root.resolve())
        token = (uuid.uuid4().hex + "\n").encode("ascii")
        while True:
            busy = False
            for git_lock in guard_paths:
                try:
                    git_lock.parent.mkdir(parents=True, exist_ok=True)
                except FileExistsError as exc:
                    cleanup_error = release_git_locks()
                    if cleanup_error:
                        raise SnapshotError(
                            f"Git mutation guard cleanup failed: {cleanup_error}",
                        ) from exc
                    raise SnapshotError(
                        f"Git mutation lock parent is not a directory: {git_lock.parent}",
                    ) from exc
                try:
                    flags = (
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL
                        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                    )
                    descriptor = os.open(git_lock, flags, 0o600)
                except FileExistsError:
                    busy = True
                    break
                try:
                    metadata = os.fstat(descriptor)
                except Exception:
                    os.close(descriptor)
                    try:
                        git_lock.unlink()
                    except OSError:
                        pass
                    release_git_locks()
                    raise
                identity = (metadata.st_dev, metadata.st_ino)
                acquired.append((git_lock, descriptor, identity))
                try:
                    if os.write(descriptor, token) != len(token):
                        raise OSError("short Git mutation-lock write")
                    os.fsync(descriptor)
                except Exception:
                    cleanup_error = release_git_locks()
                    if cleanup_error:
                        raise SnapshotError(
                            f"Git mutation guard cleanup failed: {cleanup_error}",
                        )
                    raise
            if busy:
                cleanup_error = release_git_locks()
                if cleanup_error:
                    raise SnapshotError(
                        f"Git mutation guard cleanup failed: {cleanup_error}",
                    )
                if time.monotonic() - started >= timeout:
                    raise TimeoutError("timed out waiting for native Git mutation locks")
                time.sleep(0.1)
                continue
            break
        for git_lock, _descriptor, identity in acquired:
            _OWNED_GIT_LOCKS[str(git_lock)] = (owner, *identity)
        guard_key = str(root.resolve())
        _OWNED_GIT_GUARDS[guard_key] = (
            owner,
            tuple(str(path) for path in guard_paths),
        )
        active_operation = _git_operation_state_active(root.resolve())
        if active_operation:
            raise SnapshotError(
                f"repository has an active Git operation: {active_operation}",
            )
        yield round(time.monotonic() - started, 3)
    finally:
        propagating = sys.exc_info()[0] is not None
        guard_key = str(root.resolve())
        recorded_guard = _OWNED_GIT_GUARDS.get(guard_key)
        if recorded_guard is not None and recorded_guard[0] == owner:
            _OWNED_GIT_GUARDS.pop(guard_key, None)
        cleanup_error = release_git_locks()
        flock_error = ""
        try:
            if not cooperative_owned:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            flock_error = f"flock unlock: {type(exc).__name__}: {str(exc)[:120]}"
        finally:
            try:
                handle.close()
            except OSError as exc:
                if not flock_error:
                    flock_error = f"flock close: {type(exc).__name__}: {str(exc)[:120]}"
        cleanup_error = "; ".join(part for part in (cleanup_error, flock_error) if part)
        if cleanup_error and not propagating:
            raise SnapshotError(f"repository mutation guard cleanup failed: {cleanup_error}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_private_artifact(run_dir: Path, path: Path, payload: bytes) -> str:
    """Atomically replace one run artifact without ever following its pathname."""
    root = run_dir.resolve(strict=True)
    if run_dir.is_symlink():
        raise SnapshotError("run directory must not be a symlink")
    parent = path.parent
    if os.path.lexists(parent) and parent.is_symlink():
        raise SnapshotError("run artifact directory must not be a symlink")
    parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = parent.resolve(strict=True)
    if resolved_parent != root and root not in resolved_parent.parents:
        raise SnapshotError("run artifact directory escapes the run")
    before = parent.lstat()
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    directory_fd = os.open(parent, directory_flags)
    temporary = f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        opened = os.fstat(directory_fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise SnapshotError("run artifact directory changed during write")
        flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(temporary, flags, 0o600, dir_fd=directory_fd)
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short artifact write")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(
            temporary, path.name,
            src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)
    return hashlib.sha256(payload).hexdigest()


def _publication_git_env() -> dict[str, str]:
    """Non-interactive Git environment with literal pathspec semantics."""
    env = os.environ.copy()
    env.update({
        "GIT_LITERAL_PATHSPECS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "Never",
        "GIT_AUTHOR_NAME": "Ceraxia",
        "GIT_AUTHOR_EMAIL": "ceraxia@localhost",
        "GIT_COMMITTER_NAME": "Ceraxia",
        "GIT_COMMITTER_EMAIL": "ceraxia@localhost",
    })
    return env


def _git_object_id(value: object) -> str:
    oid = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40,64}", oid):
        raise SnapshotError("Git returned an invalid object id")
    return oid


def _publication_branch_and_head(root: Path) -> tuple[str, str]:
    env = _publication_git_env()
    branch = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"], cwd=root, env=env,
        capture_output=True, text=True, timeout=30,
    )
    if branch.returncode != 0 or branch.stdout.strip() != "refs/heads/main":
        raise SnapshotError("autonomous publication requires the checked-out main branch")
    head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=root, env=env,
        capture_output=True, text=True, timeout=30, check=True,
    ).stdout.strip()
    return "main", _git_object_id(head)


def _remote_main_sha(root: Path) -> str:
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--refs", "origin", "refs/heads/main"],
        cwd=root, env=_publication_git_env(), capture_output=True, timeout=60,
    )
    if result.returncode != 0 or len(result.stdout) > 4096:
        raise SnapshotError("origin/main could not be read non-interactively")
    rows = [row for row in result.stdout.splitlines() if row]
    if len(rows) != 1 or b"\t" not in rows[0]:
        raise SnapshotError("origin/main returned an invalid ref record")
    raw_oid, raw_ref = rows[0].split(b"\t", 1)
    if raw_ref != b"refs/heads/main":
        raise SnapshotError("origin/main returned an unexpected ref")
    return _git_object_id(raw_oid.decode("ascii", errors="strict"))


def _remote_main_contains(root: Path, commit_sha: str, remote_sha: str) -> bool:
    commit = _git_object_id(commit_sha)
    remote = _git_object_id(remote_sha)
    if commit == remote:
        return True
    env = _publication_git_env()
    present = subprocess.run(
        ["git", "cat-file", "-e", f"{remote}^{{commit}}"], cwd=root, env=env,
        capture_output=True, timeout=30,
    )
    if present.returncode != 0:
        fetched = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "fetch", "--quiet", "--no-tags",
             "origin", "refs/heads/main"],
            cwd=root, env=env, capture_output=True, timeout=120,
        )
        if fetched.returncode != 0:
            return False
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, remote],
        cwd=root, env=env, capture_output=True, timeout=30,
    ).returncode == 0


def _remote_publication_matches(
    root: Path,
    commit_sha: str,
    remote_sha: str,
    paths: Iterable[str],
    expected_targets: str,
) -> tuple[bool, str]:
    if not _remote_main_contains(root, commit_sha, remote_sha):
        return False, ""
    remote_targets = _git_tree_target_fingerprint(root, remote_sha, paths)
    return remote_targets == expected_targets, remote_targets


@contextmanager
def _publication_lock(root: Path, timeout: int = 30):
    """Serialize publication while leaving native Git locks to Git itself."""
    import fcntl

    key = str(root.resolve())
    owner = threading.get_ident()
    existing = _OWNED_PUBLICATION_GUARDS.get(key)
    if existing is not None and existing[0] == owner:
        _OWNED_PUBLICATION_GUARDS[key] = (owner, existing[1] + 1)
        try:
            yield 0.0
        finally:
            current = _OWNED_PUBLICATION_GUARDS.get(key)
            if current is not None and current[0] == owner:
                if current[1] <= 1:
                    _OWNED_PUBLICATION_GUARDS.pop(key, None)
                else:
                    _OWNED_PUBLICATION_GUARDS[key] = (owner, current[1] - 1)
        return

    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    runtime.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:20]
    lock_path = runtime / f"skitarii-repo-{name}.lock"
    handle = lock_path.open("a+b")
    started = time.monotonic()
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - started >= timeout:
                    raise TimeoutError("timed out waiting for the repository publication lock")
                time.sleep(0.1)
        _OWNED_PUBLICATION_GUARDS[key] = (owner, 1)
        yield round(time.monotonic() - started, 3)
    finally:
        current = _OWNED_PUBLICATION_GUARDS.get(key)
        if current is not None and current[0] == owner:
            _OWNED_PUBLICATION_GUARDS.pop(key, None)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


@contextmanager
def _run_apply_lock(run_dir: Path, timeout: int = 30):
    """Serialize cancellation and durable repository-mutation transitions per run."""
    import fcntl

    resolved = run_dir.resolve()
    key = str(resolved)
    owner = threading.get_ident()
    existing = _OWNED_RUN_APPLY_GUARDS.get(key)
    if existing is not None and existing[0] == owner:
        _OWNED_RUN_APPLY_GUARDS[key] = (owner, existing[1] + 1)
        try:
            yield
        finally:
            current = _OWNED_RUN_APPLY_GUARDS.get(key)
            if current is not None and current[0] == owner:
                if current[1] <= 1:
                    _OWNED_RUN_APPLY_GUARDS.pop(key, None)
                else:
                    _OWNED_RUN_APPLY_GUARDS[key] = (owner, current[1] - 1)
        return
    lock_root = Path(os.environ.get("WARMMASTER_RUNTIME_ROOT", "/tmp")) / "skitarii-apply-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_name = hashlib.sha256(key.encode("utf-8")).hexdigest()
    lock_path = lock_root / f"{lock_name}.lock"
    handle = lock_path.open("a+b")
    started = time.monotonic()
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - started >= timeout:
                    raise TimeoutError("timed out waiting for the run mutation lock")
                time.sleep(0.05)
        _OWNED_RUN_APPLY_GUARDS[key] = (owner, 1)
        yield
    finally:
        current = _OWNED_RUN_APPLY_GUARDS.get(key)
        if current is not None and current[0] == owner:
            _OWNED_RUN_APPLY_GUARDS.pop(key, None)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _git_blob_sha256(root: Path, oid: str, cache: dict[str, str]) -> str:
    object_id = _git_object_id(oid)
    if object_id not in cache:
        payload = _bounded_command_stdout(
            ["git", "cat-file", "blob", object_id], root,
            timeout=60, max_bytes=MAX_PATCH_FILE_BYTES,
        )
        cache[object_id] = hashlib.sha256(payload).hexdigest()
    return cache[object_id]


def _target_entry_fingerprint(
    root: Path,
    paths: Iterable[str],
    entries: dict[str, tuple[str, str]],
) -> str:
    digest = hashlib.sha256(b"skitarii-content-v1\0")
    cache: dict[str, str] = {}
    for rel in sorted({_safe_relative_path(path) for path in paths}):
        entry = entries.get(rel)
        if entry is None:
            digest.update(f"missing\0{rel}\0\0\0".encode("utf-8", errors="strict"))
            continue
        mode, oid = entry
        if mode not in {"100644", "100755"}:
            raise SnapshotError(f"publication target has an unsupported Git mode: {rel}")
        content_sha = _git_blob_sha256(root, oid, cache)
        digest.update(
            f"file\0{rel}\0{mode}\0{content_sha}\0".encode("utf-8", errors="strict"),
        )
    return digest.hexdigest()


def _git_tree_target_fingerprint(root: Path, treeish: str, paths: Iterable[str]) -> str:
    commit = _git_object_id(treeish)
    env = _publication_git_env()
    entries: dict[str, tuple[str, str]] = {}
    selected = sorted({_safe_relative_path(path) for path in paths})
    for rel in selected:
        result = subprocess.run(
            ["git", "ls-tree", "-z", commit, "--", rel], cwd=root, env=env,
            capture_output=True, timeout=30, check=True,
        )
        records = [record for record in result.stdout.split(b"\0") if record]
        if not records:
            continue
        if len(records) != 1 or b"\t" not in records[0]:
            raise SnapshotError(f"Git tree returned an ambiguous publication target: {rel}")
        metadata, raw_path = records[0].split(b"\t", 1)
        fields = metadata.split()
        decoded = raw_path.decode("utf-8", errors="strict")
        if len(fields) != 3 or fields[1] != b"blob" or decoded != rel:
            raise SnapshotError(f"Git tree returned an invalid publication target: {rel}")
        entries[rel] = (
            fields[0].decode("ascii", errors="strict"),
            _git_object_id(fields[2].decode("ascii", errors="strict")),
        )
    return _target_entry_fingerprint(root, selected, entries)


def _git_index_target_fingerprint(root: Path, paths: Iterable[str]) -> str:
    selected = sorted({_safe_relative_path(path) for path in paths})
    wanted = set(selected)
    raw = _bounded_command_stdout(
        ["git", "ls-files", "--stage", "-z"], root,
        timeout=60, max_bytes=40_000_000,
    )
    entries: dict[str, tuple[str, str]] = {}
    for record in raw.split(b"\0"):
        if not record or b"\t" not in record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        rel = raw_path.decode("utf-8", errors="strict")
        if rel not in wanted:
            continue
        fields = metadata.split()
        if len(fields) != 3 or fields[2] != b"0" or rel in entries:
            raise SnapshotError(f"publication target has an unmerged index entry: {rel}")
        entries[rel] = (
            fields[0].decode("ascii", errors="strict"),
            _git_object_id(fields[1].decode("ascii", errors="strict")),
        )
    flags = _bounded_command_stdout(
        ["git", "ls-files", "-v", "-z"], root,
        timeout=60, max_bytes=40_000_000,
    )
    for record in flags.split(b"\0"):
        if not record or len(record) < 3 or record[1:2] != b" ":
            continue
        rel = record[2:].decode("utf-8", errors="strict")
        if rel in wanted and record[:1] != b"H":
            raise SnapshotError(f"publication target has special Git index flags: {rel}")
    return _target_entry_fingerprint(root, selected, entries)


def _publication_targets_clean(
    root: Path,
    head: str,
    paths: Iterable[str],
    expected_worktree: str,
    *,
    allowed_large: dict[str, dict[str, Any]] | None = None,
) -> bool:
    selected = sorted({_safe_relative_path(path) for path in paths})
    worktree = _git_visible_content_fingerprint(
        root, paths=selected, allowed_large=allowed_large,
    )
    return bool(
        worktree == expected_worktree
        and _git_tree_target_fingerprint(root, head, selected) == expected_worktree
        and _git_index_target_fingerprint(root, selected) == expected_worktree
    )


def _publication_attributes_safe(root: Path, paths: Iterable[str]) -> bool:
    env = _publication_git_env()
    for rel in sorted({_safe_relative_path(path) for path in paths}):
        result = subprocess.run(
            ["git", "check-attr", "-z", "filter", "working-tree-encoding",
             "text", "eol", "ident", "--", rel],
            cwd=root, env=env, capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            return False
        fields = result.stdout.split(b"\0")
        triples = [fields[index:index + 3] for index in range(0, len(fields) - 1, 3)]
        if any(
            len(triple) != 3
            or triple[0].decode("utf-8", errors="strict") != rel
            or triple[2] not in {b"unspecified", b"unset"}
            for triple in triples
        ):
            return False
    return True


def _publication_pathspec_file(run_dir: Path, name: str, paths: Iterable[str]) -> Path:
    selected = sorted({_safe_relative_path(path) for path in paths})
    if not selected:
        raise SnapshotError("publication pathspec cannot be empty")
    path = run_dir / "work" / name
    payload = b"".join(rel.encode("utf-8", errors="strict") + b"\0" for rel in selected)
    _write_private_artifact(run_dir, path, payload)
    return path.resolve(strict=True)


def _publication_new_paths(root: Path, base_head: str, paths: Iterable[str]) -> list[str]:
    env = _publication_git_env()
    new_paths: list[str] = []
    for rel in sorted({_safe_relative_path(path) for path in paths}):
        result = subprocess.run(
            ["git", "ls-tree", "-z", base_head, "--", rel], cwd=root, env=env,
            capture_output=True, timeout=30, check=True,
        )
        records = [record for record in result.stdout.split(b"\0") if record]
        if not records:
            new_paths.append(rel)
        elif len(records) != 1:
            raise SnapshotError(f"Git tree returned an ambiguous baseline target: {rel}")
    return new_paths


def _publication_commit_details(
    root: Path,
    commit_sha: str,
    base_head: str,
    paths: Iterable[str],
    expected_targets: str,
) -> tuple[str, str, str]:
    commit = _git_object_id(commit_sha)
    base = _git_object_id(base_head)
    env = _publication_git_env()
    parents = subprocess.run(
        ["git", "rev-list", "--parents", "-n", "1", commit], cwd=root, env=env,
        capture_output=True, text=True, timeout=30, check=True,
    ).stdout.split()
    if len(parents) != 2 or _git_object_id(parents[0]) != commit:
        raise SnapshotError("publication commit must have exactly one parent")
    parent = _git_object_id(parents[1])
    if parent != base:
        raise SnapshotError("publication commit parent changed before publication")
    changed = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "--no-renames",
         "-r", "-z", base, commit, "--"],
        cwd=root, env=env, capture_output=True, timeout=60, check=True,
    ).stdout
    changed_paths = sorted({
        _safe_relative_path(raw.decode("utf-8", errors="strict"))
        for raw in changed.split(b"\0") if raw
    })
    selected = sorted({_safe_relative_path(path) for path in paths})
    if changed_paths != selected:
        raise SnapshotError("publication commit changed files outside the verified patch")
    tree = subprocess.run(
        ["git", "rev-parse", f"{commit}^{{tree}}"], cwd=root, env=env,
        capture_output=True, text=True, timeout=30, check=True,
    ).stdout.strip()
    tree_sha = _git_object_id(tree)
    target_fingerprint = _git_tree_target_fingerprint(root, commit, selected)
    if target_fingerprint != expected_targets:
        raise SnapshotError("publication commit bytes differ from the verified patch")
    return parent, tree_sha, target_fingerprint


def _publication_next_action(stage: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "kind": "retry_publish",
        "method": "POST",
        "endpoint": "POST /runs/{task_id}/apply_patch",
        "body": {
            "expected_repository_fingerprint": str(stage.get("baseline_fingerprint") or ""),
            "expected_patch_sha256": str(stage.get("patch_sha256") or ""),
            "expected_checks_sha256": str(stage.get("checks_sha256") or ""),
            "confirm_apply": True,
        },
        "reason": reason,
    }


def _record_linked_publication_progress(run_dir: Path, phase: str, reason: str) -> None:
    """Expose recoverable publication as working, never as a stale blocker."""
    if phase == "blocked":
        return
    try:
        mission_dir = _mission_dir(run_dir)
        if not mission_dir or not mission_dir.exists():
            return
        from . import mission_control as mc

        mission = _read_json(mission_dir / "mission.json")
        mission_id = str(mission.get("mission_id") or mission_dir.name)
        current = _read_json(mission_dir / "mission_state.json")
        if (
            str(current.get("run_status") or "") == phase
            and str(current.get("phase") or "") == phase
        ):
            return
        mc.record_mission_state(
            mission_dir,
            "executing",
            run_status=phase,
            active=True,
            phase=phase,
        )
        mc.append_progress_event(
            mission_dir / "progress_events.jsonl",
            mc.progress_event(
                mission_id,
                "Ceraxia",
                "governor",
                "finalizing",
                "running",
                "Verified code is being published",
                reason[:400],
            ),
        )
    except Exception:
        # The ledger checkpoint remains authoritative and the publisher must not
        # lose repository recovery merely because a UI projection is unavailable.
        return


def _persist_publication_checkpoint(
    run_dir: Path,
    ledger: Any,
    stage: dict[str, Any],
    *,
    phase: str,
    reason: str,
) -> None:
    """Make commit/push recovery durable before the next external side effect."""
    existing = ledger.data.get("result") if isinstance(getattr(ledger, "data", None), dict) else {}
    result = dict(existing) if isinstance(existing, dict) else {}
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
    if "work/skitarii.patch" not in artifacts:
        artifacts = [*artifacts, "work/skitarii.patch"]
    status = "blocked" if phase == "blocked" else phase
    result.update({
        "ok": False,
        "status": status,
        "phase": phase,
        "final_step": "skitarii",
        "summary": reason,
        "artifact_root": str(run_dir.resolve()),
        "artifacts": artifacts,
        "patch_stage": dict(stage),
        "ready_to_apply": False,
        "next_action": (
            {} if phase == "blocked" else _publication_next_action(stage, reason)
        ),
    })
    ledger.data["result"] = result
    ledger.force_status(
        "blocked" if phase == "blocked" else phase,
        reason=reason,
    )
    ledger.record_event(
        "skitarii_publication_checkpoint",
        {
            "phase": phase,
            "publication_status": str(stage.get("publication_status") or ""),
            "commit_sha": str(stage.get("commit_sha") or ""),
            "attempts": int(stage.get("publication_attempts") or 0),
        },
    )
    _record_linked_publication_progress(run_dir, phase, reason)


def _clear_publication_intent_entries(
    root: Path,
    base_head: str,
    new_paths: Iterable[str],
) -> None:
    """Remove only safe intent-to-add entries left by an interrupted commit."""
    env = _publication_git_env()
    for rel in sorted({_safe_relative_path(path) for path in new_paths}):
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--", rel], cwd=root, env=env,
            capture_output=True, timeout=30, check=True,
        ).stdout
        if not listed:
            continue
        staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet", base_head, "--", rel],
            cwd=root, env=env, capture_output=True, timeout=30,
        )
        if staged.returncode != 0:
            raise SnapshotError(
                f"new publication target acquired staged content outside the transaction: {rel}",
            )
        reset = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "reset", "-q",
             base_head, "--", rel], cwd=root, env=env,
            capture_output=True, timeout=30,
        )
        if reset.returncode != 0:
            raise SnapshotError("could not clear an interrupted publication intent entry")


def _rollback_unverified_publication_commit(
    root: Path,
    commit_sha: str,
    pathspec_file: Path,
) -> bool:
    """CAS-remove only the just-created local commit and repair target index entries."""
    commit = _git_object_id(commit_sha)
    env = _publication_git_env()
    try:
        _branch, head = _publication_branch_and_head(root)
        if head != commit:
            return False
        parents = subprocess.run(
            ["git", "rev-list", "--parents", "-n", "1", commit], cwd=root, env=env,
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout.split()
        if len(parents) != 2 or _git_object_id(parents[0]) != commit:
            return False
        parent = _git_object_id(parents[1])
        moved = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "update-ref",
             "refs/heads/main", parent, commit],
            cwd=root, env=env, capture_output=True, timeout=30,
        )
        if moved.returncode != 0:
            return False
        reset = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "reset", "-q", parent,
             f"--pathspec-from-file={pathspec_file}", "--pathspec-file-nul"],
            cwd=root, env=env, capture_output=True, timeout=60,
        )
        return reset.returncode == 0
    except (OSError, subprocess.SubprocessError, SnapshotError):
        return False


def _published_stage_proof_valid(
    stage: dict[str, Any] | None,
    *,
    verify_remote: bool = False,
    root: Path | None = None,
) -> bool:
    if not _patch_stage_passed(stage, require_applied=True, require_published=True):
        return False
    assert isinstance(stage, dict)
    changed_paths = _stage_conflict_paths(stage)
    if not changed_paths:
        return False
    publication_root = (root or REPO_ROOT).resolve()
    try:
        parent, tree_sha, target_fingerprint = _publication_commit_details(
            publication_root,
            str(stage.get("commit_sha") or ""),
            str(stage.get("publication_base_head") or ""),
            changed_paths,
            str(stage.get("patched_target_fingerprint") or ""),
        )
        if (
            parent != str(stage.get("commit_parent_sha") or "")
            or tree_sha != str(stage.get("commit_tree_sha") or "")
            or target_fingerprint != str(stage.get("published_target_fingerprint") or "")
        ):
            return False
        if verify_remote:
            remote_sha = _remote_main_sha(publication_root)
            matches, remote_targets = _remote_publication_matches(
                publication_root,
                str(stage.get("commit_sha") or ""),
                remote_sha,
                changed_paths,
                str(stage.get("patched_target_fingerprint") or ""),
            )
            if not matches or remote_targets != str(
                stage.get("remote_target_fingerprint") or ""
            ):
                return False
    except (OSError, UnicodeError, subprocess.SubprocessError, SnapshotError):
        return False
    return True


def _completion_stage_proof_valid(stage: dict[str, Any] | None) -> bool:
    if not _applied_stage_proof_valid(stage):
        return False
    if not isinstance(stage, dict) or stage.get("publication_required") is not True:
        return True
    return _published_stage_proof_valid(stage, verify_remote=True)


def _publish_verified_patch(
    root: Path,
    stage: dict[str, Any],
    run_dir: Path,
    ledger: Any,
) -> dict[str, Any]:
    """Commit only verified targets and prove their presence in origin/main."""
    root = root.resolve()
    changed_paths = _stage_conflict_paths(stage)
    if not changed_paths or not _applied_stage_proof_valid(stage):
        stage["publication_status"] = "conflict"
        stage["publication_error"] = "verified live-apply proof is missing"
        _persist_publication_checkpoint(
            run_dir, ledger, stage, phase="blocked",
            reason="autonomous publication stopped: verified live-apply proof is missing",
        )
        return stage
    base_head = _git_object_id(stage.get("publication_base_head"))
    expected_targets = str(stage.get("patched_target_fingerprint") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_targets):
        stage["publication_status"] = "conflict"
        stage["publication_error"] = "verified target fingerprint is missing"
        _persist_publication_checkpoint(
            run_dir, ledger, stage, phase="blocked",
            reason="autonomous publication stopped: verified target fingerprint is missing",
        )
        return stage
    try:
        with _publication_lock(root, timeout=1800) as wait_seconds:
            stage["publication_lock_wait_seconds"] = wait_seconds
            stage["publication_attempts"] = int(stage.get("publication_attempts") or 0) + 1
            _branch, head = _publication_branch_and_head(root)
            recorded_commit = str(stage.get("commit_sha") or "")

            if recorded_commit:
                commit_sha = _git_object_id(recorded_commit)
                parent, tree_sha, target_fingerprint = _publication_commit_details(
                    root, commit_sha, base_head, changed_paths, expected_targets,
                )
            elif head != base_head:
                # A crash can happen after git commit updates main and before the
                # resulting SHA is checkpointed. Adopt it only if every byte and
                # every changed path proves that it is exactly this mission.
                commit_sha = head
                parent, tree_sha, target_fingerprint = _publication_commit_details(
                    root, commit_sha, base_head, changed_paths, expected_targets,
                )
            else:
                remote_before = _remote_main_sha(root)
                stage["remote_main_sha"] = remote_before
                if remote_before != base_head:
                    raise SnapshotError("origin/main advanced before publication")
                new_paths = [
                    _safe_relative_path(path)
                    for path in (stage.get("publication_new_paths") or [])
                ]
                if (
                    stage.get("publication_intent_owned") is True
                    or stage.get("publication_intent_prepared") is True
                ):
                    _clear_publication_intent_entries(root, base_head, new_paths)
                    stage["publication_intent_owned"] = False
                if _git_metadata_fingerprint(root) != str(
                    stage.get("baseline_metadata_fingerprint") or ""
                ):
                    raise SnapshotError("Git HEAD or index changed before publication")
                if _git_visible_content_fingerprint(root, paths=changed_paths) != expected_targets:
                    raise SnapshotError("verified patch targets changed before publication")
                if _git_index_target_fingerprint(root, changed_paths) != str(
                    stage.get("baseline_target_fingerprint") or ""
                ):
                    raise SnapshotError("publication target index changed before commit")
                if not _publication_attributes_safe(root, changed_paths):
                    raise SnapshotError("publication target has unsafe Git clean attributes")

                pathspec_file = _publication_pathspec_file(
                    run_dir, ".skitarii-publication-paths", changed_paths,
                )
                stage["publication_status"] = "publishing"
                stage["publication_error"] = ""
                stage["publication_intent_prepared"] = bool(new_paths)
                stage["publication_intent_owned"] = False
                _persist_publication_checkpoint(
                    run_dir, ledger, stage, phase="publishing",
                    reason="verified patch is being committed to main",
                )
                env = _publication_git_env()
                if new_paths:
                    new_pathspec = _publication_pathspec_file(
                        run_dir, ".skitarii-publication-new-paths", new_paths,
                    )
                    intent = subprocess.run(
                        ["git", "-c", "core.hooksPath=/dev/null",
                         "-c", "core.autocrlf=false", "add", "--intent-to-add",
                         f"--pathspec-from-file={new_pathspec}", "--pathspec-file-nul"],
                        cwd=root, env=env, capture_output=True, timeout=60,
                    )
                    if intent.returncode != 0:
                        raise SnapshotError("new publication targets could not be prepared")
                    stage["publication_intent_owned"] = True
                    _persist_publication_checkpoint(
                        run_dir, ledger, stage, phase="publishing",
                        reason="new mission paths are prepared for the verified commit",
                    )
                message = f"Ceraxia: {run_dir.name}"[:240]
                try:
                    committed = subprocess.run(
                        ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false",
                         "-c", "core.autocrlf=false", "-c", "user.name=Ceraxia",
                         "-c", "user.email=ceraxia@localhost", "commit", "--only",
                         "--no-verify", "--no-gpg-sign", "--cleanup=verbatim", "-m", message,
                         f"--pathspec-from-file={pathspec_file}", "--pathspec-file-nul"],
                        cwd=root, env=env, capture_output=True, timeout=120,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("publication commit outcome is unknown and will be recovered") from exc
                if committed.returncode != 0:
                    if stage.get("publication_intent_owned") is True:
                        _clear_publication_intent_entries(root, base_head, new_paths)
                        stage["publication_intent_owned"] = False
                    raise RuntimeError("verified publication commit could not be created")
                _branch, commit_sha = _publication_branch_and_head(root)
                try:
                    parent, tree_sha, target_fingerprint = _publication_commit_details(
                        root, commit_sha, base_head, changed_paths, expected_targets,
                    )
                except (OSError, UnicodeError, subprocess.SubprocessError, SnapshotError) as exc:
                    rolled_back = _rollback_unverified_publication_commit(
                        root, commit_sha, pathspec_file,
                    )
                    stage["unverified_commit_rolled_back"] = rolled_back
                    if not rolled_back:
                        stage["local_repository_requires_inspection"] = True
                    raise SnapshotError(
                        "new local commit failed publication proof"
                        + (" and was rolled back" if rolled_back else " and could not be rolled back"),
                    ) from exc

            stage.update({
                "commit_sha": commit_sha,
                "commit_parent_sha": parent,
                "commit_tree_sha": tree_sha,
                "published_target_fingerprint": target_fingerprint,
                "committed_to_main": True,
                "publication_status": "publishing",
                "publication_error": "",
                "publication_intent_owned": False,
                "publication_intent_prepared": False,
            })
            _persist_publication_checkpoint(
                run_dir, ledger, stage, phase="publishing",
                reason="verified commit is awaiting origin/main confirmation",
            )

            try:
                remote_before = _remote_main_sha(root)
            except SnapshotError:
                stage.update({
                    "publication_status": "push_pending",
                    "pushed_to_origin": False,
                    "remote_contains_commit": False,
                    "publication_error": "origin/main is temporarily unavailable",
                })
                _persist_publication_checkpoint(
                    run_dir, ledger, stage, phase="push_pending",
                    reason="verified commit exists locally; origin/main is temporarily unavailable",
                )
                return stage
            stage["remote_main_sha"] = remote_before
            remote_matches, remote_targets = _remote_publication_matches(
                root, commit_sha, remote_before, changed_paths, expected_targets,
            )
            stage["remote_target_fingerprint"] = remote_targets
            if remote_matches:
                contains = True
            else:
                if remote_before != base_head:
                    raise SnapshotError("origin/main diverged before the verified commit was pushed")
                if head != commit_sha and _publication_branch_and_head(root)[1] != commit_sha:
                    raise SnapshotError("local main changed before the verified commit was pushed")
                try:
                    pushed = subprocess.run(
                        ["git", "-c", "core.hooksPath=/dev/null", "push", "--porcelain",
                         "--no-verify", "origin", f"{commit_sha}:refs/heads/main"],
                        cwd=root, env=_publication_git_env(), capture_output=True, timeout=120,
                    )
                except subprocess.TimeoutExpired:
                    pushed = None
                try:
                    remote_after = _remote_main_sha(root)
                except SnapshotError:
                    remote_after = ""
                stage["remote_main_sha"] = remote_after
                if remote_after:
                    contains, remote_targets = _remote_publication_matches(
                        root, commit_sha, remote_after, changed_paths, expected_targets,
                    )
                    stage["remote_target_fingerprint"] = remote_targets
                else:
                    contains = False
                if not contains and pushed is not None and pushed.returncode == 0:
                    stage["publication_error"] = "push returned success but remote proof is unavailable"
            if not contains:
                remote_after = str(stage.get("remote_main_sha") or "")
                if remote_after and remote_after != base_head:
                    stage.update({
                        "publication_status": "conflict",
                        "pushed_to_origin": False,
                        "remote_contains_commit": False,
                        "publication_error": "origin/main diverged from the verified commit",
                    })
                    _persist_publication_checkpoint(
                        run_dir, ledger, stage, phase="blocked",
                        reason="origin/main diverged; autonomous publication will not force-push",
                    )
                    return stage
                stage.update({
                    "publication_status": "push_pending",
                    "pushed_to_origin": False,
                    "remote_contains_commit": False,
                    "publication_error": str(stage.get("publication_error") or
                                             "origin/main publication will be retried"),
                })
                _persist_publication_checkpoint(
                    run_dir, ledger, stage, phase="push_pending",
                    reason="verified commit exists locally; origin/main push will be retried",
                )
                return stage

            stage.update({
                "publication_status": "pushed",
                "pushed_to_origin": True,
                "remote_contains_commit": True,
                "publication_error": "",
            })
            _persist_publication_checkpoint(
                run_dir, ledger, stage, phase="publishing",
                reason="origin/main contains the verified commit; finalizing mission protocol",
            )
            return stage
    except RuntimeError as exc:
        stage.update({
            "publication_status": "push_pending",
            "pushed_to_origin": False,
            "remote_contains_commit": False,
            "publication_error": str(exc)[:200],
        })
        _persist_publication_checkpoint(
            run_dir, ledger, stage, phase="push_pending",
            reason="verified publication transaction will be retried",
        )
    except (OSError, UnicodeError, subprocess.SubprocessError, SnapshotError, TimeoutError) as exc:
        stage.update({
            "publication_status": "conflict",
            "pushed_to_origin": False,
            "remote_contains_commit": False,
            "publication_error": str(exc)[:200],
        })
        _persist_publication_checkpoint(
            run_dir, ledger, stage, phase="blocked",
            reason=f"autonomous publication stopped safely: {str(exc)[:180]}",
        )
    return stage


def _changed_paths_against_head(root: Path) -> list[str]:
    """Ask Git for authoritative decoded paths after applying into a temp baseline."""
    tracked = subprocess.run(
        ["git", "diff", "--name-only", "-z", "HEAD", "--"],
        cwd=root, capture_output=True, timeout=60, check=True,
    ).stdout
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=root, capture_output=True, timeout=60, check=True,
    ).stdout
    paths = {
        _safe_relative_path(raw.decode("utf-8", errors="strict"))
        for raw in (tracked + untracked).split(b"\0") if raw
    }
    if len(paths) > MAX_PATCH_FILES:
        raise SnapshotError(f"patch changes more than {MAX_PATCH_FILES} files")
    return sorted(paths)


def _materialize_snapshot(snapshot: WorkspaceSnapshot, destination: Path) -> None:
    """Create a self-contained Git baseline from the captured repository bytes."""
    destination.mkdir(parents=True, exist_ok=True)
    for rel, content in snapshot.items():
        path = _materialize_path(destination, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(content), encoding="utf-8")
    for rel, encoded in snapshot.blobs.items():
        path = _materialize_path(destination, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(str(encoded), validate=True))
    for rel, target in snapshot.symlinks.items():
        safe_rel = _safe_relative_path(rel)
        path = _materialize_path(destination, safe_rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(_safe_symlink_target(safe_rel, target))
    # Clean oversized tracked assets are immutable manifest entries. They are omitted
    # from disposable worktrees so verification never duplicates multi-GB blobs; the
    # content fingerprint below projects their recorded hashes back into the tree.
    for rel, mode in snapshot.modes.items():
        path = _materialize_path(destination, rel)
        if str(mode) == "120000":
            if not path.is_symlink():
                raise SnapshotError(f"expected baseline symlink is missing: {rel}")
            continue
        if path.is_symlink() or not path.exists():
            raise SnapshotError(f"baseline mode target is missing: {rel}")
        current = path.stat().st_mode
        path.chmod((current | 0o111) if str(mode) == "100755" else (current & ~0o111))
    subprocess.run(["git", "init", "-q"], cwd=destination, check=True, timeout=60)
    subprocess.run(["git", "add", "-f", "-A", "--", "."], cwd=destination, check=True, timeout=120)
    subprocess.run(
        ["git", "-c", "user.email=skitarii@invalid", "-c", "user.name=Skitarii",
         "commit", "--allow-empty", "-qm", "captured baseline"],
        cwd=destination, check=True, timeout=120,
    )


def _run_check_set(checks: list[dict[str, Any]], baseline: Path) -> tuple[bool, list[dict], str]:
    if not checks:
        return False, [], "accepted patch has no executable checks to rerun"
    if len(checks) > MAX_VERIFY_CHECKS:
        return False, [], f"verification check count exceeds {MAX_VERIFY_CHECKS}"
    deadline = time.monotonic() + MAX_VERIFY_TOTAL_SECONDS
    results: list[dict] = []
    for check in checks:
        command = str(check.get("cmd") or "")
        if not command or len(command.encode("utf-8")) > MAX_VERIFY_COMMAND_BYTES:
            return False, results, "verification command is empty or oversized"
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            return False, results, "verification set exceeded its cumulative deadline"
        result = _run_sandboxed_check(command, baseline, min(180, remaining))
        ok = result.returncode == 0 and not result.timed_out and not result.output_limit
        stdout = (result.stdout or "").strip()
        if "expect_stdout" in check:
            ok = ok and stdout == str(check["expect_stdout"]).strip()
        oracle_result = None
        oracle_command = str(check.get("oracle") or "").strip()
        if oracle_command:
            remaining = int(deadline - time.monotonic())
            if remaining <= 0:
                return False, results, "verification set exceeded its cumulative deadline"
            oracle_result = _run_sandboxed_check(oracle_command, baseline, min(180, remaining))
            ok = (
                ok and oracle_result.returncode == 0
                and not oracle_result.timed_out and not oracle_result.output_limit
                and stdout == (oracle_result.stdout or "").strip()
            )
        record = {
            "command": command,
            "returncode": result.returncode,
            "ok": ok,
            "stdout": result.stdout[-400:],
            "stderr": result.stderr[-400:],
            "timed_out": result.timed_out,
            "output_limit": result.output_limit,
            "limit_reason": result.limit_reason,
        }
        if oracle_result is not None:
            record["oracle_returncode"] = oracle_result.returncode
            record["oracle_output_limit"] = oracle_result.output_limit
        results.append(record)
        if not ok:
            return False, results, "a bounded isolated verification check failed"
    return True, results, ""


def _verify_and_stage_patch(
    verdict: dict[str, Any],
    run_dir: Path,
    ledger: Any,
    workspace: dict[str, str] | None = None,
    *,
    autoapply: bool | None = None,
    persist_artifacts: bool = True,
    expected_conflict_fingerprint: str = "",
) -> dict[str, Any] | None:
    """Verify a frozen patch, reject stale baselines, and transactionally auto-apply."""
    import tempfile

    patch_bundle = verdict.get("patch_bundle") if isinstance(verdict.get("patch_bundle"), dict) else None
    diff = str((patch_bundle or {}).get("unified_diff") or "")
    if not diff.strip():
        return None
    patch_file = run_dir / "work" / "skitarii.patch"

    def run_git(
        args: list[str], cwd: Path, timeout: int = 120, *, patch_input: bool = False,
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
            input=diff if patch_input else None,
        )

    root = REPO_ROOT.resolve()
    snapshot = workspace if isinstance(workspace, WorkspaceSnapshot) else WorkspaceSnapshot(dict(workspace or {}))
    out: dict[str, Any] = {
        "applies_to_live": None,
        "tests_pass_in_worktree": None,
        "applied_to_live": False,
        "post_apply_tests_passed": None,
        "rolled_back": False,
        "apply_cmd": "",
    }
    expected_fingerprint = ""
    expected_post_content = ""
    expected_baseline_targets = ""
    expected_post_targets = ""
    expected_metadata = ""
    pre_apply_metadata = ""
    authoritative_changed: list[str] = []
    publication_guard: Any = None

    try:
        out["resource_bounds"] = _validate_patch_resource_bounds(diff)
    except SnapshotError as exc:
        out["tests_pass_in_worktree"] = False
        out["reason"] = str(exc)
        ledger.record_event("skitarii_patch_stage", out)
        return out
    patch_bytes = diff.encode("utf-8", errors="strict")
    out["patch_file"] = str(patch_file)
    out["patch_sha256"] = (
        _write_private_artifact(run_dir, patch_file, patch_bytes)
        if persist_artifacts else hashlib.sha256(patch_bytes).hexdigest()
    )

    def rollback_live_if_safe() -> None:
        if not out.get("applied_to_live") or out.get("rolled_back"):
            return
        active_guard = _OWNED_GIT_GUARDS.get(str(root.resolve()))
        if active_guard is None or active_guard[0] != threading.get_ident():
            try:
                out["rollback_guard_reacquired"] = True
                with _repo_lock(root):
                    rollback_live_if_safe()
            except Exception as guard_exc:
                out["rollback_failed"] = True
                out["rollback_error"] = (
                    "could not reacquire repository mutation guard: "
                    f"{type(guard_exc).__name__}: {str(guard_exc)[:180]}"
                )
            return
        try:
            current_metadata, current_targets = _stable_scoped_live_state(
                root, authoritative_changed,
                allowed_large=snapshot.external_assets,
            )
            if (
                not expected_post_targets
                or current_targets != expected_post_targets
                or not pre_apply_metadata
                or current_metadata != pre_apply_metadata
            ):
                out["rollback_blocked_external_target_change"] = True
                return
            reverse_check = run_git(
                ["apply", "--reverse", "--check", "--binary", "--whitespace=nowarn", "-"], root, 60,
                patch_input=True,
            )
            if reverse_check.returncode == 0:
                # The check may itself be slow.  Close that avoidable TOCTOU
                # window before allowing the reverse patch to touch live bytes.
                confirmed_metadata, confirmed_targets = _stable_scoped_live_state(
                    root, authoritative_changed,
                    allowed_large=snapshot.external_assets,
                )
                if (
                    confirmed_targets != expected_post_targets
                    or confirmed_metadata != pre_apply_metadata
                ):
                    out["rollback_blocked_external_target_change"] = True
                    return
                reverse = run_git(
                    ["apply", "--reverse", "--binary", "--whitespace=nowarn", "-"], root, 60,
                    patch_input=True,
                )
            else:
                reverse = reverse_check
            restored_metadata, restored_targets = _stable_scoped_live_state(
                root, authoritative_changed,
                allowed_large=snapshot.external_assets,
            )
            out["rolled_back"] = (
                reverse.returncode == 0
                and restored_targets == expected_baseline_targets
                and restored_metadata == pre_apply_metadata
            )
            if out["rolled_back"]:
                out["applied_to_live"] = False
            else:
                out["rollback_failed"] = True
        except Exception as rollback_exc:
            out["rollback_failed"] = True
            out["rollback_error"] = f"{type(rollback_exc).__name__}: {str(rollback_exc)[:200]}"
    try:
        if run_git(["rev-parse", "--git-dir"], root, 30).returncode != 0:
            out["reason"] = "REPO_ROOT is not a git repository"
            ledger.record_event("skitarii_patch_stage", out)
            return out
        if not snapshot.inventory:
            out["reason"] = "captured repository snapshot is missing"
            ledger.record_event("skitarii_patch_stage", out)
            return out

        expected_fingerprint = snapshot.fingerprint or _workspace_fingerprint(snapshot, root)
        expected_metadata = (
            snapshot.metadata_fingerprint or _git_metadata_fingerprint(root)
        )
        # Keep the full snapshot hash for audit only.  The caller-confirmed
        # baseline_fingerprint is populated below from the scoped CAS manifest,
        # so unrelated worktree activity is not part of the apply authority.
        out["baseline_snapshot_fingerprint"] = expected_fingerprint
        out["baseline_metadata_fingerprint"] = expected_metadata
        out["conflict_scope_version"] = 2
        checks = [
            check for check in (verdict.get("checks") or [])
            if isinstance(check, dict) and check.get("cmd")
        ]
        if len(checks) > MAX_VERIFY_CHECKS:
            out["reason"] = f"verification check count exceeds {MAX_VERIFY_CHECKS}"
            out["tests_pass_in_worktree"] = False
            ledger.record_event("skitarii_patch_stage", out)
            return out
        checks_file = run_dir / "work" / ".skitarii-verification-checks.json"
        checks_bytes = json.dumps(checks, ensure_ascii=False).encode("utf-8", errors="strict")
        out["checks_sha256"] = (
            _write_private_artifact(run_dir, checks_file, checks_bytes)
            if persist_artifacts else hashlib.sha256(checks_bytes).hexdigest()
        )

        verify_dir = Path(tempfile.mkdtemp(prefix="skitarii-verify-"))
        try:
            _materialize_snapshot(snapshot, verify_dir)
            applied = run_git(
                ["apply", "--binary", "--whitespace=nowarn", "-"],
                verify_dir, 120, patch_input=True,
            )
            out["applied_in_worktree"] = applied.returncode == 0
            if applied.returncode != 0:
                out["worktree_apply_stderr"] = (applied.stderr or applied.stdout or "")[:300]
                out["tests_pass_in_worktree"] = False
                out["reason"] = "patch does not apply to the captured baseline"
            else:
                authoritative_changed = _changed_paths_against_head(verify_dir)
                out["resource_bounds"]["changed_paths"] = authoritative_changed
                if not authoritative_changed:
                    raise SnapshotError("non-empty patch produced no filesystem change")
                runner_control = next(
                    (path for path in authoritative_changed if _is_runner_control_path(path)),
                    "",
                )
                if runner_control:
                    raise SnapshotError(
                        f"patch changes forbidden test-runner control code: {runner_control}",
                    )
                control_path = next(
                    (
                        path for path in authoritative_changed
                        if PurePosixPath(path).name in {
                            ".gitattributes", ".gitignore", ".gitmodules",
                        }
                    ),
                    "",
                )
                if control_path:
                    raise SnapshotError(
                        f"patches may not change Git ignore/submodule controls: {control_path}",
                    )
                protected_changed = set(authoritative_changed) & (
                    set(snapshot.external_assets) | set(snapshot.symlinks)
                )
                if protected_changed:
                    raise SnapshotError(
                        "patch touches an immutable large asset or symlink: "
                        + sorted(protected_changed)[0],
                    )
                for changed_path in authoritative_changed:
                    candidate_path = _materialize_path(verify_dir, changed_path)
                    if candidate_path.is_symlink():
                        raise SnapshotError(
                            f"patches may not create or modify symlinks: {changed_path}",
                        )
                    if _has_runner_control_config(changed_path, candidate_path):
                        raise SnapshotError(
                            f"patch changes forbidden pytest configuration: {changed_path}",
                        )
                    baseline_config = snapshot.get(changed_path)
                    if isinstance(baseline_config, str) and _runner_control_config_text(
                        changed_path, baseline_config,
                    ):
                        raise SnapshotError(
                            f"patch changes existing pytest configuration: {changed_path}",
                        )
                expected_post_content = _git_visible_content_fingerprint(
                    verify_dir,
                    allowed_large=snapshot.external_assets,
                    reject_ignored_nodes=True,
                    project_missing_approved=True,
                )
                expected_baseline_targets = _snapshot_content_fingerprint(
                    snapshot, paths=authoritative_changed,
                )
                expected_post_targets = _git_visible_content_fingerprint(
                    verify_dir, paths=authoritative_changed,
                    allowed_large=snapshot.external_assets,
                )
                out["expected_post_content_fingerprint"] = expected_post_content
                out["baseline_target_fingerprint"] = expected_baseline_targets
                out["patched_target_fingerprint"] = expected_post_targets
                out["baseline_fingerprint"] = _stage_conflict_fingerprint(out)
                if not out["baseline_fingerprint"]:
                    raise SnapshotError("could not bind the scoped patch conflict proof")
                if (
                    expected_conflict_fingerprint
                    and not secrets.compare_digest(
                        out["baseline_fingerprint"], expected_conflict_fingerprint,
                    )
                ):
                    out["tests_pass_in_worktree"] = False
                    out["reason"] = "scoped conflict proof changed before live apply"
                    ledger.record_event("skitarii_patch_stage", out)
                    return out
                passed, results, reason = _run_check_set(checks, verify_dir)
                out["tests_pass_in_worktree"] = passed
                out["verification_results"] = results
                if reason:
                    out["reason"] = reason
        finally:
            shutil.rmtree(verify_dir, ignore_errors=True)

        if out["tests_pass_in_worktree"] is not True:
            ledger.record_event("skitarii_patch_stage", out)
            return out

        should_autoapply = (
            os.environ.get("SKITARII_AUTOAPPLY") == "1"
            if autoapply is None else bool(autoapply)
        )
        should_autopublish = bool(
            should_autoapply and os.environ.get("SKITARII_AUTOPUBLISH") == "1"
        )
        out.update({
            "publication_required": should_autopublish,
            "publication_status": "not_required" if not should_autopublish else "preflight",
            "committed_to_main": False,
            "pushed_to_origin": False,
            "remote_contains_commit": False,
            "publication_attempts": 0,
        })
        if should_autopublish:
            guard = _publication_lock(root, timeout=1800)
            publication_wait = guard.__enter__()
            publication_guard = guard
            out["publication_transaction_lock_wait_seconds"] = publication_wait

        with _repo_lock(root) as lock_wait:
            out["lock_wait_seconds"] = lock_wait
            ledger_path = getattr(ledger, "path", None)
            if ledger_path:
                try:
                    from .ledger import TaskLedger

                    current_ledger = TaskLedger.load(Path(ledger_path))
                    current_status = str(current_ledger.to_dict().get("status") or "")
                    if current_ledger.cancel_requested() or current_status in {"cancelling", "cancelled"}:
                        out["applies_to_live"] = False
                        if should_autopublish:
                            out["publication_status"] = "cancelled"
                        out["reason"] = "run was cancelled before repository apply"
                        current_ledger.record_event("skitarii_apply_cancelled", {"status": current_status})
                        return out
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    out["applies_to_live"] = False
                    out["reason"] = f"could not confirm run cancellation state: {exc}"
                    return out
            live_metadata, live_targets = _stable_scoped_live_state(
                root, authoritative_changed,
                allowed_large=snapshot.external_assets,
            )
            out["live_metadata_fingerprint_before_apply"] = live_metadata
            out["live_target_fingerprint_before_apply"] = live_targets
            if live_metadata != expected_metadata:
                out["applies_to_live"] = False
                out["reason"] = "stale_baseline: live HEAD or index changed after mission dispatch"
                ledger.record_event("skitarii_patch_stage", out)
                return out
            if live_targets != expected_baseline_targets:
                out["applies_to_live"] = False
                out["reason"] = "stale_baseline: patch target changed after mission dispatch"
                ledger.record_event("skitarii_patch_stage", out)
                return out
            pre_apply_metadata = live_metadata

            if should_autopublish:
                try:
                    branch, base_head = _publication_branch_and_head(root)
                    remote_base = _remote_main_sha(root)
                    if remote_base != base_head:
                        raise SnapshotError(
                            "autonomous publication requires local main to equal origin/main",
                        )
                    if not _publication_targets_clean(
                        root, base_head, authoritative_changed, expected_baseline_targets,
                        allowed_large=snapshot.external_assets,
                    ):
                        raise SnapshotError(
                            "autonomous publication refuses a dirty patch target",
                        )
                    if not _publication_attributes_safe(root, authoritative_changed):
                        raise SnapshotError(
                            "autonomous publication refuses target paths with Git clean attributes",
                        )
                    out.update({
                        "publication_branch": branch,
                        "publication_remote": "origin",
                        "publication_base_head": base_head,
                        "publication_remote_base": remote_base,
                        "remote_main_sha": remote_base,
                        "publication_new_paths": _publication_new_paths(
                            root, base_head, authoritative_changed,
                        ),
                        "publication_status": "preflight_passed",
                    })
                except (OSError, UnicodeError, subprocess.SubprocessError, SnapshotError) as exc:
                    out["applies_to_live"] = False
                    out["publication_status"] = "blocked"
                    out["publication_error"] = str(exc)[:200]
                    out["reason"] = f"publication preflight blocked live mutation: {str(exc)[:180]}"
                    ledger.record_event("skitarii_patch_stage", out)
                    return out

            ignored = subprocess.run(
                ["git", "check-ignore", "-z", "--stdin"],
                cwd=root,
                input="".join(f"{path}\0" for path in authoritative_changed),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if ignored.returncode not in {0, 1}:
                out["reason"] = "could not validate live Git ignore policy"
                out["apply_stderr"] = (ignored.stderr or ignored.stdout or "")[:300]
                ledger.record_event("skitarii_patch_stage", out)
                return out
            if ignored.returncode == 0 and ignored.stdout:
                ignored_path = ignored.stdout.split("\0", 1)[0]
                out["reason"] = f"patch target is ignored by the live repository: {ignored_path}"
                out["applies_to_live"] = False
                ledger.record_event("skitarii_patch_stage", out)
                return out

            checked = run_git(
                ["apply", "--check", "--binary", "--whitespace=nowarn", "-"],
                root, 60, patch_input=True,
            )
            if checked.returncode != 0:
                out["applies_to_live"] = False
                out["apply_stderr"] = (checked.stderr or checked.stdout or "")[:300]
                out["reason"] = "verified patch no longer applies to the live baseline"
                ledger.record_event("skitarii_patch_stage", out)
                return out
            confirmed_metadata, confirmed_targets = _stable_scoped_live_state(
                root, authoritative_changed,
                allowed_large=snapshot.external_assets,
            )
            if (
                confirmed_metadata != pre_apply_metadata
                or confirmed_targets != expected_baseline_targets
            ):
                out["applies_to_live"] = False
                out["reason"] = "stale_baseline: patch target or Git metadata changed during apply validation"
                ledger.record_event("skitarii_patch_stage", out)
                return out
            out["applies_to_live"] = True

            if not should_autoapply:
                ledger.record_event("skitarii_patch_stage", out)
                return out

            if should_autopublish:
                out["publication_status"] = "apply_intent"
                try:
                    with _run_apply_lock(run_dir, timeout=30):
                        final_ledger_path = getattr(ledger, "path", None)
                        if final_ledger_path:
                            from .ledger import TaskLedger

                            final_ledger = TaskLedger.load(Path(final_ledger_path))
                            final_status = str(final_ledger.to_dict().get("status") or "")
                            if final_ledger.cancel_requested() or final_status in {
                                "cancelling", "cancelled",
                            }:
                                out["applies_to_live"] = False
                                out["publication_status"] = "cancelled"
                                out["reason"] = "run was cancelled before durable repository mutation"
                                return out
                        _persist_publication_checkpoint(
                            run_dir, ledger, out, phase="apply_intent",
                            reason="verified patch is queued for live apply and publication",
                        )
                except Exception as checkpoint_exc:  # noqa: BLE001 - fail before mutation.
                    out["applies_to_live"] = False
                    out["publication_status"] = "blocked"
                    out["publication_error"] = (
                        f"{type(checkpoint_exc).__name__}: {str(checkpoint_exc)[:160]}"
                    )
                    out["reason"] = "could not persist live-apply intent"
                    return out

            live_apply = run_git(
                ["apply", "--binary", "--whitespace=nowarn", "-"],
                root, 60, patch_input=True,
            )
            out["applied_to_live"] = live_apply.returncode == 0
            if live_apply.returncode != 0:
                out["reason"] = "verified patch could not be applied to the live repository"
                out["apply_stderr"] = (live_apply.stderr or live_apply.stdout or "")[:300]
                ledger.record_event("skitarii_patch_stage", out)
                return out
            if should_autopublish:
                out["publication_status"] = "applied_unverified"
                try:
                    _persist_publication_checkpoint(
                        run_dir, ledger, out, phase="applied_unverified",
                        reason="live patch was applied; durable post-apply verification is running",
                    )
                except Exception as checkpoint_exc:  # noqa: BLE001 - rollback while guarded.
                    out["post_apply_tests_passed"] = False
                    out["reason"] = (
                        "could not checkpoint applied patch: "
                        f"{type(checkpoint_exc).__name__}: {str(checkpoint_exc)[:160]}"
                    )
                    rollback_live_if_safe()
                    return out

            post_snapshot = _full_repo_snapshot(
                max_files=10_000,
                max_total_bytes=100_000_000,
                max_file_bytes=20_000_000,
            )
            post_fingerprint = post_snapshot.fingerprint
            out["post_apply_fingerprint"] = post_fingerprint
            post_content = _snapshot_content_fingerprint(post_snapshot)
            post_targets = _snapshot_content_fingerprint(
                post_snapshot, paths=authoritative_changed,
            )
            post_metadata, live_post_targets = _stable_scoped_live_state(
                root, authoritative_changed,
                allowed_large=snapshot.external_assets,
            )
            out["post_apply_content_fingerprint"] = post_content
            out["post_apply_target_fingerprint"] = post_targets
            out["post_apply_metadata_fingerprint"] = post_metadata
            current_targets = ""
            current_metadata = ""
            try:
                if (
                    post_targets != expected_post_targets
                    or live_post_targets != expected_post_targets
                    or post_metadata != pre_apply_metadata
                ):
                    out["post_apply_tests_passed"] = False
                    out["reason"] = "live patch targets or Git metadata diverged from verification"
                else:
                    post_dir = Path(tempfile.mkdtemp(prefix="skitarii-post-apply-"))
                    try:
                        _materialize_snapshot(post_snapshot, post_dir)
                        post_passed, post_results, post_reason = _run_check_set(checks, post_dir)
                        out["post_apply_tests_passed"] = post_passed
                        out["post_apply_verification_results"] = post_results
                        if post_reason:
                            out["reason"] = post_reason
                    finally:
                        shutil.rmtree(post_dir, ignore_errors=True)
                current_metadata, current_targets = _stable_scoped_live_state(
                    root, authoritative_changed,
                    allowed_large=snapshot.external_assets,
                )
            except Exception as exc:
                out["post_apply_tests_passed"] = False
                out["post_apply_error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
                out["reason"] = "post-apply verification infrastructure failed"
                try:
                    current_metadata, current_targets = _stable_scoped_live_state(
                        root, authoritative_changed,
                        allowed_large=snapshot.external_assets,
                    )
                except Exception as fingerprint_exc:
                    out["rollback_failed"] = True
                    out["rollback_error"] = (
                        "could not prove the live patch-target state: "
                        f"{type(fingerprint_exc).__name__}: {str(fingerprint_exc)[:180]}"
                    )
            if (
                current_targets != expected_post_targets
                or current_metadata != pre_apply_metadata
            ):
                out["post_apply_tests_passed"] = False
                out["reason"] = "live patch targets or Git metadata changed during post-apply verification"

            if out["post_apply_tests_passed"] is not True:
                rollback_live_if_safe()
            elif should_autopublish:
                out["publication_status"] = "publishing"
                try:
                    _persist_publication_checkpoint(
                        run_dir, ledger, out, phase="publishing",
                        reason="live verification passed; preparing commit and origin/main push",
                    )
                except Exception as checkpoint_exc:  # noqa: BLE001 - mutation needs durable recovery.
                    out["post_apply_tests_passed"] = False
                    out["reason"] = (
                        "publication checkpoint failed before commit: "
                        f"{type(checkpoint_exc).__name__}: {str(checkpoint_exc)[:160]}"
                    )
                    rollback_live_if_safe()
        if (
            should_autopublish
            and out.get("applied_to_live") is True
            and out.get("post_apply_tests_passed") is True
            and not out.get("rolled_back")
        ):
            out = _publish_verified_patch(root, out, run_dir, ledger)
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:300]
        if not out.get("applied_to_live"):
            if out.get("applies_to_live") is not True:
                out["applies_to_live"] = False
            if not out.get("reason"):
                out["reason"] = str(exc)[:300]
            if out.get("tests_pass_in_worktree") is not True:
                out["tests_pass_in_worktree"] = False
        if out.get("applied_to_live") and out.get("post_apply_tests_passed") is not True:
            out["reason"] = "autoapply transaction failed; live repository requires inspection"
            rollback_live_if_safe()
    finally:
        if publication_guard is not None:
            publication_guard.__exit__(None, None, None)
    ledger.record_event("skitarii_patch_stage", out)
    return out


def _resume_interrupted_publication_apply(
    run_dir: Path,
    ledger: Any,
    stage: dict[str, Any],
) -> dict[str, Any]:
    """Recover a crash between live apply intent and durable post-verification."""
    changed_paths = _stage_conflict_paths(stage)
    if not changed_paths or not _stage_artifacts_match(run_dir, stage):
        stage["publication_status"] = "conflict"
        stage["publication_error"] = "interrupted apply artifacts or conflict proof are invalid"
        _persist_publication_checkpoint(
            run_dir, ledger, stage, phase="blocked",
            reason="interrupted live apply cannot be recovered safely",
        )
        return stage
    patch_path = (run_dir / "work" / "skitarii.patch").resolve(strict=True)
    checks_path = (run_dir / "work" / ".skitarii-verification-checks.json").resolve(strict=True)
    try:
        patch_bytes = patch_path.read_bytes()
        checks_bytes = checks_path.read_bytes()
        patch_text = patch_bytes.decode("utf-8", errors="strict")
        checks = json.loads(checks_bytes.decode("utf-8", errors="strict"))
        if not isinstance(checks, list):
            raise SnapshotError("verification checks are not a list")
        root = REPO_ROOT.resolve()
        baseline_metadata = str(stage.get("baseline_metadata_fingerprint") or "")
        baseline_targets = str(stage.get("baseline_target_fingerprint") or "")
        patched_targets = str(stage.get("patched_target_fingerprint") or "")
        base_head = _git_object_id(stage.get("publication_base_head"))
        with _publication_lock(root, timeout=1800):
            with _repo_lock(root, timeout=1800):
                _branch, head = _publication_branch_and_head(root)
                if head != base_head:
                    raise SnapshotError("local main changed during interrupted live apply")
                live_metadata, live_targets = _stable_scoped_live_state(root, changed_paths)
                if live_metadata != baseline_metadata:
                    raise SnapshotError("Git index changed during interrupted live apply")
                if live_targets == patched_targets:
                    reverse_check = subprocess.run(
                        ["git", "apply", "--reverse", "--check", "--binary",
                         "--whitespace=nowarn", "-"],
                        cwd=root, input=patch_text, text=True, capture_output=True, timeout=60,
                    )
                    if reverse_check.returncode != 0:
                        raise SnapshotError("interrupted live patch cannot be reversed safely")
                    reversed_patch = subprocess.run(
                        ["git", "apply", "--reverse", "--binary", "--whitespace=nowarn", "-"],
                        cwd=root, input=patch_text, text=True, capture_output=True, timeout=60,
                    )
                    if reversed_patch.returncode != 0:
                        raise SnapshotError("interrupted live patch rollback failed")
                    restored_metadata, restored_targets = _stable_scoped_live_state(
                        root, changed_paths,
                    )
                    if restored_metadata != baseline_metadata or restored_targets != baseline_targets:
                        raise SnapshotError("interrupted live patch rollback could not be proven")
                elif live_targets != baseline_targets:
                    raise SnapshotError("patch targets changed during interrupted live apply")
                stage.update({
                    "applied_to_live": False,
                    "post_apply_tests_passed": None,
                    "rolled_back": True,
                    "publication_status": "apply_intent",
                })
                _persist_publication_checkpoint(
                    run_dir, ledger, stage, phase="apply_intent",
                    reason="interrupted apply was restored to its verified baseline",
                )
            snapshot = _full_repo_snapshot()
            if (
                snapshot.metadata_fingerprint != baseline_metadata
                or _snapshot_content_fingerprint(snapshot, paths=changed_paths) != baseline_targets
            ):
                raise SnapshotError("baseline changed while interrupted apply was being recovered")
            verdict = {
                "accepted": True,
                "checks": checks,
                "patch_bundle": {"unified_diff": patch_text},
            }
            recovered = _verify_and_stage_patch(
                verdict,
                run_dir,
                ledger,
                snapshot,
                autoapply=True,
                persist_artifacts=False,
                expected_conflict_fingerprint=str(stage.get("baseline_fingerprint") or ""),
            )
            if not isinstance(recovered, dict):
                raise SnapshotError("interrupted apply recovery returned no patch stage")
            recovered_status = str(recovered.get("publication_status") or "")
            if (
                not _patch_stage_passed(recovered, require_applied=True)
                or recovered_status in {"blocked", "conflict"}
            ):
                recovered["publication_status"] = "conflict"
                reason = str(
                    recovered.get("reason") or recovered.get("error")
                    or "interrupted apply could not pass frozen verification"
                )
                recovered["publication_error"] = reason[:200]
                _persist_publication_checkpoint(
                    run_dir, ledger, recovered, phase="blocked",
                    reason=f"interrupted apply recovery stopped safely: {reason[:170]}",
                )
            return recovered
    except (OSError, UnicodeError, json.JSONDecodeError, subprocess.SubprocessError,
            SnapshotError, TimeoutError) as exc:
        stage.update({
            "publication_status": "conflict",
            "publication_error": str(exc)[:200],
        })
        _persist_publication_checkpoint(
            run_dir, ledger, stage, phase="blocked",
            reason=f"interrupted live apply stopped safely: {str(exc)[:180]}",
        )
        return stage


def _complete_applied_skitarii_result(
    run_dir: Path,
    ledger: Any,
    previous_result: dict[str, Any],
    stage: dict[str, Any],
) -> dict[str, Any]:
    published = stage.get("publication_required") is True
    summary = (
        "Verified patch was applied, rechecked, committed only on mission paths, "
        "and confirmed in origin/main."
        if published else
        "Verified patch applied to the live repository and rechecked successfully."
    )
    updated = dict(previous_result)
    updated.update({
        "ok": True,
        "phase": "completed",
        "status": "completed",
        "summary": summary,
        "patch_stage": stage,
        "ready_to_apply": False,
        "protocol_finalize_pending": False,
        "protocol_finalize_error": "",
        "next_action": {},
    })
    pending = dict(updated)
    pending.update({
        "ok": False,
        "phase": "protocol_finalize_pending",
        "status": "protocol_finalize_pending",
        "protocol_finalize_pending": True,
        "protocol_finalize_error": "",
        "next_action": {
            "kind": "reconcile_mission_protocol",
            "method": "POST",
            "endpoint": "POST /runs/{task_id}/apply_patch",
            "body": {
                "expected_repository_fingerprint": str(stage.get("baseline_fingerprint") or ""),
                "expected_patch_sha256": str(stage.get("patch_sha256") or ""),
                "expected_checks_sha256": str(stage.get("checks_sha256") or ""),
                "confirm_apply": True,
            },
            "reason": "repository publication succeeded; mission protocol finalization is in progress",
        },
    })
    ledger.data["result"] = pending
    ledger.force_status(
        "protocol_finalize_pending",
        reason="mission protocol finalization in progress",
    )
    try:
        _finalize_linked_completion(run_dir, ledger, updated)
    except Exception as exc:  # noqa: BLE001 - repository publication is already durable.
        error = f"{type(exc).__name__}: {str(exc)[:240]}"
        pending["protocol_finalize_error"] = error
        pending["next_action"]["reason"] = (
            "repository publication succeeded but mission protocol finalization must be retried"
        )
        ledger.data["result"] = pending
        ledger.force_status(
            "protocol_finalize_pending",
            reason="mission protocol finalization pending",
        )
        ledger.record_event("skitarii_mission_finalize_error", {"error": error})
        return {**pending, "task_id": str(ledger.to_dict().get("task_id") or run_dir.name)}
    ledger.data["result"] = updated
    ledger.force_status("completed", reason="verified publication and mission protocol completed")
    return {**updated, "task_id": str(ledger.to_dict().get("task_id") or run_dir.name)}


def _apply_staged_patch_locked(
    run_dir: Path,
    ledger: Any,
    expected_fingerprint: str,
    *,
    expected_patch_sha256: str = "",
    expected_checks_sha256: str = "",
) -> dict[str, Any]:
    """Controlled apply action for a previously verified ready-to-apply result."""
    ledger_data = ledger.to_dict() if hasattr(ledger, "to_dict") else {}
    result = ledger_data.get("result", {})
    if not isinstance(result, dict) or str(result.get("final_step") or "") != "skitarii":
        return {"ok": False, "status": "blocked", "error": "run has no Skitarii result"}
    result_phase = str(result.get("phase") or result.get("status") or "")
    publication_phases = {
        "apply_intent", "applied_unverified", "publishing", "push_pending",
        "protocol_finalize_pending",
    }
    if bool(ledger_data.get("cancel_requested")) or str(ledger_data.get("status") or "") in {
        "cancelling", "cancelled",
    }:
        if result_phase not in publication_phases:
            return {"ok": False, "status": "cancelled", "error": "run was cancelled before apply"}
        cancellation_stage = (
            result.get("patch_stage") if isinstance(result.get("patch_stage"), dict) else {}
        )
        if (
            expected_fingerprint != str(cancellation_stage.get("baseline_fingerprint") or "")
            or expected_patch_sha256 != str(cancellation_stage.get("patch_sha256") or "")
            or expected_checks_sha256 != str(cancellation_stage.get("checks_sha256") or "")
            or not _stage_artifacts_match(run_dir, cancellation_stage)
        ):
            return {
                "ok": False,
                "status": str(ledger_data.get("status") or "cancelling"),
                "error": "publication recovery confirmation mismatch",
            }
        ledger.data.pop("cancel_requested", None)
        ledger.data.pop("cancel_reason", None)
        ledger.force_status(result_phase, reason="publication is already durable and cannot be cancelled")
        ledger.record_event(
            "cancel_rejected_after_repository_mutation",
            {"phase": result_phase},
        )
    if result.get("protocol_finalize_pending"):
        stage = result.get("patch_stage") if isinstance(result.get("patch_stage"), dict) else {}
        if (
            expected_fingerprint != str(stage.get("baseline_fingerprint") or "")
            or expected_patch_sha256 != str(stage.get("patch_sha256") or "")
            or expected_checks_sha256 != str(stage.get("checks_sha256") or "")
            or not _completion_stage_proof_valid(stage)
            or not _stage_artifacts_match(run_dir, stage)
        ):
            return {"ok": False, "status": "blocked", "error": "reconciliation confirmation mismatch"}
        reconciled = dict(result)
        reconciled.update({
            "ok": True,
            "phase": "completed",
            "status": "completed",
            "protocol_finalize_pending": False,
            "protocol_finalize_error": "",
            "next_action": {},
        })
        try:
            _finalize_linked_completion(run_dir, ledger, reconciled)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {str(exc)[:240]}"
            pending = dict(result)
            pending.update({
                "ok": False,
                "phase": "protocol_finalize_pending",
                "status": "protocol_finalize_pending",
                "protocol_finalize_pending": True,
                "protocol_finalize_error": error,
            })
            ledger.data["result"] = pending
            ledger.force_status(
                "protocol_finalize_pending",
                reason="mission protocol reconciliation still pending",
            )
            ledger.record_event("skitarii_mission_finalize_error", {"error": error})
            return {
                **pending,
                "task_id": str(ledger_data.get("task_id") or run_dir.name),
                "error": f"mission protocol reconciliation failed: {exc}",
            }
        ledger.data["result"] = reconciled
        ledger.force_status("completed", reason="mission protocol reconciliation completed")
        return {**reconciled, "task_id": str(ledger_data.get("task_id") or run_dir.name)}
    if result_phase in {"apply_intent", "applied_unverified"}:
        stage = result.get("patch_stage") if isinstance(result.get("patch_stage"), dict) else {}
        if (
            expected_fingerprint != str(stage.get("baseline_fingerprint") or "")
            or expected_patch_sha256 != str(stage.get("patch_sha256") or "")
            or expected_checks_sha256 != str(stage.get("checks_sha256") or "")
            or not _stage_conflict_paths(stage)
            or not _stage_artifacts_match(run_dir, stage)
        ):
            return {"ok": False, "status": "blocked", "error": "apply recovery confirmation mismatch"}
        recovered_stage = _resume_interrupted_publication_apply(run_dir, ledger, stage)
        if not _published_stage_proof_valid(recovered_stage, verify_remote=True):
            latest = ledger.to_dict().get("result", {})
            if isinstance(latest, dict):
                return {**latest, "task_id": str(ledger_data.get("task_id") or run_dir.name)}
            return {"ok": False, "status": "blocked", "error": "interrupted apply recovery failed"}
        return _complete_applied_skitarii_result(
            run_dir, ledger, result, recovered_stage,
        )
    if result_phase in {
        "publishing", "push_pending",
    }:
        stage = result.get("patch_stage") if isinstance(result.get("patch_stage"), dict) else {}
        if (
            expected_fingerprint != str(stage.get("baseline_fingerprint") or "")
            or expected_patch_sha256 != str(stage.get("patch_sha256") or "")
            or expected_checks_sha256 != str(stage.get("checks_sha256") or "")
            or not _applied_stage_proof_valid(stage)
            or not _stage_artifacts_match(run_dir, stage)
        ):
            return {"ok": False, "status": "blocked", "error": "publication confirmation mismatch"}
        published_stage = _publish_verified_patch(REPO_ROOT.resolve(), stage, run_dir, ledger)
        if not _published_stage_proof_valid(published_stage, verify_remote=True):
            latest = ledger.to_dict().get("result", {})
            if isinstance(latest, dict):
                return {**latest, "task_id": str(ledger_data.get("task_id") or run_dir.name)}
            return {"ok": False, "status": "push_pending", "error": "publication remains pending"}
        return _complete_applied_skitarii_result(
            run_dir, ledger, result, published_stage,
        )
    if not result.get("ready_to_apply"):
        return {"ok": False, "status": str(result.get("status") or "blocked"),
                "error": "run is not ready to apply"}
    stage = result.get("patch_stage") if isinstance(result.get("patch_stage"), dict) else {}
    recorded = str(stage.get("baseline_fingerprint") or "")
    if not expected_fingerprint or expected_fingerprint != recorded:
        return {"ok": False, "status": "blocked", "error": "repository fingerprint confirmation mismatch"}
    recorded_patch_sha = str(stage.get("patch_sha256") or "")
    recorded_checks_sha = str(stage.get("checks_sha256") or "")
    if not expected_patch_sha256 or expected_patch_sha256 != recorded_patch_sha:
        return {"ok": False, "status": "blocked", "error": "patch digest confirmation mismatch"}
    if not expected_checks_sha256 or expected_checks_sha256 != recorded_checks_sha:
        return {"ok": False, "status": "blocked", "error": "verification digest confirmation mismatch"}
    changed_paths = _stage_conflict_paths(stage)
    recorded_metadata = str(stage.get("baseline_metadata_fingerprint") or "")
    recorded_targets = str(stage.get("baseline_target_fingerprint") or "")
    recorded_post_targets = str(stage.get("patched_target_fingerprint") or "")
    if not changed_paths:
        return {"ok": False, "status": "blocked", "error": "patch conflict proof is missing or invalid"}
    raw_patch_path = run_dir / "work" / "skitarii.patch"
    patch_path = raw_patch_path.resolve()
    root = run_dir.resolve()
    if raw_patch_path.is_symlink() or root not in patch_path.parents or not patch_path.is_file():
        return {"ok": False, "status": "blocked", "error": "staged patch artifact is missing"}
    raw_checks_path = run_dir / "work" / ".skitarii-verification-checks.json"
    checks_path = raw_checks_path.resolve()
    if raw_checks_path.is_symlink() or root not in checks_path.parents or not checks_path.is_file():
        return {"ok": False, "status": "blocked", "error": "verification checks are unavailable"}
    try:
        patch_bytes = patch_path.read_bytes()
        checks_bytes = checks_path.read_bytes()
    except OSError as exc:
        return {"ok": False, "status": "blocked", "error": f"verified artifacts are unavailable: {exc}"}
    if hashlib.sha256(patch_bytes).hexdigest() != recorded_patch_sha:
        return {"ok": False, "status": "blocked", "error": "staged patch artifact changed after verification"}
    if hashlib.sha256(checks_bytes).hexdigest() != recorded_checks_sha:
        return {"ok": False, "status": "blocked", "error": "verification checks changed after verification"}
    try:
        patch_text = patch_bytes.decode("utf-8", errors="strict")
        checks = json.loads(checks_bytes.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "status": "blocked", "error": f"verification checks are unavailable: {exc}"}
    if not isinstance(checks, list):
        return {"ok": False, "status": "blocked", "error": "verification checks are corrupt"}
    live_root = REPO_ROOT.resolve()
    try:
        live_metadata, live_targets = _stable_scoped_live_state(
            live_root, changed_paths,
        )
        if live_metadata != recorded_metadata:
            return {"ok": False, "status": "stale_baseline",
                    "error": "live HEAD or index changed after the patch was staged"}
        if live_targets != recorded_targets:
            return {"ok": False, "status": "stale_baseline",
                    "error": "patch target changed after the patch was staged"}
        snapshot = _full_repo_snapshot()
    except (OSError, subprocess.SubprocessError, SnapshotError) as exc:
        return {"ok": False, "status": "blocked", "error": f"live repository snapshot failed: {exc}"}
    if (
        snapshot.metadata_fingerprint != recorded_metadata
        or _snapshot_content_fingerprint(snapshot, paths=changed_paths) != recorded_targets
    ):
        return {"ok": False, "status": "stale_baseline",
                "error": "patch target or Git metadata changed while apply was prepared"}
    verdict = {
        "accepted": True,
        "checks": checks,
        "patch_bundle": {"unified_diff": patch_text},
    }
    new_stage = _verify_and_stage_patch(
        verdict, run_dir, ledger, snapshot,
        autoapply=True,
        persist_artifacts=False,
        expected_conflict_fingerprint=recorded,
    )
    if not _patch_stage_passed(new_stage, require_applied=True):
        return {
            "ok": False, "status": "blocked",
            "error": str((new_stage or {}).get("reason") or (new_stage or {}).get("error") or "apply failed"),
            "patch_stage": new_stage or {},
        }
    if (
        str((new_stage or {}).get("baseline_fingerprint") or "") != recorded
        or str((new_stage or {}).get("patch_sha256") or "") != recorded_patch_sha
        or str((new_stage or {}).get("checks_sha256") or "") != recorded_checks_sha
        or str((new_stage or {}).get("patched_target_fingerprint") or "")
        != recorded_post_targets
    ):
        return {
            "ok": False,
            "status": "blocked",
            "error": "scoped conflict proof changed during apply",
        }
    assert isinstance(new_stage, dict)
    if (
        new_stage.get("publication_required") is True
        and not _published_stage_proof_valid(new_stage, verify_remote=True)
    ):
        latest = ledger.to_dict().get("result", {})
        if isinstance(latest, dict):
            return {**latest, "task_id": str(ledger.to_dict().get("task_id") or run_dir.name)}
        return {"ok": False, "status": "push_pending", "error": "publication remains pending"}
    return _complete_applied_skitarii_result(run_dir, ledger, result, new_stage)


def apply_staged_patch(
    run_dir: Path,
    ledger: Any,
    expected_fingerprint: str,
    *,
    expected_patch_sha256: str = "",
    expected_checks_sha256: str = "",
) -> dict[str, Any]:
    """Serialize apply per run and reload durable state before compare-and-set."""
    from .ledger import TaskLedger

    resolved_run = run_dir.resolve()
    with _run_apply_lock(resolved_run, timeout=1800):
        ledger_path = resolved_run / "task_ledger.json"
        fresh_ledger = TaskLedger.load(ledger_path)
        return _apply_staged_patch_locked(
            resolved_run,
            fresh_ledger,
            expected_fingerprint,
            expected_patch_sha256=expected_patch_sha256,
            expected_checks_sha256=expected_checks_sha256,
        )


def begin_run_cancellation(run_dir: Path, reason: str = "") -> dict[str, Any]:
    """Atomically order cancellation before, or reject it after, live mutation WAL."""
    from .ledger import TaskLedger

    resolved = run_dir.resolve()
    with _run_apply_lock(resolved, timeout=30):
        ledger = TaskLedger.load(resolved / "task_ledger.json")
        payload = ledger.to_dict()
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        phase = str(result.get("phase") or result.get("status") or "")
        if phase in {
            "apply_intent", "applied_unverified", "publishing",
            "push_pending", "protocol_finalize_pending",
        }:
            return {
                "ok": False,
                "status": phase,
                "error": "run cannot be cancelled after durable repository mutation began",
                "ledger": payload,
            }
        if str(payload.get("status") or "") in {
            "completed", "failed", "blocked", "cancelled", "corrupt",
        }:
            return {
                "ok": False,
                "status": str(payload.get("status") or ""),
                "error": "run is already terminal",
                "ledger": payload,
            }
        if not ledger.request_cancel(reason):
            return {
                "ok": False,
                "status": str(ledger.to_dict().get("status") or ""),
                "error": "run is already terminal",
                "ledger": ledger.to_dict(),
            }
        return {"ok": True, "status": "cancelling", "ledger": ledger.to_dict()}


def should_handle(run_dir: Path) -> bool:
    if os.environ.get("SKITARII_ENABLED", "1") != "1":
        return False
    ref = _read_json(run_dir / "mission_ref.json")
    governor = str(ref.get("assigned_governor") or "")
    if governor and governor != "Ceraxia":
        return False
    # a code mission has a code contract in the run dir
    contract = _read_json(run_dir / "contract.json")
    kind = str(contract.get("kind") or "")
    if governor == "Ceraxia" or "code" in kind.lower():
        return bool(str(contract.get("goal") or "").strip())
    return False


def _mission_dir(run_dir: Path) -> Path | None:
    ref = _read_json(run_dir / "mission_ref.json")
    mission_id = ref.get("mission_id")
    mission_dir = ref.get("mission_dir")
    if not isinstance(mission_id, str) or not isinstance(mission_dir, str):
        return None
    try:
        return _bound_mission_directory(mission_dir, mission_id)
    except CeraxiaDirectiveError:
        return None


def _finalize_linked_completion(run_dir: Path, ledger: Any, result: dict[str, Any]) -> None:
    """Write a protocol-complete deterministic Ceraxia/Skitarii acceptance trail."""
    stage = result.get("patch_stage") if isinstance(result.get("patch_stage"), dict) else {}
    if stage.get("publication_required") is True and not _published_stage_proof_valid(
        stage, verify_remote=True,
    ):
        raise RuntimeError("origin/main publication proof is missing or no longer valid")
    mission_dir = _mission_dir(run_dir)
    if not mission_dir:
        raise RuntimeError("native run is missing mission_ref.json")
    if not mission_dir.exists():
        raise RuntimeError(f"linked mission directory does not exist: {mission_dir}")
    from . import mission_control as mc

    mission = _read_json(mission_dir / "mission.json")
    mission_id = str(mission.get("mission_id") or mission_dir.name)
    ledger_data = ledger.to_dict() if hasattr(ledger, "to_dict") else {}
    report = mc.governor_report(
        mission_id,
        governor=str(ledger_data.get("governor") or mission.get("assigned_governor") or "Ceraxia"),
        status="ready",
        summary=str(result.get("summary") or "Verified code patch applied successfully."),
        deliverables=[str(item) for item in (result.get("artifacts") or [])],
        quality_review={
            "passed": True,
            "checks": [
                {"name": "skitarii_acceptance", "ok": bool(result.get("ok"))},
                {"name": "repository_apply", "ok": str(result.get("status") or "") == "completed"},
                {
                    "name": "origin_main_publication",
                    "ok": bool(
                        not isinstance(result.get("patch_stage"), dict)
                        or result.get("patch_stage", {}).get("publication_required") is not True
                        or result.get("patch_stage", {}).get("publication_status") == "pushed"
                    ),
                },
            ],
            "final_manifest_summary": {},
        },
        revision_plan={"required": False, "steps": []},
        user_facing_answer=str(result.get("summary") or "Verified code patch applied successfully."),
    )
    review = mc.acceptance_review(
        mission_id,
        accepted=True,
        reason=(
            "Skitarii private verification, isolated host recheck, transactional "
            "repository apply, and required origin/main publication all passed."
        ),
        required_revision={},
        escalate_to_user=False,
    )
    mc.validate_protocol_payload(report, expected_type="governor_report")
    mc.validate_protocol_payload(review, expected_type="acceptance_review")
    mc._write_json(mission_dir / "governor_report.json", report)
    mc._write_json(mc._next_numbered_path(mission_dir / "governor_reports", "governor_report"), report)
    mc._write_json(mission_dir / "acceptance_review.json", review)
    mc._write_json(
        mc._next_numbered_path(mission_dir / "acceptance_reviews", "acceptance_review"), review,
    )
    final = mc.final_response(
        mission_id,
        "completed",
        str(result.get("summary") or "Verified code patch applied successfully."),
        artifacts=[str(item) for item in (result.get("artifacts") or [])],
    )
    mc.validate_protocol_payload(final, expected_type="final_response")
    mc._write_json(mission_dir / "final_response.json", final)
    mc.record_mission_state(
        mission_dir, "completed", run_status="completed", phase="completed",
    )
    mc.append_progress_event(
        mission_dir / "progress_events.jsonl",
        mc.progress_event(
            mission_id, "Ceraxia", "governor", "reviewing", "done",
            "Бригадир передал верифицированный отчёт", str(report.get("summary") or "")[:400],
        ),
    )
    mc.append_progress_event(
        mission_dir / "progress_events.jsonl",
        mc.progress_event(
            mission_id, "Warmaster", "commander", "completed", "done",
            "Финал принят", str(review.get("reason") or "")[:400],
        ),
    )
    ledger.record_event("skitarii_protocol_completion_recorded", {"mission_id": mission_id})


def _finalize_linked_blocked(
    run_dir: Path,
    ledger: Any,
    summary: str,
    *,
    phase: str = "blocked",
    artifacts: list[str] | None = None,
    next_action: dict[str, Any] | None = None,
    needs_user: bool = False,
    question: str = "",
) -> None:
    mission_dir = _mission_dir(run_dir)
    if not mission_dir:
        raise RuntimeError("native run is missing mission_ref.json")
    if not mission_dir.exists():
        raise RuntimeError(f"linked mission directory does not exist: {mission_dir}")
    from . import mission_control as mc

    mission_id = str(_read_json(mission_dir / "mission.json").get("mission_id") or mission_dir.name)
    # Result phases are deliberately more specific than the public progress
    # protocol.  Keep that diagnostic value in the durable result/state, but
    # never leak it into progress_event.phase where only PROGRESS_PHASES are
    # valid.  Building the event before any writes also prevents a validation
    # error from leaving a partially finalized mission.
    terminal_event = mc.progress_event(
        mission_id,
        "Ceraxia",
        "governor",
        "blocked",
        "blocked",
        "Варбанда Skitarii остановила код-миссию",
        (question or summary)[:400],
    )
    final = mc.final_response(mission_id, "blocked", summary, artifacts=artifacts or [])
    final["phase"] = phase
    final["needs_user"] = needs_user
    if question:
        final["question"] = question
    if next_action:
        final["next_action"] = next_action
    mc._write_json(mission_dir / "final_response.json", final)
    mc.record_mission_state(
        mission_dir,
        "blocked",
        run_status="blocked",
        phase=phase,
        needs_user=needs_user,
    )
    mc.append_progress_event(
        mission_dir / "progress_events.jsonl",
        terminal_event,
    )
    ledger.record_event("skitarii_protocol_blocked_recorded", {"mission_id": mission_id, "phase": phase})


def _finalize_linked_revision(
    run_dir: Path,
    ledger: Any,
    summary: str,
    *,
    artifacts: list[str] | None = None,
    findings: list[dict[str, Any]] | None = None,
    next_action: dict[str, Any] | None = None,
) -> None:
    """Return an exhausted worker approach to Ceraxia without killing the mission."""

    mission_dir = _mission_dir(run_dir)
    if not mission_dir or not mission_dir.exists():
        raise RuntimeError("native run is missing its linked mission directory")
    from . import mission_control as mc

    mission_id = str(
        _read_json(mission_dir / "mission.json").get("mission_id") or mission_dir.name
    )
    actionable = [dict(item) for item in findings or [] if isinstance(item, dict)]
    remediation = str(
        (next_action or {}).get("remediation")
        or (actionable[0].get("remediation") if actionable else "")
        or "Ceraxia must select another repair approach and redispatch Skitarii."
    )
    report = mc.governor_report(
        mission_id,
        governor="Ceraxia",
        status="needs_revision",
        summary=summary,
        deliverables=artifacts or [],
        quality_review={
            "passed": False,
            "checks": actionable,
            "findings": actionable,
        },
        revision_plan={
            "required": True,
            "reason": summary,
            "steps": [{
                "step_id": "skitarii_mission",
                "worker": "Skitarii",
                "order": remediation,
                "findings": actionable,
            }],
        },
        user_facing_answer="",
    )
    review = mc.acceptance_review(
        mission_id,
        accepted=False,
        reason=summary,
        required_revision={
            "to": "Ceraxia",
            "order": remediation,
            "findings": actionable,
        },
        escalate_to_user=False,
    )
    order = mc.revision_order(
        mission_id,
        to="Ceraxia",
        reason=summary,
        order=remediation,
        required_steps=["Choose another repair approach", "Redispatch Skitarii"],
    )
    mc.validate_protocol_payload(report, expected_type="governor_report")
    mc.validate_protocol_payload(review, expected_type="acceptance_review")
    mc.validate_protocol_payload(order, expected_type="revision_order")
    mc._write_json(mission_dir / "governor_report.json", report)
    mc._write_json(
        mc._next_numbered_path(mission_dir / "governor_reports", "governor_report"),
        report,
    )
    mc._write_json(mission_dir / "acceptance_review.json", review)
    mc._write_json(
        mc._next_numbered_path(mission_dir / "acceptance_reviews", "acceptance_review"),
        review,
    )
    mc._write_json(mission_dir / "revision_order.json", order)
    mc.record_mission_state(
        mission_dir, "revision", run_status="revision", phase="revision",
    )
    mc.append_progress_event(
        mission_dir / "progress_events.jsonl",
        mc.progress_event(
            mission_id,
            "Ceraxia",
            "governor",
            "revising",
            "running",
            "Цераксия получила конкретную ревизию",
            remediation[:400],
        ),
    )
    ledger.record_event(
        "skitarii_protocol_revision_recorded", {"mission_id": mission_id}
    )


def _finalize_linked_failed(
    run_dir: Path,
    ledger: Any,
    summary: str,
    *,
    artifacts: list[str] | None = None,
    findings: list[dict[str, Any]] | None = None,
    revision_exhausted: bool = True,
) -> None:
    """Persist an exhausted autonomous effort as explained failure, never block."""

    mission_dir = _mission_dir(run_dir)
    if not mission_dir or not mission_dir.exists():
        raise RuntimeError("native run is missing its linked mission directory")
    from . import mission_control as mc

    mission_id = str(
        _read_json(mission_dir / "mission.json").get("mission_id") or mission_dir.name
    )
    actionable = [dict(item) for item in findings or [] if isinstance(item, dict)]
    report = mc.governor_report(
        mission_id,
        governor="Ceraxia",
        status="failed",
        summary=summary,
        deliverables=artifacts or [],
        quality_review={
            "passed": False,
            "checks": actionable,
            "findings": actionable,
            "autonomous_revision_exhausted": revision_exhausted,
        },
        revision_plan={
            "required": False,
            "reason": (
                "autonomous approaches exhausted"
                if revision_exhausted else "internal contract failure"
            ),
            "steps": [],
        },
        user_facing_answer=summary,
    )
    mc.validate_protocol_payload(report, expected_type="governor_report")
    mc._write_json(mission_dir / "governor_report.json", report)
    mc._write_json(
        mc._next_numbered_path(mission_dir / "governor_reports", "governor_report"),
        report,
    )
    final = mc.final_response(
        mission_id, "failed", summary, artifacts=artifacts or [],
    )
    final["review_findings"] = actionable
    if revision_exhausted:
        final["autonomous_revision_exhausted"] = True
    mc._write_json(mission_dir / "final_response.json", final)
    mc.record_mission_state(
        mission_dir, "failed", run_status="failed", phase="failed",
    )
    mc.append_progress_event(
        mission_dir / "progress_events.jsonl",
        mc.progress_event(
            mission_id,
            "Ceraxia",
            "governor",
            "failed",
            "failed",
            (
                "Автономные подходы исчерпаны"
                if revision_exhausted else "Внутренний контракт Skitarii нарушен"
            ),
            summary[:400],
        ),
    )
    ledger.record_event(
        "skitarii_protocol_failure_recorded", {"mission_id": mission_id}
    )


def _finalize_linked_cancelled(
    run_dir: Path,
    ledger: Any,
    summary: str,
    *,
    artifacts: list[str] | None = None,
) -> None:
    mission_dir = _mission_dir(run_dir)
    if not mission_dir or not mission_dir.exists():
        return
    from . import mission_control as mc

    mission_id = str(_read_json(mission_dir / "mission.json").get("mission_id") or mission_dir.name)
    final = mc.final_response(
        mission_id, "cancelled", summary, artifacts=artifacts or [],
    )
    mc._write_json(mission_dir / "final_response.json", final)
    mc.record_mission_state(
        mission_dir, "cancelled", run_status="cancelled", phase="cancelled",
    )
    mc.append_progress_event(
        mission_dir / "progress_events.jsonl",
        mc.progress_event(
            mission_id, "Ceraxia", "governor", "cancelled", "cancelled",
            "Code mission cancelled", summary,
        ),
    )
    ledger.record_event("skitarii_protocol_cancelled_recorded", {"mission_id": mission_id})


def _bridge_failure(
    run_dir: Path,
    task_id: str,
    summary: str,
    *,
    phase: str = "blocked",
    status: str = "blocked",
    needs_user: bool = False,
    error: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "task_id": task_id,
        "phase": phase,
        "status": status,
        "final_step": "skitarii",
        "summary": summary,
        "artifacts": [],
        "artifact_root": str(run_dir.resolve()),
        "patch_stage": {},
        "ready_to_apply": False,
        "next_action": {},
        "needs_user": needs_user,
        "via": "skitarii",
        "rounds": [],
    }
    if error:
        payload["error"] = error
    return payload


def _skitarii_terminal_cleanup_proven(ledger: Any) -> bool:
    data = ledger.to_dict()
    meta = data.get("skitarii_mission")
    return bool(
        isinstance(meta, dict)
        and meta.get("inflight") is False
        and meta.get("cleanup_complete") is True
    )


def _skitarii_exception_is_external_blocker(error: BaseException) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, urllib.error.URLError):
            return True
        current = current.__cause__ or current.__context__
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "identity",
            "different service endpoint",
            "bearer token",
            "cleanup is unresolved",
            "cleanup was not proven",
            "request failed",
            "bridge timeout",
        )
    )


def _record_skitarii_internal_contract_failure(
    run_dir: Path,
    task_id: str,
    ledger: Any,
    *,
    summary: str,
    error: str,
) -> dict[str, Any]:
    finding = review_finding(
        "skitarii_bridge_internal_contract_failure",
        "Skitarii returned a result that the bridge could not validate.",
        error,
        "A terminal Skitarii result satisfies the shared verdict contract.",
        "Repair the Skitarii result serialization or bridge contract, then resume the persisted mission.",
        "infrastructure",
        True,
        entity_kind="skitarii_verdict",
        entity_id="terminal-verdict",
    )
    failure = _bridge_failure(
        run_dir,
        task_id,
        summary,
        phase="failed",
        status="failed",
        error=error,
    )
    failure["verification_findings"] = [finding]
    failure["review_findings"] = [finding]
    failure["next_action"] = {
        "kind": "repair_internal_contract",
        "reason": finding["what_failed"],
        "remediation": finding["remediation"],
        "retryable": True,
        "findings": [finding],
    }
    ledger.set_result(failure)
    ledger.force_status("failed", reason=summary)
    ledger.record_event(
        "skitarii_internal_contract_failure", {"error": error[:500]}
    )
    try:
        _finalize_linked_failed(
            run_dir,
            ledger,
            summary,
            findings=[finding],
            revision_exhausted=False,
        )
    except Exception as finalize_exc:  # noqa: BLE001 - failure is already durable
        ledger.record_event(
            "skitarii_finalize_error", {"error": str(finalize_exc)[:300]}
        )
    return failure


def _ceraxia_reprepare_action(
    message: str = "",
    *,
    run_dir: Path | None = None,
    task_id: str = "",
) -> dict[str, Any]:
    """Describe the only safe recovery for a pre-directive Ceraxia run."""
    source_task_id = str(task_id or (run_dir.name if run_dir is not None else "")).strip()
    if not _MISSION_ID_RE.fullmatch(source_task_id):
        raise ValueError("recovery source has an invalid task identity")
    task_memory = _read_json(run_dir / "task_memory.json") if run_dir is not None else {}
    task_memory_id = str(task_memory.get("task_memory_id") or source_task_id).strip()
    root_task_id = str(task_memory.get("root_task_id") or task_memory_id).strip()
    if not _MISSION_ID_RE.fullmatch(task_memory_id) or not _MISSION_ID_RE.fullmatch(root_task_id):
        raise ValueError("recovery source has an invalid task-memory lineage")
    stem = source_task_id[:119].rstrip(".-_") or "ceraxia-code-run"
    return {
        "kind": "reprepare_ceraxia_run",
        "method": "POST",
        "endpoint": "POST /orchestrate_run",
        "body": {
            "message": message,
            "task_id": f"{stem}-native",
            "governor_transport": "http",
            "run_mode": "http",
            "auto_start": True,
            "reuse_existing": True,
            "task_memory_id": task_memory_id,
            "root_task_id": root_task_id,
            "parent_task_id": source_task_id,
            "continuation_of": source_task_id,
        },
        "reason": (
            "this historical run predates native Ceraxia leadership; create a "
            "fresh mission instead of mutating execution evidence"
        ),
    }


def _acceptance_source_reprepare_action(
    message: str = "",
    *,
    run_dir: Path | None = None,
    task_id: str = "",
) -> dict[str, Any]:
    """Recover a current run whose commander provenance is missing or inconsistent."""
    action = _ceraxia_reprepare_action(message, run_dir=run_dir, task_id=task_id)
    action["reason"] = (
        "the linked commander-order acceptance provenance is missing, corrupt, or "
        "does not match this mission; create a fresh mission through Abaddon"
    )
    return action


def _skitarii_json_request(
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    timeout: float = 30.0,
    allowed_http_statuses: frozenset[int] = frozenset(),
) -> dict[str, Any]:
    """Call the loopback Skitarii API with a bounded, object-only response."""
    headers = {"Content-Type": "application/json"} if body is not None else {}
    bearer = os.environ.get("SKITARII_BEARER_TOKEN", "")
    if bearer:
        if any(char in bearer for char in "\r\n"):
            raise RuntimeError("invalid Skitarii bearer token")
        headers["Authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(
        f"{SKITARII_URL}{path}", data=body, headers=headers, method=method,
    )
    http_status = 200
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            http_status = int(getattr(response, "status", 200) or 200)
            raw_length = response.headers.get("Content-Length")
            if raw_length:
                try:
                    if int(raw_length) > MAX_SKITARII_RESPONSE_BYTES:
                        raise RuntimeError("Skitarii response exceeds the configured byte limit")
                except ValueError as exc:
                    raise RuntimeError("Skitarii returned an invalid Content-Length") from exc
            raw = response.read(MAX_SKITARII_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raw = exc.read(MAX_SKITARII_RESPONSE_BYTES + 1)
        http_status = int(exc.code)
        if http_status not in allowed_http_statuses:
            detail = raw[:8192].decode("utf-8", errors="replace")
            raise RuntimeError(f"Skitarii HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Skitarii request failed: {exc}") from exc
    if len(raw) > MAX_SKITARII_RESPONSE_BYTES:
        raise RuntimeError("Skitarii response exceeds the configured byte limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Skitarii returned malformed JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Skitarii returned a non-object JSON response")
    if http_status in allowed_http_statuses:
        payload = dict(payload)
        payload["_http_status"] = http_status
    return payload


def _service_request_sha256(payload: dict[str, Any]) -> str:
    bound_payload = dict(payload)
    bound_payload.pop("task_id", None)
    canonical = json.dumps(
        bound_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _canonical_json_sha256(payload: Any) -> str:
    """Hash one JSON value using the same representation on every retry."""

    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _revision_execution_action(
    *,
    task_id: str,
    mission_meta: dict[str, Any],
    findings: list[dict[str, Any]],
    current_result: dict[str, Any],
    reason: str,
    remediation: str,
    revision_owner: str,
) -> dict[str, Any]:
    """Build a replay-stable continuation bound to the exact failed attempt.

    The token is an idempotency/evidence key, not a bearer secret.  It commits to
    the Warmaster task, the concrete Skitarii attempt and the canonical verifier
    evidence/result which justified starting another revision.
    """

    mission_id = str(mission_meta.get("id") or "").strip()
    try:
        attempt = int(mission_meta.get("attempt") or 0)
    except (TypeError, ValueError):
        return {}
    if not mission_id or attempt < 1:
        return {}

    mission_binding = {"id": mission_id, "attempt": attempt}
    token_payload = {
        "schema": "skitarii-revision-execution/v1",
        "task_id": task_id,
        "skitarii_mission": mission_binding,
        "findings": findings,
        # next_action is deliberately absent from current_result, otherwise the
        # token would recursively contain itself.
        "current_result": current_result,
    }
    revision_token = _canonical_json_sha256(token_payload)
    return {
        "kind": "execute_revision",
        "method": "POST",
        "endpoint": "POST /runs/{task_id}/start_revision_http",
        "body": {"revision_token": revision_token},
        "reason": reason,
        "remediation": remediation,
        "revision_owner": revision_owner,
        "retryable": True,
        "findings": findings,
        "revision_binding": {
            "schema": token_payload["schema"],
            "task_id": task_id,
            "skitarii_mission": mission_binding,
            "findings_sha256": _canonical_json_sha256(findings),
            "current_result_sha256": _canonical_json_sha256(current_result),
        },
    }


def _service_mission_id(task_id: str, attempt: int = 1) -> str:
    safe_task = re.sub(r"[^A-Za-z0-9_.-]", "-", task_id)[:88].strip(".-") or "task"
    normalized_attempt = max(1, int(attempt))
    digest = hashlib.sha256(f"{task_id}\0{normalized_attempt}".encode("utf-8")).hexdigest()[:16]
    return f"wm-{safe_task}-{normalized_attempt}-{digest}"


def _parent_skitarii_mission_id(
    run_dir: Path, task_memory: dict[str, Any],
) -> str:
    """Return a validated checkpoint source for an immutable recovery child.

    Recovery runs are siblings, so their fresh ledger cannot name the old
    Skitarii mission that owns the useful checkpoint. Missing, corrupt, or
    stale parent metadata is deliberately treated as no hint: execution can
    still start from the repository snapshot.
    """
    parent_task_id = str(task_memory.get("parent_task_id") or "").strip()
    if not parent_task_id or not _MISSION_ID_RE.fullmatch(parent_task_id):
        return ""
    parent_run = (run_dir.parent / parent_task_id).resolve()
    run_root = run_dir.parent.resolve()
    if parent_run == run_dir.resolve() or parent_run.parent != run_root:
        return ""
    parent_ledger = _read_json(parent_run / "task_ledger.json")
    mission_meta = (
        parent_ledger.get("skitarii_mission")
        if isinstance(parent_ledger.get("skitarii_mission"), dict)
        else {}
    )
    mission_id = str(mission_meta.get("id") or "").strip()
    service = str(mission_meta.get("service") or "").strip()
    if service and service != SKITARII_URL:
        return ""
    try:
        attempt = int(mission_meta.get("attempt") or 0)
    except (TypeError, ValueError):
        return ""
    if attempt > 0 and mission_id != _service_mission_id(parent_task_id, attempt):
        return ""
    return mission_id if _MISSION_ID_RE.fullmatch(mission_id) else ""


def _service_clarification_action() -> dict[str, Any]:
    return {
        "kind": "provide_clarification",
        "method": "POST",
        "endpoint": "POST /runs/{task_id}/clarification",
        "body": {"answer": ""},
        "reason": "Skitarii is waiting for clarification on this same mission",
    }


def _expired_clarification_action(question: str) -> dict[str, Any]:
    return {
        "kind": "prompt_user",
        "method": "",
        "endpoint": "",
        "body": {"question": question},
        "reason": "The clarification window expired; answer in the original task before retrying",
    }


def _cancel_service_mission(mission_id: str) -> None:
    if not _MISSION_ID_RE.fullmatch(mission_id):
        return
    try:
        _skitarii_json_request(
            "POST",
            f"/missions/{mission_id}/cancel",
            body=b"{}",
            timeout=10.0,
        )
    except RuntimeError:
        # The Warmaster ledger remains the source of truth. A service-side cleanup
        # failure is surfaced by the normal bridge error/cancellation event.
        pass


def _relay_skitarii_progress(latest: Any, snapshot: dict[str, Any], already_relayed: int) -> int:
    """Surface the fighter's human-readable step notes onto the run event stream.

    The Skitarii mission records readable progress notes (plan choices, actions,
    revisions) in each event's ``text`` field, but they never leave the worker:
    the run ledger only sees dispatch/start/verdict milestones. Without this the
    app shows "handed to the fighter" and then nothing until a final verdict.
    Relay every new text-bearing note onto the run's own event stream so Ceraxia
    and the app can read, in plain language, what the fighter is actually doing.
    """
    events = snapshot.get("events")
    if not isinstance(events, list):
        return already_relayed
    steps = [
        event for event in events
        if isinstance(event, dict) and str(event.get("text") or "").strip()
    ]
    if len(steps) <= already_relayed:
        return already_relayed
    for step in steps[already_relayed:]:
        latest.record_event(
            "skitarii_step",
            {
                "text": str(step.get("text") or "").strip()[:1000],
                "kind": str(step.get("type") or "note"),
                "at": str(step.get("at") or ""),
            },
        )
    latest.save()
    return len(steps)


def _await_async_skitarii_mission(
    body: bytes,
    run_dir: Path,
    task_id: str,
    ledger: Any,
    timeout_sec: int,
) -> dict[str, Any]:
    """Start, poll and (when requested) cancel one durable service mission."""
    creation_body = json.loads(body.decode("utf-8"))
    request_sha256 = _service_request_sha256(creation_body)
    current_ledger = type(ledger).load(run_dir / "task_ledger.json")
    current_data = current_ledger.to_dict()
    old_meta = (
        current_data.get("skitarii_mission")
        if isinstance(current_data.get("skitarii_mission"), dict) else {}
    )
    try:
        old_attempt = max(0, int(old_meta.get("attempt") or 0))
    except (TypeError, ValueError):
        old_attempt = 0
    old_status = str(old_meta.get("status") or "")
    active_statuses = {"planned", "queued", "running", "needs_user", "cancelling"}
    unresolved_statuses = {
        "cancel_cleanup_unproven",
        "identity_mismatch",
        "service_mismatch",
    }
    if old_attempt > 0 and (
        old_status in unresolved_statuses
        or old_meta.get("cleanup_complete") is False
        or bool(str(old_meta.get("identity_error") or ""))
    ):
        raise RuntimeError(
            "previous Skitarii mission identity or cleanup is unresolved; reconcile it before retrying",
        )
    if (
        old_attempt > 0
        and old_status in active_statuses
        and str(old_meta.get("service") or "") != SKITARII_URL
    ):
        current_ledger.data["skitarii_mission"] = {
            **old_meta,
            "identity_error": "service_mismatch",
        }
        current_ledger.save()
        raise RuntimeError("active Skitarii mission belongs to a different service endpoint")
    expected_old_id = _service_mission_id(task_id, old_attempt) if old_attempt else ""
    reuse_active_attempt = (
        old_attempt > 0
        and str(old_meta.get("id") or "") == expected_old_id
        and str(old_meta.get("request_sha256") or "") == request_sha256
        and old_status in active_statuses
    )
    if old_attempt > 0 and old_status in active_statuses and not reuse_active_attempt:
        current_ledger.data["skitarii_mission"] = {
            **old_meta,
            "identity_error": "request_identity_mismatch",
        }
        current_ledger.save()
        raise RuntimeError(
            "active Skitarii mission has a different request identity; cancel it and prove cleanup before retrying",
        )
    attempt = old_attempt if reuse_active_attempt else max(1, old_attempt + 1)
    requested_id = _service_mission_id(task_id, attempt)
    creation_body["task_id"] = requested_id
    current_ledger.data["skitarii_mission"] = {
        "id": requested_id,
        "attempt": attempt,
        "request_sha256": request_sha256,
        "status": str(old_meta.get("status") or "planned") if reuse_active_attempt else "planned",
        "service": SKITARII_URL,
    }
    # Persist the adoption key before touching the service. A crash before POST and
    # a crash after POST therefore both retry the exact same attempt id.
    current_ledger.save()
    ledger = current_ledger
    existing = _skitarii_json_request(
        "GET",
        f"/missions/{requested_id}",
        timeout=30.0,
        allowed_http_statuses=frozenset({404}),
    )
    adopted = existing.get("_http_status") != 404
    pending_snapshot: dict[str, Any] | None = existing if adopted else None
    if adopted and str(existing.get("request_sha256") or "") != request_sha256:
        current_ledger.data["skitarii_mission"]["status"] = "identity_mismatch"
        current_ledger.save()
        raise RuntimeError("Skitarii mission request identity does not match the current attempt")
    if not adopted:
        creation_bytes = json.dumps(creation_body, ensure_ascii=False).encode("utf-8")
        post_timeout = min(max(float(timeout_sec), 30.0), 180.0)
        # A full bounded worker queue (429) is transient backpressure, not a task
        # failure.  The deterministic requested_id makes each re-POST an idempotent
        # re-attach (a create that already landed answers 409 below), so we back
        # off and retry inside the mission wall budget instead of collapsing a
        # retryable 429 into a dead "blocked" verdict.
        backpressure_deadline = time.monotonic() + min(
            max(int(timeout_sec), 30), _SKITARII_BACKPRESSURE_MAX_WAIT_SEC,
        )
        backpressure_delay = _SKITARII_BACKPRESSURE_BASE_DELAY_SEC
        while True:
            created = _skitarii_json_request(
                "POST",
                "/missions",
                body=creation_bytes,
                timeout=post_timeout,
                # 409: idempotent re-attach after a crash between POST and the
                # ledger write.  429: the worker queue is momentarily full.
                allowed_http_statuses=frozenset({409, 429}),
            )
            if created.get("_http_status") != 429:
                break
            if time.monotonic() >= backpressure_deadline:
                raise _SkitariiQueueBackpressure(
                    "Skitarii worker queue stayed full for the whole retry budget; "
                    "the mission is still queued and can be retried",
                )
            ledger.record_event(
                "skitarii_queue_backpressure",
                {
                    "mission_id": requested_id,
                    "retry_in_sec": round(backpressure_delay, 1),
                    "detail": str(created.get("error") or "worker queue full")[:200],
                },
            )
            time.sleep(backpressure_delay)
            backpressure_delay = min(
                backpressure_delay * 2, _SKITARII_BACKPRESSURE_MAX_DELAY_SEC,
            )
        if created.get("_http_status") == 409:
            pending_snapshot = _skitarii_json_request(
                "GET", f"/missions/{requested_id}", timeout=30.0,
            )
            if str(pending_snapshot.get("request_sha256") or "") != request_sha256:
                current_ledger.data["skitarii_mission"]["status"] = "identity_mismatch"
                current_ledger.save()
                raise RuntimeError("Skitarii duplicate mission has a different request identity")
            adopted = True
        else:
            mission_id = str(created.get("mission_id") or "")
            if (
                mission_id != requested_id
                or str(created.get("request_sha256") or "") != request_sha256
            ):
                raise RuntimeError("Skitarii returned an invalid asynchronous mission id")
    mission_id = requested_id
    if not _MISSION_ID_RE.fullmatch(mission_id):
        raise RuntimeError("Skitarii returned an invalid asynchronous mission id")
    ledger.data["skitarii_mission"] = {
        "id": mission_id,
        "attempt": attempt,
        "request_sha256": request_sha256,
        "status": str((pending_snapshot or {}).get("status") or "queued"),
        "service": SKITARII_URL,
    }
    ledger.save()
    ledger.record_event(
        "skitarii_mission_adopted" if adopted else "skitarii_mission_started",
        {"mission_id": mission_id, "attempt": attempt, "request_sha256": request_sha256},
    )
    deadline = time.monotonic() + max(1, int(timeout_sec)) + 120
    last_waiting: tuple[str, str] | None = None
    consecutive_errors = 0
    cancel_sent = False
    checkpoint_resume_attempts = 0
    relayed_progress_count = 0
    while True:
        latest = type(ledger).load(run_dir / "task_ledger.json")
        latest_data = latest.to_dict()
        if latest.cancel_requested() or str(latest_data.get("status") or "") in {"cancelling", "cancelled"}:
            if not cancel_sent:
                try:
                    cancel_ack = _skitarii_json_request(
                        "POST", f"/missions/{mission_id}/cancel", body=b"{}", timeout=15.0,
                    )
                except RuntimeError as exc:
                    latest.record_event(
                        "skitarii_cancel_forward_error", {"mission_id": mission_id, "error": str(exc)[:300]},
                    )
                else:
                    cancel_sent = bool(cancel_ack.get("ok")) or str(cancel_ack.get("status") or "") in {
                        "cancelling", "cancelled",
                    }
                    if cancel_sent:
                        cancelling_meta = dict(
                            latest_data.get("skitarii_mission")
                            if isinstance(latest_data.get("skitarii_mission"), dict) else {}
                        )
                        cancelling_meta.update({"id": mission_id, "status": "cancelling"})
                        latest.data["skitarii_mission"] = cancelling_meta
                        latest.save()
                        latest.record_event("skitarii_cancel_forwarded", {"mission_id": mission_id})
        if time.monotonic() >= deadline:
            _cancel_service_mission(mission_id)
            timeout_meta = dict(
                latest_data.get("skitarii_mission")
                if isinstance(latest_data.get("skitarii_mission"), dict) else {}
            )
            timeout_meta.update({"id": mission_id, "status": "cancel_cleanup_unproven"})
            latest.data["skitarii_mission"] = timeout_meta
            latest.save()
            raise TimeoutError(f"Skitarii mission {mission_id} exceeded its bridge timeout")
        if pending_snapshot is not None:
            snapshot = pending_snapshot
            pending_snapshot = None
        else:
            try:
                snapshot = _skitarii_json_request(
                    "GET", f"/missions/{mission_id}", timeout=30.0,
                )
                consecutive_errors = 0
            except RuntimeError:
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    _cancel_service_mission(mission_id)
                    raise
                time.sleep(SKITARII_POLL_INTERVAL_SEC)
                continue
        if str(snapshot.get("request_sha256") or "") != request_sha256:
            identity_meta = dict(
                latest_data.get("skitarii_mission")
                if isinstance(latest_data.get("skitarii_mission"), dict) else {}
            )
            identity_meta.update({"id": mission_id, "status": "identity_mismatch"})
            latest.data["skitarii_mission"] = identity_meta
            latest.save()
            _cancel_service_mission(mission_id)
            raise RuntimeError("Skitarii mission identity changed while it was running")
        relayed_progress_count = _relay_skitarii_progress(
            latest, snapshot, relayed_progress_count,
        )
        status = str(snapshot.get("status") or "")
        mission_meta = (
            latest_data.get("skitarii_mission")
            if isinstance(latest_data.get("skitarii_mission"), dict) else {}
        )
        lifecycle_changed = status in {"done", "failed", "blocked", "cancelled"} and (
            mission_meta.get("inflight") is not snapshot.get("inflight")
            or mission_meta.get("cleanup_complete") is not snapshot.get("cleanup_complete")
        )
        if mission_meta.get("id") != mission_id or mission_meta.get("status") != status or lifecycle_changed:
            updated_meta = dict(mission_meta)
            updated_meta.update({
                "id": mission_id,
                "attempt": attempt,
                "request_sha256": request_sha256,
                "status": status,
                "service": SKITARII_URL,
            })
            if status in {"done", "failed", "blocked", "cancelled"}:
                updated_meta["inflight"] = snapshot.get("inflight")
                updated_meta["cleanup_complete"] = snapshot.get("cleanup_complete")
            latest.data["skitarii_mission"] = updated_meta
            latest.save()
        if status == "needs_user":
            question = str(snapshot.get("question") or "").strip()
            if not question:
                _cancel_service_mission(mission_id)
                raise RuntimeError("Skitarii entered needs_user without a question")
            signature = (status, question)
            if signature != last_waiting:
                waiting = {
                    "ok": False,
                    "task_id": task_id,
                    "phase": "needs_user",
                    "status": "needs_user",
                    "final_step": "skitarii",
                    "summary": question,
                    "question": question,
                    "needs_user": True,
                    "artifacts": [],
                    "artifact_root": str(run_dir.resolve()),
                    "patch_stage": {},
                    "ready_to_apply": False,
                    "next_action": _service_clarification_action(),
                    "skitarii_mission_id": mission_id,
                    "via": "skitarii",
                    "rounds": [],
                }
                latest.set_result(waiting)
                latest.record_event(
                    "skitarii_needs_user", {"mission_id": mission_id, "question": question[:500]},
                )
                last_waiting = signature
        elif last_waiting is not None:
            latest.record_event("skitarii_clarification_received", {"mission_id": mission_id})
            last_waiting = None
        if status in {"done", "failed", "blocked", "cancelled"}:
            result = snapshot.get("result")
            if not isinstance(result, dict):
                raise RuntimeError(f"Skitarii terminal mission {mission_id} has no object result")
            inflight = snapshot.get("inflight")
            cleanup_complete = snapshot.get("cleanup_complete")
            if inflight is True:
                time.sleep(SKITARII_POLL_INTERVAL_SEC)
                continue
            if inflight is not False:
                raise RuntimeError("Skitarii terminal mission omitted its inflight lifecycle proof")
            if cleanup_complete is not True:
                if status not in {"failed", "blocked"} or result.get("accepted") is not False:
                    raise RuntimeError("Skitarii terminal mission did not prove sandbox cleanup")
                result = dict(result)
                result.setdefault("cleanup_error", str(snapshot.get("cleanup_error") or "sandbox cleanup failed"))
            if (
                status in {"blocked", "failed"}
                and (
                    result.get("restart_recovery_required") is True
                    or result.get("error_code") == "task_checkpoint_commit_pending"
                )
            ):
                if checkpoint_resume_attempts:
                    time.sleep(
                        min(30.0, float(2 ** min(checkpoint_resume_attempts, 5)))
                    )
                resumed = _skitarii_json_request(
                    "POST",
                    f"/missions/{mission_id}/resume",
                    body=b"{}",
                    timeout=180.0,
                )
                if resumed.get("ok") is not True:
                    raise RuntimeError(
                        "Skitarii restart checkpoint salvage was not accepted: "
                        + str(resumed.get("error") or resumed.get("status") or "unknown response")
                    )
                checkpoint_resume_attempts += 1
                latest.record_event(
                    "skitarii_checkpoint_salvage_started",
                    {
                        "mission_id": mission_id,
                        "after_restart": result.get("restart_recovery_required") is True,
                        "attempt": checkpoint_resume_attempts,
                    },
                )
                time.sleep(SKITARII_POLL_INTERVAL_SEC)
                continue
            return result
        if status not in {"queued", "running", "needs_user"}:
            _cancel_service_mission(mission_id)
            raise RuntimeError(f"Skitarii returned unknown mission status: {status!r}")
        time.sleep(SKITARII_POLL_INTERVAL_SEC)


def answer_skitarii_mission(run_dir: Path, task_id: str, answer: str) -> dict[str, Any]:
    """Forward a user answer to the still-running service mission for this run."""
    from .ledger import TaskLedger

    text = str(answer).strip()
    if not text:
        return {"ok": False, "status": "invalid", "error": "answer is required"}
    if len(text.encode("utf-8")) > 20_000:
        return {"ok": False, "status": "invalid", "error": "answer exceeds 20000 bytes"}
    ledger = TaskLedger.load(run_dir / "task_ledger.json")
    data = ledger.to_dict()
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    mission_meta = data.get("skitarii_mission") if isinstance(data.get("skitarii_mission"), dict) else {}
    mission_id = str(result.get("skitarii_mission_id") or mission_meta.get("id") or "")
    try:
        attempt = int(mission_meta.get("attempt") or 0)
    except (TypeError, ValueError):
        attempt = 0
    request_sha256 = str(mission_meta.get("request_sha256") or "")
    if (
        str(result.get("status") or "") != "needs_user"
        or not result.get("needs_user")
        or not _MISSION_ID_RE.fullmatch(mission_id)
        or attempt < 1
        or mission_id != _service_mission_id(task_id, attempt)
        or not re.fullmatch(r"[0-9a-f]{64}", request_sha256)
    ):
        return {"ok": False, "status": "conflict", "error": "run is not waiting for clarification"}
    snapshot = _skitarii_json_request(
        "GET", f"/missions/{mission_id}", timeout=30.0,
    )
    if (
        str(snapshot.get("status") or "") != "needs_user"
        or str(snapshot.get("request_sha256") or "") != request_sha256
    ):
        return {"ok": False, "status": "conflict", "error": "service mission is not waiting for this answer"}
    response = _skitarii_json_request(
        "POST",
        f"/missions/{mission_id}/answer",
        body=json.dumps({"answer": text}, ensure_ascii=False).encode("utf-8"),
        timeout=30.0,
    )
    if response.get("ok") is not True:
        return {
            "ok": False,
            "status": str(response.get("status") or "conflict"),
            "error": "Skitarii did not accept the clarification",
        }
    resumed = dict(result)
    resumed.update({
        "phase": "running",
        "status": "running",
        "summary": "Clarification accepted; Skitarii resumed the same mission.",
        "question": "",
        "needs_user": False,
        "next_action": {},
    })
    ledger.set_result(resumed)
    ledger.record_event("skitarii_answer_forwarded", {"mission_id": mission_id})
    return {"ok": True, "status": "running", "task_id": task_id, "mission_id": mission_id}


def cancel_skitarii_mission_for_run(run_dir: Path, task_id: str) -> dict[str, Any]:
    """Cancel the durable service mission even when no bridge poll thread survived."""
    from .ledger import TaskLedger

    ledger = TaskLedger.load(run_dir / "task_ledger.json")
    data = ledger.to_dict()
    meta = data.get("skitarii_mission") if isinstance(data.get("skitarii_mission"), dict) else {}
    mission_id = str(meta.get("id") or "")
    try:
        attempt = int(meta.get("attempt") or 0)
    except (TypeError, ValueError):
        attempt = 0
    request_sha256 = str(meta.get("request_sha256") or "")
    if (
        attempt < 1
        or mission_id != _service_mission_id(task_id, attempt)
        or str(meta.get("service") or "") != SKITARII_URL
        or not re.fullmatch(r"[0-9a-f]{64}", request_sha256)
        or str(meta.get("status") or "") not in {
            "planned", "queued", "running", "needs_user", "cancelling",
            "cancel_cleanup_unproven",
        }
    ):
        return {"ok": False, "status": "not_active", "error": "run has no active Skitarii mission"}
    meta_status = str(meta.get("status") or "")
    if meta_status == "cancel_cleanup_unproven":
        snapshot = _skitarii_json_request(
            "GET", f"/missions/{mission_id}", timeout=15.0,
        )
        if str(snapshot.get("request_sha256") or "") != request_sha256:
            return {
                "ok": False,
                "status": "identity_mismatch",
                "error": "cancelled mission identity changed",
            }
        recovered_status = str(snapshot.get("status") or "")
        if (
            recovered_status in {"done", "failed", "blocked", "cancelled"}
            and snapshot.get("inflight") is False
            and snapshot.get("cleanup_complete") is True
        ):
            recovered_meta = dict(meta)
            recovered_meta.update({
                "status": recovered_status,
                "inflight": False,
                "cleanup_complete": True,
            })
            recovered_meta.pop("identity_error", None)
            ledger.data["skitarii_mission"] = recovered_meta
            ledger.save()
            ledger.record_event(
                "skitarii_cancel_cleanup_reconciled",
                {"mission_id": mission_id, "status": recovered_status},
            )
            if recovered_status == "cancelled":
                _cancelled_bridge_result(run_dir, task_id)
                return {"ok": True, "status": "cancelled", "mission_id": mission_id}
            return {
                "ok": False,
                "status": recovered_status,
                "mission_id": mission_id,
                "cleanup_complete": True,
                "error": "mission reached a non-cancelled terminal state after cancellation timeout",
            }
    if meta_status != "cancelling":
        response = _skitarii_json_request(
            "POST", f"/missions/{mission_id}/cancel", body=b"{}", timeout=15.0,
        )
    else:
        response = {"ok": True, "status": "cancelling"}
    acknowledged = response.get("ok") is True or str(response.get("status") or "") in {
        "cancelling", "cancelled",
    }
    if not acknowledged:
        return {
            "ok": False,
            "status": str(response.get("status") or "conflict"),
            "error": "Skitarii mission did not acknowledge cancellation",
        }
    updated = dict(meta)
    updated["status"] = "cancelling"
    ledger.data["skitarii_mission"] = updated
    ledger.save()
    ledger.record_event("skitarii_mission_cancel_forwarded", {"mission_id": mission_id})
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        snapshot = _skitarii_json_request("GET", f"/missions/{mission_id}", timeout=15.0)
        if str(snapshot.get("request_sha256") or "") != request_sha256:
            return {"ok": False, "status": "identity_mismatch", "error": "cancelled mission identity changed"}
        status = str(snapshot.get("status") or "")
        inflight = snapshot.get("inflight")
        cleanup_complete = snapshot.get("cleanup_complete")
        if status in {"done", "failed", "blocked", "cancelled"} and inflight is False:
            terminal_meta = dict(updated)
            terminal_meta.update({
                "status": status,
                "inflight": False,
                "cleanup_complete": cleanup_complete,
            })
            if cleanup_complete is True:
                terminal_meta.pop("identity_error", None)
            ledger = TaskLedger.load(run_dir / "task_ledger.json")
            ledger.data["skitarii_mission"] = terminal_meta
            ledger.save()
            if status == "cancelled" and cleanup_complete is True:
                _cancelled_bridge_result(run_dir, task_id)
                return {"ok": True, "status": "cancelled", "mission_id": mission_id}
            result = snapshot.get("result") if isinstance(snapshot.get("result"), dict) else {}
            error = str(result.get("cleanup_error") or snapshot.get("cleanup_error") or "sandbox cleanup was not proven")
            failure = _bridge_failure(
                run_dir, task_id, f"Skitarii cancellation blocked: {error}",
                phase="cancel_cleanup_failed", error=error,
            )
            ledger.set_result(failure)
            ledger.force_status("blocked", reason="Skitarii cancellation cleanup failed")
            try:
                _finalize_linked_blocked(run_dir, ledger, str(failure["summary"]), phase="cancel_cleanup_failed")
            except Exception as exc:  # noqa: BLE001
                ledger.record_event("skitarii_finalize_error", {"error": str(exc)[:300]})
            return {"ok": False, "status": "blocked", "mission_id": mission_id, "error": error}
        time.sleep(0.25)
    ledger = TaskLedger.load(run_dir / "task_ledger.json")
    timeout_meta = dict(updated)
    timeout_meta["status"] = "cancel_cleanup_unproven"
    ledger.data["skitarii_mission"] = timeout_meta
    failure = _bridge_failure(
        run_dir, task_id, "Skitarii cancellation timed out before cleanup was proven.",
        phase="cancel_cleanup_unproven",
    )
    ledger.data["result"] = failure
    ledger.force_status("blocked", reason="Skitarii cancellation cleanup was not proven")
    return {"ok": False, "status": "blocked", "mission_id": mission_id,
            "error": "cancellation cleanup was not proven before timeout"}


def _cancelled_bridge_result(run_dir: Path, task_id: str) -> dict[str, Any] | None:
    from .ledger import TaskLedger
    from . import mission_control as mc

    ledger = TaskLedger.load(run_dir / "task_ledger.json")
    data = ledger.to_dict()
    if not ledger.cancel_requested() and str(data.get("status") or "") not in {"cancelling", "cancelled"}:
        return None
    mission_meta = data.get("skitarii_mission") if isinstance(data.get("skitarii_mission"), dict) else {}
    if not (
        str(mission_meta.get("status") or "") == "cancelled"
        and mission_meta.get("inflight") is False
        and mission_meta.get("cleanup_complete") is True
    ):
        return None
    msg = "Skitarii result discarded because the run was cancelled."
    cancelled = _bridge_failure(
        run_dir, task_id, msg, phase="cancelled", status="cancelled",
    )
    ledger.set_result(cancelled)
    ledger.force_status("cancelled", reason="cancelled before Skitarii staging")
    mission_dir = _mission_dir(run_dir)
    if mission_dir and mission_dir.exists():
        mission_id = str(_read_json(mission_dir / "mission.json").get("mission_id") or mission_dir.name)
        final = mc.final_response(mission_id, "cancelled", msg, artifacts=[])
        mc._write_json(mission_dir / "final_response.json", final)
        mc.record_mission_state(mission_dir, "cancelled", run_status="cancelled", phase="cancelled")
        mc.append_progress_event(
            mission_dir / "progress_events.jsonl",
            mc.progress_event(
                mission_id, "Ceraxia", "governor", "cancelled", "cancelled",
                "Код-миссия отменена", msg,
            ),
        )
    return cancelled


def run_via_skitarii(
    run_dir: Path,
    task_id: str,
    timeout_sec: int = 5400,
    execution_mode: str | None = None,
) -> dict[str, Any]:
    """Execute or continue the same durable code mission through Skitarii."""
    from .ledger import TaskLedger  # local import to avoid cycles
    from . import mission_control as mc

    contract = _read_json(run_dir / "contract.json")
    goal = str(contract.get("goal") or "")
    task_memory = _read_json(run_dir / "task_memory.json")
    task_memory_id = str(task_memory.get("task_memory_id") or "").strip()
    root_task_id = str(task_memory.get("root_task_id") or "").strip()
    parent_task_id = str(task_memory.get("parent_task_id") or "").strip()
    if not task_memory_id or not root_task_id:
        raise ValueError(
            "run has no durable task-memory lineage; reprepare it before Skitarii execution"
        )
    if not _MISSION_ID_RE.fullmatch(task_memory_id) or not _MISSION_ID_RE.fullmatch(root_task_id):
        raise ValueError("run has an invalid task-memory identity")
    if parent_task_id and (
        not _MISSION_ID_RE.fullmatch(parent_task_id) or parent_task_id == task_id
    ):
        raise ValueError("run has an invalid parent task-memory identity")
    parent_skitarii_mission_id = _parent_skitarii_mission_id(run_dir, task_memory)
    ledger = TaskLedger.load(run_dir / "task_ledger.json")
    ledger_status = str(ledger.to_dict().get("status") or "")
    if execution_mode is None:
        execution_mode = (
            "revision" if ledger_status in {"revision", "needs_revision"}
            else ("resume" if ledger_status == "interrupted" else "full")
        )
    if execution_mode not in {"full", "resume", "revision"}:
        raise ValueError("execution_mode must be full, resume, or revision")
    try:
        leadership_directive = _load_ceraxia_directive(run_dir, task_id)
    except CeraxiaDirectiveError as exc:
        msg = f"Skitarii blocked: Ceraxia leadership directive is invalid ({exc})."
        reprepare_action = _ceraxia_reprepare_action(
            goal, run_dir=run_dir, task_id=task_id,
        )
        ledger.record_event("ceraxia_directive_blocked", {"error": str(exc)[:300]})
        failure = _bridge_failure(
            run_dir,
            task_id,
            msg,
            phase="ceraxia_directive_invalid",
            error=str(exc),
        )
        failure["next_action"] = reprepare_action
        ledger.set_result(failure)
        ledger.force_status("blocked", reason="Ceraxia leadership directive is invalid")
        try:
            _finalize_linked_blocked(
                run_dir,
                ledger,
                msg,
                phase="ceraxia_directive_invalid",
                next_action=reprepare_action,
            )
        except Exception as finalize_exc:  # noqa: BLE001
            ledger.record_event("skitarii_finalize_error", {"error": str(finalize_exc)[:300]})
        return failure
    try:
        acceptance_source = _load_commander_order_acceptance_source(
            run_dir, leadership_directive,
        )
    except CeraxiaDirectiveError as exc:
        msg = f"Skitarii blocked: commander acceptance source is invalid ({exc})."
        reprepare_action = _acceptance_source_reprepare_action(
            goal, run_dir=run_dir, task_id=task_id,
        )
        ledger.record_event("acceptance_source_blocked", {"error": str(exc)[:300]})
        failure = _bridge_failure(
            run_dir,
            task_id,
            msg,
            phase="acceptance_source_invalid",
            error=str(exc),
        )
        failure.update({
            "error_code": "acceptance_source_invalid",
            "acceptance_source_status": "invalid",
            "next_action": reprepare_action,
        })
        ledger.set_result(failure)
        ledger.force_status("blocked", reason="commander acceptance source is invalid")
        try:
            _finalize_linked_blocked(
                run_dir,
                ledger,
                msg,
                phase="acceptance_source_invalid",
                next_action=reprepare_action,
            )
        except Exception as finalize_exc:  # noqa: BLE001
            ledger.record_event("skitarii_finalize_error", {"error": str(finalize_exc)[:300]})
        return failure
    try:
        goal = _normalize_goal_repo_scope(goal)
        workspace, is_patch = _collect_workspace(goal)
        # A Warmaster code mission targets the configured repository even when it
        # creates only new files. Direct /mission callers can still use greenfield
        # mode, but the repo bridge must produce an applyable patch, not hide output
        # in a runtime deliverables directory.
        if not is_patch and os.environ.get("SKITARII_WARMMASTER_ARTIFACT_ONLY") != "1":
            workspace = _full_repo_snapshot()
            is_patch = True
    except SnapshotError as exc:
        msg = f"Skitarii blocked: complete repository snapshot failed ({exc})."
        ledger.record_event("skitarii_snapshot_blocked", {"error": str(exc)[:300]})
        failure = _bridge_failure(run_dir, task_id, msg)
        ledger.set_result(failure)
        ledger.force_status("blocked", reason="complete repository snapshot failed")
        try:
            _finalize_linked_blocked(run_dir, ledger, msg)
        except Exception as finalize_exc:  # noqa: BLE001
            ledger.record_event("skitarii_finalize_error", {"error": str(finalize_exc)[:300]})
        return failure
    mode = "patch" if is_patch else "greenfield"
    # SAFETY: a patch task whose source we couldn't load must NOT silently turn into a
    # greenfield rewrite from scratch — that produces a plausible-looking but wrong
    # "fix". Block and ask the user to name the files/dir instead.
    if is_patch and not getattr(workspace, "inventory", list(workspace)):
        msg = ("Это правка существующего кода, но я не смог определить какие файлы/каталог "
               "менять. Уточни путь(и) к файлам или каталог проекта — писать с нуля я не буду.")
        ledger.record_event("skitarii_patch_no_source", {"goal": goal[:200]})
        failure = _bridge_failure(run_dir, task_id, msg, needs_user=True)
        ledger.set_result(failure)
        ledger.force_status("blocked", reason="patch task with no loadable source")
        try:
            _finalize_linked_blocked(
                run_dir, ledger, msg, needs_user=True, question=msg, phase="needs_user",
            )
        except Exception as finalize_exc:  # noqa: BLE001
            ledger.record_event("skitarii_finalize_error", {"error": str(finalize_exc)[:300]})
        return failure
    inventory = getattr(workspace, "inventory", sorted(workspace.keys()))
    ledger.record_event(
        "ceraxia_delegated_to_skitarii",
        {
            "decision": leadership_directive["decision"],
            "mission_id": leadership_directive["mission_id"],
            "priority_count": len(leadership_directive["priorities"]),
            "success_condition_count": len(leadership_directive["success_conditions"]),
        },
    )
    ledger.record_event("skitarii_dispatch", {"service": SKITARII_URL, "mode": mode,
                                              "execution_mode": execution_mode,
                                              "preloaded_file_count": len(inventory),
                                              "preloaded_file_sample": inventory[:50]})
    ledger.set_status("running")

    mission_payload = {"goal": goal, "task_id": task_id,
                       "delegating_task_id": task_id, "max_wall_sec": timeout_sec,
                       "task_memory_id": task_memory_id,
                       "root_task_id": root_task_id,
                       "parent_task_id": parent_task_id,
                       "leadership_directive": leadership_directive,
                       "acceptance_source": acceptance_source,
                       "mode": mode, "workspace_files": workspace,
                       "workspace_blobs": getattr(workspace, "blobs", {}),
                       "workspace_external_assets": getattr(workspace, "external_assets", {}),
                       "workspace_inventory": inventory,
                       "workspace_deleted": getattr(workspace, "deleted_paths", []),
                       "workspace_modes": getattr(workspace, "modes", {}),
                       "workspace_symlinks": getattr(workspace, "symlinks", {})}
    if parent_skitarii_mission_id:
        mission_payload["parent_skitarii_mission_id"] = parent_skitarii_mission_id
    body = json.dumps(mission_payload, ensure_ascii=False).encode("utf-8")
    started = time.monotonic()
    try:
        verdict = _await_async_skitarii_mission(
            body, run_dir, task_id, ledger, timeout_sec,
        )
    except Exception as exc:  # noqa: BLE001
        try:
            cancelled = _cancelled_bridge_result(run_dir, task_id)
        except Exception:
            cancelled = None
        if cancelled is not None:
            return cancelled
        ledger = TaskLedger.load(run_dir / "task_ledger.json")
        if isinstance(exc, _SkitariiQueueBackpressure):
            # Capacity backpressure is retryable: name the reason and flag it as
            # retryable instead of dying mutely.  The deterministic mission id keeps
            # a later re-dispatch idempotent.
            ledger.record_event(
                "skitarii_queue_backpressure_exhausted", {"error": str(exc)[:300]},
            )
            failure = _bridge_failure(
                run_dir,
                task_id,
                f"Skitarii worker queue is saturated: {exc}",
                phase="skitarii_backpressure",
                status="blocked",
                error=str(exc),
            )
            failure["retryable"] = True
            failure["next_action"] = {
                "kind": "retry_skitarii_dispatch",
                "reason": (
                    "The Skitarii worker queue was saturated; the mission is queued "
                    "and can be re-dispatched once a worker slot frees up."
                ),
                "retryable": True,
            }
            ledger.set_result(failure)
            ledger.set_status("blocked")
            try:
                _finalize_linked_blocked(
                    run_dir, ledger, str(failure["summary"]),
                    phase="skitarii_backpressure",
                )
            except Exception as finalize_exc:  # noqa: BLE001
                ledger.record_event(
                    "skitarii_finalize_error", {"error": str(finalize_exc)[:300]},
                )
            return failure
        if (
            _skitarii_terminal_cleanup_proven(ledger)
            and not _skitarii_exception_is_external_blocker(exc)
        ):
            return _record_skitarii_internal_contract_failure(
                run_dir,
                task_id,
                ledger,
                summary=f"Skitarii terminal contract failed: {exc}",
                error=f"{type(exc).__name__}: {exc}",
            )
        ledger.record_event("skitarii_error", {"error": str(exc)})
        failure = _bridge_failure(
            run_dir,
            task_id,
            f"Skitarii unreachable: {exc}",
            phase="skitarii_error",
            error=str(exc),
        )
        ledger.set_result(failure)
        ledger.set_status("blocked")
        try:
            _finalize_linked_blocked(run_dir, ledger, str(failure["summary"]), phase="skitarii_error")
        except Exception as finalize_exc:  # noqa: BLE001
            ledger.record_event("skitarii_finalize_error", {"error": str(finalize_exc)[:300]})
        return failure

    cancelled = _cancelled_bridge_result(run_dir, task_id)
    if cancelled is not None:
        return cancelled

    if not isinstance(verdict, dict):
        msg = "Skitarii returned a malformed non-object verdict."
        return _record_skitarii_internal_contract_failure(
            run_dir,
            task_id,
            ledger,
            summary=msg,
            error="invalid verdict shape",
        )

    accepted_value = verdict.get("accepted")
    needs_user_value = verdict.get("needs_user", False)
    verdict_schema_error = ""
    try:
        verification_findings = validate_review_findings(
            verdict.get("verification_findings", []),
            require_nonempty=(
                verdict.get("verification_degraded") is True
                or verdict.get("revision_required") is True
            ),
            context="Skitarii verdict verification_findings",
        )
    except ProtocolValidationError as exc:
        verification_findings = []
        verdict_schema_error = str(exc)
    if verdict_schema_error:
        pass
    elif str(verdict.get("task_memory_id") or "") != task_memory_id:
        verdict_schema_error = "task_memory_id does not match the delegated task page"
    elif str(verdict.get("root_task_id") or "") != root_task_id:
        verdict_schema_error = "root_task_id does not match the delegated task lineage"
    elif str(verdict.get("parent_task_id") or "") != parent_task_id:
        verdict_schema_error = "parent_task_id does not match the delegated task lineage"
    elif not isinstance(accepted_value, bool):
        verdict_schema_error = "accepted must be a boolean"
    elif not isinstance(needs_user_value, bool):
        verdict_schema_error = "needs_user must be a boolean"
    elif accepted_value and needs_user_value:
        verdict_schema_error = "accepted and needs_user cannot both be true"
    elif "artifacts" in verdict and not isinstance(verdict.get("artifacts"), list):
        verdict_schema_error = "artifacts must be a list"
    elif "rounds" in verdict and not isinstance(verdict.get("rounds"), list):
        verdict_schema_error = "rounds must be a list"
    elif accepted_value:
        checks_value = verdict.get("checks")
        held_out_count = verdict.get("held_out_check_count")
        verification_degraded = verdict.get("verification_degraded") is True
        public_replay_acceptance = (
            verdict.get("public_replay_acceptance")
            if isinstance(verdict.get("public_replay_acceptance"), dict) else {}
        )
        held_out_acceptance = (
            verdict.get("held_out_acceptance")
            if isinstance(verdict.get("held_out_acceptance"), dict) else {}
        )
        patch_bundle_value = (
            verdict.get("patch_bundle")
            if isinstance(verdict.get("patch_bundle"), dict) else {}
        )
        if not isinstance(checks_value, list) or not checks_value:
            verdict_schema_error = "accepted verdict must contain a non-empty checks list"
        elif str(verdict.get("status") or "").lower() not in {"done", "completed"}:
            verdict_schema_error = "accepted verdict must have a completed service status"
        elif verdict.get("held_out_required") is not True:
            verdict_schema_error = "accepted verdict must require the private verifier"
        elif verification_degraded and (
            type(held_out_count) is not int or held_out_count != 0
        ):
            verdict_schema_error = "degraded verdict must report zero private checks"
        elif verification_degraded and not str(
            verdict.get("held_out_status") or ""
        ).startswith("degraded_"):
            verdict_schema_error = "degraded verdict must identify the private-verifier failure"
        elif verification_degraded and public_replay_acceptance.get("accepted") is not True:
            verdict_schema_error = "degraded verdict requires successful independent public replay"
        elif verification_degraded and (
            not isinstance(verification_findings, list) or not verification_findings
        ):
            verdict_schema_error = "degraded verdict requires actionable verification findings"
        elif not verification_degraded and (
            type(held_out_count) is not int or held_out_count <= 0
        ):
            verdict_schema_error = "accepted verdict must report private verifier checks"
        elif not verification_degraded and str(verdict.get("held_out_status") or "") != "passed":
            verdict_schema_error = "accepted verdict private verifier status must be passed"
        elif not verification_degraded and held_out_acceptance.get("accepted") is not True:
            verdict_schema_error = "accepted verdict must include successful private verifier evidence"
        elif patch_bundle_value.get("apply_gate") != "accepted":
            verdict_schema_error = "accepted verdict patch bundle apply gate must be accepted"
    if verdict_schema_error:
        msg = f"Skitarii returned a malformed verdict: {verdict_schema_error}."
        return _record_skitarii_internal_contract_failure(
            run_dir,
            task_id,
            ledger,
            summary=msg,
            error=verdict_schema_error,
        )

    ledger = TaskLedger.load(run_dir / "task_ledger.json")
    accepted = accepted_value
    summary = str(verdict.get("summary") or "")
    raw_artifacts = verdict.get("artifacts") if isinstance(verdict.get("artifacts"), list) else []
    artifacts = [str(a) for a in raw_artifacts]
    rounds = verdict.get("rounds") if isinstance(verdict.get("rounds"), list) else []
    files = verdict.get("files") if isinstance(verdict.get("files"), dict) else {}
    verification_degraded = verdict.get("verification_degraded") is True

    # persist the deliverable files next to the run and in the mission dir
    saved: list[str] = []
    out_dir = run_dir / "work" / "code"
    out_dir.mkdir(parents=True, exist_ok=True)
    mdir = _mission_dir(run_dir)
    deliverable_error = ""
    for path, content in files.items():
        # keep the project's directory structure — never collapse src/x.py and
        # tests/x.py to one x.py. Normalise the relative path and block traversal.
        try:
            rel = _safe_relative_path(path)
        except SnapshotError as exc:
            deliverable_error = str(exc)
            continue
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(str(content), encoding="utf-8")
        if mdir:
            mdest = mdir / "deliverables" / rel
            mdest.parent.mkdir(parents=True, exist_ok=True)
            mdest.write_text(str(content), encoding="utf-8")
        saved.append(f"work/code/{rel}")

    # accepted patch → validate it against the live source and stage it (isolated worktree
    # re-run); the live tree is not mutated unless SKITARII_AUTOAPPLY=1.
    patch_stage = _verify_and_stage_patch(verdict, run_dir, ledger, workspace) if accepted and is_patch else None
    ready_to_apply = False
    if patch_stage and patch_stage.get("patch_file"):
        patch_path = Path(str(patch_stage["patch_file"]))
        try:
            patch_rel = str(patch_path.relative_to(run_dir)).replace("\\", "/")
        except ValueError:
            patch_rel = "work/skitarii.patch"
        if patch_rel not in saved:
            saved.append(patch_rel)
        if mdir and patch_path.is_file():
            mdest = mdir / "deliverables" / "skitarii.patch"
            mdest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(patch_path, mdest)
    autoapply = os.environ.get("SKITARII_AUTOAPPLY") == "1"
    autopublish = bool(autoapply and os.environ.get("SKITARII_AUTOPUBLISH") == "1")
    publish_pending = False
    if str((patch_stage or {}).get("publication_status") or "") == "cancelled":
        cancelled_summary = "Code mission cancelled before durable repository mutation."
        cancelled = _bridge_failure(
            run_dir,
            task_id,
            cancelled_summary,
            phase="cancelled",
            status="cancelled",
        )
        cancelled.update({
            "artifacts": saved,
            "patch_stage": patch_stage or {},
            "rounds": rounds,
            "via": "skitarii",
        })
        ledger.set_result(cancelled)
        ledger.force_status("cancelled", reason="cancelled before repository mutation")
        _finalize_linked_cancelled(
            run_dir, ledger, cancelled_summary, artifacts=saved,
        )
        return cancelled
    if accepted and deliverable_error:
        accepted = False
        summary = f"Unsafe deliverable path blocked completion: {deliverable_error}"
    elif accepted and is_patch and not _patch_stage_passed(patch_stage):
        accepted = False
        stage_reason = str((patch_stage or {}).get("reason") or (patch_stage or {}).get("error") or
                           (patch_stage or {}).get("apply_stderr") or "patch staging did not pass")
        summary = f"Patch verification blocked completion: {stage_reason}"
    elif accepted and is_patch and autoapply and not _patch_stage_passed(patch_stage, require_applied=True):
        accepted = False
        summary = "Patch passed verification but could not be applied to the live repository."
    elif (
        accepted and is_patch and autopublish
        and not _patch_stage_passed(
            patch_stage, require_applied=True, require_published=True,
        )
    ):
        accepted = False
        publication_status = str((patch_stage or {}).get("publication_status") or "")
        publish_pending = publication_status in {"publishing", "push_pending"}
        summary = (
            "Verified patch is applied and committed; origin/main publication will resume automatically."
            if publish_pending else
            "Verified patch publication stopped safely before autonomous completion."
        )
    elif accepted and is_patch and not autoapply:
        accepted = False
        ready_to_apply = True
        if patch_stage is not None:
            patch_stage["ready_to_apply"] = True
        summary = (
            "Patch verified and ready to apply, but the live repository is unchanged "
            "because SKITARII_AUTOAPPLY is not enabled."
        )
    service_verdict_status = str(verdict.get("status") or "").strip().lower()
    revision_exhausted = verdict.get("revision_exhausted") is True
    reported_revision_required = verdict.get("revision_required") is True
    reported_revision_retryable = bool(
        reported_revision_required
        and verification_findings
        # A verifier may report both a non-retryable invariant and a separate
        # repairable defect.  The mission remains revisable when at least one
        # validated finding supplies a retry path; keep every finding in the
        # action so the next attempt does not lose the non-retryable evidence.
        and any(finding.get("retryable") is True for finding in verification_findings)
    )
    worker_failed = bool(
        not accepted
        and not ready_to_apply
        and not publish_pending
        and (
            revision_exhausted
            or (
                service_verdict_status == "failed"
                and not reported_revision_retryable
            )
        )
    )
    if not accepted:
        verdict["accepted"] = False
        verdict["status"] = (
            "ready_to_apply" if ready_to_apply
            else (
                "push_pending" if publish_pending
                else (
                    "failed" if worker_failed
                    else ("revision" if verdict.get("revision_required") is True else "blocked")
                )
            )
        )

    needs_user = bool(verdict.get("needs_user"))
    question = str(verdict.get("question") or "")
    revision_required = bool(
        not accepted
        and reported_revision_required
        and not ready_to_apply
        and not publish_pending
        and not needs_user
        and not worker_failed
    )
    status = (
        "completed" if accepted
        else (
            "ready_to_apply" if ready_to_apply
            else (
                "push_pending" if publish_pending
                else (
                    "needs_user" if needs_user
                    else (
                        "failed" if worker_failed
                        else ("revision" if revision_required else "blocked")
                    )
                )
            )
        )
    )
    # This is the canonical result to which an executable revision token is
    # bound.  next_action is added only after the token exists to avoid a
    # self-referential hash.
    current_result = {
        "ok": accepted,
        "status": status,
        "final_step": "skitarii",
        "phase": status,
        "task_memory_id": task_memory_id,
        "root_task_id": root_task_id,
        "parent_task_id": parent_task_id,
        "task_checkpoint": (
            verdict.get("task_checkpoint")
            if isinstance(verdict.get("task_checkpoint"), dict)
            else {}
        ),
        "task_checkpoint_error": str(verdict.get("task_checkpoint_error") or ""),
        "workspace_checkpoint_available": isinstance(
            verdict.get("workspace_checkpoint"), dict
        ) and bool(verdict.get("workspace_checkpoint")),
        "summary": summary,
        "artifacts": saved,
        "artifact_root": str(run_dir.resolve()),
        "patch_stage": patch_stage or {},
        "ready_to_apply": ready_to_apply,
        "needs_user": needs_user,
        "question": question,
        "verification_degraded": verification_degraded,
        "verification_mode": str(verdict.get("verification_mode") or "private_held_out"),
        "verification_findings": verification_findings,
        "revision_required": revision_required,
        "revision_exhausted": revision_exhausted,
    }
    next_action = {}
    if ready_to_apply:
        next_action = {
            "kind": "apply_verified_patch",
            "artifact": "work/skitarii.patch",
            "method": "POST",
            "endpoint": "POST /runs/{task_id}/apply_patch",
            "body": {
                "expected_repository_fingerprint": str(
                    (patch_stage or {}).get("baseline_fingerprint") or ""
                ),
                "expected_patch_sha256": str((patch_stage or {}).get("patch_sha256") or ""),
                "expected_checks_sha256": str((patch_stage or {}).get("checks_sha256") or ""),
                "confirm_apply": True,
            },
            "expected_repository_fingerprint": str(
                (patch_stage or {}).get("baseline_fingerprint") or ""
            ),
            "reason": "verification passed; operator apply policy is disabled",
        }
    elif publish_pending:
        next_action = _publication_next_action(patch_stage or {}, summary)
    elif needs_user:
        next_action = _expired_clarification_action(question)
    elif revision_required:
        first_finding = verification_findings[0] if verification_findings else {}
        revision_reason = str(first_finding.get("what_failed") or summary)
        remediation = str(
            first_finding.get("remediation")
            or "Ceraxia must select another repair approach and redispatch Skitarii."
        )
        revision_owner = str(first_finding.get("revision_owner") or "governor")
        mission_meta = (
            ledger.to_dict().get("skitarii_mission")
            if isinstance(ledger.to_dict().get("skitarii_mission"), dict) else {}
        )
        if reported_revision_retryable:
            next_action = _revision_execution_action(
                task_id=task_id,
                mission_meta=mission_meta,
                findings=verification_findings,
                current_result=current_result,
                reason=revision_reason,
                remediation=remediation,
                revision_owner=revision_owner,
            )
        if not next_action:
            # Non-retryable findings and an unbound/malformed mission identity
            # remain diagnostic only; neither may become an executable command.
            next_action = {
                "kind": "revise_code_mission",
                "reason": revision_reason,
                "remediation": remediation,
                "revision_owner": revision_owner,
                "retryable": reported_revision_retryable,
                "findings": verification_findings,
            }
    elif worker_failed:
        next_action = {
            "kind": "inspect_exhausted_attempts",
            "reason": summary,
            "retryable": False,
            "findings": verification_findings,
        }
    ledger.record_event("skitarii_verdict", {"accepted": accepted, "status": status,
                                             "rounds": len(rounds),
                                             "seconds": int(time.monotonic() - started),
                                             "artifacts": saved, "next_action": next_action})
    result_payload = {**current_result, "next_action": next_action}
    if accepted:
        reconcile_action = {
            "kind": "reconcile_mission_protocol",
            "method": "POST",
            "endpoint": "POST /runs/{task_id}/apply_patch",
            "body": {
                "expected_repository_fingerprint": str((patch_stage or {}).get("baseline_fingerprint") or ""),
                "expected_patch_sha256": str((patch_stage or {}).get("patch_sha256") or ""),
                "expected_checks_sha256": str((patch_stage or {}).get("checks_sha256") or ""),
                "confirm_apply": True,
            },
            "reason": "repository publication succeeded; mission protocol finalization is in progress",
        }
        pending = dict(result_payload)
        pending.update({
            "ok": False,
            "status": "protocol_finalize_pending",
            "phase": "protocol_finalize_pending",
            "protocol_finalize_pending": True,
            "protocol_finalize_error": "",
            "next_action": reconcile_action,
        })
        ledger.set_result(pending)
        ledger.force_status(
            "protocol_finalize_pending",
            reason="mission protocol finalization in progress",
        )
        try:
            _finalize_linked_completion(run_dir, ledger, result_payload)
        except Exception as exc:  # noqa: BLE001 - repository publication is already durable.
            error = f"{type(exc).__name__}: {str(exc)[:240]}"
            ledger.record_event("skitarii_finalize_error", {"error": error})
            accepted = False
            status = "protocol_finalize_pending"
            next_action = reconcile_action
            next_action["reason"] = (
                "repository publication succeeded but mission protocol finalization must be retried"
            )
            pending.update({"protocol_finalize_error": error, "next_action": next_action})
            ledger.data["result"] = pending
            ledger.force_status(
                "protocol_finalize_pending",
                reason="mission protocol finalization pending",
            )
        else:
            ledger.data["result"] = result_payload
            ledger.force_status("completed", reason="repository publication and mission protocol completed")
    else:
        ledger.set_result(result_payload)
        ledger.force_status(
            "push_pending" if publish_pending
            else ("revision" if revision_required else ("failed" if worker_failed else "blocked")),
            reason=status,
        )
        if revision_required:
            try:
                _finalize_linked_revision(
                    run_dir,
                    ledger,
                    summary,
                    artifacts=saved,
                    findings=verification_findings,
                    next_action=next_action,
                )
            except Exception as exc:  # noqa: BLE001 - revision finalization is best-effort
                ledger.record_event(
                    "skitarii_finalize_error", {"error": f"{type(exc).__name__}: {str(exc)[:240]}"},
                )
        elif worker_failed:
            try:
                _finalize_linked_failed(
                    run_dir,
                    ledger,
                    summary,
                    artifacts=saved,
                    findings=verification_findings,
                )
            except Exception as exc:  # noqa: BLE001 - failure finalization is best-effort
                ledger.record_event(
                    "skitarii_finalize_error", {"error": f"{type(exc).__name__}: {str(exc)[:240]}"},
                )
        elif not publish_pending:
            try:
                _finalize_linked_blocked(
                    run_dir,
                    ledger,
                    summary,
                    phase=status,
                    artifacts=saved,
                    next_action=next_action,
                    needs_user=bool(verdict.get("needs_user")),
                    question=str(verdict.get("question") or ""),
                )
            except Exception as exc:  # noqa: BLE001 - blocked finalization is best-effort
                ledger.record_event(
                    "skitarii_finalize_error", {"error": f"{type(exc).__name__}: {str(exc)[:240]}"},
                )

    return {"ok": accepted, "phase": status,
            "task_id": task_id, "status": status, "summary": summary,
            "task_memory_id": task_memory_id, "root_task_id": root_task_id,
            "parent_task_id": parent_task_id,
            "artifacts": saved, "via": "skitarii", "rounds": rounds,
            "artifact_root": str(run_dir.resolve()), "final_step": "skitarii",
            "patch_stage": patch_stage, "ready_to_apply": ready_to_apply,
            "next_action": next_action, "needs_user": needs_user,
            "question": question,
            "verification_degraded": verification_degraded,
            "verification_mode": str(verdict.get("verification_mode") or "private_held_out"),
            "verification_findings": verification_findings,
            "revision_required": revision_required,
            "revision_exhausted": revision_exhausted}
