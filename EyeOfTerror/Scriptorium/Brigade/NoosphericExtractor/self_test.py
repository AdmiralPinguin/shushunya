#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from noospheric_extractor import run as run_without_model


MODEL_BRAIN = {"ok": True, "status": "answered", "content": "{\"status\":\"ok\"}"}


def run(request: dict, *args, **kwargs) -> dict:
    enriched = dict(request)
    enriched["model_brain"] = MODEL_BRAIN
    return run_without_model(enriched, *args, **kwargs)


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
                            "source_class": "curated_wiki",
                            "source_type": "curated_wiki",
                            "ok": True,
                            "text_excerpt": "Kharn convinced officers to parlay on a moon of Skalathrax.",
                        },
                        {
                            "source_title": "Kharn: Eater of Worlds",
                            "source_class": "official_primary_narrative",
                            "source_type": "published_primary",
                            "ok": True,
                            "text_excerpt": "Kharn joined a parlay on a moon of Skalathrax before the shelters burned.",
                        },
                        {
                            "source_title": "Blocked",
                            "ok": False,
                            "error": "HTTP Error 403: Forbidden",
                        },
                        {
                            "source_title": "Scripted Archive",
                            "ok": True,
                            "render_required": True,
                            "render_reason": "low extracted text with SPA/runtime markers",
                            "text_excerpt": "",
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
        if "луне Скалатракса" not in moon.get("narrative_ru", "") or moon.get("review_label") != "moon parley":
            raise AssertionError(f"playbook narrative and review metadata should be preserved in event notes: {moon}")
        if not moon.get("evidence_snapshots"):
            raise AssertionError("moon parley should include snapshot evidence")
        if "parlay on a moon" not in moon["evidence_snapshots"][0].get("excerpt", ""):
            raise AssertionError(f"snapshot evidence should preserve matched excerpt: {moon}")
        if not moon.get("primary_evidence_snapshots") or not moon["primary_evidence_snapshots"][0].get("is_primary_source"):
            raise AssertionError(f"primary evidence should be separated from secondary evidence: {moon}")
        if (
            moon.get("evidence_status") != "snapshot_matched"
            or data.get("summary", {}).get("events_with_evidence", 0) < 1
            or data.get("summary", {}).get("events_with_primary_evidence", 0) < 1
            or data.get("summary", {}).get("primary_snapshot_count") != 1
        ):
            raise AssertionError(f"event evidence status should be summarized: {data}")
        if not any("HTTP Error 403" in gap for gap in data.get("gaps", [])):
            raise AssertionError("snapshot fetch failures should be reported as gaps")
        if not any("requires browser render" in gap for gap in data.get("gaps", [])):
            raise AssertionError("render-required snapshots should be reported as gaps")
        generic_root = Path(temp_dir) / "generic"
        generic_root.mkdir(parents=True, exist_ok=True)
        (generic_root / "source_map.json").write_text(
            json.dumps({"topic": "Unknown battle", "sources": [{"title": "Recovered Chronicle"}]}),
            encoding="utf-8",
        )
        (generic_root / "source_snapshots.json").write_text(
            json.dumps(
                {
                    "snapshots": [
                        {
                            "source_title": "Recovered Chronicle",
                            "source_class": "official_primary_narrative",
                            "source_type": "published_primary",
                            "requested_url": "https://example.test/chronicle",
                            "ok": True,
                            "text_excerpt": "The first clash began at dawn. Later commentary drifts into speculation.",
                        }
                    ],
                    "skipped": [],
                }
            ),
            encoding="utf-8",
        )
        generic_result = run(
            {"task_id": "test-generic:fact_extraction", "step": {"expected_artifacts": ["/work/generic/direct_event_notes.json"]}},
            Path(temp_dir),
        )
        if not generic_result.get("ok"):
            raise AssertionError(f"NoosphericExtractor generic fallback failed: {generic_result}")
        generic_data = json.loads((generic_root / "direct_event_notes.json").read_text(encoding="utf-8"))
        generic_events = generic_data.get("events", [])
        if (
            generic_data.get("extraction_method") != "generic_snapshot_leads"
            or not generic_events
            or generic_events[0].get("extraction_method") != "generic_snapshot_lead"
            or generic_events[0].get("confidence") != "low"
            or "first clash began" not in generic_events[0].get("summary", "")
            or generic_data.get("summary", {}).get("low_confidence_events") != 1
            or generic_data.get("summary", {}).get("events_with_primary_evidence") < 1
        ):
            raise AssertionError(f"generic fallback should create low-confidence evidence leads: {generic_data}")
    print("[ok] NoosphericExtractor event notes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
