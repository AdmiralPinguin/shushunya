#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from start_worker import load_services


REQUIRED_METADATA_KEYS = {"name", "port", "role", "status", "capabilities", "api_contract"}


def load_worker_metadata(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"worker metadata must be an object: {path}")
    missing = REQUIRED_METADATA_KEYS - set(payload)
    if missing:
        raise AssertionError(f"worker metadata missing {sorted(missing)}: {path}")
    if not isinstance(payload["name"], str) or not payload["name"]:
        raise AssertionError(f"worker metadata name must be a non-empty string: {path}")
    if not isinstance(payload["port"], int) or payload["port"] < 7001:
        raise AssertionError(f"worker metadata port must be an int >= 7001: {path}")
    if payload["status"] not in {"prototype", "planned", "active"}:
        raise AssertionError(f"worker metadata has unsupported status: {path}")
    if not isinstance(payload["capabilities"], list) or not all(isinstance(item, str) and item for item in payload["capabilities"]):
        raise AssertionError(f"worker metadata capabilities must be non-empty strings: {path}")
    if payload["api_contract"] != "EyeOfTerror/contracts/worker_api.md":
        raise AssertionError(f"worker metadata points to wrong API contract: {path}")
    return payload


def load_port_registry(repo_root: Path) -> dict[str, dict]:
    payload = json.loads((repo_root / "EyeOfTerror" / "registry" / "ports.json").read_text(encoding="utf-8"))
    mechanicum = payload.get("mechanicum") if isinstance(payload, dict) else None
    if not isinstance(mechanicum, dict):
        raise AssertionError("ports.json must contain a mechanicum object")
    registry: dict[str, dict] = {}
    for raw_port, item in mechanicum.items():
        if not isinstance(item, dict):
            raise AssertionError(f"bad mechanicum port entry: {raw_port}")
        name = str(item.get("name") or "")
        if not name:
            raise AssertionError(f"mechanicum port entry is missing name: {raw_port}")
        registry[name] = {"port": int(raw_port), **item}
    return registry


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    services = load_services(repo_root)
    port_registry = load_port_registry(repo_root)
    metadata_by_name: dict[str, dict] = {}
    seen_ports: dict[int, str] = {}
    for metadata_path in sorted((repo_root / "Mechanicum").glob("*/worker.json")):
        metadata = load_worker_metadata(metadata_path)
        metadata_by_name[metadata["name"]] = metadata
        owner = seen_ports.setdefault(metadata["port"], metadata["name"])
        if owner != metadata["name"]:
            raise AssertionError(f"worker metadata port collision on {metadata['port']}: {owner} and {metadata['name']}")
        port_entry = port_registry.get(metadata["name"])
        if not port_entry:
            raise AssertionError(f"worker metadata missing from ports.json: {metadata['name']}")
        if port_entry["port"] != metadata["port"] or port_entry.get("path") != str(metadata_path.parent.relative_to(repo_root)):
            raise AssertionError(f"ports.json and worker metadata disagree for {metadata['name']}: {port_entry} vs {metadata}")
        if metadata["status"] == "prototype" and metadata["name"] not in services:
            raise AssertionError(f"prototype worker must be listed as a runnable service: {metadata['name']}")
    for worker, service in sorted(services.items()):
        metadata = metadata_by_name.get(worker)
        if not metadata:
            raise AssertionError(f"service config has no worker metadata: {worker}")
        if metadata["status"] == "planned":
            raise AssertionError(f"planned worker cannot be listed as a runnable service: {worker}")
        if service.get("port") != metadata["port"]:
            raise AssertionError(f"service registry and worker metadata disagree for {worker}: {service}")
        module_path = repo_root / str(service.get("module_path") or "")
        if not module_path.exists():
            raise AssertionError(f"module path missing for {worker}: {service.get('module_path')}")
        if module_path / "worker.json" != repo_root / port_registry[worker]["path"] / "worker.json":
            raise AssertionError(f"service module_path and ports.json path disagree for {worker}: {service}")
        if not isinstance(service.get("module"), str) or not service["module"]:
            raise AssertionError(f"service module must be a non-empty string for {worker}: {service}")
    print("[ok] worker services registry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
