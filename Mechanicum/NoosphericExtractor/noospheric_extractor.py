from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


PLAYBOOK_DIR = Path(__file__).resolve().parent / "playbooks"


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


def snapshot_evidence(event_id: str, snapshots: dict[str, Any]) -> list[dict[str, str]]:
    markers = EVENT_EVIDENCE_MARKERS.get(event_id, [])
    evidence: list[dict[str, str]] = []
    for snapshot in snapshots.get("snapshots", []):
        if not isinstance(snapshot, dict) or not snapshot.get("ok"):
            continue
        text = str(snapshot.get("text_excerpt") or "")
        lowered = text.lower()
        matched = [marker for marker in markers if marker.lower() in lowered]
        if matched:
            evidence.append(
                {
                    "source_title": str(snapshot.get("source_title") or ""),
                    "matched_markers": ", ".join(matched),
                    "excerpt": evidence_excerpt(text, matched),
                }
            )
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
            if key in {"event_id", "summary", "phase", "confidence", "source_refs"}
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
                    {
                        "source_title": str(snapshot.get("source_title") or ""),
                        "matched_markers": "generic excerpt lead",
                    }
                ],
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
    for event in events:
        if isinstance(event, dict):
            if "evidence_snapshots" not in event:
                event["evidence_snapshots"] = snapshot_evidence(str(event.get("event_id") or ""), source_snapshots)
            event["evidence_status"] = "snapshot_matched" if event.get("evidence_snapshots") else "missing_snapshot_evidence"
    events_with_evidence = sum(1 for event in events if isinstance(event, dict) and event.get("evidence_snapshots"))
    low_confidence_events = sum(1 for event in events if isinstance(event, dict) and str(event.get("confidence") or "").lower() == "low")
    source_coverage = source_map.get("source_coverage") if isinstance(source_map.get("source_coverage"), dict) else {}
    return {
        "topic": topic,
        "events": events,
        "extraction_method": extraction_method,
        "summary": {
            "event_count": len(events),
            "events_with_evidence": events_with_evidence,
            "events_missing_evidence": max(0, len(events) - events_with_evidence),
            "low_confidence_events": low_confidence_events,
            "source_coverage_ready": source_coverage.get("ready_for_extraction") if source_coverage else None,
        },
        "gaps": [
            "Extractor needs direct source text for exact wording and chapter-level evidence.",
            "Events marked medium confidence require confirmation from official narrative text.",
        ] + snapshot_gaps(source_snapshots),
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
    notes = extract_events(source_map, source_snapshots)
    host_path = sandbox_path(workspace_root, output_path)
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text(json.dumps(notes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "worker": "NoosphericExtractor",
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": f"Extracted {len(notes['events'])} direct event notes.",
        "artifacts": [output_path],
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
    request = payload.get("request") if isinstance(payload, dict) and isinstance(payload.get("request"), dict) else payload
    result = run(request, Path(args.workspace_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
