#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from lexmechanic import run


def main() -> int:
    request = {
        "task_id": "test-skalathrax:source_discovery",
        "contract": {
            "goal": "Собери все известное о событиях Скалатракса и сделай реконструкцию.",
        },
        "step": {
            "expected_artifacts": ["/work/skalathrax/source_map.json"],
        },
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        result = run(request, Path(temp_dir))
        if not result.get("ok"):
            raise AssertionError(f"Lexmechanic failed: {result}")
        output = Path(temp_dir) / "skalathrax" / "source_map.json"
        if not output.exists():
            raise AssertionError(f"source map was not written: {output}")
        data = json.loads(output.read_text(encoding="utf-8"))
        titles = {item.get("title") for item in data.get("sources", [])}
        required = {"Lexicanum: Battle of Skalathrax", "Kharn: Eater of Worlds"}
        if not required.issubset(titles):
            raise AssertionError(f"source map lacks required source candidates: {titles}")
        if not data.get("coverage_gaps"):
            raise AssertionError("source map must include coverage gaps")
    print("[ok] Lexmechanic source map")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
