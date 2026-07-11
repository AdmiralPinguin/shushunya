#!/usr/bin/env python3
from __future__ import annotations

import inspect

from archive_handler import ArchiveHandler
from archive_ops import prompt_diagnostics
from turn_protocol import (
    build_turn_decision_request,
    capability_contract_message,
    normalize_turn_decision,
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
    require("delegate_to_warmaster" not in actions, "Warmaster must not be exposed as a black-box delegation action")
    mission_capability = next(item for item in manifest["capabilities"] if item["action"] == "request_warmaster_mission")
    manifest_text = repr(manifest)
    require("Abaddon" in manifest_text, "capability manifest must expose Abaddon as the public commander name")
    require("EyeOfTerror Warmaster" not in manifest_text, "capability manifest leaked the legacy public commander name")
    require("Abaddon chooses the strategic route" in manifest_text, "capability manifest lost Abaddon's strategic boundary")
    require("subordinates own the detailed plan, execution, and checks" in manifest_text, "capability manifest assigns worker-level planning to the wrong layer")
    require("brigadier, workers, plan, and acceptance" not in manifest_text, "legacy micromanagement hierarchy remains in the capability manifest")
    for forbidden in ("target_governor", "IskandarKhayon", "Ceraxia", "Moriana"):
        require(forbidden not in manifest_text, f"Shushunya capability manifest must not choose Warmaster internals: {forbidden}")
    require(mission_capability.get("warmaster_capability_areas"), "Warmaster request capability must expose high-level areas")

    request = build_turn_decision_request(
        model="test-model",
        user_text="начинай тогда заниматься этим",
        recent_history=[{"role": "user", "content": "Собери большой отчет через бригады."}],
        manifest=manifest,
    )
    system = request["messages"][0]["content"]
    require("Choose exactly one action" in system, "turn controller does not require one action")
    require("Shushunya is male" in system, "turn controller must preserve male Shushunya voice in protocol replies")
    require("Do not use keyword rules" in system, "turn controller allows keyword routing")
    require("request_warmaster_mission" in system, "turn controller does not expose Warmaster request action")
    require("Abaddon" in system, "turn controller must describe the selected commander as Abaddon")
    require("delegate_to_warmaster" not in system, "turn controller still exposes black-box Warmaster delegation")
    require("target_governor" not in system, "turn controller still asks Shushunya to select a governor")
    require("IskandarKhayon" not in system and "Ceraxia" not in system and "Moriana" not in system, "turn controller leaks concrete brigadiers")
    require("missing essential input" in system, "turn controller does not require clarification for missing critical inputs")
    require(request.get("response_format") == {"type": "json_object"}, "turn controller must request JSON object output")
    require(request.get("chat_template_kwargs", {}).get("enable_thinking") is False, "turn controller must request content JSON, not hidden thinking")

    valid_request = {
        "user_request": "Собери отчет по Скалатраксу.",
        "capability_area": "research",
        "why_warmaster_needed": "Нужна бригада, источники, проверка и финальная приемка.",
        "expected_outcome": "Полная хронология событий Скалатракса.",
        "success_conditions": ["Есть хронология", "Есть список источников"],
    }
    decision = normalize_turn_decision({"action": "request_warmaster_mission", "warmaster_request": valid_request, "confidence": 0.9})
    require(decision["action"] == "request_warmaster_mission", f"valid Warmaster request was not preserved: {decision}")
    require(decision["warmaster_request"]["capability_area"] == "research", "Warmaster request lost high-level area")
    message = warmaster_request_to_message(decision["warmaster_request"])
    require("Шушуня не выбирает бригадира" in message, "Warmaster transport text must reserve assignment for Warmaster")
    require("Абаддон" in message and "Warmaster" not in message, "transport prose must expose only the Abaddon public name")
    require("он не выбирает работников и не составляет подробный план" in message, "transport prose lets Abaddon micromanage the warband")
    require("Бригадир принимает решения своей варбанды" in message, "transport prose lost the brigadier leadership boundary")
    require("Область задачи: research" in message, "Warmaster transport text must preserve high-level area")
    require("Целевой бригадир" not in message, "Warmaster transport text must not contain selected governor")
    require(ArchiveHandler.mobile_chat_explicit_warmaster_task(None, "/abaddon проверь код") == "проверь код", "English Abaddon command alias is not routed")
    require(ArchiveHandler.mobile_chat_explicit_warmaster_task(None, "абаддон: проверь код") == "проверь код", "Russian Abaddon command alias is not routed")
    require(ArchiveHandler.mobile_chat_explicit_warmaster_task(None, "/warmaster проверь код") == "проверь код", "legacy Warmaster command alias stopped working")
    require(ArchiveHandler.mobile_chat_explicit_warmaster_task(None, "вармастер: проверь код") == "проверь код", "legacy Russian command alias stopped working")
    invalid = normalize_turn_decision({"action": "request_warmaster_mission", "warmaster_request": {"user_request": "x", "expected_outcome": ""}})
    require(invalid["action"] == "ask_clarification", f"missing task should not request Warmaster: {invalid}")

    contract = capability_contract_message(manifest, {"action": "answer_in_chat"})
    require("ArchiveOfHeresy capability contract" in contract["content"], "capability contract message has wrong marker")
    diagnostics = prompt_diagnostics([contract], [], magos_message=None)
    require(diagnostics["counts"]["capability_contract"] == 1, "prompt diagnostics must count capability contracts")

    mobile_start_source = inspect.getsource(ArchiveHandler.mobile_chat_start)
    require("decide_chat_turn_action" in mobile_start_source, "mobile chat start must use LLM turn protocol")
    require("request_warmaster_mission" in mobile_start_source, "mobile chat start must use Warmaster request action")
    require("issue_mission_order" not in mobile_start_source, "mobile chat start still uses old mission-order action")
    require("mobile_chat_warmaster_task" not in mobile_start_source, "mobile chat start still uses legacy keyword Warmaster routing")
    require("mobile_chat_looks_like_task" not in mobile_start_source, "mobile chat start still uses task keyword routing")
    require("mobile_chat_is_task_confirmation" not in mobile_start_source, "mobile chat start still uses confirmation keyword routing")

    print("[ok] Archive Abaddon turn protocol and Warmaster compatibility contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
