from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


VALID_ROLES = frozenset({"presence", "mind", "canvas", "ambient"})
LEGACY_ROLES = {"operations": "mind", "archive": "canvas"}


@dataclass(frozen=True, slots=True)
class ScreenDescriptor:
    key: str
    name: str
    width: int
    height: int
    primary: bool = False
    manufacturer: str = ""
    model: str = ""

    @property
    def portrait(self) -> bool:
        return self.height > self.width


def assign_roles(
    screens: Iterable[ScreenDescriptor],
    persisted: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Assign stable semantic roles without depending on monitor indices."""

    ordered = list(screens)
    if not ordered:
        return {}

    stored = persisted or {}
    result: dict[str, str] = {}
    claimed_singletons: set[str] = set()

    for screen in ordered:
        role = LEGACY_ROLES.get(stored.get(screen.key, ""), stored.get(screen.key, ""))
        if role not in VALID_ROLES:
            continue
        if role != "ambient" and role in claimed_singletons:
            continue
        result[screen.key] = role
        if role != "ambient":
            claimed_singletons.add(role)

    def claim(role: str, candidates: Iterable[ScreenDescriptor]) -> None:
        if role in claimed_singletons:
            return
        for candidate in candidates:
            if candidate.key in result:
                continue
            result[candidate.key] = role
            claimed_singletons.add(role)
            return

    claim("presence", [s for s in ordered if s.primary])
    claim("presence", [s for s in ordered if not s.portrait])
    claim("presence", ordered)

    claim("mind", [s for s in ordered if s.portrait])
    claim("mind", ordered)

    claim("canvas", [s for s in ordered if not s.portrait])
    claim("canvas", ordered)

    for screen in ordered:
        result.setdefault(screen.key, "ambient")
    return result
