#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from scriptorium_daemon import run as run_with_model


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def fake_guidance(role: str, payload: dict, instructions: str) -> dict:
    if role != "ScriptoriumDaemon":
        raise AssertionError(f"unexpected role: {role}")
    if not payload.get("timeline") and not payload.get("synthesis_plan"):
        raise AssertionError(f"writer model payload should include timeline or synthesis_plan: {payload}")
    return {
        "ok": True,
        "status": "answered",
        "role": role,
        "content": json.dumps(
            {
                "status": "draft_augmented",
                "appendix_markdown": "Модельная редактура сохранила источниковые ограничения и усилила связность черновика.",
                "warnings": [],
            },
            ensure_ascii=False,
        ),
    }


def run(request: dict, root: Path) -> dict:
    return run_with_model(request, root, request_guidance=fake_guidance)


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
                ],
                "skipped": [{"source_title": "Missing Book", "reason": "no public URL in source map"}],
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
                                "source_title": "Kharn Eater Worlds local",
                                "source_class": "local_primary_candidate",
                                "source_type": "local_primary",
                                "matched_markers": "parley",
                                "excerpt": "Kharn convinced the officers to parley on a moon of Skalathrax before the fighting spread.",
                                "is_primary_source": True,
                            }
                        ],
                        "primary_evidence_snapshots": [
                            {
                                "source_title": "Kharn Eater Worlds local",
                                "matched_markers": "parley",
                                "excerpt": "Kharn convinced the officers to parley on a moon of Skalathrax before the fighting spread.",
                                "is_primary_source": True,
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
        write_json(
            base / "research_corpus.json",
            {
                "topic": "Skalathrax",
                "sources": [{"title": "Kharn: Eater of Worlds"}],
                "claims": [
                    {
                        "claim_id": "event_claim_1",
                        "claim": "moon parley",
                        "confidence": "medium",
                        "source_refs": ["Kharn: Eater of Worlds"],
                        "evidence_refs": ["Kharn Eater Worlds local"],
                    }
                ],
                "events": [{"event_id": "moon_parley", "summary": "moon parley"}],
                "evidence_excerpts": [{"quote_id": "evidence_1", "source_ref": "Kharn Eater Worlds local", "excerpt": "parley on a moon"}],
                "gaps": ["needs chapter evidence"],
            },
        )
        write_json(base / "structure_map.json", {"topic": "Skalathrax", "topic_structure": [], "timeline": [{"event_id": "moon_parley"}]})
        write_json(
            base / "synthesis_plan.json",
            {
                "topic": "Skalathrax",
                "intent": "event_reconstruction",
                "output_mode": "event_reconstruction",
                "needs_timeline": True,
                "sections": [
                    {"section_id": "source_base", "title": "Источники", "requires_evidence": True, "required_claim_refs": ["event_claim_1"]},
                    {"section_id": "chronology", "title": "Хронология", "requires_evidence": True, "required_claim_refs": ["event_claim_1"]},
                ],
                "evidence_trace": {"claim_refs": ["event_claim_1"]},
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
        if "Kharn Eater Worlds local [primary]; markers: parley" not in reconstruction:
            raise AssertionError("reconstruction should expose evidence excerpts")
        if "Надёжность источников" not in reconstruction or "Ready for extraction: no" not in coverage:
            raise AssertionError("draft package should expose source coverage readiness")
        if "Источники и доступность" not in reconstruction or "availability=fetched" not in reconstruction:
            raise AssertionError("reconstruction should include source inventory and availability")
        if "direct_evidence=matched 1 event marker(s); primary matched 1" not in reconstruction:
            raise AssertionError("reconstruction should disclose source-level direct evidence coverage")
        if "Discovery status: playbook_matched" not in coverage or "Sources mapped: 1" not in coverage or "moon_parley" not in coverage:
            raise AssertionError("coverage report is incomplete")
        if "evidence=Kharn Eater Worlds local: parley" not in coverage:
            raise AssertionError("coverage report should include event evidence")
        if "primary_evidence=Kharn Eater Worlds local: parley" not in coverage:
            raise AssertionError("coverage report should include primary event evidence")
        if "excerpts=Kharn convinced the officers" not in coverage:
            raise AssertionError("coverage report should include evidence excerpts")
        if "evidence_lead_1" not in coverage or "method=generic_snapshot_lead" not in coverage or "evidence_lead=true" not in coverage:
            raise AssertionError("coverage report should preserve generic evidence lead metadata")
        if "Revision Context" not in coverage or "Source step: critic_review" not in coverage:
            raise AssertionError("coverage report should expose revision context")
        if "Модельная редактура" not in reconstruction or "усилила связность" not in reconstruction:
            raise AssertionError("reconstruction should include model writer guidance")
        if "Model Guidance" not in coverage or "Status: answered" not in coverage:
            raise AssertionError("coverage report should record model writer guidance")
        guidance = json.loads((base / "scriptorium_model_guidance.json").read_text(encoding="utf-8"))
        if guidance.get("status") != "answered":
            raise AssertionError(f"model guidance artifact missing status: {guidance}")
        book_base = root / "book"
        write_json(
            book_base / "source_map.json",
            {"topic": "Book Topic", "sources": [{"title": "Primary"}], "coverage_gaps": ["needs more primary text"]},
        )
        write_json(book_base / "source_snapshots.json", {"snapshots": [], "skipped": []})
        write_json(book_base / "direct_event_notes.json", {"events": [], "gaps": ["needs more primary text"]})
        write_json(
            book_base / "research_corpus.json",
            {
                "topic": "Book Topic",
                "sources": [{"title": "Primary"}],
                "claims": [
                    {"claim_id": "claim_1", "claim": "Первое подтвержденное утверждение.", "confidence": "medium", "source_refs": ["Primary"]},
                    {"claim_id": "claim_2", "claim": "Второе подтвержденное утверждение.", "confidence": "medium", "source_refs": ["Primary"]},
                ],
                "evidence_excerpts": [{"quote_id": "evidence_1", "source_ref": "Primary", "excerpt": "Первое утверждение."}],
                "gaps": ["needs more primary text"],
            },
        )
        write_json(book_base / "structure_map.json", {"topic": "Book Topic", "topic_structure": [], "gaps": []})
        write_json(
            book_base / "synthesis_plan.json",
            {
                "topic": "Book Topic",
                "intent": "book",
                "output_mode": "book_manuscript",
                "needs_chapters": True,
                "sections": [
                    {"section_id": "source_base", "title": "Источники", "requires_evidence": True, "required_claim_refs": ["claim_1"]},
                    {"section_id": "book_body", "title": "Основная часть", "requires_evidence": True, "required_claim_refs": ["claim_2"]},
                ],
                "unsupported_sections": [],
                "evidence_trace": {"claim_refs": ["claim_1", "claim_2"]},
            },
        )
        write_json(
            book_base / "chapter_plan.json",
            {
                "chapters": [
                    {"chapter_id": "chapter_01", "title": "Глава 1", "required_claim_refs": ["claim_1"]},
                    {"chapter_id": "chapter_02", "title": "Глава 2", "required_claim_refs": ["claim_2"]},
                    {"chapter_id": "chapter_03", "title": "Глава 3", "section_refs": ["book_close"]},
                ]
            },
        )
        book_result = run(
            {
                "task_id": "test-book:draft_reconstruction",
                "step": {
                    "expected_artifacts": [
                        "/work/book/reconstruction_ru.md",
                        "/work/book/coverage_report.md",
                        "/work/book/chapters/chapter_01.md",
                        "/work/book/chapters/chapter_02.md",
                        "/work/book/chapters/chapter_03.md",
                        "/work/book/continuity_report.json",
                        "/work/book/editor_report.json",
                        "/work/book/manuscript_ru.md",
                        "/work/book/manuscript.fb2",
                    ]
                },
            },
            root,
        )
        if not book_result.get("ok"):
            raise AssertionError(f"ScriptoriumDaemon book mode failed: {book_result}")
        book_draft = (book_base / "reconstruction_ru.md").read_text(encoding="utf-8")
        book_coverage = (book_base / "coverage_report.md").read_text(encoding="utf-8")
        if "Output mode: book_manuscript" not in book_draft or "Evidence trace: claim_1" not in book_draft:
            raise AssertionError(f"book draft should use synthesis plan and evidence trace: {book_draft}")
        if "Evidence Trace" not in book_coverage or "Unsupported Sections" not in book_coverage:
            raise AssertionError(f"book coverage should expose evidence trace and unsupported sections: {book_coverage}")
        for filename in [
            "chapters/chapter_01.md",
            "chapters/chapter_02.md",
            "chapters/chapter_03.md",
            "continuity_report.json",
            "editor_report.json",
            "manuscript_ru.md",
            "manuscript.fb2",
        ]:
            if not (book_base / filename).exists():
                raise AssertionError(f"book mode did not write artifact: {filename}")
        chapter_1 = (book_base / "chapters/chapter_01.md").read_text(encoding="utf-8")
        chapter_2 = (book_base / "chapters/chapter_02.md").read_text(encoding="utf-8")
        chapter_3 = (book_base / "chapters/chapter_03.md").read_text(encoding="utf-8")
        if chapter_1 == chapter_2 or "Evidence trace: claim_1" not in chapter_1 or "Evidence trace: claim_2" not in chapter_2:
            raise AssertionError("book chapters should be chapter-specific, not duplicated whole-draft copies")
        if "не развернута" not in chapter_3:
            raise AssertionError("chapter without evidence should be explicitly blocked instead of invented")
        continuity = json.loads((book_base / "continuity_report.json").read_text(encoding="utf-8"))
        if continuity.get("status") != "needs_revision" or "chapter_03" not in continuity.get("missing_evidence_trace_chapters", []):
            raise AssertionError(f"continuity report should block ungrounded chapters: {continuity}")
        editor = json.loads((book_base / "editor_report.json").read_text(encoding="utf-8"))
        if editor.get("status") != "completed" or editor.get("grounded_chapter_count") != 2:
            raise AssertionError(f"editor report should summarize grounded chapters: {editor}")
        fb2 = (book_base / "manuscript.fb2").read_text(encoding="utf-8")
        if fb2.count("<section>") != 3:
            raise AssertionError(f"fb2 should preserve chapter section boundaries: {fb2}")
    print("[ok] ScriptoriumDaemon draft")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
