#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "http://127.0.0.1:8110"
QUALITY_ASSETS = ROOT / "quality_assets"
EXPECTED_NOTES = QUALITY_ASSETS / "expected_notes.json"
GENERATED_ASSETS = QUALITY_ASSETS / "generated"
REPORTS_DIR = ROOT / "runtime" / "test-reports"


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def request_json(method: str, url: str, **kwargs: Any) -> Any:
    response = requests.request(method, url, timeout=60, **kwargs)
    response.raise_for_status()
    return response.json()


def wait_job(base_url: str, job_id: str, timeout_seconds: int = 1800) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status = None
    while time.monotonic() < deadline:
        job = request_json("GET", f"{base_url}/forge/jobs/{job_id}")
        status = job["status"]
        if status != last_status:
            print(f"{job_id}: {status} progress={job.get('progress')}", flush=True)
            last_status = status
        if status in {"succeeded", "failed", "canceled"}:
            return job
        time.sleep(2.0)
    raise TimeoutError(f"job did not finish within {timeout_seconds}s: {job_id}")


def prepare_assets() -> dict[str, str]:
    GENERATED_ASSETS.mkdir(parents=True, exist_ok=True)
    source = GENERATED_ASSETS / "humanoid_forge_source.png"
    background_mask = GENERATED_ASSETS / "background_top_mask.png"

    image = Image.new("RGB", (512, 512), (35, 31, 30))
    draw = ImageDraw.Draw(image)
    draw.rectangle((34, 38, 478, 474), outline=(61, 48, 42), width=9)
    draw.rectangle((124, 188, 388, 448), fill=(86, 48, 40), outline=(132, 75, 52), width=6)
    draw.ellipse((186, 74, 326, 214), fill=(151, 83, 59), outline=(190, 116, 82), width=5)
    draw.polygon([(256, 216), (198, 326), (314, 326)], fill=(52, 42, 39), outline=(164, 92, 62))
    draw.line((112, 354, 400, 354), fill=(180, 98, 55), width=10)
    draw.line((76, 412, 436, 412), fill=(195, 119, 60), width=6)
    draw.rectangle((52, 86, 154, 132), fill=(72, 56, 44))
    draw.rectangle((358, 86, 460, 132), fill=(72, 56, 44))
    image.save(source)

    mask = Image.new("L", (512, 512), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rectangle((0, 0, 512, 172), fill=255)
    mask_draw.ellipse((174, 62, 338, 226), fill=0)
    mask_draw.rectangle((148, 160, 364, 450), fill=0)
    mask.save(background_mask)
    return {"source": str(source), "background_mask": str(background_mask)}


def make_contact_sheet(items: list[tuple[str, str]], output_path: Path) -> str:
    thumbs = []
    for label, path in items:
        image = Image.open(path).convert("RGB")
        image.thumbnail((256, 256))
        canvas = Image.new("RGB", (256, 300), "white")
        canvas.paste(image, ((256 - image.width) // 2, 30))
        draw = ImageDraw.Draw(canvas)
        draw.text((8, 8), label[:36], fill=(0, 0, 0))
        thumbs.append(canvas)
    sheet = Image.new("RGB", (256 * len(thumbs), 300), "white")
    for index, thumb in enumerate(thumbs):
        sheet.paste(thumb, (256 * index, 0))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path)
    return str(output_path)


def load_expected_notes() -> dict[str, Any]:
    return json.loads(EXPECTED_NOTES.read_text(encoding="utf-8"))


def scenario_specs(assets: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "name": "sdxl_txt2img_forge_character",
            "spec": {
                "type": "txt2img",
                "engine": "sdxl",
                "model": "stable-diffusion-xl-base-1.0",
                "prompt": (
                    "quality bench, cinematic demonic blacksmith in a fiery forge, "
                    "clear humanoid silhouette, warm rim light, coherent portrait"
                ),
                "negative_prompt": "abstract noise, blank image, blurry, low quality",
                "width": 512,
                "height": 512,
                "steps": 10,
                "guidance": 7.0,
                "seed": 61001,
                "quality_preset": "quality_bench_txt2img",
            },
        },
        {
            "name": "sdxl_img2img_identity_balanced",
            "spec": {
                "type": "img2img",
                "engine": "sdxl",
                "model": "stable-diffusion-xl-base-1.0",
                "prompt": (
                    "convert the source into a cinematic demonic blacksmith portrait, "
                    "keep central humanoid silhouette and face position, add forge lighting"
                ),
                "negative_prompt": "abstract emblem, logo, low quality, blurry, distorted face",
                "source_images": [assets["source"]],
                "width": 512,
                "height": 512,
                "steps": 14,
                "strength": 0.62,
                "guidance": 7.0,
                "seed": 61002,
                "quality_preset": "quality_bench_img2img_balanced",
            },
        },
        {
            "name": "sdxl_inpaint_background_localized",
            "spec": {
                "type": "inpaint",
                "engine": "sdxl",
                "model": "stable-diffusion-xl-base-1.0",
                "prompt": "replace masked top background with fiery forge sparks and orange furnace light",
                "negative_prompt": "low quality, blurry, face destroyed, overpainted subject",
                "source_images": [assets["source"]],
                "mask_image": assets["background_mask"],
                "width": 512,
                "height": 512,
                "steps": 10,
                "strength": 0.72,
                "guidance": 7.0,
                "seed": 61003,
                "quality_preset": "quality_bench_inpaint_background",
            },
        },
    ]


def write_summary(report: dict[str, Any], path: Path) -> str:
    lines = [
        "# DemonsForge Quality Bench",
        "",
        f"- run_id: `{report['run_id']}`",
        f"- mode: `{'run' if report['run_jobs'] else 'dry-run'}`",
        f"- ok: `{report['ok']}`",
        f"- started_at: `{report['started_at']}`",
        f"- finished_at: `{report.get('finished_at', '')}`",
        "",
        "## Scenarios",
        "",
    ]
    for scenario in report["scenarios"]:
        lines.append(f"### {scenario['name']}")
        lines.append("")
        lines.append(f"- dry_run_ok: `{scenario.get('dry_run_ok')}`")
        if scenario.get("job_id"):
            lines.append(f"- job_id: `{scenario['job_id']}`")
            lines.append(f"- artifact_id: `{scenario.get('artifact_id')}`")
            lines.append(f"- duration_sec: `{scenario.get('duration_sec')}`")
        evaluation = scenario.get("evaluation") or {}
        if evaluation:
            lines.append(f"- dimension_match: `{evaluation.get('dimension_match', {}).get('ok')}`")
            if "diff_from_first_source" in evaluation:
                lines.append(f"- diff_from_first_source: `{evaluation['diff_from_first_source']}`")
            if "edit_delta_hint" in evaluation:
                lines.append(f"- edit_delta: `{evaluation['edit_delta_hint'].get('class')}`")
            if "inpaint_localization_hint" in evaluation:
                hint = evaluation["inpaint_localization_hint"]
                lines.append(
                    f"- inpaint_ratio: `{hint.get('ratio')}`, "
                    f"underpaint_risk: `{hint.get('underpaint_risk')}`, "
                    f"overpaint_risk: `{hint.get('overpaint_risk')}`"
                )
        notes = scenario.get("expected_notes") or {}
        for key in ("must_keep", "must_change", "must_include", "failure_modes"):
            if notes.get(key):
                lines.append(f"- {key}: {', '.join(notes[key])}")
        lines.append("")
    if report.get("contact_sheet"):
        lines.extend(["## Contact Sheet", "", f"`{report['contact_sheet']}`", ""])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--run", action="store_true", help="enqueue real generation jobs")
    parser.add_argument("--report-json", default="")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    run_id = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S-forge-quality")
    started = time.monotonic()
    assets = prepare_assets()
    expected = load_expected_notes()
    expected_by_name = {item["name"]: item for item in expected["scenarios"]}
    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": utc_now(),
        "base_url": base_url,
        "run_jobs": bool(args.run),
        "assets": assets,
        "expected_notes_path": str(EXPECTED_NOTES),
        "scenarios": [],
        "ok": False,
    }
    sheet_items: list[tuple[str, str]] = [("source", assets["source"])]
    for scenario in scenario_specs(assets):
        name = scenario["name"]
        spec = scenario["spec"]
        entry: dict[str, Any] = {
            "name": name,
            "spec": spec,
            "expected_notes": expected_by_name.get(name, {}),
        }
        dry_run = requests.post(f"{base_url}/forge/jobs?dry_run=true", json=spec, timeout=60)
        entry["dry_run_status_code"] = dry_run.status_code
        entry["dry_run_ok"] = dry_run.status_code == 200
        if dry_run.status_code != 200:
            entry["dry_run_error"] = dry_run.text
            report["scenarios"].append(entry)
            continue
        entry["dry_run"] = dry_run.json()
        if args.run:
            started_job = time.monotonic()
            record = request_json("POST", f"{base_url}/forge/jobs", json=spec)
            finished = wait_job(base_url, record["id"])
            entry["job_id"] = finished["id"]
            entry["status"] = finished["status"]
            entry["duration_sec"] = round(time.monotonic() - started_job, 3)
            if finished["status"] != "succeeded":
                entry["error"] = finished.get("error")
            else:
                artifact_id = finished["artifacts"][0]
                entry["artifact_id"] = artifact_id
                metadata = request_json("GET", f"{base_url}/forge/artifacts/{artifact_id}/metadata")
                evaluation = request_json("GET", f"{base_url}/forge/artifacts/{artifact_id}/evaluation")
                entry["metadata"] = metadata
                entry["evaluation"] = evaluation
                sheet_items.append((name, metadata["path"]))
        report["scenarios"].append(entry)
        print(f"{name}: dry_run={entry['dry_run_ok']} status={entry.get('status', 'not-run')}", flush=True)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.run and len(sheet_items) > 1:
        report["contact_sheet"] = make_contact_sheet(sheet_items, REPORTS_DIR / f"{run_id}-contact-sheet.png")
    report["finished_at"] = utc_now()
    report["duration_sec"] = round(time.monotonic() - started, 3)
    report["ok"] = all(item.get("dry_run_ok") and item.get("status", "succeeded") == "succeeded" for item in report["scenarios"])
    report_path = Path(args.report_json) if args.report_json else REPORTS_DIR / f"{run_id}.json"
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary_path = report_path.with_suffix(".md")
    report["summary_path"] = write_summary(report, summary_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"report: {report_path}", flush=True)
    print(f"summary: {summary_path}", flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
