#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from scriptorium_daemon import run


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    request = {
        "task_id": "test-skalathrax:draft_reconstruction",
        "revision_context": {
            "reasons": ["Draft does not visibly cover required event: Kharn burns shelters"],
            "source_steps": ["critic_review"],
            "priority": "blocker",
        },
        "step": {
            "expected_artifacts": [
                "/work/skalathrax/reconstruction_ru.md",
                "/work/skalathrax/coverage_report.md",
            ]
        },
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        base = root / "skalathrax"
        write_json(
            base / "source_map.json",
            {
                "topic": "Skalathrax",
                "discovery_status": "playbook_matched",
                "sources": [
                    {"title": "Kharn: Eater of Worlds", "source_class": "official_primary_narrative", "reliability": "high"}
                ],
                "source_coverage": {
                    "source_count": 1,
                    "has_primary_or_publication": True,
                    "has_official": True,
                    "has_secondary_crosscheck": False,
                    "ready_for_extraction": False,
                    "source_types": ["published_primary"],
                },
                "coverage_gaps": ["primary text unavailable"],
            },
        )
        write_json(
            base / "source_snapshots.json",
            {
                "snapshots": [
                    {
                        "source_title": "Kharn: Eater of Worlds",
                        "ok": True,
                        "final_url": "https://example.com",
                        "title": "source",
                    }
                ]
            },
        )
        write_json(
            base / "direct_event_notes.json",
            {
                "topic": "Skalathrax",
                "events": [
                    {
                        "event_id": "moon_parley",
                        "phase": "parley",
                        "summary": "moon parley",
                        "narrative_ru": "На луне Скалатракса прошли переговоры.",
                        "confidence": "medium",
                        "source_refs": ["Kharn: Eater of Worlds"],
                        "evidence_snapshots": [
                            {
                                "source_title": "Kharn: Eater of Worlds",
                                "matched_markers": "parley",
                                "excerpt": "Kharn convinced the officers to parley on a moon of Skalathrax before the fighting spread.",
                            }
                        ],
                    }
                ],
                "gaps": ["needs chapter evidence"],
            },
        )
        write_json(
            base / "timeline.json",
            {
                "topic": "Skalathrax",
                "timeline": [
                    {
                        "event_id": "moon_parley",
                        "phase": "parley",
                        "summary": "moon parley",
                        "confidence": "medium",
                        "source_refs": ["Kharn: Eater of Worlds"],
                    },
                    {
                        "event_id": "evidence_lead_1",
                        "phase": "unknown",
                        "summary": "generic lead",
                        "confidence": "low",
                        "source_refs": ["Recovered Chronicle"],
                        "extraction_method": "generic_snapshot_lead",
                        "evidence_lead": True,
                    },
                ],
                "gaps": ["needs chapter evidence"],
                "contradictions": [{"topic": "direct events vs aftermath", "note": "keep aftermath separate"}],
            },
        )
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"ScriptoriumDaemon failed: {result}")
        reconstruction = (base / "reconstruction_ru.md").read_text(encoding="utf-8")
        coverage = (base / "coverage_report.md").read_text(encoding="utf-8")
        required = ["На луне Скалатракса", "Что еще надо проверить", "direct events vs aftermath"]
        for needle in required:
            if needle not in reconstruction:
                raise AssertionError(f"missing reconstruction text: {needle}")
        if "Фокус ревизии" not in reconstruction or "Kharn burns shelters" not in reconstruction:
            raise AssertionError("reconstruction should expose revision context")
        if "Kharn: Eater of Worlds; markers: parley" not in reconstruction:
            raise AssertionError("reconstruction should expose evidence excerpts")
        if "Надёжность источников" not in reconstruction or "Ready for extraction: no" not in coverage:
            raise AssertionError("draft package should expose source coverage readiness")
        if "Discovery status: playbook_matched" not in coverage or "Sources mapped: 1" not in coverage or "moon_parley" not in coverage:
            raise AssertionError("coverage report is incomplete")
        if "evidence=Kharn: Eater of Worlds: parley" not in coverage:
            raise AssertionError("coverage report should include event evidence")
        if "excerpts=Kharn convinced the officers" not in coverage:
            raise AssertionError("coverage report should include evidence excerpts")
        if "evidence_lead_1" not in coverage or "method=generic_snapshot_lead" not in coverage or "evidence_lead=true" not in coverage:
            raise AssertionError("coverage report should preserve generic evidence lead metadata")
        if "Revision Context" not in coverage or "Source step: critic_review" not in coverage:
            raise AssertionError("coverage report should expose revision context")
    print("[ok] ScriptoriumDaemon draft")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
