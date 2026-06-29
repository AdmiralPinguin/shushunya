from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


PLAYBOOK_DIR = Path(__file__).resolve().parent / "playbooks"
SHUSHUNYA_AGENT_DIR = Path(__file__).resolve().parents[1] / "ShushunyaAgent"
if str(SHUSHUNYA_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(SHUSHUNYA_AGENT_DIR))

from shushunya_agent.web_tools import web_search  # noqa: E402


class SearchConfig:
    max_tool_output_chars = 12000


SearchFn = Callable[[str, int], dict[str, Any]]


def load_playbook(name: str) -> dict[str, Any]:
    payload = json.loads((PLAYBOOK_DIR / name).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"source playbook must be an object: {name}")
    return payload


SOURCE_PLAYBOOKS = [load_playbook("skalathrax_sources.json")]
LIVE_DISCOVERY_ENABLED = os.environ.get("LEXMECHANIC_LIVE_DISCOVERY", "0").strip().lower() in {"1", "true", "yes", "on"}


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
    seen: set[str] = set()
    for source in sources:
        url = str(source.get("url") or "").strip().lower()
        title = " ".join(str(source.get("title") or "").split()).lower()
        key = url or title
        if key in seen:
            continue
        seen.add(key)
        result.append(source)
    return result


def source_type(source: dict[str, Any]) -> str:
    host = (urlparse(str(source.get("url") or "")).hostname or "").lower()
    kind = str(source.get("type") or "").lower()
    source_class = str(source.get("source_class") or "").lower()
    if kind in {"novel", "codex", "campaign_book", "short_story", "book"}:
        return "published_primary"
    if "blacklibrary.com" in host:
        return "official_catalog"
    if host.endswith("warhammer-community.com") or host.endswith("warhammer.com"):
        return "official_article"
    if host.endswith("lexicanum.com"):
        return "curated_wiki"
    if host.endswith("fandom.com"):
        return "community_wiki"
    if "official" in source_class:
        return "official_secondary"
    if "wiki" in kind or "wiki" in source_class:
        return "wiki"
    return "unclassified"


