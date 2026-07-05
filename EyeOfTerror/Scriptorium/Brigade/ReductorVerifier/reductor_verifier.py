from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable


EVENT_PLAYBOOK_DIR = Path(__file__).resolve().parents[1] / "NoosphericExtractor" / "playbooks"
BRIGADE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIGADE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIGADE_ROOT))

from scriptorium_model import (
    model_unavailable_payload,
    parsed_model_content,
    request_required_scriptorium_guidance,
    request_scriptorium_model_guidance,
    research_intent_from_worker_request,
)  # noqa: E402

GuidanceFn = Callable[[str, dict[str, Any], str], dict[str, Any]]

ARTIFACT_REWORK_TARGETS = {
    "corpus_index.json": ("corpus_ingestion", "CorpusIngestor"),
    "source_map.json": ("source_discovery", "Lexmechanic"),
    "source_snapshots.json": ("source_acquisition", "AuspexBrowser"),
    "rendered_snapshots.json": ("source_rendering", "OcularisRenderium"),
    "direct_event_notes.json": ("fact_extraction", "NoosphericExtractor"),
    "research_corpus.json": ("fact_extraction", "NoosphericExtractor"),
    "timeline.json": ("timeline", "Chronologis"),
    "structure_map.json": ("structure_mapping", "Chronologis"),
    "synthesis_plan.json": ("synthesis_planning", "ScriptoriumArchitect"),
    "book_outline.json": ("synthesis_planning", "ScriptoriumArchitect"),
    "chapter_plan.json": ("synthesis_planning", "ScriptoriumArchitect"),
    "reconstruction_ru.md": ("draft_reconstruction", "ScriptoriumDaemon"),
    "coverage_report.md": ("draft_reconstruction", "ScriptoriumDaemon"),
    "manuscript_ru.md": ("draft_reconstruction", "ScriptoriumDaemon"),
    "manuscript.fb2": ("draft_reconstruction", "ScriptoriumDaemon"),
}

REVISION_DEPENDENCIES = {
    "corpus_ingestion": [
        ("source_discovery", "Lexmechanic"),
        ("source_acquisition", "AuspexBrowser"),
        ("fact_extraction", "NoosphericExtractor"),
        ("structure_mapping", "Chronologis"),
        ("synthesis_planning", "ScriptoriumArchitect"),
        ("draft_reconstruction", "ScriptoriumDaemon"),
    ],
    "source_discovery": [
        ("source_acquisition", "AuspexBrowser"),
        ("fact_extraction", "NoosphericExtractor"),
        ("structure_mapping", "Chronologis"),
        ("synthesis_planning", "ScriptoriumArchitect"),
        ("draft_reconstruction", "ScriptoriumDaemon"),
    ],
    "source_acquisition": [
        ("source_rendering", "OcularisRenderium"),
        ("fact_extraction", "NoosphericExtractor"),
        ("structure_mapping", "Chronologis"),
        ("synthesis_planning", "ScriptoriumArchitect"),
        ("draft_reconstruction", "ScriptoriumDaemon"),
    ],
    "source_rendering": [
        ("fact_extraction", "NoosphericExtractor"),
        ("structure_mapping", "Chronologis"),
        ("synthesis_planning", "ScriptoriumArchitect"),
        ("draft_reconstruction", "ScriptoriumDaemon"),
    ],
    "fact_extraction": [
        ("structure_mapping", "Chronologis"),
        ("synthesis_planning", "ScriptoriumArchitect"),
        ("draft_reconstruction", "ScriptoriumDaemon"),
    ],
    "timeline": [
        ("synthesis_planning", "ScriptoriumArchitect"),
        ("draft_reconstruction", "ScriptoriumDaemon"),
    ],
    "structure_mapping": [
        ("synthesis_planning", "ScriptoriumArchitect"),
        ("draft_reconstruction", "ScriptoriumDaemon"),
    ],
    "synthesis_planning": [
        ("draft_reconstruction", "ScriptoriumDaemon"),
    ],
}

REVISION_STEP_ORDER = [
    "corpus_ingestion",
    "source_discovery",
    "source_acquisition",
    "source_rendering",
    "fact_extraction",
    "structure_mapping",
    "timeline",
    "synthesis_planning",
    "draft_reconstruction",
    "critic_review",
]


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


EVENT_PLAYBOOKS = load_event_playbooks()


PRIMARY_SOURCE_MARKERS = {
    "official",
    "primary",
    "publication",
    "documentation",
    "standard",
    "spec",
    "paper",
    "journal",
    "manual",
    "book",
    "codex",
    "novel",
    "local_primary",
    "official_primary_narrative",
    "local_primary_candidate",
}

SECONDARY_SOURCE_MARKERS = {
    "secondary",
    "wiki",
    "reference",
    "summary",
    "review",
    "article",
    "analysis",
    "crosscheck",
    "community",
    "institutional_reference",
    "general_reference",
    "secondary_wiki",
    "curated_wiki",
    "community_wiki",
}


def source_descriptor(source: dict[str, Any]) -> str:
    fields = [
        source.get("source_class"),
        source.get("source_type"),
        source.get("type"),
        source.get("discovery_method"),
        source.get("expected_use"),
    ]
    return " ".join(str(item).lower() for item in fields if item)


def descriptor_has_marker(descriptor: str, markers: set[str]) -> bool:
    return any(marker in descriptor for marker in markers)


def source_mix_metrics(source_map: dict[str, Any]) -> dict[str, Any]:
    sources = [source for source in source_map.get("sources", []) if isinstance(source, dict)]
    source_coverage = source_map.get("source_coverage") if isinstance(source_map.get("source_coverage"), dict) else {}
    source_types = source_coverage.get("source_types") if isinstance(source_coverage.get("source_types"), list) else []
    descriptors = [source_descriptor(source) for source in sources]
    descriptors.extend(str(item).lower() for item in source_types if item)
    primary_count = sum(1 for descriptor in descriptors if descriptor_has_marker(descriptor, PRIMARY_SOURCE_MARKERS))
    secondary_count = sum(1 for descriptor in descriptors if descriptor_has_marker(descriptor, SECONDARY_SOURCE_MARKERS))
    has_primary = bool(source_coverage.get("has_primary_or_publication") or source_coverage.get("has_official") or primary_count)
    has_secondary = bool(source_coverage.get("has_secondary_crosscheck") or secondary_count)
    return {
        "source_count": len(sources),
        "primary_or_official_count": primary_count,
        "secondary_or_crosscheck_count": secondary_count,
        "has_primary_or_official": has_primary,
        "has_secondary_or_crosscheck": has_secondary,
        "source_types": source_types,
    }


