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
_MAX_PRIVATE_EXPECT_BYTES = 16_384
_ONE_TRAILING_LF_MARKERS = (
    "one newline", "single newline", "one trailing newline",
    "one newline character", "один символ новой строки",
    "одним символом новой строки", "одной новой строкой",
)
_NO_TRAILING_LF_MARKERS = (
    "do not add one newline", "do not append one newline", "do not add a newline",
    "do not append a newline", "without a newline", "without newline",
    "no trailing newline", "must not end with a newline", "must not end with newline",
    "не добавлять один символ новой строки", "не добавлять новую строку",
    "без символа новой строки", "без новой строки",
)
_NEGATIVE_LITERAL_MARKERS = (
    "do not use", "don't use", "must not use", "never use", "avoid using",
    "do not output", "must not output", "never output", "forbidden value",
    "should not", "must not be", "not be used", "not be output",
    "не использовать", "не используй", "не выводить", "не выводи",
    "не должно быть", "не должен быть", "не должна быть", "не следует",
    "запрещено использовать", "запрещённое значение",
)
_FILE_LITERAL_CONTEXT_RE = re.compile(
    r"(?:"
    r"exact(?:ly)?\s+(?:file\s+|artifact\s+)?(?:content|text|bytes?)|"
    r"(?:file|artifact)(?:\s+[\w./-]+)?\s+(?:content|text|bytes?)\s+"
    r"(?:is|must\s+be|should\s+be|equals?)(?:\s+exact(?:ly)?)?|"
    r"with\s+(?:the\s+)?(?:exact\s+)?(?:content|text|bytes?)|"
    r"write\s+(?:the\s+)?(?:exact\s+)?(?:content|text|bytes?)|"
    r"с\s+(?:точным[и]?\s+)?(?:текстом|содержимым|байтами)|"
    r"содержимое(?:\s+файла)?\s+(?:точно|ровно|соответствует\s+(?:строке|тексту))|"
    r"точн(?:ое|ый|ые)\s+(?:содержимое|текст|байты)"
    r")\s*(?::|=)?\s*$",
    re.I,
)
_STDOUT_LITERAL_CONTEXT_RE = re.compile(
    r"(?:"
    r"exact(?:ly)?\s+(?:output|stdout|return\s+value)|"
    r"(?:output|stdout|return\s+value)\s+"
    r"(?:is|must\s+be|should\s+be|equals?)(?:\s+exact(?:ly)?)?|"
    r"(?:print|prints|printed|output|outputs|return|returns|yield|yields)"
    r"(?:\s+exact(?:ly)?)?|"
    r"точн(?:ый|ое)\s+вывод|"
    r"(?:вывод\w*|печат\w*|возвращ\w*|выда\w*)(?:\s+(?:ровно|точно))?"
    r")\s*(?::|=)?\s*$",
    re.I,
)
_PATH_FILE_RELATION_RE = re.compile(
    r"^\s*,?\s*(?:"
    r"with\s+(?:the\s+)?(?:exact\s+)?(?:content|text|bytes?)|"
    r"(?:content|text|bytes?)\s+(?:is|must\s+be|should\s+be|equals?)"
    r"(?:\s+exact(?:ly)?)?|"
    r"(?:has|contains?)\s+(?:the\s+)?(?:exact\s+)?(?:content|text|bytes?)|"
    r"exact(?:ly)?\s+(?:content|text|bytes?)|"
    r"с\s+(?:точным[и]?\s+)?(?:текстом|содержимым|байтами)|"
    r"(?:содержимое|текст|байты)\s+(?:точно|ровно|соответствует\s+(?:строке|тексту))"
    r")\s*(?::|=)?\s*$",
    re.I,
)
_UNQUOTED_FILE_LITERAL_RE = re.compile(
    r"(?<!\w)(?:with\s+(?:the\s+)?(?:exact\s+)?(?:content|text|bytes?)|"
    r"exact(?:\s+(?:file|artifact))?\s+(?:content|text|bytes?)|"
    r"(?:file|artifact)(?:\s+[\w./-]+)?\s+(?:content|text|bytes?)\s+"
    r"(?:is\s+)?exact(?:ly)?|"
    r"(?:точн(?:ое|ые|ый)|ровно)\s+(?:содержимое|текст|байты))"
    r"\s*(?::|=|is|must\s+be|долж(?:но|ен)\s+быть)?\s*"
    r"(?P<value>[^\s,;.!?]+)",
    re.I,
)
_UNQUOTED_STDOUT_LITERAL_RE = re.compile(
    r"(?<!\w)(?:exact(?:\s+)?(?:output|stdout|return\s+value)|"
    r"(?:output|stdout|return\s+value)\s+(?:is\s+)?exact(?:ly)?|"
    r"(?:точн(?:ое|ый)|ровно)\s+вывод)"
    r"\s*(?::|=|is|must\s+be|долж(?:но|ен)\s+быть)?\s*"
    r"(?P<value>[^\s,;.!?]+)",
    re.I,
)
_DECLARED_PATH_RE = re.compile(
    r"(?<![\w./-])(?:[\w.-]+/)*[\w.-]+\.[A-Za-z][A-Za-z0-9_-]{0,15}"
    r"(?![\w/-]|\.[\w])",
)
_CODE_TARGET_VERBS = (
    "implement", "fix", "update", "build", "create", "write", "run",
    "реализовать", "исправить", "обновить", "создать", "написать", "запустить",
    "создание",
)
_TARGET_NEGATIVE_MARKERS = (
    "do not modify", "must not modify", "do not change", "must not change",
    "do not run", "must not run", "do not create", "must not create",
    "do not write", "must not write", "do not add", "must not add",
    "preserve", "не изменять", "не менять", "не запускать", "не создавать",
    "не записывать", "не добавлять", "запрещено изменять", "сохранить без изменений",
)


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
    if not value or "\\" in value or any(ord(char) < 32 for char in value):
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


