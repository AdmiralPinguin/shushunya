#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from start_worker import load_services


DEFAULT_WORKERS = [
    "Lexmechanic",
    "AuspexBrowser",
    "NoosphericExtractor",
    "Chronologis",
    "ScriptoriumDaemon",
    "ReductorVerifier",
    "FabricatorFinalis",
]


def default_workers(repo_root: Path) -> list[str]:
    services = load_services(repo_root)
    return [
        name
        for name, _service in sorted(
            services.items(),
            key=lambda item: (int(item[1].get("port") or 0), item[0]),
        )
    ]


def build_command(repo_root: Path, worker: str, workspace_root: Path, host: str) -> list[str]:
    return [
        sys.executable,
        str(repo_root / "Mechanicum" / "start_worker.py"),
        worker,
        "--host",
        host,
        "--workspace-root",
        str(workspace_root),
        "--repo-root",
        str(repo_root),
    ]


def commands_for_workers(repo_root: Path, workers: list[str], workspace_root: Path, host: str) -> list[list[str]]:
    services = load_services(repo_root)
    missing = [worker for worker in workers if worker not in services]
    if missing:
        raise ValueError(f"unknown workers: {', '.join(missing)}")
    return [build_command(repo_root, worker, workspace_root, host) for worker in workers]


def main() -> int:
    parser = argparse.ArgumentParser(description="Start a set of Mechanicum worker services.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--workspace-root", default="runtime/mechanicum-work")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--workers", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    repo_root = Path(args.repo_root).resolve()
    workspace_root = Path(args.workspace_root)
    workers = [item.strip() for item in args.workers.split(",") if item.strip()] if args.workers else default_workers(repo_root)
    commands = commands_for_workers(repo_root, workers, workspace_root, args.host)
    if args.dry_run:
        for command in commands:
            print(" ".join(command))
        return 0
    processes = [subprocess.Popen(command, cwd=repo_root) for command in commands]
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
