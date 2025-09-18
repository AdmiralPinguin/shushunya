from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse
import requests, io

router = APIRouter()

API = "http://127.0.0.1:8009"

@router.post("/speak_full")
def speak_full(payload: dict = Body(...)):
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    # Модуль 1: эмоции (пока заглушка, текст не меняет)
    emo = requests.post(f"{API}/mod1_emotion", json={
        "text": text,
        "emotion": payload.get("emotion","neutral"),
        "intensity": payload.get("intensity",0.5)
    }).json()
    text2 = emo["text"]

    # Модуль 2: TTS → WAV
    dry = requests.post(f"{API}/speak", json={"text": text2, "speaker": payload.get("speaker") or None})
    dry_bytes = dry.content

    # Модуль 3: VoiceFX
    wet = requests.post(f"{API}/mod3_voicefx", data=dry_bytes, headers={"Content-Type":"audio/wav"})
    wet_bytes = wet.content

    # Модуль 4: SFX вставки
    sfx = requests.post(f"{API}/mod4_sfx", data=wet_bytes, headers={"Content-Type":"audio/wav"})
    sfx_bytes = sfx.content

    # Модуль 5: MasterFX
    mast = requests.post(f"{API}/mod5_masterfx", data=sfx_bytes, headers={"Content-Type":"audio/wav"})
    mast_bytes = mast.content

    return StreamingResponse(io.BytesIO(mast_bytes), media_type="audio/wav")
