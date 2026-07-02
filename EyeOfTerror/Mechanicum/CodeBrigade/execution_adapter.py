#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from execution_contract import build_blocked_execution_result, build_implemented_execution_result, build_patch_manifest
from execution_preflight import build_execution_preflight, is_repo_relative_path
from implementation_brief_contract import validate_implementation_brief


class PatchApplicationError(ValueError):
    def __init__(self, message: str, operation_results: list[dict[str, Any]], rollback_notes: str) -> None:
        super().__init__(message)
        self.operation_results = operation_results
        self.rollback_notes = rollback_notes


def extract_explicit_patch(task: str) -> dict[str, Any]:
    marker = "CERAXIA_PATCH:"
    if marker not in task:
        inferred = infer_guarded_natural_language_patch(task)
        if inferred:
            return inferred
        raise ValueError("unshaped source mutation requires a future CodeBrigade autonomous execution adapter; current adapter only executes explicit CERAXIA_PATCH or guarded inferred single-file operations")
    raw = task.split(marker, 1)[1].strip()
    decoder = json.JSONDecoder()
    payload, _ = decoder.raw_decode(raw)
    if not isinstance(payload, dict):
        raise ValueError("CERAXIA_PATCH payload must be a JSON object")
    operations = payload.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("CERAXIA_PATCH.operations must be a non-empty list")
    payload["source"] = "explicit_json_patch"
    return payload


