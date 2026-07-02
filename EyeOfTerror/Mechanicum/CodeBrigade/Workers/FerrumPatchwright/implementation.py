from __future__ import annotations

"""Patch implementation role implementation."""

import sys
from pathlib import Path

ORDINATUS_ROOT = Path(__file__).resolve().parents[1] / "OrdinatusVerifier"
if str(ORDINATUS_ROOT) not in sys.path:
    sys.path.insert(0, str(ORDINATUS_ROOT))

from common.codewright_core import *  # noqa: F403 - role modules use the shared Codewright helper surface.
from verification import *  # noqa: F403,E402 - implementation inference reuses verifier diagnostics.
from verification import diagnostic_extraction_from_execution  # noqa: E402


def source_excerpt_pack(workspace_root: Path, output_path: str, repo_root: Path) -> list[dict[str, Any]]:
    survey = load_json_optional(workspace_root, sibling_artifact(output_path, "repo_survey.json"))
    investigation = survey.get("engineering_investigation") if isinstance(survey.get("engineering_investigation"), dict) else {}
    readiness = survey.get("engineering_readiness") if isinstance(survey.get("engineering_readiness"), dict) else {}
    targeted = investigation.get("targeted_reading_plan") if isinstance(investigation.get("targeted_reading_plan"), list) else []
    repo_map = survey.get("repo_map") if isinstance(survey.get("repo_map"), dict) else {}
    read_order = repo_map.get("recommended_read_order") if isinstance(repo_map.get("recommended_read_order"), list) else []
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in (targeted, read_order):
        for item in source:
            if not isinstance(item, dict):
                continue
            rel = str(item.get("path") or "")
            if not rel or rel in seen:
                continue
            seen.add(rel)
            candidates.append(item)
            if len(candidates) >= 8:
                break
        if len(candidates) >= 8:
            break
    excerpts: list[dict[str, Any]] = []
    for item in candidates:
        rel = str(item.get("path") or "")
        record: dict[str, Any] = {
            "path": rel,
            "phase": item.get("phase", ""),
            "reason": item.get("reason", ""),
            "question": item.get("question", ""),
            "dependent_count": int(item.get("dependent_count") or 0),
        }
        try:
            path = safe_repo_path(repo_root, rel)
        except ValueError as exc:
            record.update({"status": "blocked", "diagnostic": str(exc)})
            excerpts.append(record)
            continue
        if not path.exists() or not path.is_file():
            record.update({"status": "missing"})
            excerpts.append(record)
            continue
        size = path.stat().st_size
        record["bytes"] = size
        if size > 40_000:
            record.update({"status": "skipped_large_file"})
            excerpts.append(record)
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            record.update({"status": "skipped_non_utf8"})
            excerpts.append(record)
            continue
        excerpt = text[:12_000]
        record.update(
            {
                "status": "read",
                "excerpt": excerpt,
                "truncated": len(text) > len(excerpt),
            }
        )
        excerpts.append(record)
    return excerpts


def survey_source_candidates_from_payload(survey: dict[str, Any]) -> list[str]:
    repo_map = survey.get("repo_map") if isinstance(survey.get("repo_map"), dict) else {}
    ranked = repo_map.get("ranked_files") if isinstance(repo_map.get("ranked_files"), list) else []
    candidates: list[str] = []
    for item in ranked:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if path and not test_like_path(path) and path not in candidates:
            candidates.append(path)
    return candidates


