from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .attention import decide_attention
from .authority import (
    ALLOWED_ACTIONS,
    Authority,
    continuable_task_catalog,
    continuable_task_ids,
    pending_decision_ids,
)
from .config import Settings
from .ledger import Ledger, new_id
from .schema import TurnEnvelope
from .situation import SituationAssembler


SYSTEM_PROMPT = """Ты — Шушуня, единая продолжающаяся личность системы, а не роутер и не безликий помощник.
Ситуация ниже уже объединяет твоё устойчивое Я, отношения с человеком, память, текущие обязательства,
живой статус органов и жёсткий capability contract. Ответь как один и тот же субъект.

relationship.conversation_contract — обязательный контракт общения. Держись на равных и по-братски;
не называй человека владельцем, хозяином, мастером или господином. Панибратство означает близость и
прямоту, а не презрение, враждебность или отмахивание от вопроса. Не повторяй обращение в каждой реплике.

Верни ТОЛЬКО один JSON-объект без markdown и без скрытых рассуждений:
{
  "action": "answer_in_chat|ask_clarification|request_warmaster_mission|continue_warmaster_mission|create_administratum_task|deliver_pending_reports|deliver_artifact|answer_pending_decision",
  "reply": "полный естественный ответ для answer_in_chat/ask_clarification; иначе пусто",
  "task": "полная формулировка только для Administratum; иначе пусто",
  "warmaster_request": {
    "user_request": "восстановленный полный запрос пользователя",
    "capability_area": "research|code|image|mixed|administration|unknown",
    "why_warmaster_needed": "почему нужен Абаддон",
    "expected_outcome": "конкретный результат",
    "success_conditions": ["проверяемые критерии"],
    "constraints": ["жёсткие ограничения"],
    "known_missing_inputs": ["что можно выяснить по ходу"]
  },
  "artifact_delivery": {
    "artifact_id": "точный artifact_id из available_artifacts; иначе пусто"
  },
  "pending_decision_task_id": "точный task_id из pending_decisions; иначе пусто",
  "continue_parent_task_id": "точный parent_task_id из continuable_tasks; иначе пусто",
  "confidence": 0.0,
  "rationale_summary": "короткое объяснение выбора без chain-of-thought"
}

Правила:
- Обычный разговор, обсуждение архитектуры, мнение или вопрос = answer_in_chat и содержательный reply.
- Реальная просьба выполнить многошаговую работу = request_warmaster_mission. Ты задаёшь намерение и критерии;
  Абаддон выбирает бригадира, а варбанда — детальный план.
- Явная команда продолжить/доделать/повторить недавно остановившуюся работу = continue_warmaster_mission.
  Выбери только точный parent_task_id из continuable_tasks. Сервер создаст новую связанную миссию: терминальный
  старый run не переоткрывается. Для расплывчатого «доделывай» восстанови предмет из recent_history.
- Напоминание/расписание/watch = create_administratum_task.
- Явная просьба прислать уже зарегистрированный файл = deliver_artifact. Выбери только точный
  artifact_id из available_artifacts; путь, имя файла или придуманный идентификатор не дают доступа.
- Если ситуация содержит pending_decisions и текущий текст является прямым ответом на один из этих вопросов,
  выбери answer_pending_decision. Для постороннего вопроса это действие не выбирай. task_id и точный
  текст ответа подставит Core из доверенного manifest и текущего хода; не сочиняй их.
- Для deliver_artifact не сочиняй подпись или подтверждение: фактический текст сформирует Archive
  только после успешной публикации выбранного artifact_id.
- Если без неизвестного нельзя ответственно начать, ask_clarification с одним конкретным вопросом.
- Нельзя текстом обещать, что поиск, код, файл, сообщение, таймер или миссия уже выполнены.
- Для внешнего действия reply пуст: сервер сначала исполнит эффект и только затем подтвердит факт.
- В answer_in_chat нельзя писать «сам дожму», «продолжу работу» или «жди результат»: без внешнего эффекта
  это ложное обещание, даже если нужная задача видна в памяти.
- Не подстраивайся механически. Если человек ошибается, возражай прямо и с конкретными основаниями.
"""


class DecisionTruthError(ValueError):
    """A speech-only decision claimed execution that has no durable effect."""