def infer_guarded_natural_language_patch(task: str) -> dict[str, Any]:
    replace_match = re.search(
        r"В\s+файле\s+`(?P<path>[^`]+)`.*?замени\s+`(?P<old>[^`]+)`\s+на\s+`(?P<new>[^`]+)`",
        task,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if replace_match:
        return {
            "source": "natural_language_simple_replace",
            "operations": [
                {
                    "type": "replace",
                    "path": replace_match.group("path").strip(),
                    "old": replace_match.group("old"),
                    "new": replace_match.group("new"),
                }
            ],
        }
    add_function_match = re.search(
        r"В\s+файле\s+`(?P<path>[^`]+)`.*?добавь\s+функцию\s+`(?P<name>[A-Za-z_][A-Za-z0-9_]*)`[,]?\s+возвращающую\s+`(?P<literal>[^`]+)`",
        task,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if add_function_match:
        literal = add_function_match.group("literal").strip()
        validate_safe_return_literal(literal)
        function_name = add_function_match.group("name").strip()
        return {
            "source": "natural_language_add_function",
            "operations": [
                {
                    "type": "append_python_function",
                    "path": add_function_match.group("path").strip(),
                    "function_name": function_name,
                    "return_literal": literal,
                }
            ],
        }
    create_file_match = re.search(
        r"создай\s+файл\s+`(?P<path>[^`]+)`\s+с\s+содержимым\s+`(?P<content>[^`]+)`",
        task,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if create_file_match:
        return {
            "source": "natural_language_create_file",
            "operations": [
                {
                    "type": "create_file",
                    "path": create_file_match.group("path").strip(),
                    "content": create_file_match.group("content"),
                }
            ],
        }
    return {}


def can_infer_guarded_natural_language_patch(task: str) -> bool:
    try:
        return bool(infer_guarded_natural_language_patch(task))
    except ValueError:
        return False


def infer_test_missing_function_patch(repo: Path, brief: dict[str, Any]) -> dict[str, Any]:
    candidate = infer_single_test_literal_candidate(repo, brief)
    if not candidate:
        return {}
    source_rel, source_path, function_name, literal = candidate
    if python_function_exists(source_path, function_name):
        return {}
    return {
        "source": "test_inferred_missing_function",
        "operations": [
            {
                "type": "append_python_function",
                "path": source_rel,
                "function_name": function_name,
                "return_literal": literal,
            }
        ],
    }


def infer_test_return_mismatch_patch(repo: Path, brief: dict[str, Any]) -> dict[str, Any]:
    candidate = infer_single_test_literal_candidate(repo, brief)
    if not candidate:
        return {}
    source_rel, source_path, function_name, literal = candidate
    current_return = simple_function_return_segment(source_path, function_name).get("return_expr", "")
    if not isinstance(current_return, str) or not current_return or current_return == literal:
        return {}
    validate_safe_return_literal(current_return)
    validate_safe_return_literal(literal)
    return {
        "source": "test_inferred_return_mismatch",
        "operations": [
            {
                "type": "replace_return_expression",
                "path": source_rel,
                "function_name": function_name,
                "old_expression": current_return,
                "new_expression": literal,
            }
        ],
    }


def infer_test_missing_constant_patch(repo: Path, brief: dict[str, Any]) -> dict[str, Any]:
    candidate = infer_single_test_constant_literal_candidate(repo, brief)
    if not candidate:
        return {}
    source_rel, source_path, symbol_name, literal = candidate
    if python_module_constant_exists(source_path, symbol_name):
        return {}
    return {
        "source": "test_inferred_missing_constant",
        "operations": [
            {
                "type": "append_python_constant",
                "path": source_rel,
                "symbol_name": symbol_name,
                "literal": literal,
            }
        ],
    }


def infer_test_constant_mismatch_patch(repo: Path, brief: dict[str, Any]) -> dict[str, Any]:
    candidate = infer_single_test_constant_literal_candidate(repo, brief)
    if not candidate:
        return {}
    source_rel, source_path, symbol_name, literal = candidate
    current_literal = simple_module_constant_segment(source_path, symbol_name).get("literal", "")
    if not isinstance(current_literal, str) or not current_literal or current_literal == literal:
        return {}
    validate_safe_return_literal(current_literal)
    validate_safe_return_literal(literal)
    return {
        "source": "test_inferred_constant_mismatch",
        "operations": [
            {
                "type": "replace_python_constant",
                "path": source_rel,
                "symbol_name": symbol_name,
                "old_literal": current_literal,
                "new_literal": literal,
            }
        ],
    }


def infer_single_test_literal_candidate(repo: Path, brief: dict[str, Any]) -> tuple[str, Path, str, str] | None:
    candidate_files = sorted(
        path
        for path in surveyed_paths(brief)
        if path.endswith(".py") and not is_test_path(path) and not is_docs_path(path)
    )
    test_files = sorted(path for path in surveyed_paths(brief) if path.endswith(".py") and is_test_path(path))
    if len(candidate_files) != 1 or not test_files:
        return {}
    source_rel = candidate_files[0]
    source_path = safe_operation_path(repo, source_rel)
    module_name = source_path.stem
    candidates: list[tuple[str, str]] = []
    import_pattern = re.compile(rf"(?m)^from\s+{re.escape(module_name)}\s+import\s+([A-Za-z_][A-Za-z0-9_]*)\b")
    for test_rel in test_files:
        test_path = safe_operation_path(repo, test_rel)
        if not test_path.exists() or not test_path.is_file():
            continue
        text = test_path.read_text(encoding="utf-8")
        imported_names = import_pattern.findall(text)
        for function_name in imported_names:
            literal = infer_simple_zero_arg_expected_literal(text, function_name)
            if literal:
                candidates.append((function_name, literal))
        for alias in module_import_aliases(text, module_name):
            candidates.extend(infer_simple_module_zero_arg_expected_literals(text, alias))
    unique_candidates = sorted(set(candidates))
    if len(unique_candidates) != 1:
        return None
    function_name, literal = unique_candidates[0]
    validate_safe_return_literal(literal)
    return source_rel, source_path, function_name, literal


def infer_single_test_constant_literal_candidate(repo: Path, brief: dict[str, Any]) -> tuple[str, Path, str, str] | None:
    candidate_files = sorted(
        path
        for path in surveyed_paths(brief)
        if path.endswith(".py") and not is_test_path(path) and not is_docs_path(path)
    )
    test_files = sorted(path for path in surveyed_paths(brief) if path.endswith(".py") and is_test_path(path))
    if len(candidate_files) != 1 or not test_files:
        return {}
    source_rel = candidate_files[0]
    source_path = safe_operation_path(repo, source_rel)
    module_name = source_path.stem
    candidates: list[tuple[str, str]] = []
    import_pattern = re.compile(rf"(?m)^from\s+{re.escape(module_name)}\s+import\s+([A-Za-z_][A-Za-z0-9_]*)\b")
    for test_rel in test_files:
        test_path = safe_operation_path(repo, test_rel)
        if not test_path.exists() or not test_path.is_file():
            continue
        text = test_path.read_text(encoding="utf-8")
        imported_names = import_pattern.findall(text)
        for symbol_name in imported_names:
            literal = infer_simple_imported_symbol_expected_literal(text, symbol_name)
            if literal:
                candidates.append((symbol_name, literal))
        for alias in module_import_aliases(text, module_name):
            candidates.extend(infer_simple_module_symbol_expected_literals(text, alias))
    unique_candidates = sorted(set(candidates))
    if len(unique_candidates) != 1:
        return None
    symbol_name, literal = unique_candidates[0]
    validate_safe_return_literal(literal)
    return source_rel, source_path, symbol_name, literal


def can_infer_guarded_execution(brief: dict[str, Any]) -> bool:
    task = str(brief.get("task") or "")
    if can_infer_guarded_natural_language_patch(task):
        return True
    repo_path = str(brief.get("repo_path") or "")
    if not repo_path:
        return False
    try:
        repo = Path(repo_path)
        return bool(
            infer_test_missing_function_patch(repo, brief)
            or infer_test_return_mismatch_patch(repo, brief)
            or infer_test_missing_constant_patch(repo, brief)
            or infer_test_constant_mismatch_patch(repo, brief)
        )
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return False


def infer_simple_zero_arg_expected_literal(test_text: str, function_name: str) -> str:
    escaped = re.escape(function_name)
    patterns = [
        rf"\bassert\s+{escaped}\(\)\s*==\s*(?P<literal>[^\n#]+)",
        rf"\bassert\s+{escaped}\(\)\s+is\s+(?P<literal>True|False|None)\b",
        rf"\bself\.assertEqual\(\s*{escaped}\(\)\s*,\s*(?P<literal>[^,\n\)]+)\s*\)",
    ]
    literals: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, test_text):
            literal = match.group("literal").strip()
            try:
                validate_safe_return_literal(literal)
            except ValueError:
                continue
            if literal not in literals:
                literals.append(literal)
    return literals[0] if len(literals) == 1 else ""


def module_import_aliases(test_text: str, module_name: str) -> list[str]:
    aliases: list[str] = []
    pattern = re.compile(rf"(?m)^import\s+{re.escape(module_name)}(?:\s+as\s+([A-Za-z_][A-Za-z0-9_]*))?\b")
    for match in pattern.finditer(test_text):
        alias = match.group(1) or module_name
        if alias not in aliases:
            aliases.append(alias)
    return aliases


def infer_simple_module_zero_arg_expected_literals(test_text: str, module_alias: str) -> list[tuple[str, str]]:
    escaped = re.escape(module_alias)
    candidates: list[tuple[str, str]] = []
    patterns = [
        re.compile(rf"\b{escaped}\.([A-Za-z_][A-Za-z0-9_]*)\(\)\s*==\s*(?P<literal>[^\n#]+)"),
        re.compile(rf"\bself\.assertEqual\(\s*{escaped}\.([A-Za-z_][A-Za-z0-9_]*)\(\)\s*,\s*(?P<literal>[^,\n\)]+)\s*\)"),
    ]
    for pattern in patterns:
        for match in pattern.finditer(test_text):
            function_name = match.group(1)
            literal = match.group("literal").strip()
            try:
                validate_safe_return_literal(literal)
            except ValueError:
                continue
            candidates.append((function_name, literal))
    return candidates


def infer_simple_module_symbol_expected_literals(test_text: str, module_alias: str) -> list[tuple[str, str]]:
    escaped = re.escape(module_alias)
    candidates: list[tuple[str, str]] = []
    patterns = [
        re.compile(rf"\b{escaped}\.([A-Z][A-Z0-9_]*)\s*==\s*(?P<literal>[^\n#]+)"),
        re.compile(rf"\bself\.assertEqual\(\s*{escaped}\.([A-Z][A-Z0-9_]*)\s*,\s*(?P<literal>[^,\n\)]+)\s*\)"),
    ]
    for pattern in patterns:
        for match in pattern.finditer(test_text):
            symbol_name = match.group(1)
            literal = match.group("literal").strip()
            try:
                validate_safe_return_literal(literal)
            except ValueError:
                continue
            candidates.append((symbol_name, literal))
    return candidates


def infer_simple_imported_symbol_expected_literal(test_text: str, symbol_name: str) -> str:
    escaped = re.escape(symbol_name)
    patterns = [
        rf"\bassert\s+{escaped}\s*==\s*(?P<literal>[^\n#]+)",
        rf"\bassert\s+{escaped}\s+is\s+(?P<literal>True|False|None)\b",
        rf"\bself\.assertEqual\(\s*{escaped}\s*,\s*(?P<literal>[^,\n\)]+)\s*\)",
    ]
    literals: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, test_text):
            literal = match.group("literal").strip()
            try:
                validate_safe_return_literal(literal)
            except ValueError:
                continue
            if literal not in literals:
                literals.append(literal)
    return literals[0] if len(literals) == 1 else ""


def python_function_exists(source_path: Path, function_name: str) -> bool:
    if not source_path.exists() or source_path.suffix != ".py":
        return False
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    return any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name for node in ast.walk(tree))


