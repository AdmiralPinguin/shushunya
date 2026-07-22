"""Skitarii warband orchestrator.

The whole flow the old six paper-workers should have been:
    goal -> Postanovshchik (executable checks)
         -> Fighter loop (write -> RUN -> fix)  [retry if acceptance fails]
         -> Priyomshchik (independently re-run the checks)
         -> honest verdict for the governor.

No paper hand-offs: every stage's truth is real execution in the workdir/VM.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from acceptor import accept
from critic import judge_product
from harness import run_fighter
from product_probe import collect_evidence
from spec import build_spec, workspace_file_listing

_MAX_POLISH_ROUNDS = int(os.environ.get("SKITARII_MAX_POLISH_ROUNDS", "2"))


def run_quality_phase(goal: str, executor: Any, *, contract: list[str],
                      product: dict[str, Any], checks: list[dict], emit,
                      task_id: str = "", memory_task_id: str = "",
                      ask_fn=None, cancel_fn=None,
                      durable_checkpoint_fn=None, progress=None,
                      max_wall_sec: int = 1800) -> dict[str, Any]:
    """Run the PRODUCT, judge it against the quality contract, polish boundedly.

    The executable checks prove «works»; this phase chases «well made»: probe
    evidence (transcripts/screens) goes to the multimodal critic, its findings
    feed at most _MAX_POLISH_ROUNDS fighter rounds, then the honest report is
    attached to the verdict. Never a dead end: an unreachable probe or critic
    degrades to a pass with a note, and exhausted rounds deliver WITH warnings."""
    report: dict[str, Any] = {"quality_contract": contract, "rounds": [], "passed": True}
    if not contract:
        return report
    runtime_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime")
    os.makedirs(runtime_dir, exist_ok=True)
    scratch = tempfile.mkdtemp(prefix="probe-", dir=runtime_dir)
    for rnd in range(_MAX_POLISH_ROUNDS + 1):
        if cancel_fn is not None and cancel_fn():
            report["cancelled"] = True
            break
        evidence = collect_evidence(executor, product, scratch)
        emit("Продукт запущен на пробу: "
             f"{len(evidence.get('texts') or [])} расшифровок, "
             f"{len(evidence.get('screens') or [])} кадров.")
        verdict = judge_product(goal, contract, evidence)
        report["rounds"].append({
            "round": rnd, "passed": verdict.get("passed"),
            "findings": verdict.get("findings") or [],
            "facts": evidence.get("facts") or {},
        })
        if verdict.get("passed"):
            report["passed"] = True
            emit("Критик качества: продукт принят по контракту.")
            break
        report["passed"] = False
        report["findings"] = verdict.get("findings") or []
        instructions = str(verdict.get("polish_instructions") or "").strip()
        if rnd >= _MAX_POLISH_ROUNDS or not instructions:
            emit("Критик качества: замечания остались — сдаю с честным списком.")
            break
        emit(f"Критик качества: {len(report['findings'])} замечаний — полиш-раунд {rnd + 1}.")
        polish_goal = (
            goal
            + "\n\nPRODUCT QUALITY POLISH ROUND. The functional checks already pass —"
              " KEEP THEM PASSING. A product critic reviewed the RUNNING product and"
              " demands these concrete improvements:\n- "
            + "\n- ".join(report["findings"])
            + f"\n\nWork order:\n{instructions}"
        )
        fighter = run_fighter(polish_goal, checks, executor, task_id=task_id,
                              memory_task_id=memory_task_id,
                              ask_fn=ask_fn, cancel_fn=cancel_fn,
                              max_steps=30, max_wall_sec=max_wall_sec,
                              durable_checkpoint_fn=durable_checkpoint_fn,
                              progress=progress)
        if fighter.get("cancelled"):
            report["cancelled"] = True
            break
        post = accept(executor, [], checks)
        if not post.get("accepted"):
            emit("Полиш сломал функциональные проверки — один ремонтный заход.")
            fails = [r for r in post.get("results", []) if not r.get("ok")]
            repair_goal = (
                goal + "\n\nYour polish BROKE the acceptance checks. Fix exactly these"
                       " and re-run them, changing nothing else:\n- "
                + "\n- ".join(str(f.get("target"))[:160] for f in fails)
            )
            run_fighter(repair_goal, checks, executor, task_id=task_id,
                        memory_task_id=memory_task_id,
                        ask_fn=ask_fn, cancel_fn=cancel_fn,
                        max_steps=25, max_wall_sec=max_wall_sec,
                        durable_checkpoint_fn=durable_checkpoint_fn,
                        progress=progress)
            post = accept(executor, [], checks)
            report["function_after_polish"] = bool(post.get("accepted"))
            if not post.get("accepted"):
                report["broke_function"] = True
                emit("Функция не восстановлена — останавливаю полиш, фиксирую в отчёте.")
                break
    return report


def _attach_quality(verdict: dict[str, Any], goal: str, executor: Any,
                    spec: dict[str, Any], checks: list[dict], *, build_project: bool,
                    emit, task_id: str, memory_task_id: str, ask_fn, cancel_fn,
                    durable_checkpoint_fn, progress, max_wall_sec: int) -> dict[str, Any]:
    """Accepted function -> chase quality (whole-project missions only)."""
    contract = [c for c in (spec.get("quality_contract") or []) if str(c).strip()]
    if not build_project or not contract:
        return verdict
    emit("Функция принята — запускаю продукт и зову критика качества.")
    quality = run_quality_phase(goal, executor, contract=contract,
                                product=spec.get("product") or {}, checks=checks,
                                emit=emit, task_id=task_id,
                                memory_task_id=memory_task_id,
                                ask_fn=ask_fn, cancel_fn=cancel_fn,
                                durable_checkpoint_fn=durable_checkpoint_fn,
                                progress=progress,
                                max_wall_sec=min(int(max_wall_sec), 2400))
    verdict["quality_report"] = quality
    if not quality.get("passed"):
        verdict["quality_warnings"] = quality.get("findings") or []
    return verdict


def run_mission(goal: str, executor: Any, *, checks: list[str] | None = None,
                task_id: str = "", memory_task_id: str = "",
                ask_fn=None, cancel_fn=None,
                max_fighter_rounds: int = 2, max_steps: int = 40,
                max_wall_sec: int = 3600,
                durable_checkpoint_fn=None, progress=None,
                build_project: bool = False) -> dict[str, Any]:
    """Drive one code mission end to end. Returns a verdict dict.

    `progress(text)` — optional live plain-language feed of what the fighter does.
    `build_project` — whole-project task: acceptance includes the real build."""
    def emit(text: str) -> None:
        if progress is None:
            return
        line = str(text or "").strip()
        if line:
            try:
                progress(line)
            except Exception:
                pass

    # 1) Postanovshchik: derive executable success checks (unless caller gave them).
    if checks:
        norm = [c if isinstance(c, dict) else {"cmd": str(c)} for c in checks]
        spec = {"deliverables": [], "checks": norm}
    else:
        spec = build_spec(goal, build_project=build_project,
                          existing_files=workspace_file_listing(executor))
    checks = spec["checks"]
    deliverables = spec["deliverables"]

    # The quality bar goes into the fighter's goal UP FRONT: a contractor who
    # learns the bar only from the critic afterwards builds the minimum first
    # (the first galaga shipped colored rectangles for ships).
    if build_project and spec.get("quality_contract"):
        goal = (goal + "\n\nPRODUCT QUALITY BAR — a critic will judge the RUNNING"
                       " product against these, build to them from the start:\n- "
                + "\n- ".join(spec["quality_contract"]))

    # 1.5) Repair mode, mechanically: on an inherited workspace this subtask may be
    # ALREADY DONE — run the acceptance first and skip the fighter when it is green.
    # Without this every retry re-walks the whole staircase, perturbing finished
    # floors (fresh package names, rewrites) instead of landing on the broken one.
    if checks:
        try:
            pre = accept(executor, deliverables, checks)
        except Exception:
            pre = {"accepted": False}
        if pre.get("accepted"):
            emit("Уже сдано прошлыми попытками: приёмка зелёная без бойца — не переделываю.")
            verdict = {"status": "done", "accepted": True,
                       "rounds": [{"round": 0, "fighter_ok": True, "steps": 0,
                                   "seconds": 0, "acceptance": pre}],
                       "summary": "already satisfied by the inherited workspace",
                       "artifacts": deliverables, "checks": checks}
            return _attach_quality(verdict, goal, executor, spec, checks,
                                   build_project=build_project, emit=emit,
                                   task_id=task_id, memory_task_id=memory_task_id,
                                   ask_fn=ask_fn, cancel_fn=cancel_fn,
                                   durable_checkpoint_fn=durable_checkpoint_fn,
                                   progress=progress, max_wall_sec=max_wall_sec)

    rounds: list[dict[str, Any]] = []
    last_fighter: dict[str, Any] = {}
    for rnd in range(1, max_fighter_rounds + 1):
        # 2) Fighter: agentic loop against the real checks. On a retry, feed back the
        #    acceptance failures so it fixes the exact thing that failed.
        retry_note = ""
        if rounds:
            prev = rounds[-1]["acceptance"]
            fails = [r for r in prev["results"] if not r["ok"]]
            retry_note = ("\n\nA previous attempt FAILED acceptance. Keep the checks that already pass working, "
                          "and fix exactly these, then re-run them:\n"
                          + "\n".join(f"- `{f['target']}` -> {f.get('why') or f.get('stderr') or 'failed'}" for f in fails))
            emit(f"Приёмка не прошла — захожу на раунд {rnd}, чиню провалившиеся проверки.")
        fighter = run_fighter(goal + retry_note, checks, executor, task_id=task_id,
                              memory_task_id=memory_task_id,
                              ask_fn=ask_fn, cancel_fn=cancel_fn,
                              max_steps=max_steps, max_wall_sec=max_wall_sec,
                              durable_checkpoint_fn=durable_checkpoint_fn,
                              progress=progress)
        last_fighter = fighter
        if fighter.get("cancelled"):
            return {"status": "cancelled", "accepted": False, "rounds": rounds,
                    "summary": "cancelled", "artifacts": [], "checks": checks}

        # 3) Priyomshchik: independent re-run. The fighter's own 'ok' is not trusted.
        artifacts = fighter.get("artifacts") or deliverables
        emit(f"Приёмщик независимо перезапускает {len(checks)} проверк(и) — доверяю только реальному прогону.")
        acceptance = accept(executor, deliverables or artifacts, checks)
        emit("Приёмка: принято." if acceptance["accepted"] else "Приёмка: НЕ принято, есть провалы.")
        rounds.append({"round": rnd, "fighter_ok": fighter["ok"],
                       "steps": fighter["steps"], "seconds": fighter["seconds"],
                       "acceptance": acceptance})
        if acceptance["accepted"]:
            verdict = {"status": "done", "accepted": True, "rounds": rounds,
                       "summary": fighter.get("summary", ""),
                       "artifacts": artifacts, "checks": checks}
            return _attach_quality(verdict, goal, executor, spec, checks,
                                   build_project=build_project, emit=emit,
                                   task_id=task_id, memory_task_id=memory_task_id,
                                   ask_fn=ask_fn, cancel_fn=cancel_fn,
                                   durable_checkpoint_fn=durable_checkpoint_fn,
                                   progress=progress, max_wall_sec=max_wall_sec)

    # 4) Exhausted rounds without acceptance -> honest escalation with the real failures.
    last_acceptance = rounds[-1]["acceptance"]
    fails = [r for r in last_acceptance["results"] if not r["ok"]]
    diagnostics = [
        {
            "code": "public_candidate_failure",
            "what_failed": "The candidate still fails a public executable acceptance check.",
            "evidence": (
                f"{item.get('target') or 'public check'}: "
                f"{item.get('why') or item.get('stderr') or item.get('stdout') or 'failed'}"
            )[:2_000],
            "expected": "Every public behavioural check passes without regressing earlier green checks.",
            "remediation": "Fix the reported behaviour and rerun the complete acceptance set.",
            "revision_owner": "fighter",
            "retryable": True,
            "entity_kind": "behavioural_check",
            "entity_id": f"public-{index}",
        }
        for index, item in enumerate(fails, 1)
    ]
    acceptance_reason = str(last_acceptance.get("reason") or "").strip()
    if not diagnostics:
        # Individual commands can all exit zero while the structural gate still
        # rejects a weak acceptance set (for example, grep/run-only checks).  That
        # is an acceptance-spec defect, not proof that the candidate succeeded.
        diagnostics = [{
            "code": "acceptance_spec_failure",
            "what_failed": "The public acceptance specification did not prove the requested behaviour.",
            "evidence": (
                acceptance_reason
                or "Acceptance rejected without a failing per-check result."
            )[:2_000],
            "expected": "At least one task-linked behavioural oracle or real test proves the requested outcome.",
            "remediation": "Regenerate a behavioural/test acceptance check, then rerun the candidate against the complete set.",
            "revision_owner": "infrastructure",
            "retryable": True,
            "entity_kind": "acceptance",
            "entity_id": "public-acceptance-spec",
        }]
    truthful_summary = (
        f"Acceptance rejected: {acceptance_reason}"
        if acceptance_reason
        else f"Acceptance failed: {diagnostics[0]['evidence']}"
    )
    return {"status": "failed", "accepted": False, "rounds": rounds,
            "summary": truthful_summary,
            "artifacts": last_fighter.get("artifacts", []),
            "checks": checks, "acceptance": last_acceptance,
            "revision_required": True,
            "verification_findings": diagnostics,
            "blockers": [
                f"{f['target']} -> {f.get('why') or f.get('stderr','') or f.get('stdout','') or 'failed'}"
                for f in fails
            ] or [diagnostics[0]["evidence"]]}


if __name__ == "__main__":  # manual driver
    import sys
    from pathlib import Path
    from executor import LocalExecutor
    wd = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/skitarii-mission")
    verdict = run_mission(sys.argv[1], LocalExecutor(wd))
    print(json.dumps({k: v for k, v in verdict.items() if k != "rounds"}, ensure_ascii=False, indent=1))
    for r in verdict["rounds"]:
        print(f"round {r['round']}: fighter_ok={r['fighter_ok']} steps={r['steps']} {r['seconds']}s accepted={r['acceptance']['accepted']}")
