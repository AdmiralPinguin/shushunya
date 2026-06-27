#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from pathlib import Path

from eye_of_terror.inner_circle.iskandar import plan_lore_reconstruction
from eye_of_terror.local_executor import execute_run
from eye_of_terror.pipeline import write_pipeline_run


def main() -> int:
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
    print("[ok] local executor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
