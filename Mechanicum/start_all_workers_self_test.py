#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from start_all_workers import commands_for_workers, default_workers


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    workers = default_workers(repo_root)
    commands = commands_for_workers(repo_root, workers, Path("runtime/test-workers"), "127.0.0.1")
    if len(commands) != len(workers):
        raise AssertionError(f"wrong command count: {commands}")
    rendered = "\n".join(" ".join(command) for command in commands)
    for worker in workers:
        if worker not in rendered:
            raise AssertionError(f"missing worker command: {worker}")
    print("[ok] start all workers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
