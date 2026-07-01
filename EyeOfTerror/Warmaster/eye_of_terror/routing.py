from __future__ import annotations

from dataclasses import dataclass

from .governors import governor_refs


@dataclass(frozen=True)
class RouteDecision:
    ok: bool
    governor: str
    kind: str
    reason: str

def route_message(message: str) -> RouteDecision:
    lowered = message.lower()
    candidates = []
    for governor in governor_refs():
        matched_terms = [term for term in governor.route_terms if term.lower() in lowered]
        if not matched_terms:
            continue
        candidates.append((len(matched_terms), governor, matched_terms))
    if not candidates:
        return RouteDecision(False, "", "general", "no supported governor matched")
    _, governor, matched_terms = max(candidates, key=lambda item: item[0])
    kind = governor.task_kinds[0] if governor.task_kinds else "general"
    reason = f"route terms matched for {governor.name}: {', '.join(matched_terms[:5])}"
    if governor.active():
        return RouteDecision(True, governor.name, kind, reason)
    return RouteDecision(False, governor.name, kind, f"governor is not active: {governor.name}")