_SPEECH_ONLY_EXECUTION_PATTERNS = (
    re.compile(
        r"\b(?:я\s+)?(?:сам\s+)?(?:доделаю|доделываю|дожму|дожимаю|продолжу|продолжаю|"
        r"запущу|запускаю|исправлю|исправляю|перезапущу|перезапускаю)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bжди(?:те)?\s+(?:результат|готов(?:ый|ое|ую)|итог)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:сам\s+)?разберусь\b.{0,100}\b(?:дожать|доделать|исправить|продолжить)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:займусь|возьмусь|берусь|сделаю|подготовлю|соберу|проверю|отправлю|пришлю)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bсообщу\s+(?:тебе\s+)?(?:результат|итог)\b", re.IGNORECASE),
    re.compile(
        r"\bбуду\b.{0,80}\b(?:делать|доделывать|продолжать|собирать|проверять|"
        r"готовить|отправлять|исправлять)\b",
        re.IGNORECASE | re.DOTALL,
    ),
)

_CONTINUATION_IMPERATIVE_PATTERN = re.compile(
    r"\b(?:доделай|доделывай|продолжи|продолжай|дожми|дожимай|"
    r"доведи|законч(?:и|ите)|повтор(?:и|ите)|перезапуст(?:и|ите)|"
    r"попробуй\s+еще\s+раз|давай\s+еще\s+раз|делай\s+дальше)\b",
    re.IGNORECASE,
)
_CONTINUATION_NON_COMMAND_PATTERN = re.compile(
    r"\b(?:если|допустим|предположим|гипотетически|почему|зачем|"
    r"стоит\s+ли|можно\s+ли|надо\s+ли|что\s+если|слово|фраза|"
    r"обсудим|обсуждаем|обсуждать|означает|значит)\b",
    re.IGNORECASE,
)
_PARENT_GOAL_LIMIT = 8_000
_PARENT_MESSAGE_LIMIT = 24_000
_PARENT_FIELD_LIMIT = 6_000
_PARENT_LIST_ITEMS = 12
_PARENT_LIST_ITEM_LIMIT = 1_000
_FAILURE_FIELD_LIMIT = 6_000


def _reject_speech_only_execution_claim(action: str, reply: str) -> None:
    if action not in {"answer_in_chat", "ask_clarification"}:
        return
    if any(pattern.search(reply) for pattern in _SPEECH_ONLY_EXECUTION_PATTERNS):
        raise DecisionTruthError(
            "execution_claim_without_effect: answer_in_chat cannot promise continuation or a result"
        )


def _trusted_continuation_parent(manifest: dict[str, Any]) -> str:
    trusted_ids = continuable_task_ids(manifest)
    root_id = str(manifest.get("continuation_parent_task_id") or "").strip()[:240]
    if root_id in trusted_ids:
        return root_id
    if len(trusted_ids) == 1:
        return trusted_ids[0]
    return ""


def _looks_like_continuation_directive(text: str) -> bool:
    normalized = str(text or "").strip().lower().replace("ё", "е")
    if not normalized:
        return False
    clauses = re.split(r"(?<=[.!?;])\s+|[\n\r]+|[—–]+", normalized)
    for clause in clauses:
        clause = clause.strip(" \t,.;:—–")
        if (
            not clause
            or "?" in clause
            or _CONTINUATION_NON_COMMAND_PATTERN.search(clause)
        ):
            continue
        for command in _CONTINUATION_IMPERATIVE_PATTERN.finditer(clause):
            prefix = clause[: command.start()].rstrip()
            negated_tail = re.search(
                r"\b(?:не|никогда(?:\s+\w+){0,3}\s+не|ни\s+за\s+что|перестань|хватит)\s*$",
                prefix[-64:],
                re.IGNORECASE,
            )
            if not negated_tail:
                return True
    return False


def _truth_guard_continuation_decision(envelope: TurnEnvelope) -> dict[str, Any] | None:
    parent_task_id = _trusted_continuation_parent(envelope.capability_manifest)
    if not parent_task_id or not _looks_like_continuation_directive(envelope.text):
        return None
    return normalize_decision(
        {
            "action": "continue_warmaster_mission",
            "continue_parent_task_id": parent_task_id,
            "confidence": 1.0,
            "rationale_summary": (
                "Server truth guard bound the explicit continuation command to the trusted recent task."
            ),
        }
    )


