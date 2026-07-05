#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from scriptorium_architect import run as run_without_model


MODEL_BRAIN = {
    "ok": True,
    "status": "answered",
    "content": json.dumps(
        {
            "status": "ok",
            "book_outline": {
                "chapters": [
                    {
                        "chapter_id": "chapter_01",
                        "title": "Модельная глава с доказательством",
                        "section_refs": ["source_base"],
                        "required_claim_refs": ["claim_1"],
                    },
                    {
                        "chapter_id": "chapter_02",
                        "title": "Недоказанная модельная глава",
                        "section_refs": ["book_body"],
                        "required_claim_refs": ["missing_claim"],
                    },
                ]
            },
        },
        ensure_ascii=False,
    ),
}


def run(request: dict, *args, **kwargs) -> dict:
    enriched = dict(request)
    enriched["model_brain"] = MODEL_BRAIN
    return run_without_model(enriched, *args, **kwargs)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    request = {
        "task_id": "test-book:synthesis_planning",
        "quality_expectations": {
            "research_intent": {
                "intent": "book",
                "output_mode": "book_manuscript",
                "required_depth": "comprehensive",
                "source_policy": "primary_and_secondary_sources_required",
                "needs_timeline": False,
                "needs_chapters": True,
                "chapter_count": 5,
            }
        },
        "step": {
            "expected_artifacts": [
                "/work/book/synthesis_plan.json",
                "/work/book/book_outline.json",
                "/work/book/chapter_plan.json",
            ]
        },
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        base = root / "book"
        write_json(
            base / "research_corpus.json",
            {
                "topic": "Test manuscript",
                "sources": [{"title": "Primary source"}, {"title": "Secondary source"}],
                "claims": [
                    {"claim_id": "claim_1", "claim": "first claim", "source_refs": ["Primary source"]},
                    {"claim_id": "claim_2", "claim": "second claim", "source_refs": ["Secondary source"]},
                ],
                "evidence_excerpts": [{"quote_id": "evidence_1", "source_ref": "Primary source", "excerpt": "first claim"}],
                "gaps": [],
            },
        )
        write_json(base / "structure_map.json", {"topic": "Test manuscript", "source_order": [{"title": "Primary source"}], "argument_flow": []})
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"ScriptoriumArchitect failed: {result}")
        plan = json.loads((base / "synthesis_plan.json").read_text(encoding="utf-8"))
        outline = json.loads((base / "book_outline.json").read_text(encoding="utf-8"))
        chapter_plan = json.loads((base / "chapter_plan.json").read_text(encoding="utf-8"))
        if plan.get("output_mode") != "book_manuscript" or plan.get("needs_chapters") is not True:
            raise AssertionError(f"bad synthesis plan mode: {plan}")
        if not plan.get("sections") or not plan.get("evidence_trace", {}).get("claim_refs"):
            raise AssertionError(f"synthesis plan should contain sections and evidence trace: {plan}")
        if plan.get("unsupported_sections"):
            raise AssertionError(f"supported corpus should not create unsupported sections: {plan}")
        if plan.get("chapter_count") != 5 or outline.get("chapter_count") != 5 or chapter_plan.get("chapter_count") != 5:
            raise AssertionError(f"book planning should preserve requested chapter count: {plan} {outline} {chapter_plan}")
        if len(outline.get("chapters", [])) != 5 or len(chapter_plan.get("chapters", [])) != 5:
            raise AssertionError(f"book outline and chapter plan should follow requested chapter count: {outline} {chapter_plan}")
        if outline.get("planning_method") != "model_guided_evidence_outline" or chapter_plan.get("chapters", [{}])[0].get("title") != "Модельная глава с доказательством":
            raise AssertionError(f"grounded model outline chapter should be accepted: {outline} {chapter_plan}")
        if "Недоказанная модельная глава" in json.dumps(chapter_plan, ensure_ascii=False):
            raise AssertionError(f"ungrounded model outline chapters must be rejected: {chapter_plan}")
        empty_chapters = [
            chapter.get("chapter_id")
            for chapter in chapter_plan.get("chapters", [])
            if not chapter.get("required_claim_refs")
        ]
        if empty_chapters:
            raise AssertionError(f"book chapter plan should not create ungrounded chapters when claims exist: {chapter_plan}")

        event_base = root / "event"
        write_json(
            event_base / "research_corpus.json",
            {
                "topic": "Event fallback",
                "sources": [{"title": "Primary source"}],
                "claims": [{"claim_id": "claim_1", "claim": "event claim", "source_refs": ["Primary source"]}],
                "evidence_excerpts": [{"quote_id": "evidence_1", "source_ref": "Primary source", "excerpt": "event claim"}],
                "gaps": [],
            },
        )
        write_json(event_base / "structure_map.json", {"topic": "Event fallback", "timeline": [{"event_id": "event_1"}]})
        fallback_request = {
            "task_id": "test-event:synthesis_planning",
            "contract": {
                "quality_gates": ["intent:event_reconstruction", "output_mode:event_reconstruction"],
                "required_artifacts": [
                    "/work/event/timeline.json",
                    "/work/event/structure_map.json",
                    "/work/event/synthesis_plan.json",
                ],
            },
            "step": {"expected_artifacts": ["/work/event/synthesis_plan.json"]},
        }
        fallback_result = run(fallback_request, root)
        if not fallback_result.get("ok"):
            raise AssertionError(f"ScriptoriumArchitect contract fallback failed: {fallback_result}")
        fallback_plan = json.loads((event_base / "synthesis_plan.json").read_text(encoding="utf-8"))
        if fallback_plan.get("output_mode") != "event_reconstruction" or fallback_plan.get("needs_timeline") is not True:
            raise AssertionError(f"contract quality gates should preserve event output mode without oversight: {fallback_plan}")
    print("[ok] ScriptoriumArchitect synthesis plan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