def ranked_source(source: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(source)
    kind = source_type(enriched)
    reliability = str(enriched.get("reliability") or "").lower()
    detail = str(enriched.get("direct_event_detail_level") or "").lower()
    class_score = {
        "published_primary": 100,
        "official_catalog": 78,
        "official_article": 76,
        "official_secondary": 72,
        "curated_wiki": 62,
        "wiki": 52,
        "community_wiki": 42,
        "unclassified": 20,
    }.get(kind, 20)
    reliability_score = {
        "high": 20,
        "medium-high": 15,
        "medium": 10,
        "low": -10,
    }.get(reliability, 0)
    detail_score = {
        "high": 15,
        "medium-high": 11,
        "medium": 8,
        "low": -5,
    }.get(detail, 0)
    score = class_score + reliability_score + detail_score
    reasons = [kind]
    if reliability:
        reasons.append(f"reliability:{reliability}")
    if detail:
        reasons.append(f"event_detail:{detail}")
    enriched["source_type"] = kind
    enriched["source_rank"] = score
    enriched["ranking_reasons"] = reasons
    return enriched


def rank_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = [ranked_source(source) for source in sources]
    return sorted(enriched, key=lambda source: int(source.get("source_rank") or 0), reverse=True)


def classify_discovered_result(result: dict[str, Any]) -> dict[str, Any] | None:
    url = str(result.get("url") or "").strip()
    title = " ".join(str(result.get("title") or "").split())
    if not url or not title:
        return None
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("lexicanum.com"):
        return {
            "title": title,
            "type": "wiki",
            "language": "unknown",
            "url": url,
            "reliability": "medium-high",
            "direct_event_detail_level": "unknown",
            "source_class": "secondary_wiki",
            "expected_use": "live-discovered chronology or named-entity lead; verify before final use",
            "discovery_method": "live_search",
        }
    if host.endswith("warhammer40k.fandom.com") or host.endswith("fandom.com"):
        return {
            "title": title,
            "type": "wiki",
            "language": "unknown",
            "url": url,
            "reliability": "medium",
            "direct_event_detail_level": "unknown",
            "source_class": "community_wiki",
            "expected_use": "live-discovered community summary; use only as a lead for stronger sources",
            "discovery_method": "live_search",
        }
    if host.endswith("warhammer-community.com"):
        return {
            "title": title,
            "type": "article",
            "language": "unknown",
            "url": url,
            "reliability": "high",
            "direct_event_detail_level": "unknown",
            "source_class": "official_secondary",
            "expected_use": "live-discovered official context; verify relevance before final use",
            "discovery_method": "live_search",
        }
    if host.endswith("blacklibrary.com") or host.endswith("warhammer.com"):
        return {
            "title": title,
            "type": "official_catalog",
            "language": "unknown",
            "url": url,
            "reliability": "high",
            "direct_event_detail_level": "low",
            "source_class": "official_secondary",
            "expected_use": "live-discovered official publication or product lead; verify narrative detail elsewhere",
            "discovery_method": "live_search",
        }
    return None


def generic_search_queries(goal: str) -> list[str]:
    return [
        f"{goal} primary source",
        f"{goal} official source",
        f"{goal} wiki",
        f"{goal} chronology",
    ]


def search_rounds(goal: str, playbooks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    playbook_queries = [
        str(query)
        for playbook in playbooks
        for query in playbook.get("search_queries", [])
        if query
    ]
    if playbook_queries:
        return [
            {"round": "playbook_seed", "purpose": "known high-value queries from matched source playbooks", "queries": playbook_queries},
            {
                "round": "official_crosscheck",
                "purpose": "official publisher, catalog, or article leads for source arbitration",
                "queries": [f"{goal} Black Library", f"{goal} Warhammer official", f"{goal} Games Workshop"],
            },
            {
                "round": "summary_crosscheck",
                "purpose": "secondary summaries for chronology and named-entity cross-checking",
                "queries": [f"{goal} Lexicanum", f"{goal} Warhammer wiki", f"{goal} chronology"],
            },
            {
                "round": "language_probe",
                "purpose": "find Russian and English variants of the same topic",
                "queries": [f"{goal} русский", f"{goal} English", f"{goal} перевод"],
            },
        ]
    return [
        {"round": "primary_probe", "purpose": "find primary or publication-level sources", "queries": [f"{goal} primary source", f"{goal} novel", f"{goal} book"]},
        {"round": "official_probe", "purpose": "find official publisher or article sources", "queries": [f"{goal} official source", f"{goal} Black Library", f"{goal} Warhammer official"]},
        {"round": "summary_probe", "purpose": "find secondary summaries for cross-checking only", "queries": [f"{goal} wiki", f"{goal} Lexicanum", f"{goal} chronology"]},
        {"round": "language_probe", "purpose": "find Russian and English variants of the same topic", "queries": [f"{goal} русский", f"{goal} English", f"{goal} перевод"]},
    ]


def default_search(query: str, limit: int) -> dict[str, Any]:
    return web_search(SearchConfig(), query, limit)


def run_discovery_queries(search_queries: list[str], searcher: SearchFn | None, limit: int = 5) -> list[dict[str, Any]]:
    if searcher is None:
        return []
    results: list[dict[str, Any]] = []
    for query in search_queries[:4]:
        try:
            payload = searcher(query, limit)
        except Exception as exc:  # noqa: BLE001 - discovery failures are recorded as source-map data.
            payload = {"ok": False, "error": str(exc)}
        results.append(
            {
                "query": query,
                "ok": bool(payload.get("ok")),
                "provider": payload.get("source") or payload.get("provider", ""),
                "results": payload.get("results", [])[:limit] if isinstance(payload.get("results"), list) else [],
                "error": payload.get("error", ""),
            }
        )
    return results


def run_discovery_rounds(rounds: list[dict[str, Any]], searcher: SearchFn | None, limit: int = 5, query_budget: int = 10) -> list[dict[str, Any]]:
    if searcher is None:
        return []
    results: list[dict[str, Any]] = []
    used_queries = 0
    for round_plan in rounds:
        queries = round_plan.get("queries") if isinstance(round_plan.get("queries"), list) else []
        round_results = run_discovery_queries(
            [str(query) for query in queries if query][: max(0, query_budget - used_queries)],
            searcher,
            limit,
        )
        used_queries += len(round_results)
        results.append(
            {
                "round": str(round_plan.get("round") or "discovery"),
                "purpose": str(round_plan.get("purpose") or ""),
                "queries": [item.get("query", "") for item in round_results],
                "results": round_results,
            }
        )
        if used_queries >= query_budget:
            break
    return results


def flatten_discovery_results(discovery_rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for round_result in discovery_rounds:
        for query_result in round_result.get("results", []):
            if isinstance(query_result, dict):
                enriched = dict(query_result)
                enriched["round"] = round_result.get("round", "")
                flattened.append(enriched)
    return flattened


def classified_live_sources(discovery_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for discovery in discovery_results:
        for result in discovery.get("results", []):
            if not isinstance(result, dict):
                continue
            candidate = classify_discovered_result(result)
            if candidate:
                candidates.append(candidate)
    return rank_sources(dedupe_sources(candidates))


def source_coverage(sources: list[dict[str, Any]], discovery_results: list[dict[str, Any]], playbooks: list[dict[str, Any]]) -> dict[str, Any]:
    source_types = [str(source.get("source_type") or source_type(source)) for source in sources if isinstance(source, dict)]
    source_classes = [str(source.get("source_class") or source.get("type") or "") for source in sources if isinstance(source, dict)]
    fetched_query_count = len(discovery_results)
    successful_query_count = sum(1 for item in discovery_results if item.get("ok"))
    has_primary = any(kind == "published_primary" or "primary" in source_class for kind, source_class in zip(source_types, source_classes))
    has_official = any(kind in {"published_primary", "official_catalog", "official_article", "official_secondary"} for kind in source_types)
    has_secondary = any(kind in {"curated_wiki", "wiki", "community_wiki"} for kind in source_types)
    live_count = sum(1 for source in sources if source.get("discovery_method") == "live_search")
    return {
        "source_count": len(sources),
        "matched_playbook_count": len(playbooks),
        "live_candidate_count": live_count,
        "query_count": fetched_query_count,
        "successful_query_count": successful_query_count,
        "has_primary_or_publication": has_primary,
        "has_official": has_official,
        "has_secondary_crosscheck": has_secondary,
        "ready_for_extraction": bool(sources) and (has_primary or has_official) and has_secondary,
        "source_types": sorted(set(source_types)),
    }


def source_map_for_contract(contract: dict[str, Any], searcher: SearchFn | None = None) -> dict[str, Any]:
    goal = str(contract.get("goal") or "")
    playbooks = matching_playbooks(goal)
    topic = next((str(playbook.get("topic") or "") for playbook in playbooks if playbook.get("topic")), goal)
    sources = dedupe_sources(
        [
            source
            for playbook in playbooks
            for source in playbook.get("sources", [])
            if isinstance(source, dict)
        ]
    )
    rounds = search_rounds(topic, playbooks)
    search_queries = [query for round_plan in rounds for query in round_plan.get("queries", []) if isinstance(query, str)]
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
    discovery_rounds = run_discovery_rounds(rounds, searcher)
    discovery_results = flatten_discovery_results(discovery_rounds)
    live_candidates = classified_live_sources(discovery_results)
    sources = rank_sources(dedupe_sources(sources + live_candidates))
    coverage = source_coverage(sources, discovery_results, playbooks)
    if sources and not coverage["ready_for_extraction"]:
        coverage_gaps.append("Source set is not extraction-ready: it needs both official/primary evidence and secondary cross-checking.")
    return {
        "topic": topic,
        "original_goal": goal,
        "sources": sources,
        "search_queries": search_queries,
        "discovery_rounds": discovery_rounds,
        "discovery_status": discovery_status,
        "discovery_results": discovery_results,
        "live_source_candidates": live_candidates,
        "source_coverage": coverage,
        "matched_playbooks": [str(playbook.get("name") or playbook.get("match_terms", ["unknown"])[0]) for playbook in playbooks],
        "coverage_gaps": coverage_gaps,
        "quality_notes": quality_notes,
    }


def configured_searcher() -> SearchFn | None:
    return default_search if LIVE_DISCOVERY_ENABLED else None


def run(request: dict[str, Any], workspace_root: Path, searcher: SearchFn | None | bool = None) -> dict[str, Any]:
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
    selected_searcher = configured_searcher() if searcher is None else searcher
    if selected_searcher is False:
        selected_searcher = None
    source_map = source_map_for_contract(contract, selected_searcher)
    host_path = sandbox_path(workspace_root, output_path)
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text(json.dumps(source_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    source_count = len(source_map["sources"])
    source_coverage = source_map.get("source_coverage") if isinstance(source_map.get("source_coverage"), dict) else {}
    if source_count == 0 or not source_coverage.get("ready_for_extraction"):
        if source_count == 0:
            summary = "Source discovery found no source candidates."
            reason = "Source discovery found no source candidates; enable live discovery or add a source playbook."
        else:
            summary = "Source discovery found candidates, but source coverage is not extraction-ready."
            reason = "Source coverage lacks official/primary evidence or secondary cross-checking; expand discovery before extraction."
        return {
            "ok": False,
            "worker": "Lexmechanic",
            "task_id": request.get("task_id"),
            "status": "blocked",
            "summary": summary,
            "artifacts": [output_path],
            "gaps": source_map["coverage_gaps"],
            "revision_plan": {
                "required": True,
                "steps": [
                    {
                        "step_id": "source_discovery",
                        "worker": "Lexmechanic",
                        "reason": reason,
                        "source": "source_discovery",
                        "priority": "blocker",
                    }
                ],
            },
            "confidence": "low",
        }
    return {
        "ok": True,
        "worker": "Lexmechanic",
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": f"Source map written with {source_count} source candidates.",
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
