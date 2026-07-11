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
import selectors
import signal
import stat
import shutil
import subprocess
import time
import tomllib
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SKITARII_URL = os.environ.get(
    "SKITARII_URL", os.environ.get("SKITARII_WARBAND_URL", "http://127.0.0.1:7200"),
)
REPO_ROOT = Path(os.environ.get("SHUSHUNYA_REPO_ROOT", "/media/shushunya/SHUSHUNYA/shushunya"))
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
SKITARII_POLL_INTERVAL_SEC = 0.5
_MISSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


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
    ) -> None:
        super().__init__(files or {})
        self.deleted_paths = deleted_paths or []
        self.modes = modes or {}
        self.symlinks = symlinks or {}
        self.blobs = blobs or {}
        self.external_assets = external_assets or {}
        self.fingerprint = fingerprint

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


def _snapshot_content_fingerprint(snapshot: WorkspaceSnapshot) -> str:
    """Hash only the current Git-visible filesystem state, independent of Git metadata."""
    digest = hashlib.sha256(b"skitarii-content-v1\0")
    for path in sorted(snapshot.inventory):
        mode = str(
            snapshot.modes.get(path)
            or (snapshot.external_assets.get(path) or {}).get("mode")
            or "100644"
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
        else:
            kind = "file"
            content_sha = str(snapshot.external_assets[path].get("sha256") or "")
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


def _git_visible_content_fingerprint(
    root: Path,
    *,
    allowed_large: dict[str, dict[str, Any]] | None = None,
    max_files: int = MAX_CANDIDATE_FILES,
    max_total_bytes: int = MAX_CANDIDATE_TOTAL_BYTES,
    max_file_bytes: int = MAX_PATCH_FILE_BYTES,
    reject_ignored_nodes: bool = False,
) -> str:
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
    fingerprint_paths = visible_paths | set(approved)
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
    total = 0
    for rel in sorted(fingerprint_paths):
        if rel not in visible_paths:
            metadata = approved.get(rel)
            if not isinstance(metadata, dict):
                raise SnapshotError(f"patched repository path disappeared: {rel}")
            mode = str(metadata.get("mode") or "100644")
            content_sha = str(metadata.get("sha256") or "")
            if not re.fullmatch(r"[0-9a-f]{64}", content_sha):
                raise SnapshotError(f"external baseline asset digest is invalid: {rel}")
            digest.update(
                f"file\0{rel}\0{mode}\0{content_sha}\0".encode("utf-8", errors="strict"),
            )
            continue
        path = _materialize_path(root, rel)
        try:
            resolved_parent = path.parent.resolve()
        except OSError as exc:
            raise SnapshotError(f"patched repository parent cannot be resolved: {rel}") from exc
        if resolved_parent != root and root not in resolved_parent.parents:
            raise SnapshotError(f"patched repository path escapes through a symlink: {rel}")
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink():
            kind = "link"
            mode = "120000"
            target = _safe_symlink_target(rel, os.readlink(path))
            _validate_resolved_symlink(root, rel, target)
            target_bytes = target.encode("utf-8", errors="strict")
            total += len(target_bytes)
            content_sha = hashlib.sha256(target_bytes).hexdigest()
        else:
            if not path.is_file():
                raise SnapshotError(f"unsupported patched repository node: {rel}")
            kind = "file"
            # Git apply changes the worktree, not the index. Hash the actual executable
            # bit so mode-only patches and dirty index/worktree differences are visible.
            mode = "100755" if path.stat().st_mode & stat.S_IXUSR else "100644"
            size = path.stat().st_size
            metadata = approved.get(rel) if isinstance(approved.get(rel), dict) else None
            if metadata:
                content_sha = _sha256_file(path)
                expected_mode = str(metadata.get("mode") or mode)
                if (
                    size != int(metadata.get("size") or -1)
                    or content_sha != str(metadata.get("sha256") or "")
                    or mode != expected_mode
                ):
                    raise SnapshotError(f"approved large baseline asset changed: {rel}")
            else:
                if size > max_file_bytes:
                    raise SnapshotError(f"patched file exceeds {max_file_bytes} bytes: {rel}")
                total += size
                content_sha = _sha256_file(path)
        if total > max_total_bytes:
            raise SnapshotError(f"patched repository exceeds {max_total_bytes} bytes")
        digest.update(
            f"{kind}\0{rel}\0{mode}\0{content_sha}\0".encode("utf-8", errors="strict"),
        )
    return digest.hexdigest()


def _git_metadata_fingerprint(root: Path) -> str:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root,
        capture_output=True, timeout=30, check=True,
    ).stdout.strip()
    index = subprocess.run(
        ["git", "ls-files", "-s", "-z"], cwd=root,
        capture_output=True, timeout=60, check=True,
    ).stdout
    return hashlib.sha256(head + b"\0" + index).hexdigest()


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
    files are base64 encoded. Clean tracked assets above the inline cap are immutable
    hash/size manifest entries; dirty/untracked oversized files and submodules block.
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
                if size > max_external_file_bytes:
                    raise SnapshotError(
                        f"tracked external asset exceeds {max_external_file_bytes} bytes: {rel}",
                    )
                if len(external_assets) >= max_external_assets:
                    raise SnapshotError(f"repository exceeds {max_external_assets} external assets")
                if external_total + size > max_external_total_bytes:
                    raise SnapshotError(
                        f"tracked external assets exceed {max_external_total_bytes} bytes",
                    )
                if not indexed_mode:
                    raise SnapshotError(f"untracked repository file exceeds inline limit: {rel}")
                worktree_clean = subprocess.run(
                    ["git", "diff", "--quiet", "--", rel], cwd=root, timeout=30,
                ).returncode == 0
                index_clean = subprocess.run(
                    ["git", "diff", "--cached", "--quiet", "--", rel], cwd=root, timeout=30,
                ).returncode == 0
                if not worktree_clean or not index_clean:
                    raise SnapshotError(f"modified repository file exceeds inline limit: {rel}")
                if len(files) + len(blobs) + len(symlinks) + len(external_assets) >= max_files:
                    raise SnapshotError(f"repository snapshot exceeds {max_files} files")
                digest = hashlib.sha256()
                with p.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                external_assets[rel] = {
                    "size": size,
                    "sha256": digest.hexdigest(),
                    "mode": "100755" if p.stat().st_mode & stat.S_IXUSR else "100644",
                    "materialized": False,
                    "reason": "clean tracked asset exceeds inline snapshot limit",
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


def _patch_stage_passed(stage: dict[str, Any] | None, *, require_applied: bool = False) -> bool:
    passed = bool(stage and stage.get("applies_to_live") is True and
                  stage.get("tests_pass_in_worktree") is True)
    if require_applied:
        return bool(
            passed and stage.get("applied_to_live") is True
            and stage.get("post_apply_tests_passed") is True
            and not stage.get("rolled_back")
        )
    return passed


@contextmanager
def _repo_lock(root: Path, timeout: int = 30):
    """Serialize cooperating Ceraxia apply transactions for one repository."""
    import fcntl

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
                    raise TimeoutError("timed out waiting for the repository apply lock")
                time.sleep(0.1)
        yield round(time.monotonic() - started, 3)
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
) -> dict[str, Any] | None:
    """Verify a frozen patch, reject stale baselines, and transactionally auto-apply."""
    import tempfile

    patch_bundle = verdict.get("patch_bundle") if isinstance(verdict.get("patch_bundle"), dict) else None
    diff = str((patch_bundle or {}).get("unified_diff") or "")
    if not diff.strip():
        return None
    patch_file = run_dir / "work" / "skitarii.patch"
    patch_file.parent.mkdir(parents=True, exist_ok=True)

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
    expected_baseline_content = ""
    expected_post_content = ""
    pre_apply_metadata = ""
    authoritative_changed: list[str] = []

    try:
        out["resource_bounds"] = _validate_patch_resource_bounds(diff)
    except SnapshotError as exc:
        out["tests_pass_in_worktree"] = False
        out["reason"] = str(exc)
        ledger.record_event("skitarii_patch_stage", out)
        return out
    patch_file.write_text(diff, encoding="utf-8")
    patch_file.chmod(0o600)
    out["patch_file"] = str(patch_file)
    out["patch_sha256"] = _sha256_file(patch_file)

    def rollback_live_if_safe() -> None:
        if not out.get("applied_to_live") or out.get("rolled_back"):
            return
        try:
            current_content = _git_visible_content_fingerprint(
                root, allowed_large=snapshot.external_assets,
            )
            current_metadata = _git_metadata_fingerprint(root)
            if (
                not expected_post_content
                or current_content != expected_post_content
                or not pre_apply_metadata
                or current_metadata != pre_apply_metadata
            ):
                out["rollback_blocked_external_change"] = True
                return
            reverse_check = run_git(
                ["apply", "--reverse", "--check", "--binary", "-"], root, 60,
                patch_input=True,
            )
            reverse = (
                run_git(
                    ["apply", "--reverse", "--binary", "-"], root, 60,
                    patch_input=True,
                )
                if reverse_check.returncode == 0 else reverse_check
            )
            restored_content = _git_visible_content_fingerprint(
                root, allowed_large=snapshot.external_assets,
            )
            restored_metadata = _git_metadata_fingerprint(root)
            out["rolled_back"] = (
                reverse.returncode == 0
                and restored_content == expected_baseline_content
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
        expected_baseline_content = _snapshot_content_fingerprint(snapshot)
        out["baseline_fingerprint"] = expected_fingerprint
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
        checks_file.write_text(json.dumps(checks, ensure_ascii=False), encoding="utf-8")
        checks_file.chmod(0o600)
        out["checks_sha256"] = _sha256_file(checks_file)

        verify_dir = Path(tempfile.mkdtemp(prefix="skitarii-verify-"))
        try:
            _materialize_snapshot(snapshot, verify_dir)
            applied = run_git(
                ["apply", "--binary", "-"], verify_dir, 120, patch_input=True,
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
                        if PurePosixPath(path).name in {".gitignore", ".gitmodules"}
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
                )
                out["expected_post_content_fingerprint"] = expected_post_content
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
                        out["reason"] = "run was cancelled before repository apply"
                        current_ledger.record_event("skitarii_apply_cancelled", {"status": current_status})
                        return out
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    out["applies_to_live"] = False
                    out["reason"] = f"could not confirm run cancellation state: {exc}"
                    return out
            live_snapshot = _full_repo_snapshot()
            live_fingerprint = live_snapshot.fingerprint
            out["live_fingerprint_before_apply"] = live_fingerprint
            if live_fingerprint != expected_fingerprint:
                out["applies_to_live"] = False
                out["reason"] = "stale_baseline: live repository changed after mission dispatch"
                ledger.record_event("skitarii_patch_stage", out)
                return out
            pre_apply_metadata = _git_metadata_fingerprint(root)

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
                ["apply", "--check", "--binary", "-"], root, 60, patch_input=True,
            )
            out["applies_to_live"] = checked.returncode == 0
            if checked.returncode != 0:
                out["apply_stderr"] = (checked.stderr or checked.stdout or "")[:300]
                out["reason"] = "verified patch no longer applies to the live baseline"
                ledger.record_event("skitarii_patch_stage", out)
                return out

            should_autoapply = (
                os.environ.get("SKITARII_AUTOAPPLY") == "1"
                if autoapply is None else bool(autoapply)
            )
            if not should_autoapply:
                ledger.record_event("skitarii_patch_stage", out)
                return out

            live_apply = run_git(
                ["apply", "--binary", "-"], root, 60, patch_input=True,
            )
            out["applied_to_live"] = live_apply.returncode == 0
            if live_apply.returncode != 0:
                out["reason"] = "verified patch could not be applied to the live repository"
                out["apply_stderr"] = (live_apply.stderr or live_apply.stdout or "")[:300]
                ledger.record_event("skitarii_patch_stage", out)
                return out

            post_snapshot = _full_repo_snapshot(
                max_files=10_000,
                max_total_bytes=100_000_000,
                max_file_bytes=20_000_000,
            )
            post_fingerprint = post_snapshot.fingerprint
            out["post_apply_fingerprint"] = post_fingerprint
            post_content = _snapshot_content_fingerprint(post_snapshot)
            post_metadata = _git_metadata_fingerprint(root)
            out["post_apply_content_fingerprint"] = post_content
            current_fingerprint = ""
            try:
                if post_content != expected_post_content or post_metadata != pre_apply_metadata:
                    out["post_apply_tests_passed"] = False
                    out["reason"] = "live repository diverged from the verified patched baseline"
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
                current_fingerprint = _full_repo_snapshot(
                    max_files=10_000,
                    max_total_bytes=100_000_000,
                    max_file_bytes=20_000_000,
                ).fingerprint
            except Exception as exc:
                out["post_apply_tests_passed"] = False
                out["post_apply_error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
                out["reason"] = "post-apply verification infrastructure failed"
                try:
                    current_fingerprint = _full_repo_snapshot(
                        max_files=10_000,
                        max_total_bytes=100_000_000,
                        max_file_bytes=20_000_000,
                    ).fingerprint
                except Exception as fingerprint_exc:
                    out["rollback_failed"] = True
                    out["rollback_error"] = (
                        "could not prove the live post-apply state: "
                        f"{type(fingerprint_exc).__name__}: {str(fingerprint_exc)[:180]}"
                    )
            if current_fingerprint != post_fingerprint:
                out["post_apply_tests_passed"] = False
                out["reason"] = "live repository changed during post-apply verification"

            if out["post_apply_tests_passed"] is not True:
                rollback_live_if_safe()
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:300]
        if not out.get("applied_to_live") and out.get("tests_pass_in_worktree") is not True:
            out["tests_pass_in_worktree"] = False
            out["reason"] = str(exc)[:300]
        if out.get("applied_to_live") and out.get("post_apply_tests_passed") is not True:
            out["reason"] = "autoapply transaction failed; live repository requires inspection"
            rollback_live_if_safe()
    ledger.record_event("skitarii_patch_stage", out)
    return out


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
    if bool(ledger_data.get("cancel_requested")) or str(ledger_data.get("status") or "") in {"cancelling", "cancelled"}:
        return {"ok": False, "status": "cancelled", "error": "run was cancelled before apply"}
    result = ledger_data.get("result", {})
    if not isinstance(result, dict) or str(result.get("final_step") or "") != "skitarii":
        return {"ok": False, "status": "blocked", "error": "run has no Skitarii result"}
    if result.get("protocol_finalize_pending"):
        stage = result.get("patch_stage") if isinstance(result.get("patch_stage"), dict) else {}
        if (
            expected_fingerprint != str(stage.get("baseline_fingerprint") or "")
            or expected_patch_sha256 != str(stage.get("patch_sha256") or "")
            or expected_checks_sha256 != str(stage.get("checks_sha256") or "")
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
            ledger.force_status("blocked", reason="mission protocol reconciliation still pending")
            ledger.record_event("skitarii_mission_finalize_error", {"error": error})
            return {
                **pending,
                "task_id": str(ledger_data.get("task_id") or run_dir.name),
                "error": f"mission protocol reconciliation failed: {exc}",
            }
        ledger.data["result"] = reconciled
        ledger.force_status("completed", reason="mission protocol reconciliation completed")
        return {**reconciled, "task_id": str(ledger_data.get("task_id") or run_dir.name)}
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
    try:
        snapshot = _full_repo_snapshot()
    except SnapshotError as exc:
        return {"ok": False, "status": "blocked", "error": f"live repository snapshot failed: {exc}"}
    if snapshot.fingerprint != recorded:
        return {"ok": False, "status": "stale_baseline",
                "error": "live repository changed after the patch was staged"}
    verdict = {
        "accepted": True,
        "checks": checks,
        "patch_bundle": {"unified_diff": patch_text},
    }
    new_stage = _verify_and_stage_patch(
        verdict, run_dir, ledger, snapshot, autoapply=True,
    )
    if not _patch_stage_passed(new_stage, require_applied=True):
        return {
            "ok": False, "status": "blocked",
            "error": str((new_stage or {}).get("reason") or (new_stage or {}).get("error") or "apply failed"),
            "patch_stage": new_stage or {},
        }
    if (
        str((new_stage or {}).get("patch_sha256") or "") != recorded_patch_sha
        or str((new_stage or {}).get("checks_sha256") or "") != recorded_checks_sha
    ):
        return {"ok": False, "status": "blocked", "error": "verified artifacts changed during apply"}
    updated = dict(result)
    updated.update({
        "ok": True,
        "phase": "completed",
        "status": "completed",
        "summary": "Verified patch applied to the live repository and rechecked successfully.",
        "patch_stage": new_stage,
        "ready_to_apply": False,
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
                "expected_repository_fingerprint": str(new_stage.get("baseline_fingerprint") or ""),
                "expected_patch_sha256": str(new_stage.get("patch_sha256") or ""),
                "expected_checks_sha256": str(new_stage.get("checks_sha256") or ""),
                "confirm_apply": True,
            },
            "reason": "repository apply succeeded; mission protocol finalization is in progress",
        },
    })
    ledger.data["result"] = pending
    ledger.force_status("blocked", reason="mission protocol finalization in progress")
    try:
        _finalize_linked_completion(run_dir, ledger, updated)
    except Exception as exc:  # noqa: BLE001 - repository apply is already committed
        error = f"{type(exc).__name__}: {str(exc)[:240]}"
        pending["protocol_finalize_error"] = error
        pending["next_action"]["reason"] = (
            "repository apply succeeded but mission protocol finalization must be retried"
        )
        ledger.data["result"] = pending
        ledger.force_status("blocked", reason="mission protocol finalization pending")
        ledger.record_event("skitarii_mission_finalize_error", {"error": error})
        return {**pending, "task_id": str(ledger.to_dict().get("task_id") or run_dir.name)}
    ledger.data["result"] = updated
    ledger.force_status("completed", reason="verified staged patch and mission protocol completed")
    return {**updated, "task_id": str(ledger.to_dict().get("task_id") or run_dir.name)}


