"""Postanovshchik: turns the user's goal into executable success checks.

One small LLM call. The checks are shell commands whose exit code 0 means pass —
they are the warband's source of truth instead of paper review.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shlex
import urllib.request
from pathlib import PurePosixPath
from typing import Any


def _first_json_object(content: str) -> dict[str, Any]:
    """Decode the first complete JSON object without swallowing trailing output."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", str(content)):
        try:
            value, _ = decoder.raw_decode(str(content), match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _chat_json(prompt: str) -> dict[str, Any]:
    base = os.environ.get("SPEC_LLM_BASE_URL", "http://127.0.0.1:8081/v1").rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    payload = {
        "model": os.environ.get("SPEC_LLM_MODEL", "Qwen3-Coder-Next-Q6_K-00001-of-00004.gguf"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 900,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(f"{base}/chat/completions",
                                 data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as resp:
        content = str(((json.loads(resp.read()).get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    return _first_json_object(content)


def _held_out_chat_json(prompt: str) -> dict[str, Any]:
    """Use the configured verifier model; default to the code-capable spec head."""
    base = os.environ.get(
        "HELD_OUT_LLM_BASE_URL",
        os.environ.get("SPEC_LLM_BASE_URL", "http://127.0.0.1:8081/v1"),
    ).rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    payload = {
        "model": os.environ.get(
            "HELD_OUT_LLM_MODEL",
            os.environ.get("SPEC_LLM_MODEL", "Qwen3-Coder-Next-Q6_K-00001-of-00004.gguf"),
        ),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 700,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=240) as resp:
        content = str(
            ((json.loads(resp.read()).get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        )
    return _first_json_object(content)


_TEST_RUNNERS = {
    "pytest", "py.test", "jest", "mocha", "phpunit", "rspec", "tox", "nox", "ctest",
}
_RUNNER_CONTROL_MODULES = {
    "pytest", "nose", "nose2", "hypothesis", "unittest", "doctest", "coverage", "tox", "nox",
}


def _is_real_test_runner(command: str) -> bool:
    """Recognise an invoked test runner, never a word merely printed by the shell."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False
    if not tokens:
        return False
    # Accept a runner at the start of any simple shell segment. This covers
    # ``cd repo && pytest`` without accepting ``echo pytest``.
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in {"&&", ";", "||"}:
            segments.append([])
        else:
            segments[-1].append(token)
    for segment in segments:
        while segment and "=" in segment[0] and not segment[0].startswith(("./", "/")):
            segment = segment[1:]
        if not segment:
            continue
        executable = segment[0].rsplit("/", 1)[-1].lower()
        if executable in _TEST_RUNNERS or (executable == "go" and segment[1:2] == ["test"]):
            return True
        if executable in {"python", "python3"} and len(segment) >= 3:
            if segment[1] == "-m" and segment[2] in {"pytest", "unittest", "nose", "nose2"}:
                return True
        if executable in {"npm", "pnpm", "yarn"}:
            actions = {token.lower() for token in segment[1:3] if not token.startswith("-")}
            if actions & {"test", "run", "run-script", "start"}:
                return True
        if executable in {"cargo", "gradle", "gradlew", "mvn", "mvnw"} and "test" in {
            token.lower() for token in segment[1:4]
        }:
            return True
        if executable in {"bash", "sh", "python", "python3", "node", "php"} and len(segment) > 1:
            script_name = segment[1].rsplit("/", 1)[-1].lower()
            if script_name.startswith(("test", "run_test")) or "/tests/" in f"/{segment[1].lower()}":
                return True
    return False


def _goal_module_hints(goal: str) -> set[str]:
    hints: set[str] = set()
    for raw_path in _goal_candidate_paths(goal):
        path = PurePosixPath(raw_path)
        if path.suffix.casefold() != ".py":
            continue
        module_parts = list(path.parts[:-1])
        if path.stem != "__init__":
            module_parts.append(path.stem)
        if module_parts and all(
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part)
            for part in module_parts
        ):
            hints.add(".".join(module_parts))
    return hints


_SAFE_ORACLE_ATTRIBUTES = {
    "collections": {"Counter"},
    "datetime": {"date", "datetime", "time", "timedelta", "timezone"},
    "decimal": {"Decimal"},
    "fractions": {"Fraction"},
    "itertools": {"chain", "combinations", "permutations", "product", "repeat"},
    "json": {"dumps", "loads"},
    "math": {
        "ceil", "comb", "cos", "e", "exp", "factorial", "floor", "gcd", "hypot",
        "isclose", "lcm", "log", "log10", "pi", "prod", "sin", "sqrt", "tan", "tau",
    },
    "operator": {"add", "eq", "floordiv", "ge", "gt", "le", "lt", "mod", "mul", "pow", "sub", "truediv"},
    "re": {"escape", "findall", "fullmatch", "match", "search", "split", "sub"},
    "statistics": {"mean", "median", "mode", "pstdev", "pvariance", "stdev", "variance"},
    "string": {"ascii_letters", "ascii_lowercase", "ascii_uppercase", "digits", "hexdigits"},
}
_SAFE_ORACLE_MODULES = set(_SAFE_ORACLE_ATTRIBUTES)
_SAFE_PURE_BUILTINS = {
    "abs", "all", "any", "bool", "float", "int", "len", "list", "max", "min",
    "pow", "range", "repr", "round", "sorted", "str", "sum", "tuple",
}
_DANGEROUS_INLINE_MODULES = {
    "builtins", "ctypes", "importlib", "multiprocessing", "os", "pathlib", "pty",
    "runpy", "shutil", "socket", "subprocess", "sys", "tempfile", "threading",
}
def _goal_candidate_paths(goal: str) -> set[str]:
    paths: set[str] = set()
    for raw in re.findall(
        r"[\w./-]+\.(?:py|js|mjs|cjs|php)", str(goal), re.I,
    ):
        value = _normalized_script_path(raw)
        if value:
            paths.add(value)
    return paths


def _normalized_script_path(raw: object) -> str:
    value = str(raw)
    if not value or "\x00" in value or "\\" in value:
        return ""
    if value.startswith("./"):
        value = value[2:]
    path = PurePosixPath(value)
    if (
        not value or path.is_absolute() or not path.parts or ".." in path.parts
        or path.parts[0].endswith(":")
    ):
        return ""
    return path.as_posix()


def _safe_direct_args(tokens: list[str], *, inline_code_index: int = -1) -> bool:
    del inline_code_index
    return all("\x00" not in token for token in tokens)


def _attribute_chain(node: ast.AST) -> tuple[str, tuple[str, ...]]:
    attributes: list[str] = []
    while isinstance(node, ast.Attribute):
        attributes.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return "", ()
    return node.id, tuple(reversed(attributes))


def _pure_oracle_expr(
    node: ast.AST, safe_aliases: dict[str, str],
    local_names: frozenset[str] = frozenset(),
) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (str, bytes, int, float, complex, bool, type(None)))
    if isinstance(node, ast.Name):
        return node.id in local_names
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_pure_oracle_expr(item, safe_aliases, local_names) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            key is not None
            and _pure_oracle_expr(key, safe_aliases, local_names)
            and _pure_oracle_expr(value, safe_aliases, local_names)
            for key, value in zip(node.keys, node.values)
        )
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        bound = set(local_names)
        for generator in node.generators:
            if (
                generator.is_async
                or not isinstance(generator.target, ast.Name)
                or generator.target.id.startswith("_")
                or not _pure_oracle_expr(generator.iter, safe_aliases, frozenset(bound))
            ):
                return False
            bound.add(generator.target.id)
            if not all(
                _pure_oracle_expr(condition, safe_aliases, frozenset(bound))
                for condition in generator.ifs
            ):
                return False
        names = frozenset(bound)
        if isinstance(node, ast.DictComp):
            return (
                _pure_oracle_expr(node.key, safe_aliases, names)
                and _pure_oracle_expr(node.value, safe_aliases, names)
            )
        return _pure_oracle_expr(node.elt, safe_aliases, names)
    if isinstance(node, ast.BinOp) and isinstance(
        node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow),
    ):
        return (
            _pure_oracle_expr(node.left, safe_aliases, local_names)
            and _pure_oracle_expr(node.right, safe_aliases, local_names)
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub, ast.Not)):
        return _pure_oracle_expr(node.operand, safe_aliases, local_names)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        return all(_pure_oracle_expr(value, safe_aliases, local_names) for value in node.values)
    if isinstance(node, ast.Compare):
        return (
            all(isinstance(op, (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)) for op in node.ops)
            and _pure_oracle_expr(node.left, safe_aliases, local_names)
            and all(
                _pure_oracle_expr(value, safe_aliases, local_names)
                for value in node.comparators
            )
        )
    if isinstance(node, ast.IfExp):
        return all(
            _pure_oracle_expr(value, safe_aliases, local_names)
            for value in (node.test, node.body, node.orelse)
        )
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        module = safe_aliases.get(node.value.id, "")
        return bool(module and node.attr in _SAFE_ORACLE_ATTRIBUTES[module])
    if isinstance(node, ast.Call):
        if any(keyword.arg is None for keyword in node.keywords):
            return False
        callable_ok = (
            isinstance(node.func, ast.Name)
            and node.func.id in _SAFE_PURE_BUILTINS
        )
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            module = safe_aliases.get(node.func.value.id, "")
            callable_ok = bool(module and node.func.attr in _SAFE_ORACLE_ATTRIBUTES[module])
        if isinstance(node.func, ast.Attribute) and node.func.attr == "join":
            callable_ok = _pure_oracle_expr(node.func.value, safe_aliases, local_names)
        return (
            callable_ok
            and all(_pure_oracle_expr(arg, safe_aliases, local_names) for arg in node.args)
            and all(
                _pure_oracle_expr(keyword.value, safe_aliases, local_names)
                for keyword in node.keywords
            )
        )
    return False


def _python_inline_probe(code: str, goal: str, *, oracle: bool) -> bool:
    try:
        tree = ast.parse(code, mode="exec")
    except (SyntaxError, ValueError, TypeError):
        return False
    goal_modules = _goal_module_hints(goal)
    safe_aliases: dict[str, str] = {}
    candidate_module_aliases: dict[str, tuple[str, ...]] = {}
    candidate_value_aliases: set[str] = set()
    bound_names: set[str] = set()
    if not tree.body:
        return False
    for statement in tree.body[:-1]:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                module_name = alias.name
                module = module_name.split(".", 1)[0].casefold()
                bound = alias.asname or alias.name.split(".", 1)[0]
                if bound in bound_names:
                    return False
                bound_names.add(bound)
                if module in _DANGEROUS_INLINE_MODULES or module in _RUNNER_CONTROL_MODULES:
                    return False
                if module in _SAFE_ORACLE_MODULES:
                    safe_aliases[bound] = module
                elif not oracle and module_name in goal_modules:
                    module_parts = alias.name.split(".")
                    candidate_module_aliases[bound] = (
                        () if alias.asname else tuple(module_parts[1:])
                    )
                else:
                    return False
        elif isinstance(statement, ast.ImportFrom) and not oracle:
            module_name = str(statement.module or "")
            if statement.level or module_name not in goal_modules:
                return False
            for alias in statement.names:
                if alias.name == "*" or alias.name.startswith("_"):
                    return False
                bound = alias.asname or alias.name
                if bound in bound_names:
                    return False
                bound_names.add(bound)
                candidate_value_aliases.add(bound)
        else:
            return False
    final = tree.body[-1]
    if not (
        isinstance(final, ast.Expr)
        and isinstance(final.value, ast.Call)
        and isinstance(final.value.func, ast.Name)
        and final.value.func.id == "print"
        and len(final.value.args) == 1
        and not final.value.keywords
    ):
        return False
    output = final.value.args[0]
    if oracle:
        return (
            not (isinstance(output, ast.Constant) and not str(output.value).strip())
            and _pure_oracle_expr(output, safe_aliases)
        )
    if not isinstance(output, ast.Call) or any(keyword.arg is None for keyword in output.keywords):
        return False
    function_ok = (
        isinstance(output.func, ast.Name)
        and output.func.id in candidate_value_aliases
    )
    if isinstance(output.func, ast.Attribute) and isinstance(output.func.value, ast.Name):
        function_ok = (
            output.func.value.id in candidate_module_aliases
            and not output.func.attr.startswith("_")
        )
    if isinstance(output.func, ast.Attribute):
        root, attributes = _attribute_chain(output.func)
        if root in candidate_module_aliases and attributes:
            function_ok = (
                attributes[:-1] == candidate_module_aliases[root]
                and not attributes[-1].startswith("_")
            )
    return (
        function_ok
        and all(_pure_oracle_expr(arg, safe_aliases) for arg in output.args)
        and all(_pure_oracle_expr(keyword.value, safe_aliases) for keyword in output.keywords)
    )


def _private_candidate_probe(command: str, goal: str) -> bool:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False
    if len(tokens) < 2:
        return False
    runtime = {
        "python3": "python3", "/usr/bin/python3": "python3",
        "node": "node", "/usr/bin/node": "node",
        "php": "php", "/usr/bin/php": "php",
    }
    executable = runtime.get(tokens[0], "")
    if not executable:
        return False
    if executable == "python3" and tokens[1] == "-c":
        return (
            len(tokens) == 3
            and _safe_direct_args(tokens, inline_code_index=2)
            and _python_inline_probe(tokens[2], goal, oracle=False)
        )
    if not _safe_direct_args(tokens):
        return False
    expected_suffixes = {
        "python3": {".py"}, "node": {".js", ".mjs", ".cjs"}, "php": {".php"},
    }[executable]
    script = _normalized_script_path(tokens[1])
    path = PurePosixPath(script) if script else PurePosixPath()
    return (
        bool(script)
        and path.suffix.casefold() in expected_suffixes
        and path.as_posix() in _goal_candidate_paths(goal)
    )


def _private_oracle(command: str) -> bool:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False
    return (
        len(tokens) == 3
        and tokens[0] in {"python3", "/usr/bin/python3"}
        and tokens[1] == "-c"
        and _safe_direct_args(tokens, inline_code_index=2)
        and _python_inline_probe(tokens[2], "", oracle=True)
    )


def _canonical_private_oracle(command: str) -> str:
    tokens = shlex.split(command, posix=True)
    return f"/usr/bin/python3 -I -S -c {shlex.quote(tokens[2])}"


def _canonical_private_candidate(command: str) -> str:
    tokens = shlex.split(command, posix=True)
    trusted = {
        "python3": "/usr/bin/python3", "/usr/bin/python3": "/usr/bin/python3",
        "node": "/usr/bin/node", "/usr/bin/node": "/usr/bin/node",
        "php": "/usr/bin/php", "/usr/bin/php": "/usr/bin/php",
    }
    tokens[0] = trusted[tokens[0]]
    if len(tokens) > 1 and tokens[1] not in {"-c"}:
        tokens[1] = _normalized_script_path(tokens[1])
    return shlex.join(tokens)


def _structured_checks(
    raw_checks: Any, *, allow_bare: bool, goal: str = "",
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for candidate in raw_checks if isinstance(raw_checks, list) else []:
        if isinstance(candidate, dict) and str(candidate.get("cmd") or "").strip():
            check = {"cmd": str(candidate["cmd"])}
            if isinstance(candidate.get("expect_stdout"), (str, int, float)):
                check["expect_stdout"] = str(candidate["expect_stdout"])
            if str(candidate.get("oracle") or "").strip():
                check["oracle"] = str(candidate["oracle"])
            expected_output = str(check.get("expect_stdout") or "").strip()
            oracle_command = str(check.get("oracle") or "").strip()
            output_evidence = bool(expected_output or oracle_command)
            # Public checks are advisory inputs to the fighter. Private acceptance
            # uses a positive grammar: a direct goal-linked runtime probe plus an
            # independent literal/computational oracle, never a wrapper blacklist.
            if allow_bare or (
                output_evidence
                and _private_candidate_probe(str(check["cmd"]), goal)
                and (not oracle_command or _private_oracle(oracle_command))
            ):
                if not allow_bare:
                    check["cmd"] = _canonical_private_candidate(str(check["cmd"]))
                if not allow_bare and oracle_command:
                    check["oracle"] = _canonical_private_oracle(oracle_command)
                checks.append(check)
        elif allow_bare and isinstance(candidate, str) and candidate.strip():
            checks.append({"cmd": candidate})
    return checks


def build_held_out_plan(goal: str) -> dict[str, Any]:
    """Generate private edge checks and preserve verifier-infrastructure provenance."""
    prompt = (
        "You prepare acceptance for a coding task. Return ONE strict JSON object and nothing else:\n"
        '{"deliverables": ["relative file paths the task must produce"],\n'
        ' "checks": [ {"cmd": "command that runs the program"},\n'
        '             {"cmd": "...", "expect_stdout": "literal expected output"},\n'
        '             {"cmd": "...", "oracle": "command that computes the correct answer"} ]}\n'
        "Rules for checks:\n"
        "- Each check is a JSON object, NOT a shell string. Do not write pipes/grep/test yourself.\n"
        "- 'cmd' is a single command that runs the deliverable (e.g. \"python3 calc.py '2+3*4'\").\n"
        "- NEVER put an answer you computed in your head into expect_stdout — you make arithmetic mistakes.\n"
        "  Use expect_stdout ONLY for values written literally in the task text.\n"
        "- For anything you'd have to compute, use 'oracle': a command (python3 -c ... / node -e ...) that\n"
        "  produces the correct answer, so the real interpreter is the source of truth, not you.\n"
        "- A bare {\"cmd\": ...} (no expect/oracle) passes on exit code 0 — use it ONLY for 'does it run / compile'.\n"
        "- REQUIRED: include at least one BEHAVIOURAL check that verifies the program does the right thing.\n"
        "Keep 2-6 checks, runnable on bare Ubuntu with php, python3, node.\n\n"
        f"TASK:\n{goal}"
    )
    generator_error = ""
    saw_payload = False
    for attempt in range(2):
        try:
            payload = _held_out_chat_json(prompt)
            saw_payload = True
        except Exception as exc:
            generator_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            payload = {}
        checks = _structured_checks(payload.get("checks"), allow_bare=False, goal=goal)[:3]
        if checks:
            return {"status": "ok", "checks": checks, "error": ""}
        if attempt == 0:
            prompt += (
                "\n\nREPAIR: your previous reply was rejected by the strict verifier grammar. Return only "
                "corrected JSON. A Python oracle shell command has exactly three argv: python3, -c, and one "
                "balanced quoted program containing exactly print(expression). Do not place an extra parenthesis "
                "outside the quoted -c program. Prefer a direct candidate command plus a pure range/generator "
                "reference expression."
            )
    if generator_error and not saw_payload:
        return {
            "status": "generator_unavailable", "checks": [],
            "error": generator_error,
        }
    return {
        "status": "invalid_spec", "checks": [],
        "error": "private verifier produced no candidate-linked behavioural checks",
    }


def build_held_out_checks(goal: str) -> list[dict[str, Any]]:
    """Compatibility helper for callers that only need the private checks."""
    return list(build_held_out_plan(goal).get("checks") or [])


def build_spec(goal: str) -> dict[str, Any]:
    """Returns {"deliverables": [...], "checks": [...]}.

    A check is a STRUCTURED dict (never a hand-written shell one-liner — the model
    mangles quoting/substitution). The acceptor turns it into a correct command:
      {"cmd": "..."}                          -> pass iff exit code 0
      {"cmd": "...", "expect_stdout": "14"}   -> pass iff trimmed stdout == "14"
      {"cmd": "...", "oracle": "python3 -c 'print(3*(5+2)-8/4)'"}
                                              -> pass iff stdout == oracle's stdout
    """
    prompt = (
        "You prepare acceptance for a coding task. Return ONE strict JSON object and nothing else:\n"
        '{"deliverables": ["relative file paths the task must produce"],\n'
        ' "checks": [ {"cmd": "command that runs the program"},\n'
        '             {"cmd": "...", "expect_stdout": "literal expected output"},\n'
        '             {"cmd": "...", "oracle": "command that computes the correct answer"} ]}\n'
        "Rules for checks:\n"
        "- Each check is a JSON object, NOT a shell string. Do not write pipes/grep/test yourself.\n"
        "- 'cmd' is a single command that runs the deliverable (e.g. \"python3 calc.py '2+3*4'\").\n"
        "- NEVER put an answer you computed in your head into expect_stdout — you make arithmetic mistakes.\n"
        "  Use expect_stdout ONLY for values written literally in the task text.\n"
        "- For anything you'd have to compute, use 'oracle': a command (python3 -c ... / node -e ...) that\n"
        "  produces the correct answer, so the real interpreter is the source of truth, not you.\n"
        "- A bare {\"cmd\": ...} (no expect/oracle) passes on exit code 0 — use it ONLY for 'does it run / "
        "compile'. A compile/syntax check ALONE is NOT acceptance: wrong logic would pass it.\n"
        "- REQUIRED: include at least one BEHAVIOURAL check that verifies the program does the right thing "
        "(expect_stdout or oracle on real inputs, or a command that runs the project's tests). Cover the main "
        "case and at least one edge case. Never rely on compile-only.\n"
        "Keep 2-6 checks, runnable on bare Ubuntu with php, python3, node.\n\n"
        f"TASK:\n{goal}"
    )
    try:
        spec = _chat_json(prompt)
    except Exception:
        spec = {}
    deliverables = [str(d) for d in (spec.get("deliverables") or []) if isinstance(d, str)]
    checks = _structured_checks(spec.get("checks"), allow_bare=True)
    # Fallback: the model gave files but no checks. Never return an empty check set
    # (that would let a mission be "accepted" having proven nothing). Synthesize a
    # minimum real check per deliverable — that it at least parses/compiles.
    if not checks and deliverables:
        _syntax = {".py": "python3 -m py_compile {p}", ".php": "php -l {p}",
                   ".js": "node --check {p}", ".sh": "bash -n {p}"}
        for d in deliverables:
            ext = "." + d.rsplit(".", 1)[-1] if "." in d else ""
            if ext in _syntax:
                checks.append({"cmd": _syntax[ext].format(p=d)})
    return {"deliverables": deliverables, "checks": checks}
