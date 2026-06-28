#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandSpec:
    name: str
    role: str
    host: str
    port: int
    depends_on: list[str]
    health_url: str
    command: list[str]
    env: dict[str, str]

    def rendered(self) -> str:
        prefixes = [f"{key}={value}" for key, value in sorted(self.env.items())]
        return " ".join(prefixes + self.command)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "role": self.role,
            "host": self.host,
            "port": self.port,
            "depends_on": self.depends_on,
            "health_url": self.health_url,
            "command": self.command,
            "env": self.env,
            "rendered": self.rendered(),
        }


def pythonpath(repo_root: Path) -> str:
    return os.pathsep.join([str(repo_root / "EyeOfTerror"), str(repo_root / "Mechanicum")])


def worker_service_plan(repo_root: Path, host: str) -> list[dict[str, object]]:
    path = repo_root / "Mechanicum" / "worker_services.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("worker_services.json must contain an object")
    workers: list[dict[str, object]] = []
    for name, item in sorted(payload.items(), key=lambda pair: (int(pair[1].get("port") or 0), pair[0]) if isinstance(pair[1], dict) else (0, pair[0])):
        if not isinstance(item, dict):
            continue
        workers.append(
            {
                "name": str(name),
                "port": int(item.get("port") or 0),
                "module_path": str(item.get("module_path") or ""),
                "module": str(item.get("module") or ""),
                "health_url": f"http://{host}:{int(item.get('port') or 0)}/health",
            }
        )
    return workers


def brigade_commands(repo_root: Path, host: str, workspace_root: Path, warmaster_run_root: Path, iskandar_run_root: Path) -> list[CommandSpec]:
    env = {"PYTHONPATH": pythonpath(repo_root)}
    return [
        CommandSpec(
            "mechanicum-workers",
            "Mechanicum worker service supervisor",
            host,
            0,
            [],
            "",
            [
                sys.executable,
                str(repo_root / "Mechanicum" / "start_all_workers.py"),
                "--repo-root",
                str(repo_root),
                "--workspace-root",
                str(workspace_root),
                "--host",
                host,
            ],
            env,
        ),
        CommandSpec(
            "iskandar-khayon",
            "Inner Circle lore reconstruction governor",
            host,
            7101,
            [],
            f"http://{host}:7101/health",
            [
                sys.executable,
                "-m",
                "eye_of_terror.inner_circle.iskandar_service",
                "--host",
                host,
                "--port",
                "7101",
                "--default-run-root",
                str(iskandar_run_root),
            ],
            env,
        ),
        CommandSpec(
            "warmaster-gateway",
            "user-facing orchestration gateway",
            host,
            7000,
            ["mechanicum-workers", "iskandar-khayon"],
            f"http://{host}:7000/health",
            [
                sys.executable,
                "-m",
                "eye_of_terror.warmaster_gateway",
                "--host",
                host,
                "--port",
                "7000",
                "--run-root",
                str(warmaster_run_root),
                "--governor-transport",
                "http",
                "--governor-host",
                host,
            ],
            env,
        ),
    ]


def brigade_plan(repo_root: Path, host: str, workspace_root: Path, warmaster_run_root: Path, iskandar_run_root: Path) -> dict[str, object]:
    commands = brigade_commands(repo_root, host, workspace_root, warmaster_run_root, iskandar_run_root)
    workers = worker_service_plan(repo_root, host)
    top_level_health_urls = {command.name: command.health_url for command in commands if command.health_url}
    worker_health_urls = {str(worker["name"]): str(worker["health_url"]) for worker in workers if worker.get("health_url")}
    return {
        "ok": True,
        "stack": "EyeOfTerror",
        "mode": "service-separated",
        "host": host,
        "ports": {
            "warmaster_gateway": 7000,
            "iskandar_khayon": 7101,
            "mechanicum_workers": [worker["port"] for worker in workers],
        },
        "repo_root": str(repo_root),
        "workspace_root": str(workspace_root),
        "warmaster_run_root": str(warmaster_run_root),
        "iskandar_run_root": str(iskandar_run_root),
        "mechanicum_workers": workers,
        "services": [command.to_dict() for command in commands],
        "dependencies": {command.name: command.depends_on for command in commands},
        "health_urls": {**top_level_health_urls, **worker_health_urls},
        "readiness_urls": list(top_level_health_urls.values()) + list(worker_health_urls.values()),
    }


