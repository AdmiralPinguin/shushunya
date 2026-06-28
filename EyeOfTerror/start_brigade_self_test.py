#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from start_brigade import brigade_commands


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
    print("[ok] EyeOfTerror brigade launcher")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
