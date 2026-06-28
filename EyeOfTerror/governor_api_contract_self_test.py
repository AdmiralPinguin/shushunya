#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from eye_of_terror.inner_circle.iskandar_service import service_capabilities


def documented_endpoints() -> set[str]:
    path = Path(__file__).resolve().parent / "contracts" / "governor_api.md"
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
    capabilities = service_capabilities()
    advertised = set(capabilities["endpoints"])
    documented = documented_endpoints()
    missing = sorted(advertised - documented)
    extra = sorted(documented - advertised)
    if missing or extra:
        raise AssertionError(f"Governor API contract mismatch: missing={missing} extra={extra}")
    text = (Path(__file__).resolve().parent / "contracts" / "governor_api.md").read_text(encoding="utf-8")
    missing_workers = [worker for worker in capabilities.get("required_workers", []) if worker not in text]
    if missing_workers:
        raise AssertionError(f"Governor API contract does not document required workers: {missing_workers}")
    print("[ok] Governor API contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
