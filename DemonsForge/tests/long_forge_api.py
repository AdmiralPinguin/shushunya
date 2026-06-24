#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageChops, ImageDraw, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forge_service.reports import prune_reports
from forge_test_lock import forge_test_lock


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "http://127.0.0.1:8110"
REPORTS_DIR = ROOT / "runtime" / "test-reports"


SCENARIOS = [
    {
        "name": "sd35_concept",
        "request": "Нарисуй темный кинематографичный портрет демона в кузнице 512x512 steps 1",
        "expect": {"type": "txt2img", "engine": "stable_diffusion"},
    },
    {
        "name": "explicit_flux",
        "request": "flux 512x512 steps 1 first concept image, atmospheric fire and steel",
        "expect": {"type": "txt2img", "engine": "flux"},
    },
    {
        "name": "sdxl_edit",
        "request": "Сделай вариацию по картинке в кинематографичном стиле 512x512 steps 2",
        "expect": {"type": "img2img", "engine": "sdxl", "required_inputs": ["source_images"]},
    },
    {
        "name": "sdxl_inpaint",
        "request": "Инпейнт: замени фон по маске на адскую кузницу 512x512 steps 2",
        "expect": {"type": "inpaint", "engine": "sdxl", "required_inputs": ["source_images", "mask_image"]},
    },
    {
        "name": "upscale",
        "request": "Увеличь картинку в 4 раза",
        "expect": {"type": "upscale", "engine": None, "required_inputs": ["source_images"]},
    },
    {
        "name": "missing_lora",
        "request": "Нарисуй персонажа lora: long_test_missing_lora",
        "expect": {"asset_type": "lora"},
    },
    {
        "name": "missing_control",
        "request": "Сделай pose controlnet depth для персонажа",
        "expect": {"asset_type": "control_asset"},
    },
]


def request_json(method: str, url: str, **kwargs: Any) -> Any:
    response = requests.request(method, url, timeout=60, **kwargs)
    response.raise_for_status()
    return response.json()


def assert_plan(name: str, spec: dict[str, Any], expect: dict[str, Any]) -> None:
    for key in ("type", "engine"):
        if key in expect and spec.get(key) != expect[key]:
            raise AssertionError(f"{name}: expected {key}={expect[key]!r}, got {spec.get(key)!r}")
    if "required_inputs" in expect:
        actual = spec.get("safety", {}).get("required_inputs")
        if actual != expect["required_inputs"]:
            raise AssertionError(f"{name}: expected required_inputs={expect['required_inputs']!r}, got {actual!r}")
    if "asset_type" in expect:
        asset_request = spec.get("asset_request") or {}
        if asset_request.get("asset_type") != expect["asset_type"]:
            raise AssertionError(f"{name}: expected asset_type={expect['asset_type']!r}, got {asset_request!r}")
        if asset_request.get("requires_user_approval") is not True:
            raise AssertionError(f"{name}: asset_request must require user approval")
    if "planner_thinker" not in spec.get("safety", {}):
        raise AssertionError(f"{name}: planner_thinker metadata missing")


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_planner_matrix(base_url: str, cycles: int, report: dict[str, Any]) -> None:
    for cycle in range(1, cycles + 1):
        started = time.monotonic()
        cycle_checks = []
        for scenario in SCENARIOS:
            spec = request_json(
                "POST",
                f"{base_url}/forge/plan",
                json={
                    "request": scenario["request"],
                    "use_memory": False,
                },
            )
            assert_plan(scenario["name"], spec, scenario["expect"])
            cycle_checks.append({"scenario": scenario["name"], "type": spec.get("type"), "engine": spec.get("engine")})
            if spec.get("asset_request") is None:
                dry_run = requests.post(f"{base_url}/forge/jobs?dry_run=true", json=spec, timeout=60)
                if scenario["expect"].get("required_inputs"):
                    if dry_run.status_code != 400:
                        raise AssertionError(f"{scenario['name']}: missing-input dry-run should fail")
                elif dry_run.status_code != 200:
                    raise AssertionError(f"{scenario['name']}: dry-run failed: {dry_run.text}")
        elapsed = time.monotonic() - started
        report.setdefault("planner_cycles", []).append(
            {"cycle": cycle, "duration_sec": round(elapsed, 3), "checks": cycle_checks}
        )
        print(f"cycle {cycle}/{cycles}: planner matrix ok in {elapsed:.2f}s", flush=True)


