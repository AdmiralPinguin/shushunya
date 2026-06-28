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
    for governor in governor_refs():
        if not governor.route_terms or not any(term.lower() in lowered for term in governor.route_terms):
            continue
        kind = governor.task_kinds[0] if governor.task_kinds else "general"
        if governor.active():
            return RouteDecision(True, governor.name, kind, f"route terms matched for {governor.name}")
        return RouteDecision(False, governor.name, kind, f"governor is not active: {governor.name}")
    return RouteDecision(False, "", "general", "no supported governor matched")
