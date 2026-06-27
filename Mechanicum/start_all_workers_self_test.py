#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from start_all_workers import DEFAULT_WORKERS, commands_for_workers


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    commands = commands_for_workers(repo_root, DEFAULT_WORKERS, Path("runtime/test-workers"), "127.0.0.1")
    if len(commands) != len(DEFAULT_WORKERS):
        raise AssertionError(f"wrong command count: {commands}")
    rendered = "\n".join(" ".join(command) for command in commands)
    for worker in DEFAULT_WORKERS:
        if worker not in rendered:
            raise AssertionError(f"missing worker command: {worker}")
    print("[ok] start all workers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