def url_is_ready(url: str, timeout_sec: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout_sec) as response:
            if response.status >= 400:
                return False
            payload = json.loads(response.read().decode("utf-8"))
        return bool(isinstance(payload, dict) and payload.get("ok"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return False


def wait_for_urls(urls: list[str], timeout_sec: float, interval_sec: float = 0.25) -> dict[str, object]:
    deadline = time.time() + timeout_sec
    pending = set(urls)
    ready: list[str] = []
    while pending and time.time() <= deadline:
        for url in list(pending):
            if url_is_ready(url):
                pending.remove(url)
                ready.append(url)
        if pending:
            time.sleep(interval_sec)
    return {"ok": not pending, "ready": sorted(ready), "pending": sorted(pending), "timeout_sec": timeout_sec}


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) != 0


def port_preflight(host: str, ports: list[int]) -> dict[str, object]:
    checked = sorted(set(port for port in ports if port > 0))
    busy = [port for port in checked if not port_is_free(host, port)]
    return {"ok": not busy, "host": host, "checked": checked, "busy": busy}


def terminate_processes(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        if process.poll() is None:
            process.wait(timeout=10)


def supervise_processes(processes: list[subprocess.Popen[bytes]], poll_interval_sec: float = 0.25) -> int:
    while True:
        for process in processes:
            code = process.poll()
            if code is not None:
                terminate_processes(processes)
                return int(code)
        time.sleep(poll_interval_sec)


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the EyeOfTerror + Iskandar + Mechanicum service brigade.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--workspace-root", default="runtime/mechanicum-work")
    parser.add_argument("--warmaster-run-root", default="runtime/warmaster-runs")
    parser.add_argument("--iskandar-run-root", default="runtime/iskandar-runs")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable startup plan and exit.")
    parser.add_argument("--wait-ready", action="store_true", help="Wait for top-level service health URLs after starting.")
    parser.add_argument("--ready-timeout-sec", type=float, default=30.0)
    parser.add_argument("--skip-port-check", action="store_true", help="Skip managed port availability preflight before starting.")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    workspace_root = Path(args.workspace_root)
    warmaster_run_root = Path(args.warmaster_run_root)
    iskandar_run_root = Path(args.iskandar_run_root)
    commands = brigade_commands(
        repo_root=repo_root,
        host=args.host,
        workspace_root=workspace_root,
        warmaster_run_root=warmaster_run_root,
        iskandar_run_root=iskandar_run_root,
    )
    plan = brigade_plan(
        repo_root=repo_root,
        host=args.host,
        workspace_root=workspace_root,
        warmaster_run_root=warmaster_run_root,
        iskandar_run_root=iskandar_run_root,
    )
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if args.dry_run:
        for command in commands:
            print(f"{command.name}: {command.rendered()}")
        return 0

    if not args.skip_port_check:
        ports = [7000, 7101] + [int(port) for port in plan.get("ports", {}).get("mechanicum_workers", [])]
        preflight = port_preflight(args.host, ports)
        if not preflight["ok"]:
            print(json.dumps({"ok": False, "port_preflight": preflight}, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1

    processes = [
        subprocess.Popen(command.command, cwd=repo_root, env={**os.environ, **command.env})
        for command in commands
    ]
    try:
        if args.wait_ready:
            urls = [str(url) for url in plan.get("readiness_urls", [])]
            readiness = wait_for_urls(urls, timeout_sec=args.ready_timeout_sec)
            if not readiness["ok"]:
                print(json.dumps({"ok": False, "readiness": readiness}, ensure_ascii=False, indent=2), file=sys.stderr)
                terminate_processes(processes)
                return 1
        return supervise_processes(processes)
    except KeyboardInterrupt:
        terminate_processes(processes)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
