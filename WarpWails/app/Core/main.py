from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import io, re, numpy as np, soundfile as sf
from .backend import SileroRU
from app.Mod3_VoxTormenta.router import router as mod3

ENG = SileroRU()
TARGET_SR = 24000

app = FastAPI(title="WarpWails Core (silero+mod3)", version="0.4.2")
app.include_router(mod3)

class SpeakIn(BaseModel):
    text: str

def _to_mono_f32(w):
    if getattr(w, "ndim", 1) > 1: w = w.mean(axis=1)
    return w.astype(np.float32, copy=False)

def _resample_np(w, sr_in, sr_out):
    if sr_in == sr_out: return w
    x = np.arange(len(w), dtype=np.float32)
    xi = np.linspace(0, len(w)-1, int(len(w)*sr_out/sr_in), dtype=np.float32)
    return np.interp(xi, x, w).astype(np.float32, copy=False)

def _f32_to_s16le_bytes(w):
    w = np.clip(w, -1.0, 1.0)
    return (w*32767.0).astype("<i2").tobytes()

def _sentences(text: str):
    parts = re.split(r'(?<=[\.\?\!\…;:])\s+', text.strip())
    return [p for p in parts if p]

@app.post("/speak", response_class=Response)
def speak(payload: SpeakIn):
    wav, sr = ENG.tts(payload.text)
    buf = io.BytesIO()
    sf.write(buf, _to_mono_f32(wav), sr, format="WAV", subtype="PCM_16")
    return Response(content=buf.getvalue(), media_type="audio/wav")

@app.post("/speak_pcm")
def speak_pcm(payload: SpeakIn):
    chunks = _sentences(payload.text) or [payload.text]
    def gen():
        for sent in chunks:
            wav, sr = ENG.tts(sent)
            wav = _resample_np(_to_mono_f32(wav), sr, TARGET_SR)
            yield _f32_to_s16le_bytes(wav)
    return StreamingResponse(gen(), media_type="application/octet-stream")
