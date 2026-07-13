#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WARMMASTER_ROOT = Path(__file__).resolve().parent
for entry in (str(PROJECT_ROOT), str(WARMMASTER_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from EyeOfTerror.common_protocol import commander_order
from eye_of_terror.inner_circle import iskandar_service as service
from eye_of_terror.native_research_run import (
    load_native_research_run,
    validate_native_research_run_package,
)
from eye_of_terror.task_prepare import prepare_native_iskandar_via_service


TOKEN = "iskandar-research-warband-test-token-0123456789abcdef"


def command(task_id: str, *, constraint: str = "Do not exceed the evidence.") -> dict:
    return commander_order(
        f"mission-{task_id}",
        to="IskandarKhayon",
        user_request="raw user wording",
        commander_intent="Delegate one bounded research outcome.",
        primary_goal="Determine the supported answer.",
        success_conditions=["Every material claim is evidence-bound."],
        constraints=[constraint],
        escalate_to_user_if=["The question is materially ambiguous."],
    )


def model_answer() -> dict:
    return {
        "ok": True,
        "status": "answered",
        "content": {
            "decision": "delegate",
            "research_objective": "Determine the supported answer.",
            "depth": "standard",
            "source_policy": "balanced",
            "error_tolerance": "strict",
            "answer_mode": "direct_answer",
            "priorities": ["Prefer direct evidence."],
            "allowed_source_classes": ["primary_source", "official_documentation"],
            "prohibited_source_classes": ["machine_generated_summary"],
            "constraints": [],
            "success_conditions": [],
            "output_requirements": ["Return a concise evidence-bound answer."],
            "escalation_conditions": [],
            "clarification_question": "",
        },
    }


def healthy_backend() -> dict:
    return {
        "name": "ResearchWarband",
        "kind": "native_research_warband",
        "endpoint": "http://127.0.0.1:7201",
        "health_endpoint": "http://127.0.0.1:7201/health",
        "healthy": True,
        "status": "healthy",
        "health": {"ok": True},
        "error": "",
        "dispatch_owner": "native_research_backend_router",
        "contract_relation": "executes one native Iskandar-delegated research mission",
    }


def request_json(
    url: str,
    payload: dict | None = None,
    *,
    token: str = TOKEN,
) -> tuple[int, dict]:
    data = None
    headers = {"Accept": "application/json"}
    method = "GET"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Authorization"] = f"Bearer {token}"
        method = "POST"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return int(exc.code), json.loads(exc.read().decode("utf-8"))


def main() -> int:
    os.environ["RESEARCH_WARBAND_BEARER_TOKEN"] = TOKEN

    task, normalized = service.task_from_payload({"commander_order": command("protocol")})
    assert task.startswith("Determine the supported answer.")
    assert normalized["mission_id"] == "mission-protocol"
    try:
        service.task_from_payload({"task": "bypass"})
    except ValueError as exc:
        assert "commander_order is required" in str(exc)
    else:
        raise AssertionError("direct governor input bypassed commander_order")
    assert service.required_workers() == []
    assert service.pipeline_summary()["steps"] == [
        {
            "step_id": "research_warband",
            "backend": "ResearchWarband",
            "depends_on": [],
            "ownership": (
                "detailed planning, search, acquisition, reading, evidence construction, "
                "analysis, writing, semantic verification, and internal repair"
            ),
        }
    ]

    with tempfile.TemporaryDirectory() as temp_dir:
        run_root = Path(temp_dir) / "runs"
        run_root.mkdir()
        model_calls: list[dict] = []

        def fake_model(*args, **kwargs):
            model_calls.append({"args": args, "kwargs": kwargs})
            return model_answer()

        with (
            patch.object(service, "research_warband_backend_health", side_effect=lambda *_a, **_k: healthy_backend()),
            patch.object(service, "request_model_decision", side_effect=fake_model),
        ):
            server = ThreadingHTTPServer(("127.0.0.1", 0), service.make_handler(run_root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                status, health = request_json(base + "/health")
                assert status == 200 and health["readiness"] is True
                status, capabilities = request_json(base + "/capabilities")
                assert status == 200
                assert capabilities["required_workers"] == []
                assert capabilities["contract_mode"] == "native_research_warband_v1"
                assert capabilities["execution_contract"]["legacy_worker_plan_present"] is False

                plan_task_id = "iskandar-structural-plan"
                status, plan = request_json(
                    base + "/plan",
                    {"task_id": plan_task_id, "commander_order": command(plan_task_id)},
                )
                assert status == 200 and plan["ok"] is True
                assert plan["contract"]["execution"] == {
                    "kind": "research_warband_mission",
                    "step_id": "research_warband",
                    "backend": "ResearchWarband",
                }
                assert plan["governor_plan"]["work_plan"][0]["worker"] == "ResearchWarband"
                assert not model_calls, "/plan invoked the leader model"
                assert not (run_root / plan_task_id).exists(), "/plan persisted a run"

                prepare_task_id = "iskandar-native-prepare"
                prepare_payload = {
                    "task_id": prepare_task_id,
                    "run_dir": str(run_root / prepare_task_id),
                    "commander_order": command(prepare_task_id),
                }
                status, prepared = request_json(base + "/prepare_run", prepare_payload)
                assert status == 200 and prepared["prepare_replayed"] is False
                assert len(model_calls) == 1
                assert validate_native_research_run_package(run_root / prepare_task_id) == []
                package = load_native_research_run(run_root / prepare_task_id)
                assert package["receipt"]["kind"] == "native_research_run_receipt"
                assert package["leadership_directive"]["delegated_to"] == "ResearchWarband"
                assert not (run_root / prepare_task_id / "dispatch").exists()

                status, replay = request_json(base + "/prepare_run", prepare_payload)
                assert status == 200 and replay["prepare_replayed"] is True
                assert replay["model_brain"]["status"] == "persisted"
                assert len(model_calls) == 1, "idempotent replay called the leader again"

                changed = dict(prepare_payload)
                changed["commander_order"] = command(
                    prepare_task_id,
                    constraint="Use only official documentation.",
                )
                status, conflict = request_json(base + "/prepare_run", changed)
                assert status == 409 and conflict["error_code"] == "prepare_identity_conflict"
                assert len(model_calls) == 1

                unauth_id = "iskandar-unauthorized"
                status, unauthorized = request_json(
                    base + "/prepare_run",
                    {
                        "task_id": unauth_id,
                        "run_dir": str(run_root / unauth_id),
                        "commander_order": command(unauth_id),
                    },
                    token="wrong",
                )
                assert status == 401 and "authentication failed" in unauthorized["error"]

                gateway_task_id = "iskandar-abaddon-prepare"
                gateway_result = prepare_native_iskandar_via_service(
                    "ignored raw message",
                    gateway_task_id,
                    run_root,
                    SimpleNamespace(name="IskandarKhayon", port=server.server_port),
                    commander_order=command(gateway_task_id),
                    require_commander_order=True,
                )
                assert gateway_result["ok"] is True, gateway_result
                assert gateway_result["contract"]["task_id"] == gateway_task_id
                assert (run_root / gateway_task_id / "task_ledger.json").is_file()
                assert validate_native_research_run_package(run_root / gateway_task_id) == []
                assert len(model_calls) == 2

                gateway_replay = prepare_native_iskandar_via_service(
                    "ignored raw message",
                    gateway_task_id,
                    run_root,
                    SimpleNamespace(name="IskandarKhayon", port=server.server_port),
                    commander_order=command(gateway_task_id),
                    require_commander_order=True,
                )
                assert gateway_replay["ok"] is True
                assert gateway_replay["prepare_replayed"] is True
                assert len(model_calls) == 2
            finally:
                server.shutdown()
                thread.join(timeout=5)

    print("[ok] native Iskandar leadership service")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
