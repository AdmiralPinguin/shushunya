from __future__ import annotations

from pathlib import Path
from typing import Any


def python_module_name(path: str) -> str:
    if not path.endswith(".py"):
        return ""
    parts = Path(path).with_suffix("").parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(part for part in parts if part)


def test_like_path(path: str) -> bool:
    lowered = path.lower()
    name = Path(path).name.lower()
    return lowered.startswith("tests/") or name.startswith("test_") or name.endswith("_test.py") or "/test" in f"/{lowered}"


def goal_tokens(goal: str) -> set[str]:
    normalized = goal.lower()
    for char in "/_-.,:;()[]{}'\"`":
        normalized = normalized.replace(char, " ")
    return {token for token in normalized.split() if len(token) > 2}


def symbol_module_index(python_symbols: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in python_symbols:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        module = str(item.get("module") or python_module_name(path))
        if module:
            index[module] = item
    return index


def test_source_links(python_symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    modules = symbol_module_index(python_symbols)
    links: list[dict[str, Any]] = []
    for item in python_symbols:
        if not isinstance(item, dict):
            continue
        test_path = str(item.get("path") or "")
        if not test_like_path(test_path):
            continue
        imports = item.get("imports") if isinstance(item.get("imports"), list) else []
        matched_sources: list[str] = []
        for imported in imports:
            text = str(imported)
            candidates = [text, text.rsplit(".", 1)[0] if "." in text else text]
            for module in candidates:
                source = modules.get(module)
                source_path = str(source.get("path") or "") if isinstance(source, dict) else ""
                if source_path and source_path != test_path and source_path not in matched_sources:
                    matched_sources.append(source_path)
        if matched_sources:
            links.append({"test_path": test_path, "source_paths": matched_sources[:12]})
    return links


def ranked_repo_files(
    goal: str,
    candidate_files: list[str],
    test_files: list[str],
    python_symbols: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tokens = goal_tokens(goal)
    scores: dict[str, dict[str, Any]] = {}

    def add(path: str, amount: int, reason: str) -> None:
        if not path:
            return
        item = scores.setdefault(path, {"path": path, "score": 0, "reasons": []})
        item["score"] += amount
        if reason not in item["reasons"]:
            item["reasons"].append(reason)

    for path in candidate_files:
        add(path, 6, "goal_filename_match")
    for path in test_files:
        add(path, 3, "test_surface")
    for link in test_source_links(python_symbols):
        test_path = str(link.get("test_path") or "")
        add(test_path, 2, "linked_test")
        for source_path in link.get("source_paths", []):
            add(str(source_path), 8, f"imported_by:{test_path}")
    for item in python_symbols:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        haystack: list[str] = [path]
        for field in ("functions", "classes", "imports"):
            values = item.get(field) if isinstance(item.get(field), list) else []
            haystack.extend(str(value) for value in values)
        lowered = " ".join(haystack).lower().replace("_", " ").replace("/", " ")
        matched = sorted(token for token in tokens if token in lowered)
        if matched:
            add(path, 4 + min(len(matched), 4), "goal_symbol_match:" + ",".join(matched[:5]))
        if path.endswith(".py") and not test_like_path(path):
            add(path, 1, "python_source")
    return sorted(scores.values(), key=lambda item: (-int(item["score"]), str(item["path"])))[:80]


def build_repo_map(
    goal: str,
    candidate_files: list[str],
    test_files: list[str],
    python_symbols: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "ranked_files": ranked_repo_files(goal, candidate_files, test_files, python_symbols),
        "test_source_links": test_source_links(python_symbols),
        "notes": [
            "ranked_files combines goal filename matches, symbol matches, test surfaces, and test-to-source imports",
            "test_source_links are derived from static Python imports and are advisory, not proof of coverage",
        ],
    }
