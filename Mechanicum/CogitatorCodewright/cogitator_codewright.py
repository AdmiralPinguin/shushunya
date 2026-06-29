from __future__ import annotations

import json
import ast
import hashlib
import re
import shlex
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

MECHANICUM_ROOT = Path(__file__).resolve().parents[1]
if str(MECHANICUM_ROOT) not in sys.path:
    sys.path.insert(0, str(MECHANICUM_ROOT))

from common.swe_guardrails import build_repo_map, python_module_name, source_candidates_from_traceback_text, test_like_path  # noqa: E402


EXCLUDED_DIRS = {
    ".git",
    ".gradle",
    ".venv",
    "__pycache__",
    "node_modules",
    "runtime",
    "tmp",
    "cache",
    ".cache",
    "live_runs",
    "models",
    "outputs",
    "build",
    "dist",
}

MAX_SYMBOL_SCAN_BYTES = 120_000


WORKER_NAME = "CogitatorCodewright"


class PatchApplyError(ValueError):
    def __init__(self, message: str, rolled_back_files: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.rolled_back_files = rolled_back_files


def worker_name() -> str:
    return WORKER_NAME


def sandbox_path(workspace_root: Path, path: str) -> Path:
    if not path.startswith("/work/"):
        raise ValueError(f"unsupported sandbox path: {path}")
    return workspace_root / path.removeprefix("/work/")


def sibling_artifact(output_path: str, filename: str) -> str:
    if not output_path.startswith("/work/"):
        raise ValueError(f"unsupported output path: {output_path}")
    return f"{output_path.rsplit('/', 1)[0]}/{filename}"


def load_json_optional(workspace_root: Path, path: str) -> dict[str, Any]:
    host_path = sandbox_path(workspace_root, path)
    if not host_path.exists():
        return {}
    payload = json.loads(host_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def read_text_optional(workspace_root: Path, path: str) -> str:
    host_path = sandbox_path(workspace_root, path)
    if not host_path.exists():
        return ""
    return host_path.read_text(encoding="utf-8")


def write_json(workspace_root: Path, path: str, payload: dict[str, Any]) -> None:
    host_path = sandbox_path(workspace_root, path)
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(workspace_root: Path, path: str, content: str) -> None:
    host_path = sandbox_path(workspace_root, path)
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text(content, encoding="utf-8")


def request_goal(request: dict[str, Any]) -> str:
    contract = request.get("contract") if isinstance(request.get("contract"), dict) else {}
    return str(request.get("goal") or request.get("task") or contract.get("goal") or "")


def role_policy_from_request(request: dict[str, Any]) -> dict[str, Any]:
    expectations = request.get("quality_expectations") if isinstance(request.get("quality_expectations"), dict) else {}
    step_quality = expectations.get("step_quality") if isinstance(expectations.get("step_quality"), dict) else {}
    role_policy = step_quality.get("role_policy") if isinstance(step_quality.get("role_policy"), dict) else {}
    return role_policy


def role_policy_allows_source_mutation(role_policy: dict[str, Any]) -> bool:
    return not role_policy or role_policy.get("may_mutate_source") is not False


def ranked_source_candidates_from_survey(workspace_root: Path, output_path: str) -> list[str]:
    survey = load_json_optional(workspace_root, sibling_artifact(output_path, "repo_survey.json"))
    repo_map = survey.get("repo_map") if isinstance(survey.get("repo_map"), dict) else {}
    ranked_files = repo_map.get("ranked_files") if isinstance(repo_map.get("ranked_files"), list) else []
    candidates: list[str] = []
    for item in ranked_files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if path.endswith(".py") and not test_like_path(path) and path not in candidates:
            candidates.append(path)
    return candidates[:20]


def output_path_from_request(request: dict[str, Any]) -> str:
    step = request.get("step") if isinstance(request.get("step"), dict) else {}
    expected = step.get("expected_artifacts") if isinstance(step.get("expected_artifacts"), list) else []
    if not expected or not isinstance(expected[0], str):
        raise ValueError("step.expected_artifacts must contain an output path")
    return expected[0]


def safe_repo_path(repo_root: Path, raw_path: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("patch path must be a non-empty string")
    candidate = Path(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"patch path must be relative and stay inside target repo: {raw_path}")
    root = repo_root.resolve()
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"patch path escapes target repo: {raw_path}")
    if any(part in EXCLUDED_DIRS for part in resolved.relative_to(root).parts):
        raise ValueError(f"patch path points into an excluded directory: {raw_path}")
    return resolved


def sha256_text(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invalidate_python_cache(path: Path) -> None:
    if path.suffix != ".py":
        return
    cache_dir = path.parent / "__pycache__"
    if not cache_dir.exists():
        return
    for cached in cache_dir.glob(f"{path.stem}.*.pyc"):
        cached.unlink(missing_ok=True)


def target_repo_root(request: dict[str, Any]) -> Path:
    raw = str(request.get("target_repo_root") or request.get("code_workspace_root") or "").strip()
    if not raw:
        goal = request_goal(request)
        marker = "CERAXIA_TARGET_REPO:"
        marker_at = goal.find(marker)
        if marker_at >= 0:
            raw = goal[marker_at + len(marker):].strip().splitlines()[0].strip()
    if not raw:
        return Path.cwd().resolve()
    return Path(raw).resolve()


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


def safe_return_literal(raw: str) -> str:
    value = raw.strip()
    if re.fullmatch(r"[+-]?\d+", value) or value in {"True", "False", "None"}:
        return value
    if re.fullmatch(r"'[^'\\]*(?:\\.[^'\\]*)*'", value) or re.fullmatch(r'"[^"\\]*(?:\\.[^"\\]*)*"', value):
        return value
    raise ValueError(f"unsupported inferred return literal: {raw}")


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


def test_paths_from_goal(goal: str) -> list[str]:
    paths: list[str] = []
    for match in re.finditer(r"`([^`]+\.py)`", goal):
        path = match.group(1).strip()
        lowered = path.lower()
        if "test" in lowered and path not in paths:
            paths.append(path)
    return paths


def test_expectation_candidates(repo_root: Path, goal: str) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for test_path in test_paths_from_goal(goal):
        path = safe_repo_path(repo_root, test_path)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        imports = re.findall(r"^\s*from\s+([A-Za-z_][A-Za-z0-9_\.]*)\s+import\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", text, flags=re.MULTILINE)
        for module_name, function_name in imports:
            expected_values = re.findall(
                rf"assertEqual\(\s*{re.escape(function_name)}\(\)\s*,\s*([+-]?\d+|True|False|None|'[^'\\]*(?:\\.[^'\\]*)*'|\"[^\"\\]*(?:\\.[^\"\\]*)*\")\s*\)",
                text,
            )
            if len(expected_values) != 1:
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
                    "literal": safe_return_literal(expected_values[0]),
                }
            )
    return candidates


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


def patch_spec_from_multi_file_marker(goal: str) -> dict[str, Any]:
    payload = extract_json_after_marker(goal, "CERAXIA_FILES:")
    if not payload:
        return {}
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("CERAXIA_FILES must contain a non-empty files list")
    operations: list[dict[str, Any]] = []
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
        operations.append(operation)
    verification_commands = payload.get("verification_commands", [])
    if verification_commands is None:
        verification_commands = []
    if not isinstance(verification_commands, list) or not all(isinstance(item, str) for item in verification_commands):
        raise ValueError("CERAXIA_FILES verification_commands must be a list of strings")
    return {
        "source": "multi_file_marker_synthesis",
        "operations": operations,
        "verification_commands": verification_commands,
    }


def synthesized_patch_spec_from_markers(goal: str) -> dict[str, Any]:
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


def patch_spec_from_request(request: dict[str, Any]) -> dict[str, Any]:
    goal = request_goal(request)
    payload = extract_json_after_marker(goal, "CERAXIA_PATCH:")
    if not payload:
        payload = synthesized_patch_spec_from_markers(goal)
    if not payload:
        payload = infer_simple_replace_patch_spec(request)
    if not payload:
        payload = infer_add_function_patch_spec(request)
    if not payload:
        payload = infer_return_mismatch_from_tests(request)
    if not payload:
        payload = infer_missing_function_from_tests(request)
    if not payload:
        return {}
    if isinstance(payload.get("ceraxia_patch"), dict):
        payload = payload["ceraxia_patch"]
    operations = payload.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("CERAXIA_PATCH must contain a non-empty operations list")
    return payload


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


def command_allowed(command: list[str]) -> bool:
    if not command:
        return False
    if command[0] == "pytest":
        return True
    if command[0] in {"python", "python3", sys.executable} and len(command) >= 3 and command[1] == "-m":
        return command[2] in {"py_compile", "pytest", "unittest"}
    return False


def run_verification_command(repo_root: Path, raw_command: str) -> dict[str, Any]:
    try:
        command = shlex.split(raw_command)
    except ValueError as exc:
        return {"command": raw_command, "returncode": 2, "stdout": "", "stderr": f"invalid command syntax: {exc}"}
    if not command_allowed(command):
        return {
            "command": raw_command,
            "returncode": 126,
            "stdout": "",
            "stderr": "verification command is outside Ceraxia's allowlist",
        }
    if command[0] in {"python", "python3"}:
        command[0] = sys.executable
    completed = subprocess.run(command, cwd=repo_root, text=True, capture_output=True, timeout=120, check=False)
    return {
        "command": raw_command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }


def repair_expected_colon(repo_root: Path, py_file: str, stderr: str) -> dict[str, Any]:
    if "SyntaxError: expected ':'" not in stderr:
        return {"applied": False, "reason": "not an expected-colon SyntaxError"}
    match = re.search(r'File "([^"]+)", line (\d+)', stderr)
    if not match:
        return {"applied": False, "reason": "could not locate failing file and line"}
    failing_path = Path(match.group(1))
    if failing_path.name != Path(py_file).name:
        return {"applied": False, "reason": "failing file does not match changed file"}
    line_number = int(match.group(2))
    path = safe_repo_path(repo_root, py_file)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if line_number < 1 or line_number > len(lines):
        return {"applied": False, "reason": "failing line is out of range"}
    original = lines[line_number - 1]
    line_without_newline = original.rstrip("\n")
    if line_without_newline.rstrip().endswith(":"):
        return {"applied": False, "reason": "failing line already ends with colon"}
    newline = "\n" if original.endswith("\n") else ""
    lines[line_number - 1] = f"{line_without_newline.rstrip()}:{newline}"
    before_hash = sha256_text(path)
    path.write_text("".join(lines), encoding="utf-8")
    invalidate_python_cache(path)
    return {
        "applied": True,
        "kind": "expected_colon",
        "path": py_file,
        "line": line_number,
        "before_sha256": before_hash,
        "after_sha256": sha256_text(path),
    }


def repair_assertion_return_mismatch(repo_root: Path, py_files: list[str], output: str) -> dict[str, Any]:
    match = re.search(r"AssertionError: ([+-]?\d+) != ([+-]?\d+)", output)
    if not match:
        return {"applied": False, "reason": "no simple integer AssertionError mismatch found"}
    actual, expected = match.groups()
    needle = f"return {actual}"
    replacement = f"return {expected}"
    candidates: list[tuple[Path, str]] = []
    for py_file in py_files:
        path = safe_repo_path(repo_root, py_file)
        content = path.read_text(encoding="utf-8")
        if content.count(needle) == 1:
            candidates.append((path, content))
    if len(candidates) != 1:
        return {"applied": False, "reason": f"expected one changed file with {needle!r}, found {len(candidates)}"}
    path, content = candidates[0]
    before_hash = sha256_text(path)
    path.write_text(content.replace(needle, replacement, 1), encoding="utf-8")
    invalidate_python_cache(path)
    return {
        "applied": True,
        "kind": "assertion_return_mismatch",
        "path": str(path.relative_to(repo_root)),
        "actual": actual,
        "expected": expected,
        "before_sha256": before_hash,
        "after_sha256": sha256_text(path),
    }


def repair_name_error_return_literal(repo_root: Path, py_files: list[str], output: str) -> dict[str, Any]:
    match = re.search(r"NameError: name '([A-Za-z_][A-Za-z0-9_]*)' is not defined", output)
    if not match:
        return {"applied": False, "reason": "no simple NameError found"}
    name = match.group(1)
    expected_match = re.search(r"assertEqual\([^,\n]+,\s*([+-]?\d+|True|False|None)\)", output)
    if not expected_match:
        return {"applied": False, "reason": "could not infer a literal expected value from assertEqual"}
    expected = expected_match.group(1)
    needle = f"return {name}"
    candidates: list[tuple[Path, str]] = []
    for py_file in py_files:
        path = safe_repo_path(repo_root, py_file)
        content = path.read_text(encoding="utf-8")
        if content.count(needle) == 1:
            candidates.append((path, content))
    if len(candidates) != 1:
        return {"applied": False, "reason": f"expected one changed file with {needle!r}, found {len(candidates)}"}
    path, content = candidates[0]
    before_hash = sha256_text(path)
    path.write_text(content.replace(needle, f"return {expected}", 1), encoding="utf-8")
    invalidate_python_cache(path)
    return {
        "applied": True,
        "kind": "name_error_return_literal",
        "path": str(path.relative_to(repo_root)),
        "name": name,
        "expected": expected,
        "before_sha256": before_hash,
        "after_sha256": sha256_text(path),
    }


def repair_import_error_missing_function(repo_root: Path, py_files: list[str], output: str) -> dict[str, Any]:
    import_match = re.search(
        r"ImportError: cannot import name '([A-Za-z_][A-Za-z0-9_]*)' from '([A-Za-z_][A-Za-z0-9_\.]*)'",
        output,
    )
    if not import_match:
        return {"applied": False, "reason": "no simple import-name ImportError found"}
    function_name, module_name = import_match.groups()
    expected_values = re.findall(
        rf"assertEqual\(\s*{re.escape(function_name)}\(\)\s*,\s*([+-]?\d+|True|False|None)\s*\)",
        output,
    )
    if not expected_values:
        for test_file in sorted(repo_root.glob("test*.py")) + sorted(repo_root.glob("*_test.py")):
            text = test_file.read_text(encoding="utf-8")
            expected_values.extend(
                re.findall(
                    rf"assertEqual\(\s*{re.escape(function_name)}\(\)\s*,\s*([+-]?\d+|True|False|None)\s*\)",
                    text,
                )
            )
    if len(expected_values) != 1:
        return {"applied": False, "reason": f"could not infer exactly one expected literal for missing function, found {len(expected_values)}"}
    expected = expected_values[0]
    module_path = f"{module_name.replace('.', '/')}.py"
    if module_path not in py_files:
        return {"applied": False, "reason": f"missing function module is not a changed file: {module_path}"}
    path = safe_repo_path(repo_root, module_path)
    content = path.read_text(encoding="utf-8")
    if re.search(rf"^\s*def\s+{re.escape(function_name)}\s*\(", content, flags=re.MULTILINE):
        return {"applied": False, "reason": f"function already exists: {function_name}"}
    before_hash = sha256_text(path)
    prefix = "" if not content or content.endswith("\n") else "\n"
    suffix = "\n" if content else ""
    addition = f"{prefix}{suffix}def {function_name}():\n    return {expected}\n"
    path.write_text(content + addition, encoding="utf-8")
    invalidate_python_cache(path)
    return {
        "applied": True,
        "kind": "import_error_missing_function",
        "path": module_path,
        "function": function_name,
        "expected": expected,
        "before_sha256": before_hash,
        "after_sha256": sha256_text(path),
    }


def python_file_summary(repo_root: Path, path: Path) -> dict[str, Any]:
    rel = str(path.relative_to(repo_root))
    try:
        if path.stat().st_size > MAX_SYMBOL_SCAN_BYTES:
            return {"path": rel, "skipped": "file_too_large_for_symbol_scan"}
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return {"path": rel, "skipped": f"python_parse_failed: {exc.__class__.__name__}"}
    functions: list[str] = []
    classes: list[str] = []
    imports: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.extend(f"{module}.{alias.name}" if module else alias.name for alias in node.names)
    return {
        "path": rel,
        "module": python_module_name(rel),
        "functions": functions[:40],
        "classes": classes[:40],
        "imports": imports[:40],
    }


def suggested_verification_commands(test_files: list[str]) -> list[str]:
    commands: list[str] = []
    py_tests = [item for item in test_files if item.endswith(".py")]
    if py_tests:
        commands.append("python -m unittest discover")
        commands.extend(f"python -m unittest {item[:-3].replace('/', '.')}" for item in py_tests[:5])
    return commands[:8]


def repo_survey(repo_root: Path, goal: str) -> dict[str, Any]:
    extension_counts: Counter[str] = Counter()
    candidate_files: list[str] = []
    test_files: list[str] = []
    config_files: list[str] = []
    python_symbols: list[dict[str, Any]] = []
    total_files = 0
    for path in sorted(repo_root.rglob("*")):
        if any(part in EXCLUDED_DIRS for part in path.relative_to(repo_root).parts):
            continue
        if not path.is_file():
            continue
        rel = str(path.relative_to(repo_root))
        if rel.endswith((".pyc", ".sqlite3", ".gguf", ".safetensors", ".bin", ".apk")):
            continue
        total_files += 1
        suffix = path.suffix.lower() or "[no_ext]"
        extension_counts[suffix] += 1
        lowered = rel.lower()
        if any(marker in lowered for marker in ("test", "self_test", "spec")):
            test_files.append(rel)
        if path.suffix == ".py" and len(python_symbols) < 80:
            python_symbols.append(python_file_summary(repo_root, path))
        if path.name in {"pyproject.toml", "package.json", "build.gradle", "settings.gradle", "gradlew", "requirements.txt"}:
            config_files.append(rel)
        goal_tokens = {token for token in goal.lower().replace("/", " ").replace("_", " ").split() if len(token) > 3}
        rel_tokens = set(lowered.replace("/", " ").replace("_", " ").replace("-", " ").split())
        if goal_tokens & rel_tokens:
            candidate_files.append(rel)
    dominant_extensions = [{"extension": ext, "count": count} for ext, count in extension_counts.most_common(12)]
    return {
        "repo_root": str(repo_root),
        "goal": goal,
        "total_files_scanned": total_files,
        "dominant_extensions": dominant_extensions,
        "candidate_files": candidate_files[:80],
        "test_files": test_files[:80],
        "python_symbols": python_symbols,
        "suggested_verification_commands": suggested_verification_commands(test_files),
        "repo_map": build_repo_map(goal, candidate_files[:80], test_files[:80], python_symbols),
        "config_files": config_files[:40],
        "excluded_dirs": sorted(EXCLUDED_DIRS),
        "summary": f"Surveyed {total_files} files; found {len(test_files)} test-like files and {len(candidate_files)} goal-matching candidates.",
    }


def run_repository_survey(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    goal = request_goal(request)
    survey = repo_survey(target_repo_root(request), goal)
    survey["role_policy"] = role_policy_from_request(request)
    write_json(workspace_root, output_path, survey)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": survey["summary"],
        "artifacts": [output_path],
        "confidence": "medium",
    }


def run_change_planning(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    survey = load_json_optional(workspace_root, sibling_artifact(output_path, "repo_survey.json"))
    goal = request_goal(request) or str(survey.get("goal") or "")
    role_policy = role_policy_from_request(request)
    candidates = survey.get("candidate_files") if isinstance(survey.get("candidate_files"), list) else []
    tests = survey.get("test_files") if isinstance(survey.get("test_files"), list) else []
    symbols = survey.get("python_symbols") if isinstance(survey.get("python_symbols"), list) else []
    suggested_commands = survey.get("suggested_verification_commands") if isinstance(survey.get("suggested_verification_commands"), list) else []
    repo_map = survey.get("repo_map") if isinstance(survey.get("repo_map"), dict) else {}
    ranked_files = repo_map.get("ranked_files") if isinstance(repo_map.get("ranked_files"), list) else []
    test_source_links = repo_map.get("test_source_links") if isinstance(repo_map.get("test_source_links"), list) else []
    read_order = repo_map.get("recommended_read_order") if isinstance(repo_map.get("recommended_read_order"), list) else []
    symbol_lines: list[str] = []
    for item in symbols[:20]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        functions = ", ".join(str(name) for name in item.get("functions", [])[:8]) if isinstance(item.get("functions"), list) else ""
        classes = ", ".join(str(name) for name in item.get("classes", [])[:8]) if isinstance(item.get("classes"), list) else ""
        skipped = str(item.get("skipped") or "")
        detail = skipped or f"functions=[{functions}] classes=[{classes}]"
        symbol_lines.append(f"- {path}: {detail}")
    ranked_lines: list[str] = []
    for item in ranked_files[:20]:
        if not isinstance(item, dict):
            continue
        reasons = ", ".join(str(reason) for reason in item.get("reasons", [])[:4]) if isinstance(item.get("reasons"), list) else ""
        ranked_lines.append(f"- {item.get('path')}: score={item.get('score')} reasons=[{reasons}]")
    link_lines: list[str] = []
    for item in test_source_links[:20]:
        if not isinstance(item, dict):
            continue
        sources = ", ".join(str(path) for path in item.get("source_paths", [])[:8]) if isinstance(item.get("source_paths"), list) else ""
        link_lines.append(f"- {item.get('test_path')} -> {sources}")
    read_order_lines: list[str] = []
    for item in read_order[:20]:
        if not isinstance(item, dict):
            continue
        read_order_lines.append(f"- {item.get('phase')}: {item.get('path')} ({item.get('reason')})")
    content = "\n".join(
        [
            "# Ceraxia Change Plan",
            "",
            f"Goal: {goal}",
            "",
            "## Scope",
            "- Inspect the named task and constrain edits to the smallest coherent module set.",
            "- Preserve user changes and expose blockers instead of guessing.",
            "",
            "## Candidate Files",
            *[f"- {item}" for item in candidates[:30]],
            "",
            "## Ranked Repo Map",
            *ranked_lines,
            "",
            "## Test Source Links",
            *link_lines,
            "",
            "## Recommended Read Order",
            *read_order_lines,
            "",
            "## Test Surface",
            *[f"- {item}" for item in tests[:30]],
            "",
            "## Python Symbol Surface",
            *symbol_lines,
            "",
            "## Suggested Verification",
            *[f"- {item}" for item in suggested_commands[:8]],
            "",
            "## Implementation Policy",
            "- Produce an auditable patch manifest before mutating source files.",
            "- Require verification commands or explicit blockers before final readiness.",
            "",
            "## Role Policy",
            f"- role: {role_policy.get('role', '')}",
            f"- authority: {role_policy.get('authority', '')}",
            f"- may_mutate_source: {role_policy.get('may_mutate_source', False)}",
            *[
                f"- required_evidence: {item}"
                for item in (role_policy.get("required_evidence") if isinstance(role_policy.get("required_evidence"), list) else [])
            ],
        ]
    )
    write_text(workspace_root, output_path, content + "\n")
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": "Code change plan written.",
        "artifacts": [output_path],
        "confidence": "medium",
    }


def run_implementation(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    plan = read_text_optional(workspace_root, sibling_artifact(output_path, "change_plan.md"))
    role_policy = role_policy_from_request(request)
    blockers: list[str] = []
    changed_files: list[dict[str, Any]] = []
    rolled_back_files: list[dict[str, Any]] = []
    patch_spec: dict[str, Any] = {}
    try:
        patch_spec = patch_spec_from_request(request)
        if patch_spec:
            if not role_policy_allows_source_mutation(role_policy):
                blockers.append("role_policy forbids source mutation for this step")
            else:
                repo_root = target_repo_root(request)
                changed_files.extend(apply_patch_operations_atomically(repo_root, patch_spec["operations"]))
        else:
            blockers.append(
                "No CERAXIA_PATCH operations were provided; direct source mutation requires an explicit patch specification."
            )
    except PatchApplyError as exc:
        blockers.append(str(exc))
        rolled_back_files = exc.rolled_back_files
    except ValueError as exc:
        blockers.append(str(exc))
    status = "applied" if changed_files and not blockers else "handoff_required"
    manifest = {
        "status": status,
        "mode": "explicit_patch_apply" if status == "applied" else "auditable_handoff",
        "task_id": request.get("task_id"),
        "summary": "Ceraxia applied explicit patch operations." if status == "applied" else "Ceraxia prepared implementation intent, but no source files were mutated by this worker.",
        "intended_actions": [
            "read concrete target files before editing",
            "apply minimal scoped patch",
            "run verification commands from verification_report.json",
            "return focused revision steps on failure",
        ],
        "plan_excerpt": plan[:3000],
        "role_policy": role_policy,
        "patch_spec_present": bool(patch_spec),
        "patch_source": str(patch_spec.get("source") or "explicit_json_patch") if patch_spec else "",
        "diagnostics": patch_spec.get("diagnostics", {}) if isinstance(patch_spec.get("diagnostics"), dict) else {},
        "operation_count": len(patch_spec.get("operations", [])) if isinstance(patch_spec.get("operations"), list) else 0,
        "changed_files": changed_files,
        "rollback": {
            "applied": bool(rolled_back_files),
            "files": rolled_back_files,
        },
        "verification_commands": patch_spec.get("verification_commands", []) if isinstance(patch_spec.get("verification_commands"), list) else [],
        "blockers": blockers,
        "warnings": [
            "Only explicit CERAXIA_PATCH operations are supported by this prototype patch worker.",
        ] if status == "applied" else [
            "The current package is an auditable implementation handoff, not a completed code change.",
        ]
    }
    write_json(workspace_root, output_path, manifest)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": "Patch manifest written with applied changes." if status == "applied" else "Patch manifest written as auditable handoff; source mutation remains blocked.",
        "artifacts": [output_path],
        "confidence": "medium",
    }


def run_verification(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    patch = load_json_optional(workspace_root, sibling_artifact(output_path, "patch_manifest.json"))
    role_policy = role_policy_from_request(request)
    blockers = [str(item) for item in patch.get("blockers", [])] if isinstance(patch.get("blockers"), list) else []
    executed: list[dict[str, Any]] = []
    repo_root = target_repo_root(request)
    changed_files = patch.get("changed_files") if isinstance(patch.get("changed_files"), list) else []
    repairs: list[dict[str, Any]] = []
    blocked_repairs: list[dict[str, Any]] = []
    candidate_source_paths: list[str] = []
    ranked_survey_sources = ranked_source_candidates_from_survey(workspace_root, output_path)
    repairs_allowed = role_policy_allows_source_mutation(role_policy)
    if patch.get("status") == "applied":
        py_files = [
            str(item.get("path"))
            for item in changed_files
            if isinstance(item, dict) and str(item.get("path") or "").endswith(".py")
        ]
        if py_files:
            cmd = [sys.executable, "-m", "py_compile", *py_files]
            completed = subprocess.run(cmd, cwd=repo_root, text=True, capture_output=True, check=False)
            executed.append(
                {
                    "command": " ".join(cmd),
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                }
            )
            if completed.returncode != 0:
                for candidate in source_candidates_from_traceback_text(completed.stderr, repo_root):
                    if candidate not in candidate_source_paths:
                        candidate_source_paths.append(candidate)
                for candidate in ranked_survey_sources:
                    if candidate not in candidate_source_paths:
                        candidate_source_paths.append(candidate)
                repaired_any = False
                for py_file in py_files:
                    if not repairs_allowed:
                        blockers.append("role_policy forbids source mutation repair")
                        blocked_repairs.append({"kind": "py_compile_repair", "path": py_file, "reason": "role_policy forbids source mutation repair"})
                        break
                    repair = repair_expected_colon(repo_root, py_file, completed.stderr)
                    if repair.get("applied"):
                        repairs.append(repair)
                        repaired_any = True
                if repaired_any:
                    completed = subprocess.run(cmd, cwd=repo_root, text=True, capture_output=True, check=False)
                    executed.append(
                        {
                            "command": " ".join(cmd),
                            "returncode": completed.returncode,
                            "stdout": completed.stdout[-4000:],
                            "stderr": completed.stderr[-4000:],
                            "after_repair": True,
                        }
                    )
                if completed.returncode != 0:
                    blockers.append("py_compile failed for changed Python files")
        if (repo_root / ".git").exists():
            cmd = ["git", "diff", "--check"]
            completed = subprocess.run(cmd, cwd=repo_root, text=True, capture_output=True, check=False)
            executed.append(
                {
                    "command": "git diff --check",
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                }
            )
            if completed.returncode != 0:
                blockers.append("git diff --check failed")
        raw_commands = patch.get("verification_commands") if isinstance(patch.get("verification_commands"), list) else []
        for raw_command in raw_commands:
            if not isinstance(raw_command, str) or not raw_command.strip():
                blockers.append("verification command must be a non-empty string")
                continue
            try:
                result = run_verification_command(repo_root, raw_command)
            except subprocess.TimeoutExpired:
                result = {"command": raw_command, "returncode": 124, "stdout": "", "stderr": "verification command timed out"}
            executed.append(result)
            if result.get("returncode") != 0:
                output = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
                for candidate in source_candidates_from_traceback_text(output, repo_root):
                    if candidate not in candidate_source_paths:
                        candidate_source_paths.append(candidate)
                for candidate in ranked_survey_sources:
                    if candidate not in candidate_source_paths:
                        candidate_source_paths.append(candidate)
                if not repairs_allowed:
                    repair = {"applied": False, "blocked": "role_policy forbids source mutation repair"}
                    blockers.append("role_policy forbids source mutation repair")
                    blocked_repairs.append({"kind": "command_repair", "command": raw_command, "reason": "role_policy forbids source mutation repair"})
                else:
                    repair = repair_import_error_missing_function(repo_root, py_files, output)
                if not repair.get("applied") and repairs_allowed:
                    repair = repair_name_error_return_literal(repo_root, py_files, output)
                if not repair.get("applied") and repairs_allowed:
                    repair = repair_assertion_return_mismatch(repo_root, py_files, output)
                if repair.get("applied"):
                    repairs.append(repair)
                    try:
                        result = run_verification_command(repo_root, raw_command)
                    except subprocess.TimeoutExpired:
                        result = {"command": raw_command, "returncode": 124, "stdout": "", "stderr": "verification command timed out"}
                    result["after_repair"] = True
                    executed.append(result)
                if result.get("returncode") != 0:
                    blockers.append(f"verification command failed: {raw_command}")
    report = {
        "status": "blocked" if blockers else "passed",
        "task_id": request.get("task_id"),
        "role_policy": role_policy,
        "commands": [
            "python -m py_compile <changed .py files>",
            "git diff --check",
        ],
        "executed": executed,
        "repairs": repairs,
        "blockers": blockers,
        "warnings": patch.get("warnings", []),
        "summary": "Verification passed for applied changes." if not blockers else "Verification is blocked or failed.",
    }
    failed_commands = [
        item
        for item in executed
        if isinstance(item, dict) and int(item.get("returncode") or 0) != 0
    ]
    repair_state = {
        "status": "blocked" if blockers else "passed",
        "task_id": request.get("task_id"),
        "role_policy": role_policy,
        "repairs_allowed": repairs_allowed,
        "repair_attempts": repairs,
        "blocked_repairs": blocked_repairs,
        "commands_executed_count": len(executed),
        "failed_commands": failed_commands,
        "candidate_source_paths": candidate_source_paths[:20],
        "pending_blockers": blockers,
        "next_action": "inspect_blockers_or_revision_plan" if blockers else "continue_to_code_review",
        "summary": "Repair loop state recorded for verification step.",
    }
    write_json(workspace_root, output_path, report)
    write_json(workspace_root, sibling_artifact(output_path, "repair_loop_state.json"), repair_state)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": "Verification report written.",
        "artifacts": [output_path, sibling_artifact(output_path, "repair_loop_state.json")],
        "confidence": "medium",
    }


def run_code_review(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    patch = load_json_optional(workspace_root, sibling_artifact(output_path, "patch_manifest.json"))
    verification = load_json_optional(workspace_root, sibling_artifact(output_path, "verification_report.json"))
    repair_state = load_json_optional(workspace_root, sibling_artifact(output_path, "repair_loop_state.json"))
    role_policy = role_policy_from_request(request)
    blockers = verification.get("blockers") if isinstance(verification.get("blockers"), list) else []
    warnings = verification.get("warnings") if isinstance(verification.get("warnings"), list) else []
    if patch.get("status") != "applied":
        blockers = [*blockers, "Patch manifest was not applied."]
    if verification.get("status") != "passed":
        blockers = [*blockers, "Verification did not pass."]
    review = {
        "status": "blocked" if blockers else "passed",
        "approved": not blockers,
        "role_policy": role_policy,
        "repair_loop_status": repair_state.get("status", "unknown"),
        "findings": [
            {"severity": "blocker", "message": str(item)}
            for item in blockers
        ],
        "warnings": [
            *[
                {"severity": "warning", "message": str(item)}
                for item in warnings
            ],
            {
                "severity": "warning",
                "message": "Ceraxia currently supports only explicit patch operations; autonomous code synthesis is not enabled yet.",
            }
        ],
        "revision_plan": {
            "required": bool(blockers),
            "steps": [
                {
                    "step_id": "implementation",
                    "worker": "FerrumPatchwright",
                    "reason": "Enable or hand off to a source mutation worker before claiming implementation complete.",
                    "source": "code_review",
                    "priority": "blocker",
                },
                {
                    "step_id": "verification",
                    "worker": "OrdinatusVerifier",
                    "reason": "Run concrete verification after source mutation.",
                    "source": "code_review",
                    "priority": "blocker",
                },
            ] if blockers else [],
        },
    }
    write_json(workspace_root, output_path, review)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "needs_revision" if blockers else "passed",
        "summary": f"Code review written with {len(blockers)} blocker(s).",
        "artifacts": [output_path],
        "revision_plan": review["revision_plan"],
        "confidence": "medium",
    }


def run_finalize(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    patch = load_json_optional(workspace_root, sibling_artifact(output_path, "patch_manifest.json"))
    verification = load_json_optional(workspace_root, sibling_artifact(output_path, "verification_report.json"))
    repair_state = load_json_optional(workspace_root, sibling_artifact(output_path, "repair_loop_state.json"))
    review = load_json_optional(workspace_root, sibling_artifact(output_path, "code_review.json"))
    role_policy = role_policy_from_request(request)
    status = "blocked" if review.get("approved") is False else "ready"
    manifest = {
        "status": status,
        "approved": review.get("approved") is True,
        "role_policy": role_policy,
        "role_policies": {
            "implementation": patch.get("role_policy", {}),
            "verification": verification.get("role_policy", {}),
            "code_review": review.get("role_policy", {}),
            "finalize": role_policy,
        },
        "deliverables": [
            sibling_artifact(output_path, "repo_survey.json"),
            sibling_artifact(output_path, "change_plan.md"),
            sibling_artifact(output_path, "patch_manifest.json"),
            sibling_artifact(output_path, "verification_report.json"),
            sibling_artifact(output_path, "repair_loop_state.json"),
            sibling_artifact(output_path, "code_review.json"),
        ],
        "changed_files": patch.get("changed_files", []),
        "patch_source": patch.get("patch_source", ""),
        "diagnostics": patch.get("diagnostics", {}),
        "operation_count": patch.get("operation_count", 0),
        "verification_status": verification.get("status", "unknown"),
        "verification_executed": verification.get("executed", []),
        "verification_repairs": verification.get("repairs", []),
        "repair_loop_state": repair_state,
        "verification_blockers": verification.get("blockers", []),
        "verification_summary": {
            "executed_count": len(verification.get("executed", [])) if isinstance(verification.get("executed"), list) else 0,
            "repair_count": len(verification.get("repairs", [])) if isinstance(verification.get("repairs"), list) else 0,
            "blocker_count": len(verification.get("blockers", [])) if isinstance(verification.get("blockers"), list) else 0,
        },
        "review_status": review.get("status", "unknown"),
        "blockers": [item.get("message") for item in review.get("findings", []) if isinstance(item, dict)],
        "next_safe_action": "handoff_to_patch_worker" if status == "blocked" else "inspect_final_package",
        "summary": "Ceraxia code task package finalized.",
        "revision_plan": review.get("revision_plan", {"required": False, "steps": []}),
    }
    write_json(workspace_root, output_path, manifest)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": status,
        "summary": manifest["summary"],
        "artifacts": [output_path],
        "revision_plan": manifest["revision_plan"],
        "confidence": "medium",
    }


def run(request: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    step = request.get("step") if isinstance(request.get("step"), dict) else {}
    step_id = str(step.get("step_id") or "")
    output_path = output_path_from_request(request)
    handlers = {
        "repository_survey": run_repository_survey,
        "change_planning": run_change_planning,
        "implementation": run_implementation,
        "verification": run_verification,
        "code_review": run_code_review,
        "finalize": run_finalize,
    }
    handler = handlers.get(step_id)
    if handler is None:
        return {"ok": False, "worker": worker_name(), "error": f"unsupported step_id: {step_id}"}
    return handler(request, workspace_root, output_path)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run CogitatorCodewright code worker.")
    parser.add_argument("request_json")
    parser.add_argument("--workspace-root", default="runtime/mechanicum-work")
    args = parser.parse_args()
    payload = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
    result = run(payload.get("request") if isinstance(payload.get("request"), dict) else payload, Path(args.workspace_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") or result.get("status") in {"blocked", "needs_revision", "passed_with_warnings"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
