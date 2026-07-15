"""Capability smoke evaluation — 30 fixed, deliberately small tasks across the
categories that matter, plus a runner that scores them. This is a smoke suite, not a
full real-repository benchmark. The headline metric is FALSE ACCEPTED = 0%: the
warband must never report success on work that doesn't pass an independent oracle.

Each task: id, category, goal, oracle_checks (private checks used only by the RUNNER to
decide truth), and optional flags (expects_clarification, forbid_touch). Oracle checks
are intentionally never sent to the service under evaluation.

Run a subset live (needs the Skitarii service up):
    SKITARII_STANDALONE_TEST_MODE=1 must be set on the service daemon.
    The runner marks every request with standalone_test=true; both gates are
    required so evaluation cannot become a production bypass.
    python3 eval_suite.py --n 3

Run one clean complete smoke suite (the only suitable source for a raw result file):
    python3 eval_suite.py --n 0 --out eval_results.json
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import tomllib
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SERVICE = os.environ.get("SKITARII_URL", "http://127.0.0.1:7200")
SUITE_KIND = "capability_smoke"
MAX_SERVICE_RESPONSE_BYTES = 32_000_000

# The patch is supplied by the system under evaluation, so it must be treated as
# compressed, attacker-controlled input until both its declarations and the
# independently materialized tree have passed these bounds.
MAX_PATCH_INPUT_BYTES = 20_000_000
MAX_PATCH_FILES = 1_000
MAX_PATCH_FILE_BYTES = 20_000_000
MAX_PATCH_EXPANDED_BYTES = 100_000_000
MAX_WORKSPACE_FILES = 5_000
MAX_WORKSPACE_FILE_BYTES = 20_000_000
MAX_WORKSPACE_TOTAL_BYTES = 200_000_000
AMBIGUOUS_CANCEL_TIMEOUT_SEC = 120.0
AMBIGUOUS_CANCEL_POLL_INTERVAL_SEC = 0.25

# --- 30 tasks: 5 per category ----------------------------------------------------
TASKS: list[dict] = []


def _t(cat, tid, goal, checks, **flags):
    TASKS.append({"id": tid, "category": cat, "goal": goal, "oracle_checks": checks, **flags})


# 1) greenfield — small new programs
_t("greenfield", "gf1", "Напиши python fizzbuzz.py: печатает FizzBuzz 1..15 через пробел в одну строку.",
   [{"cmd": "python3 fizzbuzz.py", "oracle": "python3 -c \"print(' '.join('FizzBuzz' if i%15==0 else 'Fizz' if i%3==0 else 'Buzz' if i%5==0 else str(i) for i in range(1,16)))\""}])
_t("greenfield", "gf2", "Напиши python rev.py: печатает аргумент задом наперёд. python3 rev.py abc -> cba.",
   [{"cmd": "python3 rev.py abc", "expect_stdout": "cba"},
    {"cmd": "python3 rev.py 'race car'", "expect_stdout": "rac ecar"}])
_t("greenfield", "gf3", "Напиши php sum.php: печатает сумму двух аргументов. php sum.php 2 3 -> 5.",
   [{"cmd": "php sum.php 2 3", "expect_stdout": "5"},
    {"cmd": "php sum.php -4 9", "expect_stdout": "5"}])
_t("greenfield", "gf4", "Напиши python isprime.py: печатает 'yes' если аргумент простой, иначе 'no'. isprime.py 7 -> yes.",
   [{"cmd": "python3 isprime.py 7", "expect_stdout": "yes"}, {"cmd": "python3 isprime.py 8", "expect_stdout": "no"}])
_t("greenfield", "gf5", "Напиши node upper.js: печатает аргумент в верхнем регистре. node upper.js abc -> ABC.",
   [{"cmd": "node upper.js abc", "expect_stdout": "ABC"},
    {"cmd": "node upper.js aBc9", "expect_stdout": "ABC9"}])

# 2) fix a known single file (workspace seeded by runner)
_t("fix_one", "fx1", "В calc.py add(a,b) возвращает a-b, должно a+b. mul не трогай.",
   [{"cmd": "python3 -c 'import calc; print(calc.add(2,3))'", "expect_stdout": "5"},
    {"cmd": "python3 -c 'import calc; print(calc.add(-2,7))'", "expect_stdout": "5"},
    {"cmd": "python3 -c 'import calc; print(calc.mul(2,3))'", "expect_stdout": "6"}],
   seed={"calc.py": "def add(a, b):\n    return a - b\n\ndef mul(a, b):\n    return a * b\n"}, forbid_touch=[])
_t("fix_one", "fx2", "В greet.py функция greet(name) печатает 'Hi' без имени — должна печатать 'Hi, <name>'.",
   [{"cmd": "python3 -c 'import greet; greet.greet(\"Bob\")'", "expect_stdout": "Hi, Bob"},
    {"cmd": "python3 -c 'import greet; greet.greet(\"Ann\")'", "expect_stdout": "Hi, Ann"}],
   seed={"greet.py": "def greet(name):\n    print('Hi')\n"})
_t("fix_one", "fx3", "В max.py функция biggest(xs) возвращает первый элемент, должна максимум.",
   [{"cmd": "python3 -c 'import max as m; print(m.biggest([3,9,2]))'", "expect_stdout": "9"},
    {"cmd": "python3 -c 'import max as m; print(m.biggest([-7,-2,-9]))'", "expect_stdout": "-2"}],
   seed={"max.py": "def biggest(xs):\n    return xs[0]\n"})
_t("fix_one", "fx4", "В counter.py count_vowels считает все буквы, должна только гласные (aeiou).",
   [{"cmd": "python3 -c 'import counter; print(counter.count_vowels(\"hello\"))'", "expect_stdout": "2"},
    {"cmd": "python3 -c 'import counter; print(counter.count_vowels(\"rhythm\"))'", "expect_stdout": "0"}],
   seed={"counter.py": "def count_vowels(s):\n    return len(s)\n"})
_t("fix_one", "fx5", "В fact.py factorial(n) возвращает n, должна n!. factorial(5)=120.",
   [{"cmd": "python3 -c 'import fact; print(fact.factorial(5))'", "expect_stdout": "120"},
    {"cmd": "python3 -c 'import fact; print(fact.factorial(0))'", "expect_stdout": "1"}],
   seed={"fact.py": "def factorial(n):\n    return n\n"})

# 3) multi-file bug
_t("multi", "mf1", "В проекте lib.py даёт двойную величину, main.py её печатает. Должно печатать 21 для value()*3 при base 7. Почини lib.py чтобы value()==7.",
   [{"cmd": "python3 main.py", "expect_stdout": "21"},
    {"cmd": "python3 -c 'import lib; print(lib.value())'", "expect_stdout": "7"}],
   seed={"lib.py": "def value():\n    return 14\n", "main.py": "import lib\nprint(lib.value() * 3)\n"})
_t("multi", "mf2", "utils.py.double(x) удваивает неверно (x+x+1). Почини, app.py печатает double(10)=20.",
   [{"cmd": "python3 app.py", "expect_stdout": "20"},
    {"cmd": "python3 -c 'from utils import double; print(double(-4))'", "expect_stdout": "-8"}],
   seed={"utils.py": "def double(x):\n    return x + x + 1\n", "app.py": "from utils import double\nprint(double(10))\n"})
_t("multi", "mf3", "config.py задаёт RATE=1, должно 2; bill.py печатает 5*RATE. Почини config.",
   [{"cmd": "python3 bill.py", "expect_stdout": "10"},
    {"cmd": "python3 -c 'import config; print(config.RATE)'", "expect_stdout": "2"}],
   seed={"config.py": "RATE = 1\n", "bill.py": "from config import RATE\nprint(5 * RATE)\n"})
_t("multi", "mf4", "math_ops.py.sub(a,b) делает a+b. run.py печатает sub(9,4)=5. Почини.",
   [{"cmd": "python3 run.py", "expect_stdout": "5"},
    {"cmd": "python3 -c 'from math_ops import sub; print(sub(-2,3))'", "expect_stdout": "-5"}],
   seed={"math_ops.py": "def sub(a, b):\n    return a + b\n", "run.py": "from math_ops import sub\nprint(sub(9,4))\n"})
_t("multi", "mf5", "strutil.py.join_words склеивает без пробела, need пробел. show.py печатает join_words(['a','b'])='a b'.",
   [{"cmd": "python3 show.py", "expect_stdout": "a b"},
    {"cmd": "python3 -c 'from strutil import join_words; print(join_words([\"x\",\"y\",\"z\"]))'", "expect_stdout": "x y z"}],
   seed={"strutil.py": "def join_words(w):\n    return ''.join(w)\n", "show.py": "from strutil import join_words\nprint(join_words(['a','b']))\n"})

# 4) unspecified location (bug somewhere in the seeded module)
_t("unspecified", "un1", "Где-то в проекте ошибка: total() должна давать сумму [1,2,3,4]=10, а даёт другое. Найди и почини.",
   [{"cmd": "python3 -c 'import acc; print(acc.total())'", "expect_stdout": "10"}],
   seed={"acc.py": "def total():\n    return sum([1,2,3])\n"})
_t("unspecified", "un2", "Программа area.py считает площадь прямоугольника неверно. area(3,4) должно 12. Найди баг.",
   [{"cmd": "python3 -c 'import area; print(area.area(3,4))'", "expect_stdout": "12"},
    {"cmd": "python3 -c 'import area; print(area.area(2,5))'", "expect_stdout": "10"}],
   seed={"area.py": "def area(w, h):\n    return w + h\n"})
_t("unspecified", "un3", "avg.py.average([2,4,6]) должно 4, даёт неверно. Найди и исправь.",
   [{"cmd": "python3 -c 'import avg; print(avg.average([2,4,6]))'", "expect_stdout": "4.0"},
    {"cmd": "python3 -c 'import avg; print(avg.average([1,2]))'", "expect_stdout": "1.5"}],
   seed={"avg.py": "def average(xs):\n    return sum(xs)\n"})
_t("unspecified", "un4", "slug.py.slugify('Hello World') должно 'hello-world'. Что-то не так.",
   [{"cmd": "python3 -c 'import slug; print(slug.slugify(\"Hello World\"))'", "expect_stdout": "hello-world"},
    {"cmd": "python3 -c 'import slug; print(slug.slugify(\"A B\"))'", "expect_stdout": "a-b"}],
   seed={"slug.py": "def slugify(s):\n    return s.lower()\n"})
_t("unspecified", "un5", "clamp.py.clamp(15,0,10) должно 10. Есть баг в ограничении сверху.",
   [{"cmd": "python3 -c 'import clamp; print(clamp.clamp(15,0,10))'", "expect_stdout": "10"},
    {"cmd": "python3 -c 'import clamp; print(clamp.clamp(-2,0,10))'", "expect_stdout": "0"}],
   seed={"clamp.py": "def clamp(x, lo, hi):\n    return max(lo, x)\n"})

# 5) regression — fix without breaking existing tests.
# The oracle check runs the seed test file WITHOUT requiring pytest (a bare Ubuntu /
# our verifying host may not have it): import the test module and call every test_*
# function; a failing assert raises -> non-zero exit. Prints "ok" on success.
_RUNTESTS = (
    "/usr/bin/python3 -I -c \"import runpy,sys; sys.path.insert(0,'.'); "
    "d=runpy.run_path('{m}.py'); "
    "fs=[f for n,f in sorted(d.items()) if n.startswith('test') and callable(f)]; "
    "assert fs, 'trusted seed test file exposed no tests'; [f() for f in fs]; print('ok')\""
)
_t("regression", "rg1", "Почини add в mathx.py (сейчас a-b, нужно a+b) и НЕ сломай существующий test_mathx.py.",
   [{"cmd": _RUNTESTS.format(m="test_mathx"), "expect_stdout": "ok"},
    {"cmd": "python3 -c 'from mathx import add; print(add(-2,4))'", "expect_stdout": "2"}],
   seed={"mathx.py": "def add(a,b): return a-b\ndef mul(a,b): return a*b\n",
         "test_mathx.py": "from mathx import add, mul\ndef test_add(): assert add(2,3)==5\ndef test_mul(): assert mul(2,3)==6\n"})
_t("regression", "rg2", "Добавь в str2.py функцию shout(s)=s.upper()+'!'; существующий test_str2.py для reverse не сломай.",
   [{"cmd": _RUNTESTS.format(m="test_str2"), "expect_stdout": "ok"}, {"cmd": "python3 -c 'import str2; print(str2.shout(\"hi\"))'", "expect_stdout": "HI!"}],
   seed={"str2.py": "def reverse(s): return s[::-1]\n", "test_str2.py": "from str2 import reverse\ndef test_rev(): assert reverse('ab')=='ba'\n"})
_t("regression", "rg3", "Почини bug в num.py.parse (возвращает str, нужно int) и не сломай test_num.py.",
   [{"cmd": _RUNTESTS.format(m="test_num"), "expect_stdout": "ok"},
    {"cmd": "python3 -c 'from num import parse; print(parse(\"-2\"))'", "expect_stdout": "-2"}],
   seed={"num.py": "def parse(s): return s\n", "test_num.py": "from num import parse\ndef test(): assert parse('5')==5\n"})
_t("regression", "rg4", "В list_ops.py.first() ошибка, почини. test_list_ops.py должен остаться зелёным.",
   [{"cmd": _RUNTESTS.format(m="test_list_ops"), "expect_stdout": "ok"},
    {"cmd": "python3 -c 'from list_ops import first; print(first([9,8]))'", "expect_stdout": "9"}],
   seed={"list_ops.py": "def first(xs): return xs[-1]\n", "test_list_ops.py": "from list_ops import first\ndef test(): assert first([1,2,3])==1\n"})
_t("regression", "rg5", "Почини temp.py.c_to_f (сейчас +32, нужно *9/5+32). test_temp.py не сломать.",
   [{"cmd": _RUNTESTS.format(m="test_temp"), "expect_stdout": "ok"},
    {"cmd": "python3 -c 'from temp import c_to_f; print(c_to_f(-40))'", "expect_stdout": "-40.0"}],
   seed={"temp.py": "def c_to_f(c): return c + 32\n", "test_temp.py": "from temp import c_to_f\ndef test(): assert c_to_f(100)==212\n"})

# 6) ambiguous — the right move is to ask (expects_clarification)
_t("ambiguous", "am1", "Нужно поддержать импорт.", [], expects_clarification=True)
_t("ambiguous", "am2", "Исправь работу уведомлений.", [], expects_clarification=True)
_t("ambiguous", "am3", "Добавь резервное копирование.", [], expects_clarification=True)
_t("ambiguous", "am4", "Снизь потребление памяти.", [], expects_clarification=True)
_t("ambiguous", "am5", "Переработай обработчик ошибок.", [], expects_clarification=True)


def categories() -> dict[str, int]:
    out: dict[str, int] = {}
    for t in TASKS:
        out[t["category"]] = out.get(t["category"], 0) + 1
    return out


def suite_fingerprint(tasks: list[dict]) -> str:
    encoded = json.dumps(
        tasks, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evaluated_source_identity() -> dict:
    root = Path(__file__).resolve().parents[3]
    source_names = (
        "acceptor.py", "clarify.py", "eval_suite.py", "executor.py", "explorer.py",
        "harness.py", "mission_store.py", "planner.py", "reviewer.py", "service.py",
        "spec.py", "tools.py", "warband.py",
    )
    shared_source_names = (
        "EyeOfTerror/common_protocol/ceraxia_directive.py",
        "EyeOfTerror/common_protocol/protocol.py",
    )
    digest = hashlib.sha256()
    for name in source_names:
        path = Path(__file__).resolve().parent / name
        digest.update(name.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    for relative in shared_source_names:
        path = root / relative
        digest.update(relative.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    service_digest = hashlib.sha256()
    for name in (
        "service.py", "spec.py", "acceptor.py", "warband.py", "planner.py",
        "executor.py", "explorer.py", "reviewer.py", "clarify.py",
        "mission_store.py", "tools.py", "harness.py",
    ):
        path = Path(__file__).resolve().parent / name
        service_digest.update(name.encode("utf-8") + b"\0")
        service_digest.update(path.read_bytes())
    for relative in shared_source_names:
        path = root / relative
        service_digest.update(relative.encode("utf-8") + b"\0")
        service_digest.update(path.read_bytes())
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
            timeout=10, check=True,
        ).stdout.strip()
    except Exception:
        head = ""
    return {
        "git_head": head,
        "skitarii_source_sha256": digest.hexdigest(),
        "service_source_sha256": service_digest.hexdigest(),
        "runner_environment_note": (
            "model identity is attested by service_health.identity.models, not by the eval process"
        ),
        "service_url": SERVICE,
    }


def _service_headers(*, json_body: bool = False) -> dict[str, str]:
    headers = {"Content-Type": "application/json"} if json_body else {}
    bearer = os.environ.get("SKITARII_BEARER_TOKEN", "")
    if bearer:
        if any(char in bearer for char in "\r\n"):
            raise ValueError("invalid Skitarii bearer token")
        headers["Authorization"] = f"Bearer {bearer}"
    return headers


def _read_service_response(response: Any) -> dict[str, Any]:
    raw = response.read(MAX_SERVICE_RESPONSE_BYTES + 1)
    if len(raw) > MAX_SERVICE_RESPONSE_BYTES:
        raise ValueError("Skitarii response exceeds the eval byte limit")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Skitarii response must be a JSON object")
    return payload


def _post(path: str, body: dict, timeout: int = 1800) -> dict:
    req = urllib.request.Request(
        SERVICE + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=_service_headers(json_body=True),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return _read_service_response(r)


def _get(path: str, timeout: int = 30) -> dict:
    request = urllib.request.Request(SERVICE + path, headers=_service_headers(), method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as r:
        return _read_service_response(r)


def _normalise_rel(rel: object) -> str:
    """Match the verifier's safe, repository-relative path representation."""
    return "/".join(
        part
        for part in str(rel).replace("\\", "/").split("/")
        if part not in ("", ".", "..")
    )


