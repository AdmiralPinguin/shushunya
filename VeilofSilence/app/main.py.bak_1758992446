import io
import os
import time
from typing import List, Optional

import numpy as np
import soxr
import soundfile as sf
import webrtcvad
import httpx
from fastapi import FastAPI, UploadFile, File, Form, Request
from pydantic import BaseModel
from faster_whisper import WhisperModel


def getenv(key: str, default: Optional[str] = None):
    value = os.getenv(key, default)
    if isinstance(default, int):
        try:
            return int(value)
        except Exception:
            return default
    return value


ASR_MODEL_NAME = getenv("ASR_MODEL_NAME", "large-v3")
ASR_COMPUTE_TYPE = getenv("ASR_COMPUTE_TYPE", "int8_float16")
ASR_BEAM_SIZE = getenv("ASR_BEAM_SIZE", 5)
ASR_VAD_AGGR = getenv("ASR_VAD_AGGRESSIVENESS", 2)
ASR_MIN_SEG_MS = getenv("ASR_MIN_SEG_MS", 1200)
ASR_MAX_SIL_MS = getenv("ASR_MAX_SIL_MS", 700)
ASR_PAD_MS = getenv("ASR_PAD_MS", 200)
TARGET_SR = 16_000

START_WORDS = [w.strip().lower() for w in getenv("START_WORDS", "").split(",") if w.strip()]
STOP_WORDS = [w.strip().lower() for w in getenv("STOP_WORDS", "").split(",") if w.strip()]

EYE_OF_TERROR_URL = getenv("EYE_OF_TERROR_URL", "http://127.0.0.1:8010/stt_result")

dialog_active = False

app = FastAPI(title="Veil of Silence", version="0.1.2")


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
    frames = [pcm16[i:i + frame_len] for i in range(0, len(pcm16), frame_len)]
    voiced = [vad.is_speech(fr, sr) if len(fr) == frame_len else False for fr in frames]
    return voiced, frame_ms


def segments_from_vad(voiced: List[bool], frame_ms: int, min_seg_ms: int, max_sil_ms: int, pad_ms: int) -> List[dict]:
    segs = []
    i = 0
    n = len(voiced)
    min_f = int(min_seg_ms / frame_ms)
    maxsil_f = int(max_sil_ms / frame_ms)
    pad_f = int(pad_ms / frame_ms)
    while i < n:
        while i < n and not voiced[i]:
            i += 1
        if i >= n:
            break
        start = i
        last = i
        while i < n:
            if voiced[i]:
                last = i
            if (i - last) > maxsil_f:
                break
            i += 1
        end = max(last, start + min_f - 1)
        s = max(0, start - pad_f) * frame_ms / 1000.0
        e = min(n - 1, end + pad_f)
        e = (e + 1) * frame_ms / 1000.0
        if e - s >= min_seg_ms / 1000.0:
            segs.append({"start": s, "end": e})
    return segs


_model: Optional[WhisperModel] = None


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(ASR_MODEL_NAME, device="cuda", compute_type=ASR_COMPUTE_TYPE)
    return _model


class SegmentOut(BaseModel):
    start: float
    end: float
    text: str


class STTResponse(BaseModel):
    model: str
    language: str
    text: str
    segments: List[SegmentOut]
    rt_factor: float


@app.get("/healthz")
def healthz():
    return {"status": "ok", "model": ASR_MODEL_NAME, "compute_type": ASR_COMPUTE_TYPE}


@app.post("/stt", response_model=STTResponse)
async def stt(
    request: Request,
    file: UploadFile = File(...),
    lang: str = Form("ru"),
    translate: bool = Form(False),
) -> STTResponse:
    global dialog_active
    t0 = time.time()

    raw = await file.read()
    audio16k = load_audio_to_mono_16k(raw)
    pcm16 = float32_to_pcm16(audio16k)
    voiced, frame_ms = frames_vad_pcm16(pcm16, ASR_VAD_AGGR, TARGET_SR)
    meta = segments_from_vad(voiced, frame_ms, ASR_MIN_SEG_MS, ASR_MAX_SIL_MS, ASR_PAD_MS)

    model = get_model()
    full: List[str] = []
    seg_out: List[SegmentOut] = []
    total = len(audio16k)
    dialog_id = request.query_params.get("dialog_id")

    for seg in meta:
        s = max(0, int(seg["start"] * TARGET_SR))
        e = min(total, int(seg["end"] * TARGET_SR))
        if e <= s:
            continue
        chunk = audio16k[s:e].astype(np.float32)
        segments, _ = model.transcribe(
            audio=chunk,
            language=lang,
            beam_size=ASR_BEAM_SIZE,
            task="translate" if translate else "transcribe",
            vad_filter=False,
            word_timestamps=False,
        )
        piece = " ".join(sg.text.strip() for sg in segments if sg.text.strip())
        seg_out.append(SegmentOut(start=float(seg["start"]), end=float(seg["end"]), text=piece))
        if piece:
            full.append(piece)
            clean = piece.lower().strip()
            if not dialog_active and START_WORDS and any(sw in clean for sw in START_WORDS):
                dialog_active = True
            if dialog_active:
                try:
                    async with httpx.AsyncClient(timeout=2.5) as client:
                        await client.post(
                            EYE_OF_TERROR_URL,
                            json={
                                "dialog_id": dialog_id,
                                "text": piece,
                                "start": seg["start"],
                                "end": seg["end"],
                                "final": False,
                            },
                        )
                except Exception:
                    pass
                if STOP_WORDS and any(sw in clean for sw in STOP_WORDS):
                    dialog_active = False

    if dialog_active:
        try:
            async with httpx.AsyncClient(timeout=2.5) as client:
                await client.post(
                    EYE_OF_TERROR_URL,
                    json={
                        "dialog_id": dialog_id,
                        "text": " ".join(full).strip(),
                        "final": True,
                    },
                )
        except Exception:
            pass

    rt = (time.time() - t0) / max(1e-6, len(audio16k) / TARGET_SR)
    return STTResponse(
        model=ASR_MODEL_NAME,
        language=("en" if translate else lang),
        text=" ".join(full).strip(),
        segments=seg_out,
        rt_factor=rt,
    )


if __name__ == "__main__":
    import uvicorn
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    uvicorn.run(
        app,
        host=os.getenv("SERVER_HOST", "0.0.0.0"),
        port=int(os.getenv("SERVER_PORT", "8011")),
    )
