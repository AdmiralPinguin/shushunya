#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from pathlib import Path

from eye_of_terror.inner_circle.iskandar import plan_research_writing
from eye_of_terror.ledger import TaskLedger
from eye_of_terror.orchestrator import revision_step_ids_from_run
from eye_of_terror.pipeline import write_pipeline_run
from eye_of_terror.run_validation import validate_revision_plan


def main() -> int:
    plan = plan_research_writing("Напиши book на 3 chapters о локальных агентах.", task_id="revision-book-test").to_dict()
    contract = plan["contract"]
    oversight = plan["oversight"]
    with tempfile.TemporaryDirectory() as temp_dir:
        run_dir = Path(temp_dir) / "run"
        write_pipeline_run(plan_research_writing("Напиши book на 3 chapters о локальных агентах.", task_id="revision-book-test").contract, run_dir, oversight=oversight)
        revision_plan = {
            "required": True,
            "steps": [
                {
                    "step_id": "draft_reconstruction",
                    "worker": "ScriptoriumDaemon",
                    "reason": "Book chapter was blocked for missing evidence: /work/book/chapters/chapter_02.md",
                    "source": "critic_finding",
                    "priority": "blocker",
                }
            ],
        }
        errors = validate_revision_plan(run_dir, revision_plan)
        if errors:
            raise AssertionError(f"draft-only revision plan should be valid before final-step expansion: {errors}")
        ledger = TaskLedger.create(run_dir / "task_ledger.json", contract["task_id"], contract["goal"], contract["assigned_governor"])
        ledger.set_result({"ok": False, "status": "needs_revision", "revision_plan": revision_plan})
        step_ids = revision_step_ids_from_run(run_dir)
        if step_ids != ["draft_reconstruction", "critic_review", "finalize"]:
            raise AssertionError(f"book revision should rerun focused draft plus final review steps: {step_ids}")
    print("[ok] research revision loop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