def _goal_mentions_path(path: str, goal: str) -> bool:
    """Require an exact, task-authored relative path before private file access."""
    normalized = _normalized_script_path(path)
    if not normalized:
        return False
    return bool(re.search(
        rf"(?<![\w./-]){re.escape(normalized)}(?![\w/-]|\.[\w])",
        str(goal),
    ))


def _literal_clause_before(text: str, start: int, *, fenced: bool = False) -> str:
    window = text[max(0, start - 240):start]
    boundary = max(window.rfind("."), window.rfind(";"))
    window = window[boundary + 1:]
    if not fenced and "\n" in window:
        window = window.rsplit("\n", 1)[-1]
    return window.casefold()


def _literal_clause_after(text: str, end: int) -> str:
    suffix = text[end:end + 180]
    boundaries = [index for marker in (".", ";", "\n") if (index := suffix.find(marker)) >= 0]
    if boundaries:
        suffix = suffix[:min(boundaries)]
    return suffix.casefold()


def _context_contains_marker(context: str, marker: str) -> bool:
    if not marker.isascii():
        return marker in context
    words = r"\s+".join(re.escape(part) for part in marker.split())
    return bool(re.search(rf"(?<![A-Za-z0-9_]){words}(?![A-Za-z0-9_])", context))


def _negative_literal_context(context: str) -> bool:
    return any(
        _context_contains_marker(context, marker)
        for marker in _NEGATIVE_LITERAL_MARKERS
    )


def _relational_literal_channel(context: str) -> str:
    if _negative_literal_context(context):
        return ""
    file_match = bool(_FILE_LITERAL_CONTEXT_RE.search(context))
    stdout_match = bool(_STDOUT_LITERAL_CONTEXT_RE.search(context))
    if file_match == stdout_match:
        return ""
    return "file_bytes" if file_match else "stdout"


def _requires_one_trailing_lf(suffix: str) -> bool:
    return (
        any(
            _context_contains_marker(suffix, marker)
            for marker in _ONE_TRAILING_LF_MARKERS
        )
        and not any(
            _context_contains_marker(suffix, marker)
            for marker in _NO_TRAILING_LF_MARKERS
        )
    )


def _goal_path_occurrences(text: str, *, code_only: bool) -> list[tuple[str, int, int]]:
    code_suffixes = {".py", ".js", ".mjs", ".cjs", ".php"}
    found: list[tuple[str, int, int]] = []
    for match in _DECLARED_PATH_RE.finditer(text):
        path = _normalized_script_path(match.group(0))
        if not path:
            continue
        if code_only and PurePosixPath(path).suffix.casefold() not in code_suffixes:
            continue
        found.append((path, match.start(), match.end()))
    return found


