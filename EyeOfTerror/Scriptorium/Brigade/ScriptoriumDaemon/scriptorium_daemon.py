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

from scriptorium_model import model_unavailable_payload, parsed_model_content, request_required_scriptorium_guidance, request_scriptorium_model_guidance  # noqa: E402

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


def load_optional_json_artifact(workspace_root: Path, path: str) -> dict[str, Any]:
    host_path = sandbox_path(workspace_root, path)
    if not host_path.exists():
        return {}
    payload = json.loads(host_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


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


def research_intent_from_request(request: dict[str, Any]) -> dict[str, Any]:
    expectations = request.get("quality_expectations") if isinstance(request.get("quality_expectations"), dict) else {}
    return expectations.get("research_intent") if isinstance(expectations.get("research_intent"), dict) else {}


def claims_by_id(research_corpus: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("claim_id")): item
        for item in research_corpus.get("claims", [])
        if isinstance(item, dict) and item.get("claim_id")
    }


def evidence_line_for_claim(claim: dict[str, Any]) -> str:
    refs = claim.get("source_refs") if isinstance(claim.get("source_refs"), list) else []
    refs_text = ", ".join(str(item) for item in refs if str(item).strip()) or "source not mapped"
    confidence = str(claim.get("confidence") or "unknown")
    return f"Evidence trace: {claim.get('claim_id', '')} | confidence={confidence} | sources={refs_text}"


def model_chapter_drafts(decision: dict[str, Any]) -> dict[str, str]:
    parsed = parsed_model_content(decision)
    raw = parsed.get("chapter_drafts") or parsed.get("chapters") or {}
    drafts: dict[str, str] = {}
    if isinstance(raw, dict):
        for chapter_id, markdown in raw.items():
            text = str(markdown or "").strip()
            if chapter_id and text:
                drafts[str(chapter_id)] = text
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            chapter_id = str(item.get("chapter_id") or "").strip()
            markdown = str(item.get("markdown") or item.get("draft_markdown") or item.get("content") or "").strip()
            if chapter_id and markdown:
                drafts[chapter_id] = markdown
    return drafts


def required_evidence_markers(claims: list[dict[str, Any]]) -> list[str]:
    return [
        f"Evidence trace: {claim.get('claim_id')}"
        for claim in claims
        if str(claim.get("claim_id") or "").strip()
    ]


def model_chapter_is_grounded(markdown: str, claims: list[dict[str, Any]]) -> tuple[bool, str]:
    if not claims:
        return False, "chapter has no grounded claims"
    missing = [marker for marker in required_evidence_markers(claims) if marker not in markdown]
    if missing:
        return False, f"model draft missed evidence markers: {', '.join(missing)}"
    return True, ""


