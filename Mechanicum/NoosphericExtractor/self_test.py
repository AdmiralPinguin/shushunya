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
        snapshots_path = Path(temp_dir) / "skalathrax" / "source_snapshots.json"
        snapshots_path.write_text(
            json.dumps(
                {
                    "snapshots": [
                        {
                            "source_title": "Lexicanum: Battle of Skalathrax",
                            "ok": True,
                            "text_excerpt": "Kharn convinced officers to parlay on a moon of Skalathrax.",
                        },
                        {
                            "source_title": "Blocked",
                            "ok": False,
                            "error": "HTTP Error 403: Forbidden",
                        },
                    ],
                    "skipped": [{"source_title": "Kharn: Eater of Worlds", "reason": "no public URL in source map"}],
                }
            ),
            encoding="utf-8",
        )
        result = run(request, Path(temp_dir))
        if not result.get("ok"):
            raise AssertionError(f"NoosphericExtractor failed: {result}")
        output = Path(temp_dir) / "skalathrax" / "direct_event_notes.json"
        data = json.loads(output.read_text(encoding="utf-8"))
        event_ids = {event.get("event_id") for event in data.get("events", [])}
        required = {"moon_parley", "dreagher_shoots_anteus", "golden_absolute", "kharn_burns_shelters"}
        if not required.issubset(event_ids):
            raise AssertionError(f"missing key Skalathrax events: {required - event_ids}")
        moon = next(event for event in data["events"] if event.get("event_id") == "moon_parley")
        if not moon.get("evidence_snapshots"):
            raise AssertionError("moon parley should include snapshot evidence")
        if not any("HTTP Error 403" in gap for gap in data.get("gaps", [])):
            raise AssertionError("snapshot fetch failures should be reported as gaps")
    print("[ok] NoosphericExtractor event notes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
