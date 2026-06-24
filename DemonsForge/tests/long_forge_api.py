#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageChops, ImageDraw, ImageStat


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "http://127.0.0.1:8110"


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


def run_planner_matrix(base_url: str, cycles: int) -> None:
    for cycle in range(1, cycles + 1):
        started = time.monotonic()
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
            if spec.get("asset_request") is None:
                dry_run = requests.post(f"{base_url}/forge/jobs?dry_run=true", json=spec, timeout=60)
                if scenario["expect"].get("required_inputs"):
                    if dry_run.status_code != 400:
                        raise AssertionError(f"{scenario['name']}: missing-input dry-run should fail")
                elif dry_run.status_code != 200:
                    raise AssertionError(f"{scenario['name']}: dry-run failed: {dry_run.text}")
        elapsed = time.monotonic() - started
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


def artifact_metadata(base_url: str, artifact_id: str) -> dict[str, Any]:
    return request_json("GET", f"{base_url}/forge/artifacts/{artifact_id}/metadata")


def assert_image_changed(base_url: str, artifact_id: str, source: str, min_mean_diff: float, label: str) -> None:
    metadata = artifact_metadata(base_url, artifact_id)
    output = metadata["path"]
    diff = mean_abs_difference(source, output)
    if diff < min_mean_diff:
        raise AssertionError(f"{label}: output changed too little, mean abs diff={diff:.3f}")
    print(f"{label}: mean abs diff={diff:.3f}", flush=True)


def run_downloader_safety(base_url: str) -> None:
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
    print("asset downloader safety ok", flush=True)


def prepare_generation_inputs() -> tuple[str, str]:
    inputs = ROOT / "runtime" / "long_test_inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    source = inputs / "source.png"
    mask = inputs / "mask.png"
    image = Image.new("RGB", (512, 512), (36, 32, 30))
    draw = ImageDraw.Draw(image)
    draw.rectangle((120, 80, 390, 460), fill=(92, 48, 38))
    draw.ellipse((190, 120, 320, 250), fill=(145, 76, 55))
    image.save(source)
    mask_image = Image.new("L", (512, 512), 0)
    mask_draw = ImageDraw.Draw(mask_image)
    mask_draw.rectangle((0, 0, 512, 170), fill=255)
    mask_image.save(mask)
    return str(source), str(mask)


def run_generation_smoke(base_url: str) -> None:
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
                if metadata.get("mask_image") != mask:
                    raise AssertionError(f"inpaint metadata lost mask_image: {metadata}")
            if spec.get("loras"):
                if metadata.get("loras") != spec["loras"]:
                    raise AssertionError(f"LoRA metadata mismatch: {metadata.get('loras')} != {spec['loras']}")
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--generate", action="store_true")
    args = parser.parse_args()

    health = request_json("GET", f"{args.base_url}/health")
    print(f"health ok: commit={health.get('git_commit')} cpu_threads={health.get('cpu_threads')}", flush=True)
    thinker = request_json("GET", f"{args.base_url}/forge/planner/thinker")
    print(f"thinker: enabled={thinker.get('enabled')} ready={thinker.get('ready')}", flush=True)
    run_planner_matrix(args.base_url.rstrip("/"), max(1, args.cycles))
    run_downloader_safety(args.base_url.rstrip("/"))
    if args.generate:
        run_generation_smoke(args.base_url.rstrip("/"))
    print("long forge api ok", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
