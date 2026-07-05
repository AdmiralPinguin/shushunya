#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fabricator_finalis import normalize_revision_plan_for_request, run as run_without_model


MODEL_BRAIN = {"ok": True, "status": "answered", "content": "{\"status\":\"ok\"}"}


def run(request: dict, *args, **kwargs) -> dict:
    enriched = dict(request)
    enriched["model_brain"] = MODEL_BRAIN
    return run_without_model(enriched, *args, **kwargs)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


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
        "task_id": "test-skalathrax:finalize",
        "step": {"step_id": "finalize", "expected_artifacts": ["/work/skalathrax/final_manifest.json"]},
        "quality_expectations": {
            "step_quality": {
                "step_id": "finalize",
                "worker": "FabricatorFinalis",
                "required_inputs": ["/work/skalathrax/critic_report.json"],
                "expected_artifacts": ["/work/skalathrax/final_manifest.json"],
                "checks": ["final manifest includes deliverable, package files, critic status, warnings, and blockers"],
                "blockers": ["missing expected artifact"],
                "revision_targets": ["finalize"],
            }
        },
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        base = root / "skalathrax"
        for filename in [
            "corpus_index.json",
            "source_snapshots.json",
        ]:
            write(base / filename, json.dumps({"approved": True, "status": "passed_with_warnings"}))
        write(
            base / "source_map.json",
            json.dumps(
                {
                    "approved": True,
                    "status": "passed_with_warnings",
                    "corpus_diagnostics": {
                        "provided": True,
                        "summary": {"sources_matched": 1, "sources_non_matching": 36},
                        "non_matching_count": 36,
                        "non_matching_sample": [{"corpus_relative_path": "unrelated-00.txt"}],
                    },
                }
            ),
        )
        write(
            base / "direct_event_notes.json",
            json.dumps(
                {
                    "events": [
                        {"event_id": "moon_parley", "evidence_snapshots": [{"source_title": "Kharn: Eater of Worlds"}]},
                        {"event_id": "kharn_burns_shelters", "evidence_snapshots": [{"source_title": "Kharn: Eater of Worlds"}]},
                    ]
                }
            ),
        )
        write(
            base / "timeline.json",
            json.dumps({"timeline": [{"event_id": "moon_parley"}, {"event_id": "kharn_burns_shelters"}]}),
        )
        write(
            base / "critic_report.json",
            json.dumps(
                {
                    "approved": True,
                    "status": "passed_with_warnings",
                    "required_direct_events": ["moon_parley", "kharn_burns_shelters"],
                    "metrics": {"generic_evidence_leads": 1, "low_confidence_events": 1, "source_coverage_ready": True},
                    "revision_focus": {"present": True, "coverage_items": ["Source step: critic_review"]},
                }
            ),
        )
        write(base / "reconstruction_ru.md", "# draft\n")
        write(base / "coverage_report.md", "# coverage\n")
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"FabricatorFinalis failed: {result}")
        manifest = json.loads((base / "final_manifest.json").read_text(encoding="utf-8"))
        if manifest["status"] != "ready" or not manifest["approved"]:
            raise AssertionError(f"expected ready manifest: {manifest}")
        if manifest.get("revision_plan", {}).get("required"):
            raise AssertionError(f"ready manifest should not require revision: {manifest}")
        if not manifest.get("revision_focus", {}).get("present"):
            raise AssertionError(f"ready manifest should carry revision focus: {manifest}")
        if manifest.get("critic_metrics", {}).get("generic_evidence_leads") != 1:
            raise AssertionError(f"ready manifest should carry critic metrics: {manifest}")
        event_review = manifest.get("event_review", {})
        if event_review.get("required_direct_event_count") != 2 or event_review.get("required_events_covered") is not True:
            raise AssertionError(f"ready manifest should summarize required event coverage: {manifest}")
        if manifest.get("corpus_diagnostics", {}).get("non_matching_count") != 36:
            raise AssertionError(f"ready manifest should preserve corpus diagnostics: {manifest}")
        write(
            base / "critic_report.json",
            json.dumps(
                {
                    "approved": True,
                    "status": "passed_with_warnings",
                    "required_direct_events": ["moon_parley", "kharn_burns_shelters"],
                    "metrics": {
                        "source_coverage_ready": True,
                        "quality_gates": {
                            "applies": True,
                            "passed": False,
                            "output_mode": "research_report",
                        },
                    },
                    "revision_focus": {"present": True},
                }
            ),
        )
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"FabricatorFinalis failed on quality gate blocker: {result}")
        manifest = json.loads((base / "final_manifest.json").read_text(encoding="utf-8"))
        if manifest.get("status") != "blocked" or manifest.get("readiness_checks", {}).get("quality_gates_ready") is not False:
            raise AssertionError(f"failed quality gates should block final readiness: {manifest}")
        write(
            base / "critic_report.json",
            json.dumps(
                {
                    "approved": True,
                    "status": "passed_with_warnings",
                    "required_direct_events": ["moon_parley", "kharn_burns_shelters"],
                    "metrics": {"generic_evidence_leads": 1, "low_confidence_events": 1, "source_coverage_ready": True},
                    "revision_focus": {"present": True, "coverage_items": ["Source step: critic_review"]},
                }
            ),
        )
        write(base / "source_map.json", "{")
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"FabricatorFinalis failed on invalid package JSON: {result}")
        manifest = json.loads((base / "final_manifest.json").read_text(encoding="utf-8"))
        if (
            manifest.get("status") != "blocked"
            or manifest.get("readiness_checks", {}).get("package_files_valid") is not False
            or manifest.get("package_file_errors", [{}])[0].get("path") != "/work/skalathrax/source_map.json"
        ):
            raise AssertionError(f"invalid JSON package artifact should block final readiness: {manifest}")
        invalid_revision_workers = {step.get("worker") for step in manifest.get("revision_plan", {}).get("steps", [])}
        if "Lexmechanic" not in invalid_revision_workers:
            raise AssertionError(f"invalid source_map should produce source discovery revision: {manifest}")
        write(
            base / "source_map.json",
            json.dumps(
                {
                    "approved": True,
                    "status": "passed_with_warnings",
                    "corpus_diagnostics": {
                        "provided": True,
                        "summary": {"sources_matched": 1, "sources_non_matching": 36},
                        "non_matching_count": 36,
                        "non_matching_sample": [{"corpus_relative_path": "unrelated-00.txt"}],
                    },
                }
            ),
        )
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"FabricatorFinalis failed after restoring package JSON: {result}")
        manifest = json.loads((base / "final_manifest.json").read_text(encoding="utf-8"))
        if manifest.get("readiness_checks", {}).get("source_coverage_ready") is not True:
            raise AssertionError(f"ready manifest should carry source coverage readiness: {manifest}")
        if manifest.get("readiness_checks", {}).get("comprehensive_depth_ready") is not True:
            raise AssertionError(f"standard ready manifest should pass comprehensive depth readiness: {manifest}")
        if manifest.get("readiness_checks", {}).get("required_events_covered") is not True:
            raise AssertionError(f"standard ready manifest should pass required event readiness: {manifest}")
        if manifest.get("readiness_checks", {}).get("required_event_evidence_covered") is not True:
            raise AssertionError(f"standard ready manifest should pass required evidence readiness: {manifest}")
        if manifest.get("readiness_checks", {}).get("corpus_requirements_satisfied") is not True:
            raise AssertionError(f"standard ready manifest should satisfy corpus requirements: {manifest}")
        if manifest.get("quality_expectations", {}).get("check_count") != 1:
            raise AssertionError(f"ready manifest should carry quality expectations: {manifest}")
        write(base / "direct_event_notes.json", json.dumps({"events": [{"event_id": "moon_parley", "evidence_snapshots": []}]}))
        write(
            base / "critic_report.json",
            json.dumps(
                {
                    "approved": True,
                    "status": "passed_with_warnings",
                    "required_direct_events": ["moon_parley"],
                    "metrics": {"source_coverage_ready": True},
                    "revision_focus": {"present": True},
                }
            ),
        )
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"FabricatorFinalis failed on missing required event evidence: {result}")
        manifest = json.loads((base / "final_manifest.json").read_text(encoding="utf-8"))
        if (
            manifest.get("status") != "blocked"
            or manifest.get("readiness_checks", {}).get("required_event_evidence_covered") is not False
            or "missing direct evidence" not in json.dumps(manifest)
        ):
            raise AssertionError(f"missing required event evidence should block final readiness: {manifest}")
        evidence_revision_workers = {step.get("worker") for step in manifest.get("revision_plan", {}).get("steps", [])}
        if not {"NoosphericExtractor", "Chronologis", "ScriptoriumArchitect", "ScriptoriumDaemon"}.issubset(evidence_revision_workers):
            raise AssertionError(f"missing required event evidence should produce downstream revision plan: {manifest}")
        write(
            base / "direct_event_notes.json",
            json.dumps(
                {
                    "events": [
                        {"event_id": "moon_parley", "evidence_snapshots": [{"source_title": "Kharn: Eater of Worlds"}]},
                        {"event_id": "kharn_burns_shelters", "evidence_snapshots": [{"source_title": "Kharn: Eater of Worlds"}]},
                    ]
                }
            ),
        )
        write(
            base / "critic_report.json",
            json.dumps(
                {
                    "approved": True,
                    "status": "passed_with_warnings",
                    "required_direct_events": ["moon_parley", "golden_absolute"],
                    "metrics": {"source_coverage_ready": True},
                    "revision_focus": {"present": True},
                    "revision_plan": {
                        "required": True,
                        "steps": [
                            {
                                "step_id": "draft_reconstruction",
                                "worker": "ScriptoriumDaemon",
                                "reason": "critic already requested draft rebuild",
                                "source": "critic_finding",
                                "priority": "blocker",
                            }
                        ],
                    },
                }
            ),
        )
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"FabricatorFinalis failed on missing required event: {result}")
        manifest = json.loads((base / "final_manifest.json").read_text(encoding="utf-8"))
        if (
            manifest.get("status") != "blocked"
            or manifest.get("readiness_checks", {}).get("required_events_covered") is not False
            or "golden_absolute" not in json.dumps(manifest)
        ):
            raise AssertionError(f"missing required event should block final readiness: {manifest}")
        revision_workers = {step.get("worker") for step in manifest.get("revision_plan", {}).get("steps", [])}
        if not {"NoosphericExtractor", "Chronologis", "ScriptoriumArchitect", "ScriptoriumDaemon"}.issubset(revision_workers):
            raise AssertionError(f"missing required event should produce downstream revision plan: {manifest}")
        revision_steps = manifest.get("revision_plan", {}).get("steps", [])
        revision_step_ids = [step.get("step_id") for step in revision_steps]
        if len(revision_step_ids) != len(set(revision_step_ids)):
            raise AssertionError(f"final manifest revision plan should not duplicate step ids: {manifest}")
        if revision_step_ids != ["fact_extraction", "structure_mapping", "synthesis_planning", "draft_reconstruction"]:
            raise AssertionError(f"final manifest revision plan should follow pipeline order: {manifest}")
        draft_revision = next((step for step in revision_steps if step.get("step_id") == "draft_reconstruction"), {})
        if "critic already requested draft rebuild" not in draft_revision.get("reason", "") or "Missing required direct events" not in draft_revision.get("reason", ""):
            raise AssertionError(f"duplicate draft revision reasons should be merged: {manifest}")
        write(
            base / "critic_report.json",
            json.dumps(
                {
                    "approved": True,
                    "status": "passed_with_warnings",
                    "required_direct_events": ["moon_parley"],
                    "metrics": {"source_coverage_ready": False},
                    "revision_focus": {"present": True},
                }
            ),
        )
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"FabricatorFinalis failed on weak source coverage: {result}")
        manifest = json.loads((base / "final_manifest.json").read_text(encoding="utf-8"))
        if manifest.get("status") != "blocked" or "source coverage is not extraction-ready" not in json.dumps(manifest):
            raise AssertionError(f"weak source coverage should block final readiness: {manifest}")
        write(
            base / "critic_report.json",
            json.dumps(
                {
                    "approved": True,
                    "status": "passed_with_warnings",
                    "required_direct_events": ["moon_parley"],
                    "metrics": {
                        "source_coverage_ready": True,
                        "comprehensive_depth": {
                            "mode": "comprehensive",
                            "passed": False,
                            "corpus_requirements": {
                                "required": True,
                                "missing_primary_texts": [{"title": "Kharn: Eater of Worlds"}],
                            },
                        },
                    },
                    "revision_focus": {"present": True},
                }
            ),
        )
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"FabricatorFinalis failed on weak comprehensive depth: {result}")
        manifest = json.loads((base / "final_manifest.json").read_text(encoding="utf-8"))
        if manifest.get("status") != "blocked" or "comprehensive depth" not in json.dumps(manifest):
            raise AssertionError(f"weak comprehensive depth should block final readiness: {manifest}")
        if manifest.get("corpus_requirements", {}).get("missing_primary_texts", [{}])[0].get("title") != "Kharn: Eater of Worlds":
            raise AssertionError(f"final manifest should preserve corpus requirements: {manifest}")
        corpus_revision_workers = {step.get("worker") for step in manifest.get("revision_plan", {}).get("steps", [])}
        if not {"CorpusIngestor", "Lexmechanic", "AuspexBrowser", "OcularisRenderium", "NoosphericExtractor", "Chronologis", "ScriptoriumArchitect", "ScriptoriumDaemon"}.issubset(corpus_revision_workers):
            raise AssertionError(f"missing corpus requirements should produce full upstream revision plan: {manifest}")
        write(
            base / "critic_report.json",
            json.dumps(
                {
                    "approved": True,
                    "status": "passed_with_warnings",
                    "required_direct_events": ["moon_parley"],
                    "metrics": {
                        "source_coverage_ready": True,
                        "comprehensive_depth": {
                            "mode": "comprehensive",
                            "passed": True,
                            "corpus_requirements": {
                                "required": True,
                                "missing_primary_texts": [{"title": "Lucius: The Faultless Blade"}],
                            },
                        },
                    },
                    "revision_focus": {"present": True},
                }
            ),
        )
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"FabricatorFinalis failed on inconsistent corpus readiness: {result}")
        manifest = json.loads((base / "final_manifest.json").read_text(encoding="utf-8"))
        if (
            manifest.get("status") != "blocked"
            or manifest.get("readiness_checks", {}).get("corpus_requirements_satisfied") is not False
            or "Lucius: The Faultless Blade" not in json.dumps(manifest)
        ):
            raise AssertionError(f"corpus requirements should block even when comprehensive depth claims pass: {manifest}")
        write(
            base / "critic_report.json",
            json.dumps(
                {
                    "approved": True,
                    "status": "passed_with_warnings",
                    "required_direct_events": ["moon_parley"],
                    "metrics": {"generic_evidence_leads": 1, "low_confidence_events": 1, "source_coverage_ready": True},
                    "revision_focus": {"present": True, "coverage_items": ["Source step: critic_review"]},
                }
            ),
        )
        bad_quality_request = json.loads(json.dumps(request))
        bad_quality_request["quality_expectations"]["step_quality"]["expected_artifacts"] = ["/work/skalathrax/wrong.json"]
        result = run(bad_quality_request, root)
        if not result.get("ok"):
            raise AssertionError(f"FabricatorFinalis failed on bad quality expectations: {result}")
        manifest = json.loads((base / "final_manifest.json").read_text(encoding="utf-8"))
        if manifest.get("status") != "blocked" or "expected_artifacts do not match" not in json.dumps(manifest):
            raise AssertionError(f"bad quality expectations should block manifest: {manifest}")
        short_base = root / "python"
        short_request = {
            "task_id": "test-python:finalize",
            "step": {"step_id": "finalize", "expected_artifacts": ["/work/python/final_manifest.json"]},
            "quality_expectations": {
                "research_intent": {
                    "intent": "qa_answer",
                    "output_mode": "short_answer",
                    "required_depth": "standard",
                    "source_policy": "answer_with_citations",
                    "needs_timeline": False,
                    "needs_chapters": False,
                },
                "step_quality": {
                    "step_id": "finalize",
                    "worker": "FabricatorFinalis",
                    "required_inputs": ["/work/python/critic_report.json"],
                    "expected_artifacts": ["/work/python/final_manifest.json"],
                    "checks": ["final manifest matches output mode package"],
                    "blockers": ["missing expected artifact"],
                    "revision_targets": ["finalize"],
                },
            },
        }
        write(short_base / "corpus_index.json", json.dumps({"approved": True}))
        write(short_base / "source_map.json", json.dumps({"source_coverage": {"ready_for_extraction": True}, "sources": [{"title": "Python Docs"}]}))
        write(short_base / "source_snapshots.json", json.dumps({"snapshots": []}))
        write(short_base / "direct_event_notes.json", json.dumps({"events": []}))
        write(short_base / "reconstruction_ru.md", "# Python\n")
        write(short_base / "coverage_report.md", "# Coverage\n")
        write(
            short_base / "critic_report.json",
            json.dumps(
                {
                    "approved": True,
                    "status": "approved",
                    "metrics": {
                        "source_coverage_ready": True,
                        "quality_gates": {"applies": True, "passed": True, "output_mode": "short_answer"},
                    },
                    "revision_focus": {"present": True},
                }
            ),
        )
        result = run(short_request, root)
        if not result.get("ok"):
            raise AssertionError(f"FabricatorFinalis failed on short answer without timeline: {result}")
        short_manifest = json.loads((short_base / "final_manifest.json").read_text(encoding="utf-8"))
        if short_manifest.get("status") != "ready" or "/work/python/timeline.json" in json.dumps(short_manifest):
            raise AssertionError(f"short answer final package should not require timeline: {short_manifest}")
        book_base = root / "bookfinal"
        book_request = {
            "task_id": "test-book:finalize",
            "step": {"step_id": "finalize", "expected_artifacts": ["/work/bookfinal/final_manifest.json"]},
            "quality_expectations": {
                "research_intent": {
                    "intent": "book",
                    "output_mode": "book_manuscript",
                    "required_depth": "comprehensive",
                    "source_policy": "broad_sources_with_gaps_disclosed",
                    "needs_timeline": False,
                    "needs_chapters": True,
                },
                "step_quality": {
                    "step_id": "finalize",
                    "worker": "FabricatorFinalis",
                    "required_inputs": ["/work/bookfinal/critic_report.json"],
                    "expected_artifacts": ["/work/bookfinal/final_manifest.json"],
                    "checks": ["book final package exposes fb2 deliverable"],
                    "blockers": ["missing expected artifact"],
                    "revision_targets": ["finalize"],
                },
            },
        }
        for filename, payload in {
            "corpus_index.json": {"approved": True},
            "source_map.json": {"source_coverage": {"ready_for_extraction": True}, "sources": [{"title": "Source"}]},
            "source_snapshots.json": {"snapshots": []},
            "direct_event_notes.json": {"events": []},
            "research_corpus.json": {"sources": [{"title": "Source"}], "claims": [{"claim_id": "claim_1", "source_refs": ["Source"]}], "gaps": []},
            "structure_map.json": {"topic_structure": []},
            "synthesis_plan.json": {"output_mode": "book_manuscript", "evidence_trace": {"claim_refs": ["claim_1"]}},
            "book_outline.json": {"chapters": [{"chapter_id": "chapter_01", "required_claim_refs": ["claim_1"]}]},
            "chapter_plan.json": {"chapters": [{"chapter_id": "chapter_01", "required_claim_refs": ["claim_1"]}]},
            "continuity_report.json": {"status": "completed"},
            "editor_report.json": {"status": "completed"},
            "critic_report.json": {
                "approved": True,
                "status": "passed",
                "metrics": {
                    "source_coverage_ready": True,
                    "quality_gates": {"applies": True, "passed": True, "output_mode": "book_manuscript"},
                },
            },
        }.items():
            write(book_base / filename, json.dumps(payload))
        write(book_base / "reconstruction_ru.md", "# Draft\n")
        write(book_base / "coverage_report.md", "# Coverage\n")
        write(book_base / "chapters/chapter_01.md", "# Глава 1\n\nEvidence trace: claim_1.\n")
        write(book_base / "manuscript_ru.md", "# Глава 1\n\nEvidence trace: claim_1.\n")
        write(book_base / "manuscript.fb2", "<FictionBook><body><section><p>Evidence trace: claim_1.</p></section></body></FictionBook>\n")
        result = run(book_request, root)
        if not result.get("ok"):
            raise AssertionError(f"FabricatorFinalis failed on book final package: {result}")
        book_manifest = json.loads((book_base / "final_manifest.json").read_text(encoding="utf-8"))
        fb2_file = next((item for item in book_manifest.get("files", []) if item.get("path") == "/work/bookfinal/manuscript.fb2"), {})
        chapter_file = next((item for item in book_manifest.get("files", []) if item.get("path") == "/work/bookfinal/chapters/chapter_01.md"), {})
        if (
            book_manifest.get("status") != "ready"
            or book_manifest.get("deliverable") != "/work/bookfinal/manuscript.fb2"
            or book_manifest.get("draft_deliverable") != "/work/bookfinal/reconstruction_ru.md"
            or fb2_file.get("kind") != "fb2"
            or chapter_file.get("kind") != "markdown"
            or book_manifest.get("readiness_checks", {}).get("book_continuity_ready") is not True
            or book_manifest.get("readiness_checks", {}).get("book_editor_ready") is not True
        ):
            raise AssertionError(f"book final manifest should expose mode-specific fb2 deliverable: {book_manifest}")
        (book_base / "chapters/chapter_01.md").unlink()
        result = run(book_request, root)
        if not result.get("ok"):
            raise AssertionError(f"FabricatorFinalis failed on missing book chapter: {result}")
        book_manifest = json.loads((book_base / "final_manifest.json").read_text(encoding="utf-8"))
        chapter_revision_workers = {step.get("worker") for step in book_manifest.get("revision_plan", {}).get("steps", [])}
        if (
            book_manifest.get("status") != "blocked"
            or book_manifest.get("readiness_checks", {}).get("book_manifest_complete") is not False
            or "ScriptoriumDaemon" not in chapter_revision_workers
        ):
            raise AssertionError(f"missing book chapter should block final readiness and reroute to writer: {book_manifest}")
        write(book_base / "chapters/chapter_01.md", "# Глава 1\n\nEvidence trace: claim_1.\n")
        write(book_base / "continuity_report.json", json.dumps({"status": "needs_revision"}))
        result = run(book_request, root)
        if not result.get("ok"):
            raise AssertionError(f"FabricatorFinalis failed on bad book continuity: {result}")
        book_manifest = json.loads((book_base / "final_manifest.json").read_text(encoding="utf-8"))
        if book_manifest.get("status") != "blocked" or book_manifest.get("readiness_checks", {}).get("book_continuity_ready") is not False:
            raise AssertionError(f"book continuity should independently block final readiness: {book_manifest}")
        write(book_base / "continuity_report.json", json.dumps({"status": "completed"}))
        (base / "timeline.json").unlink()
        write(
            base / "critic_report.json",
            json.dumps(
                {
                    "approved": False,
                    "status": "needs_revision",
                    "findings": [{"severity": "blocker", "message": "Draft does not visibly cover required event: test"}],
                    "revision_plan": {
                        "required": True,
                        "steps": [
                            {
                                "step_id": "draft_reconstruction",
                                "worker": "ScriptoriumDaemon",
                                "reason": "Draft does not visibly cover required event: test",
                                "source": "critic_finding",
                                "priority": "blocker",
                            }
                        ],
                    },
                }
            ),
        )
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"FabricatorFinalis failed on missing file: {result}")
        manifest = json.loads((base / "final_manifest.json").read_text(encoding="utf-8"))
        if manifest["status"] != "blocked" or not manifest["missing"]:
            raise AssertionError(f"expected blocked manifest: {manifest}")
        revision_steps = manifest.get("revision_plan", {}).get("steps", [])
        revision_workers = {step.get("worker") for step in revision_steps}
        if not manifest.get("revision_plan", {}).get("required") or not {"ScriptoriumDaemon", "Chronologis"}.issubset(revision_workers):
            raise AssertionError(f"blocked manifest did not expose merged revision plan: {manifest}")
    print("[ok] FabricatorFinalis manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
