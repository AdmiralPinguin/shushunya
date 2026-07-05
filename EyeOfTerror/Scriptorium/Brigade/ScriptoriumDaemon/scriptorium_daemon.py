from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable


PHASE_TITLES = {
    "prelude": "Предыстория",
    "arrival": "Прибытие",
    "parley": "Переговоры",
    "parley_collapse": "Срыв переговоров",
    "escalation": "Эскалация",
    "battle": "Битва",
    "turning_point": "Перелом",
    "betrayal": "Предательство",
    "aftermath_boundary": "Граница последствий",
}

EVENT_PLAYBOOK_DIR = Path(__file__).resolve().parents[1] / "NoosphericExtractor" / "playbooks"
BRIGADE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIGADE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIGADE_ROOT))

from scriptorium_model import parsed_model_content, request_scriptorium_model_guidance  # noqa: E402

GuidanceFn = Callable[[str, dict[str, Any], str], dict[str, Any]]


def load_event_playbooks() -> list[dict[str, Any]]:
    playbooks: list[dict[str, Any]] = []
    if not EVENT_PLAYBOOK_DIR.exists():
        return playbooks
    for path in sorted(EVENT_PLAYBOOK_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            playbooks.append(payload)
    return playbooks


def playbook_narratives_ru() -> dict[str, str]:
    narratives: dict[str, str] = {}
    for playbook in load_event_playbooks():
        for event in playbook.get("events", []):
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("event_id") or "")
            narrative = str(event.get("narrative_ru") or "")
            if event_id and narrative:
                narratives[event_id] = narrative
    return narratives


PLAYBOOK_RU_SUMMARIES = playbook_narratives_ru()


def sandbox_path(workspace_root: Path, path: str) -> Path:
    if not path.startswith("/work/"):
        raise ValueError(f"unsupported sandbox path: {path}")
    return workspace_root / path.removeprefix("/work/")


def sibling_artifact(output_path: str, filename: str) -> str:
    if not output_path.startswith("/work/"):
        raise ValueError(f"unsupported output path: {output_path}")
    parent = output_path.rsplit("/", 1)[0]
    return f"{parent}/{filename}"


def load_json_artifact(workspace_root: Path, path: str) -> dict[str, Any]:
    host_path = sandbox_path(workspace_root, path)
    if not host_path.exists():
        raise FileNotFoundError(path)
    payload = json.loads(host_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"artifact must be an object: {path}")
    return payload


def confidence_marker(value: Any) -> str:
    text = str(value or "").lower()
    if text in {"high", "medium-high"}:
        return ""
    if text == "medium":
        return " По этому пункту нужна сверка с первичным текстом."
    return " Уверенность по этому пункту ограничена."


def event_text(event: dict[str, Any], notes_by_id: dict[str, dict[str, Any]]) -> str:
    event_id = str(event.get("event_id") or "")
    note = notes_by_id.get(event_id, {})
    text = str(note.get("narrative_ru") or PLAYBOOK_RU_SUMMARIES.get(event_id) or event.get("summary") or "")
    refs = event.get("source_refs") or note.get("source_refs") or []
    ref_text = ", ".join(str(item) for item in refs if item)
    suffix = confidence_marker(event.get("confidence") or note.get("confidence"))
    if ref_text:
        return f"{text}{suffix} Источники: {ref_text}."
    return f"{text}{suffix}"


def event_evidence_lines(event: dict[str, Any], notes_by_id: dict[str, dict[str, Any]]) -> list[str]:
    note = notes_by_id.get(str(event.get("event_id") or ""), {})
    evidence = note.get("evidence_snapshots") if isinstance(note.get("evidence_snapshots"), list) else []
    lines: list[str] = []
    for item in evidence[:3]:
        if not isinstance(item, dict):
            continue
        excerpt = " ".join(str(item.get("excerpt") or "").split())
        if not excerpt:
            continue
        source_title = str(item.get("source_title") or "source")
        markers = str(item.get("matched_markers") or "")
        primary_text = " [primary]" if item.get("is_primary_source") else ""
        marker_text = f"; markers: {markers}" if markers else ""
        lines.append(f"> {source_title}{primary_text}{marker_text}: {excerpt}")
    return lines


def revision_context_lines(revision_context: dict[str, Any] | None, heading: str) -> list[str]:
    if not revision_context:
        return []
    reasons = [str(item).strip() for item in revision_context.get("reasons", []) if str(item).strip()]
    source_steps = [str(item).strip() for item in revision_context.get("source_steps", []) if str(item).strip()]
    priority = str(revision_context.get("priority") or "").strip()
    lines = [heading, ""]
    if priority:
        lines.append(f"- Priority: {priority}")
    for reason in dict.fromkeys(reasons):
        lines.append(f"- Reason: {reason}")
    for source in dict.fromkeys(source_steps):
        lines.append(f"- Source step: {source}")
    lines.append("")
    return lines