def python_module_constant_exists(source_path: Path, symbol_name: str) -> bool:
    return bool(simple_module_constant_segment(source_path, symbol_name))


def validate_safe_return_literal(literal: str) -> None:
    try:
        ast.parse(literal, mode="eval")
        ast.literal_eval(literal)
    except (SyntaxError, ValueError) as exc:
        detail = exc.msg if isinstance(exc, SyntaxError) else str(exc)
        raise ValueError(f"natural language add-function return value is not a safe Python literal: {detail}") from exc


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


def explicitly_missing_paths(brief: dict[str, Any]) -> set[str]:
    evidence = brief.get("repo_survey_evidence") if isinstance(brief.get("repo_survey_evidence"), dict) else {}
    items = evidence.get("missing_path_hints")
    if not isinstance(items, list):
        return set()
    return {str(item) for item in items if is_repo_relative_path(item)}


def scope_budget(brief: dict[str, Any]) -> dict[str, Any]:
    forecast = brief.get("execution_forecast") if isinstance(brief.get("execution_forecast"), dict) else {}
    budget = forecast.get("scope_budget") if isinstance(forecast.get("scope_budget"), dict) else {}
    return budget


def requested_test_edit_paths(brief: dict[str, Any]) -> set[str]:
    task = str(brief.get("task") or "").lower()
    explicit_test_edit_requested = bool(
        re.search(r"\b(update|change|edit|add|repair|tighten)\b.{0,80}\b(test|self-test|self test)\b", task)
        or re.search(r"\b(test|self-test|self test)\b.{0,80}\b(update|change|edit|add|repair|tighten)\b", task)
        or re.search(r"\b(drift|prove)\b", task)
        or re.search(r"(обнов|измени|добав|исправ).{0,80}тест", task)
        or re.search(r"тест.{0,80}(обнов|измени|добав|исправ|доказ)", task)
    )
    if not explicit_test_edit_requested:
        return set()
    evidence = brief.get("repo_survey_evidence") if isinstance(brief.get("repo_survey_evidence"), dict) else {}
    existing_hints = evidence.get("existing_path_hints") if isinstance(evidence.get("existing_path_hints"), list) else []
    test_files = evidence.get("test_files") if isinstance(evidence.get("test_files"), list) else []
    explicit_existing = {str(path) for path in existing_hints if isinstance(path, str)}
    return {
        str(path)
        for path in test_files
        if isinstance(path, str) and is_test_path(str(path)) and str(path) in explicit_existing
    }


