#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge_service.reports import prune_reports
from forge_test_lock import forge_test_lock


DEFAULT_BASE_URL = "http://127.0.0.1:8110"
REPORTS_DIR = ROOT / "runtime" / "test-reports"


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def request_json(method: str, url: str, **kwargs: Any) -> Any:
    response = requests.request(method, url, timeout=60, **kwargs)
    response.raise_for_status()
    return response.json()


def wait_job(base_url: str, job_id: str, timeout_seconds: int = 2400) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status = None
    while time.monotonic() < deadline:
        job = request_json("GET", f"{base_url}/forge/jobs/{job_id}")
        if job["status"] != last_status:
            print(f"{job_id}: {job['status']} progress={job.get('progress')}", flush=True)
            last_status = job["status"]
        if job["status"] in {"succeeded", "failed", "canceled"}:
            return job
        time.sleep(5)
    raise TimeoutError(f"job timed out: {job_id}")


def make_contact_sheet(items: list[tuple[str, str]], path: Path) -> str:
    thumbs = []
    for label, image_path in items:
        image = Image.open(image_path).convert("RGB")
        image.thumbnail((256, 256))
        thumbs.append((label, image.copy()))
    if not thumbs:
        raise ValueError("no images for contact sheet")
    width = 320 * min(2, len(thumbs))
    rows = (len(thumbs) + 1) // 2
    height = 320 * rows
    sheet = Image.new("RGB", (width, height), (24, 24, 28))
    draw = ImageDraw.Draw(sheet)
    for index, (label, image) in enumerate(thumbs):
        x = (index % 2) * 320 + 32
        y = (index // 2) * 320 + 24
        sheet.paste(image, (x, y))
        draw.text((x, y + image.height + 8), label[:42], fill=(230, 230, 230))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)
    return str(path)


