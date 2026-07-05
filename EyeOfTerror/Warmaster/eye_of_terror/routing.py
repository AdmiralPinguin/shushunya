from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

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


def route_message(message: str) -> RouteDecision:
    lowered = message.lower()
    candidates = []
    for governor in governor_refs():
        matched_terms = [term for term in governor.route_terms if term_matches(term, lowered)]
        if not matched_terms:
            continue
        candidates.append((len(matched_terms), governor, matched_terms))
    if not candidates:
        return RouteDecision(False, "", "general", "no supported governor matched")
    matched_governors = [
        {
            "name": governor.name,
            "status": governor.status,
            "active": governor.active(),
            "kind": governor.task_kinds[0] if governor.task_kinds else "general",
            "score": score,
            "matched_terms": matched_terms,
        }
        for score, governor, matched_terms in sorted(candidates, key=lambda item: item[0], reverse=True)
    ]
    # Prefer the best-scoring active governor so a stronger match on an inactive
    # (planned) governor never blocks a task an active governor could handle.
    active_candidates = [item for item in candidates if item[1].active()]
    if active_candidates:
        active_ranked = sorted(active_candidates, key=lambda item: item[0], reverse=True)
        _, governor, matched_terms = active_ranked[0]
        kind = governor.task_kinds[0] if governor.task_kinds else "general"
        supporting = [
            {
                "name": candidate.name,
                "kind": candidate.task_kinds[0] if candidate.task_kinds else "general",
                "score": score,
                "matched_terms": terms,
            }
            for score, candidate, terms in active_ranked[1:]
            if candidate.name != governor.name
            and has_strategic_support_signal(terms)
        ]
        requires_decomposition = bool(supporting)
        reason = f"route terms matched for {governor.name}: {', '.join(matched_terms[:5])}"
        if requires_decomposition:
            reason = f"multi-governor task: primary {governor.name}; supporting governors: {', '.join(item['name'] for item in supporting)}"
        inactive = [item for item in matched_governors if not item["active"]]
        return RouteDecision(
            True,
            governor.name,
            kind,
            reason,
            matched_governors=matched_governors,
            supporting_governors=supporting,
            inactive_matches=inactive,
            requires_decomposition=requires_decomposition,
        )
    # Only inactive governors matched: report the strongest one for a useful hint.
    _, governor, matched_terms = max(candidates, key=lambda item: item[0])
    kind = governor.task_kinds[0] if governor.task_kinds else "general"
    return RouteDecision(
        False,
        governor.name,
        kind,
        f"governor is not active: {governor.name}",
        matched_governors=matched_governors,
        inactive_matches=matched_governors,
    )
