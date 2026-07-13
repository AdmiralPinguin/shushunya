from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .preferences import Preferences


ALLOWED_ACTIONS = {
    "answer_in_chat",
    "ask_clarification",
    "request_warmaster_mission",
    "create_administratum_task",
    "deliver_pending_reports",
}


@dataclass(frozen=True)
class Authorization:
    verdict: str
    code: str
    explanation: str


class Authority:
    """Hard capability boundary. The model can propose, never grant itself rights."""

    def __init__(self, preferences: Preferences):
        self.preferences = preferences

    def authorize(
        self,
        action: str,
        decision: dict[str, Any],
        manifest: dict[str, Any],
        *,
        forced: bool = False,
        context_scope: str = "*",
    ) -> Authorization:
        if action not in ALLOWED_ACTIONS:
            return Authorization("deny", "unknown_action", "Действие отсутствует в жёстком реестре Core.")
        capabilities = {
            str(item.get("action") or ""): item
            for item in manifest.get("capabilities", [])
            if isinstance(item, dict) and item.get("available") is True
        }
        if action not in capabilities:
            return Authorization("deny", "capability_unavailable", "Текущий capability contract не разрешает это действие.")
        if action in {"answer_in_chat", "ask_clarification", "deliver_pending_reports"}:
            return Authorization("auto", "local_turn", "Действие остаётся внутри текущего разговора.")
        if action == "request_warmaster_mission":
            request = decision.get("warmaster_request") if isinstance(decision.get("warmaster_request"), dict) else {}
            if not str(request.get("user_request") or "").strip() or not str(request.get("expected_outcome") or "").strip():
                return Authorization("deny", "incomplete_abaddon_request", "Нет исходной задачи или проверяемого ожидаемого результата.")
        if action == "create_administratum_task" and not str(decision.get("task") or "").strip():
            return Authorization("deny", "incomplete_administratum_request", "Нечего записывать в Administratum.")
        if action == "request_warmaster_mission":
            request = decision.get("warmaster_request") if isinstance(decision.get("warmaster_request"), dict) else {}
            target_scope = str(request.get("capability_area") or "unknown").strip().lower() or "unknown"
        elif action == "create_administratum_task":
            target_scope = "administratum"
        else:
            target_scope = "*"
        # A scope printed by the model cannot weaken an owner restriction.
        # Check all restrictive rules for this external action before using the
        # model-labelled target as a preference lookup hint.
        restriction = self.preferences.restrictive(action, context_scope=context_scope or "*")
        rule = restriction or self.preferences.lookup(
            action,
            target_scope=target_scope,
            context_scope=context_scope or "*",
        )
        if rule and not forced:
            if rule.get("verdict") == "never_auto":
                return Authorization("ask", "owner_never_auto", "Владелец запретил выполнять этот класс действий без отдельного подтверждения.")
            if rule.get("verdict") == "ask":
                return Authorization("ask", "owner_rejected_before", "Владелец ранее отклонил такое действие в этом контексте; нужно отдельное подтверждение.")
        # These two effects exist only after the current user turn explicitly
        # asked for them and the controller selected the matching capability.
        # They are cancellable delegations, not money/messages/destructive IO.
        return Authorization("auto", "explicit_turn_capability", "Текущий запрос владельца и capability contract дают узкое разрешение.")
