from __future__ import annotations

from typing import Any

from .ledger import Ledger


RELATIONSHIP_DEFAULTS: dict[str, Any] = {
    "owner_contract": {
        "language": "ru",
        "directness": "high",
        "profanity_between_us": "allowed when natural, never as a substitute for facts",
        "challenge": "argue concretely when there is evidence; do not agree performatively",
        "progress_reporting": "outcomes and honest current state, no decorative activity",
    },
    "delegation_boundary": {
        "explicit_current_request": "may execute through known reversible capabilities",
        "inferred_future_interest": "may prepare internally; external or irreversible effects require owner authority",
        "approval_is_not_delegation": True,
    },
    "open_tensions": [],
    "shared_themes": [],
}


class Relationship:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    def seed(self) -> None:
        for key, value in RELATIONSHIP_DEFAULTS.items():
            if self.ledger.projection_get("relationship", key) is None:
                self.ledger.projection_put("relationship", key, value, actor="relationship-seed")

    def snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, default in RELATIONSHIP_DEFAULTS.items():
            item = self.ledger.projection_get("relationship", key)
            result[key] = item["value"] if item else default
        return result

    def correct(self, key: str, value: Any) -> dict[str, Any]:
        return self.ledger.projection_put("relationship", key, value, actor="owner-correction")
