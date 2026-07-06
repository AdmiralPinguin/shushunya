from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from EyeOfTerror.model_brain import request_model_decision

from .governors import governor_refs


@dataclass(frozen=True)
class RouteDecision:
    ok: bool
    governor: str
    kind: str
    reason: str
    matched_governors: list[dict[str, Any]] = field(default_factory=list)
    supporting_governors: list[dict[str, Any]] = field(default_factory=list)
    inactive_matches: list[dict[str, Any]] = field(default_factory=list)
    requires_decomposition: bool = False
    error_code: str = ""
    model_brain: dict[str, Any] = field(default_factory=dict)
    llm_route: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "governor": self.governor,
            "kind": self.kind,
            "reason": self.reason,
            "matched_governors": self.matched_governors,
            "supporting_governors": self.supporting_governors,
            "inactive_matches": self.inactive_matches,
            "requires_decomposition": self.requires_decomposition,
            "error_code": self.error_code,
            "model_brain": self.model_brain,
            "llm_route": self.llm_route,
        }


def term_matches(term: str, lowered_message: str) -> bool:
    """Match a route term against the message without incidental infixes.

    Cyrillic stems and multi-word phrases match as a word-initial prefix, so
    truncated stems keep working (``код`` -> ``кодовая``, ``приложени`` ->
    ``приложении``). ASCII single-word terms match as a whole word only, so a
    short term does not leak into an unrelated longer word (``repo`` no longer
    matches ``report``, ``app`` not ``happiness``, ``test`` not ``latest``).
    The registry already lists full forms (e.g. both ``repo`` and
    ``repository``) so whole-word matching loses no intended coverage."""
    term = term.lower().strip()
    if not term:
        return False
    if term == "код":
        return (
            re.search(
                r"\bкод(?:\b|[ауеом]\b|ов(?:ая|ое|ые|ый|ого|ому|ым|ом|ую|ой|ыми|ых)?\b)",
                lowered_message,
            )
            is not None
        )
    if " " in term or re.search(r"[^\x00-\x7f]", term):
        return re.search(r"\b" + re.escape(term), lowered_message) is not None
    return re.search(r"\b" + re.escape(term) + r"\b", lowered_message) is not None


WEAK_SUPPORT_TERMS = {
    "исслед",
    "источник",
    "source",
}


def has_strategic_support_signal(matched_terms: list[str]) -> bool:
    return any(term.lower().strip() not in WEAK_SUPPORT_TERMS for term in matched_terms)


def route_kind_for(governor: Any, lowered_message: str) -> str:
    kinds = list(governor.task_kinds)
    if governor.name == "Moriana":
        if any(term in lowered_message for term in ("комикс", "comic", "storyboard", "раскадров", "панел", "panel")):
            return "comic_generation" if "comic_generation" in kinds else (kinds[0] if kinds else "general")
        if any(term in lowered_message for term in ("серия", "серию", "серии", "набор картинок", "несколько картинок", "image series", "series of images", "batch of images")):
            return "image_series_generation" if "image_series_generation" in kinds else (kinds[0] if kinds else "general")
    return kinds[0] if kinds else "general"


def route_candidates(message: str) -> dict[str, Any]:
    lowered = message.lower()
    candidates = []
    for governor in governor_refs():
        matched_terms = [term for term in governor.route_terms if term_matches(term, lowered)]
        if not matched_terms:
            continue
        candidates.append((len(matched_terms), governor, matched_terms))
    matched_governors = [
        {
            "name": governor.name,
            "status": governor.status,
            "active": governor.active(),
            "kind": route_kind_for(governor, lowered),
            "score": score,
            "matched_terms": matched_terms,
        }
        for score, governor, matched_terms in sorted(candidates, key=lambda item: item[0], reverse=True)
    ]
    return {
        "matched_governors": matched_governors,
        "inactive_matches": [item for item in matched_governors if not item["active"]],
        "active_matches": [item for item in matched_governors if item["active"]],
    }


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    elif "{" in stripped and "}" in stripped:
        stripped = stripped[stripped.find("{") : stripped.rfind("}") + 1]
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("router response is not a JSON object")
    return parsed


def _governor_payload(governor_name: str) -> dict[str, Any]:
    for governor in governor_refs():
        if governor.name == governor_name:
            return {
                "name": governor.name,
                "status": governor.status,
                "active": governor.active(),
                "service": governor.service,
                "port": governor.port,
                "task_kinds": list(governor.task_kinds),
                "kind": list(governor.task_kinds)[0] if governor.task_kinds else "general",
            }
    return {}


