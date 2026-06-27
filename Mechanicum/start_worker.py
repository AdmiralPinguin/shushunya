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
    if args.worker not in services:
        raise SystemExit(f"unknown worker: {args.worker}")
    service = services[args.worker]
    port = args.port or int(service["port"])
    serve(
        worker_name=args.worker,
        module_path=repo_root / str(service["module_path"]),
        module_name=str(service["module"]),
        host=args.host,
        port=port,
        workspace_root=Path(args.workspace_root),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