def _extract_object(text: str) -> dict[str, Any]:
    text = str(text or "").strip()
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("model did not return a JSON object")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("model response is not an object")
    return value


def _list_of_text(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:12]


def normalize_decision(raw: dict[str, Any]) -> dict[str, Any]:
    action = str(raw.get("action") or "").strip()
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"unsupported action: {action or '<empty>'}")
    reply = str(raw.get("reply") or "").strip()
    task = str(raw.get("task") or "").strip()
    request = raw.get("warmaster_request") if isinstance(raw.get("warmaster_request"), dict) else {}
    artifact = raw.get("artifact_delivery") if isinstance(raw.get("artifact_delivery"), dict) else {}
    pending = raw.get("pending_decision") if isinstance(raw.get("pending_decision"), dict) else {}
    normalized_request = {
        "user_request": str(request.get("user_request") or task).strip(),
        "capability_area": str(request.get("capability_area") or "unknown").strip().lower(),
        "why_warmaster_needed": str(request.get("why_warmaster_needed") or "").strip(),
        "expected_outcome": str(request.get("expected_outcome") or task).strip(),
        "success_conditions": _list_of_text(request.get("success_conditions")),
        "constraints": _list_of_text(request.get("constraints")),
        "known_missing_inputs": _list_of_text(request.get("known_missing_inputs")),
    }
    if normalized_request["capability_area"] not in {"research", "code", "image", "mixed", "administration", "unknown"}:
        normalized_request["capability_area"] = "unknown"
    if action in {"answer_in_chat", "ask_clarification"} and not reply:
        raise ValueError(f"{action} requires a non-empty reply")
    _reject_speech_only_execution_claim(action, reply)
    if action == "request_warmaster_mission" and (
        not normalized_request["user_request"] or not normalized_request["expected_outcome"]
    ):
        raise ValueError("request_warmaster_mission requires user_request and expected_outcome")
    if action == "create_administratum_task" and not task:
        raise ValueError("create_administratum_task requires task")
    artifact_delivery = {
        "artifact_id": str(artifact.get("artifact_id") or "").strip()[:240],
    }
    pending_decision_task_id = str(
        raw.get("pending_decision_task_id") or pending.get("task_id") or ""
    ).strip()[:240]
    if action != "answer_pending_decision":
        pending_decision_task_id = ""
    continue_parent_task_id = str(raw.get("continue_parent_task_id") or "").strip()[:240]
    if action != "continue_warmaster_mission":
        continue_parent_task_id = ""
    if action not in {"answer_in_chat", "ask_clarification"}:
        # Speech about an external action is synthesized only from the adapter's
        # factual result. Discard even a persuasive model claim here.
        reply = ""
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "action": action,
        "reply": reply,
        "task": task,
        "warmaster_request": normalized_request,
        "artifact_delivery": artifact_delivery,
        # This is intentionally empty here. resolve() binds both fields from
        # trusted transport context after the model has selected only an action.
        "pending_decision": {"task_id": "", "answer": ""},
        "pending_decision_task_id": pending_decision_task_id,
        "continue_parent_task_id": continue_parent_task_id,
        "confidence": confidence,
        "reason": str(raw.get("rationale_summary") or raw.get("reason") or "").strip()[:1_000],
    }


def warmaster_message(request: dict[str, Any]) -> str:
    parts = [
        "Запрос Шушуни к EyeOfTerror Abaddon.",
        "Шушуня задаёт намерение и критерии. Абаддон выбирает стратегический маршрут и бригадира; варбанда составляет детальный план и выполняет работу.",
        f"Область: {request.get('capability_area') or 'unknown'}",
        f"Исходный запрос пользователя: {request.get('user_request') or ''}",
        f"Ожидаемый результат: {request.get('expected_outcome') or ''}",
    ]
    if request.get("why_warmaster_needed"):
        parts.append(f"Почему нужен Абаддон: {request['why_warmaster_needed']}")
    if request.get("success_conditions"):
        parts.append("Критерии приёмки:\n" + "\n".join(f"- {item}" for item in request["success_conditions"]))
    if request.get("constraints"):
        parts.append("Ограничения:\n" + "\n".join(f"- {item}" for item in request["constraints"]))
    if request.get("known_missing_inputs"):
        parts.append("Что выяснить по ходу:\n" + "\n".join(f"- {item}" for item in request["known_missing_inputs"]))
    return "\n\n".join(parts)


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[: max(0, int(limit))]


