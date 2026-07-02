from __future__ import annotations

import re
from dataclasses import dataclass

from .governors import governor_refs


@dataclass(frozen=True)
class RouteDecision:
    ok: bool
    governor: str
    kind: str
    reason: str


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
    # Prefer the best-scoring active governor so a stronger match on an inactive
    # (planned) governor never blocks a task an active governor could handle.
    active_candidates = [item for item in candidates if item[1].active()]
    if active_candidates:
        _, governor, matched_terms = max(active_candidates, key=lambda item: item[0])
        kind = governor.task_kinds[0] if governor.task_kinds else "general"
        reason = f"route terms matched for {governor.name}: {', '.join(matched_terms[:5])}"
        return RouteDecision(True, governor.name, kind, reason)
    # Only inactive governors matched: report the strongest one for a useful hint.
    _, governor, matched_terms = max(candidates, key=lambda item: item[0])
    kind = governor.task_kinds[0] if governor.task_kinds else "general"
    return RouteDecision(False, governor.name, kind, f"governor is not active: {governor.name}")
