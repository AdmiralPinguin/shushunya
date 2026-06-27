#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from noospheric_extractor import run


def main() -> int:
    request = {
        "task_id": "test-skalathrax:fact_extraction",
        "step": {
            "expected_artifacts": ["/work/skalathrax/direct_event_notes.json"],
        },
    }
    source_map = {
        "topic": "Skalathrax",
        "sources": [
            {"title": "Lexicanum: Battle of Skalathrax"},
            {"title": "Lexicanum: Dreagher"},
            {"title": "Kharn: Eater of Worlds"},
        ],
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        source_path = Path(temp_dir) / "skalathrax" / "source_map.json"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(json.dumps(source_map), encoding="utf-8")
        result = run(request, Path(temp_dir))
        if not result.get("ok"):
            raise AssertionError(f"NoosphericExtractor failed: {result}")
        output = Path(temp_dir) / "skalathrax" / "direct_event_notes.json"
        data = json.loads(output.read_text(encoding="utf-8"))
        event_ids = {event.get("event_id") for event in data.get("events", [])}
        required = {"moon_parley", "dreagher_shoots_anteus", "golden_absolute", "kharn_burns_shelters"}
        if not required.issubset(event_ids):
            raise AssertionError(f"missing key Skalathrax events: {required - event_ids}")
    print("[ok] NoosphericExtractor event notes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
