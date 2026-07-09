from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


PLAYBOOK_DIR = Path(__file__).resolve().parent / "playbooks"
BRIGADE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIGADE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIGADE_ROOT))

from scriptorium_model import model_unavailable_payload, parsed_model_content, request_required_scriptorium_guidance  # noqa: E402


def load_playbook(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"playbook must be an object: {path.name}")
    return payload


def load_event_playbooks() -> list[dict[str, Any]]:
    playbooks: list[dict[str, Any]] = []
    if not PLAYBOOK_DIR.exists():
        return playbooks
    for path in sorted(PLAYBOOK_DIR.glob("*.json")):
        try:
            playbooks.append(load_playbook(path))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return playbooks


EVENT_PLAYBOOKS = load_event_playbooks()


def sandbox_path(workspace_root: Path, path: str) -> Path:
    if not path.startswith("/work/"):
        raise ValueError(f"unsupported sandbox path: {path}")
    return workspace_root / path.removeprefix("/work/")


def source_map_path_for_output(output_path: str) -> str:
    if not output_path.startswith("/work/"):
        raise ValueError(f"unsupported output path: {output_path}")
    parent = output_path.rsplit("/", 1)[0]
    return f"{parent}/source_map.json"


def source_snapshots_path_for_output(output_path: str) -> str:
    if not output_path.startswith("/work/"):
        raise ValueError(f"unsupported output path: {output_path}")
    parent = output_path.rsplit("/", 1)[0]
    return f"{parent}/source_snapshots.json"


def rendered_snapshots_path_for_output(output_path: str) -> str:
    if not output_path.startswith("/work/"):
        raise ValueError(f"unsupported output path: {output_path}")
    parent = output_path.rsplit("/", 1)[0]
    return f"{parent}/rendered_snapshots.json"


def research_corpus_path_for_output(output_path: str) -> str:
    if not output_path.startswith("/work/"):
        raise ValueError(f"unsupported output path: {output_path}")
    parent = output_path.rsplit("/", 1)[0]
    return f"{parent}/research_corpus.json"


EVENT_EVIDENCE_MARKERS = {
    str(event.get("event_id")): [str(marker) for marker in event.get("evidence_markers", [])]
    for playbook in EVENT_PLAYBOOKS
    for event in playbook.get("events", [])
    if isinstance(event, dict) and event.get("event_id")
}


