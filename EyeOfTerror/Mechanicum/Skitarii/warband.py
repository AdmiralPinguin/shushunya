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
from typing import Any

from acceptor import accept
from harness import run_fighter
from spec import build_spec


def run_mission(goal: str, executor: Any, *, checks: list[str] | None = None,
                task_id: str = "", ask_fn=None, cancel_fn=None,
                max_fighter_rounds: int = 2, max_steps: int = 40,
                max_wall_sec: int = 3600) -> dict[str, Any]:
    """Drive one code mission end to end. Returns a verdict dict."""
    # 1) Postanovshchik: derive executable success checks (unless caller gave them).
    if checks:
        norm = [c if isinstance(c, dict) else {"cmd": str(c)} for c in checks]
        spec = {"deliverables": [], "checks": norm}
    else:
        spec = build_spec(goal)
    checks = spec["checks"]
    deliverables = spec["deliverables"]

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
        fighter = run_fighter(goal + retry_note, checks, executor, task_id=task_id,
                              ask_fn=ask_fn, cancel_fn=cancel_fn,
                              max_steps=max_steps, max_wall_sec=max_wall_sec)
        last_fighter = fighter
        if fighter.get("cancelled"):
            return {"status": "cancelled", "accepted": False, "rounds": rounds,
                    "summary": "cancelled", "artifacts": [], "checks": checks}

        # 3) Priyomshchik: independent re-run. The fighter's own 'ok' is not trusted.
        artifacts = fighter.get("artifacts") or deliverables
        acceptance = accept(executor, deliverables or artifacts, checks)
        rounds.append({"round": rnd, "fighter_ok": fighter["ok"],
                       "steps": fighter["steps"], "seconds": fighter["seconds"],
                       "acceptance": acceptance})
        if acceptance["accepted"]:
            return {"status": "done", "accepted": True, "rounds": rounds,
                    "summary": fighter.get("summary", ""),
                    "artifacts": artifacts, "checks": checks}

    # 4) Exhausted rounds without acceptance -> honest escalation with the real failures.
    fails = [r for r in rounds[-1]["acceptance"]["results"] if not r["ok"]]
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
    return {"status": "failed", "accepted": False, "rounds": rounds,
            "summary": last_fighter.get("summary", ""),
            "artifacts": last_fighter.get("artifacts", []),
            "checks": checks,
            "revision_required": True,
            "verification_findings": diagnostics,
            "blockers": [
                f"{f['target']} -> {f.get('why') or f.get('stderr','') or f.get('stdout','') or 'failed'}"
                for f in fails
            ]}


if __name__ == "__main__":  # manual driver
    import sys
    from pathlib import Path
    from executor import LocalExecutor
    wd = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/skitarii-mission")
    verdict = run_mission(sys.argv[1], LocalExecutor(wd))
    print(json.dumps({k: v for k, v in verdict.items() if k != "rounds"}, ensure_ascii=False, indent=1))
    for r in verdict["rounds"]:
        print(f"round {r['round']}: fighter_ok={r['fighter_ok']} steps={r['steps']} {r['seconds']}s accepted={r['acceptance']['accepted']}")
