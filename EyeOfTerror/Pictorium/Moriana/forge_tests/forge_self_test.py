#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import runpy
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[4]
ROOT = PROJECT_ROOT / "DemonsForge"
TESTS_ROOT = PROJECT_ROOT / "EyeOfTerror" / "Pictorium" / "Moriana" / "forge_tests"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS_ROOT))

from EyeOfTerror.Pictorium.Moriana.moriana_core.forge_reports import prune_reports
from EyeOfTerror.model_brain import model_settings
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
        "../EyeOfTerror/Pictorium/Moriana/forge_runtime/config.py",
        "../EyeOfTerror/Pictorium/Moriana/forge_runtime/projects.py",
        "../EyeOfTerror/Pictorium/Moriana/forge_runtime/queue.py",
        "../EyeOfTerror/Pictorium/Moriana/forge_runtime/server.py",
        "../EyeOfTerror/Pictorium/Moriana/forge_runtime/client.py",
        "../EyeOfTerror/Pictorium/Moriana/forge_runtime/schemas.py",
        "../EyeOfTerror/Pictorium/Moriana/forge_runtime/storage.py",
        "../EyeOfTerror/Pictorium/Moriana/forge_runtime/archive_memory.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_core/asset_catalog.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_core/asset_downloader.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_core/character_profiles.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_core/forge_reports.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_core/image_evaluator.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_core/project_planner.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_core/prompt_thinker.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_core/promptwright.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_executor.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_forge_monitor.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_governor.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_quality.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_revision.py",
        "../EyeOfTerror/Pictorium/Moriana/moriana_runtime.py",
        "../EyeOfTerror/Pictorium/pictorium_model.py",
        "../EyeOfTerror/Pictorium/testing/fake_model_server.py",
        "../EyeOfTerror/Pictorium/Brigades/Image/worker_api.py",
        "../EyeOfTerror/Pictorium/Brigades/Image/Workers/Promptwright/worker.py",
        "../EyeOfTerror/Pictorium/Brigades/Image/Workers/ModelQuartermaster/worker.py",
        "../EyeOfTerror/Pictorium/Brigades/Image/Workers/ForgeDispatcher/worker.py",
        "../EyeOfTerror/Pictorium/Brigades/Image/Workers/ImageVerifier/worker.py",
        "../EyeOfTerror/Pictorium/Brigades/Image/Workers/ArtifactFinalis/worker.py",
        "../EyeOfTerror/Pictorium/Brigades/Image/self_test.py",
        "../EyeOfTerror/Pictorium/Brigades/Comics/worker_api.py",
        "../EyeOfTerror/Pictorium/Brigades/Comics/Workers/ScenarioScribe/worker.py",
        "../EyeOfTerror/Pictorium/Brigades/Comics/Workers/StoryboardArchitect/worker.py",
        "../EyeOfTerror/Pictorium/Brigades/Comics/Workers/CharacterSheetwright/worker.py",
        "../EyeOfTerror/Pictorium/Brigades/Comics/Workers/Panelwright/worker.py",
        "../EyeOfTerror/Pictorium/Brigades/Comics/Workers/LayoutFinalis/worker.py",
        "../EyeOfTerror/Pictorium/Brigades/Comics/self_test.py",
        "../EyeOfTerror/Pictorium/Moriana/benches/quality_bench.py",
        "../EyeOfTerror/Pictorium/Moriana/benches/project_bench.py",
        "../EyeOfTerror/Pictorium/Moriana/benches/long_forge_api.py",
        "../EyeOfTerror/Pictorium/Moriana/forge_tests/smoke_forge_api.py",
        "../EyeOfTerror/Pictorium/Moriana/forge_tests/moriana_e2e_self_test.py",
        "../EyeOfTerror/Pictorium/Moriana/forge_tests/moriana_quality_trials.py",
        "../EyeOfTerror/Pictorium/Moriana/forge_tests/moriana_live_quality_trials.py",
        "../EyeOfTerror/Pictorium/Moriana/forge_tests/moriana_service_self_test.py",
        "../EyeOfTerror/Pictorium/Moriana/forge_tests/moriana_runtime_self_test.py",
    ]
    command = [str(ROOT / "DemonsForge/bin/python"), "-m", "py_compile", *files]
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=120)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip())
    return {"files": len(files)}


def smoke_test() -> dict[str, Any]:
    runpy.run_path(str(TESTS_ROOT / "smoke_forge_api.py"), run_name="__main__")
    return {"script": "EyeOfTerror/Pictorium/Moriana/forge_tests/smoke_forge_api.py"}


def demonsforge_boundary_test() -> dict[str, Any]:
    forbidden = (
        "EyeOfTerror.Warmaster",
        "EyeOfTerror.Pictorium.Brigades",
        "moriana_governor",
        "moriana_executor",
        "moriana_quality",
        "moriana_revision",
        "Promptwright",
        "ModelQuartermaster",
        "ForgeDispatcher",
        "ImageVerifier",
        "ArtifactFinalis",
        "ScenarioScribe",
        "StoryboardArchitect",
        "CharacterSheetwright",
        "Panelwright",
        "LayoutFinalis",
    )
    ignored_parts = {"DemonsForge", "runtime", "artifacts", "__pycache__"}
    violations = []
    checked = 0
    for path in ROOT.rglob("*.py"):
        if any(part in ignored_parts for part in path.relative_to(ROOT).parts):
            continue
        checked += 1
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                violations.append({"path": str(path.relative_to(PROJECT_ROOT)), "marker": marker})
    if violations:
        raise RuntimeError(json.dumps({"violations": violations}, ensure_ascii=False))
    return {"checked_python_files": checked, "forbidden_markers": len(forbidden)}


