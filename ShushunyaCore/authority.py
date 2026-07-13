from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .preferences import Preferences


ALLOWED_ACTIONS = {
    "answer_in_chat",
    "ask_clarification",
    "request_warmaster_mission",
    "continue_warmaster_mission",
    "create_administratum_task",
    "deliver_pending_reports",
    "deliver_artifact",
    "answer_pending_decision",
}


def _artifact_catalog(capability: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = capability.get("artifacts") if isinstance(capability, dict) else None
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _artifact_catalog_hint(capability: dict[str, Any] | None) -> str:
    names: list[str] = []
    for item in _artifact_catalog(capability):
        value = str(
            item.get("filename")
            or item.get("display_name")
            or item.get("name")
            or ""
        ).strip()
        value = " ".join(value.split())[:160]
        if value and value not in names:
            names.append(value)
        if len(names) >= 8:
            break
    if not names:
        return "Сейчас у меня нет ни одного доступного файла, который можно отправить в этот разговор."
    return "Сейчас я могу отправить: " + "; ".join(names) + "."


def _artifact_denial(message: str, capability: dict[str, Any] | None) -> str:
    del message
    return (
        "Этот файл я сейчас прислать не могу: его ещё нет среди доступных мне вложений для этого разговора. "
        "Сначала мне нужно получить или создать файл и добавить его в доступные; после этого я смогу его отправить. "
        f"{_artifact_catalog_hint(capability)}"
    )


def pending_decision_ids(manifest: dict[str, Any]) -> list[str]:
    """Return only task ids published by the trusted turn capability."""
    result: list[str] = []
    root_id = str(manifest.get("pending_decision_task_id") or "").strip()[:240]
    if root_id:
        result.append(root_id)
    for capability in manifest.get("capabilities", []):
        if not (
            isinstance(capability, dict)
            and capability.get("action") == "answer_pending_decision"
            and capability.get("available") is True
        ):
            continue
        direct_id = str(capability.get("pending_decision_task_id") or "").strip()[:240]
        if direct_id and direct_id not in result:
            result.append(direct_id)
        decisions = capability.get("pending_decisions")
        for item in decisions if isinstance(decisions, list) else []:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("task_id") or "").strip()[:240]
            if task_id and task_id not in result:
                result.append(task_id)
        break
    return result[:12]


