#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from start_brigade import (
    CommandSpec,
    brigade_commands,
    brigade_plan,
    canonical_run_root,
    command_start_order,
    health_payload_is_ready,
    port_preflight,
    registry_port,
    startup_stages,
    supervise_processes,
    url_is_ready,
    wait_for_urls,
    warband_service_plan,
    worker_service_plan,
)


class HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/health":
            payload = {"ok": True}
        elif self.path == "/health?vm=1":
            payload = {
                "status": "ok",
                "service": "Skitarii",
                "vm_alive": True,
                "process_boundary_ready": True,
            }
        elif self.path == "/health?vm=missing-boundary":
            payload = {
                "status": "ok",
                "service": "Skitarii",
                "vm_alive": True,
            }
        else:
            payload = {"ok": False}
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200 if payload.get("ok") is not False else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    repo_root = next(
        candidate
        for candidate in Path(__file__).resolve().parents
        if (candidate / "EyeOfTerror" / "model_brain.py").is_file()
    )
    commands = brigade_commands(
        repo_root=repo_root,
        host="127.0.0.1",
        workspace_root=Path("runtime/test-work"),
        warmaster_run_root=Path("runtime/test-warmaster-runs"),
        iskandar_run_root=Path("runtime/test-iskandar-runs"),
    )
    rendered = "\n".join(command.rendered() for command in commands)
    expected_warmaster_root = canonical_run_root(
        repo_root,
        Path("runtime/test-warmaster-runs"),
    )
    names = {command.name for command in commands}
    expected_names = {"mechanicum-workers", "iskandar-khayon", "ceraxia", "warmaster-gateway"}
    if names != expected_names:
        raise AssertionError(f"brigade command names mismatch: {names}")
    if registry_port(repo_root, "eye_of_terror", "WarmasterGateway", 0) != 7000 or registry_port(repo_root, "eye_of_terror", "IskandarKhayon", 0) != 7101:
        raise AssertionError("brigade launcher did not read top-level ports from registry")
    ports_by_name = {command.name: command.port for command in commands}
    if ports_by_name.get("warmaster-gateway") != registry_port(repo_root, "eye_of_terror", "WarmasterGateway", 0):
        raise AssertionError(f"warmaster command port drifted from registry: {ports_by_name}")
    if ports_by_name.get("iskandar-khayon") != registry_port(repo_root, "eye_of_terror", "IskandarKhayon", 0):
        raise AssertionError(f"iskandar command port drifted from registry: {ports_by_name}")
    if ports_by_name.get("ceraxia") != registry_port(repo_root, "eye_of_terror", "Ceraxia", 0):
        raise AssertionError(f"ceraxia command port drifted from registry: {ports_by_name}")
    required = [
        "--governor-transport http",
        "eye_of_terror.inner_circle.iskandar_service",
        "eye_of_terror.inner_circle.ceraxia_service",
        "eye_of_terror.warmaster_gateway",
        "start_all_workers.py",
    ]
    missing = [item for item in required if item not in rendered]
    if missing:
        raise AssertionError(f"brigade command plan missing entries: {missing}\n{rendered}")
    commands_by_name = {command.name: command for command in commands}
    gateway = commands_by_name["warmaster-gateway"]
    ceraxia = commands_by_name["ceraxia"]
    gateway_run_root = gateway.command[gateway.command.index("--run-root") + 1]
    ceraxia_run_root = ceraxia.command[ceraxia.command.index("--default-run-root") + 1]
    if gateway_run_root != str(expected_warmaster_root):
        raise AssertionError(f"gateway run root is not canonical: {gateway.command}")
    if ceraxia_run_root != gateway_run_root:
        raise AssertionError(
            "Ceraxia and Gateway command lines split the run universe: "
            f"gateway={gateway.command} ceraxia={ceraxia.command}"
        )
    if (
        gateway.env.get("WARMMASTER_RUN_ROOT") != str(expected_warmaster_root)
        or ceraxia.env.get("WARMMASTER_RUN_ROOT") != str(expected_warmaster_root)
    ):
        raise AssertionError(
            "Gateway and Ceraxia must share the exact canonical WARMMASTER_RUN_ROOT: "
            f"gateway={gateway.env} ceraxia={ceraxia.env}"
        )
    try:
        brigade_commands(
            repo_root=repo_root,
            host="127.0.0.1",
            workspace_root=Path("runtime/test-work"),
            warmaster_run_root=Path("runtime/test-warmaster-runs"),
            iskandar_run_root=Path("runtime/test-iskandar-runs"),
            ceraxia_run_root=Path("runtime/a-different-ceraxia-world"),
        )
    except ValueError as exc:
        if "share one canonical run root" not in str(exc):
            raise
    else:
        raise AssertionError("launcher accepted split Ceraxia/Gateway run roots")
    if "SkitariiWarband" in names or "Skitarii" in rendered or "7200" in rendered:
        raise AssertionError("externally supervised Skitarii must not be launched as a generic brigade process")
    if not all(
        not any(key.endswith("_MODEL_ENABLED") for key in command.env)
        and command.env.get("EYE_MODEL_BASE_URL", "").endswith("/v1")
        for command in commands
    ):
        raise AssertionError(f"brigade commands must require model brain without an enable switch: {[command.env for command in commands]}")
    plan = brigade_plan(
        repo_root=repo_root,
        host="127.0.0.1",
        workspace_root=Path("runtime/test-work"),
        warmaster_run_root=Path("runtime/test-warmaster-runs"),
        iskandar_run_root=Path("runtime/test-iskandar-runs"),
    )
    if plan.get("mode") != "service-separated" or plan.get("ports", {}).get("warmaster_gateway") != 7000 or plan.get("ports", {}).get("ceraxia") != 7104:
        raise AssertionError(f"bad brigade JSON plan: {plan}")
    if plan.get("warmaster_run_root") != str(expected_warmaster_root):
        raise AssertionError(f"brigade plan exposes a non-canonical run root: {plan}")
    if plan.get("model_brain", {}).get("required") is not True or not plan.get("model_brain", {}).get("model"):
        raise AssertionError(f"brigade plan did not expose model brain defaults: {plan}")
    service_names = {item.get("name") for item in plan.get("services", []) if isinstance(item, dict)}
    if service_names != expected_names:
        raise AssertionError(f"bad brigade service names in JSON plan: {plan}")
    worker_contract = plan.get("worker_contract", {})
    if (
        not isinstance(worker_contract, dict)
        or worker_contract.get("kind") != "eye_of_terror_brigade_worker_contract"
        or worker_contract.get("contract_version") != 1
        or "warmaster-gateway" not in worker_contract.get("consumers", [])
    ):
        raise AssertionError(f"bad brigade worker contract header: {worker_contract}")
    if "health_url" not in worker_contract.get("mechanicum_worker_required_fields", []):
        raise AssertionError(f"brigade worker contract must require worker health_url: {worker_contract}")
    if (
        "supervisor" not in worker_contract.get("external_warband_required_fields", [])
        or worker_contract.get("external_warband_count") != 1
    ):
        raise AssertionError(f"brigade contract must expose one externally supervised warband: {worker_contract}")
    contract_edges = {item.get("service"): item.get("depends_on") for item in worker_contract.get("dependency_edges", []) if isinstance(item, dict)}
    if contract_edges.get("warmaster-gateway") != ["mechanicum-workers", "iskandar-khayon", "ceraxia"]:
        raise AssertionError(f"brigade worker contract dependency edges drifted: {worker_contract}")
    dependencies = plan.get("dependencies", {})
    if dependencies.get("warmaster-gateway") != ["mechanicum-workers", "iskandar-khayon", "ceraxia"]:
        raise AssertionError(f"bad brigade dependencies: {plan}")
    stages = plan.get("startup_stages", [])
    if (
        len(stages) != 2
        or set(stages[0].get("services", [])) != {"mechanicum-workers", "iskandar-khayon", "ceraxia"}
        or stages[1].get("services") != ["warmaster-gateway"]
        or "http://127.0.0.1:7002/health" not in stages[0].get("health_urls", [])
        or "http://127.0.0.1:7200/health?vm=1" not in stages[0].get("health_urls", [])
        or "http://127.0.0.1:7000/health" not in stages[1].get("health_urls", [])
    ):
        raise AssertionError(f"bad brigade startup stages: {plan}")
    if [command.name for command in command_start_order(commands, stages)] != ["ceraxia", "iskandar-khayon", "mechanicum-workers", "warmaster-gateway"]:
        raise AssertionError(f"bad command start order: {stages}")
    try:
        command_start_order(commands, [{"stage": 1, "services": ["warmaster-gateway"], "health_urls": []}])
    except ValueError:
        pass
    else:
        raise AssertionError("command start order should reject incomplete startup stages")
    try:
        startup_stages(
            [
                CommandSpec("a", "fixture", "127.0.0.1", 1, ["b"], "", [sys.executable, "-c", "pass"], {}),
                CommandSpec("b", "fixture", "127.0.0.1", 2, ["a"], "", [sys.executable, "-c", "pass"], {}),
            ],
            [],
        )
    except ValueError:
        pass
    else:
        raise AssertionError("startup stages should reject cyclic dependencies")
    health_urls = plan.get("health_urls", {})
    if health_urls.get("warmaster-gateway") != "http://127.0.0.1:7000/health" or health_urls.get("iskandar-khayon") != "http://127.0.0.1:7101/health":
        raise AssertionError(f"bad brigade health URLs: {plan}")
    if health_urls.get("ceraxia") != "http://127.0.0.1:7104/health":
        raise AssertionError(f"bad Ceraxia health URL: {plan}")
    if health_urls.get("Lexmechanic") != "http://127.0.0.1:7002/health":
        raise AssertionError(f"bad worker health URLs: {plan}")
    if health_urls.get("SkitariiWarband") != "http://127.0.0.1:7200/health?vm=1":
        raise AssertionError(f"bad native warband health URL: {plan}")
    warbands = plan.get("warbands", [])
    if warbands != [
        {
            "name": "SkitariiWarband",
            "role": "native coding warband for detailed planning, implementation, verification, and repair",
            "port": 7200,
            "path": "EyeOfTerror/Mechanicum/Skitarii",
            "supervisor": "skitarii-warband.service",
            "health_url": "http://127.0.0.1:7200/health?vm=1",
            "lifecycle": "externally_managed",
        }
    ]:
        raise AssertionError(f"bad native warband plan: {warbands}")
    if plan.get("ports", {}).get("warbands") != {"SkitariiWarband": 7200}:
        raise AssertionError(f"bad native warband port plan: {plan}")
    worker_names = {item.get("name") for item in plan.get("mechanicum_workers", []) if isinstance(item, dict)}
    required_workers = {
        "CorpusIngestor",
        "Lexmechanic",
        "AuspexBrowser",
        "OcularisRenderium",
        "NoosphericExtractor",
        "Chronologis",
        "ScriptoriumArchitect",
        "ScriptoriumDaemon",
        "ReductorVerifier",
        "FabricatorFinalis",
        "Promptwright",
        "ModelQuartermaster",
        "ForgeDispatcher",
        "ImageVerifier",
        "ArtifactFinalis",
    }
    if not required_workers.issubset(worker_names):
        raise AssertionError(f"brigade JSON plan missing workers: {required_workers - worker_names}")
    worker_ports = plan.get("ports", {}).get("mechanicum_workers", [])
    if worker_ports != [7002, 7003, 7004, 7005, 7006, 7007, 7009, 7012, 7013, 7021, 7022, 7023, 7024, 7025, 7026]:
        raise AssertionError(f"bad worker port plan: {worker_ports}")
    if "http://127.0.0.1:7002/health" not in plan.get("readiness_urls", []):
        raise AssertionError(f"worker readiness URL missing from plan: {plan}")
    if "http://127.0.0.1:7200/health?vm=1" not in plan.get("readiness_urls", []):
        raise AssertionError(f"native warband readiness URL missing from plan: {plan}")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        services_path = temp_root / "LegacyMechanicum" / "worker_services.json"
        services_path.parent.mkdir(parents=True, exist_ok=True)
        services_path.write_text(json.dumps({"BrokenWorker": {"port": 7002, "module_path": "LegacyMechanicum/BrokenWorker"}}), encoding="utf-8")
        try:
            worker_service_plan(temp_root, "127.0.0.1")
        except ValueError as exc:
            if "module" not in str(exc):
                raise
        else:
            raise AssertionError("brigade worker service plan accepted an incomplete worker service entry")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        registry_path = temp_root / "EyeOfTerror" / "Warmaster" / "registry" / "ports.json"
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(json.dumps({"warbands": {"7200": {"name": "SkitariiWarband"}}}), encoding="utf-8")
        try:
            warband_service_plan(temp_root, "127.0.0.1")
        except ValueError as exc:
            if "incomplete" not in str(exc):
                raise
        else:
            raise AssertionError("brigade warband plan accepted an incomplete external service entry")
    server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        readiness = wait_for_urls([f"http://127.0.0.1:{server.server_port}/health"], timeout_sec=2.0)
        if not readiness.get("ok"):
            raise AssertionError(f"readiness helper did not observe health endpoint: {readiness}")
        skitarii_url = f"http://127.0.0.1:{server.server_port}/health?vm=1"
        if not url_is_ready(skitarii_url):
            raise AssertionError("readiness helper rejected a fully ready Skitarii health payload")
        incomplete_url = (
            f"http://127.0.0.1:{server.server_port}/health?vm=missing-boundary"
        )
        if url_is_ready(incomplete_url):
            raise AssertionError("readiness helper accepted Skitarii without process boundary")
        if health_payload_is_ready(
            skitarii_url,
            {"status": "ok", "vm_alive": True, "process_boundary_ready": False},
        ):
            raise AssertionError("readiness contract accepted a false Skitarii process boundary")
    finally:
        server.shutdown()
        thread.join(timeout=5)
    busy_server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    busy_thread = threading.Thread(target=busy_server.serve_forever, daemon=True)
    busy_thread.start()
    try:
        preflight = port_preflight("127.0.0.1", [busy_server.server_port])
        if preflight.get("ok") or busy_server.server_port not in preflight.get("busy", []):
            raise AssertionError(f"port preflight did not detect busy port: {preflight}")
    finally:
        busy_server.shutdown()
        busy_thread.join(timeout=5)
    short = subprocess.Popen([sys.executable, "-c", "raise SystemExit(7)"])
    long = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    code = supervise_processes([short, long], poll_interval_sec=0.05)
    if code != 7 or long.poll() is None:
        raise AssertionError(f"supervisor did not fail fast and terminate peers: code={code} long={long.poll()}")
    print("[ok] EyeOfTerror brigade launcher")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
