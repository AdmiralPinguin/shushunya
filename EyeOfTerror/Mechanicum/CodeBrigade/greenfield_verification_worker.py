#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from greenfield_architect import request_greenfield_model_guidance
from greenfield_implementation_worker import execute_module_synthesis_contracts, extract_json_object
from verification_adapter import run_verification_commands


def workspace_file_snapshots(repo: Path | None, project_brief: dict[str, Any], *, max_files: int = 12, max_chars_per_file: int = 4000) -> list[dict[str, Any]]:
    if repo is None:
        return []
    snapshots: list[dict[str, Any]] = []
    expected_files = [str(path) for path in project_brief.get("expected_files", []) if isinstance(path, str)]
    for rel_path in expected_files:
        if len(snapshots) >= max_files:
            break
        if not rel_path.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css")):
            continue
        path = _repo_relative_path(repo, rel_path)
        if path is None or not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        snapshots.append(
            {
                "path": rel_path,
                "content": text[:max_chars_per_file],
                "truncated": len(text) > max_chars_per_file,
            }
        )
    return snapshots


def repair_guidance_for_verification(project_brief: dict[str, Any], verification: dict[str, Any], signature: str, request_guidance=request_greenfield_model_guidance, repo: Path | None = None) -> dict[str, Any]:
    supported_operations = [
        {
            "type": "remove_undefined_name_line",
            "shape": {"repair_hypothesis": {"target_file": "relative/path.py", "target_line": 2, "action": "Remove the stray undefined name line."}},
            "constraints": ["only for NameError where the target line is exactly the undefined symbol"],
        },
        {
            "type": "replace_exact",
            "shape": {"operations": [{"type": "replace_exact", "path": "relative/path.py", "old_text": "exact current text", "new_text": "replacement text"}]},
            "constraints": ["old_text must exist exactly once in the target file"],
        },
        {
            "type": "replace_return_expression",
            "shape": {"operations": [{"type": "replace_return_expression", "path": "relative/path.py", "function_name": "name", "old_expression": "current_expr", "new_expression": "new_expr"}]},
            "constraints": ["Python only", "function must exist exactly once", "current return AST must match old_expression"],
        },
        {
            "type": "replace_python_constant",
            "shape": {"operations": [{"type": "replace_python_constant", "path": "relative/path.py", "symbol_name": "NAME", "old_literal": "False", "new_literal": "True"}]},
            "constraints": ["Python only", "top-level assignment must exist exactly once", "current value AST must match old_literal"],
        },
        {
            "type": "replace_function_body",
            "shape": {"operations": [{"type": "replace_function_body", "path": "relative/path.py", "function_name": "name", "old_body": "current statements", "new_body": "replacement statements"}]},
            "constraints": ["Python only", "function must exist exactly once", "current body AST must match old_body", "old_body may be a single current statement when the function is currently minimal", "this single operation may replace the entire body of one function, including multiple statements, new branches, and exception handling"],
        },
    ]
    return request_guidance(
        "GreenfieldRepairWorker",
        {
            "project_name": project_brief.get("project_name"),
            "template_id": project_brief.get("template_id"),
            "verification_status": verification.get("status"),
            "verification_results": verification.get("results", []),
            "failure_signature": signature,
            "common_failure_fixes": project_brief.get("template_contract", {}).get("common_failure_fixes", []),
            "supported_repair_operations": supported_operations,
            "workspace_file_snapshots": workspace_file_snapshots(repo, project_brief),
        },
        "Given the failed greenfield verification output, return JSON only. Choose one supported bounded repair operation when the evidence is clear, or return {\"status\":\"blocked\",\"blockers\":[...]} when no safe bounded repair applies. Use replace_function_body for multi-statement fixes inside one function, including adding branches and raising exceptions; one replace_function_body operation is allowed to replace the whole body of that single function. A minimal current function body is valid old_body evidence: for example old_body can be one current statement when workspace_file_snapshots show that statement and tests define the intended replacement behavior. Use replace_return_expression only when exactly one return expression changes. workspace_file_snapshots contain the current source and tests; derive old_text, old_expression, old_literal, or old_body from those snapshots and set the matching new_* value from the failing test evidence. Do not claim old_* is missing when the current code is present in workspace_file_snapshots. Do not invent unrelated scope.",
    )


