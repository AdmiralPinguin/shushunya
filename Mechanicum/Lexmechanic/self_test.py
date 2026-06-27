#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from lexmechanic import classify_discovered_result, run, source_map_for_contract


def main() -> int:
    fake_search_calls = []

    def fake_search(query: str, limit: int) -> dict:
        fake_search_calls.append((query, limit))
        return {
            "ok": True,
            "source": "fake",
            "results": [
                {
                    "title": "Lexicanum Candidate",
                    "url": "https://wh40k.lexicanum.com/wiki/Candidate",
                    "snippet": "candidate source",
                }
            ],
        }

    discovered = source_map_for_contract({"goal": "unknown topic"}, fake_search)
    if not discovered["sources"] or discovered["sources"][0].get("discovery_method") != "live_search":
        raise AssertionError(f"live discovery should create classified source candidates: {discovered['sources']}")
    if classify_discovered_result({"title": "Bad", "url": "https://example.com/nope"}) is not None:
        raise AssertionError("unknown domains must not become source candidates")
    if not discovered["discovery_results"] or discovered["discovery_results"][0]["provider"] != "fake":
        raise AssertionError(f"fake discovery was not recorded: {discovered['discovery_results']}")
    if not fake_search_calls:
        raise AssertionError("fake searcher was not called")

    request = {
        "task_id": "test-skalathrax:source_discovery",
        "contract": {
            "goal": "Собери все известное о событиях Скалатракса и сделай реконструкцию.",
        },
        "step": {
            "expected_artifacts": ["/work/skalathrax/source_map.json"],
        },
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        result = run(request, Path(temp_dir), searcher=False)
        if not result.get("ok"):
            raise AssertionError(f"Lexmechanic failed: {result}")
        output = Path(temp_dir) / "skalathrax" / "source_map.json"
        if not output.exists():
            raise AssertionError(f"source map was not written: {output}")
        data = json.loads(output.read_text(encoding="utf-8"))
        titles = {item.get("title") for item in data.get("sources", [])}
        required = {"Lexicanum: Battle of Skalathrax", "Kharn: Eater of Worlds"}
        if not required.issubset(titles):
            raise AssertionError(f"source map lacks required source candidates: {titles}")
        if data.get("matched_playbooks") != ["skalathrax_sources"]:
            raise AssertionError(f"wrong matched playbooks: {data.get('matched_playbooks')}")
        if data.get("discovery_status") != "playbook_matched":
            raise AssertionError(f"wrong discovery status: {data.get('discovery_status')}")
        if not data.get("coverage_gaps"):
            raise AssertionError("source map must include coverage gaps")
        generic_request = {
            "task_id": "test-generic:source_discovery",
            "contract": {"goal": "Собери историю неизвестной битвы."},
            "step": {"expected_artifacts": ["/work/generic/source_map.json"]},
        }
        generic_result = run(generic_request, Path(temp_dir), searcher=fake_search)
        if not generic_result.get("ok"):
            raise AssertionError(f"Lexmechanic generic fallback failed: {generic_result}")
        generic = json.loads((Path(temp_dir) / "generic" / "source_map.json").read_text(encoding="utf-8"))
        if not generic.get("sources") or generic["sources"][0].get("source_class") != "secondary_wiki":
            raise AssertionError(f"generic fallback should classify live candidates: {generic['sources']}")
        if generic.get("discovery_status") != "needs_live_discovery":
            raise AssertionError(f"generic fallback should request live discovery: {generic}")
        if not generic.get("discovery_results"):
            raise AssertionError(f"generic fallback should record discovery results: {generic}")
        if not any("live source discovery" in gap for gap in generic.get("coverage_gaps", [])):
            raise AssertionError(f"generic fallback should demand live discovery: {generic}")
    print("[ok] Lexmechanic source map")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
