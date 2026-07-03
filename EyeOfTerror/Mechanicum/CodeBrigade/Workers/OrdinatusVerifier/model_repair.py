from __future__ import annotations

"""Model-backed verification repair helpers for OrdinatusVerifier."""

from common.codewright_core import *  # noqa: F403 - shared Codewright helper surface.


def patch_payload_from_model_repair_content(content: str) -> dict[str, Any]:
    text = content.strip()
    if not text:
        return {}
    if "CERAXIA_PATCH:" in text:
        text = text[text.find("CERAXIA_PATCH:") + len("CERAXIA_PATCH:"):].strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if "```" in lines:
            lines = lines[:lines.index("```")]
        text = "\n".join(lines).strip()
    try:
        payload = json.JSONDecoder().raw_decode(text)[0]
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, dict) and isinstance(payload.get("ceraxia_patch"), dict):
        payload = payload["ceraxia_patch"]
    return payload if isinstance(payload, dict) else {}


def normalize_model_repair_patch(payload: dict[str, Any]) -> dict[str, Any]:
    operations = payload.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("model repair patch must contain a non-empty operations list")
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise ValueError(f"model repair operation {index} must be an object")
        if not str(operation.get("path") or "").strip():
            raise ValueError(f"model repair operation {index} must include path")
    normalized = dict(payload)
    normalized["operations"] = operations
    return normalized


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
    lines[line_number - 1] = f"{match.group(1)}return {new_expression}{match.group(3) or ''}"
    source_path.write_text("".join(lines), encoding="utf-8")


def apply_model_repair_operation(repo_root: Path, operation: dict[str, Any]) -> dict[str, Any]:
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
        separator = "" if current.endswith("\n") or not current else "\n"
        path.write_text(f"{current}{separator}{content}", encoding="utf-8")
    else:
        raise ValueError(f"unsupported model repair operation type: {op_type}")
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


