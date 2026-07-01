#!/usr/bin/env python3
from __future__ import annotations

import json
import argparse
import importlib.util
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKER_CONTRACT = "EyeOfTerror/Warmaster/contracts/worker_api.md"
GOVERNOR_CONTRACT = "EyeOfTerror/Warmaster/contracts/governor_api.md"
WARMASTER_CONTRACT = "EyeOfTerror/Warmaster/contracts/warmaster_api.md"
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


def check_governors(errors: list[str]) -> int:
    require((REPO_ROOT / WARMASTER_CONTRACT).exists(), f"Warmaster API contract missing: {WARMASTER_CONTRACT}", errors)
    require((REPO_ROOT / GOVERNOR_CONTRACT).exists(), f"governor API contract missing: {GOVERNOR_CONTRACT}", errors)
    registry = load_json(REPO_ROOT / "EyeOfTerror" / "Warmaster" / "registry" / "governors.json")
    port_registry = load_json(REPO_ROOT / "EyeOfTerror" / "Warmaster" / "registry" / "ports.json").get("eye_of_terror", {})
    governor_ports: dict[int, str] = {}
    seen_ports: dict[int, str] = {}
    for name, item in registry.items():
        require(isinstance(item, dict), f"governor entry is not an object: {name}", errors)
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        port = int(item.get("port") or 0)
        task_kinds = item.get("task_kinds")
        route_terms = item.get("route_terms")
        require(status in VALID_GOVERNOR_STATUSES, f"governor {name} has invalid status: {status}", errors)
        require(port >= 7101, f"governor {name} has invalid port: {port}", errors)
        require(isinstance(task_kinds, list) and all(isinstance(kind, str) and kind for kind in task_kinds), f"governor {name} has invalid task_kinds", errors)
        require(isinstance(route_terms, list) and all(isinstance(term, str) and term for term in route_terms), f"governor {name} has invalid route_terms", errors)
        owner = seen_ports.setdefault(port, name)
        require(owner == name, f"governor port collision on {port}: {owner} and {name}", errors)
        governor_ports[port] = name
        if status == "active":
            service = str(item.get("service") or "")
            require(bool(service), f"active governor {name} must declare service", errors)
            if service:
                require(importlib.util.find_spec(service) is not None, f"active governor {name} service is not importable: {service}", errors)
    if isinstance(port_registry, dict):
        registry_ports = set(governor_ports)
        port_registry_ports = {
            int(raw_port)
            for raw_port, item in port_registry.items()
            if isinstance(item, dict) and int(raw_port) >= 7101
        }
        for port in sorted(registry_ports - port_registry_ports):
            require(False, f"governor {governor_ports[port]} missing from port registry on {port}", errors)
        for raw_port, item in port_registry.items():
            if not isinstance(item, dict):
                continue
            port = int(raw_port)
            name = str(item.get("name") or "")
            if port == 7000:
                continue
            require(governor_ports.get(port) == name, f"governor port registry mismatch on {port}: {name}", errors)
    return len(registry)


def check_port_registry(errors: list[str]) -> int:
    registry = load_json(REPO_ROOT / "EyeOfTerror" / "Warmaster" / "registry" / "ports.json")
    count = 0
    seen_ports: dict[int, str] = {}
    for section in ("eye_of_terror", "mechanicum"):
        entries = registry.get(section)
        require(isinstance(entries, dict), f"port registry section missing: {section}", errors)
        if not isinstance(entries, dict):
            continue
        for raw_port, item in entries.items():
            count += 1
            port = int(raw_port)
            name = str(item.get("name") or "") if isinstance(item, dict) else ""
            path = str(item.get("path") or "") if isinstance(item, dict) else ""
            require(bool(name), f"port {port} missing service name", errors)
            require(bool(path), f"port {port} missing service path", errors)
            require((REPO_ROOT / path).exists(), f"port {port} path does not exist: {path}", errors)
            owner = seen_ports.setdefault(port, name)
            require(owner == name, f"global port collision on {port}: {owner} and {name}", errors)
    return count


def check_worker_manifests(errors: list[str]) -> int:
    require((REPO_ROOT / WORKER_CONTRACT).exists(), f"worker API contract missing: {WORKER_CONTRACT}", errors)
    port_registry = load_json(REPO_ROOT / "EyeOfTerror" / "Warmaster" / "registry" / "ports.json").get("mechanicum", {})
    manifest_ports: dict[int, dict[str, Any]] = {}
    seen_ports: dict[int, str] = {}
    metadata_paths = {
        path
        for path in (REPO_ROOT / "Mechanicum").glob("*/worker.json")
    }
    services = load_json(REPO_ROOT / "Mechanicum" / "worker_services.json")
    if isinstance(services, dict):
        for service in services.values():
            if isinstance(service, dict) and service.get("module_path"):
                metadata_paths.add(REPO_ROOT / str(service["module_path"]) / "worker.json")
    metadata_paths = sorted(metadata_paths)
    for metadata_path in metadata_paths:
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
    return len(metadata_paths)