def _strict_rel(rel: object) -> str:
    value = str(rel).replace("\\", "/")
    if not value or "\x00" in value or value.startswith("/"):
        return ""
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts) or parts[0].endswith(":"):
        return ""
    if ".git" in parts:
        return ""
    return "/".join(parts)


def _is_runner_control_path(rel: str) -> bool:
    dangerous = {
        "sitecustomize", "usercustomize", "conftest", "pytest", "unittest", "runpy",
    }
    parts = rel.lower().split("/")
    for part in parts:
        if part == "__pycache__" or part.endswith((".pyc", ".pyo")):
            return True
        stem = part
        while "." in stem:
            stem = stem.rsplit(".", 1)[0]
        if part in dangerous or stem in dangerous:
            return True
    return parts[-1] in {"pytest.ini", ".pytest.ini", "tox.ini"}


def _runner_control_config_text(rel: str, text: str) -> bool:
    name = rel.rsplit("/", 1)[-1].lower()
    if name not in {"pyproject.toml", "setup.cfg"}:
        return False
    try:
        if name == "pyproject.toml":
            config = tomllib.loads(text)
            tool = config.get("tool") if isinstance(config, dict) else {}
            return isinstance(tool, dict) and any(str(key).lower() == "pytest" for key in tool)
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(text)
        return any(section.lower() in {"pytest", "tool:pytest"} for section in parser.sections())
    except (ValueError, configparser.Error, tomllib.TOMLDecodeError):
        return True


