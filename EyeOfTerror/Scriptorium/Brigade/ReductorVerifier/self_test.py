#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from reductor_verifier import normalize_revision_plan_for_request, run as run_with_model


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, payload: dict) -> None:
    write(path, json.dumps(payload, ensure_ascii=False))


def fake_guidance(role: str, payload: dict, instructions: str) -> dict:
    if role != "ReductorVerifier":
        raise AssertionError(f"unexpected role: {role}")
    if "reconstruction_preview" not in payload or "hard_findings" not in payload:
        raise AssertionError(f"critic model payload is incomplete: {payload}")
    return {
        "ok": True,
        "status": "answered",
        "role": role,
        "content": json.dumps(
            {
                "status": "passed",
                "warnings": ["semantic critic reviewed chronology and source boundaries"],
                "evidence_notes": ["stubbed model review for self-test"],
            },
            ensure_ascii=False,
        ),
    }


def run(request: dict, root: Path) -> dict:
    return run_with_model(request, root, request_guidance=fake_guidance)


def main() -> int:
    normalized_revision = normalize_revision_plan_for_request(
        {
            "required": True,
            "steps": [
                {
                    "step_id": "timeline",
                    "worker": "Chronologis",
                    "reason": "timeline needs revision",
                    "source": "critic_finding",
                    "priority": "blocker",
                }
            ],
        },
        {
            "quality_expectations": {
                "revision_policy": {
                    "allowed_steps": ["fact_extraction", "structure_mapping", "synthesis_planning", "draft_reconstruction", "critic_review", "finalize"]
                }
            }
        },
    )
    if normalized_revision.get("steps", [{}])[0].get("step_id") != "structure_mapping":
        raise AssertionError(f"research pipeline revision should target structure_mapping instead of legacy timeline: {normalized_revision}")
    request = {
        "task_id": "test-skalathrax:critic_review",
        "step": {"step_id": "critic_review", "expected_artifacts": ["/work/skalathrax/critic_report.json"]},
        "quality_expectations": {
            "step_quality": {
                "step_id": "critic_review",
                "worker": "ReductorVerifier",
                "required_inputs": ["/work/skalathrax/reconstruction_ru.md", "/work/skalathrax/coverage_report.md"],
                "expected_artifacts": ["/work/skalathrax/critic_report.json"],
                "checks": ["critic compares draft against contract, extracted facts, timeline, and coverage report"],
                "blockers": ["missing expected artifact"],
                "revision_targets": ["critic_review", "finalize"],
            },
            "final_review": {"critic_step": "critic_review", "final_step": "finalize"},
            "revision_policy": {"source_step": "critic_review", "final_steps": ["critic_review", "finalize"]},
        },
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        base = root / "skalathrax"
        write_json(base / "corpus_index.json", {"summary": {"sources_matched": 0}, "sources": [], "gaps": []})
        write_json(
            base / "source_map.json",
            {
                "discovery_status": "playbook_matched",
                "sources": [
                    {"title": "Kharn: Eater of Worlds", "source_class": "official_primary_narrative"},
                    {"title": "Lexicanum: Battle of Skalathrax", "source_class": "secondary_wiki"},
                ]
            },
        )
        write_json(base / "source_snapshots.json", {"snapshots": [{"source_title": "Lexicanum", "ok": True}], "skipped": []})
        write_json(base / "rendered_snapshots.json", {"rendered_snapshots": [], "summary": {"render_required": 0}})
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
        write_json(
            base / "timeline.json",
            {
                "timeline": [{"event_id": item} for item in events],
                "summary": {"generic_evidence_leads": 1, "low_confidence_events": 1},
                "gaps": ["gap"],
            },
        )
        write(
            base / "reconstruction_ru.md",
            "На луне Скалатракса были переговоры. Дреагер стреляет в Антея. Golden Absolute. "
            "ночь Скалатракса и укрытия. Кхарн выжигает убежища. Пожиратели Миров стали резать друг друга. "
            "## Фокус ревизии\n- Reason: Draft did not cover shelters\n"
            "## Что еще надо проверить\n- gap\n",
        )
        write(base / "coverage_report.md", "## Revision Context\n- Source step: critic_review\n## Gaps\n- gap\n")
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"ReductorVerifier failed: {result}")
        report = json.loads((base / "critic_report.json").read_text(encoding="utf-8"))
        if not report["approved"] or report["status"] != "passed_with_warnings":
            raise AssertionError(f"expected approved with warnings: {report}")
        if not report.get("revision_focus", {}).get("present"):
            raise AssertionError(f"expected verifier to record revision focus: {report}")
        if report.get("metrics", {}).get("comprehensive_depth", {}).get("passed") is not True:
            raise AssertionError(f"standard task should not fail comprehensive depth checks: {report}")
        if report.get("metrics", {}).get("generic_evidence_leads") != 1 or "generic low-confidence evidence lead" not in json.dumps(report):
            raise AssertionError(f"expected verifier to warn about generic evidence leads: {report}")
        if (
            report.get("quality_expectations", {}).get("check_count") != 1
            or report.get("quality_expectations", {}).get("revision_targets") != ["critic_review", "finalize"]
        ):
            raise AssertionError(f"expected verifier to preserve quality expectations: {report}")
        if report.get("model_guidance", {}).get("status") != "answered":
            raise AssertionError(f"expected verifier to record model critic guidance: {report}")
        if "semantic critic reviewed chronology" not in json.dumps(report.get("warnings", [])):
            raise AssertionError(f"expected verifier to include model critic warnings: {report}")
        write_json(
            base / "source_map.json",
            {
                "discovery_status": "playbook_matched",
                "depth_profile": {
                    "mode": "comprehensive",
                    "min_source_count": 24,
                    "min_live_candidate_count": 8,
                    "min_direct_evidence_sources": 6,
                    "min_primary_evidence_sources": 1,
                    "min_direct_event_count": 10,
                    "min_draft_chars": 60000,
                    "min_required_event_detail_chars": 180,
                    "min_required_event_evidence_chars": 24,
                },
                "source_coverage": {
                    "source_count": 2,
                    "live_candidate_count": 0,
                    "ready_for_extraction": True,
                },
                "sources": [
                    {"title": "Kharn: Eater of Worlds", "type": "novel", "url": "", "source_class": "official_primary_narrative"},
                    {"title": "Lexicanum: Battle of Skalathrax", "source_class": "secondary_wiki"},
                ],
                "corpus_requirements": {
                    "required": True,
                    "missing_count": 1,
                    "missing_primary_texts": [
                        {
                            "title": "Kharn: Eater of Worlds",
                            "suggested_filenames": ["Kharn_Eater_of_Worlds.epub"],
                        }
                    ],
                },
            },
        )
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"ReductorVerifier failed on comprehensive depth pass: {result}")
        report = json.loads((base / "critic_report.json").read_text(encoding="utf-8"))
        if report.get("approved") or report.get("metrics", {}).get("comprehensive_depth", {}).get("passed"):
            raise AssertionError(f"comprehensive under-depth result should block approval: {report}")
        report_text = json.dumps(report)
        if (
            "Comprehensive draft is too short" not in report_text
            or "lacks accessible primary text" not in report_text
            or "too few primary-evidence sources" not in report_text
            or "too few extracted direct events" not in report_text
            or "under-detailed in final draft" not in report_text
        ):
            raise AssertionError(f"comprehensive depth blockers missing: {report}")
        event_coverage = report.get("metrics", {}).get("comprehensive_depth", {}).get("required_event_coverage", {})
        if (
            event_coverage.get("required_event_count") != 6
            or not event_coverage.get("under_detailed_required_events")
            or event_coverage.get("required_events_with_evidence_support") != 0
        ):
            raise AssertionError(f"required event coverage metrics missing: {report}")
        if report.get("metrics", {}).get("comprehensive_depth", {}).get("primary_evidence_source_count") != 0:
            raise AssertionError(f"secondary evidence should not count as primary evidence: {report}")
        revision_workers = {step.get("worker") for step in report.get("revision_plan", {}).get("steps", [])}
        if "CorpusIngestor" not in revision_workers:
            raise AssertionError(f"missing primary corpus blocker should route through CorpusIngestor: {report}")
        requirements = report.get("metrics", {}).get("comprehensive_depth", {}).get("corpus_requirements", {})
        if requirements.get("missing_count") != 1 or requirements.get("missing_primary_texts", [{}])[0].get("title") != "Kharn: Eater of Worlds":
            raise AssertionError(f"critic metrics should preserve corpus requirements: {report}")
        source_map_with_local_primary = json.loads((base / "source_map.json").read_text(encoding="utf-8"))
        source_map_with_local_primary["sources"].insert(
            0,
            {
                "title": "Kharn Eater Worlds local",
                "local_path": "/project/Corpus/Kharn Eater Worlds.epub",
                "corpus_relative_path": "Kharn Eater Worlds.epub",
                "source_class": "local_primary_candidate",
                "discovery_method": "local_corpus",
            },
        )
        source_map_with_local_primary["corpus_requirements"] = {"required": False, "missing_count": 0, "missing_primary_texts": []}
        write_json(base / "source_map.json", source_map_with_local_primary)
        write_json(
            base / "direct_event_notes.json",
            {
                "events": [
                    {"event_id": item, "evidence_snapshots": [{"source_title": "Kharn Eater Worlds local", "matched_markers": item}]}
                    for item in events
                ],
                "gaps": ["gap"],
            },
        )
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"ReductorVerifier failed on local primary match: {result}")
        report = json.loads((base / "critic_report.json").read_text(encoding="utf-8"))
        if "Kharn: Eater of Worlds" in json.dumps(report.get("findings", []), ensure_ascii=False):
            raise AssertionError(f"matching local corpus source should satisfy missing Kharn primary blocker: {report}")
        if report.get("metrics", {}).get("comprehensive_depth", {}).get("primary_evidence_source_count") != 1:
            raise AssertionError(f"local primary evidence should count toward comprehensive depth: {report}")
        write_json(
            base / "source_map.json",
            {
                "discovery_status": "playbook_matched",
                "sources": [
                    {"title": "Kharn: Eater of Worlds", "source_class": "official_primary_narrative"},
                    {"title": "Lexicanum: Battle of Skalathrax", "source_class": "secondary_wiki"},
                ],
            },
        )
        bad_quality_request = json.loads(json.dumps(request))
        bad_quality_request["quality_expectations"]["step_quality"]["expected_artifacts"] = ["/work/skalathrax/wrong.json"]
        result = run(bad_quality_request, root)
        if not result.get("ok"):
            raise AssertionError(f"ReductorVerifier failed on bad quality expectations: {result}")
        report = json.loads((base / "critic_report.json").read_text(encoding="utf-8"))
        if report.get("approved") or "expected_artifacts do not match" not in json.dumps(report):
            raise AssertionError(f"bad quality expectations should block approval: {report}")
        write_json(
            base / "direct_event_notes.json",
            {
                "events": [
                    {"event_id": item, "evidence_snapshots": [{"source_title": "Lexicanum", "matched_markers": item}]}
                    for item in events
                ]
                + [{"event_id": "generic_extra_event", "evidence_snapshots": [{"source_title": "Lexicanum"}]}],
                "gaps": [],
            },
        )
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"ReductorVerifier failed on generic event coverage pass: {result}")
        report = json.loads((base / "critic_report.json").read_text(encoding="utf-8"))
        if report.get("approved") or "generic_extra_event" not in json.dumps(report):
            raise AssertionError(f"generic direct-event coverage should block approval: {report}")
        revision_workers = {step.get("worker") for step in report.get("revision_plan", {}).get("steps", [])}
        if not {"NoosphericExtractor", "Chronologis", "ScriptoriumDaemon"}.issubset(revision_workers):
            raise AssertionError(f"generic direct-event coverage did not produce upstream revision plan: {report}")
        write_json(base / "timeline.json", {"timeline": [{"event_id": "moon_parley"}], "gaps": []})
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"ReductorVerifier failed on second pass: {result}")
        report = json.loads((base / "critic_report.json").read_text(encoding="utf-8"))
        if report["approved"] or report["status"] != "needs_revision":
            raise AssertionError(f"expected missing events to fail: {report}")
        revision_steps = report.get("revision_plan", {}).get("steps", [])
        revision_workers = {step.get("worker") for step in revision_steps}
        if not {"NoosphericExtractor", "Chronologis", "ScriptoriumDaemon"}.issubset(revision_workers):
            raise AssertionError(f"missing event review did not produce a worker rework plan: {report}")
        write_json(base / "timeline.json", {"timeline": [{"event_id": item} for item in events], "gaps": []})
        write_json(base / "direct_event_notes.json", {"events": [{"event_id": item} for item in events], "gaps": []})
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"ReductorVerifier failed on evidence pass: {result}")
        report = json.loads((base / "critic_report.json").read_text(encoding="utf-8"))
        if report["approved"] or "lacks fetched source evidence" not in json.dumps(report):
            raise AssertionError(f"expected missing evidence to fail: {report}")
        revision_steps = report.get("revision_plan", {}).get("steps", [])
        revision_workers = {step.get("worker") for step in revision_steps}
        if not {"AuspexBrowser", "OcularisRenderium", "NoosphericExtractor", "Chronologis", "ScriptoriumDaemon"}.issubset(revision_workers):
            raise AssertionError(f"missing evidence review did not produce source rework plan: {report}")
        source_acquisition_index = next(
            index for index, step in enumerate(revision_steps) if step.get("step_id") == "source_acquisition"
        )
        source_rendering_index = next(
            index for index, step in enumerate(revision_steps) if step.get("step_id") == "source_rendering"
        )
        timeline_index = next(index for index, step in enumerate(revision_steps) if step.get("step_id") == "timeline")
        draft_index = next(index for index, step in enumerate(revision_steps) if step.get("step_id") == "draft_reconstruction")
        if not source_acquisition_index < source_rendering_index < timeline_index < draft_index:
            raise AssertionError(f"revision dependencies are not ordered downstream: {report}")
        qa_request = json.loads(json.dumps(request))
        qa_request["quality_expectations"]["research_intent"] = {
            "intent": "qa_answer",
            "output_mode": "short_answer",
            "required_depth": "standard",
            "source_policy": "answer_with_citations",
            "needs_timeline": False,
            "needs_chapters": False,
        }
        write_json(
            base / "source_map.json",
            {
                "discovery_status": "research_ready",
                "sources": [
                    {"title": "Primary", "source_class": "official_primary_narrative"},
                    {"title": "Secondary", "source_class": "secondary_wiki"},
                ],
                "source_coverage": {"ready_for_extraction": True},
            },
        )
        write_json(base / "direct_event_notes.json", {"events": [], "gaps": []})
        write_json(
            base / "research_corpus.json",
            {
                "sources": [{"title": "Primary"}],
                "claims": [{"claim_id": "claim_1", "claim": "Supported answer.", "source_refs": ["Primary"]}],
                "evidence_excerpts": [{"quote_id": "evidence_1", "source_ref": "Primary", "excerpt": "Supported answer."}],
                "gaps": [],
            },
        )
        write_json(
            base / "synthesis_plan.json",
            {
                "output_mode": "short_answer",
                "evidence_trace": {"claim_refs": ["claim_1"]},
                "sections": [{"section_id": "answer", "requires_evidence": True, "required_claim_refs": ["claim_1"]}],
                "unsupported_sections": [],
            },
        )
        write(
            base / "reconstruction_ru.md",
            ("Supported answer. Evidence trace: claim_1. " * 20) + "\n## Что еще надо проверить\n- none\n",
        )
        write(base / "coverage_report.md", "## Evidence Trace\n- claim_1\n## Unsupported Sections\n- none\n## Gaps\n- none\n")
        result = run(qa_request, root)
        if not result.get("ok"):
            raise AssertionError(f"ReductorVerifier failed on QA quality gates: {result}")
        report = json.loads((base / "critic_report.json").read_text(encoding="utf-8"))
        gates = report.get("metrics", {}).get("quality_gates", {})
        if not report.get("approved") or gates.get("passed") is not True:
            raise AssertionError(f"supported QA should pass quality gates: {report}")
        write_json(
            base / "research_corpus.json",
            {
                "sources": [{"title": "Primary"}],
                "claims": [{"claim_id": "claim_1", "claim": "Unsupported answer.", "source_refs": []}],
                "evidence_excerpts": [],
                "gaps": [],
            },
        )
        result = run(qa_request, root)
        if not result.get("ok"):
            raise AssertionError(f"ReductorVerifier failed on QA failed gates: {result}")
        report = json.loads((base / "critic_report.json").read_text(encoding="utf-8"))
        if report.get("approved") or report.get("metrics", {}).get("quality_gates", {}).get("passed") is not False:
            raise AssertionError(f"unsupported QA should fail quality gates: {report}")
        report_request = json.loads(json.dumps(request))
        report_request["quality_expectations"]["research_intent"] = {
            "intent": "topic_report",
            "output_mode": "research_report",
            "required_depth": "deep",
            "source_policy": "broad_sources_with_gaps_disclosed",
            "needs_timeline": False,
            "needs_chapters": False,
        }
        report_claims = [
            {"claim_id": f"claim_{index}", "claim": f"Supported report claim {index}.", "source_refs": [f"Source {index}"]}
            for index in range(1, 4)
        ]
        write_json(
            base / "source_map.json",
            {
                "discovery_status": "research_ready",
                "sources": [
                    {"title": "Source 1", "source_class": "official_primary_narrative"},
                    {"title": "Source 2", "source_class": "secondary_wiki"},
                    {"title": "Source 3", "source_class": "secondary_wiki"},
                    {"title": "Source 4", "source_class": "secondary_wiki"},
                ],
                "source_coverage": {"ready_for_extraction": True},
            },
        )
        write_json(base / "research_corpus.json", {"sources": [{"title": f"Source {index}"} for index in range(1, 5)], "claims": report_claims, "contradictions": [], "gaps": []})
        write_json(base / "structure_map.json", {"topic_structure": [{"title": "Обзор"}], "contradictions": []})
        write_json(
            base / "synthesis_plan.json",
            {
                "output_mode": "research_report",
                "evidence_trace": {"claim_refs": ["claim_1", "claim_2", "claim_3"]},
                "sections": [
                    {"section_id": "overview", "title": "Обзор", "requires_evidence": True, "required_claim_refs": ["claim_1", "claim_2"]},
                    {"section_id": "conclusion", "title": "Выводы", "requires_evidence": True, "required_claim_refs": ["claim_3"]},
                ],
                "unsupported_sections": [],
            },
        )
        write(base / "reconstruction_ru.md", ("Plain report body without planned headings. Evidence trace: claim_1. " * 90) + "\n## Что еще надо проверить\n- none\n")
        write(base / "coverage_report.md", "## Evidence Trace\n- claim_1\n- claim_2\n- claim_3\n## Unsupported Sections\n- none\n## Gaps\n- none\n")
        result = run(report_request, root)
        if not result.get("ok"):
            raise AssertionError(f"ReductorVerifier failed on report structure gates: {result}")
        report = json.loads((base / "critic_report.json").read_text(encoding="utf-8"))
        structure_metrics = report.get("metrics", {}).get("synthesis_structure", {})
        if report.get("approved") or structure_metrics.get("passed") is not False or "Draft misses required evidence trace" not in json.dumps(report):
            raise AssertionError(f"report critic should block missing synthesis structure/evidence traces: {report}")
        revision_workers = {step.get("worker") for step in report.get("revision_plan", {}).get("steps", [])}
        if "ScriptoriumDaemon" not in revision_workers:
            raise AssertionError(f"report structure blocker should reroute to writer: {report}")
        book_base = root / "bookcritic"
        book_request = {
            "task_id": "bookcritic:critic_review",
            "step": {"step_id": "critic_review", "expected_artifacts": ["/work/bookcritic/critic_report.json"]},
            "quality_expectations": {
                "research_intent": {
                    "intent": "book",
                    "output_mode": "book_manuscript",
                    "required_depth": "book",
                    "source_policy": "broad_sources_with_gaps_disclosed",
                    "needs_timeline": False,
                    "needs_chapters": True,
                }
            },
            "contract": {
                "required_artifacts": [
                    "/work/bookcritic/research_corpus.json",
                    "/work/bookcritic/synthesis_plan.json",
                    "/work/bookcritic/reconstruction_ru.md",
                    "/work/bookcritic/coverage_report.md",
                    "/work/bookcritic/book_outline.json",
                    "/work/bookcritic/chapter_plan.json",
                    "/work/bookcritic/chapters/chapter_01.md",
                    "/work/bookcritic/chapters/chapter_02.md",
                    "/work/bookcritic/chapters/chapter_03.md",
                    "/work/bookcritic/continuity_report.json",
                    "/work/bookcritic/editor_report.json",
                    "/work/bookcritic/manuscript_ru.md",
                    "/work/bookcritic/manuscript.fb2",
                ]
            },
        }
        write_json(book_base / "corpus_index.json", {"summary": {"sources_matched": 6}, "sources": [], "gaps": []})
        write_json(
            book_base / "source_map.json",
            {
                "discovery_status": "research_ready",
                "sources": [
                    {"title": f"Source {index}", "source_class": "official_primary_narrative" if index == 1 else "secondary_wiki"}
                    for index in range(1, 7)
                ],
                "source_coverage": {"ready_for_extraction": True},
            },
        )
        write_json(book_base / "source_snapshots.json", {"snapshots": [{"source_title": f"Source {index}", "ok": True} for index in range(1, 7)], "skipped": []})
        write_json(book_base / "rendered_snapshots.json", {"rendered_snapshots": [], "summary": {"render_required": 0}})
        write_json(book_base / "direct_event_notes.json", {"events": [], "gaps": []})
        claims = [
            {"claim_id": f"claim_{index}", "claim": f"Подтвержденное утверждение {index}.", "source_refs": [f"Source {index}"]}
            for index in range(1, 7)
        ]
        write_json(
            book_base / "research_corpus.json",
            {"sources": [{"title": f"Source {index}"} for index in range(1, 7)], "claims": claims, "contradictions": [], "gaps": []},
        )
        write_json(book_base / "structure_map.json", {"topic_structure": [], "contradictions": []})
        write_json(
            book_base / "synthesis_plan.json",
            {
                "output_mode": "book_manuscript",
                "evidence_trace": {"claim_refs": [claim["claim_id"] for claim in claims]},
                "sections": [{"section_id": "book_body", "requires_evidence": True, "required_claim_refs": ["claim_1", "claim_2"]}],
                "unsupported_sections": [],
            },
        )
        write_json(book_base / "book_outline.json", {"chapters": [{"chapter_id": f"chapter_{index:02d}"} for index in range(1, 4)]})
        write_json(book_base / "chapter_plan.json", {"chapters": [{"chapter_id": f"chapter_{index:02d}"} for index in range(1, 4)]})
        write(book_base / "reconstruction_ru.md", ("Evidence trace: claim_1. " * 700) + "\n## Что еще надо проверить\n- none\n")
        write(book_base / "coverage_report.md", "## Evidence Trace\n- claim_1\n## Unsupported Sections\n- none\n## Gaps\n- none\n")
        write(book_base / "chapters/chapter_01.md", "# Глава 1\n\nEvidence trace: claim_1.\n")
        write(book_base / "chapters/chapter_02.md", "# Глава 2\n\nГлава не развернута: для неё нет подтвержденных claims.\n")
        write(book_base / "chapters/chapter_03.md", "# Глава 3\n\nEvidence trace: claim_3.\n")
        write_json(
            book_base / "continuity_report.json",
            {"status": "needs_revision", "missing_evidence_trace_chapters": ["chapter_02"], "repeated_chapters": []},
        )
        write_json(book_base / "editor_report.json", {"status": "completed", "grounded_chapter_count": 2})
        write(book_base / "manuscript_ru.md", "# Глава 1\n\nEvidence trace: claim_1.\n\n# Глава 2\n\nГлава не развернута.\n\n# Глава 3\n\nEvidence trace: claim_3.\n")
        write(book_base / "manuscript.fb2", "<FictionBook><body><section></section><section></section><section></section></body></FictionBook>\n")
        result = run(book_request, root)
        if not result.get("ok"):
            raise AssertionError(f"ReductorVerifier failed on book pipeline review: {result}")
        report = json.loads((book_base / "critic_report.json").read_text(encoding="utf-8"))
        if report.get("approved") or "Book chapter was blocked for missing evidence" not in json.dumps(report):
            raise AssertionError(f"book critic should block ungrounded chapters: {report}")
        if report.get("metrics", {}).get("quality_gates", {}).get("passed") is not True:
            raise AssertionError(f"book quality gates should pass so chapter blocker is isolated: {report}")
        if report.get("metrics", {}).get("book_pipeline", {}).get("continuity_status") != "needs_revision":
            raise AssertionError(f"book metrics should expose continuity status: {report}")
        revision_workers = {step.get("worker") for step in report.get("revision_plan", {}).get("steps", [])}
        if "ScriptoriumDaemon" not in revision_workers:
            raise AssertionError(f"book chapter blocker should reroute to writer: {report}")
        write_json(
            base / "source_map.json",
            {
                "discovery_status": "playbook_matched",
                "sources": [{"title": "Community summary", "source_class": "community_wiki", "source_type": "community_wiki"}],
                "source_coverage": {
                    "source_count": 1,
                    "has_primary_or_publication": False,
                    "has_official": False,
                    "has_secondary_crosscheck": True,
                    "ready_for_extraction": False,
                },
            },
        )
        write_json(
            base / "direct_event_notes.json",
            {
                "events": [
                    {"event_id": item, "evidence_snapshots": [{"source_title": "Community summary"}]}
                    for item in events
                ],
                "gaps": [],
            },
        )
        write_json(base / "timeline.json", {"timeline": [{"event_id": item} for item in events], "gaps": []})
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"ReductorVerifier failed on source coverage pass: {result}")
        report = json.loads((base / "critic_report.json").read_text(encoding="utf-8"))
        if report.get("approved") or "Source coverage is not extraction-ready" not in json.dumps(report):
            raise AssertionError(f"weak source coverage should block approval: {report}")
        revision_workers = {step.get("worker") for step in report.get("revision_plan", {}).get("steps", [])}
        if not {"Lexmechanic", "AuspexBrowser", "OcularisRenderium", "NoosphericExtractor", "Chronologis", "ScriptoriumDaemon"}.issubset(revision_workers):
            raise AssertionError(f"weak source coverage did not produce full upstream revision plan: {report}")
        generic_base = root / "generic"
        generic_request = {
            "task_id": "generic:critic_review",
            "step": {"step_id": "critic_review", "expected_artifacts": ["/work/generic/critic_report.json"]},
        }
        write_json(generic_base / "corpus_index.json", {"summary": {"sources_matched": 0}, "sources": [], "gaps": []})
        write_json(generic_base / "source_map.json", {"topic": "Armageddon conflict", "discovery_status": "needs_live_discovery", "sources": []})
        write_json(generic_base / "source_snapshots.json", {"snapshots": [], "skipped": []})
        write_json(generic_base / "rendered_snapshots.json", {"rendered_snapshots": [], "summary": {"render_required": 0}})
        write_json(generic_base / "direct_event_notes.json", {"events": [], "gaps": []})
        write_json(generic_base / "timeline.json", {"timeline": [], "summary": {}, "gaps": []})
        write(generic_base / "reconstruction_ru.md", "## Что еще надо проверить\n- source discovery\n")
        write(generic_base / "coverage_report.md", "## Gaps\n- source discovery\n")
        result = run(generic_request, root)
        if not result.get("ok"):
            raise AssertionError(f"ReductorVerifier failed on generic review: {result}")
        generic_report = json.loads((generic_base / "critic_report.json").read_text(encoding="utf-8"))
        if "moon parley" in json.dumps(generic_report) or "Kharn burns shelters" in json.dumps(generic_report):
            raise AssertionError(f"generic review incorrectly applied Skalathrax playbook: {generic_report}")
    print("[ok] ReductorVerifier review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