def continuable_task_catalog(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only continuation candidates published by trusted Archive state."""
    result: list[dict[str, Any]] = []
    for capability in manifest.get("capabilities", []):
        if not (
            isinstance(capability, dict)
            and capability.get("action") == "continue_warmaster_mission"
            and capability.get("available") is True
        ):
            continue
        items = capability.get("continuable_tasks")
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            parent_task_id = str(item.get("parent_task_id") or "").strip()[:240]
            goal = str(item.get("goal") or "").strip()[:1_200]
            if parent_task_id and goal and all(
                existing.get("parent_task_id") != parent_task_id for existing in result
            ):
                result.append(
                    {
                        "parent_task_id": parent_task_id,
                        "goal": goal,
                        "state": str(item.get("state") or "").strip()[:80],
                        "failure_summary": str(item.get("failure_summary") or "").strip()[:1_200],
                    }
                )
        break
    return result[:12]


def continuable_task_ids(manifest: dict[str, Any]) -> list[str]:
    return [item["parent_task_id"] for item in continuable_task_catalog(manifest)]


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
            return Authorization("deny", "unknown_action", "Я не умею надёжно выполнить это действие доступным способом.")
        declared_capabilities = {
            str(item.get("action") or ""): item
            for item in manifest.get("capabilities", [])
            if isinstance(item, dict)
        }
        capabilities = {
            name: item
            for name, item in declared_capabilities.items()
            if item.get("available") is True
        }
        if action not in capabilities:
            if action == "deliver_artifact":
                return Authorization(
                    "deny",
                    "artifact_catalog_unavailable",
                    _artifact_denial(
                        "Файл нельзя доставить: каталог артефактов недоступен для этого хода.",
                        None,
                    ),
                )
            return Authorization("deny", "capability_unavailable", "Сейчас я не могу выполнить это действие из этого разговора.")
        if action in {"answer_in_chat", "ask_clarification", "deliver_pending_reports"}:
            return Authorization("auto", "local_turn", "Действие остаётся внутри текущего разговора.")
        if action == "answer_pending_decision":
            trusted_task_ids = pending_decision_ids(manifest)
            pending = decision.get("pending_decision") if isinstance(decision.get("pending_decision"), dict) else {}
            decision_task_id = str(
                decision.get("pending_decision_task_id") or pending.get("task_id") or ""
            ).strip()
            answer = str(pending.get("answer") or "").strip()
            if not trusted_task_ids:
                return Authorization(
                    "deny",
                    "pending_decision_unavailable",
                    "В текущем контексте нет подтверждённого вопроса, которому можно передать ответ.",
                )
            if decision_task_id not in trusted_task_ids:
                return Authorization(
                    "deny",
                    "pending_decision_mismatch",
                    "Я не смог однозначно связать этот ответ с открытым вопросом.",
                )
            if not answer:
                return Authorization(
                    "deny",
                    "pending_decision_answer_empty",
                    "Ответ на ожидающий вопрос пуст; передавать в миссию нечего.",
                )
            return Authorization(
                "auto",
                "explicit_pending_decision_answer",
                "Текущий текст будет передан только подтверждённой ожидающей миссии.",
            )
        if action == "continue_warmaster_mission":
            trusted_task_ids = continuable_task_ids(manifest)
            parent_task_id = str(decision.get("continue_parent_task_id") or "").strip()
            if not trusted_task_ids:
                return Authorization(
                    "deny",
                    "continuation_unavailable",
                    "В текущем живом состоянии нет остановившейся задачи, которую можно честно продолжить.",
                )
            if parent_task_id not in trusted_task_ids:
                return Authorization(
                    "deny",
                    "continuation_task_mismatch",
                    "Я не смог однозначно связать команду продолжить с подтверждённой задачей.",
                )
        if action == "request_warmaster_mission":
            request = decision.get("warmaster_request") if isinstance(decision.get("warmaster_request"), dict) else {}
            if not str(request.get("user_request") or "").strip() or not str(request.get("expected_outcome") or "").strip():
                return Authorization("deny", "incomplete_abaddon_request", "Нет исходной задачи или проверяемого ожидаемого результата.")
        if action == "create_administratum_task" and not str(decision.get("task") or "").strip():
            return Authorization("deny", "incomplete_administratum_request", "Я не понял, что именно нужно записать.")
        if action == "deliver_artifact":
            delivery = decision.get("artifact_delivery") if isinstance(decision.get("artifact_delivery"), dict) else {}
            artifact_id = str(delivery.get("artifact_id") or "").strip()
            if not artifact_id:
                return Authorization(
                    "deny",
                    "incomplete_artifact_delivery",
                    _artifact_denial(
                        "Не выбран artifact_id для доставки.",
                        capabilities[action],
                    ),
                )
            # The model may select only an opaque id that Archive placed in this
            # turn's capability catalog. A filename, path or invented id is not
            # authority to read or send anything from the host filesystem.
            catalog = _artifact_catalog(capabilities[action])
            allowed_ids = {
                str(item.get("artifact_id") or "").strip()
                for item in catalog
                if isinstance(item, dict) and str(item.get("artifact_id") or "").strip()
            }
            if artifact_id not in allowed_ids:
                return Authorization(
                    "deny",
                    "artifact_not_in_capability",
                    _artifact_denial(
                        "Запрошенный файл не зарегистрирован либо не видим в доверенном каталоге этого хода.",
                        capabilities[action],
                    ),
                )
        if action == "request_warmaster_mission":
            request = decision.get("warmaster_request") if isinstance(decision.get("warmaster_request"), dict) else {}
            target_scope = str(request.get("capability_area") or "unknown").strip().lower() or "unknown"
        elif action == "continue_warmaster_mission":
            target_scope = "existing_abaddon_mission"
        elif action == "create_administratum_task":
            target_scope = "administratum"
        elif action == "deliver_artifact":
            target_scope = "owner_artifact_delivery"
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
                return Authorization("ask", "owner_never_auto", "Я помню, что ты запретил мне делать такие вещи без отдельного подтверждения.")
            if rule.get("verdict") == "ask":
                return Authorization("ask", "owner_rejected_before", "Я помню, что раньше ты отклонил такое действие; сейчас мне нужно отдельное подтверждение.")
        # These effects exist only after the current user turn explicitly
        # asked for them and the controller selected the matching capability.
        # They are cancellable delegations, not money/messages/destructive IO.
        return Authorization("auto", "explicit_turn_capability", "Твой текущий запрос даёт мне узкое разрешение на это действие.")
