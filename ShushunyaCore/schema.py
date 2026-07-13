from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


TurnAction = Literal[
    "answer_in_chat",
    "ask_clarification",
    "request_warmaster_mission",
    "create_administratum_task",
    "deliver_pending_reports",
    "deliver_artifact",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TurnContext(StrictModel):
    persona: str = ""
    recalled_memory: str = ""
    live_roster: str = ""
    pending_reports: dict[str, Any] = Field(default_factory=dict)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class TurnEnvelope(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=240)
    session_id: str = Field(default="shushunya-main", max_length=160)
    memory_namespace: str = Field(default="shushunya", max_length=160)
    source: str = Field(default="app", max_length=80)
    text: str = Field(default="", max_length=100_000)
    image_attached: bool = False
    model: str = Field(default="", max_length=240)
    recent_history: list[dict[str, Any]] = Field(default_factory=list)
    capability_manifest: dict[str, Any]
    context: TurnContext = Field(default_factory=TurnContext)
    forced_action: TurnAction | None = None
    correlation_id: str = Field(default="", max_length=240)


class PreferenceEvidence(StrictModel):
    action_kind: str = Field(min_length=1, max_length=120)
    target_scope: str = Field(default="*", max_length=240)
    context_scope: str = Field(default="*", max_length=240)
    verdict: Literal["approved_once", "rejected", "delegate_future", "never_auto"]
    evidence: str = Field(default="", max_length=4_000)


class AgendaRequest(StrictModel):
    title: str = Field(min_length=1, max_length=500)
    kind: str = Field(default="background", max_length=120)
    value: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    urgency: float = Field(default=0.0, ge=0.0, le=1.0)
    cost: float = Field(default=0.1, ge=0.0, le=1.0)
    risk: float = Field(default=0.0, ge=0.0, le=1.0)
    stop_condition: str = Field(min_length=1, max_length=2_000)
    budget_seconds: int = Field(default=300, ge=1, le=86_400)
    max_attempts: int = Field(default=3, ge=1, le=20)
    payload: dict[str, Any] = Field(default_factory=dict)
