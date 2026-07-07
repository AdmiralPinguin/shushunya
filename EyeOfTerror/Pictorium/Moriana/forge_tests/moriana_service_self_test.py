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

from EyeOfTerror.Pictorium.Moriana.moriana_governor import make_handler, task_from_payload
from EyeOfTerror.Pictorium.testing.fake_model_server import fake_pictorium_model
from EyeOfTerror.common_protocol import commander_order, validate_protocol_payload


def request_json(base: str, method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise AssertionError(f"endpoint returned non-object JSON: {path}")
    return result


def request_bytes(base: str, path: str) -> bytes:
    with urllib.request.urlopen(base + path, timeout=10) as response:
        data = response.read()
    if not data:
        raise AssertionError(f"endpoint returned an empty file: {path}")
    return data


def moriana_command(task: str, task_id: str) -> dict[str, object]:
    order = commander_order(
        f"mission-{task_id}",
        to="Moriana",
        user_request=task,
        commander_intent="Проверить, что Мориана принимает приказ Вармастера как протокольный вход.",
        primary_goal=task,
        success_conditions=[
            "governor_plan preserves the commander mission_id",
            "worker_order packets preserve the commander mission_id",
            "Moriana does not fall back to a direct raw task protocol",
        ],
        constraints=["Do not answer the user directly from Moriana."],
        escalate_to_user_if=["visual task cannot be represented by active workers"],
    )
    validate_protocol_payload(order, expected_type="commander_order")
    return order


def protocol_only_order(task_id: str) -> dict[str, object]:
    order = commander_order(
        f"mission-{task_id}",
        to="Moriana",
        user_request="ПРИКАЗ ВАРМАСТЕРА\nСырой визуальный запрос не должен стать task.",
        commander_intent="Передать Мориане нормализованную визуальную задачу.",
        primary_goal="нарисуй протокольную картинку 512x512",
        success_conditions=["governor receives primary_goal as task compatibility text"],
        constraints=["Do not use raw user_request as the transport task."],
    )
    validate_protocol_payload(order, expected_type="commander_order")
    return order


def _main() -> int:
    direct_order = protocol_only_order("moriana-protocol-direct")
    direct_task, direct_command = task_from_payload({"commander_order": direct_order})
    if (
        not direct_task.startswith(str(direct_order["primary_goal"]))
        or direct_task.startswith("ПРИКАЗ ВАРМАСТЕРА")
        or "Do not use raw user_request as the transport task." not in direct_task
        or direct_command != direct_order
    ):
        raise AssertionError(f"Moriana task_from_payload did not stay protocol-first: task={direct_task!r} command={direct_command}")
    try:
        task_from_payload({"task": "сырой обход бригадира"})
    except ValueError as exc:
        if "commander_order is required" not in str(exc):
            raise AssertionError(f"bad direct task rejection: {exc}") from exc
    else:
        raise AssertionError("Moriana accepted direct task input without commander_order")
    with tempfile.TemporaryDirectory(prefix="moriana-service-self-test-") as tmp:
        run_root = Path(tmp) / "runs"
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(run_root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            health = request_json(base, "GET", "/health")
            if not health.get("ok") or health.get("governor") != "Moriana":
                raise AssertionError(f"bad health payload: {health}")
            capabilities = request_json(base, "GET", "/capabilities")
            if (
                not capabilities.get("ok")
                or capabilities.get("governor") != "Moriana"
                or "ScenarioScribe" not in capabilities.get("required_workers", [])
                or "Promptwright" not in capabilities.get("required_workers", [])
                or "GET /runs/{run_id}" not in capabilities.get("endpoints", [])
                or "GET /runs/{run_id}/revision-decision" not in capabilities.get("endpoints", [])
                or "POST /runs/{run_id}/apply_revision" not in capabilities.get("endpoints", [])
            ):
                raise AssertionError(f"bad capabilities payload: {capabilities}")
            try:
                request_json(base, "POST", "/plan", {"task": "нарисуй картинку 512x512", "task_id": "moriana-raw-reject"})
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
                rejected = json.loads(exc.read().decode("utf-8"))
                if "commander_order is required" not in str(rejected.get("error", "")):
                    raise AssertionError(f"bad raw task rejection: {rejected}")
            else:
                raise AssertionError("Moriana /plan accepted raw task without commander_order")
            image_plan = request_json(
                base,
                "POST",
                "/plan",
                {
                    "task": "нарисуй картинку 512x512",
                    "task_id": "moriana-http-image",
                    "commander_order": moriana_command("нарисуй картинку 512x512", "moriana-http-image"),
                },
            )
            if (
                not image_plan.get("ok")
                or image_plan.get("contract", {}).get("assigned_governor") != "Moriana"
                or image_plan.get("contract", {}).get("worker_plan", [])[0].get("worker") != "Promptwright"
            ):
                raise AssertionError(f"bad image plan payload: {image_plan}")
            comic_plan = request_json(
                base,
                "POST",
                "/plan",
                {
                    "task": "сделай комикс 3 панели про кузню",
                    "task_id": "moriana-http-comic",
                    "commander_order": moriana_command("сделай комикс 3 панели про кузню", "moriana-http-comic"),
                },
            )
            if (
                not comic_plan.get("ok")
                or comic_plan.get("contract", {}).get("worker_plan", [])[0].get("worker") != "ScenarioScribe"
            ):
                raise AssertionError(f"bad comic plan payload: {comic_plan}")
            protocol_only_plan = request_json(
                base,
                "POST",
                "/plan",
                {"task_id": "moriana-http-protocol-only-plan", "commander_order": protocol_only_order("moriana-http-protocol-only-plan")},
            )
            if (
                not protocol_only_plan.get("ok")
                or protocol_only_plan.get("governor_plan", {}).get("understanding") != "нарисуй протокольную картинку 512x512"
                or "task" in protocol_only_plan.get("actions", {}).get("next_action", {}).get("body", {})
            ):
                raise AssertionError(f"Moriana /plan did not use commander_order as authority: {protocol_only_plan}")
            prepared = request_json(
                base,
                "POST",
                "/prepare_run",
                {
                    "task": "сделай комикс 3 панели про кузню",
                    "task_id": "moriana-http-comic-run",
                    "commander_order": moriana_command("сделай комикс 3 панели про кузню", "moriana-http-comic-run"),
                },
            )
            run_dir = Path(str(prepared.get("run_dir") or ""))
            if (
                not prepared.get("ok")
                or prepared.get("governor") != "Moriana"
                or not run_dir.exists()
                or not (run_dir / "contract.json").exists()
                or not (run_dir / "dispatch" / "scenario.json").exists()
                or not (run_dir / "artifact_registry.json").exists()
            ):
                raise AssertionError(f"bad prepare_run payload: {prepared}")
            protocol_task = "нарисуй протокольную картинку 512x512"
            protocol_command = moriana_command(protocol_task, "moriana-http-protocol-run")
            protocol_prepared = request_json(
                base,
                "POST",
                "/prepare_run",
                {
                    "task": protocol_task,
                    "task_id": "moriana-http-protocol-run",
                    "commander_order": protocol_command,
                },
            )
            protocol_run_dir = Path(str(protocol_prepared.get("run_dir") or ""))
            protocol_plan = json.loads((protocol_run_dir / "governor_plan.json").read_text(encoding="utf-8"))
            protocol_dispatch = json.loads((protocol_run_dir / "dispatch" / "image_planning.json").read_text(encoding="utf-8"))
            if (
                not protocol_prepared.get("ok")
                or protocol_plan.get("mission_id") != "mission-moriana-http-protocol-run"
                or protocol_dispatch.get("worker_order", {}).get("mission_id") != "mission-moriana-http-protocol-run"
                or protocol_dispatch.get("request", {}).get("worker_order", {}).get("mission_id") != "mission-moriana-http-protocol-run"
            ):
                raise AssertionError(
                    "Moriana /prepare_run did not preserve commander_order mission_id: "
                    f"prepared={protocol_prepared} plan={protocol_plan} dispatch={protocol_dispatch}"
                )
            protocol_only_run = request_json(
                base,
                "POST",
                "/runs",
                {"task_id": "moriana-http-protocol-only-run", "commander_order": protocol_only_order("moriana-http-protocol-only-run")},
            )
            if (
                not protocol_only_run.get("ok")
                or str(protocol_only_run.get("status", {}).get("task") or "").startswith("ПРИКАЗ ВАРМАСТЕРА")
                or not str(protocol_only_run.get("status", {}).get("task") or "").startswith("нарисуй протокольную картинку 512x512")
                or "Do not use raw user_request as the transport task." not in str(protocol_only_run.get("status", {}).get("task") or "")
            ):
                raise AssertionError(f"Moriana /runs did not use commander_order as authority: {protocol_only_run}")
            executed = request_json(
                base,
                "POST",
                "/runs",
                {
                    "task": "нарисуй HTTP smoke картинку 512x512",
                    "task_id": "moriana-http-exec-image",
                    "commander_order": moriana_command("нарисуй HTTP smoke картинку 512x512", "moriana-http-exec-image"),
                    "execute": True,
                    "test_artifact_mode": "good",
                },
            )
            if not executed.get("ok") or executed.get("status", {}).get("status") != "completed":
                raise AssertionError(f"bad /runs execution payload: {executed}")
            status = request_json(base, "GET", "/runs/moriana-http-exec-image/status")
            if status.get("status", {}).get("status") != "completed":
                raise AssertionError(f"bad /runs/{{id}}/status payload: {status}")
            artifacts = request_json(base, "GET", "/runs/moriana-http-exec-image/artifacts")
            if not artifacts.get("artifacts"):
                raise AssertionError(f"bad /runs/{{id}}/artifacts payload: {artifacts}")
            detail = request_json(base, "GET", "/runs/moriana-http-exec-image")
            if (
                detail.get("status", {}).get("status") != "completed"
                or detail.get("artifact_summary", {}).get("accepted_visual_artifact_count") != 1
                or detail.get("quality_report", {}).get("next_action") != "accept_final"
                or detail.get("final", {}).get("final_selection", {}).get("selected_count") != 1
                or detail.get("revision_decision", {}).get("revision_strategy", {}).get("mode") != "accept_best_selected_final"
            ):
                raise AssertionError(f"bad /runs/{{id}} detail payload: {detail}")
            filtered_artifacts = request_json(base, "GET", "/runs/moriana-http-exec-image/artifacts?type=image&status=accepted")
            if (
                filtered_artifacts.get("filters", {}).get("status") != "accepted"
                or len(filtered_artifacts.get("artifacts", [])) != 1
            ):
                raise AssertionError(f"bad filtered /runs/{{id}}/artifacts payload: {filtered_artifacts}")
            artifact_id = str(filtered_artifacts.get("artifacts", [{}])[0].get("artifact_id") or "")
            artifact_bytes = request_bytes(base, f"/runs/moriana-http-exec-image/artifacts/{artifact_id}/file")
            if not artifact_bytes.startswith(b"\x89PNG"):
                raise AssertionError("artifact file endpoint did not return a PNG")
            final = request_json(base, "GET", "/runs/moriana-http-exec-image/final")
            if final.get("final", {}).get("status") != "ready":
                raise AssertionError(f"bad /runs/{{id}}/final payload: {final}")
            quality = request_json(base, "GET", "/runs/moriana-http-exec-image/quality")
            if quality.get("quality_report", {}).get("next_action") != "accept_final":
                raise AssertionError(f"bad /runs/{{id}}/quality payload: {quality}")
            decision = request_json(base, "GET", "/runs/moriana-http-exec-image/revision-decision")
            if decision.get("revision_decision", {}).get("action") != "accept_final":
                raise AssertionError(f"bad /runs/{{id}}/revision-decision payload: {decision}")
            audit = request_json(base, "POST", "/runs/moriana-http-exec-image/audit", {})
            if audit.get("quality_report", {}).get("kind") != "pictorium_quality_report" or audit.get("revision_decision", {}).get("kind") != "pictorium_revision_decision":
                raise AssertionError(f"bad /runs/{{id}}/audit payload: {audit}")
            decided = request_json(base, "POST", "/runs/moriana-http-exec-image/decide_revision", {})
            if decided.get("revision_decision", {}).get("action") != "accept_final":
                raise AssertionError(f"bad /runs/{{id}}/decide_revision payload: {decided}")
            failed = request_json(
                base,
                "POST",
                "/runs",
                {
                    "task": "нарисуй HTTP smoke картинку для apply revision 512x512",
                    "task_id": "moriana-http-revision-image",
                    "commander_order": moriana_command("нарисуй HTTP smoke картинку для apply revision 512x512", "moriana-http-revision-image"),
                    "execute": True,
                },
            )
            if failed.get("ok") or failed.get("revision_decision", {}).get("action") != "wait_or_resubmit_forge_job":
                raise AssertionError(f"bad failed revision fixture payload: {failed}")
            applied = request_json(base, "POST", "/runs/moriana-http-revision-image/apply_revision", {"test_artifact_mode": "revision_good"})
            if not applied.get("ok") or applied.get("revision_decision", {}).get("action") != "accept_final":
                raise AssertionError(f"bad /runs/{{id}}/apply_revision payload: {applied}")
        finally:
            server.shutdown()
            thread.join(timeout=5)
    print("[ok] Moriana HTTP service endpoints")
    return 0


def main() -> int:
    with fake_pictorium_model():
        return _main()


if __name__ == "__main__":
    raise SystemExit(main())
