#!/usr/bin/env python3
from __future__ import annotations

import json

from lexmechanic import default_search, source_map_for_contract


def main() -> int:
    source_map = source_map_for_contract({"goal": "Battle of Skalathrax Kharn"}, default_search)
    results = source_map.get("discovery_results", [])
    if not results:
        raise AssertionError("live discovery did not record any query results")
    if not any(item.get("ok") and item.get("results") for item in results):
        raise AssertionError(json.dumps(results, ensure_ascii=False, indent=2))
    print("[ok] Lexmechanic live discovery")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
