from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PLAYBOOK_DIR = Path(__file__).resolve().parent / "playbooks"


def load_playbook(name: str) -> dict[str, Any]:
    payload = json.loads((PLAYBOOK_DIR / name).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"source playbook must be an object: {name}")
    return payload


SOURCE_PLAYBOOKS = [load_playbook("skalathrax_sources.json")]


def sandbox_path(workspace_root: Path, path: str) -> Path:
    if not path.startswith("/work/"):
        raise ValueError(f"unsupported sandbox path: {path}")
    return workspace_root / path.removeprefix("/work/")


def matching_playbooks(goal: str) -> list[dict[str, Any]]:
    lowered = goal.lower()
    matches = []
    for playbook in SOURCE_PLAYBOOKS:
        terms = [str(term).lower() for term in playbook.get("match_terms", [])]
        if any(term in lowered for term in terms):
            matches.append(playbook)
    return matches


def dedupe_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source in sources:
        key = (str(source.get("title") or ""), str(source.get("url") or ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(source)
    return result


def generic_search_queries(goal: str) -> list[str]:
    return [
        f"{goal} primary source",
        f"{goal} official source",
        f"{goal} wiki",
        f"{goal} chronology",
    ]


def source_map_for_contract(contract: dict[str, Any]) -> dict[str, Any]:
    goal = str(contract.get("goal") or "")
    playbooks = matching_playbooks(goal)
    sources = dedupe_sources(
        [
            source
            for playbook in playbooks
            for source in playbook.get("sources", [])
            if isinstance(source, dict)
        ]
    )
    search_queries = [
        str(query)
        for playbook in playbooks
        for query in playbook.get("search_queries", [])
        if query
    ] or generic_search_queries(goal)
    coverage_gaps = [
        str(gap)
        for playbook in playbooks
        for gap in playbook.get("coverage_gaps", [])
        if gap
    ]
    if not playbooks:
        coverage_gaps.append("No source playbook matched this task; live source discovery is required.")
    discovery_status = "playbook_matched" if playbooks else "needs_live_discovery"
    quality_notes = [
        str(note)
        for playbook in playbooks
        for note in playbook.get("quality_notes", [])
        if note
    ] or [
        "A pass requires at least one reliable primary or official source candidate.",
        "Secondary summaries can guide discovery but must not become sole evidence.",
    ]
    return {
        "topic": goal,
        "sources": sources,
        "search_queries": search_queries,
        "discovery_status": discovery_status,
        "matched_playbooks": [str(playbook.get("name") or playbook.get("match_terms", ["unknown"])[0]) for playbook in playbooks],
        "coverage_gaps": coverage_gaps,
        "quality_notes": quality_notes,
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
