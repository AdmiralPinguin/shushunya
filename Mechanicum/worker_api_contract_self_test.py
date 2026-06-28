#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from worker_runtime import worker_api_endpoints


def documented_endpoints() -> set[str]:
    path = Path(__file__).resolve().parents[1] / "EyeOfTerror" / "contracts" / "worker_api.md"
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
    advertised = set(worker_api_endpoints())
    documented = documented_endpoints()
    missing = sorted(advertised - documented)
    extra = sorted(documented - advertised)
    if missing or extra:
        raise AssertionError(f"Worker API contract mismatch: missing={missing} extra={extra}")
    print("[ok] Worker API contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
