#!/usr/bin/env python3
from __future__ import annotations

import json
import unittest
from pathlib import Path

from start_worker import load_services, load_worker_aliases, resolve_worker_name


REQUIRED_METADATA_KEYS = {"name", "port", "role", "status", "capabilities", "api_contract"}
CERAXIA_ROLE_CONTRACTS = {
    "LogisRepository": ("repository_survey", "MagosStrategos"),
    "MagosStrategos": ("change_planning", "FerrumPatchwright"),
    "FerrumPatchwright": ("implementation", "OrdinatusVerifier"),
    "OrdinatusVerifier": ("verification", "JudicatorCodicis"),
    "JudicatorCodicis": ("code_review", "SealwrightFinalis"),
    "SealwrightFinalis": ("finalize", ""),
}


class WorkerAliasResolutionTests(unittest.TestCase):
    def test_runtime_aliases_resolve_without_shadowing_workers(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        services = load_services(repo_root)
        aliases = load_worker_aliases(repo_root)
        self.assertTrue(aliases)
        for alias, worker in aliases.items():
            self.assertNotIn(alias, services)
            self.assertIn(worker, services)
            self.assertEqual(resolve_worker_name(alias, services, aliases), worker)

    def test_concrete_worker_names_still_work_and_unknown_alias_fails_closed(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        services = load_services(repo_root)
        aliases = load_worker_aliases(repo_root)
        self.assertEqual(resolve_worker_name("JudicatorCodicis", services, aliases), "JudicatorCodicis")
        self.assertEqual(resolve_worker_name("code.reviewer", services, aliases), "JudicatorCodicis")
        with self.assertRaises(SystemExit):
            resolve_worker_name("missing.alias", services, aliases)


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
    if payload["api_contract"] != "EyeOfTerror/Warmaster/contracts/worker_api.md":
        raise AssertionError(f"worker metadata points to wrong API contract: {path}")
    if payload["name"] in CERAXIA_ROLE_CONTRACTS:
        role_contract = payload.get("role_contract")
        if not isinstance(role_contract, dict):
            raise AssertionError(f"Ceraxia worker metadata must include role_contract: {path}")
        expected_step, expected_handoff = CERAXIA_ROLE_CONTRACTS[payload["name"]]
        if role_contract.get("owned_step") != expected_step:
            raise AssertionError(f"Ceraxia worker role_contract has wrong owned_step: {path}")
        handoff_to = role_contract.get("handoff_to")
        if not isinstance(handoff_to, list) or not all(isinstance(item, str) for item in handoff_to):
            raise AssertionError(f"Ceraxia worker role_contract handoff_to must be a string list: {path}")
        if expected_handoff and handoff_to != [expected_handoff]:
            raise AssertionError(f"Ceraxia worker role_contract has wrong handoff_to: {path}")
        if not expected_handoff and handoff_to:
            raise AssertionError(f"Ceraxia final worker should not hand off further: {path}")
        expected_artifacts = role_contract.get("expected_artifacts")
        if not isinstance(expected_artifacts, list) or not all(isinstance(item, str) and item for item in expected_artifacts):
            raise AssertionError(f"Ceraxia worker role_contract expected_artifacts must be non-empty strings: {path}")
        if not isinstance(role_contract.get("authority"), str) or not role_contract.get("authority"):
            raise AssertionError(f"Ceraxia worker role_contract authority must be a non-empty string: {path}")
    return payload


def load_port_registry(repo_root: Path) -> dict[str, dict]:
    payload = json.loads((repo_root / "EyeOfTerror" / "Warmaster" / "registry" / "ports.json").read_text(encoding="utf-8"))
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
    aliases = load_worker_aliases(repo_root)
    port_registry = load_port_registry(repo_root)
    if not aliases:
        raise AssertionError("worker_aliases.json must define runtime aliases")
    for alias, worker in aliases.items():
        if alias in services:
            raise AssertionError(f"worker alias must not shadow a concrete service name: {alias}")
        if worker not in services:
            raise AssertionError(f"worker alias points at unknown service: {alias} -> {worker}")
        if resolve_worker_name(alias, services, aliases) != worker:
            raise AssertionError(f"worker alias does not resolve to target: {alias} -> {worker}")
    if resolve_worker_name("code.reviewer", services, aliases) != "JudicatorCodicis":
        raise AssertionError(f"code.reviewer alias drifted: {aliases}")
    if resolve_worker_name("JudicatorCodicis", services, aliases) != "JudicatorCodicis":
        raise AssertionError("concrete worker names must remain valid alongside aliases")
    try:
        resolve_worker_name("missing.alias", services, aliases)
    except SystemExit:
        pass
    else:
        raise AssertionError("unknown aliases must fail closed")
    metadata_paths = {
        path
        for path in (repo_root / "Mechanicum").glob("*/worker.json")
    }
    for service in services.values():
        if isinstance(service, dict) and service.get("module_path"):
            metadata_paths.add(repo_root / str(service["module_path"]) / "worker.json")
    metadata_by_name: dict[str, dict] = {}
    seen_ports: dict[int, str] = {}
    for metadata_path in sorted(metadata_paths):
        if not metadata_path.exists():
            continue
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