def _target_prefix(text: str, path_start: int) -> str:
    offset = max(0, path_start - 180)
    prefix = text[offset:path_start]
    masked = list(prefix)
    for _path, start, end in _goal_path_occurrences(prefix, code_only=False):
        for index in range(start, end):
            if masked[index] == ".":
                masked[index] = "_"
    masked_text = "".join(masked)
    boundary = max(masked_text.rfind("."), masked_text.rfind(";"), masked_text.rfind("\n"))
    return prefix[boundary + 1:].casefold()


def _literal_target(text: str, literal_start: int, channel: str) -> str:
    occurrences = [
        item for item in _goal_path_occurrences(text, code_only=channel == "stdout")
        if item[2] <= literal_start and literal_start - item[2] <= 360
    ]
    if not occurrences:
        return ""
    path, path_start, path_end = max(occurrences, key=lambda item: item[2])
    prefix = _target_prefix(text, path_start)
    relation = text[path_end:literal_start].casefold()
    target_context = prefix + " " + relation
    if any(
        _context_contains_marker(target_context, marker)
        for marker in _TARGET_NEGATIVE_MARKERS
    ):
        return ""
    if channel == "file_bytes":
        return path if _PATH_FILE_RELATION_RE.fullmatch(relation) else ""
    direct_subject = bool(re.match(
        r"\s+(?:print|prints|output|outputs|return|returns|yield|yields)\b",
        relation,
    ))
    positive_verb = any(
        _context_contains_marker(prefix, marker) for marker in _CODE_TARGET_VERBS
    )
    return path if direct_subject or positive_verb else ""


def _authoritative_literal_policies(goal: str) -> dict[str, dict[str, Any]]:
    """Extract whole task-authored values; never authorize by substring presence."""
    text = str(goal)
    policies: dict[str, dict[str, Any]] = {
        "file_bytes": {"targets": {}, "unbound": {}},
        "stdout": {"targets": {}, "unbound": {}},
    }

    def record(value: str, start: int, end: int, *, fenced: bool = False) -> None:
        context = _literal_clause_before(text, start, fenced=fenced)
        channel = _relational_literal_channel(context)
        if (
            not value
            or len(value.encode("utf-8")) > _MAX_PRIVATE_EXPECT_BYTES
            or not channel
        ):
            return
        suffix = _literal_clause_after(text, end)
        one_lf = _requires_one_trailing_lf(suffix)
        target = _literal_target(text, start, channel)
        if target:
            target_values = policies[channel]["targets"].setdefault(target, {})
            target_values[value] = bool(target_values.get(value) or one_lf)
        else:
            unbound = policies[channel]["unbound"]
            unbound[value] = bool(unbound.get(value) or one_lf)

    # Fenced blocks are data only when introduced by an explicit content/output
    # clause.  The line breaks surrounding the fence are Markdown framing.
    for match in re.finditer(
        r"```(?:[A-Za-z0-9_.+-]+)?[ \t]*\r?\n(?P<value>.*?)\r?\n```",
        text,
        re.S,
    ):
        record(match.group("value"), match.start(), match.end(), fenced=True)

    quote_pairs = (("'", "'"), ('"', '"'), ("`", "`"), ("“", "”"), ("«", "»"))
    for opening, closing in quote_pairs:
        prefix = r"(?<!\w)" if opening == "'" else ""
        suffix = r"(?!\w)" if closing == "'" else ""
        pattern = prefix + re.escape(opening) + r"(?P<value>[^\r\n]*?)" + re.escape(closing) + suffix
        for match in re.finditer(pattern, text):
            record(match.group("value"), match.start(), match.end())

    # Unquoted values are deliberately narrow: one whole token immediately after
    # an explicit exact-content/output phrase.  This cannot bless a token merely
    # because it appears elsewhere in a filename, identifier or negative clause.
    for channel, pattern in (
        ("file_bytes", _UNQUOTED_FILE_LITERAL_RE),
        ("stdout", _UNQUOTED_STDOUT_LITERAL_RE),
    ):
        for match in pattern.finditer(text):
            value = match.group("value")
            context = (
                _literal_clause_before(text, match.start())
                + " " + text[match.start():match.start("value")].casefold()
            )
            if (
                value
                and not any(marker in value for marker in ("'", '"', "`"))
                and not _negative_literal_context(context)
            ):
                suffix = _literal_clause_after(text, match.end())
                one_lf = _requires_one_trailing_lf(suffix)
                target = _literal_target(text, match.start("value"), channel)
                if target:
                    target_values = policies[channel]["targets"].setdefault(target, {})
                    target_values[value] = bool(target_values.get(value) or one_lf)
                else:
                    unbound = policies[channel]["unbound"]
                    unbound[value] = bool(unbound.get(value) or one_lf)

    # A later generic directive may strengthen newline semantics for a pair that
    # was already bound by an authoritative target relation.  It cannot create a
    # new target/value pair.
    for channel in ("file_bytes", "stdout"):
        unbound = policies[channel]["unbound"]
        for values in policies[channel]["targets"].values():
            for literal in set(values) & set(unbound):
                values[literal] = bool(values[literal] or unbound[literal])
    return policies