def _known_governors() -> list[dict[str, Any]]:
    return [
        {
            "name": governor.name,
            "status": governor.status,
            "active": governor.active(),
            "service": governor.service,
            "port": governor.port,
            "task_kinds": list(governor.task_kinds),
        }
        for governor in governor_refs()
    ]


def _supporting_payload(names: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not isinstance(names, list):
        return result
    for item in names:
        name = item.get("name") if isinstance(item, dict) else item
        if not isinstance(name, str) or not name.strip():
            continue
        payload = _governor_payload(name.strip())
        if payload:
            result.append(payload)
    return result


def route_message(message: str) -> RouteDecision:
    candidates = route_candidates(message)
    model_decision = request_model_decision(
        "WarmasterRouter",
        "LLM-first task routing governor selector",
        {
            "message": message,
            "candidate_hints": candidates,
            "known_governors": _known_governors(),
            "required_json_schema": {
                "ok": "boolean",
                "governor": "IskandarKhayon | Ceraxia | Moriana | empty string",
                "kind": "research | code | image_generation | image_series_generation | comic_generation | general",
                "requires_decomposition": "boolean",
                "supporting_governors": ["governor names if the task must be split"],
                "reason": "short concrete reason",
            },
        },
        layer="routing_service",
        instructions=(
            "You are the only authority for routing this task. The candidate_hints are advisory, not binding. "
            "Return one strict JSON object and nothing else. Route lore, research, source reconstruction, translation, "
            "and writing synthesis to IskandarKhayon. Route software creation, repair, architecture, tests, and repo work "
            "to Ceraxia. Route image generation, drawing tools, Stable Diffusion, comics, panels, and image series to Moriana. "
            "If the user asks for multiple active departments, set requires_decomposition=true with a primary governor and "
            "supporting_governors. If no department should accept it, return ok=false."
        ),
    )
    if not model_decision.get("ok"):
        return RouteDecision(
            False,
            "",
            "general",
            "model router unavailable; deterministic routing fallback is forbidden",
            matched_governors=candidates["matched_governors"],
            inactive_matches=candidates["inactive_matches"],
            error_code="model_router_unavailable",
            model_brain=model_decision,
        )
    try:
        llm_route = _extract_json_object(str(model_decision.get("content") or ""))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return RouteDecision(
            False,
            "",
            "general",
            f"model router returned invalid route JSON: {exc}",
            matched_governors=candidates["matched_governors"],
            inactive_matches=candidates["inactive_matches"],
            error_code="invalid_model_route",
            model_brain=model_decision,
        )
    reason = str(llm_route.get("reason") or "model router declined route").strip()
    if not bool(llm_route.get("ok")):
        return RouteDecision(
            False,
            str(llm_route.get("governor") or ""),
            str(llm_route.get("kind") or "general"),
            reason,
            matched_governors=candidates["matched_governors"],
            inactive_matches=candidates["inactive_matches"],
            error_code="model_declined_route",
            model_brain=model_decision,
            llm_route=llm_route,
        )
    governor_name = str(llm_route.get("governor") or "").strip()
    governor_payload = _governor_payload(governor_name)
    if not governor_payload:
        return RouteDecision(
            False,
            governor_name,
            str(llm_route.get("kind") or "general"),
            f"model router selected unknown governor: {governor_name}",
            matched_governors=candidates["matched_governors"],
            inactive_matches=candidates["inactive_matches"],
            error_code="unknown_model_governor",
            model_brain=model_decision,
            llm_route=llm_route,
        )
    if not governor_payload.get("active"):
        return RouteDecision(
            False,
            governor_name,
            str(llm_route.get("kind") or governor_payload.get("kind") or "general"),
            f"model router selected inactive governor: {governor_name}",
            matched_governors=candidates["matched_governors"],
            inactive_matches=candidates["inactive_matches"] or [governor_payload],
            error_code="governor_inactive",
            model_brain=model_decision,
            llm_route=llm_route,
        )
    supporting = _supporting_payload(llm_route.get("supporting_governors"))
    return RouteDecision(
        True,
        governor_name,
        str(llm_route.get("kind") or governor_payload.get("kind") or "general"),
        reason,
        matched_governors=candidates["matched_governors"],
        supporting_governors=supporting,
        inactive_matches=candidates["inactive_matches"],
        requires_decomposition=bool(llm_route.get("requires_decomposition")) and bool(supporting),
        model_brain=model_decision,
        llm_route=llm_route,
    )