def restore_model_repair_snapshot(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
        invalidate_python_cache(path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    invalidate_python_cache(path)


def apply_model_repair_operations_atomically(repo_root: Path, operations: list[Any]) -> list[dict[str, Any]]:
    changed_files: list[dict[str, Any]] = []
    snapshots: dict[Path, bytes | None] = {}
    try:
        for operation in operations:
            if not isinstance(operation, dict):
                raise ValueError("each model repair operation must be an object")
            path = safe_repo_path(repo_root, str(operation.get("path") or ""))
            if path not in snapshots:
                snapshots[path] = path.read_bytes() if path.exists() else None
            changed_files.append(apply_model_repair_operation(repo_root, operation))
    except ValueError as exc:
        rolled_back_files: list[dict[str, Any]] = []
        mutated_paths = {
            safe_repo_path(repo_root, str(item.get("path") or ""))
            for item in changed_files
            if isinstance(item, dict) and item.get("changed")
        }
        for path, content in reversed(list(snapshots.items())):
            restore_model_repair_snapshot(path, content)
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


def model_repair_excerpt_paths(
    repo_root: Path,
    patch: dict[str, Any],
    executed: list[dict[str, Any]],
    candidate_source_paths: list[str],
) -> list[str]:
    paths: list[str] = []

    def add(raw_path: Any) -> None:
        path = str(raw_path or "").strip()
        if not path or path in paths:
            return
        try:
            if safe_repo_path(repo_root, path).is_file():
                paths.append(path)
        except ValueError:
            return

    changed_files = patch.get("changed_files") if isinstance(patch.get("changed_files"), list) else []
    for item in changed_files:
        if isinstance(item, dict):
            add(item.get("path"))
    for path in candidate_source_paths:
        add(path)
    for item in executed:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command") or "")
        try:
            parts = shlex.split(command)
        except ValueError:
            parts = []
        for part in parts:
            if part.endswith(".py"):
                add(part)
        output = f"{item.get('stdout', '')}\n{item.get('stderr', '')}"
        for path in traceback_source_paths_from_text(output, repo_root):
            add(path)
    return paths[:16]


def model_repair_source_excerpts(repo_root: Path, paths: list[str], max_chars: int = 6000) -> list[dict[str, Any]]:
    excerpts: list[dict[str, Any]] = []
    for raw_path in paths:
        try:
            path = safe_repo_path(repo_root, raw_path)
        except ValueError as exc:
            excerpts.append({"path": raw_path, "status": "blocked", "reason": str(exc)})
            continue
        if not path.is_file():
            excerpts.append({"path": raw_path, "status": "missing"})
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        excerpts.append(
            {
                "path": raw_path,
                "status": "read",
                "sha256": sha256_text(path),
                "excerpt": text[:max_chars],
                "truncated": len(text) > max_chars,
            }
        )
    return excerpts


def traceback_source_paths_from_text(text: str, repo_root: Path) -> list[str]:
    paths: list[str] = []
    for match in re.finditer(r'^\s*File "([^"]+)", line \d+, in [A-Za-z_][A-Za-z0-9_]*\s*$', text, flags=re.MULTILINE):
        raw_path = match.group(1)
        path = Path(raw_path)
        try:
            if path.is_absolute():
                rel_path = str(path.resolve().relative_to(repo_root.resolve()))
            else:
                rel_path = raw_path.replace("\\", "/")
            if rel_path.endswith(".py") and rel_path not in paths:
                paths.append(rel_path)
        except (OSError, ValueError):
            continue
    return paths[:20]


def run_full_verification_pass(repo_root: Path, py_files: list[str], raw_commands: list[Any], command_runner: Any) -> dict[str, Any]:
    executed: list[dict[str, Any]] = []
    blockers: list[str] = []
    if py_files:
        cmd = [sys.executable, "-m", "py_compile", *py_files]
        completed = subprocess.run(cmd, cwd=repo_root, text=True, capture_output=True, check=False)
        executed.append(
            {
                "command": " ".join(cmd),
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
                "after_model_repair": True,
            }
        )
        if completed.returncode != 0:
            blockers.append("py_compile failed for changed Python files")
    if (repo_root / ".git").exists():
        completed = subprocess.run(["git", "diff", "--check"], cwd=repo_root, text=True, capture_output=True, check=False)
        executed.append(
            {
                "command": "git diff --check",
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
                "after_model_repair": True,
            }
        )
        if completed.returncode != 0:
            blockers.append("git diff --check failed")
    for raw_command in raw_commands:
        if not isinstance(raw_command, str) or not raw_command.strip():
            blockers.append("verification command must be a non-empty string")
            continue
        try:
            result = command_runner(repo_root, raw_command)
        except subprocess.TimeoutExpired:
            result = {"command": raw_command, "returncode": 124, "stdout": "", "stderr": "verification command timed out"}
        result["after_model_repair"] = True
        executed.append(result)
        if result.get("returncode") != 0:
            blockers.append(f"verification command failed: {raw_command}")
    return {"executed": executed, "blockers": blockers}


def merge_changed_files(existing: list[Any], additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = [item for item in existing if isinstance(item, dict)]
    known_paths = {str(item.get("path") or "") for item in merged}
    for item in additions:
        path = str(item.get("path") or "")
        if not path:
            continue
        if path in known_paths:
            for existing_item in merged:
                if existing_item.get("path") == path:
                    existing_item.update({key: value for key, value in item.items() if key not in {"path"}})
                    existing_item["changed"] = bool(existing_item.get("changed") or item.get("changed"))
                    existing_item["repaired_by_verifier"] = True
                    break
            continue
        merged.append({**item, "repaired_by_verifier": True})
        known_paths.add(path)
    return merged


def run_model_repair_attempt(
    *,
    request: dict[str, Any],
    patch: dict[str, Any],
    executed: list[dict[str, Any]],
    blockers: list[str],
    candidate_source_paths: list[str],
    repo_root: Path,
    diagnostic_extraction: dict[str, Any],
) -> dict[str, Any]:
    failed_commands = [item for item in executed if isinstance(item, dict) and int(item.get("returncode") or 0) != 0]
    if not failed_commands:
        return {"applied": False, "kind": "model_repair", "reason": "no failed commands available"}
    diagnostics = diagnostic_extraction if isinstance(diagnostic_extraction, dict) else {}
    excerpt_paths = model_repair_excerpt_paths(repo_root, patch, executed, candidate_source_paths)
    context = dict(request)
    context["verification_repair"] = {
        "goal": request_goal(request),
        "blockers": blockers,
        "failed_commands": [
            {
                "command": item.get("command", ""),
                "returncode": item.get("returncode"),
                "stdout_excerpt": str(item.get("stdout") or "")[-3000:],
                "stderr_excerpt": str(item.get("stderr") or "")[-3000:],
            }
            for item in failed_commands[-6:]
        ],
        "diagnostic_extraction": diagnostics,
        "source_excerpts": model_repair_source_excerpts(repo_root, excerpt_paths),
        "required_shape": {
            "operations": [
                {"type": "replace", "path": "relative/file.py", "old": "exact old text", "new": "exact new text"}
            ],
            "diagnostics": {"repair_hypothesis": "why this fixes the failing verification without weakening tests"},
        },
    }
    decision = request_model_decision(
        "OrdinatusVerifier",
        "CodeBrigade verification repair worker",
        context,
        layer="code_worker_verification_repair",
        instructions=(
            "Generate exactly one concrete CERAXIA_PATCH JSON object that repairs the implementation so the supplied "
            "verification commands pass. Use only repo-relative paths. Prefer exact replace operations using text from "
            "source_excerpts. Do not edit tests unless the task explicitly requested a test-file change. Return only "
            "CERAXIA_PATCH: followed by JSON."
        ),
    )
    payload = patch_payload_from_model_repair_content(str(decision.get("content") or ""))
    if not payload:
        return {"applied": False, "kind": "model_repair", "decision": decision, "reason": "model did not return a parseable CERAXIA_PATCH"}
    try:
        normalized = normalize_model_repair_patch(payload)
        changed_files = apply_model_repair_operations_atomically(repo_root, normalized["operations"])
    except PatchApplyError as exc:
        return {
            "applied": False,
            "kind": "model_repair",
            "decision": decision,
            "reason": str(exc),
            "rolled_back_files": exc.rolled_back_files,
        }
    except ValueError as exc:
        return {"applied": False, "kind": "model_repair", "decision": decision, "reason": str(exc)}
    return {
        "applied": True,
        "kind": "model_repair",
        "changed_files": changed_files,
        "operation_count": len(normalized["operations"]),
        "diagnostics": normalized.get("diagnostics", {}) if isinstance(normalized.get("diagnostics"), dict) else {},
        "decision": {
            key: value
            for key, value in decision.items()
            if key not in {"content"}
        },
    }
