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
                "sources": [
                    {"title": "Kharn: Eater of Worlds", "source_class": "official_primary_narrative", "reliability": "high"}
                ],
                "coverage_gaps": ["primary text unavailable"],
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
                    }
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
        if "Sources mapped: 1" not in coverage or "moon_parley" not in coverage:
            raise AssertionError("coverage report is incomplete")
    print("[ok] ScriptoriumDaemon draft")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
