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
    "deliver_artifact",
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
            or item.get("artifact_id")
            or ""
        ).strip()
        value = " ".join(value.split())[:160]
        if value and value not in names:
            names.append(value)
        if len(names) >= 8:
            break
    if not names:
        return "Сейчас Archive не показывает этому ходу ни одного доступного файла."
    return "Доступные этому ходу файлы: " + "; ".join(names) + "."


def _artifact_denial(message: str, capability: dict[str, Any] | None) -> str:
    return (
        f"{message} Archive доставляет только заранее зарегистрированный artifact_id, "
        "видимый в каталоге текущих session/source. "
        "Чтобы исправить: сначала варбанда (или локальный издатель) должна зарегистрировать "
        "файл в Archive и открыть его текущему чату; после этого я смогу прислать его. "
        f"{_artifact_catalog_hint(capability)}"
    )


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
            return Authorization("deny", "capability_unavailable", "Текущий capability contract не разрешает это действие.")
        if action in {"answer_in_chat", "ask_clarification", "deliver_pending_reports"}:
            return Authorization("auto", "local_turn", "Действие остаётся внутри текущего разговора.")
        if action == "request_warmaster_mission":
            request = decision.get("warmaster_request") if isinstance(decision.get("warmaster_request"), dict) else {}
            if not str(request.get("user_request") or "").strip() or not str(request.get("expected_outcome") or "").strip():
                return Authorization("deny", "incomplete_abaddon_request", "Нет исходной задачи или проверяемого ожидаемого результата.")
        if action == "create_administratum_task" and not str(decision.get("task") or "").strip():
            return Authorization("deny", "incomplete_administratum_request", "Нечего записывать в Administratum.")
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
                return Authorization("ask", "owner_never_auto", "Владелец запретил выполнять этот класс действий без отдельного подтверждения.")
            if rule.get("verdict") == "ask":
                return Authorization("ask", "owner_rejected_before", "Владелец ранее отклонил такое действие в этом контексте; нужно отдельное подтверждение.")
        # These effects exist only after the current user turn explicitly
        # asked for them and the controller selected the matching capability.
        # They are cancellable delegations, not money/messages/destructive IO.
        return Authorization("auto", "explicit_turn_capability", "Текущий запрос владельца и capability contract дают узкое разрешение.")
