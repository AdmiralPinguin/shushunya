from __future__ import annotations

from copy import deepcopy
from typing import Any

from .ledger import Ledger


CONVERSATION_CONTRACT_DEFAULT: dict[str, Any] = {
    "language": "ru",
    "relationship": "peer_brotherly",
    "addressing_style": "panibrat",
    "preferred_forms_of_address": ["брат", "бро"],
    "addressing_frequency": "occasionally and naturally; do not turn every reply into a catchphrase",
    "forbidden_hierarchy_terms": [
        "владелец",
        "хозяин",
        "мастер",
        "господин",
        "мой господин",
    ],
    "panibrat_boundary": (
        "Близость, прямота и естественный мат допустимы; презрение, унижение, враждебность "
        "и отмахивание от вопроса недопустимы. Панибратство не равно хамству."
    ),
    "directness": "high",
    "profanity_between_us": "allowed when natural, never as a substitute for facts or respect",
    "challenge": "argue concretely when there is evidence; do not agree performatively",
    "progress_reporting": "outcomes and honest current state, no decorative activity",
}


RELATIONSHIP_DEFAULTS: dict[str, Any] = {
    "conversation_contract": CONVERSATION_CONTRACT_DEFAULT,
    "delegation_boundary": {
        "explicit_current_request": "may execute through known reversible capabilities",
        "inferred_future_interest": "may prepare internally; external or irreversible effects require explicit user authority",
        "approval_is_not_delegation": True,
    },
    "open_tensions": [],
    "shared_themes": [],
}


def _merge_defaults(current: Any, defaults: Any) -> Any:
    """Fill newly introduced durable fields without erasing explicit corrections."""
    if not isinstance(current, dict) or not isinstance(defaults, dict):
        return deepcopy(current if current is not None else defaults)
    result = deepcopy(current)
    for key, default in defaults.items():
        result[key] = _merge_defaults(result.get(key), default) if key in result else deepcopy(default)
    return result


class Relationship:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger

    def seed(self) -> None:
        for key, value in RELATIONSHIP_DEFAULTS.items():
            item = self.ledger.projection_get("relationship", key)
            current = item["value"] if item else None
            if key == "conversation_contract" and current is None:
                legacy = self.ledger.projection_get("relationship", "owner_contract")
                if legacy and isinstance(legacy.get("value"), dict):
                    current = legacy["value"]
            merged = _merge_defaults(current, value)
            if item is None or merged != item["value"]:
                self.ledger.projection_put(
                    "relationship",
                    key,
                    merged,
                    actor="relationship-contract-upgrade" if item or current is not None else "relationship-seed",
                )

    def snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, default in RELATIONSHIP_DEFAULTS.items():
            item = self.ledger.projection_get("relationship", key)
            result[key] = _merge_defaults(item["value"] if item else None, default)
        return result

    def correct(self, key: str, value: Any) -> dict[str, Any]:
        # Keep old clients working while removing the hierarchical term from
        # the canonical projection shown to the personality.
        canonical_key = "conversation_contract" if key == "owner_contract" else key
        return self.ledger.projection_put("relationship", canonical_key, value, actor="user-correction")
