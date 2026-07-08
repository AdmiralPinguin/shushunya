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
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    manifest = turn_capability_manifest()
    actions = {item["action"] for item in manifest["capabilities"]}
    require(
        {"answer_in_chat", "ask_clarification", "delegate_to_warmaster", "create_administratum_task"} <= actions,
        f"capability manifest is incomplete: {actions}",
    )

    request = build_turn_decision_request(
        model="test-model",
        user_text="начинай тогда заниматься этим",
        recent_history=[{"role": "user", "content": "Собери большой отчет через бригады."}],
        manifest=manifest,
    )
    system = request["messages"][0]["content"]
    require("Choose exactly one action" in system, "turn controller does not require one action")
    require("Do not use keyword rules" in system, "turn controller allows keyword routing")
    require(request.get("response_format") == {"type": "json_object"}, "turn controller must request JSON object output")
    require(request.get("chat_template_kwargs", {}).get("enable_thinking") is False, "turn controller must request content JSON, not hidden thinking")

    decision = normalize_turn_decision({"action": "delegate_to_warmaster", "task": "Собери отчет.", "confidence": 0.9})
    require(decision["action"] == "delegate_to_warmaster", f"valid Warmaster decision was not preserved: {decision}")
    invalid = normalize_turn_decision({"action": "delegate_to_warmaster", "task": ""})
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
