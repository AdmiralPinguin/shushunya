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
                },
                {
                    "title": "Official Candidate",
                    "url": "https://www.warhammer-community.com/en-gb/articles/candidate/",
                    "snippet": "official source",
                },
                {
                    "title": "Community Candidate",
                    "url": "https://warhammer40k.fandom.com/wiki/Candidate",
                    "snippet": "community source",
                },
            ],
        }

    def weak_search(query: str, limit: int) -> dict:
        return {
            "ok": True,
            "source": "fake",
            "results": [
                {
                    "title": "Only Community Candidate",
                    "url": "https://warhammer40k.fandom.com/wiki/Only_Community_Candidate",
                    "snippet": "community source",
                }
            ],
        }

    def noisy_playbook_search(query: str, limit: int) -> dict:
        return {
            "ok": True,
            "source": "fake",
            "results": [
                {
                    "title": "Chaos Artefacts",
                    "url": "https://wh40k.lexicanum.com/wiki/Axe_of_Blind_Fury",
                    "snippet": "A general artefact page without the requested battle.",
                },
                {
                    "title": "Black Library - Weakness of Others, The (eShort)",
                    "url": "https://www.blacklibrary.com/warhammer-40000/quick-reads/the-weakness-of-others-ebook.html",
                    "snippet": "The Weakness of Others is a known source from the playbook.",
                },
            ],
        }

    discovered = source_map_for_contract({"goal": "unknown topic"}, fake_search)
    if not discovered["sources"] or discovered["sources"][0].get("discovery_method") != "live_search":
        raise AssertionError(f"live discovery should create classified source candidates: {discovered['sources']}")
    if discovered["sources"][0].get("source_type") != "official_article":
        raise AssertionError(f"official live sources should rank first: {discovered['sources']}")
    if not discovered["sources"][0].get("ranking_reasons"):
        raise AssertionError(f"ranked live sources should explain their rank: {discovered['sources']}")
    community = next((source for source in discovered["sources"] if source.get("source_class") == "community_wiki"), {})
    if community.get("source_type") != "community_wiki" or community.get("source_rank", 0) >= discovered["sources"][0].get("source_rank", 0):
        raise AssertionError(f"community wiki should be classified and ranked below official sources: {discovered['sources']}")
    if classify_discovered_result({"title": "Bad", "url": "https://example.com/nope"}) is not None:
        raise AssertionError("unknown domains must not become source candidates")
    if not discovered["discovery_results"] or discovered["discovery_results"][0]["provider"] != "fake":
        raise AssertionError(f"fake discovery was not recorded: {discovered['discovery_results']}")
    if len(fake_search_calls) < 4:
        raise AssertionError("fake searcher was not called")
    if not discovered.get("discovery_rounds") or discovered["discovery_rounds"][0].get("round") != "primary_probe":
        raise AssertionError(f"discovery rounds should expose source-search strategy: {discovered.get('discovery_rounds')}")
    if not discovered.get("source_coverage", {}).get("ready_for_extraction"):
        raise AssertionError(f"source coverage should mark official/wiki fallback as extraction-ready: {discovered.get('source_coverage')}")

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
        if data.get("topic") != "Skalathrax" or "Максимально" in data.get("topic", ""):
            raise AssertionError(f"playbook source map should use normalized topic: {data.get('topic')}")
        if data.get("discovery_status") != "playbook_matched":
            raise AssertionError(f"wrong discovery status: {data.get('discovery_status')}")
        if not data.get("coverage_gaps"):
            raise AssertionError("source map must include coverage gaps")
        if not data.get("source_coverage", {}).get("has_primary_or_publication"):
            raise AssertionError(f"playbook source coverage should detect primary/publication sources: {data.get('source_coverage')}")
        generic_request = {
            "task_id": "test-generic:source_discovery",
            "contract": {"goal": "Собери историю неизвестной битвы."},
            "step": {"expected_artifacts": ["/work/generic/source_map.json"]},
        }
        generic_result = run(generic_request, Path(temp_dir), searcher=fake_search)
        if not generic_result.get("ok"):
            raise AssertionError(f"Lexmechanic generic fallback failed: {generic_result}")
        generic = json.loads((Path(temp_dir) / "generic" / "source_map.json").read_text(encoding="utf-8"))
        if not generic.get("sources") or generic["sources"][0].get("source_type") != "official_article":
            raise AssertionError(f"generic fallback should classify live candidates: {generic['sources']}")
        if generic.get("discovery_status") != "needs_live_discovery":
            raise AssertionError(f"generic fallback should request live discovery: {generic}")
        if not generic.get("discovery_results"):
            raise AssertionError(f"generic fallback should record discovery results: {generic}")
        if not generic.get("discovery_rounds") or len(generic.get("discovery_rounds", [])) < 2:
            raise AssertionError(f"generic fallback should record multiple discovery rounds: {generic}")
        if not generic.get("source_coverage", {}).get("ready_for_extraction"):
            raise AssertionError(f"generic fallback should expose extraction-ready coverage: {generic.get('source_coverage')}")
        if not any("live source discovery" in gap for gap in generic.get("coverage_gaps", [])):
            raise AssertionError(f"generic fallback should demand live discovery: {generic}")
        blocked_request = {
            "task_id": "test-blocked:source_discovery",
            "contract": {"goal": "Собери историю неизвестной битвы без источников."},
            "step": {"expected_artifacts": ["/work/blocked/source_map.json"]},
        }
        blocked_result = run(blocked_request, Path(temp_dir), searcher=False)
        if (
            blocked_result.get("ok")
            or blocked_result.get("status") != "blocked"
            or not blocked_result.get("revision_plan", {}).get("required")
            or not (Path(temp_dir) / "blocked" / "source_map.json").exists()
        ):
            raise AssertionError(f"generic source discovery without live search should block with diagnostics: {blocked_result}")
        weak_request = {
            "task_id": "test-weak:source_discovery",
            "contract": {"goal": "Собери историю сомнительной битвы."},
            "step": {"expected_artifacts": ["/work/weak/source_map.json"]},
        }
        weak_result = run(weak_request, Path(temp_dir), searcher=weak_search)
        if (
            weak_result.get("ok")
            or weak_result.get("status") != "blocked"
            or "not extraction-ready" not in weak_result.get("summary", "")
            or not (Path(temp_dir) / "weak" / "source_map.json").exists()
        ):
            raise AssertionError(f"weak source discovery should block before downstream work: {weak_result}")
        weak_map = json.loads((Path(temp_dir) / "weak" / "source_map.json").read_text(encoding="utf-8"))
        if weak_map.get("source_coverage", {}).get("ready_for_extraction"):
            raise AssertionError(f"weak source map should record failed source coverage: {weak_map}")
        long_goal = "Максимально полно реконструируй непосредственные события Скалатракса " + ("очень длинное задание " * 20)
        live_playbook = source_map_for_contract({"goal": long_goal}, fake_search)
        live_queries = [query for round_plan in live_playbook.get("discovery_rounds", []) for query in round_plan.get("queries", [])]
        if any("очень длинное задание" in query for query in live_queries):
            raise AssertionError(f"live discovery queries should use normalized topic, not full prompt: {live_queries}")
        urls = [source.get("url") for source in live_playbook.get("sources", []) if source.get("url")]
        if len(urls) != len(set(urls)):
            raise AssertionError(f"source dedupe should collapse duplicate live/playbook URLs: {live_playbook.get('sources')}")
        noisy = source_map_for_contract({"goal": "Собери события Скалатракса"}, noisy_playbook_search)
        noisy_titles = {source.get("title") for source in noisy.get("sources", [])}
        if "Chaos Artefacts" in noisy_titles or "Black Library - Weakness of Others, The (eShort)" not in noisy_titles:
            raise AssertionError(f"playbook live discovery should filter irrelevant same-domain noise: {noisy.get('sources')}")
    print("[ok] Lexmechanic source map")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