def section_claims(section: dict[str, Any], claim_index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    refs = section.get("required_claim_refs") if isinstance(section.get("required_claim_refs"), list) else []
    return [claim_index[str(ref)] for ref in refs if str(ref) in claim_index]


def build_mode_draft(
    source_map: dict[str, Any],
    research_corpus: dict[str, Any],
    structure_map: dict[str, Any],
    synthesis_plan: dict[str, Any],
    revision_context: dict[str, Any] | None = None,
) -> tuple[str, str]:
    output_mode = str(synthesis_plan.get("output_mode") or "research_report")
    topic = str(synthesis_plan.get("topic") or research_corpus.get("topic") or source_map.get("topic") or "задача")
    claim_index = claims_by_id(research_corpus)
    lines = [
        f"# {topic}",
        "",
        f"Output mode: {output_mode}",
        "",
    ]
    lines.extend(revision_context_lines(revision_context, "## Фокус ревизии"))
    sections = synthesis_plan.get("sections") if isinstance(synthesis_plan.get("sections"), list) else []
    for section in sections:
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or section.get("section_id") or "Раздел")
        claims = section_claims(section, claim_index)
        lines.append(f"## {title}")
        lines.append("")
        if section.get("requires_evidence") and not claims:
            lines.append("Раздел не написан: для него нет evidence trace в research_corpus.")
            lines.append("")
            continue
        if claims:
            for claim in claims:
                lines.append(str(claim.get("claim") or "").strip())
                lines.append("")
                lines.append(f"> {evidence_line_for_claim(claim)}")
                lines.append("")
        else:
            gaps = research_corpus.get("gaps") if isinstance(research_corpus.get("gaps"), list) else []
            if gaps:
                lines.append("Этот раздел фиксирует ограничения корпуса и не добавляет неподтвержденные сведения.")
            else:
                lines.append("Нет дополнительных неподтвержденных утверждений сверх корпуса.")
            lines.append("")
    contradictions = structure_map.get("contradictions") if isinstance(structure_map.get("contradictions"), list) else []
    gaps = list(source_map.get("coverage_gaps", [])) + list(research_corpus.get("gaps", []) if isinstance(research_corpus.get("gaps"), list) else [])
    if contradictions:
        lines.extend(["## Противоречия", ""])
        for item in contradictions:
            if isinstance(item, dict):
                lines.append(f"- {item.get('topic') or item.get('contradiction_id')}: {item.get('note') or item.get('summary')}")
        lines.append("")
    lines.extend(["## Что еще надо проверить", ""])
    for gap in dict.fromkeys(str(item) for item in gaps if item):
        lines.append(f"- {gap}")
    if not gaps:
        lines.append("- Явных пробелов в текущем корпусе не указано.")
    lines.append("")

    coverage = [
        "# Coverage Report",
        "",
        f"- Output mode: {output_mode}",
        f"- Intent: {synthesis_plan.get('intent', '')}",
        f"- Sources mapped: {len(research_corpus.get('sources', []) if isinstance(research_corpus.get('sources'), list) else [])}",
        f"- Claims: {len(claim_index)}",
        f"- Evidence excerpts: {len(research_corpus.get('evidence_excerpts', []) if isinstance(research_corpus.get('evidence_excerpts'), list) else [])}",
        f"- Unsupported sections: {len(synthesis_plan.get('unsupported_sections', []) if isinstance(synthesis_plan.get('unsupported_sections'), list) else [])}",
        "",
        "## Evidence Trace",
        "",
    ]
    for claim_id, claim in claim_index.items():
        coverage.append(f"- {claim_id}: {evidence_line_for_claim(claim)}")
    coverage.extend(["", "## Unsupported Sections", ""])
    unsupported = synthesis_plan.get("unsupported_sections") if isinstance(synthesis_plan.get("unsupported_sections"), list) else []
    if unsupported:
        for item in unsupported:
            if isinstance(item, dict):
                coverage.append(f"- {item.get('section_id')}: {item.get('reason')}")
    else:
        coverage.append("- none")
    coverage.extend(["", "## Gaps", ""])
    for gap in dict.fromkeys(str(item) for item in gaps if item):
        coverage.append(f"- {gap}")
    if not gaps:
        coverage.append("- none")
    return "\n".join(lines).rstrip() + "\n", "\n".join(coverage).rstrip() + "\n"


