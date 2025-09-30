from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse
import requests, io

router = APIRouter()
API = "http://127.0.0.1:8009"
TO = 60  # seconds

@router.post("/speak_full")
def speak_full(payload: dict = Body(...)):
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    # 1) Emotion (stub)
    r = requests.post(f"{API}/mod1_emotion",
                      json={"text": text,
                            "emotion": payload.get("emotion","neutral"),
                            "intensity": payload.get("intensity",0.5)},
                      timeout=TO)
    r.raise_for_status()
    text2 = r.json().get("text", text)

    # 2) TTS → WAV
    r = requests.post(f"{API}/speak",
                      json={"text": text2, "speaker": payload.get("speaker")},
                      timeout=TO)
    r.raise_for_status()
    dry_bytes = r.content

    # 3) VoiceFX
    r = requests.post(f"{API}/mod3_voicefx",
                      data=dry_bytes,
                      headers={"Content-Type":"audio/wav"},
                      timeout=TO)
    r.raise_for_status()
    wet_bytes = r.content

    # 4) SFX (stub)
    r = requests.post(f"{API}/mod4_sfx",
                      data=wet_bytes,
                      headers={"Content-Type":"audio/wav"},
                      timeout=TO)
    r.raise_for_status()
    sfx_bytes = r.content

    # 5) MasterFX (stub)
    r = requests.post(f"{API}/mod5_masterfx",
                      data=sfx_bytes,
                      headers={"Content-Type":"audio/wav"},
                      timeout=TO)
    r.raise_for_status()
    out_bytes = r.content

    return StreamingResponse(io.BytesIO(out_bytes), media_type="audio/wav")
