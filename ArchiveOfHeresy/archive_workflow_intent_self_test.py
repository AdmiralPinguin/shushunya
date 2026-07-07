#!/usr/bin/env python3
from __future__ import annotations

import archive_handler
from archive_handler import ArchiveHandler
from archive_ops import prompt_diagnostics, workflow_role_guard_message


class FakeArchiveHandler:
    def mobile_chat_explicit_warmaster_task(self, text):
        return ArchiveHandler.mobile_chat_explicit_warmaster_task(self, text)

    def mobile_chat_looks_like_task(self, text):
        return ArchiveHandler.mobile_chat_looks_like_task(self, text)

    def mobile_chat_is_task_confirmation(self, text):
        return ArchiveHandler.mobile_chat_is_task_confirmation(self, text)

    def mobile_chat_contextual_task(self, history, task_index, task_text):
        return ArchiveHandler.mobile_chat_contextual_task(self, history, task_index, task_text)

    def mobile_chat_last_task_request(self, session_id):
        return ArchiveHandler.mobile_chat_last_task_request(self, session_id)

    def mobile_chat_warmaster_task(self, session_id, text):
        return ArchiveHandler.mobile_chat_warmaster_task(self, session_id, text)

    def mobile_chat_workflow_intent(self, session_id, text, image_data_url=""):
        return ArchiveHandler.mobile_chat_workflow_intent(self, session_id, text, image_data_url)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    handler = FakeArchiveHandler()
    substantial_task = (
        "Мне нужно что бы ты мне в одну книжку собрал вообще все что происходило. "
        "Нету одной нормальной книги, есть редкие упоминания местами. "
        "Я хочу почитать об этом с источниками."
    )
    history = [
        {"role": "user", "content": substantial_task},
        {"role": "assistant", "content": "Нужно собрать источники, проверить их и оформить итог."},
    ]
    original_chat_history = archive_handler.chat_history
    archive_handler.chat_history = lambda session_id, limit=16, after_id=0: list(history)
    try:
        require(handler.mobile_chat_looks_like_task(substantial_task), "substantial source-gathering request was not detected as a task")
        intent = handler.mobile_chat_workflow_intent("shushunya-main", "Начинай тогда заниматься этим")
        require(intent.get("kind") == "warmaster_start", f"confirmation did not route to Warmaster: {intent!r}")
        require("одну книжку" in str(intent.get("task") or ""), "recovered task text lost the prior user request")
        require("short confirmation" in str(intent.get("reason") or ""), "confirmation reason was not preserved")

        legacy_task = handler.mobile_chat_warmaster_task("shushunya-main", "Начинай тогда заниматься этим")
        require("одну книжку" in legacy_task, "legacy task detector no longer recovers contextual task")
    finally:
        archive_handler.chat_history = original_chat_history

    require(not handler.mobile_chat_is_task_confirmation("Погоди, давай обсудим это"), "discussion phrase must not start a workflow")
    require(not handler.mobile_chat_is_task_confirmation("Можешь рассказать что думаешь?"), "question phrase must not start a workflow")
    require(handler.mobile_chat_is_task_confirmation("Можешь начинать"), "polite start phrase must remain a valid workflow confirmation")
    require(
        handler.mobile_chat_workflow_intent("shushunya-main", "Погоди, давай обсудим это").get("kind") == "chat",
        "blocked discussion phrase routed outside chat",
    )

    guard = workflow_role_guard_message({"kind": "chat", "workflow_started": False, "reason": "ordinary chat"})
    guard_text = str(guard.get("content") or "")
    require("Do not say that you started" in guard_text, "workflow guard does not forbid fake execution claims")
    diagnostics = prompt_diagnostics([guard], [], magos_message=None)
    require(diagnostics["counts"]["workflow"] == 1, "workflow guard was not counted in prompt diagnostics")

    print("[ok] Archive workflow intent gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