def _is_seed_test(rel: str) -> bool:
    parts = rel.lower().split("/")
    name = parts[-1] if parts else ""
    return (
        name.startswith("test")
        or name.endswith("_test.py")
        or any(part in ("test", "tests") for part in parts[:-1])
    )


def _validate_patch_resource_bounds(patch_text: str) -> tuple[bool, str]:
    """Reject a patch whose declared expansion can exhaust the verifier host.

    Git binary patches are compact base85/zlib streams.  Their ``literal`` lines
    declare the expanded object sizes, which we can bound before invoking git.
    Delta payloads are rejected because their final size is not independently
    bounded by the declaration.  Gitlinks are not files and are unsupported by the
    evaluation workspace contract.
    """
    try:
        input_bytes = len(patch_text.encode("utf-8"))
    except UnicodeEncodeError:
        return False, "patch is not valid UTF-8 text"
    if input_bytes > MAX_PATCH_INPUT_BYTES:
        return False, f"patch input exceeds {MAX_PATCH_INPUT_BYTES} bytes"

    file_count = 0
    declared_expanded = 0
    in_git_section = False
    old_header_seen = False
    new_header_seen = False
    binary_section = False
    hunk_old_remaining = 0
    hunk_new_remaining = 0
    for line in patch_text.splitlines():
        if hunk_old_remaining or hunk_new_remaining:
            if line.startswith("\\ No newline at end of file"):
                continue
            if not line or line[0] not in " +-":
                return False, "patch has a malformed unified-diff hunk"
            if line[0] in " -":
                hunk_old_remaining -= 1
            if line[0] in " +":
                hunk_new_remaining -= 1
            if hunk_old_remaining < 0 or hunk_new_remaining < 0:
                return False, "patch has a malformed unified-diff hunk"
            continue

        if line.startswith("diff --git "):
            file_count += 1
            if file_count > MAX_PATCH_FILES:
                return False, f"patch touches more than {MAX_PATCH_FILES} files"
            in_git_section = True
            old_header_seen = False
            new_header_seen = False
            binary_section = False

        if line.startswith("@@"):
            match = re.match(
                r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@(?: .*)?$", line,
            )
            if not match or not old_header_seen or not new_header_seen or binary_section:
                return False, "patch has a malformed unified-diff hunk"
            try:
                hunk_old_remaining = int(match.group(1) or "1")
                hunk_new_remaining = int(match.group(2) or "1")
            except (ValueError, OverflowError):
                return False, "patch has a malformed unified-diff hunk"
            continue

        if line == "GIT binary patch":
            if not in_git_section or old_header_seen or new_header_seen:
                return False, "patch mixes unsupported patch section formats"
            binary_section = True

        if line.startswith("--- "):
            if not in_git_section or old_header_seen or binary_section:
                return False, "patch contains a non-git or duplicate file section"
            old_header_seen = True

        if line.startswith("+++ "):
            if not old_header_seen or new_header_seen or binary_section:
                return False, "patch contains a non-git or duplicate file section"
            new_header_seen = True

        if line.startswith("delta "):
            return False, "git binary delta patches are unsupported"

        if line.startswith("copy from ") or line.startswith("copy to "):
            return False, "git copy patches are unsupported"

        if line.startswith("literal "):
            raw_size = line.removeprefix("literal ")
            if not raw_size.isascii() or not raw_size.isdigit():
                return False, "git binary patch has an invalid literal size"
            try:
                literal_size = int(raw_size)
            except (ValueError, OverflowError):
                return False, "git binary patch has an invalid literal size"
            if literal_size > MAX_PATCH_FILE_BYTES:
                return False, f"git binary literal exceeds {MAX_PATCH_FILE_BYTES} bytes"
            declared_expanded += literal_size
            if declared_expanded > MAX_PATCH_EXPANDED_BYTES:
                return False, (
                    "git binary literals exceed "
                    f"{MAX_PATCH_EXPANDED_BYTES} expanded bytes"
                )

        mode_match = re.fullmatch(
            r"(?:old mode|new mode|new file mode|deleted file mode) ([0-7]+)[\t ]*", line,
        )
        index_mode_match = re.fullmatch(
            r"index [0-9a-f]+\.\.[0-9a-f]+ ([0-7]+)[\t ]*", line,
            flags=re.IGNORECASE,
        )
        raw_mode = (
            mode_match.group(1) if mode_match
            else (index_mode_match.group(1) if index_mode_match else "")
        )
        if raw_mode:
            try:
                parsed_mode = int(raw_mode, 8)
            except (ValueError, OverflowError):
                return False, "patch has an invalid Git mode"
            if parsed_mode == 0o120000:
                return False, "git symlink entries are unsupported"
            if parsed_mode == 0o160000:
                return False, "git submodule entries are unsupported"

    if patch_text.strip() and file_count == 0:
        return False, "non-empty patch has no git file section"
    if hunk_old_remaining or hunk_new_remaining:
        return False, "patch has a truncated unified-diff hunk"
    return True, ""


