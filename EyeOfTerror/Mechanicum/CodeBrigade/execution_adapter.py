#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from execution_contract import build_blocked_execution_result, build_implemented_execution_result
from execution_preflight import build_execution_preflight, is_repo_relative_path
from implementation_brief_contract import validate_implementation_brief


def extract_explicit_patch(task: str) -> dict[str, Any]:
    marker = "CERAXIA_PATCH:"
    if marker not in task:
        raise ValueError("real CodeBrigade execution adapter is not configured for tasks without explicit CERAXIA_PATCH")
    raw = task.split(marker, 1)[1].strip()
    decoder = json.JSONDecoder()
    payload, _ = decoder.raw_decode(raw)
    if not isinstance(payload, dict):
        raise ValueError("CERAXIA_PATCH payload must be a JSON object")
    operations = payload.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("CERAXIA_PATCH.operations must be a non-empty list")
    return payload


def safe_operation_path(repo: Path, rel_path: str) -> Path:
    if not is_repo_relative_path(rel_path):
        raise ValueError(f"patch path must be repo-relative: {rel_path}")
    path = repo / rel_path
    try:
        path.resolve().relative_to(repo.resolve())
    except ValueError as exc:
        raise ValueError(f"patch path escapes repo: {rel_path}") from exc
    if path.is_symlink():
        raise ValueError(f"patch path must not be a symlink: {rel_path}")
    return path


def surveyed_paths(brief: dict[str, Any]) -> set[str]:
    evidence = brief.get("repo_survey_evidence") if isinstance(brief.get("repo_survey_evidence"), dict) else {}
    values: set[str] = set()
    for key in ("candidate_files", "test_files"):
        items = evidence.get(key)
        if isinstance(items, list):
            values.update(str(item) for item in items)
    return values


def apply_patch_operations(repo: Path, brief: dict[str, Any], patch: dict[str, Any]) -> tuple[list[str], str]:
    allowed_paths = surveyed_paths(brief)
    originals: dict[Path, str | None] = {}
    changed: list[str] = []
    operations = patch["operations"]
    try:
        for index, operation in enumerate(operations):
            if not isinstance(operation, dict):
                raise ValueError(f"patch operation {index} must be an object")
            op_type = str(operation.get("type") or "")
            rel_path = str(operation.get("path") or "")
            if rel_path not in allowed_paths:
                raise ValueError(f"patch path is outside surveyed candidate/test files: {rel_path}")
            path = safe_operation_path(repo, rel_path)
            if not path.exists() or not path.is_file():
                raise ValueError(f"patch target does not exist: {rel_path}")
            if path not in originals:
                originals[path] = path.read_text(encoding="utf-8")
            if op_type == "replace":
                old = operation.get("old")
                new = operation.get("new")
                if not isinstance(old, str) or not old:
                    raise ValueError(f"replace operation requires non-empty old text: {rel_path}")
                if not isinstance(new, str):
                    raise ValueError(f"replace operation requires string new text: {rel_path}")
                content = path.read_text(encoding="utf-8")
                count = content.count(old)
                if count != 1:
                    raise ValueError(f"replace operation requires exactly one match in {rel_path}, found {count}")
                path.write_text(content.replace(old, new, 1), encoding="utf-8")
            elif op_type == "write_file":
                content = operation.get("content")
                if not isinstance(content, str):
                    raise ValueError(f"write_file operation requires string content: {rel_path}")
                if path.read_text(encoding="utf-8") != content and operation.get("overwrite") is not True:
                    raise ValueError(f"write_file target differs and overwrite is not true: {rel_path}")
                path.write_text(content, encoding="utf-8")
            else:
                raise ValueError(f"unsupported patch operation type: {op_type}")
            if rel_path not in changed:
                changed.append(rel_path)
    except Exception:
        for path, original in originals.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(original, encoding="utf-8")
        raise
    return changed, f"applied {len(operations)} explicit CERAXIA_PATCH operations"


def execute_implementation_brief(brief: dict[str, Any]) -> dict[str, Any]:
    validation_problems = validate_implementation_brief(brief)
    if validation_problems:
        return build_blocked_execution_result([f"invalid implementation brief: {problem}" for problem in validation_problems])
    preflight = build_execution_preflight(brief)
    if not preflight["ok"]:
        return build_blocked_execution_result(preflight["blockers"], preflight)
    try:
        patch = extract_explicit_patch(str(brief.get("task") or ""))
        changed_files, patch_summary = apply_patch_operations(Path(str(brief.get("repo_path") or "")), brief, patch)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as exc:
        return build_blocked_execution_result([str(exc)], preflight)
    return build_implemented_execution_result(changed_files, patch_summary, preflight)
