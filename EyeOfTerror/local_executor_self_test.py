#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from eye_of_terror.inner_circle.iskandar import plan_lore_reconstruction
from eye_of_terror.local_executor import execute_run, terminal_payload_allows_completion
from eye_of_terror.pipeline import write_pipeline_run


def main() -> int:
    if terminal_payload_allows_completion({"ok": True, "status": "blocked"}):
        raise AssertionError("blocked terminal payload should not complete a run")
    if terminal_payload_allows_completion({"ok": True, "status": "ready", "revision_plan": {"required": True}}):
        raise AssertionError("required revision plan should not complete a run")
    if not terminal_payload_allows_completion({"ok": True, "status": "ready", "revision_plan": {"required": False}}):
        raise AssertionError("ready terminal payload should complete a run")
    repo_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        run_dir = root / "run"
        work_dir = root / "work"
        plan = plan_lore_reconstruction("Собери все известное о событиях Скалатракса.", task_id="executor-test")
        write_pipeline_run(plan.contract, run_dir)
        summary = execute_run(repo_root, run_dir, work_dir, timeout_sec=30)
        if not summary.get("ok"):
            raise AssertionError(summary)
        manifest = work_dir / "skalathrax" / "final_manifest.json"
        if not manifest.exists():
            raise AssertionError("final manifest was not written")
        if not (run_dir / "task_ledger.json").exists():
            raise AssertionError("task ledger was not written")
        ledger = json.loads((run_dir / "task_ledger.json").read_text(encoding="utf-8"))
        if ledger.get("result", {}).get("revision_plan", {}).get("required"):
            raise AssertionError(f"ready run should not require revision: {ledger}")
    print("[ok] local executor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
