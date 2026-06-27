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


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    services = load_services(repo_root)
    expected = {
        "Lexmechanic": (7002, "lexmechanic"),
        "AuspexBrowser": (7009, "auspex_browser"),
        "NoosphericExtractor": (7003, "noospheric_extractor"),
        "Chronologis": (7004, "chronologis"),
        "ScriptoriumDaemon": (7005, "scriptorium_daemon"),
        "ReductorVerifier": (7006, "reductor_verifier"),
        "FabricatorFinalis": (7007, "fabricator_finalis"),
    }
    for worker, (port, module) in expected.items():
        service = services.get(worker)
        if not service:
            raise AssertionError(f"missing service config: {worker}")
        if service.get("port") != port or service.get("module") != module:
            raise AssertionError(f"bad service config for {worker}: {service}")
        module_path = repo_root / service["module_path"]
        if not module_path.exists():
            raise AssertionError(f"module path missing for {worker}: {service['module_path']}")
        metadata = load_worker_metadata(module_path / "worker.json")
        if metadata["name"] != worker or metadata["port"] != port:
            raise AssertionError(f"service registry and worker metadata disagree for {worker}: {metadata}")
        if metadata["status"] == "planned":
            raise AssertionError(f"planned worker cannot be listed as a runnable service: {worker}")
    seen_ports: dict[int, str] = {}
    for metadata_path in sorted((repo_root / "Mechanicum").glob("*/worker.json")):
        metadata = load_worker_metadata(metadata_path)
        owner = seen_ports.setdefault(metadata["port"], metadata["name"])
        if owner != metadata["name"]:
            raise AssertionError(f"worker metadata port collision on {metadata['port']}: {owner} and {metadata['name']}")
    print("[ok] worker services registry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