def apply_greenfield_synthesis_repair(repo: Path, project_brief: dict[str, Any], verification: dict[str, Any], signature: str, request_guidance=request_greenfield_model_guidance) -> dict[str, Any]:
    return execute_module_synthesis_contracts(
        repo,
        project_brief,
        request_guidance,
        synthesis_stage="verification_repair",
        verification_context={
            "status": verification.get("status", ""),
            "failure_signature": signature,
            "results": verification.get("results", []),
        },
    )


def project_file_content_map(project_brief: dict[str, Any]) -> dict[str, str]:
    rows = project_brief.get("files") if isinstance(project_brief.get("files"), list) else []
    contents: dict[str, str] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        content = item.get("content")
        if path and isinstance(content, str):
            contents[path] = content
    return contents


def _repo_relative_path(repo: Path, rel_path: str) -> Path | None:
    if not rel_path or rel_path.startswith(("/", "~")):
        return None
    path = (repo / rel_path).resolve()
    try:
        path.relative_to(repo.resolve())
    except ValueError:
        return None
    return path


def _repair_spec_from_guidance(repair_guidance: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(repair_guidance, dict) or not repair_guidance.get("ok"):
        return {}
    content = repair_guidance.get("content")
    if not isinstance(content, str) or not content.strip():
        return {}
    try:
        parsed = extract_json_object(content)
    except (json.JSONDecodeError, ValueError):
        return {}
    repair_operation = parsed.get("repair_operation") if isinstance(parsed.get("repair_operation"), dict) else {}
    if isinstance(repair_operation.get("repair_hypothesis"), dict):
        hypothesis = dict(repair_operation["repair_hypothesis"])
        hypothesis.setdefault("type", repair_operation.get("type"))
        hypothesis.setdefault("operation", repair_operation.get("type"))
        return hypothesis
    hypothesis = parsed.get("repair_hypothesis") if isinstance(parsed.get("repair_hypothesis"), dict) else parsed
    if not isinstance(hypothesis, dict):
        return {}
    evidence = parsed.get("evidence") if isinstance(parsed.get("evidence"), dict) else {}
    if evidence:
        hypothesis = dict(hypothesis)
        hypothesis.setdefault("target_file", evidence.get("traceback_source") or evidence.get("target_file") or evidence.get("path"))
        hypothesis.setdefault("target_line", evidence.get("line_number") or evidence.get("target_line") or evidence.get("line"))
    hypothesis.setdefault("target_file", parsed.get("scope_boundary"))
    hypothesis.setdefault("action", parsed.get("hypothesis") or parsed.get("action") or parsed.get("repair"))
    return hypothesis


def _repair_operations_from_guidance(repair_guidance: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(repair_guidance, dict) or not repair_guidance.get("ok"):
        return []
    content = repair_guidance.get("content")
    if not isinstance(content, str) or not content.strip():
        return []
    try:
        parsed = extract_json_object(content)
    except (json.JSONDecodeError, ValueError):
        return []
    operations = parsed.get("operations")
    if isinstance(operations, list):
        return [row for row in operations if isinstance(row, dict)]
    repair_operation = parsed.get("repair_operation")
    if isinstance(repair_operation, dict):
        parent_type = str(repair_operation.get("type") or "")
        nested_operations = repair_operation.get("operations")
        if isinstance(nested_operations, list):
            rows: list[dict[str, Any]] = []
            for row in nested_operations:
                if not isinstance(row, dict):
                    continue
                next_row = dict(row)
                if parent_type and not next_row.get("type"):
                    next_row["type"] = parent_type
                rows.append(next_row)
            return rows
        if any(key in repair_operation for key in ("old_text", "new_text", "old", "new", "old_expression", "new_expression", "old_literal", "new_literal", "old_body", "new_body")):
            return [repair_operation]
    if isinstance(parsed.get("repair_hypothesis"), dict):
        hypothesis = parsed["repair_hypothesis"]
        if any(key in hypothesis for key in ("old_text", "new_text", "old", "new", "old_expression", "new_expression", "old_literal", "new_literal", "old_body", "new_body")):
            return [hypothesis]
    if any(key in parsed for key in ("old_text", "new_text", "old", "new", "old_expression", "new_expression", "old_literal", "new_literal", "old_body", "new_body")):
        return [parsed]
    return []


def _undefined_name_from_verification(verification: dict[str, Any]) -> str:
    combined = "\n".join(
        f"{item.get('stdout') or ''}\n{item.get('stderr') or ''}"
        for item in verification.get("results", [])
        if isinstance(item, dict)
    )
    match = re.search(r"NameError: name '([A-Za-z_][A-Za-z0-9_]*)' is not defined", combined)
    return match.group(1) if match else ""


def _guided_line_repair(repo: Path, verification: dict[str, Any], repair_guidance: dict[str, Any] | None) -> dict[str, Any] | None:
    spec = _repair_spec_from_guidance(repair_guidance)
    target_file = str(spec.get("target_file") or spec.get("path") or "")
    target_line = spec.get("target_line") or spec.get("line")
    action = " ".join(
        str(spec.get(key) or "")
        for key in ("action", "repair", "hypothesis")
        if spec.get(key)
    ).lower()
    if not target_file or not isinstance(target_line, int):
        return None
    path = _repo_relative_path(repo, target_file)
    if path is None or not path.exists() or not path.is_file():
        return {
            "path": target_file,
            "repair": "guided_line_repair",
            "status": "blocked",
            "blocker": "guided target file is missing or outside workspace",
        }
    undefined_name = _undefined_name_from_verification(verification)
    if not undefined_name:
        return None
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    index = target_line - 1
    if index < 0 or index >= len(lines):
        return {
            "path": target_file,
            "repair": "guided_line_repair",
            "status": "blocked",
            "blocker": "guided target line is outside file",
        }
    line = lines[index]
    if line.strip() != undefined_name:
        return None
    if action and not any(marker in action for marker in ("remove", "delete", "stray", "удал")):
        return None
    del lines[index]
    path.write_text("".join(lines), encoding="utf-8")
    return {
        "path": target_file,
        "repair": "guided_remove_undefined_name_line",
        "status": "applied",
        "target_line": target_line,
        "undefined_name": undefined_name,
    }


def _guided_exact_replace_repair(repo: Path, repair_guidance: dict[str, Any] | None) -> dict[str, Any] | None:
    operations = _repair_operations_from_guidance(repair_guidance)
    if not operations:
        return None
    repaired_files: list[dict[str, Any]] = []
    blockers: list[str] = []
    handled_count = 0
    for index, operation in enumerate(operations, start=1):
        op_type = str(operation.get("type") or operation.get("operation") or operation.get("action") or "replace_exact")
        if op_type not in {"replace", "replace_exact", "exact_replace", "replace_text"}:
            continue
        handled_count += 1
        target_file = str(operation.get("target_file") or operation.get("path") or "")
        old_text = operation.get("old_text", operation.get("old"))
        new_text = operation.get("new_text", operation.get("new"))
        if not target_file or not isinstance(old_text, str) or old_text == "" or not isinstance(new_text, str):
            blockers.append(f"guided replace operation {index} is incomplete")
            continue
        path = _repo_relative_path(repo, target_file)
        if path is None or not path.exists() or not path.is_file():
            blockers.append(f"guided replace target is missing or outside workspace: {target_file}")
            continue
        content = path.read_text(encoding="utf-8")
        match_count = content.count(old_text)
        if match_count != 1:
            blockers.append(f"guided replace requires exactly one match in {target_file}, found {match_count}")
            continue
        path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        repaired_files.append(
            {
                "path": target_file,
                "repair": "guided_exact_replace",
                "status": "applied",
                "operation_index": index,
            }
        )
    if not handled_count:
        return None
    if repaired_files:
        return {
            "path": "",
            "repair": "guided_exact_replace",
            "status": "applied",
            "repaired_files": repaired_files,
            "blockers": blockers,
        }
    if blockers:
        return {
            "path": "",
            "repair": "guided_exact_replace",
            "status": "blocked",
            "blocker": "; ".join(blockers),
        }
    return None


def _ast_expr_equal(left: ast.AST, right: ast.AST) -> bool:
    return ast.dump(left, include_attributes=False) == ast.dump(right, include_attributes=False)


def _parse_python_expression(expression: str) -> ast.AST:
    return ast.parse(expression, mode="eval").body


def _parse_python_body(body: str) -> list[ast.stmt]:
    parsed = ast.parse(body if body.endswith("\n") else body + "\n", mode="exec")
    if not parsed.body:
        raise ValueError("function body cannot be empty")
    return parsed.body


def _ast_body_equal(left: list[ast.stmt], right: list[ast.stmt]) -> bool:
    return ast.dump(ast.Module(body=left, type_ignores=[]), include_attributes=False) == ast.dump(ast.Module(body=right, type_ignores=[]), include_attributes=False)


def _replace_return_expression_in_file(source_path: Path, function_name: str, old_expression: str, new_expression: str) -> None:
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    old_expr = _parse_python_expression(old_expression)
    _parse_python_expression(new_expression)
    candidates = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name]
    if len(candidates) != 1:
        raise ValueError(f"replace_return_expression requires exactly one function named {function_name}, found {len(candidates)}")
    function = candidates[0]
    returns = [node for node in ast.walk(function) if isinstance(node, ast.Return)]
    matching_returns = [node for node in returns if node.value is not None and _ast_expr_equal(node.value, old_expr)]
    if len(matching_returns) != 1:
        raise ValueError(f"replace_return_expression requires exactly one matching return expression in {function_name}, found {len(matching_returns)}")
    return_node = matching_returns[0]
    if return_node.lineno != return_node.end_lineno:
        raise ValueError("replace_return_expression only supports single-line return statements")
    lines = source.splitlines(keepends=True)
    line_index = return_node.lineno - 1
    old_line = lines[line_index]
    prefix = old_line[: len(old_line) - len(old_line.lstrip())]
    trailing = "\n" if old_line.endswith("\n") else ""
    lines[line_index] = f"{prefix}return {new_expression}{trailing}"
    source_path.write_text("".join(lines), encoding="utf-8")


def _replace_function_body_in_file(source_path: Path, function_name: str, old_body: str, new_body: str) -> None:
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    old_nodes = _parse_python_body(old_body)
    new_nodes = _parse_python_body(new_body)
    candidates = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name]
    if len(candidates) != 1:
        raise ValueError(f"replace_function_body requires exactly one function named {function_name}, found {len(candidates)}")
    function = candidates[0]
    if not function.body:
        raise ValueError(f"function body for {function_name} is empty")
    if not _ast_body_equal(function.body, old_nodes):
        raise ValueError(f"current body for {function_name} does not match old_body")
    first_body_line = min(node.lineno for node in function.body)
    last_body_line = max((node.end_lineno or node.lineno) for node in function.body)
    if first_body_line <= function.lineno or last_body_line < first_body_line:
        raise ValueError(f"function body range for {function_name} is not recoverable")
    lines = source.splitlines(keepends=True)
    first_line_text = lines[first_body_line - 1]
    body_indent = first_line_text[: len(first_line_text) - len(first_line_text.lstrip())]
    rendered_body: list[str] = []
    for raw_line in new_body.splitlines():
        if raw_line.strip():
            rendered_body.append(body_indent + raw_line.rstrip() + "\n")
        else:
            rendered_body.append("\n")
    if not rendered_body:
        raise ValueError("new function body cannot be empty")
    lines[first_body_line - 1 : last_body_line] = rendered_body
    source_path.write_text("".join(lines), encoding="utf-8")


