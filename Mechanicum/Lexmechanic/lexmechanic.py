from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def sandbox_path(workspace_root: Path, path: str) -> Path:
    if not path.startswith("/work/"):
        raise ValueError(f"unsupported sandbox path: {path}")
    return workspace_root / path.removeprefix("/work/")


def known_lore_sources(goal: str) -> list[dict[str, Any]]:
    lowered = goal.lower()
    if "skalathrax" not in lowered and "скалатрак" not in lowered:
        return []
    return [
        {
            "title": "Lexicanum: Battle of Skalathrax",
            "type": "wiki",
            "language": "en",
            "url": "https://wh40k.lexicanum.com/wiki/Battle_of_Skalathrax",
            "reliability": "medium-high",
            "direct_event_detail_level": "high",
            "source_class": "secondary_wiki",
            "expected_use": "baseline chronology and named participants",
        },
        {
            "title": "Lexicanum: Dreagher",
            "type": "wiki",
            "language": "en",
            "url": "https://wh40k.lexicanum.com/wiki/Dreagher",
            "reliability": "medium-high",
            "direct_event_detail_level": "medium",
            "source_class": "secondary_wiki",
            "expected_use": "parley trigger and World Eaters command context",
        },
        {
            "title": "Kharn: Eater of Worlds",
            "type": "novel",
            "language": "en",
            "url": "",
            "reliability": "high",
            "direct_event_detail_level": "high",
            "source_class": "official_primary_narrative",
            "expected_use": "direct narrative around Kharn, Dreagher, parley, Golden Absolute, and the battle",
        },
        {
            "title": "Lucius: The Faultless Blade",
            "type": "novel",
            "language": "en",
            "url": "",
            "reliability": "high",
            "direct_event_detail_level": "medium",
            "source_class": "official_primary_narrative",
            "expected_use": "Emperor's Children and Lucius-side references",
        },
        {
            "title": "The Weakness of Others",
            "type": "short_story",
            "language": "en",
            "url": "",
            "reliability": "high",
            "direct_event_detail_level": "medium",
            "source_class": "official_primary_narrative",
            "expected_use": "supplementary character/event details",
        },
        {
            "title": "White Dwarf 477",
            "type": "magazine",
            "language": "en",
            "url": "https://www.warhammer-community.com/en-gb/articles/JUPjBJqs/white-dwarf-477-preview-the-world-eaters-kill-maim-and-burn-with-brutal-new-rules/",
            "reliability": "high",
            "direct_event_detail_level": "medium",
            "source_class": "official_secondary",
            "expected_use": "official rules/lore context for World Eaters and Skalathrax references",
        },
        {
            "title": "Warhammer 40k Fandom: Battle of Skalathrax",
            "type": "wiki",
            "language": "en",
            "url": "https://warhammer40k.fandom.com/wiki/Battle_of_Skalathrax",
            "reliability": "medium",
            "direct_event_detail_level": "medium",
            "source_class": "secondary_wiki",
            "expected_use": "cross-check public summary; automated fetch may fail with 403",
        },
    ]


def source_map_for_contract(contract: dict[str, Any]) -> dict[str, Any]:
    goal = str(contract.get("goal") or "")
    sources = known_lore_sources(goal)
    search_queries = [
        f"{goal} primary source",
        f"{goal} Lexicanum",
        f"{goal} official source",
    ]
    if "skalathrax" in goal.lower() or "скалатрак" in goal.lower():
        search_queries = [
            "Battle of Skalathrax Kharn Eater of Worlds Dreagher Golden Absolute",
            "Battle of Skalathrax Emperor's Children World Eaters parley moon",
            "Skalathrax Lucius Faultless Blade Weakness of Others White Dwarf 477",
        ]
    return {
        "topic": goal,
        "sources": sources,
        "search_queries": search_queries,
        "coverage_gaps": [
            "Full official book text may be unavailable to automated workers.",
            "Wiki summaries must be treated as secondary sources, not final authority.",
        ],
        "quality_notes": [
            "A pass requires at least one official narrative source candidate and one independent secondary summary.",
            "Community snippets may suggest leads but should not be used as sole evidence for direct events.",
        ],
    }


def run(request: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    contract = request.get("contract")
    step = request.get("step")
    if not isinstance(contract, dict):
        return {"ok": False, "worker": "Lexmechanic", "error": "request.contract must be an object"}
    if not isinstance(step, dict):
        return {"ok": False, "worker": "Lexmechanic", "error": "request.step must be an object"}
    expected_artifacts = step.get("expected_artifacts")
    if not isinstance(expected_artifacts, list) or not expected_artifacts:
        return {"ok": False, "worker": "Lexmechanic", "error": "step.expected_artifacts is empty"}
    output_path = str(expected_artifacts[0])
    source_map = source_map_for_contract(contract)
    host_path = sandbox_path(workspace_root, output_path)
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text(json.dumps(source_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "worker": "Lexmechanic",
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": f"Source map written with {len(source_map['sources'])} source candidates.",
        "artifacts": [output_path],
        "gaps": source_map["coverage_gaps"],
        "confidence": "medium",
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run Lexmechanic on a Worker API request JSON.")
    parser.add_argument("request_json")
    parser.add_argument("--workspace-root", default="runtime/lexmechanic-work")
    args = parser.parse_args()
    payload = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
    request = payload.get("request") if isinstance(payload, dict) and isinstance(payload.get("request"), dict) else payload
    result = run(request, Path(args.workspace_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
