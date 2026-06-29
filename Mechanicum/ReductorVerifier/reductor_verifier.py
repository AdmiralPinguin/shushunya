from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQUIRED_DIRECT_EVENT_IDS = {
    "moon_parley": "moon parley",
    "dreagher_shoots_anteus": "Dreagher and Anteus",
    "golden_absolute": "Golden Absolute",
    "cold_night_shelters": "deadly night and shelters",
    "kharn_burns_shelters": "Kharn burns shelters",
    "fratricide_spreads": "fratricide spreads",
}

EVENT_TEXT_MARKERS = {
    "moon_parley": ["луне Скалатракса", "переговор"],
    "dreagher_shoots_anteus": ["Дреагер", "Анте"],
    "golden_absolute": ["Golden Absolute"],
    "cold_night_shelters": ["ночь Скалатракса", "укрыти"],
    "kharn_burns_shelters": ["Кхарн", "убежищ"],
    "fratricide_spreads": ["Пожиратели Миров стали", "резать друг друга"],
}

ARTIFACT_REWORK_TARGETS = {
    "corpus_index.json": ("corpus_ingestion", "CorpusIngestor"),
    "source_map.json": ("source_discovery", "Lexmechanic"),
    "source_snapshots.json": ("source_acquisition", "AuspexBrowser"),
    "direct_event_notes.json": ("fact_extraction", "NoosphericExtractor"),
    "timeline.json": ("timeline", "Chronologis"),
    "reconstruction_ru.md": ("draft_reconstruction", "ScriptoriumDaemon"),
    "coverage_report.md": ("draft_reconstruction", "ScriptoriumDaemon"),
}

REVISION_DEPENDENCIES = {
    "corpus_ingestion": [
        ("source_discovery", "Lexmechanic"),
        ("source_acquisition", "AuspexBrowser"),
        ("fact_extraction", "NoosphericExtractor"),
        ("timeline", "Chronologis"),
        ("draft_reconstruction", "ScriptoriumDaemon"),
    ],
    "source_discovery": [
        ("source_acquisition", "AuspexBrowser"),
        ("fact_extraction", "NoosphericExtractor"),
        ("timeline", "Chronologis"),
        ("draft_reconstruction", "ScriptoriumDaemon"),
    ],
    "source_acquisition": [
        ("fact_extraction", "NoosphericExtractor"),
        ("timeline", "Chronologis"),
        ("draft_reconstruction", "ScriptoriumDaemon"),
    ],
    "fact_extraction": [
        ("timeline", "Chronologis"),
        ("draft_reconstruction", "ScriptoriumDaemon"),
    ],
    "timeline": [
        ("draft_reconstruction", "ScriptoriumDaemon"),
    ],
}

REVISION_STEP_ORDER = [
    "corpus_ingestion",
    "source_discovery",
    "source_acquisition",
    "fact_extraction",
    "timeline",
    "draft_reconstruction",
    "critic_review",
]


def sandbox_path(workspace_root: Path, path: str) -> Path:
    if not path.startswith("/work/"):
        raise ValueError(f"unsupported sandbox path: {path}")
    return workspace_root / path.removeprefix("/work/")


def sibling_artifact(output_path: str, filename: str) -> str:
    if not output_path.startswith("/work/"):
        raise ValueError(f"unsupported output path: {output_path}")
    parent = output_path.rsplit("/", 1)[0]
    return f"{parent}/{filename}"