def _replace_python_constant_in_file(source_path: Path, symbol_name: str, old_literal: str, new_literal: str) -> None:
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    old_expr = _parse_python_expression(old_literal)
    _parse_python_expression(new_literal)
    matches: list[ast.Assign | ast.AnnAssign] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [target for target in node.targets if isinstance(target, ast.Name) and target.id == symbol_name]
            if targets:
                matches.append(node)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == symbol_name:
            matches.append(node)
    if len(matches) != 1:
        raise ValueError(f"replace_python_constant requires exactly one top-level assignment for {symbol_name}, found {len(matches)}")
    node = matches[0]
    value = node.value
    if value is None or not _ast_expr_equal(value, old_expr):
        raise ValueError(f"current value for {symbol_name} does not match old_literal")
    if node.lineno != node.end_lineno:
        raise ValueError("replace_python_constant only supports single-line assignments")
    lines = source.splitlines(keepends=True)
    line_index = node.lineno - 1
    old_line = lines[line_index]
    prefix = old_line[: len(old_line) - len(old_line.lstrip())]
    trailing = "\n" if old_line.endswith("\n") else ""
    if isinstance(node, ast.AnnAssign) and node.annotation is not None:
        annotation = ast.get_source_segment(source, node.annotation)
        if not annotation:
            raise ValueError(f"annotation source for {symbol_name} is not recoverable")
        lines[line_index] = f"{prefix}{symbol_name}: {annotation} = {new_literal}{trailing}"
    else:
        lines[line_index] = f"{prefix}{symbol_name} = {new_literal}{trailing}"
    source_path.write_text("".join(lines), encoding="utf-8")


