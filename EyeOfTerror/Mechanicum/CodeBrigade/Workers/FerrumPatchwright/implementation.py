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

# Re-export the split submodules so the facade's `from implementation import *`
# and existing helper imports keep working, and the orchestration below can
# call the marker/inference builders by bare name.
from patch_markers import *  # noqa: F403,E402
from patch_inference import *  # noqa: F403,E402


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
