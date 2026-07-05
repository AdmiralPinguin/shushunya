#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(ROOT))

from EyeOfTerror.Pictorium.Moriana.moriana_core.forge_reports import prune_reports
from forge_test_lock import LOCK_ENV, forge_test_lock

PYTHON = ROOT / "DemonsForge/bin/python"
REPORTS_DIR = ROOT / "runtime" / "test-reports"


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_command(name: str, command: list[str], timeout_seconds: int) -> dict[str, Any]:
    started = time.monotonic()
    env = {**os.environ, LOCK_ENV: "1"}
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=timeout_seconds, env=env)
    return {
        "name": name,
        "command": command,
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "duration_sec": round(time.monotonic() - started, 3),
        "stdout_tail": completed.stdout.strip().splitlines()[-20:],
        "stderr_tail": completed.stderr.strip().splitlines()[-20:],
    }


def cycle_once(index: int, args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    steps = [
        run_command(
            "self_test",
            [str(PYTHON), "tests/forge_self_test.py"],
            timeout_seconds=args.self_test_timeout,
        )
    ]
    bench_command = [str(PYTHON), "../EyeOfTerror/Pictorium/Moriana/benches/quality_bench.py"]
    if args.quality_run:
        bench_command.append("--run")
    if args.concept_engines:
        bench_command.append("--concept-engines")
    steps.append(run_command("quality_bench", bench_command, timeout_seconds=args.quality_timeout))
    return {
        "cycle": index,
        "started_at": utc_now(),
        "duration_sec": round(time.monotonic() - started, 3),
        "steps": steps,
        "ok": all(step["ok"] for step in steps),
    }


def write_summary(report: dict[str, Any], path: Path) -> str:
    lines = [
        "# Forge Cycle Report",
        "",
        f"Run ID: `{report['run_id']}`",
        "",
        f"Result: `{'pass' if report.get('ok') else 'fail'}`",
        f"Duration: `{report.get('duration_sec')}` seconds",
        f"Iterations: `{report.get('iterations')}`",
        f"Quality run: `{report.get('quality_run')}`",
        f"Concept engines: `{report.get('concept_engines')}`",
        "",
        "## Cycles",
        "",
    ]
    for cycle in report["cycles"]:
        lines.extend(
            [
                f"### Cycle {cycle['cycle']}",
                "",
                f"- result: `{'pass' if cycle.get('ok') else 'fail'}`",
                f"- duration_sec: `{cycle.get('duration_sec')}`",
                "",
                "| Step | Result | Duration | Exit | Notes |",
                "| --- | ---: | ---: | ---: | --- |",
            ]
        )
        for step in cycle["steps"]:
            result = "pass" if step.get("ok") else "fail"
            stdout_tail = step.get("stdout_tail") or []
            stderr_tail = step.get("stderr_tail") or []
            notes = ""
            if not step.get("ok") and stderr_tail:
                notes = str(stderr_tail[-1])
            elif stdout_tail:
                notes = str(stdout_tail[-1])
            lines.append(
                f"| `{step['name']}` | {result} | `{step.get('duration_sec')}` | "
                f"`{step.get('returncode')}` | {notes} |"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def main() -> int:
    with forge_test_lock():
        return _main()


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--sleep-seconds", type=int, default=0)
    parser.add_argument("--quality-run", action="store_true")
    parser.add_argument("--concept-engines", action="store_true")
    parser.add_argument("--self-test-timeout", type=int, default=300)
    parser.add_argument("--quality-timeout", type=int, default=3600)
    parser.add_argument("--report-json", default="")
    args = parser.parse_args()

    run_id = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S-forge-cycle")
    started = time.monotonic()
    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": utc_now(),
        "iterations": max(1, args.iterations),
        "sleep_seconds": max(0, args.sleep_seconds),
        "quality_run": bool(args.quality_run),
        "concept_engines": bool(args.concept_engines),
        "cycles": [],
        "ok": False,
    }
    for index in range(1, max(1, args.iterations) + 1):
        cycle = cycle_once(index, args)
        cycle["finished_at"] = utc_now()
        report["cycles"].append(cycle)
        print(json.dumps({"cycle": index, "ok": cycle["ok"]}, ensure_ascii=False), flush=True)
        if index < max(1, args.iterations) and args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)
    report["finished_at"] = utc_now()
    report["duration_sec"] = round(time.monotonic() - started, 3)
    report["ok"] = all(cycle["ok"] for cycle in report["cycles"])
    report_path = Path(args.report_json) if args.report_json else REPORTS_DIR / f"{run_id}.json"
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path = report_path.with_suffix(".md")
    report["summary_path"] = write_summary(report, summary_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    prune_reports()
    print(f"report: {report_path}", flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