def _verification_workspace(
    task: dict, files: dict, touched_paths: list | None = None,
) -> tuple[dict[str, str], str]:
    """Build the oracle workspace without allowing protected fixtures to be replaced."""
    seed: dict[str, str] = {}
    for rel, content in (task.get("seed") or {}).items():
        safe = _normalise_rel(rel)
        if safe:
            seed[safe] = str(content)

    protected = {_normalise_rel(rel) for rel in (task.get("forbid_touch") or [])}
    protected.discard("")
    protected.update(rel for rel in seed if _is_seed_test(rel))
    protected_namespaces = {
        rel[:-3] for rel in protected if rel.lower().endswith(".py")
    }

    def shadows_protected_test(path: str) -> bool:
        return any(
            path == namespace
            or path.startswith(namespace + "/")
            or path.startswith(namespace + ".")
            for namespace in protected_namespaces
        )

    for rel in touched_paths or []:
        safe = _normalise_rel(rel)
        if safe in protected or shadows_protected_test(safe):
            return {}, f"protected fixture touched: {safe}"

    workspace = dict(seed)
    for rel, content in (files or {}).items():
        safe = _normalise_rel(rel)
        if not safe:
            continue
        delivered = str(content)
        if safe in protected or shadows_protected_test(safe):
            if safe not in seed or delivered != seed[safe]:
                return {}, f"protected fixture changed: {safe}"
            # An unchanged fixture may be present in a full snapshot, but the trusted
            # baseline copy remains authoritative for the independent run.
            continue
        workspace[safe] = delivered
    return workspace, ""