def check_worker_services(errors: list[str]) -> int:
    services = load_json(REPO_ROOT / "Mechanicum" / "worker_services.json")
    for name, service in services.items():
        require(isinstance(service, dict), f"worker service entry is not an object: {name}", errors)
        if not isinstance(service, dict):
            continue
        module_path = REPO_ROOT / str(service.get("module_path") or "")
        module_name = str(service.get("module") or "")
        metadata_path = module_path / "worker.json"
        require(module_path.exists(), f"worker service {name} module path missing: {module_path}", errors)
        require(bool(module_name), f"worker service {name} module missing", errors)
        if module_name:
            require((module_path / f"{module_name}.py").exists(), f"worker service {name} module file missing: {module_name}.py", errors)
        require(metadata_path.exists(), f"worker service {name} metadata missing: {metadata_path}", errors)
        if metadata_path.exists():
            metadata = load_json(metadata_path)
            require(metadata.get("name") == name, f"worker service {name} metadata name mismatch", errors)
            require(metadata.get("port") == service.get("port"), f"worker service {name} port mismatch", errors)
            require(metadata.get("status") != "planned", f"planned worker listed as runnable service: {name}", errors)
    return len(services)


def require_string_list(value: Any, message: str, errors: list[str], *, allow_empty: bool = False) -> None:
    valid = isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)
    if valid and (allow_empty or bool(value)):
        return
    errors.append(message)


def check_source_playbooks(errors: list[str]) -> int:
    playbook_dir = REPO_ROOT / "EyeOfTerror" / "Scriptorium" / "Brigade" / "Lexmechanic" / "playbooks"
    count = 0
    seen_names: set[str] = set()
    for path in sorted(playbook_dir.glob("*.json")):
        count += 1
        try:
            playbook = load_json(path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            require(False, f"source playbook {path.name} is invalid: {exc}", errors)
            continue
        name = str(playbook.get("name") or path.stem)
        require(name not in seen_names, f"source playbook duplicate name: {name}", errors)
        seen_names.add(name)
        require(bool(str(playbook.get("topic") or "").strip()), f"source playbook {path.name} missing topic", errors)
        require_string_list(playbook.get("match_terms"), f"source playbook {path.name} missing match_terms", errors)
        require_string_list(playbook.get("search_queries"), f"source playbook {path.name} missing search_queries", errors)
        sources = playbook.get("sources")
        require(isinstance(sources, list) and bool(sources), f"source playbook {path.name} missing sources", errors)
        if not isinstance(sources, list):
            continue
        seen_titles: set[str] = set()
        for index, source in enumerate(sources):
            require(isinstance(source, dict), f"source playbook {path.name} source #{index} is not an object", errors)
            if not isinstance(source, dict):
                continue
            title = str(source.get("title") or "").strip()
            require(bool(title), f"source playbook {path.name} source #{index} missing title", errors)
            if title:
                require(title not in seen_titles, f"source playbook {path.name} duplicate source title: {title}", errors)
                seen_titles.add(title)
            require(bool(str(source.get("type") or "").strip()), f"source playbook {path.name} source {title or index} missing type", errors)
            require(bool(str(source.get("source_class") or "").strip()), f"source playbook {path.name} source {title or index} missing source_class", errors)
            require(bool(str(source.get("expected_use") or "").strip()), f"source playbook {path.name} source {title or index} missing expected_use", errors)
    return count


def check_event_playbooks(errors: list[str]) -> int:
    playbook_dir = REPO_ROOT / "EyeOfTerror" / "Scriptorium" / "Brigade" / "NoosphericExtractor" / "playbooks"
    count = 0
    for path in sorted(playbook_dir.glob("*.json")):
        count += 1
        try:
            playbook = load_json(path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            require(False, f"event playbook {path.name} is invalid: {exc}", errors)
            continue
        require_string_list(playbook.get("match_terms"), f"event playbook {path.name} missing match_terms", errors)
        events = playbook.get("events")
        require(isinstance(events, list) and bool(events), f"event playbook {path.name} missing events", errors)
        if not isinstance(events, list):
            continue
        seen_event_ids: set[str] = set()
        required_count = 0
        for index, event in enumerate(events):
            require(isinstance(event, dict), f"event playbook {path.name} event #{index} is not an object", errors)
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("event_id") or "").strip()
            require(bool(event_id), f"event playbook {path.name} event #{index} missing event_id", errors)
            if event_id:
                require(event_id not in seen_event_ids, f"event playbook {path.name} duplicate event_id: {event_id}", errors)
                seen_event_ids.add(event_id)
            for field in ("summary", "narrative_ru", "phase", "confidence"):
                require(bool(str(event.get(field) or "").strip()), f"event playbook {path.name} event {event_id or index} missing {field}", errors)
            require_string_list(event.get("source_refs"), f"event playbook {path.name} event {event_id or index} missing source_refs", errors)
            require_string_list(event.get("evidence_markers"), f"event playbook {path.name} event {event_id or index} missing evidence_markers", errors)
            if event.get("required_for_review"):
                required_count += 1
                require(bool(str(event.get("review_label") or "").strip()), f"event playbook {path.name} required event {event_id or index} missing review_label", errors)
                require_string_list(event.get("draft_markers"), f"event playbook {path.name} required event {event_id or index} missing draft_markers", errors)
        require(required_count > 0, f"event playbook {path.name} has no required_for_review events", errors)
    return count


def run_doctor() -> dict[str, Any]:
    errors: list[str] = []
    checks = [
        ("governors", check_governors),
        ("port_registry", check_port_registry),
        ("worker_manifests", check_worker_manifests),
        ("worker_services", check_worker_services),
        ("source_playbooks", check_source_playbooks),
        ("event_playbooks", check_event_playbooks),
    ]
    counts: dict[str, int] = {}
    for name, check in checks:
        counts[name] = check(errors)
    return {"ok": not errors, "checks": [name for name, _ in checks], "counts": counts, "errors": errors}


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
