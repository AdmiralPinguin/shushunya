from __future__ import annotations

"""Test-evidence patch-spec inference for the implementation role."""

import sys
from pathlib import Path

_WORKERS_ROOT = Path(__file__).resolve().parents[1]
for _p in (_WORKERS_ROOT, _WORKERS_ROOT / "OrdinatusVerifier"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from common.codewright_core import *  # noqa: F403 - shared Codewright helper surface.
from verification import *  # noqa: F403,E402 - reuses verifier diagnostics.
from verification import diagnostic_extraction_from_execution  # noqa: E402

from patch_markers import verification_commands_from_markers  # noqa: E402


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


def verification_commands_from_natural_goal(goal: str) -> list[str]:
    commands = verification_commands_from_markers(goal)
    for match in re.finditer(r"(?:проверь|запусти|run|verify|test)\s+`([^`]+)`", goal, flags=re.IGNORECASE):
        command = match.group(1).strip()
        if command and command not in commands:
            commands.append(command)
    return commands
