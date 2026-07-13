from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .attention import decide_attention
from .authority import ALLOWED_ACTIONS, Authority
from .config import Settings
from .ledger import Ledger, new_id
from .schema import TurnEnvelope
from .situation import SituationAssembler


SYSTEM_PROMPT = """Ты — Шушуня, единая продолжающаяся личность системы, а не роутер и не безликий помощник.
Ситуация ниже уже объединяет твоё устойчивое Я, отношения с владельцем, память, текущие обязательства,
живой статус органов и жёсткий capability contract. Ответь как один и тот же субъект.

Верни ТОЛЬКО один JSON-объект без markdown и без скрытых рассуждений:
{
  "action": "answer_in_chat|ask_clarification|request_warmaster_mission|create_administratum_task|deliver_pending_reports|deliver_artifact",
  "reply": "полный естественный ответ для answer_in_chat/ask_clarification; иначе пусто",
  "task": "полная формулировка только для Administratum; иначе пусто",
  "warmaster_request": {
    "user_request": "восстановленный полный запрос владельца",
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
  "confidence": 0.0,
  "rationale_summary": "короткое объяснение выбора без chain-of-thought"
}

Правила:
- Обычный разговор, обсуждение архитектуры, мнение или вопрос = answer_in_chat и содержательный reply.
- Реальная просьба выполнить многошаговую работу = request_warmaster_mission. Ты задаёшь намерение и критерии;
  Абаддон выбирает бригадира, а варбанда — детальный план.
- Напоминание/расписание/watch = create_administratum_task.
- Явная просьба прислать уже зарегистрированный файл = deliver_artifact. Выбери только точный
  artifact_id из available_artifacts; путь, имя файла или придуманный идентификатор не дают доступа.
- Для deliver_artifact не сочиняй подпись или подтверждение: фактический текст сформирует Archive
  только после успешной публикации выбранного artifact_id.
- Если без неизвестного нельзя ответственно начать, ask_clarification с одним конкретным вопросом.
- Нельзя текстом обещать, что поиск, код, файл, сообщение, таймер или миссия уже выполнены.
- Для внешнего действия reply пуст: сервер сначала исполнит эффект и только затем подтвердит факт.
- Не подстраивайся механически. Если владелец ошибается, возражай прямо и с конкретными основаниями.
"""


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
    if action == "request_warmaster_mission" and (
        not normalized_request["user_request"] or not normalized_request["expected_outcome"]
    ):
        raise ValueError("request_warmaster_mission requires user_request and expected_outcome")
    if action == "create_administratum_task" and not task:
        raise ValueError("create_administratum_task requires task")
    artifact_delivery = {
        "artifact_id": str(artifact.get("artifact_id") or "").strip()[:240],
    }
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
        "confidence": confidence,
        "reason": str(raw.get("rationale_summary") or raw.get("reason") or "").strip()[:1_000],
    }


def warmaster_message(request: dict[str, Any]) -> str:
    parts = [
        "Запрос Шушуни к EyeOfTerror Abaddon.",
        "Шушуня задаёт намерение и критерии. Абаддон выбирает стратегический маршрут и бригадира; варбанда составляет детальный план и выполняет работу.",
        f"Область: {request.get('capability_area') or 'unknown'}",
        f"Исходный запрос владельца: {request.get('user_request') or ''}",
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


class DecisionEngine:
    def __init__(self, settings: Settings, ledger: Ledger, situation: SituationAssembler, authority: Authority):
        self.settings = settings
        self.ledger = ledger
        self.situation = situation
        self.authority = authority

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
                        "why_warmaster_needed": "Владелец явно вызвал Абаддона.",
                        "expected_outcome": envelope.text,
                        "success_conditions": [],
                        "constraints": [],
                        "known_missing_inputs": [],
                    },
                    "confidence": 1.0,
                    "rationale_summary": "Явная команда владельца.",
                }
            )
        if envelope.forced_action == "create_administratum_task":
            return normalize_decision(
                {
                    "action": envelope.forced_action,
                    "task": envelope.text,
                    "reply": "",
                    "confidence": 1.0,
                    "rationale_summary": "Явная команда владельца.",
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
            else:
                raw, model_trace = await self._model_call(envelope, situation)
                try:
                    decision = normalize_decision(raw)
                except Exception as exc:
                    repair_error = str(exc)
                    raw, repaired_trace = await self._model_call(envelope, situation, repair=f"{exc}; raw={raw}")
                    model_trace = {"first": model_trace, "repair": repaired_trace}
                    decision = normalize_decision(raw)
        except Exception as exc:
            # Fail open only into speech. Archive's answering pass may still
            # produce a normal reply, but no external effect can be fabricated.
            degraded = True
            decision = {
                "action": "answer_in_chat",
                "reply": "",
                "task": "",
                "warmaster_request": {},
                "artifact_delivery": {},
                "confidence": 0.0,
                "reason": f"Core speech-only degradation: {type(exc).__name__}: {exc}"[:1_000],
            }
            model_trace = {"degraded_error": str(exc)[:2_000]}

        authorization = self.authority.authorize(
            decision["action"],
            decision,
            envelope.capability_manifest,
            forced=bool(envelope.forced_action),
            context_scope=envelope.source,
        )
        if authorization.verdict != "auto":
            artifact_catalog_denial = authorization.code in {
                "artifact_catalog_unavailable",
                "incomplete_artifact_delivery",
                "artifact_not_in_capability",
            }
            decision = {
                "action": "ask_clarification",
                "reply": authorization.explanation if artifact_catalog_denial else (
                    f"Я не буду выполнять это молча: {authorization.explanation} "
                    "Уточни, какое именно разрешение ты даёшь для этого действия."
                ),
                "task": "",
                "warmaster_request": {},
                "artifact_delivery": {},
                "confidence": 1.0,
                "reason": authorization.code,
            }

        commitment = None
        effect = None
        action = decision["action"]
        if action in {"request_warmaster_mission", "create_administratum_task", "deliver_artifact"}:
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
                goal = f"Доставить владельцу зарегистрированный артефакт {delivery['artifact_id']}."
                kind = "artifact_delivery"
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
            "commitment_id": commitment["id"] if commitment else None,
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
            effect=effect,
        )