def _authoritative_expected_value(
    expected: object,
    goal: str,
    *,
    exact_bytes: bool,
    target: str,
    target_goal: str,
) -> bool:
    if not isinstance(expected, str):
        return False
    policies = _authoritative_literal_policies(goal)
    channel = "file_bytes" if exact_bytes else "stdout"
    values = dict(policies[channel]["targets"].get(target) or {})
    if channel == "stdout" and not values and target:
        authoritative_targets = {
            path for path, _start, _end in _goal_path_occurrences(goal, code_only=True)
        }
        enriched_targets = {
            path for path, _start, _end in _goal_path_occurrences(target_goal, code_only=True)
        }
        if not authoritative_targets and enriched_targets == {target}:
            values = dict(policies[channel]["unbound"])
    for literal, one_lf in values.items():
        if exact_bytes:
            authorized = literal + "\n" if one_lf and not literal.endswith("\n") else literal
            if expected == authorized:
                return True
        elif expected == literal or (one_lf and expected == literal + "\n"):
            return True
    return False


def _private_file_bytes_check(candidate: Any, goal: str) -> dict[str, Any] | None:
    """Validate an inert exact-file assertion from authoritative task literals."""
    if not isinstance(candidate, dict) or candidate.get("kind") != "file_bytes":
        return None
    path = _normalized_script_path(candidate.get("path") or "")
    expected = candidate.get("expect_bytes")
    if (
        not path
        or not _goal_mentions_path(path, goal)
        or not isinstance(expected, str)
        or not _authoritative_expected_value(
            expected, goal, exact_bytes=True, target=path, target_goal=goal,
        )
        or len(expected.encode("utf-8")) > _MAX_PRIVATE_EXPECT_BYTES
    ):
        return None
    return {"kind": "file_bytes", "path": path, "expect_bytes": expected}


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
            not isinstance(output, ast.Constant)
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


def _private_candidate_target(command: str, goal: str) -> str:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return ""
    if len(tokens) < 2 or tokens[0] not in {
        "python3", "/usr/bin/python3", "node", "/usr/bin/node", "php", "/usr/bin/php",
    }:
        return ""
    if tokens[1] != "-c":
        path = _normalized_script_path(tokens[1])
        return path if path in _goal_candidate_paths(goal) else ""
    if tokens[0] not in {"python3", "/usr/bin/python3"} or len(tokens) != 3:
        return ""
    try:
        tree = ast.parse(tokens[2], mode="exec")
    except (SyntaxError, ValueError, TypeError):
        return ""
    imported: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            imported.update(alias.name for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom) and statement.module and not statement.level:
            imported.add(statement.module)
    matches: list[str] = []
    for path in _goal_candidate_paths(goal):
        pure = PurePosixPath(path)
        if pure.suffix.casefold() != ".py":
            continue
        parts = list(pure.parts[:-1])
        if pure.stem != "__init__":
            parts.append(pure.stem)
        module = ".".join(parts)
        if module and module in imported:
            matches.append(path)
    return matches[0] if len(matches) == 1 else ""


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