def apply_staged_patch(
    run_dir: Path,
    ledger: Any,
    expected_fingerprint: str,
    *,
    expected_patch_sha256: str = "",
    expected_checks_sha256: str = "",
) -> dict[str, Any]:
    """Serialize apply per run and reload durable state before compare-and-set."""
    import fcntl
    from .ledger import TaskLedger

    resolved_run = run_dir.resolve()
    lock_root = Path(os.environ.get("WARMMASTER_RUNTIME_ROOT", "/tmp")) / "skitarii-apply-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_name = hashlib.sha256(str(resolved_run).encode("utf-8")).hexdigest()
    lock_path = lock_root / f"{lock_name}.lock"
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            ledger_path = resolved_run / "task_ledger.json"
            fresh_ledger = TaskLedger.load(ledger_path)
            return _apply_staged_patch_locked(
                resolved_run,
                fresh_ledger,
                expected_fingerprint,
                expected_patch_sha256=expected_patch_sha256,
                expected_checks_sha256=expected_checks_sha256,
            )
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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
    md = str(ref.get("mission_dir") or "")
    return Path(md) if md else None


def _finalize_linked_completion(run_dir: Path, ledger: Any, result: dict[str, Any]) -> None:
    """Write a protocol-complete deterministic Ceraxia/Skitarii acceptance trail."""
    mission_dir = _mission_dir(run_dir)
    if not mission_dir or not mission_dir.exists():
        return
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
            "Skitarii private verification, isolated host recheck, and transactional "
            "repository apply all passed."
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
    if not mission_dir or not mission_dir.exists():
        return
    from . import mission_control as mc

    mission_id = str(_read_json(mission_dir / "mission.json").get("mission_id") or mission_dir.name)
    final = mc.final_response(mission_id, "blocked", summary, artifacts=artifacts or [])
    final["phase"] = phase
    final["needs_user"] = needs_user
    if question:
        final["question"] = question
    if next_action:
        final["next_action"] = next_action
    mc._write_json(mission_dir / "final_response.json", final)
    mc.record_mission_state(mission_dir, "blocked", run_status="blocked", phase=phase)
    mc.append_progress_event(
        mission_dir / "progress_events.jsonl",
        mc.progress_event(
            mission_id,
            "Ceraxia",
            "governor",
            phase,
            "blocked",
            "Варбанда Skitarii остановила код-миссию",
            (question or summary)[:400],
        ),
    )
    ledger.record_event("skitarii_protocol_blocked_recorded", {"mission_id": mission_id, "phase": phase})


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


