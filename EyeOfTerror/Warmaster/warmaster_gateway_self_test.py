#!/usr/bin/env python3
"""Gateway regression barrier for the native Ceraxia -> Skitarii route.

This intentionally does not recreate the retired six-worker code pipeline.
Research/image worker behavior has its own focused barriers; this test owns the
user-facing code route and the absence of phantom code-worker machinery.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from unittest import mock

PROJECT_ROOT = next(
    candidate
    for candidate in Path(__file__).resolve().parents
    if (candidate / "EyeOfTerror" / "model_brain.py").is_file()
)
WARM_ROOT = PROJECT_ROOT / "EyeOfTerror" / "Warmaster"
for entry in (PROJECT_ROOT, WARM_ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import eye_of_terror.brigade as brigade
import eye_of_terror.local_executor as local_executor
import eye_of_terror.mission_control as mission_control
import eye_of_terror.orchestrator as orchestrator
import eye_of_terror.routing as routing
import eye_of_terror.skitarii_bridge as skitarii_bridge
import eye_of_terror.task_prepare as task_prepare
import eye_of_terror.warmaster_gateway as warmaster_gateway
from EyeOfTerror.model_brain import model_contract
from eye_of_terror.inner_circle import ceraxia_service
from eye_of_terror.native_code_run import (
    is_native_code_run,
    validate_native_code_run_package,
)
from eye_of_terror.warmaster_gateway import make_handler


RETIRED_CODE_WORKERS = {
    "CogitatorCodewright",
    "LogisRepository",
    "MagosStrategos",
    "FerrumPatchwright",
    "OrdinatusVerifier",
    "JudicatorCodicis",
    "SealwrightFinalis",
}
RETIRED_CODE_PORTS = {7014, 7015, 7016, 7017, 7018, 7019, 7020}


def fake_model_decision(
    owner: str,
    role: str,
    request: dict,
    *,
    layer: str = "worker",
    instructions: str = "",
) -> dict:
    del instructions
    if owner == "WarmasterRouter" or layer == "routing_service":
        content = {
            "ok": True,
            "governor": "Ceraxia",
            "kind": "code",
            "requires_decomposition": False,
            "supporting_governors": [],
            "reason": "native code gateway self-test",
        }
    elif owner == "WarmasterCommander" or layer == "command":
        content = {
            "commander_intent": "Delegate one bounded code mission through Ceraxia.",
            "primary_goal": str(request.get("message") or "Complete the code task."),
            "success_conditions": ["The requested behavior passes executable verification."],
            "constraints": ["Preserve existing user changes."],
            "escalate_to_user_if": ["A product decision is required."],
        }
    elif owner == "WarmasterAcceptance" or layer == "acceptance":
        content = {
            "accepted": True,
            "reason": "native gateway self-test acceptance",
            "required_revision": {},
            "escalate_to_user": False,
        }
    else:
        content = {"status": "ok", "owner": owner, "layer": layer}
    return {
        **model_contract(owner, role, layer=layer),
        "ok": True,
        "status": "answered",
        "elapsed_ms": 1,
        "content": json.dumps(content, ensure_ascii=False),
        "finish_reason": "stop",
        "error": "",
    }


def ceraxia_model_answer() -> dict:
    return {
        "ok": True,
        "status": "answered",
        "content": json.dumps(
            {
                "decision": "delegate",
                "mission_intent": "Deliver the requested behavior without scope drift.",
                "priorities": ["Correct behavior", "Honest verification"],
                "constraints": ["Preserve existing user changes."],
                "success_conditions": ["The requested behavior passes executable verification."],
                "tradeoffs": ["Prefer a bounded change over a broad refactor."],
                "escalation_conditions": ["A product decision is required."],
            },
            ensure_ascii=False,
        ),
    }


class TerminalSkitariiHandler(BaseHTTPRequestHandler):
    """Minimal HTTP warband double; all Warmaster bridge code remains real."""

    protocol_version = "HTTP/1.1"

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        if path == "/health":
            models = {
                role: {"model": f"terminal-smoke-{role}"}
                for role in ("planner", "reviewer", "spec", "fighter", "held_out")
            }
            self._reply(
                200,
                {
                    "ok": True,
                    "status": "ok",
                    "vm_alive": True,
                    "process_boundary_ready": True,
                    "identity": {
                        "source_sha256": str(getattr(self.server, "expected_source_sha256", "")),
                        "instance_id": "gateway-terminal-smoke",
                        "held_out_required": True,
                        "models": models,
                    },
                },
            )
            return
        if path.startswith("/missions/"):
            mission_id = path.removeprefix("/missions/")
            missions = getattr(self.server, "missions", {})
            mission = missions.get(mission_id)
            if not isinstance(mission, dict):
                self._reply(404, {"ok": False, "status": "missing", "mission_id": mission_id})
                return
            self._reply(
                200,
                {
                    "ok": True,
                    "mission_id": mission_id,
                    "request_sha256": mission["request_sha256"],
                    "status": "done",
                    "inflight": False,
                    "cleanup_complete": True,
                    "result": {
                        "accepted": True,
                        "needs_user": False,
                        "status": "done",
                        "summary": "Terminal service-boundary smoke completed.",
                        "artifacts": [],
                        "rounds": [],
                        "files": {"terminal-smoke.txt": "terminal-smoke-ok\n"},
                        "checks": [{"name": "terminal-smoke", "ok": True}],
                        "held_out_required": True,
                        "held_out_check_count": 1,
                        "held_out_status": "passed",
                        "held_out_acceptance": {"accepted": True},
                        "patch_bundle": {"apply_gate": "accepted"},
                    },
                },
            )
            return
        self._reply(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        if path != "/missions":
            self._reply(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        mission_id = str(payload.get("task_id") or "")
        request_sha256 = skitarii_bridge._service_request_sha256(payload)
        missions = getattr(self.server, "missions")
        requests = getattr(self.server, "mission_requests")
        missions[mission_id] = {
            "request_sha256": request_sha256,
            "payload": payload,
        }
        requests.append(payload)
        self._reply(
            202,
            {
                "ok": True,
                "mission_id": mission_id,
                "request_sha256": request_sha256,
                "status": "queued",
            },
        )

    def log_message(self, _format: str, *_args: object) -> None:
        return


def request_json(
    url: str,
    payload: dict | None = None,
    *,
    expected_status: int = 200,
) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = int(response.status)
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        body = json.loads(exc.read().decode("utf-8"))
    if status != expected_status:
        raise AssertionError(f"{url} returned HTTP {status}, expected {expected_status}: {body}")
    if not isinstance(body, dict):
        raise AssertionError(f"{url} returned a non-object JSON payload: {body!r}")
    return body


def assert_native_shape(payload: dict, run_dir: Path) -> None:
    contract = payload.get("contract") if isinstance(payload.get("contract"), dict) else {}
    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    execution = contract.get("execution") if isinstance(contract.get("execution"), dict) else {}
    steps = status.get("steps") if isinstance(status.get("steps"), list) else []
    expected_execution = {
        "kind": "skitarii_mission",
        "step_id": "skitarii",
        "backend": "SkitariiWarband",
    }
    if (
        payload.get("ok") is not True
        or payload.get("governor") != "Ceraxia"
        or contract.get("assigned_governor") != "Ceraxia"
        or execution != expected_execution
        or "worker_plan" in contract
        or status.get("governor") != "Ceraxia"
        or status.get("step_count") != 1
        or len(steps) != 1
        or steps[0].get("step_id") != "skitarii"
        or steps[0].get("backend") != "SkitariiWarband"
        or steps[0].get("worker") != "SkitariiWarband"
    ):
        raise AssertionError(f"gateway did not expose the native single-warband shape: {payload}")
    if (run_dir / "dispatch").exists():
        raise AssertionError("native Ceraxia run recreated the retired dispatch directory")
    if not is_native_code_run(run_dir):
        raise AssertionError(f"gateway run is not recognized as native: {run_dir}")
    errors = validate_native_code_run_package(run_dir)
    if errors:
        raise AssertionError(f"gateway persisted an invalid native package: {errors}")


def main() -> int:
    warmaster_gateway.request_model_decision = fake_model_decision
    local_executor.request_model_decision = fake_model_decision
    mission_control.request_model_decision = fake_model_decision
    routing.request_model_decision = fake_model_decision

    # The production path resolves this correctly from EyeOfTerror/Warmaster.
    # This assignment also supports the flattened local review snapshot.
    brigade.REPO_ROOT = PROJECT_ROOT

    with tempfile.TemporaryDirectory() as raw_root:
        run_root = Path(raw_root) / "runs"
        mission_warmaster_root = Path(raw_root) / "warmaster"
        skitarii_server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            TerminalSkitariiHandler,
        )
        skitarii_server.missions = {}  # type: ignore[attr-defined]
        skitarii_server.mission_requests = []  # type: ignore[attr-defined]
        skitarii_base = f"http://127.0.0.1:{skitarii_server.server_port}"
        ceraxia_server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            ceraxia_service.make_handler(run_root),
        )
        gateway_server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_handler(run_root),
        )
        skitarii_thread = threading.Thread(target=skitarii_server.serve_forever, daemon=True)
        ceraxia_thread = threading.Thread(target=ceraxia_server.serve_forever, daemon=True)
        gateway_thread = threading.Thread(target=gateway_server.serve_forever, daemon=True)
        original_governor_by_name = task_prepare.governor_by_name

        def gateway_test_governor(name: str):
            if name == "Ceraxia":
                return SimpleNamespace(
                    name="Ceraxia",
                    port=ceraxia_server.server_port,
                    active=lambda: True,
                )
            return original_governor_by_name(name)

        def temporary_open_mission(
            _warmaster_root: Path,
            message: str,
            task_id: str | None,
            source_channel: str = "main_chat",
        ) -> dict:
            return mission_control.open_mission(
                mission_warmaster_root,
                message,
                task_id,
                source_channel=source_channel,
            )

        with (
            mock.patch.dict(
                os.environ,
                {
                    "SHUSHUNYA_REPO_ROOT": str(PROJECT_ROOT),
                    "SKITARII_URL": skitarii_base,
                    "SKITARII_WARMMASTER_ARTIFACT_ONLY": "1",
                    "SKITARII_AUTOAPPLY": "0",
                },
                clear=False,
            ),
            mock.patch.object(skitarii_bridge, "SKITARII_URL", skitarii_base),
            mock.patch.object(skitarii_bridge, "SKITARII_POLL_INTERVAL_SEC", 0.01),
            mock.patch.object(
                ceraxia_service,
                "request_model_decision",
                return_value=ceraxia_model_answer(),
            ) as ceraxia_brain,
            mock.patch.object(
                task_prepare,
                "governor_by_name",
                side_effect=gateway_test_governor,
            ),
            mock.patch.object(orchestrator, "open_mission", side_effect=temporary_open_mission),
        ):
            skitarii_server.expected_source_sha256 = (  # type: ignore[attr-defined]
                ceraxia_service.expected_skitarii_source_sha256()
            )
            if not skitarii_server.expected_source_sha256:  # type: ignore[attr-defined]
                raise AssertionError("test fixture could not calculate the Skitarii source identity")
            skitarii_thread.start()
            ceraxia_thread.start()
            gateway_thread.start()
            try:
                gateway_base = f"http://127.0.0.1:{gateway_server.server_port}"
                health = request_json(gateway_base + "/health")
                if health.get("gateway") != "WarmasterGateway" or health.get("display_name") != "Abaddon":
                    raise AssertionError(f"bad gateway identity: {health}")

                plan = request_json(gateway_base + "/brigade_plan")
                warbands = plan.get("warbands") if isinstance(plan.get("warbands"), list) else []
                if (
                    plan.get("ports", {}).get("warbands") != {"SkitariiWarband": 7200}
                    or len(warbands) != 1
                    or warbands[0].get("name") != "SkitariiWarband"
                    or warbands[0].get("lifecycle") != "externally_managed"
                    or warbands[0].get("supervisor") != "skitarii-warband.service"
                ):
                    raise AssertionError(f"gateway brigade plan lost the native warband lifecycle: {plan}")
                worker_names = {
                    str(item.get("name") or "")
                    for item in plan.get("mechanicum_workers", [])
                    if isinstance(item, dict)
                }
                worker_ports = {
                    int(item.get("port") or 0)
                    for item in plan.get("mechanicum_workers", [])
                    if isinstance(item, dict)
                }
                if RETIRED_CODE_WORKERS & worker_names or RETIRED_CODE_PORTS & worker_ports:
                    raise AssertionError(f"retired code workers leaked into the brigade plan: {plan}")

                local_block = request_json(
                    gateway_base + "/orchestrate",
                    {
                        "message": "fix the python application",
                        "task_id": "native-code-local-block",
                    },
                    expected_status=409,
                )
                local_preflight = (
                    local_block.get("prepare", {}).get("task_preflight", {})
                    if isinstance(local_block.get("prepare"), dict)
                    else {}
                )
                if local_preflight.get("error_code") != "ceraxia_leader_service_required":
                    raise AssertionError(f"local code planning bypassed live Ceraxia: {local_block}")

                preflight = request_json(
                    gateway_base + "/task_preflight",
                    {
                        "message": "fix the python application",
                        "task_id": "native-code-preflight",
                        "governor_transport": "http",
                    },
                )
                preflight_contract = (
                    preflight.get("contract")
                    if isinstance(preflight.get("contract"), dict)
                    else preflight.get("contract_summary", {})
                )
                preflight_execution = (
                    preflight_contract.get("execution")
                    if isinstance(preflight_contract, dict)
                    else {}
                )
                if (
                    preflight.get("ok") is not True
                    or preflight.get("governor") != "Ceraxia"
                    or preflight_execution.get("backend") != "SkitariiWarband"
                    or "worker_plan" in preflight_contract
                    or (run_root / "native-code-preflight").exists()
                    or ceraxia_brain.call_count != 0
                ):
                    raise AssertionError(f"native structural preflight drifted: {preflight}")

                prepared = request_json(
                    gateway_base + "/orchestrate",
                    {
                        "message": "fix the python application",
                        "task_id": "native-code-gateway",
                        "governor_transport": "http",
                    },
                )
                run_dir = Path(str(prepared.get("run_dir") or ""))
                prepared_task = (
                    prepared.get("prepare", {}).get("task", {})
                    if isinstance(prepared.get("prepare"), dict)
                    else {}
                )
                assert_native_shape(prepared_task, run_dir)
                if ceraxia_brain.call_count != 1:
                    raise AssertionError("one gateway prepare must make exactly one Ceraxia leader decision")
                directive = prepared_task.get("leadership_directive", {})
                if directive.get("delegated_to") != "SkitariiWarband":
                    raise AssertionError(f"gateway lost the Ceraxia leadership directive: {prepared}")
                backend = orchestrator.execution_backend_route(run_dir)
                if (
                    backend.get("ok") is not True
                    or backend.get("backend") != "SkitariiWarband"
                    or backend.get("execution", {}).get("kind") != "skitarii_mission"
                ):
                    raise AssertionError(f"prepared native run did not enter the central backend router: {backend}")

                serialized = json.dumps(prepared, ensure_ascii=False)
                if any(name in serialized for name in RETIRED_CODE_WORKERS):
                    raise AssertionError(f"phantom code worker leaked into gateway response: {prepared}")

                race_task_id = "native-code-reservation-race"
                race_messages = (
                    "Create standalone race-alpha.txt with exact content alpha.",
                    "Create standalone race-beta.txt with exact content beta.",
                )
                race_barrier = threading.Barrier(3)
                race_results: list[dict] = []
                race_errors: list[BaseException] = []
                decisions_before_race = ceraxia_brain.call_count

                def submit_race(message: str) -> None:
                    try:
                        race_barrier.wait(timeout=10)
                        race_results.append(
                            request_json(
                                gateway_base + "/orchestrate_run",
                                {
                                    "message": message,
                                    "task_id": race_task_id,
                                    "governor_transport": "http",
                                    "run_mode": "http",
                                    "auto_start": False,
                                    "reuse_existing": True,
                                },
                            )
                        )
                    except BaseException as exc:  # noqa: BLE001 - thread reports into the test.
                        race_errors.append(exc)

                race_threads = [
                    threading.Thread(target=submit_race, args=(message,), daemon=True)
                    for message in race_messages
                ]
                for race_thread in race_threads:
                    race_thread.start()
                race_barrier.wait(timeout=10)
                for race_thread in race_threads:
                    race_thread.join(timeout=30)
                if race_errors or any(race_thread.is_alive() for race_thread in race_threads):
                    raise AssertionError(f"concurrent reservation smoke failed: {race_errors}")
                race_run = run_root / race_task_id
                race_mission = mission_warmaster_root / "missions" / f"mission-{race_task_id}"
                race_order = json.loads(
                    (race_mission / "commander_order.json").read_text(encoding="utf-8")
                )
                race_contract = json.loads(
                    (race_run / "contract.json").read_text(encoding="utf-8")
                )
                winner = str(race_order.get("primary_goal") or "")
                loser = race_messages[1] if winner == race_messages[0] else race_messages[0]
                coherent_bytes = b"\n".join(
                    path.read_bytes()
                    for path in sorted(race_mission.rglob("*"))
                    if path.is_file()
                ) + (race_run / "contract.json").read_bytes()
                if (
                    len(race_results) != 2
                    or {item.get("phase") for item in race_results}
                    != {"ready_to_start", "existing_run"}
                    or winner not in race_messages
                    or winner not in str(race_contract.get("goal") or "")
                    or loser.encode("utf-8") in coherent_bytes
                    or ceraxia_brain.call_count != decisions_before_race + 1
                    or not (race_run / "mission_ref.json").is_file()
                ):
                    raise AssertionError(
                        "per-task reservation produced a mixed mission trail: "
                        f"results={race_results}, order={race_order}, contract={race_contract}"
                    )

                long_prefix = "long-" + ("x" * 115)
                long_task_ids = (long_prefix + "a", long_prefix + "b")
                long_messages = (
                    "Create standalone long-alpha.txt with exact content alpha.",
                    "Create standalone long-beta.txt with exact content beta.",
                )
                long_barrier = threading.Barrier(3)
                long_results: list[dict] = []
                long_errors: list[BaseException] = []
                decisions_before_long_race = ceraxia_brain.call_count

                def submit_long_race(task_and_message: tuple[str, str]) -> None:
                    long_task_id, long_message = task_and_message
                    try:
                        long_barrier.wait(timeout=10)
                        long_results.append(
                            request_json(
                                gateway_base + "/orchestrate_run",
                                {
                                    "message": long_message,
                                    "task_id": long_task_id,
                                    "governor_transport": "http",
                                    "run_mode": "http",
                                    "auto_start": False,
                                    "reuse_existing": True,
                                },
                            )
                        )
                    except BaseException as exc:  # noqa: BLE001 - thread reports into the test.
                        long_errors.append(exc)

                long_threads = [
                    threading.Thread(
                        target=submit_long_race,
                        args=(pair,),
                        daemon=True,
                    )
                    for pair in zip(long_task_ids, long_messages, strict=True)
                ]
                for long_thread in long_threads:
                    long_thread.start()
                long_barrier.wait(timeout=10)
                for long_thread in long_threads:
                    long_thread.join(timeout=30)
                long_mission_ids = [
                    mission_control.mission_id_for(task, message)
                    for task, message in zip(long_task_ids, long_messages, strict=True)
                ]
                if (
                    long_errors
                    or any(long_thread.is_alive() for long_thread in long_threads)
                    or len(long_results) != 2
                    or {item.get("phase") for item in long_results} != {"ready_to_start"}
                    or len(set(long_mission_ids)) != 2
                    or any(len(mission_id) > 128 for mission_id in long_mission_ids)
                    or ceraxia_brain.call_count != decisions_before_long_race + 2
                ):
                    raise AssertionError(
                        "long task ids collided before native preparation: "
                        f"results={long_results}, errors={long_errors}, missions={long_mission_ids}"
                    )
                for long_task_id, long_message, long_mission_id in zip(
                    long_task_ids,
                    long_messages,
                    long_mission_ids,
                    strict=True,
                ):
                    long_run = run_root / long_task_id
                    long_mission = mission_warmaster_root / "missions" / long_mission_id
                    long_order = json.loads(
                        (long_mission / "commander_order.json").read_text(encoding="utf-8")
                    )
                    long_contract = json.loads(
                        (long_run / "contract.json").read_text(encoding="utf-8")
                    )
                    long_ref = json.loads(
                        (long_run / "mission_ref.json").read_text(encoding="utf-8")
                    )
                    if (
                        long_order.get("primary_goal") != long_message
                        or long_contract.get("mission_id") != long_mission_id
                        or long_ref.get("mission_id") != long_mission_id
                        or not long_mission.is_dir()
                    ):
                        raise AssertionError(
                            "hashed long mission identity mixed protocol trails: "
                            f"order={long_order}, contract={long_contract}, ref={long_ref}"
                        )

                auto_messages = (
                    "Create a standalone artifact for collision smoke alpha-one.txt.",
                    "Create a standalone artifact for collision smoke beta-two.txt.",
                )
                auto_barrier = threading.Barrier(3)
                auto_results: list[dict] = []
                auto_errors: list[BaseException] = []
                decisions_before_auto_race = ceraxia_brain.call_count

                def submit_auto_race(auto_message: str) -> None:
                    try:
                        auto_barrier.wait(timeout=10)
                        auto_results.append(
                            request_json(
                                gateway_base + "/orchestrate_run",
                                {
                                    "message": auto_message,
                                    "governor_transport": "http",
                                    "run_mode": "http",
                                    "auto_start": False,
                                    "reuse_existing": True,
                                },
                            )
                        )
                    except BaseException as exc:  # noqa: BLE001 - thread reports into the test.
                        auto_errors.append(exc)

                auto_threads = [
                    threading.Thread(target=submit_auto_race, args=(message,), daemon=True)
                    for message in auto_messages
                ]
                for auto_thread in auto_threads:
                    auto_thread.start()
                auto_barrier.wait(timeout=10)
                for auto_thread in auto_threads:
                    auto_thread.join(timeout=30)
                expected_auto_ids = {
                    mission_control.task_id_for_message(message): message
                    for message in auto_messages
                }
                observed_auto_ids = {
                    str(result.get("task_id") or "")
                    for result in auto_results
                }
                if (
                    auto_errors
                    or any(auto_thread.is_alive() for auto_thread in auto_threads)
                    or len(auto_results) != 2
                    or {item.get("phase") for item in auto_results} != {"ready_to_start"}
                    or observed_auto_ids != set(expected_auto_ids)
                    or ceraxia_brain.call_count != decisions_before_auto_race + 2
                ):
                    raise AssertionError(
                        "implicit task identity collision was not isolated: "
                        f"results={auto_results}, errors={auto_errors}, expected={expected_auto_ids}"
                    )
                for auto_task_id, auto_message in expected_auto_ids.items():
                    auto_mission_id = mission_control.mission_id_for(auto_task_id, auto_message)
                    auto_run = run_root / auto_task_id
                    auto_mission = mission_warmaster_root / "missions" / auto_mission_id
                    auto_order = json.loads(
                        (auto_mission / "commander_order.json").read_text(encoding="utf-8")
                    )
                    auto_contract = json.loads(
                        (auto_run / "contract.json").read_text(encoding="utf-8")
                    )
                    if (
                        auto_order.get("primary_goal") != auto_message
                        or auto_contract.get("task_id") != auto_task_id
                        or auto_contract.get("mission_id") != auto_mission_id
                        or not (auto_run / "mission_ref.json").is_file()
                    ):
                        raise AssertionError(
                            "implicit task id produced an orphan or mixed mission: "
                            f"order={auto_order}, contract={auto_contract}"
                        )

                terminal_task_id = "native-code-terminal-smoke"
                submitted = request_json(
                    gateway_base + "/orchestrate_run",
                    {
                        "message": (
                            "Create a new standalone artifact terminal-smoke.txt with exact "
                            "content terminal-smoke-ok and verify it."
                        ),
                        "task_id": terminal_task_id,
                        "governor_transport": "http",
                        "run_mode": "http",
                        "auto_start": True,
                        "reuse_existing": False,
                        "timeout_sec": 30,
                    },
                    expected_status=202,
                )
                if submitted.get("phase") != "started":
                    raise AssertionError(f"terminal native smoke did not start: {submitted}")

                deadline = time.monotonic() + 15
                terminal_state: dict = {}
                while time.monotonic() < deadline:
                    terminal_state = request_json(
                        gateway_base + f"/runs/{terminal_task_id}/orchestration"
                    )
                    mission_state = (
                        terminal_state.get("mission_state")
                        if isinstance(terminal_state.get("mission_state"), dict)
                        else {}
                    )
                    if (
                        mission_state.get("status") == "completed"
                        and terminal_state.get("status") == "completed"
                    ):
                        break
                    time.sleep(0.02)
                else:
                    raise AssertionError(f"terminal native smoke never completed: {terminal_state}")

                terminal_run = run_root / terminal_task_id
                terminal_mission = mission_warmaster_root / "missions" / f"mission-{terminal_task_id}"
                terminal_contract = json.loads(
                    (terminal_run / "contract.json").read_text(encoding="utf-8")
                )
                terminal_final = json.loads(
                    (terminal_mission / "final_response.json").read_text(encoding="utf-8")
                )
                terminal_ledger = json.loads(
                    (terminal_run / "task_ledger.json").read_text(encoding="utf-8")
                )
                mission_requests = skitarii_server.mission_requests  # type: ignore[attr-defined]
                if (
                    terminal_ledger.get("status") != "completed"
                    or terminal_final.get("status") != "completed"
                    or (terminal_run / "work" / "code" / "terminal-smoke.txt").read_text(
                        encoding="utf-8"
                    ) != "terminal-smoke-ok\n"
                    or terminal_contract.get("execution", {}).get("backend") != "SkitariiWarband"
                    or "worker_plan" in terminal_contract
                    or (terminal_run / "dispatch").exists()
                    or len(mission_requests) != 1
                    or mission_requests[0].get("leadership_directive", {}).get("leader") != "Ceraxia"
                ):
                    raise AssertionError(
                        "terminal service-boundary smoke lost a native invariant: "
                        f"state={terminal_state}, final={terminal_final}, ledger={terminal_ledger}"
                    )

                immutable_paths = sorted(
                    path
                    for path in terminal_mission.rglob("*")
                    if path.is_file()
                ) + [terminal_run / "task_ledger.json", terminal_run / "mission_ref.json"]
                immutable_before = {
                    str(path): path.read_bytes()
                    for path in immutable_paths
                }
                rejected_rerun = request_json(
                    gateway_base + "/orchestrate_run",
                    {
                        "message": "Try to overwrite the completed native mission in place.",
                        "task_id": terminal_task_id,
                        "governor_transport": "http",
                        "run_mode": "http",
                        "auto_start": True,
                        "reuse_existing": True,
                        "force": True,
                    },
                    expected_status=409,
                )
                immutable_after = {
                    str(path): path.read_bytes()
                    for path in immutable_paths
                }
                if (
                    rejected_rerun.get("ok") is not False
                    or rejected_rerun.get("start", {}).get("error_code")
                    != "native_preflight_failed"
                    or immutable_after != immutable_before
                    or len(skitarii_server.mission_requests) != 1  # type: ignore[attr-defined]
                ):
                    raise AssertionError(
                        "reusing a terminal task id mutated its immutable protocol trail: "
                        f"response={rejected_rerun}"
                    )
            finally:
                gateway_server.shutdown()
                ceraxia_server.shutdown()
                skitarii_server.shutdown()
                gateway_thread.join(timeout=30)
                ceraxia_thread.join(timeout=30)
                skitarii_thread.join(timeout=30)

    print("[ok] Warmaster gateway native Ceraxia -> Skitarii route")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
