from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteDecision:
    ok: bool
    governor: str
    kind: str
    reason: str


LORE_TERMS = (
    "скалатрак",
    "skalathrax",
    "лор",
    "lore",
    "источник",
    "source",
    "событи",
    "event",
    "реконструкц",
    "reconstruction",
)

CODE_TERMS = ("код", "repo", "repository", "python", "bug", "ошибк", "приложени")
IMAGE_TERMS = ("картин", "image", "stable diffusion", "рисовал", "forge")


def route_message(message: str) -> RouteDecision:
    lowered = message.lower()
    if any(term in lowered for term in LORE_TERMS):
        return RouteDecision(True, "IskandarKhayon", "research", "lore/research terms matched")
    if any(term in lowered for term in CODE_TERMS):
        return RouteDecision(False, "", "code", "code tasks need a code governor before routing")
    if any(term in lowered for term in IMAGE_TERMS):
        return RouteDecision(False, "", "image_generation", "image tasks need a forge governor before routing")
    return RouteDecision(False, "", "general", "no supported governor matched")