def _service_mission_id(task_id: str, attempt: int = 1) -> str:
    safe_task = re.sub(r"[^A-Za-z0-9_.-]", "-", task_id)[:88].strip(".-") or "task"
    normalized_attempt = max(1, int(attempt))
    digest = hashlib.sha256(f"{task_id}\0{normalized_attempt}".encode("utf-8")).hexdigest()[:16]
    return f"wm-{safe_task}-{normalized_attempt}-{digest}"


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
    if (
        old_attempt > 0
        and old_status in {"planned", "queued", "running", "needs_user"}
        and str(old_meta.get("service") or "") != SKITARII_URL
    ):
        current_ledger.data["skitarii_mission"] = {**old_meta, "status": "service_mismatch"}
        current_ledger.save()
        raise RuntimeError("active Skitarii mission belongs to a different service endpoint")
    expected_old_id = _service_mission_id(task_id, old_attempt) if old_attempt else ""
    reuse_active_attempt = (
        old_attempt > 0
        and str(old_meta.get("id") or "") == expected_old_id
        and str(old_meta.get("request_sha256") or "") == request_sha256
        and old_status in {"planned", "queued", "running", "needs_user"}
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
        created = _skitarii_json_request(
            "POST",
            "/missions",
            body=json.dumps(creation_body, ensure_ascii=False).encode("utf-8"),
            timeout=min(max(float(timeout_sec), 30.0), 180.0),
            # A crash may happen after service creation but before the ledger write.
            # The deterministic id turns that race into an idempotent re-attach.
            allowed_http_statuses=frozenset({409}),
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
        or str(meta.get("status") or "") not in {"planned", "queued", "running", "needs_user", "cancelling"}
    ):
        return {"ok": False, "status": "not_active", "error": "run has no active Skitarii mission"}
    if str(meta.get("status") or "") != "cancelling":
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


def run_via_skitarii(run_dir: Path, task_id: str, timeout_sec: int = 5400) -> dict[str, Any]:
    """Execute the code mission through Skitarii and record a terminal result."""
    from .ledger import TaskLedger  # local import to avoid cycles
    from . import mission_control as mc

    contract = _read_json(run_dir / "contract.json")
    goal = str(contract.get("goal") or "")
    ledger = TaskLedger.load(run_dir / "task_ledger.json")
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
    ledger.record_event("skitarii_dispatch", {"service": SKITARII_URL, "mode": mode,
                                              "preloaded_file_count": len(inventory),
                                              "preloaded_file_sample": inventory[:50]})
    ledger.set_status("running")

    body = json.dumps({"goal": goal, "task_id": task_id, "max_wall_sec": timeout_sec,
                       "mode": mode, "workspace_files": workspace,
                       "workspace_blobs": getattr(workspace, "blobs", {}),
                       "workspace_external_assets": getattr(workspace, "external_assets", {}),
                       "workspace_inventory": inventory,
                       "workspace_deleted": getattr(workspace, "deleted_paths", []),
                       "workspace_modes": getattr(workspace, "modes", {}),
                       "workspace_symlinks": getattr(workspace, "symlinks", {})},
                      ensure_ascii=False).encode("utf-8")
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
        failure = _bridge_failure(run_dir, task_id, msg, phase="skitarii_error", error="invalid verdict shape")
        ledger.set_result(failure)
        ledger.force_status("blocked", reason="invalid Skitarii verdict shape")
        try:
            _finalize_linked_blocked(run_dir, ledger, msg, phase="skitarii_error")
        except Exception as finalize_exc:  # noqa: BLE001
            ledger.record_event("skitarii_finalize_error", {"error": str(finalize_exc)[:300]})
        return failure

    accepted_value = verdict.get("accepted")
    needs_user_value = verdict.get("needs_user", False)
    verdict_schema_error = ""
    if not isinstance(accepted_value, bool):
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
        elif type(held_out_count) is not int or held_out_count <= 0:
            verdict_schema_error = "accepted verdict must report private verifier checks"
        elif str(verdict.get("held_out_status") or "") != "passed":
            verdict_schema_error = "accepted verdict private verifier status must be passed"
        elif held_out_acceptance.get("accepted") is not True:
            verdict_schema_error = "accepted verdict must include successful private verifier evidence"
        elif patch_bundle_value.get("apply_gate") != "accepted":
            verdict_schema_error = "accepted verdict patch bundle apply gate must be accepted"
    if verdict_schema_error:
        msg = f"Skitarii returned a malformed verdict: {verdict_schema_error}."
        failure = _bridge_failure(
            run_dir, task_id, msg, phase="skitarii_error", error=verdict_schema_error,
        )
        ledger.set_result(failure)
        ledger.force_status("blocked", reason="invalid Skitarii verdict contract")
        try:
            _finalize_linked_blocked(run_dir, ledger, msg, phase="skitarii_error")
        except Exception as finalize_exc:  # noqa: BLE001
            ledger.record_event("skitarii_finalize_error", {"error": str(finalize_exc)[:300]})
        return failure

    ledger = TaskLedger.load(run_dir / "task_ledger.json")
    accepted = accepted_value
    summary = str(verdict.get("summary") or "")
    raw_artifacts = verdict.get("artifacts") if isinstance(verdict.get("artifacts"), list) else []
    artifacts = [str(a) for a in raw_artifacts]
    rounds = verdict.get("rounds") if isinstance(verdict.get("rounds"), list) else []
    files = verdict.get("files") if isinstance(verdict.get("files"), dict) else {}

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
    elif accepted and is_patch and not autoapply:
        accepted = False
        ready_to_apply = True
        if patch_stage is not None:
            patch_stage["ready_to_apply"] = True
        summary = (
            "Patch verified and ready to apply, but the live repository is unchanged "
            "because SKITARII_AUTOAPPLY is not enabled."
        )
    if not accepted:
        verdict["accepted"] = False
        verdict["status"] = "ready_to_apply" if ready_to_apply else "blocked"

    needs_user = bool(verdict.get("needs_user"))
    question = str(verdict.get("question") or "")
    status = (
        "completed" if accepted
        else ("ready_to_apply" if ready_to_apply else ("needs_user" if needs_user else "blocked"))
    )
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
    elif needs_user:
        next_action = _expired_clarification_action(question)
    ledger.record_event("skitarii_verdict", {"accepted": accepted, "status": status,
                                             "rounds": len(rounds),
                                             "seconds": int(time.monotonic() - started),
                                             "artifacts": saved, "next_action": next_action})
    result_payload = {
        "ok": accepted, "status": status, "final_step": "skitarii",
        "phase": status, "summary": summary, "artifacts": saved,
        "artifact_root": str(run_dir.resolve()),
        "patch_stage": patch_stage or {},
        "ready_to_apply": ready_to_apply,
        "next_action": next_action,
        "needs_user": needs_user,
        "question": question,
    }
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
            "reason": "repository apply succeeded; mission protocol finalization is in progress",
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
        ledger.force_status("blocked", reason="mission protocol finalization in progress")
        try:
            _finalize_linked_completion(run_dir, ledger, result_payload)
        except Exception as exc:  # noqa: BLE001 - repository apply is already committed
            error = f"{type(exc).__name__}: {str(exc)[:240]}"
            ledger.record_event("skitarii_finalize_error", {"error": error})
            accepted = False
            status = "protocol_finalize_pending"
            next_action = reconcile_action
            next_action["reason"] = (
                "repository apply succeeded but mission protocol finalization must be retried"
            )
            pending.update({"protocol_finalize_error": error, "next_action": next_action})
            ledger.data["result"] = pending
            ledger.force_status("blocked", reason="mission protocol finalization pending")
        else:
            ledger.data["result"] = result_payload
            ledger.force_status("completed", reason="repository apply and mission protocol completed")
    else:
        ledger.set_result(result_payload)
        ledger.set_status("blocked")
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
            "artifacts": saved, "via": "skitarii", "rounds": rounds,
            "artifact_root": str(run_dir.resolve()), "final_step": "skitarii",
            "patch_stage": patch_stage, "ready_to_apply": ready_to_apply,
            "next_action": next_action, "needs_user": needs_user,
            "question": question}