def _guided_ast_repair(repo: Path, repair_guidance: dict[str, Any] | None) -> dict[str, Any] | None:
    operations = _repair_operations_from_guidance(repair_guidance)
    if not operations:
        return None
    repaired_files: list[dict[str, Any]] = []
    blockers: list[str] = []
    handled_count = 0
    for index, operation in enumerate(operations, start=1):
        op_type = str(operation.get("type") or operation.get("operation") or operation.get("action") or "")
        if op_type not in {"replace_return_expression", "replace_function_body", "replace_python_constant"}:
            continue
        handled_count += 1
        target_file = str(operation.get("target_file") or operation.get("path") or "")
        path = _repo_relative_path(repo, target_file)
        if path is None or not path.exists() or not path.is_file():
            blockers.append(f"{op_type} target is missing or outside workspace: {target_file}")
            continue
        if path.suffix != ".py":
            blockers.append(f"{op_type} only supports Python files: {target_file}")
            continue
        if op_type == "replace_return_expression":
            function_name = str(operation.get("function_name") or "")
            old_expression = operation.get("old_expression")
            new_expression = operation.get("new_expression")
            if not target_file or not function_name or not isinstance(old_expression, str) or not isinstance(new_expression, str):
                blockers.append(f"replace_return_expression operation {index} is incomplete")
                continue
            try:
                _replace_return_expression_in_file(path, function_name, old_expression, new_expression)
            except (SyntaxError, ValueError) as exc:
                blockers.append(f"replace_return_expression failed for {target_file}:{function_name}: {exc}")
                continue
            repaired_files.append(
                {
                    "path": target_file,
                    "repair": "guided_replace_return_expression",
                    "status": "applied",
                    "operation_index": index,
                    "function_name": function_name,
                }
            )
            continue
        if op_type == "replace_function_body":
            function_name = str(operation.get("function_name") or "")
            old_body = operation.get("old_body")
            new_body = operation.get("new_body")
            if not target_file or not function_name or not isinstance(old_body, str) or not isinstance(new_body, str):
                blockers.append(f"replace_function_body operation {index} is incomplete")
                continue
            try:
                _replace_function_body_in_file(path, function_name, old_body, new_body)
            except (SyntaxError, ValueError) as exc:
                blockers.append(f"replace_function_body failed for {target_file}:{function_name}: {exc}")
                continue
            repaired_files.append(
                {
                    "path": target_file,
                    "repair": "guided_replace_function_body",
                    "status": "applied",
                    "operation_index": index,
                    "function_name": function_name,
                }
            )
            continue
        symbol_name = str(operation.get("symbol_name") or operation.get("name") or "")
        old_literal = operation.get("old_literal", operation.get("old_expression"))
        new_literal = operation.get("new_literal", operation.get("new_expression"))
        if not target_file or not symbol_name or not isinstance(old_literal, str) or not isinstance(new_literal, str):
            blockers.append(f"replace_python_constant operation {index} is incomplete")
            continue
        try:
            _replace_python_constant_in_file(path, symbol_name, old_literal, new_literal)
        except (SyntaxError, ValueError) as exc:
            blockers.append(f"replace_python_constant failed for {target_file}:{symbol_name}: {exc}")
            continue
        repaired_files.append(
            {
                "path": target_file,
                "repair": "guided_replace_python_constant",
                "status": "applied",
                "operation_index": index,
                "symbol_name": symbol_name,
            }
        )
    if not handled_count:
        return None
    if repaired_files:
        return {
            "path": "",
            "repair": "guided_ast_repair",
            "status": "applied",
            "repaired_files": repaired_files,
            "blockers": blockers,
        }
    if blockers:
        return {
            "path": "",
            "repair": "guided_ast_repair",
            "status": "blocked",
            "blocker": "; ".join(blockers),
        }
    return None