def fb2_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def unique_strings(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def chapter_required_claim_refs(chapter: dict[str, Any], synthesis_plan: dict[str, Any]) -> list[str]:
    direct_refs = chapter.get("required_claim_refs") if isinstance(chapter.get("required_claim_refs"), list) else []
    refs = unique_strings(direct_refs)
    if refs:
        return refs
    section_refs = set(unique_strings(chapter.get("section_refs") if isinstance(chapter.get("section_refs"), list) else []))
    if not section_refs:
        return []
    sections = synthesis_plan.get("sections") if isinstance(synthesis_plan.get("sections"), list) else []
    collected: list[Any] = []
    for section in sections:
        if not isinstance(section, dict) or str(section.get("section_id") or "") not in section_refs:
            continue
        section_claims = section.get("required_claim_refs") if isinstance(section.get("required_claim_refs"), list) else []
        collected.extend(section_claims)
    return unique_strings(collected)


def build_chapter_markdown(
    chapter: dict[str, Any],
    index: int,
    claim_index: dict[str, dict[str, Any]],
    synthesis_plan: dict[str, Any],
    research_corpus: dict[str, Any],
    model_drafts: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    chapter_id = str(chapter.get("chapter_id") or f"chapter_{index:02d}")
    title = str(chapter.get("title") or f"Глава {index}")
    claim_refs = chapter_required_claim_refs(chapter, synthesis_plan)
    claims = [claim_index[claim_ref] for claim_ref in claim_refs if claim_ref in claim_index]
    model_drafts = model_drafts or {}
    model_draft = str(model_drafts.get(chapter_id) or "").strip()
    model_draft_used = False
    model_draft_rejected = ""
    lines = [
        f"# {title}",
        "",
        f"Chapter ID: {chapter_id}",
        f"Output mode: {synthesis_plan.get('output_mode', '')}",
        "",
    ]
    missing_claim_refs = [claim_ref for claim_ref in claim_refs if claim_ref not in claim_index]
    if model_draft:
        model_draft_ok, model_draft_rejected = model_chapter_is_grounded(model_draft, claims)
        if model_draft_ok:
            lines = []
            if not model_draft.startswith("#"):
                lines.extend([f"# {title}", ""])
            lines.append(model_draft)
            if not model_draft.endswith("\n"):
                lines.append("")
            model_draft_used = True
        else:
            lines.extend(["Модельный черновик главы отклонён: " + model_draft_rejected, ""])
    if claims and not model_draft_used:
        for claim in claims:
            claim_text = str(claim.get("claim") or "").strip()
            if claim_text:
                lines.append(claim_text)
                lines.append("")
            lines.append(f"> {evidence_line_for_claim(claim)}")
            lines.append("")
    else:
        lines.append("Глава не развернута: для неё нет подтвержденных claims в research_corpus.")
        lines.append("")
    gaps = research_corpus.get("gaps") if isinstance(research_corpus.get("gaps"), list) else []
    if gaps:
        lines.extend(["## Ограничения главы", ""])
        for gap in unique_strings(gaps)[:6]:
            lines.append(f"- {gap}")
        lines.append("")
    record = {
        "chapter_id": chapter_id,
        "title": title,
        "required_claim_refs": claim_refs,
        "written_claim_refs": [str(claim.get("claim_id") or "") for claim in claims if claim.get("claim_id")],
        "missing_claim_refs": missing_claim_refs,
        "char_count": len("\n".join(lines)),
        "has_evidence_trace": bool(claims),
        "model_draft_used": model_draft_used,
        "model_draft_rejected": model_draft_rejected,
    }
    return "\n".join(lines).rstrip() + "\n", record


def build_continuity_report(chapter_records: list[dict[str, Any]], synthesis_plan: dict[str, Any]) -> dict[str, Any]:
    seen_bodies: set[tuple[str, ...]] = set()
    repeated_chapters: list[str] = []
    for record in chapter_records:
        fingerprint = tuple(record.get("written_claim_refs", []))
        chapter_id = str(record.get("chapter_id") or "")
        if fingerprint and fingerprint in seen_bodies:
            repeated_chapters.append(chapter_id)
        if fingerprint:
            seen_bodies.add(fingerprint)
    missing_evidence = [str(record.get("chapter_id") or "") for record in chapter_records if not record.get("has_evidence_trace")]
    missing_claims = {
        str(record.get("chapter_id") or ""): record.get("missing_claim_refs", [])
        for record in chapter_records
        if record.get("missing_claim_refs")
    }
    checks = [
        "chapter order preserved",
        "chapter-specific claim refs used",
        "source limitations repeated where corpus gaps exist",
    ]
    status = "completed" if not repeated_chapters and not missing_evidence and not missing_claims else "needs_revision"
    return {
        "status": status,
        "output_mode": synthesis_plan.get("output_mode", ""),
        "chapter_count": len(chapter_records),
        "checks": checks,
        "repeated_chapters": repeated_chapters,
        "missing_evidence_trace_chapters": missing_evidence,
        "missing_claim_refs_by_chapter": missing_claims,
        "chapters": chapter_records,
    }


def build_editor_report(chapter_records: list[dict[str, Any]], synthesis_plan: dict[str, Any]) -> dict[str, Any]:
    unsupported = synthesis_plan.get("unsupported_sections") if isinstance(synthesis_plan.get("unsupported_sections"), list) else []
    short_chapters = [
        str(record.get("chapter_id") or "")
        for record in chapter_records
        if int(record.get("char_count") or 0) < 120
    ]
    status = "completed" if not unsupported and not short_chapters else "needs_revision"
    return {
        "status": status,
        "checks": [
            "unsupported sections blocked",
            "evidence trace retained",
            "chapter minimum substance checked",
            "model chapter drafts accepted only with required evidence trace",
        ],
        "unsupported_sections": unsupported,
        "short_chapters": short_chapters,
        "grounded_chapter_count": sum(1 for record in chapter_records if record.get("has_evidence_trace")),
        "model_drafted_chapter_count": sum(1 for record in chapter_records if record.get("model_draft_used")),
        "rejected_model_drafts": {
            str(record.get("chapter_id") or ""): record.get("model_draft_rejected")
            for record in chapter_records
            if record.get("model_draft_rejected")
        },
        "chapter_count": len(chapter_records),
    }


def write_book_artifacts(
    workspace_root: Path,
    expected_artifacts: list[Any],
    reconstruction_path: str,
    draft: str,
    synthesis_plan: dict[str, Any],
    research_corpus: dict[str, Any] | None = None,
    model_guidance: dict[str, Any] | None = None,
) -> list[str]:
    paths = [str(item) for item in expected_artifacts]
    chapter_paths = [path for path in paths if "/chapters/" in path and path.endswith(".md")]
    if not chapter_paths:
        return []
    artifacts: list[str] = []
    research_corpus = research_corpus or {}
    claim_index = claims_by_id(research_corpus)
    model_drafts = model_chapter_drafts(model_guidance or {})
    chapter_plan_path = sibling_artifact(reconstruction_path, "chapter_plan.json")
    chapter_plan = load_optional_json_artifact(workspace_root, chapter_plan_path)
    chapters = chapter_plan.get("chapters") if isinstance(chapter_plan.get("chapters"), list) else []
    chapter_records: list[dict[str, Any]] = []
    for index, chapter_path in enumerate(chapter_paths, start=1):
        chapter = chapters[index - 1] if index - 1 < len(chapters) and isinstance(chapters[index - 1], dict) else {}
        content, record = build_chapter_markdown(chapter, index, claim_index, synthesis_plan, research_corpus, model_drafts)
        sandbox_path(workspace_root, chapter_path).parent.mkdir(parents=True, exist_ok=True)
        sandbox_path(workspace_root, chapter_path).write_text(content, encoding="utf-8")
        record["path"] = chapter_path
        chapter_records.append(record)
        artifacts.append(chapter_path)
    manuscript_path = next((path for path in paths if path.endswith("/manuscript_ru.md")), "")
    if manuscript_path:
        manuscript = "\n\n".join(sandbox_path(workspace_root, path).read_text(encoding="utf-8") for path in chapter_paths)
        sandbox_path(workspace_root, manuscript_path).write_text(manuscript, encoding="utf-8")
        artifacts.append(manuscript_path)
    continuity_path = next((path for path in paths if path.endswith("/continuity_report.json")), "")
    if continuity_path:
        payload = build_continuity_report(chapter_records, synthesis_plan)
        sandbox_path(workspace_root, continuity_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        artifacts.append(continuity_path)
    editor_path = next((path for path in paths if path.endswith("/editor_report.json")), "")
    if editor_path:
        payload = build_editor_report(chapter_records, synthesis_plan)
        sandbox_path(workspace_root, editor_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        artifacts.append(editor_path)
    fb2_path = next((path for path in paths if path.endswith("/manuscript.fb2")), "")
    if fb2_path and manuscript_path:
        title = fb2_escape(str(synthesis_plan.get("topic") or "Manuscript"))
        sections = []
        for chapter_path in chapter_paths:
            body = fb2_escape(sandbox_path(workspace_root, chapter_path).read_text(encoding="utf-8"))
            sections.append(f"<section><p>{body}</p></section>")
        fb2 = f'<?xml version="1.0" encoding="utf-8"?>\n<FictionBook><description><title-info><book-title>{title}</book-title><lang>ru</lang></title-info></description><body>{"".join(sections)}</body></FictionBook>\n'
        sandbox_path(workspace_root, fb2_path).write_text(fb2, encoding="utf-8")
        artifacts.append(fb2_path)
    return artifacts


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
    research_corpus: dict[str, Any] | None = None,
    synthesis_plan: dict[str, Any] | None = None,
    revision_context: dict[str, Any] | None = None,
) -> str:
    research_corpus = research_corpus or {}
    synthesis_plan = synthesis_plan or {}
    sources = [item for item in source_map.get("sources", []) if isinstance(item, dict)]
    snapshots = [item for item in source_snapshots.get("snapshots", []) if isinstance(item, dict)]
    events = [item for item in timeline.get("timeline", []) if isinstance(item, dict)]
    claim_index = claims_by_id(research_corpus)
    trace_refs = synthesis_plan.get("evidence_trace", {}).get("claim_refs") if isinstance(synthesis_plan.get("evidence_trace"), dict) else []
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
    lines.extend(["", "## Evidence Trace", ""])
    if trace_refs:
        for claim_ref in trace_refs:
            claim = claim_index.get(str(claim_ref))
            if claim:
                lines.append(f"- {claim_ref}: {evidence_line_for_claim(claim)}")
            else:
                lines.append(f"- {claim_ref}: missing from research_corpus")
    elif claim_index:
        for claim_id, claim in claim_index.items():
            lines.append(f"- {claim_id}: {evidence_line_for_claim(claim)}")
    else:
        lines.append("- no research_corpus claim refs supplied")
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
    research_corpus: dict[str, Any] | None = None,
    synthesis_plan: dict[str, Any] | None = None,
    structure_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "task_id": request.get("task_id"),
        "step": request.get("step"),
        "contract": request.get("contract") if isinstance(request.get("contract"), dict) else {},
        "quality_expectations": request.get("quality_expectations") if isinstance(request.get("quality_expectations"), dict) else {},
        "revision_context": request.get("revision_context") if isinstance(request.get("revision_context"), dict) else {},
        "research_corpus": research_corpus or {},
        "synthesis_plan": synthesis_plan or {},
        "structure_map": structure_map or {},
        "output_mode": (synthesis_plan or {}).get("output_mode", ""),
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
    research_corpus_path = sibling_artifact(reconstruction_path, "research_corpus.json")
    structure_map_path = sibling_artifact(reconstruction_path, "structure_map.json")
    synthesis_plan_path = sibling_artifact(reconstruction_path, "synthesis_plan.json")
    try:
        source_map = load_json_artifact(workspace_root, source_path)
        source_snapshots = load_json_artifact(workspace_root, source_snapshots_path)
        notes = load_json_artifact(workspace_root, notes_path)
        timeline = load_optional_json_artifact(workspace_root, timeline_path)
        research_corpus = load_optional_json_artifact(workspace_root, research_corpus_path)
        structure_map = load_optional_json_artifact(workspace_root, structure_map_path)
        synthesis_plan = load_optional_json_artifact(workspace_root, synthesis_plan_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "worker": "ScriptoriumDaemon", "error": str(exc)}

    revision_context = request.get("revision_context") if isinstance(request.get("revision_context"), dict) else None
    if synthesis_plan and research_corpus:
        output_mode = str(synthesis_plan.get("output_mode") or "")
        if output_mode == "event_reconstruction":
            reconstruction = build_reconstruction(source_map, source_snapshots, notes, timeline, revision_context)
            coverage_report = build_coverage_report(source_map, source_snapshots, notes, timeline, research_corpus, synthesis_plan, revision_context)
        else:
            reconstruction = ""
            coverage_report = ""
            reconstruction, coverage_report = build_mode_draft(source_map, research_corpus, structure_map, synthesis_plan, revision_context)
    else:
        output_mode = str(research_intent_from_request(request).get("output_mode") or "event_reconstruction")
        reconstruction = build_reconstruction(source_map, source_snapshots, notes, timeline, revision_context)
        coverage_report = build_coverage_report(source_map, source_snapshots, notes, timeline, revision_context=revision_context)
    guidance = request_required_scriptorium_guidance(
        "ScriptoriumDaemon",
        request,
        model_payload(request, source_map, notes, timeline, reconstruction, coverage_report, research_corpus, synthesis_plan, structure_map),
        (
            "You are the Scriptorium writer. Improve the Russian output only from supplied research_corpus, "
            "synthesis_plan, output_mode, timeline/structure, evidence excerpts, and gaps. Do not invent unsupported sections. Return JSON with optional "
            "reconstruction_ru_markdown or appendix_markdown plus warnings."
        ),
        request_guidance,
    )
    if not guidance.get("ok"):
        return model_unavailable_payload("ScriptoriumDaemon", request.get("task_id"), guidance)
    reconstruction, coverage_report = apply_model_guidance(reconstruction, coverage_report, guidance)
    for output_path, content in ((reconstruction_path, reconstruction), (coverage_path, coverage_report)):
        host_path = sandbox_path(workspace_root, output_path)
        host_path.parent.mkdir(parents=True, exist_ok=True)
        host_path.write_text(content, encoding="utf-8")
    extra_artifacts = []
    if output_mode in {"book_manuscript", "book_manuscript_with_timeline"}:
        extra_artifacts = write_book_artifacts(workspace_root, expected_artifacts, reconstruction_path, reconstruction, synthesis_plan, research_corpus, guidance)
    guidance_path = write_model_guidance_artifact(workspace_root, reconstruction_path, guidance)
    return {
        "ok": True,
        "worker": "ScriptoriumDaemon",
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": f"Draft written for {output_mode}.",
        "artifacts": [reconstruction_path, coverage_path, *extra_artifacts, guidance_path],
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