def _candidate_test_operands(command: str) -> set[str]:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return set()
    operands: set[str] = set()
    if len(tokens) >= 2 and tokens[1] != "-c":
        for token in tokens[2:]:
            if token:
                operands.add(token)
                if "=" in token:
                    operands.add(token.split("=", 1)[1])
                try:
                    expression = ast.parse(token, mode="eval")
                except (SyntaxError, ValueError, TypeError):
                    expression = None
                if expression is not None:
                    for child in ast.walk(expression):
                        if isinstance(child, ast.Constant) and child.value not in {None, "", b""}:
                            operands.add(str(child.value))
        return operands
    if len(tokens) != 3 or tokens[1] != "-c":
        return operands
    try:
        tree = ast.parse(tokens[2], mode="exec")
    except (SyntaxError, ValueError, TypeError):
        return operands
    if not tree.body:
        return operands
    final = tree.body[-1]
    if not isinstance(final, ast.Expr) or not isinstance(final.value, ast.Call):
        return operands
    candidate_call = final.value.args[0] if (
        isinstance(final.value.func, ast.Name)
        and final.value.func.id == "print"
        and len(final.value.args) == 1
        and isinstance(final.value.args[0], ast.Call)
    ) else None
    if isinstance(candidate_call, ast.Call):
        for node in [*candidate_call.args, *(item.value for item in candidate_call.keywords)]:
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and child.value not in {None, "", b""}:
                    operands.add(str(child.value))
    return operands


def _task_scalar_operands(goal: str) -> set[str]:
    operands: set[str] = set()
    policies = _authoritative_literal_policies(goal)
    for channel in ("file_bytes", "stdout"):
        for values in policies[channel]["targets"].values():
            operands.update(values)
        operands.update(policies[channel]["unbound"])
    operands.update(re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])", str(goal)))
    lowered = str(goal).casefold()
    formatting_operands = (
        (" ", ("space-separated", "separated by spaces", "separated by a space", "разделены пробелом")),
        (",", ("comma-separated", "separated by commas", "separated by a comma", "разделены запятыми")),
        ("\n", ("newline-separated", "separated by newlines", "one per line", "каждый с новой строки")),
    )
    for scalar, markers in formatting_operands:
        if any(marker in lowered for marker in markers):
            operands.add(scalar)
    return operands


def _oracle_scalar_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)


def _private_oracle_for_check(
    command: str,
    candidate_command: str,
    authoritative_goal: str,
) -> bool:
    """Require an independent computation tied to authoritative/test operands."""
    if not _private_oracle(command):
        return False
    try:
        tokens = shlex.split(command, posix=True)
        tree = ast.parse(tokens[2], mode="exec")
    except (IndexError, UnicodeError, ValueError, SyntaxError, TypeError):
        return False
    output = tree.body[-1].value.args[0]  # shape is pinned by _private_oracle
    operands = _candidate_test_operands(candidate_command) | _task_scalar_operands(
        authoritative_goal,
    )
    authorized_leaf_ids: set[int] = set()
    for node in ast.walk(output):
        if not isinstance(node, ast.Constant) or node.value in {None, "", b""}:
            continue
        try:
            value = _oracle_scalar_text(node.value)
        except UnicodeError:
            return False
        if value not in operands:
            return False
        authorized_leaf_ids.add(id(node))
    if not authorized_leaf_ids:
        return False

    def depends_on_operand(node: ast.AST) -> bool:
        return any(id(child) in authorized_leaf_ids for child in ast.walk(node))

    def string_like(node: ast.AST) -> bool:
        if isinstance(node, ast.Constant):
            return isinstance(node.value, (str, bytes))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return string_like(node.left) or string_like(node.right)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            return node.func.id in {"str", "repr"}
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            return node.func.attr == "join"
        return False

    identity_calls = {"str", "repr", "int", "float", "bool", "list", "tuple", "range"}
    for node in ast.walk(output):
        if not depends_on_operand(node):
            continue
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Add) and string_like(node):
                continue
            return True
        if isinstance(node, (ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp)):
            return True
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in identity_calls:
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr == "join":
                continue
            return True
    return False


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
    file_evidence_goal: str | None = None,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for candidate in raw_checks if isinstance(raw_checks, list) else []:
        authoritative_goal = file_evidence_goal if file_evidence_goal is not None else goal
        file_check = (
            _private_file_bytes_check(
                candidate,
                authoritative_goal,
            )
            if not allow_bare else None
        )
        if file_check is not None:
            checks.append(file_check)
            continue
        if isinstance(candidate, dict) and str(candidate.get("cmd") or "").strip():
            check = {"cmd": str(candidate["cmd"])}
            candidate_target = _private_candidate_target(check["cmd"], goal)
            if isinstance(candidate.get("expect_stdout"), (str, int, float)):
                check["expect_stdout"] = str(candidate["expect_stdout"])
            if str(candidate.get("oracle") or "").strip():
                check["oracle"] = str(candidate["oracle"])
            literal_authorized = (
                "expect_stdout" in check
                and _authoritative_expected_value(
                    check["expect_stdout"], authoritative_goal, exact_bytes=False,
                    target=candidate_target, target_goal=goal,
                )
            )
            if not allow_bare and "expect_stdout" in check and not literal_authorized:
                # A valid independent oracle may still carry the check.  Otherwise
                # removing this value makes output_evidence false and rejects it.
                # In either case the model's unauthoritative literal cannot decide.
                check.pop("expect_stdout", None)
            expected_output = str(check.get("expect_stdout") or "").strip()
            oracle_command = str(check.get("oracle") or "").strip()
            output_evidence = bool(expected_output or oracle_command)
            # Public checks are advisory inputs to the fighter. Private acceptance
            # uses a positive grammar: a direct goal-linked runtime probe plus an
            # independent literal/computational oracle, never a wrapper blacklist.
            if allow_bare or (
                output_evidence
                and _private_candidate_probe(str(check["cmd"]), goal)
                and (
                    not oracle_command
                    or _private_oracle_for_check(
                        oracle_command, str(check["cmd"]), authoritative_goal,
                    )
                )
            ):
                if not allow_bare:
                    check["cmd"] = _canonical_private_candidate(str(check["cmd"]))
                if not allow_bare and oracle_command:
                    check["oracle"] = _canonical_private_oracle(oracle_command)
                checks.append(check)
        elif allow_bare and isinstance(candidate, str) and candidate.strip():
            checks.append({"cmd": candidate})
    return checks


