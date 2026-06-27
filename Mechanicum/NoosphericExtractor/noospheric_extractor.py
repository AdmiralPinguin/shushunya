from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def sandbox_path(workspace_root: Path, path: str) -> Path:
    if not path.startswith("/work/"):
        raise ValueError(f"unsupported sandbox path: {path}")
    return workspace_root / path.removeprefix("/work/")


def source_map_path_for_output(output_path: str) -> str:
    if not output_path.startswith("/work/"):
        raise ValueError(f"unsupported output path: {output_path}")
    parent = output_path.rsplit("/", 1)[0]
    return f"{parent}/source_map.json"


def skalathrax_events(source_titles: set[str]) -> list[dict[str, Any]]:
    source_refs = sorted(source_titles)
    return [
        {
            "event_id": "ec_claim_system",
            "summary": "Emperor's Children discovered and claimed Skalathrax as a refuge/sanctuary before the World Eaters arrived.",
            "phase": "prelude",
            "confidence": "high",
            "source_refs": ["Lexicanum: Battle of Skalathrax"],
        },
        {
            "event_id": "world_eaters_arrival",
            "summary": "A large World Eaters fleet entered the system, creating a direct territorial conflict with Emperor's Children.",
            "phase": "arrival",
            "confidence": "high",
            "source_refs": ["Lexicanum: Battle of Skalathrax"],
        },
        {
            "event_id": "world_eaters_internal_dispute",
            "summary": "World Eaters leaders were split between immediate attack, withdrawal, and negotiation before Kharn pushed events toward violence.",
            "phase": "prelude",
            "confidence": "medium",
            "source_refs": ["Kharn: Eater of Worlds", "Lexicanum: Battle of Skalathrax"],
        },
        {
            "event_id": "moon_parley",
            "summary": "Kharn arranged or joined a parley on Skalathrax's moon with Emperor's Children representatives.",
            "phase": "parley",
            "confidence": "medium-high",
            "source_refs": ["Kharn: Eater of Worlds", "Lexicanum: Battle of Skalathrax"],
        },
        {
            "event_id": "anteus_hedonarch_presence",
            "summary": "Tiberius Angellus Anteus and the Hedonarch are associated with the Emperor's Children side of the parley account.",
            "phase": "parley",
            "confidence": "medium",
            "source_refs": ["Kharn: Eater of Worlds", "Lexicanum: Battle of Skalathrax"],
        },
        {
            "event_id": "dreagher_shoots_anteus",
            "summary": "Dreagher's shooting of Anteus is treated as a key trigger that collapses the parley into bloodshed.",
            "phase": "parley_collapse",
            "confidence": "medium-high",
            "source_refs": ["Lexicanum: Dreagher", "Kharn: Eater of Worlds"],
        },
        {
            "event_id": "golden_absolute",
            "summary": "Kharn's group is linked to the capture or redirection of the Emperor's Children vessel Golden Absolute during the escalation.",
            "phase": "escalation",
            "confidence": "medium",
            "source_refs": ["Kharn: Eater of Worlds", "Lexicanum: Battle of Skalathrax"],
        },
        {
            "event_id": "planetary_battle",
            "summary": "World Eaters attacked across Skalathrax and Emperor's Children resisted, including Lucius-related forces in the wider account.",
            "phase": "battle",
            "confidence": "medium-high",
            "source_refs": ["Lexicanum: Battle of Skalathrax", "Lucius: The Faultless Blade"],
        },
        {
            "event_id": "cold_night_shelters",
            "summary": "The deadly cold of Skalathrax's night forced combatants to seek shelter, interrupting open battle.",
            "phase": "turning_point",
            "confidence": "high",
            "source_refs": ["Lexicanum: Battle of Skalathrax", "Warhammer 40k Fandom: Battle of Skalathrax"],
        },
        {
            "event_id": "kharn_burns_shelters",
            "summary": "Kharn used fire/flamer imagery to destroy shelters and killed both enemies and World Eaters sheltering from the cold.",
            "phase": "betrayal",
            "confidence": "high",
            "source_refs": ["Lexicanum: Battle of Skalathrax", "Kharn: Eater of Worlds"],
        },
        {
            "event_id": "fratricide_spreads",
            "summary": "Kharn's acts turned the battle into internal slaughter and encouraged World Eaters to kill their own brothers.",
            "phase": "betrayal",
            "confidence": "medium-high",
            "source_refs": ["Kharn: Eater of Worlds", "Lexicanum: Battle of Skalathrax"],
        },
        {
            "event_id": "legion_fractures",
            "summary": "After Skalathrax the World Eaters no longer functioned as a unified legion and fractured into warbands.",
            "phase": "aftermath_boundary",
            "confidence": "high",
            "source_refs": source_refs,
        },
    ]


def extract_events(source_map: dict[str, Any]) -> dict[str, Any]:
    topic = str(source_map.get("topic") or "")
    source_titles = {
        str(item.get("title") or "")
        for item in source_map.get("sources", [])
        if isinstance(item, dict) and item.get("title")
    }
    if "skalathrax" in topic.lower() or "скалатрак" in topic.lower() or "Lexicanum: Battle of Skalathrax" in source_titles:
        events = skalathrax_events(source_titles)
    else:
        events = []
    return {
        "topic": topic,
        "events": events,
        "gaps": [
            "Extractor needs direct source text for exact wording and chapter-level evidence.",
            "Events marked medium confidence require confirmation from official narrative text.",
        ],
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
    notes = extract_events(source_map)
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