def moriana_quality_trials() -> dict[str, Any]:
    completed = subprocess.run(
        [
            str(ROOT / "DemonsForge/bin/python"),
            "../EyeOfTerror/Pictorium/Moriana/forge_tests/moriana_quality_trials.py",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip())
    stdout = completed.stdout.strip()
    parsed: dict[str, Any] = {}
    if stdout:
        try:
            loaded = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            parsed = loaded
    return {
        "script": "EyeOfTerror/Pictorium/Moriana/forge_tests/moriana_quality_trials.py",
        "stdout": stdout,
        "trial_count": parsed.get("trial_count"),
        "coverage_gap_count": parsed.get("coverage_gap_count"),
        "avg_quality_score": parsed.get("avg_quality_score"),
        "evidence_adjusted_score": parsed.get("evidence_adjusted_score"),
        "readiness_verdict": parsed.get("readiness_verdict"),
    }


def moriana_e2e_self_test() -> dict[str, Any]:
    completed = subprocess.run(
        [
            str(ROOT / "DemonsForge/bin/python"),
            "../EyeOfTerror/Pictorium/Moriana/forge_tests/moriana_e2e_self_test.py",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip())
    return {"script": "EyeOfTerror/Pictorium/Moriana/forge_tests/moriana_e2e_self_test.py", "stdout": completed.stdout.strip()}


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


def model_endpoint_reachable(timeout_sec: float = 2.0) -> dict[str, Any]:
    settings = model_settings()
    parsed = urlparse(str(settings.get("base_url") or ""))
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return {"ok": False, "base_url": settings.get("base_url"), "error": "model base_url has no host"}
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return {"ok": True, "base_url": settings.get("base_url"), "model": settings.get("model")}
    except OSError as exc:
        return {"ok": False, "base_url": settings.get("base_url"), "model": settings.get("model"), "error": str(exc)}


def moriana_live_quality_trials() -> dict[str, Any]:
    model_preflight = model_endpoint_reachable()
    if not model_preflight.get("ok"):
        raise RuntimeError(f"model endpoint is not reachable for Moriana live trials: {model_preflight}")
    completed = subprocess.run(
        [
            str(ROOT / "DemonsForge/bin/python"),
            "../EyeOfTerror/Pictorium/Moriana/forge_tests/moriana_live_quality_trials.py",
            "--profile",
            "smoke",
            "--max-wait-sec",
            "1800",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=2400,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip())
    stdout = completed.stdout.strip()
    parsed: dict[str, Any] = {}
    if stdout:
        try:
            loaded = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            parsed = loaded
    return {
        "script": "EyeOfTerror/Pictorium/Moriana/forge_tests/moriana_live_quality_trials.py",
        "stdout": stdout,
        "trial_count": parsed.get("trial_count"),
        "weak_case_count": parsed.get("weak_case_count"),
        "avg_quality_score": parsed.get("avg_quality_score"),
        "readiness_verdict": parsed.get("readiness_verdict"),
    }


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
            if details.get("readiness_verdict"):
                notes = (
                    f"verdict={details.get('readiness_verdict')}; "
                    f"evidence_score={details.get('evidence_adjusted_score', details.get('avg_quality_score'))}; "
                    f"coverage_gaps={details.get('coverage_gap_count', 'n/a')}; "
                    f"weak_cases={details.get('weak_case_count', 'n/a')}"
                )
            elif details.get("stdout"):
                stdout = details["stdout"]
                lines = stdout.splitlines() if isinstance(stdout, str) else [str(item) for item in stdout]
                notes = "; ".join(lines[-2:])
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
    parser.add_argument("--skip-live", action="store_true", help="Compatibility flag; live checks are skipped unless --run-live or --require-live is set.")
    parser.add_argument("--run-live", action="store_true", help="Run optional live visual checks.")
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
    report["steps"].append(run_step("demonsforge_boundary_test", demonsforge_boundary_test))
    report["steps"].append(run_step("moriana_e2e_self_test", moriana_e2e_self_test))
    report["steps"].append(run_step("moriana_quality_trials", moriana_quality_trials))
    run_live = (args.run_live or args.require_live) and not args.skip_live
    if run_live:
        live_step = run_step("live_quality_bench_dry_run", lambda: live_quality_dry_run(args.base_url.rstrip("/")))
        if args.require_live or live_step["ok"]:
            report["steps"].append(live_step)
        else:
            live_step["skipped"] = True
            report["steps"].append(live_step)
        moriana_live_step = run_step("moriana_live_quality_trials", moriana_live_quality_trials)
        if args.require_live or moriana_live_step["ok"]:
            report["steps"].append(moriana_live_step)
        else:
            moriana_live_step["skipped"] = True
            report["steps"].append(moriana_live_step)
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