def source_coverage_lines(source_map: dict[str, Any], heading: str) -> list[str]:
    coverage = source_map.get("source_coverage") if isinstance(source_map.get("source_coverage"), dict) else {}
    if not coverage:
        return []
    lines = [heading, ""]
    lines.append(f"- Source count: {coverage.get('source_count', 0)}")
    if coverage.get("local_corpus_source_count") is not None:
        lines.append(f"- Local corpus sources: {coverage.get('local_corpus_source_count', 0)}")
    lines.append(f"- Official or primary support: {'yes' if coverage.get('has_official') or coverage.get('has_primary_or_publication') else 'no'}")
    lines.append(f"- Secondary cross-check: {'yes' if coverage.get('has_secondary_crosscheck') else 'no'}")
    lines.append(f"- Ready for extraction: {'yes' if coverage.get('ready_for_extraction') else 'no'}")
    source_types = coverage.get("source_types") if isinstance(coverage.get("source_types"), list) else []
    if source_types:
        lines.append(f"- Source types: {', '.join(str(item) for item in source_types)}")
    requirements = source_map.get("corpus_requirements") if isinstance(source_map.get("corpus_requirements"), dict) else {}
    if requirements.get("required"):
        missing = requirements.get("missing_primary_texts") if isinstance(requirements.get("missing_primary_texts"), list) else []
        titles = [str(item.get("title") or "") for item in missing if isinstance(item, dict) and item.get("title")]
        if titles:
            lines.append(f"- Missing local primary texts: {', '.join(titles)}")
    lines.append("")
    return lines


def evidence_counts_by_source(notes: dict[str, Any], primary_only: bool = False) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for event in notes.get("events", []):
        if not isinstance(event, dict):
            continue
        evidence = event.get("evidence_snapshots") if isinstance(event.get("evidence_snapshots"), list) else []
        for item in evidence:
            if not isinstance(item, dict):
                continue
            if primary_only and not item.get("is_primary_source"):
                continue
            source_title = str(item.get("source_title") or "")
            if source_title:
                counts[source_title] += 1
    return dict(counts)


def source_match_tokens(text: str) -> set[str]:
    stopwords = {"the", "and", "for", "with", "warhammer", "black", "library", "ebook", "novel", "local"}
    return {token for token in "".join(char.lower() if char.isalnum() else " " for char in text).split() if len(token) > 2 and token not in stopwords}


def evidence_count_for_source(source: dict[str, Any], evidence_counts: dict[str, int]) -> int:
    title = str(source.get("title") or "")
    if evidence_counts.get(title):
        return evidence_counts[title]
    source_tokens = source_match_tokens(
        " ".join(
            [
                title,
                str(source.get("local_path") or ""),
                str(source.get("corpus_relative_path") or ""),
            ]
        )
    )
    if not source_tokens:
        return 0
    required = min(2, len(source_tokens))
    total = 0
    for evidence_title, count in evidence_counts.items():
        evidence_tokens = source_match_tokens(evidence_title)
        if evidence_tokens and len(source_tokens & evidence_tokens) >= required:
            total += count
    return total


def source_inventory_lines(source_map: dict[str, Any], source_snapshots: dict[str, Any], notes: dict[str, Any] | None = None) -> list[str]:
    sources = [item for item in source_map.get("sources", []) if isinstance(item, dict)]
    if not sources:
        return []
    lines = ["## Источники и доступность", ""]
    evidence_counts = evidence_counts_by_source(notes or {})
    primary_evidence_counts = evidence_counts_by_source(notes or {}, primary_only=True)
    fetched_by_title = {
        str(item.get("source_title") or ""): item
        for item in source_snapshots.get("snapshots", [])
        if isinstance(item, dict)
    }
    skipped_by_title = {
        str(item.get("source_title") or ""): item
        for item in source_snapshots.get("skipped", [])
        if isinstance(item, dict)
    }
    for source in sources:
        title = str(source.get("title") or "untitled")
        source_class = str(source.get("source_class") or source.get("type") or "unknown")
        reliability = str(source.get("reliability") or "unknown")
        expected_use = str(source.get("expected_use") or "")
        snapshot = fetched_by_title.get(title)
        skipped = skipped_by_title.get(title)
        if snapshot:
            availability = "fetched" if snapshot.get("ok") else f"failed: {snapshot.get('error') or 'fetch failed'}"
        elif skipped:
            availability = f"not fetched: {skipped.get('reason') or 'skipped'}"
        else:
            availability = "not requested"
        evidence_count = evidence_count_for_source(source, evidence_counts)
        primary_evidence_count = evidence_count_for_source(source, primary_evidence_counts)
        if evidence_count:
            direct_evidence = f"matched {evidence_count} event marker(s)"
            if primary_evidence_count:
                direct_evidence += f"; primary matched {primary_evidence_count}"
        elif snapshot and snapshot.get("ok"):
            direct_evidence = "no direct event markers"
        elif skipped:
            direct_evidence = "unavailable"
        else:
            direct_evidence = "not checked"
        use_text = f" | use={expected_use}" if expected_use else ""
        lines.append(
            f"- {title} | class={source_class} | reliability={reliability} | "
            f"availability={availability} | direct_evidence={direct_evidence}{use_text}"
        )
    lines.append("")
    return lines