def load_optional_snapshots(workspace_root: Path, output_path: str) -> dict[str, Any]:
    snapshots_path = sandbox_path(workspace_root, source_snapshots_path_for_output(output_path))
    if not snapshots_path.exists():
        return {}
    payload = json.loads(snapshots_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def load_optional_rendered_snapshots(workspace_root: Path, output_path: str) -> dict[str, Any]:
    rendered_path = sandbox_path(workspace_root, rendered_snapshots_path_for_output(output_path))
    if not rendered_path.exists():
        return {}
    payload = json.loads(rendered_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def merge_rendered_snapshots(source_snapshots: dict[str, Any], rendered_snapshots: dict[str, Any]) -> dict[str, Any]:
    if not rendered_snapshots:
        return source_snapshots
    merged = dict(source_snapshots)
    snapshots = [dict(item) for item in source_snapshots.get("snapshots", []) if isinstance(item, dict)]
    rendered_items = rendered_snapshots.get("rendered_snapshots") if isinstance(rendered_snapshots.get("rendered_snapshots"), list) else []
    for rendered in rendered_items:
        if not isinstance(rendered, dict) or not rendered.get("ok"):
            continue
        snapshots.append(
            {
                "source_title": rendered.get("source_title", ""),
                "source_class": rendered.get("source_class", "rendered_source"),
                "source_type": rendered.get("source_type", "rendered_source"),
                "requested_url": rendered.get("requested_url", ""),
                "final_url": rendered.get("final_url", ""),
                "ok": True,
                "title": rendered.get("title", ""),
                "text_excerpt": rendered.get("text_excerpt", ""),
                "rendered": True,
                "render_required": False,
            }
        )
    merged["snapshots"] = snapshots
    merged["rendered_summary"] = rendered_snapshots.get("summary", {}) if isinstance(rendered_snapshots.get("summary"), dict) else {}
    return merged


def evidence_excerpt(text: str, matched: list[str], max_chars: int = 520) -> str:
    compact = " ".join(text.split())
    if not compact:
        return ""
    lowered = compact.lower()
    positions = [lowered.find(marker.lower()) for marker in matched if marker and marker.lower() in lowered]
    pivot = min([position for position in positions if position >= 0], default=0)
    start = max(0, pivot - max_chars // 3)
    end = min(len(compact), start + max_chars)
    if end - start < max_chars and start > 0:
        start = max(0, end - max_chars)
    excerpt = compact[start:end].strip()
    if start > 0:
        excerpt = "... " + excerpt
    if end < len(compact):
        excerpt += " ..."
    return excerpt


def snapshot_is_primary(snapshot: dict[str, Any]) -> bool:
    source_class = str(snapshot.get("source_class") or "").lower()
    source_type = str(snapshot.get("source_type") or "").lower()
    return bool(snapshot.get("local_path")) or "primary" in source_class or source_type in {"published_primary", "local_primary", "official_primary_extract"}


def evidence_item(snapshot: dict[str, Any], matched_markers: str, excerpt: str) -> dict[str, Any]:
    return {
        "source_title": str(snapshot.get("source_title") or ""),
        "source_class": str(snapshot.get("source_class") or ""),
        "source_type": str(snapshot.get("source_type") or ""),
        "local_path": str(snapshot.get("local_path") or ""),
        "matched_markers": matched_markers,
        "excerpt": excerpt,
        "is_primary_source": snapshot_is_primary(snapshot),
    }


def snapshot_search_text(snapshot: dict[str, Any]) -> str:
    """Full text for evidence search: user-provided local primaries are read
    whole from disk — the snapshot excerpt covers only the head of the file,
    and the key scene is usually not on the first pages."""
    local_path = str(snapshot.get("local_path") or "")
    if local_path:
        try:
            return Path(local_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    return str(snapshot.get("text_excerpt") or "")


def snapshot_evidence(event_id: str, snapshots: dict[str, Any]) -> list[dict[str, Any]]:
    markers = EVENT_EVIDENCE_MARKERS.get(event_id, [])
    evidence: list[dict[str, str]] = []
    for snapshot in snapshots.get("snapshots", []):
        if not isinstance(snapshot, dict) or not snapshot.get("ok"):
            continue
        text = snapshot_search_text(snapshot)
        lowered = text.lower()
        matched = [marker for marker in markers if marker.lower() in lowered]
        if matched:
            evidence.append(evidence_item(snapshot, ", ".join(matched), evidence_excerpt(text, matched)))
    return evidence


def snapshot_gaps(snapshots: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    for snapshot in snapshots.get("snapshots", []):
        if not isinstance(snapshot, dict):
            continue
        title = snapshot.get("source_title") or snapshot.get("requested_url") or "unknown source"
        if snapshot.get("render_required"):
            reason = snapshot.get("render_reason") or "JavaScript-rendered page needs browser rendering"
            gaps.append(f"Source requires browser render for {title}: {reason}")
        if not snapshot.get("ok"):
            error = snapshot.get("error") or "fetch failed"
            gaps.append(f"Source snapshot unavailable for {title}: {error}")
    for skipped in snapshots.get("skipped", []):
        if isinstance(skipped, dict):
            title = skipped.get("source_title") or "unknown source"
            reason = skipped.get("reason") or "skipped"
            gaps.append(f"Source not fetched for {title}: {reason}")
    return gaps


def playbook_matches(playbook: dict[str, Any], topic: str, source_titles: set[str]) -> bool:
    haystack = " ".join([topic.lower(), *(title.lower() for title in source_titles)])
    return any(str(term).lower() in haystack for term in playbook.get("match_terms", []))


def events_from_playbook(playbook: dict[str, Any], source_titles: set[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw_event in playbook.get("events", []):
        if not isinstance(raw_event, dict):
            continue
        event = {
            key: value
            for key, value in raw_event.items()
            if key
            in {
                "event_id",
                "summary",
                "narrative_ru",
                "phase",
                "confidence",
                "source_refs",
                "required_for_review",
                "review_label",
            }
        }
        if event.get("source_refs") == ["__ALL_SOURCE_TITLES__"]:
            event["source_refs"] = sorted(source_titles)
        events.append(event)
    return events


def first_sentence(text: str, max_chars: int = 360) -> str:
    compact = " ".join(text.split())
    if not compact:
        return ""
    match = re.search(r"(?<=[.!?])\s+", compact)
    sentence = compact[: match.start()].strip() if match else compact[:max_chars].strip()
    return sentence[:max_chars].strip()


def source_ref(snapshot: dict[str, Any]) -> str:
    return str(snapshot.get("source_title") or snapshot.get("title") or snapshot.get("requested_url") or "unknown source")


def stable_id(prefix: str, index: int) -> str:
    return f"{prefix}_{index}"


def generic_events_from_snapshots(source_snapshots: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for snapshot in source_snapshots.get("snapshots", []):
        if not isinstance(snapshot, dict) or not snapshot.get("ok"):
            continue
        excerpt = first_sentence(str(snapshot.get("text_excerpt") or ""))
        if not excerpt:
            continue
        event_id = f"evidence_lead_{len(events) + 1}"
        events.append(
            {
                "event_id": event_id,
                "summary": excerpt,
                "phase": "unknown",
                "confidence": "low",
                "source_refs": [str(snapshot.get("source_title") or snapshot.get("requested_url") or "unknown source")],
                "source_class": str(snapshot.get("source_class") or ""),
                "extraction_method": "generic_snapshot_lead",
                "evidence_snapshots": [
                    evidence_item(snapshot, "generic excerpt lead", excerpt)
                ],
            }
        )
    return events


def primary_evidence_leads(source_snapshots: dict[str, Any], existing_source_refs: set[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for snapshot in source_snapshots.get("snapshots", []):
        if not isinstance(snapshot, dict) or not snapshot.get("ok") or not snapshot_is_primary(snapshot):
            continue
        title = str(snapshot.get("source_title") or snapshot.get("requested_url") or "unknown primary source")
        text = str(snapshot.get("text_excerpt") or "")
        excerpt = first_sentence(text, max_chars=520)
        if not excerpt:
            continue
        if title in existing_source_refs:
            continue
        event_id = f"primary_text_evidence_{len(events) + 1}"
        events.append(
            {
                "event_id": event_id,
                "summary": excerpt,
                "phase": "primary_text",
                "confidence": "medium",
                "source_refs": [title],
                "source_class": str(snapshot.get("source_class") or ""),
                "extraction_method": "primary_text_evidence_lead",
                "evidence_snapshots": [evidence_item(snapshot, "primary text excerpt lead", excerpt)],
            }
        )
    return events


def extract_events(source_map: dict[str, Any], source_snapshots: dict[str, Any] | None = None) -> dict[str, Any]:
    topic = str(source_map.get("topic") or "")
    source_snapshots = source_snapshots or {}
    source_titles = {
        str(item.get("title") or "")
        for item in source_map.get("sources", [])
        if isinstance(item, dict) and item.get("title")
    }
    events: list[dict[str, Any]] = []
    for playbook in EVENT_PLAYBOOKS:
        if playbook_matches(playbook, topic, source_titles):
            events.extend(events_from_playbook(playbook, source_titles))
    extraction_method = "playbook" if events else "generic_snapshot_leads"
    if not events:
        events.extend(generic_events_from_snapshots(source_snapshots))
    existing_source_refs = {
        str(ref)
        for event in events
        if isinstance(event, dict)
        for ref in (event.get("source_refs") if isinstance(event.get("source_refs"), list) else [])
    }
    primary_leads = primary_evidence_leads(source_snapshots, existing_source_refs)
    events.extend(primary_leads)
    for event in events:
        if isinstance(event, dict):
            if "evidence_snapshots" not in event:
                event["evidence_snapshots"] = snapshot_evidence(str(event.get("event_id") or ""), source_snapshots)
            primary_evidence = [
                item
                for item in event.get("evidence_snapshots", [])
                if isinstance(item, dict) and item.get("is_primary_source")
            ]
            event["primary_evidence_snapshots"] = primary_evidence
            event["evidence_status"] = "snapshot_matched" if event.get("evidence_snapshots") else "missing_snapshot_evidence"
    events_with_evidence = sum(1 for event in events if isinstance(event, dict) and event.get("evidence_snapshots"))
    events_with_primary_evidence = sum(1 for event in events if isinstance(event, dict) and event.get("primary_evidence_snapshots"))
    low_confidence_events = sum(1 for event in events if isinstance(event, dict) and str(event.get("confidence") or "").lower() == "low")
    source_coverage = source_map.get("source_coverage") if isinstance(source_map.get("source_coverage"), dict) else {}
    primary_snapshot_count = sum(1 for item in source_snapshots.get("snapshots", []) if isinstance(item, dict) and item.get("ok") and snapshot_is_primary(item))
    return {
        "topic": topic,
        "events": events,
        "extraction_method": extraction_method,
        "summary": {
            "event_count": len(events),
            "events_with_evidence": events_with_evidence,
            "events_with_primary_evidence": events_with_primary_evidence,
            "events_missing_evidence": max(0, len(events) - events_with_evidence),
            "low_confidence_events": low_confidence_events,
            "primary_snapshot_count": primary_snapshot_count,
            "primary_evidence_lead_count": len(primary_leads),
            "source_coverage_ready": source_coverage.get("ready_for_extraction") if source_coverage else None,
        },
        "gaps": [
            "Extractor needs direct source text for exact wording and chapter-level evidence.",
            "Events marked medium confidence require confirmation from official narrative text.",
        ] + snapshot_gaps(source_snapshots),
    }


def claims_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            continue
        summary = str(event.get("summary") or "").strip()
        if not summary:
            continue
        claims.append(
            {
                "claim_id": stable_id("event_claim", index),
                "claim": summary,
                "claim_type": "event",
                "confidence": str(event.get("confidence") or "unknown"),
                "source_refs": event.get("source_refs", []) if isinstance(event.get("source_refs"), list) else [],
                "evidence_refs": [item.get("source_title") for item in event.get("evidence_snapshots", []) if isinstance(item, dict) and item.get("source_title")],
                "event_id": event.get("event_id", ""),
            }
        )
    return claims


def claims_from_snapshots(source_snapshots: dict[str, Any], existing_claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    known = {str(item.get("claim") or "").lower() for item in existing_claims if isinstance(item, dict)}
    claims: list[dict[str, Any]] = []
    for snapshot in source_snapshots.get("snapshots", []):
        if not isinstance(snapshot, dict) or not snapshot.get("ok"):
            continue
        claim = first_sentence(str(snapshot.get("text_excerpt") or ""), max_chars=420)
        if not claim or claim.lower() in known:
            continue
        claims.append(
            {
                "claim_id": stable_id("source_claim", len(claims) + 1),
                "claim": claim,
                "claim_type": "source_lead",
                "confidence": "medium" if snapshot_is_primary(snapshot) else "low",
                "source_refs": [source_ref(snapshot)],
                "evidence_refs": [source_ref(snapshot)],
            }
        )
    return claims


def evidence_quotes_from_notes(notes: dict[str, Any], source_snapshots: dict[str, Any]) -> list[dict[str, Any]]:
    quotes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for event in notes.get("events", []):
        if not isinstance(event, dict):
            continue
        for evidence in event.get("evidence_snapshots", []):
            if not isinstance(evidence, dict):
                continue
            excerpt = str(evidence.get("excerpt") or "").strip()
            source_title = str(evidence.get("source_title") or "")
            if not excerpt or (source_title, excerpt) in seen:
                continue
            seen.add((source_title, excerpt))
            quotes.append(
                {
                    "quote_id": stable_id("evidence", len(quotes) + 1),
                    "source_ref": source_title,
                    "event_id": event.get("event_id", ""),
                    "excerpt": excerpt,
                    "is_primary_source": bool(evidence.get("is_primary_source")),
                }
            )
    for snapshot in source_snapshots.get("snapshots", []):
        if not isinstance(snapshot, dict) or not snapshot.get("ok"):
            continue
        excerpt = first_sentence(str(snapshot.get("text_excerpt") or ""), max_chars=520)
        source_title = source_ref(snapshot)
        if not excerpt or (source_title, excerpt) in seen:
            continue
        seen.add((source_title, excerpt))
        quotes.append(
            {
                "quote_id": stable_id("evidence", len(quotes) + 1),
                "source_ref": source_title,
                "event_id": "",
                "excerpt": excerpt,
                "is_primary_source": snapshot_is_primary(snapshot),
            }
        )
    return quotes


def arguments_from_claims(claims: list[dict[str, Any]], source_map: dict[str, Any]) -> list[dict[str, Any]]:
    arguments: list[dict[str, Any]] = []
    source_classes = {
        str(item.get("title") or ""): str(item.get("class") or item.get("source_class") or item.get("type") or item.get("source_type") or "")
        for item in source_map.get("sources", [])
        if isinstance(item, dict)
    }
    for index, claim in enumerate(claims[:12], start=1):
        refs = claim.get("source_refs") if isinstance(claim.get("source_refs"), list) else []
        arguments.append(
            {
                "argument_id": stable_id("argument", index),
                "summary": f"Use claim {claim.get('claim_id')} as a supported synthesis point.",
                "claim_refs": [claim.get("claim_id")],
                "source_refs": refs,
                "source_classes": [source_classes.get(str(ref), "") for ref in refs],
                "confidence": claim.get("confidence", "unknown"),
            }
        )
    return arguments


def definitions_from_topic(source_map: dict[str, Any], claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    topic = str(source_map.get("topic") or "").strip()
    if not topic:
        return []
    first_claim = next((str(claim.get("claim") or "") for claim in claims if isinstance(claim, dict) and claim.get("claim")), "")
    return [
        {
            "term": topic,
            "definition": first_claim or f"Research topic: {topic}",
            "confidence": "low" if not first_claim else "medium",
            "source_refs": claims[0].get("source_refs", []) if claims else [],
        }
    ]


def known_source_refs(source_map: dict[str, Any], source_snapshots: dict[str, Any]) -> set[str]:
    refs = {
        str(item.get("title") or "")
        for item in source_map.get("sources", [])
        if isinstance(item, dict) and item.get("title")
    }
    for snapshot in source_snapshots.get("snapshots", []):
        if isinstance(snapshot, dict):
            refs.add(source_ref(snapshot))
    return {ref for ref in refs if ref}


def refs_are_known(refs: Any, known_refs: set[str]) -> bool:
    if not isinstance(refs, list) or not refs:
        return False
    return any(str(ref) in known_refs for ref in refs)


def append_unique_by_text(items: list[dict[str, Any]], additions: list[dict[str, Any]], text_key: str) -> list[dict[str, Any]]:
    seen = {str(item.get(text_key) or "").strip().lower() for item in items if isinstance(item, dict)}
    for addition in additions:
        text = str(addition.get(text_key) or "").strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        items.append(addition)
    return items


def model_research_layers(guidance: dict[str, Any], source_map: dict[str, Any], source_snapshots: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    parsed = parsed_model_content(guidance)
    known_refs = known_source_refs(source_map, source_snapshots)
    layers: dict[str, list[dict[str, Any]]] = {
        "claims": [],
        "arguments": [],
        "definitions": [],
        "quotes": [],
        "contradictions": [],
        "open_questions": [],
    }
    for raw in parsed.get("claims", []) if isinstance(parsed.get("claims"), list) else []:
        if not isinstance(raw, dict) or not str(raw.get("claim") or "").strip():
            continue
        source_refs = raw.get("source_refs") if isinstance(raw.get("source_refs"), list) else raw.get("evidence_refs")
        if not refs_are_known(source_refs, known_refs):
            continue
        layers["claims"].append(
            {
                "claim_id": str(raw.get("claim_id") or stable_id("model_claim", len(layers["claims"]) + 1)),
                "claim": str(raw.get("claim") or "").strip(),
                "claim_type": str(raw.get("claim_type") or "model_guided"),
                "confidence": str(raw.get("confidence") or "low"),
                "source_refs": [str(ref) for ref in source_refs if str(ref) in known_refs],
                "evidence_refs": [str(ref) for ref in (raw.get("evidence_refs") if isinstance(raw.get("evidence_refs"), list) else source_refs) if str(ref)],
                "extraction_method": "model_guided_grounded",
            }
        )
    for raw in parsed.get("arguments", []) if isinstance(parsed.get("arguments"), list) else []:
        if not isinstance(raw, dict) or not str(raw.get("summary") or "").strip():
            continue
        source_refs = raw.get("source_refs") if isinstance(raw.get("source_refs"), list) else []
        claim_refs = raw.get("claim_refs") if isinstance(raw.get("claim_refs"), list) else []
        if not claim_refs and not refs_are_known(source_refs, known_refs):
            continue
        layers["arguments"].append(
            {
                "argument_id": str(raw.get("argument_id") or stable_id("model_argument", len(layers["arguments"]) + 1)),
                "summary": str(raw.get("summary") or "").strip(),
                "claim_refs": [str(ref) for ref in claim_refs if str(ref).strip()],
                "source_refs": [str(ref) for ref in source_refs if str(ref) in known_refs],
                "confidence": str(raw.get("confidence") or "low"),
                "extraction_method": "model_guided_grounded",
            }
        )
    for raw in parsed.get("definitions", []) if isinstance(parsed.get("definitions"), list) else []:
        if not isinstance(raw, dict) or not str(raw.get("term") or "").strip() or not str(raw.get("definition") or "").strip():
            continue
        source_refs = raw.get("source_refs") if isinstance(raw.get("source_refs"), list) else []
        if source_refs and not refs_are_known(source_refs, known_refs):
            continue
        layers["definitions"].append(
            {
                "term": str(raw.get("term") or "").strip(),
                "definition": str(raw.get("definition") or "").strip(),
                "confidence": str(raw.get("confidence") or "low"),
                "source_refs": [str(ref) for ref in source_refs if str(ref) in known_refs],
                "extraction_method": "model_guided_grounded",
            }
        )
    raw_quotes = parsed.get("quotes") or parsed.get("evidence_excerpts") or []
    for raw in raw_quotes if isinstance(raw_quotes, list) else []:
        if not isinstance(raw, dict) or not str(raw.get("excerpt") or "").strip():
            continue
        source = str(raw.get("source_ref") or raw.get("source_title") or "").strip()
        if source not in known_refs:
            continue
        layers["quotes"].append(
            {
                "quote_id": str(raw.get("quote_id") or stable_id("model_evidence", len(layers["quotes"]) + 1)),
                "source_ref": source,
                "event_id": str(raw.get("event_id") or ""),
                "excerpt": str(raw.get("excerpt") or "").strip(),
                "is_primary_source": bool(raw.get("is_primary_source")),
                "extraction_method": "model_guided_grounded",
            }
        )
    for raw in parsed.get("contradictions", []) if isinstance(parsed.get("contradictions"), list) else []:
        if isinstance(raw, dict) and str(raw.get("summary") or "").strip():
            layers["contradictions"].append(
                {
                    "contradiction_id": str(raw.get("contradiction_id") or stable_id("model_contradiction", len(layers["contradictions"]) + 1)),
                    "summary": str(raw.get("summary") or "").strip(),
                    "status": str(raw.get("status") or "unresolved"),
                    "source_refs": [str(ref) for ref in raw.get("source_refs", []) if str(ref) in known_refs] if isinstance(raw.get("source_refs"), list) else [],
                    "extraction_method": "model_guided",
                }
            )
    for raw in parsed.get("open_questions", []) if isinstance(parsed.get("open_questions"), list) else []:
        if isinstance(raw, dict):
            question = str(raw.get("question") or raw.get("summary") or "").strip()
            reason = str(raw.get("reason") or "model_guided_gap")
        else:
            question = str(raw or "").strip()
            reason = "model_guided_gap"
        if question:
            layers["open_questions"].append({"question": question, "reason": reason})
    return layers


def build_research_corpus(source_map: dict[str, Any], source_snapshots: dict[str, Any], notes: dict[str, Any], guidance: dict[str, Any]) -> dict[str, Any]:
    event_claims = claims_from_events([event for event in notes.get("events", []) if isinstance(event, dict)])
    claims = event_claims + claims_from_snapshots(source_snapshots, event_claims)
    evidence_quotes = evidence_quotes_from_notes(notes, source_snapshots)
    model_layers = model_research_layers(guidance, source_map, source_snapshots)
    claims = append_unique_by_text(claims, model_layers["claims"], "claim")
    evidence_quotes = append_unique_by_text(evidence_quotes, model_layers["quotes"], "excerpt")
    gaps = notes.get("gaps", []) if isinstance(notes.get("gaps"), list) else []
    coverage_risks = [
        {
            "risk_id": stable_id("coverage_risk", index),
            "summary": gap,
            "status": "open",
        }
        for index, gap in enumerate(gaps, start=1)
        if any(marker in str(gap).lower() for marker in ["blocked", "unavailable", "requires browser", "403", "404"])
    ]
    contradictions = [
        {
            "contradiction_id": stable_id("contradiction", index),
            "summary": gap,
            "status": "unresolved",
        }
        for index, gap in enumerate(gaps, start=1)
        if any(marker in str(gap).lower() for marker in ["contradict", "conflict", "inconsistent", "uncertain"])
    ]
    contradictions = append_unique_by_text(contradictions, model_layers["contradictions"], "summary")
    snapshots = [snapshot for snapshot in source_snapshots.get("snapshots", []) if isinstance(snapshot, dict)]
    definitions = definitions_from_topic(source_map, claims)
    definitions = append_unique_by_text(definitions, model_layers["definitions"], "term")
    arguments = arguments_from_claims(claims, source_map)
    arguments = append_unique_by_text(arguments, model_layers["arguments"], "summary")
    open_questions = [{"question": gap, "reason": "coverage_gap"} for gap in gaps]
    open_questions = append_unique_by_text(open_questions, model_layers["open_questions"], "question")
    return {
        "version": 1,
        "topic": source_map.get("topic", ""),
        "sources": source_map.get("sources", []) if isinstance(source_map.get("sources"), list) else [],
        "snapshots": snapshots,
        "rendered_text": [
            {
                "source_ref": source_ref(snapshot),
                "text_excerpt": str(snapshot.get("text_excerpt") or ""),
                "rendered": bool(snapshot.get("rendered")),
            }
            for snapshot in snapshots
            if snapshot.get("ok") and str(snapshot.get("text_excerpt") or "")
        ],
        "events": notes.get("events", []) if isinstance(notes.get("events"), list) else [],
        "claims": claims,
        "arguments": arguments,
        "definitions": definitions,
        "quotes": evidence_quotes,
        "evidence_excerpts": evidence_quotes,
        "contradictions": contradictions,
        "coverage_risks": coverage_risks,
        "open_questions": open_questions,
        "confidence": {
            "event_summary": notes.get("summary", {}),
            "claim_count": len(claims),
            "evidence_excerpt_count": len(evidence_quotes),
            "source_count": len(source_map.get("sources", []) if isinstance(source_map.get("sources"), list) else []),
        },
        "gaps": gaps,
        "model_guidance": guidance,
    }


def run(request: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    step = request.get("step")
    if not isinstance(step, dict):
        return {"ok": False, "worker": "NoosphericExtractor", "error": "request.step must be an object"}
    expected_artifacts = step.get("expected_artifacts")
    if not isinstance(expected_artifacts, list) or not expected_artifacts:
        return {"ok": False, "worker": "NoosphericExtractor", "error": "step.expected_artifacts is empty"}
    output_path = str(expected_artifacts[0])
    source_path = source_map_path_for_output(output_path)
    source_host_path = sandbox_path(workspace_root, source_path)
    if not source_host_path.exists():
        return {"ok": False, "worker": "NoosphericExtractor", "error": "source_map is missing", "missing": source_path}
    source_map = json.loads(source_host_path.read_text(encoding="utf-8"))
    source_snapshots = load_optional_snapshots(workspace_root, output_path)
    rendered_snapshots = load_optional_rendered_snapshots(workspace_root, output_path)
    source_snapshots = merge_rendered_snapshots(source_snapshots, rendered_snapshots)
    guidance = request_required_scriptorium_guidance(
        "NoosphericExtractor",
        request,
        {
            "task_id": request.get("task_id"),
            "step": step,
            "source_map": source_map,
            "source_snapshots": source_snapshots,
        },
        "Extract the claims/events/arguments that matter for the task and identify evidence risks. Return JSON guidance only.",
    )
    if not guidance.get("ok"):
        return model_unavailable_payload("NoosphericExtractor", request.get("task_id"), guidance)
    notes = extract_events(source_map, source_snapshots)
    notes["model_guidance"] = guidance
    research_corpus = build_research_corpus(source_map, source_snapshots, notes, guidance)
    host_path = sandbox_path(workspace_root, output_path)
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text(json.dumps(notes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    corpus_path = next((str(item) for item in expected_artifacts if str(item).endswith("/research_corpus.json")), research_corpus_path_for_output(output_path))
    corpus_host_path = sandbox_path(workspace_root, corpus_path)
    corpus_host_path.parent.mkdir(parents=True, exist_ok=True)
    corpus_host_path.write_text(json.dumps(research_corpus, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "worker": "NoosphericExtractor",
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": f"Extracted {len(notes['events'])} events and {len(research_corpus['claims'])} claims into research corpus.",
        "artifacts": [output_path, corpus_path],
        "model_guidance": guidance,
        "gaps": notes["gaps"],
        "confidence": "medium",
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run NoosphericExtractor on a Worker API request JSON.")
    parser.add_argument("request_json")
    parser.add_argument("--workspace-root", default="runtime/noospheric-work")
    args = parser.parse_args()
    payload = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
    _brigade_root = Path(__file__).resolve().parents[1]
    if str(_brigade_root) not in sys.path:
        sys.path.insert(0, str(_brigade_root))
    from worker_protocol import strict_worker_request_from_payload  # noqa: PLC0415

    request = strict_worker_request_from_payload(payload, "NoosphericExtractor")
    result = run(request, Path(args.workspace_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
