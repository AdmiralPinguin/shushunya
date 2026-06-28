#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from eye_of_terror.warmaster_gateway import gateway_capabilities


def documented_endpoints() -> set[str]:
    path = Path(__file__).resolve().parent / "contracts" / "warmaster_api.md"
    endpoints: set[str] = set()
    in_block = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "```text":
            in_block = True
            continue
        if in_block and line.strip() == "```":
            break
        if in_block and line.strip():
            method, endpoint = line.split(maxsplit=1)
            endpoints.add(f"{method} {endpoint}")
    return endpoints


def main() -> int:
    advertised = set(gateway_capabilities()["endpoints"])
    documented = documented_endpoints()
    missing = sorted(advertised - documented)
    extra = sorted(documented - advertised)
    if missing or extra:
        raise AssertionError(f"Warmaster API contract mismatch: missing={missing} extra={extra}")
    print("[ok] Warmaster API contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
