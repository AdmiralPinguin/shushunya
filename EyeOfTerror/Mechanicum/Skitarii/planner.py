"""Planner — the head of the warband (separate agent, non-coder model).

The coder fighters (Qwen) are strong hands but a weak head: they don't decompose,
don't plan, and can tunnel into a wall. So a separate planner on a general model
(gemma) does the thinking: split the goal into ordered subtasks, hand each to a
fighter, watch progress, and decide done/continue/redirect. Head and hands are
deliberately different agents on different models.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any

from warband import run_mission
from acceptor import accept
from spec import build_spec


def _planner_chat(prompt: str, max_tokens: int = 1200) -> str:
    base = os.environ.get("PLANNER_LLM_BASE_URL", "http://127.0.0.1:8079/v1").rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    payload = {
        "model": os.environ.get("PLANNER_LLM_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(f"{base}/chat/completions",
                                 data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as resp:
        return str(((json.loads(resp.read()).get("choices") or [{}])[0].get("message") or {}).get("content") or "")


def _extract_json(text: str) -> Any:
    m = re.search(r"\[.*\]|\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def decompose(goal: str) -> list[dict[str, Any]]:
    """Split the goal into ordered subtasks. Returns [] for a small single-file task
    (the caller then runs one fighter directly)."""
    prompt = (
        "You are the planning head of a coding warband. Split the task into the MINIMUM number of "
        "independent subtasks, each producing one file or coherent module with a clear goal. "
        "A small task (one script/file) is ONE subtask — do not over-split. Order them so earlier "
        "subtasks are prerequisites of later ones.\n"
        'Return ONE JSON array and nothing else: [{"title": "...", "goal": "concrete instruction for a coder", '
        '"depends_on": [indexes of earlier subtasks it needs, or []]}]\n\n'
        f"TASK:\n{goal}"
    )
    try:
        parsed = _extract_json(_planner_chat(prompt))
    except Exception:
        parsed = None
    if not isinstance(parsed, list):
        return []
    subtasks = []
    for i, item in enumerate(parsed):
        if isinstance(item, dict) and str(item.get("goal") or "").strip():
            subtasks.append({
                "title": str(item.get("title") or f"subtask {i+1}"),
                "goal": str(item.get("goal")),
                "depends_on": [int(x) for x in (item.get("depends_on") or []) if isinstance(x, (int, float))],
            })
    return subtasks


def reconsider(top_goal: str, stuck_goal: str, failed: dict[str, Any]) -> str:
    """The head's anti-stuck move: a fighter got stuck (rounds exhausted, checks still
    red). Ask the planner model for a DIFFERENT, simpler approach to the same goal."""
    fails = ""
    acc = failed.get("acceptance") or {}
    if isinstance(acc, dict):
        fails = "; ".join(str(r.get("why") or r.get("target")) for r in acc.get("results", []) if not r.get("ok"))
    prompt = (
        "A coder got STUCK on a subtask: it exhausted its attempts and the checks are still failing. "
        "Do not repeat the same approach. Rewrite the subtask instruction with a DIFFERENT, simpler, more "
        "robust strategy that still satisfies it (e.g. a simpler algorithm, standard library instead of a "
        "dependency, smaller scope first). Return ONLY the new instruction text, no preamble.\n\n"
        f"OVERALL TASK:\n{top_goal}\n\nSTUCK SUBTASK:\n{stuck_goal}\n\n"
        f"WHAT FAILED:\n{fails or failed.get('summary','')}"
    )
    try:
        out = _planner_chat(prompt, max_tokens=500).strip()
        return out if len(out) > 10 else ""
    except Exception:
        return ""


def _run_with_retry(goal: str, executor: Any, task_id: str, *, top_goal: str,
                    note, max_wall_sec: int, rounds: int = 2, ask_fn=None, cancel_fn=None) -> dict[str, Any]:
    """Run a fighter; if it gets stuck, let the planner change the approach once."""
    res = run_mission(goal, executor, task_id=task_id, max_fighter_rounds=rounds,
                      max_wall_sec=max_wall_sec, ask_fn=ask_fn, cancel_fn=cancel_fn)
    if res.get("accepted") or res.get("status") == "cancelled":
        return res
    note("Планировщик: боец застрял — переобдумываю подход.")
    new_goal = reconsider(top_goal, goal, res)
    if not new_goal:
        return res
    note(f"Планировщик: новый подход → {new_goal[:120]}")
    res2 = run_mission(new_goal, executor, task_id=task_id, max_fighter_rounds=rounds,
                       max_wall_sec=max_wall_sec, ask_fn=ask_fn, cancel_fn=cancel_fn)
    res2["reconsidered"] = True
    return res2


def _dependency_waves(subtasks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group subtasks into waves: each wave holds subtasks whose deps are all satisfied
    by earlier waves. Subtasks within a wave are independent → can run in parallel."""
    done: set[int] = set()
    waves: list[list[dict[str, Any]]] = []
    remaining = list(range(len(subtasks)))
    while remaining:
        wave_idx = [i for i in remaining
                    if all(d in done for d in subtasks[i].get("depends_on", []) if 0 <= d < len(subtasks))]
        if not wave_idx:                      # broken deps → run the rest sequentially
            wave_idx = remaining[:1]
        waves.append([subtasks[i] for i in wave_idx])
        done.update(wave_idx)
        remaining = [i for i in remaining if i not in wave_idx]
    return waves


