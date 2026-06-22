#!/usr/bin/env python3
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forge_service.server import app


def main() -> None:
    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code == 200, health.text
    caps = client.get("/forge/capabilities")
    assert caps.status_code == 200, caps.text
    assert "engines" in caps.json()
    runtime = client.get("/forge/runtime")
    assert runtime.status_code == 200, runtime.text
    assert runtime.json()["cpu_only"] is True
    plan = client.post(
        "/forge/plan",
        json={"request": "Нарисуй кинематографичный портрет демона в кузнице, вертикально"},
    )
    assert plan.status_code == 200, plan.text
    spec = plan.json()
    assert spec["type"] == "txt2img"
    assert spec["prompt"]
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
    queued = client.post(
        "/forge/jobs",
        json={
            "type": "metadata-read",
            "prompt": "smoke unsupported stub",
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
    print("smoke ok")


if __name__ == "__main__":
    main()