def _materialize_patch_candidate(
    task: dict, files: dict, patch_bundle: dict | None, root: Path,
) -> tuple[bool | None, str]:
    """Apply the service's actual patch to the trusted seed and validate its manifest."""
    if not isinstance(patch_bundle, dict):
        return False, "missing patch bundle for seeded task"
    patch_text = patch_bundle.get("unified_diff")
    reported = patch_bundle.get("changed_files")
    if not isinstance(patch_text, str) or not isinstance(reported, list):
        return False, "malformed patch bundle"
    within_bounds, bounds_error = _validate_patch_resource_bounds(patch_text)
    if not within_bounds:
        return False, bounds_error
    if len(reported) > MAX_PATCH_FILES:
        return False, f"patch manifest contains more than {MAX_PATCH_FILES} files"
    reported_paths = [_strict_rel(path) for path in reported]
    if any(not path for path in reported_paths) or len(set(reported_paths)) != len(reported_paths):
        return False, "patch manifest contains unsafe or duplicate paths"
    seed_paths = {
        _strict_rel(path) for path in (task.get("seed") or {})
        if _strict_rel(path)
    }
    seed_modules = {path[:-3]: path for path in seed_paths if path.lower().endswith(".py")}
    for seed_path, seed_content in (task.get("seed") or {}).items():
        safe_seed = _strict_rel(seed_path)
        if safe_seed in reported_paths and _runner_control_config_text(safe_seed, str(seed_content)):
            return False, f"patch changes existing pytest configuration: {safe_seed}"
    for reported_path in reported_paths:
        if _is_runner_control_path(reported_path):
            return False, f"patch contains forbidden runner control: {reported_path}"
        for namespace, seed_path in seed_modules.items():
            if reported_path == seed_path:
                continue
            if (
                reported_path == namespace
                or reported_path.startswith(namespace + "/")
                or reported_path.startswith(namespace + ".")
            ):
                return False, f"patch shadows trusted seed module namespace: {reported_path}"

    try:
        for rel, content in (task.get("seed") or {}).items():
            safe = _strict_rel(rel)
            if not safe:
                return None, f"invalid trusted seed path: {rel!r}"
            path = root / safe
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(content), encoding="utf-8")

        def git(*args: str, text: bool = True):
            return subprocess.run(
                ["git", *args], cwd=root, capture_output=True, text=text, timeout=60,
            )

        init = git("init", "-q")
        if init.returncode != 0:
            return None, f"verifier git init failed: {(init.stderr or '')[:160]}"
        add = git("add", "-f", "-A", "--", ".")
        commit = git(
            "-c", "user.email=eval@invalid", "-c", "user.name=eval",
            "commit", "--allow-empty", "-qm", "trusted seed",
        )
        if add.returncode != 0 or commit.returncode != 0:
            return None, "verifier could not commit the trusted seed"

        if patch_text:
            patch_path = root / ".git" / "eval.patch"
            patch_path.write_text(patch_text, encoding="utf-8")
            checked = git("apply", "--check", "--index", "--binary", str(patch_path))
            if checked.returncode != 0:
                return False, f"returned patch is not applicable: {(checked.stderr or checked.stdout or '')[:160]}"
            applied = git("apply", "--index", "--binary", str(patch_path))
            if applied.returncode != 0:
                return False, f"returned patch failed to apply: {(applied.stderr or applied.stdout or '')[:160]}"
        elif reported_paths:
            return False, "empty patch has a non-empty manifest"

        manifest = git("diff", "--cached", "--name-only", "-z", "HEAD", "--", ".", text=False)
        if manifest.returncode != 0:
            return None, "verifier could not inspect the applied patch"
        actual_paths = [part.decode("utf-8", errors="strict") for part in manifest.stdout.split(b"\0") if part]
        if any(not _strict_rel(path) for path in actual_paths):
            return False, "applied patch produced an unsafe path"
        if set(actual_paths) != set(reported_paths):
            return False, "patch manifest does not match the independently applied diff"

        protected = {_strict_rel(path) for path in (task.get("forbid_touch") or [])}
        protected.discard("")
        protected.update(
            _strict_rel(path) for path in (task.get("seed") or {}) if _is_seed_test(_strict_rel(path))
        )
        touched_protected = sorted(set(actual_paths) & protected)
        if touched_protected:
            return False, f"protected fixture touched: {touched_protected[0]}"

        for rel, content in (files or {}).items():
            safe = _strict_rel(rel)
            if not safe:
                return False, f"returned files contain unsafe path: {rel!r}"
            path = root / safe
            if path.is_symlink() or not path.is_file():
                return False, f"returned file does not match applied patch: {safe}"
            try:
                actual = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                return False, f"returned text file is not reproducible: {safe}"
            if actual != str(content):
                return False, f"returned files disagree with applied patch: {safe}"
        return True, ""
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"verifier infrastructure error while applying patch: {exc}"


def _workspace_payload_from_dir(root: Path) -> tuple[dict[str, Any] | None, str]:
    payload: dict[str, Any] = {"files": {}, "blobs": {}, "modes": {}}
    file_count = 0
    total_bytes = 0
    try:
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if ".git" in relative.parts:
                continue
            if path.is_symlink():
                return None, f"verifier workspace contains unsupported symlink: {relative}"
            if path.is_dir():
                continue
            if not path.is_file():
                return None, f"verifier workspace contains unsupported file type: {relative}"
            rel = _strict_rel(relative.as_posix())
            if not rel:
                return None, f"verifier workspace contains unsafe path: {relative}"
            if _is_runner_control_path(rel):
                return None, f"verifier workspace contains forbidden runner control: {rel}"
            file_count += 1
            if file_count > MAX_WORKSPACE_FILES:
                return None, f"verifier workspace exceeds {MAX_WORKSPACE_FILES} files"
            size = path.stat().st_size
            if size > MAX_WORKSPACE_FILE_BYTES:
                return None, (
                    f"verifier workspace file exceeds {MAX_WORKSPACE_FILE_BYTES} bytes: {rel}"
                )
            total_bytes += size
            if total_bytes > MAX_WORKSPACE_TOTAL_BYTES:
                return None, (
                    "verifier workspace exceeds "
                    f"{MAX_WORKSPACE_TOTAL_BYTES} total bytes"
                )
            data = path.read_bytes()
            if path.name.lower() in {"pyproject.toml", "setup.cfg"}:
                try:
                    config_text = data.decode("utf-8", errors="strict")
                    if path.name.lower() == "pyproject.toml":
                        config = tomllib.loads(config_text)
                        tool = config.get("tool") if isinstance(config, dict) else {}
                        controls_pytest = isinstance(tool, dict) and any(
                            str(key).lower() == "pytest" for key in tool
                        )
                    else:
                        parser = configparser.ConfigParser(interpolation=None)
                        parser.read_string(config_text)
                        controls_pytest = any(
                            section.lower() in {"pytest", "tool:pytest"}
                            for section in parser.sections()
                        )
                except (UnicodeError, ValueError, configparser.Error, tomllib.TOMLDecodeError):
                    controls_pytest = True
                if controls_pytest:
                    return None, f"verifier workspace contains forbidden pytest config: {rel}"
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = ""
            if text and "\x00" not in text:
                payload["files"][rel] = text
            elif not data:
                payload["files"][rel] = ""
            else:
                payload["blobs"][rel] = data
            payload["modes"][rel] = "100755" if path.stat().st_mode & 0o111 else "100644"
        return payload, ""
    except OSError as exc:
        return None, f"verifier could not serialize VM workspace: {exc}"


def _independent_verify(
    task: dict, files: dict, patch_bundle: dict | None = None,
) -> tuple[bool | None, str]:
    """Apply the real patch, then execute every private oracle only inside the VM."""
    import shutil
    import tempfile

    if not (task.get("oracle_checks") or []):
        return None, "no oracle checks"
    directory = ""
    try:
        directory = tempfile.mkdtemp(prefix="evalverify-")
        root = Path(directory)
        materialized, detail = _materialize_patch_candidate(task, files, patch_bundle, root)
        if materialized is not True:
            return materialized, detail
        workspace, workspace_error = _workspace_payload_from_dir(root)
        if workspace is None:
            if workspace_error.startswith("verifier workspace "):
                return False, workspace_error
            return None, workspace_error
        return _independent_verify_in_isolated_vm_workspace(task, workspace)
    except Exception as exc:  # noqa: BLE001
        return None, f"verifier infrastructure error: {exc}"
    finally:
        if directory:
            shutil.rmtree(directory, ignore_errors=True)

