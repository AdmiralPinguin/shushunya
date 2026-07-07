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
from EyeOfTerror.Pictorium.Moriana.forge_tests.moriana_live_quality_trials import (
    build_report as build_live_trial_report,
    default_run_root as default_live_trial_run_root,
    selected_trials as selected_live_trials,
    trial_task_id as live_trial_task_id,
)
from EyeOfTerror.Pictorium.Moriana import moriana_forge_monitor as forge_monitor_module
from EyeOfTerror.Pictorium.Moriana.moriana_forge_monitor import monitor_forge_job
from EyeOfTerror.Pictorium.Moriana.moriana_governor import create_or_execute_run, make_handler, prepare_run
from EyeOfTerror.Pictorium.Moriana.moriana_executor import execute_revision_run
from EyeOfTerror.Pictorium.Moriana.moriana_runtime import MorianaRunStore
from EyeOfTerror.Pictorium.testing.fake_model_server import fake_pictorium_model
from EyeOfTerror.common_protocol import commander_order


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


def request_bytes(base: str, path: str) -> bytes:
    with urllib.request.urlopen(base + path, timeout=15) as response:
        data = response.read()
    if not data:
        raise AssertionError(f"endpoint returned an empty file: {path}")
    return data


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


def moriana_command(task: str, task_id: str) -> dict[str, object]:
    return commander_order(
        f"mission-{task_id}",
        to="Moriana",
        user_request=task,
        commander_intent="Создать и проверить визуальный run через Пикториум.",
        primary_goal=task,
        success_conditions=[
            "Moriana работает только по commander_order",
            "воркеры получают структурированные worker_order",
            "результат фиксирует качество и блокеры",
        ],
        constraints=[],
    )


def run_payload(task: str, task_id: str, **extra: object) -> dict[str, object]:
    return {
        "task": task,
        "task_id": task_id,
        "commander_order": moriana_command(task, task_id),
        **extra,
    }