def write_summary(report: dict[str, Any], path: Path) -> str:
    lines = [
        "# Shushunya Project Bench",
        "",
        f"Run ID: `{report['run_id']}`",
        f"Mode: `{'run' if report['run_jobs'] else 'dry-run'}`",
        f"Project type: `{report['project_request']['project_type']}`",
        f"Result: `{'pass' if report.get('ok') else 'fail'}`",
        f"Duration: `{report.get('duration_sec')}` seconds",
        "",
        "## Steps",
        "",
        "| Step | Engine | Status | Job | Artifact | Warnings |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    for step in report.get("steps", []):
        warnings = []
        evaluation = step.get("evaluation") or {}
        actual = evaluation.get("actual_image") or {}
        stddev = actual.get("stddev") if isinstance(actual, dict) else None
        if isinstance(stddev, list) and stddev and sum(float(value) for value in stddev) / len(stddev) < 5.0:
            warnings.append("flat_or_blank_image")
        metadata = step.get("metadata") or {}
        step_count = int(metadata.get("steps") or step.get("steps") or 0)
        if step.get("engine") == "sdxl" and step_count < 12:
            warnings.append("sdxl_understepped_noise_risk")
        if (
            step.get("engine") == "sdxl"
            and isinstance(stddev, list)
            and stddev
            and sum(float(value) for value in stddev) / len(stddev) > 70.0
            and int(metadata.get("image_size_bytes") or 0) > 300_000
        ):
            warnings.append("sdxl_high_variance_pattern_risk")
        lines.append(
            f"| `{step.get('step_id')}` | `{step.get('engine')}` | `{step.get('status')}` | "
            f"`{step.get('job_id') or ''}` | `{step.get('artifact_id') or ''}` | {', '.join(warnings)} |"
        )
    if report.get("review_criteria"):
        criteria = report["review_criteria"]
        lines.extend(
            [
                "",
                "## Review Criteria",
                "",
                f"- character_id: `{criteria.get('character_id')}`",
                f"- must_preserve: {', '.join(criteria.get('must_preserve') or [])}",
                f"- avoid: {', '.join(criteria.get('avoid') or [])}",
            ]
        )
        for item in criteria.get("manual_focus") or []:
            lines.append(f"- {item}")
    if report.get("contact_sheet"):
        lines.extend(["", "## Contact Sheet", "", f"`{report['contact_sheet']}`", ""])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def main() -> int:
    with forge_test_lock():
        return _main()


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--project-type", choices=["concept_batch", "comic_storyboard", "character_sheet"], default="comic_storyboard")
    parser.add_argument("--variants", type=int, default=2)
    parser.add_argument("--panels", type=int, default=4)
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=512)
    parser.add_argument("--engine-strategy", choices=["auto", "planner", "mixed_concept"], default="auto")
    parser.add_argument("--request", default="Сделай максимально устрашающий комикс про Шушуню в демонической кузне")
    parser.add_argument("--report-json", default="")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    run_id = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S-shushunya-project")
    started = time.monotonic()
    project_request = {
        "request": args.request,
        "project_type": args.project_type,
        "variants": args.variants,
        "panels": args.panels,
        "width": args.width,
        "height": args.height,
        "engine_strategy": args.engine_strategy,
        "use_memory": False,
        "use_thinker": False,
    }
    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": utc_now(),
        "base_url": base_url,
        "run_jobs": bool(args.run),
        "project_request": project_request,
        "ok": False,
        "steps": [],
    }

    health = request_json("GET", f"{base_url}/health")
    report["health"] = health
    dry_run = request_json("POST", f"{base_url}/forge/projects?dry_run=true", json=project_request)
    report["dry_run"] = dry_run
    character_profile = dry_run.get("project", {}).get("character_profile") or {}
    if character_profile:
        report["review_criteria"] = {
            "character_id": character_profile.get("id"),
            "must_preserve": character_profile.get("must_preserve", []),
            "avoid": character_profile.get("avoid", []),
            "manual_focus": [
                "reject images that read as a mostly normal blue cat",
                "prefer obvious asymmetry and mostly mutated demon flesh",
                "prefer visible preserved blue cat fragments rather than full blue fur coverage",
            ],
        }
    validations = dry_run.get("validations", [])
    if not all(item.get("valid") for item in validations):
        report["finished_at"] = utc_now()
        report["duration_sec"] = round(time.monotonic() - started, 3)
        report["ok"] = False
    elif args.run:
        created = request_json("POST", f"{base_url}/forge/projects", json=project_request)
        report["created"] = created
        sheet_items: list[tuple[str, str]] = []
        for step in created["project"]["steps"]:
            entry: dict[str, Any] = {
                "step_id": step["id"],
                "role": step["role"],
                "engine": step["spec"].get("engine"),
                "job_id": step.get("job_id"),
            }
            if step.get("job_id"):
                finished = wait_job(base_url, step["job_id"])
                entry["status"] = finished["status"]
                entry["progress"] = finished.get("progress")
                entry["artifacts"] = finished.get("artifacts", [])
                if finished["status"] == "succeeded" and finished.get("artifacts"):
                    artifact_id = finished["artifacts"][0]
                    entry["artifact_id"] = artifact_id
                    metadata = request_json("GET", f"{base_url}/forge/artifacts/{artifact_id}/metadata")
                    evaluation = request_json("GET", f"{base_url}/forge/artifacts/{artifact_id}/evaluation")
                    entry["metadata"] = metadata
                    entry["evaluation"] = evaluation
                    sheet_items.append((step["id"], metadata["path"]))
                elif finished.get("error"):
                    entry["error"] = finished["error"]
            report["steps"].append(entry)
        report["refreshed_project"] = request_json("POST", f"{base_url}/forge/projects/{created['project']['id']}/refresh")
        if sheet_items:
            report["contact_sheet"] = make_contact_sheet(sheet_items, REPORTS_DIR / f"{run_id}-contact-sheet.png")
        report["ok"] = all(step.get("status") == "succeeded" for step in report["steps"])
    else:
        project = dry_run["project"]
        for step, validation in zip(project["steps"], validations):
            report["steps"].append(
                {
                    "step_id": step["id"],
                    "role": step["role"],
                    "engine": step["spec"].get("engine"),
                    "steps": step["spec"].get("steps"),
                    "status": "dry-run-valid" if validation.get("valid") else "dry-run-invalid",
                    "validation": validation,
                }
            )
        report["ok"] = True

    report["finished_at"] = utc_now()
    report["duration_sec"] = round(time.monotonic() - started, 3)
    report_path = Path(args.report_json) if args.report_json else REPORTS_DIR / f"{run_id}.json"
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path = report_path.with_suffix(".md")
    report["summary_path"] = write_summary(report, summary_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    prune_reports()
    print(f"report: {report_path}", flush=True)
    print(f"summary: {summary_path}", flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
