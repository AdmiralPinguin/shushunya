from fastapi import APIRouter
from pydantic import BaseModel
router = APIRouter(prefix="/mod1_emotion", tags=["mod1"])
class In(BaseModel):
    text: str
    intent: str = "neutral"
    intensity: float = 0.0
    tempo: float = 0.0
    seed: int | None = None
class Out(BaseModel):
    text: str
    markup: str | None = None
@router.post("", response_model=Out)
def map_emotion(payload: In) -> Out:
    return Out(text=payload.text, markup=None)
