#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fabricator_finalis import run


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> int:
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
            "source_map.json",
            "source_snapshots.json",
            "direct_event_notes.json",
        ]:
            write(base / filename, json.dumps({"approved": True, "status": "passed_with_warnings"}))
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
        if manifest.get("readiness_checks", {}).get("source_coverage_ready") is not True:
            raise AssertionError(f"ready manifest should carry source coverage readiness: {manifest}")
        if manifest.get("readiness_checks", {}).get("comprehensive_depth_ready") is not True:
            raise AssertionError(f"standard ready manifest should pass comprehensive depth readiness: {manifest}")
        if manifest.get("readiness_checks", {}).get("required_events_covered") is not True:
            raise AssertionError(f"standard ready manifest should pass required event readiness: {manifest}")
        if manifest.get("quality_expectations", {}).get("check_count") != 1:
            raise AssertionError(f"ready manifest should carry quality expectations: {manifest}")
        write(
            base / "critic_report.json",
            json.dumps(
                {
                    "approved": True,
                    "status": "passed_with_warnings",
                    "required_direct_events": ["moon_parley", "golden_absolute"],
                    "metrics": {"source_coverage_ready": True},
                    "revision_focus": {"present": True},
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