def build_reconstruction(
    source_map: dict[str, Any],
    source_snapshots: dict[str, Any],
    notes: dict[str, Any],
    timeline: dict[str, Any],
    revision_context: dict[str, Any] | None = None,
) -> str:
    topic = str(timeline.get("topic") or notes.get("topic") or source_map.get("topic") or "задача")
    notes_by_id = {
        str(item.get("event_id")): item
        for item in notes.get("events", [])
        if isinstance(item, dict) and item.get("event_id")
    }
    events = [item for item in timeline.get("timeline", []) if isinstance(item, dict)]
    by_phase: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_phase[str(event.get("phase") or "unknown")].append(event)

    lines = [
        f"# Реконструкция: {topic}",
        "",
        "Это рабочая реконструкция, собранная из извлеченных событий. Она отделяет прямой ход событий от последствий и не закрывает пробелы выдуманными деталями.",
        "",
    ]
    lines.extend(revision_context_lines(revision_context, "## Фокус ревизии"))
    lines.extend(source_coverage_lines(source_map, "## Надёжность источников"))
    lines.extend(source_inventory_lines(source_map, source_snapshots, notes))
    for phase, phase_events in by_phase.items():
        lines.append(f"## {PHASE_TITLES.get(phase, phase)}")
        lines.append("")
        for event in phase_events:
            lines.append(event_text(event, notes_by_id))
            evidence_lines = event_evidence_lines(event, notes_by_id)
            if evidence_lines:
                lines.append("")
                lines.extend(evidence_lines)
            lines.append("")
    contradictions = timeline.get("contradictions", [])
    if contradictions:
        lines.append("## Замечания к хронологии")
        lines.append("")
        for item in contradictions:
            if isinstance(item, dict):
                lines.append(f"- {item.get('topic')}: {item.get('note')}")
        lines.append("")
    gaps = list(source_map.get("coverage_gaps", [])) + list(notes.get("gaps", [])) + list(timeline.get("gaps", []))
    if gaps:
        lines.append("## Что еще надо проверить")
        lines.append("")
        for gap in dict.fromkeys(str(item) for item in gaps if item):
            lines.append(f"- {gap}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_coverage_report(
    source_map: dict[str, Any],
    source_snapshots: dict[str, Any],
    notes: dict[str, Any],
    timeline: dict[str, Any],
    revision_context: dict[str, Any] | None = None,
) -> str:
    sources = [item for item in source_map.get("sources", []) if isinstance(item, dict)]
    snapshots = [item for item in source_snapshots.get("snapshots", []) if isinstance(item, dict)]
    events = [item for item in timeline.get("timeline", []) if isinstance(item, dict)]
    notes_by_id = {
        str(item.get("event_id")): item
        for item in notes.get("events", [])
        if isinstance(item, dict) and item.get("event_id")
    }
    gaps = list(source_map.get("coverage_gaps", [])) + list(notes.get("gaps", [])) + list(timeline.get("gaps", []))
    lines = [
        "# Coverage Report",
        "",
        f"- Discovery status: {source_map.get('discovery_status', 'unknown')}",
        f"- Sources mapped: {len(sources)}",
        f"- Source URLs fetched: {sum(1 for item in snapshots if item.get('ok'))}",
        f"- Source URL failures: {sum(1 for item in snapshots if not item.get('ok'))}",
        f"- Direct events extracted: {len(notes.get('events', []))}",
        f"- Timeline events: {len(events)}",
        "",
    ]
    lines.extend(revision_context_lines(revision_context, "## Revision Context"))
    lines.extend(source_coverage_lines(source_map, "## Source Coverage"))
    lines.extend(["## Sources", ""])
    for source in sources:
        title = source.get("title", "")
        source_class = source.get("source_class", source.get("type", ""))
        reliability = source.get("reliability", "")
        use = source.get("expected_use", "")
        lines.append(f"- {title} | {source_class} | reliability={reliability} | {use}")
    if snapshots:
        lines.extend(["", "## Source Snapshots", ""])
        for snapshot in snapshots:
            status = "ok" if snapshot.get("ok") else "failed"
            title = snapshot.get("source_title", "")
            final_url = snapshot.get("final_url") or snapshot.get("requested_url") or ""
            detail = snapshot.get("title") or snapshot.get("error") or ""
            lines.append(f"- {title} | {status} | {final_url} | {detail}")
    lines.extend(["", "## Gaps", ""])
    for gap in dict.fromkeys(str(item) for item in gaps if item):
        lines.append(f"- {gap}")
    lines.extend(["", "## Event Coverage", ""])
    for event in events:
        refs = ", ".join(str(item) for item in event.get("source_refs", []) if item)
        note = notes_by_id.get(str(event.get("event_id") or ""), {})
        evidence = "; ".join(
            f"{item.get('source_title')}: {item.get('matched_markers')}"
            for item in note.get("evidence_snapshots", [])
            if isinstance(item, dict)
        )
        primary_evidence = "; ".join(
            f"{item.get('source_title')}: {item.get('matched_markers')}"
            for item in note.get("primary_evidence_snapshots", [])
            if isinstance(item, dict)
        )
        excerpts = " || ".join(
            " ".join(str(item.get("excerpt") or "").split())[:220]
            for item in note.get("evidence_snapshots", [])
            if isinstance(item, dict) and item.get("excerpt")
        )
        evidence_text = f" | evidence={evidence}" if evidence else " | evidence=missing"
        primary_text = f" | primary_evidence={primary_evidence}" if primary_evidence else ""
        excerpt_text = f" | excerpts={excerpts}" if excerpts else ""
        method = str(event.get("extraction_method") or note.get("extraction_method") or "")
        method_text = f" | method={method}" if method else ""
        lead_text = " | evidence_lead=true" if event.get("evidence_lead") or method == "generic_snapshot_lead" else ""
        lines.append(
            f"- {event.get('event_id')} | phase={event.get('phase')} | "
            f"confidence={event.get('confidence')} | refs={refs}{method_text}{lead_text}{evidence_text}{primary_text}{excerpt_text}"
        )
    return "\n".join(lines).rstrip() + "\n"


