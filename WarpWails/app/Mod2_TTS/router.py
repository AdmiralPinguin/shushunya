from fastapi import APIRouter, Response
from pydantic import BaseModel
import io, wave, numpy as np, torch

router = APIRouter()
device = "cuda" if torch.cuda.is_available() else "cpu"
model, *_ = torch.hub.load('snakers4/silero-models','silero_tts',
                           language='ru', speaker='v4_ru', trust_repo=True)
model = model.to(device)
DEFAULT_SPK = 'aidar'
DEFAULT_SR  = 24000

class SpeakIn(BaseModel):
    text: str
    speaker: str | None = None
    sample_rate: int | None = None

def _tts_bytes(text: str, speaker: str, sr: int) -> bytes:
    with torch.inference_mode():
        try:
            audio = model.apply_tts(text=text, speaker=speaker, sample_rate=sr)
        except TypeError:
            try:
                audio = model.apply_tts(ssml_text=text, speaker=speaker, sample_rate=sr)
            except TypeError:
                audio = model.apply_tts(text, speaker, sr)
    if isinstance(audio, torch.Tensor):
        audio = audio.cpu().numpy()
    audio16 = (np.clip(audio, -1, 1) * 32767).astype('<i2')
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
        wf.writeframes(audio16.tobytes())
    return buf.getvalue()

@router.post("/speak", response_class=Response)
def speak(payload: SpeakIn):
    spk = payload.speaker or DEFAULT_SPK
    sr  = payload.sample_rate or DEFAULT_SR
    return Response(content=_tts_bytes(payload.text, spk, sr),
                    media_type="audio/wav")
