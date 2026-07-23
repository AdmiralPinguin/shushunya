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

from acceptor import check_kind
from product_probe import normalize_profile


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
        # Немного креативности и здесь (решение владельца): на нуле каждая попытка
        # получает ту же самую нарезку/те же проверки — ретрай не может сменить заход.
        # JSON-строгость обеспечивает парсер, не жадность декодирования.
        "temperature": float(os.environ.get("SKITARII_SPEC_TEMPERATURE", "0.2")),
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
        # Немного креативности и здесь (решение владельца): на нуле каждая попытка
        # получает ту же самую нарезку/те же проверки — ретрай не может сменить заход.
        # JSON-строгость обеспечивает парсер, не жадность декодирования.
        "temperature": float(os.environ.get("SKITARII_SPEC_TEMPERATURE", "0.2")),
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
    for raw_path in _positive_code_targets(goal):
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
    "one newline character", "exactly one lf", "followed by one lf",
    "followed by exactly one lf", "+ lf",
    "один символ новой строки", "одним символом новой строки",
    "одной новой строкой", "один символ lf в конце",
    "символом переноса строки lf",
)
_NO_TRAILING_LF_MARKERS = (
    "do not add one newline", "do not append one newline", "do not add a newline",
    "do not append a newline", "without a newline", "without newline",
    "no trailing newline", "must not end with a newline", "must not end with newline",
    "without lf", "no trailing lf", "must not end with lf",
    "do not append + lf", "do not add + lf", "don't append + lf",
    "не добавлять один символ новой строки", "не добавлять новую строку",
    "не добавлять lf", "без символа новой строки", "без новой строки",
    "без символа переноса строки", "без lf",
)
_AMBIGUOUS_TRAILING_LF_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:not|isn't|is\s+not)\s+exactly\s+one\s+lf"
    r"(?![A-Za-z0-9_])|"
    r"(?<![A-Za-z0-9_])not(?:\s*,[^,\r\n]{0,80},)?\s+followed\s+by\s+"
    r"(?:exactly\s+)?one\s+lf(?![A-Za-z0-9_])|"
    r"(?<![А-Яа-яЁё0-9_])не\s+ровно\s+один\s+lf(?![А-Яа-яЁё0-9_])",
    re.I,
)
_LF_UNSPECIFIED = "unspecified"
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
    r"containing\s+exactly|"
    r"write\s+(?:the\s+)?(?:exact\s+)?(?:content|text|bytes?)|"
    r"(?:file\s+)?content\s+matches?|"
    r"exact\s+byte\s+match|"
    r"с\s+(?:точным[и]?\s+)?(?:текстом|содержимым|байтами)|"
    r"содержимое(?:\s+файла)?\s+совпадает\s+с|"
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
    r"точн\w*\s+вывод\w*|"
    r"(?:вывод\w*|печат\w*|возвращ\w*|выда\w*)(?:\s+(?:ровно|точно))?"
    r")\s*(?::|=)?\s*$",
    re.I,
)
_PATH_FILE_RELATION_RE = re.compile(
    r"^\s*,?\s*(?:"
    r"with\s+(?:the\s+)?(?:exact\s+)?(?:content|text|bytes?)|"
    r"containing\s+exactly|"
    r"(?:content|text|bytes?)\s+(?:is|must\s+be|should\s+be|equals?)"
    r"(?:\s+exact(?:ly)?)?|"
    r"(?:has|contains?)\s+(?:the\s+)?(?:exact\s+)?(?:content|text|bytes?)|"
    r"exact(?:ly)?\s+(?:content|text|bytes?)|"
    r"с\s+(?:точным[и]?\s+)?(?:текстом|содержимым|байтами)|"
    r"(?:содержимое|текст|байты)\s+(?:точно|ровно|соответствует\s+(?:строке|тексту))"
    r")\s*(?::|=)?\s*$",
    re.I,
)
_EN_STDOUT_PREDICATE_SRC = (
    r"(?:"
    r"(?:print|prints|printed|output|outputs|return|returns|yield|yields)"
    r"(?:\s+(?:the\s+)?(?:(?:exact|exactly)\s+)?"
    r"(?:output|stdout|value|text))?(?:\s+exact(?:ly)?)?|"
    r"(?:exact(?:ly)?\s+)?(?:output|stdout|return\s+value)"
    r"(?:\s+(?:is|must\s+be|should\s+be|equals?))?"
    r"(?:\s+exact(?:ly)?)?"
    r")"
)
_RU_STDOUT_PREDICATE_SRC = (
    r"(?:печат\w*|вывод\w*|возвращ\w*|выда\w*)"
    r"(?:\s+(?:ровно|точно))?"
)
_PATH_STDOUT_RELATION_RE = re.compile(
    r"^\s*(?:[.,;:]\s*)?(?:"
    r"(?:(?:and(?:\s+then)?|then|to|so\s+that)\s+)?"
    r"(?:(?:it|its|the\s+(?:program|script|application)|"
    r"program|script|application)\s+)?"
    r"(?:(?:must|should|shall|will)\s+)?"
    + _EN_STDOUT_PREDICATE_SRC
    + r"|(?:that|which)\s+(?:(?:must|should|shall|will)\s+)?"
    + _EN_STDOUT_PREDICATE_SRC
    + r"|whose\s+(?:exact\s+)?(?:output|stdout|return\s+value)\s+"
    r"(?:is|must\s+be|should\s+be|equals?)(?:\s+exact(?:ly)?)?|"
    r"с\s+точн\w*\s+(?:вывод\w*|результат\w*)|"
    r"(?:(?:и(?:\s+затем)?|затем|чтобы)\s+)?"
    r"(?:(?:он|она|оно|программ\w*|скрипт)\s+)?"
    r"(?:(?:долж\w*|буд(?:ет|ут))\s+)?"
    + _RU_STDOUT_PREDICATE_SRC
    + r"|котор(?:ый|ая|ое|ые)\s+"
    r"(?:(?:долж\w*|буд(?:ет|ут))\s+)?"
    + _RU_STDOUT_PREDICATE_SRC
    + r")\s*(?::|=)?\s*$",
    re.I,
)
_STANDALONE_FILE_LITERAL_CONTEXT_RE = re.compile(
    r"^\s*(?:"
    r"exact(?:ly)?\s+(?:(?:file|artifact)\s+)?(?:content|text|bytes?)|"
    r"(?:(?:file|artifact)\s+)?(?:content|text|bytes?)\s+"
    r"(?:is|must\s+be|should\s+be|equals?|matches?)(?:\s+exact(?:ly)?)?|"
    r"exact\s+byte\s+match|"
    r"точн\w*\s+(?:содержим\w*|текст\w*|байт\w*)|"
    r"(?:содержим\w*(?:\s+файла)?|текст\w*|байт\w*)\s+"
    r"(?:точно|ровно|совпада\w*(?:\s+с)?|"
    r"соответств\w*(?:\s+(?:строк\w*|текст\w*))?)"
    r")\s*(?::|=)?\s*$",
    re.I,
)
_POSTFIX_TARGET_NEGATION_RE = re.compile(
    r"^\s*(?:is|remains?)\s+(?:forbidden|prohibited)\b|"
    r"^\s*(?:must|should)\s+not\s+(?:be\s+)?"
    r"(?:created|written|modified|updated|run)\b|"
    r"^\s*(?:запрещ\w*|нельзя)\b",
    re.I,
)
_PATH_SCALAR_PREDICATE_RE = re.compile(
    r"^\s*(?::\s*)?(?:(?:must|should|shall|will)\s+)?"
    r"(?:add|adds|subtract|subtracts|multiply|multiplies|divide|divides|"
    r"clamp|clamps|round|rounds|format|formats|convert|converts|calculate|"
    r"calculates|compute|computes|emit|emits|output|outputs|print|prints|"
    r"return|returns|yield|yields|parse|parses|validate|validates|"
    r"добав\w*|вычита\w*|умнож\w*|дел\w*|округл\w*|формат\w*|"
    r"преобраз\w*|вычисл\w*|вывод\w*|печат\w*|возвращ\w*|провер\w*)\b",
    re.I,
)
_UNQUOTED_FILE_LITERAL_RE = re.compile(
    r"(?<!\w)(?:with\s+(?:the\s+)?(?:exact\s+)?(?:content|text|bytes?)|"
    r"containing\s+exactly|"
    r"(?:file\s+)?content\s+matches?|"
    r"exact\s+byte\s+match|"
    r"exact(?:\s+(?:file|artifact))?\s+(?:content|text|bytes?)|"
    r"(?:file|artifact)(?:\s+[\w./-]+)?\s+(?:content|text|bytes?)\s+"
    r"(?:is\s+)?exact(?:ly)?|"
    r"с\s+(?:точным[и]?\s+)?(?:текстом|содержимым|байтами)|"
    r"содержимое(?:\s+файла)?\s+совпадает\s+с|"
    r"(?:точн(?:ое|ые|ый)|ровно)\s+(?:содержимое|текст|байты))"
    r"\s*(?::|=|is|must\s+be|matches?\s*(?::|=)?|"
    r"долж(?:но|ен)\s+быть|совпадает\s+с)?\s*"
    r"(?P<value>[^\s,;.!?]+)",
    re.I,
)
_UNQUOTED_STDOUT_LITERAL_RE = re.compile(
    r"(?<!\w)(?:exact(?:\s+)?(?:output|stdout|return\s+value)|"
    r"(?:output|stdout|return\s+value)\s+(?:is\s+)?exact(?:ly)?|"
    r"(?:точн\w*|ровно)\s+вывод\w*)"
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
    "never modify", "never change", "never run", "never create",
    "never write", "never add", "should not modify", "should not change",
    "should not run", "should not create", "should not write", "should not add",
    "avoid modifying", "avoid changing", "avoid creating", "avoid writing",
    "preserve", "не изменять", "не менять", "не запускать", "не создавать",
    "не записывать", "не добавлять", "запрещено изменять", "сохранить без изменений",
)
_CLAUSE_NEGATOR_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:not|never|cannot|can\s+not|can't|don't|doesn't|"
    r"didn't|won't|wouldn't|shouldn't|mustn't|avoid(?:ing)?|without|"
    r"forbidden|prohibited)(?![A-Za-z0-9_])|"
    r"(?<![А-Яа-яЁё0-9_])(?:не|нельзя|никогда|без|избега\w*|запрещ\w*)"
    r"(?![А-Яа-яЁё0-9_])",
    re.I,
)
_ADVERSATIVE_RE = re.compile(
    r"(?<!\w)(?:but|however|instead|yet|но|однако|зато)(?!\w)",
    re.I,
)
_POSITIVE_TARGET_PREFIX_RE = re.compile(
    r"^(?:"
    r"\s*(?:[-*]\s*)?(?:(?:please|kindly|can\s+you|could\s+you|would\s+you)\s+)?"
    r"(?:(?:using|based\s+on)\s+"
    r"(?:[\w./-]+\.[A-Za-z][A-Za-z0-9_-]{0,15})"
    r"(?:\s*(?:,|and)\s*[\w./-]+\.[A-Za-z][A-Za-z0-9_-]{0,15})*"
    r"\s*,\s*)?"
    r"(?:create|write|generate|produce|deliver|make|add|implement|"
    r"fix|update|modify|build|develop|repair|run)\s+"
    r"(?:(?:(?:one|a|an|the|new|single|requested|target|output|"
    r"repository[- ]root|root(?:[- ]level)?|plain[- ]text|text|marker|file|"
    r"artifact|script|module|program|code|bug|issue|support|feature|behavior|"
    r"behaviour|in|at|named|called|and)\s+)|"
    r"(?:[A-Za-z][A-Za-z0-9_+.-]{0,31}[- ](?:script|program)\s+)|"
    r"(?:[\w./-]+\.[A-Za-z][A-Za-z0-9_-]{0,15}\s+))*"
    r"|\s*(?:[-*]\s*)?(?:(?:пожалуйста|можешь|можете)\s+)?"
    r"(?:(?:используя|на\s+основе)\s+"
    r"(?:[\w./-]+\.[A-Za-z][A-Za-z0-9_-]{0,15})"
    r"(?:\s*(?:,|и)\s*[\w./-]+\.[A-Za-z][A-Za-z0-9_-]{0,15})*"
    r"\s*,\s*)?"
    r"(?:созда\w*|напиш\w*|напис\w*|запис\w*|"
    r"сгенерир\w*|добав\w*|реализ\w*|исправ\w*|обнов\w*|измен\w*|"
    r"разработ\w*|запуст\w*)\s+"
    r"(?:(?:(?:один|одну|новый|новую|новое|единственный|запрошенный|"
    r"целев\w*|выходн\w*|корнев\w*|текстов\w*|маркер\w*|файл\w*|артефакт\w*|скрипт\w*|"
    r"модул\w*|программ\w*|код\w*|ошибк\w*|поддержк\w*|функци\w*|в|с|именем|названием|и)"
    r"\s+)|(?:[A-Za-z][A-Za-z0-9_+.-]{0,31}[- ]?(?:скрипт|программ)\w*\s+)|"
    r"(?:[\w./-]+\.[A-Za-z][A-Za-z0-9_-]{0,15}\s+))*"
    r"|\s*explorer\s+(?:found|identified|located)\s+"
    r"(?:(?:the\s+)?(?:implementation|target|code|module)\s+)?(?:in|at)\s+"
    r")$",
    re.I,
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


def _masked_clause_text(text: str) -> str:
    """Hide non-structural dots so sentence boundaries cannot split paths/numbers."""
    masked = list(text)
    for _path, start, end in _goal_path_occurrences(text, code_only=False):
        for index in range(start, end):
            if masked[index] == ".":
                masked[index] = "_"
    for match in re.finditer(r"(?<=\d)\.(?=\d)", text):
        masked[match.start()] = "_"
    return "".join(masked)


def _literal_clause_before(text: str, start: int, *, fenced: bool = False) -> str:
    window = text[max(0, start - 240):start]
    masked = _masked_clause_text(window)
    boundary = max(
        masked.rfind("."), masked.rfind("?"), masked.rfind("!"),
        masked.rfind(";"),
    )
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


def _predicate_scope(context: str) -> str:
    return _ADVERSATIVE_RE.split(context)[-1]


def _negative_literal_context(context: str) -> bool:
    # A comma may only introduce a parenthetical ("do not, under any
    # circumstances, ..."); it is never a safe polarity boundary.  An explicit
    # contrast may start a new predicate and therefore a new scope.
    scoped = _predicate_scope(context)
    return bool(_CLAUSE_NEGATOR_RE.search(scoped)) or any(
        _context_contains_marker(scoped, marker)
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


def _strong_unbound_file_literal_context(context: str) -> bool:
    """Bind only a path-free standalone exact-file authority clause."""
    return (
        not _goal_path_occurrences(context, code_only=False)
        and bool(_STANDALONE_FILE_LITERAL_CONTEXT_RE.fullmatch(context))
    )


def _lf_marker_is_negated(suffix: str, marker_start: int) -> bool:
    prefix = suffix[max(0, marker_start - 96):marker_start]
    # Commas commonly wrap emphatic negation.  Only an explicit predicate
    # boundary resets it; otherwise fail closed.
    scope = re.split(
        r";|(?<!\w)(?:but|however|then|но|однако|затем)(?!\w)",
        prefix,
        flags=re.I,
    )[-1]
    return bool(_CLAUSE_NEGATOR_RE.search(scope))


def _trailing_lf_policy(suffix: str) -> bool | str | None:
    """Return ONE, ZERO, BASE_RAW, or conflict for the bounded literal suffix."""
    if _AMBIGUOUS_TRAILING_LF_RE.search(suffix):
        return None
    zero_lf = any(
        _context_contains_marker(suffix, marker)
        for marker in _NO_TRAILING_LF_MARKERS
    )
    one_lf = False
    negated_one_lf = False
    for marker in _ONE_TRAILING_LF_MARKERS:
        words = r"\s+".join(re.escape(part) for part in marker.split())
        for match in re.finditer(
            rf"(?<![A-Za-z0-9_]){words}(?![A-Za-z0-9_])",
            suffix,
            re.I,
        ):
            if _lf_marker_is_negated(suffix, match.start()):
                negated_one_lf = True
            else:
                one_lf = True
    if one_lf and zero_lf:
        return None
    if one_lf:
        return True
    if zero_lf:
        return False
    if negated_one_lf:
        return None
    return _LF_UNSPECIFIED


def _requires_one_trailing_lf(suffix: str) -> bool:
    return _trailing_lf_policy(suffix) is True


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


def _target_suffix(text: str, path_end: int) -> str:
    suffix = text[path_end:path_end + 180]
    masked = _masked_clause_text(suffix)
    boundaries = [
        index for marker in (".", "?", "!", ";", "\n")
        if (index := masked.find(marker)) >= 0
    ]
    if boundaries:
        suffix = suffix[:min(boundaries)]
    return suffix.casefold()


def _positive_target_occurrence(text: str, path_start: int, path_end: int) -> bool:
    scope = _predicate_scope(_target_prefix(text, path_start))
    if _CLAUSE_NEGATOR_RE.search(scope):
        return False
    if not _POSITIVE_TARGET_PREFIX_RE.search(scope):
        return False
    return not _POSTFIX_TARGET_NEGATION_RE.search(_target_suffix(text, path_end))


def _explicit_scalar_target_occurrence(
    text: str,
    path_start: int,
    path_end: int,
) -> bool:
    if _positive_target_occurrence(text, path_start, path_end):
        return True
    scope = _predicate_scope(_target_prefix(text, path_start))
    suffix = _target_suffix(text, path_end)
    if _CLAUSE_NEGATOR_RE.search(scope) or _POSTFIX_TARGET_NEGATION_RE.search(suffix):
        return False
    return bool(re.search(r"(?<!\w)(?:for|для)\s+$", scope, re.I)) or bool(
        _PATH_SCALAR_PREDICATE_RE.search(suffix)
    )


def _ambiguous_scalar_co_targets(text: str, path_start: int, path_end: int) -> bool:
    """Unlabelled scalar evidence cannot be shared by coordinated output paths."""
    masked = _masked_clause_text(text)
    left = max(
        masked.rfind(".", 0, path_start), masked.rfind("?", 0, path_start),
        masked.rfind("!", 0, path_start), masked.rfind(";", 0, path_start),
        masked.rfind("\n", 0, path_start),
    ) + 1
    right = len(text)
    for marker in (".", "?", "!", ";", "\n"):
        boundary = masked.find(marker, path_end)
        if boundary >= 0:
            right = min(right, boundary)
    for match in _ADVERSATIVE_RE.finditer(text, left, path_start):
        left = match.end()
    following_contrast = _ADVERSATIVE_RE.search(text, path_end, right)
    if following_contrast:
        right = following_contrast.start()
    occurrences = _goal_path_occurrences(text[left:right], code_only=True)
    if len(occurrences) <= 1:
        return False
    owners = sum(
        _explicit_scalar_target_occurrence(text, left + start, left + end)
        for _path, start, end in occurrences
    )
    return owners != 1


def _negative_target_occurrence(text: str, path_start: int, path_end: int) -> bool:
    scope = _predicate_scope(_target_prefix(text, path_start))
    suffix = _target_suffix(text, path_end)
    return bool(_CLAUSE_NEGATOR_RE.search(scope)) or bool(
        _POSTFIX_TARGET_NEGATION_RE.search(suffix)
    )


def _scalar_target_occurrence(text: str, path_start: int, path_end: int) -> bool:
    if _ambiguous_scalar_co_targets(text, path_start, path_end):
        return False
    return _explicit_scalar_target_occurrence(text, path_start, path_end)


def _positive_target_paths(text: str, *, code_only: bool) -> set[str]:
    """Return paths relationally governed by a positive action, never mere inputs."""
    targets: set[str] = set()
    for path, path_start, path_end in _goal_path_occurrences(text, code_only=code_only):
        if _positive_target_occurrence(text, path_start, path_end):
            targets.add(path)
    return targets


def _positive_file_targets(text: str) -> set[str]:
    return _positive_target_paths(text, code_only=False)


def _positive_code_targets(text: str) -> set[str]:
    return _positive_target_paths(text, code_only=True)


def _scalar_labeled_code_targets(text: str) -> set[str]:
    targets: set[str] = set()
    for path, path_start, path_end in _goal_path_occurrences(text, code_only=True):
        if _scalar_target_occurrence(text, path_start, path_end):
            targets.add(path)
    return targets


def _target_evidence_segments(text: str, target: str) -> list[str]:
    occurrences = _goal_path_occurrences(text, code_only=True)
    all_paths = _goal_path_occurrences(text, code_only=False)
    segments: list[str] = []
    masked_text = _masked_clause_text(text)
    for path, path_start, path_end in occurrences:
        if path != target or not _scalar_target_occurrence(text, path_start, path_end):
            continue
        boundaries = (
            masked_text.rfind(".", 0, path_start),
            masked_text.rfind("?", 0, path_start),
            masked_text.rfind("!", 0, path_start),
            masked_text.rfind(";", 0, path_start),
            masked_text.rfind("\n", 0, path_start),
        )
        segment_start = max(boundaries) + 1
        for contrast in _ADVERSATIVE_RE.finditer(text, segment_start, path_start):
            segment_start = contrast.end()
        segment_end = len(text)
        for marker in (".", "?", "!", "\n"):
            boundary = masked_text.find(marker, path_end)
            if boundary >= 0:
                segment_end = min(segment_end, boundary)
        for _next_path, next_start, next_end in all_paths:
            if next_start > path_start:
                if (
                    _scalar_target_occurrence(text, next_start, next_end)
                    or _negative_target_occurrence(text, next_start, next_end)
                    or _ambiguous_scalar_co_targets(text, next_start, next_end)
                ):
                    segment_end = min(segment_end, next_start)
                    break
        # A later explicitly negative continuation cannot lend its scalars to
        # the positive target clause, even before the sentence ends.
        cursor = path_end
        while (separator := masked_text.find(";", cursor, segment_end)) >= 0:
            next_separator = masked_text.find(";", separator + 1, segment_end)
            clause_end = segment_end if next_separator < 0 else next_separator
            continuation = text[separator + 1:clause_end]
            if _CLAUSE_NEGATOR_RE.search(continuation[:120]):
                segment_end = separator
                break
            cursor = separator + 1
        segments.append(text[segment_start:segment_end])
    return segments


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
    if _negative_literal_context(target_context):
        return ""
    if channel == "file_bytes":
        return path if (
            _PATH_FILE_RELATION_RE.fullmatch(relation)
            and path in _positive_file_targets(text)
        ) else ""
    return path if (
        path in _positive_code_targets(text)
        and _PATH_STDOUT_RELATION_RE.fullmatch(relation)
    ) else ""


def _authoritative_literal_policies(goal: str) -> dict[str, dict[str, Any]]:
    """Extract whole task-authored values; never authorize by substring presence."""
    text = str(goal)
    policies: dict[str, dict[str, Any]] = {
        "file_bytes": {"targets": {}, "unbound": {}, "unbound_exact": {}},
        "stdout": {"targets": {}, "unbound": {}, "unbound_exact": {}},
    }

    def merged_policy(old: Any, new: Any) -> Any:
        if old is None or new is None:
            return None
        if old == new:
            return old
        if old == _LF_UNSPECIFIED:
            return new
        if new == _LF_UNSPECIFIED:
            return old
        return None

    def store_policy(table: dict[str, Any], value: str, policy: Any) -> None:
        if value not in table:
            table[value] = policy
        else:
            table[value] = merged_policy(table[value], policy)

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
        one_lf = _trailing_lf_policy(suffix)
        target = _literal_target(text, start, channel)
        if target:
            target_values = policies[channel]["targets"].setdefault(target, {})
            store_policy(target_values, value, one_lf)
        else:
            unbound = policies[channel]["unbound"]
            store_policy(unbound, value, one_lf)
            if channel == "file_bytes" and _strong_unbound_file_literal_context(context):
                exact_unbound = policies[channel]["unbound_exact"]
                store_policy(exact_unbound, value, one_lf)

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
                one_lf = _trailing_lf_policy(suffix)
                target = _literal_target(text, match.start("value"), channel)
                if target:
                    target_values = policies[channel]["targets"].setdefault(target, {})
                    store_policy(target_values, value, one_lf)
                else:
                    unbound = policies[channel]["unbound"]
                    store_policy(unbound, value, one_lf)
                    if (
                        channel == "file_bytes"
                        and _strong_unbound_file_literal_context(context)
                    ):
                        exact_unbound = policies[channel]["unbound_exact"]
                        store_policy(exact_unbound, value, one_lf)

    # A later generic directive may strengthen newline semantics for a pair that
    # was already bound by an authoritative target relation.  It cannot create a
    # new target/value pair.
    for channel in ("file_bytes", "stdout"):
        unbound = policies[channel]["unbound"]
        for values in policies[channel]["targets"].values():
            for literal in set(values) & set(unbound):
                values[literal] = merged_policy(values[literal], unbound[literal])
    return policies


def _authoritative_expected_value(
    expected: object,
    goal: str,
    *,
    exact_bytes: bool,
    target: str,
    target_goal: str,
    precedence_goals: tuple[str, ...] = (),
) -> bool:
    if not isinstance(expected, str):
        return False
    channel = "file_bytes" if exact_bytes else "stdout"

    def target_values(evidence_goal: str) -> dict[str, bool]:
        policies = _authoritative_literal_policies(evidence_goal)
        values = dict(policies[channel]["targets"].get(target) or {})
        if not values and target:
            authoritative_targets = {
                path for path, _start, _end in _goal_path_occurrences(
                    evidence_goal, code_only=channel == "stdout",
                )
            }
            if channel == "stdout":
                fallback_targets = _positive_code_targets(target_goal)
                can_bind_unbound = (
                    not authoritative_targets and fallback_targets == {target}
                )
            else:
                local_positive_targets = _positive_file_targets(evidence_goal)
                fallback_targets = _positive_file_targets(target_goal)
                can_bind_unbound = (
                    local_positive_targets == {target}
                    or (
                        not authoritative_targets
                        and fallback_targets == {target}
                    )
                )
            if can_bind_unbound:
                values = dict(
                    policies[channel][
                        "unbound" if channel == "stdout" else "unbound_exact"
                    ]
                )
        return values

    # Authority is tiered, never unioned.  A real clarification wins the raw
    # commander request for a target/channel it specifies; that request wins a
    # Ceraxia paraphrase.  Lower tiers remain usable only for unspecified targets.
    values: dict[str, bool] = {}
    for precedence_goal in precedence_goals:
        values = target_values(precedence_goal)
        if values:
            break
    if not values:
        values = target_values(goal)
    for literal, one_lf in values.items():
        if one_lf is None:
            continue
        if exact_bytes:
            authorized = (
                literal + "\n"
                if one_lf is True and not literal.endswith("\n")
                else literal
            )
            if expected == authorized:
                return True
        elif expected == literal or (one_lf is True and expected == literal + "\n"):
            return True
    return False


def _private_file_bytes_check(
    candidate: Any,
    goal: str,
    *,
    precedence_goals: tuple[str, ...] = (),
) -> dict[str, Any] | None:
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
            precedence_goals=precedence_goals,
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
        and path.as_posix() in _positive_code_targets(goal)
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
        return path if path in _positive_code_targets(goal) else ""
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
    for path in _positive_code_targets(goal):
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


def _task_scalar_operands(
    goal: str,
    *,
    precedence_goals: tuple[str, ...] = (),
    target: str = "",
    target_goal: str = "",
) -> set[str]:
    def target_scoped_text(evidence_goal: str) -> str:
        if not target:
            return evidence_goal
        labeled_targets = _scalar_labeled_code_targets(evidence_goal)
        if target in labeled_targets:
            return "\n".join(_target_evidence_segments(evidence_goal, target))
        mentioned_targets = {
            path for path, _start, _end in _goal_path_occurrences(
                evidence_goal, code_only=True,
            )
        }
        if (
            not mentioned_targets
            and _positive_code_targets(target_goal) == {target}
        ):
            return evidence_goal
        return ""

    def literal_operands(evidence_goal: str) -> set[str]:
        found: set[str] = set()
        policies = _authoritative_literal_policies(evidence_goal)
        scoped = target_scoped_text(evidence_goal)
        scoped_policies = (
            _authoritative_literal_policies(scoped) if scoped else None
        )
        for channel in ("file_bytes", "stdout"):
            if target:
                found.update(policies[channel]["targets"].get(target) or {})
            else:
                for values in policies[channel]["targets"].values():
                    found.update(values)
            if not target:
                found.update(policies[channel]["unbound"])
            elif scoped_policies is not None:
                found.update(scoped_policies[channel]["unbound"])
        return found

    def numeric_operands(evidence_goal: str) -> set[str]:
        scoped = target_scoped_text(evidence_goal)
        if not scoped:
            return set()
        return set(re.findall(
            r"(?<![\w.])-?\d+(?:\.\d+)?(?!\w|\.\d)", scoped,
        ))

    def format_separators(evidence_goal: str) -> set[str]:
        found: set[str] = set()
        scoped = target_scoped_text(evidence_goal)
        if not scoped:
            return found
        lowered = scoped.casefold()
        for scalar, markers in formatting_markers:
            if any(marker in lowered for marker in markers):
                found.add(scalar)
        return found

    formatting_markers = (
        (" ", ("space-separated", "separated by spaces", "separated by a space", "разделены пробелом")),
        (",", ("comma-separated", "separated by commas", "separated by a comma", "разделены запятыми")),
        ("\n", ("newline-separated", "separated by newlines", "one per line", "каждый с новой строки")),
    )
    operands: set[str] = set()
    for extractor in (literal_operands, numeric_operands, format_separators):
        selected: set[str] = set()
        for precedence_goal in precedence_goals:
            selected = extractor(precedence_goal)
            if selected:
                break
        operands.update(selected or extractor(goal))
    return operands


def _oracle_scalar_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)


def _private_oracle_for_check(
    command: str,
    candidate_command: str,
    authoritative_goal: str,
    *,
    precedence_goals: tuple[str, ...] = (),
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
    candidate_target = _private_candidate_target(candidate_command, authoritative_goal)
    operands = _candidate_test_operands(candidate_command) | _task_scalar_operands(
        authoritative_goal,
        precedence_goals=precedence_goals,
        target=candidate_target,
        target_goal=authoritative_goal,
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


def _check_rejection(
    index: int,
    code: str,
    what_failed: str,
    evidence: str,
    expected: str,
    remediation: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "what_failed": what_failed,
        "evidence": evidence,
        "expected": expected,
        "remediation": remediation,
        "revision_owner": "infrastructure",
        "retryable": True,
        "entity_kind": "private_check",
        "entity_id": f"candidate-{index}",
    }


def _structured_checks_with_diagnostics(
    raw_checks: Any, *, allow_bare: bool, goal: str = "",
    file_evidence_goal: str | None = None,
    primary_evidence_goals: tuple[str, ...] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate checks while preserving why every rejected candidate failed."""

    checks: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    if not isinstance(raw_checks, list):
        return checks, [_check_rejection(
            1,
            "checks_not_array",
            "The verifier response did not contain a checks array.",
            f"Observed {type(raw_checks).__name__} instead of a JSON array.",
            "A JSON array of structured check objects.",
            "Return checks as an array and keep each command or file assertion in its own object.",
        )]

    authoritative_goal = file_evidence_goal if file_evidence_goal is not None else goal
    for index, candidate in enumerate(raw_checks, 1):
        file_check = (
            _private_file_bytes_check(
                candidate,
                authoritative_goal,
                precedence_goals=primary_evidence_goals,
            )
            if not allow_bare else None
        )
        if file_check is not None:
            checks.append(file_check)
            continue
        if isinstance(candidate, dict) and candidate.get("kind") == "file_bytes":
            rejections.append(_check_rejection(
                index,
                "file_evidence_not_authorized",
                "The exact-file assertion is not linked to an authoritative task path and literal.",
                "The path, bytes, or newline policy could not be proven from the commander request.",
                "A task-named relative file and exact task-authored UTF-8 bytes.",
                "Copy the exact path and literal from the task; do not infer content or a trailing newline.",
            ))
            continue
        if isinstance(candidate, dict) and str(candidate.get("cmd") or "").strip():
            check = {"cmd": str(candidate["cmd"])}
            candidate_target = _private_candidate_target(check["cmd"], goal)
            supplied_literal = isinstance(candidate.get("expect_stdout"), (str, int, float))
            if supplied_literal:
                check["expect_stdout"] = str(candidate["expect_stdout"])
            if str(candidate.get("oracle") or "").strip():
                check["oracle"] = str(candidate["oracle"])
            literal_authorized = (
                "expect_stdout" in check
                and _authoritative_expected_value(
                    check["expect_stdout"], authoritative_goal, exact_bytes=False,
                    target=candidate_target, target_goal=goal,
                    precedence_goals=primary_evidence_goals,
                )
            )
            if not allow_bare and "expect_stdout" in check and not literal_authorized:
                check.pop("expect_stdout", None)
            expected_output = str(check.get("expect_stdout") or "").strip()
            oracle_command = str(check.get("oracle") or "").strip()
            output_evidence = bool(expected_output or oracle_command)
            candidate_linked = allow_bare or _private_candidate_probe(
                str(check["cmd"]), goal
            )
            oracle_authorized = (
                not oracle_command
                or allow_bare
                or _private_oracle_for_check(
                    oracle_command, str(check["cmd"]), authoritative_goal,
                    precedence_goals=primary_evidence_goals,
                )
            )
            if allow_bare or (output_evidence and candidate_linked and oracle_authorized):
                if not allow_bare:
                    check["cmd"] = _canonical_private_candidate(str(check["cmd"]))
                if not allow_bare and oracle_command:
                    check["oracle"] = _canonical_private_oracle(oracle_command)
                checks.append(check)
                continue
            if not candidate_linked:
                rejections.append(_check_rejection(
                    index,
                    "candidate_not_task_linked",
                    "The command does not directly execute a positively requested deliverable.",
                    "No safe runtime/path binding was found in the authoritative task wording.",
                    "A direct python3/node/php invocation of a task-named relative code file.",
                    "Run the exact deliverable path named by the task without shell wrappers.",
                ))
            elif supplied_literal and not literal_authorized and not oracle_command:
                rejections.append(_check_rejection(
                    index,
                    "expected_output_not_authorized",
                    "The expected stdout was not stated as authoritative task output.",
                    "The proposed literal could not be bound to the candidate target.",
                    "A literal copied exactly from the task or an independent computational oracle.",
                    "Copy an explicit expected value from the task, otherwise replace it with a pure oracle.",
                ))
            elif not output_evidence:
                rejections.append(_check_rejection(
                    index,
                    "behavioural_evidence_missing",
                    "The check only proves that the candidate exits successfully.",
                    "No authorized expected stdout or independent oracle remained after validation.",
                    "At least one immutable behavioural output assertion.",
                    "Add task-authored expect_stdout or a pure independently computed oracle.",
                ))
            else:
                rejections.append(_check_rejection(
                    index,
                    "oracle_not_authorized",
                    "The proposed oracle is not independent and inside the positive grammar.",
                    "The oracle shape or its operands could not be derived safely from the task.",
                    "A pure interpreter expression using only task-authored operands.",
                    "Return a single pure print(expression) oracle with no candidate imports or filesystem access.",
                ))
        elif allow_bare and isinstance(candidate, str) and candidate.strip():
            checks.append({"cmd": candidate})
        else:
            rejections.append(_check_rejection(
                index,
                "check_shape_invalid",
                "The check is not a supported structured command or file assertion.",
                "The candidate lacks a non-empty cmd or a valid file_bytes shape.",
                "A JSON object with cmd plus output evidence, or a file_bytes assertion.",
                "Return one supported structured check object and no shell pipeline string.",
            ))
    return checks, rejections


def _structured_checks(
    raw_checks: Any, *, allow_bare: bool, goal: str = "",
    file_evidence_goal: str | None = None,
    primary_evidence_goals: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    checks, _rejections = _structured_checks_with_diagnostics(
        raw_checks,
        allow_bare=allow_bare,
        goal=goal,
        file_evidence_goal=file_evidence_goal,
        primary_evidence_goals=primary_evidence_goals,
    )
    return checks


def _arbiter_chat_json(prompt: str) -> dict[str, Any]:
    """Independent arbiter on the PLANNER model (gemma), not the fighter's Qwen.

    A separate head judges whether a failing check is itself broken; the coder
    cannot be its own judge (that is how false-accepts sneak in)."""
    base = os.environ.get(
        "SKITARII_ARBITER_LLM_BASE_URL",
        os.environ.get("PLANNER_LLM_BASE_URL", "http://127.0.0.1:8079/v1"),
    ).rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    payload = {
        "model": os.environ.get(
            "SKITARII_ARBITER_LLM_MODEL",
            os.environ.get("PLANNER_LLM_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf"),
        ),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": float(os.environ.get("SKITARII_ARBITER_TEMPERATURE", "0.2")),
        "max_tokens": 1200,
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as resp:
        content = str(((json.loads(resp.read()).get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    return _first_json_object(content)


def arbitrate_failed_checks(goal: str, failures: list[dict], *,
                            deliverables: list[str] | None = None) -> list[dict]:
    """Judge each failing acceptance check: is the CHECK broken, or the code?

    The fighter is powerless to change checks and burns every round against a
    check that is itself wrong (an unspecified tie-break order, a literal
    expect_stdout the spec author guessed wrong, a grep for one valid phrasing).
    A separate arbiter (planner model) rules per check and, when the check is
    genuinely broken, returns a REPLACEMENT that is still behavioural — it may
    de-brittle or fully specify a check, never weaken it to a trivial pass.

    Returns a list aligned by `cmd`: [{"cmd", "verdict", "reason", "replacement"?}].
    Replacements are validated (non-empty, still behavioural, not a bare
    presence check) before the caller may swap them in."""
    if not failures:
        return []
    items = []
    for f in failures[:8]:
        items.append({
            "cmd": str(f.get("target") or f.get("cmd") or ""),
            "why": str(f.get("why") or f.get("stderr") or "failed")[:400],
            "got": str(f.get("stdout") or "")[:300],
            "expected": str(f.get("expected") or "")[:300],
        })
    prompt = (
        "You are the acceptance ARBITER of a coding warband. A coder's work FAILED these "
        "acceptance checks. Some checks may themselves be WRONG: an under-specified "
        "expected output (e.g. tie-break order never stated), a literal expect_stdout the "
        "author guessed incorrectly, or a check that greps for one valid phrasing of a "
        "correct-but-different implementation. Others are legitimately the coder's fault.\n"
        "Rule on EACH check. Return ONE JSON array, one object per check, nothing else:\n"
        '[{"cmd": "<the exact cmd>", "verdict": "check_broken" | "fighter_at_fault", '
        '"reason": "<short>", "replacement": {"cmd": "...", "expect_stdout"|"oracle": "..."} | null}]\n'
        "Rules for a replacement (ONLY when verdict is check_broken):\n"
        "- It MUST still be behavioural: run the deliverable and judge its OUTPUT with "
        "expect_stdout or an oracle command; or a real test run. NEVER a bare no-op, "
        "NEVER grep of source text, NEVER something that passes without exercising the code.\n"
        "- Fix the actual defect: fully specify the order/format, or use an oracle "
        "(python3 -c ...) so the real interpreter is the source of truth, not a guess.\n"
        "- If the check is fine and the code is wrong, verdict fighter_at_fault, replacement null.\n\n"
        f"TASK:\n{goal}\n\n"
        + ("DELIVERABLES:\n" + "\n".join(deliverables[:20]) + "\n\n" if deliverables else "")
        + "FAILED CHECKS:\n" + json.dumps(items, ensure_ascii=False, indent=1)
    )
    try:
        raw = _arbiter_chat_json(prompt)
    except Exception:
        return []
    rulings = raw if isinstance(raw, list) else raw.get("rulings") if isinstance(raw, dict) else None
    if not isinstance(rulings, list):
        return []
    out: list[dict] = []
    for r in rulings:
        if not isinstance(r, dict):
            continue
        verdict = str(r.get("verdict") or "").strip()
        cmd = str(r.get("cmd") or "").strip()
        if not cmd or verdict not in ("check_broken", "fighter_at_fault"):
            continue
        ruling = {"cmd": cmd, "verdict": verdict, "reason": str(r.get("reason") or "")[:300]}
        if verdict == "check_broken":
            repl = r.get("replacement")
            valid = _structured_checks([repl], allow_bare=True, goal=goal) if isinstance(repl, dict) else []
            # A replacement must survive validation, stay non-brittle, and remain
            # behavioural — otherwise the arbiter would be weakening acceptance.
            if (valid and not _is_brittle_presence_check(valid[0])
                    and check_kind(valid[0]) in ("behavior", "test")):
                ruling["replacement"] = valid[0]
            else:
                # No safe replacement -> do not silently drop the check; keep it,
                # downgrade the ruling so the caller does not swap anything in.
                ruling["verdict"] = "fighter_at_fault"
                ruling["reason"] = "check flagged broken but no safe behavioural replacement"
        out.append(ruling)
    return out


def build_held_out_plan(
    goal: str,
    *,
    task_goal: str | None = None,
    primary_task_goals: tuple[str, ...] = (),
) -> dict[str, Any]:
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
    rejections: list[dict[str, Any]] = []
    for attempt in range(2):
        try:
            payload = _held_out_chat_json(prompt)
            saw_payload = True
        except Exception as exc:
            generator_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            payload = {}
        checks, rejections = _structured_checks_with_diagnostics(
            payload.get("checks"), allow_bare=False, goal=goal,
            file_evidence_goal=evidence_goal,
            primary_evidence_goals=primary_task_goals,
        )
        checks = checks[:3]
        if checks:
            return {"status": "ok", "checks": checks, "error": ""}
        if attempt == 0:
            rejection_feedback = json.dumps(
                rejections[:8], ensure_ascii=False, separators=(",", ":"),
            )
            prompt += (
                "\n\nREPAIR: your previous reply was rejected by the strict verifier grammar. Return only "
                "corrected JSON. Here are the exact validator findings; repair them rather than guessing:\n"
                + rejection_feedback
                + "\nA Python oracle shell command has exactly three argv: python3, -c, and one "
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
            "findings": [_check_rejection(
                1,
                "private_verifier_unavailable",
                "The private-check generator could not return a response.",
                generator_error,
                "A valid structured private verification plan.",
                "Retry the verifier generator; until it recovers, use independent public behavioural replay with degraded assurance.",
            )],
        }
    return {
        "status": "invalid_spec", "checks": [],
        "error": "private verifier produced no candidate-linked behavioural checks",
        "findings": rejections or [_check_rejection(
            1,
            "private_verifier_no_valid_checks",
            "The private verifier could not produce a candidate-linked behavioural check.",
            "Both bounded generator responses were rejected by the positive grammar.",
            "At least one safe task-linked check with immutable output evidence.",
            "Regenerate the checks from the exact commander wording or continue with public behavioural replay at degraded assurance.",
        )],
    }


def build_held_out_checks(goal: str) -> list[dict[str, Any]]:
    """Compatibility helper for callers that only need the private checks."""
    return list(build_held_out_plan(goal).get("checks") or [])


_INSPECTION_HEADS = {
    "grep", "egrep", "fgrep", "rg", "ag", "test", "[", "ls", "cat",
    "stat", "file", "find", "head", "tail", "wc", "readlink", "realpath",
}


def _is_brittle_presence_check(check: dict) -> bool:
    """True for a bare command whose FIRST token merely inspects files (grep/test/
    ls/cat…). Such a check proves a file exists or literally contains some text —
    NOT that the code works — so it makes valid-but-differently-written code fail
    acceptance forever (e.g. grep 'apply plugin' rejects a modern plugins{} block).
    A check that RUNS the deliverable and greps its OUTPUT starts with the program,
    not with grep, and an expect_stdout/oracle check asserts computed output — both
    are behavioural and kept. Only leading file-inspection with no output evidence
    is dropped."""
    if not isinstance(check, dict):
        return True
    if str(check.get("expect_stdout") or "").strip() or str(check.get("oracle") or "").strip():
        return False
    cmd = str(check.get("cmd") or "").strip()
    if not cmd:
        return True
    # `cd dir && grep ...` is still a grep: strip leading cd-prefixes before looking
    # at the head, or every inspection check hides behind a cd (they did).
    while True:
        stripped = re.sub(r"^cd\s+[\w.\-/]+\s*&&\s*", "", cmd)
        if stripped == cmd:
            break
        cmd = stripped
    try:
        head = shlex.split(cmd)[0]
    except (ValueError, IndexError):
        head = cmd.split()[0] if cmd.split() else ""
    return head.rsplit("/", 1)[-1] in _INSPECTION_HEADS


# (signal substrings in goal/deliverables, canonical build command). The real build
# succeeding IS the behavioural acceptance of a whole-project task — not a grep for
# config strings that the sandbox cannot even prove correct.
_BUILD_ECOSYSTEMS: tuple[tuple[tuple[str, ...], str], ...] = (
    # Self-contained: the acceptor runs with a bare PATH, so the check itself finds
    # the toolchain (persistent /opt in the sandbox) before falling back to PATH.
    (("build.gradle", "gradlew", "android", "androidmanifest", "gradle", ".kt"),
     'TC=/opt/skitarii-toolchain; [ -d "$TC/jdk" ] && export JAVA_HOME="$TC/jdk"; '
     '[ -d "$TC/android-sdk" ] && export ANDROID_HOME="$TC/android-sdk" ANDROID_SDK_ROOT="$TC/android-sdk"; '
     'export PATH="$TC/jdk/bin:$TC/gradle/bin:$PATH"; '
     "if [ -x ./gradlew ]; then ./gradlew assembleDebug --stacktrace; "
     "else gradle assembleDebug --stacktrace; fi"),
    (("package.json", "node_modules", "react", "vite", "webpack", ".ts", "tsconfig"),
     "npm ci 2>/dev/null || npm install; npm run build --if-present && (npm test --if-present || true)"),
    (("cargo.toml", ".rs"), "cargo build --release && cargo test"),
    (("pom.xml",), "mvn -q -B package"),
    (("go.mod",), "go build ./... && go test ./..."),
    (("cmakelists",), "cmake -S . -B build && cmake --build build"),
    (("makefile",), "make"),
)


def _detect_build_check(goal: str, deliverables: list[str]) -> dict | None:
    hay = (goal + "\n" + "\n".join(deliverables)).lower()
    for signals, command in _BUILD_ECOSYSTEMS:
        if any(s in hay for s in signals):
            # Behavioural by construction: build output goes to stderr (recorded for
            # diagnosis), stdout carries only the success token, so the acceptor's
            # structural gate sees an expect_stdout check, not a weak bare "run".
            return {"cmd": f"({command}) 1>&2 && echo BUILD_OK",
                    "expect_stdout": "BUILD_OK"}
    return None


_BUILDISH_CMD_RE = re.compile(
    r"gradlew?\b|assemble|npm run build|cargo build|mvn\b|cmake|go build|(^|[;&]\s*)make\b"
)


_CHECK_CD_RE = re.compile(r"^cd\s+([\w.\-/]+)\s*&&\s*")

def workspace_file_listing(executor: Any, limit: int = 80) -> list[str]:
    """Existing source files of the (possibly inherited) workspace, for spec grounding."""
    try:
        res = executor.bash(
            "find . -type f -not -path './.git/*' -not -path './build/*' "
            "-not -path './.gradle/*' -not -path './node_modules/*' "
            f"| sed 's|^\\./||' | sort | head -{int(limit)}",
            timeout=60,
        )
    except Exception:
        return []
    if res.get("returncode") != 0:
        return []
    return [line.strip() for line in str(res.get("stdout") or "").splitlines() if line.strip()]


_PROJECT_ROOT_MARKERS = (
    "settings.gradle", "settings.gradle.kts", "gradlew", "package.json",
    "Cargo.toml", "pom.xml", "go.mod", "CMakeLists.txt", "Makefile",
)


def _strip_project_wrapper(deliverables: list[str]) -> list[str]:
    """Drop a single invented wrapper directory from deliverable paths.

    Every mission owns an isolated workspace whose ROOT is the project root, and
    workspace inheritance restores previous work at that root. A spec that wraps
    everything in `Galaga/` fights both: the fighter continues at the root while
    the acceptance looks inside the wrapper. Strip the wrapper only when it is
    provably one — all deliverables share a single top directory that directly
    contains a project-root marker (settings.gradle, package.json, …); an `app/`
    module dir has no such marker and stays untouched."""
    tops = {d.strip("/").split("/", 1)[0] for d in deliverables if d.strip("/")}
    if len(tops) != 1:
        return deliverables
    top = next(iter(tops))
    if not all("/" in d.strip("/") for d in deliverables):
        return deliverables
    rooted = {d.strip("/").split("/", 1)[1] for d in deliverables}
    if not any(r in _PROJECT_ROOT_MARKERS for r in rooted):
        return deliverables
    return sorted(rooted)


def _align_check_dirs(checks: list[dict], deliverables: list[str]) -> list[dict]:
    """Rewrite `cd <dir> && ...` in checks whose dir contradicts the deliverables.

    A real top-level spec demanded deliverables under GalagaApp/ while its checks ran
    `cd Galaga && ./gradlew ...` — no repair round can satisfy that: moving files to
    either name still fails the other half. The deliverables are the contract, so a
    cd into a directory no deliverable lives in is redirected to the single directory
    they share (or dropped from the command when they live at the workspace root)."""
    tops = {d.strip("/").split("/", 1)[0] for d in deliverables if d.strip("/")}
    rooted = {t for t in tops if "." not in t}  # bare files at root are not dirs
    # A root marker among the deliverables (settings.gradle, package.json, …) means
    # the project lives AT the workspace root: an invalid cd must be dropped, not
    # redirected into some module dir where the build wrapper does not exist.
    root_project = any(d.strip("/") in _PROJECT_ROOT_MARKERS for d in deliverables)
    aligned: list[dict] = []
    for check in checks:
        cmd = str(check.get("cmd") or "")
        m = _CHECK_CD_RE.match(cmd)
        if m and deliverables:
            cd_dir = m.group(1).strip("/").split("/", 1)[0]
            if cd_dir not in tops:
                if root_project or not rooted:
                    check = {**check, "cmd": cmd[m.end():]}
                elif len(rooted) == 1:
                    fixed = f"cd {next(iter(rooted))} && " + cmd[m.end():]
                    check = {**check, "cmd": fixed}
        aligned.append(check)
    return aligned


def _canonicalize_build_expect(check: dict) -> dict:
    """Build command + expect_stdout = a check that fails even on success.

    expect_stdout compares the WHOLE normalized stdout, but a build prints pages
    (an attempt with a finished gameplay layer died on `gradlew assembleDebug`
    vs expect 'BUILD SUCCESSFUL' — got gradle's JVM banner). Rewrite to the
    canonical form: build output to stderr, stdout carries only the token, exit
    code stays the oracle."""
    cmd = str(check.get("cmd") or "")
    if ("expect_stdout" not in check or "oracle" in check
            or "echo BUILD_OK" in cmd or not _BUILDISH_CMD_RE.search(cmd)):
        return check
    return {"cmd": f"({cmd}) 1>&2 && echo BUILD_OK", "expect_stdout": "BUILD_OK"}


def _as_build_oracle(check: dict) -> dict:
    """Turn a bare build command into a behavioural check (success token on stdout).

    Last-resort repair for specs whose checks the acceptor's structural gate would
    reject as compile/run-only: the fighter cannot change the checks at accept time,
    so an unpassable spec must be fixed here. Only build-like bare commands are
    upgraded — everything else is returned untouched."""
    cmd = str(check.get("cmd") or "")
    if ("expect_stdout" in check or "oracle" in check or check.get("kind") == "file_bytes"
            or not _BUILDISH_CMD_RE.search(cmd)):
        return check
    return {"cmd": f"({cmd}) 1>&2 && echo BUILD_OK", "expect_stdout": "BUILD_OK"}


def build_spec(goal: str, *, build_project: bool = False,
               existing_files: list[str] | None = None) -> dict[str, Any]:
    """Returns {"deliverables": [...], "checks": [...]}.

    A check is a STRUCTURED dict (never a hand-written shell one-liner — the model
    mangles quoting/substitution). The acceptor turns it into a correct command:
      {"cmd": "..."}                          -> pass iff exit code 0
      {"cmd": "...", "expect_stdout": "14"}   -> pass iff trimmed stdout == "14"
      {"cmd": "...", "oracle": "python3 -c 'print(3*(5+2)-8/4)'"}
                                              -> pass iff stdout == oracle's stdout

    ``build_project`` marks a WHOLE-project task (not a scaffolding subtask): its true
    acceptance is that the project actually builds, so the ecosystem build command is
    injected as the primary behavioural gate.
    """
    prompt = (
        "You prepare acceptance for a coding task. Return ONE strict JSON object and nothing else:\n"
        '{"deliverables": ["relative file paths the task must produce"],\n'
        ' "checks": [ {"cmd": "command that runs the program"},\n'
        '             {"cmd": "...", "expect_stdout": "literal expected output"},\n'
        '             {"cmd": "...", "oracle": "command that computes the correct answer"} ],\n'
        ' "product": {"kind": "cli|server|android|web|library|none",\n'
        '             "run": ["commands that exercise the finished product on real input"],\n'
        '             "start": "server start command, when kind=server",\n'
        '             "endpoints": ["urls to probe, when kind=server"]},\n'
        ' "quality_contract": ["3-7 product-quality criteria a demanding owner would check"]}\n'
        "quality_contract is the bar ABOVE minimal function: for a game — drawn sprites (not"
        " colored rectangles), difficulty progression, sound, restart; for a CLI — helpful --help,"
        " honest exit codes, readable errors; for a server — sane error responses, input validation."
        " Concrete and checkable by looking at the running product; no vague words like 'good UX'.\n"
        "Rules for checks:\n"
        "- Each check is a JSON object, NOT a shell string. Do not write pipes/grep/test yourself.\n"
        "- 'cmd' is a single command that RUNS or BUILDS the deliverable (e.g. \"python3 calc.py '2+3*4'\",\n"
        "  \"./gradlew assembleDebug\", \"npm run build\", \"cargo test\").\n"
        "- FORBIDDEN: acceptance by inspecting file contents (grep/test/ls/cat/find, OR a python/node script\n"
        "  that opens a deliverable and asserts on its text) for a literal string or path. That only proves a\n"
        "  file holds some text — not that the code works — and wrongly rejects valid code written differently\n"
        "  (a modern plugins{} block, a renamed symbol, an attribute set in code not XML). Acceptance MUST\n"
        "  execute the deliverable: build it, run it, or run its tests, and judge the RESULT.\n"
        "- NEVER put an answer you computed in your head into expect_stdout — you make arithmetic mistakes.\n"
        "  Use expect_stdout ONLY for values written literally in the task text.\n"
        "- For anything you'd have to compute, use 'oracle': a command (python3 -c ... / node -e ...) that\n"
        "  produces the correct answer, so the real interpreter is the source of truth, not you.\n"
        "- A compile/build check alone is weak for logic; add a BEHAVIOURAL check (expect_stdout or oracle on\n"
        "  real inputs, or the project's tests) covering the main case and one edge case where feasible.\n"
        "- The workspace root IS the project root: build.gradle/settings.gradle/package.json belong at the\n"
        "  TOP level of deliverable paths. Do NOT invent a wrapper directory (no 'MyApp/...' prefix), and\n"
        "  checks must not cd anywhere — they already run at the project root. One layout, used everywhere.\n"
        "Keep 2-6 checks, runnable on bare Ubuntu with php, python3, node.\n\n"
        + (
            # Inherited-workspace grounding: without it the spec invents its own
            # language/layout each attempt (a Java deliverable list once buried a
            # finished Kotlin project) and acceptance demands files nobody planned.
            "The workspace ALREADY CONTAINS these files (work inherited from a previous "
            "attempt):\n" + "\n".join(existing_files[:80]) + "\n"
            "Deliverables MUST fit this existing layout: same language, same paths — "
            "extend or fix what exists, never demand a parallel layout or a language "
            "switch.\n\n"
            if existing_files else ""
        )
        + f"TASK:\n{goal}"
    )
    try:
        spec = _chat_json(prompt)
    except Exception:
        spec = {}
    deliverables = [str(d) for d in (spec.get("deliverables") or []) if isinstance(d, str)]
    deliverables = _strip_project_wrapper(deliverables)

    def _cleaned_checks(raw: object) -> list[dict]:
        parsed = _structured_checks(raw, allow_bare=True, goal=goal)
        # Drop brittle file-inspection checks: they assert a file contains a literal,
        # not that the code works, and loop forever against valid-but-different code.
        parsed = [c for c in parsed if not _is_brittle_presence_check(c)]
        # Force path coherence: checks must cd into the deliverables' directory.
        parsed = _align_check_dirs(parsed, deliverables)
        # And a passable shape for build checks: token on stdout, log on stderr.
        return [_canonicalize_build_expect(c) for c in parsed]

    checks = _cleaned_checks(spec.get("checks"))
    # SPEC-TIME structural gate. The acceptor rejects compile/run-only check sets AND
    # empty ones, but it does so at ACCEPT time — when the fighter can no longer change
    # the checks, so a weak/empty spec is an unpassable mission (one subtask burned all
    # its rounds against "Add an expect_stdout/oracle" advice addressed to nobody;
    # another died with every check dropped by the brittle filter and .kt deliverables
    # no syntax fallback knows). Repair it here instead: one regeneration with explicit
    # feedback, then a build-as-oracle fallback.
    def _has_functional(cs: list[dict]) -> bool:
        return bool({check_kind(c) for c in cs} & {"behavior", "test"})

    if not _has_functional(checks):
        if checks:
            complaint = (
                "\n\nPREVIOUS ATTEMPT REJECTED: every check was compile/run-only — no behavioural"
                " or functional test, so wrong logic could pass."
            )
        else:
            complaint = (
                "\n\nPREVIOUS ATTEMPT REJECTED: no usable check survived — checks that inspect"
                " file contents (grep/cat or scripts asserting on source text) are forbidden"
                " and were dropped."
            )
        feedback = complaint + (
            " Add at least one check with expect_stdout"
            " or oracle that runs the deliverable on real input. If the task is pure"
            " configuration/scaffolding with no observable stdout, make the BUILD the oracle:"
            ' {"cmd": "(<build command>) 1>&2 && echo BUILD_OK", "expect_stdout": "BUILD_OK"}.'
        )
        try:
            retry_checks = _cleaned_checks(_chat_json(prompt + feedback).get("checks"))
        except Exception:
            retry_checks = []
        if _has_functional(retry_checks):
            checks = retry_checks
        elif checks:
            checks = [_as_build_oracle(c) for c in checks]
        elif retry_checks:
            checks = [_as_build_oracle(c) for c in retry_checks]
    if build_project:
        build_check = _detect_build_check(goal, deliverables)
        if build_check is not None and build_check not in checks:
            # The real build succeeding is THE behavioural gate for the whole task.
            checks = [build_check, *checks]
    # Fallback: the model gave files but no runnable checks survived. Never return an
    # empty set (that accepts having proven nothing). Assert each deliverable at least
    # parses / is well-formed — validity, not a magic string.
    if not checks and deliverables:
        _syntax = {
            ".py": "python3 -m py_compile {p}", ".php": "php -l {p}",
            ".js": "node --check {p}", ".sh": "bash -n {p}",
            ".json": "python3 -c \"import json;json.load(open({p!r}))\"",
            ".xml": "python3 -c \"import xml.dom.minidom as m;m.parse({p!r})\"",
        }
        for d in deliverables:
            ext = "." + d.rsplit(".", 1)[-1] if "." in d else ""
            if ext in _syntax:
                checks.append({"cmd": _syntax[ext].format(p=d)})
    # Last net: still nothing runnable (e.g. .kt/.java deliverables the syntax map
    # doesn't know). An empty set is an unpassable mission by construction, so fall
    # back to the ecosystem build — for JVM/Android sources "the project builds" is
    # the weakest honest oracle that still executes the code.
    if not checks:
        fallback_build = _detect_build_check(goal, deliverables)
        if fallback_build is not None:
            checks = [fallback_build]
    quality_contract = [
        str(c).strip() for c in (spec.get("quality_contract") or [])
        if isinstance(c, str) and str(c).strip()
    ][:7]
    product = normalize_profile(spec.get("product"), goal, deliverables)
    return {"deliverables": deliverables, "checks": checks,
            "quality_contract": quality_contract, "product": product}
