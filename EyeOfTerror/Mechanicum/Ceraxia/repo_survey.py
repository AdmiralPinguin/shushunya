#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "runtime",
    "runs",
    "models",
    "videos",
    "build",
    "dist",
}

SOURCE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".kt", ".java", ".go", ".rs", ".sh"}
CONFIG_SUFFIXES = {".json", ".toml", ".yaml", ".yml", ".ini", ".env"}
DOC_SUFFIXES = {".md", ".rst", ".txt"}


def excluded(path: Path, root: Path, exclude_patterns: list[str]) -> bool:
    rel_parts = path.relative_to(root).parts
    if any(part in DEFAULT_EXCLUDE_DIRS for part in rel_parts):
        return True
    rel = "/".join(rel_parts)
    return any(pattern.rstrip("/") in rel for pattern in exclude_patterns)


def score_candidate(path: Path) -> int:
    name = path.name.lower()
    suffix = path.suffix.lower()
    score = 0
    if suffix in SOURCE_SUFFIXES:
        score += 5
    if suffix in CONFIG_SUFFIXES:
        score += 3
    if suffix in DOC_SUFFIXES:
        score += 1
    if name in {"main.py", "app.py", "server.py", "cli.py", "__init__.py"}:
        score += 4
    if "test" in name or path.parent.name.lower() in {"test", "tests"}:
        score -= 2
    return score


def survey_repository(repo_path: str, focus: list[str], exclude_patterns: list[str]) -> dict[str, Any]:
    root = Path(repo_path)
    if not root.exists() or not root.is_dir():
        return {
            "kind": "ceraxia_repo_survey",
            "repo_path": str(root),
            "repo_exists": False,
            "read_only": True,
            "status": "blocked_missing_repo",
            "focus": focus,
            "exclude_patterns": exclude_patterns,
            "file_count": 0,
            "suffix_counts": {},
            "candidate_files": [],
            "test_files": [],
            "entrypoint_candidates": [],
        }
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if excluded(path, root, exclude_patterns):
            continue
        files.append(path)
        if len(files) >= 2000:
            break
    suffix_counts = Counter(path.suffix.lower() or "<none>" for path in files)
    scored = sorted(
        ((score_candidate(path), str(path.relative_to(root))) for path in files),
        key=lambda item: (-item[0], item[1]),
    )
    candidates = [path for score, path in scored if score > 0][:30]
    tests = [
        str(path.relative_to(root))
        for path in files
        if "test" in path.name.lower() or path.parent.name.lower() in {"test", "tests"}
    ][:30]
    entrypoints = [
        str(path.relative_to(root))
        for path in files
        if path.name.lower() in {"main.py", "app.py", "server.py", "cli.py"}
    ][:20]
    return {
        "kind": "ceraxia_repo_survey",
        "repo_path": str(root),
        "repo_exists": True,
        "read_only": True,
        "status": "surveyed",
        "focus": focus,
        "exclude_patterns": exclude_patterns,
        "file_count": len(files),
        "suffix_counts": dict(sorted(suffix_counts.items())),
        "candidate_files": candidates,
        "test_files": tests,
        "entrypoint_candidates": entrypoints,
    }
