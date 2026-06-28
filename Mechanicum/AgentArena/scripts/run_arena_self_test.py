#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from pathlib import Path

from run_arena import RunResult, summarize_results, write_json


def main() -> int:
    results = [
        RunResult(agent="shushunya", task_id="a", ok=True, duration_sec=1.25, checks=[]),
        RunResult(agent="shushunya", task_id="b", ok=False, duration_sec=2.0, checks=[]),
        RunResult(agent="aider", task_id="a", ok=True, duration_sec=3.0, checks=[]),
    ]
    summary = summarize_results(results)
    if summary["total"] != 3 or summary["passed"] != 2 or summary["failed"] != 1:
        raise AssertionError(f"bad arena summary totals: {summary}")
    if summary["by_agent"]["shushunya"]["pass_rate"] != 0.5:
        raise AssertionError(f"bad arena per-agent summary: {summary}")
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "report.json"
        write_json(path, {"ok": True})
        if '"ok": true' not in path.read_text(encoding="utf-8"):
            raise AssertionError("arena write_json did not write report")
        leftovers = list(Path(temp_dir).glob("*.tmp"))
        if leftovers:
            raise AssertionError(f"arena write_json left temp files: {leftovers}")
    print("[ok] AgentArena runner")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
