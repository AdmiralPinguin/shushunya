#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import runpy
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(ROOT))

from EyeOfTerror.Pictorium.Moriana.moriana_core.forge_reports import prune_reports
from forge_test_lock import forge_test_lock

DEFAULT_BASE_URL = "http://127.0.0.1:8110"
REPORTS_DIR = ROOT / "runtime" / "test-reports"


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_step(name: str, func) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = func()
    except Exception as exc:
        return {"name": name, "ok": False, "duration_sec": round(time.monotonic() - started, 3), "error": str(exc)}
    return {"name": name, "ok": True, "duration_sec": round(time.monotonic() - started, 3), "result": result}


def py_compile() -> dict[str, Any]:
    files = [
        "forge_service/config.py",
        "forge_service/projects.py",
        "forge_service/queue.py",
        "forge_service/server.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_core/asset_catalog.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_core/asset_downloader.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_core/character_profiles.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_core/forge_reports.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_core/image_evaluator.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_core/project_planner.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_core/prompt_thinker.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_core/promptwright.py",
        "../EyeOfTerror/Pictorium/Moriana/benches/quality_bench.py",
        "../EyeOfTerror/Pictorium/Moriana/benches/project_bench.py",
        "../EyeOfTerror/Pictorium/Moriana/benches/long_forge_api.py",
        "tests/smoke_forge_api.py",
    ]
    command = [str(ROOT / "DemonsForge/bin/python"), "-m", "py_compile", *files]
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=120)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip())
    return {"files": len(files)}


def smoke_test() -> dict[str, Any]:
    runpy.run_path(str(ROOT / "tests/smoke_forge_api.py"), run_name="__main__")
    return {"script": "tests/smoke_forge_api.py"}


def live_quality_dry_run(base_url: str) -> dict[str, Any]:
    health = requests.get(f"{base_url}/health", timeout=10)
    health.raise_for_status()
    completed = subprocess.run(
        [
            str(ROOT / "DemonsForge/bin/python"),
            "../EyeOfTerror/Pictorium/Moriana/benches/quality_bench.py",
            "--base-url",
            base_url,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=180,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip())
    return {"health": health.json(), "stdout": completed.stdout.strip().splitlines()[-3:]}


def write_summary(report: dict[str, Any], path: Path) -> str:
    lines = [
        "# Forge Self Test Report",
        "",
        f"Run ID: `{report['run_id']}`",
        "",
        f"Result: `{'pass' if report.get('ok') else 'fail'}`",
        f"Duration: `{report.get('duration_sec')}` seconds",
        "",
        "## Steps",
        "",
        "| Step | Result | Duration | Notes |",
        "| --- | ---: | ---: | --- |",
    ]
    for step in report["steps"]:
        result_label = "pass" if step.get("ok") else "skip" if step.get("skipped") else "fail"
        notes = step.get("error") or ""
        if not notes and isinstance(step.get("result"), dict):
            details = step["result"]
            if details.get("stdout"):
                notes = "; ".join(str(item) for item in details["stdout"][-2:])
            elif details.get("script"):
                notes = str(details["script"])
            elif details.get("files"):
                notes = f"{details['files']} files"
        lines.append(f"| `{step['name']}` | {result_label} | `{step.get('duration_sec')}` | {notes} |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def main() -> int:
    with forge_test_lock():
        return _main()


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--skip-live", action="store_true")
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--report-json", default="")
    args = parser.parse_args()

    run_id = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S-forge-self-test")
    started = time.monotonic()
    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": utc_now(),
        "base_url": args.base_url,
        "steps": [],
        "ok": False,
    }
    report["steps"].append(run_step("py_compile", py_compile))
    report["steps"].append(run_step("smoke_test", smoke_test))
    if not args.skip_live:
        live_step = run_step("live_quality_bench_dry_run", lambda: live_quality_dry_run(args.base_url.rstrip("/")))
        if args.require_live or live_step["ok"]:
            report["steps"].append(live_step)
        else:
            live_step["skipped"] = True
            report["steps"].append(live_step)
    report["finished_at"] = utc_now()
    report["duration_sec"] = round(time.monotonic() - started, 3)
    report["ok"] = all(step.get("ok") or step.get("skipped") for step in report["steps"])
    report_path = Path(args.report_json) if args.report_json else REPORTS_DIR / f"{run_id}.json"
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path = report_path.with_suffix(".md")
    report["summary_path"] = write_summary(report, summary_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    prune_reports()
    print(f"report: {report_path}", flush=True)
    print(json.dumps({"ok": report["ok"], "steps": [{k: step.get(k) for k in ("name", "ok", "skipped")} for step in report["steps"]]}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
