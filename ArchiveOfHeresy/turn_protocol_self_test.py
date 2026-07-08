#!/usr/bin/env python3
from __future__ import annotations

import inspect

from archive_handler import ArchiveHandler
from archive_ops import prompt_diagnostics
from turn_protocol import (
    build_turn_decision_request,
    capability_contract_message,
    mission_order_to_warmaster_message,
    normalize_turn_decision,
    turn_capability_manifest,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    manifest = turn_capability_manifest()
    actions = {item["action"] for item in manifest["capabilities"]}
    require(
        {"answer_in_chat", "ask_clarification", "issue_mission_order", "create_administratum_task"} <= actions,
        f"capability manifest is incomplete: {actions}",
    )
    require("delegate_to_warmaster" not in actions, "Warmaster must not be exposed as a black-box delegation action")
    mission_capability = next(item for item in manifest["capabilities"] if item["action"] == "issue_mission_order")
    require(mission_capability.get("available_governors"), "mission order capability must expose available governors")

    request = build_turn_decision_request(
        model="test-model",
        user_text="начинай тогда заниматься этим",
        recent_history=[{"role": "user", "content": "Собери большой отчет через бригады."}],
        manifest=manifest,
    )
    system = request["messages"][0]["content"]
    require("Choose exactly one action" in system, "turn controller does not require one action")
    require("Do not use keyword rules" in system, "turn controller allows keyword routing")
    require("issue_mission_order" in system, "turn controller does not expose mission-order action")
    require("delegate_to_warmaster" not in system, "turn controller still exposes black-box Warmaster delegation")
    require("missing essential input" in system, "turn controller does not require clarification for missing critical inputs")
    require(request.get("response_format") == {"type": "json_object"}, "turn controller must request JSON object output")
    require(request.get("chat_template_kwargs", {}).get("enable_thinking") is False, "turn controller must request content JSON, not hidden thinking")

    valid_order = {
        "user_request": "Собери отчет по Скалатраксу.",
        "target_governor": "IskandarKhayon",
        "department": "Scriptorium",
        "task_kind": "lore_reconstruction",
        "why_this_governor": "Нужна реконструкция лора и источников.",
        "primary_goal": "Полная хронология событий Скалатракса.",
        "success_conditions": ["Есть хронология", "Есть список источников"],
    }
    decision = normalize_turn_decision({"action": "issue_mission_order", "mission_order": valid_order, "confidence": 0.9})
    require(decision["action"] == "issue_mission_order", f"valid mission order was not preserved: {decision}")
    require(decision["mission_order"]["target_governor"] == "IskandarKhayon", "mission order lost target governor")
    message = mission_order_to_warmaster_message(decision["mission_order"])
    require("Целевой бригадир: IskandarKhayon" in message, "Warmaster transport text must preserve selected governor")
    require("Почему этот бригадир" in message, "Warmaster transport text must preserve governor rationale")
    invalid = normalize_turn_decision({"action": "issue_mission_order", "mission_order": {"user_request": "x"}})
    require(invalid["action"] == "ask_clarification", f"missing task should not delegate: {invalid}")

    contract = capability_contract_message(manifest, {"action": "answer_in_chat"})
    require("ArchiveOfHeresy capability contract" in contract["content"], "capability contract message has wrong marker")
    diagnostics = prompt_diagnostics([contract], [], magos_message=None)
    require(diagnostics["counts"]["capability_contract"] == 1, "prompt diagnostics must count capability contracts")

    mobile_start_source = inspect.getsource(ArchiveHandler.mobile_chat_start)
    require("decide_chat_turn_action" in mobile_start_source, "mobile chat start must use LLM turn protocol")
    require("mobile_chat_warmaster_task" not in mobile_start_source, "mobile chat start still uses legacy keyword Warmaster routing")
    require("mobile_chat_looks_like_task" not in mobile_start_source, "mobile chat start still uses task keyword routing")
    require("mobile_chat_is_task_confirmation" not in mobile_start_source, "mobile chat start still uses confirmation keyword routing")

    print("[ok] Archive turn protocol contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
