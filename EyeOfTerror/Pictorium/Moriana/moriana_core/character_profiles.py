from __future__ import annotations

import json
from typing import Any

from DemonsForge.forge_service import config


CHARACTER_PROFILES_PATH = config.QUALITY_ASSETS_DIR / "character_profiles.json"


def character_profiles() -> dict[str, Any]:
    payload: dict[str, Any] = {"version": 1, "profiles": []}
    if CHARACTER_PROFILES_PATH.exists():
        try:
            payload = json.loads(CHARACTER_PROFILES_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return {
                "version": 1,
                "profiles": [],
                "error": f"invalid character profile json: {exc}",
                "path": str(CHARACTER_PROFILES_PATH),
            }
    return {
        "version": payload.get("version", 1),
        "path": str(CHARACTER_PROFILES_PATH),
        "profiles": payload.get("profiles", []),
    }


def character_profile_for_text(text: str) -> dict[str, Any] | None:
    lowered = text.lower()
    for profile in character_profiles().get("profiles", []):
        aliases = [str(profile.get("id", "")), str(profile.get("name", ""))]
        aliases.extend(str(item) for item in profile.get("aliases", []))
        if any(alias and alias.lower() in lowered for alias in aliases):
            return profile
    return None
