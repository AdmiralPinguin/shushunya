#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from start_worker import load_services


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
        if not (repo_root / service["module_path"]).exists():
            raise AssertionError(f"module path missing for {worker}: {service['module_path']}")
    print("[ok] worker services registry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
