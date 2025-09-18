from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

router = APIRouter()

@router.post("/mod1_emotion")
def mod1_emotion(payload: dict = Body(...)):
    text = payload.get("text","")
    emotion = payload.get("emotion","neutral")
    intensity = payload.get("intensity",0.5)
    return JSONResponse({"text": text, "emotion": emotion, "intensity": intensity})