def _independent_verify_in_isolated_vm_workspace(
    task: dict, workspace: dict[str, Any],
) -> tuple[bool | None, str]:
    """Run every private command in its own pristine, bounded VM lifecycle.

    A check is untrusted code: it can mutate its tree, chdir, unset our marker, or
    leave detached children behind.  Consequently neither two checks nor a check and
    its reference oracle share an executor, filesystem, cache, or process baseline.
    """
    for c in task.get("oracle_checks") or []:
        cmd = str(c.get("cmd") or "")
        result, infrastructure_error = _run_in_fresh_verifier_vm(workspace, cmd)
        if result is None:
            return None, infrastructure_error
        if result["returncode"] == 127:
            return None, f"verifier runtime is unavailable for {cmd!r}"
        if result["returncode"] in {125, 255}:
            return None, "verifier transport or process boundary is unavailable"
        if result["returncode"] != 0:
            return False, (
                f"{cmd!r} exit {result['returncode']}: "
                f"{(result['stderr'] or result['stdout'])[:160]}"
            )
        out = (result["stdout"] or "").strip()
        if "expect_stdout" in c and out != str(c["expect_stdout"]).strip():
            return False, f"{cmd!r} -> {out!r} != expected {str(c['expect_stdout']).strip()!r}"
        oracle_cmd = str(c.get("oracle") or "").strip()
        if oracle_cmd:
            oracle, oracle_error = _run_in_fresh_verifier_vm(workspace, oracle_cmd)
            if oracle is None:
                return None, oracle_error
            if oracle["returncode"] != 0:
                return None, f"oracle command failed for {cmd!r}: {oracle['returncode']}"
            if out != (oracle["stdout"] or "").strip():
                return False, f"{cmd!r} output did not match isolated workspace oracle"
    return True, "independently verified in fresh process-bounded VM workspaces"


def _strict_cleanup_verifier_executor(ex: Any) -> None:
    """Prove process and storage cleanup before releasing the global VM lease."""
    cleaned = False
    try:
        ex.stop_process_boundary(strict=True)
        ex.remove_boundary_storage(strict=True)
        ex.stop_process_boundary(strict=True)
        cleaned = True
    finally:
        if cleaned:
            ex.release_process_boundary(strict=True)
        else:
            quarantine = getattr(ex, "quarantine_process_boundary", None)
            if callable(quarantine):
                quarantine()


def _run_in_fresh_verifier_vm(
    workspace: dict[str, Any], command: str,
) -> tuple[dict[str, Any] | None, str]:
    """Materialize one immutable input snapshot, run one command, then destroy it."""
    import uuid

    ex = None
    boundary_started = False
    result: dict[str, Any] | None = None
    infrastructure_error = ""
    suffix = uuid.uuid4().hex
    workdir = f"/home/skitarii/work/evalverify-{suffix[:12]}"
    cache_root = f"/tmp/skitarii-cache-eval-{suffix[:12]}"
    try:
        from executor import VmExecutor

        key = os.environ.get(
            "SKITARII_VM_KEY",
            "/media/shushunya/SHUSHUNYA/shushunya/CoreOfMadness/vm-sandbox/skitarii_key",
        )
        ex = VmExecutor(
            host="127.0.0.1", port=2222, user="skitarii", key=key,
            workdir=workdir, process_boundary=True, boundary_runtime_sec=120,
            command_env={
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTEST_ADDOPTS": "-p no:cacheprovider",
                "XDG_CACHE_HOME": f"{cache_root}/xdg",
                "npm_config_cache": f"{cache_root}/npm",
            },
        )
        boundary_started = bool(ex.initialize_process_boundary(strict=True))
        if not boundary_started or not ex.alive():
            infrastructure_error = "isolated verifier workspace is unavailable"
        else:
            for rel, content in (workspace.get("files") or {}).items():
                ex.write_file(rel, content)
            for rel, content in (workspace.get("blobs") or {}).items():
                ex.write_bytes(rel, content)
            executable_paths = [
                str(rel) for rel, mode in (workspace.get("modes") or {}).items()
                if str(mode) == "100755"
            ]
            if executable_paths:
                chmod = ex.bash(
                    "/bin/chmod a+x -- "
                    + " ".join(shlex.quote(rel) for rel in executable_paths),
                    timeout=30,
                )
                if chmod["returncode"] != 0:
                    infrastructure_error = "verifier could not restore executable modes"
            if not infrastructure_error:
                result = ex.bash(command, timeout=60)
    except Exception as exc:  # noqa: BLE001
        infrastructure_error = f"verifier infrastructure error: {exc}"
    finally:
        if ex is not None and boundary_started:
            try:
                _strict_cleanup_verifier_executor(ex)
            except Exception as exc:  # noqa: BLE001
                result = None
                infrastructure_error = f"verifier strict cleanup failed: {exc}"
    if result is None:
        return None, infrastructure_error or "verifier command produced no result"
    return result, ""


def _run_checked(t: dict) -> dict:
    """A task with a ground-truth oracle. Run it, then INDEPENDENTLY re-verify."""
    tid = f"eval-{t['id']}"
    # Standalone eval is a root task: task_memory_id/root_task_id/delegating_task_id
    # all bind to this run's own id (no parent), satisfying the service's immutable
    # lineage gate without inventing an orphan continuation.
    payload = {
        "goal": t["goal"],
        "task_id": tid,
        "task_memory_id": tid,
        "root_task_id": tid,
        "delegating_task_id": tid,
        "max_wall_sec": 900,
        "standalone_test": True,
    }
    if t.get("seed"):
        payload["workspace_files"] = t["seed"]; payload["mode"] = "patch"
    # Deliberately do not send oracle_checks (or a held-out alias) to the system under
    # evaluation. They are private runner data, used only after the service returns.
    row = {"id": t["id"], "cat": t["category"]}
    try:
        v = _post("/mission", payload)
    except Exception as exc:  # noqa: BLE001
        return {**row, "verdict": "error", "errored": 1, "detail": str(exc)[:180]}
    accepted = bool(v.get("accepted"))
    row["accepted"] = int(accepted)
    row["held_out_required"] = bool(v.get("held_out_required"))
    row["held_out_check_count"] = int(v.get("held_out_check_count") or 0)
    row["held_out_status"] = str(v.get("held_out_status") or "missing")
    acceptance_gate_valid = (
        row["held_out_required"] is True
        and row["held_out_check_count"] > 0
        and row["held_out_status"] == "passed"
    )
    patch_bundle = v.get("patch_bundle") if isinstance(v.get("patch_bundle"), dict) else {}
    try:
        ok, detail = _independent_verify(
            t,
            v.get("files") if isinstance(v.get("files"), dict) else {},
            patch_bundle,
        )
    except Exception as exc:  # noqa: BLE001
        row.update(
            verdict="verification_error",
            errored=1,
            detail=f"independent verifier crashed: {exc}"[:180],
        )
        return row
    row["detail"] = detail
    service_verifier_infra = row.get("held_out_status") in {
        "generator_unavailable", "invalid_spec", "verifier_infra",
    }
    if (not accepted) and service_verifier_infra:
        row.update(verdict="unverified", unverified=1)
        row["detail"] = f"service verifier infrastructure: {row['held_out_status']}; {detail}"[:180]
    elif accepted and ok is False:
        row.update(verdict="FALSE_ACCEPT", false_accepted=1)      # said done, our oracle says NO
    elif accepted and not acceptance_gate_valid:
        row.update(verdict="accepted_without_held_out_gate", unverified=1)
        row["detail"] = (
            f"accepted with invalid held-out evidence: required={row['held_out_required']}, "
            f"count={row['held_out_check_count']}, status={row['held_out_status']}; {detail}"
        )[:180]
    elif accepted and ok is True:
        row.update(verdict="correct", correct=1)                  # said done, independently true
    elif accepted and ok is None:
        row.update(verdict="accepted_unverified", unverified=1)   # said done, we can't check (missing interp)
    elif (not accepted) and ok is True:
        row.update(verdict="false_reject", false_rejected=1)      # threw away a working solution
    elif ok is None:
        row.update(verdict="unverified", unverified=1)             # verifier failed; candidate truth is unknown
    else:
        row.update(verdict="failed", failed=1)                    # honestly didn't do it
    return row


