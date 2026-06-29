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
        "summary": {"source_coverage_ready": True},
        "events": [
            {"event_id": "kharn_burns_shelters", "phase": "betrayal", "summary": "burns shelters", "confidence": "high", "evidence_status": "snapshot_matched"},
            {"event_id": "moon_parley", "phase": "parley", "summary": "moon parley", "confidence": "medium", "evidence_status": "missing_snapshot_evidence"},
            {"event_id": "ec_claim_system", "phase": "prelude", "summary": "claim", "confidence": "high"},
            {"event_id": "legion_fractures", "phase": "aftermath_boundary", "summary": "fractures", "confidence": "high"},
            {
                "event_id": "evidence_lead_1",
                "phase": "unknown",
                "summary": "generic lead",
                "confidence": "low",
                "source_class": "secondary",
                "extraction_method": "generic_snapshot_lead",
            },
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
        if ordered != ["ec_claim_system", "moon_parley", "kharn_burns_shelters", "legion_fractures", "evidence_lead_1"]:
            raise AssertionError(f"timeline order is wrong: {ordered}")
        if not data["contradictions"]:
            raise AssertionError("timeline should flag aftermath boundary")
        lead = data["timeline"][-1]
        if not lead.get("evidence_lead") or lead.get("extraction_method") != "generic_snapshot_lead":
            raise AssertionError(f"timeline should preserve generic evidence lead metadata: {lead}")
        if data.get("summary", {}).get("generic_evidence_leads") != 1 or data.get("summary", {}).get("low_confidence_events") != 1:
            raise AssertionError(f"timeline should summarize evidence lead uncertainty: {data.get('summary')}")
        if data.get("summary", {}).get("events_missing_evidence") != 1 or data.get("summary", {}).get("source_coverage_ready") is not True:
            raise AssertionError(f"timeline should summarize evidence coverage: {data.get('summary')}")
    print("[ok] Chronologis timeline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
