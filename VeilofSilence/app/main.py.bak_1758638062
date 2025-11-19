import os, io, time
from typing import List
import numpy as np, soundfile as sf, webrtcvad
from fastapi import FastAPI, UploadFile, File, Form
from pydantic import BaseModel
from faster_whisper import WhisperModel
import soxr
import httpx

def getenv(k, d=None):
    v = os.getenv(k, d)
    if isinstance(d, int):
        try: return int(v)
        except: return d
    return v

ASR_MODEL_NAME   = getenv("ASR_MODEL_NAME", "large-v3")
ASR_COMPUTE_TYPE = getenv("ASR_COMPUTE_TYPE", "int8_float16")
ASR_BEAM_SIZE    = getenv("ASR_BEAM_SIZE", 5)
ASR_VAD_AGGR     = getenv("ASR_VAD_AGGRESSIVENESS", 2)
ASR_MIN_SEG_MS   = getenv("ASR_MIN_SEG_MS", 1200)
ASR_MAX_SIL_MS   = getenv("ASR_MAX_SIL_MS", 700)
ASR_PAD_MS       = getenv("ASR_PAD_MS", 200)
TARGET_SR        = 16000

app = FastAPI(title="Veil of Silence", version="0.1.1")
EYE_OF_TERROR_URL = os.getenv('EYE_OF_TERROR_URL','http://127.0.0.1:8010/stt_result')

def load_audio_to_mono_16k(file_bytes: bytes) -> np.ndarray:
    data, sr = sf.read(io.BytesIO(file_bytes), dtype="float32", always_2d=True)
    mono = data.mean(axis=1).astype(np.float32)
    if sr != TARGET_SR:
        mono = soxr.resample(mono, sr, TARGET_SR).astype(np.float32)
    return mono

def float32_to_pcm16(x: np.ndarray) -> bytes:
    x = np.clip(x, -1.0, 1.0)
    return (x * 32767.0).astype(np.int16).tobytes()

def frames_vad_pcm16(pcm16: bytes, vad_level: int, sr: int = TARGET_SR, frame_ms: int = 20):
    vad = webrtcvad.Vad(vad_level)
    frame_len = int(sr * frame_ms / 1000) * 2
    frames = [pcm16[i:i+frame_len] for i in range(0, len(pcm16), frame_len)]
    voiced = [vad.is_speech(fr, sr) if len(fr) == frame_len else False for fr in frames]
    return voiced, frame_ms

def segments_from_vad(voiced: List[bool], frame_ms: int,
                      min_seg_ms: int, max_sil_ms: int, pad_ms: int):
    segs, i, n = [], 0, len(voiced)
    min_f = int(min_seg_ms / frame_ms)
    maxsil_f = int(max_sil_ms / frame_ms)
    pad_f = int(pad_ms / frame_ms)
    while i < n:
        while i < n and not voiced[i]: i += 1
        if i >= n: break
        start = i; last = i
        while i < n:
            if voiced[i]: last = i
            if (i - last) > maxsil_f: break
            i += 1
        end = max(last, start + min_f - 1)
        s = max(0, start - pad_f) * frame_ms / 1000.0
        e = min(n - 1, end + pad_f)
        e = (e + 1) * frame_ms / 1000.0
        if e - s >= min_seg_ms / 1000.0:
            segs.append({"start": s, "end": e})
    return segs

_model: WhisperModel = None
def get_model() -> WhisperModel:
    global _model
    if _model is None:
        # CUDA будет использоваться CTranslate2, PyTorch не нужен
        _model = WhisperModel(ASR_MODEL_NAME, device="cuda", compute_type=ASR_COMPUTE_TYPE)
    return _model

class SegmentOut(BaseModel):
    start: float; end: float; text: str

class STTResponse(BaseModel):
    model: str; language: str; text: str; segments: List[SegmentOut]; rt_factor: float

@app.get("/healthz")
def healthz():
    return {"status": "ok", "model": ASR_MODEL_NAME, "compute_type": ASR_COMPUTE_TYPE}

@app.post("/stt", response_model=STTResponse)
async def stt(file: UploadFile = File(...), lang: str = Form("ru"), translate: bool = Form(False)):
    t0 = time.time()
    raw = await file.read()
    audio16k = load_audio_to_mono_16k(raw)
    pcm16 = float32_to_pcm16(audio16k)
    voiced, frame_ms = frames_vad_pcm16(pcm16, ASR_VAD_AGGR, TARGET_SR)
    meta = segments_from_vad(voiced, frame_ms, ASR_MIN_SEG_MS, ASR_MAX_SIL_MS, ASR_PAD_MS)

    model = get_model()
    full, seg_out = [], []
    total = len(audio16k)
    for seg in meta:
        s = max(0, int(seg["start"] * TARGET_SR))
        e = min(total, int(seg["end"] * TARGET_SR))
        if e <= s: continue
        chunk = audio16k[s:e].astype(np.float32)
        segments, _ = model.transcribe(
            audio=chunk, language=lang, beam_size=ASR_BEAM_SIZE,
            task="translate" if translate else "transcribe",
            vad_filter=False, word_timestamps=False
        )
        piece = " ".join(sg.text.strip() for sg in segments if sg.text.strip())
        seg_out.append(SegmentOut(start=float(seg["start"]), end=float(seg["end"]), text=piece))
        if piece: full.append(piece)
    # forward to Eye Of Terror
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            await client.post(EYE_OF_TERROR_URL, json={"text": " ".join(full).strip()})
    except Exception:
        pass

    rt = (time.time() - t0) / max(1e-6, len(audio16k) / TARGET_SR)
    # ## FINAL_FORWARD
    if dialog_active:
        try:
            async with httpx.AsyncClient(timeout=2.5) as client:
                await client.post(EYE_OF_TERROR_URL, json={"text": piece.strip(), "final": True})
        except Exception:
            pass
    return STTResponse(model=ASR_MODEL_NAME, language=("en" if translate else lang),
                       text=" ".join(full).strip(), segments=seg_out, rt_factor=rt)

if __name__ == "__main__":
    import uvicorn, dotenv
    dotenv.load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    uvicorn.run(app, host=os.getenv("SERVER_HOST","0.0.0.0"),
                     port=int(os.getenv("SERVER_PORT","8011")))

# ================= VAD + HOTWORD =================
import webrtcvad
import collections

vad = webrtcvad.Vad(2)  # чувствительность 0–3 (чем выше — тем чувствительнее)
frame_buffer = collections.deque(maxlen=50)

HOTWORD = "эй шушуня"
dialog_active = False

def process_audio_frame(frame_bytes, sample_rate=16000):
    """Обработка входного звукового фрейма (для VAD)."""
    is_speech = vad.is_speech(frame_bytes, sample_rate)
    frame_buffer.append((frame_bytes, is_speech))
    return is_speech

async def handle_transcript(text: str):
    """
    Обработка расшифровки текста.
    - ждет хотворд
    - при его появлении включает диалоговый режим
    - по 'конец' или 'стоп' завершает диалог
    """
    global dialog_active
    clean = text.lower().strip()

    if not dialog_active:
        if HOTWORD in clean:
            dialog_active = True
            print("🔥 Хотворд словлен, включаем диалоговый режим")
            return {"event": "dialog_start"}
    else:
        if "конец" in clean or "стоп" in clean:
            dialog_active = False
            print("💤 Диалог завершён")
            return {"event": "dialog_end"}
        else:
            print(f"👉 Транскрипт в диалоге: {clean}")
            return {"event": "dialog_text", "text": clean}