def playbook_matches(playbook: dict[str, Any], source_map: dict[str, Any], notes: dict[str, Any], timeline: dict[str, Any]) -> bool:
    source_titles = [
        str(source.get("title") or "")
        for source in source_map.get("sources", [])
        if isinstance(source, dict) and source.get("title")
    ]
    note_ids = [
        str(item.get("event_id") or "")
        for item in notes.get("events", [])
        if isinstance(item, dict) and item.get("event_id")
    ]
    timeline_ids = [
        str(item.get("event_id") or "")
        for item in timeline.get("timeline", [])
        if isinstance(item, dict) and item.get("event_id")
    ]
    haystack = " ".join(
        [
            str(source_map.get("topic") or ""),
            str(source_map.get("original_goal") or ""),
            *source_titles,
            *note_ids,
            *timeline_ids,
        ]
    ).lower()
    terms = [str(term).lower() for term in playbook.get("match_terms", []) if term]
    return any(term in haystack for term in terms)


def required_review_events(source_map: dict[str, Any], notes: dict[str, Any], timeline: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for playbook in EVENT_PLAYBOOKS:
        if not playbook_matches(playbook, source_map, notes, timeline):
            continue
        for event in playbook.get("events", []):
            if not isinstance(event, dict) or not event.get("required_for_review"):
                continue
            event_id = str(event.get("event_id") or "")
            if not event_id or event_id in seen:
                continue
            seen.add(event_id)
            events.append(event)
    return events


def required_event_label(event: dict[str, Any]) -> str:
    return str(event.get("review_label") or event.get("summary") or event.get("event_id") or "required event")


def required_event_markers(event: dict[str, Any]) -> list[str]:
    markers = event.get("draft_markers") if isinstance(event.get("draft_markers"), list) else []
    if not markers:
        markers = event.get("evidence_markers") if isinstance(event.get("evidence_markers"), list) else []
    return [str(marker) for marker in markers if str(marker).strip()]


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


def primary_source_title_tokens(source_map: dict[str, Any]) -> list[set[str]]:
    tokens: list[set[str]] = []
    for source in source_map.get("sources", []) if isinstance(source_map.get("sources"), list) else []:
        if not isinstance(source, dict):
            continue
        source_class = str(source.get("source_class") or source.get("type") or "").lower()
        source_kind = str(source.get("type") or "").lower()
        if (
            "primary" not in source_class
            and source_kind not in {"novel", "short_story", "book", "codex", "campaign_book"}
            and not str(source.get("local_path") or "").strip()
        ):
            continue
        token_text = " ".join(
            [
                str(source.get("title") or ""),
                str(source.get("local_path") or ""),
                str(source.get("corpus_relative_path") or ""),
            ]
        )
        source_tokens = relevance_tokens(token_text)
        if source_tokens:
            tokens.append(source_tokens)
    return tokens


def primary_evidence_source_count(source_map: dict[str, Any], notes: dict[str, Any]) -> int:
    primary_tokens = primary_source_title_tokens(source_map)
    if not primary_tokens:
        return 0
    sources: set[str] = set()
    for event in notes.get("events", []) if isinstance(notes.get("events"), list) else []:
        if not isinstance(event, dict):
            continue
        evidence = event.get("evidence_snapshots") if isinstance(event.get("evidence_snapshots"), list) else []
        for item in evidence:
            if not isinstance(item, dict):
                continue
            source_title = str(item.get("source_title") or "")
            source_tokens = relevance_tokens(source_title)
            if not source_tokens:
                continue
            required = min(2, len(source_tokens))
            if any(len(source_tokens & candidate) >= required for candidate in primary_tokens):
                sources.add(source_title)
    return len(sources)


def inaccessible_primary_titles(source_map: dict[str, Any]) -> list[str]:
    corpus_requirements = source_map.get("corpus_requirements") if isinstance(source_map.get("corpus_requirements"), dict) else {}
    missing = corpus_requirements.get("missing_primary_texts") if isinstance(corpus_requirements.get("missing_primary_texts"), list) else []
    if missing:
        return [str(item.get("title") or "untitled primary source") for item in missing if isinstance(item, dict)]
    local_primary_tokens = [
        relevance_tokens(
            " ".join(
                [
                    str(source.get("title") or ""),
                    str(source.get("local_path") or ""),
                    str(source.get("corpus_relative_path") or ""),
                ]
            )
        )
        for source in source_map.get("sources", [])
        if isinstance(source, dict) and str(source.get("local_path") or "").strip()
    ]
    titles: list[str] = []
    for source in source_map.get("sources", []):
        if not isinstance(source, dict):
            continue
        source_class = str(source.get("source_class") or source.get("type") or "").lower()
        if "primary" not in source_class and str(source.get("type") or "").lower() not in {"novel", "short_story", "book"}:
            continue
        if not str(source.get("url") or "").strip() and not str(source.get("local_path") or "").strip():
            source_tokens = relevance_tokens(str(source.get("title") or ""))
            if source_tokens and any(len(source_tokens & local_tokens) >= min(2, len(source_tokens)) for local_tokens in local_primary_tokens):
                continue
            titles.append(str(source.get("title") or "untitled primary source"))
    return titles


def relevance_tokens(text: str) -> set[str]:
    stopwords = {"the", "and", "for", "with", "warhammer", "black", "library", "ebook", "novel"}
    return {token for token in "".join(char.lower() if char.isalnum() else " " for char in text).split() if len(token) > 2 and token not in stopwords}


def evidence_support_chars(note: dict[str, Any]) -> int:
    total = 0
    evidence = note.get("evidence_snapshots") if isinstance(note.get("evidence_snapshots"), list) else []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        for field_name in ("excerpt", "matched_markers", "text_excerpt"):
            value = item.get(field_name)
            if isinstance(value, list):
                total += sum(len(str(part)) for part in value if str(part).strip())
            elif str(value or "").strip():
                total += len(str(value))
    return total


def marker_context_chars(text: str, markers: list[str], radius: int = 120) -> int:
    if not markers:
        return 0
    lowered = text.lower()
    intervals: list[tuple[int, int]] = []
    for marker in markers:
        needle = marker.lower()
        position = lowered.find(needle)
        if position < 0:
            continue
        intervals.append((max(0, position - radius), min(len(text), position + len(marker) + radius)))
    if not intervals:
        return 0
    intervals.sort()
    merged: list[tuple[int, int]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return sum(end - start for start, end in merged)


def comprehensive_required_event_metrics(
    depth_profile: dict[str, Any],
    notes: dict[str, Any],
    reconstruction: str,
    required_events: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if not required_events:
        return [], {
            "required_event_count": 0,
            "required_events_with_draft_coverage": 0,
            "required_events_with_evidence_support": 0,
            "under_detailed_required_events": [],
        }
    min_detail_chars = int(depth_profile.get("min_required_event_detail_chars") or 180)
    min_evidence_chars = int(depth_profile.get("min_required_event_evidence_chars") or 24)
    note_by_event_id = {
        str(item.get("event_id")): item
        for item in notes.get("events", [])
        if isinstance(item, dict) and item.get("event_id")
    }
    findings: list[dict[str, str]] = []
    draft_covered = 0
    evidence_supported = 0
    under_detailed: list[str] = []
    weak_evidence: list[str] = []
    for event in required_events:
        event_id = str(event.get("event_id") or "")
        label = required_event_label(event)
        markers = required_event_markers(event)
        detail_chars = marker_context_chars(reconstruction, markers)
        if markers and text_contains_markers(reconstruction, markers):
            draft_covered += 1
        if detail_chars < min_detail_chars:
            under_detailed.append(label)
            findings.append(
                {
                    "severity": "blocker",
                    "message": f"Required event is under-detailed in final draft: {label} ({detail_chars}/{min_detail_chars} chars of local context).",
                }
            )
        note = note_by_event_id.get(event_id, {})
        support_chars = evidence_support_chars(note) if isinstance(note, dict) else 0
        if support_chars >= min_evidence_chars:
            evidence_supported += 1
        else:
            weak_evidence.append(label)
            findings.append(
                {
                    "severity": "blocker",
                    "message": f"Required event lacks substantive evidence support: {label} ({support_chars}/{min_evidence_chars} chars).",
                }
            )
    return findings, {
        "required_event_count": len(required_events),
        "required_events_with_draft_coverage": draft_covered,
        "required_events_with_evidence_support": evidence_supported,
        "min_required_event_detail_chars": min_detail_chars,
        "min_required_event_evidence_chars": min_evidence_chars,
        "under_detailed_required_events": under_detailed,
        "weak_evidence_required_events": weak_evidence,
    }


def comprehensive_depth_findings(
    source_map: dict[str, Any],
    notes: dict[str, Any],
    reconstruction: str,
    required_events: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    depth_profile = source_map.get("depth_profile") if isinstance(source_map.get("depth_profile"), dict) else {}
    if depth_profile.get("mode") != "comprehensive":
        return [], {"mode": str(depth_profile.get("mode") or "standard"), "passed": True}
    source_coverage = source_map.get("source_coverage") if isinstance(source_map.get("source_coverage"), dict) else {}
    source_count = int(source_coverage.get("source_count") or len(source_map.get("sources", [])))
    live_candidate_count = int(source_coverage.get("live_candidate_count") or 0)
    evidence_source_count = direct_evidence_source_count(notes)
    primary_evidence_count = primary_evidence_source_count(source_map, notes)
    draft_chars = len(reconstruction)
    min_source_count = int(depth_profile.get("min_source_count") or 0)
    min_live_candidate_count = int(depth_profile.get("min_live_candidate_count") or 0)
    min_direct_evidence_sources = int(depth_profile.get("min_direct_evidence_sources") or 0)
    min_primary_evidence_sources = int(depth_profile.get("min_primary_evidence_sources") or 0)
    min_draft_chars = int(depth_profile.get("min_draft_chars") or 0)
    min_direct_event_count = int(depth_profile.get("min_direct_event_count") or 0)
    missing_primary = inaccessible_primary_titles(source_map)
    findings: list[dict[str, str]] = []
    direct_event_count = len(notes.get("events", [])) if isinstance(notes.get("events"), list) else 0
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
    if primary_evidence_count < min_primary_evidence_sources:
        findings.append(
            {
                "severity": "blocker",
                "message": f"Comprehensive task has too few primary-evidence sources: {primary_evidence_count}/{min_primary_evidence_sources}.",
            }
        )
    if direct_event_count < min_direct_event_count:
        findings.append(
            {
                "severity": "blocker",
                "message": f"Comprehensive task has too few extracted direct events: {direct_event_count}/{min_direct_event_count}.",
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
    required_event_findings, required_event_metrics = comprehensive_required_event_metrics(
        depth_profile,
        notes,
        reconstruction,
        required_events,
    )
    findings.extend(required_event_findings)
    metrics = {
        "mode": "comprehensive",
        "passed": not findings,
        "source_count": source_count,
        "min_source_count": min_source_count,
        "live_candidate_count": live_candidate_count,
        "min_live_candidate_count": min_live_candidate_count,
        "direct_evidence_source_count": evidence_source_count,
        "min_direct_evidence_sources": min_direct_evidence_sources,
        "primary_evidence_source_count": primary_evidence_count,
        "min_primary_evidence_sources": min_primary_evidence_sources,
        "direct_event_count": direct_event_count,
        "min_direct_event_count": min_direct_event_count,
        "draft_chars": draft_chars,
        "min_draft_chars": min_draft_chars,
        "inaccessible_primary_count": len(missing_primary),
        "corpus_requirements": source_map.get("corpus_requirements", {}),
        "required_event_coverage": required_event_metrics,
    }
    return findings, metrics


def mode_gate_thresholds(output_mode: str) -> dict[str, Any]:
    defaults = {
        "min_sources": 4,
        "min_confirmed_claims": 3,
        "min_evidence_coverage_percent": 60,
        "max_unresolved_contradictions": 2,
        "min_draft_chars": 3500,
    }
    by_mode = {
        "short_answer": {"min_sources": 1, "min_confirmed_claims": 1, "min_evidence_coverage_percent": 50, "max_unresolved_contradictions": 1, "min_draft_chars": 400},
        "research_report": defaults,
        "comparative_review": {"min_sources": 4, "min_confirmed_claims": 4, "min_evidence_coverage_percent": 65, "max_unresolved_contradictions": 2, "min_draft_chars": 4500},
        "investigative_report": {"min_sources": 5, "min_confirmed_claims": 4, "min_evidence_coverage_percent": 70, "max_unresolved_contradictions": 4, "min_draft_chars": 5500},
        "event_reconstruction": {"min_sources": 4, "min_confirmed_claims": 4, "min_evidence_coverage_percent": 70, "max_unresolved_contradictions": 3, "min_draft_chars": 7000},
        "longform_article": {"min_sources": 6, "min_confirmed_claims": 6, "min_evidence_coverage_percent": 70, "max_unresolved_contradictions": 3, "min_draft_chars": 10000},
        "book_manuscript": {"min_sources": 6, "min_confirmed_claims": 6, "min_evidence_coverage_percent": 75, "max_unresolved_contradictions": 4, "min_draft_chars": 12000},
        "book_manuscript_with_timeline": {"min_sources": 6, "min_confirmed_claims": 6, "min_evidence_coverage_percent": 75, "max_unresolved_contradictions": 4, "min_draft_chars": 12000},
    }
    thresholds = dict(defaults)
    thresholds.update(by_mode.get(output_mode, {}))
    return thresholds


def claim_has_evidence(claim: dict[str, Any]) -> bool:
    for field_name in ("source_refs", "evidence_refs"):
        values = claim.get(field_name)
        if isinstance(values, list) and any(str(item).strip() for item in values):
            return True
    return False


def mode_quality_gates(
    output_mode: str,
    research_corpus: dict[str, Any],
    structure_map: dict[str, Any],
    reconstruction: str,
    synthesis_plan: dict[str, Any],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if not synthesis_plan:
        return [], {"applies": False, "passed": True, "output_mode": output_mode}
    claims = [item for item in research_corpus.get("claims", []) if isinstance(item, dict)]
    confirmed_claims = [claim for claim in claims if claim_has_evidence(claim)]
    source_count = len(research_corpus.get("sources", []) if isinstance(research_corpus.get("sources"), list) else [])
    evidence_coverage_percent = int(round((len(confirmed_claims) / max(1, len(claims))) * 100))
    corpus_contradictions = research_corpus.get("contradictions") if isinstance(research_corpus.get("contradictions"), list) else []
    structure_contradictions = structure_map.get("contradictions") if isinstance(structure_map.get("contradictions"), list) else []
    unresolved_contradictions = [
        item
        for item in [*corpus_contradictions, *structure_contradictions]
        if isinstance(item, dict) and str(item.get("status") or "unresolved") != "resolved"
    ]
    thresholds = mode_gate_thresholds(output_mode)
    checks = {
        "min_sources": source_count >= thresholds["min_sources"],
        "min_confirmed_claims": len(confirmed_claims) >= thresholds["min_confirmed_claims"],
        "evidence_coverage_percent": evidence_coverage_percent >= thresholds["min_evidence_coverage_percent"],
        "unresolved_contradiction_count": len(unresolved_contradictions) <= thresholds["max_unresolved_contradictions"],
        "draft_length_by_mode": len(reconstruction) >= thresholds["min_draft_chars"],
        "critic_approval_required": True,
    }
    findings: list[dict[str, str]] = []
    if not checks["min_sources"]:
        findings.append({"severity": "blocker", "message": f"Quality gate failed for {output_mode}: sources {source_count}/{thresholds['min_sources']}."})
    if not checks["min_confirmed_claims"]:
        findings.append({"severity": "blocker", "message": f"Quality gate failed for {output_mode}: confirmed claims {len(confirmed_claims)}/{thresholds['min_confirmed_claims']}."})
    if not checks["evidence_coverage_percent"]:
        findings.append({"severity": "blocker", "message": f"Quality gate failed for {output_mode}: evidence coverage {evidence_coverage_percent}%/{thresholds['min_evidence_coverage_percent']}%."})
    if not checks["unresolved_contradiction_count"]:
        findings.append({"severity": "blocker", "message": f"Quality gate failed for {output_mode}: unresolved contradictions {len(unresolved_contradictions)}/{thresholds['max_unresolved_contradictions']}."})
    if not checks["draft_length_by_mode"]:
        findings.append({"severity": "blocker", "message": f"Quality gate failed for {output_mode}: draft length {len(reconstruction)}/{thresholds['min_draft_chars']} chars."})
    return findings, {
        "applies": True,
        "passed": not findings,
        "output_mode": output_mode,
        "thresholds": thresholds,
        "checks": checks,
        "source_count": source_count,
        "claim_count": len(claims),
        "confirmed_claim_count": len(confirmed_claims),
        "evidence_coverage_percent": evidence_coverage_percent,
        "unresolved_contradiction_count": len(unresolved_contradictions),
        "draft_chars": len(reconstruction),
    }


def synthesis_structure_findings(output_mode: str, reconstruction: str, synthesis_plan: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if not synthesis_plan or output_mode in {"event_reconstruction", "book_manuscript", "book_manuscript_with_timeline"}:
        return [], {"applies": False}
    sections = synthesis_plan.get("sections") if isinstance(synthesis_plan.get("sections"), list) else []
    missing_sections: list[str] = []
    missing_claim_traces: list[str] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "").strip()
        section_id = str(section.get("section_id") or "").strip()
        if title and f"## {title}" not in reconstruction:
            missing_sections.append(section_id or title)
        if not section.get("requires_evidence"):
            continue
        for claim_ref in section.get("required_claim_refs", []) if isinstance(section.get("required_claim_refs"), list) else []:
            marker = f"Evidence trace: {claim_ref}"
            if marker not in reconstruction:
                missing_claim_traces.append(str(claim_ref))
    findings: list[dict[str, str]] = []
    if missing_sections:
        findings.append({"severity": "blocker", "message": f"Draft misses synthesis section(s): {', '.join(missing_sections[:8])}."})
    if missing_claim_traces:
        findings.append({"severity": "blocker", "message": f"Draft misses required evidence trace claim(s): {', '.join(missing_claim_traces[:12])}."})
    return findings, {
        "applies": True,
        "passed": not findings,
        "checked_section_count": len(sections),
        "missing_sections": missing_sections,
        "missing_claim_traces": missing_claim_traces,
    }


def book_artifact_findings(workspace_root: Path, critic_path: str, required_artifacts: list[Any]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    findings: list[dict[str, str]] = []
    chapter_paths = sorted(str(item) for item in required_artifacts if "/chapters/" in str(item) and str(item).endswith(".md"))
    chapter_texts: dict[str, str] = {}
    for chapter_path in chapter_paths:
        if not artifact_exists(workspace_root, chapter_path):
            findings.append({"severity": "blocker", "message": f"Book chapter missing required artifact: {chapter_path}"})
            continue
        text = read_text(workspace_root, chapter_path)
        chapter_texts[chapter_path] = text
        if "Evidence trace:" not in text:
            findings.append({"severity": "blocker", "message": f"Book chapter lacks evidence trace: {chapter_path}"})
        if "не развернута" in text or "нет подтвержденных claims" in text:
            findings.append({"severity": "blocker", "message": f"Book chapter was blocked for missing evidence: {chapter_path}"})
    normalized_bodies = [" ".join(text.split()) for text in chapter_texts.values()]
    repeated_count = len(normalized_bodies) - len(set(normalized_bodies))
    if repeated_count > 0:
        findings.append({"severity": "blocker", "message": f"Book chapters are duplicated instead of chapter-specific drafts: {repeated_count} duplicate(s)."})
    manuscript_path = sibling_artifact(critic_path, "manuscript_ru.md")
    fb2_path = sibling_artifact(critic_path, "manuscript.fb2")
    continuity_path = sibling_artifact(critic_path, "continuity_report.json")
    editor_path = sibling_artifact(critic_path, "editor_report.json")
    if artifact_exists(workspace_root, manuscript_path):
        manuscript = read_text(workspace_root, manuscript_path)
        for chapter_path, text in chapter_texts.items():
            first_heading = next((line for line in text.splitlines() if line.startswith("# ")), "")
            if first_heading and first_heading not in manuscript:
                findings.append({"severity": "blocker", "message": f"Book manuscript omits chapter heading from {chapter_path}."})
    if artifact_exists(workspace_root, fb2_path):
        fb2 = read_text(workspace_root, fb2_path)
        if chapter_paths and fb2.count("<section>") < len(chapter_paths):
            findings.append({"severity": "blocker", "message": f"Book FB2 lost chapter section boundaries: {fb2.count('<section>')}/{len(chapter_paths)}."})
    continuity = load_optional_json(workspace_root, continuity_path)
    editor = load_optional_json(workspace_root, editor_path)
    chapter_plan_path = sibling_artifact(critic_path, "chapter_plan.json")
    chapter_plan = load_optional_json(workspace_root, chapter_plan_path)
    missing_planned_claim_refs: dict[str, list[str]] = {}
    for chapter in chapter_plan.get("chapters", []) if isinstance(chapter_plan.get("chapters"), list) else []:
        if not isinstance(chapter, dict):
            continue
        chapter_id = str(chapter.get("chapter_id") or "")
        planned_refs = [str(ref) for ref in chapter.get("required_claim_refs", []) if str(ref).strip()] if isinstance(chapter.get("required_claim_refs"), list) else []
        if not planned_refs:
            findings.append({"severity": "blocker", "message": f"Book chapter plan has ungrounded chapter with no required claims: {chapter_id or 'unknown chapter'}"})
            continue
        chapter_text = next((text for path, text in chapter_texts.items() if chapter_id and chapter_id in path), "")
        missing_refs = [ref for ref in planned_refs if f"Evidence trace: {ref}" not in chapter_text]
        if missing_refs:
            missing_planned_claim_refs[chapter_id or "unknown"] = missing_refs
    for chapter_id, refs in missing_planned_claim_refs.items():
        findings.append({"severity": "blocker", "message": f"Book chapter misses planned evidence claim(s): {chapter_id}: {', '.join(refs[:8])}"})
    if continuity and continuity.get("status") != "completed":
        findings.append({"severity": "blocker", "message": f"Book continuity report requires revision: {continuity.get('status')}"})
    if editor and editor.get("status") != "completed":
        findings.append({"severity": "blocker", "message": f"Book editor report requires revision: {editor.get('status')}"})
    metrics = {
        "chapter_count": len(chapter_paths),
        "chapters_with_evidence_trace": sum(1 for text in chapter_texts.values() if "Evidence trace:" in text),
        "duplicated_chapter_count": repeated_count,
        "continuity_status": continuity.get("status", "") if continuity else "",
        "editor_status": editor.get("status", "") if editor else "",
        "missing_planned_claim_refs": missing_planned_claim_refs,
    }
    return findings, metrics


def text_contains_markers(text: str, markers: list[str]) -> bool:
    lowered = text.lower()
    return all(marker.lower() in lowered for marker in markers)


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
            add_revision_step(steps, "structure_mapping", "Chronologis", message, "critic_finding")
            add_revision_step(steps, "synthesis_planning", "ScriptoriumArchitect", message, "critic_finding")
            add_revision_step(steps, "draft_reconstruction", "ScriptoriumDaemon", message, "critic_finding")
        elif "timeline event lacks extracted direct-event note" in lowered:
            add_revision_step(steps, "fact_extraction", "NoosphericExtractor", message, "critic_finding")
            add_revision_step(steps, "structure_mapping", "Chronologis", message, "critic_finding")
            add_revision_step(steps, "synthesis_planning", "ScriptoriumArchitect", message, "critic_finding")
            add_revision_step(steps, "draft_reconstruction", "ScriptoriumDaemon", message, "critic_finding")
        elif (
            "draft does not visibly cover" in lowered
            or "coverage gaps clearly" in lowered
            or "under-detailed in final draft" in lowered
            or "draft misses synthesis section" in lowered
            or "draft misses required evidence trace" in lowered
        ):
            add_revision_step(steps, "draft_reconstruction", "ScriptoriumDaemon", message, "critic_finding")
        elif "book chapter" in lowered or "book manuscript" in lowered or "book fb2" in lowered or "book continuity" in lowered or "book editor" in lowered:
            add_revision_step(steps, "draft_reconstruction", "ScriptoriumDaemon", message, "critic_finding")
        elif "lacks fetched source evidence" in lowered or "lacks substantive evidence support" in lowered:
            add_revision_step(steps, "source_acquisition", "AuspexBrowser", message, "critic_finding")
            add_revision_step(steps, "source_rendering", "OcularisRenderium", message, "critic_finding")
            add_revision_step(steps, "fact_extraction", "NoosphericExtractor", message, "critic_finding")
            add_revision_step(steps, "draft_reconstruction", "ScriptoriumDaemon", message, "critic_finding")
        elif "source discovery did not find" in lowered:
            add_revision_step(steps, "source_discovery", "Lexmechanic", message, "critic_finding")
            add_revision_step(steps, "source_acquisition", "AuspexBrowser", message, "critic_finding")
            add_revision_step(steps, "source_rendering", "OcularisRenderium", message, "critic_finding")
        elif "comprehensive task has too few mapped sources" in lowered or "comprehensive task has too few live-discovered" in lowered:
            add_revision_step(steps, "source_discovery", "Lexmechanic", message, "critic_finding")
            add_revision_step(steps, "source_acquisition", "AuspexBrowser", message, "critic_finding")
        elif "comprehensive task has too few direct-evidence sources" in lowered:
            add_revision_step(steps, "source_discovery", "Lexmechanic", message, "critic_finding")
            add_revision_step(steps, "source_acquisition", "AuspexBrowser", message, "critic_finding")
            add_revision_step(steps, "source_rendering", "OcularisRenderium", message, "critic_finding")
            add_revision_step(steps, "fact_extraction", "NoosphericExtractor", message, "critic_finding")
        elif "comprehensive task has too few primary-evidence sources" in lowered:
            add_revision_step(steps, "source_discovery", "Lexmechanic", message, "critic_finding")
            add_revision_step(steps, "source_acquisition", "AuspexBrowser", message, "critic_finding")
            add_revision_step(steps, "source_rendering", "OcularisRenderium", message, "critic_finding")
            add_revision_step(steps, "fact_extraction", "NoosphericExtractor", message, "critic_finding")
        elif "comprehensive task lacks accessible primary text" in lowered:
            add_revision_step(steps, "corpus_ingestion", "CorpusIngestor", message, "critic_finding")
            add_revision_step(steps, "source_discovery", "Lexmechanic", message, "critic_finding")
            add_revision_step(steps, "source_acquisition", "AuspexBrowser", message, "critic_finding")
            add_revision_step(steps, "source_rendering", "OcularisRenderium", message, "critic_finding")
            add_revision_step(steps, "fact_extraction", "NoosphericExtractor", message, "critic_finding")
        elif "comprehensive task has too few extracted direct events" in lowered:
            add_revision_step(steps, "fact_extraction", "NoosphericExtractor", message, "critic_finding")
            add_revision_step(steps, "structure_mapping", "Chronologis", message, "critic_finding")
            add_revision_step(steps, "synthesis_planning", "ScriptoriumArchitect", message, "critic_finding")
            add_revision_step(steps, "draft_reconstruction", "ScriptoriumDaemon", message, "critic_finding")
        elif "comprehensive draft is too short" in lowered:
            add_revision_step(steps, "fact_extraction", "NoosphericExtractor", message, "critic_finding")
            add_revision_step(steps, "structure_mapping", "Chronologis", message, "critic_finding")
            add_revision_step(steps, "synthesis_planning", "ScriptoriumArchitect", message, "critic_finding")
            add_revision_step(steps, "draft_reconstruction", "ScriptoriumDaemon", message, "critic_finding")
        elif "source set is not extraction-ready" in lowered or "source coverage is not extraction-ready" in lowered:
            add_revision_step(steps, "source_discovery", "Lexmechanic", message, "critic_finding")
            add_revision_step(steps, "source_acquisition", "AuspexBrowser", message, "critic_finding")
            add_revision_step(steps, "source_rendering", "OcularisRenderium", message, "critic_finding")
        else:
            add_revision_step(steps, "critic_review", "ReductorVerifier", message, "critic_finding")
    expand_revision_dependencies(steps)
    steps = sort_revision_steps(steps)
    return {
        "required": bool(steps),
        "steps": steps,
    }


def normalize_revision_plan_for_request(revision_plan: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    expectations = request.get("quality_expectations") if isinstance(request.get("quality_expectations"), dict) else {}
    revision_policy = expectations.get("revision_policy") if isinstance(expectations.get("revision_policy"), dict) else {}
    allowed_steps = {str(step_id) for step_id in revision_policy.get("allowed_steps", []) if isinstance(step_id, str)}
    if "timeline" in allowed_steps or "structure_mapping" not in allowed_steps:
        return revision_plan
    normalized_steps: list[dict[str, Any]] = []
    for step in revision_plan.get("steps", []) if isinstance(revision_plan.get("steps"), list) else []:
        if not isinstance(step, dict):
            continue
        updated = dict(step)
        if updated.get("step_id") == "timeline" and updated.get("worker") == "Chronologis":
            updated["step_id"] = "structure_mapping"
        existing = next((item for item in normalized_steps if item.get("step_id") == updated.get("step_id") and item.get("worker") == updated.get("worker")), None)
        if existing:
            existing_reasons = [part.strip() for part in str(existing.get("reason") or "").split(" | ") if part.strip()]
            reason = str(updated.get("reason") or "")
            if reason and reason not in existing_reasons:
                existing_reasons.append(reason)
                existing["reason"] = " | ".join(existing_reasons[:6])
            continue
        normalized_steps.append(updated)
    return {"required": bool(normalized_steps) or bool(revision_plan.get("required")), "steps": sort_revision_steps(normalized_steps)}


def quality_expectation_summary(request: dict[str, Any]) -> dict[str, Any]:
    expectations = request.get("quality_expectations") if isinstance(request.get("quality_expectations"), dict) else {}
    step_quality = expectations.get("step_quality") if isinstance(expectations.get("step_quality"), dict) else {}
    final_review = expectations.get("final_review") if isinstance(expectations.get("final_review"), dict) else {}
    revision_policy = expectations.get("revision_policy") if isinstance(expectations.get("revision_policy"), dict) else {}
    research_intent = expectations.get("research_intent") if isinstance(expectations.get("research_intent"), dict) else {}
    return {
        "provided": bool(expectations),
        "step_id": str(step_quality.get("step_id") or ""),
        "worker": str(step_quality.get("worker") or ""),
        "check_count": len(step_quality.get("checks") if isinstance(step_quality.get("checks"), list) else []),
        "blocker_count": len(step_quality.get("blockers") if isinstance(step_quality.get("blockers"), list) else []),
        "revision_targets": step_quality.get("revision_targets", []) if isinstance(step_quality.get("revision_targets"), list) else [],
        "final_review": final_review,
        "revision_policy": revision_policy,
        "research_intent": research_intent,
    }


def research_intent_from_request(request: dict[str, Any]) -> dict[str, Any]:
    return research_intent_from_worker_request(request)


def load_optional_json(workspace_root: Path, path: str) -> dict[str, Any]:
    host_path = sandbox_path(workspace_root, path)
    if not host_path.exists():
        return {}
    payload = json.loads(host_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


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


def model_review_payload(
    request: dict[str, Any],
    source_map: dict[str, Any],
    notes: dict[str, Any],
    timeline: dict[str, Any],
    reconstruction: str,
    coverage: str,
    findings: list[dict[str, str]],
    warnings: list[dict[str, str]],
    comprehensive_metrics: dict[str, Any],
    quality_gates: dict[str, Any],
) -> dict[str, Any]:
    return {
        "task_id": request.get("task_id"),
        "step": request.get("step"),
        "contract": request.get("contract") if isinstance(request.get("contract"), dict) else {},
        "quality_expectations": quality_expectation_summary(request),
        "hard_findings": findings,
        "hard_warnings": warnings,
        "metrics": {
            "source_count": len(source_map.get("sources", [])) if isinstance(source_map.get("sources"), list) else 0,
            "event_note_count": len(notes.get("events", [])) if isinstance(notes.get("events"), list) else 0,
            "timeline_count": len(timeline.get("timeline", [])) if isinstance(timeline.get("timeline"), list) else 0,
            "draft_chars": len(reconstruction),
            "comprehensive_depth": comprehensive_metrics,
            "quality_gates": quality_gates,
        },
        "source_map": source_map,
        "direct_event_notes": notes,
        "timeline": timeline,
        "reconstruction_preview": reconstruction[:26000],
        "coverage_report_preview": coverage[:14000],
    }


def model_review_findings(decision: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    if not decision.get("ok"):
        return [], [
            {
                "severity": "warning",
                "message": f"Model critic unavailable: {decision.get('error') or decision.get('status') or 'unknown error'}",
            }
        ]
    parsed = parsed_model_content(decision)
    status = str(parsed.get("status") or "").lower()
    model_is_blocking = status in {"blocked", "block", "needs_revision", "failed", "fail"}
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    blocker_target = blockers if model_is_blocking else warnings
    blocker_severity = "blocker" if model_is_blocking else "warning"
    for field_name, severity, target in (
        ("blockers", blocker_severity, blocker_target),
        ("findings", blocker_severity, blocker_target),
        ("warnings", "warning", warnings),
    ):
        values = parsed.get(field_name)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict):
                message = str(item.get("message") or item.get("note") or item.get("summary") or "").strip()
                item_severity = str(item.get("severity") or severity)
            else:
                message = str(item).strip()
                item_severity = severity
            if message:
                target.append({"severity": item_severity, "message": f"Model critic: {message}"})
    if model_is_blocking and not blockers:
        reason = str(parsed.get("reason") or parsed.get("summary") or "model critic requested revision").strip()
        blockers.append({"severity": "blocker", "message": f"Model critic: {reason}"})
    return blockers, warnings


def review_artifacts(
    workspace_root: Path,
    critic_path: str,
    request: dict[str, Any],
    request_guidance: GuidanceFn,
) -> dict[str, Any]:
    reconstruction_path = sibling_artifact(critic_path, "reconstruction_ru.md")
    coverage_path = sibling_artifact(critic_path, "coverage_report.md")
    source_path = sibling_artifact(critic_path, "source_map.json")
    source_snapshots_path = sibling_artifact(critic_path, "source_snapshots.json")
    rendered_snapshots_path = sibling_artifact(critic_path, "rendered_snapshots.json")
    notes_path = sibling_artifact(critic_path, "direct_event_notes.json")
    timeline_path = sibling_artifact(critic_path, "timeline.json")
    structure_path = sibling_artifact(critic_path, "structure_map.json")
    research_corpus_path = sibling_artifact(critic_path, "research_corpus.json")
    synthesis_plan_path = sibling_artifact(critic_path, "synthesis_plan.json")
    corpus_path = sibling_artifact(critic_path, "corpus_index.json")
    intent = research_intent_from_request(request)
    output_mode = str(intent.get("output_mode") or "")
    needs_timeline = bool(intent.get("needs_timeline"))
    timeline_active = needs_timeline or output_mode == "event_reconstruction" or not intent
    has_synthesis_plan = artifact_exists(workspace_root, synthesis_plan_path)
    contract = request.get("contract") if isinstance(request.get("contract"), dict) else {}
    required_artifacts = contract.get("required_artifacts") if isinstance(contract.get("required_artifacts"), list) else []
    contract_requires_synthesis = any(str(item).endswith("/synthesis_plan.json") for item in required_artifacts)
    contract_requires_structure = any(str(item).endswith("/structure_map.json") for item in required_artifacts)
    required_paths = [
        corpus_path,
        reconstruction_path,
        coverage_path,
        source_path,
        source_snapshots_path,
        rendered_snapshots_path,
        notes_path,
    ]
    if has_synthesis_plan or contract_requires_synthesis:
        required_paths.extend([research_corpus_path, synthesis_plan_path])
    if timeline_active:
        required_paths.append(timeline_path)
    if artifact_exists(workspace_root, structure_path) or contract_requires_structure:
        required_paths.append(structure_path)
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
    rendered_snapshots = load_json(workspace_root, rendered_snapshots_path)
    notes = load_json(workspace_root, notes_path)
    timeline = load_optional_json(workspace_root, timeline_path)
    structure_map = load_optional_json(workspace_root, structure_path)
    research_corpus = load_optional_json(workspace_root, research_corpus_path)
    synthesis_plan = load_optional_json(workspace_root, synthesis_plan_path)
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
    if timeline_active:
        for event_id in sorted(note_event_ids - timeline_event_ids):
            findings.append({"severity": "blocker", "message": f"Missing required direct event in timeline: {event_id}"})
        for event_id in sorted(timeline_event_ids - note_event_ids):
            findings.append({"severity": "blocker", "message": f"Timeline event lacks extracted direct-event note: {event_id}"})
    for event_id, note in sorted(note_by_event_id.items()):
        if not isinstance(note, dict) or not note.get("evidence_snapshots"):
            findings.append({"severity": "blocker", "message": f"Required event lacks fetched source evidence: {event_id}"})
    required_events = required_review_events(source_map, notes, timeline) if timeline_active else []
    for event in required_events:
        event_id = str(event.get("event_id") or "")
        label = required_event_label(event)
        markers = required_event_markers(event)
        if event_id:
            if event_id not in timeline_event_ids:
                findings.append({"severity": "blocker", "message": f"Missing required direct event in timeline: {label}"})
            elif markers and not text_contains_markers(reconstruction, markers):
                findings.append({"severity": "blocker", "message": f"Draft does not visibly cover required event: {label}"})
            elif not note_by_event_id.get(event_id, {}).get("evidence_snapshots"):
                findings.append({"severity": "blocker", "message": f"Required event lacks fetched source evidence: {label}"})

    source_mix = source_mix_metrics(source_map)
    if not source_mix["has_primary_or_official"]:
        warnings.append({"severity": "warning", "message": "No primary, official, publication, or documentation source candidate is listed."})
    if not source_mix["has_secondary_or_crosscheck"]:
        warnings.append({"severity": "warning", "message": "No secondary, reference, or cross-check source is listed."})
    if source_map.get("discovery_status") == "needs_live_discovery":
        findings.append({"severity": "blocker", "message": "Source discovery did not find concrete sources."})
    source_coverage = source_map.get("source_coverage") if isinstance(source_map.get("source_coverage"), dict) else {}
    if source_coverage and not source_coverage.get("ready_for_extraction"):
        findings.append({"severity": "blocker", "message": "Source coverage is not extraction-ready: official/primary evidence and secondary cross-checking are both required."})
    if "## Gaps" not in coverage or "Что еще надо проверить" not in reconstruction:
        findings.append({"severity": "blocker", "message": "Draft package does not expose coverage gaps clearly."})
    if synthesis_plan:
        unsupported = synthesis_plan.get("unsupported_sections") if isinstance(synthesis_plan.get("unsupported_sections"), list) else []
        if unsupported and "Unsupported Sections" not in coverage:
            findings.append({"severity": "blocker", "message": "Draft package hides unsupported synthesis sections."})
        if "Evidence Trace" not in coverage:
            findings.append({"severity": "blocker", "message": "Draft package does not expose evidence trace required by synthesis plan."})
        claim_refs = synthesis_plan.get("evidence_trace", {}).get("claim_refs") if isinstance(synthesis_plan.get("evidence_trace"), dict) else []
        if not claim_refs:
            findings.append({"severity": "blocker", "message": "Synthesis plan has no claim_refs for evidence-grounded writing."})
    synthesis_structure_metrics: dict[str, Any] = {}
    structure_findings, synthesis_structure_metrics = synthesis_structure_findings(output_mode, reconstruction, synthesis_plan)
    findings.extend(structure_findings)
    book_metrics: dict[str, Any] = {}
    if output_mode in {"book_manuscript", "book_manuscript_with_timeline"}:
        for filename in ("book_outline.json", "chapter_plan.json", "manuscript_ru.md", "manuscript.fb2", "continuity_report.json", "editor_report.json"):
            path = sibling_artifact(critic_path, filename)
            if not artifact_exists(workspace_root, path):
                findings.append({"severity": "blocker", "message": f"Book output missing required artifact: {path}"})
        book_findings, book_metrics = book_artifact_findings(workspace_root, critic_path, required_artifacts)
        findings.extend(book_findings)
    comprehensive_findings, comprehensive_metrics = comprehensive_depth_findings(source_map, notes, reconstruction, required_events)
    findings.extend(comprehensive_findings)
    gate_findings, quality_gates = mode_quality_gates(output_mode, research_corpus, structure_map, reconstruction, synthesis_plan)
    findings.extend(gate_findings)
    model_guidance = request_required_scriptorium_guidance(
        "ReductorVerifier",
        request,
        model_review_payload(request, source_map, notes, timeline, reconstruction, coverage, findings, warnings, comprehensive_metrics, quality_gates),
        (
            "You are an independent Scriptorium critic. Check whether the draft actually satisfies the user's "
            "research/writing task, whether chronology is complete, whether unsupported invention exists, and "
            "whether the revision plan points to the right upstream workers. Return JSON with status, blockers, "
            "warnings, and evidence_notes. Do not waive hard source/evidence blockers."
        ),
        request_guidance,
    )
    if not model_guidance.get("ok"):
        return model_unavailable_payload("ReductorVerifier", request.get("task_id"), model_guidance)
    model_blockers, model_warnings = model_review_findings(model_guidance)
    findings.extend(model_blockers)
    warnings.extend(model_warnings)

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
        "required_direct_events": sorted(str(event.get("event_id") or "") for event in required_events if event.get("event_id")),
        "findings": findings,
        "warnings": warnings,
        "revision_plan": revision_plan_from_findings(findings, []),
        "revision_focus": revision_focus,
        "model_guidance": model_guidance,
        "metrics": {
            "sources": len(source_map.get("sources", [])),
            "direct_event_notes": len(notes.get("events", [])),
            "timeline_events": len(timeline_event_ids),
            "generic_evidence_leads": generic_evidence_leads,
            "low_confidence_events": low_confidence_events,
            "source_coverage_ready": bool(source_coverage.get("ready_for_extraction")) if source_coverage else None,
            "source_mix": source_mix,
            "draft_chars": len(reconstruction),
            "comprehensive_depth": comprehensive_metrics,
            "snapshot_count": len(source_snapshots.get("snapshots", [])),
            "rendered_snapshot_count": len(rendered_snapshots.get("rendered_snapshots", [])),
            "output_mode": output_mode,
            "claim_count": len(research_corpus.get("claims", []) if isinstance(research_corpus.get("claims"), list) else []),
            "evidence_trace_count": len(synthesis_plan.get("evidence_trace", {}).get("claim_refs", []) if isinstance(synthesis_plan.get("evidence_trace"), dict) else []),
            "structure_sections": len(structure_map.get("topic_structure", []) if isinstance(structure_map.get("topic_structure"), list) else []),
            "quality_gates": quality_gates,
            "synthesis_structure": synthesis_structure_metrics,
            "book_pipeline": book_metrics,
        },
    }


def run(
    request: dict[str, Any],
    workspace_root: Path,
    request_guidance: GuidanceFn = request_scriptorium_model_guidance,
) -> dict[str, Any]:
    step = request.get("step")
    if not isinstance(step, dict):
        return {"ok": False, "worker": "ReductorVerifier", "error": "request.step must be an object"}
    expected_artifacts = step.get("expected_artifacts")
    if not isinstance(expected_artifacts, list) or not expected_artifacts:
        return {"ok": False, "worker": "ReductorVerifier", "error": "step.expected_artifacts is empty"}
    critic_path = str(expected_artifacts[0])
    try:
        report = review_artifacts(workspace_root, critic_path, request, request_guidance)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "worker": "ReductorVerifier", "error": str(exc)}
    if report.get("error_code") == "model_brain_unavailable":
        return report
    expectation_findings = quality_expectation_findings(request)
    if expectation_findings:
        report.setdefault("findings", []).extend(expectation_findings)
        report["approved"] = False
        report["status"] = "needs_revision"
        report["revision_plan"] = revision_plan_from_findings(report.get("findings", []), report.get("missing_artifacts", []))
    report["revision_plan"] = normalize_revision_plan_for_request(report.get("revision_plan", {}), request)
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
