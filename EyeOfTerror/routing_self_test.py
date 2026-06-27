#!/usr/bin/env python3
from __future__ import annotations

from eye_of_terror.routing import route_message


def main() -> int:
    lore = route_message("Собери события Скалатракса")
    if not lore.ok or lore.governor != "IskandarKhayon":
        raise AssertionError(lore)
    code = route_message("почини баг в python приложении")
    if code.ok or code.kind != "code":
        raise AssertionError(code)
    unknown = route_message("сделай что-нибудь")
    if unknown.ok or unknown.kind != "general":
        raise AssertionError(unknown)
    print("[ok] Warmaster routing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