def is_test_path(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    name = Path(rel_path).name.lower()
    return "tests" in parts or name.startswith("test_") or name.endswith("_test.py") or ".spec." in name or ".test." in name


def is_docs_path(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    suffix = Path(rel_path).suffix.lower()
    return "docs" in parts or suffix in {".md", ".rst", ".txt"}


def validate_patch_scope_budget(brief: dict[str, Any], operations: list[Any]) -> None:
    budget = scope_budget(brief)
    max_source_files = int(budget.get("max_source_files_to_edit") or 0)
    max_test_files = int(budget.get("max_test_files_to_edit_without_explicit_user_request") or 0)
    max_docs_files = int(budget.get("max_docs_files_to_edit") or 0)
    requested_test_paths = requested_test_edit_paths(brief)
    source_files: set[str] = set()
    test_files: set[str] = set()
    docs_files: set[str] = set()
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        rel_path = str(operation.get("path") or "")
        if not rel_path:
            continue
        if is_test_path(rel_path) and rel_path not in requested_test_paths:
            test_files.add(rel_path)
        elif is_docs_path(rel_path):
            docs_files.add(rel_path)
        else:
            source_files.add(rel_path)
    if max_source_files and len(source_files) > max_source_files:
        raise ValueError(f"patch source file count exceeds scope budget: {len(source_files)} > {max_source_files}")
    if len(test_files) > max_test_files:
        raise ValueError(f"patch edits test files without explicit budget: {', '.join(sorted(test_files))}")
    if max_docs_files and len(docs_files) > max_docs_files:
        raise ValueError(f"patch docs file count exceeds scope budget: {len(docs_files)} > {max_docs_files}")


def simple_function_return_segment(source_path: Path, function_name: str) -> dict[str, Any]:
    text = source_path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != function_name:
            continue
        returns = [child for child in ast.walk(node) if isinstance(child, ast.Return)]
        if len(returns) != 1 or returns[0].value is None:
            return {}
        try:
            return_expr = ast.get_source_segment(text, returns[0].value) or ""
        except Exception:
            return_expr = ""
        return {"line": returns[0].lineno, "return_expr": return_expr.strip()}
    return {}


def simple_module_constant_segment(source_path: Path, symbol_name: str) -> dict[str, Any]:
    if not source_path.exists() or source_path.suffix != ".py":
        return {}
    text = source_path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    matches: list[ast.Assign | ast.AnnAssign] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if targets == [symbol_name] and isinstance(node.value, ast.Constant):
                matches.append(node)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == symbol_name and isinstance(node.value, ast.Constant):
            matches.append(node)
    if len(matches) != 1:
        return {}
    node = matches[0]
    value = node.value
    literal = ast.get_source_segment(text, value) or repr(value.value)
    return {"line": node.lineno, "literal": literal.strip()}


def replace_return_expression_in_file(source_path: Path, function_name: str, old_expression: str, new_expression: str) -> None:
    text = source_path.read_text(encoding="utf-8")
    function = simple_function_return_segment(source_path, function_name)
    if function.get("return_expr") != old_expression:
        raise ValueError(f"current return expression for {function_name} does not match expected expression")
    try:
        ast.parse(new_expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"new return expression is not valid Python: {exc.msg}") from exc
    line_number = int(function.get("line") or 0)
    lines = text.splitlines(keepends=True)
    if line_number < 1 or line_number > len(lines):
        raise ValueError(f"return line for {function_name} is out of range")
    line = lines[line_number - 1]
    match = re.match(r"^(\s*)return\s+(.+?)(\r?\n)?$", line)
    if not match:
        raise ValueError(f"return line for {function_name} is not a simple single-line return")
    if match.group(2).strip() != old_expression:
        raise ValueError(f"return line for {function_name} does not match expected expression")
    newline = match.group(3) or ""
    lines[line_number - 1] = f"{match.group(1)}return {new_expression}{newline}"
    source_path.write_text("".join(lines), encoding="utf-8")


def replace_module_constant_in_file(source_path: Path, symbol_name: str, old_literal: str, new_literal: str) -> None:
    text = source_path.read_text(encoding="utf-8")
    constant = simple_module_constant_segment(source_path, symbol_name)
    if constant.get("literal") != old_literal:
        raise ValueError(f"current literal for {symbol_name} does not match expected literal")
    try:
        ast.parse(new_literal, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"new literal is not valid Python: {exc.msg}") from exc
    line_number = int(constant.get("line") or 0)
    lines = text.splitlines(keepends=True)
    if line_number < 1 or line_number > len(lines):
        raise ValueError(f"assignment line for {symbol_name} is out of range")
    line = lines[line_number - 1]
    match = re.match(rf"^(\s*{re.escape(symbol_name)}(?:\s*:\s*[^=]+)?\s*=\s*)(.+?)(\r?\n)?$", line)
    if not match:
        raise ValueError(f"assignment line for {symbol_name} is not a simple single-line assignment")
    if match.group(2).strip() != old_literal:
        raise ValueError(f"assignment line for {symbol_name} does not match expected literal")
    newline = match.group(3) or ""
    lines[line_number - 1] = f"{match.group(1)}{new_literal}{newline}"
    source_path.write_text("".join(lines), encoding="utf-8")


def append_python_function_to_file(source_path: Path, function_name: str, return_literal: str) -> None:
    if source_path.suffix != ".py":
        raise ValueError("append_python_function only supports Python files")
    validate_safe_return_literal(return_literal)
    text = source_path.read_text(encoding="utf-8")
    if text.strip():
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
                raise ValueError(f"function already exists: {function_name}")
    separator = "" if not text or text.endswith("\n") else "\n"
    if text.strip():
        separator += "\n"
    source_path.write_text(f"{text}{separator}def {function_name}():\n    return {return_literal}\n", encoding="utf-8")


def append_python_constant_to_file(source_path: Path, symbol_name: str, literal: str) -> None:
    if source_path.suffix != ".py":
        raise ValueError("append_python_constant only supports Python files")
    validate_safe_return_literal(literal)
    text = source_path.read_text(encoding="utf-8")
    if text.strip() and python_module_constant_exists(source_path, symbol_name):
        raise ValueError(f"constant already exists: {symbol_name}")
    separator = "" if not text or text.endswith("\n") else "\n"
    source_path.write_text(f"{text}{separator}{symbol_name} = {literal}\n", encoding="utf-8")


def apply_patch_operations(repo: Path, brief: dict[str, Any], patch: dict[str, Any]) -> tuple[list[str], str, list[dict[str, Any]], str]:
    allowed_paths = surveyed_paths(brief)
    allowed_new_paths = explicitly_missing_paths(brief)
    originals: dict[Path, str | None] = {}
    changed: list[str] = []
    operation_results: list[dict[str, Any]] = []
    operations = patch["operations"]
    validate_patch_scope_budget(brief, operations)
    try:
        for index, operation in enumerate(operations):
            if not isinstance(operation, dict):
                raise ValueError(f"patch operation {index} must be an object")
            op_type = str(operation.get("type") or "")
            rel_path = str(operation.get("path") or "")
            if op_type == "create_file":
                if rel_path not in allowed_new_paths:
                    raise ValueError(f"create_file path must be an explicit missing path hint: {rel_path}")
            elif rel_path not in allowed_paths:
                raise ValueError(f"patch path is outside surveyed candidate/test files: {rel_path}")
            path = safe_operation_path(repo, rel_path)
            if op_type == "create_file":
                if path.exists():
                    raise ValueError(f"create_file target already exists: {rel_path}")
                if not path.parent.exists() or not path.parent.is_dir() or path.parent.is_symlink():
                    raise ValueError(f"create_file parent must be an existing non-symlink directory: {rel_path}")
                content = operation.get("content")
                if not isinstance(content, str):
                    raise ValueError(f"create_file operation requires string content: {rel_path}")
                originals[path] = None
                path.write_text(content, encoding="utf-8")
            elif not path.exists() or not path.is_file():
                raise ValueError(f"patch target does not exist: {rel_path}")
            elif path not in originals:
                originals[path] = path.read_text(encoding="utf-8")
            if op_type == "create_file":
                pass
            elif op_type == "replace":
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
            elif op_type == "replace_return_expression":
                function_name = operation.get("function_name")
                old_expression = operation.get("old_expression")
                new_expression = operation.get("new_expression")
                if not isinstance(function_name, str) or not function_name:
                    raise ValueError(f"replace_return_expression requires function_name: {rel_path}")
                if not isinstance(old_expression, str) or not isinstance(new_expression, str):
                    raise ValueError(f"replace_return_expression requires old_expression and new_expression: {rel_path}")
                if path.suffix != ".py":
                    raise ValueError(f"replace_return_expression only supports Python files: {rel_path}")
                replace_return_expression_in_file(path, function_name, old_expression, new_expression)
            elif op_type == "append_python_function":
                function_name = operation.get("function_name")
                return_literal = operation.get("return_literal")
                if not isinstance(function_name, str) or not function_name:
                    raise ValueError(f"append_python_function requires function_name: {rel_path}")
                if not isinstance(return_literal, str):
                    raise ValueError(f"append_python_function requires return_literal: {rel_path}")
                append_python_function_to_file(path, function_name, return_literal)
            elif op_type == "append_python_constant":
                symbol_name = operation.get("symbol_name")
                literal = operation.get("literal")
                if not isinstance(symbol_name, str) or not symbol_name:
                    raise ValueError(f"append_python_constant requires symbol_name: {rel_path}")
                if not isinstance(literal, str):
                    raise ValueError(f"append_python_constant requires literal: {rel_path}")
                append_python_constant_to_file(path, symbol_name, literal)
            elif op_type == "replace_python_constant":
                symbol_name = operation.get("symbol_name")
                old_literal = operation.get("old_literal")
                new_literal = operation.get("new_literal")
                if not isinstance(symbol_name, str) or not symbol_name:
                    raise ValueError(f"replace_python_constant requires symbol_name: {rel_path}")
                if not isinstance(old_literal, str) or not isinstance(new_literal, str):
                    raise ValueError(f"replace_python_constant requires old_literal and new_literal: {rel_path}")
                if path.suffix != ".py":
                    raise ValueError(f"replace_python_constant only supports Python files: {rel_path}")
                replace_module_constant_in_file(path, symbol_name, old_literal, new_literal)
            else:
                raise ValueError(f"unsupported patch operation type: {op_type}")
            if rel_path not in changed:
                changed.append(rel_path)
            operation_results.append({"index": index, "operation": op_type, "path": rel_path, "status": "applied"})
    except Exception as exc:
        for path, original in originals.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(original, encoding="utf-8")
        operation_results.append(
            {
                "index": len(operation_results),
                "operation": str(operation.get("type") or "") if isinstance(operation, dict) else "",
                "path": str(operation.get("path") or "") if isinstance(operation, dict) else "",
                "status": "failed_rolled_back",
            }
        )
        rollback_notes = f"rolled back {len(originals)} touched files after patch failure"
        raise PatchApplicationError(str(exc), operation_results, rollback_notes) from exc
    source = str(patch.get("source") or "explicit_json_patch")
    return changed, f"applied {len(operations)} {source} operations", operation_results, ""


def execute_implementation_brief(brief: dict[str, Any]) -> dict[str, Any]:
    validation_problems = validate_implementation_brief(brief)
    if validation_problems:
        return build_blocked_execution_result([f"invalid implementation brief: {problem}" for problem in validation_problems])
    preflight = build_execution_preflight(brief)
    if not preflight["ok"]:
        return build_blocked_execution_result(preflight["blockers"], preflight)
    try:
        try:
            patch = extract_explicit_patch(str(brief.get("task") or ""))
        except ValueError as exc:
            if "future CodeBrigade autonomous execution adapter" not in str(exc):
                raise
            repo = Path(str(brief.get("repo_path") or ""))
            patch = (
                infer_test_missing_function_patch(repo, brief)
                or infer_test_return_mismatch_patch(repo, brief)
                or infer_test_missing_constant_patch(repo, brief)
                or infer_test_constant_mismatch_patch(repo, brief)
            )
            if not patch:
                raise
        changed_files, patch_summary, operation_results, rollback_notes = apply_patch_operations(Path(str(brief.get("repo_path") or "")), brief, patch)
    except PatchApplicationError as exc:
        return build_blocked_execution_result(
            [str(exc)],
            preflight,
            exc.rollback_notes,
            exc.operation_results,
            build_patch_manifest([], exc.operation_results, exc.rollback_notes),
        )
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as exc:
        return build_blocked_execution_result([str(exc)], preflight)
    return build_implemented_execution_result(
        changed_files,
        patch_summary,
        preflight,
        operation_results,
        build_patch_manifest(changed_files, operation_results, rollback_notes),
    )