def build_held_out_plan(goal: str, *, task_goal: str | None = None) -> dict[str, Any]:
    """Generate private edge checks and preserve verifier-infrastructure provenance."""
    evidence_goal = str(task_goal) if task_goal is not None else goal
    prompt = (
        "You prepare acceptance for a coding task. Return ONE strict JSON object and nothing else:\n"
        '{"deliverables": ["relative file paths the task must produce"],\n'
        ' "checks": [ {"cmd": "command that runs the program"},\n'
        '             {"cmd": "...", "expect_stdout": "literal expected output"},\n'
        '             {"cmd": "...", "oracle": "command that computes the correct answer"},\n'
        '             {"kind": "file_bytes", "path": "task-named relative file",\n'
        '              "expect_bytes": "exact UTF-8 content, with no implicit newline"} ]}\n'
        "Rules for checks:\n"
        "- Each check is a JSON object, NOT a shell string. Do not write pipes/grep/test yourself.\n"
        "- For a non-executable file whose exact content is stated by the task, use file_bytes. Its path must\n"
        "  appear literally in the task. Copy the exact requested content into expect_bytes; preserve explicit\n"
        "  newlines and never invent a trailing newline. Do NOT use cat/stat/test or Python to inspect the file.\n"
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
        checks = _structured_checks(
            payload.get("checks"), allow_bare=False, goal=goal,
            file_evidence_goal=evidence_goal,
        )[:3]
        if checks:
            return {"status": "ok", "checks": checks, "error": ""}
        if attempt == 0:
            prompt += (
                "\n\nREPAIR: your previous reply was rejected by the strict verifier grammar. Return only "
                "corrected JSON. A Python oracle shell command has exactly three argv: python3, -c, and one "
                "balanced quoted program containing exactly print(expression). Do not place an extra parenthesis "
                "outside the quoted -c program. For an exact static artifact, use ONLY "
                '{"kind":"file_bytes","path":"the exact relative path from TASK",'
                '"expect_bytes":"the exact task-requested UTF-8 content"}; do not use a shell command. '
                "For executable code, prefer a direct candidate command plus a pure range/generator reference "
                "expression."
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
    checks = _structured_checks(spec.get("checks"), allow_bare=True, goal=goal)
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
