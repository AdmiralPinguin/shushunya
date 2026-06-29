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
        if report.get("metrics", {}).get("generic_evidence_leads") != 1 or "generic low-confidence evidence lead" not in json.dumps(report):
            raise AssertionError(f"expected verifier to warn about generic evidence leads: {report}")
        if (
            report.get("quality_expectations", {}).get("check_count") != 1
            or report.get("quality_expectations", {}).get("revision_targets") != ["critic_review", "finalize"]
        ):
            raise AssertionError(f"expected verifier to preserve quality expectations: {report}")
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
        if not {"AuspexBrowser", "NoosphericExtractor", "Chronologis", "ScriptoriumDaemon"}.issubset(revision_workers):
            raise AssertionError(f"missing evidence review did not produce source rework plan: {report}")
        source_acquisition_index = next(
            index for index, step in enumerate(revision_steps) if step.get("step_id") == "source_acquisition"
        )
        timeline_index = next(index for index, step in enumerate(revision_steps) if step.get("step_id") == "timeline")
        draft_index = next(index for index, step in enumerate(revision_steps) if step.get("step_id") == "draft_reconstruction")
        if not source_acquisition_index < timeline_index < draft_index:
            raise AssertionError(f"revision dependencies are not ordered downstream: {report}")
    print("[ok] ReductorVerifier review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
