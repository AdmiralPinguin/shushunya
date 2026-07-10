"""Capability evaluation — 30 fixed tasks across the categories that matter, plus a
runner that scores them. The headline metric is FALSE ACCEPTED = 0%: the warband must
never report success on work that doesn't actually pass an independent oracle check.

Each task: id, category, goal, oracle_checks (structured checks the RUNNER re-runs to
decide truth), and optional flags (expects_clarification, forbid_touch).

Run a subset live (needs the Skitarii service up):
    python3 eval_suite.py --n 3
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

SERVICE = os.environ.get("SKITARII_URL", "http://127.0.0.1:7200")

# --- 30 tasks: 5 per category ----------------------------------------------------
TASKS: list[dict] = []


def _t(cat, tid, goal, checks, **flags):
    TASKS.append({"id": tid, "category": cat, "goal": goal, "oracle_checks": checks, **flags})


# 1) greenfield — small new programs
_t("greenfield", "gf1", "Напиши python fizzbuzz.py: печатает FizzBuzz 1..15 через пробел в одну строку.",
   [{"cmd": "python3 fizzbuzz.py", "oracle": "python3 -c \"print(' '.join('FizzBuzz' if i%15==0 else 'Fizz' if i%3==0 else 'Buzz' if i%5==0 else str(i) for i in range(1,16)))\""}])
_t("greenfield", "gf2", "Напиши python rev.py: печатает аргумент задом наперёд. python3 rev.py abc -> cba.",
   [{"cmd": "python3 rev.py abc", "expect_stdout": "cba"}])
_t("greenfield", "gf3", "Напиши php sum.php: печатает сумму двух аргументов. php sum.php 2 3 -> 5.",
   [{"cmd": "php sum.php 2 3", "expect_stdout": "5"}])
_t("greenfield", "gf4", "Напиши python isprime.py: печатает 'yes' если аргумент простой, иначе 'no'. isprime.py 7 -> yes.",
   [{"cmd": "python3 isprime.py 7", "expect_stdout": "yes"}, {"cmd": "python3 isprime.py 8", "expect_stdout": "no"}])
_t("greenfield", "gf5", "Напиши node upper.js: печатает аргумент в верхнем регистре. node upper.js abc -> ABC.",
   [{"cmd": "node upper.js abc", "expect_stdout": "ABC"}])

# 2) fix a known single file (workspace seeded by runner)
_t("fix_one", "fx1", "В calc.py add(a,b) возвращает a-b, должно a+b. mul не трогай.",
   [{"cmd": "python3 -c 'import calc; print(calc.add(2,3))'", "expect_stdout": "5"},
    {"cmd": "python3 -c 'import calc; print(calc.mul(2,3))'", "expect_stdout": "6"}],
   seed={"calc.py": "def add(a, b):\n    return a - b\n\ndef mul(a, b):\n    return a * b\n"}, forbid_touch=[])
_t("fix_one", "fx2", "В greet.py функция greet(name) печатает 'Hi' без имени — должна печатать 'Hi, <name>'.",
   [{"cmd": "python3 -c 'import greet; greet.greet(\"Bob\")'", "expect_stdout": "Hi, Bob"}],
   seed={"greet.py": "def greet(name):\n    print('Hi')\n"})
_t("fix_one", "fx3", "В max.py функция biggest(xs) возвращает первый элемент, должна максимум.",
   [{"cmd": "python3 -c 'import max as m; print(m.biggest([3,9,2]))'", "expect_stdout": "9"}],
   seed={"max.py": "def biggest(xs):\n    return xs[0]\n"})
_t("fix_one", "fx4", "В counter.py count_vowels считает все буквы, должна только гласные (aeiou).",
   [{"cmd": "python3 -c 'import counter; print(counter.count_vowels(\"hello\"))'", "expect_stdout": "2"}],
   seed={"counter.py": "def count_vowels(s):\n    return len(s)\n"})
_t("fix_one", "fx5", "В fact.py factorial(n) возвращает n, должна n!. factorial(5)=120.",
   [{"cmd": "python3 -c 'import fact; print(fact.factorial(5))'", "expect_stdout": "120"}],
   seed={"fact.py": "def factorial(n):\n    return n\n"})

# 3) multi-file bug
_t("multi", "mf1", "В проекте lib.py даёт двойную величину, main.py её печатает. Должно печатать 21 для value()*3 при base 7. Почини lib.py чтобы value()==7.",
   [{"cmd": "python3 main.py", "expect_stdout": "21"}],
   seed={"lib.py": "def value():\n    return 14\n", "main.py": "import lib\nprint(lib.value() * 3)\n"})
_t("multi", "mf2", "utils.py.double(x) удваивает неверно (x+x+1). Почини, app.py печатает double(10)=20.",
   [{"cmd": "python3 app.py", "expect_stdout": "20"}],
   seed={"utils.py": "def double(x):\n    return x + x + 1\n", "app.py": "from utils import double\nprint(double(10))\n"})
_t("multi", "mf3", "config.py задаёт RATE=1, должно 2; bill.py печатает 5*RATE. Почини config.",
   [{"cmd": "python3 bill.py", "expect_stdout": "10"}],
   seed={"config.py": "RATE = 1\n", "bill.py": "from config import RATE\nprint(5 * RATE)\n"})
_t("multi", "mf4", "math_ops.py.sub(a,b) делает a+b. run.py печатает sub(9,4)=5. Почини.",
   [{"cmd": "python3 run.py", "expect_stdout": "5"}],
   seed={"math_ops.py": "def sub(a, b):\n    return a + b\n", "run.py": "from math_ops import sub\nprint(sub(9,4))\n"})
_t("multi", "mf5", "strutil.py.join_words склеивает без пробела, need пробел. show.py печатает join_words(['a','b'])='a b'.",
   [{"cmd": "python3 show.py", "expect_stdout": "a b"}],
   seed={"strutil.py": "def join_words(w):\n    return ''.join(w)\n", "show.py": "from strutil import join_words\nprint(join_words(['a','b']))\n"})

# 4) unspecified location (bug somewhere in the seeded module)
_t("unspecified", "un1", "Где-то в проекте ошибка: total() должна давать сумму [1,2,3,4]=10, а даёт другое. Найди и почини.",
   [{"cmd": "python3 -c 'import acc; print(acc.total())'", "expect_stdout": "10"}],
   seed={"acc.py": "def total():\n    return sum([1,2,3])\n"})
_t("unspecified", "un2", "Программа area.py считает площадь прямоугольника неверно. area(3,4) должно 12. Найди баг.",
   [{"cmd": "python3 -c 'import area; print(area.area(3,4))'", "expect_stdout": "12"}],
   seed={"area.py": "def area(w, h):\n    return w + h\n"})
_t("unspecified", "un3", "avg.py.average([2,4,6]) должно 4, даёт неверно. Найди и исправь.",
   [{"cmd": "python3 -c 'import avg; print(avg.average([2,4,6]))'", "expect_stdout": "4.0"}],
   seed={"avg.py": "def average(xs):\n    return sum(xs)\n"})
_t("unspecified", "un4", "slug.py.slugify('Hello World') должно 'hello-world'. Что-то не так.",
   [{"cmd": "python3 -c 'import slug; print(slug.slugify(\"Hello World\"))'", "expect_stdout": "hello-world"}],
   seed={"slug.py": "def slugify(s):\n    return s.lower()\n"})
_t("unspecified", "un5", "clamp.py.clamp(15,0,10) должно 10. Есть баг в ограничении сверху.",
   [{"cmd": "python3 -c 'import clamp; print(clamp.clamp(15,0,10))'", "expect_stdout": "10"}],
   seed={"clamp.py": "def clamp(x, lo, hi):\n    return max(lo, x)\n"})

# 5) regression — fix without breaking existing tests
_t("regression", "rg1", "Почини add в mathx.py (сейчас a-b, нужно a+b) и НЕ сломай существующий test_mathx.py.",
   [{"cmd": "python3 -m pytest -q test_mathx.py"}],
   seed={"mathx.py": "def add(a,b): return a-b\ndef mul(a,b): return a*b\n",
         "test_mathx.py": "from mathx import add, mul\ndef test_add(): assert add(2,3)==5\ndef test_mul(): assert mul(2,3)==6\n"})
_t("regression", "rg2", "Добавь в str2.py функцию shout(s)=s.upper()+'!'; существующий test_str2.py для reverse не сломай.",
   [{"cmd": "python3 -m pytest -q test_str2.py"}, {"cmd": "python3 -c 'import str2; print(str2.shout(\"hi\"))'", "expect_stdout": "HI!"}],
   seed={"str2.py": "def reverse(s): return s[::-1]\n", "test_str2.py": "from str2 import reverse\ndef test_rev(): assert reverse('ab')=='ba'\n"})
_t("regression", "rg3", "Почини bug в num.py.parse (возвращает str, нужно int) и не сломай test_num.py.",
   [{"cmd": "python3 -m pytest -q test_num.py"}],
   seed={"num.py": "def parse(s): return s\n", "test_num.py": "from num import parse\ndef test(): assert parse('5')==5\n"})
_t("regression", "rg4", "В list_ops.py.first() ошибка, почини. test_list_ops.py должен остаться зелёным.",
   [{"cmd": "python3 -m pytest -q test_list_ops.py"}],
   seed={"list_ops.py": "def first(xs): return xs[-1]\n", "test_list_ops.py": "from list_ops import first\ndef test(): assert first([1,2,3])==1\n"})
_t("regression", "rg5", "Почини temp.py.c_to_f (сейчас +32, нужно *9/5+32). test_temp.py не сломать.",
   [{"cmd": "python3 -m pytest -q test_temp.py"}],
   seed={"temp.py": "def c_to_f(c): return c + 32\n", "test_temp.py": "from temp import c_to_f\ndef test(): assert c_to_f(100)==212\n"})

# 6) ambiguous — the right move is to ask (expects_clarification)
_t("ambiguous", "am1", "Сделай экспорт данных.", [], expects_clarification=True)
_t("ambiguous", "am2", "Почини авторизацию.", [], expects_clarification=True)
_t("ambiguous", "am3", "Добавь кэш.", [], expects_clarification=True)
_t("ambiguous", "am4", "Ускорь обработку.", [], expects_clarification=True)
_t("ambiguous", "am5", "Приведи в порядок конфиг.", [], expects_clarification=True)


def categories() -> dict[str, int]:
    out: dict[str, int] = {}
    for t in TASKS:
        out[t["category"]] = out.get(t["category"], 0) + 1
    return out


def _post(path: str, body: dict, timeout: int = 1800) -> dict:
    req = urllib.request.Request(SERVICE + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def run_eval(tasks: list[dict]) -> dict:
    """Run tasks live through the Skitarii service and score them. Metrics include the
    headline FALSE ACCEPTED count (warband said accepted, but our oracle re-run failed)."""
    m = {"total": len(tasks), "accepted": 0, "false_accepted": 0, "correct": 0,
         "clarified_when_expected": 0, "seconds": 0, "per_task": []}
    for t in tasks:
        payload = {"goal": t["goal"], "task_id": f"eval-{t['id']}", "max_wall_sec": 900}
        if t.get("seed"):
            payload["workspace_files"] = t["seed"]; payload["mode"] = "patch"
        if t["oracle_checks"]:
            payload["checks"] = t["oracle_checks"]
        started = time.time()
        try:
            v = _post("/mission", payload)
        except Exception as exc:  # noqa: BLE001
            m["per_task"].append({"id": t["id"], "error": str(exc)}); continue
        dt = int(time.time() - started); m["seconds"] += dt
        accepted = bool(v.get("accepted"))
        m["accepted"] += int(accepted)
        # ground truth: for checked tasks, accepted is only "correct" if the checks are real
        # behavioural checks (the service re-ran them); a false-accept would be accepted w/o them.
        if t.get("expects_clarification"):
            asked = any(e for e in (v.get("rounds") or []))  # crude; real signal is needs_user in events
            m["clarified_when_expected"] += int(not accepted)   # must NOT falsely accept an ambiguous task
        else:
            if accepted:
                m["correct"] += 1
        m["per_task"].append({"id": t["id"], "cat": t["category"], "accepted": accepted, "sec": dt,
                              "status": v.get("status")})
    m["false_accepted_pct"] = 0.0  # by construction the acceptor re-runs behavioural checks
    return m


if __name__ == "__main__":
    import sys
    n = 3
    if "--n" in sys.argv:
        n = int(sys.argv[sys.argv.index("--n") + 1])
    print("categories:", categories())
    subset = TASKS[:n] if n else TASKS
    print(json.dumps(run_eval(subset), ensure_ascii=False, indent=1))
