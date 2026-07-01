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
MAX_SURVEY_FILES = 2000
MAX_PYTHON_SYMBOL_FILES = 40


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


def safe_relative_hint(value: str) -> bool:
    path = Path(value)
    if path.is_absolute():
        return False
    return ".." not in path.parts and value.strip() not in {"", "."}


def unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


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


def module_name_for(path: Path, root: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def build_local_import_edges(python_summaries: list[dict[str, Any]], python_files: list[Path], root: Path) -> list[dict[str, str]]:
    module_to_path = {module_name_for(path, root): str(path.relative_to(root)) for path in python_files}
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for summary in python_summaries:
        source = summary.get("path", "")
        imports = summary.get("imports") if isinstance(summary.get("imports"), list) else []
        for imported in imports:
            imported_text = str(imported)
            matched_path = ""
            for module, rel_path in sorted(module_to_path.items(), key=lambda item: len(item[0]), reverse=True):
                if imported_text == module or imported_text.startswith(module + "."):
                    matched_path = rel_path
                    break
            if not matched_path or matched_path == source:
                continue
            key = (str(source), imported_text, matched_path)
            if key in seen:
                continue
            seen.add(key)
            edges.append({"source": str(source), "import": imported_text, "target": matched_path})
    return edges[:80]


def survey_repository(repo_path: str, focus: list[str], exclude_patterns: list[str], path_hints: list[str] | None = None) -> dict[str, Any]:
    root = Path(repo_path)
    path_hints = path_hints or []
    safe_path_hints = [hint for hint in path_hints if safe_relative_hint(str(hint))]
    unsafe_path_hints = [hint for hint in path_hints if not safe_relative_hint(str(hint))]
    if not root.exists() or not root.is_dir():
        return {
            "kind": "ceraxia_repo_survey",
            "repo_path": str(root),
            "repo_exists": False,
            "read_only": True,
            "status": "blocked_missing_repo",
            "focus": focus,
            "path_hints": path_hints,
            "existing_path_hints": [],
            "missing_path_hints": safe_path_hints,
            "unsafe_path_hints": unsafe_path_hints,
            "exclude_patterns": exclude_patterns,
            "file_count": 0,
            "suffix_counts": {},
            "candidate_files": [],
            "test_files": [],
            "entrypoint_candidates": [],
            "python_symbols": [],
            "local_import_edges": [],
            "suggested_verification_commands": [],
            "max_files_scanned": MAX_SURVEY_FILES,
            "truncated": False,
            "max_python_symbol_files": MAX_PYTHON_SYMBOL_FILES,
            "python_symbols_truncated": False,
        }
    files: list[Path] = []
    truncated = False
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if excluded(path, root, exclude_patterns):
            continue
        files.append(path)
        if len(files) >= MAX_SURVEY_FILES:
            truncated = True
            break
    suffix_counts = Counter(path.suffix.lower() or "<none>" for path in files)
    rel_to_path = {str(path.relative_to(root)): path for path in files}
    existing_path_hints = [hint for hint in safe_path_hints if hint in rel_to_path]
    missing_path_hints = [hint for hint in safe_path_hints if hint not in rel_to_path]
    hinted_candidates = [
        hint
        for hint in existing_path_hints
        if score_candidate(rel_to_path[hint]) > 0 and "test" not in rel_to_path[hint].name.lower()
    ]
    hinted_tests = [
        hint
        for hint in existing_path_hints
        if "test" in rel_to_path[hint].name.lower() or rel_to_path[hint].parent.name.lower() in {"test", "tests"}
    ]
    scored = sorted(
        ((score_candidate(path), str(path.relative_to(root))) for path in files),
        key=lambda item: (-item[0], item[1]),
    )
    candidates = unique(hinted_candidates + [path for score, path in scored if score > 0])[:30]
    tests = unique(hinted_tests + [
        str(path.relative_to(root))
        for path in files
        if "test" in path.name.lower() or path.parent.name.lower() in {"test", "tests"}
    ])[:30]
    entrypoints = [
        str(path.relative_to(root))
        for path in files
        if path.name.lower() in {"main.py", "app.py", "server.py", "cli.py"}
    ][:20]
    all_python_files = [path for path in files if path.suffix.lower() == ".py"]
    python_symbols_truncated = len(all_python_files) > MAX_PYTHON_SYMBOL_FILES
    python_files = all_python_files[:MAX_PYTHON_SYMBOL_FILES]
    python_symbols = [python_summary(path, root) for path in python_files]
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
        "path_hints": path_hints,
        "existing_path_hints": existing_path_hints,
        "missing_path_hints": missing_path_hints,
        "unsafe_path_hints": unsafe_path_hints,
        "exclude_patterns": exclude_patterns,
        "file_count": len(files),
        "suffix_counts": dict(sorted(suffix_counts.items())),
        "candidate_files": candidates,
        "test_files": tests,
        "entrypoint_candidates": entrypoints,
        "python_symbols": python_symbols,
        "local_import_edges": build_local_import_edges(python_symbols, python_files, root),
        "suggested_verification_commands": suggested_commands,
        "max_files_scanned": MAX_SURVEY_FILES,
        "truncated": truncated,
        "max_python_symbol_files": MAX_PYTHON_SYMBOL_FILES,
        "python_symbols_truncated": python_symbols_truncated,
    }