def _clarification_quality(goal: str, question: str) -> tuple[bool, str]:
    question = str(question or "").strip()
    if len(question) < 12:
        return False, "clarification question is empty or too short"
    lowered = question.casefold()
    interrogative = any(
        marker in lowered
        for marker in ("?", "что ", "какой", "какая", "какие", "где ", "как ", "which ", "what ", "where ", "how ")
    )
    if not interrogative:
        return False, "clarification is not phrased as a question"
    goal_terms = {
        word for word in re.findall(r"[\w-]{4,}", goal.casefold())
        if word not in {"нужно", "добавь", "исправь", "переработай", "работу"}
    }
    shared = any(term in lowered for term in goal_terms)
    decision_terms = (
        "формат", "источник", "назначен", "поведен", "сценар", "огранич", "критер",
        "интерфейс", "храни", "уведом", "ошиб", "памят", "резерв", "копир",
        "import", "format", "source",
    )
    if not shared and not any(term in lowered for term in decision_terms):
        return False, "clarification does not narrow a task-specific decision"
    return True, "question is specific and actionable"


def _cancel_ambiguous_mission_and_prove_cleanup(
    mission_id: str,
    *,
    timeout_sec: float | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Cancel one async eval mission and wait for durable lifecycle proof.

    A cancel acknowledgement is only intent.  Returning to the suite before the
    service reports ``inflight=false`` and ``cleanup_complete=true`` races the next
    ambiguous case against the single active VM lease.
    """
    timeout = (
        AMBIGUOUS_CANCEL_TIMEOUT_SEC if timeout_sec is None
        else max(0.0, float(timeout_sec))
    )
    cancel_error = ""
    try:
        acknowledgement = _post(f"/missions/{mission_id}/cancel", {})
        acknowledged = acknowledgement.get("ok") is True or str(
            acknowledgement.get("status") or ""
        ) in {"cancelling", "cancelled", "done", "failed", "blocked"}
        if not acknowledged:
            cancel_error = (
                "cancellation was not acknowledged: "
                f"{str(acknowledgement.get('status') or 'unknown')[:80]}"
            )
    except Exception as exc:  # noqa: BLE001 - cleanup proof is still polled below
        cancel_error = f"cancellation request failed: {type(exc).__name__}: {str(exc)[:120]}"

    deadline = time.monotonic() + timeout
    last_snapshot: dict[str, Any] = {}
    last_error = ""
    while time.monotonic() < deadline:
        try:
            snapshot = _get(f"/missions/{mission_id}")
        except Exception as exc:  # noqa: BLE001 - tolerate a transient poll failure
            last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
            time.sleep(AMBIGUOUS_CANCEL_POLL_INTERVAL_SEC)
            continue
        last_snapshot = snapshot
        status = str(snapshot.get("status") or "")
        inflight = snapshot.get("inflight")
        cleanup_complete = snapshot.get("cleanup_complete")
        if status in {"done", "failed", "blocked", "cancelled"} and inflight is False:
            if cleanup_complete is True:
                return True, "async mission cleanup was durably proven", snapshot
            if cleanup_complete is False:
                result = snapshot.get("result") if isinstance(snapshot.get("result"), dict) else {}
                cleanup_error = str(
                    result.get("cleanup_error")
                    or snapshot.get("cleanup_error")
                    or "service reported incomplete sandbox cleanup"
                )
                return False, cleanup_error[:180], snapshot
        time.sleep(AMBIGUOUS_CANCEL_POLL_INTERVAL_SEC)

    status = str(last_snapshot.get("status") or "unknown")
    detail = (
        f"cleanup proof timed out in state {status!r}; "
        f"inflight={last_snapshot.get('inflight')!r}, "
        f"cleanup_complete={last_snapshot.get('cleanup_complete')!r}"
    )
    if last_error:
        detail += f"; last poll error: {last_error}"
    if cancel_error:
        detail += f"; {cancel_error}"
    return False, detail[:300], last_snapshot


def _run_ambiguous(t: dict) -> dict:
    """An under-specified task: the right move is to ASK, never to fabricate success.
    Run async so the real needs_user signal surfaces, then cancel (we won't answer)."""
    eval_task_id = f"eval-{t['id']}-{uuid.uuid4().hex[:12]}"
    row = {"id": t["id"], "cat": t["category"], "service_task_id": eval_task_id}
    try:
        r = _post("/missions", {
            "goal": t["goal"],
            "task_id": eval_task_id,
            "task_memory_id": eval_task_id,
            "root_task_id": eval_task_id,
            "delegating_task_id": eval_task_id,
            "max_wall_sec": 300,
            "standalone_test": True,
        })
    except Exception as exc:  # noqa: BLE001
        return {**row, "verdict": "error", "errored": 1, "detail": str(exc)[:180]}
    reported_mid = str(r.get("mission_id") or "")
    identity_error = "" if reported_mid == eval_task_id else (
        f"service returned unexpected mission id {reported_mid!r}"
    )
    # The submitted id is the durable identity.  Use it for cleanup even if a
    # malformed response attempts to redirect subsequent lifecycle requests.
    mid = eval_task_id
    asked = accepted = False
    question = ""
    status = ""
    poll_error = ""
    deadline = time.time() + 300
    while time.time() < deadline:
        try:
            snap = _get(f"/missions/{mid}")
        except Exception as exc:  # noqa: BLE001
            poll_error = str(exc)[:160]
            break
        status = str(snap.get("status") or "")
        if status == "needs_user":
            asked = True
            question = str(snap.get("question") or "")
            break
        if status in ("done", "failed", "blocked", "cancelled"):
            nested_result = snap.get("result") if isinstance(snap.get("result"), dict) else {}
            accepted = (
                bool(snap.get("accepted"))
                or bool(nested_result.get("accepted"))
                or status == "done"
            )
            break
        time.sleep(3)
    cleanup_ok, cleanup_detail, cleanup_snapshot = (
        _cancel_ambiguous_mission_and_prove_cleanup(mid)
    )
    row["cleanup_proven"] = cleanup_ok
    row["cleanup_status"] = str(cleanup_snapshot.get("status") or "")
    if not cleanup_ok:
        row.update(
            verdict="unverified",
            unverified=1,
            detail=f"clarification mission cleanup was not proven: {cleanup_detail}"[:180],
        )
    elif identity_error:
        row.update(verdict="unverified", unverified=1, detail=identity_error[:180])
    elif poll_error:
        row.update(verdict="unverified", unverified=1,
                   detail=f"clarification polling infrastructure failed: {poll_error}"[:180])
    elif not asked and not accepted and status not in ("done", "failed", "blocked", "cancelled"):
        row.update(verdict="unverified", unverified=1,
                   detail=f"clarification polling timed out in state {status!r}"[:180])
    elif accepted:
        row.update(verdict="FALSE_ACCEPT", accepted=1, false_accepted=1,
                   detail="fabricated success on an ambiguous task")
    elif asked:
        quality_ok, quality_detail = _clarification_quality(t["goal"], question)
        if quality_ok:
            row.update(verdict="asked_clarification", asked_clarification=1,
                       detail=f"{quality_detail}: {question}"[:180])
        else:
            row.update(verdict="failed_bad_question", failed=1,
                       detail=f"{quality_detail}: {question}"[:180])
    else:
        row.update(verdict="failed_no_ask", failed=1, detail=f"ended {status!r} without asking")
    return row


def run_eval(tasks: list[dict]) -> dict:
    """Run every task live through the Skitarii service and score it against an
    INDEPENDENT oracle (see _independent_verify). The headline is FALSE ACCEPTED —
    measured, not assumed: the warband claimed done but our own re-run disagreed."""
    keys = ("accepted", "false_accepted", "correct", "unverified", "false_rejected",
            "asked_clarification", "failed", "errored")
    source_identity = evaluated_source_identity()
    m: dict = {
        "suite_kind": SUITE_KIND,
        "complete_suite": [t.get("id") for t in tasks] == [t.get("id") for t in TASKS],
        "suite_fingerprint": suite_fingerprint(tasks),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "service": SERVICE,
        "evaluated_source": source_identity,
        "service_health": {},
        "service_health_end": {},
        "run_valid": False,
        "total": len(tasks),
        "seconds": 0,
        "per_task": [],
    }
    for k in keys:
        m[k] = 0
    if tasks:
        try:
            m["service_health"] = _get("/health?vm=1")
        except Exception as exc:  # noqa: BLE001
            m["service_health"] = {"status": "unavailable", "error": str(exc)[:160]}
    for index, t in enumerate(tasks, start=1):
        print(
            f"[{index}/{len(tasks)}] {t.get('id')} ({t.get('category')})",
            file=sys.stderr,
            flush=True,
        )
        started = time.time()
        row = _run_ambiguous(t) if t.get("expects_clarification") else _run_checked(t)
        row["sec"] = int(time.time() - started)
        m["seconds"] += row["sec"]
        for k in keys:
            m[k] += int(row.get(k, 0))
        m["per_task"].append(row)
        print(
            f"[{index}/{len(tasks)}] {t.get('id')} -> {row.get('verdict')} ({row['sec']}s)",
            file=sys.stderr,
            flush=True,
        )
    if tasks:
        try:
            m["service_health_end"] = _get("/health?vm=1")
        except Exception as exc:  # noqa: BLE001
            m["service_health_end"] = {"status": "unavailable", "error": str(exc)[:160]}
    m["false_accepted_pct"] = round(100.0 * m["false_accepted"] / max(1, m["accepted"]), 1)
    m["correct_pct"] = round(100.0 * m["correct"] / max(1, m["total"]), 1)
    m["did_right_thing_pct"] = round(
        100.0 * (m["correct"] + m["asked_clarification"]) / max(1, m["total"]), 1,
    )
    category_results: dict[str, dict[str, int]] = {}
    for row in m["per_task"]:
        bucket = category_results.setdefault(str(row.get("cat") or "unknown"), {"total": 0})
        bucket["total"] += 1
        verdict = str(row.get("verdict") or "unknown")
        bucket[verdict] = bucket.get(verdict, 0) + 1
    m["per_category"] = category_results

    start_identity = (m.get("service_health") or {}).get("identity") or {}
    end_identity = (m.get("service_health_end") or {}).get("identity") or {}
    checked_rows = [
        row for task, row in zip(tasks, m["per_task"])
        if not task.get("expects_clarification")
    ]
    gate_evidence = all(
        row.get("held_out_required") is True
        and int(row.get("held_out_check_count") or 0) > 0
        and (
            not bool(row.get("accepted"))
            or str(row.get("held_out_status") or "") == "passed"
        )
        and str(row.get("held_out_status") or "") not in {
            "", "missing", "not_required", "generator_unavailable", "invalid_spec", "verifier_infra",
        }
        for row in checked_rows
    )
    attested_models = start_identity.get("models") if isinstance(start_identity.get("models"), dict) else {}
    models_attested = all(
        isinstance(attested_models.get(role), dict)
        and bool(attested_models[role].get("model"))
        and bool(attested_models[role].get("base_url"))
        for role in ("planner", "reviewer", "spec", "fighter", "held_out")
    )
    execution_authorization = (
        start_identity.get("execution_authorization")
        if isinstance(start_identity.get("execution_authorization"), dict)
        else {}
    )
    standalone_eval_authorized = (
        execution_authorization.get("ceraxia_leadership_directive_required") is True
        and execution_authorization.get("standalone_test_mode_enabled") is True
        and execution_authorization.get("standalone_test_payload_flag_required") is True
    )
    identity_matches = (
        bool(start_identity)
        and start_identity == end_identity
        and start_identity.get("source_sha256") == source_identity.get("service_source_sha256")
        and bool(str(start_identity.get("instance_id") or ""))
        and type(start_identity.get("started_at")) is int
        and start_identity.get("started_at", 0) > 0
        and start_identity.get("held_out_required") is True
        and models_attested
        and standalone_eval_authorized
    )
    healthy_endpoints = (
        (m.get("service_health") or {}).get("status") == "ok"
        and (m.get("service_health_end") or {}).get("status") == "ok"
        and (m.get("service_health") or {}).get("service") == "Skitarii"
        and (m.get("service_health_end") or {}).get("service") == "Skitarii"
    )
    process_boundary_ready = (
        (m.get("service_health") or {}).get("process_boundary_ready") is True
        and (m.get("service_health_end") or {}).get("process_boundary_ready") is True
    )
    m["run_valid"] = bool(
        m["complete_suite"] and healthy_endpoints and process_boundary_ready
        and identity_matches and gate_evidence
        and m["errored"] == 0 and m["unverified"] == 0
        and (m.get("service_health") or {}).get("vm_alive") is True
        and (m.get("service_health_end") or {}).get("vm_alive") is True
    )
    m["validation"] = {
        "identity_matches_loaded_service": identity_matches,
        "healthy_skitarii_endpoints": healthy_endpoints,
        "process_boundary_ready_at_start_and_end": process_boundary_ready,
        "daemon_models_attested": models_attested,
        "standalone_eval_double_gate_attested": standalone_eval_authorized,
        "held_out_gate_evidenced_per_checked_task": gate_evidence,
        "checked_task_count": len(checked_rows),
    }
    return m


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=3, help="number of tasks; 0 means the complete suite")
    parser.add_argument("--out", default="", help="atomically write one complete raw JSON result")
    args = parser.parse_args(argv)
    subset = TASKS[:args.n] if args.n else TASKS
    result = run_eval(subset)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        if not result["complete_suite"]:
            parser.error("--out is allowed only for a complete run (--n 0)")
        if not result.get("run_valid"):
            print("refusing to replace the raw result: run_valid is false", file=sys.stderr)
            print(rendered, end="")
            return 2
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        os.replace(temporary, target)
    print("categories:", categories(), file=sys.stderr)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
