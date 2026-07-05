from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

BRIGADE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIGADE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIGADE_ROOT))

from scriptorium_model import model_unavailable_payload, request_required_scriptorium_guidance  # noqa: E402


PHASE_ORDER = {
    "prelude": 10,
    "arrival": 20,
    "parley": 30,
    "parley_collapse": 40,
    "escalation": 50,
    "battle": 60,
    "turning_point": 70,
    "betrayal": 80,
    "aftermath_boundary": 90,
}


def sandbox_path(workspace_root: Path, path: str) -> Path:
    if not path.startswith("/work/"):
        raise ValueError(f"unsupported sandbox path: {path}")
    return workspace_root / path.removeprefix("/work/")


def notes_path_for_output(output_path: str) -> str:
    if not output_path.startswith("/work/"):
        raise ValueError(f"unsupported output path: {output_path}")
    parent = output_path.rsplit("/", 1)[0]
    return f"{parent}/direct_event_notes.json"


def build_timeline(notes: dict[str, Any]) -> dict[str, Any]:
    events = [event for event in notes.get("events", []) if isinstance(event, dict)]
    sorted_events = sorted(
        events,
        key=lambda event: (
            PHASE_ORDER.get(str(event.get("phase") or ""), 999),
            str(event.get("event_id") or ""),
        ),
    )
    timeline = []
    for index, event in enumerate(sorted_events, start=1):
        timeline.append(
            {
                "order": index,
                "event_id": event.get("event_id"),
                "phase": event.get("phase"),
                "summary": event.get("summary"),
                "narrative_ru": event.get("narrative_ru", ""),
                "confidence": event.get("confidence"),
                "source_refs": event.get("source_refs", []),
                "source_class": event.get("source_class", ""),
                "extraction_method": event.get("extraction_method", ""),
                "evidence_status": event.get("evidence_status", ""),
                "required_for_review": bool(event.get("required_for_review")),
                "review_label": event.get("review_label", ""),
                "evidence_lead": str(event.get("extraction_method") or "") == "generic_snapshot_lead",
            }
        )
    contradictions = []
    if any(item.get("event_id") == "legion_fractures" for item in sorted_events):
        contradictions.append(
            {
                "topic": "direct events vs aftermath",
                "note": "World Eaters fragmentation belongs at the boundary after the direct battle, not as a substitute for the battle narrative.",
            }
        )
    return {
        "topic": notes.get("topic", ""),
        "timeline": timeline,
        "summary": {
            "events": len(timeline),
            "low_confidence_events": sum(1 for item in timeline if item.get("confidence") == "low"),
            "generic_evidence_leads": sum(1 for item in timeline if item.get("evidence_lead")),
            "events_missing_evidence": sum(1 for item in timeline if item.get("evidence_status") == "missing_snapshot_evidence"),
            "source_coverage_ready": notes.get("summary", {}).get("source_coverage_ready") if isinstance(notes.get("summary"), dict) else None,
        },
        "phase_order": PHASE_ORDER,
        "contradictions": contradictions,
        "gaps": notes.get("gaps", []),
    }


def run(request: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    step = request.get("step")
    if not isinstance(step, dict):
        return {"ok": False, "worker": "Chronologis", "error": "request.step must be an object"}
    expected_artifacts = step.get("expected_artifacts")
    if not isinstance(expected_artifacts, list) or not expected_artifacts:
        return {"ok": False, "worker": "Chronologis", "error": "step.expected_artifacts is empty"}
    output_path = str(expected_artifacts[0])
    notes_path = notes_path_for_output(output_path)
    notes_host_path = sandbox_path(workspace_root, notes_path)
    if not notes_host_path.exists():
        return {"ok": False, "worker": "Chronologis", "error": "direct_event_notes is missing", "missing": notes_path}
    notes = json.loads(notes_host_path.read_text(encoding="utf-8"))
    guidance = request_required_scriptorium_guidance(
        "Chronologis",
        request,
        {"task_id": request.get("task_id"), "step": step, "notes": notes},
        "Order extracted material for the requested task and identify chronology/source-order risks. Return JSON guidance only.",
    )
    if not guidance.get("ok"):
        return model_unavailable_payload("Chronologis", request.get("task_id"), guidance)
    timeline = build_timeline(notes)
    timeline["model_guidance"] = guidance
    host_path = sandbox_path(workspace_root, output_path)
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "worker": "Chronologis",
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": f"Timeline written with {len(timeline['timeline'])} events.",
        "artifacts": [output_path],
        "model_guidance": guidance,
        "gaps": timeline["gaps"],
        "confidence": "medium",
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run Chronologis on a Worker API request JSON.")
    parser.add_argument("request_json")
    parser.add_argument("--workspace-root", default="runtime/chronologis-work")
    args = parser.parse_args()
    payload = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
    request = payload.get("request") if isinstance(payload, dict) and isinstance(payload.get("request"), dict) else payload
    result = run(request, Path(args.workspace_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
