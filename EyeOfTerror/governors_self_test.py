#!/usr/bin/env python3
from __future__ import annotations

from eye_of_terror.governors import governor_by_name, governor_refs
from eye_of_terror.inner_circle.iskandar_service import service_capabilities


def main() -> int:
    refs = governor_refs()
    names = {ref.name for ref in refs}
    if "IskandarKhayon" not in names:
        raise AssertionError(names)
    iskandar = governor_by_name("IskandarKhayon")
    if not iskandar or not iskandar.active() or iskandar.port != 7101:
        raise AssertionError(iskandar)
    iskandar_capabilities = service_capabilities()
    if sorted(iskandar_capabilities.get("task_kinds", [])) != sorted(iskandar.task_kinds):
        raise AssertionError(f"Iskandar task kinds disagree with registry: {iskandar_capabilities}")
    code = governor_by_name("CogitatorCodewrightGovernor")
    if not code or code.active():
        raise AssertionError(code)
    print("[ok] governor registry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
