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
    return os.pathsep.join([str(repo_root / "EyeOfTerror" / "Warmaster"), str(repo_root / "LegacyMechanicum")])


def model_env_defaults() -> dict[str, str]:
    return {
        "EYE_MODEL_BASE_URL": os.environ.get("EYE_MODEL_BASE_URL", os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8080/v1")),
        "EYE_MODEL_NAME": os.environ.get("EYE_MODEL_NAME", os.environ.get("LLM_MODEL", os.environ.get("ARCHIVE_DEFAULT_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf"))),
        "EYE_MODEL_TIMEOUT_SEC": os.environ.get("EYE_MODEL_TIMEOUT_SEC", "180"),
        "EYE_MODEL_MAX_TOKENS": os.environ.get("EYE_MODEL_MAX_TOKENS", "1024"),
    }


def worker_service_plan(repo_root: Path, host: str) -> list[dict[str, object]]:
    path = repo_root / "LegacyMechanicum" / "worker_services.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("worker_services.json must contain an object")
    workers: list[dict[str, object]] = []
    for name, item in sorted(payload.items(), key=lambda pair: (int(pair[1].get("port") or 0), pair[0]) if isinstance(pair[1], dict) else (0, pair[0])):
        if not isinstance(item, dict):
            raise ValueError(f"worker service entry must be an object: {name}")
        port = int(item.get("port") or 0)
        module_path = str(item.get("module_path") or "")
        module = str(item.get("module") or "")
        if port <= 0:
            raise ValueError(f"worker service entry has invalid port: {name}")
        if not module_path:
            raise ValueError(f"worker service entry is missing module_path: {name}")
        if not module:
            raise ValueError(f"worker service entry is missing module: {name}")
        workers.append(
            {
                "name": str(name),
                "port": port,
                "module_path": module_path,
                "module": module,
                "health_url": f"http://{host}:{port}/health",
            }
        )
    return workers


def registry_port(repo_root: Path, section: str, service_name: str, default: int) -> int:
    path = repo_root / "EyeOfTerror" / "Warmaster" / "registry" / "ports.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    entries = payload.get(section) if isinstance(payload, dict) else {}
    if not isinstance(entries, dict):
        return default
    for raw_port, item in entries.items():
        if isinstance(item, dict) and item.get("name") == service_name:
            try:
                return int(raw_port)
            except ValueError:
                return default
    return default


def brigade_commands(
    repo_root: Path,
    host: str,
    workspace_root: Path,
    warmaster_run_root: Path,
    iskandar_run_root: Path,
    ceraxia_run_root: Path | None = None,
) -> list[CommandSpec]:
    env = {"PYTHONPATH": pythonpath(repo_root), **model_env_defaults()}
    warmaster_port = registry_port(repo_root, "eye_of_terror", "WarmasterGateway", 7000)
    iskandar_port = registry_port(repo_root, "eye_of_terror", "IskandarKhayon", 7101)
    ceraxia_port = registry_port(repo_root, "eye_of_terror", "Ceraxia", 7104)
    resolved_ceraxia_run_root = ceraxia_run_root or Path("runtime/ceraxia-runs")
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
                str(repo_root / "LegacyMechanicum" / "start_all_workers.py"),
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
            iskandar_port,
            [],
            f"http://{host}:{iskandar_port}/health",
            [
                sys.executable,
                "-m",
                "eye_of_terror.inner_circle.iskandar_service",
                "--host",
                host,
                "--port",
                str(iskandar_port),
                "--default-run-root",
                str(iskandar_run_root),
            ],
            env,
        ),
        CommandSpec(
            "ceraxia",
            "Inner Circle code task governor",
            host,
            ceraxia_port,
            [],
            f"http://{host}:{ceraxia_port}/health",
            [
                sys.executable,
                "-m",
                "eye_of_terror.inner_circle.ceraxia_service",
                "--host",
                host,
                "--port",
                str(ceraxia_port),
                "--default-run-root",
                str(resolved_ceraxia_run_root),
            ],
            env,
        ),
        CommandSpec(
            "warmaster-gateway",
            "user-facing orchestration gateway",
            host,
            warmaster_port,
            ["mechanicum-workers", "iskandar-khayon", "ceraxia"],
            f"http://{host}:{warmaster_port}/health",
            [
                sys.executable,
                "-m",
                "eye_of_terror.warmaster_gateway",
                "--host",
                host,
                "--port",
                str(warmaster_port),
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


def startup_stages(commands: list[CommandSpec], workers: list[dict[str, object]]) -> list[dict[str, object]]:
    pending = {command.name: command for command in commands}
    completed: set[str] = set()
    stages: list[dict[str, object]] = []
    while pending:
        ready = [
            command
            for command in pending.values()
            if all(dependency in completed for dependency in command.depends_on)
        ]
        if not ready:
            unresolved = {name: command.depends_on for name, command in pending.items()}
            raise ValueError(f"cyclic or unresolved brigade dependencies: {unresolved}")
        ready.sort(key=lambda command: command.name)
        health_urls: list[str] = []
        for command in ready:
            if command.health_url:
                health_urls.append(command.health_url)
            if command.name == "mechanicum-workers":
                health_urls.extend(str(worker["health_url"]) for worker in workers if worker.get("health_url"))
        stages.append(
            {
                "stage": len(stages) + 1,
                "services": [command.name for command in ready],
                "health_urls": health_urls,
            }
        )
        for command in ready:
            pending.pop(command.name)
            completed.add(command.name)
    return stages


def brigade_worker_contract(commands: list[CommandSpec], workers: list[dict[str, object]], readiness_urls: list[str]) -> dict[str, object]:
    return {
        "kind": "eye_of_terror_brigade_worker_contract",
        "contract_version": 1,
        "producer": "start_brigade.py",
        "consumers": ["warmaster-gateway", "inner-circle-governors", "mechanicum-supervisor"],
        "top_level_service_required_fields": ["name", "role", "host", "port", "depends_on", "health_url", "command", "env"],
        "mechanicum_worker_required_fields": ["name", "port", "module_path", "module", "health_url"],
        "dependency_edges": [
            {"service": command.name, "depends_on": command.depends_on}
            for command in commands
        ],
        "readiness_url_count": len(readiness_urls),
        "readiness_urls": readiness_urls,
        "worker_count": len(workers),
    }


def brigade_plan(
    repo_root: Path,
    host: str,
    workspace_root: Path,
    warmaster_run_root: Path,
    iskandar_run_root: Path,
    ceraxia_run_root: Path | None = None,
) -> dict[str, object]:
    resolved_ceraxia_run_root = ceraxia_run_root or Path("runtime/ceraxia-runs")
    commands = brigade_commands(repo_root, host, workspace_root, warmaster_run_root, iskandar_run_root, resolved_ceraxia_run_root)
    workers = worker_service_plan(repo_root, host)
    top_level_health_urls = {command.name: command.health_url for command in commands if command.health_url}
    worker_health_urls = {str(worker["name"]): str(worker["health_url"]) for worker in workers if worker.get("health_url")}
    readiness_urls = list(top_level_health_urls.values()) + list(worker_health_urls.values())
    return {
        "ok": True,
        "stack": "EyeOfTerror",
        "mode": "service-separated",
        "host": host,
        "ports": {
            "warmaster_gateway": next((command.port for command in commands if command.name == "warmaster-gateway"), 7000),
            "iskandar_khayon": next((command.port for command in commands if command.name == "iskandar-khayon"), 7101),
            "ceraxia": next((command.port for command in commands if command.name == "ceraxia"), 7104),
            "mechanicum_workers": [worker["port"] for worker in workers],
        },
        "repo_root": str(repo_root),
        "workspace_root": str(workspace_root),
        "warmaster_run_root": str(warmaster_run_root),
        "iskandar_run_root": str(iskandar_run_root),
        "ceraxia_run_root": str(resolved_ceraxia_run_root),
        "mechanicum_workers": workers,
        "services": [command.to_dict() for command in commands],
        "dependencies": {command.name: command.depends_on for command in commands},
        "startup_stages": startup_stages(commands, workers),
        "health_urls": {**top_level_health_urls, **worker_health_urls},
        "readiness_urls": readiness_urls,
        "worker_contract": brigade_worker_contract(commands, workers, readiness_urls),
        "model_brain": {
            "required": True,
            "base_url": env_value(commands, "EYE_MODEL_BASE_URL"),
            "model": env_value(commands, "EYE_MODEL_NAME"),
        },
    }


def env_value(commands: list[CommandSpec], key: str) -> str:
    for command in commands:
        if key in command.env:
            return command.env[key]
    return ""


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


def command_start_order(commands: list[CommandSpec], stages: list[dict[str, object]]) -> list[CommandSpec]:
    by_name = {command.name: command for command in commands}
    ordered: list[CommandSpec] = []
    for stage in stages:
        services = stage.get("services") if isinstance(stage.get("services"), list) else []
        for service in services:
            name = str(service)
            if name not in by_name:
                raise ValueError(f"startup stage references unknown service: {name}")
            ordered.append(by_name[name])
    if {command.name for command in ordered} != set(by_name):
        missing = sorted(set(by_name) - {command.name for command in ordered})
        raise ValueError(f"startup stages do not cover services: {missing}")
    return ordered


def start_process(command: CommandSpec, repo_root: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(command.command, cwd=repo_root, env={**os.environ, **command.env})


def start_processes_by_stage(
    commands: list[CommandSpec],
    stages: list[dict[str, object]],
    repo_root: Path,
    ready_timeout_sec: float,
) -> tuple[list[subprocess.Popen[bytes]], dict[str, object]]:
    by_name = {command.name: command for command in commands}
    processes: list[subprocess.Popen[bytes]] = []
    readiness: list[dict[str, object]] = []
    for stage in stages:
        services = [str(service) for service in stage.get("services", []) if str(service)]
        for service in services:
            if service not in by_name:
                terminate_processes(processes)
                raise ValueError(f"startup stage references unknown service: {service}")
            processes.append(start_process(by_name[service], repo_root))
        urls = [str(url) for url in stage.get("health_urls", []) if str(url)]
        result = wait_for_urls(urls, timeout_sec=ready_timeout_sec) if urls else {"ok": True, "ready": [], "pending": [], "timeout_sec": ready_timeout_sec}
        readiness.append({"stage": stage.get("stage"), "services": services, **result})
        if not result["ok"]:
            terminate_processes(processes)
            return processes, {"ok": False, "stages": readiness}
    return processes, {"ok": True, "stages": readiness}


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
    parser.add_argument("--ceraxia-run-root", default="runtime/ceraxia-runs")
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
    ceraxia_run_root = Path(args.ceraxia_run_root)
    commands = brigade_commands(
        repo_root=repo_root,
        host=args.host,
        workspace_root=workspace_root,
        warmaster_run_root=warmaster_run_root,
        iskandar_run_root=iskandar_run_root,
        ceraxia_run_root=ceraxia_run_root,
    )
    plan = brigade_plan(
        repo_root=repo_root,
        host=args.host,
        workspace_root=workspace_root,
        warmaster_run_root=warmaster_run_root,
        iskandar_run_root=iskandar_run_root,
        ceraxia_run_root=ceraxia_run_root,
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

    stages = plan.get("startup_stages") if isinstance(plan.get("startup_stages"), list) else []
    processes: list[subprocess.Popen[bytes]]
    try:
        if args.wait_ready:
            processes, readiness = start_processes_by_stage(commands, stages, repo_root, args.ready_timeout_sec)
            if not readiness["ok"]:
                print(json.dumps({"ok": False, "readiness": readiness}, ensure_ascii=False, indent=2), file=sys.stderr)
                return 1
        else:
            processes = [start_process(command, repo_root) for command in command_start_order(commands, stages)]
        return supervise_processes(processes)
    except KeyboardInterrupt:
        terminate_processes(processes)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
