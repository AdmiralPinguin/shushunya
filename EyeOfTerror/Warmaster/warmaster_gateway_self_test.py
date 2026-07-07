#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import eye_of_terror.brigade as brigade
import eye_of_terror.mission_control as mission_control
import eye_of_terror.routing as routing
import eye_of_terror.local_executor as local_executor
import eye_of_terror.task_prepare as task_prepare
import eye_of_terror.warmaster_gateway as warmaster_gateway
from EyeOfTerror.model_brain import model_contract
from eye_of_terror.inner_circle.iskandar_service import make_handler as make_iskandar_handler
from eye_of_terror.warmaster_gateway import brigade_readiness_summary, cancel_http_worker_tasks, compact_brigade_readiness, make_handler, parse_limit, parse_nonnegative_int, prepare_run_root, requested_step_ids_from_payload, resolve_run_child_path, resume_step_ids_from_run, revision_step_ids_from_run, valid_task_id, validate_service_host
from eye_of_terror.ledger import TaskLedger
from eye_of_terror.inner_circle.iskandar import plan_lore_reconstruction
from eye_of_terror.pipeline import write_pipeline_run


LOCAL_EXEC_TIMEOUT_SEC = 600


def fake_model_decision(owner: str, role: str, request: dict, *, layer: str = "worker", instructions: str = "") -> dict:
    if owner == "WarmasterRouter" or layer == "routing_service":
        message = str(request.get("message") or "").lower()
        governor = "IskandarKhayon"
        kind = "research"
        if any(term in message for term in ("код", "python", "repository", "приложени", "почини")) or re.search(r"\brepo\b", message):
            governor = "Ceraxia"
            kind = "code"
        if any(term in message for term in ("stable diffusion", "рисовал", "изображ", "картин", "комикс", "панел", "серии", "серию")):
            governor = "Moriana"
            kind = "image_generation"
        if any(term in message for term in ("комикс", "панел")):
            kind = "comic_generation"
        if any(term in message for term in ("серии", "серию", "series")):
            kind = "image_series_generation"
        content = {
            "ok": True,
            "governor": governor,
            "kind": kind,
            "requires_decomposition": False,
            "supporting_governors": [],
            "reason": "self-test model route",
        }
    elif owner == "WarmasterCommander" or layer == "command":
        content = {
            "commander_intent": "Frame the request as a mission for the selected governor.",
            "primary_goal": "Complete the user's requested task and verify the result before final delivery.",
            "success_conditions": [
                "the assigned governor produces a structured report",
                "internal revisions are not shown to the user as final answers",
            ],
            "constraints": ["use the common mission protocol"],
            "escalate_to_user_if": ["a real user choice or unavailable external access blocks completion"],
        }
    elif owner == "WarmasterAcceptance" or layer == "acceptance":
        content = {
            "accepted": True,
            "reason": "self-test acceptance passed",
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


def install_fast_model_brain() -> None:
    warmaster_gateway.request_model_decision = fake_model_decision
    local_executor.request_model_decision = fake_model_decision
    mission_control.request_model_decision = fake_model_decision
    routing.request_model_decision = fake_model_decision


def write_gateway_test_corpus(corpus_root: Path) -> None:
    entries = [
        ("kharn_eater_of_worlds.txt", "Kharn: Eater of Worlds", ["Skalathrax", "Kharn", "World Eaters"]),
        ("lucius_faultless_blade.txt", "Lucius: The Faultless Blade", ["Skalathrax", "Lucius", "Emperor's Children"]),
        ("weakness_of_others.txt", "The Weakness of Others", ["Skalathrax", "World Eaters", "aftermath"]),
    ]
    for filename, title, tags in entries:
        path = corpus_root / filename
        text = (
            f"{title}\n"
            "Skalathrax Kharn World Eaters Emperor's Children battle cold night shelters "
            "direct event evidence primary narrative chronology. "
        ) * 80
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        metadata = {
            "title": title,
            "language": "en",
            "source_class": "official_primary_narrative",
            "source_type": "book",
            "type": "book",
            "reliability": "user-provided-test-primary",
            "tags": tags,
            "aliases": tags,
            "expected_use": "gateway self-test local primary text for comprehensive event reconstruction",
        }
        write_json(path.with_suffix(path.suffix + ".json"), metadata)


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def patch_dispatch_ports(run_dir: Path, port: int) -> None:
    for dispatch_path in sorted((run_dir / "dispatch").glob("*.json")):
        packet = json.loads(dispatch_path.read_text(encoding="utf-8"))
        if isinstance(packet, dict):
            packet["port"] = port
            write_json(dispatch_path, packet)


def request_json(url: str, payload: dict | None = None, timeout: int = 120, allow_legacy_task: bool = True) -> dict:
    request_payload = dict(payload) if isinstance(payload, dict) else payload
    if allow_legacy_task and isinstance(request_payload, dict) and url.rstrip("/").endswith("/task"):
        request_payload.setdefault("allow_legacy_direct_task", True)
    data = None if request_payload is None else json.dumps(request_payload).encode("utf-8")
    method = "POST" if data else "GET"
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def request_options(url: str) -> int:
    req = urllib.request.Request(url, method="OPTIONS")
    with urllib.request.urlopen(req, timeout=120) as response:
        return response.status


def make_cancel_handler(calls: list[str]) -> type[BaseHTTPRequestHandler]:
    class CancelHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            calls.append(self.path)
            body = {"ok": True, "task": {"status": "cancelled"}}
            data = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return CancelHandler


def make_bad_prepare_handler(run_root: Path) -> type[BaseHTTPRequestHandler]:
    class BadPrepareHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path == "/capabilities":
                body = {"ok": True, "required_workers": ["Lexmechanic", "FabricatorFinalis"]}
            else:
                body = {"ok": True, "governor": "IskandarKhayon"}
            data = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8"))
            plan = plan_lore_reconstruction(str(payload.get("task") or ""), task_id=str(payload.get("task_id") or "") or None)
            if self.path == "/plan":
                body = plan.to_dict()
            elif self.path == "/prepare_run":
                run_dir = Path(str(payload.get("run_dir") or run_root / plan.contract.task_id))
                status = write_pipeline_run(plan.contract, run_dir, oversight=plan.to_dict()["oversight"])
                if "bad-dispatch" in str(payload.get("task_id") or ""):
                    (run_dir / "dispatch" / "source_discovery.json").write_text("{", encoding="utf-8")
                elif "bad-worker" in str(payload.get("task_id") or ""):
                    dispatch_path = run_dir / "dispatch" / "source_discovery.json"
                    packet = json.loads(dispatch_path.read_text(encoding="utf-8"))
                    packet["worker"] = "Chronologis"
                    write_json(dispatch_path, packet)
                else:
                    (run_dir / "oversight.json").unlink()
                body = {"ok": True, "status": status}
            else:
                body = {"ok": False, "error": "not found"}
            data = json.dumps(body).encode("utf-8")
            self.send_response(200 if body.get("ok") else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return BadPrepareHandler


def main() -> int:
    install_fast_model_brain()
    if parse_limit("999999", default=20) != 200 or parse_limit("bad", default=20) != 20:
        raise AssertionError("limit parser did not clamp values")
    if parse_nonnegative_int("42", default=0) != 42 or parse_nonnegative_int("bad", default=7) != 7:
        raise AssertionError("nonnegative integer parser returned an unexpected value")
    if requested_step_ids_from_payload({"step_ids": [" source_discovery "]}) != ["source_discovery"]:
        raise AssertionError("step id parser did not normalize requested step ids")
    try:
        requested_step_ids_from_payload({"step_ids": ["source_discovery", "source_discovery"]})
    except ValueError:
        pass
    else:
        raise AssertionError("step id parser accepted duplicate requested step ids")
    if not valid_task_id("valid-task_1.2") or valid_task_id("../escape") or valid_task_id("x" * 129):
        raise AssertionError("task id validator accepted an unsafe value")
    if validate_service_host("localhost") != "localhost":
        raise AssertionError("loopback host validator rejected localhost")
    try:
        validate_service_host("example.com")
    except ValueError:
        pass
    else:
        raise AssertionError("service host validator accepted non-loopback host")
    readiness = brigade_readiness_summary(
        governors=[
            {"name": "IskandarKhayon", "status": "active", "runtime": {"reachable": False}},
            {"name": "Moriana", "status": "active", "runtime": {"reachable": False}},
        ],
        workers=[
            {"name": "Lexmechanic", "status": "prototype", "runtime": {"reachable": True}},
            {"name": "OcularisRenderium", "status": "planned", "runtime": {"reachable": False}},
        ],
        requirements=[
            {
                "governor": "IskandarKhayon",
                "satisfied": False,
                "missing_workers": ["MissingWorker"],
                "unavailable_workers": [{"name": "OcularisRenderium"}],
            }
        ],
    )
    if (
        readiness.get("ready")
        or readiness.get("blocker_count") != 4
        or readiness.get("warning_count") != 1
        or not any("IskandarKhayon" in blocker for blocker in readiness.get("blockers", []))
    ):
        raise AssertionError(f"bad brigade readiness summary: {readiness}")
    invalid_readiness = compact_brigade_readiness(host="example.com")
    if invalid_readiness.get("ready") or not invalid_readiness.get("error") or invalid_readiness.get("blocker_count") != 1:
        raise AssertionError(f"compact readiness should fail soft on invalid host: {invalid_readiness}")
    with tempfile.TemporaryDirectory() as temp_dir:
        run_root = Path(temp_dir) / "runs"
        corpus_root = Path(temp_dir) / "corpus"
        write_gateway_test_corpus(corpus_root)
        old_corpus_dir = os.environ.get("SHUSHUNYA_CORPUS_DIR")
        os.environ["SHUSHUNYA_CORPUS_DIR"] = str(corpus_root)
        try:
            resolve_run_child_path(run_root / "x", str(Path(temp_dir) / "escape"), "work")
        except ValueError:
            pass
        else:
            raise AssertionError("run child path resolver accepted path outside run_dir")
        invalid_transport = warmaster_gateway.prepare_task(
            "Исследуй Скалатракс и сделай report.",
            "invalid-transport-task",
            run_root,
            governor_transport="warp",
        )
        invalid_transport_body = invalid_transport.get("actions", {}).get("next_action", {}).get("body", {})
        if (
            invalid_transport.get("error_code") != "invalid_governor_transport"
            or invalid_transport_body.get("message") != "Исследуй Скалатракс и сделай report."
            or invalid_transport_body.get("task_id") != "invalid-transport-task"
            or invalid_transport_body.get("governor_transport") != "warp"
        ):
            raise AssertionError(f"invalid transport action did not preserve executable task body: {invalid_transport}")
        original_planner = task_prepare.plan_lore_reconstruction
        try:
            class BadContract:
                task_id = "bad-contract"

                def to_dict(self) -> dict:
                    return {
                        "version": 1,
                        "task_id": self.task_id,
                        "kind": "research",
                        "goal": "bad",
                        "assigned_governor": "IskandarKhayon",
                        "completion_criteria": ["done"],
                        "worker_plan": [],
                    }

            task_prepare.plan_lore_reconstruction = lambda _message, task_id=None: type("BadPlan", (), {"contract": BadContract()})()
            bad_contract = warmaster_gateway.prepare_task("Исследуй Скалатракс и сделай report.", "bad-contract", run_root)
            if (
                bad_contract.get("error_code") != "invalid_task_contract"
                or bad_contract.get("actions", {}).get("next_action", {}).get("kind") != "inspect_governor"
                or (run_root / "bad-contract").exists()
            ):
                raise AssertionError(f"Warmaster accepted an invalid task contract: {bad_contract}")
        finally:
            task_prepare.plan_lore_reconstruction = original_planner
        try:
            class MissingWorkerContract:
                task_id = "missing-worker-contract"
                goal = "missing worker"

                def to_dict(self) -> dict:
                    return {
                        "version": 1,
                        "task_id": self.task_id,
                        "kind": "research",
                        "goal": self.goal,
                        "assigned_governor": "IskandarKhayon",
                        "completion_criteria": ["done"],
                        "worker_plan": [
                            {
                                "step_id": "missing",
                                "worker": "MissingMechanicum",
                                "purpose": "prove worker plan preflight",
                            }
                        ],
                    }

            task_prepare.plan_lore_reconstruction = lambda _message, task_id=None: type("MissingWorkerPlan", (), {"contract": MissingWorkerContract()})()
            missing_worker_contract = warmaster_gateway.prepare_task(
                "Исследуй Скалатракс и сделай report.",
                "missing-worker-contract",
                run_root,
            )
            if (
                missing_worker_contract.get("error_code") != "contract_workers_missing"
                or missing_worker_contract.get("actions", {}).get("next_action", {}).get("kind") != "inspect_brigade"
                or (run_root / "missing-worker-contract").exists()
            ):
                raise AssertionError(f"Warmaster accepted a contract with a missing worker: {missing_worker_contract}")
        finally:
            task_prepare.plan_lore_reconstruction = original_planner
        try:
            class PlannedWorkerContract:
                task_id = "planned-worker-contract"
                goal = "planned worker"

                def to_dict(self) -> dict:
                    return {
                        "version": 1,
                        "task_id": self.task_id,
                        "kind": "research",
                        "goal": self.goal,
                        "assigned_governor": "IskandarKhayon",
                        "completion_criteria": ["done"],
                        "worker_plan": [
                            {
                                "step_id": "forge_render",
                                "worker": "ForgeRelay",
                                "purpose": "prove planned worker availability preflight",
                            }
                        ],
                    }

            class PlannedWorkerPlan:
                contract = PlannedWorkerContract()

                def to_dict(self) -> dict:
                    return {
                        "ok": True,
                        "contract": self.contract.to_dict(),
                        "validation": {"ok": True, "errors": []},
                        "oversight": {
                            "governor": "IskandarKhayon",
                            "requires_gap_disclosure": True,
                            "final_review": {"required_artifacts": [], "quality_gates": []},
                        },
                    }

            task_prepare.plan_lore_reconstruction = lambda _message, task_id=None: PlannedWorkerPlan()
            planned_worker_contract = warmaster_gateway.prepare_task(
                "Исследуй Скалатракс и сделай report.",
                "planned-worker-contract",
                run_root,
            )
            if (
                planned_worker_contract.get("error_code") != "contract_workers_unavailable"
                or not planned_worker_contract.get("unavailable_workers")
                or (run_root / "planned-worker-contract").exists()
            ):
                raise AssertionError(f"Warmaster accepted a contract with a planned worker: {planned_worker_contract}")
            planned_worker_preflight = warmaster_gateway.preflight_task(
                "Исследуй Скалатракс и сделай report.",
                "planned-worker-contract",
                run_root,
            )
            if planned_worker_preflight.get("error_code") != "contract_workers_unavailable" or not planned_worker_preflight.get("worker_availability", {}).get("unavailable_workers"):
                raise AssertionError(f"Warmaster preflight missed planned worker availability: {planned_worker_preflight}")
        finally:
            task_prepare.plan_lore_reconstruction = original_planner
        try:
            good_plan = original_planner("Исследуй Скалатракс и сделай report.", task_id="missing-oversight-contract")

            class MissingOversightPlan:
                contract = good_plan.contract

                def to_dict(self) -> dict:
                    return {"ok": True, "contract": self.contract.to_dict(), "validation": {"ok": True, "errors": []}}

            task_prepare.plan_lore_reconstruction = lambda _message, task_id=None: MissingOversightPlan()
            missing_oversight_contract = warmaster_gateway.prepare_task(
                "Исследуй Скалатракс и сделай report.",
                "missing-oversight-contract",
                run_root,
            )
            if missing_oversight_contract.get("error_code") != "invalid_oversight" or (run_root / "missing-oversight-contract").exists():
                raise AssertionError(f"Warmaster accepted a plan without oversight: {missing_oversight_contract}")
        finally:
            task_prepare.plan_lore_reconstruction = original_planner
        bad_dispatch = Path(temp_dir) / "bad-dispatch" / "dispatch"
        bad_dispatch.mkdir(parents=True, exist_ok=True)
        (bad_dispatch / "broken.json").write_text("{", encoding="utf-8")
        bad_cancel = cancel_http_worker_tasks(bad_dispatch.parent)
        if not bad_cancel or bad_cancel[0].get("ok"):
            raise AssertionError(f"bad dispatch cancel fan-out should report failure: {bad_cancel}")
        iskandar_server = ThreadingHTTPServer(("127.0.0.1", 0), make_iskandar_handler(run_root))
        iskandar_thread = threading.Thread(target=iskandar_server.serve_forever, daemon=True)
        iskandar_thread.start()
        try:
            class ServiceGovernor:
                name = "IskandarKhayon"
                port = iskandar_server.server_port
                status = "active"

                def active(self) -> bool:
                    return True

                def to_dict(self) -> dict:
                    return {
                        "name": self.name,
                        "status": self.status,
                        "port": self.port,
                        "task_kinds": ["research", "research_writing", "lore_reconstruction"],
                        "route_terms": ["скалатракс"],
                        "service": "eye_of_terror.inner_circle.iskandar_service",
                    }

            service_prepared = warmaster_gateway.prepare_task_via_governor_service(
                "Исследуй Скалатракс и сделай report.",
                "warmaster-governor-http-test",
                run_root,
                ServiceGovernor(),
            )
            if (
                not service_prepared.get("ok")
                or service_prepared.get("governor_transport") != "http"
                or not (Path(service_prepared["run_dir"]) / "dispatch" / "source_discovery.json").exists()
                or not (Path(service_prepared["run_dir"]) / "task_ledger.json").exists()
                or service_prepared.get("actions", {}).get("next_action", {}).get("kind") != "preflight_run"
            ):
                raise AssertionError(f"bad http governor preparation: {service_prepared}")
            bad_prepare_server = ThreadingHTTPServer(("127.0.0.1", 0), make_bad_prepare_handler(run_root))
            bad_prepare_thread = threading.Thread(target=bad_prepare_server.serve_forever, daemon=True)
            bad_prepare_thread.start()
            try:
                class BadPrepareGovernor(ServiceGovernor):
                    port = bad_prepare_server.server_port

                bad_prepared = warmaster_gateway.prepare_task_via_governor_service(
                    "Исследуй Скалатракс и сделай report.",
                    "warmaster-governor-bad-prepare-test",
                    run_root,
                    BadPrepareGovernor(),
                )
                if (
                    bad_prepared.get("error_code") != "governor_prepare_invalid_run"
                    or not bad_prepared.get("cleanup", {}).get("removed")
                    or (run_root / "warmaster-governor-bad-prepare-test").exists()
                ):
                    raise AssertionError(f"Warmaster accepted invalid governor-prepared run package: {bad_prepared}")
                bad_dispatch_prepared = warmaster_gateway.prepare_task_via_governor_service(
                    "Исследуй Скалатракс и сделай report.",
                    "warmaster-governor-bad-dispatch-test",
                    run_root,
                    BadPrepareGovernor(),
                )
                if (
                    bad_dispatch_prepared.get("error_code") != "governor_prepare_invalid_run"
                    or not any("source_discovery.json" in error for error in bad_dispatch_prepared.get("validation", {}).get("errors", []))
                    or not bad_dispatch_prepared.get("cleanup", {}).get("removed")
                    or (run_root / "warmaster-governor-bad-dispatch-test").exists()
                ):
                    raise AssertionError(f"Warmaster accepted invalid governor-prepared dispatch: {bad_dispatch_prepared}")
                bad_worker_prepared = warmaster_gateway.prepare_task_via_governor_service(
                    "Исследуй Скалатракс и сделай report.",
                    "warmaster-governor-bad-worker-test",
                    run_root,
                    BadPrepareGovernor(),
                )
                if (
                    bad_worker_prepared.get("error_code") != "governor_prepare_invalid_run"
                    or not any("dispatch worker mismatch" in error for error in bad_worker_prepared.get("validation", {}).get("errors", []))
                    or not bad_worker_prepared.get("cleanup", {}).get("removed")
                    or (run_root / "warmaster-governor-bad-worker-test").exists()
                ):
                    raise AssertionError(f"Warmaster accepted mismatched governor-prepared dispatch worker: {bad_worker_prepared}")
            finally:
                bad_prepare_server.shutdown()
                bad_prepare_thread.join(timeout=120)
            original_worker_refs = brigade.worker_refs
            brigade.worker_refs = lambda: []
            try:
                missing_workers = warmaster_gateway.prepare_task_via_governor_service(
                    "Исследуй Скалатракс и сделай report.",
                    "warmaster-governor-missing-workers-test",
                    run_root,
                    ServiceGovernor(),
                )
                if (
                    missing_workers.get("error_code") != "governor_workers_missing"
                    or "Lexmechanic" not in missing_workers.get("missing_workers", [])
                    or missing_workers.get("actions", {}).get("next_action", {}).get("kind") != "inspect_brigade"
                ):
                    raise AssertionError(f"missing governor workers were not rejected: {missing_workers}")
            finally:
                brigade.worker_refs = original_worker_refs
            original_governor_by_name = task_prepare.governor_by_name
            task_prepare.governor_by_name = lambda _name: ServiceGovernor()
            gateway_server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                make_handler(run_root, default_governor_transport="http"),
            )
            gateway_thread = threading.Thread(target=gateway_server.serve_forever, daemon=True)
            gateway_thread.start()
            try:
                gateway_base = f"http://127.0.0.1:{gateway_server.server_port}"
                service_task = request_json(
                    gateway_base + "/task",
                    {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-default-http-governor-test"},
                )
                if service_task.get("governor_transport") != "http" or not service_task.get("ok"):
                    raise AssertionError(f"gateway did not use default http governor transport: {service_task}")
                service_preflight = request_json(
                    gateway_base + "/task_preflight",
                    {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-default-http-preflight-test"},
                )
                service_preflight_body = service_preflight.get("actions", {}).get("next_action", {}).get("body", {})
                if (
                    not service_preflight.get("ok")
                    or service_preflight.get("governor_transport") != "http"
                    or service_preflight.get("governor_plan_actions", {}).get("next_action", {}).get("kind") != "prepare_run"
                    or service_preflight_body.get("message") != "Исследуй Скалатракс и сделай report."
                    or service_preflight_body.get("governor_transport") != "http"
                    or service_preflight_body.get("task_id") != "warmaster-default-http-preflight-test"
                ):
                    raise AssertionError(f"http governor task preflight did not preserve creation action transport: {service_preflight}")
            finally:
                gateway_server.shutdown()
                gateway_thread.join(timeout=120)
                task_prepare.governor_by_name = original_governor_by_name
            original_governor_refs = brigade.governor_refs
            brigade.governor_refs = lambda: [ServiceGovernor()]
            try:
                governor_snapshot = warmaster_gateway.governor_registry_snapshot(include_health=True)
                required_workers = governor_snapshot[0].get("runtime", {}).get("capabilities", {}).get("capabilities", {}).get("required_workers", [])
                if "Lexmechanic" not in required_workers or "FabricatorFinalis" not in required_workers:
                    raise AssertionError(f"governor health snapshot did not include service capabilities: {governor_snapshot}")
                requirements = warmaster_gateway.governor_worker_requirements(governor_snapshot, warmaster_gateway.worker_registry_snapshot())
                if not requirements or not requirements[0].get("satisfied") or requirements[0].get("missing_workers"):
                    raise AssertionError(f"governor worker requirements were not satisfied: {requirements}")
                pipelines = warmaster_gateway.governor_pipeline_summaries(governor_snapshot)
                if (
                    not pipelines
                    or pipelines[0].get("governor") != "IskandarKhayon"
                    or pipelines[0].get("pipeline", {}).get("steps", [])[0].get("worker") != "CorpusIngestor"
                ):
                    raise AssertionError(f"governor pipeline summaries were not exposed: {pipelines}")
            finally:
                brigade.governor_refs = original_governor_refs
        finally:
            iskandar_server.shutdown()
            iskandar_thread.join(timeout=120)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(run_root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            if request_options(base + "/task") != 204:
                raise AssertionError("OPTIONS did not return 204")
            try:
                request_json(
                    base + "/task",
                    {"message": "Исследуй Скалатракс и сделай report.", "task_id": "legacy-task-blocked-test"},
                    allow_legacy_task=False,
                )
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                blocked_legacy = json.loads(exc.read().decode("utf-8"))
                if (
                    blocked_legacy.get("error_code") != "legacy_direct_task_requires_explicit_opt_in"
                    or blocked_legacy.get("actions", {}).get("next_action", {}).get("endpoint") != "POST /orchestrate_run"
                    or blocked_legacy.get("client_action", {}).get("path") != "/orchestrate_run"
                ):
                    raise AssertionError(f"legacy /task did not point clients at command protocol: {blocked_legacy}")
            else:
                raise AssertionError("POST /task should require explicit legacy opt-in")
            health = request_json(base + "/health")
            if not health.get("ok"):
                raise AssertionError(f"bad health: {health}")
            capabilities = request_json(base + "/capabilities")
            required_capabilities = {
                "model_backed_gateway_orchestration",
                "background_execution",
                "worker_registry",
                "worker_cancel_fanout",
                "run_action_hints",
                "run_orchestration_cards",
                "run_execution_preflight",
                "restricted_step_execution",
                "interrupted_run_resume",
                "http_governor_planning",
                "brigade_plan_snapshot",
                "brigade_health_snapshot",
                "brigade_readiness_summary",
                "run_package_diagnostics",
                "run_oversight_read",
            }
            if not required_capabilities.issubset(set(capabilities.get("capabilities", []))):
                raise AssertionError(f"bad gateway capabilities response: {capabilities}")
            if (
                not capabilities.get("actions", {}).get("can_preflight_task")
                or not capabilities.get("actions", {}).get("can_preflight_runs")
                or not capabilities.get("actions", {}).get("can_execute_step_subsets")
                or not capabilities.get("actions", {}).get("can_check_brigade_readiness")
                or not capabilities.get("actions", {}).get("can_list_recoverable_runs")
                or not capabilities.get("actions", {}).get("can_list_orchestration_cards")
                or not capabilities.get("actions", {}).get("can_bulk_start_recoverable_runs")
                or not capabilities.get("actions", {}).get("can_poll_global_events")
                or capabilities.get("actions", {}).get("preferred_task_flow", [None])[0] != "POST /orchestrate_run"
                or "POST /task with allow_legacy_direct_task=true" not in capabilities.get("actions", {}).get("legacy_direct_task_flow", [])
                or capabilities.get("actions", {}).get("can_create_legacy_task")
                or not capabilities.get("actions", {}).get("legacy_direct_task_requires_explicit_opt_in")
                or "POST /orchestrate" not in capabilities.get("actions", {}).get("diagnostic_prepare_flow", [])
                or "POST /orchestrate_run" not in capabilities.get("command_protocol_endpoints", [])
                or "POST /task" not in capabilities.get("legacy_diagnostic_endpoints", [])
                or "GET /events?after=0" not in capabilities.get("actions", {}).get("polling", [])
                or "GET /recovery" not in capabilities.get("actions", {}).get("maintenance", [])
                or "GET /runs/{task_id}/package" not in capabilities.get("actions", {}).get("run_inspection", [])
                or "GET /runs/{task_id}/oversight" not in capabilities.get("actions", {}).get("run_inspection", [])
                or "final_package_read" not in capabilities.get("capabilities", [])
                or "POST /recovery/start_resume_local" not in capabilities.get("actions", {}).get("maintenance", [])
                or capabilities.get("summary", {}).get("governors", {}).get("active", 0) < 1
                or capabilities.get("summary", {}).get("workers", {}).get("active", 0) < 1
                or capabilities.get("display", {}).get("headline") != "Warmaster Gateway capabilities"
                or capabilities.get("client_action", {}).get("path") != "/state"
                or capabilities.get("model_brain", {}).get("kind") != "eye_of_terror_model_brain"
            ):
                raise AssertionError(f"gateway capabilities did not expose task action hints: {capabilities}")
            brigade_plan = request_json(base + "/brigade_plan")
            if (
                not brigade_plan.get("ok")
                or brigade_plan.get("ports", {}).get("warmaster_gateway") != 7000
                or not any(item.get("name") == "Lexmechanic" for item in brigade_plan.get("mechanicum_workers", []))
            ):
                raise AssertionError(f"bad brigade plan response: {brigade_plan}")
            brigade_health = request_json(base + "/brigade_health")
            if (
                not brigade_health.get("ok")
                or brigade_health.get("summary", {}).get("workers_total", 0) < 1
                or "ready" not in brigade_health.get("summary", {})
                or not isinstance(brigade_health.get("summary", {}).get("blockers"), list)
                or not isinstance(brigade_health.get("summary", {}).get("warnings"), list)
                or "workers" not in brigade_health.get("services", {})
            ):
                raise AssertionError(f"bad brigade health response: {brigade_health}")
            state = request_json(base + "/state")
            if state.get("brigade_plan", {}).get("mode") != "service-separated":
                raise AssertionError(f"state did not include brigade plan: {state}")
            if not state.get("actions", {}).get("can_create_task"):
                raise AssertionError(f"state did not include gateway action hints: {state}")
            if "brigade_health" in state:
                raise AssertionError(f"plain state should not include health checks: {state}")
            state_with_health = request_json(base + "/state?health=1")
            if state_with_health.get("brigade_health", {}).get("summary", {}).get("workers_total", 0) < 1:
                raise AssertionError(f"state health did not include brigade health: {state_with_health}")
            doctor = request_json(base + "/doctor")
            if not doctor.get("ok") or "worker_manifests" not in doctor.get("checks", []) or doctor.get("counts", {}).get("worker_manifests", 0) < 1:
                raise AssertionError(f"bad doctor response: {doctor}")
            corrupt_dir = run_root / "corrupt-ledger-test"
            corrupt_dir.mkdir(parents=True, exist_ok=True)
            (corrupt_dir / "status.json").write_text(json.dumps({"task_id": "corrupt-ledger-test"}), encoding="utf-8")
            (corrupt_dir / "task_ledger.json").write_text("{", encoding="utf-8")
            corrupt_runs = request_json(base + "/runs")
            corrupt_item = next((item for item in corrupt_runs.get("runs", []) if item.get("task_id") == "corrupt-ledger-test"), None)
            if not corrupt_item or corrupt_item.get("status") != "corrupt" or not corrupt_item.get("ledger_error"):
                raise AssertionError(f"corrupt ledger was not represented safely: {corrupt_runs}")
            corrupt_status_dir = run_root / "corrupt-status-test"
            corrupt_status_dir.mkdir(parents=True, exist_ok=True)
            (corrupt_status_dir / "status.json").write_text("{", encoding="utf-8")
            TaskLedger.create(corrupt_status_dir / "task_ledger.json", "corrupt-status-test", "test corrupt status", "IskandarKhayon")
            corrupt_status = request_json(base + "/runs/corrupt-status-test")
            if not corrupt_status.get("ok") or "status_error" not in corrupt_status:
                raise AssertionError(f"corrupt status was not represented safely: {corrupt_status}")
            corrupt_status_summary = request_json(base + "/runs/corrupt-status-test/summary")
            if corrupt_status_summary.get("summary", {}).get("status") != "corrupt":
                raise AssertionError(f"corrupt status summary was not represented safely: {corrupt_status_summary}")
            corrupt_contract_dir = run_root / "corrupt-contract-test"
            corrupt_contract_dir.mkdir(parents=True, exist_ok=True)
            (corrupt_contract_dir / "contract.json").write_text("{", encoding="utf-8")
            try:
                request_json(base + "/runs/corrupt-contract-test/contract")
            except urllib.error.HTTPError as exc:
                if exc.code != 500:
                    raise
                corrupt_contract = json.loads(exc.read().decode("utf-8"))
                if corrupt_contract.get("error_code") != "corrupt_contract":
                    raise AssertionError(f"bad corrupt contract response: {corrupt_contract}")
            else:
                raise AssertionError("corrupt contract endpoint should return a diagnostic error")
            governors = request_json(base + "/governors")
            if (
                not governors.get("ok")
                or not any(item.get("name") == "IskandarKhayon" for item in governors.get("governors", []))
                or governors.get("summary", {}).get("active", 0) < 1
                or governors.get("display", {}).get("headline") != "Governor registry"
            ):
                raise AssertionError(f"bad governors response: {governors}")
            governor_health = request_json(base + "/governors?health=1")
            if (
                not governor_health.get("health_checked")
                or not all("runtime" in item for item in governor_health.get("governors", []))
                or "reachable" not in governor_health.get("summary", {})
                or governor_health.get("display", {}).get("headline") != "Governor registry"
            ):
                raise AssertionError(f"bad governor health response: {governor_health}")
            workers = request_json(base + "/workers")
            if (
                not workers.get("ok")
                or not any(item.get("name") == "Lexmechanic" for item in workers.get("workers", []))
                or workers.get("summary", {}).get("active", 0) < 1
                or workers.get("display", {}).get("headline") != "Worker registry"
            ):
                raise AssertionError(f"bad workers response: {workers}")
            lexmechanic = next(item for item in workers["workers"] if item.get("name") == "Lexmechanic")
            if not lexmechanic.get("metadata_available") or "web_search" not in lexmechanic.get("capabilities", []):
                raise AssertionError(f"workers response did not expose worker metadata: {workers}")
            worker_health = request_json(base + "/workers?health=1")
            if (
                not worker_health.get("health_checked")
                or not all("runtime" in item for item in worker_health.get("workers", []))
                or "reachable" not in worker_health.get("summary", {})
                or worker_health.get("display", {}).get("headline") != "Worker registry"
            ):
                raise AssertionError(f"bad worker health response: {worker_health}")
            code_task = request_json(base + "/task", {"message": "почини python приложение", "task_id": "ceraxia-code-route-test"})
            if (
                not code_task.get("ok")
                or code_task.get("governor") != "Ceraxia"
                or code_task.get("status", {}).get("governor") != "Ceraxia"
                or code_task.get("status", {}).get("step_count") != 6
                or code_task.get("status", {}).get("steps", [])[0].get("step_id") != "repository_survey"
                or code_task.get("status", {}).get("steps", [])[0].get("worker") != "LogisRepository"
                or code_task.get("status", {}).get("steps", [])[0].get("port") != 7015
                or code_task.get("actions", {}).get("next_action", {}).get("kind") != "preflight_run"
                or code_task.get("model_brain", {}).get("status") != "answered"
            ):
                raise AssertionError(f"code route should create a Ceraxia run: {code_task}")
            image_preflight = request_json(base + "/task_preflight", {"message": "сделай рисовалку stable diffusion 512x512", "task_id": "supported-image"})
            if (
                not image_preflight.get("ok")
                or image_preflight.get("governor") != "Moriana"
                or image_preflight.get("contract_summary", {}).get("assigned_governor") != "Moriana"
                or image_preflight.get("contract_summary", {}).get("step_count") != 5
                or image_preflight.get("actions", {}).get("next_action", {}).get("kind") != "prepare_orchestrated_task"
                or image_preflight.get("client_action", {}).get("path") != "/orchestrate"
                or image_preflight.get("governor_plan_actions", {}).get("next_action", {}).get("kind") != "prepare_run"
            ):
                raise AssertionError(f"image route should preflight through Moriana: {image_preflight}")
            comic_preflight = request_json(base + "/task_preflight", {"message": "сделай комикс 4 панели про техножреца", "task_id": "supported-comic"})
            if (
                not comic_preflight.get("ok")
                or comic_preflight.get("governor") != "Moriana"
                or comic_preflight.get("route", {}).get("kind") != "comic_generation"
                or comic_preflight.get("contract_summary", {}).get("assigned_governor") != "Moriana"
                or comic_preflight.get("contract_summary", {}).get("kind") != "comic_generation"
                or comic_preflight.get("contract_summary", {}).get("step_count") != 5
                or comic_preflight.get("actions", {}).get("next_action", {}).get("kind") != "prepare_orchestrated_task"
                or comic_preflight.get("client_action", {}).get("path") != "/orchestrate"
                or comic_preflight.get("governor_plan_actions", {}).get("next_action", {}).get("kind") != "prepare_run"
            ):
                raise AssertionError(f"comic route should preflight through Moriana: {comic_preflight}")
            series_preflight = request_json(base + "/task_preflight", {"message": "сделай серию 3 изображения про одну кузню", "task_id": "supported-image-series"})
            if (
                not series_preflight.get("ok")
                or series_preflight.get("governor") != "Moriana"
                or series_preflight.get("route", {}).get("kind") != "image_series_generation"
                or series_preflight.get("contract_summary", {}).get("kind") != "image_series_generation"
                or series_preflight.get("contract_summary", {}).get("step_count") != 5
                or series_preflight.get("actions", {}).get("next_action", {}).get("kind") != "prepare_orchestrated_task"
                or series_preflight.get("client_action", {}).get("path") != "/orchestrate"
            ):
                raise AssertionError(f"image series route should preflight through Moriana: {series_preflight}")
            try:
                request_json(
                    base + "/task",
                    {"message": "Исследуй Скалатракс и сделай report.", "task_id": "../escape"},
                )
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
                invalid_task = json.loads(exc.read().decode("utf-8"))
                if (
                    invalid_task.get("error_code") != "invalid_task_id"
                    or invalid_task.get("phase") != "task_blocked"
                    or invalid_task.get("display", {}).get("headline") != "Task cannot be prepared"
                ):
                    raise AssertionError(f"bad invalid task_id response: {invalid_task}")
            else:
                raise AssertionError("unsafe task_id should be rejected")
            preflight = request_json(
                base + "/task_preflight",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-preflight-test"},
            )
            preflight_steps = preflight.get("contract_summary", {}).get("steps", [])
            preflight_action_body = preflight.get("actions", {}).get("next_action", {}).get("body", {})
            if (
                not preflight.get("ok")
                or (run_root / "warmaster-preflight-test").exists()
                or len(preflight_steps) < 2
                or preflight_steps[0].get("worker") != "CorpusIngestor"
                or preflight_steps[1].get("depends_on") != ["corpus_ingestion"]
                or preflight_steps[2].get("expected_artifacts") != ["/work/skalathrax/source_snapshots.json"]
                or preflight_steps[2].get("expected_artifact_count") != 1
                or preflight_steps[3].get("worker") != "OcularisRenderium"
                or preflight_steps[3].get("expected_artifacts") != ["/work/skalathrax/rendered_snapshots.json"]
                or preflight_steps[4].get("depends_on") != ["source_rendering"]
                or preflight.get("oversight_summary", {}).get("final_review", {}).get("final_artifact") != "/work/skalathrax/final_manifest.json"
                or not preflight.get("oversight_validation", {}).get("ok")
                or preflight.get("actions", {}).get("can_create_task") is not True
                or preflight_action_body.get("message") != "Исследуй Скалатракс и сделай report."
                or preflight_action_body.get("task_id") != "warmaster-preflight-test"
                or preflight.get("actions", {}).get("next_action", {}).get("kind") != "prepare_orchestrated_task"
                or preflight.get("actions", {}).get("next_action", {}).get("method") != "POST"
                or preflight.get("client_action", {}).get("path") != "/orchestrate"
                or preflight.get("phase") != "task_ready"
                or preflight.get("decision", {}).get("can_create_task") is not True
                or preflight.get("display", {}).get("headline") != "Task is ready"
                or preflight.get("model_brain", {}).get("status") != "answered"
            ):
                raise AssertionError(f"task preflight should not create a run: {preflight}")
            if "brigade_readiness" in preflight:
                raise AssertionError(f"default task preflight should stay lightweight: {preflight}")
            preflight_with_readiness = request_json(
                base + "/task_preflight",
                {
                    "message": "Исследуй Скалатракс и сделай report.",
                    "task_id": "warmaster-preflight-readiness-test",
                    "include_brigade_health": True,
                },
            )
            if (
                not preflight_with_readiness.get("ok")
                or "brigade_readiness" not in preflight_with_readiness
                or not isinstance(preflight_with_readiness.get("brigade_readiness", {}).get("blockers"), list)
                or not isinstance(preflight_with_readiness.get("brigade_readiness", {}).get("warnings"), list)
            ):
                raise AssertionError(f"task preflight did not expose compact brigade readiness: {preflight_with_readiness}")
            orchestrated = request_json(
                base + "/orchestrate",
                {
                    "message": "Исследуй Скалатракс и сделай report.",
                    "task_id": "warmaster-orchestrate-test",
                    "run_mode": "local",
                    "timeout_sec": 180,
                },
            )
            orchestrated_run_dir = Path(orchestrated.get("run_dir", ""))
            orchestrated_ledger = json.loads((orchestrated_run_dir / "task_ledger.json").read_text(encoding="utf-8"))
            orchestrated_mission_ref = json.loads((orchestrated_run_dir / "mission_ref.json").read_text(encoding="utf-8"))
            if (
                not orchestrated.get("ok")
                or orchestrated.get("phase") != "ready_to_start"
                or [item.get("stage") for item in orchestrated.get("trace", [])] != ["commander_intake", "task_preflight", "task", "run_preflight"]
                or not str(orchestrated.get("mission_id") or "").startswith("mission-")
                or orchestrated_mission_ref.get("mission_id") != orchestrated.get("mission_id")
                or not Path(str(orchestrated_mission_ref.get("mission_dir") or "")).joinpath("commander_order.json").exists()
                or orchestrated.get("client_action", {}).get("path") != "/runs/warmaster-orchestrate-test/start_local"
                or orchestrated_ledger.get("status") != "created"
                or not any(item.get("type") == "run_preflight_recorded" for item in orchestrated_ledger.get("events", []))
                or orchestrated.get("model_brain", {}).get("status") != "answered"
            ):
                raise AssertionError(f"prepare orchestration did not stop at a start recommendation: {orchestrated}")
            orchestrated_state = request_json(base + "/runs/warmaster-orchestrate-test/orchestration?events_after=0")
            if (
                orchestrated_state.get("phase") != "ready_to_start"
                or not orchestrated_state.get("decision", {}).get("can_start")
                or orchestrated_state.get("display", {}).get("headline") != "Run is ready to start"
                or orchestrated_state.get("display", {}).get("progress", {}).get("planned_steps") != 10
                or orchestrated_state.get("next_action", {}).get("kind") != "start"
                or orchestrated_state.get("client_action", {}).get("path") != "/runs/warmaster-orchestrate-test/start_http"
                or orchestrated_state.get("snapshot", {}).get("summary", {}).get("task_id") != "warmaster-orchestrate-test"
            ):
                raise AssertionError(f"orchestration state did not expose ready-to-start decision: {orchestrated_state}")
            orchestrated_activity = request_json(base + "/runs/warmaster-orchestrate-test/activity")
            if (
                not orchestrated_activity.get("ok")
                or not orchestrated_activity.get("progress_events")
                or not orchestrated_activity.get("protocol_activity_cards")
                or orchestrated_activity.get("activity_log")
                or orchestrated_activity.get("governor_activity", {}).get("log_text")
                or orchestrated_activity.get("activity_cards", [{}])[0].get("source") != "mission_protocol"
            ):
                raise AssertionError(f"command-protocol activity did not expose structured progress cards: {orchestrated_activity}")
            orchestrated_start = request_json(
                base + "/orchestrate_start",
                {"task_id": "warmaster-orchestrate-test", "run_mode": "local", "timeout_sec": 180},
            )
            if (
                not orchestrated_start.get("ok")
                or orchestrated_start.get("phase") != "started"
                or orchestrated_start.get("operation") != "start"
                or orchestrated_start.get("next_action", {}).get("kind") != "poll"
                or orchestrated_start.get("client_action", {}).get("path") != "/runs/warmaster-orchestrate-test/snapshot"
                or orchestrated_start.get("snapshot", {}).get("task_id") != "warmaster-orchestrate-test"
                or orchestrated_start.get("model_brain", {}).get("status") != "answered"
            ):
                raise AssertionError(f"orchestrated start did not return a polling snapshot: {orchestrated_start}")
            orchestrated_start_events = request_json(base + "/runs/warmaster-orchestrate-test/events")
            if not any(item.get("type") == "background_start_requested" for item in orchestrated_start_events.get("events", [])):
                raise AssertionError(f"orchestrated start did not record background start: {orchestrated_start_events}")
            orchestrated_run = request_json(
                base + "/orchestrate_run",
                {
                    "message": "Исследуй Скалатракс и сделай report.",
                    "task_id": "warmaster-orchestrate-run-test",
                    "run_mode": "local",
                    "timeout_sec": 180,
                },
            )
            if (
                not orchestrated_run.get("ok")
                or orchestrated_run.get("phase") != "started"
                or [item.get("stage") for item in orchestrated_run.get("trace", [])]
                != ["commander_intake", "task_preflight", "task", "run_preflight", "orchestrate_start"]
                or orchestrated_run.get("start", {}).get("operation") != "start"
                or orchestrated_run.get("next_action", {}).get("kind") != "poll"
                or orchestrated_run.get("orchestration", {}).get("task_id") != "warmaster-orchestrate-run-test"
                or "headline" not in orchestrated_run.get("display", {})
                or not isinstance(orchestrated_run.get("decision"), dict)
                or not isinstance(orchestrated_run.get("display_events"), list)
                or "{task_id}" in str(orchestrated_run.get("client_action", {}).get("path") or "")
                or orchestrated_run.get("model_brain", {}).get("status") != "answered"
            ):
                raise AssertionError(f"one-shot orchestration did not prepare and start a run: {orchestrated_run}")
            plan_understanding = str(orchestrated_run.get("prepare", {}).get("governor_plan", {}).get("understanding") or "")
            if plan_understanding.startswith("ПРИКАЗ ВАРМАСТЕРА"):
                raise AssertionError(f"governor_plan leaked raw commander order: {plan_understanding}")
            orchestrated_run_events = request_json(base + "/runs/warmaster-orchestrate-run-test/events")
            if (
                not any(item.get("type") == "run_preflight_recorded" for item in orchestrated_run_events.get("events", []))
                or not any(item.get("type") == "background_start_requested" for item in orchestrated_run_events.get("events", []))
            ):
                raise AssertionError(f"one-shot orchestration did not record prepare/start events: {orchestrated_run_events}")
            orchestrated_retry = request_json(
                base + "/orchestrate_run",
                {
                    "message": "Исследуй Скалатракс и сделай report.",
                    "task_id": "warmaster-orchestrate-run-test",
                    "run_mode": "local",
                    "auto_start": False,
                },
            )
            if (
                not orchestrated_retry.get("ok")
                or orchestrated_retry.get("phase") != "existing_run"
                or not orchestrated_retry.get("reused_existing")
                or orchestrated_retry.get("orchestration", {}).get("task_id") != "warmaster-orchestrate-run-test"
                or "headline" not in orchestrated_retry.get("orchestration", {}).get("display", {})
                or "headline" not in orchestrated_retry.get("display", {})
                or not isinstance(orchestrated_retry.get("decision"), dict)
                or not isinstance(orchestrated_retry.get("display_events"), list)
                or orchestrated_retry.get("prepare", {}).get("task_preflight", {}).get("error_code") != "task_exists"
                or orchestrated_retry.get("model_brain", {}).get("status") != "answered"
            ):
                raise AssertionError(f"one-shot orchestration retry did not reuse existing run: {orchestrated_retry}")
            task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-test"},
            )
            if (
                not task.get("ok")
                or task.get("governor") != "IskandarKhayon"
                or task.get("actions", {}).get("next_action", {}).get("kind") != "preflight_run"
                or task.get("actions", {}).get("next_action", {}).get("endpoint") != "POST /runs/{task_id}/preflight_http"
                or task.get("client_action", {}).get("path") != "/runs/warmaster-test/preflight_http"
                or task.get("phase") != "task_created"
                or task.get("display", {}).get("headline") != "Task created"
            ):
                raise AssertionError(f"bad task response: {task}")
            global_events = request_json(base + "/events?limit=20")
            warmaster_event = next(
                (item for item in global_events.get("events", []) if item.get("task_id") == "warmaster-test" and item.get("type") == "task_created"),
                {},
            )
            if (
                not global_events.get("ok")
                or not warmaster_event
                or warmaster_event.get("run_status") != "created"
                or warmaster_event.get("governor") != "IskandarKhayon"
                or not warmaster_event.get("run_updated_at")
                or warmaster_event.get("display", {}).get("headline") != "Task created"
                or warmaster_event.get("run_client_action", {}).get("path") != "/runs/warmaster-test/start_http"
                or not any(item.get("task_id") == "warmaster-test" and item.get("headline") == "Task created" for item in global_events.get("display_events", []))
                or not all("global_index" in item for item in global_events.get("events", []))
            ):
                raise AssertionError(f"global run events did not expose task creation: {global_events}")
            global_cursor = global_events.get("cursor", {})
            next_global_events = request_json(base + f"/events?after={global_cursor.get('next', 0)}")
            if next_global_events.get("cursor", {}).get("after") != global_cursor.get("next"):
                raise AssertionError(f"global run event cursor did not advance from previous next: {next_global_events}")
            try:
                request_json(
                    base + "/task",
                    {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-test"},
                )
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                duplicate = json.loads(exc.read().decode("utf-8"))
                if (
                    duplicate.get("error_code") != "task_exists"
                    or duplicate.get("actions", {}).get("next_action", {}).get("kind") != "inspect_existing_run"
                ):
                    raise AssertionError(f"bad duplicate task response: {duplicate}")
            else:
                raise AssertionError("duplicate task_id should not overwrite an existing run")
            try:
                request_json(
                    base + "/task_preflight",
                    {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-test"},
                )
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                duplicate_preflight = json.loads(exc.read().decode("utf-8"))
            else:
                raise AssertionError("duplicate task preflight should return a conflict")
            if (
                duplicate_preflight.get("ok")
                or duplicate_preflight.get("actions", {}).get("can_create_task")
                or duplicate_preflight.get("actions", {}).get("next_action", {}).get("kind") != "inspect_existing_run"
            ):
                raise AssertionError(f"duplicate task preflight should expose inspect action: {duplicate_preflight}")
            state = request_json(base + "/state?run_limit=5")
            if (
                not state.get("ok")
                or not any(item.get("task_id") == "warmaster-test" for item in state.get("runs", []))
                or not any(item.get("name") == "Lexmechanic" for item in state.get("workers", []))
                or "state_snapshot" not in state.get("capabilities", {}).get("capabilities", [])
                or "process_active_run_snapshot" not in state.get("capabilities", {}).get("capabilities", [])
                or not isinstance(state.get("process_active_runs"), list)
                or state.get("run_summary", {}).get("total", 0) < 2
                or not any(
                    item.get("task_id") == "warmaster-test" and "headline" in item.get("display", {}) and isinstance(item.get("decision"), dict)
                    for item in state.get("orchestration_cards", [])
                )
            ):
                raise AssertionError(f"bad gateway state: {state}")
            run_dir = Path(task["run_dir"])
            if not (run_dir / "dispatch" / "source_discovery.json").exists():
                raise AssertionError(f"gateway did not prepare run package: {task}")
            run_preflight = request_json(base + "/runs/warmaster-test/preflight_local", {"timeout_sec": 180})
            if (
                not run_preflight.get("ok")
                or run_preflight.get("step_ids", [])[0] != "corpus_ingestion"
                or run_preflight.get("oversight_summary", {}).get("final_review", {}).get("final_step") != "finalize"
                or run_preflight.get("run_status") != "created"
                or run_preflight.get("actions", {}).get("can_start_run") is not True
                or run_preflight.get("actions", {}).get("next_action", {}).get("kind") != "start_run"
                or run_preflight.get("actions", {}).get("next_action", {}).get("endpoint") != "POST /runs/{task_id}/start_local"
                or run_preflight.get("phase") != "ready_to_start"
                or not run_preflight.get("decision", {}).get("can_start")
                or run_preflight.get("display", {}).get("headline") != "Run is ready to start"
                or run_preflight.get("client_action", {}).get("path") != "/runs/warmaster-test/start_local"
            ):
                raise AssertionError(f"bad local run preflight: {run_preflight}")
            completed_preflight_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-completed-preflight-test"},
            )
            completed_ledger_path = Path(completed_preflight_task["run_dir"], "task_ledger.json")
            completed_ledger = json.loads(completed_ledger_path.read_text(encoding="utf-8"))
            completed_ledger["status"] = "completed"
            completed_ledger["result"] = {"status": "ready", "workspace_root": str(Path(completed_preflight_task["run_dir"], "workspace"))}
            write_json(completed_ledger_path, completed_ledger)
            completed_preflight = request_json(base + "/runs/warmaster-completed-preflight-test/preflight_local", {"timeout_sec": 180})
            completed_next_action = completed_preflight.get("actions", {}).get("next_action", {})
            if (
                not completed_preflight.get("ok")
                or completed_preflight.get("actions", {}).get("can_start_run")
                or completed_next_action.get("kind") != "rerun_requires_force"
                or completed_next_action.get("endpoint") != "POST /runs/{task_id}/start_local"
                or completed_next_action.get("body", {}).get("force") is not True
                or completed_preflight.get("client_action", {}).get("path") != "/runs/warmaster-completed-preflight-test/start_local"
                or completed_preflight.get("client_action", {}).get("body", {}).get("force") is not True
            ):
                raise AssertionError(f"completed run preflight should preserve run-status action gates: {completed_preflight}")
            missing_oversight_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-missing-oversight-test"},
            )
            Path(missing_oversight_task["run_dir"], "oversight.json").unlink()
            try:
                request_json(base + "/runs/warmaster-missing-oversight-test/preflight_local", {"timeout_sec": 180})
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                missing_oversight = json.loads(exc.read().decode("utf-8"))
                if (
                    missing_oversight.get("ok")
                    or not missing_oversight.get("oversight_errors")
                    or missing_oversight.get("actions", {}).get("next_action", {}).get("kind") != "inspect_oversight"
                ):
                    raise AssertionError(f"run preflight should reject missing oversight: {missing_oversight}")
            else:
                raise AssertionError("run preflight should fail when oversight.json is missing")
            bad_oversight_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-bad-oversight-test"},
            )
            bad_oversight_path = Path(bad_oversight_task["run_dir"], "oversight.json")
            bad_oversight_payload = json.loads(bad_oversight_path.read_text(encoding="utf-8"))
            bad_oversight_payload["final_review"]["final_artifact"] = "/work/skalathrax/not-produced.json"
            write_json(bad_oversight_path, bad_oversight_payload)
            bad_oversight_inspection = request_json(base + "/runs/warmaster-bad-oversight-test/oversight")
            if (
                not bad_oversight_inspection.get("ok")
                or bad_oversight_inspection.get("validation", {}).get("ok")
                or not any("not required by contract" in error for error in bad_oversight_inspection.get("validation", {}).get("errors", []))
            ):
                raise AssertionError(f"oversight endpoint should diagnose inconsistent oversight: {bad_oversight_inspection}")
            bad_oversight_summary = request_json(base + "/runs/warmaster-bad-oversight-test/summary")
            if (
                not bad_oversight_summary.get("summary", {}).get("oversight_errors")
                or bad_oversight_summary.get("summary", {}).get("actions", {}).get("can_start")
                or bad_oversight_summary.get("summary", {}).get("actions", {}).get("next_action", {}).get("kind") != "inspect_oversight"
            ):
                raise AssertionError(f"summary should block actions for inconsistent oversight: {bad_oversight_summary}")
            bad_revision_policy_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-bad-revision-policy-test"},
            )
            bad_revision_policy_path = Path(bad_revision_policy_task["run_dir"], "oversight.json")
            bad_revision_policy_payload = json.loads(bad_revision_policy_path.read_text(encoding="utf-8"))
            bad_revision_policy_payload["revision_policy"] = {
                "source_step": "missing_critic",
                "final_steps": ["finalize"],
                "requires_downstream_rerun": "yes",
                "requires_focused_context": True,
                "requires_gap_disclosure": True,
            }
            write_json(bad_revision_policy_path, bad_revision_policy_payload)
            bad_revision_policy_summary = request_json(base + "/runs/warmaster-bad-revision-policy-test/summary")
            bad_revision_policy_errors = bad_revision_policy_summary.get("summary", {}).get("oversight_errors", [])
            if (
                not any("revision_policy.source_step" in error for error in bad_revision_policy_errors)
                or not any("must include final_review step: critic_review" in error for error in bad_revision_policy_errors)
                or not any("revision_policy.requires_downstream_rerun must be a boolean" in error for error in bad_revision_policy_errors)
                or bad_revision_policy_summary.get("summary", {}).get("actions", {}).get("next_action", {}).get("kind") != "inspect_oversight"
            ):
                raise AssertionError(f"summary should block bad revision policy: {bad_revision_policy_summary}")
            bad_quality_matrix_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-bad-quality-matrix-test"},
            )
            bad_quality_matrix_path = Path(bad_quality_matrix_task["run_dir"], "oversight.json")
            bad_quality_matrix_payload = json.loads(bad_quality_matrix_path.read_text(encoding="utf-8"))
            bad_quality_matrix_payload["step_quality_matrix"][0]["worker"] = "WrongWorker"
            bad_quality_matrix_payload["step_quality_matrix"][1]["revision_targets"] = ["missing_step"]
            write_json(bad_quality_matrix_path, bad_quality_matrix_payload)
            bad_quality_matrix_summary = request_json(base + "/runs/warmaster-bad-quality-matrix-test/summary")
            bad_quality_matrix_errors = bad_quality_matrix_summary.get("summary", {}).get("oversight_errors", [])
            if (
                not any("worker does not match run step" in error for error in bad_quality_matrix_errors)
                or not any("revision_targets references unknown step" in error for error in bad_quality_matrix_errors)
                or bad_quality_matrix_summary.get("summary", {}).get("actions", {}).get("next_action", {}).get("kind") != "inspect_oversight"
            ):
                raise AssertionError(f"summary should block bad step quality matrix: {bad_quality_matrix_summary}")
            try:
                request_json(base + "/runs/warmaster-bad-oversight-test/package")
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                bad_package = json.loads(exc.read().decode("utf-8"))
                if (
                    bad_package.get("ok")
                    or not bad_package.get("validation", {}).get("errors")
                    or bad_package.get("client_action", {}).get("path") != "/runs/warmaster-bad-oversight-test/oversight"
                    or bad_package.get("next_action", {}).get("kind") != "inspect_oversight"
                    or not bad_package.get("display", {}).get("headline")
                    or not bad_package.get("run_summary", {}).get("oversight_errors")
                ):
                    raise AssertionError(f"package diagnostics should reject bad oversight: {bad_package}")
            else:
                raise AssertionError("package diagnostics should fail for inconsistent oversight")
            bad_oversight_direct = request_json(base + "/runs/warmaster-bad-oversight-test/oversight")
            if (
                not bad_oversight_direct.get("ok")
                or not bad_oversight_direct.get("validation", {}).get("errors")
                or bad_oversight_direct.get("client_action", {}).get("path") != "/runs/warmaster-bad-oversight-test/oversight"
                or bad_oversight_direct.get("next_action", {}).get("kind") != "inspect_oversight"
                or not bad_oversight_direct.get("display", {}).get("headline")
                or not bad_oversight_direct.get("run_summary", {}).get("oversight_errors")
            ):
                raise AssertionError(f"bad oversight diagnostics should expose client state: {bad_oversight_direct}")
            bad_package_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-bad-package-test"},
            )
            Path(bad_package_task["run_dir"], "dispatch", "source_discovery.json").write_text("{", encoding="utf-8")
            bad_package_summary = request_json(base + "/runs/warmaster-bad-package-test/summary")
            if (
                not bad_package_summary.get("summary", {}).get("package_errors")
                or bad_package_summary.get("summary", {}).get("actions", {}).get("can_start")
                or bad_package_summary.get("summary", {}).get("actions", {}).get("next_action", {}).get("kind") != "inspect_package"
            ):
                raise AssertionError(f"summary should block actions for inconsistent run package: {bad_package_summary}")
            try:
                request_json(base + "/runs/warmaster-bad-package-test/package")
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                broken_package = json.loads(exc.read().decode("utf-8"))
                if (
                    broken_package.get("ok")
                    or not broken_package.get("validation", {}).get("errors")
                    or broken_package.get("client_action", {}).get("path") != "/runs/warmaster-bad-package-test/package"
                    or broken_package.get("next_action", {}).get("kind") != "inspect_package"
                    or not broken_package.get("display", {}).get("headline")
                    or not broken_package.get("run_summary", {}).get("package_errors")
                ):
                    raise AssertionError(f"package diagnostics should reject corrupt dispatch: {broken_package}")
            else:
                raise AssertionError("package diagnostics should fail for corrupt dispatch")
            try:
                request_json(base + "/runs/warmaster-bad-oversight-test/preflight_local", {"timeout_sec": 180})
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                bad_oversight = json.loads(exc.read().decode("utf-8"))
                if (
                    bad_oversight.get("ok")
                    or not any("not required by contract" in error for error in bad_oversight.get("oversight_errors", []))
                    or bad_oversight.get("actions", {}).get("next_action", {}).get("kind") != "inspect_oversight"
                ):
                    raise AssertionError(f"run preflight should reject inconsistent oversight: {bad_oversight}")
            else:
                raise AssertionError("run preflight should fail when oversight final artifact drifts from contract")
            try:
                request_json(base + "/runs/warmaster-test/preflight_local", {"step_ids": ["fact_extraction"], "timeout_sec": 180})
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                blocked_preflight = json.loads(exc.read().decode("utf-8"))
                if blocked_preflight.get("ok") or not blocked_preflight.get("input_failures"):
                    raise AssertionError(f"restricted run preflight should require existing inputs: {blocked_preflight}")
                if blocked_preflight.get("actions", {}).get("next_action", {}).get("kind") != "inspect_package":
                    raise AssertionError(f"restricted run preflight should expose package inspection action: {blocked_preflight}")
            else:
                raise AssertionError("restricted local run preflight should fail before dependency artifacts exist")
            try:
                request_json(base + "/runs/warmaster-test/execute_local", {"step_ids": ["missing_step"], "timeout_sec": 180}, timeout=180)
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
                unknown_step = json.loads(exc.read().decode("utf-8"))
                if "unknown run steps" not in unknown_step.get("error", ""):
                    raise AssertionError(f"bad unknown step rejection: {unknown_step}")
            else:
                raise AssertionError("execution should reject unknown requested step ids")
            preflight_events = request_json(base + "/runs/warmaster-test/events")
            recorded_preflights = [
                item
                for item in preflight_events.get("events", [])
                if item.get("type") == "run_preflight_recorded"
            ]
            if len(recorded_preflights) < 2 or recorded_preflights[-1].get("payload", {}).get("input_failures") != 1:
                raise AssertionError(f"run preflight was not recorded in ledger events: {preflight_events}")
            post_preflight_summary = request_json(base + "/runs/warmaster-test/summary")
            last_preflight = post_preflight_summary.get("summary", {}).get("last_preflight", {})
            if last_preflight.get("ok") or last_preflight.get("input_failures") != 1 or last_preflight.get("mode") != "local":
                raise AssertionError(f"run summary did not expose last preflight: {post_preflight_summary}")
            try:
                revision_step_ids_from_run(run_dir)
            except ValueError as exc:
                if "revision_plan" not in str(exc):
                    raise
            else:
                raise AssertionError("run without a revision plan should not expose revision steps")
            run_status = request_json(base + "/runs/warmaster-test")
            if (
                not run_status.get("ok")
                or run_status.get("task_id") != "warmaster-test"
                or not run_status.get("ledger")
                or run_status.get("phase") != "ready_to_start"
                or not run_status.get("decision", {}).get("can_start")
                or run_status.get("display", {}).get("headline") != "Run is ready to start"
                or run_status.get("client_action", {}).get("path") != "/runs/warmaster-test/start_http"
            ):
                raise AssertionError(f"bad run status: {run_status}")
            run_summary = request_json(base + "/runs/warmaster-test/summary")
            if (
                not run_summary.get("ok")
                or run_summary.get("summary", {}).get("task_id") != "warmaster-test"
                or run_summary.get("summary", {}).get("revision_plan", {}).get("required")
                or not run_summary.get("summary", {}).get("actions", {}).get("can_preflight_local")
                or not run_summary.get("summary", {}).get("actions", {}).get("can_preflight_http")
                or not run_summary.get("summary", {}).get("actions", {}).get("can_start")
                or run_summary.get("summary", {}).get("actions", {}).get("next_action", {}).get("kind") != "start"
                or run_summary.get("summary", {}).get("actions", {}).get("next_action", {}).get("method") != "POST"
                or run_summary.get("phase") != "ready_to_start"
                or not run_summary.get("decision", {}).get("can_start")
                or run_summary.get("display", {}).get("headline") != "Run is ready to start"
                or run_summary.get("client_action", {}).get("path") != "/runs/warmaster-test/start_http"
                or run_summary.get("summary", {}).get("oversight_summary", {}).get("final_review", {}).get("final_artifact") != "/work/skalathrax/final_manifest.json"
                or run_summary.get("summary", {}).get("oversight_summary", {}).get("quality_gate_count") != 9
                or run_summary.get("summary", {}).get("oversight_summary", {}).get("step_quality_matrix_count") != 10
                or run_summary.get("summary", {}).get("oversight_summary", {}).get("step_quality_check_count", 0) < 8
                or run_summary.get("summary", {}).get("oversight_summary", {}).get("iteration_policy", {}).get("recommended_endpoint") != "POST /runs/{task_id}/start_research_loop_http"
                or run_summary.get("summary", {}).get("progress", {}).get("next_step_id") != "corpus_ingestion"
                or run_summary.get("summary", {}).get("progress", {}).get("next_ready_step_id") != "corpus_ingestion"
                or run_summary.get("summary", {}).get("progress", {}).get("ready_step_ids") != ["corpus_ingestion"]
                or "fact_extraction" not in run_summary.get("summary", {}).get("progress", {}).get("waiting_step_ids", [])
                or run_summary.get("summary", {}).get("progress", {}).get("ready_steps") != 1
                or run_summary.get("summary", {}).get("progress", {}).get("waiting_steps") != 9
                or run_summary.get("summary", {}).get("progress", {}).get("step_states", [{}])[0].get("worker") != "CorpusIngestor"
                or run_summary.get("summary", {}).get("progress", {}).get("step_states", [{}])[0].get("status") != "pending"
                or run_summary.get("summary", {}).get("progress", {}).get("step_states", [{}])[0].get("quality_hints", {}).get("check_count", 0) < 1
            ):
                raise AssertionError(f"bad run summary: {run_summary}")
            fact_step = next(
                (
                    item
                    for item in run_summary.get("summary", {}).get("progress", {}).get("step_states", [])
                    if item.get("step_id") == "fact_extraction"
                ),
                {},
            )
            if fact_step.get("input_artifacts") != ["/work/skalathrax/rendered_snapshots.json"]:
                raise AssertionError(f"run summary did not expose step input artifacts: {run_summary}")
            if fact_step.get("dependencies_ready") or not fact_step.get("dependency_status"):
                raise AssertionError(f"run summary did not expose dependency readiness: {run_summary}")
            source_step = request_json(base + "/runs/warmaster-test/steps/source_discovery")
            if (
                not source_step.get("ok")
                or source_step.get("step", {}).get("worker") != "Lexmechanic"
                or source_step.get("step", {}).get("status") != "pending"
            ):
                raise AssertionError(f"bad run step state: {source_step}")
            snapshot = request_json(base + "/runs/warmaster-test/snapshot?events_after=0&event_limit=1")
            if (
                not snapshot.get("ok")
                or snapshot.get("summary", {}).get("task_id") != "warmaster-test"
                or snapshot.get("active")
                or snapshot.get("event_cursor", {}).get("next") != 1
                or len(snapshot.get("display_events", [])) != 1
                or not snapshot.get("governor_activity", {}).get("chat_independent")
                or len(snapshot.get("governor_activity", {}).get("entries", [])) < 2
                or snapshot.get("governor_activity", {}).get("final_report", {}).get("kind") != "final_report"
                or snapshot.get("run_client_action", {}).get("path") != "/runs/warmaster-test/start_http"
                or snapshot.get("revision_plan", {}).get("required")
                or snapshot.get("summary", {}).get("oversight_summary", {}).get("final_review", {}).get("critic_step") != "critic_review"
            ):
                raise AssertionError(f"bad run snapshot: {snapshot}")
            activity = request_json(base + "/runs/warmaster-test/activity")
            if (
                not activity.get("ok")
                or not activity.get("governor_activity", {}).get("chat_independent")
                or activity.get("governor_activity", {}).get("task_id") != "warmaster-test"
                or not activity.get("summary_activity_cards")
                or activity.get("activity_log")
                or activity.get("governor_activity", {}).get("log_text")
            ):
                raise AssertionError(f"bad governor activity response: {activity}")
            run_active = request_json(base + "/runs/warmaster-test/active")
            if not run_active.get("ok") or run_active.get("active"):
                raise AssertionError(f"bad run active response: {run_active}")
            contract = request_json(base + "/runs/warmaster-test/contract")
            if (
                not contract.get("ok")
                or contract["contract"].get("task_id") != "warmaster-test"
                or contract.get("phase") != "ready_to_start"
                or contract.get("client_action", {}).get("path") != "/runs/warmaster-test/start_http"
                or not contract.get("display", {}).get("headline")
            ):
                raise AssertionError(f"bad run contract: {contract}")
            package = request_json(base + "/runs/warmaster-test/package")
            if (
                not package.get("ok")
                or not package.get("validation", {}).get("ok")
                or package.get("contract_summary", {}).get("step_count") != 10
                or package.get("dispatch_count") != 10
                or not package.get("files", {}).get("oversight")
                or package.get("phase") != "ready_to_start"
                or package.get("next_action", {}).get("kind") != "start"
                or package.get("client_action", {}).get("path") != "/runs/warmaster-test/start_http"
                or not package.get("display", {}).get("headline")
                or package.get("run_summary", {}).get("task_id") != "warmaster-test"
            ):
                raise AssertionError(f"bad run package diagnostics: {package}")
            oversight = request_json(base + "/runs/warmaster-test/oversight")
            if (
                not oversight.get("ok")
                or not oversight.get("validation", {}).get("ok")
                or oversight.get("summary", {}).get("final_review", {}).get("final_step") != "finalize"
                or oversight.get("summary", {}).get("revision_policy", {}).get("source_step") != "critic_review"
                or oversight.get("summary", {}).get("revision_policy", {}).get("requires_downstream_rerun") is not True
                or oversight.get("summary", {}).get("iteration_policy", {}).get("max_revision_cycles") != 3
                or oversight.get("oversight", {}).get("final_review", {}).get("final_artifact") != "/work/skalathrax/final_manifest.json"
                or oversight.get("oversight", {}).get("artifact_roles", {}).get("draft") != ["/work/skalathrax/reconstruction_ru.md"]
                or oversight.get("phase") != "ready_to_start"
                or oversight.get("client_action", {}).get("path") != "/runs/warmaster-test/start_http"
                or not oversight.get("display", {}).get("headline")
            ):
                raise AssertionError(f"bad run oversight: {oversight}")
            dispatch = request_json(base + "/runs/warmaster-test/dispatch")
            if (
                not dispatch.get("ok")
                or not any(item.get("packet", {}).get("worker") == "Lexmechanic" for item in dispatch.get("dispatch", []))
                or dispatch.get("phase") != "ready_to_start"
                or dispatch.get("client_action", {}).get("path") != "/runs/warmaster-test/start_http"
                or not dispatch.get("display", {}).get("headline")
            ):
                raise AssertionError(f"bad run dispatch: {dispatch}")
            worker_tasks = request_json(base + "/runs/warmaster-test/worker_tasks")
            if (
                not worker_tasks.get("ok")
                or not worker_tasks.get("worker_tasks")
                or worker_tasks["worker_tasks"][0].get("task_id") != "warmaster-test:corpus_ingestion"
                or worker_tasks.get("phase") != "ready_to_start"
                or worker_tasks.get("client_action", {}).get("path") != "/runs/warmaster-test/start_http"
                or not worker_tasks.get("display", {}).get("headline")
            ):
                raise AssertionError(f"bad worker task mapping: {worker_tasks}")
            try:
                request_json(base + "/runs/warmaster-test/worker_tasks?live=1&host=example.com")
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
                bad_host = json.loads(exc.read().decode("utf-8"))
                if "loopback" not in bad_host.get("error", ""):
                    raise AssertionError(f"bad worker task host rejection: {bad_host}")
            else:
                raise AssertionError("worker task live lookup should reject non-loopback host")
            events = request_json(base + "/runs/warmaster-test/events?limit=1")
            if (
                not events.get("ok")
                or len(events.get("events", [])) != 1
                or len(events.get("display_events", [])) != 1
                or events.get("run_client_action", {}).get("path") != "/runs/warmaster-test/start_http"
                or events.get("client_action", {}).get("path") != "/runs/warmaster-test/start_http"
                or events.get("phase") != "ready_to_start"
                or not events.get("display", {}).get("headline")
                or events.get("cursor", {}).get("next") != events.get("cursor", {}).get("total")
            ):
                raise AssertionError(f"bad run events: {events}")
            first_events = request_json(base + "/runs/warmaster-test/events?after=0&limit=1")
            if (
                not first_events.get("ok")
                or len(first_events.get("events", [])) != 1
                or first_events.get("display_events", [{}])[0].get("headline") != "Task created"
                or first_events.get("cursor", {}).get("next") != 1
            ):
                raise AssertionError(f"bad cursor run events: {first_events}")
            run_list = request_json(base + "/runs")
            if (
                not run_list.get("ok")
                or run_list.get("run_summary", {}).get("total", 0) < 1
                or not any(item.get("task_id") == "warmaster-test" and item.get("progress", {}).get("planned_steps") == 10 for item in run_list.get("runs", []))
                or not any(item.get("task_id") == "warmaster-test" and "headline" in item.get("display", {}) for item in run_list.get("orchestration_cards", []))
            ):
                raise AssertionError(f"bad run list: {run_list}")
            limited_run_list = request_json(base + "/runs?limit=1")
            if (
                not limited_run_list.get("ok")
                or len(limited_run_list.get("runs", [])) != 1
                or len(limited_run_list.get("orchestration_cards", [])) != 1
                or limited_run_list.get("run_summary", {}).get("total", 0) < 2
            ):
                raise AssertionError(f"bad limited run list: {limited_run_list}")
            restricted_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-restricted-test"},
            )
            restricted_run_dir = Path(restricted_task["run_dir"])
            restricted = request_json(
                base + "/runs/warmaster-restricted-test/execute_local",
                {"step_ids": ["corpus_ingestion"], "timeout_sec": LOCAL_EXEC_TIMEOUT_SEC},
                timeout=LOCAL_EXEC_TIMEOUT_SEC,
            )
            if (
                not restricted.get("ok")
                or not restricted.get("summary", {}).get("partial_execution")
                or restricted.get("phase") != "resume_required"
                or not restricted.get("decision", {}).get("can_resume")
                or restricted.get("display", {}).get("headline") != "Run can be resumed"
                or restricted.get("next_action", {}).get("kind") != "resume"
                or restricted.get("client_action", {}).get("path") != "/runs/warmaster-restricted-test/start_resume_http"
            ):
                raise AssertionError(f"restricted execution did not report partial success: {restricted}")
            restricted_ledger = request_json(base + "/runs/warmaster-restricted-test/ledger")
            if restricted_ledger.get("ledger", {}).get("status") != "interrupted":
                raise AssertionError(f"restricted execution should leave pending work interrupted: {restricted_ledger}")
            restricted_summary = request_json(base + "/runs/warmaster-restricted-test/summary")
            if restricted_summary.get("summary", {}).get("progress", {}).get("ready_step_ids", [None])[0] != "source_discovery":
                raise AssertionError(f"restricted execution did not advance ready steps: {restricted_summary}")
            restricted_pending = resume_step_ids_from_run(restricted_run_dir)
            if not restricted_pending or restricted_pending[0] != "source_discovery" or "corpus_ingestion" in restricted_pending:
                raise AssertionError(f"restricted execution did not expose resumable pending steps: {restricted_pending}")
            restricted_resumed = request_json(
                base + "/runs/warmaster-restricted-test/resume_local",
                {"timeout_sec": LOCAL_EXEC_TIMEOUT_SEC},
                timeout=LOCAL_EXEC_TIMEOUT_SEC,
            )
            if (
                not restricted_resumed.get("ok")
                or restricted_resumed.get("next_action", {}).get("kind") != "resume"
                or restricted_resumed.get("client_action", {}).get("path") != "/runs/warmaster-restricted-test/start_resume_http"
            ):
                raise AssertionError(f"restricted run did not resume cleanly: {restricted_resumed}")
            executed = request_json(
                base + "/runs/warmaster-test/execute_local",
                {"timeout_sec": LOCAL_EXEC_TIMEOUT_SEC},
                timeout=LOCAL_EXEC_TIMEOUT_SEC,
            )
            if (
                not executed.get("ok")
                or executed.get("phase") != "completed"
                or not executed.get("decision", {}).get("can_inspect_final")
                or executed.get("display", {}).get("headline") != "Run completed"
                or executed.get("next_action", {}).get("kind") != "inspect_final"
                or executed.get("client_action", {}).get("path") != "/runs/warmaster-test/final"
            ):
                raise AssertionError(f"bad local execution: {executed}")
            ledger = request_json(base + "/runs/warmaster-test/ledger")
            if not ledger.get("ok") or ledger["ledger"].get("status") != "completed":
                raise AssertionError(f"bad ledger after execution: {ledger}")
            try:
                request_json(base + "/runs/warmaster-test/cancel", {"reason": "late cancel should not mutate terminal run"})
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                late_cancel = json.loads(exc.read().decode("utf-8"))
                if (
                    late_cancel.get("ledger", {}).get("status") != "completed"
                    or late_cancel.get("client_action", {}).get("path") != "/runs/warmaster-test/summary"
                ):
                    raise AssertionError(f"late cancel should preserve completed ledger: {late_cancel}")
            else:
                raise AssertionError("Warmaster should reject cancellation for completed runs")
            ledger_after_late_cancel = request_json(base + "/runs/warmaster-test/ledger")
            if ledger_after_late_cancel.get("ledger", {}).get("status") != "completed":
                raise AssertionError(f"late cancel rewrote completed run state: {ledger_after_late_cancel}")
            artifacts = request_json(base + "/runs/warmaster-test/artifacts")
            if (
                not artifacts.get("ok")
                or not artifacts.get("artifacts")
                or not artifacts["artifacts"][0].get("exists")
                or artifacts.get("phase") != "completed"
                or artifacts.get("client_action", {}).get("path") != "/runs/warmaster-test/final"
                or not artifacts.get("display", {}).get("headline")
            ):
                raise AssertionError(f"bad artifacts response: {artifacts}")
            artifact_paths = {item.get("path") for item in artifacts.get("artifacts", [])}
            if "/work/skalathrax/reconstruction_ru.md" not in artifact_paths:
                raise AssertionError(f"artifacts response did not expand final manifest package: {artifacts}")
            final_manifest_item = next((item for item in artifacts.get("artifacts", []) if item.get("path") == "/work/skalathrax/final_manifest.json"), {})
            if (
                final_manifest_item.get("manifest_summary", {}).get("status") != "ready"
                or "critic_metrics" not in final_manifest_item.get("manifest_summary", {})
                or "event_review" not in final_manifest_item.get("manifest_summary", {})
                or "corpus_diagnostics" not in final_manifest_item.get("manifest_summary", {})
                or "corpus_requirements" not in final_manifest_item.get("manifest_summary", {})
                or "package_file_errors" not in final_manifest_item.get("manifest_summary", {})
                or "readiness_checks" not in final_manifest_item.get("manifest_summary", {})
                or final_manifest_item.get("manifest_summary", {}).get("file_count", 0) < 1
            ):
                raise AssertionError(f"artifacts response did not expose final manifest summary: {artifacts}")
            final_manifest_host_path = Path(final_manifest_item.get("host_path") or "")
            original_manifest_text = final_manifest_host_path.read_text(encoding="utf-8")
            try:
                final_manifest_host_path.write_text("{", encoding="utf-8")
                corrupt_artifacts = request_json(base + "/runs/warmaster-test/artifacts")
                corrupt_manifest_item = next((item for item in corrupt_artifacts.get("artifacts", []) if item.get("path") == "/work/skalathrax/final_manifest.json"), {})
                if (
                    not corrupt_manifest_item.get("manifest_error")
                    or corrupt_artifacts.get("phase") != "completed"
                    or corrupt_artifacts.get("client_action", {}).get("path") != "/runs/warmaster-test/start_http"
                    or corrupt_artifacts.get("client_action", {}).get("body", {}).get("force") is not True
                ):
                    raise AssertionError(f"artifacts response did not expose corrupt final manifest error: {corrupt_artifacts}")
            finally:
                final_manifest_host_path.write_text(original_manifest_text, encoding="utf-8")
            completed_snapshot = request_json(base + "/runs/warmaster-test/snapshot?events_after=0&event_limit=3")
            if (
                not completed_snapshot.get("ok")
                or not completed_snapshot.get("artifacts")
                or completed_snapshot.get("summary", {}).get("status") != "completed"
                or completed_snapshot.get("revision_plan", {}).get("required")
                or not completed_snapshot.get("summary", {}).get("actions", {}).get("force_required_for_rerun")
                or completed_snapshot.get("summary", {}).get("actions", {}).get("can_start")
                or completed_snapshot.get("summary", {}).get("actions", {}).get("next_action", {}).get("kind") != "rerun_requires_force"
                or completed_snapshot.get("summary", {}).get("actions", {}).get("next_action", {}).get("body", {}).get("force") is not True
                or completed_snapshot.get("summary", {}).get("final_manifest_summary", {}).get("status") != "ready"
                or "critic_metrics" not in completed_snapshot.get("summary", {}).get("final_manifest_summary", {})
                or "event_review" not in completed_snapshot.get("summary", {}).get("final_manifest_summary", {})
                or "corpus_diagnostics" not in completed_snapshot.get("summary", {}).get("final_manifest_summary", {})
                or "corpus_requirements" not in completed_snapshot.get("summary", {}).get("final_manifest_summary", {})
                or "package_file_errors" not in completed_snapshot.get("summary", {}).get("final_manifest_summary", {})
                or "readiness_checks" not in completed_snapshot.get("summary", {}).get("final_manifest_summary", {})
                or "blocker_count" not in completed_snapshot.get("summary", {}).get("final_manifest_summary", {})
                or completed_snapshot.get("summary", {}).get("progress", {}).get("pending_step_ids")
            ):
                raise AssertionError(f"bad completed run snapshot: {completed_snapshot}")
            final_state = completed_snapshot.get("summary", {}).get("progress", {}).get("step_states", [])[-1]
            if final_state.get("step_id") != "finalize" or "/work/skalathrax/final_manifest.json" not in final_state.get("artifacts", []):
                raise AssertionError(f"completed progress did not expose final step artifacts: {completed_snapshot}")
            final_expected = final_state.get("expected_artifact_status", [{}])[0]
            final_actual = final_state.get("artifact_status", [{}])[0]
            if not final_expected.get("exists") or not final_actual.get("exists") or final_actual.get("bytes", 0) <= 0:
                raise AssertionError(f"completed progress did not expose artifact file status: {completed_snapshot}")
            final_step_state = request_json(base + "/runs/warmaster-test/steps/finalize")
            if (
                "/work/skalathrax/final_manifest.json" not in final_step_state.get("step", {}).get("artifacts", [])
                or final_step_state.get("phase") != "completed"
                or final_step_state.get("client_action", {}).get("path") != "/runs/warmaster-test/final"
                or not final_step_state.get("display", {}).get("headline")
            ):
                raise AssertionError(f"bad final step state endpoint: {final_step_state}")
            final_step_artifacts = request_json(base + "/runs/warmaster-test/steps/finalize/artifacts")
            if (
                "/work/skalathrax/final_manifest.json" not in final_step_artifacts.get("artifacts", [])
                or not final_step_artifacts.get("artifact_status", [{}])[0].get("exists")
                or final_step_artifacts.get("phase") != "completed"
                or final_step_artifacts.get("client_action", {}).get("path") != "/runs/warmaster-test/final"
                or not final_step_artifacts.get("display", {}).get("headline")
            ):
                raise AssertionError(f"bad final step artifacts endpoint: {final_step_artifacts}")
            artifact_path = artifacts["artifacts"][0]["path"]
            text_artifact = request_json(base + f"/runs/warmaster-test/artifact_text?path={artifact_path}")
            if (
                not text_artifact.get("ok")
                or "ready" not in text_artifact.get("text", "")
                or text_artifact.get("phase") != "completed"
                or text_artifact.get("client_action", {}).get("path") != "/runs/warmaster-test/final"
                or not text_artifact.get("display", {}).get("headline")
            ):
                raise AssertionError(f"bad artifact text response: {text_artifact}")
            reconstruction_text = request_json(base + "/runs/warmaster-test/artifact_text?path=/work/skalathrax/reconstruction_ru.md")
            reconstruction_body = reconstruction_text.get("text", "")
            if (
                not reconstruction_text.get("ok")
                or "Output mode: research_report" not in reconstruction_body
                or "Evidence trace:" not in reconstruction_body
            ):
                raise AssertionError(f"bad expanded artifact text response: {reconstruction_text}")
            final_package = request_json(base + "/runs/warmaster-test/final?max_bytes=1000")
            reconstruction_preview = next(
                (item for item in final_package.get("files", []) if item.get("path") == "/work/skalathrax/reconstruction_ru.md"),
                {},
            )
            if (
                not final_package.get("ok")
                or final_package.get("summary", {}).get("status") != "ready"
                or final_package.get("deliverable") != "/work/skalathrax/reconstruction_ru.md"
                or final_package.get("manifest", {}).get("status") != "ready"
                or "Output mode: research_report" not in reconstruction_preview.get("preview", {}).get("text", "")
                or final_package.get("phase") != "completed"
                or final_package.get("client_action", {}).get("path") != "/runs/warmaster-test/final"
                or not final_package.get("display", {}).get("headline")
            ):
                raise AssertionError(f"bad final package response: {final_package}")
            completed_summary = request_json(base + "/runs/warmaster-test/summary")
            if (
                completed_summary.get("phase") != "completed"
                or completed_summary.get("display", {}).get("headline") != "Run completed"
                or completed_summary.get("client_action", {}).get("path") != "/runs/warmaster-test/final"
            ):
                raise AssertionError(f"completed summary did not expose orchestration envelope: {completed_summary}")
            completed_orchestration = request_json(base + "/runs/warmaster-test/orchestration?max_bytes=1000")
            if (
                completed_orchestration.get("phase") != "completed"
                or not completed_orchestration.get("decision", {}).get("can_inspect_final")
                or completed_orchestration.get("display", {}).get("headline") != "Run completed"
                or completed_orchestration.get("display", {}).get("final_deliverable") != "/work/skalathrax/reconstruction_ru.md"
                or not completed_orchestration.get("display_events")
                or completed_orchestration.get("governor_activity", {}).get("final_report", {}).get("kind") != "final_report"
                or completed_orchestration.get("final", {}).get("summary", {}).get("status") != "ready"
                or completed_orchestration.get("next_action", {}).get("kind") != "inspect_final"
                or completed_orchestration.get("client_action", {}).get("path") != "/runs/warmaster-test/final"
                or completed_orchestration.get("snapshot", {}).get("summary", {}).get("status") != "completed"
            ):
                raise AssertionError(f"completed orchestration state did not expose final package: {completed_orchestration}")
            completed_run_list = request_json(base + "/runs")
            completed_card = next((item for item in completed_run_list.get("orchestration_cards", []) if item.get("task_id") == "warmaster-test"), {})
            if (
                completed_card.get("phase") != completed_orchestration.get("phase")
                or completed_card.get("decision", {}).get("recommended_kind") != completed_orchestration.get("decision", {}).get("recommended_kind")
                or completed_card.get("display", {}).get("headline") != completed_orchestration.get("display", {}).get("headline")
                or completed_card.get("client_action", {}).get("path") != "/runs/warmaster-test/final"
            ):
                raise AssertionError(f"run card diverged from orchestration state: card={completed_card}, state={completed_orchestration}")
            completed_global_events = request_json(base + "/events?limit=200")
            completed_result_event = next(
                (
                    item
                    for item in completed_global_events.get("events", [])
                    if item.get("task_id") == "warmaster-test" and item.get("type") == "result_recorded"
                ),
                {},
            )
            if (
                completed_result_event.get("run_status") != "completed"
                or completed_result_event.get("run_next_action", {}).get("kind") != "rerun_requires_force"
                or completed_result_event.get("run_final_manifest_summary", {}).get("status") != "ready"
                or "event_review" not in completed_result_event.get("run_final_manifest_summary", {})
                or "corpus_diagnostics" not in completed_result_event.get("run_final_manifest_summary", {})
                or "corpus_requirements" not in completed_result_event.get("run_final_manifest_summary", {})
                or "package_file_errors" not in completed_result_event.get("run_final_manifest_summary", {})
            ):
                raise AssertionError(f"global run events did not expose completed run action state: {completed_global_events}")
            text_preview = request_json(base + f"/runs/warmaster-test/artifact_text?path={artifact_path}&max_bytes=8")
            if not text_preview.get("ok") or len(text_preview.get("text", "").encode("utf-8")) > 8:
                raise AssertionError(f"bad artifact preview response: {text_preview}")
            event_types = [event.get("type") for event in ledger["ledger"].get("events", [])]
            if event_types.count("task_created") != 1:
                raise AssertionError(f"ledger should preserve original task_created event: {ledger}")
            try:
                request_json(
                    base + "/runs/warmaster-test/execute_local",
                    {"timeout_sec": LOCAL_EXEC_TIMEOUT_SEC},
                    timeout=LOCAL_EXEC_TIMEOUT_SEC,
                )
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                blocked = json.loads(exc.read().decode("utf-8"))
                if "already completed" not in blocked.get("error", ""):
                    raise AssertionError(f"bad rerun block response: {blocked}")
            else:
                raise AssertionError("completed run should not execute again without force=true")
            forced = request_json(
                base + "/runs/warmaster-test/execute_local",
                {"timeout_sec": LOCAL_EXEC_TIMEOUT_SEC, "force": True},
                timeout=LOCAL_EXEC_TIMEOUT_SEC,
            )
            if not forced.get("ok"):
                raise AssertionError(f"forced rerun failed: {forced}")
            resume_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-resume-test"},
            )
            if not resume_task.get("ok"):
                raise AssertionError(f"bad resume task response: {resume_task}")
            resume_ledger_path = Path(resume_task["run_dir"]) / "task_ledger.json"
            resume_ledger = TaskLedger.load(resume_ledger_path)
            resume_ledger.set_status("interrupted")
            resume_summary = request_json(base + "/runs/warmaster-resume-test/summary")
            resume_actions = resume_summary.get("summary", {}).get("actions", {})
            if (
                not resume_actions.get("can_resume")
                or resume_actions.get("can_start")
                or resume_actions.get("can_execute")
                or resume_actions.get("next_action", {}).get("kind") != "resume"
                or resume_actions.get("next_action", {}).get("method") != "POST"
            ):
                raise AssertionError(f"interrupted run did not expose resume action: {resume_summary}")
            resume_orchestration = request_json(base + "/runs/warmaster-resume-test/orchestration")
            if (
                resume_orchestration.get("phase") != "resume_required"
                or not resume_orchestration.get("decision", {}).get("can_resume")
                or resume_orchestration.get("decision", {}).get("recommended_kind") != "resume"
            ):
                raise AssertionError(f"interrupted run did not expose orchestration resume decision: {resume_orchestration}")
            resumed = request_json(
                base + "/runs/warmaster-resume-test/resume_local",
                {"timeout_sec": LOCAL_EXEC_TIMEOUT_SEC},
                timeout=LOCAL_EXEC_TIMEOUT_SEC,
            )
            if (
                not resumed.get("ok")
                or resumed.get("phase") != "completed"
                or resumed.get("display", {}).get("headline") != "Run completed"
                or resumed.get("next_action", {}).get("kind") != "inspect_final"
                or resumed.get("client_action", {}).get("path") != "/runs/warmaster-resume-test/final"
            ):
                raise AssertionError(f"resume execution failed: {resumed}")
            resumed_ledger = request_json(base + "/runs/warmaster-resume-test/ledger")
            resumed_events = [event.get("type") for event in resumed_ledger.get("ledger", {}).get("events", [])]
            if "resume_execution_requested" not in resumed_events or resumed_ledger.get("ledger", {}).get("status") != "completed":
                raise AssertionError(f"resume execution was not recorded: {resumed_ledger}")
            partial_resume = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-partial-resume-test"},
            )
            if not partial_resume.get("ok"):
                raise AssertionError(f"bad partial resume task response: {partial_resume}")
            partial_run_dir = Path(partial_resume["run_dir"])
            partial_ledger = TaskLedger.load(partial_run_dir / "task_ledger.json")
            partial_ledger.record_step(
                "corpus_ingestion",
                "CorpusIngestor",
                "completed",
                ["/work/skalathrax/corpus_index.json"],
                "done",
            )
            partial_ledger.record_step(
                "source_discovery",
                "Lexmechanic",
                "completed",
                ["/work/skalathrax/source_map.json"],
                "done",
                {
                    "worker_view": {
                        "display": {"headline": "Lexmechanic task completed", "severity": "info"},
                        "client_action": {"kind": "inspect_task", "method": "GET", "path": "/tasks/warmaster-partial-resume-test", "body": {}},
                    }
                },
            )
            partial_ledger.set_status("interrupted")
            partial_summary = request_json(base + "/runs/warmaster-partial-resume-test/summary")
            partial_source_state = next(
                (
                    item
                    for item in partial_summary.get("summary", {}).get("progress", {}).get("step_states", [])
                    if item.get("step_id") == "source_discovery"
                ),
                {},
            )
            if (
                partial_source_state.get("worker_view", {}).get("display", {}).get("headline") != "Lexmechanic task completed"
                or partial_source_state.get("worker_view", {}).get("client_action", {}).get("path") != "/tasks/warmaster-partial-resume-test"
            ):
                raise AssertionError(f"run progress did not expose worker view state: {partial_summary}")
            partial_steps = resume_step_ids_from_run(partial_run_dir)
            if partial_steps[:3] != ["source_acquisition", "source_rendering", "fact_extraction"] or "source_discovery" in partial_steps:
                raise AssertionError(f"partial resume did not skip completed steps: {partial_steps}")
            ledger_path = run_dir / "task_ledger.json"
            ledger_payload = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger_payload.setdefault("result", {})["revision_plan"] = {
                "required": True,
                "steps": [
                    {
                        "step_id": "draft_reconstruction",
                        "worker": "ScriptoriumDaemon",
                        "reason": "test revision",
                        "source": "self_test",
                        "priority": "blocker",
                    }
                ],
            }
            write_json(ledger_path, ledger_payload)
            revision_summary = request_json(base + "/runs/warmaster-test/summary")
            revision_plan_summary = revision_summary.get("summary", {}).get("revision_plan_summary", {})
            if (
                not revision_summary.get("summary", {}).get("actions", {}).get("can_execute_revision")
                or not revision_plan_summary.get("required")
                or not revision_plan_summary.get("valid")
                or revision_plan_summary.get("step_ids") != ["draft_reconstruction"]
                or revision_plan_summary.get("workers") != ["ScriptoriumDaemon"]
            ):
                raise AssertionError(f"summary did not expose revision action: {revision_summary}")
            revision_orchestration = request_json(base + "/runs/warmaster-test/orchestration")
            if (
                revision_orchestration.get("phase") != "revision_required"
                or not revision_orchestration.get("decision", {}).get("can_execute_revision")
                or revision_orchestration.get("decision", {}).get("recommended_kind") != "execute_revision"
            ):
                raise AssertionError(f"revision-required run did not expose orchestration revision decision: {revision_orchestration}")
            failed_revision_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-failed-revision-actions-test"},
            )
            failed_revision_dir = Path(failed_revision_task["run_dir"])
            failed_revision_ledger_path = failed_revision_dir / "task_ledger.json"
            failed_revision_ledger = json.loads(failed_revision_ledger_path.read_text(encoding="utf-8"))
            failed_revision_ledger["status"] = "failed"
            failed_revision_ledger.setdefault("result", {})["revision_plan"] = ledger_payload["result"]["revision_plan"]
            write_json(failed_revision_ledger_path, failed_revision_ledger)
            failed_revision_summary = request_json(base + "/runs/warmaster-failed-revision-actions-test/summary")
            failed_revision_actions = failed_revision_summary.get("summary", {}).get("actions", {})
            if (
                failed_revision_actions.get("can_start")
                or failed_revision_actions.get("can_execute")
                or not failed_revision_actions.get("can_start_revision")
                or not failed_revision_actions.get("can_execute_revision")
            ):
                raise AssertionError(f"revision-required failed run exposed unsafe actions: {failed_revision_summary}")
            interrupted_revision_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-interrupted-revision-actions-test"},
            )
            interrupted_revision_dir = Path(interrupted_revision_task["run_dir"])
            interrupted_revision_ledger_path = interrupted_revision_dir / "task_ledger.json"
            interrupted_revision_ledger = json.loads(interrupted_revision_ledger_path.read_text(encoding="utf-8"))
            interrupted_revision_ledger["status"] = "interrupted"
            interrupted_revision_ledger.setdefault("result", {})["revision_plan"] = ledger_payload["result"]["revision_plan"]
            write_json(interrupted_revision_ledger_path, interrupted_revision_ledger)
            interrupted_revision_summary = request_json(base + "/runs/warmaster-interrupted-revision-actions-test/summary")
            interrupted_revision_actions = interrupted_revision_summary.get("summary", {}).get("actions", {})
            if (
                interrupted_revision_actions.get("can_resume")
                or not interrupted_revision_actions.get("can_start_revision")
                or interrupted_revision_actions.get("next_action", {}).get("kind") != "execute_revision"
            ):
                raise AssertionError(f"revision-required interrupted run should prefer revision action: {interrupted_revision_summary}")
            invalid_revision_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-invalid-revision-plan-test"},
            )
            invalid_revision_dir = Path(invalid_revision_task["run_dir"])
            invalid_revision_ledger_path = invalid_revision_dir / "task_ledger.json"
            invalid_revision_ledger = json.loads(invalid_revision_ledger_path.read_text(encoding="utf-8"))
            invalid_revision_ledger["status"] = "failed"
            invalid_revision_ledger.setdefault("result", {})["revision_plan"] = {
                "required": True,
                "steps": [
                    {
                        "step_id": "draft_reconstruction",
                        "worker": "Chronologis",
                        "reason": "wrong worker",
                        "source": "self_test",
                        "priority": "blocker",
                    }
                ],
            }
            write_json(invalid_revision_ledger_path, invalid_revision_ledger)
            invalid_revision_summary = request_json(base + "/runs/warmaster-invalid-revision-plan-test/summary")
            invalid_revision_actions = invalid_revision_summary.get("summary", {}).get("actions", {})
            invalid_revision_plan_summary = invalid_revision_summary.get("summary", {}).get("revision_plan_summary", {})
            if (
                not invalid_revision_summary.get("summary", {}).get("revision_plan_errors")
                or invalid_revision_plan_summary.get("valid")
                or not invalid_revision_plan_summary.get("errors")
                or invalid_revision_actions.get("can_start_revision")
                or invalid_revision_actions.get("can_execute_revision")
                or invalid_revision_actions.get("next_action", {}).get("kind") != "inspect_revision"
            ):
                raise AssertionError(f"invalid revision plan exposed revision actions: {invalid_revision_summary}")
            try:
                revision_step_ids_from_run(invalid_revision_dir)
            except ValueError as exc:
                if "revision_plan is invalid" not in str(exc):
                    raise
            else:
                raise AssertionError("invalid revision plan should not produce revision step ids")
            incomplete_downstream_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-incomplete-downstream-revision-test"},
            )
            incomplete_downstream_dir = Path(incomplete_downstream_task["run_dir"])
            incomplete_downstream_ledger_path = incomplete_downstream_dir / "task_ledger.json"
            incomplete_downstream_ledger = json.loads(incomplete_downstream_ledger_path.read_text(encoding="utf-8"))
            incomplete_downstream_ledger["status"] = "failed"
            incomplete_downstream_ledger.setdefault("result", {})["revision_plan"] = {
                "required": True,
                "steps": [
                    {
                        "step_id": "source_discovery",
                        "worker": "Lexmechanic",
                        "reason": "source map needs revision",
                        "source": "self_test",
                        "priority": "blocker",
                    }
                ],
            }
            write_json(incomplete_downstream_ledger_path, incomplete_downstream_ledger)
            incomplete_downstream_summary = request_json(base + "/runs/warmaster-incomplete-downstream-revision-test/summary")
            incomplete_downstream_errors = incomplete_downstream_summary.get("summary", {}).get("revision_plan_errors", [])
            if (
                not any("missing downstream rerun steps" in error for error in incomplete_downstream_errors)
                or incomplete_downstream_summary.get("summary", {}).get("actions", {}).get("can_execute_revision")
            ):
                raise AssertionError(f"incomplete downstream revision plan was accepted: {incomplete_downstream_summary}")
            corpus_revision_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-corpus-revision-plan-test"},
            )
            corpus_revision_dir = Path(corpus_revision_task["run_dir"])
            corpus_revision_ledger_path = corpus_revision_dir / "task_ledger.json"
            corpus_revision_ledger = json.loads(corpus_revision_ledger_path.read_text(encoding="utf-8"))
            corpus_revision_ledger["status"] = "failed"
            corpus_revision_ledger.setdefault("result", {})["revision_plan"] = {
                "required": True,
                "steps": [
                    {
                        "step_id": "corpus_ingestion",
                        "worker": "CorpusIngestor",
                        "reason": "local primary corpus must be indexed before source discovery",
                        "source": "self_test",
                        "priority": "blocker",
                    },
                    {
                        "step_id": "source_discovery",
                        "worker": "Lexmechanic",
                        "reason": "source map must include indexed local primary texts",
                        "source": "self_test",
                        "priority": "blocker",
                    },
                    {
                        "step_id": "source_acquisition",
                        "worker": "AuspexBrowser",
                        "reason": "local sources must be snapshotted before extraction",
                        "source": "self_test",
                        "priority": "blocker",
                    },
                    {
                        "step_id": "source_rendering",
                        "worker": "OcularisRenderium",
                        "reason": "render-required sources must be snapshotted before extraction",
                        "source": "self_test",
                        "priority": "blocker",
                    },
                    {
                        "step_id": "fact_extraction",
                        "worker": "NoosphericExtractor",
                        "reason": "facts must be extracted from the revised source set",
                        "source": "self_test",
                        "priority": "blocker",
                    },
                    {
                        "step_id": "structure_mapping",
                        "worker": "Chronologis",
                        "reason": "structure map must be rebuilt from revised evidence",
                        "source": "self_test",
                        "priority": "blocker",
                    },
                    {
                        "step_id": "synthesis_planning",
                        "worker": "ScriptoriumArchitect",
                        "reason": "synthesis plan must be rebuilt from revised evidence and structure",
                        "source": "self_test",
                        "priority": "blocker",
                    },
                    {
                        "step_id": "draft_reconstruction",
                        "worker": "ScriptoriumDaemon",
                        "reason": "draft must be rebuilt from revised synthesis plan",
                        "source": "self_test",
                        "priority": "blocker",
                    },
                ],
            }
            write_json(corpus_revision_ledger_path, corpus_revision_ledger)
            corpus_revision_summary = request_json(base + "/runs/warmaster-corpus-revision-plan-test/summary")
            corpus_revision_plan_summary = corpus_revision_summary.get("summary", {}).get("revision_plan_summary", {})
            if (
                not corpus_revision_summary.get("summary", {}).get("actions", {}).get("can_execute_revision")
                or not corpus_revision_plan_summary.get("valid")
                or corpus_revision_plan_summary.get("step_ids", [None])[0] != "corpus_ingestion"
                or "CorpusIngestor" not in corpus_revision_plan_summary.get("workers", [])
                or corpus_revision_summary.get("summary", {}).get("actions", {}).get("next_action", {}).get("kind") != "execute_revision"
            ):
                raise AssertionError(f"corpus-first revision plan was not executable: {corpus_revision_summary}")
            corpus_revision_steps = revision_step_ids_from_run(corpus_revision_dir)
            if corpus_revision_steps != [
                "corpus_ingestion",
                "source_discovery",
                "source_acquisition",
                "source_rendering",
                "fact_extraction",
                "structure_mapping",
                "synthesis_planning",
                "draft_reconstruction",
                "critic_review",
                "finalize",
            ]:
                raise AssertionError(f"bad corpus-first revision step expansion: {corpus_revision_steps}")
            revision_steps = revision_step_ids_from_run(run_dir)
            if revision_steps != ["draft_reconstruction", "critic_review", "finalize"]:
                raise AssertionError(f"bad revision step expansion: {revision_steps}")
            policy_order_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-revision-policy-order-test"},
            )
            policy_order_dir = Path(policy_order_task["run_dir"])
            policy_order_ledger_path = policy_order_dir / "task_ledger.json"
            policy_order_ledger = json.loads(policy_order_ledger_path.read_text(encoding="utf-8"))
            policy_order_ledger.setdefault("result", {})["revision_plan"] = ledger_payload["result"]["revision_plan"]
            write_json(policy_order_ledger_path, policy_order_ledger)
            policy_order_oversight_path = policy_order_dir / "oversight.json"
            policy_order_oversight = json.loads(policy_order_oversight_path.read_text(encoding="utf-8"))
            policy_order_oversight["revision_policy"]["final_steps"] = ["finalize", "critic_review"]
            write_json(policy_order_oversight_path, policy_order_oversight)
            policy_order_steps = revision_step_ids_from_run(policy_order_dir)
            if policy_order_steps != ["draft_reconstruction", "finalize", "critic_review"]:
                raise AssertionError(f"revision execution did not follow oversight policy order: {policy_order_steps}")
            policy_allowed_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-revision-policy-allowed-test"},
            )
            policy_allowed_dir = Path(policy_allowed_task["run_dir"])
            policy_allowed_ledger_path = policy_allowed_dir / "task_ledger.json"
            policy_allowed_ledger = json.loads(policy_allowed_ledger_path.read_text(encoding="utf-8"))
            policy_allowed_ledger["status"] = "failed"
            policy_allowed_ledger.setdefault("result", {})["revision_plan"] = ledger_payload["result"]["revision_plan"]
            write_json(policy_allowed_ledger_path, policy_allowed_ledger)
            policy_allowed_oversight_path = policy_allowed_dir / "oversight.json"
            policy_allowed_oversight = json.loads(policy_allowed_oversight_path.read_text(encoding="utf-8"))
            policy_allowed_oversight["revision_policy"]["allowed_steps"] = ["critic_review", "finalize"]
            write_json(policy_allowed_oversight_path, policy_allowed_oversight)
            policy_allowed_summary = request_json(base + "/runs/warmaster-revision-policy-allowed-test/summary")
            policy_allowed_errors = policy_allowed_summary.get("summary", {}).get("revision_plan_errors", [])
            if (
                not any("not allowed by oversight revision_policy" in error for error in policy_allowed_errors)
                or policy_allowed_summary.get("summary", {}).get("actions", {}).get("can_execute_revision")
            ):
                raise AssertionError(f"revision policy allowed_steps did not block disallowed revision: {policy_allowed_summary}")
            revision_execution = request_json(
                base + "/runs/warmaster-test/execute_revision_local",
                {"timeout_sec": LOCAL_EXEC_TIMEOUT_SEC},
                timeout=LOCAL_EXEC_TIMEOUT_SEC,
            )
            if (
                not revision_execution.get("ok")
                or not revision_execution.get("summary", {}).get("revision_execution")
                or revision_execution.get("summary", {}).get("step_ids") != revision_steps
            ):
                raise AssertionError(f"bad revision execution response: {revision_execution}")
            revision_ledger = request_json(base + "/runs/warmaster-test/ledger")
            revision_event = next(
                (
                    event
                    for event in revision_ledger.get("ledger", {}).get("events", [])
                    if event.get("type") == "revision_execution_started"
                ),
                None,
            )
            if not revision_event or revision_event.get("payload", {}).get("step_ids") != revision_steps:
                raise AssertionError(f"revision execution event missing from ledger: {revision_ledger}")
            loop_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-research-loop-test"},
            )
            if not loop_task.get("ok"):
                raise AssertionError(f"bad research loop task response: {loop_task}")
            loop_result = request_json(
                base + "/runs/warmaster-research-loop-test/research_loop_local",
                {"timeout_sec": LOCAL_EXEC_TIMEOUT_SEC, "max_revision_cycles": 2},
                timeout=LOCAL_EXEC_TIMEOUT_SEC,
            )
            if (
                not loop_result.get("ok")
                or loop_result.get("phase") != "completed"
                or not loop_result.get("cycles")
                or loop_result.get("run_summary", {}).get("status") != "completed"
                or loop_result.get("decision", {}).get("can_inspect_final") is not True
            ):
                raise AssertionError(f"research loop did not complete a ready run: {loop_result}")
            loop_ledger = request_json(base + "/runs/warmaster-research-loop-test/ledger")
            loop_events = [event.get("type") for event in loop_ledger.get("ledger", {}).get("events", [])]
            if "research_loop_started" not in loop_events or "research_loop_finished" not in loop_events:
                raise AssertionError(f"research loop events missing from ledger: {loop_ledger}")
            repeated_loop_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-research-loop-repeat-test"},
            )
            repeated_loop_dir = Path(repeated_loop_task["run_dir"])
            repeated_loop_ledger_path = repeated_loop_dir / "task_ledger.json"
            repeated_loop_ledger = json.loads(repeated_loop_ledger_path.read_text(encoding="utf-8"))
            repeated_loop_ledger["status"] = "failed"
            repeated_loop_ledger.setdefault("result", {})["revision_plan"] = ledger_payload["result"]["revision_plan"]
            write_json(repeated_loop_ledger_path, repeated_loop_ledger)
            try:
                repeated_result = request_json(
                    base + "/runs/warmaster-research-loop-repeat-test/research_loop_local",
                    {"timeout_sec": LOCAL_EXEC_TIMEOUT_SEC, "max_revision_cycles": 0},
                    timeout=LOCAL_EXEC_TIMEOUT_SEC,
                )
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                repeated_result = json.loads(exc.read().decode("utf-8"))
            if repeated_result.get("ok") or repeated_result.get("stop_reason") != "revision_cycle_limit":
                raise AssertionError(f"research loop did not honor revision cycle limit: {repeated_result}")
            unsafe_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-unsafe-workspace-test"},
            )
            if not unsafe_task.get("ok"):
                raise AssertionError(f"bad unsafe workspace task response: {unsafe_task}")
            try:
                request_json(
                    base + "/runs/warmaster-unsafe-workspace-test/execute_http",
                    {"timeout_sec": 180, "host": "example.com"},
                    timeout=180,
                )
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
                bad_host = json.loads(exc.read().decode("utf-8"))
                if "loopback" not in bad_host.get("error", ""):
                    raise AssertionError(f"bad execute_http host rejection: {bad_host}")
            else:
                raise AssertionError("execute_http should reject non-loopback host")
            try:
                request_json(
                    base + "/runs/warmaster-unsafe-workspace-test/execute_local",
                    {"timeout_sec": 180, "workspace_root": str(Path(temp_dir) / "outside-work")},
                    timeout=180,
                )
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
                unsafe_workspace = json.loads(exc.read().decode("utf-8"))
                if "path must stay inside run_dir" not in unsafe_workspace.get("error", ""):
                    raise AssertionError(f"bad unsafe workspace response: {unsafe_workspace}")
            else:
                raise AssertionError("execute_local should reject workspace_root outside run_dir")
            background_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-background-test"},
            )
            if not background_task.get("ok"):
                raise AssertionError(f"bad background task response: {background_task}")
            started = request_json(
                base + "/runs/warmaster-background-test/start_local",
                {"timeout_sec": LOCAL_EXEC_TIMEOUT_SEC},
            )
            if (
                started.get("status") != "started"
                or started.get("next_action", {}).get("kind") != "poll"
                or started.get("client_action", {}).get("path") != "/runs/warmaster-background-test/snapshot"
            ):
                raise AssertionError(f"background start failed: {started}")
            for _ in range(max(150, LOCAL_EXEC_TIMEOUT_SEC * 5)):
                background_ledger = request_json(base + "/runs/warmaster-background-test/ledger")
                if background_ledger["ledger"].get("status") == "completed":
                    break
                time.sleep(0.2)
            else:
                raise AssertionError(f"background run did not complete: {background_ledger}")
            background_events = [event.get("type") for event in background_ledger["ledger"].get("events", [])]
            if "background_start_requested" not in background_events:
                raise AssertionError(f"background start event missing: {background_ledger}")
            cancel_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-cancel-test"},
            )
            if not cancel_task.get("ok"):
                raise AssertionError(f"bad cancel task response: {cancel_task}")
            cancel_calls: list[str] = []
            worker_server = ThreadingHTTPServer(("127.0.0.1", 0), make_cancel_handler(cancel_calls))
            worker_thread = threading.Thread(target=worker_server.serve_forever, daemon=True)
            worker_thread.start()
            try:
                patch_dispatch_ports(Path(cancel_task["run_dir"]), worker_server.server_port)
                cancelled = request_json(base + "/runs/warmaster-cancel-test/cancel", {"reason": "test"})
            finally:
                worker_server.shutdown()
                worker_thread.join(timeout=120)
            if (
                not cancelled.get("ok")
                or not cancelled["ledger"].get("cancel_requested")
                or cancelled.get("next_action", {}).get("kind") != "poll"
                or cancelled.get("client_action", {}).get("path") != "/runs/warmaster-cancel-test/snapshot"
            ):
                raise AssertionError(f"bad cancel response: {cancelled}")
            if not cancel_calls or not any(item.get("ok") for item in cancelled.get("worker_cancellations", [])):
                raise AssertionError(f"cancel was not propagated to worker tasks: {cancelled}")
            try:
                request_json(base + "/runs/warmaster-cancel-test/execute_local", {"timeout_sec": 180}, timeout=180)
            except urllib.error.HTTPError as exc:
                if exc.code != 500:
                    raise
                cancelled_execution = json.loads(exc.read().decode("utf-8"))
                if not cancelled_execution.get("summary", {}).get("cancelled"):
                    raise AssertionError(f"bad cancelled execution response: {cancelled_execution}")
            else:
                raise AssertionError("cancelled run should not execute successfully")
            stale_dir = run_root / "stale-test"
            stale_dir.mkdir(parents=True, exist_ok=True)
            stale = TaskLedger.create(stale_dir / "task_ledger.json", "stale-test", "goal", "IskandarKhayon")
            stale.set_status("running")
            recovered = request_json(base + "/recover_stale", {}, timeout=10)
            if not recovered.get("ok") or recovered["recovered"][0].get("status") != "interrupted":
                raise AssertionError(f"bad stale recovery: {recovered}")
            if not recovered["recovered"][0].get("actions", {}).get("can_resume"):
                raise AssertionError(f"recovered run did not expose resume action: {recovered}")
            recovery_state = request_json(base + "/state?run_limit=5")
            if (
                recovery_state.get("recovery", {}).get("recoverable", 0) < 1
                or "stale-test" not in recovery_state.get("recovery", {}).get("task_ids", [])
                or not any(
                    item.get("task_id") == "stale-test"
                    and item.get("next_action", {}).get("kind") == "resume"
                    and item.get("client_action", {}).get("path") == "/runs/stale-test/start_resume_http"
                    and item.get("display", {}).get("headline") == "Recovery needs inspection"
                    for item in recovery_state.get("recovery", {}).get("candidates", [])
                )
            ):
                raise AssertionError(f"state did not expose recoverable interrupted runs: {recovery_state}")
            recovery_runs = request_json(base + "/runs?limit=5")
            if recovery_runs.get("recovery", {}).get("recoverable", 0) < 1:
                raise AssertionError(f"run listing did not expose recovery summary: {recovery_runs}")
            recovery_endpoint = request_json(base + "/recovery")
            if (
                not recovery_endpoint.get("ok")
                or "stale-test" not in recovery_endpoint.get("recovery", {}).get("task_ids", [])
            ):
                raise AssertionError(f"recovery endpoint did not expose stale run: {recovery_endpoint}")
            stale_recovery = next(
                (item for item in recovery_endpoint.get("recovery", {}).get("candidates", []) if item.get("task_id") == "stale-test"),
                {},
            )
            if stale_recovery.get("resume_ready") or not stale_recovery.get("resume_errors"):
                raise AssertionError(f"recovery endpoint should diagnose malformed stale runs: {recovery_endpoint}")
            if stale_recovery.get("client_action", {}).get("path") != "/runs/stale-test/start_resume_http":
                raise AssertionError(f"recovery endpoint did not expose executable client action: {recovery_endpoint}")
            if stale_recovery.get("display", {}).get("severity") != "warning":
                raise AssertionError(f"recovery endpoint did not expose blocked display state: {recovery_endpoint}")
            bulk_task = request_json(
                base + "/task",
                {"message": "Исследуй Скалатракс и сделай report.", "task_id": "warmaster-bulk-recovery-test"},
            )
            if not bulk_task.get("ok"):
                raise AssertionError(f"bad bulk recovery task response: {bulk_task}")
            bulk_ledger_path = Path(bulk_task["run_dir"]) / "task_ledger.json"
            TaskLedger.load(bulk_ledger_path).set_status("interrupted")
            recovery_with_bulk = request_json(base + "/recovery")
            bulk_recovery = next(
                (item for item in recovery_with_bulk.get("recovery", {}).get("candidates", []) if item.get("task_id") == "warmaster-bulk-recovery-test"),
                {},
            )
            if (
                recovery_with_bulk.get("recovery", {}).get("startable", 0) < 1
                or not bulk_recovery.get("resume_ready")
                or not bulk_recovery.get("pending_step_ids")
                or bulk_recovery.get("client_action", {}).get("path") != "/runs/warmaster-bulk-recovery-test/start_resume_http"
                or bulk_recovery.get("display", {}).get("headline") != "Recovery is ready"
            ):
                raise AssertionError(f"recovery endpoint should expose startable resume packages: {recovery_with_bulk}")
            bulk_timeout_sec = LOCAL_EXEC_TIMEOUT_SEC
            bulk_started = request_json(base + "/recovery/start_resume_local", {"timeout_sec": bulk_timeout_sec}, timeout=120)
            if (
                bulk_started.get("started", 0) < 1
                or not any(item.get("task_id") == "warmaster-bulk-recovery-test" and item.get("ok") for item in bulk_started.get("results", []))
                or not any(item.get("task_id") == "stale-test" and not item.get("ok") for item in bulk_started.get("results", []))
                or not any(
                    item.get("task_id") == "warmaster-bulk-recovery-test"
                    and item.get("client_action", {}).get("path") == "/runs/warmaster-bulk-recovery-test/snapshot"
                    for item in bulk_started.get("results", [])
                )
                or not any(
                    item.get("task_id") == "stale-test"
                    and item.get("client_action", {}).get("path") == "/runs/stale-test/package"
                    for item in bulk_started.get("results", [])
                )
            ):
                raise AssertionError(f"bulk recovery did not start valid runs and skip malformed runs: {bulk_started}")
            for _ in range(max(300, bulk_timeout_sec * 5)):
                bulk_ledger = request_json(base + "/runs/warmaster-bulk-recovery-test/ledger")
                if bulk_ledger["ledger"].get("status") in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError(f"bulk recovery run did not complete: {bulk_ledger}")
            if bulk_ledger["ledger"].get("status") != "completed":
                raise AssertionError(f"bulk recovery run did not complete successfully: {bulk_ledger}")
            bulk_events = [event.get("type") for event in bulk_ledger["ledger"].get("events", [])]
            if "resume_execution_requested" not in bulk_events or "background_start_requested" not in bulk_events:
                raise AssertionError(f"bulk recovery did not record resume/background events: {bulk_ledger}")
        finally:
            server.shutdown()
            thread.join(timeout=120)
        startup_run = warmaster_gateway.prepare_task(
            "Исследуй Скалатракс и сделай report.",
            "warmaster-startup-recover-test",
            run_root,
        )
        if not startup_run.get("ok"):
            raise AssertionError(f"bad startup recovery task: {startup_run}")
        startup_ledger_path = Path(startup_run["run_dir"]) / "task_ledger.json"
        TaskLedger.load(startup_ledger_path).set_status("running")
        recovered_on_start = prepare_run_root(run_root)
        if not any(item.get("task_id") == "warmaster-startup-recover-test" and item.get("status") == "interrupted" for item in recovered_on_start):
            raise AssertionError(f"startup recovery did not report interrupted run: {recovered_on_start}")
        startup_ledger_after = TaskLedger.load(startup_ledger_path).to_dict()
        if startup_ledger_after.get("status") != "interrupted":
            raise AssertionError(f"startup recovery did not persist interrupted status: {startup_ledger_after}")
        if old_corpus_dir is None:
            os.environ.pop("SHUSHUNYA_CORPUS_DIR", None)
        else:
            os.environ["SHUSHUNYA_CORPUS_DIR"] = old_corpus_dir
    print("[ok] Warmaster gateway")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
