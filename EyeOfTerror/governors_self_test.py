#!/usr/bin/env python3
from __future__ import annotations

from eye_of_terror.governors import governor_by_name, governor_refs


def main() -> int:
    refs = governor_refs()
    names = {ref.name for ref in refs}
    if "IskandarKhayon" not in names:
        raise AssertionError(names)
    iskandar = governor_by_name("IskandarKhayon")
    if not iskandar or not iskandar.active() or iskandar.port != 7101:
        raise AssertionError(iskandar)
    code = governor_by_name("CogitatorCodewrightGovernor")
    if not code or code.active():
        raise AssertionError(code)
    print("[ok] governor registry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