def wait_job(base_url: str, job_id: str, timeout_seconds: int = 900) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status = None
    while time.monotonic() < deadline:
        job = request_json("GET", f"{base_url}/forge/jobs/{job_id}")
        status = job["status"]
        if status != last_status:
            print(f"job {job_id}: {status} progress={job.get('progress')}", flush=True)
            last_status = status
        if status in {"succeeded", "failed", "canceled"}:
            return job
        time.sleep(2)
    raise TimeoutError(f"job did not finish within {timeout_seconds}s: {job_id}")


def mean_abs_difference(left: str, right: str) -> float:
    with Image.open(left) as left_image, Image.open(right) as right_image:
        left_rgb = left_image.convert("RGB")
        right_rgb = right_image.convert("RGB").resize(left_rgb.size)
        diff = ImageChops.difference(left_rgb, right_rgb)
        stat = ImageStat.Stat(diff)
        return sum(stat.mean) / len(stat.mean)


def masked_mean_abs_difference(left: str, right: str, mask: str) -> dict[str, float]:
    with Image.open(left) as left_image, Image.open(right) as right_image, Image.open(mask) as mask_image:
        left_rgb = left_image.convert("RGB")
        right_rgb = right_image.convert("RGB").resize(left_rgb.size)
        mask_l = mask_image.convert("L").resize(left_rgb.size)
        diff = ImageChops.difference(left_rgb, right_rgb)
        masked = ImageStat.Stat(diff, mask_l)
        unmasked = ImageStat.Stat(diff, ImageChops.invert(mask_l))
        return {
            "masked": round(sum(masked.mean) / len(masked.mean), 3),
            "unmasked": round(sum(unmasked.mean) / len(unmasked.mean), 3),
        }


def artifact_metadata(base_url: str, artifact_id: str) -> dict[str, Any]:
    return request_json("GET", f"{base_url}/forge/artifacts/{artifact_id}/metadata")


def assert_image_changed(base_url: str, artifact_id: str, source: str, min_mean_diff: float, label: str) -> None:
    metadata = artifact_metadata(base_url, artifact_id)
    output = metadata["path"]
    diff = mean_abs_difference(source, output)
    if diff < min_mean_diff:
        raise AssertionError(f"{label}: output changed too little, mean abs diff={diff:.3f}")
    print(f"{label}: mean abs diff={diff:.3f}", flush=True)


def image_stats(path: str) -> dict[str, Any]:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        stat = ImageStat.Stat(rgb)
        return {
            "width": rgb.width,
            "height": rgb.height,
            "mean": [round(value, 3) for value in stat.mean],
            "stddev": [round(value, 3) for value in stat.stddev],
        }