def load_json(workspace_root: Path, path: str) -> dict[str, Any]:
    payload = json.loads(sandbox_path(workspace_root, path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"artifact must be an object: {path}")
    return payload


def read_text(workspace_root: Path, path: str) -> str:
    return sandbox_path(workspace_root, path).read_text(encoding="utf-8")


def artifact_exists(workspace_root: Path, path: str) -> bool:
    return sandbox_path(workspace_root, path).exists()


def direct_evidence_source_count(notes: dict[str, Any]) -> int:
    sources: set[str] = set()
    for event in notes.get("events", []):
        if not isinstance(event, dict):
            continue
        evidence = event.get("evidence_snapshots") if isinstance(event.get("evidence_snapshots"), list) else []
        for item in evidence:
            if not isinstance(item, dict):
                continue
            source_title = str(item.get("source_title") or "")
            if source_title:
                sources.add(source_title)
    return len(sources)


def inaccessible_primary_titles(source_map: dict[str, Any]) -> list[str]:
    titles: list[str] = []
    for source in source_map.get("sources", []):
        if not isinstance(source, dict):
            continue
        source_class = str(source.get("source_class") or source.get("type") or "").lower()
        if "primary" not in source_class and str(source.get("type") or "").lower() not in {"novel", "short_story", "book"}:
            continue
        if not str(source.get("url") or "").strip() and not str(source.get("local_path") or "").strip():
            titles.append(str(source.get("title") or "untitled primary source"))
    return titles


def comprehensive_depth_findings(source_map: dict[str, Any], notes: dict[str, Any], reconstruction: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    depth_profile = source_map.get("depth_profile") if isinstance(source_map.get("depth_profile"), dict) else {}
    if depth_profile.get("mode") != "comprehensive":
        return [], {"mode": str(depth_profile.get("mode") or "standard"), "passed": True}
    source_coverage = source_map.get("source_coverage") if isinstance(source_map.get("source_coverage"), dict) else {}
    source_count = int(source_coverage.get("source_count") or len(source_map.get("sources", [])))
    live_candidate_count = int(source_coverage.get("live_candidate_count") or 0)
    evidence_source_count = direct_evidence_source_count(notes)
    draft_chars = len(reconstruction)
    min_source_count = int(depth_profile.get("min_source_count") or 0)
    min_live_candidate_count = int(depth_profile.get("min_live_candidate_count") or 0)
    min_direct_evidence_sources = int(depth_profile.get("min_direct_evidence_sources") or 0)
    min_draft_chars = int(depth_profile.get("min_draft_chars") or 0)
    missing_primary = inaccessible_primary_titles(source_map)
    findings: list[dict[str, str]] = []
    if source_count < min_source_count:
        findings.append(
            {
                "severity": "blocker",
                "message": f"Comprehensive task has too few mapped sources: {source_count}/{min_source_count}.",
            }
        )
    if live_candidate_count < min_live_candidate_count:
        findings.append(
            {
                "severity": "blocker",
                "message": f"Comprehensive task has too few live-discovered source candidates: {live_candidate_count}/{min_live_candidate_count}.",
            }
        )
    if evidence_source_count < min_direct_evidence_sources:
        findings.append(
            {
                "severity": "blocker",
                "message": f"Comprehensive task has too few direct-evidence sources: {evidence_source_count}/{min_direct_evidence_sources}.",
            }
        )
    if draft_chars < min_draft_chars:
        findings.append(
            {
                "severity": "blocker",
                "message": f"Comprehensive draft is too short for requested depth: {draft_chars}/{min_draft_chars} chars.",
            }
        )
    if missing_primary:
        joined = ", ".join(missing_primary[:6])
        findings.append(
            {
                "severity": "blocker",
                "message": f"Comprehensive task lacks accessible primary text URLs or local corpus files for: {joined}.",
            }
        )
    metrics = {
        "mode": "comprehensive",
        "passed": not findings,
        "source_count": source_count,
        "min_source_count": min_source_count,
        "live_candidate_count": live_candidate_count,
        "min_live_candidate_count": min_live_candidate_count,
        "direct_evidence_source_count": evidence_source_count,
        "min_direct_evidence_sources": min_direct_evidence_sources,
        "draft_chars": draft_chars,
        "min_draft_chars": min_draft_chars,
        "inaccessible_primary_count": len(missing_primary),
    }
    return findings, metrics


def text_contains_markers(text: str, markers: list[str]) -> bool:
    lowered = text.lower()
    return all(marker.lower() in lowered for marker in markers)


def should_apply_required_event_playbook(source_map: dict[str, Any], notes: dict[str, Any], timeline: dict[str, Any]) -> bool:
    haystack = " ".join(
        [
            str(source_map.get("topic") or ""),
            *(str(source.get("title") or "") for source in source_map.get("sources", []) if isinstance(source, dict)),
        ]
    ).lower()
    if "skalathrax" in haystack or "скалатрак" in haystack:
        return True
    known_ids = set(REQUIRED_DIRECT_EVENT_IDS)
    note_ids = {
        str(item.get("event_id") or "")
        for item in notes.get("events", [])
        if isinstance(item, dict)
    }
    timeline_ids = {
        str(item.get("event_id") or "")
        for item in timeline.get("timeline", [])
        if isinstance(item, dict)
    }
    return bool((note_ids | timeline_ids) & known_ids)


def extract_section_bullets(text: str, headings: set[str]) -> list[str]:
    bullets: list[str] = []
    in_section = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            in_section = line.removeprefix("## ").strip().lower() in headings
            continue
        if in_section and line.startswith("- "):
            bullets.append(line.removeprefix("- ").strip())
    return bullets


def revision_focus_from_artifacts(reconstruction: str, coverage: str) -> dict[str, Any]:
    reconstruction_items = extract_section_bullets(reconstruction, {"фокус ревизии"})
    coverage_items = extract_section_bullets(coverage, {"revision context"})
    return {
        "present": bool(reconstruction_items or coverage_items),
        "reconstruction_items": reconstruction_items,
        "coverage_items": coverage_items,
    }


def add_revision_step(steps: list[dict[str, str]], step_id: str, worker: str, reason: str, source: str) -> None:
    for item in steps:
        if item.get("step_id") != step_id or item.get("worker") != worker:
            continue
        existing_reasons = [part.strip() for part in item.get("reason", "").split(" | ") if part.strip()]
        if reason and reason not in existing_reasons:
            existing_reasons.append(reason)
            item["reason"] = " | ".join(existing_reasons[:6])
        existing_sources = [part.strip() for part in item.get("source", "").split(",") if part.strip()]
        if source and source not in existing_sources:
            existing_sources.append(source)
            item["source"] = ",".join(existing_sources)
        return
    steps.append(
        {
            "step_id": step_id,
            "worker": worker,
            "reason": reason,
            "source": source,
            "priority": "blocker",
        }
    )


def expand_revision_dependencies(steps: list[dict[str, str]]) -> None:
    index = 0
    while index < len(steps):
        item = steps[index]
        step_id = item.get("step_id", "")
        reason = item.get("reason", "")
        source = item.get("source", "")
        for dependent_step_id, dependent_worker in REVISION_DEPENDENCIES.get(step_id, []):
            add_revision_step(
                steps,
                dependent_step_id,
                dependent_worker,
                f"Depends on revised step {step_id}",
                source or "revision_dependency",
            )
        index += 1


def sort_revision_steps(steps: list[dict[str, str]]) -> list[dict[str, str]]:
    order = {step_id: index for index, step_id in enumerate(REVISION_STEP_ORDER)}
    return sorted(steps, key=lambda item: (order.get(item.get("step_id", ""), len(order)), item.get("step_id", "")))


def revision_plan_from_findings(findings: list[dict[str, str]], missing_artifacts: list[str]) -> dict[str, Any]:
    steps: list[dict[str, str]] = []
    for artifact in missing_artifacts:
        filename = artifact.rsplit("/", 1)[-1]
        target = ARTIFACT_REWORK_TARGETS.get(filename)
        if target:
            step_id, worker = target
            add_revision_step(steps, step_id, worker, f"Missing artifact: {artifact}", "missing_artifacts")
    for finding in findings:
        message = str(finding.get("message") or "")
        lowered = message.lower()
        if "missing required direct event in timeline" in lowered:
            add_revision_step(steps, "fact_extraction", "NoosphericExtractor", message, "critic_finding")
            add_revision_step(steps, "timeline", "Chronologis", message, "critic_finding")
            add_revision_step(steps, "draft_reconstruction", "ScriptoriumDaemon", message, "critic_finding")
        elif "timeline event lacks extracted direct-event note" in lowered:
            add_revision_step(steps, "fact_extraction", "NoosphericExtractor", message, "critic_finding")
            add_revision_step(steps, "timeline", "Chronologis", message, "critic_finding")
            add_revision_step(steps, "draft_reconstruction", "ScriptoriumDaemon", message, "critic_finding")
        elif "draft does not visibly cover" in lowered or "coverage gaps clearly" in lowered:
            add_revision_step(steps, "draft_reconstruction", "ScriptoriumDaemon", message, "critic_finding")
        elif "lacks fetched source evidence" in lowered:
            add_revision_step(steps, "source_acquisition", "AuspexBrowser", message, "critic_finding")
            add_revision_step(steps, "fact_extraction", "NoosphericExtractor", message, "critic_finding")
            add_revision_step(steps, "draft_reconstruction", "ScriptoriumDaemon", message, "critic_finding")
        elif "source discovery did not find" in lowered:
            add_revision_step(steps, "source_discovery", "Lexmechanic", message, "critic_finding")
            add_revision_step(steps, "source_acquisition", "AuspexBrowser", message, "critic_finding")
        elif "comprehensive task has too few mapped sources" in lowered or "comprehensive task has too few live-discovered" in lowered:
            add_revision_step(steps, "source_discovery", "Lexmechanic", message, "critic_finding")
            add_revision_step(steps, "source_acquisition", "AuspexBrowser", message, "critic_finding")
        elif "comprehensive task has too few direct-evidence sources" in lowered:
            add_revision_step(steps, "source_discovery", "Lexmechanic", message, "critic_finding")
            add_revision_step(steps, "source_acquisition", "AuspexBrowser", message, "critic_finding")
            add_revision_step(steps, "fact_extraction", "NoosphericExtractor", message, "critic_finding")
        elif "comprehensive task lacks accessible primary text" in lowered:
            add_revision_step(steps, "corpus_ingestion", "CorpusIngestor", message, "critic_finding")
            add_revision_step(steps, "source_discovery", "Lexmechanic", message, "critic_finding")
            add_revision_step(steps, "source_acquisition", "AuspexBrowser", message, "critic_finding")
            add_revision_step(steps, "fact_extraction", "NoosphericExtractor", message, "critic_finding")
        elif "comprehensive draft is too short" in lowered:
            add_revision_step(steps, "fact_extraction", "NoosphericExtractor", message, "critic_finding")
            add_revision_step(steps, "timeline", "Chronologis", message, "critic_finding")
            add_revision_step(steps, "draft_reconstruction", "ScriptoriumDaemon", message, "critic_finding")
        elif "source set is not extraction-ready" in lowered or "source coverage is not extraction-ready" in lowered:
            add_revision_step(steps, "source_discovery", "Lexmechanic", message, "critic_finding")
            add_revision_step(steps, "source_acquisition", "AuspexBrowser", message, "critic_finding")
        else:
            add_revision_step(steps, "critic_review", "ReductorVerifier", message, "critic_finding")
    expand_revision_dependencies(steps)
    steps = sort_revision_steps(steps)
    return {
        "required": bool(steps),
        "steps": steps,
    }


def quality_expectation_summary(request: dict[str, Any]) -> dict[str, Any]:
    expectations = request.get("quality_expectations") if isinstance(request.get("quality_expectations"), dict) else {}
    step_quality = expectations.get("step_quality") if isinstance(expectations.get("step_quality"), dict) else {}
    final_review = expectations.get("final_review") if isinstance(expectations.get("final_review"), dict) else {}
    revision_policy = expectations.get("revision_policy") if isinstance(expectations.get("revision_policy"), dict) else {}
    return {
        "provided": bool(expectations),
        "step_id": str(step_quality.get("step_id") or ""),
        "worker": str(step_quality.get("worker") or ""),
        "check_count": len(step_quality.get("checks") if isinstance(step_quality.get("checks"), list) else []),
        "blocker_count": len(step_quality.get("blockers") if isinstance(step_quality.get("blockers"), list) else []),
        "revision_targets": step_quality.get("revision_targets", []) if isinstance(step_quality.get("revision_targets"), list) else [],
        "final_review": final_review,
        "revision_policy": revision_policy,
    }


def quality_expectation_findings(request: dict[str, Any]) -> list[dict[str, str]]:
    expectations = request.get("quality_expectations") if isinstance(request.get("quality_expectations"), dict) else {}
    step_quality = expectations.get("step_quality") if isinstance(expectations.get("step_quality"), dict) else {}
    if not step_quality:
        return []
    findings: list[dict[str, str]] = []
    step = request.get("step") if isinstance(request.get("step"), dict) else {}
    step_id = str(step.get("step_id") or "")
    if step_id and str(step_quality.get("step_id") or "") != step_id:
        findings.append({"severity": "blocker", "message": f"Quality expectations target another step: {step_quality.get('step_id')}"})
    if str(step_quality.get("worker") or "") not in {"", "ReductorVerifier"}:
        findings.append({"severity": "blocker", "message": f"Quality expectations target another worker: {step_quality.get('worker')}"})
    expected_artifacts = step.get("expected_artifacts") if isinstance(step.get("expected_artifacts"), list) else []
    if step_quality.get("expected_artifacts") != expected_artifacts:
        findings.append({"severity": "blocker", "message": "Quality expectations expected_artifacts do not match request.step"})
    for field_name in ("checks", "blockers", "revision_targets"):
        values = step_quality.get(field_name)
        if not isinstance(values, list) or not values:
            findings.append({"severity": "blocker", "message": f"Quality expectations missing non-empty {field_name}"})
    return findings


def review_artifacts(workspace_root: Path, critic_path: str) -> dict[str, Any]:
    reconstruction_path = sibling_artifact(critic_path, "reconstruction_ru.md")
    coverage_path = sibling_artifact(critic_path, "coverage_report.md")
    source_path = sibling_artifact(critic_path, "source_map.json")
    source_snapshots_path = sibling_artifact(critic_path, "source_snapshots.json")
    notes_path = sibling_artifact(critic_path, "direct_event_notes.json")
    timeline_path = sibling_artifact(critic_path, "timeline.json")
    corpus_path = sibling_artifact(critic_path, "corpus_index.json")
    required_paths = [corpus_path, reconstruction_path, coverage_path, source_path, source_snapshots_path, notes_path, timeline_path]
    missing_artifacts = [path for path in required_paths if not artifact_exists(workspace_root, path)]
    if missing_artifacts:
        findings = [{"severity": "blocker", "message": f"Missing artifact: {path}"} for path in missing_artifacts]
        return {
            "status": "failed",
            "approved": False,
            "missing_artifacts": missing_artifacts,
            "findings": findings,
            "warnings": [],
            "revision_plan": revision_plan_from_findings(findings, missing_artifacts),
        }

    source_map = load_json(workspace_root, source_path)
    source_snapshots = load_json(workspace_root, source_snapshots_path)
    notes = load_json(workspace_root, notes_path)
    timeline = load_json(workspace_root, timeline_path)
    reconstruction = read_text(workspace_root, reconstruction_path)
    coverage = read_text(workspace_root, coverage_path)
    revision_focus = revision_focus_from_artifacts(reconstruction, coverage)
    timeline_event_ids = {
        str(item.get("event_id"))
        for item in timeline.get("timeline", [])
        if isinstance(item, dict) and item.get("event_id")
    }
    note_by_event_id = {
        str(item.get("event_id")): item
        for item in notes.get("events", [])
        if isinstance(item, dict) and item.get("event_id")
    }
    findings: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    note_event_ids = set(note_by_event_id)
    for event_id in sorted(note_event_ids - timeline_event_ids):
        findings.append({"severity": "blocker", "message": f"Missing required direct event in timeline: {event_id}"})
    for event_id in sorted(timeline_event_ids - note_event_ids):
        findings.append({"severity": "blocker", "message": f"Timeline event lacks extracted direct-event note: {event_id}"})
    for event_id, note in sorted(note_by_event_id.items()):
        if not isinstance(note, dict) or not note.get("evidence_snapshots"):
            findings.append({"severity": "blocker", "message": f"Required event lacks fetched source evidence: {event_id}"})
    if should_apply_required_event_playbook(source_map, notes, timeline):
        for event_id, label in REQUIRED_DIRECT_EVENT_IDS.items():
            if event_id not in timeline_event_ids:
                findings.append({"severity": "blocker", "message": f"Missing required direct event in timeline: {label}"})
            elif not text_contains_markers(reconstruction, EVENT_TEXT_MARKERS[event_id]):
                findings.append({"severity": "blocker", "message": f"Draft does not visibly cover required event: {label}"})
            elif not note_by_event_id.get(event_id, {}).get("evidence_snapshots"):
                findings.append({"severity": "blocker", "message": f"Required event lacks fetched source evidence: {label}"})

    source_classes = {
        str(item.get("source_class") or item.get("type") or "")
        for item in source_map.get("sources", [])
        if isinstance(item, dict)
    }
    if "official_primary_narrative" not in source_classes:
        warnings.append({"severity": "warning", "message": "No official primary narrative source candidate is listed."})
    if "secondary_wiki" not in source_classes:
        warnings.append({"severity": "warning", "message": "No secondary wiki/source summary is listed for cross-checking."})
    if source_map.get("discovery_status") == "needs_live_discovery":
        findings.append({"severity": "blocker", "message": "Source discovery did not find concrete sources."})
    source_coverage = source_map.get("source_coverage") if isinstance(source_map.get("source_coverage"), dict) else {}
    if source_coverage and not source_coverage.get("ready_for_extraction"):
        findings.append({"severity": "blocker", "message": "Source coverage is not extraction-ready: official/primary evidence and secondary cross-checking are both required."})
    if "## Gaps" not in coverage or "Что еще надо проверить" not in reconstruction:
        findings.append({"severity": "blocker", "message": "Draft package does not expose coverage gaps clearly."})
    comprehensive_findings, comprehensive_metrics = comprehensive_depth_findings(source_map, notes, reconstruction)
    findings.extend(comprehensive_findings)

    notes_gaps = [str(item) for item in notes.get("gaps", []) if item]
    timeline_gaps = [str(item) for item in timeline.get("gaps", []) if item]
    if notes_gaps or timeline_gaps:
        warnings.append(
            {
                "severity": "warning",
                "message": "Worker package still depends on unverified or inaccessible primary wording.",
            }
        )
    timeline_summary = timeline.get("summary") if isinstance(timeline.get("summary"), dict) else {}
    generic_evidence_leads = int(timeline_summary.get("generic_evidence_leads") or 0)
    low_confidence_events = int(timeline_summary.get("low_confidence_events") or 0)
    if generic_evidence_leads:
        warnings.append(
            {
                "severity": "warning",
                "message": f"Timeline includes {generic_evidence_leads} generic low-confidence evidence lead(s).",
            }
        )

    status = "passed_with_warnings" if not findings else "needs_revision"
    return {
        "status": status,
        "approved": not findings,
        "checked_artifacts": required_paths,
        "required_direct_events": sorted(REQUIRED_DIRECT_EVENT_IDS),
        "findings": findings,
        "warnings": warnings,
        "revision_plan": revision_plan_from_findings(findings, []),
        "revision_focus": revision_focus,
        "metrics": {
            "sources": len(source_map.get("sources", [])),
            "direct_event_notes": len(notes.get("events", [])),
            "timeline_events": len(timeline_event_ids),
            "generic_evidence_leads": generic_evidence_leads,
            "low_confidence_events": low_confidence_events,
            "source_coverage_ready": bool(source_coverage.get("ready_for_extraction")) if source_coverage else None,
            "draft_chars": len(reconstruction),
            "comprehensive_depth": comprehensive_metrics,
            "snapshot_count": len(source_snapshots.get("snapshots", [])),
        },
    }


def run(request: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    step = request.get("step")
    if not isinstance(step, dict):
        return {"ok": False, "worker": "ReductorVerifier", "error": "request.step must be an object"}
    expected_artifacts = step.get("expected_artifacts")
    if not isinstance(expected_artifacts, list) or not expected_artifacts:
        return {"ok": False, "worker": "ReductorVerifier", "error": "step.expected_artifacts is empty"}
    critic_path = str(expected_artifacts[0])
    try:
        report = review_artifacts(workspace_root, critic_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "worker": "ReductorVerifier", "error": str(exc)}
    expectation_findings = quality_expectation_findings(request)
    if expectation_findings:
        report.setdefault("findings", []).extend(expectation_findings)
        report["approved"] = False
        report["status"] = "needs_revision"
        report["revision_plan"] = revision_plan_from_findings(report.get("findings", []), report.get("missing_artifacts", []))
    report["quality_expectations"] = quality_expectation_summary(request)
    host_path = sandbox_path(workspace_root, critic_path)
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "worker": "ReductorVerifier",
        "task_id": request.get("task_id"),
        "status": report["status"],
        "summary": f"Review finished with {len(report['findings'])} findings and {len(report['warnings'])} warnings.",
        "artifacts": [critic_path],
        "gaps": [item["message"] for item in report["findings"]],
        "revision_plan": report.get("revision_plan", {}),
        "confidence": "medium",
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run ReductorVerifier on a Worker API request JSON.")
    parser.add_argument("request_json")
    parser.add_argument("--workspace-root", default="runtime/reductor-work")
    args = parser.parse_args()
    payload = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
    request = payload.get("request") if isinstance(payload, dict) and isinstance(payload.get("request"), dict) else payload
    result = run(request, Path(args.workspace_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
