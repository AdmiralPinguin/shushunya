#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
WARMMASTER_ROOT = PROJECT_ROOT / "EyeOfTerror" / "Warmaster"
if str(WARMMASTER_ROOT) not in sys.path:
    sys.path.insert(0, str(WARMMASTER_ROOT))

from PIL import Image

from EyeOfTerror.Pictorium.Moriana.forge_runtime.schemas import ArtifactRecord, JobRecord, JobSpec, JobStatus
from EyeOfTerror.Pictorium.Moriana.forge_runtime.storage import ForgeStore
from EyeOfTerror.Pictorium.Moriana.moriana_forge_monitor import monitor_forge_job
from EyeOfTerror.Pictorium.Moriana.moriana_governor import create_or_execute_run, make_handler, prepare_run


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def request_json(base: str, method: str, path: str, payload: dict[str, object] | None = None, *, expect_status: int = 200) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status = response.status
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        status = exc.code
        result = json.loads(exc.read().decode("utf-8"))
    if status != expect_status:
        raise AssertionError(f"{method} {path} returned {status}, expected {expect_status}: {result}")
    if not isinstance(result, dict):
        raise AssertionError(f"{method} {path} returned non-object JSON")
    return result


def assert_run_workspace(run_dir: Path) -> None:
    expected_dirs = ["input", "plan", "brigade", "prompts", "parameters", "results", "artifacts", "errors", "revisions", "final"]
    for dirname in expected_dirs:
        if not (run_dir / dirname).is_dir():
            raise AssertionError(f"missing Moriana run workspace directory: {dirname}")
    for filename in ("status.json", "artifact_registry.json", "input/task.json", "plan/moriana_plan.json"):
        if not (run_dir / filename).exists():
            raise AssertionError(f"missing Moriana run workspace file: {filename}")