def _main() -> int:
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

        inline_cleanup_job = JobRecord(
            id="inline-cleanup-job",
            spec=JobSpec(prompt="inline cleanup test image", width=512, height=512),
            status=JobStatus.queued,
            progress=0.0,
        )
        forge_store.create_job(inline_cleanup_job)
        unload_calls: list[bool] = []

        class FakeInlineQueue:
            def __init__(self, store: ForgeStore, start_worker: bool = True):
                self.store = store
                self.start_worker = start_worker

            def run_pending_once(self) -> bool:
                self.store.update_job("inline-cleanup-job", status=JobStatus.succeeded.value, progress=1.0)
                return True

            def unload_engines(self, engine_name: str | None = None) -> dict[str, object]:
                unload_calls.append(True)
                return {"ok": True, "engine": engine_name, "unloaded": ["fake"]}

        original_queue = forge_monitor_module.ForgeQueue
        forge_monitor_module.ForgeQueue = FakeInlineQueue  # type: ignore[assignment]
        try:
            inline_monitored = monitor_forge_job(
                db_path=forge_db_path,
                job_record=inline_cleanup_job.model_dump(mode="json"),
                run_inline_once=True,
            )
        finally:
            forge_monitor_module.ForgeQueue = original_queue
        if not unload_calls or inline_monitored.get("status") != "succeeded":
            raise AssertionError(f"inline Forge monitor must unload engines after run_inline_once: {inline_monitored}")
        live_report = build_live_trial_report(
            [
                {
                    "id": "mock-comic",
                    "task_kind": "comic",
                    "expected_kind": "comic",
                    "delivery_ready": True,
                    "quality_next_action": "accept_final",
                    "quality_score": 100,
                    "accepted_visual_artifact_count": 3,
                    "expected_min_visual_artifacts": 4,
                    "blocker_count": 0,
                }
            ],
            run_root=run_root,
        )
        if live_report.get("ok") or live_report.get("weak_cases", [{}])[0].get("weak_reasons") != ["accepted_visual_artifact_count_below_expected"]:
            raise AssertionError(f"live quality report should reject under-produced comic panels: {live_report}")
        if default_live_trial_run_root() != PROJECT_ROOT / "runtime" / "pictorium" / "runs":
            raise AssertionError(f"live quality runner must preserve runs by default: {default_live_trial_run_root()}")
        if len(selected_live_trials("smoke")) != 1 or len(selected_live_trials("full")) <= 1:
            raise AssertionError("live quality runner profile selection is broken")
        generated_live_id = live_trial_task_id("live 20260706", {"id": "simple smoke"})
        if generated_live_id != "live-20260706-simple-smoke":
            raise AssertionError(f"live quality runner did not build a safe persistent run id: {generated_live_id}")

        prepared = prepare_run("нарисуй картинку 512x512", "prepared-image", run_root / "prepared-image")
        run_dir = Path(str(prepared["run_dir"]))
        assert_run_workspace(run_dir)
        prepared_status = load_json(run_dir / "status.json")
        if prepared_status.get("run_id") != "prepared-image" or not prepared_status.get("pictorium_runtime"):
            raise AssertionError(f"prepare_run did not install Moriana runtime state: {prepared_status}")
        try:
            create_or_execute_run(run_root, {"task": "сырой запрос без приказа", "task_id": "raw-run-rejected"})
        except ValueError as exc:
            if "commander_order is required" not in str(exc):
                raise
        else:
            raise AssertionError("Moriana create_or_execute_run accepted raw task without commander_order")

        success = create_or_execute_run(
            run_root,
            run_payload("нарисуй простую картинку алтаря 512x512", "image-success", execute=True, test_artifact_mode="good"),
        )
        if not success.get("ok") or success.get("status", {}).get("status") != "completed":
            raise AssertionError(f"image success run did not complete: {success}")
        success_dir = Path(str(success["run_dir"]))
        assert_run_workspace(success_dir)
        success_types = artifact_types(success_dir)
        for required_type in ("prompt", "resource_report", "dispatch", "verification", "image", "final", "quality_report", "revision_decision"):
            if required_type not in success_types:
                raise AssertionError(f"image success registry missing {required_type}: {success_types}")
        success_registry = load_json(success_dir / "artifact_registry.json")
        worker_artifacts = [
            item
            for item in success_registry.get("artifacts", [])
            if isinstance(item, dict)
            and item.get("created_by") != "Moriana"
            and item.get("type") in {"prompt", "resource_report", "dispatch", "verification", "final"}
        ]
        if not worker_artifacts or any(item.get("metadata", {}).get("model_guidance_status") != "answered" for item in worker_artifacts):
            raise AssertionError(f"Moriana registry did not preserve worker model guidance status: {worker_artifacts}")
        image_plan_payload = load_json(success_dir / "prompts" / "image_plan_attempt_01.json")
        if image_plan_payload.get("model_guidance", {}).get("status") != "answered" or not image_plan_payload.get("model_guidance", {}).get("decision"):
            raise AssertionError(f"image plan did not preserve structured model guidance: {image_plan_payload}")
        success_quality = load_json(success_dir / "final" / "quality_report.json")
        if success_quality.get("next_action") != "accept_final" or not success_quality.get("delivery_ready"):
            raise AssertionError(f"image success quality report should be ready: {success_quality}")
        success_decision = load_json(success_dir / "final" / "revision_decision.json")
        if success_decision.get("action") != "accept_final" or success_decision.get("revision_required"):
            raise AssertionError(f"image success revision decision should accept final: {success_decision}")
        success_final = load_json(success_dir / "final" / "final_manifest.json")
        success_selection = success_final.get("final_selection", {})
        if (
            success_selection.get("selected_count") != 1
            or success_selection.get("accepted_candidate_count") != 1
            or success_decision.get("final_selection", {}).get("selected_count") != 1
            or success_decision.get("revision_strategy", {}).get("mode") != "accept_best_selected_final"
        ):
            raise AssertionError(f"image success did not explain final selection: {success_final} / {success_decision}")

        revision = create_or_execute_run(
            run_root,
            run_payload("нарисуй сложную картинку кузни 512x512", "image-revision", execute=True, test_artifact_mode="bad_then_good", max_revision_cycles=1),
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
        revision_final = load_json(revision_dir / "final" / "final_manifest.json")
        revision_selection = revision_final.get("final_selection", {})
        if (
            revision_selection.get("selected_count") != 1
            or revision_selection.get("best_attempt") != 2
            or revision_selection.get("rejected_candidate_count") < 1
            or revision_selection.get("selected_attempts") != [2]
        ):
            raise AssertionError(f"revision loop did not select the accepted revised artifact: {revision_selection}")

        failure = create_or_execute_run(
            run_root,
            run_payload("нарисуй картинку без тестового артефакта 512x512", "image-pending-failure", execute=True),
        )
        failure_dir = Path(str(failure["run_dir"]))
        if failure.get("ok") or load_json(failure_dir / "status.json").get("status") != "revising":
            raise AssertionError(f"missing-artifact run should stay in revising state with an explicit pending blocker: {failure}")
        if not (failure_dir / "revisions" / "revision_01.json").exists():
            raise AssertionError("failed image run did not write revision plan")
        failure_quality = load_json(failure_dir / "final" / "quality_report.json")
        if failure_quality.get("next_action") != "revise" or not failure_quality.get("revision_targets"):
            raise AssertionError(f"failed image run quality report should request revision: {failure_quality}")
        failure_decision = load_json(failure_dir / "final" / "revision_decision.json")
        if (
            failure_decision.get("action") != "wait_or_resubmit_forge_job"
            or not failure_decision.get("revision_required")
            or not any(item.get("target_worker") == "ForgeDispatcher" for item in failure_decision.get("targets", []) if isinstance(item, dict))
            or failure_decision.get("revision_strategy", {}).get("mode") != "continue_or_resubmit_generation"
        ):
            raise AssertionError(f"failed image run revision decision should target ForgeDispatcher: {failure_decision}")
        applied = execute_revision_run(MorianaRunStore(run_root), "image-pending-failure", test_artifact_mode="revision_good")
        applied_registry = load_json(failure_dir / "artifact_registry.json")
        if (
            not applied.get("ok")
            or applied.get("status", {}).get("status") != "completed"
            or applied.get("revision_execution", {}).get("ok") is not True
            or load_json(failure_dir / "final" / "revision_decision.json").get("action") != "accept_final"
            or "revision_execution" not in {str(item.get("type") or "") for item in applied_registry.get("artifacts", []) if isinstance(item, dict)}
        ):
            raise AssertionError(f"failed image run did not recover through apply_revision: {applied}")

        pending = create_or_execute_run(
            run_root,
            run_payload("нарисуй pending forge картинку 512x512", "image-pending-forge-job", execute=True, submit=True, wait_for_result=True, max_wait_sec=0),
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
        pending_decision = load_json(pending_dir / "final" / "revision_decision.json")
        if pending_decision.get("action") != "wait_or_resubmit_forge_job" or "forge_job_not_finished" not in pending_decision.get("blocker_codes", []):
            raise AssertionError(f"pending Forge job revision decision should wait/resubmit: {pending_decision}")

        series = create_or_execute_run(
            run_root,
            run_payload(
                "сделай серию 3 изображения про один и тот же древний механикум-алтарь 512x512",
                "image-series-success",
                execute=True,
                test_artifact_mode="series_good",
            ),
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
        series_quality = load_json(series_dir / "final" / "quality_report.json")
        if series_quality.get("accepted_image_count") != 3 or series_quality.get("next_action") != "accept_final":
            raise AssertionError(f"series quality report did not see all accepted images: {series_quality}")
        series_decision = load_json(series_dir / "final" / "revision_decision.json")
        if series_decision.get("action") != "accept_final":
            raise AssertionError(f"series revision decision should accept final: {series_decision}")
        series_selection = load_json(series_dir / "final" / "final_manifest.json").get("final_selection", {})
        if series_selection.get("selected_count") != 3 or series_selection.get("accepted_candidate_count") != 3:
            raise AssertionError(f"series final selection should include all accepted images: {series_selection}")

        failed_series = create_or_execute_run(
            run_root,
            run_payload("сделай серию 2 изображения для проверки ревизии 512x512", "image-series-revision", execute=True),
        )
        failed_series_dir = Path(str(failed_series["run_dir"]))
        if failed_series.get("ok") or load_json(failed_series_dir / "final" / "revision_decision.json").get("action") != "wait_or_resubmit_forge_job":
            raise AssertionError(f"failed image series should request Forge revision: {failed_series}")
        applied_series = execute_revision_run(MorianaRunStore(run_root), "image-series-revision", test_artifact_mode="series_good")
        applied_series_registry = load_json(failed_series_dir / "artifact_registry.json")
        applied_series_artifacts = [item for item in applied_series_registry.get("artifacts", []) if isinstance(item, dict)]
        accepted_series_attempts = {
            int(item.get("attempt") or 0)
            for item in applied_series_artifacts
            if item.get("type") == "image" and item.get("status") == "accepted"
        }
        rejected_series_attempts = {
            int(item.get("attempt") or 0)
            for item in applied_series_artifacts
            if item.get("type") == "verification" and item.get("status") == "rejected"
        }
        if (
            not applied_series.get("ok")
            or applied_series.get("final", {}).get("attempt") != 2
            or load_json(failed_series_dir / "final" / "revision_decision.json").get("action") != "accept_final"
            or accepted_series_attempts != {2}
            or 1 not in rejected_series_attempts
            or "revision_execution" not in {str(item.get("type") or "") for item in applied_series_artifacts}
        ):
            raise AssertionError(f"image series apply_revision did not preserve attempts and recover: {applied_series}")
        applied_series_selection = load_json(failed_series_dir / "final" / "final_manifest.json").get("final_selection", {})
        if applied_series_selection.get("selected_count") != 2 or applied_series_selection.get("selected_attempts") != [2]:
            raise AssertionError(f"image series revision did not select attempt 2 artifacts: {applied_series_selection}")

        comic = create_or_execute_run(
            run_root,
            run_payload("сделай комикс 4 панели про техножреца у древней кузни", "comic-success", execute=True, test_artifact_mode="comic_panels_good"),
        )
        comic_dir = Path(str(comic["run_dir"]))
        comic_types = artifact_types(comic_dir)
        for required_type in ("plan", "character_sheet", "comic_panel", "layout", "final"):
            if required_type not in comic_types:
                raise AssertionError(f"comic registry missing {required_type}: {comic_types}")
        comic_registry = load_json(comic_dir / "artifact_registry.json")
        accepted_panel_artifacts = [
            item
            for item in comic_registry.get("artifacts", [])
            if isinstance(item, dict)
            and item.get("type") == "comic_panel"
            and item.get("step") == "panel_art_generation"
            and item.get("status") == "accepted"
        ]
        if (
            len(accepted_panel_artifacts) != 4
            or comic.get("final", {}).get("panel_artifact_count") != 4
            or comic.get("quality_report", {}).get("accepted_visual_artifact_count") != 4
        ):
            raise AssertionError(f"comic run did not preserve accepted panel art artifacts: {comic}")
        if comic.get("status", {}).get("task_kind") != "comic":
            raise AssertionError(f"comic run did not preserve task_kind: {comic}")
        comic_selection = load_json(comic_dir / "final" / "final_manifest.json").get("final_selection", {})
        comic_decision = load_json(comic_dir / "final" / "revision_decision.json")
        if (
            comic_selection.get("policy") != "manifest_panel_artifacts"
            or comic_selection.get("selected_count") != 4
            or comic_decision.get("final_selection", {}).get("selected_count") != 4
        ):
            raise AssertionError(f"comic final selection should include all accepted panels: {comic_selection} / {comic_decision}")

        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(run_root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            created = request_json(
                base,
                "POST",
                "/runs",
                run_payload("нарисуй HTTP картинку 512x512", "http-image", execute=True, test_artifact_mode="good"),
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
            detail = request_json(base, "GET", "/runs/http-image")
            if (
                detail.get("status", {}).get("status") != "completed"
                or detail.get("artifact_summary", {}).get("accepted_visual_artifact_count") != 1
                or detail.get("quality_report", {}).get("next_action") != "accept_final"
                or detail.get("final", {}).get("final_selection", {}).get("selected_count") != 1
                or detail.get("revision_decision", {}).get("revision_strategy", {}).get("mode") != "accept_best_selected_final"
            ):
                raise AssertionError(f"HTTP run detail failed: {detail}")
            filtered_artifacts = request_json(base, "GET", "/runs/http-image/artifacts?type=image&status=accepted")
            if (
                filtered_artifacts.get("filters", {}).get("type") != "image"
                or len(filtered_artifacts.get("artifacts", [])) != 1
                or filtered_artifacts.get("artifacts", [{}])[0].get("status") != "accepted"
            ):
                raise AssertionError(f"HTTP filtered artifacts failed: {filtered_artifacts}")
            artifact_id = str(filtered_artifacts.get("artifacts", [{}])[0].get("artifact_id") or "")
            artifact_bytes = request_bytes(base, f"/runs/http-image/artifacts/{artifact_id}/file")
            if not artifact_bytes.startswith(b"\x89PNG"):
                raise AssertionError("HTTP artifact file endpoint did not return a PNG")
            final = request_json(base, "GET", "/runs/http-image/final")
            if final.get("final", {}).get("status") != "ready":
                raise AssertionError(f"HTTP final failed: {final}")
            quality = request_json(base, "GET", "/runs/http-image/quality")
            if quality.get("quality_report", {}).get("next_action") != "accept_final":
                raise AssertionError(f"HTTP quality failed: {quality}")
            decision = request_json(base, "GET", "/runs/http-image/revision-decision")
            if decision.get("revision_decision", {}).get("action") != "accept_final":
                raise AssertionError(f"HTTP revision-decision failed: {decision}")
            audit = request_json(base, "POST", "/runs/http-image/audit", {})
            if audit.get("quality_report", {}).get("kind") != "pictorium_quality_report" or audit.get("revision_decision", {}).get("kind") != "pictorium_revision_decision":
                raise AssertionError(f"HTTP audit failed: {audit}")
            decided = request_json(base, "POST", "/runs/http-image/decide_revision", {})
            if decided.get("revision_decision", {}).get("action") != "accept_final":
                raise AssertionError(f"HTTP decide_revision failed: {decided}")
            http_failed = request_json(
                base,
                "POST",
                "/runs",
                run_payload("нарисуй HTTP картинку для ревизии 512x512", "http-image-revision", execute=True),
            )
            if http_failed.get("ok") or http_failed.get("revision_decision", {}).get("action") != "wait_or_resubmit_forge_job":
                raise AssertionError(f"HTTP failed revision fixture did not request revision: {http_failed}")
            http_applied = request_json(base, "POST", "/runs/http-image-revision/apply_revision", {"test_artifact_mode": "revision_good"})
            if (
                not http_applied.get("ok")
                or http_applied.get("status", {}).get("status") != "completed"
                or http_applied.get("revision_decision", {}).get("action") != "accept_final"
                or http_applied.get("revision_execution", {}).get("ok") is not True
            ):
                raise AssertionError(f"HTTP apply_revision failed: {http_applied}")
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


def main() -> int:
    with fake_pictorium_model():
        return _main()


if __name__ == "__main__":
    raise SystemExit(main())
