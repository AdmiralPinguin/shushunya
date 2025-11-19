from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List, Literal, Optional, Dict, Any

class TargetModel(BaseModel):
    name: Literal["20b","70b","7b"]
    purpose: Literal["code","chat","reason","summarize","plan","main"]

class ToolCall(BaseModel):
    tool: Literal[
        "tts.speak",
        "stt.transcribe",
        "render.display"     # простая фиксация текста в artifacts
    ]
    args: Dict[str, Any] = Field(default_factory=dict)

class Step(BaseModel):
    id: str
    kind: Literal["tool","model"]
    route: Optional[TargetModel] = None
    call: Optional[ToolCall] = None
    wait_for: List[str] = Field(default_factory=list)
    emit: Optional[str] = None

class Criteria(BaseModel):
    success_when: List[str] = Field(default_factory=list)
    deliver: List[str] = Field(default_factory=list)

class Plan(BaseModel):
    version: Literal["1.0"] = "1.0"
    route_parts: Dict[str, str] = Field(default_factory=dict)
    steps: List[Step]
    criteria: Criteria

class InboundMessage(BaseModel):
    text: Optional[str] = None
    audio_b64: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)

class OrchestratorResult(BaseModel):
    ok: bool
    artifacts: Dict[str, Any] = Field(default_factory=dict)
    logs: List[str] = Field(default_factory=list)
