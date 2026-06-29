#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from pathlib import Path

from analyze_reports import analyze_reports
from report_metrics import failure_reason
from run_arena import RunResult, analyze_artifact_orchestration, summarize_results, write_json


def main() -> int:
    if failure_reason(127, [{"type": "command", "ok": False}], "missing Docker/Podman") != "agent_unavailable":
        raise AssertionError("arena failure_reason should classify missing runtimes separately")
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
            checks=[{"type": "file_contains", "path": "report.md", "ok": False, "output": "AssertionError"}],
            exit_code=0,
            orchestration={
                "ok": False,
                "failing_diagnostic_steps": [],
                "edit_steps": [2],
                "verified_after_last_edit": False,
            },
        ),
        RunResult(agent="aider", task_id="a", ok=True, duration_sec=3.0, checks=[]),
        RunResult(agent="openhands", task_id="a", ok=False, duration_sec=0.1, checks=[], exit_code=127, error="missing Docker/Podman"),
    ]
    summary = summarize_results(results)
    if summary["total"] != 4 or summary["passed"] != 2 or summary["failed"] != 2:
        raise AssertionError(f"bad arena summary totals: {summary}")
    if summary["by_agent"]["shushunya"]["pass_rate"] != 0.5:
        raise AssertionError(f"bad arena per-agent summary: {summary}")
    if summary["by_agent"]["openhands"]["unavailable"] != 1 or summary["by_agent"]["openhands"]["runnable_pass_rate"] is not None:
        raise AssertionError(f"bad arena unavailable summary: {summary}")
    if summary.get("failure_reasons", {}).get("post_run_checks") != 1 or summary.get("failed_check_types", {}).get("file_contains") != 1:
        raise AssertionError(f"bad arena direct summary failure counters: {summary}")
    if summary.get("failed_check_symptoms", {}).get("assertion_error") != 1:
        raise AssertionError(f"bad arena direct summary symptom counters: {summary}")
    quality = summary.get("orchestration_quality", {}).get("shushunya", {})
    if quality.get("chain_pass_rate") != 0.5 or quality.get("missing_failing_diagnostic") != 1:
        raise AssertionError(f"bad arena orchestration quality summary: {summary}")
    artifact_ok = analyze_artifact_orchestration(
        [
            {"_seq": 1, "action": {"action": "read_file", "path": "/work/input.csv"}, "result": {"ok": True}},
            {"_seq": 2, "action": {"action": "write_file", "path": "/work/report.md"}, "result": {"ok": True}},
        ],
        {"seed_files": {"input.csv": "x"}, "checks": [{"path": "report.md"}]},
        "unit",
    )
    if artifact_ok.get("ok") is not True or artifact_ok.get("style") != "artifact_reads_before_writes":
        raise AssertionError(f"bad artifact orchestration success analysis: {artifact_ok}")
    artifact_bad = analyze_artifact_orchestration(
        [{"_seq": 1, "action": {"action": "write_file", "path": "/work/report.md"}, "result": {"ok": True}}],
        {"seed_files": {"input.csv": "x"}, "checks": [{"path": "report.md"}]},
        "unit",
    )
    if artifact_bad.get("ok") is not False or artifact_bad.get("missing_input_reads") != ["input.csv"]:
        raise AssertionError(f"bad artifact orchestration failure analysis: {artifact_bad}")
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
                    {
                        "agent": "shushunya",
                        "task_id": "artifact",
                        "ok": False,
                        "duration_sec": 3.0,
                        "exit_code": 0,
                        "checks": [{"type": "file_contains", "path": "report.md", "ok": False, "output": "AssertionError"}],
                        "orchestration": {
                            "style": "artifact_reads_before_writes",
                            "ok": False,
                            "missing_input_reads": ["input.csv"],
                            "missing_output_writes": [],
                        },
                    },
                ],
            },
        )
        analysis = analyze_reports([report])
        if analysis["stats"][0]["pass_rate"] != 0.333 or not analysis["recent_failures"]:
            raise AssertionError(f"bad arena report analysis: {analysis}")
        quality_rows = analysis.get("orchestration_quality", [])
        if not quality_rows or quality_rows[0].get("chain_pass_rate") != 0.5 or quality_rows[0].get("missing_edit") != 1:
            raise AssertionError(f"bad arena orchestration report analysis: {analysis}")
        artifact_rows = analysis.get("artifact_quality", [])
        if not artifact_rows or artifact_rows[0].get("missing_input_reads") != 1:
            raise AssertionError(f"bad arena artifact report analysis: {analysis}")
        if analysis.get("failure_reasons", {}).get("agent_exit") != 1 or analysis.get("failure_reasons", {}).get("post_run_checks") != 1:
            raise AssertionError(f"bad arena failure reason analysis: {analysis}")
        if analysis.get("failed_check_types", {}).get("file_contains") != 1:
            raise AssertionError(f"bad arena failed check type aggregation: {analysis}")
        if analysis.get("failed_check_symptoms", {}).get("assertion_error") != 1:
            raise AssertionError(f"bad arena failed check symptom aggregation: {analysis}")
        post_check_failures = [item for item in analysis["recent_failures"] if item.get("failure_reason") == "post_run_checks"]
        if not post_check_failures or post_check_failures[0].get("failed_checks", [{}])[0].get("path") != "report.md":
            raise AssertionError(f"bad arena failed check summary: {analysis}")
    print("[ok] AgentArena runner")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
