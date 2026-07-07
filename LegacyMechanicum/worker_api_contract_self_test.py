#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from worker_runtime import worker_api_endpoints


def documented_endpoints() -> set[str]:
    path = Path(__file__).resolve().parents[1] / "EyeOfTerror" / "Warmaster" / "contracts" / "worker_api.md"
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


def contract_text() -> str:
    path = Path(__file__).resolve().parents[1] / "EyeOfTerror" / "Warmaster" / "contracts" / "worker_api.md"
    return path.read_text(encoding="utf-8")


def main() -> int:
    advertised = set(worker_api_endpoints())
    documented = documented_endpoints()
    missing = sorted(advertised - documented)
    extra = sorted(documented - advertised)
    if missing or extra:
        raise AssertionError(f"Worker API contract mismatch: missing={missing} extra={extra}")
    if "worker mismatch" not in contract_text():
        raise AssertionError("Worker API contract must document dispatch worker mismatch rejection")
    if '"worker_report"' not in contract_text() or "canonical command-protocol result" not in contract_text():
        raise AssertionError("Worker API contract must document canonical worker_report responses")
    print("[ok] Worker API contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