def _run_wave_parallel(wave, base_executor, task_id, top_goal, note, per, ask_fn, cancel_fn):
    """Run one wave. A single subtask runs in the shared workdir. Several independent
    subtasks each run in an isolated worktree (child executor, seeded from the current
    project state), then their changes are merged back. Concurrency is capped."""
    if len(wave) == 1:
        return [_run_with_retry(wave[0]["goal"], base_executor, task_id, top_goal=top_goal,
                                note=note, max_wall_sec=per, ask_fn=ask_fn, cancel_fn=cancel_fn)]
    import concurrent.futures as _f
    base = getattr(base_executor, "workdir", "")
    limit = max(1, int(os.environ.get("SKITARII_PARALLEL", "2")))

    def _one(idx_sub):
        idx, sub = idx_sub
        child = base_executor.child(f"{task_id}-s{idx}")
        cdir = getattr(child, "workdir", "")
        # seed the child with the current project so it has context
        base_executor.bash(f"rm -rf {cdir!r}; mkdir -p {cdir!r}; cp -a {base!r}/. {cdir!r}/ 2>/dev/null || true", timeout=60)
        # parallel fighters don't ask the user (can't interleave questions); cancel still applies
        res = _run_with_retry(sub["goal"], child, task_id, top_goal=top_goal, note=note,
                              max_wall_sec=per, ask_fn=None, cancel_fn=cancel_fn)
        return idx, cdir, res

    results: dict[int, dict] = {}
    dirs: dict[int, str] = {}
    with _f.ThreadPoolExecutor(max_workers=min(limit, len(wave))) as pool:
        for idx, cdir, res in pool.map(_one, list(enumerate(wave))):
            results[idx] = res
            dirs[idx] = cdir
    # merge each child's changes back into the shared project, in order; a file touched
    # by two children is flagged as a conflict for a follow-up integrator.
    seen: dict[str, int] = {}
    for idx in range(len(wave)):
        cdir = dirs[idx]
        changed = (base_executor.bash(
            f"cd {cdir!r} && find . -type f -not -path './.git/*' -not -path './.bg/*' -newer . 2>/dev/null; "
            f"cd {cdir!r} && ls -1 2>/dev/null", timeout=30).get("stdout") or "")
        base_executor.bash(f"cp -a {cdir!r}/. {base!r}/ 2>/dev/null || true", timeout=60)
        for f in set(changed.split()):
            if f in seen and seen[f] != idx:
                note(f"Интеграция: файл {f} менялся в нескольких подзадачах — беру последнюю версию (нужен интегратор при конфликте).")
            seen[f] = idx
    return [results[i] for i in range(len(wave))]


def plan_and_run(goal: str, executor: Any, *, task_id: str = "", ask_fn=None, cancel_fn=None,
                 max_wall_sec: int = 5400, memory=None) -> dict[str, Any]:
    """Plan the goal, run fighters per subtask in one shared workdir, then accept the
    whole thing against the top-level checks. `memory(note)` is an optional callback."""
    def note(msg: str) -> None:
        if memory:
            try:
                memory(msg)
            except Exception:
                pass

    subtasks = decompose(goal)
    # small task → single fighter, but with the head's anti-stuck retry
    if len(subtasks) <= 1:
        note("Планировщик: задача простая, один боец.")
        return _run_with_retry(goal, executor, task_id, top_goal=goal, note=note,
                               max_wall_sec=max_wall_sec, ask_fn=ask_fn, cancel_fn=cancel_fn)

    note(f"Планировщик разбил на {len(subtasks)} подзадач: " + "; ".join(s["title"] for s in subtasks))
    top_spec = build_spec(goal)          # final acceptance for the whole task
    waves = _dependency_waves(subtasks)
    sub_results: list[dict[str, Any]] = []
    per = max(300, max_wall_sec // max(1, len(subtasks)))
    for wave in waves:
        if cancel_fn is not None and cancel_fn():
            return {"status": "cancelled", "accepted": False, "subtasks": sub_results,
                    "summary": "cancelled", "artifacts": [], "checks": top_spec["checks"]}
        # independent subtasks in one wave run in parallel, each in its own isolated
        # worktree (child executor). With a single Qwen slot they still serialise on the
        # model, but the isolation + merge is ready for multi-slot / a stronger model.
        results = _run_wave_parallel(wave, executor, task_id, goal, note, per, ask_fn, cancel_fn)
        for sub, res in zip(wave, results):
            sub_results.append({"title": sub["title"], "status": res.get("status"),
                                "accepted": res.get("accepted"), "reconsidered": res.get("reconsidered", False)})
            note(f"  → {sub['title']}: {res.get('status')}")
            if res.get("status") == "cancelled":
                return {"status": "cancelled", "accepted": False, "subtasks": sub_results,
                        "summary": "cancelled", "artifacts": [], "checks": top_spec["checks"]}
            if not res.get("accepted"):
                note(f"Планировщик: подзадача '{sub['title']}' не сдалась — эскалирую.")
                return {"status": "failed", "accepted": False, "subtasks": sub_results,
                        "summary": f"Subtask '{sub['title']}' failed: {res.get('summary','')}",
                        "artifacts": res.get("artifacts", []), "checks": top_spec["checks"]}

    # whole-task acceptance: re-run the top-level checks against the combined project
    acceptance = accept(executor, top_spec["deliverables"], top_spec["checks"])
    note(f"Планировщик: итоговая приёмка — {'принято' if acceptance['accepted'] else 'НЕ принято'}.")
    return {"status": "done" if acceptance["accepted"] else "failed",
            "accepted": acceptance["accepted"], "subtasks": sub_results,
            "summary": f"Собрано из {len(subtasks)} подзадач. Итоговые проверки: "
                       f"{'все прошли' if acceptance['accepted'] else 'не все прошли'}.",
            "artifacts": [], "checks": top_spec["checks"], "acceptance": acceptance}