def model_payload(
    request: dict[str, Any],
    source_map: dict[str, Any],
    notes: dict[str, Any],
    timeline: dict[str, Any],
    reconstruction: str,
    coverage_report: str,
) -> dict[str, Any]:
    return {
        "task_id": request.get("task_id"),
        "step": request.get("step"),
        "contract": request.get("contract") if isinstance(request.get("contract"), dict) else {},
        "quality_expectations": request.get("quality_expectations") if isinstance(request.get("quality_expectations"), dict) else {},
        "revision_context": request.get("revision_context") if isinstance(request.get("revision_context"), dict) else {},
        "source_summary": {
            "topic": source_map.get("topic"),
            "discovery_status": source_map.get("discovery_status"),
            "coverage": source_map.get("source_coverage", {}),
            "coverage_gaps": source_map.get("coverage_gaps", []),
            "source_count": len(source_map.get("sources", [])) if isinstance(source_map.get("sources"), list) else 0,
        },
        "events": notes.get("events", []) if isinstance(notes.get("events"), list) else [],
        "timeline": timeline.get("timeline", []) if isinstance(timeline.get("timeline"), list) else [],
        "timeline_gaps": timeline.get("gaps", []) if isinstance(timeline.get("gaps"), list) else [],
        "draft_reconstruction_preview": reconstruction[:24000],
        "coverage_report_preview": coverage_report[:12000],
    }


def model_guidance_section(decision: dict[str, Any]) -> list[str]:
    if not decision.get("ok"):
        return []
    parsed = parsed_model_content(decision)
    appendix = str(
        parsed.get("appendix_markdown")
        or parsed.get("narrative_addendum")
        or parsed.get("revision_notes")
        or ""
    ).strip()
    if not appendix:
        content = str(decision.get("content") or "").strip()
        if not content or content.startswith("{"):
            return []
        appendix = content
    return [
        "## Модельная редактура",
        "",
        appendix,
        "",
    ]