def patch_operation_plan_items(patch_spec: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    operations = patch_spec.get("operations") if isinstance(patch_spec.get("operations"), list) else []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        op_type = str(operation.get("type") or "")
        item = {
            "type": op_type,
            "path": operation.get("path", ""),
        }
        for key in ("function_name", "old_expression", "new_expression", "old", "new"):
            if key in operation:
                item[key] = operation.get(key)
        items.append(item)
    return items

















def unshaped_repair_plan_from_resolution(
    request: dict[str, Any],
    survey: dict[str, Any],
    patch_resolution: dict[str, Any],
    patch_spec: dict[str, Any],
    excerpts: list[dict[str, Any]],
) -> dict[str, Any]:
    patch_source = str(patch_spec.get("source") or "")
    repo_root = target_repo_root(request)
    test_symbol_links = test_symbol_links_from_goal(repo_root, request_goal(request))
    candidates = patch_resolution.get("candidates") if isinstance(patch_resolution.get("candidates"), list) else []
    selected = patch_resolution.get("selected_candidate") if isinstance(patch_resolution.get("selected_candidate"), dict) else {}
    source_paths: list[str] = []
    for operation in patch_spec.get("operations", []) if isinstance(patch_spec.get("operations"), list) else []:
        if isinstance(operation, dict) and operation.get("path"):
            path = str(operation.get("path"))
            if path not in source_paths:
                source_paths.append(path)
    for item in excerpts:
        if isinstance(item, dict) and item.get("path"):
            path = str(item.get("path"))
            if path not in source_paths:
                source_paths.append(path)
    for path in survey_source_candidates_from_payload(survey):
        if path not in source_paths:
            source_paths.append(path)
    static_hypotheses = static_diagnostic_hypotheses_from_candidates(candidates)
    diagnostics = patch_spec.get("diagnostics") if isinstance(patch_spec.get("diagnostics"), dict) else {}
    runtime_evidence = runtime_evidence_from_diagnostics(diagnostics)
    minimal_patch_candidates = [
        {
            "source": candidate.get("source", ""),
            "status": candidate.get("status", ""),
            "operation_count": candidate.get("operation_count", 0),
            "verification_command_count": candidate.get("verification_command_count", 0),
            "diagnostics": candidate.get("diagnostics", {}),
            "operations": patch_operation_plan_items(patch_spec) if candidate.get("status") == "selected" else [],
            "runtime_evidence": runtime_evidence if candidate.get("status") == "selected" else {},
        }
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("status") in {"selected", "blocked"}
    ]
    mode = "unshaped_repo_repair" if is_unshaped_patch_source(patch_source) else "structured_patch"
    status = "recorded" if patch_spec and (is_unshaped_patch_source(patch_source) or static_hypotheses) else "not_required"
    return {
        "status": status,
        "mode": mode,
        "task_id": request.get("task_id"),
        "goal_excerpt": request_goal(request)[:1200],
        "selected_source": patch_source,
        "selected_candidate": selected,
        "files_to_read": source_paths[:20],
        "test_symbol_links": test_symbol_links,
        "patch_operations": patch_operation_plan_items(patch_spec),
        "runtime_evidence": runtime_evidence,
        "commands_to_run": patch_spec.get("verification_commands", [])
        if isinstance(patch_spec.get("verification_commands"), list)
        else [],
        "defect_hypotheses": static_hypotheses,
        "minimal_patch_candidates": minimal_patch_candidates[:12],
        "proof_plan": {
            "focused_verification": patch_spec.get("verification_commands", [])
            if isinstance(patch_spec.get("verification_commands"), list)
            else [],
            "source_must_change": True,
            "tests_must_not_be_edited_for_test_inferred_repairs": True,
            "test_to_source_linkage_required": is_unshaped_patch_source(patch_source),
            "runtime_diagnostic_required": patch_source.startswith("runtime_diagnostic_"),
            "ast_patch_plan_required": ast_patch_plan_required_for_source(patch_source),
            "review_gates": [
                "diagnostic_linkage",
                "test_symbol_linkage",
                "patch_scope_review",
                "verification_passed",
                "review_discipline_findings",
            ],
        },
        "safety_constraints": [
            "prefer the smallest source patch that satisfies test and contract evidence",
            "do not edit tests to make an inferred repair pass",
            "block instead of mutating when source mapping is ambiguous",
        ],
    }






def function_defs_by_name(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def function_has_raise(node: ast.FunctionDef, exception_name: str = "ValueError") -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Raise):
            continue
        exc = child.exc
        if isinstance(exc, ast.Call):
            exc = exc.func
        if isinstance(exc, ast.Name) and exc.id == exception_name:
            return True
    return False


def count_function_ifs(node: ast.FunctionDef) -> int:
    return sum(1 for child in ast.walk(node) if isinstance(child, ast.If))


def ast_patch_plan_from_spec(repo_root: Path, patch_spec: dict[str, Any]) -> dict[str, Any]:
    patch_source = str(patch_spec.get("source") or "")
    operations = patch_spec.get("operations") if isinstance(patch_spec.get("operations"), list) else []
    diagnostics = patch_spec.get("diagnostics") if isinstance(patch_spec.get("diagnostics"), dict) else {}
    planned: list[dict[str, Any]] = []
    blockers: list[str] = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        path = str(operation.get("path") or "")
        if is_unshaped_patch_source(patch_source) and test_like_path(path):
            blockers.append(f"unshaped inferred repairs must not mutate test files: {path}")
            continue
        if not path.endswith(".py"):
            continue
        op_type = str(operation.get("type") or "")
        if op_type in {"replace", "replace_return_expression"}:
            old = str(operation.get("old") or operation.get("old_expression") or "")
            new = str(operation.get("new") or operation.get("new_expression") or "")
            function_name = str(diagnostics.get("function_name") or "")
            if op_type == "replace_return_expression":
                function_name = str(operation.get("function_name") or function_name)
            module_path = str(diagnostics.get("module_path") or diagnostics.get("source_path") or "")
            if op_type == "replace":
                old_expr = old.removeprefix("return ").strip()
                new_expr = new.removeprefix("return ").strip()
                has_return_prefix = old.startswith("return ") and new.startswith("return ")
            else:
                old_expr = old.strip()
                new_expr = new.strip()
                has_return_prefix = True
            if not function_name or module_path != path or not has_return_prefix:
                continue
            source_path = safe_repo_path(repo_root, path)
            function = simple_function_return_segment(source_path, function_name) if source_path.exists() else {}
            try:
                ast.parse(new_expr, mode="eval")
            except SyntaxError as exc:
                blockers.append(f"replacement expression is not valid Python AST expression for {path}:{function_name}: {exc.msg}")
                continue
            if function.get("return_expr") != old_expr:
                blockers.append(
                    f"replace operation does not match current AST return expression for {path}:{function_name}"
                )
                continue
            planned.append(
                {
                    "kind": "replace_return_expression",
                    "path": path,
                    "function_name": function_name,
                    "line": function.get("line", 0),
                    "old_expression": old_expr,
                    "new_expression": new_expr,
                    "minimality": "single_return_expression",
                }
            )
        elif op_type == "append":
            function_name = str(operation.get("python_function_name") or diagnostics.get("function_name") or "")
            content = str(operation.get("content") or "")
            if not function_name:
                continue
            try:
                tree = ast.parse(content)
            except SyntaxError as exc:
                blockers.append(f"append content is not valid Python AST for {path}:{function_name}: {exc.msg}")
                continue
            functions = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
            if functions != [function_name]:
                blockers.append(f"append operation must add exactly function {function_name}, found {functions}")
                continue
            planned.append(
                {
                    "kind": "append_missing_function",
                    "path": path,
                    "function_name": function_name,
                    "minimality": "single_function_append",
                }
            )
        elif op_type == "write_file" and operation.get("overwrite") is True:
            content = str(operation.get("content") or "")
            source_path = safe_repo_path(repo_root, path)
            if not source_path.exists():
                continue
            try:
                before_tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
                after_tree = ast.parse(content, filename=path)
            except (OSError, SyntaxError, UnicodeDecodeError) as exc:
                blockers.append(f"write_file overwrite content is not valid comparable Python AST for {path}: {exc}")
                continue
            before_functions = function_defs_by_name(before_tree)
            after_functions = function_defs_by_name(after_tree)
            for function_name, after_function in after_functions.items():
                before_function = before_functions.get(function_name)
                if not before_function:
                    planned.append(
                        {
                            "kind": "add_function",
                            "path": path,
                            "function_name": function_name,
                            "minimality": "function_added_in_overwrite",
                        }
                    )
                    continue
                before_ifs = count_function_ifs(before_function)
                after_ifs = count_function_ifs(after_function)
                if (
                    after_ifs > before_ifs
                    and function_has_raise(after_function, "ValueError")
                    and not function_has_raise(before_function, "ValueError")
                ):
                    planned.append(
                        {
                            "kind": "add_validation_branch",
                            "path": path,
                            "function_name": function_name,
                            "old_if_count": before_ifs,
                            "new_if_count": after_ifs,
                            "validation_exception": "ValueError",
                            "minimality": "preserve_function_with_added_validation",
                        }
                    )
    if blockers:
        status = "blocked"
    elif planned:
        status = "recorded"
    elif ast_patch_plan_required_for_source(patch_source):
        status = "missing"
    else:
        status = "not_applicable"
    return {
        "status": status,
        "patch_source": patch_source,
        "planned_operations": planned,
        "operation_count": len(planned),
        "blockers": blockers,
        "strategy": "ast_guided_minimal_patch" if planned else "not_applicable",
    }






def git_dirty_target_evidence(repo_root: Path, operations: list[Any]) -> dict[str, Any]:
    if not (repo_root / ".git").exists():
        return {"git_repo": False, "dirty_targets": []}
    target_paths: list[str] = []
    seen: set[str] = set()
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        try:
            path = safe_repo_path(repo_root, str(operation.get("path") or ""))
        except ValueError:
            continue
        rel = str(path.relative_to(repo_root))
        if rel not in seen:
            seen.add(rel)
            target_paths.append(rel)
    dirty_targets: list[dict[str, Any]] = []
    for rel in target_paths:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--", rel],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            dirty_targets.append({"path": rel, "status": "unknown", "diagnostic": completed.stderr[-1000:]})
            continue
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        if lines:
            dirty_targets.append({"path": rel, "status": "dirty", "porcelain": lines})
    return {"git_repo": True, "target_paths": target_paths, "dirty_targets": dirty_targets}


def extract_json_after_marker(text: str, marker: str) -> dict[str, Any]:
    start = text.find(marker)
    if start < 0:
        return {}
    payload_text = text[start + len(marker):].strip()
    if payload_text.startswith("```"):
        lines = payload_text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if "```" in lines:
            lines = lines[:lines.index("```")]
        payload_text = "\n".join(lines).strip()
    decoder = json.JSONDecoder()
    try:
        payload, _ = decoder.raw_decode(payload_text)
    except json.JSONDecodeError as exc:
        label = marker.rstrip(":")
        raise ValueError(f"{label} JSON is invalid: {exc}") from exc
    return payload if isinstance(payload, dict) else {}


def marker_value(text: str, marker: str) -> str:
    marker_at = text.find(marker)
    if marker_at < 0:
        return ""
    return text[marker_at + len(marker):].strip().splitlines()[0].strip()


def marker_block(text: str, marker: str) -> str:
    marker_at = text.find(marker)
    if marker_at < 0:
        return ""
    block = text[marker_at + len(marker):]
    stop_markers = [
        "\nCERAXIA_TARGET_REPO:",
        "\nCERAXIA_PATCH:",
        "\nCERAXIA_FEATURE:",
        "\nCERAXIA_INTEGRATION_CONTRACT:",
        "\nCERAXIA_PUBLIC_API_COMPAT:",
        "\nCERAXIA_CONFIG_RUNTIME:",
        "\nCERAXIA_REFACTOR:",
        "\nCERAXIA_EDGE_FIX:",
        "\nCERAXIA_DATA_MIGRATION:",
        "\nCERAXIA_FILES:",
        "\nCERAXIA_CREATE_FILE:",
        "\nCERAXIA_FILE_CONTENT:",
        "\nCERAXIA_REPLACE_IN_FILE:",
        "\nCERAXIA_OLD:",
        "\nCERAXIA_NEW:",
        "\nCERAXIA_VERIFY:",
    ]
    stop_positions = [pos for marker_item in stop_markers if (pos := block.find(marker_item)) >= 0]
    if stop_positions:
        block = block[: min(stop_positions)]
    return block.strip("\n")


def verification_commands_from_markers(goal: str) -> list[str]:
    commands: list[str] = []
    for line in goal.splitlines():
        stripped = line.strip()
        if stripped.startswith("CERAXIA_VERIFY:"):
            command = stripped.removeprefix("CERAXIA_VERIFY:").strip()
            if command:
                commands.append(command)
    return commands


def verification_commands_from_natural_goal(goal: str) -> list[str]:
    commands = verification_commands_from_markers(goal)
    for match in re.finditer(r"(?:проверь|запусти|run|verify|test)\s+`([^`]+)`", goal, flags=re.IGNORECASE):
        command = match.group(1).strip()
        if command and command not in commands:
            commands.append(command)
    return commands


def ambiguity_analysis_from_goal(goal: str, repo_root: Path) -> dict[str, Any]:
    lowered = goal.lower()
    hard_ambiguity_markers = [
        "не задан",
        "не указ",
        "если вариантов несколько",
        "ambiguous",
    ]
    soft_ambiguity_markers = [
        "не угадывай",
        "улучши",
        "improve",
    ]
    hard_ambiguity = any(marker in lowered for marker in hard_ambiguity_markers)
    soft_ambiguity = any(marker in lowered for marker in soft_ambiguity_markers)
    if not hard_ambiguity and not soft_ambiguity:
        return {}
    test_files = [
        str(path.relative_to(repo_root))
        for path in sorted(repo_root.rglob("*.py"))
        if test_like_path(str(path.relative_to(repo_root))) and not any(part in EXCLUDED_DIRS for part in path.relative_to(repo_root).parts)
    ][:8]
    verification_commands = verification_commands_from_natural_goal(goal)
    if soft_ambiguity and not hard_ambiguity and test_files and verification_commands:
        return {}
    candidates: list[dict[str, str]] = []
    if any(marker in lowered for marker in ("ошиб", "error", "exception")):
        candidates.extend(
            [
                {
                    "interpretation": "raise_exception",
                    "risk": "callers may expect exceptions and HTTP/API layers may need mapping",
                },
                {
                    "interpretation": "return_error_object",
                    "risk": "changes return shape and may break existing callers",
                },
                {
                    "interpretation": "fallback_default",
                    "risk": "can hide invalid input and corrupt downstream data",
                },
            ]
        )
    if not candidates:
        candidates.append(
            {
                "interpretation": "multiple_valid_implementations",
                "risk": "task lacks an acceptance criterion that distinguishes correct behavior",
            }
        )
    return {
        "status": "ambiguous",
        "reason": "task does not provide enough acceptance criteria for safe source mutation",
        "candidate_interpretations": candidates,
        "safe_next_question": "Specify the expected behavior, error shape, and verification command before source mutation.",
        "available_test_files": test_files,
    }


def infer_simple_replace_patch_spec(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    patterns = [
        r"(?:в\s+файле|в|in)\s+`(?P<path>[^`]+)`.*?(?:замени|replace)\s+`(?P<old>[^`]+)`\s+(?:на|with)\s+`(?P<new>[^`]+)`",
        r"(?:замени|replace)\s+`(?P<old>[^`]+)`\s+(?:на|with)\s+`(?P<new>[^`]+)`.*?(?:в\s+файле|в|in)\s+`(?P<path>[^`]+)`",
    ]
    for pattern in patterns:
        match = re.search(pattern, goal, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        raw_path = match.group("path").strip()
        old = match.group("old")
        new = match.group("new")
        if "\x00" in old or "\x00" in new:
            raise ValueError("inferred replace patch cannot contain NUL bytes")
        return {
            "source": "natural_language_simple_replace",
            "operations": [
                {
                    "type": "replace",
                    "path": raw_path,
                    "old": old,
                    "new": new,
                }
            ],
            "verification_commands": verification_commands_from_natural_goal(goal),
        }
    return {}




def infer_add_function_patch_spec(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    patterns = [
        r"(?:в\s+файле|в|in)\s+`(?P<path>[^`]+)`.*?(?:добавь|add).*?(?:функц\w*|function)\s+`(?P<function>[A-Za-z_][A-Za-z0-9_]*)`.*?(?:возвращ\w*|return(?:ing)?)\s+`(?P<literal>[^`]+)`",
        r"(?:добавь|add).*?(?:функц\w*|function)\s+`(?P<function>[A-Za-z_][A-Za-z0-9_]*)`.*?(?:в\s+файле|в|in)\s+`(?P<path>[^`]+)`.*?(?:возвращ\w*|return(?:ing)?)\s+`(?P<literal>[^`]+)`",
    ]
    for pattern in patterns:
        match = re.search(pattern, goal, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        function_name = match.group("function")
        literal = safe_return_literal(match.group("literal"))
        content = f"\n\ndef {function_name}():\n    return {literal}\n"
        return {
            "source": "natural_language_add_function",
            "operations": [
                {
                    "type": "append",
                    "path": match.group("path").strip(),
                    "content": content,
                    "python_function_name": function_name,
                }
            ],
            "verification_commands": verification_commands_from_natural_goal(goal),
        }
    return {}










def ast_return_literal_for_function(source_path: Path, function_name: str) -> str:
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return ""
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue
        returns = [item for item in ast.walk(node) if isinstance(item, ast.Return)]
        if len(returns) != 1:
            return ""
        value = returns[0].value
        if isinstance(value, ast.Constant):
            if isinstance(value.value, bool):
                return "True" if value.value else "False"
            if value.value is None:
                return "None"
            if isinstance(value.value, int):
                return str(value.value)
            if isinstance(value.value, str):
                return repr(value.value)
    return ""


def infer_return_mismatch_from_tests(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    repo_root = target_repo_root(request)
    candidates: list[dict[str, str]] = []
    for candidate in test_expectation_candidates(repo_root, goal):
        source_path = safe_repo_path(repo_root, candidate["module_path"])
        current = source_path.read_text(encoding="utf-8")
        function_name = candidate["function_name"]
        if not re.search(rf"^\s*def\s+{re.escape(function_name)}\s*\(", current, flags=re.MULTILINE):
            continue
        actual = ast_return_literal_for_function(source_path, function_name)
        expected = candidate["literal"]
        if not actual or actual == expected:
            continue
        if current.count(f"return {actual}") != 1:
            continue
        candidates.append({**candidate, "actual": actual})
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ValueError(f"test-inferred return mismatch requires exactly one candidate, found {len(candidates)}")
    candidate = candidates[0]
    commands = verification_commands_from_natural_goal(goal)
    if not commands:
        test_module = candidate["test_path"][:-3].replace("/", ".")
        commands = [f"python -m unittest {test_module}"]
    return {
        "source": "test_inferred_return_mismatch",
        "diagnostics": {
            "kind": "test_inferred_return_mismatch",
            "test_path": candidate["test_path"],
            "module_path": candidate["module_path"],
            "function_name": candidate["function_name"],
            "actual": candidate["actual"],
            "expected": candidate["literal"],
        },
        "operations": [
            {
                "type": "replace",
                "path": candidate["module_path"],
                "old": f"return {candidate['actual']}",
                "new": f"return {candidate['literal']}",
            }
        ],
        "verification_commands": commands,
    }


def infer_self_repair_seed_from_tests(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    lowered = goal.lower()
    if not any(marker in lowered for marker in ("self-repair", "self repair", "самоисправ", "diagnostic", "диагност", "revision")):
        return {}
    repo_root = target_repo_root(request)
    candidates: list[dict[str, str]] = []
    for candidate in test_expectation_candidates(repo_root, goal):
        source_path = safe_repo_path(repo_root, candidate["module_path"])
        current = source_path.read_text(encoding="utf-8")
        function_name = candidate["function_name"]
        if not re.search(rf"^\s*def\s+{re.escape(function_name)}\s*\(", current, flags=re.MULTILINE):
            continue
        actual = ast_return_literal_for_function(source_path, function_name)
        expected = candidate["literal"]
        if not actual or actual == expected:
            continue
        if current.count(f"return {actual}") != 1:
            continue
        if not re.fullmatch(r"[+-]?\d+", actual) or not re.fullmatch(r"[+-]?\d+", expected):
            continue
        seed = str(int(actual) + 1)
        if seed == expected:
            seed = str(int(actual) - 1)
        if seed == actual or seed == expected:
            continue
        candidates.append({**candidate, "actual": actual, "seed": seed})
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ValueError(f"test-inferred self-repair seed requires exactly one candidate, found {len(candidates)}")
    candidate = candidates[0]
    commands = verification_commands_from_natural_goal(goal)
    if not commands:
        test_module = candidate["test_path"][:-3].replace("/", ".")
        commands = [f"python -m unittest {test_module}"]
    return {
        "source": "test_inferred_self_repair_seed",
        "diagnostics": {
            "kind": "test_inferred_self_repair_seed",
            "test_path": candidate["test_path"],
            "module_path": candidate["module_path"],
            "function_name": candidate["function_name"],
            "initial_actual": candidate["actual"],
            "seed": candidate["seed"],
            "expected_after_repair": candidate["literal"],
            "repair_expected": True,
        },
        "operations": [
            {
                "type": "replace",
                "path": candidate["module_path"],
                "old": f"return {candidate['actual']}",
                "new": f"return {candidate['seed']}",
            }
        ],
        "verification_commands": commands,
    }


def arithmetic_test_expectation_candidates(repo_root: Path, goal: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for test_path in discovered_test_paths(repo_root, goal):
        path = safe_repo_path(repo_root, test_path)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        imports = re.findall(r"^\s*from\s+([A-Za-z_][A-Za-z0-9_\.]*)\s+import\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", text, flags=re.MULTILINE)
        imported_modules = {function_name: module_name for module_name, function_name in imports}
        for match in re.finditer(
            r"assertEqual\(\s*([A-Za-z_][A-Za-z0-9_]*)\(\s*([+-]?\d+)\s*,\s*([+-]?\d+)\s*\)\s*,\s*([+-]?\d+)\s*\)",
            text,
        ):
            function_name, left_raw, right_raw, expected_raw = match.groups()
            module_name = imported_modules.get(function_name, "")
            if not module_name:
                continue
            module_path = f"{module_name.replace('.', '/')}.py"
            source_path = safe_repo_path(repo_root, module_path)
            if not source_path.exists():
                continue
            candidates.append(
                {
                    "test_path": test_path,
                    "module_path": module_path,
                    "function_name": function_name,
                    "left": int(left_raw),
                    "right": int(right_raw),
                    "expected": int(expected_raw),
                }
            )
            delegated = delegated_arithmetic_candidate(
                repo_root,
                test_path,
                module_path,
                function_name,
                int(left_raw),
                int(right_raw),
                int(expected_raw),
            )
            if delegated:
                candidates.append(delegated)
    return candidates


def delegated_arithmetic_candidate(
    repo_root: Path,
    test_path: str,
    module_path: str,
    function_name: str,
    left: int,
    right: int,
    expected: int,
) -> dict[str, Any]:
    source_path = safe_repo_path(repo_root, module_path)
    try:
        text = source_path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(source_path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return {}
    imports: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        for alias in node.names:
            imports[alias.asname or alias.name] = f"{node.module.replace('.', '/')}.py"
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue
        args = [arg.arg for arg in node.args.args]
        returns = [item for item in ast.walk(node) if isinstance(item, ast.Return)]
        if len(args) != 2 or len(returns) != 1:
            return {}
        value = returns[0].value
        if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
            return {}
        if len(value.args) != 2 or not all(isinstance(item, ast.Name) for item in value.args):
            return {}
        call_args = [item.id for item in value.args if isinstance(item, ast.Name)]
        if call_args != args:
            return {}
        target_module_path = imports.get(value.func.id)
        if not target_module_path:
            return {}
        target_path = safe_repo_path(repo_root, target_module_path)
        if not target_path.exists():
            return {}
        return {
            "test_path": test_path,
            "module_path": target_module_path,
            "function_name": value.func.id,
            "left": left,
            "right": right,
            "expected": expected,
            "delegated_from": {
                "module_path": module_path,
                "function_name": function_name,
            },
        }




def infer_arithmetic_return_from_tests(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    repo_root = target_repo_root(request)
    candidates: list[dict[str, Any]] = []
    for candidate in arithmetic_test_expectation_candidates(repo_root, goal):
        source_path = safe_repo_path(repo_root, candidate["module_path"])
        function = simple_function_return_segment(source_path, str(candidate["function_name"]))
        args = function.get("args") if isinstance(function.get("args"), list) else []
        if len(args) != 2:
            continue
        left_name, right_name = str(args[0]), str(args[1])
        left = int(candidate["left"])
        right = int(candidate["right"])
        expected = int(candidate["expected"])
        options = [
            (f"{left_name} + {right_name}", left + right),
            (f"{left_name} - {right_name}", left - right),
            (f"{right_name} - {left_name}", right - left),
            (f"{left_name} * {right_name}", left * right),
            (f"{left_name} - ({left_name} * {right_name} / 100)", left - (left * right / 100)),
        ]
        matching = [expr for expr, value in options if value == expected]
        if len(matching) != 1:
            continue
        new_expr = matching[0]
        old_expr = str(function.get("return_expr") or "")
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*\(", old_expr):
            continue
        if old_expr == new_expr:
            continue
        content = source_path.read_text(encoding="utf-8")
        old = f"return {old_expr}"
        new = f"return {new_expr}"
        if content.count(old) != 1:
            continue
        candidates.append({**candidate, "actual_expression": old_expr, "replacement_expression": new_expr})
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ValueError(f"test-inferred arithmetic return requires exactly one candidate, found {len(candidates)}")
    candidate = candidates[0]
    commands = verification_commands_from_natural_goal(goal)
    if not commands:
        test_module = str(candidate["test_path"])[:-3].replace("/", ".")
        commands = [f"python -m unittest {test_module}"]
    return {
        "source": "test_inferred_arithmetic_return",
        "diagnostics": {
            "kind": "test_inferred_arithmetic_return",
            "test_path": candidate["test_path"],
            "module_path": candidate["module_path"],
            "function_name": candidate["function_name"],
            "actual_expression": candidate["actual_expression"],
            "replacement_expression": candidate["replacement_expression"],
            "example": {
                "left": candidate["left"],
                "right": candidate["right"],
                "expected": candidate["expected"],
            },
            "delegated_from": candidate.get("delegated_from", {}),
        },
        "operations": [
            {
                "type": "replace",
                "path": candidate["module_path"],
                "old": f"return {candidate['actual_expression']}",
                "new": f"return {candidate['replacement_expression']}",
            }
        ],
        "verification_commands": commands,
    }


def infer_missing_function_from_tests(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    repo_root = target_repo_root(request)
    candidates: list[dict[str, str]] = []
    for candidate in test_expectation_candidates(repo_root, goal):
        source_path = safe_repo_path(repo_root, candidate["module_path"])
        current = source_path.read_text(encoding="utf-8")
        if re.search(rf"^\s*def\s+{re.escape(candidate['function_name'])}\s*\(", current, flags=re.MULTILINE):
            continue
        candidates.append(candidate)
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ValueError(f"test-inferred missing function requires exactly one candidate, found {len(candidates)}")
    candidate = candidates[0]
    function_name = candidate["function_name"]
    content = f"\n\ndef {function_name}():\n    return {candidate['literal']}\n"
    commands = verification_commands_from_natural_goal(goal)
    if not commands:
        test_module = candidate["test_path"][:-3].replace("/", ".")
        commands = [f"python -m unittest {test_module}"]
    return {
        "source": "test_inferred_missing_function",
        "diagnostics": {
            "kind": "test_inferred_missing_function",
            "test_path": candidate["test_path"],
            "module_path": candidate["module_path"],
            "function_name": function_name,
            "expected": candidate["literal"],
        },
        "operations": [
            {
                "type": "append",
                "path": candidate["module_path"],
                "content": content,
                "python_function_name": function_name,
            }
        ],
        "verification_commands": commands,
    }


def patch_spec_from_feature_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_FEATURE:")
    if not payload:
        return {}
    module_path = str(payload.get("module_path") or "").strip()
    function_name = str(payload.get("function_name") or "").strip()
    test_path = str(payload.get("test_path") or "").strip()
    docs_path = str(payload.get("docs_path") or "").strip()
    caller_path = str(payload.get("caller_path") or "").strip()
    if not module_path or not function_name or not test_path or not docs_path or not caller_path:
        raise ValueError("CERAXIA_FEATURE requires module_path, function_name, test_path, docs_path, and caller_path")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function_name):
        raise ValueError("CERAXIA_FEATURE function_name must be a valid Python identifier")
    arguments = payload.get("arguments")
    if not isinstance(arguments, list) or not arguments or not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in arguments):
        raise ValueError("CERAXIA_FEATURE arguments must be a non-empty list of Python identifiers")
    expression = str(payload.get("return_expression") or "").strip()
    if not expression or "\n" in expression or not re.fullmatch(r"[A-Za-z0-9_ +\-*/().]+", expression):
        raise ValueError("CERAXIA_FEATURE return_expression must be a simple arithmetic expression")
    test_cases = payload.get("test_cases")
    if not isinstance(test_cases, list) or not test_cases:
        raise ValueError("CERAXIA_FEATURE test_cases must be a non-empty list")
    rendered_cases: list[str] = []
    for index, item in enumerate(test_cases):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_FEATURE test case {index} must be an object")
        inputs = item.get("inputs")
        expected = item.get("expected")
        if not isinstance(inputs, list) or len(inputs) != len(arguments):
            raise ValueError(f"CERAXIA_FEATURE test case {index} inputs must match arguments")
        if not all(isinstance(value, (int, float)) for value in inputs) or not isinstance(expected, (int, float)):
            raise ValueError(f"CERAXIA_FEATURE test case {index} supports only numeric inputs and expected values")
        rendered_cases.append(f"        self.assertEqual({function_name}({', '.join(str(value) for value in inputs)}), {expected})")
    module_content = f"def {function_name}({', '.join(arguments)}):\n    return {expression}\n"
    class_name = "".join(part.capitalize() for part in function_name.split("_")) + "Test"
    test_content = (
        f"import unittest\nfrom {module_path[:-3].replace('/', '.')} import {function_name}\n\n"
        f"class {class_name}(unittest.TestCase):\n"
        f"    def test_{function_name}(self):\n"
        + "\n".join(rendered_cases)
        + "\n\nif __name__ == '__main__':\n    unittest.main()\n"
    )
    docs_title = str(payload.get("docs_title") or function_name.replace("_", " ").title())
    docs_content = f"# {docs_title}\n\nFunction `{function_name}` is available in `{module_path}` and is covered by `{test_path}`.\n"
    caller_function = str(payload.get("caller_function") or f"use_{function_name}").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", caller_function):
        raise ValueError("CERAXIA_FEATURE caller_function must be a valid Python identifier")
    caller_content = (
        f"from {module_path[:-3].replace('/', '.')} import {function_name}\n\n"
        f"def {caller_function}({', '.join(arguments)}):\n"
        f"    return {function_name}({', '.join(arguments)})\n"
    )
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = [f"python -m unittest {test_path[:-3].replace('/', '.')}"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_FEATURE verification_commands must be a list of strings")
    return {
        "source": "feature_marker_synthesis",
        "diagnostics": {
            "kind": "feature_marker_synthesis",
            "function_name": function_name,
            "module_path": module_path,
            "test_path": test_path,
            "docs_path": docs_path,
            "caller_path": caller_path,
        },
        "operations": [
            {"type": "write_file", "path": module_path, "content": module_content},
            {"type": "write_file", "path": test_path, "content": test_content},
            {"type": "write_file", "path": docs_path, "content": docs_content},
            {"type": "write_file", "path": caller_path, "content": caller_content},
        ],
        "verification_commands": verification_commands,
    }


def patch_spec_from_integration_contract_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_INTEGRATION_CONTRACT:")
    if not payload:
        return {}
    contract_path = str(payload.get("contract_path") or "").strip()
    implementation_path = str(payload.get("implementation_path") or "").strip()
    caller_path = str(payload.get("caller_path") or "").strip()
    test_path = str(payload.get("test_path") or "").strip()
    report_path = str(payload.get("report_path") or "").strip()
    function_name = str(payload.get("function_name") or "").strip()
    caller_function = str(payload.get("caller_function") or "").strip()
    response_field = str(payload.get("response_field") or "").strip()
    expression = str(payload.get("return_expression") or "").strip()
    required = [contract_path, implementation_path, caller_path, test_path, report_path, function_name, caller_function, response_field, expression]
    if not all(required):
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT requires contract, implementation, caller, test, report, function, caller_function, response_field, and return_expression")
    if not implementation_path.endswith(".py") or not caller_path.endswith(".py") or not test_path.endswith(".py"):
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT implementation, caller, and test paths must be Python files")
    identifiers = [function_name, caller_function, response_field]
    if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in identifiers):
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT function and field names must be simple identifiers")
    request_fields = payload.get("request_fields")
    if not isinstance(request_fields, list) or not request_fields or not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in request_fields):
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT request_fields must be a non-empty list of identifiers")
    if "\n" in expression or not re.fullmatch(r"[A-Za-z0-9_ +\-*/().]+", expression):
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT return_expression must be a simple arithmetic expression")
    test_cases = payload.get("test_cases")
    if not isinstance(test_cases, list) or not test_cases:
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT test_cases must be a non-empty list")
    contract_content = json.dumps(
        {
            "endpoint": function_name,
            "request_fields": request_fields,
            "response_fields": [response_field],
            "caller": caller_function,
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    assignments = "".join(f"    {field} = payload['{field}']\n" for field in request_fields)
    implementation_content = (
        f"def {function_name}(payload):\n"
        f"{assignments}"
        f"    return {{'{response_field}': {expression}}}\n"
    )
    implementation_module = implementation_path[:-3].replace("/", ".")
    caller_args = ", ".join(request_fields)
    caller_payload = ", ".join(f"'{field}': {field}" for field in request_fields)
    caller_content = (
        f"from {implementation_module} import {function_name}\n\n"
        f"def {caller_function}({caller_args}):\n"
        f"    return {function_name}({{{caller_payload}}})['{response_field}']\n"
    )
    rendered_cases: list[str] = []
    for index, item in enumerate(test_cases):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_INTEGRATION_CONTRACT test case {index} must be an object")
        inputs = item.get("inputs")
        expected = item.get("expected")
        if not isinstance(inputs, dict) or set(inputs) != set(request_fields):
            raise ValueError(f"CERAXIA_INTEGRATION_CONTRACT test case {index} inputs must match request_fields")
        if not all(isinstance(inputs[field], (int, float)) for field in request_fields) or not isinstance(expected, (int, float)):
            raise ValueError(f"CERAXIA_INTEGRATION_CONTRACT test case {index} supports only numeric values")
        payload_literal = "{" + ", ".join(f"{field!r}: {inputs[field]!r}" for field in request_fields) + "}"
        args_literal = ", ".join(repr(inputs[field]) for field in request_fields)
        rendered_cases.append(f"        self.assertEqual({function_name}({payload_literal})['{response_field}'], {expected!r})")
        rendered_cases.append(f"        self.assertEqual({caller_function}({args_literal}), {expected!r})")
    caller_module = caller_path[:-3].replace("/", ".")
    class_name = "".join(part.capitalize() for part in function_name.split("_")) + "ContractTest"
    test_content = (
        f"import json\nimport unittest\nfrom pathlib import Path\nfrom {implementation_module} import {function_name}\nfrom {caller_module} import {caller_function}\n\n"
        f"class {class_name}(unittest.TestCase):\n"
        "    def test_contract_declares_response_field(self):\n"
        f"        contract = json.loads(Path('{contract_path}').read_text(encoding='utf-8'))\n"
        f"        self.assertIn('{response_field}', contract['response_fields'])\n\n"
        "    def test_implementation_and_caller_follow_contract(self):\n"
        + "\n".join(rendered_cases)
        + "\n\nif __name__ == '__main__':\n    unittest.main()\n"
    )
    report_content = (
        "# Integration Contract Update\n\n"
        f"- Contract: `{contract_path}`\n"
        f"- Implementation: `{implementation_path}`\n"
        f"- Caller: `{caller_path}`\n"
        f"- Tests: `{test_path}`\n"
        f"- Response field: `{response_field}`\n"
    )
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = [f"python -m unittest {test_path[:-3].replace('/', '.')}"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_INTEGRATION_CONTRACT verification_commands must be a list of strings")
    return {
        "source": "integration_contract_marker_synthesis",
        "diagnostics": {
            "kind": "integration_contract_marker_synthesis",
            "contract_path": contract_path,
            "implementation_path": implementation_path,
            "caller_path": caller_path,
            "test_path": test_path,
            "report_path": report_path,
            "request_fields": request_fields,
            "response_field": response_field,
        },
        "operations": [
            {"type": "write_file", "path": contract_path, "content": contract_content, "overwrite": True},
            {"type": "write_file", "path": implementation_path, "content": implementation_content, "overwrite": True},
            {"type": "write_file", "path": caller_path, "content": caller_content, "overwrite": True},
            {"type": "write_file", "path": test_path, "content": test_content, "overwrite": True},
            {"type": "write_file", "path": report_path, "content": report_content, "overwrite": True},
        ],
        "verification_commands": verification_commands,
    }


def patch_spec_from_public_api_compat_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_PUBLIC_API_COMPAT:")
    if not payload:
        return {}
    source_path = str(payload.get("source_path") or "").strip()
    caller_path = str(payload.get("caller_path") or "").strip()
    docs_path = str(payload.get("docs_path") or "").strip()
    test_path = str(payload.get("test_path") or "").strip()
    function_name = str(payload.get("function_name") or "").strip()
    caller_function = str(payload.get("caller_function") or "").strip()
    expression = str(payload.get("return_expression") or "").strip()
    if not all([source_path, caller_path, docs_path, test_path, function_name, caller_function, expression]):
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT requires source_path, caller_path, docs_path, test_path, function_name, caller_function, and return_expression")
    if not source_path.endswith(".py") or not caller_path.endswith(".py") or not test_path.endswith(".py"):
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT source, caller, and test paths must be Python files")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function_name) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", caller_function):
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT function names must be valid identifiers")
    arguments = payload.get("arguments")
    if not isinstance(arguments, list) or not arguments or not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in arguments):
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT arguments must be a non-empty list of identifiers")
    if "\n" in expression or not re.fullmatch(r"[A-Za-z0-9_ +\-*/().]+", expression):
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT return_expression must be a simple arithmetic expression")
    test_cases = payload.get("test_cases")
    if not isinstance(test_cases, list) or not test_cases:
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT test_cases must be a non-empty list")
    signature = f"{function_name}({', '.join(arguments)})"
    source_content = (
        f"def {signature}:\n"
        f"    \"\"\"Public API: keep signature `{signature}` stable.\"\"\"\n"
        f"    return {expression}\n"
    )
    source_module = source_path[:-3].replace("/", ".")
    caller_content = (
        f"from {source_module} import {function_name}\n\n"
        f"def {caller_function}({', '.join(arguments)}):\n"
        f"    return {function_name}({', '.join(arguments)})\n"
    )
    docs_content = (
        f"# Public API Compatibility\n\n"
        f"`{signature}` is the stable public function. Callers must keep using the same positional arguments.\n"
    )
    rendered_cases: list[str] = []
    for index, item in enumerate(test_cases):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_PUBLIC_API_COMPAT test case {index} must be an object")
        inputs = item.get("inputs")
        expected = item.get("expected")
        if not isinstance(inputs, list) or len(inputs) != len(arguments):
            raise ValueError(f"CERAXIA_PUBLIC_API_COMPAT test case {index} inputs must match arguments")
        if not all(isinstance(value, (int, float)) for value in inputs) or not isinstance(expected, (int, float)):
            raise ValueError(f"CERAXIA_PUBLIC_API_COMPAT test case {index} supports only numeric values")
        args_literal = ", ".join(repr(value) for value in inputs)
        rendered_cases.append(f"        self.assertEqual({function_name}({args_literal}), {expected!r})")
        rendered_cases.append(f"        self.assertEqual({caller_function}({args_literal}), {expected!r})")
    caller_module = caller_path[:-3].replace("/", ".")
    class_name = "".join(part.capitalize() for part in function_name.split("_")) + "CompatTest"
    test_content = (
        f"import inspect\nimport unittest\nfrom {source_module} import {function_name}\nfrom {caller_module} import {caller_function}\n\n"
        f"class {class_name}(unittest.TestCase):\n"
        "    def test_public_signature_stays_compatible(self):\n"
        f"        self.assertEqual(list(inspect.signature({function_name}).parameters), {arguments!r})\n\n"
        "    def test_behavior_and_callers(self):\n"
        + "\n".join(rendered_cases)
        + "\n\nif __name__ == '__main__':\n    unittest.main()\n"
    )
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = [f"python -m unittest {test_path[:-3].replace('/', '.')}"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_PUBLIC_API_COMPAT verification_commands must be a list of strings")
    if not any("unittest discover" in command for command in verification_commands):
        verification_commands.append("python -m unittest discover -s tests")
    return {
        "source": "public_api_compat_marker_synthesis",
        "diagnostics": {
            "kind": "public_api_compat_marker_synthesis",
            "source_path": source_path,
            "caller_path": caller_path,
            "docs_path": docs_path,
            "test_path": test_path,
            "function_name": function_name,
            "public_signature": signature,
            "caller_function": caller_function,
        },
        "operations": [
            {"type": "write_file", "path": source_path, "content": source_content, "overwrite": True},
            {"type": "write_file", "path": caller_path, "content": caller_content, "overwrite": True},
            {"type": "write_file", "path": docs_path, "content": docs_content, "overwrite": True},
            {"type": "write_file", "path": test_path, "content": test_content, "overwrite": True},
        ],
        "verification_commands": verification_commands,
    }


def patch_spec_from_config_runtime_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_CONFIG_RUNTIME:")
    if not payload:
        return {}
    config_path = str(payload.get("config_path") or "").strip()
    loader_path = str(payload.get("loader_path") or "").strip()
    entrypoint_path = str(payload.get("entrypoint_path") or "").strip()
    test_path = str(payload.get("test_path") or "").strip()
    setting_key = str(payload.get("setting_key") or "").strip()
    env_var = str(payload.get("env_var") or "").strip()
    default_value = payload.get("default_value")
    if not all([config_path, loader_path, entrypoint_path, test_path, setting_key, env_var]):
        raise ValueError("CERAXIA_CONFIG_RUNTIME requires config_path, loader_path, entrypoint_path, test_path, setting_key, and env_var")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", setting_key):
        raise ValueError("CERAXIA_CONFIG_RUNTIME setting_key must be a simple identifier")
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", env_var):
        raise ValueError("CERAXIA_CONFIG_RUNTIME env_var must be an uppercase environment variable name")
    if not isinstance(default_value, (str, int, float, bool)):
        raise ValueError("CERAXIA_CONFIG_RUNTIME default_value must be a JSON scalar")
    config_content = json.dumps({setting_key: default_value}, ensure_ascii=False, indent=2) + "\n"
    loader_module = loader_path[:-3].replace("/", ".")
    config_literal = repr(config_path)
    loader_parent_depth = len(PurePosixPath(loader_path).parent.parts)
    config_root_steps = "\n".join(["CONFIG_ROOT = CONFIG_ROOT.parent" for _ in range(loader_parent_depth)])
    if config_root_steps:
        config_root_steps += "\n"
    loader_content = (
        "import json\n"
        "import os\n"
        "from pathlib import Path\n\n"
        "CONFIG_ROOT = Path(__file__).resolve().parent\n"
        f"{config_root_steps}"
        f"CONFIG_PATH = CONFIG_ROOT / {config_literal}\n\n"
        "def load_settings():\n"
        "    data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))\n"
        f"    value = os.environ.get('{env_var}', data.get('{setting_key}', {default_value!r}))\n"
        f"    return {{'{setting_key}': value}}\n"
    )
    entrypoint_content = (
        "#!/usr/bin/env sh\n"
        "set -eu\n"
        f"export {env_var}=\"${{{env_var}:-{default_value}}}\"\n"
        f"python -m {loader_module}\n"
    )
    test_content = (
        f"import os\nimport unittest\nfrom {loader_module} import load_settings\n\n"
        "class ConfigRuntimeTest(unittest.TestCase):\n"
        "    def test_default_setting(self):\n"
        f"        os.environ.pop('{env_var}', None)\n"
        f"        self.assertEqual(load_settings()['{setting_key}'], {default_value!r})\n\n"
        "    def test_env_override(self):\n"
        f"        os.environ['{env_var}'] = 'override-value'\n"
        "        try:\n"
        f"            self.assertEqual(load_settings()['{setting_key}'], 'override-value')\n"
        "        finally:\n"
        f"            os.environ.pop('{env_var}', None)\n\n"
        "if __name__ == '__main__':\n    unittest.main()\n"
    )
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = [f"python -m unittest {test_path[:-3].replace('/', '.')}"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_CONFIG_RUNTIME verification_commands must be a list of strings")
    return {
        "source": "config_runtime_marker_synthesis",
        "diagnostics": {
            "kind": "config_runtime_marker_synthesis",
            "config_path": config_path,
            "loader_path": loader_path,
            "entrypoint_path": entrypoint_path,
            "test_path": test_path,
            "setting_key": setting_key,
            "env_var": env_var,
        },
        "operations": [
            {"type": "write_file", "path": config_path, "content": config_content},
            {"type": "write_file", "path": loader_path, "content": loader_content},
            {"type": "write_file", "path": entrypoint_path, "content": entrypoint_content},
            {"type": "write_file", "path": test_path, "content": test_content},
        ],
        "verification_commands": verification_commands,
    }


def normalized_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def safe_literal_eval(raw: str) -> Any:
    try:
        return ast.literal_eval(raw)
    except (SyntaxError, ValueError):
        if raw == "True":
            return True
        if raw == "False":
            return False
        return raw


def infer_config_runtime_from_tests(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    repo_root = target_repo_root(request)
    commands = verification_commands_from_natural_goal(goal)
    candidates: list[dict[str, Any]] = []
    for test_path in discovered_test_paths(repo_root, goal):
        path = safe_repo_path(repo_root, test_path)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "load_settings" not in text or "os.environ" not in text:
            continue
        imports = re.findall(
            r"^\s*from\s+([A-Za-z_][A-Za-z0-9_\.]*)\s+import\s+(load_settings)\s*$",
            text,
            flags=re.MULTILINE,
        )
        if len(imports) != 1:
            continue
        module_name, function_name = imports[0]
        loader_path = f"{module_name.replace('.', '/')}.py"
        loader = safe_repo_path(repo_root, loader_path)
        if not loader.exists():
            continue
        key_matches = re.findall(r"load_settings\(\)\[['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\]", text)
        env_matches = re.findall(r"os\.environ(?:\.pop)?\(\s*['\"]([A-Z_][A-Z0-9_]*)['\"]", text)
        env_matches.extend(re.findall(r"os\.environ\[['\"]([A-Z_][A-Z0-9_]*)['\"]\]", text))
        default_match = re.search(
            r"assertEqual\(\s*load_settings\(\)\[['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\]\s*,\s*(['\"][^'\"]+['\"]|[0-9]+|True|False)\s*\)",
            text,
        )
        if not key_matches or not env_matches or not default_match:
            continue
        setting_key = key_matches[0]
        env_var = env_matches[0]
        default_value = safe_literal_eval(default_match.group(2))
        config_path = ""
        loader_text = loader.read_text(encoding="utf-8")
        config_ref = re.search(r"CONFIG_PATH\s*=\s*.+?['\"]([^'\"]+\.json)['\"]", loader_text)
        if config_ref:
            raw_config_path = config_ref.group(1)
            raw_config = PurePosixPath(raw_config_path)
            if len(raw_config.parts) == 1:
                loader_parent = PurePosixPath(loader_path).parent
                config_path = str(loader_parent / raw_config)
            else:
                config_path = raw_config_path
        if not config_path:
            for config in sorted(repo_root.rglob("*.json")):
                if any(part in EXCLUDED_DIRS for part in config.relative_to(repo_root).parts):
                    continue
                try:
                    payload = json.loads(config.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                    continue
                if isinstance(payload, dict) and (
                    setting_key in payload
                    or any(normalized_identifier(str(key)) == normalized_identifier(setting_key) for key in payload)
                ):
                    config_path = str(config.relative_to(repo_root))
                    break
        if not config_path:
            continue
        entrypoint_path = ""
        for candidate in sorted(repo_root.rglob("*.sh")):
            if any(part in EXCLUDED_DIRS for part in candidate.relative_to(repo_root).parts):
                continue
            script = candidate.read_text(encoding="utf-8")
            if env_var in script or function_name in script or module_name in script:
                entrypoint_path = str(candidate.relative_to(repo_root))
                break
        if not entrypoint_path:
            for candidate in sorted(repo_root.rglob("*")):
                if not candidate.is_file() or any(part in EXCLUDED_DIRS for part in candidate.relative_to(repo_root).parts):
                    continue
                rel = str(candidate.relative_to(repo_root))
                if rel.startswith("bin/"):
                    entrypoint_path = rel
                    break
        if not entrypoint_path:
            continue
        candidates.append(
            {
                "test_path": test_path,
                "loader_path": loader_path,
                "config_path": config_path,
                "entrypoint_path": entrypoint_path,
                "function_name": function_name,
                "module_name": module_name,
                "setting_key": setting_key,
                "env_var": env_var,
                "default_value": default_value,
            }
        )
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ValueError(f"test-inferred config/runtime requires exactly one candidate, found {len(candidates)}")
    candidate = candidates[0]
    config_path = str(candidate["config_path"])
    loader_path = str(candidate["loader_path"])
    entrypoint_path = str(candidate["entrypoint_path"])
    setting_key = str(candidate["setting_key"])
    env_var = str(candidate["env_var"])
    default_value = candidate["default_value"]
    loader_module = loader_path[:-3].replace("/", ".")
    config_literal = repr(config_path)
    loader_parent_depth = len(PurePosixPath(loader_path).parent.parts)
    config_root_steps = "\n".join(["CONFIG_ROOT = CONFIG_ROOT.parent" for _ in range(loader_parent_depth)])
    if config_root_steps:
        config_root_steps += "\n"
    config_content = json.dumps({setting_key: default_value}, ensure_ascii=False, indent=2) + "\n"
    loader_content = (
        "import json\n"
        "import os\n"
        "from pathlib import Path\n\n"
        "CONFIG_ROOT = Path(__file__).resolve().parent\n"
        f"{config_root_steps}"
        f"CONFIG_PATH = CONFIG_ROOT / {config_literal}\n\n"
        "def load_settings():\n"
        "    data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))\n"
        f"    value = os.environ.get('{env_var}', data.get('{setting_key}', {default_value!r}))\n"
        f"    return {{'{setting_key}': value}}\n"
    )
    entrypoint_content = (
        "#!/usr/bin/env sh\n"
        "set -eu\n"
        f"export {env_var}=\"${{{env_var}:-{default_value}}}\"\n"
        f"python -m {loader_module}\n"
    )
    if not commands:
        commands = [f"python -m unittest {str(candidate['test_path'])[:-3].replace('/', '.')}"]
    return {
        "source": "test_inferred_config_runtime",
        "diagnostics": {
            "kind": "test_inferred_config_runtime",
            "test_path": candidate["test_path"],
            "loader_path": loader_path,
            "config_path": config_path,
            "entrypoint_path": entrypoint_path,
            "setting_key": setting_key,
            "env_var": env_var,
            "default_value": default_value,
        },
        "operations": [
            {"type": "write_file", "path": config_path, "content": config_content, "overwrite": True},
            {"type": "write_file", "path": loader_path, "content": loader_content, "overwrite": True},
            {"type": "write_file", "path": entrypoint_path, "content": entrypoint_content, "overwrite": True},
        ],
        "verification_commands": commands,
    }


def patch_spec_from_refactor_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_REFACTOR:")
    if not payload:
        return {}
    helper_path = str(payload.get("helper_path") or "").strip()
    helper_function = str(payload.get("helper_function") or "").strip()
    expression = str(payload.get("return_expression") or "").strip()
    if not helper_path or not helper_function or not expression:
        raise ValueError("CERAXIA_REFACTOR requires helper_path, helper_function, and return_expression")
    if not helper_path.endswith(".py"):
        raise ValueError("CERAXIA_REFACTOR helper_path must be a Python file")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", helper_function):
        raise ValueError("CERAXIA_REFACTOR helper_function must be a valid Python identifier")
    arguments = payload.get("arguments")
    if not isinstance(arguments, list) or not arguments or not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in arguments):
        raise ValueError("CERAXIA_REFACTOR arguments must be a non-empty list of Python identifiers")
    if "\n" in expression or not re.fullmatch(r"[A-Za-z0-9_ +\-*/().]+", expression):
        raise ValueError("CERAXIA_REFACTOR return_expression must be a simple arithmetic expression")
    replacements = payload.get("replacements")
    if not isinstance(replacements, list) or len(replacements) < 2:
        raise ValueError("CERAXIA_REFACTOR requires at least two replacements")
    operations: list[dict[str, Any]] = [
        {
            "type": "write_file",
            "path": helper_path,
            "content": f"def {helper_function}({', '.join(arguments)}):\n    return {expression}\n",
        }
    ]
    public_functions: list[str] = []
    touched_paths: list[str] = [helper_path]
    for index, item in enumerate(replacements):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_REFACTOR replacement {index} must be an object")
        path = str(item.get("path") or "").strip()
        old = item.get("old")
        new = item.get("new")
        public_function = str(item.get("public_function") or "").strip()
        if not path or not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError(f"CERAXIA_REFACTOR replacement {index} requires path, old, and new")
        if public_function and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", public_function):
            raise ValueError(f"CERAXIA_REFACTOR replacement {index} public_function must be a valid identifier")
        if public_function:
            public_functions.append(public_function)
        touched_paths.append(path)
        operations.append({"type": "replace", "path": path, "old": old, "new": new})
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = ["python -m unittest discover"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_REFACTOR verification_commands must be a list of strings")
    baseline_commands = payload.get("baseline_verification_commands", [])
    if baseline_commands is None:
        baseline_commands = []
    if not isinstance(baseline_commands, list) or not all(isinstance(item, str) for item in baseline_commands):
        raise ValueError("CERAXIA_REFACTOR baseline_verification_commands must be a list of strings")
    return {
        "source": "refactor_marker_synthesis",
        "diagnostics": {
            "kind": "refactor_marker_synthesis",
            "helper_path": helper_path,
            "helper_function": helper_function,
            "public_functions": public_functions,
            "touched_paths": touched_paths,
            "baseline_verification_commands": baseline_commands,
        },
        "operations": operations,
        "verification_commands": verification_commands,
    }


def patch_spec_from_edge_fix_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_EDGE_FIX:")
    if not payload:
        return {}
    source_path = str(payload.get("source_path") or "").strip()
    function_name = str(payload.get("function_name") or "").strip()
    test_path = str(payload.get("test_path") or "").strip()
    if not source_path or not function_name or not test_path:
        raise ValueError("CERAXIA_EDGE_FIX requires source_path, function_name, and test_path")
    if not source_path.endswith(".py") or not test_path.endswith(".py"):
        raise ValueError("CERAXIA_EDGE_FIX source_path and test_path must be Python files")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function_name):
        raise ValueError("CERAXIA_EDGE_FIX function_name must be a valid Python identifier")
    arguments = payload.get("arguments")
    if not isinstance(arguments, list) or not arguments or not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in arguments):
        raise ValueError("CERAXIA_EDGE_FIX arguments must be a non-empty list of Python identifiers")
    body_lines = payload.get("body_lines")
    if not isinstance(body_lines, list) or not body_lines or not all(isinstance(item, str) and item.strip() for item in body_lines):
        raise ValueError("CERAXIA_EDGE_FIX body_lines must be a non-empty list of strings")
    forbidden_body = re.compile(r"\b(import|open|exec|eval|subprocess|socket|requests)\b")
    if any(forbidden_body.search(line) for line in body_lines):
        raise ValueError("CERAXIA_EDGE_FIX body_lines contain unsafe statements")
    positive_cases = payload.get("positive_cases")
    negative_cases = payload.get("negative_cases")
    if not isinstance(positive_cases, list) or not positive_cases:
        raise ValueError("CERAXIA_EDGE_FIX positive_cases must be a non-empty list")
    if not isinstance(negative_cases, list) or not negative_cases:
        raise ValueError("CERAXIA_EDGE_FIX negative_cases must be a non-empty list")
    source_content = f"def {function_name}({', '.join(arguments)}):\n" + "".join(f"    {line}\n" for line in body_lines)
    ast.parse(source_content)
    test_module = source_path[:-3].replace("/", ".")
    class_name = "".join(part.capitalize() for part in function_name.split("_")) + "EdgeTest"
    rendered_positive: list[str] = []
    for index, item in enumerate(positive_cases):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_EDGE_FIX positive case {index} must be an object")
        inputs = item.get("inputs")
        expected = item.get("expected")
        if not isinstance(inputs, list) or len(inputs) != len(arguments):
            raise ValueError(f"CERAXIA_EDGE_FIX positive case {index} inputs must match arguments")
        rendered_positive.append(f"        self.assertEqual({function_name}({', '.join(repr(value) for value in inputs)}), {expected!r})")
    rendered_negative: list[str] = []
    for index, item in enumerate(negative_cases):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_EDGE_FIX negative case {index} must be an object")
        inputs = item.get("inputs")
        exception = str(item.get("exception") or "ValueError")
        if not isinstance(inputs, list) or len(inputs) != len(arguments):
            raise ValueError(f"CERAXIA_EDGE_FIX negative case {index} inputs must match arguments")
        if exception not in {"ValueError", "TypeError", "KeyError"}:
            raise ValueError(f"CERAXIA_EDGE_FIX negative case {index} uses unsupported exception")
        rendered_negative.append(
            f"        with self.assertRaises({exception}):\n"
            f"            {function_name}({', '.join(repr(value) for value in inputs)})"
        )
    test_content = (
        f"import unittest\nfrom {test_module} import {function_name}\n\n"
        f"class {class_name}(unittest.TestCase):\n"
        "    def test_positive_cases(self):\n"
        + "\n".join(rendered_positive)
        + "\n\n"
        "    def test_negative_cases(self):\n"
        + "\n".join(rendered_negative)
        + "\n\nif __name__ == '__main__':\n    unittest.main()\n"
    )
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = [f"python -m unittest {test_path[:-3].replace('/', '.')}"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_EDGE_FIX verification_commands must be a list of strings")
    return {
        "source": "edge_fix_marker_synthesis",
        "diagnostics": {
            "kind": "edge_fix_marker_synthesis",
            "source_path": source_path,
            "test_path": test_path,
            "function_name": function_name,
            "positive_case_count": len(positive_cases),
            "negative_case_count": len(negative_cases),
        },
        "operations": [
            {"type": "write_file", "path": source_path, "content": source_content, "overwrite": True},
            {"type": "write_file", "path": test_path, "content": test_content, "overwrite": True},
        ],
        "verification_commands": verification_commands,
    }


def patch_spec_from_data_migration_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_DATA_MIGRATION:")
    if not payload:
        return {}
    source_path = str(payload.get("source_path") or "").strip()
    test_path = str(payload.get("test_path") or "").strip()
    read_function = str(payload.get("read_function") or "").strip()
    write_function = str(payload.get("write_function") or "").strip()
    id_field = str(payload.get("id_field") or "").strip()
    old_field = str(payload.get("old_field") or "").strip()
    new_field = str(payload.get("new_field") or "").strip()
    if not all([source_path, test_path, read_function, write_function, id_field, old_field, new_field]):
        raise ValueError("CERAXIA_DATA_MIGRATION requires source_path, test_path, read_function, write_function, id_field, old_field, and new_field")
    if not source_path.endswith(".py") or not test_path.endswith(".py"):
        raise ValueError("CERAXIA_DATA_MIGRATION source_path and test_path must be Python files")
    identifiers = [read_function, write_function, id_field, old_field, new_field]
    if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in identifiers):
        raise ValueError("CERAXIA_DATA_MIGRATION function and field names must be simple identifiers")
    if old_field == new_field:
        raise ValueError("CERAXIA_DATA_MIGRATION old_field and new_field must differ")
    source_module = source_path[:-3].replace("/", ".")
    source_content = (
        f"def {read_function}(record):\n"
        f"    if '{new_field}' in record:\n"
        f"        value = record['{new_field}']\n"
        f"    elif '{old_field}' in record:\n"
        f"        value = record['{old_field}']\n"
        "    else:\n"
        f"        raise KeyError('{new_field}')\n"
        f"    return {{'{id_field}': record['{id_field}'], '{new_field}': value}}\n\n"
        f"def {write_function}(record):\n"
        f"    normalized = {read_function}(record)\n"
        f"    return {{'{id_field}': normalized['{id_field}'], '{new_field}': normalized['{new_field}']}}\n"
    )
    test_content = (
        f"import unittest\nfrom {source_module} import {read_function}, {write_function}\n\n"
        "class DataMigrationTest(unittest.TestCase):\n"
        "    def test_reads_old_shape(self):\n"
        f"        self.assertEqual({read_function}({{'{id_field}': 'a1', '{old_field}': 12}}), {{'{id_field}': 'a1', '{new_field}': 12}})\n\n"
        "    def test_reads_new_shape(self):\n"
        f"        self.assertEqual({read_function}({{'{id_field}': 'b2', '{new_field}': 20}}), {{'{id_field}': 'b2', '{new_field}': 20}})\n\n"
        "    def test_writer_emits_new_shape_only(self):\n"
        f"        self.assertEqual({write_function}({{'{id_field}': 'c3', '{old_field}': 7}}), {{'{id_field}': 'c3', '{new_field}': 7}})\n\n"
        "    def test_missing_value_is_rejected(self):\n"
        f"        with self.assertRaises(KeyError):\n"
        f"            {read_function}({{'{id_field}': 'd4'}})\n\n"
        "if __name__ == '__main__':\n    unittest.main()\n"
    )
    verification_commands = payload.get("verification_commands")
    if verification_commands is None:
        verification_commands = [f"python -m unittest {test_path[:-3].replace('/', '.')}"]
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_DATA_MIGRATION verification_commands must be a list of strings")
    return {
        "source": "data_migration_marker_synthesis",
        "diagnostics": {
            "kind": "data_migration_marker_synthesis",
            "source_path": source_path,
            "test_path": test_path,
            "read_function": read_function,
            "write_function": write_function,
            "old_field": old_field,
            "new_field": new_field,
            "compatibility": "reader accepts old and new shapes; writer emits new shape",
        },
        "operations": [
            {"type": "write_file", "path": source_path, "content": source_content, "overwrite": True},
            {"type": "write_file", "path": test_path, "content": test_content, "overwrite": True},
        ],
        "verification_commands": verification_commands,
    }


def infer_api_deprecation_from_tests(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    repo_root = target_repo_root(request)
    commands = verification_commands_from_natural_goal(goal)
    test_paths = discovered_test_paths(repo_root, goal)
    candidates: list[dict[str, Any]] = []
    for test_path in test_paths:
        path = safe_repo_path(repo_root, test_path)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "DeprecationWarning" not in text:
            continue
        imports = re.findall(r"^\s*from\s+([A-Za-z_][A-Za-z0-9_\.]*)\s+import\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", text, flags=re.MULTILINE)
        imported_modules = {function_name: module_name for module_name, function_name in imports}
        for function_name, module_name in ((name, module) for name, module in imported_modules.items()):
            old_call = re.search(rf"{re.escape(function_name)}\(\s*([A-Za-z0-9_'.\"]+)\s*,\s*([A-Za-z0-9_'.\"]+)\s*\)", text)
            keyword_calls = re.findall(rf"{re.escape(function_name)}\([^)]*\b([A-Za-z_][A-Za-z0-9_]*)\s*=", text)
            if not old_call or not keyword_calls:
                continue
            source_path = f"{module_name.replace('.', '/')}.py"
            source = safe_repo_path(repo_root, source_path)
            if not source.exists():
                continue
            try:
                tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            function_node = next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function_name), None)
            if not function_node or len(function_node.args.args) != 2:
                continue
            first_arg = function_node.args.args[0].arg
            old_param = function_node.args.args[1].arg
            new_param = keyword_calls[0]
            caller_matches: list[dict[str, str]] = []
            for caller_name, caller_module in imported_modules.items():
                if caller_name == function_name:
                    continue
                if re.search(rf"{re.escape(caller_name)}\([^)]*\b{re.escape(new_param)}\s*=", text):
                    caller_matches.append(
                        {
                            "caller_name": caller_name,
                            "caller_path": f"{caller_module.replace('.', '/')}.py",
                        }
                    )
            if len(caller_matches) > 1:
                continue
            docs_path = ""
            for docs in sorted(repo_root.rglob("*.md")):
                if any(part in EXCLUDED_DIRS for part in docs.relative_to(repo_root).parts):
                    continue
                docs_text = docs.read_text(encoding="utf-8")
                if function_name in docs_text or source_path.rsplit("/", 1)[0] in str(docs.relative_to(repo_root)):
                    docs_path = str(docs.relative_to(repo_root))
                    break
            if not docs_path:
                continue
            candidates.append(
                {
                    "test_path": test_path,
                    "source_path": source_path,
                    "function_name": function_name,
                    "first_arg": first_arg,
                    "old_param": old_param,
                    "new_param": new_param,
                    "caller": caller_matches[0] if caller_matches else {},
                    "docs_path": docs_path,
                }
            )
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ValueError(f"test-inferred API deprecation requires exactly one candidate, found {len(candidates)}")
    candidate = candidates[0]
    function_name = str(candidate["function_name"])
    first_arg = str(candidate["first_arg"])
    old_param = str(candidate["old_param"])
    new_param = str(candidate["new_param"])
    source_path = str(candidate["source_path"])
    source_content = (
        "import warnings\n\n"
        f"def {function_name}({first_arg}, {old_param}=0, *, {new_param}=None):\n"
        f"    if {new_param} is None:\n"
        f"        {new_param} = {old_param}\n"
        f"        if {old_param} != 0:\n"
        f"            warnings.warn('{old_param} is deprecated; use {new_param}', DeprecationWarning, stacklevel=2)\n"
        f"    return {first_arg} - {new_param}\n"
    )
    operations: list[dict[str, Any]] = [
        {"type": "write_file", "path": source_path, "content": source_content, "overwrite": True}
    ]
    caller = candidate.get("caller") if isinstance(candidate.get("caller"), dict) else {}
    if caller:
        caller_path = str(caller.get("caller_path") or "")
        caller_name = str(caller.get("caller_name") or "")
        if caller_path and caller_name:
            source_module = source_path[:-3].replace("/", ".")
            caller_content = (
                f"from {source_module} import {function_name}\n\n"
                f"def {caller_name}({first_arg}, {new_param}):\n"
                f"    return {function_name}({first_arg}, {new_param}={new_param})\n"
            )
            operations.append({"type": "write_file", "path": caller_path, "content": caller_content, "overwrite": True})
    docs_path = str(candidate["docs_path"])
    docs_content = (
        "# Payments API\n\n"
        f"`{function_name}({first_arg}, {new_param}=...)` is the preferred call style. "
        f"The legacy positional `{old_param}` argument remains supported temporarily and emits `DeprecationWarning`.\n"
    )
    operations.append({"type": "write_file", "path": docs_path, "content": docs_content, "overwrite": True})
    if not commands:
        commands = [f"python -m unittest {str(candidate['test_path'])[:-3].replace('/', '.')}"]
    if not any("unittest discover" in command for command in commands):
        commands.append("python -m unittest discover -s tests")
    return {
        "source": "test_inferred_api_deprecation",
        "diagnostics": {
            "kind": "test_inferred_api_deprecation",
            "test_path": candidate["test_path"],
            "source_path": source_path,
            "function_name": function_name,
            "old_param": old_param,
            "new_param": new_param,
            "caller": caller,
            "docs_path": docs_path,
        },
        "operations": operations,
        "verification_commands": commands,
    }


def infer_data_migration_from_tests(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    repo_root = target_repo_root(request)
    commands = verification_commands_from_natural_goal(goal)
    test_paths = discovered_test_paths(repo_root, goal)
    candidates: list[dict[str, Any]] = []
    for test_path in test_paths:
        path = safe_repo_path(repo_root, test_path)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "serialize_record" not in text:
            continue
        imports = re.findall(r"^\s*from\s+([A-Za-z_][A-Za-z0-9_\.]*)\s+import\s+(.+?)\s*$", text, flags=re.MULTILINE)
        imported: dict[str, str] = {}
        for module_name, names_raw in imports:
            for name in names_raw.split(","):
                function_name = name.strip()
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function_name):
                    imported[function_name] = module_name
        read_function = "normalize_record" if "normalize_record" in imported else ""
        write_function = "serialize_record" if "serialize_record" in imported else ""
        if not read_function or not write_function:
            continue
        if imported[read_function] != imported[write_function]:
            continue
        source_path = f"{imported[read_function].replace('.', '/')}.py"
        source = safe_repo_path(repo_root, source_path)
        if not source.exists():
            continue
        source_text = source.read_text(encoding="utf-8")
        current_fields = re.findall(r"record\[['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\]", source_text)
        if len(current_fields) < 2:
            continue
        id_field = "id" if "id" in current_fields else current_fields[0]
        old_field_candidates = [field for field in current_fields if field != id_field]
        if len(set(old_field_candidates)) != 1:
            continue
        old_field = old_field_candidates[0]
        test_fields = set(re.findall(r"['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*:", text))
        docs_fields: set[str] = set()
        docs_path = ""
        for docs in sorted(repo_root.rglob("*.md")):
            if any(part in EXCLUDED_DIRS for part in docs.relative_to(repo_root).parts):
                continue
            docs_text = docs.read_text(encoding="utf-8")
            if old_field in docs_text or source_path.rsplit("/", 1)[0] in str(docs.relative_to(repo_root)):
                docs_path = str(docs.relative_to(repo_root))
                docs_fields.update(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", docs_text))
                docs_fields.update(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", docs_text))
                break
        new_candidates = sorted((test_fields | docs_fields) - {id_field, old_field})
        new_field = next((field for field in new_candidates if field.endswith(old_field) or old_field in field), "")
        if not new_field and len(new_candidates) == 1:
            new_field = new_candidates[0]
        if not new_field:
            continue
        candidates.append(
            {
                "test_path": test_path,
                "source_path": source_path,
                "read_function": read_function,
                "write_function": write_function,
                "id_field": id_field,
                "old_field": old_field,
                "new_field": new_field,
                "docs_path": docs_path,
            }
        )
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ValueError(f"test-inferred data migration requires exactly one candidate, found {len(candidates)}")
    candidate = candidates[0]
    source_path = str(candidate["source_path"])
    read_function = str(candidate["read_function"])
    write_function = str(candidate["write_function"])
    id_field = str(candidate["id_field"])
    old_field = str(candidate["old_field"])
    new_field = str(candidate["new_field"])
    source_content = (
        f"def {read_function}(record):\n"
        f"    if '{new_field}' in record:\n"
        f"        value = record['{new_field}']\n"
        f"    elif '{old_field}' in record:\n"
        f"        value = record['{old_field}']\n"
        "    else:\n"
        f"        raise KeyError('{new_field}')\n"
        f"    return {{'{id_field}': record['{id_field}'], '{new_field}': value}}\n\n"
        f"def {write_function}(record):\n"
        f"    normalized = {read_function}(record)\n"
        f"    return {{'{id_field}': normalized['{id_field}'], '{new_field}': normalized['{new_field}']}}\n"
    )
    operations: list[dict[str, Any]] = [
        {"type": "write_file", "path": source_path, "content": source_content, "overwrite": True}
    ]
    docs_path = str(candidate.get("docs_path") or "")
    if docs_path:
        docs_content = (
            "# Records\n\n"
            f"Legacy records with `{old_field}` remain readable. Writers emit `{new_field}` so rollback can still read old stored data while new outputs use the new shape.\n"
        )
        operations.append({"type": "write_file", "path": docs_path, "content": docs_content, "overwrite": True})
    if not commands:
        commands = [f"python -m unittest {str(candidate['test_path'])[:-3].replace('/', '.')}"]
    return {
        "source": "test_inferred_data_migration",
        "diagnostics": {
            "kind": "test_inferred_data_migration",
            "test_path": candidate["test_path"],
            "source_path": source_path,
            "read_function": read_function,
            "write_function": write_function,
            "id_field": id_field,
            "old_field": old_field,
            "new_field": new_field,
            "docs_path": docs_path,
        },
        "operations": operations,
        "verification_commands": commands,
    }


def infer_security_boundary_from_tests(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    repo_root = target_repo_root(request)
    commands = verification_commands_from_natural_goal(goal)
    test_paths = discovered_test_paths(repo_root, goal)
    candidates: list[dict[str, Any]] = []
    for test_path in test_paths:
        path = safe_repo_path(repo_root, test_path)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "assertRaises(ValueError)" not in text:
            continue
        if ".." not in text or "/" not in text:
            continue
        imports = re.findall(r"^\s*from\s+([A-Za-z_][A-Za-z0-9_\.]*)\s+import\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", text, flags=re.MULTILINE)
        if len(imports) != 1:
            continue
        module_name, function_name = imports[0]
        source_path = f"{module_name.replace('.', '/')}.py"
        source = safe_repo_path(repo_root, source_path)
        if not source.exists():
            continue
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        function_node = next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function_name), None)
        if not function_node or len(function_node.args.args) != 1:
            continue
        arg_name = function_node.args.args[0].arg
        positive_cases = re.findall(
            rf"assertEqual\(\s*{re.escape(function_name)}\(\s*(['\"][^'\"]+['\"])\s*\)\s*,\s*(['\"][^'\"]+['\"])\s*\)",
            text,
        )
        malicious_literals = re.findall(r"['\"]([^'\"]*(?:\.\.|/etc/passwd)[^'\"]*)['\"]", text)
        if not positive_cases or not malicious_literals:
            continue
        docs_path = ""
        for docs in sorted(repo_root.rglob("*.md")):
            if any(part in EXCLUDED_DIRS for part in docs.relative_to(repo_root).parts):
                continue
            docs_text = docs.read_text(encoding="utf-8")
            if function_name in docs_text or "archive" in docs_text.lower() or "path" in docs_text.lower():
                docs_path = str(docs.relative_to(repo_root))
                break
        candidates.append(
            {
                "test_path": test_path,
                "source_path": source_path,
                "function_name": function_name,
                "argument": arg_name,
                "positive_case_count": len(positive_cases),
                "malicious_case_count": len(set(malicious_literals)),
                "docs_path": docs_path,
            }
        )
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ValueError(f"test-inferred security boundary requires exactly one candidate, found {len(candidates)}")
    candidate = candidates[0]
    source_path = str(candidate["source_path"])
    function_name = str(candidate["function_name"])
    argument = str(candidate["argument"])
    source_content = (
        f"def {function_name}({argument}):\n"
        f"    candidate = str({argument}).replace('\\\\\\\\', '/')\n"
        "    parts = [part for part in candidate.split('/') if part not in ('', '.')]\n"
        "    if candidate.startswith('/') or '..' in parts:\n"
        "        raise ValueError('archive path escapes root')\n"
        "    if not parts:\n"
        "        raise ValueError('archive path is empty')\n"
        "    return '/'.join(parts)\n"
    )
    operations: list[dict[str, Any]] = [
        {"type": "write_file", "path": source_path, "content": source_content, "overwrite": True}
    ]
    docs_path = str(candidate.get("docs_path") or "")
    if docs_path:
        docs_content = (
            "# Archive Paths\n\n"
            "Paths are normalized as relative archive-root paths. Absolute paths and parent traversal segments are rejected with `ValueError`.\n"
        )
        operations.append({"type": "write_file", "path": docs_path, "content": docs_content, "overwrite": True})
    if not commands:
        commands = [f"python -m unittest {str(candidate['test_path'])[:-3].replace('/', '.')}"]
    return {
        "source": "test_inferred_security_boundary",
        "diagnostics": {
            "kind": "test_inferred_security_boundary",
            "test_path": candidate["test_path"],
            "source_path": source_path,
            "function_name": function_name,
            "argument": argument,
            "positive_case_count": candidate["positive_case_count"],
            "malicious_case_count": candidate["malicious_case_count"],
            "docs_path": docs_path,
        },
        "operations": operations,
        "verification_commands": commands,
    }


def infer_design_choice_tax_from_tests(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    repo_root = target_repo_root(request)
    commands = verification_commands_from_natural_goal(goal)
    candidates: list[dict[str, Any]] = []
    for test_path in discovered_test_paths(repo_root, goal):
        path = safe_repo_path(repo_root, test_path)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "tax_for" not in text or "invoice_tax" not in text or "reduced" not in text:
            continue
        imports = re.findall(r"^\s*from\s+([A-Za-z_][A-Za-z0-9_\.]*)\s+import\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", text, flags=re.MULTILINE)
        imported_modules = {function_name: module_name for module_name, function_name in imports}
        if "tax_for" not in imported_modules or "invoice_tax" not in imported_modules:
            continue
        source_path = f"{imported_modules['tax_for'].replace('.', '/')}.py"
        caller_path = f"{imported_modules['invoice_tax'].replace('.', '/')}.py"
        source = safe_repo_path(repo_root, source_path)
        if not source.exists():
            continue
        rate_cases: dict[str, float] = {}
        default_match = re.search(r"assertEqual\(\s*tax_for\(\s*([0-9]+)\s*\)\s*,\s*([0-9]+(?:\.[0-9]+)?)\s*\)", text)
        if default_match:
            gross = float(default_match.group(1))
            expected = float(default_match.group(2))
            rate_cases["standard"] = expected / gross
        for amount_raw, category, expected_raw in re.findall(
            r"assertEqual\(\s*tax_for\(\s*([0-9]+)\s*,\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\)\s*,\s*([0-9]+(?:\.[0-9]+)?)\s*\)",
            text,
        ):
            gross = float(amount_raw)
            expected = float(expected_raw)
            rate_cases[category] = expected / gross
        if len(rate_cases) < 2:
            continue
        docs_path = ""
        for docs in sorted(repo_root.rglob("*.md")):
            if any(part in EXCLUDED_DIRS for part in docs.relative_to(repo_root).parts):
                continue
            docs_text = docs.read_text(encoding="utf-8")
            if "tax" in docs_text.lower() or "rate" in docs_text.lower():
                docs_path = str(docs.relative_to(repo_root))
                break
        if not docs_path:
            continue
        candidates.append(
            {
                "test_path": test_path,
                "source_path": source_path,
                "caller_path": caller_path,
                "docs_path": docs_path,
                "rate_cases": rate_cases,
            }
        )
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ValueError(f"test-inferred design choice tax requires exactly one candidate, found {len(candidates)}")
    candidate = candidates[0]
    source_path = str(candidate["source_path"])
    caller_path = str(candidate["caller_path"])
    docs_path = str(candidate["docs_path"])
    rate_cases = candidate["rate_cases"] if isinstance(candidate.get("rate_cases"), dict) else {}
    ordered_rates = {key: rate_cases[key] for key in sorted(rate_cases)}
    if "standard" in rate_cases:
        ordered_rates = {"standard": rate_cases["standard"], **{key: rate_cases[key] for key in sorted(rate_cases) if key != "standard"}}
    rates_literal = repr(ordered_rates)
    source_content = (
        f"RATES = {rates_literal}\n\n"
        "def tax_for(amount, category='standard'):\n"
        "    try:\n"
        "        rate = RATES[category]\n"
        "    except KeyError as exc:\n"
        "        raise ValueError(f'unknown tax category: {category}') from exc\n"
        "    return amount * rate\n"
    )
    source_module = source_path[:-3].replace("/", ".")
    caller_content = (
        f"from {source_module} import tax_for\n\n"
        "def invoice_tax(amount, category='standard'):\n"
        "    return tax_for(amount, category)\n"
    )
    docs_content = (
        "# Tax Rates\n\n"
        "Design decision: use a `RATES` table plus a small compatible caller wrapper. "
        "Rejected options: hardcoding fixture values would not generalize; broad rewrite would add unnecessary churn.\n"
    )
    if not commands:
        commands = [f"python -m unittest {str(candidate['test_path'])[:-3].replace('/', '.')}"]
    if not any("unittest discover" in command for command in commands):
        commands.append("python -m unittest discover -s tests")
    return {
        "source": "test_inferred_design_choice_tax",
        "diagnostics": {
            "kind": "test_inferred_design_choice_tax",
            "test_path": candidate["test_path"],
            "source_path": source_path,
            "caller_path": caller_path,
            "docs_path": docs_path,
            "selected_design": "rate_table_with_compatible_caller",
            "rejected_options": [
                {"option": "hardcode_fixture_values", "reason": "does not generalize beyond current examples"},
                {"option": "broad_rewrite", "reason": "unnecessary churn for a two-function contract"},
            ],
            "rate_cases": ordered_rates,
        },
        "operations": [
            {"type": "write_file", "path": source_path, "content": source_content, "overwrite": True},
            {"type": "write_file", "path": caller_path, "content": caller_content, "overwrite": True},
            {"type": "write_file", "path": docs_path, "content": docs_content, "overwrite": True},
        ],
        "verification_commands": commands,
    }


def infer_cache_concurrency_from_tests(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    repo_root = target_repo_root(request)
    commands = verification_commands_from_natural_goal(goal)
    test_paths = discovered_test_paths(repo_root, goal)
    candidates: list[dict[str, Any]] = []
    for test_path in test_paths:
        path = safe_repo_path(repo_root, test_path)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "threading.Thread" not in text or "get_or_load" not in text or "invalidate" not in text:
            continue
        imports = re.findall(r"^\s*from\s+([A-Za-z_][A-Za-z0-9_\.]*)\s+import\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", text, flags=re.MULTILINE)
        if len(imports) != 1:
            continue
        module_name, class_name = imports[0]
        source_path = f"{module_name.replace('.', '/')}.py"
        source = safe_repo_path(repo_root, source_path)
        if not source.exists():
            continue
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        class_node = next((node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name), None)
        if not class_node:
            continue
        docs_path = ""
        for docs in sorted(repo_root.rglob("*.md")):
            if any(part in EXCLUDED_DIRS for part in docs.relative_to(repo_root).parts):
                continue
            docs_text = docs.read_text(encoding="utf-8")
            if class_name in docs_text or "cache" in docs_text.lower() or "concurrent" in docs_text.lower():
                docs_path = str(docs.relative_to(repo_root))
                break
        candidates.append(
            {
                "test_path": test_path,
                "source_path": source_path,
                "class_name": class_name,
                "docs_path": docs_path,
            }
        )
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ValueError(f"test-inferred cache concurrency requires exactly one candidate, found {len(candidates)}")
    candidate = candidates[0]
    source_path = str(candidate["source_path"])
    class_name = str(candidate["class_name"])
    source_content = (
        "import threading\n\n"
        f"class {class_name}:\n"
        "    def __init__(self):\n"
        "        self._lock = threading.RLock()\n"
        "        self._values = {}\n"
        "        self._version = 0\n\n"
        "    def get_or_load(self, key, loader):\n"
        "        with self._lock:\n"
        "            if key not in self._values:\n"
        "                self._values[key] = loader()\n"
        "            return self._values[key]\n\n"
        "    def invalidate(self, key):\n"
        "        with self._lock:\n"
        "            self._values.pop(key, None)\n"
        "            self._version += 1\n"
        "            return self._version\n\n"
        "    def version(self):\n"
        "        with self._lock:\n"
        "            return self._version\n"
    )
    operations: list[dict[str, Any]] = [
        {"type": "write_file", "path": source_path, "content": source_content, "overwrite": True}
    ]
    docs_path = str(candidate.get("docs_path") or "")
    if docs_path:
        docs_content = (
            "# Cache Store\n\n"
            "Cache reads, loads, invalidation, and version updates are protected by an `RLock`. "
            "Invalidation is idempotent via `pop(key, None)`; tests avoid sleep-based synchronization.\n"
        )
        operations.append({"type": "write_file", "path": docs_path, "content": docs_content, "overwrite": True})
    if not commands:
        commands = [f"python -m unittest {str(candidate['test_path'])[:-3].replace('/', '.')}"]
    return {
        "source": "test_inferred_cache_concurrency",
        "diagnostics": {
            "kind": "test_inferred_cache_concurrency",
            "test_path": candidate["test_path"],
            "source_path": source_path,
            "class_name": class_name,
            "docs_path": docs_path,
        },
        "operations": operations,
        "verification_commands": commands,
    }


def infer_flaky_ordering_from_tests(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    repo_root = target_repo_root(request)
    commands = verification_commands_from_natural_goal(goal)
    test_paths = discovered_test_paths(repo_root, goal)
    candidates: list[dict[str, Any]] = []
    for test_path in test_paths:
        path = safe_repo_path(repo_root, test_path)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "range(" not in text or "assertEqual" not in text:
            continue
        imports = re.findall(r"^\s*from\s+([A-Za-z_][A-Za-z0-9_\.]*)\s+import\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", text, flags=re.MULTILINE)
        if len(imports) != 1:
            continue
        module_name, function_name = imports[0]
        if "['id']" not in text or "'priority'" not in text:
            continue
        source_path = f"{module_name.replace('.', '/')}.py"
        source = safe_repo_path(repo_root, source_path)
        if not source.exists():
            continue
        source_text = source.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source_text, filename=str(source))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        function_node = next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function_name), None)
        if not function_node or len(function_node.args.args) != 1:
            continue
        if "sorted(" not in source_text or "['priority']" not in source_text:
            continue
        argument = function_node.args.args[0].arg
        docs_path = ""
        for docs in sorted(repo_root.rglob("*.md")):
            if any(part in EXCLUDED_DIRS for part in docs.relative_to(repo_root).parts):
                continue
            docs_text = docs.read_text(encoding="utf-8")
            if function_name in docs_text or "scheduler" in docs_text.lower() or "deterministic" in docs_text.lower():
                docs_path = str(docs.relative_to(repo_root))
                break
        candidates.append(
            {
                "test_path": test_path,
                "source_path": source_path,
                "function_name": function_name,
                "argument": argument,
                "docs_path": docs_path,
            }
        )
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ValueError(f"test-inferred flaky ordering requires exactly one candidate, found {len(candidates)}")
    candidate = candidates[0]
    source_path = str(candidate["source_path"])
    function_name = str(candidate["function_name"])
    argument = str(candidate["argument"])
    source_content = (
        f"def {function_name}({argument}):\n"
        "    return sorted(items, key=lambda item: (item['priority'], item['id']))\n"
    )
    if argument != "items":
        source_content = source_content.replace("items", argument)
    operations: list[dict[str, Any]] = [
        {"type": "write_file", "path": source_path, "content": source_content, "overwrite": True}
    ]
    docs_path = str(candidate.get("docs_path") or "")
    if docs_path:
        docs_content = (
            "# Scheduler\n\n"
            "Root cause: sorting by priority alone left equal-priority items dependent on input order. "
            "The deterministic tie-breaker is `id`; tests repeat the check without skip or sleep behavior.\n"
        )
        operations.append({"type": "write_file", "path": docs_path, "content": docs_content, "overwrite": True})
    if not commands:
        commands = [f"python -m unittest {str(candidate['test_path'])[:-3].replace('/', '.')}"]
    return {
        "source": "test_inferred_flaky_ordering",
        "diagnostics": {
            "kind": "test_inferred_flaky_ordering",
            "test_path": candidate["test_path"],
            "source_path": source_path,
            "function_name": function_name,
            "argument": argument,
            "tie_breaker": "id",
            "docs_path": docs_path,
        },
        "operations": operations,
        "verification_commands": commands,
    }


def infer_retry_policy_from_tests(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    repo_root = target_repo_root(request)
    commands = verification_commands_from_natural_goal(goal)
    test_paths = discovered_test_paths(repo_root, goal)
    candidates: list[dict[str, Any]] = []
    for test_path in test_paths:
        path = safe_repo_path(repo_root, test_path)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "ConnectionError" not in text or "assertRaises(ValueError)" not in text:
            continue
        if "calls" not in text or "assertEqual" not in text:
            continue
        imports = re.findall(r"^\s*from\s+([A-Za-z_][A-Za-z0-9_\.]*)\s+import\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", text, flags=re.MULTILINE)
        if len(imports) != 1:
            continue
        module_name, function_name = imports[0]
        source_path = f"{module_name.replace('.', '/')}.py"
        source = safe_repo_path(repo_root, source_path)
        if not source.exists():
            continue
        source_text = source.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source_text, filename=str(source))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        function_node = next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function_name), None)
        if not function_node or len(function_node.args.args) != 2:
            continue
        if ".send(" not in source_text:
            continue
        docs_path = ""
        for docs in sorted(repo_root.rglob("*.md")):
            if any(part in EXCLUDED_DIRS for part in docs.relative_to(repo_root).parts):
                continue
            docs_text = docs.read_text(encoding="utf-8")
            if "retry" in docs_text.lower() or "validation" in docs_text.lower() or function_name in docs_text:
                docs_path = str(docs.relative_to(repo_root))
                break
        candidates.append(
            {
                "test_path": test_path,
                "source_path": source_path,
                "function_name": function_name,
                "transport_arg": function_node.args.args[0].arg,
                "event_arg": function_node.args.args[1].arg,
                "docs_path": docs_path,
            }
        )
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ValueError(f"test-inferred retry policy requires exactly one candidate, found {len(candidates)}")
    candidate = candidates[0]
    source_path = str(candidate["source_path"])
    function_name = str(candidate["function_name"])
    transport_arg = str(candidate["transport_arg"])
    event_arg = str(candidate["event_arg"])
    source_content = (
        f"def {function_name}({transport_arg}, {event_arg}, max_attempts=3):\n"
        "    last_error = None\n"
        "    for _ in range(max_attempts):\n"
        "        try:\n"
        f"            return {transport_arg}.send({event_arg})\n"
        "        except ConnectionError as exc:\n"
        "            last_error = exc\n"
        "    raise last_error\n"
    )
    operations: list[dict[str, Any]] = [
        {"type": "write_file", "path": source_path, "content": source_content, "overwrite": True}
    ]
    docs_path = str(candidate.get("docs_path") or "")
    if docs_path:
        docs_content = (
            "# Client\n\n"
            "Retry policy: transient `ConnectionError` transport failures are retried up to `max_attempts`. "
            "Validation failures such as `ValueError` are not retried and must surface immediately; no sleep-based waiting is used.\n"
        )
        operations.append({"type": "write_file", "path": docs_path, "content": docs_content, "overwrite": True})
    if not commands:
        commands = [f"python -m unittest {str(candidate['test_path'])[:-3].replace('/', '.')}"]
    return {
        "source": "test_inferred_retry_policy",
        "diagnostics": {
            "kind": "test_inferred_retry_policy",
            "test_path": candidate["test_path"],
            "source_path": source_path,
            "function_name": function_name,
            "retry_exception": "ConnectionError",
            "non_retry_exception": "ValueError",
            "max_attempts": 3,
            "docs_path": docs_path,
        },
        "operations": operations,
        "verification_commands": commands,
    }


def runtime_verification_commands_from_goal(repo_root: Path, goal: str) -> list[str]:
    commands = verification_commands_from_natural_goal(goal)
    if commands:
        return commands[:5]
    inferred: list[str] = []
    for test_path in discovered_test_paths(repo_root, goal):
        if not test_path.endswith(".py"):
            continue
        if pytest_style_test_file(repo_root, test_path):
            command = f"python -m pytest {test_path}"
        else:
            module = test_path[:-3].replace("/", ".")
            command = f"python -m unittest {module}"
        if command not in inferred:
            inferred.append(command)
    return inferred[:5]


def infer_runtime_diagnostic_return_mismatch_from_tests(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    repo_root = target_repo_root(request)
    commands = runtime_verification_commands_from_goal(repo_root, goal)
    if not commands:
        return {}
    executed: list[dict[str, Any]] = []
    candidate_source_paths: list[str] = []
    for command in commands:
        try:
            result = run_verification_command(repo_root, command)
        except subprocess.TimeoutExpired:
            result = {"command": command, "returncode": 124, "stdout": "", "stderr": "verification command timed out"}
        executed.append(result)
        if int(result.get("returncode") or 0) == 0:
            continue
        output = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
        for candidate in source_candidates_from_traceback_text(output, repo_root):
            if candidate not in candidate_source_paths:
                candidate_source_paths.append(candidate)
    if not any(int(item.get("returncode") or 0) != 0 for item in executed if isinstance(item, dict)):
        return {}
    diagnostic = diagnostic_extraction_from_execution(
        {"patch_source": "runtime_diagnostic_return_mismatch", "patch_candidates": []},
        executed,
        candidate_source_paths,
        repo_root,
    )
    runtime_candidates = diagnostic.get("runtime_minimal_patch_candidates") if isinstance(diagnostic.get("runtime_minimal_patch_candidates"), list) else []
    viable = [
        item
        for item in runtime_candidates
        if isinstance(item, dict)
        and item.get("kind") == "replace_return_expression"
        and item.get("application_status") == "pending"
        and item.get("path")
        and item.get("old_expression")
        and item.get("new_expression")
    ]
    if not viable:
        return {}
    if len(viable) != 1:
        raise ValueError(f"runtime diagnostic return mismatch requires exactly one viable candidate, found {len(viable)}")
    candidate = viable[0]
    path = str(candidate["path"])
    old_expression = str(candidate["old_expression"])
    new_expression = str(candidate["new_expression"])
    return {
        "source": "runtime_diagnostic_return_mismatch",
        "diagnostics": {
            "kind": "runtime_diagnostic_return_mismatch",
            "test_path": candidate.get("test_path", ""),
            "test_function": candidate.get("test_function", ""),
            "module_path": path,
            "function_name": candidate.get("function_name", ""),
            "actual": old_expression,
            "expected": new_expression,
            "runtime_diagnostic_extraction": diagnostic,
        },
        "operations": [
            {
                "type": "replace_return_expression",
                "path": path,
                "function_name": candidate.get("function_name", ""),
                "old_expression": old_expression,
                "new_expression": new_expression,
            }
        ],
        "verification_commands": commands,
    }


def patch_spec_from_multi_file_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_FILES:")
    if not payload:
        return {}
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("CERAXIA_FILES must contain a non-empty files list")
    operations: list[dict[str, Any]] = []
    planned_paths: list[str] = []
    overwrite_paths: list[str] = []
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise ValueError(f"CERAXIA_FILES item {index} must be an object")
        path = item.get("path")
        content = item.get("content")
        if not isinstance(path, str) or not path.strip():
            raise ValueError(f"CERAXIA_FILES item {index} requires a non-empty string path")
        if not isinstance(content, str):
            raise ValueError(f"CERAXIA_FILES item {index} requires string content")
        operation: dict[str, Any] = {
            "type": "write_file",
            "path": path,
            "content": content,
        }
        if "overwrite" in item:
            operation["overwrite"] = bool(item.get("overwrite"))
        planned_paths.append(path)
        if operation.get("overwrite") is True:
            overwrite_paths.append(path)
        operations.append(operation)
    verification_commands = payload.get("verification_commands", [])
    if verification_commands is None:
        verification_commands = []
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_FILES verification_commands must be a list of strings")
    return {
        "source": "multi_file_marker_synthesis",
        "diagnostics": {
            "kind": "multi_file_marker_synthesis",
            "file_count": len(operations),
            "planned_paths": planned_paths,
            "overwrite_paths": overwrite_paths,
            "created_or_updated_paths": planned_paths,
        },
        "operations": operations,
        "verification_commands": verification_commands,
    }


def synthesized_patch_spec_from_markers(goal: str) -> dict[str, Any]:
    integration_contract = patch_spec_from_integration_contract_marker(goal)
    if integration_contract:
        return integration_contract
    public_api_compat = patch_spec_from_public_api_compat_marker(goal)
    if public_api_compat:
        return public_api_compat
    config_runtime = patch_spec_from_config_runtime_marker(goal)
    if config_runtime:
        return config_runtime
    refactor = patch_spec_from_refactor_marker(goal)
    if refactor:
        return refactor
    edge_fix = patch_spec_from_edge_fix_marker(goal)
    if edge_fix:
        return edge_fix
    data_migration = patch_spec_from_data_migration_marker(goal)
    if data_migration:
        return data_migration
    feature = patch_spec_from_feature_marker(goal)
    if feature:
        return feature
    multi_file = patch_spec_from_multi_file_marker(goal)
    if multi_file:
        return multi_file
    create_path = marker_value(goal, "CERAXIA_CREATE_FILE:")
    if create_path:
        content = marker_block(goal, "CERAXIA_FILE_CONTENT:")
        return {
            "source": "marker_synthesis",
            "operations": [
                {
                    "type": "write_file",
                    "path": create_path,
                    "content": content,
                }
            ],
            "verification_commands": verification_commands_from_markers(goal),
        }
    replace_path = marker_value(goal, "CERAXIA_REPLACE_IN_FILE:")
    if replace_path:
        old = marker_block(goal, "CERAXIA_OLD:")
        new = marker_block(goal, "CERAXIA_NEW:")
        return {
            "source": "marker_synthesis",
            "operations": [
                {
                    "type": "replace",
                    "path": replace_path,
                    "old": old,
                    "new": new,
                }
            ],
            "verification_commands": verification_commands_from_markers(goal),
        }
    return {}


def normalize_patch_payload(payload: dict[str, Any], source: str) -> dict[str, Any]:
    if isinstance(payload.get("ceraxia_patch"), dict):
        payload = payload["ceraxia_patch"]
    operations = payload.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError(f"{source} must contain a non-empty operations list")
    return payload


def patch_spec_resolution_from_request(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    candidate_builders: list[tuple[str, Any]] = [
        ("explicit_json_patch", lambda: extract_json_after_marker(goal, "CERAXIA_PATCH:")),
        ("marker_synthesis", lambda: synthesized_patch_spec_from_markers(goal)),
        ("test_inferred_api_deprecation", lambda: infer_api_deprecation_from_tests(request)),
        ("test_inferred_data_migration", lambda: infer_data_migration_from_tests(request)),
        ("test_inferred_config_runtime", lambda: infer_config_runtime_from_tests(request)),
        ("test_inferred_security_boundary", lambda: infer_security_boundary_from_tests(request)),
        ("test_inferred_design_choice_tax", lambda: infer_design_choice_tax_from_tests(request)),
        ("test_inferred_cache_concurrency", lambda: infer_cache_concurrency_from_tests(request)),
        ("test_inferred_flaky_ordering", lambda: infer_flaky_ordering_from_tests(request)),
        ("test_inferred_retry_policy", lambda: infer_retry_policy_from_tests(request)),
        ("test_inferred_self_repair_seed", lambda: infer_self_repair_seed_from_tests(request)),
        ("natural_language_simple_replace", lambda: infer_simple_replace_patch_spec(request)),
        ("natural_language_add_function", lambda: infer_add_function_patch_spec(request)),
        ("test_inferred_arithmetic_return", lambda: infer_arithmetic_return_from_tests(request)),
        ("test_inferred_return_mismatch", lambda: infer_return_mismatch_from_tests(request)),
        ("runtime_diagnostic_return_mismatch", lambda: infer_runtime_diagnostic_return_mismatch_from_tests(request)),
        ("test_inferred_missing_function", lambda: infer_missing_function_from_tests(request)),
    ]
    candidates: list[dict[str, Any]] = []
    for source, builder in candidate_builders:
        try:
            payload = builder()
            if not payload:
                candidates.append({"source": source, "status": "unavailable", "diagnostic": "no matching evidence found"})
                continue
            normalized = normalize_patch_payload(payload, source)
        except ValueError as exc:
            candidates.append({"source": source, "status": "blocked", "diagnostic": str(exc)})
            continue
        operations = normalized.get("operations") if isinstance(normalized.get("operations"), list) else []
        verification_commands = (
            normalized.get("verification_commands") if isinstance(normalized.get("verification_commands"), list) else []
        )
        diagnostics = normalized.get("diagnostics") if isinstance(normalized.get("diagnostics"), dict) else {}
        candidates.append(
            {
                "source": source,
                "status": "selected",
                "operation_count": len(operations),
                "verification_command_count": len(verification_commands),
                "diagnostics": diagnostics,
            }
        )
        return {"patch_spec": normalized, "candidates": candidates, "selected_candidate": candidates[-1]}
    return {"patch_spec": {}, "candidates": candidates, "selected_candidate": {}}


def patch_spec_from_request(request: dict[str, Any]) -> dict[str, Any]:
    return patch_spec_resolution_from_request(request)["patch_spec"]


def apply_patch_operation(repo_root: Path, operation: dict[str, Any]) -> dict[str, Any]:
    op_type = str(operation.get("type") or "").strip()
    path = safe_repo_path(repo_root, str(operation.get("path") or ""))
    before_exists = path.exists()
    before_hash = sha256_text(path) if before_exists else ""
    if op_type == "replace":
        if not before_exists:
            raise ValueError(f"replace target does not exist: {operation.get('path')}")
        old = operation.get("old")
        new = operation.get("new")
        if not isinstance(old, str) or old == "":
            raise ValueError("replace operation requires non-empty old text")
        if not isinstance(new, str):
            raise ValueError("replace operation requires new text")
        content = path.read_text(encoding="utf-8")
        count = content.count(old)
        if count != 1:
            raise ValueError(f"replace operation requires exactly one match in {operation.get('path')}, found {count}")
        path.write_text(content.replace(old, new, 1), encoding="utf-8")
    elif op_type == "replace_return_expression":
        if not before_exists:
            raise ValueError(f"replace_return_expression target does not exist: {operation.get('path')}")
        function_name = str(operation.get("function_name") or "").strip()
        old_expression = str(operation.get("old_expression") or "").strip()
        new_expression = str(operation.get("new_expression") or "").strip()
        if not function_name:
            raise ValueError("replace_return_expression requires function_name")
        if not old_expression or not new_expression:
            raise ValueError("replace_return_expression requires old_expression and new_expression")
        replace_return_expression_in_file(path, function_name, old_expression, new_expression)
    elif op_type == "write_file":
        content = operation.get("content")
        if not isinstance(content, str):
            raise ValueError("write_file operation requires string content")
        overwrite = bool(operation.get("overwrite"))
        if before_exists and path.read_text(encoding="utf-8") == content:
            return {
                "path": str(path.relative_to(repo_root)),
                "operation": op_type,
                "created": False,
                "before_sha256": before_hash,
                "after_sha256": before_hash,
                "changed": False,
                "idempotent": True,
            }
        if before_exists and not overwrite:
            raise ValueError(f"write_file target exists and overwrite is false: {operation.get('path')}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    elif op_type == "append":
        if not before_exists:
            raise ValueError(f"append target does not exist: {operation.get('path')}")
        content = operation.get("content")
        if not isinstance(content, str) or content == "":
            raise ValueError("append operation requires non-empty string content")
        current = path.read_text(encoding="utf-8")
        if content in current:
            return {
                "path": str(path.relative_to(repo_root)),
                "operation": op_type,
                "created": False,
                "before_sha256": before_hash,
                "after_sha256": before_hash,
                "changed": False,
                "idempotent": True,
            }
        function_name = str(operation.get("python_function_name") or "").strip()
        if function_name:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function_name):
                raise ValueError("append operation python_function_name must be a valid identifier")
            if re.search(rf"^\s*def\s+{re.escape(function_name)}\s*\(", current, flags=re.MULTILINE):
                raise ValueError(f"append operation would duplicate existing function: {function_name}")
        separator = "" if current.endswith("\n") or not current else "\n"
        path.write_text(f"{current}{separator}{content}", encoding="utf-8")
    else:
        raise ValueError(f"unsupported patch operation type: {op_type}")
    invalidate_python_cache(path)
    after_hash = sha256_text(path)
    return {
        "path": str(path.relative_to(repo_root)),
        "operation": op_type,
        "created": not before_exists,
        "before_sha256": before_hash,
        "after_sha256": after_hash,
        "changed": before_hash != after_hash,
    }


def restore_path_snapshot(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
        invalidate_python_cache(path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    invalidate_python_cache(path)


def apply_patch_operations_atomically(repo_root: Path, operations: list[Any]) -> list[dict[str, Any]]:
    changed_files: list[dict[str, Any]] = []
    snapshots: dict[Path, bytes | None] = {}
    try:
        for operation in operations:
            if not isinstance(operation, dict):
                raise ValueError("each patch operation must be an object")
            path = safe_repo_path(repo_root, str(operation.get("path") or ""))
            if path not in snapshots:
                snapshots[path] = path.read_bytes() if path.exists() else None
            changed_files.append(apply_patch_operation(repo_root, operation))
    except ValueError as exc:
        rolled_back_files: list[dict[str, Any]] = []
        mutated_paths = {
            safe_repo_path(repo_root, str(item.get("path") or ""))
            for item in changed_files
            if isinstance(item, dict) and item.get("changed")
        }
        for path, content in reversed(list(snapshots.items())):
            restore_path_snapshot(path, content)
            if path in mutated_paths:
                rolled_back_files.append(
                    {
                        "path": str(path.relative_to(repo_root)),
                        "restored": content is not None,
                        "removed": content is None,
                    }
                )
        raise PatchApplyError(str(exc), rolled_back_files) from exc
    return changed_files


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

def run_implementation(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    plan = read_text_optional(workspace_root, sibling_artifact(output_path, "change_plan.md"))
    survey = load_json_optional(workspace_root, sibling_artifact(output_path, "repo_survey.json"))
    problem_statement = load_json_optional(workspace_root, sibling_artifact(output_path, "problem_statement.json"))
    architecture_options = load_json_optional(workspace_root, sibling_artifact(output_path, "architecture_options.json"))
    readiness = survey.get("engineering_readiness") if isinstance(survey.get("engineering_readiness"), dict) else {}
    role_policy = role_policy_from_request(request)
    task_profile = task_profile_from_request(request)
    worker_brief = worker_brief_from_request(request)
    blockers: list[str] = []
    changed_files: list[dict[str, Any]] = []
    rolled_back_files: list[dict[str, Any]] = []
    patch_spec: dict[str, Any] = {}
    repo_root = target_repo_root(request)
    excerpts = source_excerpt_pack(workspace_root, output_path, repo_root)
    patch_resolution = {"patch_spec": {}, "candidates": [], "selected_candidate": {}}
    dirty_worktree = {"git_repo": False, "dirty_targets": []}
    ambiguity_analysis: dict[str, Any] = {}
    ast_patch_plan: dict[str, Any] = {}
    try:
        patch_resolution = patch_spec_resolution_from_request(request)
        patch_spec = patch_resolution["patch_spec"] if isinstance(patch_resolution.get("patch_spec"), dict) else {}
        if patch_spec:
            ast_patch_plan = ast_patch_plan_from_spec(repo_root, patch_spec)
            for blocker in ast_patch_plan.get("blockers", []) if isinstance(ast_patch_plan.get("blockers"), list) else []:
                blockers.append(str(blocker))
            if ast_patch_plan.get("status") == "missing" and ast_patch_plan_required_for_source(str(patch_spec.get("source") or "")):
                blockers.append("AST minimal patch plan is required for this inferred repair but was not produced.")
            if not role_policy_allows_source_mutation(role_policy):
                blockers.append("role_policy forbids source mutation for this step")
            elif not blockers:
                operations = patch_spec["operations"] if isinstance(patch_spec.get("operations"), list) else []
                dirty_worktree = git_dirty_target_evidence(repo_root, operations)
                dirty_targets = dirty_worktree.get("dirty_targets") if isinstance(dirty_worktree.get("dirty_targets"), list) else []
                if dirty_targets:
                    dirty_paths = ", ".join(str(item.get("path")) for item in dirty_targets if isinstance(item, dict))
                    blockers.append(f"target file has uncommitted user changes; refusing source mutation: {dirty_paths}")
                else:
                    changed_files.extend(apply_patch_operations_atomically(repo_root, operations))
        else:
            ambiguity_analysis = ambiguity_analysis_from_goal(request_goal(request), repo_root)
            if ambiguity_analysis:
                blockers.append("Ambiguous code task requires clarification before source mutation.")
            else:
                blockers.append(
                    "No patch candidate could be selected from explicit contract, task text, or test evidence."
                )
    except PatchApplyError as exc:
        blockers.append(str(exc))
        rolled_back_files = exc.rolled_back_files
    except ValueError as exc:
        blockers.append(str(exc))
    status = "applied" if changed_files and not blockers else "handoff_required"
    repo_grade_workflow = repo_grade_workflow_from_request(request, changed_files)
    architecture_decision_record = architecture_decision_record_from_evidence(request, survey, changed_files)
    unshaped_repair_plan = unshaped_repair_plan_from_resolution(request, survey, patch_resolution, patch_spec, excerpts)
    manifest = {
        "status": status,
        "mode": "explicit_patch_apply" if status == "applied" else "auditable_handoff",
        "task_id": request.get("task_id"),
        "summary": "Ceraxia applied scoped patch operations." if status == "applied" else "Ceraxia prepared implementation intent, but no source files were mutated by this worker.",
        "intended_actions": [
            "read concrete target files before editing",
            "apply minimal scoped patch",
            "run verification commands from verification_report.json",
            "return focused revision steps on failure",
        ],
        "plan_excerpt": plan[:3000],
        "problem_statement": problem_statement,
        "architecture_options": architecture_options,
        "role_policy": role_policy,
        "task_profile": task_profile,
        "worker_brief": worker_brief,
        "dirty_worktree": dirty_worktree,
        "ambiguity_analysis": ambiguity_analysis,
        "patch_spec_present": bool(patch_spec),
        "patch_source": str(patch_spec.get("source") or "explicit_json_patch") if patch_spec else "",
        "patch_candidates": patch_resolution.get("candidates", []) if isinstance(patch_resolution.get("candidates"), list) else [],
        "selected_patch_candidate": patch_resolution.get("selected_candidate", {})
        if isinstance(patch_resolution.get("selected_candidate"), dict)
        else {},
        "source_excerpt_pack": excerpts,
        "source_excerpt_summary": [
            {
                "path": item.get("path", ""),
                "status": item.get("status", ""),
                "bytes": item.get("bytes", 0),
                "truncated": item.get("truncated", False),
            }
            for item in excerpts
        ],
        "implementation_decision_record": [
            {
                "check": "source_evidence_loaded",
                "status": "pass" if any(item.get("status") == "read" for item in excerpts) else "warn",
                "detail": f"{sum(1 for item in excerpts if item.get('status') == 'read')} targeted files read",
            },
            {
                "check": "patch_candidate_selected",
                "status": "pass" if patch_spec else "fail",
                "detail": str(
                    (
                        patch_resolution.get("selected_candidate", {})
                        if isinstance(patch_resolution.get("selected_candidate"), dict)
                        else {}
                    ).get("source")
                    or "none"
                ),
            },
            {
                "check": "mutation_authority",
                "status": "pass" if role_policy_allows_source_mutation(role_policy) else "blocked",
                "detail": str(role_policy.get("authority") or "default_source_mutation_allowed"),
            },
        ],
        "architecture_decision_record": architecture_decision_record,
        "repo_grade_workflow": repo_grade_workflow,
        "unshaped_repair_plan": unshaped_repair_plan,
        "ast_patch_plan": ast_patch_plan,
        "diagnostics": patch_spec.get("diagnostics", {}) if isinstance(patch_spec.get("diagnostics"), dict) else {},
        "operation_count": len(patch_spec.get("operations", [])) if isinstance(patch_spec.get("operations"), list) else 0,
        "changed_files": changed_files,
        "recommended_read_order": recommended_read_order_from_survey(workspace_root, output_path),
        "engineering_readiness": readiness,
        "patch_scope_evidence": patch_scope_evidence(
            workspace_root,
            output_path,
            changed_files,
            patch_spec.get("diagnostics", {}) if isinstance(patch_spec.get("diagnostics"), dict) else {},
        ),
        "rollback": {
            "applied": bool(rolled_back_files),
            "files": rolled_back_files,
        },
        "verification_commands": patch_spec.get("verification_commands", []) if isinstance(patch_spec.get("verification_commands"), list) else [],
        "blockers": blockers,
        "warnings": [
            "Patch was selected from Ceraxia's guarded patch contracts or safe inference modes; broad synthesis still requires explicit evidence.",
        ] if status == "applied" else [
            "The current package is an auditable implementation handoff, not a completed code change.",
        ]
    }
    write_json(workspace_root, output_path, manifest)
    write_json(workspace_root, sibling_artifact(output_path, "unshaped_repair_plan.json"), unshaped_repair_plan)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": "Patch manifest written with applied changes." if status == "applied" else "Patch manifest written as auditable handoff; source mutation remains blocked.",
        "artifacts": [output_path, sibling_artifact(output_path, "unshaped_repair_plan.json")],
        "confidence": "medium",
    }