def _purge_python_caches(repo: Path) -> list[str]:
    removed: list[str] = []
    root = repo.resolve()
    for cache_dir in repo.rglob("__pycache__"):
        try:
            cache_dir.resolve().relative_to(root)
        except ValueError:
            continue
        if not cache_dir.is_dir():
            continue
        for item in cache_dir.glob("*.pyc"):
            try:
                item.unlink()
                removed.append(str(item.relative_to(root)))
            except OSError:
                continue
        try:
            cache_dir.rmdir()
        except OSError:
            pass
    return sorted(removed)


def apply_greenfield_repair(repo: Path, project_brief: dict[str, Any], verification: dict[str, Any], repair_guidance: dict[str, Any] | None = None) -> dict[str, Any]:
    template_contents = project_file_content_map(project_brief)
    expected_files = [str(path) for path in project_brief.get("expected_files", []) if isinstance(path, str)]
    repaired_files: list[dict[str, Any]] = []
    blockers: list[str] = []
    guided_repair = _guided_line_repair(repo, verification, repair_guidance)
    if guided_repair is not None:
        if guided_repair.get("status") == "applied":
            repaired_files.append(guided_repair)
        elif guided_repair.get("blocker"):
            blockers.append(str(guided_repair["blocker"]))
    guided_replace = _guided_exact_replace_repair(repo, repair_guidance)
    if guided_replace is not None:
        if guided_replace.get("status") == "applied":
            repaired_files.extend([row for row in guided_replace.get("repaired_files", []) if isinstance(row, dict)])
            blockers.extend(str(item) for item in guided_replace.get("blockers", []) if isinstance(item, str))
        elif guided_replace.get("blocker"):
            blockers.append(str(guided_replace["blocker"]))
    guided_ast = _guided_ast_repair(repo, repair_guidance)
    if guided_ast is not None:
        if guided_ast.get("status") == "applied":
            repaired_files.extend([row for row in guided_ast.get("repaired_files", []) if isinstance(row, dict)])
            blockers.extend(str(item) for item in guided_ast.get("blockers", []) if isinstance(item, str))
        elif guided_ast.get("blocker"):
            blockers.append(str(guided_ast["blocker"]))
    for rel_path in expected_files:
        if rel_path == "greenfield_project_brief.json":
            continue
        path = repo / rel_path
        if path.exists():
            continue
        if rel_path not in template_contents:
            blockers.append(f"missing file has no template repair content: {rel_path}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(template_contents[rel_path], encoding="utf-8")
        repaired_files.append({"path": rel_path, "repair": "restored_missing_template_file"})
    readme = repo / "README.md"
    if readme.exists() and readme.is_file():
        text = readme.read_text(encoding="utf-8")
        additions: list[str] = []
        for command in project_brief.get("run_commands", []):
            if isinstance(command, str) and command and command not in text:
                additions.append(f"```bash\n{command}\n```")
        for command in project_brief.get("verification_commands", []):
            if isinstance(command, str) and command and command not in text:
                additions.append(f"```bash\n{command}\n```")
        if additions:
            readme.write_text(text.rstrip() + "\n\n## Repaired Commands\n\n" + "\n\n".join(additions) + "\n", encoding="utf-8")
            repaired_files.append({"path": "README.md", "repair": "added_missing_contract_commands"})
    elif "README.md" in template_contents:
        readme.write_text(template_contents["README.md"], encoding="utf-8")
        repaired_files.append({"path": "README.md", "repair": "restored_missing_template_file"})
    if not repaired_files and not blockers:
        blockers.append("no bounded greenfield repair was applicable")
    if verification.get("status") == "failed" and repaired_files:
        behavior_repair_files = [
            row
            for row in repaired_files
            if str(row.get("path") or "").endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css"))
        ]
        if not behavior_repair_files:
            blockers.append("failed verification received only metadata repair; no source or test behavior changed")
    purged_caches = _purge_python_caches(repo) if repaired_files else []
    return {
        "kind": "code_brigade_greenfield_repair_execution",
        "contract_version": "eye-mechanicum.v1",
        "status": "applied" if repaired_files and not blockers else "not_applicable",
        "repaired_files": repaired_files,
        "blockers": blockers,
        "purged_python_caches": purged_caches,
        "verification_status_before": verification.get("status", ""),
    }


def verification_failure_signature(verification: dict[str, Any]) -> str:
    return json.dumps(
        [
            {
                "command": item.get("command"),
                "status": item.get("status"),
                "stderr": str(item.get("stderr") or "")[-500:],
            }
            for item in verification.get("results", [])
            if isinstance(item, dict)
        ],
        ensure_ascii=False,
        sort_keys=True,
    )


def build_stop_condition_evidence(
    reason: str,
    attempts: list[dict[str, Any]],
    final_verification: dict[str, Any],
    repeated_signature: bool = False,
) -> dict[str, Any]:
    combined_error = "\n".join(
        str(row.get("stderr") or "")
        for row in final_verification.get("results", [])
        if isinstance(row, dict)
    ).lower()
    return {
        "kind": "code_brigade_greenfield_stop_condition_evidence",
        "reason": reason,
        "attempt_count": len(attempts),
        "repair_attempt_count": sum(1 for attempt in attempts if isinstance(attempt.get("repair_execution"), dict)),
        "repeated_failure_signature": repeated_signature,
        "dependency_unavailable_hint": "module not found" in combined_error or "no module named" in combined_error,
        "secret_required_hint": "token" in combined_error or "api key" in combined_error or "secret" in combined_error,
        "system_package_hint": "command not found" in combined_error or "no such file or directory" in combined_error,
        "final_status": final_verification.get("status", ""),
    }


def verification_loop_result(
    status: str,
    attempts: list[dict[str, Any]],
    final_verification: dict[str, Any],
    stop_reason: str,
    repeated_signature: bool = False,
) -> dict[str, Any]:
    return {
        "kind": "code_brigade_greenfield_verification_loop",
        "status": status,
        "attempts": attempts,
        "final_verification": final_verification,
        "stop_reason": stop_reason,
        "stop_condition_evidence": build_stop_condition_evidence(stop_reason, attempts, final_verification, repeated_signature),
    }


def run_greenfield_verification_loop(repo: Path, commands: list[str], project_brief: dict[str, Any], max_cycles: int = 2, request_guidance=request_greenfield_model_guidance) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    previous_signature = ""
    final_verification: dict[str, Any] = {}
    for cycle in range(1, max_cycles + 1):
        verification = run_verification_commands(commands, str(repo), execute=True)
        final_verification = verification
        signature = verification_failure_signature(verification)
        if verification.get("status") == "passed":
            attempts.append({"cycle": cycle, "status": verification.get("status", ""), "failure_signature": "", "repair_guidance": {}})
            return verification_loop_result("passed", attempts, verification, "verification passed")
        if signature and signature == previous_signature:
            attempts.append({"cycle": cycle, "status": verification.get("status", ""), "failure_signature": signature, "repair_guidance": {}, "repair_execution": {"status": "skipped_repeat_failure", "repaired_files": [], "blockers": ["same verification failure repeats"]}})
            return verification_loop_result("blocked", attempts, verification, "same verification failure repeats", repeated_signature=True)
        repair_guidance = repair_guidance_for_verification(project_brief, verification, signature, request_guidance, repo)
        repair_execution = apply_greenfield_repair(repo, project_brief, verification, repair_guidance)
        if repair_execution.get("status") != "applied":
            synthesis_repair = apply_greenfield_synthesis_repair(repo, project_brief, verification, signature, request_guidance)
            repair_execution = {
                "kind": "code_brigade_greenfield_repair_execution",
                "contract_version": "eye-mechanicum.v1",
                "status": "applied" if synthesis_repair.get("status") == "applied" else "not_applicable",
                "repair_strategy": "module_synthesis_repair",
                "repaired_files": [{"path": path, "repair": "verification_repair_module_synthesis"} for path in synthesis_repair.get("changed_files", []) if isinstance(path, str)],
                "blockers": [
                    *[str(item) for item in repair_execution.get("blockers", []) if isinstance(item, str)],
                    *[
                        f"{row.get('path')}: {'; '.join(str(item) for item in row.get('blockers', []) if isinstance(item, str))}"
                        for row in synthesis_repair.get("rows", [])
                        if isinstance(row, dict) and row.get("blockers")
                    ],
                ],
                "verification_status_before": verification.get("status", ""),
                "synthesis_repair_report": synthesis_repair,
            }
        attempts.append(
            {
                "cycle": cycle,
                "status": verification.get("status", ""),
                "failure_signature": signature,
                "repair_guidance": repair_guidance,
                "repair_execution": repair_execution,
            }
        )
        if repair_execution.get("status") != "applied":
            return verification_loop_result("blocked", attempts, verification, "no bounded repair applicable")
        previous_signature = signature
    if attempts and isinstance(attempts[-1].get("repair_execution"), dict) and attempts[-1]["repair_execution"].get("status") == "applied":
        verification = run_verification_commands(commands, str(repo), execute=True)
        final_verification = verification
        if verification.get("status") == "passed":
            attempts.append({"cycle": max_cycles + 1, "status": verification.get("status", ""), "failure_signature": "", "repair_guidance": {}, "post_repair_verification": True})
            return verification_loop_result("passed", attempts, verification, "verification passed after final repair")
    return verification_loop_result("blocked", attempts, final_verification, "max verification cycles reached")