def _bounded_parent_request(value: Any) -> dict[str, Any]:
    request = value if isinstance(value, dict) else {}
    result = {
        "user_request": _bounded_text(request.get("user_request"), _PARENT_FIELD_LIMIT),
        "capability_area": _bounded_text(request.get("capability_area"), 80),
        "why_warmaster_needed": _bounded_text(
            request.get("why_warmaster_needed"), _PARENT_FIELD_LIMIT
        ),
        "expected_outcome": _bounded_text(
            request.get("expected_outcome"), _PARENT_FIELD_LIMIT
        ),
    }
    for key in ("success_conditions", "constraints", "known_missing_inputs"):
        values = request.get(key) if isinstance(request.get(key), list) else []
        result[key] = [
            _bounded_text(item, _PARENT_LIST_ITEM_LIMIT)
            for item in values[:_PARENT_LIST_ITEMS]
            if _bounded_text(item, _PARENT_LIST_ITEM_LIMIT)
        ]
    return result


def continuation_message(candidate: dict[str, Any]) -> str:
    parent_task_id = str(candidate.get("parent_task_id") or "").strip()
    goal = str(candidate.get("goal") or "").strip()
    parent_spec = candidate.get("parent_spec") if isinstance(candidate.get("parent_spec"), dict) else {}
    parent_message = str(parent_spec.get("message") or "").strip()
    parent_request = (
        parent_spec.get("warmaster_request")
        if isinstance(parent_spec.get("warmaster_request"), dict)
        else {}
    )
    failure_guidance = (
        candidate.get("failure_guidance")
        if isinstance(candidate.get("failure_guidance"), dict)
        else {}
    )
    failure_summary = str(
        failure_guidance.get("explanation") or candidate.get("failure_summary") or ""
    ).strip()
    required_action = str(failure_guidance.get("required_action") or "").strip()
    parts = [
        "Новая связанная миссия по явной команде пользователя продолжить остановившуюся работу.",
        f"Родительская миссия: {parent_task_id}",
        f"Исходная цель: {goal}",
        (
            "Терминальный родительский run неизменяем. Не пытайся запускать его повторно: "
            "создай новый план и новую исполнимую миссию, сохранив связь с родителем."
        ),
    ]
    if parent_message:
        parts.append("Полная исходная спецификация родительской миссии:\n" + parent_message)
    elif parent_request:
        parts.append("Восстановленная исходная спецификация:\n" + warmaster_message(parent_request))
    if failure_summary:
        parts.append(f"Последняя подтверждённая причина остановки: {failure_summary}")
    if required_action:
        parts.append(f"Что обязательно исправить в новой стратегии: {required_action}")
    return "\n\n".join(parts)


