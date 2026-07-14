#!/usr/bin/env python3
"""Regression barrier for the native Ceraxia -> Skitarii preparation path."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PROJECT_ROOT = next(
    candidate
    for candidate in Path(__file__).resolve().parents
    if (candidate / "EyeOfTerror" / "model_brain.py").is_file()
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eye_of_terror import task_prepare
from eye_of_terror.inner_circle import ceraxia_service as service
from eye_of_terror.native_code_run import (
    is_native_code_run,
    validate_native_code_run_package,
)
from EyeOfTerror.common_protocol import commander_order, validate_protocol_payload


def command(task: str, task_id: str, *, extra_constraint: str = "") -> dict:
    constraints = ["Preserve existing user changes."]
    if extra_constraint:
        constraints.append(extra_constraint)
    payload = commander_order(
        f"mission-{task_id}",
        to="Ceraxia",
        user_request=task,
        commander_intent="Delegate one bounded code mission through Ceraxia.",
        primary_goal=task,
        success_conditions=["The requested behavior passes executable verification."],
        constraints=constraints,
        escalate_to_user_if=["A product decision is required."],
    )
    validate_protocol_payload(payload, expected_type="commander_order")
    return payload


def model_answer(decision: str = "delegate") -> dict:
    return {
        "ok": True,
        "content": json.dumps(
            {
                "decision": decision,
                "mission_intent": "Deliver the requested behavior without scope drift.",
                "priorities": ["Correct behavior", "Honest verification"],
                "constraints": ["Preserve existing user changes."],
                "success_conditions": [
                    "The requested behavior passes executable verification.",
                ],
                "tradeoffs": ["Prefer a bounded change over a broad refactor."],
                "escalation_conditions": ["A product decision is required."],
            },
            ensure_ascii=False,
        ),
    }


def healthy_backend() -> dict:
    return {
        "name": "SkitariiWarband",
        "kind": "vm_isolated_code_warband",
        "endpoint": "http://127.0.0.1:7200",
        "healthy": True,
        "status": "healthy",
        "lifecycle": "active",
        "health": {"status": "ok", "vm_alive": True, "process_boundary_ready": True},
        "error": "",
    }


def task_memory_context(task_memory_id: str) -> dict:
    return {
        "task_memory_id": task_memory_id,
        "root_task_id": task_memory_id,
        "available": True,
        "revision": 1,
        "sha256": "1" * 64,
        "content": f"# Task memory\n\nGoal page for {task_memory_id}.",
    }


def request_json(url: str, payload: dict | None = None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return int(exc.code), json.loads(exc.read().decode("utf-8"))


def main() -> int:
    assert service.required_workers() == []
    pipeline = service.pipeline_summary()
    assert pipeline["kind"] == "native_code_run"
    assert pipeline["step_count"] == 1
    assert pipeline["steps"] == [
        {
            "step_id": "skitarii",
            "backend": "SkitariiWarband",
            "depends_on": [],
            "ownership": (
                "repository exploration, detailed planning, implementation, "
                "verification, and internal repair"
            ),
        }
    ]
    assert "worker_plan" not in json.dumps(pipeline)
    assert "repository_survey" not in json.dumps(service.service_capabilities())

    with tempfile.TemporaryDirectory() as raw_root:
        root = Path(raw_root)
        resolved = service.resolve_run_dir(root, "", "single-run-world")
        assert resolved == (root / "single-run-world").resolve(), resolved
        alternate = root.parent / "orphan-ceraxia-world" / "single-run-world"
        try:
            service.resolve_run_dir(root, str(alternate), "single-run-world")
        except ValueError as exc:
            assert "exact task-scoped child" in str(exc), exc
        else:
            raise AssertionError("Ceraxia accepted an orphan run root outside Gateway's world")
        handler = service.make_handler(root)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        task = "Fix the native Ceraxia preparation regression."
        task_id = "native-ceraxia-service"
        order = command(task, task_id)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        prepare_payload = {
            "task_id": task_id,
            "commander_order": order,
            "run_dir": str(root / task_id),
        }
        with (
            mock.patch.object(service, "skitarii_backend_health", side_effect=lambda *a, **k: healthy_backend()),
            mock.patch.object(service, "_load_task_memory_context", side_effect=task_memory_context),
            mock.patch.object(service, "request_model_decision", return_value=model_answer()) as brain,
        ):
            thread.start()
            try:
                status, preview = request_json(
                    base + "/plan",
                    {"task_id": task_id, "commander_order": order},
                )
                assert status == 200 and preview["ok"] is True, (status, preview)
                assert brain.call_count == 0, "structural /plan must not invoke Ceraxia"
                assert preview["contract"]["execution"] == {
                    "kind": "skitarii_mission",
                    "step_id": "skitarii",
                    "backend": "SkitariiWarband",
                }
                assert "worker_plan" not in preview["contract"]
                assert len(preview["governor_plan"]["work_plan"]) == 1

                status, prepared = request_json(base + "/prepare_run", prepare_payload)
                assert status == 200 and prepared["ok"] is True
                assert prepared["prepare_replayed"] is False
                assert brain.call_count == 1, "prepare must make one leader decision"
                run_dir = root / task_id
                assert is_native_code_run(run_dir)
                assert validate_native_code_run_package(run_dir) == []
                assert not (run_dir / "dispatch").exists()
                assert prepared["leadership_directive"]["delegated_to"] == "SkitariiWarband"

                status, replayed = request_json(base + "/prepare_run", prepare_payload)
                assert status == 200 and replayed["prepare_replayed"] is True
                assert replayed["leadership_directive"] == prepared["leadership_directive"]
                assert brain.call_count == 1, "idempotent replay must not ask the model again"

                memory_conflicting = dict(prepare_payload)
                memory_conflicting["task_memory_id"] = "different-goal-memory"
                status, memory_conflict = request_json(
                    base + "/prepare_run", memory_conflicting,
                )
                assert status == 409
                assert memory_conflict["error_code"] == "prepare_identity_conflict"
                assert brain.call_count == 1

                conflicting = dict(prepare_payload)
                conflicting["commander_order"] = command(
                    task,
                    task_id,
                    extra_constraint="Different request identity.",
                )
                status, conflict = request_json(base + "/prepare_run", conflicting)
                assert status == 409
                assert conflict["error_code"] == "prepare_identity_conflict"
                assert brain.call_count == 1

                gateway_task_id = "native-ceraxia-gateway"
                gateway_order = command(task, gateway_task_id)
                governor = SimpleNamespace(
                    name="Ceraxia",
                    port=server.server_address[1],
                )
                gateway_prepared = task_prepare.prepare_native_ceraxia_via_service(
                    task,
                    gateway_task_id,
                    root,
                    governor,
                    commander_order=gateway_order,
                )
                assert gateway_prepared["ok"] is True, gateway_prepared
                assert gateway_prepared["prepare_replayed"] is False
                assert brain.call_count == 2
                gateway_run_dir = root / gateway_task_id
                assert validate_native_code_run_package(gateway_run_dir) == []
                assert (gateway_run_dir / "task_ledger.json").is_file()
                gateway_memory_context = json.loads(
                    (gateway_run_dir / "task_memory_context.json").read_text(
                        encoding="utf-8",
                    )
                )
                assert gateway_memory_context["task_memory_id"] == gateway_task_id

                gateway_replayed = task_prepare.prepare_native_ceraxia_via_service(
                    task,
                    gateway_task_id,
                    root,
                    governor,
                    commander_order=gateway_order,
                )
                assert gateway_replayed["ok"] is True, gateway_replayed
                assert gateway_replayed["prepare_replayed"] is True
                assert brain.call_count == 2

                gateway_conflict = task_prepare.prepare_native_ceraxia_via_service(
                    task,
                    gateway_task_id,
                    root,
                    governor,
                    commander_order=command(
                        task,
                        gateway_task_id,
                        extra_constraint="Different gateway request identity.",
                    ),
                )
                assert gateway_conflict["ok"] is False
                assert gateway_conflict["error_code"] == "ceraxia_prepare_identity_conflict"
                assert brain.call_count == 2

                tampered_task_id = "native-ceraxia-receipt-tamper"
                tampered_order = command(task, tampered_task_id)
                original_post = task_prepare._post_governor_json

                def tamper_receipt(url: str, payload: dict, governor_name: str) -> dict:
                    result = original_post(url, payload, governor_name)
                    if url.endswith("/prepare_run") and result.get("ok") is True:
                        receipt_path = root / tampered_task_id / "native_run_receipt.json"
                        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                        receipt["prepare_request_sha256"] = "0" * 64
                        receipt_path.write_text(
                            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )
                    return result

                with mock.patch.object(
                    task_prepare,
                    "_post_governor_json",
                    side_effect=tamper_receipt,
                ):
                    tampered = task_prepare.prepare_native_ceraxia_via_service(
                        task,
                        tampered_task_id,
                        root,
                        governor,
                        commander_order=tampered_order,
                    )
                assert tampered["ok"] is False
                assert tampered["error_code"] == "governor_prepare_invalid_run"
                assert "different prepare request" in tampered["error"]
                assert not (root / tampered_task_id).exists()
                assert brain.call_count == 3

                partial_task_id = "native-ceraxia-partial-publish"
                partial_final = root / partial_task_id

                def fail_during_staged_write(staging_dir: Path, *args, **kwargs):
                    assert staging_dir.parent == root
                    assert staging_dir.name.startswith(f".{partial_task_id}.prepare-")
                    assert not partial_final.exists(), "partial final run became observable"
                    (staging_dir / "partial.json").write_text("{}\n", encoding="utf-8")
                    raise OSError("simulated package write failure")

                with mock.patch.object(
                    service,
                    "write_native_code_run",
                    side_effect=fail_during_staged_write,
                ):
                    status, partial = request_json(
                        base + "/prepare_run",
                        {
                            "task_id": partial_task_id,
                            "commander_order": command(task, partial_task_id),
                            "run_dir": str(partial_final),
                        },
                    )
                assert status == 500 and partial["ok"] is False
                assert not partial_final.exists()
                assert not list(root.glob(f".{partial_task_id}.prepare-*"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    with tempfile.TemporaryDirectory() as raw_root:
        root = Path(raw_root)
        handler = service.make_handler(root)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        task_id = "native-ceraxia-reject"
        order = command("Reject this unsafe mission.", task_id)
        with (
            mock.patch.object(service, "skitarii_backend_health", side_effect=lambda *a, **k: healthy_backend()),
            mock.patch.object(service, "_load_task_memory_context", side_effect=task_memory_context),
            mock.patch.object(service, "request_model_decision", return_value=model_answer("reject")) as brain,
        ):
            thread.start()
            try:
                status, rejected = request_json(
                    f"http://127.0.0.1:{server.server_address[1]}/prepare_run",
                    {
                        "task_id": task_id,
                        "commander_order": order,
                        "run_dir": str(root / task_id),
                    },
                )
                assert status == 409
                assert rejected["error_code"] == "delegation_not_authorized"
                assert not (root / task_id).exists()
                assert brain.call_count == 1
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    print("ceraxia native service self-test: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
