#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    tomllib = None  # type: ignore[assignment]


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
PACKAGE_MANIFEST_NAMES = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
}
MAX_SURVEY_FILES = 2000
MAX_PYTHON_SYMBOL_FILES = 40
MAX_SOURCE_SUMMARY_FILES = 80


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
    if is_test_file(path):
        score -= 2
    return score


def is_test_file(path: Path) -> bool:
    name = path.name.lower()
    parent_names = {part.lower() for part in path.parts}
    return (
        "test" in name
        or name.endswith((".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx"))
        or name.endswith((".test.ts", ".test.tsx", ".test.js", ".test.jsx"))
        or bool(parent_names & {"test", "tests", "__tests__"})
    )


def safe_relative_hint(value: str) -> bool:
    path = Path(value)
    if path.is_absolute():
        return False
    return ".." not in path.parts and value.strip() not in {"", "."}


def normalized_hint(value: str) -> str:
    return value.strip().replace("\\", "/").rstrip("/")


def direct_existing_path_hints(root: Path, safe_path_hints: list[str], exclude_patterns: list[str]) -> tuple[list[str], dict[str, Path]]:
    existing: list[str] = []
    paths: dict[str, Path] = {}
    resolved_root = root.resolve()
    for hint in safe_path_hints:
        normalized = normalized_hint(str(hint))
        if not normalized:
            continue
        candidate = root / normalized
        try:
            resolved_candidate = candidate.resolve()
            resolved_candidate.relative_to(resolved_root)
        except (OSError, ValueError):
            continue
        if not candidate.exists():
            continue
        if candidate.is_file() and not excluded(candidate, root, exclude_patterns):
            paths[hint] = candidate
        existing.append(hint)
    return existing, paths


def unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def unique_edges(edges: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in edges:
        key = (str(edge.get("source", "")), str(edge.get("import", "")), str(edge.get("target", "")))
        if key in seen:
            continue
        seen.add(key)
        result.append(edge)
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
            imports.extend(relative_python_imports(path, root, node))
    return {
        "path": rel,
        "parse_error": "",
        "functions": sorted(functions)[:30],
        "classes": sorted(classes)[:30],
        "imports": sorted(set(imports))[:30],
    }


def relative_python_imports(path: Path, root: Path, node: ast.ImportFrom) -> list[str]:
    module = node.module or ""
    if node.level <= 0:
        return [f"{module}.{alias.name}".strip(".") for alias in node.names]
    package_parts = list(path.relative_to(root).parent.parts)
    keep_count = max(0, len(package_parts) - (node.level - 1))
    base_parts = package_parts[:keep_count]
    module_parts = module.split(".") if module else []
    imports: list[str] = []
    for alias in node.names:
        alias_parts = [] if alias.name == "*" else alias.name.split(".")
        imports.append(".".join([*base_parts, *module_parts, *alias_parts]).strip("."))
    return [item for item in imports if item]


def source_language(path: Path) -> str:
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".kt": "kotlin",
        ".java": "java",
        ".go": "go",
        ".rs": "rust",
        ".sh": "shell",
    }.get(path.suffix.lower(), path.suffix.lower().lstrip(".") or "unknown")


