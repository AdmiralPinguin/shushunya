#!/usr/bin/env python3
from __future__ import annotations

import inspect

from archive_handler import ArchiveHandler
from archive_ops import decide_chat_turn_action, prompt_diagnostics
from ShushunyaCore.decide import normalize_decision
from ShushunyaCore.ledger import MIGRATION_1
from turn_protocol import (
    capability_contract_message,
    normalize_warmaster_request,
    turn_capability_manifest,
    warmaster_request_to_message,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    manifest = turn_capability_manifest()
    actions = {item["action"] for item in manifest["capabilities"]}
    require(
        {"answer_in_chat", "ask_clarification", "request_warmaster_mission", "create_administratum_task"} <= actions,
        f"capability manifest is incomplete: {actions}",
    )
    manifest_text = repr(manifest)
    require("Abaddon" in manifest_text, "public commander must be Abaddon")
    require("target_governor" not in manifest_text, "Shushunya must not choose a brigadier")

    valid_request = normalize_warmaster_request(
        {
            "user_request": "Собери отчёт по Скалатраксу.",
            "capability_area": "research",
            "why_warmaster_needed": "Нужны источники и проверка.",
            "expected_outcome": "Проверенная хронология.",
            "success_conditions": ["Есть источники"],
        }
    )
    message = warmaster_request_to_message(valid_request)
    require("Шушуня не выбирает бригадира" in message, "Abaddon boundary was lost")

    external = normalize_decision(
        {
            "action": "request_warmaster_mission",
            "reply": "я уже всё сделал",
            "warmaster_request": valid_request,
            "confidence": 0.9,
        }
    )
    require(external["reply"] == "", "pre-execution model speech must be structurally discarded")

    contract = capability_contract_message(manifest, {"action": "answer_in_chat"})
    diagnostics = prompt_diagnostics([contract], [], magos_message=None)
    require(diagnostics["counts"]["capability_contract"] == 1, "capability contract disappeared")

    decide_source = inspect.getsource(decide_chat_turn_action)
    require("core_resolve_turn" in decide_source, "Archive still owns the turn decision")
    require("build_turn_decision_request" not in decide_source, "old poor-context controller remains live")
    require("assemble_shushunya_turn_context" in decide_source, "Core is not fed the rich context")
    require("speech-only degradation" in decide_source, "Core failure does not fail open into chat")

    start_source = inspect.getsource(ArchiveHandler.mobile_chat_start)
    require("run_core_turn_payload" in start_source, "mobile job does not delegate the complete Core-owned turn")
    core_turn_source = inspect.getsource(ArchiveHandler.run_core_turn_payload)
    require("core_context_bundle" in core_turn_source and "core_effect" in core_turn_source, "queued turn loses Core truth")
    warmaster_source = inspect.getsource(ArchiveHandler.run_mobile_warmaster_payload)
    require("core_dispatch_effect" in warmaster_source, "Core-owned tasks bypass its durable outbox")
    require("canonical_start" in warmaster_source, "Abaddon canonical auto-start path is not identified")

    require("'blocked'" not in MIGRATION_1 and '"blocked"' not in MIGRATION_1, "Core schema contains blocked")
    require(ArchiveHandler.mobile_chat_explicit_warmaster_task(None, "/abaddon проверь код") == "проверь код", "Abaddon alias broke")
    require(ArchiveHandler.mobile_chat_explicit_warmaster_task(None, "/warmaster проверь код") == "проверь код", "legacy alias broke")

    print("[ok] Archive transport -> one rich ShushunyaCore turn -> factual effects")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
