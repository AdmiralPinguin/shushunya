#!/usr/bin/env python3
import sys
from pathlib import Path
import time

from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forge_service.server import app
from forge_service.client import DemonsForgeClient
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
    img2img_dry_run = client.post(
        "/forge/jobs?dry_run=true",
        json={
            "type": "img2img",
            "engine": "sdxl",
            "prompt": "dry run",
            "source_images": [str(input_image.relative_to(root))],
            "width": 64,
            "height": 64,
            "steps": 1,
            "strength": 0.2,
        },
    )
    assert img2img_dry_run.status_code == 200, img2img_dry_run.text
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