def make_contact_sheet(items: list[tuple[str, str]], output_path: Path) -> str:
    thumbs = []
    for label, path in items:
        image = Image.open(path).convert("RGB")
        image.thumbnail((256, 256))
        canvas = Image.new("RGB", (256, 296), "white")
        canvas.paste(image, ((256 - image.width) // 2, 28))
        draw = ImageDraw.Draw(canvas)
        draw.text((8, 6), label[:36], fill=(0, 0, 0))
        draw.text((8, 276), Path(path).parent.name[:28], fill=(40, 40, 40))
        thumbs.append(canvas)
    sheet = Image.new("RGB", (256 * len(thumbs), 296), "white")
    for index, thumb in enumerate(thumbs):
        sheet.paste(thumb, (256 * index, 0))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path)
    return str(output_path)


def run_downloader_safety(base_url: str, report: dict[str, Any]) -> None:
    bad_specs = [
        {
            "type": "asset-download",
            "asset_download": {
                "name": "blocked_without_approval",
                "asset_type": "lora",
                "source_url": "https://huggingface.co/example/example/resolve/main/file.safetensors",
                "approved": False,
            },
        },
        {
            "type": "asset-download",
            "asset_download": {
                "name": "blocked_unverified_url",
                "asset_type": "lora",
                "source_url": "http://127.0.0.1/file.safetensors",
                "approved": True,
            },
        },
    ]
    for spec in bad_specs:
        response = requests.post(f"{base_url}/forge/jobs?dry_run=true", json=spec, timeout=60)
        if response.status_code != 400:
            raise AssertionError(f"asset downloader safety should reject spec: {response.status_code} {response.text}")
        report.setdefault("checks", []).append(
            {"name": "asset_downloader_safety", "ok": True, "status_code": response.status_code}
        )
    print("asset downloader safety ok", flush=True)


def prepare_generation_inputs() -> tuple[str, str]:
    inputs = ROOT / "runtime" / "long_test_inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    source = inputs / "source.png"
    mask = inputs / "mask.png"
    image = Image.new("RGB", (512, 512), (38, 34, 32))
    draw = ImageDraw.Draw(image)
    draw.rectangle((42, 44, 470, 470), outline=(58, 48, 42), width=10)
    draw.rectangle((126, 190, 386, 448), fill=(86, 46, 38), outline=(118, 70, 52), width=6)
    draw.ellipse((188, 78, 324, 214), fill=(150, 82, 58), outline=(184, 112, 80), width=5)
    draw.polygon([(256, 214), (202, 318), (310, 318)], fill=(54, 42, 38), outline=(160, 90, 60))
    draw.line((120, 356, 392, 356), fill=(178, 96, 54), width=10)
    draw.line((84, 410, 428, 410), fill=(190, 116, 58), width=6)
    draw.rectangle((54, 86, 150, 130), fill=(70, 55, 44))
    draw.rectangle((362, 86, 458, 130), fill=(70, 55, 44))
    image.save(source)
    mask_image = Image.new("L", (512, 512), 0)
    mask_draw = ImageDraw.Draw(mask_image)
    mask_draw.rectangle((0, 0, 512, 170), fill=255)
    mask_draw.ellipse((176, 66, 336, 226), fill=0)
    mask_draw.rectangle((150, 160, 362, 448), fill=0)
    mask_image.save(mask)
    return str(source), str(mask)


def run_generation_smoke(base_url: str, report: dict[str, Any]) -> None:
    source, mask = prepare_generation_inputs()
    local_loras = request_json("GET", f"{base_url}/forge/loras")
    jobs = [
        {
            "type": "txt2img",
            "engine": "sdxl",
            "model": "stable-diffusion-xl-base-1.0",
            "prompt": "small CPU smoke, demonic forge icon, simple composition",
            "width": 512,
            "height": 512,
            "steps": 2,
            "guidance": 5.0,
            "seed": 41001,
        },
        {
            "type": "img2img",
            "engine": "sdxl",
            "model": "stable-diffusion-xl-base-1.0",
            "prompt": "refine into cinematic demonic forge portrait",
            "source_images": [source],
            "width": 512,
            "height": 512,
            "steps": 4,
            "strength": 0.85,
            "guidance": 5.0,
            "seed": 41002,
        },
        {
            "type": "inpaint",
            "engine": "sdxl",
            "model": "stable-diffusion-xl-base-1.0",
            "prompt": "paint fiery forge background",
            "source_images": [source],
            "mask_image": mask,
            "width": 512,
            "height": 512,
            "steps": 4,
            "strength": 0.85,
            "guidance": 5.0,
            "seed": 41003,
        },
    ]
    if local_loras:
        jobs.append(
            {
                "type": "txt2img",
                "engine": "sdxl",
                "model": "stable-diffusion-xl-base-1.0",
                "prompt": "small CPU LoRA smoke, bright offset lighting test",
                "width": 512,
                "height": 512,
                "steps": 2,
                "guidance": 5.0,
                "seed": 41004,
                "loras": [{"name": local_loras[0]["name"], "weight": 0.5}],
            }
        )
    for spec in jobs:
        started = time.monotonic()
        record = request_json("POST", f"{base_url}/forge/jobs", json=spec)
        finished = wait_job(base_url, record["id"])
        if finished["status"] != "succeeded":
            raise AssertionError(f"generation job failed: {finished}")
        if not finished.get("artifacts"):
            raise AssertionError(f"generation job produced no artifacts: {finished['id']}")
        for artifact_id in finished["artifacts"]:
            verified = request_json("GET", f"{base_url}/forge/artifacts/{artifact_id}/verify")
            if not verified.get("ok"):
                raise AssertionError(f"artifact verify failed: {verified}")
            metadata = artifact_metadata(base_url, artifact_id)
            if spec["type"] == "img2img":
                assert_image_changed(base_url, artifact_id, source, 1.5, "img2img")
                if metadata.get("source_images") != [source]:
                    raise AssertionError(f"img2img metadata lost source_images: {metadata}")
            if spec["type"] == "inpaint":
                assert_image_changed(base_url, artifact_id, source, 1.0, "inpaint")
                region_diff = masked_mean_abs_difference(source, metadata["path"], mask)
                print(f"inpaint region diff: {region_diff}", flush=True)
                if metadata.get("mask_image") != mask:
                    raise AssertionError(f"inpaint metadata lost mask_image: {metadata}")
            if spec.get("loras"):
                if metadata.get("loras") != spec["loras"]:
                    raise AssertionError(f"LoRA metadata mismatch: {metadata.get('loras')} != {spec['loras']}")
        report.setdefault("generation_jobs", []).append(
            {
                "job_id": finished["id"],
                "type": spec["type"],
                "engine": spec.get("engine"),
                "status": finished["status"],
                "duration_sec": round(time.monotonic() - started, 3),
                "artifacts": finished.get("artifacts", []),
                "loras": spec.get("loras", []),
            }
        )
        print(f"{spec['type']} generation ok: {finished['id']} artifacts={finished['artifacts']}", flush=True)
    if local_loras:
        runtime = request_json("GET", f"{base_url}/forge/runtime")
        if runtime.get("embedded_worker"):
            loaded_loras = {
                name
                for engine in runtime.get("loaded_engines", [])
                for name in engine.get("loaded_loras", [])
            }
            expected = re.sub(r"[^A-Za-z0-9_]", "_", f"lora_{local_loras[0]['name']}")
            if expected not in loaded_loras:
                raise AssertionError(f"runtime did not report loaded LoRA {expected!r}: {runtime}")
            print(f"LoRA runtime load ok: {expected}", flush=True)
        else:
            print("LoRA metadata ok; runtime load check skipped for external worker mode", flush=True)


def run_quality_generation(base_url: str, report: dict[str, Any]) -> None:
    source, mask = prepare_generation_inputs()
    jobs = [
        (
            "sdxl_quality_txt2img",
            {
                "type": "txt2img",
                "engine": "sdxl",
                "model": "stable-diffusion-xl-base-1.0",
                "prompt": (
                    "quality evaluation, cinematic demonic blacksmith in a fiery forge, "
                    "clear silhouette, warm rim light, coherent composition"
                ),
                "negative_prompt": "low quality, blurry, broken anatomy, abstract noise",
                "width": 512,
                "height": 512,
                "steps": 10,
                "guidance": 7.0,
                "seed": 51001,
                "quality_preset": "quality_eval",
                "safety": {"quality_preset": "quality_eval"},
            },
        ),
        (
            "sdxl_quality_img2img",
            {
                "type": "img2img",
                "engine": "sdxl",
                "model": "stable-diffusion-xl-base-1.0",
                "prompt": "turn the simple source into a cinematic demonic blacksmith portrait, keep central figure",
                "negative_prompt": "low quality, blurry, abstract noise",
                "source_images": [source],
                "width": 512,
                "height": 512,
                "steps": 8,
                "strength": 0.78,
                "guidance": 7.0,
                "seed": 51002,
                "quality_preset": "quality_eval_edit",
                "safety": {"quality_preset": "quality_eval_edit"},
            },
        ),
        (
            "sdxl_quality_inpaint",
            {
                "type": "inpaint",
                "engine": "sdxl",
                "model": "stable-diffusion-xl-base-1.0",
                "prompt": "replace masked top background with fiery forge sparks and orange furnace light",
                "negative_prompt": "low quality, blurry, abstract noise",
                "source_images": [source],
                "mask_image": mask,
                "width": 512,
                "height": 512,
                "steps": 8,
                "strength": 0.72,
                "guidance": 7.0,
                "seed": 51003,
                "quality_preset": "quality_eval_inpaint",
                "safety": {"quality_preset": "quality_eval_inpaint"},
            },
        ),
    ]
    sheet_items: list[tuple[str, str]] = [("source", source)]
    quality_jobs = []
    for label, spec in jobs:
        started = time.monotonic()
        record = request_json("POST", f"{base_url}/forge/jobs", json=spec)
        finished = wait_job(base_url, record["id"], timeout_seconds=1800)
        if finished["status"] != "succeeded":
            raise AssertionError(f"quality generation job failed: {finished}")
        artifact_id = finished["artifacts"][0]
        metadata = artifact_metadata(base_url, artifact_id)
        verified = request_json("GET", f"{base_url}/forge/artifacts/{artifact_id}/verify")
        if not verified.get("ok"):
            raise AssertionError(f"quality artifact verify failed: {verified}")
        path = metadata["path"]
        sheet_items.append((label, path))
        diff_from_source = None
        region_diff = None
        if spec["type"] in {"img2img", "inpaint"}:
            diff_from_source = mean_abs_difference(source, path)
        if spec["type"] == "inpaint":
            region_diff = masked_mean_abs_difference(source, path, mask)
        quality_jobs.append(
            {
                "label": label,
                "job_id": finished["id"],
                "artifact_id": artifact_id,
                "path": path,
                "duration_sec": round(time.monotonic() - started, 3),
                "prompt": spec["prompt"],
                "steps": spec["steps"],
                "guidance": spec["guidance"],
                "strength": spec.get("strength"),
                "diff_from_source": None if diff_from_source is None else round(diff_from_source, 3),
                "region_diff": region_diff,
                "image_stats": image_stats(path),
            }
        )
        print(f"{label} ok: {finished['id']} artifact={artifact_id}", flush=True)
    sheet_path = make_contact_sheet(sheet_items, REPORTS_DIR / f"{report['run_id']}-quality-sheet.png")
    report["quality_generation"] = {
        "jobs": quality_jobs,
        "contact_sheet": sheet_path,
        "note": "This is still a CPU-bounded quality probe, not a final artistic benchmark.",
    }


def run_edit_sweep(base_url: str, report: dict[str, Any]) -> None:
    source, _mask = prepare_generation_inputs()
    prompt = (
        "convert the source into a cinematic demonic blacksmith portrait, "
        "keep the humanoid silhouette and central face, add forge lighting"
    )
    jobs = [
        ("img2img_soft", 0.35, 12, 52001),
        ("img2img_balanced", 0.62, 14, 52002),
        ("img2img_strong", 0.88, 18, 52003),
    ]
    sheet_items: list[tuple[str, str]] = [("source", source)]
    sweep_jobs = []
    for label, strength, steps, seed in jobs:
        started = time.monotonic()
        spec = {
            "type": "img2img",
            "engine": "sdxl",
            "model": "stable-diffusion-xl-base-1.0",
            "prompt": prompt,
            "negative_prompt": "abstract emblem, logo, low quality, blurry, distorted face",
            "source_images": [source],
            "width": 512,
            "height": 512,
            "steps": steps,
            "strength": strength,
            "guidance": 7.0,
            "seed": seed,
            "quality_preset": label,
            "safety": {"quality_preset": label},
        }
        record = request_json("POST", f"{base_url}/forge/jobs", json=spec)
        finished = wait_job(base_url, record["id"], timeout_seconds=1800)
        if finished["status"] != "succeeded":
            raise AssertionError(f"edit sweep job failed: {finished}")
        artifact_id = finished["artifacts"][0]
        metadata = artifact_metadata(base_url, artifact_id)
        path = metadata["path"]
        sheet_items.append((label, path))
        sweep_jobs.append(
            {
                "label": label,
                "job_id": finished["id"],
                "artifact_id": artifact_id,
                "path": path,
                "duration_sec": round(time.monotonic() - started, 3),
                "strength": strength,
                "steps": steps,
                "diff_from_source": round(mean_abs_difference(source, path), 3),
                "image_stats": image_stats(path),
            }
        )
        print(f"{label} ok: {finished['id']} artifact={artifact_id}", flush=True)
    sheet_path = make_contact_sheet(sheet_items, REPORTS_DIR / f"{report['run_id']}-edit-sweep-sheet.png")
    report["edit_sweep"] = {
        "prompt": prompt,
        "jobs": sweep_jobs,
        "contact_sheet": sheet_path,
        "note": "Compare visual identity retention against source and diff_from_source.",
    }


def write_report(report: dict[str, Any], explicit_path: str | None = None) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = Path(explicit_path) if explicit_path else REPORTS_DIR / f"{report['run_id']}.json"
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    prune_reports()
    return path


def main() -> int:
    with forge_test_lock():
        return _main()


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--quality-generate", action="store_true")
    parser.add_argument("--edit-sweep", action="store_true")
    parser.add_argument("--report-json", default="")
    args = parser.parse_args()

    run_id = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S-forge-long")
    started = time.monotonic()
    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": utc_now(),
        "base_url": args.base_url,
        "cycles": max(1, args.cycles),
        "generate": bool(args.generate),
        "quality_generate": bool(args.quality_generate),
        "edit_sweep": bool(args.edit_sweep),
        "ok": False,
    }
    health = request_json("GET", f"{args.base_url}/health")
    report["health"] = health
    print(f"health ok: commit={health.get('git_commit')} cpu_threads={health.get('cpu_threads')}", flush=True)
    thinker = request_json("GET", f"{args.base_url}/forge/planner/thinker")
    report["thinker"] = thinker
    print(f"thinker: enabled={thinker.get('enabled')} ready={thinker.get('ready')}", flush=True)
    run_planner_matrix(args.base_url.rstrip("/"), max(1, args.cycles), report)
    run_downloader_safety(args.base_url.rstrip("/"), report)
    if args.generate:
        run_generation_smoke(args.base_url.rstrip("/"), report)
    if args.quality_generate:
        run_quality_generation(args.base_url.rstrip("/"), report)
    if args.edit_sweep:
        run_edit_sweep(args.base_url.rstrip("/"), report)
    report["finished_at"] = utc_now()
    report["duration_sec"] = round(time.monotonic() - started, 3)
    report["ok"] = True
    report_path = write_report(report, args.report_json or None)
    print(f"report: {report_path}", flush=True)
    print("long forge api ok", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
