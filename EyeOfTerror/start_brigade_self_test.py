#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from start_brigade import CommandSpec, brigade_commands, brigade_plan, port_preflight, startup_stages, supervise_processes, wait_for_urls


class HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        payload = {"ok": self.path == "/health"}
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200 if payload["ok"] else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    commands = brigade_commands(
        repo_root=repo_root,
        host="127.0.0.1",
        workspace_root=Path("runtime/test-work"),
        warmaster_run_root=Path("runtime/test-warmaster-runs"),
        iskandar_run_root=Path("runtime/test-iskandar-runs"),
    )
    rendered = "\n".join(command.rendered() for command in commands)
    names = {command.name for command in commands}
    expected_names = {"mechanicum-workers", "iskandar-khayon", "warmaster-gateway"}
    if names != expected_names:
        raise AssertionError(f"brigade command names mismatch: {names}")
    required = [
        "--governor-transport http",
        "eye_of_terror.inner_circle.iskandar_service",
        "eye_of_terror.warmaster_gateway",
        "Mechanicum/start_all_workers.py",
    ]
    missing = [item for item in required if item not in rendered]
    if missing:
        raise AssertionError(f"brigade command plan missing entries: {missing}\n{rendered}")
    plan = brigade_plan(
        repo_root=repo_root,
        host="127.0.0.1",
        workspace_root=Path("runtime/test-work"),
        warmaster_run_root=Path("runtime/test-warmaster-runs"),
        iskandar_run_root=Path("runtime/test-iskandar-runs"),
    )
    if plan.get("mode") != "service-separated" or plan.get("ports", {}).get("warmaster_gateway") != 7000:
        raise AssertionError(f"bad brigade JSON plan: {plan}")
    service_names = {item.get("name") for item in plan.get("services", []) if isinstance(item, dict)}
    if service_names != expected_names:
        raise AssertionError(f"bad brigade service names in JSON plan: {plan}")
    dependencies = plan.get("dependencies", {})
    if dependencies.get("warmaster-gateway") != ["mechanicum-workers", "iskandar-khayon"]:
        raise AssertionError(f"bad brigade dependencies: {plan}")
    stages = plan.get("startup_stages", [])
    if (
        len(stages) != 2
        or set(stages[0].get("services", [])) != {"mechanicum-workers", "iskandar-khayon"}
        or stages[1].get("services") != ["warmaster-gateway"]
        or "http://127.0.0.1:7002/health" not in stages[0].get("health_urls", [])
        or "http://127.0.0.1:7000/health" not in stages[1].get("health_urls", [])
    ):
        raise AssertionError(f"bad brigade startup stages: {plan}")
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
    if health_urls.get("Lexmechanic") != "http://127.0.0.1:7002/health":
        raise AssertionError(f"bad worker health URLs: {plan}")
    worker_names = {item.get("name") for item in plan.get("mechanicum_workers", []) if isinstance(item, dict)}
    required_workers = {"Lexmechanic", "AuspexBrowser", "NoosphericExtractor", "Chronologis", "ScriptoriumDaemon", "ReductorVerifier", "FabricatorFinalis"}
    if not required_workers.issubset(worker_names):
        raise AssertionError(f"brigade JSON plan missing workers: {required_workers - worker_names}")
    worker_ports = plan.get("ports", {}).get("mechanicum_workers", [])
    if worker_ports != [7002, 7003, 7004, 7005, 7006, 7007, 7009]:
        raise AssertionError(f"bad worker port plan: {worker_ports}")
    if "http://127.0.0.1:7002/health" not in plan.get("readiness_urls", []):
        raise AssertionError(f"worker readiness URL missing from plan: {plan}")
    server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        readiness = wait_for_urls([f"http://127.0.0.1:{server.server_port}/health"], timeout_sec=2.0)
        if not readiness.get("ok"):
            raise AssertionError(f"readiness helper did not observe health endpoint: {readiness}")
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
