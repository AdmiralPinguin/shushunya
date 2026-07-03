#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from eye_of_terror import local_executor
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
                        "reason": "Draft misses required event | Depends on revised step timeline",
                        "source": "critic_finding,revision_dependency",
                        "priority": "blocker",
                    }
                ],
            }
        }
    )
    if revision_contexts.get("draft_reconstruction", {}).get("reasons") != [
        "Draft misses required event",
        "Depends on revised step timeline",
    ]:
        raise AssertionError(f"bad revision context mapping: {revision_contexts}")
    if revision_contexts.get("draft_reconstruction", {}).get("source_steps") != ["critic_finding", "revision_dependency"]:
        raise AssertionError(f"bad revision source mapping: {revision_contexts}")
    repo_root = Path(__file__).resolve().parents[2]
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
        bad_quality_run = root / "bad-quality-run"
        bad_quality_dispatch = bad_quality_run / "dispatch"
        source = root / "bad-quality-work" / "test" / "source_map.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(json.dumps({"topic": "test", "sources": []}), encoding="utf-8")
        write_json(
            bad_quality_run / "contract.json",
            {"task_id": "bad-quality-local", "goal": "test bad quality", "assigned_governor": "IskandarKhayon"},
        )
        write_json(
            bad_quality_run / "status.json",
            {
                "task_id": "bad-quality-local",
                "governor": "IskandarKhayon",
                "steps": [
                    {
                        "step_id": "fact_extraction",
                        "worker": "NoosphericExtractor",
                        "input_artifacts": ["/work/test/source_map.json"],
                        "expected_artifacts": ["/work/test/direct_event_notes.json"],
                    }
                ],
                "dispatch_dir": str(bad_quality_dispatch),
            },
        )
        write_json(
            bad_quality_dispatch / "fact_extraction.json",
            {
                "step_id": "fact_extraction",
                "worker": "NoosphericExtractor",
                "request": {
                    "task_id": "bad-quality-local:fact_extraction",
                    "input_artifacts": ["/work/test/source_map.json"],
                    "step": {"step_id": "fact_extraction", "expected_artifacts": ["/work/test/direct_event_notes.json"]},
                    "quality_expectations": {
                        "step_quality": {
                            "step_id": "fact_extraction",
                            "worker": "WrongWorker",
                            "expected_artifacts": ["/work/test/direct_event_notes.json"],
                            "checks": ["check"],
                            "blockers": ["blocker"],
                            "revision_targets": ["fact_extraction"],
                        },
                        "revision_policy": {
                            "source_step": "critic_review",
                            "final_steps": ["critic_review", "finalize"],
                            "allowed_steps": ["critic_review", "finalize"],
                            "requires_downstream_rerun": True,
                            "requires_focused_context": True,
                            "requires_gap_disclosure": True,
                        },
                    },
                },
            },
        )
        bad_quality_summary = execute_run(repo_root, bad_quality_run, root / "bad-quality-work", timeout_sec=30)
        if (
            bad_quality_summary.get("ok")
            or bad_quality_summary.get("steps", [{}])[0].get("payload", {}).get("error") != "worker request preflight failed"
            or not bad_quality_summary.get("steps", [{}])[0].get("payload", {}).get("quality_expectation_errors")
            or not any(
                error.get("field") == "revision_policy.allowed_steps"
                for error in bad_quality_summary.get("steps", [{}])[0].get("payload", {}).get("quality_expectation_errors", [])
            )
        ):
            raise AssertionError(f"local executor did not reject bad quality expectations: {bad_quality_summary}")
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
        timeout_run = root / "timeout-run"
        timeout_dispatch = timeout_run / "dispatch"
        write_json(
            timeout_run / "contract.json",
            {"task_id": "timeout-local", "goal": "test timeout", "assigned_governor": "IskandarKhayon"},
        )
        write_json(
            timeout_run / "status.json",
            {
                "task_id": "timeout-local",
                "governor": "IskandarKhayon",
                "steps": [{"step_id": "source_discovery", "worker": "Lexmechanic", "expected_artifacts": ["/work/test/source_map.json"]}],
                "dispatch_dir": str(timeout_dispatch),
            },
        )
        write_json(
            timeout_dispatch / "source_discovery.json",
            {
                "step_id": "source_discovery",
                "worker": "Lexmechanic",
                "request": {
                    "task_id": "timeout-local:source_discovery",
                    "contract": {"goal": "Собери историю неизвестной битвы."},
                    "step": {"expected_artifacts": ["/work/test/source_map.json"]},
                },
            },
        )
        timeout_summary = execute_run(repo_root, timeout_run, root / "timeout-work", timeout_sec=0)
        timeout_payload = timeout_summary.get("steps", [{}])[0].get("payload", {})
        if timeout_summary.get("ok") or timeout_payload.get("error_code") != "worker_timeout":
            raise AssertionError(f"local executor did not record worker timeout: {timeout_summary}")
        if timeout_payload.get("model_brain", {}).get("status") != "answered":
            raise AssertionError(f"local executor did not attach model brain status: {timeout_summary}")
        if timeout_payload.get("attempt_count") != 1 or timeout_payload.get("timeout_retries") != 0:
            raise AssertionError(f"zero-second timeout should not be retried: {timeout_summary}")
        timeout_ledger = json.loads((timeout_run / "task_ledger.json").read_text(encoding="utf-8"))
        if timeout_ledger.get("status") != "failed" or timeout_ledger.get("steps", [{}])[0].get("status") != "failed":
            raise AssertionError(f"worker timeout was not recorded durably: {timeout_ledger}")
        flaky_repo = root / "flaky-repo"
        flaky_worker_dir = flaky_repo / "workers"
        flaky_worker_dir.mkdir(parents=True)
        (flaky_worker_dir / "flaky_worker.py").write_text(
            """#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("dispatch")
parser.add_argument("--workspace-root", required=True)
args = parser.parse_args()
workspace = Path(args.workspace_root)
workspace.mkdir(parents=True, exist_ok=True)
marker = workspace / "first_attempt_marker"
if not marker.exists():
    marker.write_text("seen", encoding="utf-8")
    time.sleep(2)
print(json.dumps({"ok": True, "status": "completed", "summary": "flaky worker recovered", "artifacts": []}))
""",
            encoding="utf-8",
        )
        old_command = local_executor.WORKER_COMMANDS.get("FlakyWorker")
        local_executor.WORKER_COMMANDS["FlakyWorker"] = (".", "workers/flaky_worker.py")
        try:
            flaky_run = root / "flaky-run"
            flaky_dispatch = flaky_run / "dispatch"
            write_json(
                flaky_run / "contract.json",
                {"task_id": "flaky-local", "goal": "test timeout retry", "assigned_governor": "IskandarKhayon"},
            )
            write_json(
                flaky_run / "status.json",
                {
                    "task_id": "flaky-local",
                    "governor": "IskandarKhayon",
                    "steps": [{"step_id": "flaky_step", "worker": "FlakyWorker"}],
                    "dispatch_dir": str(flaky_dispatch),
                },
            )
            write_json(
                flaky_dispatch / "flaky_step.json",
                {
                    "step_id": "flaky_step",
                    "worker": "FlakyWorker",
                    "request": {"task_id": "flaky-local:flaky_step"},
                },
            )
            flaky_summary = execute_run(flaky_repo, flaky_run, root / "flaky-work", timeout_sec=1, timeout_retries=1)
            if not flaky_summary.get("ok") or flaky_summary.get("steps", [{}])[0].get("returncode") != 0:
                raise AssertionError(f"local executor should retry one flaky timeout: {flaky_summary}")
        finally:
            if old_command is None:
                local_executor.WORKER_COMMANDS.pop("FlakyWorker", None)
            else:
                local_executor.WORKER_COMMANDS["FlakyWorker"] = old_command
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
