#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from start_brigade import brigade_commands, brigade_plan


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
    plan = brigade_plan(
        repo_root=repo_root,
        host="127.0.0.1",
        workspace_root=Path("runtime/test-work"),
        warmaster_run_root=Path("runtime/test-warmaster-runs"),
        iskandar_run_root=Path("runtime/test-iskandar-runs"),
    )
    if plan.get("mode") != "service-separated" or plan.get("ports", {}).get("warmaster_gateway") != 7000:
        raise AssertionError(f"bad brigade JSON plan: {plan}")
    service_names = {item.get("name") for item in plan.get("services", []) if isinstance(item, dict)}
    if service_names != expected_names:
        raise AssertionError(f"bad brigade service names in JSON plan: {plan}")
    dependencies = plan.get("dependencies", {})
    if dependencies.get("warmaster-gateway") != ["mechanicum-workers", "iskandar-khayon"]:
        raise AssertionError(f"bad brigade dependencies: {plan}")
    health_urls = plan.get("health_urls", {})
    if health_urls.get("warmaster-gateway") != "http://127.0.0.1:7000/health" or health_urls.get("iskandar-khayon") != "http://127.0.0.1:7101/health":
        raise AssertionError(f"bad brigade health URLs: {plan}")
    worker_names = {item.get("name") for item in plan.get("mechanicum_workers", []) if isinstance(item, dict)}
    required_workers = {"Lexmechanic", "AuspexBrowser", "NoosphericExtractor", "Chronologis", "ScriptoriumDaemon", "ReductorVerifier", "FabricatorFinalis"}
    if not required_workers.issubset(worker_names):
        raise AssertionError(f"brigade JSON plan missing workers: {required_workers - worker_names}")
    worker_ports = plan.get("ports", {}).get("mechanicum_workers", [])
    if worker_ports != [7002, 7003, 7004, 7005, 7006, 7007, 7009]:
        raise AssertionError(f"bad worker port plan: {worker_ports}")
    print("[ok] EyeOfTerror brigade launcher")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
