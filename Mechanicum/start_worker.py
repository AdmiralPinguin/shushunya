#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from worker_runtime import serve


def load_services(repo_root: Path) -> dict:
    path = repo_root / "Mechanicum" / "worker_services.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("worker_services.json must contain an object")
    return payload


def load_worker_aliases(repo_root: Path) -> dict[str, str]:
    path = repo_root / "Mechanicum" / "worker_aliases.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("worker_aliases.json must contain an object")
    aliases: dict[str, str] = {}
    for alias, worker in payload.items():
        if not isinstance(alias, str) or not alias:
            raise ValueError("worker alias names must be non-empty strings")
        if not isinstance(worker, str) or not worker:
            raise ValueError(f"worker alias target must be a non-empty string: {alias}")
        aliases[alias] = worker
    return aliases


def resolve_worker_name(requested: str, services: dict, aliases: dict[str, str]) -> str:
    if requested in services:
        return requested
    resolved = aliases.get(requested, requested)
    if resolved not in services:
        raise SystemExit(f"unknown worker: {requested}")
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description="Start one Mechanicum worker service by registry name.")
    parser.add_argument("worker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="Override configured port")
    parser.add_argument("--workspace-root", default="runtime/mechanicum-work")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()
    repo_root = Path(args.repo_root).resolve()
    services = load_services(repo_root)
    worker_name = resolve_worker_name(args.worker, services, load_worker_aliases(repo_root))
    service = services[worker_name]
    port = args.port or int(service["port"])
    serve(
        worker_name=worker_name,
        module_path=repo_root / str(service["module_path"]),
        module_name=str(service["module"]),
        host=args.host,
        port=port,
        workspace_root=Path(args.workspace_root),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