def artifact_types(run_dir: Path) -> set[str]:
    registry = load_json(run_dir / "artifact_registry.json")
    return {str(item.get("type") or "") for item in registry.get("artifacts", []) if isinstance(item, dict)}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="moriana-runtime-self-test-") as tmp:
        run_root = Path(tmp) / "runtime" / "pictorium" / "runs"
        forge_db_path = Path(tmp) / "forge-monitor.sqlite3"
        forge_store = ForgeStore(forge_db_path)
        completed_job = JobRecord(
            id="forge-monitor-job",
            spec=JobSpec(prompt="monitor test image", width=512, height=512),
            status=JobStatus.succeeded,
            progress=1.0,
        )
        forge_store.create_job(completed_job)
        forge_artifact_path = Path(tmp) / "forge_artifact.png"
        forge_metadata_path = Path(tmp) / "forge_artifact.json"
        Image.new("RGB", (512, 512), (10, 20, 30)).save(forge_artifact_path)
        forge_metadata_path.write_text('{"source":"moriana_runtime_self_test"}\n', encoding="utf-8")
        forge_store.add_artifact(
            ArtifactRecord(
                id="forge-monitor-artifact",
                job_id="forge-monitor-job",
                kind="image",
                path=str(forge_artifact_path),
                metadata_path=str(forge_metadata_path),
                metadata={"width": 512, "height": 512},
            )
        )
        monitored = monitor_forge_job(db_path=forge_db_path, job_record=completed_job.model_dump(mode="json"))
        if not monitored.get("ok") or monitored.get("artifact_paths") != [str(forge_artifact_path)]:
            raise AssertionError(f"Forge monitor did not resolve completed job artifact: {monitored}")

        prepared = prepare_run("нарисуй картинку 512x512", "prepared-image", run_root / "prepared-image")
        run_dir = Path(str(prepared["run_dir"]))
        assert_run_workspace(run_dir)
        prepared_status = load_json(run_dir / "status.json")
        if prepared_status.get("run_id") != "prepared-image" or not prepared_status.get("pictorium_runtime"):
            raise AssertionError(f"prepare_run did not install Moriana runtime state: {prepared_status}")

        success = create_or_execute_run(
            run_root,
            {
                "task": "нарисуй простую картинку алтаря 512x512",
                "task_id": "image-success",
                "execute": True,
                "test_artifact_mode": "good",
            },
        )
        if not success.get("ok") or success.get("status", {}).get("status") != "completed":
            raise AssertionError(f"image success run did not complete: {success}")
        success_dir = Path(str(success["run_dir"]))
        assert_run_workspace(success_dir)
        success_types = artifact_types(success_dir)
        for required_type in ("prompt", "resource_report", "dispatch", "verification", "image", "final"):
            if required_type not in success_types:
                raise AssertionError(f"image success registry missing {required_type}: {success_types}")

        revision = create_or_execute_run(
            run_root,
            {
                "task": "нарисуй сложную картинку кузни 512x512",
                "task_id": "image-revision",
                "execute": True,
                "test_artifact_mode": "bad_then_good",
                "max_revision_cycles": 1,
            },
        )
        revision_dir = Path(str(revision["run_dir"]))
        revision_registry = load_json(revision_dir / "artifact_registry.json")
        rejected_images = [
            item
            for item in revision_registry.get("artifacts", [])
            if isinstance(item, dict) and item.get("type") == "image" and item.get("status") == "rejected"
        ]
        accepted_images = [
            item
            for item in revision_registry.get("artifacts", [])
            if isinstance(item, dict) and item.get("type") == "image" and item.get("status") == "accepted"
        ]
        if not revision.get("ok") or not rejected_images or not accepted_images or not (revision_dir / "revisions" / "revision_01.json").exists():
            raise AssertionError(f"revision loop did not preserve rejected and accepted attempts: {revision}")

        failure = create_or_execute_run(
            run_root,
            {
                "task": "нарисуй картинку без тестового артефакта 512x512",
                "task_id": "image-pending-failure",
                "execute": True,
            },
        )
        failure_dir = Path(str(failure["run_dir"]))
        if failure.get("ok") or load_json(failure_dir / "status.json").get("status") != "failed":
            raise AssertionError(f"missing-artifact run should be explicit failed/pending blocker: {failure}")
        if not (failure_dir / "revisions" / "revision_01.json").exists():
            raise AssertionError("failed image run did not write revision plan")

        pending = create_or_execute_run(
            run_root,
            {
                "task": "нарисуй pending forge картинку 512x512",
                "task_id": "image-pending-forge-job",
                "execute": True,
                "submit": True,
                "wait_for_result": True,
                "max_wait_sec": 0,
            },
        )
        pending_dir = Path(str(pending["run_dir"]))
        pending_registry = load_json(pending_dir / "artifact_registry.json")
        rejected_results = [
            item
            for item in pending_registry.get("artifacts", [])
            if isinstance(item, dict) and item.get("type") == "result" and item.get("status") == "rejected"
        ]
        if pending.get("ok") or not rejected_results or pending.get("forge_monitor", {}).get("status") != "queued":
            raise AssertionError(f"pending Forge job was not tracked as a rejected runtime result: {pending}")

        series = create_or_execute_run(
            run_root,
            {
                "task": "сделай серию 3 изображения про один и тот же древний механикум-алтарь 512x512",
                "task_id": "image-series-success",
                "execute": True,
                "test_artifact_mode": "series_good",
            },
        )
        series_dir = Path(str(series["run_dir"]))
        series_registry = load_json(series_dir / "artifact_registry.json")
        series_images = [
            item
            for item in series_registry.get("artifacts", [])
            if isinstance(item, dict) and item.get("type") == "image" and item.get("status") == "accepted"
        ]
        if (
            not series.get("ok")
            or series.get("status", {}).get("task_kind") != "image_series"
            or series.get("final", {}).get("kind") != "pictorium_image_series_final_manifest"
            or series.get("final", {}).get("series_count") != 3
            or len(series_images) != 3
        ):
            raise AssertionError(f"image series run did not complete as a real series: {series}")

        comic = create_or_execute_run(
            run_root,
            {
                "task": "сделай комикс 4 панели про техножреца у древней кузни",
                "task_id": "comic-success",
                "execute": True,
            },
        )
        comic_dir = Path(str(comic["run_dir"]))
        comic_types = artifact_types(comic_dir)
        for required_type in ("plan", "character_sheet", "comic_panel", "layout", "final"):
            if required_type not in comic_types:
                raise AssertionError(f"comic registry missing {required_type}: {comic_types}")
        if comic.get("status", {}).get("task_kind") != "comic":
            raise AssertionError(f"comic run did not preserve task_kind: {comic}")

        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(run_root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            created = request_json(
                base,
                "POST",
                "/runs",
                {
                    "task": "нарисуй HTTP картинку 512x512",
                    "task_id": "http-image",
                    "execute": True,
                    "test_artifact_mode": "good",
                },
            )
            if not created.get("ok") or created.get("run_id") != "http-image":
                raise AssertionError(f"HTTP create/execute failed: {created}")
            listed = request_json(base, "GET", "/runs")
            if "http-image" not in {str(item.get("run_id") or "") for item in listed.get("runs", []) if isinstance(item, dict)}:
                raise AssertionError(f"HTTP runs list missed http-image: {listed}")
            status = request_json(base, "GET", "/runs/http-image/status")
            if status.get("status", {}).get("status") != "completed":
                raise AssertionError(f"HTTP status failed: {status}")
            artifacts = request_json(base, "GET", "/runs/http-image/artifacts")
            if not artifacts.get("artifacts"):
                raise AssertionError(f"HTTP artifacts failed: {artifacts}")
            final = request_json(base, "GET", "/runs/http-image/final")
            if final.get("final", {}).get("status") != "ready":
                raise AssertionError(f"HTTP final failed: {final}")
            revised = request_json(base, "POST", "/runs/http-image/revise", {"reason": "test revision request"})
            if revised.get("status", {}).get("status") != "revising":
                raise AssertionError(f"HTTP revise failed: {revised}")
            accepted = request_json(base, "POST", "/runs/http-image/accept", {})
            if not accepted.get("ok") or not accepted.get("final", {}).get("accepted_at"):
                raise AssertionError(f"HTTP accept failed: {accepted}")
        finally:
            server.shutdown()
            thread.join(timeout=5)

    print("[ok] Moriana runtime, artifact registry, revision loop, and app API")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
