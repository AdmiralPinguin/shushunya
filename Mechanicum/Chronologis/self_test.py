#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from chronologis import run


def main() -> int:
    request = {
        "task_id": "test-skalathrax:timeline",
        "step": {"expected_artifacts": ["/work/skalathrax/timeline.json"]},
    }
    notes = {
        "topic": "Skalathrax",
        "events": [
            {"event_id": "kharn_burns_shelters", "phase": "betrayal", "summary": "burns shelters", "confidence": "high"},
            {"event_id": "moon_parley", "phase": "parley", "summary": "moon parley", "confidence": "medium"},
            {"event_id": "ec_claim_system", "phase": "prelude", "summary": "claim", "confidence": "high"},
            {"event_id": "legion_fractures", "phase": "aftermath_boundary", "summary": "fractures", "confidence": "high"},
        ],
        "gaps": ["gap"],
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        notes_path = Path(temp_dir) / "skalathrax" / "direct_event_notes.json"
        notes_path.parent.mkdir(parents=True, exist_ok=True)
        notes_path.write_text(json.dumps(notes), encoding="utf-8")
        result = run(request, Path(temp_dir))
        if not result.get("ok"):
            raise AssertionError(f"Chronologis failed: {result}")
        data = json.loads((Path(temp_dir) / "skalathrax" / "timeline.json").read_text(encoding="utf-8"))
        ordered = [item["event_id"] for item in data["timeline"]]
        if ordered != ["ec_claim_system", "moon_parley", "kharn_burns_shelters", "legion_fractures"]:
            raise AssertionError(f"timeline order is wrong: {ordered}")
        if not data["contradictions"]:
            raise AssertionError("timeline should flag aftermath boundary")
    print("[ok] Chronologis timeline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