def generic_source_summary(path: Path, root: Path) -> dict[str, Any]:
    rel = str(path.relative_to(root))
    try:
        text = path.read_text(encoding="utf-8")[:200_000]
    except (OSError, UnicodeDecodeError) as exc:
        return {"path": rel, "language": source_language(path), "parse_error": str(exc), "symbols": [], "import_like": []}
    suffix = path.suffix.lower()
    patterns = [
        r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"\binterface\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"\btype\s+([A-Za-z_][A-Za-z0-9_]*)",
    ]
    if suffix in {".js", ".jsx", ".ts", ".tsx"}:
        patterns.extend(
            [
                r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)",
                r"\bconst\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?\(",
                r"\bexport\s+default\s+function\s+([A-Za-z_][A-Za-z0-9_]*)",
            ]
        )
    elif suffix == ".go":
        patterns.append(r"\bfunc\s+(?:\([^)]+\)\s*)?([A-Za-z_][A-Za-z0-9_]*)")
    elif suffix == ".rs":
        patterns.append(r"\bfn\s+([A-Za-z_][A-Za-z0-9_]*)")
    elif suffix in {".java", ".kt"}:
        patterns.append(r"\bfun\s+([A-Za-z_][A-Za-z0-9_]*)")
    elif suffix == ".sh":
        patterns.append(r"(?m)^([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{")
    symbols: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text):
            if match not in symbols:
                symbols.append(match)
    import_like: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ", "export ", "require(", "package ", "use ", "mod ")):
            import_like.append(stripped[:160])
        if len(import_like) >= 30:
            break
    return {
        "path": rel,
        "language": source_language(path),
        "parse_error": "",
        "symbols": symbols[:40],
        "import_like": import_like,
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


def relative_import_target(line: str) -> str:
    patterns = [
        r"\bfrom\s+['\"](\.[^'\"]+)['\"]",
        r"\bimport\s+[^'\"]+\s+from\s+['\"](\.[^'\"]+)['\"]",
        r"\bimport\s+['\"](\.[^'\"]+)['\"]",
        r"\bexport\s+[^'\"]+\s+from\s+['\"](\.[^'\"]+)['\"]",
        r"\bimport\s*\(\s*['\"](\.[^'\"]+)['\"]\s*\)",
        r"\brequire\s*\(\s*['\"](\.[^'\"]+)['\"]\s*\)",
    ]
    for pattern in patterns:
        match = re.search(pattern, line)
        if match:
            return match.group(1)
    return ""


def resolve_relative_source_import(source_rel: str, import_target: str, rel_to_path: dict[str, Path], root: Path) -> str:
    source_path = root / source_rel
    raw_target = (source_path.parent / import_target).resolve()
    try:
        normalized = raw_target.relative_to(root.resolve())
    except ValueError:
        return ""
    candidates: list[Path] = []
    if normalized.suffix:
        candidates.append(normalized)
    else:
        candidates.extend(
            [
                normalized.with_suffix(suffix)
                for suffix in [".ts", ".tsx", ".js", ".jsx", ".py", ".kt", ".java", ".go", ".rs"]
            ]
        )
        candidates.extend(
            normalized / f"index{suffix}"
            for suffix in [".ts", ".tsx", ".js", ".jsx"]
        )
    for candidate in candidates:
        rel = str(candidate)
        if rel in rel_to_path:
            return rel
    return ""


def build_generic_import_edges(source_summaries: list[dict[str, Any]], rel_to_path: dict[str, Path], root: Path) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for summary in source_summaries:
        source = str(summary.get("path", ""))
        language = str(summary.get("language", ""))
        import_like = summary.get("import_like") if isinstance(summary.get("import_like"), list) else []
        for line in import_like:
            target_import = relative_import_target(str(line))
            if not target_import:
                continue
            target = resolve_relative_source_import(source, target_import, rel_to_path, root)
            if not target or target == source:
                continue
            key = (source, target_import, target)
            if key in seen:
                continue
            seen.add(key)
            edges.append({"source": source, "import": target_import, "target": target, "language": language})
    return edges[:80]


def go_module_path(root: Path) -> str:
    go_mod = root / "go.mod"
    try:
        text = go_mod.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    match = re.search(r"(?m)^\s*module\s+([^\s]+)", text)
    return match.group(1).strip() if match else ""


def go_import_paths(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    text = re.sub(r"//.*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    imports: list[str] = []
    for match in re.finditer(r"(?m)^\s*import\s+(?:[._A-Za-z][\w.]*\s+)?\"([^\"]+)\"", text):
        imports.append(match.group(1))
    for block in re.finditer(r"(?ms)^\s*import\s*\((.*?)\)", text):
        for match in re.finditer(r"(?:^|\s)(?:[._A-Za-z][\w.]*\s+)?\"([^\"]+)\"", block.group(1)):
            imports.append(match.group(1))
    return unique(imports)


def build_go_module_import_edges(go_files: list[Path], root: Path) -> list[dict[str, str]]:
    module = go_module_path(root)
    if not module:
        return []
    package_files: dict[str, list[str]] = {}
    for path in go_files:
        rel = str(path.relative_to(root))
        package_dir = str(path.relative_to(root).parent)
        if package_dir == ".":
            package_dir = ""
        package_files.setdefault(package_dir, []).append(rel)
    for rows in package_files.values():
        rows.sort(key=lambda rel: (is_test_file(Path(rel)), rel))
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for path in sorted(go_files, key=lambda item: str(item.relative_to(root))):
        source = str(path.relative_to(root))
        for imported in go_import_paths(path):
            if imported == module:
                package_dir = ""
            elif imported.startswith(module + "/"):
                package_dir = imported.removeprefix(module + "/").strip("/")
            else:
                continue
            targets = package_files.get(package_dir) or []
            target = next((item for item in targets if item != source), "")
            if not target:
                continue
            key = (source, imported, target)
            if key in seen:
                continue
            seen.add(key)
            edges.append({"source": source, "import": imported, "target": target, "language": "go"})
    return edges[:80]


def rust_import_targets(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    text = re.sub(r"//.*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    targets: list[str] = []
    for match in re.finditer(r"(?m)^\s*(?:pub\s+)?mod\s+([A-Za-z_][A-Za-z0-9_]*)\s*;", text):
        targets.append("mod:" + match.group(1))
    for match in re.finditer(r"(?m)^\s*use\s+crate::([A-Za-z_][A-Za-z0-9_]*)", text):
        targets.append("crate:" + match.group(1))
    return unique(targets)


def resolve_rust_module_target(source_path: Path, import_target: str, rel_to_path: dict[str, Path], root: Path) -> str:
    if ":" not in import_target:
        return ""
    kind, module_name = import_target.split(":", 1)
    candidates: list[Path] = []
    if kind == "mod":
        candidates = [
            source_path.parent / f"{module_name}.rs",
            source_path.parent / module_name / "mod.rs",
        ]
    elif kind == "crate":
        crate_root = source_path.parent
        if source_path.name != "lib.rs" and "src" in source_path.parts:
            rel_parts = source_path.relative_to(root).parts
            if "src" in rel_parts:
                crate_root = root.joinpath(*rel_parts[: rel_parts.index("src") + 1])
        candidates = [
            crate_root / f"{module_name}.rs",
            crate_root / module_name / "mod.rs",
        ]
    for candidate in candidates:
        try:
            rel = str(candidate.resolve().relative_to(root.resolve()))
        except (OSError, ValueError):
            continue
        if rel in rel_to_path:
            return rel
    return ""


def build_rust_crate_import_edges(rust_files: list[Path], rel_to_path: dict[str, Path], root: Path) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for path in sorted(rust_files, key=lambda item: str(item.relative_to(root))):
        source = str(path.relative_to(root))
        for imported in rust_import_targets(path):
            target = resolve_rust_module_target(path, imported, rel_to_path, root)
            if not target or target == source:
                continue
            key = (source, imported, target)
            if key in seen:
                continue
            seen.add(key)
            edges.append({"source": source, "import": imported, "target": target, "language": "rust"})
    return edges[:80]


def build_repository_dependency_graph(
    files: list[Path],
    root: Path,
    dependency_edges: list[dict[str, str]],
    tests: list[str],
    entrypoints: list[str],
    contract_surface_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    contract_surfaces = {str(row.get("path") or "") for row in contract_surface_candidates if isinstance(row, dict)}
    source_paths = {str(path.relative_to(root)) for path in files if path.suffix.lower() in SOURCE_SUFFIXES}
    graph_paths = sorted(source_paths | {str(edge.get("source") or "") for edge in dependency_edges} | {str(edge.get("target") or "") for edge in dependency_edges})
    outgoing: dict[str, list[str]] = {path: [] for path in graph_paths if path}
    incoming: dict[str, list[str]] = {path: [] for path in graph_paths if path}
    language_by_path = {
        str(path.relative_to(root)): source_language(path)
        for path in files
        if path.suffix.lower() in SOURCE_SUFFIXES
    }
    for edge in dependency_edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if not source or not target:
            continue
        outgoing.setdefault(source, [])
        incoming.setdefault(target, [])
        if target not in outgoing[source]:
            outgoing[source].append(target)
        if source not in incoming[target]:
            incoming[target].append(source)
        incoming.setdefault(source, incoming.get(source, []))
        outgoing.setdefault(target, outgoing.get(target, []))
    nodes: list[dict[str, Any]] = []
    for path in sorted(set(outgoing) | set(incoming)):
        tags: list[str] = []
        if path in tests or is_test_file(Path(path)):
            tags.append("test")
        if path in entrypoints:
            tags.append("entrypoint")
        if path in contract_surfaces:
            tags.append("contract_surface")
        if incoming.get(path):
            tags.append("depended_on")
        if outgoing.get(path):
            tags.append("depends_on_others")
        nodes.append(
            {
                "path": path,
                "language": language_by_path.get(path, source_language(Path(path))),
                "tags": tags,
                "incoming_count": len(incoming.get(path, [])),
                "outgoing_count": len(outgoing.get(path, [])),
                "incoming": sorted(incoming.get(path, []))[:20],
                "outgoing": sorted(outgoing.get(path, []))[:20],
            }
        )
    language_counts = Counter(str(node["language"]) for node in nodes)
    high_impact_nodes = [
        {
            "path": node["path"],
            "reason": ",".join(node["tags"]) or "dependency_degree",
            "incoming_count": node["incoming_count"],
            "outgoing_count": node["outgoing_count"],
        }
        for node in nodes
        if node["incoming_count"] >= 2 or "entrypoint" in node["tags"] or "contract_surface" in node["tags"]
    ][:40]
    return {
        "kind": "ceraxia_repository_dependency_graph",
        "node_count": len(nodes),
        "edge_count": len(dependency_edges),
        "language_counts": dict(sorted(language_counts.items())),
        "nodes": nodes[:160],
        "edges": dependency_edges[:160],
        "reverse_index": build_reverse_dependency_index(dependency_edges),
        "high_impact_nodes": high_impact_nodes,
    }


def build_recommended_read_order(
    existing_path_hints: list[str],
    entrypoints: list[str],
    candidates: list[str],
    tests: list[str],
    edges: list[dict[str, str]],
) -> list[dict[str, str]]:
    ordered: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(path: str, reason: str) -> None:
        if not path or path in seen:
            return
        seen.add(path)
        ordered.append({"path": path, "reason": reason})

    for path in existing_path_hints:
        add(path, "explicit user path hint")
    for path in entrypoints:
        add(path, "public entrypoint candidate")
    for path in candidates:
        add(path, "ranked source/config candidate")
    for edge in edges:
        add(str(edge.get("source", "")), "dependency edge source")
        add(str(edge.get("target", "")), "dependency edge target")
    for path in tests:
        add(path, "test surface")
    return ordered[:80]


def build_reverse_dependency_index(edges: list[dict[str, str]]) -> dict[str, list[str]]:
    reverse: dict[str, list[str]] = {}
    for edge in edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if not source or not target:
            continue
        reverse.setdefault(target, [])
        if source not in reverse[target]:
            reverse[target].append(source)
    return {target: sorted(sources) for target, sources in sorted(reverse.items())}


def build_test_coverage_links(edges: list[dict[str, str]]) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for edge in edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if not source or not target or not is_test_file(Path(source)):
            continue
        key = (source, target)
        if key in seen:
            continue
        seen.add(key)
        links.append({"test": source, "target": target})
    return links[:80]


def build_missing_python_import_hints(
    python_summaries: list[dict[str, Any]],
    python_files: list[Path],
    root: Path,
) -> list[dict[str, str]]:
    module_to_path = {module_name_for(path, root): str(path.relative_to(root)) for path in python_files}
    stdlib_modules = getattr(sys, "stdlib_module_names", set())
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for summary in python_summaries:
        source = str(summary.get("path") or "")
        if not source or not is_test_file(Path(source)):
            continue
        imports = summary.get("imports") if isinstance(summary.get("imports"), list) else []
        for imported in imports:
            imported_text = str(imported)
            root_module = imported_text.split(".", 1)[0]
            if not root_module or root_module in stdlib_modules:
                continue
            if root_module in module_to_path or any(module.startswith(root_module + ".") for module in module_to_path):
                continue
            suggested_path = root_module.replace(".", "/") + ".py"
            key = (source, imported_text, suggested_path)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "source": source,
                    "import": imported_text,
                    "suggested_path": suggested_path,
                    "reason": "test imports a local-looking Python module that is not present in the surveyed repo",
                }
            )
    return rows[:80]


def build_caller_candidates(candidates: list[str], reverse_dependency_index: dict[str, list[str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        callers = reverse_dependency_index.get(candidate, [])
        if not callers:
            continue
        rows.append(
            {
                "target": candidate,
                "callers": callers[:20],
                "caller_count": len(callers),
            }
        )
    return rows[:80]


def contract_surface_score(path: Path) -> int:
    rel = "/".join(part.lower() for part in path.parts)
    name = path.name.lower()
    suffix = path.suffix.lower()
    score = 0
    if any(token in rel for token in ("api", "schema", "contract", "openapi", "swagger", "proto", "graphql", "route", "endpoint")):
        score += 4
    if suffix in {".json", ".yaml", ".yml", ".toml"}:
        score += 2
    if suffix in SOURCE_SUFFIXES and any(token in name for token in ("api", "schema", "client", "route", "endpoint", "handler")):
        score += 2
    if is_test_file(path):
        score -= 1
    return score


def build_contract_surface_candidates(files: list[Path], root: Path) -> list[dict[str, Any]]:
    scored = sorted(
        (
            (contract_surface_score(path), str(path.relative_to(root)))
            for path in files
        ),
        key=lambda item: (-item[0], item[1]),
    )
    rows: list[dict[str, Any]] = []
    for score, rel in scored:
        if score <= 0:
            continue
        rows.append({"path": rel, "score": score, "reason": "api/schema/contract naming or file type"})
        if len(rows) >= 30:
            break
    return rows


def package_manifest_row(path: Path, root: Path) -> dict[str, Any]:
    rel = str(path.relative_to(root))
    name = path.name
    row: dict[str, Any] = {
        "path": rel,
        "ecosystem": "unknown",
        "package_name": "",
        "dependency_count": 0,
        "dev_dependency_count": 0,
        "script_count": 0,
        "parse_error": "",
    }
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        row["parse_error"] = str(exc)
        return row
    try:
        if name == "package.json":
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError("package.json root is not an object")
            row.update(
                {
                    "ecosystem": "node",
                    "package_name": str(payload.get("name") or ""),
                    "dependency_count": len(payload.get("dependencies", {}) if isinstance(payload.get("dependencies"), dict) else {}),
                    "dev_dependency_count": len(payload.get("devDependencies", {}) if isinstance(payload.get("devDependencies"), dict) else {}),
                    "script_count": len(payload.get("scripts", {}) if isinstance(payload.get("scripts"), dict) else {}),
                }
            )
        elif name == "pyproject.toml":
            if tomllib is None:
                raise ValueError("tomllib is unavailable")
            payload = tomllib.loads(text)
            project = payload.get("project", {}) if isinstance(payload, dict) and isinstance(payload.get("project"), dict) else {}
            optional = project.get("optional-dependencies", {}) if isinstance(project.get("optional-dependencies"), dict) else {}
            build_system = payload.get("build-system", {}) if isinstance(payload, dict) and isinstance(payload.get("build-system"), dict) else {}
            row.update(
                {
                    "ecosystem": "python",
                    "package_name": str(project.get("name") or ""),
                    "dependency_count": len(project.get("dependencies", []) if isinstance(project.get("dependencies"), list) else []),
                    "dev_dependency_count": sum(len(items) for items in optional.values() if isinstance(items, list)),
                    "script_count": len(project.get("scripts", {}) if isinstance(project.get("scripts"), dict) else {}),
                    "build_dependency_count": len(build_system.get("requires", []) if isinstance(build_system.get("requires"), list) else []),
                }
            )
        elif name == "requirements.txt":
            dependencies = [
                line.strip()
                for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith("#") and not line.lstrip().startswith("-")
            ]
            row.update({"ecosystem": "python", "dependency_count": len(dependencies)})
        elif name == "go.mod":
            requires = [
                line.strip()
                for line in text.splitlines()
                if line.strip().startswith("require ") and not line.strip().startswith("require (")
            ]
            module_match = re.search(r"(?m)^module\s+(.+)$", text)
            row.update({"ecosystem": "go", "package_name": module_match.group(1).strip() if module_match else "", "dependency_count": len(requires)})
        elif name == "Cargo.toml":
            if tomllib is None:
                raise ValueError("tomllib is unavailable")
            payload = tomllib.loads(text)
            package = payload.get("package", {}) if isinstance(payload, dict) and isinstance(payload.get("package"), dict) else {}
            deps = payload.get("dependencies", {}) if isinstance(payload, dict) and isinstance(payload.get("dependencies"), dict) else {}
            dev_deps = payload.get("dev-dependencies", {}) if isinstance(payload, dict) and isinstance(payload.get("dev-dependencies"), dict) else {}
            row.update({"ecosystem": "rust", "package_name": str(package.get("name") or ""), "dependency_count": len(deps), "dev_dependency_count": len(dev_deps)})
        elif name in {"pom.xml", "build.gradle", "build.gradle.kts"}:
            row.update({"ecosystem": "jvm", "dependency_count": text.count("<dependency>") + len(re.findall(r"\bimplementation\s*[\(\"']", text))})
    except (json.JSONDecodeError, ValueError) as exc:
        row["parse_error"] = str(exc)
    return row


def build_package_manifest_candidates(files: list[Path], root: Path) -> list[dict[str, Any]]:
    manifests = sorted(
        [path for path in files if path.name in PACKAGE_MANIFEST_NAMES],
        key=lambda path: str(path.relative_to(root)),
    )
    return [package_manifest_row(path, root) for path in manifests[:40]]


def build_repository_cartography(
    entrypoints: list[str],
    tests: list[str],
    contract_surface_candidates: list[dict[str, Any]],
    package_manifest_candidates: list[dict[str, Any]],
    caller_candidates: list[dict[str, Any]],
    recommended_read_order: list[dict[str, str]],
    missing_python_import_hints: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    missing_python_import_hints = missing_python_import_hints or []
    risky_by_path: dict[str, set[str]] = {}
    for row in contract_surface_candidates[:20]:
        path = str(row.get("path") or "")
        if path:
            risky_by_path.setdefault(path, set()).add("contract_surface")
    for row in caller_candidates[:20]:
        path = str(row.get("target") or "")
        caller_count = int(row.get("caller_count") or 0)
        if path and caller_count > 0:
            risky_by_path.setdefault(path, set()).add(f"caller_count:{caller_count}")
    for path in entrypoints[:20]:
        risky_by_path.setdefault(path, set()).add("entrypoint")
    risky_modules = [
        {"path": path, "reasons": sorted(reasons)}
        for path, reasons in sorted(risky_by_path.items())
    ][:40]
    return {
        "kind": "ceraxia_repository_cartography",
        "entrypoints": entrypoints[:40],
        "test_inventory": tests[:60],
        "contract_surfaces": contract_surface_candidates[:40],
        "package_manifests": package_manifest_candidates[:40],
        "missing_python_import_hints": missing_python_import_hints[:40],
        "risky_modules": risky_modules,
        "recommended_read_order": recommended_read_order[:80],
        "summary": {
            "entrypoint_count": len(entrypoints),
            "test_count": len(tests),
            "contract_surface_count": len(contract_surface_candidates),
            "package_manifest_count": len(package_manifest_candidates),
            "missing_python_import_hint_count": len(missing_python_import_hints),
            "risky_module_count": len(risky_modules),
        },
    }


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
            "source_summaries": [],
            "local_import_edges": [],
            "generic_import_edges": [],
            "repository_dependency_graph": {
                "kind": "ceraxia_repository_dependency_graph",
                "node_count": 0,
                "edge_count": 0,
                "language_counts": {},
                "nodes": [],
                "edges": [],
                "reverse_index": {},
                "high_impact_nodes": [],
            },
            "reverse_dependency_index": {},
            "test_coverage_links": [],
            "caller_candidates": [],
            "contract_surface_candidates": [],
            "package_manifest_candidates": [],
            "recommended_read_order": [],
            "repository_cartography": {
                "kind": "ceraxia_repository_cartography",
                "entrypoints": [],
                "test_inventory": [],
                "contract_surfaces": [],
                "package_manifests": [],
                "missing_python_import_hints": [],
                "risky_modules": [],
                "recommended_read_order": [],
                "summary": {
                    "entrypoint_count": 0,
                    "test_count": 0,
                    "contract_surface_count": 0,
                    "package_manifest_count": 0,
                    "missing_python_import_hint_count": 0,
                    "risky_module_count": 0,
                },
            },
            "missing_python_import_hints": [],
            "suggested_verification_commands": [],
            "max_files_scanned": MAX_SURVEY_FILES,
            "truncated": False,
            "max_python_symbol_files": MAX_PYTHON_SYMBOL_FILES,
            "python_symbols_truncated": False,
            "max_source_summary_files": MAX_SOURCE_SUMMARY_FILES,
            "source_summaries_truncated": False,
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
    direct_existing_hints, direct_hint_files = direct_existing_path_hints(root, safe_path_hints, exclude_patterns)
    for hint, path in direct_hint_files.items():
        rel_to_path.setdefault(normalized_hint(hint), path)
        if path not in files:
            files.append(path)
            suffix_counts[path.suffix.lower() or "<none>"] += 1
    existing_path_hints = unique([hint for hint in safe_path_hints if normalized_hint(hint) in rel_to_path] + direct_existing_hints)
    missing_path_hints = [hint for hint in safe_path_hints if normalized_hint(hint) not in rel_to_path and hint not in direct_existing_hints]
    hinted_candidates = [
        hint
        for hint in existing_path_hints
        if score_candidate(rel_to_path[hint]) > 0 and not is_test_file(rel_to_path[hint])
    ]
    hinted_tests = [
        hint
        for hint in existing_path_hints
        if is_test_file(rel_to_path[hint])
    ]
    scored = sorted(
        ((score_candidate(path), str(path.relative_to(root))) for path in files),
        key=lambda item: (-item[0], item[1]),
    )
    candidates = unique(hinted_candidates + [path for score, path in scored if score > 0 and not is_test_file(rel_to_path[path])])[:30]
    tests = unique(hinted_tests + [
        str(path.relative_to(root))
        for path in files
        if is_test_file(path)
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
    all_source_files = [path for path in files if path.suffix.lower() in SOURCE_SUFFIXES]
    source_summaries_truncated = len(all_source_files) > MAX_SOURCE_SUMMARY_FILES
    source_summaries = [generic_source_summary(path, root) for path in all_source_files[:MAX_SOURCE_SUMMARY_FILES]]
    python_edges = build_local_import_edges(python_symbols, python_files, root)
    go_edges = build_go_module_import_edges([path for path in all_source_files if path.suffix.lower() == ".go"], root)
    rust_edges = build_rust_crate_import_edges([path for path in all_source_files if path.suffix.lower() == ".rs"], rel_to_path, root)
    generic_edges = unique_edges([*build_generic_import_edges(source_summaries, rel_to_path, root), *go_edges, *rust_edges])[:120]
    dependency_edges = unique_edges([*python_edges, *generic_edges])[:120]
    reverse_dependency_index = build_reverse_dependency_index(dependency_edges)
    test_coverage_links = build_test_coverage_links(dependency_edges)
    missing_python_import_hints = build_missing_python_import_hints(python_symbols, python_files, root)
    caller_candidates = build_caller_candidates(candidates, reverse_dependency_index)
    contract_surface_candidates = build_contract_surface_candidates(files, root)
    package_manifest_candidates = build_package_manifest_candidates(files, root)
    repository_dependency_graph = build_repository_dependency_graph(files, root, dependency_edges, tests, entrypoints, contract_surface_candidates)
    recommended_read_order = build_recommended_read_order(existing_path_hints, entrypoints, candidates, tests, dependency_edges)
    repository_cartography = build_repository_cartography(
        entrypoints,
        tests,
        contract_surface_candidates,
        package_manifest_candidates,
        caller_candidates,
        recommended_read_order,
        missing_python_import_hints,
    )
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
        "source_summaries": source_summaries,
        "local_import_edges": dependency_edges,
        "generic_import_edges": generic_edges,
        "repository_dependency_graph": repository_dependency_graph,
        "reverse_dependency_index": reverse_dependency_index,
        "test_coverage_links": test_coverage_links,
        "missing_python_import_hints": missing_python_import_hints,
        "caller_candidates": caller_candidates,
        "contract_surface_candidates": contract_surface_candidates,
        "package_manifest_candidates": package_manifest_candidates,
        "recommended_read_order": recommended_read_order,
        "repository_cartography": repository_cartography,
        "suggested_verification_commands": suggested_commands,
        "max_files_scanned": MAX_SURVEY_FILES,
        "truncated": truncated,
        "max_python_symbol_files": MAX_PYTHON_SYMBOL_FILES,
        "python_symbols_truncated": python_symbols_truncated,
        "max_source_summary_files": MAX_SOURCE_SUMMARY_FILES,
        "source_summaries_truncated": source_summaries_truncated,
    }
