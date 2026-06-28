#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandSpec:
    name: str
    role: str
    host: str
    port: int
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
            "command": self.command,
            "env": self.env,
            "rendered": self.rendered(),
        }


def pythonpath(repo_root: Path) -> str:
    return os.pathsep.join([str(repo_root / "EyeOfTerror"), str(repo_root / "Mechanicum")])


def worker_service_plan(repo_root: Path) -> list[dict[str, object]]:
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
    workers = worker_service_plan(repo_root)
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
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the EyeOfTerror + Iskandar + Mechanicum service brigade.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--workspace-root", default="runtime/mechanicum-work")
    parser.add_argument("--warmaster-run-root", default="runtime/warmaster-runs")
    parser.add_argument("--iskandar-run-root", default="runtime/iskandar-runs")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable startup plan and exit.")
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

    processes = [
        subprocess.Popen(command.command, cwd=repo_root, env={**os.environ, **command.env})
        for command in commands
    ]
    try:
        return max(process.wait() for process in processes)
    except KeyboardInterrupt:
        for process in processes:
            process.terminate()
        for process in processes:
            process.wait(timeout=10)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
