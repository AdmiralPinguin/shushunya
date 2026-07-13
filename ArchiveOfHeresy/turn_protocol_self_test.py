#!/usr/bin/env python3
from __future__ import annotations

import inspect
from unittest.mock import patch

from archive_handler import ArchiveHandler
from archive_ops import (
    CORE_DEGRADED_SAFE_REPLY,
    continuation_candidates_for_history,
    decide_chat_turn_action,
    prompt_diagnostics,
    run_mobile_chat_payload,
)
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
        {
            "answer_in_chat",
            "ask_clarification",
            "request_warmaster_mission",
            "continue_warmaster_mission",
            "create_administratum_task",
        } <= actions,
        f"capability manifest is incomplete: {actions}",
    )
    manifest_text = repr(manifest)
    require("Abaddon" in manifest_text, "public commander must be Abaddon")
    require("target_governor" not in manifest_text, "Shushunya must not choose a brigadier")

    continuation_manifest = turn_capability_manifest(
        continuable_tasks=[
            {
                "parent_task_id": "task-galaga-failed",
                "goal": "Собрать рабочий APK Galaga",
                "state": "failed",
                "failure_summary": "Получился только skeleton без APK.",
            }
        ]
    )
    continuation = next(
        item
        for item in continuation_manifest["capabilities"]
        if item["action"] == "continue_warmaster_mission"
    )
    require(continuation["available"] is True, "trusted failed task did not enable continuation")
    require(
        continuation_manifest["continuation_parent_task_id"] == "task-galaga-failed",
        "continuation root lost the trusted parent identity",
    )
    require(
        continuation["continuable_tasks"][0]["failure_summary"] == "Получился только skeleton без APK.",
        "continuation failure evidence disappeared",
    )
    with patch(
        "archive_ops.continuable_tasks",
        return_value=[
            {"parent_task_id": "task-old", "goal": "Старый отчёт", "state": "failed"},
            {"parent_task_id": "task-galaga", "goal": "Собрать APK Galaga", "state": "failed"},
        ],
    ):
        ranked = continuation_candidates_for_history(
            [
                {"role": "assistant", "content": "Старый отчёт провален", "dedupe_key": "warmaster:task-old:failed"},
                {"role": "assistant", "content": "Galaga не собрана", "dedupe_key": "warmaster:task-galaga:accepted"},
                {"role": "user", "content": "Пиздуй доделывай", "dedupe_key": "turn:latest:user"},
            ]
        )
    require(
        ranked[0]["parent_task_id"] == "task-galaga",
        "continuation root did not bind to the task most recently mentioned in shared history",
    )
    require(
        ranked[0].get("context_root") is True,
        "history-ranked continuation was not marked as the trusted root",
    )
    with patch(
        "archive_ops.continuable_tasks",
        return_value=[
            {"parent_task_id": "task-galaga", "goal": "Galaga Android приложение", "state": "failed"},
            {"parent_task_id": "task-calendar", "goal": "Календарь Android", "state": "failed"},
        ],
    ):
        generic = continuation_candidates_for_history(
            [{"role": "assistant", "content": "Android приложение не работает"}]
        )
    require(
        not any(item.get("context_root") for item in generic),
        "one generic shared token guessed a continuation root",
    )
    ambiguous_manifest = turn_capability_manifest(
        continuable_tasks=[
            {"parent_task_id": "task-a", "goal": "Первая задача", "state": "failed"},
            {"parent_task_id": "task-b", "goal": "Вторая задача", "state": "failed"},
        ]
    )
    require(
        ambiguous_manifest["continuation_parent_task_id"] == "",
        "unmatched multiple continuation candidates were guessed instead of left ambiguous",
    )
    decision_filtered = turn_capability_manifest(
        pending_decisions=[{"task_id": "task-a", "question": "Какой вариант?"}],
        continuable_tasks=[
            {"parent_task_id": "task-a", "goal": "Первая задача", "state": "blocked"},
        ],
    )
    decision_continuation = next(
        item
        for item in decision_filtered["capabilities"]
        if item["action"] == "continue_warmaster_mission"
    )
    require(
        decision_continuation["available"] is False,
        "task awaiting a typed decision leaked into generic continuation",
    )

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
    require(
        "continue_warmaster_mission" in core_turn_source,
        "trusted continuation action is not routed through the durable Core effect",
    )
    completion_source = inspect.getsource(ArchiveHandler.mobile_chat_completion)
    require(
        "continue_warmaster_mission" in completion_source
        and "continuation_parent_task_id" in completion_source,
        "foreground chat route drops the linked continuation effect",
    )
    require(
        completion_source.index('dedupe_key=f"core-turn:{request_id}:user"')
        < completion_source.index('dedupe_key=f"core-effect:{effect_id}:queued"')
        < completion_source.index('create_mobile_job("warmaster", payload)'),
        "Android action route queues before persisting the ordered user turn and factual ack",
    )
    require(
        'dedupe_key=f"core-effect:{effect_id}:user"' not in completion_source,
        "a reused continuation effect still suppresses a distinct later user turn",
    )
    require(
        "Принял. Запускаю работу" not in completion_source,
        "queued Android ack still claims execution before confirmation",
    )
    require(
        CORE_DEGRADED_SAFE_REPLY and "CORE_DEGRADED_SAFE_REPLY" in decide_source,
        "Core degradation does not have a deterministic non-empty safe reply",
    )
    chat_pipeline_source = inspect.getsource(run_mobile_chat_payload)
    require(
        'payload.get("turn_capabilities")' not in chat_pipeline_source
        and 'trusted_turn_context.get("turn_capabilities")' in chat_pipeline_source,
        "raw HTTP payload can still inject a capability catalog",
    )
    generic_source = inspect.getsource(ArchiveHandler.chat_completion)
    require(
        "trusted_turn_context=" not in generic_source,
        "generic HTTP completion accidentally marks client JSON as trusted turn context",
    )
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
