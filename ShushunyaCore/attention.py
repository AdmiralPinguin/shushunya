from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AttentionDecision:
    mode: str
    score: float
    explanation: str


def decide_attention(
    *,
    owner_waiting: bool,
    urgency: float,
    novelty: float,
    actionability: float,
    owner_required: bool,
    duplicate: bool = False,
) -> AttentionDecision:
    if owner_waiting:
        return AttentionDecision("immediate", 1.0, "Владелец прямо ждёт ответ на текущий ход.")
    score = 0.35 * urgency + 0.25 * novelty + 0.25 * actionability + (0.15 if owner_required else 0.0)
    if duplicate:
        score -= 0.55
    score = max(0.0, min(1.0, score))
    if score >= 0.78:
        return AttentionDecision("immediate", score, "Событие срочное, новое и требует действия владельца.")
    if score >= 0.38:
        return AttentionDecision("digest", score, "Полезно сообщить при следующем возвращении владельца.")
    return AttentionDecision("silent", score, "Новых практически полезных сведений недостаточно для прерывания.")
