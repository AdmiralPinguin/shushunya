from __future__ import annotations

import json
import os
import re
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


def load_playbook(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"source playbook must be an object: {path.name}")
    return payload


def load_source_playbooks() -> list[dict[str, Any]]:
    playbooks: list[dict[str, Any]] = []
    if not PLAYBOOK_DIR.exists():
        return playbooks
    for path in sorted(PLAYBOOK_DIR.glob("*.json")):
        try:
            playbooks.append(load_playbook(path))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return playbooks


SOURCE_PLAYBOOKS = load_source_playbooks()
LIVE_DISCOVERY_ENABLED = os.environ.get("LEXMECHANIC_LIVE_DISCOVERY", "0").strip().lower() in {"1", "true", "yes", "on"}
SOURCE_CACHE_DIR = os.environ.get("LEXMECHANIC_SOURCE_CACHE_DIR", "").strip()
STRONG_COMPREHENSIVE_GOAL_TERMS = [
    "максимально полно",
    "до последней",
    "не ограничивай объем",
    "full reconstruction",
    "complete reconstruction",
    "exhaustive",
]
WEAK_COMPREHENSIVE_GOAL_TERMS = [
    "вся доступная",
    "все доступные",
    "все известн",
    "all available",
    "all known",
    "не краткая справка",
    "исследовательская реконструкция",
]


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


def cache_key(topic: str) -> str:
    key = re.sub(r"[^a-zа-я0-9]+", "-", topic.lower()).strip("-")
    return key or "default"


def source_cache_path(topic: str) -> Path | None:
    if not SOURCE_CACHE_DIR:
        return None
    return Path(SOURCE_CACHE_DIR) / f"{cache_key(topic)}.json"


def cached_sources_for_topic(topic: str) -> list[dict[str, Any]]:
    path = source_cache_path(topic)
    if not path or not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    sources = payload.get("sources") if isinstance(payload, dict) else []
    source_items = sources if isinstance(sources, list) else []
    result: list[dict[str, Any]] = []
    for source in source_items:
        if not isinstance(source, dict):
            continue
        cached = dict(source)
        if cached.get("discovery_method") == "live_search":
            cached["discovery_method"] = "cached_live_search"
        result.append(cached)
    return result


def write_source_cache(topic: str, sources: list[dict[str, Any]]) -> None:
    path = source_cache_path(topic)
    if not path:
        return
    cacheable = [source for source in sources if isinstance(source, dict) and source.get("url")]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"topic": topic, "sources": cacheable}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def dedupe_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        url = str(source.get("url") or "").strip().lower()
        local_path = str(source.get("local_path") or "").strip().lower()
        title = " ".join(str(source.get("title") or "").split()).lower()
        key = url or local_path or title
        if key in seen:
            continue
        seen.add(key)
        result.append(source)
    return result


