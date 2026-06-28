#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from eye_of_terror.inner_circle.iskandar import plan_lore_reconstruction
from eye_of_terror.local_executor import execute_run, revision_contexts_from_result, terminal_payload_allows_completion
from eye_of_terror.pipeline import write_pipeline_run


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if terminal_payload_allows_completion({"ok": True, "status": "blocked"}):
        raise AssertionError("blocked terminal payload should not complete a run")
    if terminal_payload_allows_completion({"ok": True}):
        raise AssertionError("terminal payload without status should not complete a run")
    if terminal_payload_allows_completion({"ok": True, "status": "mystery"}):
        raise AssertionError("unknown terminal payload status should not complete a run")
    if terminal_payload_allows_completion({"ok": True, "status": "ready", "revision_plan": {"required": True}}):
        raise AssertionError("required revision plan should not complete a run")
    if not terminal_payload_allows_completion({"ok": True, "status": "ready", "revision_plan": {"required": False}}):
        raise AssertionError("ready terminal payload should complete a run")
    revision_contexts = revision_contexts_from_result(
        {
            "revision_plan": {
                "required": True,
                "steps": [
                    {
                        "step_id": "draft_reconstruction",
                        "worker": "ScriptoriumDaemon",
                        "reason": "Draft misses required event",
                        "source": "critic_finding",
                        "priority": "blocker",
                    }
                ],
            }
        }
    )
    if revision_contexts.get("draft_reconstruction", {}).get("reasons") != ["Draft misses required event"]:
        raise AssertionError(f"bad revision context mapping: {revision_contexts}")
    repo_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        missing_input_run = root / "missing-input-run"
        missing_input_dispatch = missing_input_run / "dispatch"
        write_json(
            missing_input_run / "contract.json",
            {"task_id": "missing-input-local", "goal": "test missing input", "assigned_governor": "IskandarKhayon"},
        )
        write_json(
            missing_input_run / "status.json",
            {
                "task_id": "missing-input-local",
                "governor": "IskandarKhayon",
                "steps": [
                    {
                        "step_id": "fact_extraction",
                        "worker": "NoosphericExtractor",
                        "depends_on": ["source_acquisition"],
                        "input_artifacts": ["/work/test/missing.json"],
                        "expected_artifacts": ["/work/test/direct_event_notes.json"],
                    }
                ],
                "dispatch_dir": str(missing_input_dispatch),
            },
        )
        write_json(
            missing_input_dispatch / "fact_extraction.json",
            {
                "step_id": "fact_extraction",
                "worker": "NoosphericExtractor",
                "request": {
                    "task_id": "missing-input-local:fact_extraction",
                    "input_artifacts": ["/work/test/missing.json"],
                    "step": {"expected_artifacts": ["/work/test/direct_event_notes.json"]},
                },
            },
        )
        missing_summary = execute_run(repo_root, missing_input_run, root / "missing-work", timeout_sec=30)
        if missing_summary.get("ok") or missing_summary.get("steps", [{}])[0].get("payload", {}).get("error") != "input artifact preflight failed":
            raise AssertionError(f"local executor did not reject missing input artifacts: {missing_summary}")
        missing_ledger = json.loads((missing_input_run / "task_ledger.json").read_text(encoding="utf-8"))
        if missing_ledger.get("status") != "failed" or missing_ledger.get("steps", [{}])[0].get("status") != "failed":
            raise AssertionError(f"missing input failure was not recorded durably: {missing_ledger}")
        corrupt_dispatch_run = root / "corrupt-dispatch-run"
        corrupt_dispatch_dir = corrupt_dispatch_run / "dispatch"
        write_json(
            corrupt_dispatch_run / "contract.json",
            {"task_id": "corrupt-dispatch-local", "goal": "test corrupt dispatch", "assigned_governor": "IskandarKhayon"},
        )
        write_json(
            corrupt_dispatch_run / "status.json",
            {
                "task_id": "corrupt-dispatch-local",
                "governor": "IskandarKhayon",
                "steps": [{"step_id": "fact_extraction", "worker": "NoosphericExtractor"}],
                "dispatch_dir": str(corrupt_dispatch_dir),
            },
        )
        corrupt_dispatch_dir.mkdir(parents=True, exist_ok=True)
        (corrupt_dispatch_dir / "fact_extraction.json").write_text("{", encoding="utf-8")
        corrupt_summary = execute_run(repo_root, corrupt_dispatch_run, root / "corrupt-work", timeout_sec=30)
        if corrupt_summary.get("ok") or "dispatch unavailable" not in corrupt_summary.get("steps", [{}])[0].get("payload", {}).get("error", ""):
            raise AssertionError(f"local executor did not record corrupt dispatch failure: {corrupt_summary}")
        corrupt_ledger = json.loads((corrupt_dispatch_run / "task_ledger.json").read_text(encoding="utf-8"))
        if corrupt_ledger.get("status") != "failed" or corrupt_ledger.get("steps", [{}])[0].get("status") != "failed":
            raise AssertionError(f"corrupt dispatch failure was not recorded durably: {corrupt_ledger}")
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