def apply_model_guidance(reconstruction: str, coverage_report: str, decision: dict[str, Any]) -> tuple[str, str]:
    if not decision.get("ok"):
        return reconstruction, coverage_report
    parsed = parsed_model_content(decision)
    replacement = str(parsed.get("reconstruction_ru_markdown") or parsed.get("draft_markdown") or "").strip()
    if replacement and "## Что еще надо проверить" in replacement:
        reconstruction = replacement.rstrip() + "\n"
    else:
        section = model_guidance_section(decision)
        if section:
            reconstruction = reconstruction.rstrip() + "\n\n" + "\n".join(section).rstrip() + "\n"
    coverage_lines = [
        coverage_report.rstrip(),
        "",
        "## Model Guidance",
        "",
        f"- Status: {decision.get('status', 'unknown')}",
        f"- Role: {decision.get('role', 'ScriptoriumDaemon')}",
    ]
    parsed_keys = ", ".join(sorted(parsed)) if parsed else ""
    if parsed_keys:
        coverage_lines.append(f"- Parsed keys: {parsed_keys}")
    return reconstruction, "\n".join(coverage_lines).rstrip() + "\n"


def write_model_guidance_artifact(workspace_root: Path, reconstruction_path: str, decision: dict[str, Any]) -> str:
    output_path = sibling_artifact(reconstruction_path, "scriptorium_model_guidance.json")
    host_path = sandbox_path(workspace_root, output_path)
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text(json.dumps(decision, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def run(
    request: dict[str, Any],
    workspace_root: Path,
    request_guidance: GuidanceFn = request_scriptorium_model_guidance,
) -> dict[str, Any]:
    step = request.get("step")
    if not isinstance(step, dict):
        return {"ok": False, "worker": "ScriptoriumDaemon", "error": "request.step must be an object"}
    expected_artifacts = step.get("expected_artifacts")
    if not isinstance(expected_artifacts, list) or len(expected_artifacts) < 2:
        return {"ok": False, "worker": "ScriptoriumDaemon", "error": "step.expected_artifacts must contain reconstruction and coverage report"}
    reconstruction_path = str(expected_artifacts[0])
    coverage_path = str(expected_artifacts[1])
    source_path = sibling_artifact(reconstruction_path, "source_map.json")
    source_snapshots_path = sibling_artifact(reconstruction_path, "source_snapshots.json")
    notes_path = sibling_artifact(reconstruction_path, "direct_event_notes.json")
    timeline_path = sibling_artifact(reconstruction_path, "timeline.json")
    try:
        source_map = load_json_artifact(workspace_root, source_path)
        source_snapshots = load_json_artifact(workspace_root, source_snapshots_path)
        notes = load_json_artifact(workspace_root, notes_path)
        timeline = load_json_artifact(workspace_root, timeline_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "worker": "ScriptoriumDaemon", "error": str(exc)}

    revision_context = request.get("revision_context") if isinstance(request.get("revision_context"), dict) else None
    reconstruction = build_reconstruction(source_map, source_snapshots, notes, timeline, revision_context)
    coverage_report = build_coverage_report(source_map, source_snapshots, notes, timeline, revision_context)
    guidance = request_guidance(
        "ScriptoriumDaemon",
        model_payload(request, source_map, notes, timeline, reconstruction, coverage_report),
        (
            "You are the Scriptorium writer. Improve the Russian reconstruction only from supplied facts, "
            "timeline, evidence excerpts, and gaps. Do not invent unsupported events. Return JSON with optional "
            "reconstruction_ru_markdown or appendix_markdown plus warnings."
        ),
    )
    reconstruction, coverage_report = apply_model_guidance(reconstruction, coverage_report, guidance)
    for output_path, content in ((reconstruction_path, reconstruction), (coverage_path, coverage_report)):
        host_path = sandbox_path(workspace_root, output_path)
        host_path.parent.mkdir(parents=True, exist_ok=True)
        host_path.write_text(content, encoding="utf-8")
    guidance_path = write_model_guidance_artifact(workspace_root, reconstruction_path, guidance)
    return {
        "ok": True,
        "worker": "ScriptoriumDaemon",
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": "Draft reconstruction and coverage report written.",
        "artifacts": [reconstruction_path, coverage_path, guidance_path],
        "model_guidance": guidance,
        "gaps": list(dict.fromkeys(str(item) for item in timeline.get("gaps", []) if item)),
        "confidence": "medium",
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run ScriptoriumDaemon on a Worker API request JSON.")
    parser.add_argument("request_json")
    parser.add_argument("--workspace-root", default="runtime/scriptorium-work")
    args = parser.parse_args()
    payload = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
    request = payload.get("request") if isinstance(payload, dict) and isinstance(payload.get("request"), dict) else payload
    result = run(request, Path(args.workspace_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
