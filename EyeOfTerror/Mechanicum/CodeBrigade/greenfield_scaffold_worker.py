#!/usr/bin/env python3
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any

from execution_contract import build_blocked_execution_result, build_patch_manifest
from execution_preflight import is_repo_relative_path
from greenfield_templates import GREENFIELD_MARKER


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repo_entries(repo: Path) -> list[Path]:
    if not repo.exists() or not repo.is_dir():
        return []
    return [path for path in repo.iterdir() if path.name not in {".git", "__pycache__"}]


def greenfield_workspace_status(repo: Path) -> dict[str, Any]:
    entries = repo_entries(repo)
    marker = repo / GREENFIELD_MARKER
    owned = marker.exists() and marker.is_file()
    return {
        "kind": "code_brigade_greenfield_workspace_status",
        "repo_path": str(repo),
        "repo_exists": repo.exists(),
        "repo_is_dir": repo.is_dir(),
        "marker": GREENFIELD_MARKER,
        "owned_by_ceraxia": owned,
        "top_level_entry_count": len(entries),
        "top_level_entries": sorted(path.name for path in entries)[:40],
        "greenfield_allowed": repo.exists() and repo.is_dir() and (owned or len(entries) == 0),
    }


def normalize_project_file_rows(files: Any) -> list[dict[str, str]]:
    if not isinstance(files, list):
        raise ValueError("project files must be a list")
    rows: list[dict[str, str]] = []
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise ValueError(f"project file {index} must be an object")
        rel_path = str(item.get("path") or "").strip()
        content = item.get("content")
        if not is_repo_relative_path(rel_path):
            raise ValueError(f"project file path must be repo-relative: {rel_path}")
        if Path(rel_path).parts and Path(rel_path).parts[0] in {".git", "__pycache__"}:
            raise ValueError(f"project file path targets forbidden workspace metadata: {rel_path}")
        if not isinstance(content, str):
            raise ValueError(f"project file content must be a string: {rel_path}")
        if rel_path.endswith(".py"):
            ast.parse(content)
        rows.append({"path": rel_path, "content": content})
    if not rows:
        raise ValueError("project file list is empty")
    return rows


def scaffold_greenfield_files(repo: Path, rows: list[dict[str, str]], workspace: dict[str, Any]) -> dict[str, Any]:
    operation_results: list[dict[str, Any]] = []
    changed_files: list[str] = []
    originals: dict[Path, str | None] = {}
    try:
        for index, row in enumerate(rows):
            rel_path = row["path"]
            path = repo / rel_path
            before_hash = file_sha256(path) if path.exists() and path.is_file() and not path.is_symlink() else ""
            if path.exists() and not path.is_file():
                raise ValueError(f"project file target exists and is not a file: {rel_path}")
            if path.exists() and not workspace["owned_by_ceraxia"] and rel_path != GREENFIELD_MARKER:
                raise ValueError(f"project file target already exists in unowned greenfield workspace: {rel_path}")
            path.parent.mkdir(parents=True, exist_ok=True)
            originals[path] = path.read_text(encoding="utf-8") if path.exists() else None
            path.write_text(row["content"], encoding="utf-8")
            after_hash = file_sha256(path)
            changed_files.append(rel_path)
            operation_results.append(
                {
                    "index": index,
                    "operation": "greenfield_create_or_update_file",
                    "path": rel_path,
                    "status": "applied",
                    "before_sha256": before_hash,
                    "after_sha256": after_hash,
                }
            )
    except Exception as exc:
        for path, original in originals.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(original, encoding="utf-8")
        return build_blocked_execution_result(
            [str(exc)],
            workspace,
            f"rolled back {len(originals)} greenfield files",
            operation_results,
            build_patch_manifest([], operation_results, f"rolled back {len(originals)} greenfield files"),
        )
    return {
        "kind": "code_brigade_greenfield_scaffold_report",
        "contract_version": "eye-mechanicum.v1",
        "status": "implemented",
        "changed_files": changed_files,
        "operation_results": operation_results,
        "patch_manifest": build_patch_manifest(changed_files, operation_results, ""),
    }
