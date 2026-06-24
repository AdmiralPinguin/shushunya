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
from forge_service.schemas import JobSpec, PlanRequest
from forge_service.server import store
from forge_service.thinker import PlannerThinker


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
    existing_asset = test_inputs / "existing_asset.safetensors"
    existing_asset.write_bytes(b"already here")

    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code == 200, health.text
    assert "db_schema_version" in health.json()
    assert "git_commit" in health.json()
    thin = DemonsForgeClient(base_url="http://testserver")
    assert thin is not None
    assert thin.artifact_file_url("abc").endswith("/forge/artifacts/abc/file")
    caps = client.get("/forge/capabilities")
    assert caps.status_code == 200, caps.text
    assert caps.json()["version"] == health.json()["version"]
    assert "engines" in caps.json()
    assert "implemented_job_types" in caps.json()
    assert "modified_at" in caps.json()["models"][0]
    assert caps.json()["limits"]["max_asset_download_bytes"] > 0
    assert caps.json()["engines"]["sdxl"]["implemented"]["img2img"] is True
    assert caps.json()["engines"]["flux"]["implemented"]["img2img"] is False
    assert caps.json()["engines"]["sdxl"]["role"] == "image_edit_refine_workhorse"
    assert caps.json()["engines"]["stable_diffusion"]["role"] == "concept_txt2img"
    assert caps.json()["engine_policy"]["txt2img_default_order"][0] == "stable_diffusion"
    runtime = client.get("/forge/runtime")
    assert runtime.status_code == 200, runtime.text
    assert runtime.json()["cpu_only"] is True
    assert runtime.json()["embedded_worker"] is True
    assert runtime.json()["pid"] > 0
    assert runtime.json()["memory"]["namespace"] == "demonsforge"
    state = client.get("/forge/state")
    assert state.status_code == 200, state.text
    assert state.json()["ok"] is True
    assert "uptime_sec" in state.json()
    assert "job_status_counts" in state.json()
    assert "dependencies" in state.json()
    memory_status = client.get("/forge/memory/status")
    assert memory_status.status_code == 200, memory_status.text
    assert memory_status.json()["write_policy"] == "proposal-only"
    assert "ready_for_authenticated_archive" in memory_status.json()
    assert "read_timeout_seconds" in memory_status.json()
    assert "write_timeout_seconds" in memory_status.json()
    unload = client.post("/forge/runtime/unload")
    assert unload.status_code == 200, unload.text
    assert unload.json()["ok"] is True
    checkpoint = client.post("/forge/runtime/checkpoint")
    assert checkpoint.status_code == 200, checkpoint.text
    assert checkpoint.json()["ok"] is True
    paused = client.post("/forge/queue/pause")
    assert paused.status_code == 200, paused.text
    assert paused.json()["paused"] is True
    resumed = client.post("/forge/queue/resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["paused"] is False
    queue_state = client.get("/forge/queue")
    assert queue_state.status_code == 200, queue_state.text
    assert "status_counts" in queue_state.json()
    assert "queued" in queue_state.json()["status_counts"]
    assert queue_state.json()["pid"] > 0
    events = client.get("/forge/events?limit=5")
    assert events.status_code == 200, events.text
    assert events.json()["ok"] is True
    assert isinstance(events.json()["events"], list)
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
    cancel_worker = ForgeQueue(store, start_worker=False)
    cancel_job = cancel_worker.submit(JobSpec(type="prompt-enhance", prompt="cancel cleanup check"))
    cancel_worker.cancel(cancel_job.id)
    assert cancel_worker.queue_state()["canceled_jobs"] == 0
    assert store.get_job(cancel_job.id).status.value == "canceled"
    assert cancel_worker.run_pending_once() is False
    assert cancel_worker.queue_state()["canceled_jobs"] == 0
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
    store.delete_memory_proposal(duplicate_hash)
    schema = client.get("/forge/schema/job")
    assert schema.status_code == 200, schema.text
    engines = client.get("/forge/engines")
    assert engines.status_code == 200, engines.text
    assert "sdxl" in engines.json()
    embeddings = client.get("/forge/embeddings")
    assert embeddings.status_code == 200, embeddings.text
    assert isinstance(embeddings.json(), list)
    samplers = client.get("/forge/samplers")
    assert samplers.status_code == 200, samplers.text
    assert "default" in samplers.json()
    schedulers = client.get("/forge/schedulers")
    assert schedulers.status_code == 200, schedulers.text
    assert any(item["name"] == "native" for item in schedulers.json())
    aspect_presets = client.get("/forge/aspect-presets")
    assert aspect_presets.status_code == 200, aspect_presets.text
    assert aspect_presets.json()["square"]["width"] == aspect_presets.json()["square"]["height"]
    thinker = client.get("/forge/planner/thinker")
    assert thinker.status_code == 200, thinker.text
    assert thinker.json()["write_policy"] == "advisory-json-patch-only"
    refreshed = client.post("/forge/registries/refresh")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["ok"] is True
    assert "capabilities" in refreshed.json()
    downloads = client.get("/forge/assets/downloads")
    assert downloads.status_code == 200, downloads.text
    plan = client.post(
        "/forge/plan",
        json={"request": "Нарисуй кинематографичный портрет демона в кузнице, вертикально"},
    )
    assert plan.status_code == 200, plan.text
    spec = plan.json()
    assert spec["type"] == "txt2img"
    assert spec["engine"] == "stable_diffusion"
    assert spec["prompt"]
    assert "memory_context" in spec["safety"]
    assert "planner_thinker" in spec["safety"]
    plan_without_memory = client.post(
        "/forge/plan",
        json={"request": "SDXL 512x512 fast plan", "use_memory": False, "use_thinker": False},
    )
    assert plan_without_memory.status_code == 200, plan_without_memory.text
    assert plan_without_memory.json()["safety"]["memory_context"]["reason"] == "disabled by request"
    assert plan_without_memory.json()["safety"]["planner_thinker"]["reason"] == "disabled by request"
    baseline = JobSpec(
        type="txt2img",
        engine="sdxl",
        model="stable-diffusion-xl-base-1.0",
        prompt="baseline",
        width=512,
        height=512,
        steps=1,
    )
    fake_thinker = PlannerThinker(
        enabled=True,
        base_url="http://127.0.0.1:1/v1",
        api_key="",
        model="fake",
        timeout=0.1,
    )
    fake_thinker._request_patch = lambda _request, _baseline: '{"patch":{"model":"not-local-model"}}'  # type: ignore[method-assign]
    guarded, thinker_meta = fake_thinker.improve_plan(
        PlanRequest(request="try non-local model", use_memory=False),
        baseline,
    )
    assert guarded.model == "stable-diffusion-xl-base-1.0"
    assert thinker_meta["used"] is False
    assert "non-local model" in thinker_meta["error"]
    smoke_plan = client.post(
        "/forge/plan",
        json={"request": "SDXL 512x512 smoke portrait", "use_memory": False},
    )
    assert smoke_plan.status_code == 200, smoke_plan.text
    assert smoke_plan.json()["steps"] == 1
    assert smoke_plan.json()["quality_preset"] == "smoke"
    assert smoke_plan.json()["safety"]["quality_preset"] == "smoke"
    quality_plan = client.post(
        "/forge/plan",
        json={"request": "SDXL 512x512 качественно cinematic portrait", "use_memory": False},
    )
    assert quality_plan.status_code == 200, quality_plan.text
    assert quality_plan.json()["quality_preset"] == "quality"
    assert quality_plan.json()["safety"]["quality_preset"] == "quality"
    assert quality_plan.json()["steps"] > smoke_plan.json()["steps"]
    planned_custom = client.post(
        "/forge/plan",
        json={"request": "SDXL 512x768 steps 7 seed 123 cinematic portrait"},
    )
    assert planned_custom.status_code == 200, planned_custom.text
    planned_spec = planned_custom.json()
    assert planned_spec["engine"] == "sdxl"
    assert planned_spec["width"] == 512
    assert planned_spec["height"] == 768
    assert planned_spec["steps"] == 7
    assert planned_spec["seed"] == 123
    planned_batch = client.post(
        "/forge/plan",
        json={"request": "SDXL 512x512 batch 3 cinematic portraits"},
    )
    assert planned_batch.status_code == 200, planned_batch.text
    assert planned_batch.json()["batch_size"] == 3
    planned_cfg = client.post(
        "/forge/plan",
        json={"request": "SDXL 512x512 cfg 5.5 cinematic portrait"},
    )
    assert planned_cfg.status_code == 200, planned_cfg.text
    assert planned_cfg.json()["cfg"] == 5.5
    assert planned_cfg.json()["guidance"] == 5.5
    planned_scheduler = client.post(
        "/forge/plan",
        json={"request": "SDXL 512x512 scheduler euler portrait"},
    )
    assert planned_scheduler.status_code == 200, planned_scheduler.text
    assert planned_scheduler.json()["scheduler"] == "euler"
    planned_img2img = client.post(
        "/forge/plan",
        json={"request": "Сделай вариацию по картинке в кинематографичном стиле"},
    )
    assert planned_img2img.status_code == 200, planned_img2img.text
    assert planned_img2img.json()["type"] == "img2img"
    assert planned_img2img.json()["engine"] == "sdxl"
    assert "source_images" in planned_img2img.json()["safety"]["planner_note"]
    assert planned_img2img.json()["safety"]["required_inputs"] == ["source_images"]
    planned_soft_edit = client.post(
        "/forge/plan",
        json={"request": "Слегка измени по картинке в кинематографичном стиле", "use_memory": False},
    )
    planned_strong_edit = client.post(
        "/forge/plan",
        json={"request": "Сильно переделай по картинке в кинематографичном стиле", "use_memory": False},
    )
    assert planned_soft_edit.status_code == 200, planned_soft_edit.text
    assert planned_strong_edit.status_code == 200, planned_strong_edit.text
    assert planned_soft_edit.json()["strength"] < planned_strong_edit.json()["strength"]
    planned_missing_lora = client.post(
        "/forge/plan",
        json={"request": "Нарисуй портрет lora: definitely_missing_lora"},
    )
    assert planned_missing_lora.status_code == 200, planned_missing_lora.text
    missing_lora_spec = planned_missing_lora.json()
    assert missing_lora_spec["asset_request"]["asset_type"] == "lora"
    assert missing_lora_spec["asset_request"]["requires_user_approval"] is True
    unresolved_asset_job = client.post("/forge/jobs?dry_run=true", json=missing_lora_spec)
    assert unresolved_asset_job.status_code == 400, unresolved_asset_job.text
    planned_control = client.post(
        "/forge/plan",
        json={"request": "Сделай pose controlnet depth для персонажа"},
    )
    assert planned_control.status_code == 200, planned_control.text
    assert planned_control.json()["asset_request"]["asset_type"] == "control_asset"
    planned_upscale = client.post(
        "/forge/plan",
        json={"request": "Увеличь картинку в 4 раза"},
    )
    assert planned_upscale.status_code == 200, planned_upscale.text
    assert planned_upscale.json()["type"] == "upscale"
    assert planned_upscale.json()["upscale_factor"] == 4
    assert planned_upscale.json()["safety"]["required_inputs"] == ["source_images"]
    planned_flux = client.post(
        "/forge/plan",
        json={"request": "flux test image 512x512 steps 1"},
    )
    assert planned_flux.status_code == 200, planned_flux.text
    if planned_flux.json()["engine"] == "flux":
        assert "runtime_warning" in planned_flux.json()["safety"]
    planned_sd35 = client.post(
        "/forge/plan",
        json={"request": "sd 3.5 512x512 steps 1 concept portrait"},
    )
    assert planned_sd35.status_code == 200, planned_sd35.text
    assert planned_sd35.json()["engine"] == "stable_diffusion"
    planned_flux_guidance = client.post(
        "/forge/plan",
        json={"request": "flux 512x512 steps 1 guidance 5 portrait"},
    )
    assert planned_flux_guidance.status_code == 200, planned_flux_guidance.text
    if planned_flux_guidance.json()["engine"] == "flux":
        assert planned_flux_guidance.json()["guidance"] == 0.0
        assert "guidance_warning" in planned_flux_guidance.json()["safety"]
        flux_plan_dry_run = client.post("/forge/jobs?dry_run=true", json=planned_flux_guidance.json())
        assert flux_plan_dry_run.status_code == 200, flux_plan_dry_run.text
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
    missing_model = client.post(
        "/forge/jobs?dry_run=true",
        json={
            "type": "txt2img",
            "engine": "sdxl",
            "model": "definitely_missing_model",
            "prompt": "missing model",
            "width": 512,
            "height": 512,
            "steps": 1,
        },
    )
    assert missing_model.status_code == 400, missing_model.text
    flux_guidance = client.post(
        "/forge/jobs?dry_run=true",
        json={
            "type": "txt2img",
            "engine": "flux",
            "prompt": "flux guidance",
            "width": 512,
            "height": 512,
            "steps": 1,
            "guidance": 5.0,
        },
    )
    assert flux_guidance.status_code == 400, flux_guidance.text
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
    rejected_existing_asset = client.post(
        "/forge/jobs?dry_run=true",
        json={
            "type": "asset-download",
            "asset_download": {
                "name": "existing_asset",
                "asset_type": "lora",
                "source_url": "https://huggingface.co/example/repo/resolve/main/existing_asset.safetensors",
                "target_dir": str(test_inputs.relative_to(root)),
                "approved": True,
            },
        },
    )
    assert rejected_existing_asset.status_code == 400, rejected_existing_asset.text
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
    assert cloned.json()["spec"]["safety"]["cloned_from"] == prompt_enhance.json()["id"]
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
    upscale_artifact = store.get_artifact(upscale_status["artifacts"][0])
    assert upscale_artifact is not None
    assert len(upscale_artifact.metadata["image_sha256"]) == 64
    assert upscale_artifact.metadata["image_size_bytes"] > 0
    artifact_file = client.get(f"/forge/artifacts/{upscale_status['artifacts'][0]}/file")
    assert artifact_file.status_code == 200, artifact_file.text
    artifact_verify = client.get(f"/forge/artifacts/{upscale_status['artifacts'][0]}/verify")
    assert artifact_verify.status_code == 200, artifact_verify.text
    assert artifact_verify.json()["ok"] is True
    assert artifact_verify.json()["verified_against_metadata"] is True
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
    job_spec = client.get(f"/forge/jobs/{job_id}/spec")
    assert job_spec.status_code == 200, job_spec.text
    assert job_spec.json()["type"] == "metadata-read"
    job_logs = client.get(f"/forge/jobs/{job_id}/logs")
    assert job_logs.status_code == 200, job_logs.text
    assert job_logs.json()["job_id"] == job_id
    assert isinstance(job_logs.json()["logs"], list)
    jobs = client.get("/forge/jobs?limit=5")
    assert jobs.status_code == 200, jobs.text
    metadata_jobs = client.get("/forge/jobs?job_type=metadata-read&limit=5")
    assert metadata_jobs.status_code == 200, metadata_jobs.text
    assert all(item["spec"]["type"] == "metadata-read" for item in metadata_jobs.json())
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
