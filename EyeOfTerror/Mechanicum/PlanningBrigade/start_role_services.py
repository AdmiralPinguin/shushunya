#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from planning_packet_contract import CONTRACT_VERSION, ROLE_ORDER
from role_service import ROOT, load_json


def service_specs(host: str = "127.0.0.1") -> list[dict[str, Any]]:
    contracts = load_json(ROOT / "service_contracts.json")
    services = contracts.get("services") if isinstance(contracts.get("services"), list) else []
    by_name = {
        str(service.get("name") or ""): service
        for service in services
        if isinstance(service, dict) and service.get("name")
    }
    specs: list[dict[str, Any]] = []
    for role in ROLE_ORDER:
        service = by_name.get(role)
        if not service:
            raise ValueError(f"service_contracts.json missing service for {role}")
        port = int(service.get("port") or 0)
        if port <= 0:
            raise ValueError(f"service_contracts.json has invalid port for {role}")
        specs.append(
            {
                "role": role,
                "host": host,
                "port": port,
                "base_url": f"http://{host}:{port}",
                "health_url": f"http://{host}:{port}/health",
                "capabilities_url": f"http://{host}:{port}/capabilities",
                "plan_url": f"http://{host}:{port}/plan",
                "command": [
                    sys.executable,
                    str(ROOT / "role_service.py"),
                    "--role",
                    role,
                    "--host",
                    host,
                    "--port",
                    str(port),
                ],
                "may_mutate_source": bool(service.get("may_mutate_source")),
                "handoff_to": str(service.get("handoff_to") or ""),
            }
        )
    return specs


def build_supervisor_manifest(host: str = "127.0.0.1") -> dict[str, Any]:
    specs = service_specs(host)
    ports = [spec["port"] for spec in specs]
    return {
        "kind": "planning_brigade_role_service_supervisor_manifest",
        "contract_version": CONTRACT_VERSION,
        "host": host,
        "status": "ready_to_start",
        "role_order": ROLE_ORDER,
        "service_count": len(specs),
        "ports": ports,
        "ports_unique": len(ports) == len(set(ports)),
        "read_only": all(spec["may_mutate_source"] is False for spec in specs),
        "services": specs,
    }


def health_ok(url: str, timeout_sec: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout_sec) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return False
    return isinstance(payload, dict) and payload.get("ok") is True


def wait_until_healthy(specs: list[dict[str, Any]], timeout_sec: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    states = {spec["role"]: False for spec in specs}
    while time.monotonic() < deadline:
        for spec in specs:
            if not states[spec["role"]]:
                states[spec["role"]] = health_ok(spec["health_url"])
        if all(states.values()):
            break
        time.sleep(0.2)
    return {
        "kind": "planning_brigade_role_service_health_wait",
        "contract_version": CONTRACT_VERSION,
        "status": "healthy" if all(states.values()) else "unhealthy",
        "services": [{"role": role, "healthy": healthy} for role, healthy in states.items()],
    }


def start_services(host: str, wait_timeout_sec: float) -> int:
    manifest = build_supervisor_manifest(host)
    if not manifest["ports_unique"] or not manifest["read_only"]:
        print(json.dumps({**manifest, "status": "blocked"}, ensure_ascii=False, indent=2), flush=True)
        return 2
    processes: list[subprocess.Popen[str]] = []
    try:
        for spec in manifest["services"]:
            processes.append(subprocess.Popen(spec["command"], text=True))
        health = wait_until_healthy(manifest["services"], timeout_sec=wait_timeout_sec)
        print(json.dumps({**manifest, "status": health["status"], "health": health}, ensure_ascii=False, indent=2), flush=True)
        if health["status"] != "healthy":
            return 2
        while all(process.poll() is None for process in processes):
            time.sleep(1.0)
        return 2
    except KeyboardInterrupt:
        return 130
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="Start all PlanningBrigade role HTTP services.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--manifest", action="store_true", help="Print the supervisor manifest without starting services.")
    parser.add_argument("--wait-timeout-sec", type=float, default=10.0)
    args = parser.parse_args()
    if args.manifest:
        print(json.dumps(build_supervisor_manifest(args.host), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    return start_services(args.host, args.wait_timeout_sec)


if __name__ == "__main__":
    raise SystemExit(main())
