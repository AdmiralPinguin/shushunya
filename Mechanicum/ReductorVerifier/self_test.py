#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from reductor_verifier import run


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, payload: dict) -> None:
    write(path, json.dumps(payload, ensure_ascii=False))


def main() -> int:
    request = {
        "task_id": "test-skalathrax:critic_review",
        "step": {"expected_artifacts": ["/work/skalathrax/critic_report.json"]},
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        base = root / "skalathrax"
        write_json(
            base / "source_map.json",
            {
                "sources": [
                    {"title": "Kharn: Eater of Worlds", "source_class": "official_primary_narrative"},
                    {"title": "Lexicanum: Battle of Skalathrax", "source_class": "secondary_wiki"},
                ]
            },
        )
        write_json(base / "source_snapshots.json", {"snapshots": [{"source_title": "Lexicanum", "ok": True}], "skipped": []})
        events = [
            "moon_parley",
            "dreagher_shoots_anteus",
            "golden_absolute",
            "cold_night_shelters",
            "kharn_burns_shelters",
            "fratricide_spreads",
        ]
        write_json(
            base / "direct_event_notes.json",
            {
                "events": [
                    {"event_id": item, "evidence_snapshots": [{"source_title": "Lexicanum", "matched_markers": item}]}
                    for item in events
                ],
                "gaps": ["gap"],
            },
        )
        write_json(base / "timeline.json", {"timeline": [{"event_id": item} for item in events], "gaps": ["gap"]})
        write(
            base / "reconstruction_ru.md",
            "На луне Скалатракса были переговоры. Дреагер стреляет в Антея. Golden Absolute. "
            "ночь Скалатракса и укрытия. Кхарн выжигает убежища. Пожиратели Миров стали резать друг друга. "
            "## Что еще надо проверить\n- gap\n",
        )
        write(base / "coverage_report.md", "## Gaps\n- gap\n")
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"ReductorVerifier failed: {result}")
        report = json.loads((base / "critic_report.json").read_text(encoding="utf-8"))
        if not report["approved"] or report["status"] != "passed_with_warnings":
            raise AssertionError(f"expected approved with warnings: {report}")
        write_json(base / "timeline.json", {"timeline": [{"event_id": "moon_parley"}], "gaps": []})
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"ReductorVerifier failed on second pass: {result}")
        report = json.loads((base / "critic_report.json").read_text(encoding="utf-8"))
        if report["approved"] or report["status"] != "needs_revision":
            raise AssertionError(f"expected missing events to fail: {report}")
        write_json(base / "timeline.json", {"timeline": [{"event_id": item} for item in events], "gaps": []})
        write_json(base / "direct_event_notes.json", {"events": [{"event_id": item} for item in events], "gaps": []})
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"ReductorVerifier failed on evidence pass: {result}")
        report = json.loads((base / "critic_report.json").read_text(encoding="utf-8"))
        if report["approved"] or "lacks fetched source evidence" not in json.dumps(report):
            raise AssertionError(f"expected missing evidence to fail: {report}")
    print("[ok] ReductorVerifier review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
