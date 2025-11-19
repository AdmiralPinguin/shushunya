from __future__ import annotations
from typing import List, Literal, Optional, Dict, Any
from fastapi import APIRouter
from pydantic import BaseModel, Field
from .engine import Engine, load_config

router = APIRouter()
_cfg = load_config()
_engine = Engine(_cfg)

class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str

class GenerateRequest(BaseModel):
    messages: List[Message]
    max_new_tokens: Optional[int] = Field(default=None)
    temperature: Optional[float] = Field(default=None)
    top_p: Optional[float] = Field(default=None)

class GenerateResponse(BaseModel):
    content: str

@router.get("/healthz")
async def healthz() -> Dict[str, Any]:
    return {
        "status": "ok",
        "backend": _engine.backend,
        "model": _cfg["engine"].get("lmstudio_model") if _engine.backend == "lmstudio" else _cfg["engine"]["model_id"],
        "port": _cfg["server"]["port"],
    }

@router.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    kwargs = {
        k: v for k, v in {
            "max_new_tokens": req.max_new_tokens,
            "temperature": req.temperature,
            "top_p": req.top_p,
        }.items() if v is not None
    }
    out = await _engine.generate([m.model_dump() for m in req.messages], **kwargs)
    return GenerateResponse(content=out)
