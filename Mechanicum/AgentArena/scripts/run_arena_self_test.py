#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from pathlib import Path

from analyze_reports import analyze_reports
from run_arena import RunResult, summarize_results, write_json


def main() -> int:
    results = [
        RunResult(
            agent="shushunya",
            task_id="a",
            ok=True,
            duration_sec=1.25,
            checks=[],
            orchestration={
                "ok": True,
                "failing_diagnostic_steps": [1],
                "edit_steps": [2],
                "verified_after_last_edit": True,
            },
        ),
        RunResult(
            agent="shushunya",
            task_id="b",
            ok=False,
            duration_sec=2.0,
            checks=[],
            orchestration={
                "ok": False,
                "failing_diagnostic_steps": [],
                "edit_steps": [2],
                "verified_after_last_edit": False,
            },
        ),
        RunResult(agent="aider", task_id="a", ok=True, duration_sec=3.0, checks=[]),
    ]
    summary = summarize_results(results)
    if summary["total"] != 3 or summary["passed"] != 2 or summary["failed"] != 1:
        raise AssertionError(f"bad arena summary totals: {summary}")
    if summary["by_agent"]["shushunya"]["pass_rate"] != 0.5:
        raise AssertionError(f"bad arena per-agent summary: {summary}")
    quality = summary.get("orchestration_quality", {}).get("shushunya", {})
    if quality.get("chain_pass_rate") != 0.5 or quality.get("missing_failing_diagnostic") != 1:
        raise AssertionError(f"bad arena orchestration quality summary: {summary}")
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "report.json"
        write_json(path, {"ok": True})
        if '"ok": true' not in path.read_text(encoding="utf-8"):
            raise AssertionError("arena write_json did not write report")
        leftovers = list(Path(temp_dir).glob("*.tmp"))
        if leftovers:
            raise AssertionError(f"arena write_json left temp files: {leftovers}")
        report = Path(temp_dir) / "report.json"
        write_json(
            report,
            {
                "suite": "smoke",
                "results": [
                    {
                        "agent": "shushunya",
                        "task_id": "a",
                        "ok": True,
                        "duration_sec": 1.0,
                        "orchestration": {
                            "ok": True,
                            "failing_diagnostic_steps": [1],
                            "edit_steps": [2],
                            "verified_after_last_edit": True,
                        },
                    },
                    {
                        "agent": "shushunya",
                        "task_id": "b",
                        "ok": False,
                        "duration_sec": 2.0,
                        "exit_code": 2,
                        "orchestration": {
                            "ok": False,
                            "failing_diagnostic_steps": [],
                            "edit_steps": [],
                            "verified_after_last_edit": False,
                        },
                    },
                ],
            },
        )
        analysis = analyze_reports([report])
        if analysis["stats"][0]["pass_rate"] != 0.5 or not analysis["recent_failures"]:
            raise AssertionError(f"bad arena report analysis: {analysis}")
        quality_rows = analysis.get("orchestration_quality", [])
        if not quality_rows or quality_rows[0].get("chain_pass_rate") != 0.5 or quality_rows[0].get("missing_edit") != 1:
            raise AssertionError(f"bad arena orchestration report analysis: {analysis}")
    print("[ok] AgentArena runner")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
