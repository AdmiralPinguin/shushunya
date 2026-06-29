#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from eye_of_terror.governors import governor_by_name, governor_refs
from eye_of_terror.inner_circle.ceraxia_service import service_capabilities as ceraxia_capabilities
from eye_of_terror.inner_circle.iskandar_service import service_capabilities


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    ports = json.loads((repo_root / "EyeOfTerror" / "registry" / "ports.json").read_text(encoding="utf-8"))
    eye_ports = ports.get("eye_of_terror", {}) if isinstance(ports, dict) else {}
    port_governors = {
        int(port): item
        for port, item in eye_ports.items()
        if isinstance(item, dict) and int(port) >= 7101
    }
    refs = governor_refs()
    names = {ref.name for ref in refs}
    if "IskandarKhayon" not in names:
        raise AssertionError(names)
    seen_ports: dict[int, str] = {}
    for ref in refs:
        owner = seen_ports.setdefault(ref.port, ref.name)
        if owner != ref.name:
            raise AssertionError(f"governor port collision on {ref.port}: {owner} and {ref.name}")
        port_entry = port_governors.get(ref.port)
        if not port_entry:
            raise AssertionError(f"governor missing from ports.json: {ref.name}")
        if port_entry and port_entry.get("name") != ref.name:
            raise AssertionError(f"governor registry and ports.json disagree for {ref.name}: {port_entry}")
        if ref.active() and not ref.service:
            raise AssertionError(f"active governor must declare service: {ref}")
        if not ref.active() and ref.service:
            raise AssertionError(f"planned governor should not declare a runnable service yet: {ref}")
        if not ref.route_terms:
            raise AssertionError(f"governor must declare route_terms: {ref}")
    iskandar = governor_by_name("IskandarKhayon")
    if not iskandar or not iskandar.active() or iskandar.port != 7101:
        raise AssertionError(iskandar)
    iskandar_capabilities = service_capabilities()
    if sorted(iskandar_capabilities.get("task_kinds", [])) != sorted(iskandar.task_kinds):
        raise AssertionError(f"Iskandar task kinds disagree with registry: {iskandar_capabilities}")
    code = governor_by_name("CogitatorCodewrightGovernor")
    if not code or code.active():
        raise AssertionError(code)
    ceraxia = governor_by_name("Ceraxia")
    if not ceraxia or not ceraxia.active() or ceraxia.port != 7104:
        raise AssertionError(ceraxia)
    ceraxia_payload = ceraxia_capabilities()
    if sorted(ceraxia_payload.get("task_kinds", [])) != sorted(ceraxia.task_kinds):
        raise AssertionError(f"Ceraxia task kinds disagree with registry: {ceraxia_payload}")
    print("[ok] governor registry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
