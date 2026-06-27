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


def text_contains_markers(text: str, markers: list[str]) -> bool:
    lowered = text.lower()
    return all(marker.lower() in lowered for marker in markers)


def review_artifacts(workspace_root: Path, critic_path: str) -> dict[str, Any]:
    reconstruction_path = sibling_artifact(critic_path, "reconstruction_ru.md")
    coverage_path = sibling_artifact(critic_path, "coverage_report.md")
    source_path = sibling_artifact(critic_path, "source_map.json")
    source_snapshots_path = sibling_artifact(critic_path, "source_snapshots.json")
    notes_path = sibling_artifact(critic_path, "direct_event_notes.json")
    timeline_path = sibling_artifact(critic_path, "timeline.json")
    required_paths = [reconstruction_path, coverage_path, source_path, source_snapshots_path, notes_path, timeline_path]
    missing_artifacts = [path for path in required_paths if not artifact_exists(workspace_root, path)]
    if missing_artifacts:
        return {
            "status": "failed",
            "approved": False,
            "missing_artifacts": missing_artifacts,
            "findings": [{"severity": "blocker", "message": f"Missing artifact: {path}"} for path in missing_artifacts],
            "warnings": [],
        }

    source_map = load_json(workspace_root, source_path)
    notes = load_json(workspace_root, notes_path)
    timeline = load_json(workspace_root, timeline_path)
    reconstruction = read_text(workspace_root, reconstruction_path)
    coverage = read_text(workspace_root, coverage_path)
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
    if "## Gaps" not in coverage or "Что еще надо проверить" not in reconstruction:
        findings.append({"severity": "blocker", "message": "Draft package does not expose coverage gaps clearly."})

    notes_gaps = [str(item) for item in notes.get("gaps", []) if item]
    timeline_gaps = [str(item) for item in timeline.get("gaps", []) if item]
    if notes_gaps or timeline_gaps:
        warnings.append(
            {
                "severity": "warning",
                "message": "Worker package still depends on unverified or inaccessible primary wording.",
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
        "metrics": {
            "sources": len(source_map.get("sources", [])),
            "direct_event_notes": len(notes.get("events", [])),
            "timeline_events": len(timeline_event_ids),
            "draft_chars": len(reconstruction),
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