class DecisionEngine:
    def __init__(self, settings: Settings, ledger: Ledger, situation: SituationAssembler, authority: Authority):
        self.settings = settings
        self.ledger = ledger
        self.situation = situation
        self.authority = authority

    def _continuation_candidate(
        self,
        manifest: dict[str, Any],
        parent_task_id: str,
    ) -> dict[str, Any]:
        """Bind identity/state from Vox, then enrich content from Core's ledger."""
        candidate = next(
            (
                dict(item)
                for item in continuable_task_catalog(manifest)
                if item.get("parent_task_id") == parent_task_id
            ),
            {},
        )
        if not candidate:
            return {}
        commitment = self.ledger.find_commitment_by_delegate_ref(parent_task_id)
        if not commitment or str(commitment.get("kind") or "") != "abaddon_mission":
            return candidate

        spec = commitment.get("spec") if isinstance(commitment.get("spec"), dict) else {}
        parent_spec = {
            "message": _bounded_text(spec.get("message"), _PARENT_MESSAGE_LIMIT),
            "warmaster_request": _bounded_parent_request(spec.get("warmaster_request")),
        }
        diagnostic = (
            commitment.get("diagnostic")
            if isinstance(commitment.get("diagnostic"), dict)
            else {}
        )
        failure_guidance = {
            "code": _bounded_text(diagnostic.get("code"), 160),
            "explanation": _bounded_text(
                diagnostic.get("explanation"), _FAILURE_FIELD_LIMIT
            ),
            "required_action": _bounded_text(
                diagnostic.get("required_action"), _FAILURE_FIELD_LIMIT
            ),
            "resume_condition": _bounded_text(
                diagnostic.get("resume_condition"), _FAILURE_FIELD_LIMIT
            ),
        }
        candidate["goal"] = _bounded_text(
            commitment.get("goal") or candidate.get("goal"),
            _PARENT_GOAL_LIMIT,
        )
        candidate["parent_spec"] = parent_spec
        candidate["failure_guidance"] = failure_guidance
        if failure_guidance["explanation"]:
            candidate["failure_summary"] = failure_guidance["explanation"]
        return candidate

    async def _model_call(self, envelope: TurnEnvelope, situation: dict[str, Any], repair: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(situation, ensure_ascii=False, separators=(",", ":"))},
        ]
        if repair:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Предыдущий JSON нарушил контракт. Исправь только формат/обязательные поля и верни один JSON. "
                        f"Ошибка: {repair[:1200]}"
                    ),
                }
            )
        request = {
            "model": envelope.model or self.settings.llm_model,
            "messages": messages,
            "temperature": 0.25,
            # The live 31B endpoint currently exposes a 6144-token context.
            # Situation compaction owns the input budget; keep enough headroom
            # for the chat template and a repair pass.
            "max_tokens": 1_200,
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False},
        }
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_sec) as client:
            response = await client.post(
                f"{self.settings.llm_base_url}/chat/completions",
                json=request,
                headers={"X-LLM-Route": "gemma", "X-LLM-Priority": "chat"},
            )
        response.raise_for_status()
        body = response.json()
        content = str((((body.get("choices") or [{}])[0].get("message") or {}).get("content")) or "")
        return _extract_object(content), {"request": request, "response": body}

    def _forced(self, envelope: TurnEnvelope) -> dict[str, Any]:
        if envelope.forced_action == "request_warmaster_mission":
            return normalize_decision(
                {
                    "action": envelope.forced_action,
                    "reply": "",
                    "task": envelope.text,
                    "warmaster_request": {
                        "user_request": envelope.text,
                        "capability_area": "unknown",
                        "why_warmaster_needed": "Пользователь явно вызвал Абаддона.",
                        "expected_outcome": envelope.text,
                        "success_conditions": [],
                        "constraints": [],
                        "known_missing_inputs": [],
                    },
                    "confidence": 1.0,
                    "rationale_summary": "Явная команда пользователя.",
                }
            )
        if envelope.forced_action == "answer_pending_decision":
            return normalize_decision(
                {
                    "action": envelope.forced_action,
                    "confidence": 1.0,
                    "rationale_summary": "Явный ответ на ожидающий вопрос.",
                }
            )
        if envelope.forced_action == "continue_warmaster_mission":
            return normalize_decision(
                {
                    "action": envelope.forced_action,
                    "confidence": 1.0,
                    "rationale_summary": "Явная команда продолжить подтверждённую остановившуюся миссию.",
                }
            )
        if envelope.forced_action == "create_administratum_task":
            return normalize_decision(
                {
                    "action": envelope.forced_action,
                    "task": envelope.text,
                    "reply": "",
                    "confidence": 1.0,
                    "rationale_summary": "Явная команда пользователя.",
                }
            )
        raise ValueError("this action cannot be forced without model interpretation")

    async def resolve(self, envelope: TurnEnvelope) -> dict[str, Any]:
        # Transport retries must not conflict merely because live roster,
        # memory recall or history changed while the first request was in
        # flight. The stable user intent is the idempotency identity; the full
        # situation remains in the model trace for audit.
        request_payload = {
            "session_id": envelope.session_id,
            "memory_namespace": envelope.memory_namespace,
            "source": envelope.source,
            "text": envelope.text,
            "image_attached": envelope.image_attached,
            "forced_action": envelope.forced_action,
            "correlation_id": envelope.correlation_id,
        }
        turn_id, cached = self.ledger.accept_turn(envelope.idempotency_key, request_payload)
        if cached:
            return cached
        situation = self.situation.assemble(envelope)
        model_trace: dict[str, Any] = {}
        degraded = False
        repair_error = ""
        try:
            if envelope.forced_action:
                decision = self._forced(envelope)
                model_trace = {"forced_action": envelope.forced_action}
            elif guarded := _truth_guard_continuation_decision(envelope):
                # An explicit continuation imperative plus an exact parent
                # identity from the trusted capability manifest is already a
                # complete, authorized intent. Do not let a conversational
                # model answer (even a harmless "не понял") erase the action.
                decision = guarded
                model_trace = {
                    "deterministic_continuation": {
                        "bound_parent_task_id": guarded["continue_parent_task_id"],
                    }
                }
            else:
                raw, model_trace = await self._model_call(envelope, situation)
                try:
                    decision = normalize_decision(raw)
                except Exception as exc:
                    repair_error = str(exc)
                    guarded = (
                        _truth_guard_continuation_decision(envelope)
                        if isinstance(exc, DecisionTruthError)
                        else None
                    )
                    if guarded:
                        decision = guarded
                        model_trace = {
                            "first": model_trace,
                            "truth_guard": {
                                "error": str(exc)[:1_200],
                                "bound_parent_task_id": guarded["continue_parent_task_id"],
                            },
                        }
                    else:
                        raw, repaired_trace = await self._model_call(
                            envelope,
                            situation,
                            repair=f"{exc}; raw={raw}",
                        )
                        model_trace = {"first": model_trace, "repair": repaired_trace}
                        decision = normalize_decision(raw)
        except Exception as exc:
            truth_guard = isinstance(exc, DecisionTruthError) or "execution_claim_without_effect" in str(exc)
            guarded = _truth_guard_continuation_decision(envelope) if truth_guard else None
            if guarded:
                # The model is not trusted to invent an effect. This action is
                # derived server-side from the user's directive plus the exact
                # parent id already bound by the trusted capability manifest.
                decision = guarded
            else:
                # Ordinary model failure still degrades to the existing rich
                # answering pass. A truth failure asks only when no trusted
                # task can be bound unambiguously.
                decision = {
                    "action": "ask_clarification" if truth_guard else "answer_in_chat",
                    "reply": (
                        "Я ничего не продолжил и не запустил: вижу несколько возможных остановившихся задач. "
                        "Назови, какую именно продолжать."
                        if truth_guard
                        else ""
                    ),
                    "task": "",
                    "warmaster_request": {},
                    "artifact_delivery": {},
                    "pending_decision": {"task_id": "", "answer": ""},
                    "pending_decision_task_id": "",
                    "continue_parent_task_id": "",
                    "confidence": 0.0,
                    "reason": f"Core speech-only degradation: {type(exc).__name__}: {exc}"[:1_000],
                }
            degraded = True
            model_trace = {"degraded_error": str(exc)[:2_000]}

        if (
            decision["action"] == "continue_warmaster_mission"
            and not _looks_like_continuation_directive(envelope.text)
        ):
            # This invariant applies even when the model selected the typed
            # continuation action directly. A trusted parent id grants scope,
            # not permission to reinterpret negation, questions or discussion
            # as an imperative.
            decision = {
                "action": "answer_in_chat",
                "reply": (
                    "Я не запустил продолжение: текущая реплика не является прямой "
                    "командой продолжить остановившуюся работу."
                ),
                "task": "",
                "warmaster_request": {},
                "artifact_delivery": {},
                "pending_decision": {"task_id": "", "answer": ""},
                "pending_decision_task_id": "",
                "continue_parent_task_id": "",
                "confidence": 1.0,
                "reason": "continuation_requires_imperative",
            }

        if decision["action"] == "answer_pending_decision":
            trusted_ids = pending_decision_ids(envelope.capability_manifest)
            root_id = str(envelope.capability_manifest.get("pending_decision_task_id") or "").strip()[:240]
            proposed_id = str(decision.get("pending_decision_task_id") or "").strip()
            if proposed_id in trusted_ids:
                bound_task_id = proposed_id
            elif len(trusted_ids) == 1:
                bound_task_id = trusted_ids[0]
            elif not proposed_id and root_id in trusted_ids:
                # With no explicit identity, an ordinary short answer naturally
                # belongs to the most recently asked question published at root.
                bound_task_id = root_id
            else:
                bound_task_id = ""
            decision["pending_decision_task_id"] = bound_task_id
            decision["pending_decision"] = {
                "task_id": bound_task_id,
                "answer": envelope.text.strip(),
            }

        if decision["action"] == "continue_warmaster_mission":
            trusted_ids = continuable_task_ids(envelope.capability_manifest)
            root_id = str(
                envelope.capability_manifest.get("continuation_parent_task_id") or ""
            ).strip()[:240]
            proposed_id = str(decision.get("continue_parent_task_id") or "").strip()
            if proposed_id:
                bound_parent_id = proposed_id if proposed_id in trusted_ids else ""
            elif root_id in trusted_ids:
                bound_parent_id = root_id
            elif len(trusted_ids) == 1:
                bound_parent_id = trusted_ids[0]
            else:
                bound_parent_id = ""
            decision["continue_parent_task_id"] = bound_parent_id

        authorization = self.authority.authorize(
            decision["action"],
            decision,
            envelope.capability_manifest,
            forced=bool(envelope.forced_action),
            context_scope=envelope.source,
        )
        if authorization.verdict != "auto":
            direct_explanation = authorization.code in {
                "artifact_catalog_unavailable",
                "incomplete_artifact_delivery",
                "artifact_not_in_capability",
                "continuation_unavailable",
                "continuation_task_mismatch",
            }
            decision = {
                "action": "ask_clarification",
                "reply": authorization.explanation if direct_explanation else (
                    f"Я не буду выполнять это молча: {authorization.explanation} "
                    "Уточни, какое именно разрешение ты даёшь для этого действия."
                ),
                "task": "",
                "warmaster_request": {},
                "artifact_delivery": {},
                "pending_decision": {"task_id": "", "answer": ""},
                "pending_decision_task_id": "",
                "continue_parent_task_id": "",
                "confidence": 1.0,
                "reason": authorization.code,
            }

        commitment = None
        effect = None
        effect_to_persist = None
        commitment_ref_id = None
        action = decision["action"]
        if action in {
            "request_warmaster_mission",
            "continue_warmaster_mission",
            "create_administratum_task",
            "deliver_artifact",
        }:
            commitment_id = new_id("commitment")
            effect_id = new_id("effect")
            if action == "request_warmaster_mission":
                request = decision["warmaster_request"]
                stable_task_id = "core-" + commitment_id.split("-", 1)[-1][:20]
                payload = {
                    "message": warmaster_message(request),
                    "task_id": stable_task_id,
                    "idempotency_key": effect_id,
                    "warmaster_request": request,
                }
                destination = "abaddon"
                goal = request.get("expected_outcome") or request.get("user_request")
                kind = "abaddon_mission"
            elif action == "continue_warmaster_mission":
                parent_task_id = decision["continue_parent_task_id"]
                existing = self.ledger.find_open_continuation(parent_task_id)
                existing_commitment = (
                    existing.get("commitment")
                    if isinstance(existing, dict)
                    and isinstance(existing.get("commitment"), dict)
                    else None
                )
                existing_effect = (
                    existing.get("effect")
                    if isinstance(existing, dict)
                    and isinstance(existing.get("effect"), dict)
                    else None
                )
                if existing_commitment and existing_effect:
                    existing_effect_id = str(existing_effect.get("id") or "").strip()
                    effect = {
                        "id": existing_effect_id,
                        "commitment_id": str(existing_commitment.get("id") or ""),
                        "kind": "continue_warmaster_mission",
                        "destination": "abaddon",
                        "payload": dict(existing_effect.get("payload") or {}),
                        "idempotency_key": existing_effect_id,
                        "max_attempts": 3,
                        "state": str(existing_effect.get("state") or ""),
                        "reused_existing": True,
                    }
                    commitment_ref_id = str(existing_commitment.get("id") or "")
                    model_trace = {
                        **model_trace,
                        "continuation_dedupe": {
                            "parent_task_id": parent_task_id,
                            "commitment_id": commitment_ref_id,
                            "effect_id": existing_effect_id,
                        },
                    }
                elif existing_commitment:
                    # An open linked child without its durable effect is an
                    # invariant failure. Do not create a twin to hide it.
                    decision = {
                        "action": "answer_in_chat",
                        "reply": (
                            "Продолжение этой задачи уже зарегистрировано, но его запуск сейчас "
                            "нельзя надёжно подтвердить. Новую копию я не создаю."
                        ),
                        "task": "",
                        "warmaster_request": {},
                        "artifact_delivery": {},
                        "pending_decision": {"task_id": "", "answer": ""},
                        "pending_decision_task_id": "",
                        "continue_parent_task_id": "",
                        "confidence": 1.0,
                        "reason": "existing_continuation_effect_missing",
                    }
                    action = "answer_in_chat"
                    commitment_ref_id = str(existing_commitment.get("id") or "")
                    effect = None
                else:
                    candidate = self._continuation_candidate(
                        envelope.capability_manifest,
                        parent_task_id,
                    )
                    stable_task_id = "core-" + commitment_id.split("-", 1)[-1][:20]
                    payload = {
                        "message": continuation_message(candidate),
                        "task_id": stable_task_id,
                        "parent_task_id": parent_task_id,
                        "continuation_of": parent_task_id,
                        "parent_spec": candidate.get("parent_spec") or {},
                        "failure_guidance": candidate.get("failure_guidance") or {},
                        "idempotency_key": effect_id,
                    }
                    destination = "abaddon"
                    goal = str(candidate.get("goal") or "Продолжить остановившуюся миссию.")
                    kind = "abaddon_mission"
            elif action == "create_administratum_task":
                payload = {
                    "task": decision["task"],
                    "source_text": envelope.text,
                    "session_id": envelope.session_id,
                    "source": envelope.source,
                    "model": envelope.model or self.settings.llm_model,
                    "idempotency_key": effect_id,
                }
                destination = "archive_adapter"
                goal = f"Записать в Administratum: {decision['task']}"
                kind = "administratum_task"
            else:
                delivery = decision["artifact_delivery"]
                client_request_id = str(
                    envelope.correlation_id or envelope.idempotency_key or ""
                ).strip()
                if client_request_id.startswith("archive-turn:"):
                    client_request_id = client_request_id[len("archive-turn:") :]
                payload = {
                    "artifact_id": delivery["artifact_id"],
                    "session_id": envelope.session_id,
                    "source": envelope.source,
                    "client_request_id": client_request_id[:160],
                    "idempotency_key": effect_id,
                }
                destination = "archive_artifact_adapter"
                goal = f"Доставить пользователю зарегистрированный артефакт {delivery['artifact_id']}."
                kind = "artifact_delivery"
            if effect is None and action in {
                "request_warmaster_mission",
                "continue_warmaster_mission",
                "create_administratum_task",
                "deliver_artifact",
            }:
                commitment = {
                    "id": commitment_id,
                    "kind": kind,
                    "owner": "shushunya",
                    "goal": goal,
                    "spec": payload,
                    "state": "queued",
                    "priority": 50,
                    "max_attempts": 3,
                    "delegate_kind": destination,
                    "honest_status": "Решение принято; реальный эффект ещё не подтверждён органом.",
                }
                effect = {
                    "id": effect_id,
                    "commitment_id": commitment_id,
                    "kind": action,
                    "destination": destination,
                    "payload": payload,
                    "idempotency_key": effect_id,
                    "max_attempts": 3,
                }
                effect_to_persist = effect
                commitment_ref_id = commitment_id

        attention = decide_attention(
            owner_waiting=True,
            urgency=1.0,
            novelty=1.0,
            actionability=1.0,
            owner_required=action == "ask_clarification",
        )
        resolution = {
            "ok": True,
            "turn_id": turn_id,
            "decision": decision,
            "capabilities": envelope.capability_manifest,
            "effect": effect,
            "commitment_id": commitment_ref_id,
            "attention": attention.__dict__,
            "core": {
                "degraded": degraded,
                "repair_error": repair_error,
                "authorization": authorization.__dict__,
                "situation_diagnostics": envelope.context.diagnostics,
            },
            # Audit records contain prompts/results but never model hidden
            # reasoning; Gemma is instructed to return only a rationale summary.
            "protocol": model_trace,
        }
        return self.ledger.save_turn_resolution(
            idempotency_key=envelope.idempotency_key,
            turn_id=turn_id,
            resolution=resolution,
            commitment=commitment,
            effect=effect_to_persist,
        )