def source_type(source: dict[str, Any]) -> str:
    host = (urlparse(str(source.get("url") or "")).hostname or "").lower()
    kind = str(source.get("type") or "").lower()
    source_class = str(source.get("source_class") or "").lower()
    if source.get("local_path") or source_class == "local_primary_candidate":
        return "local_primary"
    if kind in {"novel", "codex", "campaign_book", "short_story", "book"}:
        return "published_primary"
    if "primary" in source_class and kind in {"extract", "excerpt"}:
        return "official_primary_extract"
    if "blacklibrary.com" in host:
        return "official_catalog"
    if host.endswith("warhammer-community.com") or host.endswith("warhammer.com"):
        return "official_article"
    if host.endswith("lexicanum.com"):
        return "curated_wiki"
    if host.endswith("fandom.com"):
        return "community_wiki"
    if host.endswith("reddit.com"):
        return "community_excerpt"
    if host.endswith("miraheze.org") or "1d6chan" in host:
        return "community_wiki_low"
    if host.endswith("goodreads.com") or host.endswith("amazon.com"):
        return "catalog_or_review"
    if any(domain in host for domain in ["wargamer.com", "belloflostsouls.net", "wordpress.com", "blogspot.com"]):
        return "review_or_blog"
    if host.endswith("youtube.com") or host.endswith("youtu.be"):
        return "video_or_transcript_lead"
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
        "local_primary": 96,
        "official_primary_extract": 88,
        "official_catalog": 78,
        "official_article": 76,
        "official_secondary": 72,
        "curated_wiki": 62,
        "wiki": 52,
        "community_wiki": 42,
        "community_excerpt": 40,
        "review_or_blog": 34,
        "catalog_or_review": 32,
        "video_or_transcript_lead": 28,
        "community_wiki_low": 25,
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
        if "/downloads/product/" in url.lower() and "extract" in url.lower():
            return {
                "title": title,
                "type": "extract",
                "language": "unknown",
                "url": url,
                "reliability": "high",
                "direct_event_detail_level": "medium",
                "source_class": "official_primary_extract",
                "expected_use": "official free extract from a primary publication; use as direct wording only within excerpt scope",
                "discovery_method": "live_search",
            }
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
    if host.endswith("reddit.com"):
        return {
            "title": title,
            "type": "discussion_excerpt",
            "language": "unknown",
            "url": url,
            "reliability": "medium",
            "direct_event_detail_level": "medium",
            "source_class": "community_excerpt",
            "expected_use": "public discussion or excerpt lead; verify against official sources before narrative use",
            "discovery_method": "live_search",
        }
    if host.endswith("miraheze.org") or "1d6chan" in host:
        return {
            "title": title,
            "type": "wiki",
            "language": "unknown",
            "url": url,
            "reliability": "low",
            "direct_event_detail_level": "medium",
            "source_class": "community_wiki_low",
            "expected_use": "low-reliability lore summary; use only for lead discovery and disagreement checks",
            "discovery_method": "live_search",
        }
    if host.endswith("goodreads.com") or host.endswith("amazon.com"):
        return {
            "title": title,
            "type": "catalog_or_review",
            "language": "unknown",
            "url": url,
            "reliability": "medium",
            "direct_event_detail_level": "low",
            "source_class": "catalog_or_review",
            "expected_use": "publication metadata or review lead; not direct event authority",
            "discovery_method": "live_search",
        }
    if any(domain in host for domain in ["wargamer.com", "belloflostsouls.net", "wordpress.com", "blogspot.com"]):
        return {
            "title": title,
            "type": "review_or_blog",
            "language": "unknown",
            "url": url,
            "reliability": "medium-low",
            "direct_event_detail_level": "low",
            "source_class": "review_or_blog",
            "expected_use": "review or lore article lead; use for source discovery and comparison only",
            "discovery_method": "live_search",
        }
    if host.endswith("youtube.com") or host.endswith("youtu.be"):
        return {
            "title": title,
            "type": "video_or_transcript_lead",
            "language": "unknown",
            "url": url,
            "reliability": "medium-low",
            "direct_event_detail_level": "low",
            "source_class": "video_or_transcript_lead",
            "expected_use": "video metadata or transcript lead; requires transcript extraction before narrative use",
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


def depth_profile_for_goal(goal: str, playbooks: list[dict[str, Any]]) -> dict[str, Any]:
    lowered = goal.lower()
    weak_hits = [term for term in WEAK_COMPREHENSIVE_GOAL_TERMS if term in lowered]
    comprehensive = any(term in lowered for term in STRONG_COMPREHENSIVE_GOAL_TERMS) or len(weak_hits) >= 2
    if comprehensive:
        return {
            "mode": "comprehensive",
            "reason": "goal requests maximal or exhaustive coverage",
            "query_budget": 28 if playbooks else 24,
            "per_query_limit": 8,
            "per_round_query_limit": 8,
            "min_source_count": 24 if playbooks else 12,
            "min_live_candidate_count": 8 if playbooks else 6,
            "min_direct_evidence_sources": 6,
            "min_primary_evidence_sources": 1,
            "min_draft_chars": 60000,
        }
    return {
        "mode": "standard",
        "reason": "default research depth",
        "query_budget": 10,
        "per_query_limit": 5,
        "per_round_query_limit": 4,
        "min_source_count": 1,
        "min_live_candidate_count": 0,
        "min_direct_evidence_sources": 1,
        "min_primary_evidence_sources": 0,
        "min_draft_chars": 0,
    }


def playbook_source_title_queries(playbooks: list[dict[str, Any]]) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()
    for playbook in playbooks:
        for source in playbook.get("sources", []):
            if not isinstance(source, dict):
                continue
            source_class = str(source.get("source_class") or source.get("type") or "").lower()
            if "primary" not in source_class and str(source.get("type") or "").lower() not in {"novel", "short_story", "book"}:
                continue
            title = " ".join(str(source.get("title") or "").split())
            if not title:
                continue
            for query in (f'"{title}" Black Library', f'"{title}" Warhammer'):
                key = query.lower()
                if key not in seen:
                    seen.add(key)
                    queries.append(query)
    return queries


def deep_context_queries(goal: str, playbooks: list[dict[str, Any]]) -> list[str]:
    queries = [
        f"{goal} excerpt",
        f"{goal} sources",
        f"{goal} reddit excerpt",
        f"{goal} lore discussion",
    ]
    for playbook in playbooks:
        for source in playbook.get("sources", []):
            if not isinstance(source, dict):
                continue
            title = " ".join(str(source.get("title") or "").split())
            if not title:
                continue
            queries.extend([f'"{title}" excerpt', f'"{title}" review', f'"{title}" {goal}'])
    deduped: list[str] = []
    seen: set[str] = set()
    for query in queries:
        key = query.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(query)
    return deduped


def search_rounds(goal: str, playbooks: list[dict[str, Any]], depth_profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    playbook_queries = [
        str(query)
        for playbook in playbooks
        for query in playbook.get("search_queries", [])
        if query
    ]
    if playbook_queries:
        title_queries = playbook_source_title_queries(playbooks)
        depth_profile = depth_profile or {}
        rounds = [
            {"round": "playbook_seed", "purpose": "known high-value queries from matched source playbooks", "queries": playbook_queries},
        ]
        if title_queries:
            rounds.append(
                {
                    "round": "source_title_probe",
                    "purpose": "official catalog or publication pages for named primary sources",
                    "queries": title_queries,
                }
            )
        if depth_profile.get("mode") == "comprehensive":
            rounds.append(
                {
                    "round": "deep_context_probe",
                    "purpose": "public excerpts, reviews, discussions, and tertiary leads for exhaustive source mapping",
                    "queries": deep_context_queries(goal, playbooks),
                }
            )
        rounds.extend(
            [
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
        )
        return rounds
    return [
        {"round": "primary_probe", "purpose": "find primary or publication-level sources", "queries": [f"{goal} primary source", f"{goal} novel", f"{goal} book"]},
        {"round": "official_probe", "purpose": "find official publisher or article sources", "queries": [f"{goal} official source", f"{goal} Black Library", f"{goal} Warhammer official"]},
        {"round": "summary_probe", "purpose": "find secondary summaries for cross-checking only", "queries": [f"{goal} wiki", f"{goal} Lexicanum", f"{goal} chronology"]},
        {"round": "language_probe", "purpose": "find Russian and English variants of the same topic", "queries": [f"{goal} русский", f"{goal} English", f"{goal} перевод"]},
    ]


def default_search(query: str, limit: int) -> dict[str, Any]:
    return web_search(SearchConfig(), query, limit)


def run_discovery_queries(search_queries: list[str], searcher: SearchFn | None, limit: int = 5, query_limit: int = 4) -> list[dict[str, Any]]:
    if searcher is None:
        return []
    results: list[dict[str, Any]] = []
    for query in search_queries[:query_limit]:
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


def run_discovery_rounds(
    rounds: list[dict[str, Any]],
    searcher: SearchFn | None,
    limit: int = 5,
    query_budget: int = 10,
    per_round_query_limit: int = 4,
) -> list[dict[str, Any]]:
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
            per_round_query_limit,
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


def relevance_tokens(text: str) -> set[str]:
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "warhammer",
        "black",
        "library",
        "lexicanum",
        "wiki",
        "fandom",
        "official",
        "source",
    }
    return {token for token in re.findall(r"[a-zа-я0-9]+", text.lower()) if len(token) > 2 and token not in stopwords}


def term_matches_haystack(term: str, haystack: str, haystack_tokens: set[str]) -> bool:
    normalized_term = " ".join(term.lower().split())
    if normalized_term and normalized_term in haystack:
        return True
    tokens = relevance_tokens(term)
    if not tokens:
        return False
    required = min(len(tokens), 2)
    return len(tokens & haystack_tokens) >= required


def relevant_live_result(result: dict[str, Any], relevance_terms: list[str] | None) -> bool:
    if not relevance_terms:
        return True
    haystack = " ".join(
        [
            str(result.get("title") or ""),
            str(result.get("url") or ""),
            str(result.get("snippet") or ""),
        ]
    ).lower()
    haystack_tokens = relevance_tokens(haystack)
    return any(term_matches_haystack(term, haystack, haystack_tokens) for term in relevance_terms if term)


def classified_live_sources(discovery_results: list[dict[str, Any]], relevance_terms: list[str] | None = None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for discovery in discovery_results:
        for result in discovery.get("results", []):
            if not isinstance(result, dict):
                continue
            if not relevant_live_result(result, relevance_terms):
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
    has_primary = any(kind in {"published_primary", "local_primary"} or "primary" in source_class for kind, source_class in zip(source_types, source_classes))
    has_official = any(kind in {"published_primary", "local_primary", "official_catalog", "official_article", "official_secondary"} for kind in source_types)
    has_secondary = any(kind in {"curated_wiki", "wiki", "community_wiki"} for kind in source_types)
    live_count = sum(1 for source in sources if source.get("discovery_method") in {"live_search", "cached_live_search"})
    return {
        "source_count": len(sources),
        "matched_playbook_count": len(playbooks),
        "live_candidate_count": live_count,
        "local_corpus_source_count": sum(1 for source in sources if source.get("discovery_method") == "local_corpus"),
        "query_count": fetched_query_count,
        "successful_query_count": successful_query_count,
        "has_primary_or_publication": has_primary,
        "has_official": has_official,
        "has_secondary_crosscheck": has_secondary,
        "ready_for_extraction": bool(sources) and (has_primary or has_official) and has_secondary,
        "source_types": sorted(set(source_types)),
    }


def local_primary_tokens(sources: list[dict[str, Any]]) -> list[set[str]]:
    tokens: list[set[str]] = []
    for source in sources:
        if not isinstance(source, dict) or not str(source.get("local_path") or "").strip():
            continue
        tokens.append(
            relevance_tokens(
                " ".join(
                    [
                        str(source.get("title") or ""),
                        str(source.get("local_path") or ""),
                        str(source.get("corpus_relative_path") or ""),
                    ]
                )
            )
        )
    return tokens


def source_satisfied_by_local(source: dict[str, Any], local_tokens: list[set[str]]) -> bool:
    if str(source.get("local_path") or "").strip():
        return True
    source_tokens = relevance_tokens(str(source.get("title") or ""))
    if not source_tokens:
        return False
    required = min(2, len(source_tokens))
    return any(len(source_tokens & candidate) >= required for candidate in local_tokens)


def suggested_corpus_filenames(title: str) -> list[str]:
    stem = re.sub(r"[^a-zA-Zа-яА-Я0-9]+", "_", title).strip("_") or "primary_text"
    return [f"{stem}{extension}" for extension in [".epub", ".fb2", ".txt", ".md"]]


def corpus_requirements_for_sources(sources: list[dict[str, Any]], corpus_index: dict[str, Any] | None = None) -> dict[str, Any]:
    local_tokens = local_primary_tokens(sources)
    required: list[dict[str, Any]] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_class = str(source.get("source_class") or source.get("type") or "").lower()
        source_kind = str(source.get("type") or "").lower()
        if "primary" not in source_class and source_kind not in {"novel", "short_story", "book"}:
            continue
        if str(source.get("url") or "").strip() or source_satisfied_by_local(source, local_tokens):
            continue
        title = str(source.get("title") or "untitled primary source")
        required.append(
            {
                "title": title,
                "type": source.get("type", ""),
                "language": source.get("language", "unknown"),
                "source_class": source.get("source_class", ""),
                "reason": "Primary source has no public URL and no matching local corpus file.",
                "suggested_filenames": suggested_corpus_filenames(title),
                "supported_extensions": sorted({".epub", ".fb2", ".txt", ".md", ".html", ".xhtml"}),
            }
        )
    corpus_summary = corpus_index.get("summary") if isinstance(corpus_index, dict) and isinstance(corpus_index.get("summary"), dict) else {}
    return {
        "required": bool(required),
        "missing_count": len(required),
        "missing_primary_texts": required,
        "corpus_root": str(corpus_index.get("corpus_root") or "") if isinstance(corpus_index, dict) else "",
        "corpus_summary": corpus_summary,
    }


def load_corpus_sources(request: dict[str, Any], workspace_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    input_artifacts = request.get("input_artifacts") if isinstance(request.get("input_artifacts"), list) else []
    corpus_path = next((str(path) for path in input_artifacts if isinstance(path, str) and path.endswith("/corpus_index.json")), "")
    if not corpus_path:
        return [], {}
    host_path = sandbox_path(workspace_root, corpus_path)
    if not host_path.exists():
        return [], {"missing": corpus_path}
    try:
        payload = json.loads(host_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], {"error": str(exc), "path": corpus_path}
    sources = payload.get("sources") if isinstance(payload, dict) else []
    source_items = [dict(source) for source in sources if isinstance(source, dict)]
    return source_items, payload if isinstance(payload, dict) else {}


def source_map_for_contract(
    contract: dict[str, Any],
    searcher: SearchFn | None = None,
    corpus_sources: list[dict[str, Any]] | None = None,
    corpus_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    depth_profile = depth_profile_for_goal(goal, playbooks)
    rounds = search_rounds(topic, playbooks, depth_profile)
    cached_sources = cached_sources_for_topic(topic)
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
    cached_live_count = sum(1 for source in cached_sources if source.get("discovery_method") == "cached_live_search")
    min_live_candidate_count = int(depth_profile.get("min_live_candidate_count") or 0)
    cache_satisfies_live_depth = bool(playbooks) and min_live_candidate_count > 0 and cached_live_count >= min_live_candidate_count
    discovery_rounds = []
    if cache_satisfies_live_depth:
        discovery_status = "playbook_matched_cached_live"
        quality_notes.append("Cached live discovery already satisfies the requested live-candidate depth; fresh network probing was skipped.")
    else:
        discovery_rounds = run_discovery_rounds(
            rounds,
            searcher,
            limit=int(depth_profile.get("per_query_limit") or 5),
            query_budget=int(depth_profile.get("query_budget") or 10),
            per_round_query_limit=int(depth_profile.get("per_round_query_limit") or 4),
        )
    discovery_results = flatten_discovery_results(discovery_rounds)
    relevance_terms = None
    if playbooks:
        relevance_terms = [topic] + [
            str(source.get("title") or "")
            for source in sources
            if isinstance(source, dict) and source.get("title")
        ]
    live_candidates = classified_live_sources(discovery_results, relevance_terms)
    corpus_candidates = corpus_sources or []
    sources = rank_sources(dedupe_sources(corpus_candidates + sources + cached_sources + live_candidates))
    coverage = source_coverage(sources, discovery_results, playbooks)
    corpus_requirements = corpus_requirements_for_sources(sources, corpus_index)
    corpus_summary = corpus_index.get("summary") if isinstance(corpus_index, dict) and isinstance(corpus_index.get("summary"), dict) else {}
    corpus_gaps = corpus_index.get("gaps") if isinstance(corpus_index, dict) and isinstance(corpus_index.get("gaps"), list) else []
    coverage_gaps.extend(str(gap) for gap in corpus_gaps if gap)
    if sources and not coverage["ready_for_extraction"]:
        coverage_gaps.append("Source set is not extraction-ready: it needs both official/primary evidence and secondary cross-checking.")
    return {
        "topic": topic,
        "original_goal": goal,
        "sources": sources,
        "search_queries": search_queries,
        "depth_profile": depth_profile,
        "discovery_rounds": discovery_rounds,
        "discovery_status": discovery_status,
        "discovery_results": discovery_results,
        "live_source_candidates": live_candidates,
        "cached_source_candidates": cached_sources,
        "local_corpus_candidates": corpus_candidates,
        "corpus_summary": corpus_summary,
        "corpus_requirements": corpus_requirements,
        "source_cache": {
            "enabled": bool(source_cache_path(topic)),
            "cached_source_count": len(cached_sources),
            "path": str(source_cache_path(topic) or ""),
        },
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
    corpus_sources, corpus_index = load_corpus_sources(request, workspace_root)
    source_map = source_map_for_contract(contract, selected_searcher, corpus_sources=corpus_sources, corpus_index=corpus_index)
    write_source_cache(str(source_map.get("topic") or ""), source_map.get("sources", []))
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
