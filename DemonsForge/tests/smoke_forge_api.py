#!/usr/bin/env python3
import sys
from pathlib import Path
import time

from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forge_service.server import app
from forge_service.client import DemonsForgeClient
from forge_service.queue import ForgeQueue
from forge_service.schemas import JobSpec
from forge_service.server import store


def wait_for_terminal(client: TestClient, job_id: str) -> dict:
    for _ in range(50):
        response = client.get(f"/forge/jobs/{job_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {"succeeded", "failed", "canceled"}:
            return payload
        time.sleep(0.1)
    raise AssertionError(f"job did not finish: {job_id}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    test_inputs = root / "runtime" / "test_inputs"
    test_inputs.mkdir(parents=True, exist_ok=True)
    input_image = test_inputs / "upscale_source.png"
    Image.new("RGB", (16, 16), (128, 32, 16)).save(input_image)

    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code == 200, health.text
    assert "db_schema_version" in health.json()
    assert "git_commit" in health.json()
    thin = DemonsForgeClient(base_url="http://testserver")
    assert thin is not None
    caps = client.get("/forge/capabilities")
    assert caps.status_code == 200, caps.text
    assert "engines" in caps.json()
    assert "implemented_job_types" in caps.json()
    assert caps.json()["engines"]["sdxl"]["implemented"]["img2img"] is True
    assert caps.json()["engines"]["flux"]["implemented"]["img2img"] is False
    runtime = client.get("/forge/runtime")
    assert runtime.status_code == 200, runtime.text
    assert runtime.json()["cpu_only"] is True
    assert runtime.json()["embedded_worker"] is True
    assert runtime.json()["memory"]["namespace"] == "demonsforge"
    memory_status = client.get("/forge/memory/status")
    assert memory_status.status_code == 200, memory_status.text
    assert memory_status.json()["write_policy"] == "proposal-only"
    assert "ready_for_authenticated_archive" in memory_status.json()
    assert "read_timeout_seconds" in memory_status.json()
    assert "write_timeout_seconds" in memory_status.json()
    unload = client.post("/forge/runtime/unload")
    assert unload.status_code == 200, unload.text
    assert unload.json()["ok"] is True
    paused = client.post("/forge/queue/pause")
    assert paused.status_code == 200, paused.text
    assert paused.json()["paused"] is True
    resumed = client.post("/forge/queue/resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["paused"] is False
    queue_state = client.get("/forge/queue")
    assert queue_state.status_code == 200, queue_state.text
    assert "status_counts" in queue_state.json()
    external_worker = ForgeQueue(store, start_worker=False)
    external_worker.pause()
    paused_job = external_worker.submit(
        JobSpec(type="prompt-enhance", prompt="external worker pause check")
    )
    assert external_worker.run_pending_once() is False
    assert store.get_job(paused_job.id).status.value == "queued"
    external_worker.resume()
    assert external_worker.run_pending_once() is True
    assert store.get_job(paused_job.id).status.value == "succeeded"
    memory_policy = client.get("/forge/memory/policy")
    assert memory_policy.status_code == 200, memory_policy.text
    assert "asset approvals/rejections" in memory_policy.json()["durable_topics"]
    assert "progress events" in memory_policy.json()["do_not_write"]
    memory_catalog = client.get("/forge/memory/catalog?create=true")
    assert memory_catalog.status_code == 200, memory_catalog.text
    assert isinstance(memory_catalog.json(), dict)
    if memory_catalog.json().get("ok") is False:
        assert "memory" in memory_catalog.json()
    memory_search = client.get("/forge/memory/search?q=sdxl&layers=focus,wiki,vector,graph&limit=2")
    assert memory_search.status_code == 200, memory_search.text
    assert isinstance(memory_search.json(), dict)
    memory_events = client.get("/forge/memory/events?limit=2")
    assert memory_events.status_code == 200, memory_events.text
    assert isinstance(memory_events.json(), dict)
    memory_proposals = client.get("/forge/memory/proposals?limit=2")
    assert memory_proposals.status_code == 200, memory_proposals.text
    assert isinstance(memory_proposals.json(), list)
    empty_memory_proposal = client.post(
        "/forge/memory/propose",
        json={"proposal": "   "},
    )
    assert empty_memory_proposal.status_code == 422, empty_memory_proposal.text
    memory_proposal_dry_run = client.post(
        "/forge/memory/propose?dry_run=true",
        json={
            "proposal": "DemonsForge smoke dry-run proposal should not be written.",
            "evidence": "This validates dry_run only.",
        },
    )
    assert memory_proposal_dry_run.status_code == 200, memory_proposal_dry_run.text
    assert memory_proposal_dry_run.json()["dry_run"] is True
    duplicate_hash = store.memory_proposal_hash("duplicate check", "smoke", "auto")
    store.record_memory_proposal(
        duplicate_hash,
        "duplicate check",
        "smoke",
        "auto",
        1,
        {"ok": True, "smoke": True},
    )
    duplicate_memory_proposal = client.post(
        "/forge/memory/propose?dry_run=true",
        json={
            "proposal": "duplicate check",
            "evidence": "smoke",
            "importance": 1,
        },
    )
    assert duplicate_memory_proposal.status_code == 200, duplicate_memory_proposal.text
    assert duplicate_memory_proposal.json()["duplicate"] is True
    schema = client.get("/forge/schema/job")
    assert schema.status_code == 200, schema.text
    samplers = client.get("/forge/samplers")
    assert samplers.status_code == 200, samplers.text
    assert "default" in samplers.json()
    schedulers = client.get("/forge/schedulers")
    assert schedulers.status_code == 200, schedulers.text
    assert any(item["name"] == "native" for item in schedulers.json())
    aspect_presets = client.get("/forge/aspect-presets")
    assert aspect_presets.status_code == 200, aspect_presets.text
    assert aspect_presets.json()["square"]["width"] == aspect_presets.json()["square"]["height"]
    downloads = client.get("/forge/assets/downloads")
    assert downloads.status_code == 200, downloads.text
    plan = client.post(
        "/forge/plan",
        json={"request": "Нарисуй кинематографичный портрет демона в кузнице, вертикально"},
    )
    assert plan.status_code == 200, plan.text
    spec = plan.json()
    assert spec["type"] == "txt2img"
    assert spec["prompt"]
    assert "memory_context" in spec["safety"]
    planned_custom = client.post(
        "/forge/plan",
        json={"request": "SDXL 512x768 steps 7 seed 123 cinematic portrait"},
    )
    assert planned_custom.status_code == 200, planned_custom.text
    planned_spec = planned_custom.json()
    assert planned_spec["width"] == 512
    assert planned_spec["height"] == 768
    assert planned_spec["steps"] == 7
    assert planned_spec["seed"] == 123
    planned_img2img = client.post(
        "/forge/plan",
        json={"request": "Сделай вариацию по картинке в кинематографичном стиле"},
    )
    assert planned_img2img.status_code == 200, planned_img2img.text
    assert planned_img2img.json()["type"] == "img2img"
    assert planned_img2img.json()["engine"] == "sdxl"
    assert "source_images" in planned_img2img.json()["safety"]["planner_note"]
    planned_missing_lora = client.post(
        "/forge/plan",
        json={"request": "Нарисуй портрет lora: definitely_missing_lora"},
    )
    assert planned_missing_lora.status_code == 200, planned_missing_lora.text
    missing_lora_spec = planned_missing_lora.json()
    assert missing_lora_spec["asset_request"]["asset_type"] == "lora"
    assert missing_lora_spec["asset_request"]["requires_user_approval"] is True
    planned_control = client.post(
        "/forge/plan",
        json={"request": "Сделай pose controlnet depth для персонажа"},
    )
    assert planned_control.status_code == 200, planned_control.text
    assert planned_control.json()["asset_request"]["asset_type"] == "control_asset"
    planned_flux = client.post(
        "/forge/plan",
        json={"request": "flux test image 512x512 steps 1"},
    )
    assert planned_flux.status_code == 200, planned_flux.text
    if planned_flux.json()["engine"] == "flux":
        assert "runtime_warning" in planned_flux.json()["safety"]
    dry_run = client.post(
        "/forge/jobs?dry_run=true",
        json={
            "type": "txt2img",
            "engine": "sdxl",
            "prompt": "dry run",
            "width": 512,
            "height": 512,
            "steps": 1,
        },
    )
    assert dry_run.status_code == 200, dry_run.text
    assert dry_run.json()["valid"] is True
    assert "estimated_min_ram_gb" in dry_run.json()["resource_estimate"]
    assert "warnings" in dry_run.json()["resource_estimate"]
    bad_scheduler = client.post(
        "/forge/jobs",
        json={
            "type": "txt2img",
            "engine": "sdxl",
            "prompt": "bad scheduler",
            "width": 512,
            "height": 512,
            "steps": 1,
            "scheduler": "not-a-scheduler",
        },
    )
    assert bad_scheduler.status_code == 400, bad_scheduler.text
    missing_lora = client.post(
        "/forge/jobs",
        json={
            "type": "txt2img",
            "engine": "sdxl",
            "prompt": "missing lora",
            "width": 512,
            "height": 512,
            "steps": 1,
            "loras": [{"name": "definitely_missing_lora", "weight": 0.8}],
        },
    )
    assert missing_lora.status_code == 400, missing_lora.text
    img2img_dry_run = client.post(
        "/forge/jobs?dry_run=true",
        json={
            "type": "img2img",
            "engine": "sdxl",
            "prompt": "dry run",
            "source_images": [str(input_image.relative_to(root))],
            "width": 512,
            "height": 512,
            "steps": 1,
            "strength": 1.0,
        },
    )
    assert img2img_dry_run.status_code == 200, img2img_dry_run.text
    upscale_dry_run = client.post(
        "/forge/jobs?dry_run=true",
        json={
            "type": "upscale",
            "source_images": [str(input_image.relative_to(root))],
            "upscale_factor": 2,
        },
    )
    assert upscale_dry_run.status_code == 200, upscale_dry_run.text
    assert upscale_dry_run.json()["resource_estimate"]["upscale"]["output_dimensions"]["width"] == 32
    rejected_download = client.post(
        "/forge/jobs?dry_run=true",
        json={
            "type": "asset-download",
            "asset_download": {
                "name": "bad",
                "asset_type": "lora",
                "source_url": "http://example.com/bad.safetensors",
                "approved": True,
            },
        },
    )
    assert rejected_download.status_code == 400, rejected_download.text
    rejected_bad_hash = client.post(
        "/forge/jobs?dry_run=true",
        json={
            "type": "asset-download",
            "asset_download": {
                "name": "bad-hash",
                "asset_type": "lora",
                "source_url": "https://huggingface.co/example/repo/resolve/main/file.safetensors",
                "approved": True,
                "sha256": "not-a-sha",
            },
        },
    )
    assert rejected_bad_hash.status_code == 400, rejected_bad_hash.text
    prompt_enhance = client.post(
        "/forge/jobs",
        json={
            "type": "prompt-enhance",
            "prompt": "кинематографичный портрет кузнеца",
        },
    )
    assert prompt_enhance.status_code == 200, prompt_enhance.text
    prompt_status = wait_for_terminal(client, prompt_enhance.json()["id"])
    assert prompt_status["status"] == "succeeded", prompt_status
    clone_dry_run = client.post(
        f"/forge/jobs/{prompt_enhance.json()['id']}/clone?dry_run=true",
        json={"overrides": {"prompt": "dry clone prompt"}, "reuse_seed": True},
    )
    assert clone_dry_run.status_code == 200, clone_dry_run.text
    assert clone_dry_run.json()["cloned_from"] == prompt_enhance.json()["id"]
    assert clone_dry_run.json()["spec"]["prompt"] == "dry clone prompt"
    cloned = client.post(
        f"/forge/jobs/{prompt_enhance.json()['id']}/clone",
        json={"overrides": {"prompt": "клонированный prompt"}, "reuse_seed": True},
    )
    assert cloned.status_code == 200, cloned.text
    assert cloned.json()["cloned_from"] == prompt_enhance.json()["id"]
    cloned_status = wait_for_terminal(client, cloned.json()["id"])
    assert cloned_status["status"] == "succeeded", cloned_status
    retry = client.post(f"/forge/jobs/{prompt_enhance.json()['id']}/retry?dry_run=true")
    assert retry.status_code == 200, retry.text
    assert retry.json()["cloned_from"] == prompt_enhance.json()["id"]
    upscale = client.post(
        "/forge/jobs",
        json={
            "type": "upscale",
            "source_images": [str(input_image.relative_to(root))],
            "upscale_factor": 2,
            "width": 64,
            "height": 64,
        },
    )
    assert upscale.status_code == 200, upscale.text
    upscale_status = wait_for_terminal(client, upscale.json()["id"])
    assert upscale_status["status"] == "succeeded", upscale_status
    assert upscale_status["artifacts"], upscale_status
    queued = client.post(
        "/forge/jobs",
        json={
            "type": "metadata-read",
            "source_images": ["README.md"],
        },
    )
    assert queued.status_code == 200, queued.text
    job_id = queued.json()["id"]
    status = client.get(f"/forge/jobs/{job_id}")
    assert status.status_code == 200, status.text
    metadata_status = wait_for_terminal(client, job_id)
    assert metadata_status["status"] == "succeeded", metadata_status
    metadata_artifact = store.get_artifact(metadata_status["artifacts"][0])
    assert metadata_artifact is not None
    metadata_entry = metadata_artifact.metadata["entries"][0]
    assert metadata_entry["size_bytes"] > 0
    assert len(metadata_entry["sha256"]) == 64
    assert "mime_type" in metadata_entry
    metadata_response = client.get(f"/forge/artifacts/{metadata_status['artifacts'][0]}/metadata")
    assert metadata_response.status_code == 200, metadata_response.text
    assert metadata_response.json()["type"] == "metadata-read"
    manifest = client.get(f"/forge/jobs/{job_id}/manifest")
    assert manifest.status_code == 200, manifest.text
    assert manifest.json()["job"]["id"] == job_id
    assert manifest.json()["artifact_count"] == len(metadata_status["artifacts"])
    job_logs = client.get(f"/forge/jobs/{job_id}/logs")
    assert job_logs.status_code == 200, job_logs.text
    assert job_logs.json()["job_id"] == job_id
    assert isinstance(job_logs.json()["logs"], list)
    jobs = client.get("/forge/jobs?limit=5")
    assert jobs.status_code == 200, jobs.text
    events = client.get(f"/forge/jobs/{job_id}/events")
    assert events.status_code == 200, events.text
    gallery = client.get("/forge/gallery")
    assert gallery.status_code == 200, gallery.text
    gallery_filtered = client.get("/forge/gallery?kind=image&job_type=upscale&limit=5")
    assert gallery_filtered.status_code == 200, gallery_filtered.text
    assert isinstance(gallery_filtered.json(), list)
    print("smoke ok")


if __name__ == "__main__":
    main()
