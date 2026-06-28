#!/usr/bin/env python3
from __future__ import annotations

import json
import argparse
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKER_CONTRACT = "EyeOfTerror/contracts/worker_api.md"
GOVERNOR_CONTRACT = "EyeOfTerror/contracts/governor_api.md"
VALID_WORKER_STATUSES = {"active", "prototype", "planned"}
VALID_GOVERNOR_STATUSES = {"active", "planned"}


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON must be an object: {path}")
    return payload


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def check_governors(errors: list[str]) -> None:
    require((REPO_ROOT / GOVERNOR_CONTRACT).exists(), f"governor API contract missing: {GOVERNOR_CONTRACT}", errors)
    registry = load_json(REPO_ROOT / "EyeOfTerror" / "registry" / "governors.json")
    seen_ports: dict[int, str] = {}
    for name, item in registry.items():
        require(isinstance(item, dict), f"governor entry is not an object: {name}", errors)
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        port = int(item.get("port") or 0)
        task_kinds = item.get("task_kinds")
        require(status in VALID_GOVERNOR_STATUSES, f"governor {name} has invalid status: {status}", errors)
        require(port >= 7101, f"governor {name} has invalid port: {port}", errors)
        require(isinstance(task_kinds, list) and all(isinstance(kind, str) and kind for kind in task_kinds), f"governor {name} has invalid task_kinds", errors)
        owner = seen_ports.setdefault(port, name)
        require(owner == name, f"governor port collision on {port}: {owner} and {name}", errors)
        if status == "active":
            require(bool(item.get("service")), f"active governor {name} must declare service", errors)


def check_port_registry(errors: list[str]) -> None:
    registry = load_json(REPO_ROOT / "EyeOfTerror" / "registry" / "ports.json")
    seen_ports: dict[int, str] = {}
    for section in ("eye_of_terror", "mechanicum"):
        entries = registry.get(section)
        require(isinstance(entries, dict), f"port registry section missing: {section}", errors)
        if not isinstance(entries, dict):
            continue
        for raw_port, item in entries.items():
            port = int(raw_port)
            name = str(item.get("name") or "") if isinstance(item, dict) else ""
            path = str(item.get("path") or "") if isinstance(item, dict) else ""
            require(bool(name), f"port {port} missing service name", errors)
            require(bool(path), f"port {port} missing service path", errors)
            require((REPO_ROOT / path).exists(), f"port {port} path does not exist: {path}", errors)
            owner = seen_ports.setdefault(port, name)
            require(owner == name, f"global port collision on {port}: {owner} and {name}", errors)


def check_worker_manifests(errors: list[str]) -> None:
    require((REPO_ROOT / WORKER_CONTRACT).exists(), f"worker API contract missing: {WORKER_CONTRACT}", errors)
    port_registry = load_json(REPO_ROOT / "EyeOfTerror" / "registry" / "ports.json").get("mechanicum", {})
    manifest_ports: dict[int, dict[str, Any]] = {}
    seen_ports: dict[int, str] = {}
    for metadata_path in sorted((REPO_ROOT / "Mechanicum").glob("*/worker.json")):
        metadata = load_json(metadata_path)
        name = str(metadata.get("name") or "")
        port = int(metadata.get("port") or 0)
        status = str(metadata.get("status") or "")
        capabilities = metadata.get("capabilities")
        require(bool(name), f"worker metadata missing name: {metadata_path}", errors)
        require(port >= 7001, f"worker {name} has invalid port: {port}", errors)
        require(status in VALID_WORKER_STATUSES, f"worker {name} has invalid status: {status}", errors)
        require(isinstance(capabilities, list) and all(isinstance(item, str) and item for item in capabilities), f"worker {name} has invalid capabilities", errors)
        require(metadata.get("api_contract") == WORKER_CONTRACT, f"worker {name} has wrong api_contract", errors)
        owner = seen_ports.setdefault(port, name)
        require(owner == name, f"worker manifest port collision on {port}: {owner} and {name}", errors)
        manifest_ports[port] = {"name": name, "path": str(metadata_path.parent.relative_to(REPO_ROOT))}
    if isinstance(port_registry, dict):
        for raw_port, item in port_registry.items():
            if not isinstance(item, dict):
                continue
            port = int(raw_port)
            if port not in manifest_ports:
                require(False, f"Mechanicum port {port} has no worker manifest", errors)
                continue
            manifest = manifest_ports[port]
            require(manifest["name"] == item.get("name"), f"port registry and worker manifest name mismatch on {port}", errors)
            require(manifest["path"] == item.get("path"), f"port registry and worker manifest path mismatch on {port}", errors)


def check_worker_services(errors: list[str]) -> None:
    services = load_json(REPO_ROOT / "Mechanicum" / "worker_services.json")
    for name, service in services.items():
        require(isinstance(service, dict), f"worker service entry is not an object: {name}", errors)
        if not isinstance(service, dict):
            continue
        module_path = REPO_ROOT / str(service.get("module_path") or "")
        metadata_path = module_path / "worker.json"
        require(module_path.exists(), f"worker service {name} module path missing: {module_path}", errors)
        require(metadata_path.exists(), f"worker service {name} metadata missing: {metadata_path}", errors)
        if metadata_path.exists():
            metadata = load_json(metadata_path)
            require(metadata.get("name") == name, f"worker service {name} metadata name mismatch", errors)
            require(metadata.get("port") == service.get("port"), f"worker service {name} port mismatch", errors)
            require(metadata.get("status") != "planned", f"planned worker listed as runnable service: {name}", errors)


def run_doctor() -> dict[str, Any]:
    errors: list[str] = []
    checks = [
        ("governors", check_governors),
        ("port_registry", check_port_registry),
        ("worker_manifests", check_worker_manifests),
        ("worker_services", check_worker_services),
    ]
    for _, check in checks:
        check(errors)
    return {"ok": not errors, "checks": [name for name, _ in checks], "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check EyeOfTerror and Mechanicum registry consistency.")
    parser.add_argument("--quiet", action="store_true", help="Print only one status line on success.")
    args = parser.parse_args()
    payload = run_doctor()
    if args.quiet and payload["ok"]:
        print("[ok] EyeOfTerror doctor")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
