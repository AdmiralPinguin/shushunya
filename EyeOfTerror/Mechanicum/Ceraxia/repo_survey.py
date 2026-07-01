#!/usr/bin/env python3
from __future__ import annotations

import ast
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


def python_summary(path: Path, root: Path) -> dict[str, Any]:
    rel = str(path.relative_to(root))
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return {"path": rel, "parse_error": str(exc), "functions": [], "classes": [], "imports": []}
    functions: list[str] = []
    classes: list[str] = []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.extend(f"{module}.{alias.name}".strip(".") for alias in node.names)
    return {
        "path": rel,
        "parse_error": "",
        "functions": sorted(functions)[:30],
        "classes": sorted(classes)[:30],
        "imports": sorted(set(imports))[:30],
    }


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
            "suggested_verification_commands": [],
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
    python_files = [path for path in files if path.suffix.lower() == ".py"][:40]
    suggested_commands: list[str] = []
    if tests:
        suggested_commands.append("python -m pytest " + " ".join(tests[:3]))
    py_compile_targets = [path for path in candidates if path.endswith(".py") and path not in tests][:5]
    if py_compile_targets:
        suggested_commands.append("python -m py_compile " + " ".join(py_compile_targets))
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
        "python_symbols": [python_summary(path, root) for path in python_files],
        "suggested_verification_commands": suggested_commands,
    }
