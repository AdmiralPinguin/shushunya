from fastapi import APIRouter, Body, Query, HTTPException
from fastapi.responses import StreamingResponse
from app.Core.config import CFG
from typing import Optional, Generator
import numpy as np
import torch
import struct, threading

router = APIRouter()
_MODEL = None
_LOCK = threading.Lock()

SR, CH, BPS = 24000, 1, 16
SPEAKERS = {"aidar","baya","kseniya","xenia","eugene","random"}
CHUNK_BYTES = int(CFG.stream.chunk_bytes)
DEFAULT_SPK = str(CFG.tts.speaker_default)

def _load_model():
    import torch
    global _MODEL
    with _LOCK:
        if _MODEL is None:
            obj = torch.hub.load('snakers4/silero-models', 'silero_tts',
                                 language='ru', speaker='v4_ru')
            _MODEL = obj[0] if isinstance(obj, (tuple, list)) else obj
    # device
    dev=CFG.tts.device
    if dev=='auto':
        dev='cuda' if (hasattr(torch,'cuda') and torch.cuda.is_available()) else 'cpu'
    try:
        _MODEL.to(dev)
    except Exception:
        pass
    return _MODEL

def _wav_header(num_samples: int, sr: int = SR, ch: int = CH, bps: int = BPS) -> bytes:
    byte_rate = sr * ch * (bps // 8); block_align = ch * (bps // 8)
    data_size = num_samples * block_align; riff_size = 36 + data_size
    return struct.pack('<4sI4s4sIHHIIHH4sI',
        b'RIFF', riff_size, b'WAVE', b'fmt ', 16, 1, ch, sr,
        byte_rate, block_align, bps, b'data', data_size)

def _pcm_s16le(x: np.ndarray) -> bytes:
    x = np.clip(x, -1.0, 1.0); return (x * 32767.0).astype('<i2').tobytes()

def _norm_speaker(s: str) -> str:
    s = s.strip().lower()
    if s == "aidar_v2": s = "aidar"
    if s not in SPEAKERS:
        raise HTTPException(status_code=400, detail=f"speaker must be one of: {', '.join(sorted(SPEAKERS))}")
    return s

def _synthesize(text: str, speaker: str) -> np.ndarray:
    m = _load_model()
    audio = m.apply_tts(text=text, speaker=speaker, sample_rate=SR)
    if isinstance(audio, torch.Tensor): audio = audio.detach().cpu().numpy()
    return audio.astype(np.float32)

def _stream_wav(pcm: bytes, chunk: int = CHUNK_BYTES) -> Generator[bytes, None, None]:
    yield _wav_header(len(pcm)//2)
    for i in range(0, len(pcm), chunk): yield pcm[i:i+chunk]

@router.get("/speak_stream")
@router.post("/speak_stream")
def speak_stream(
    text: Optional[str] = Query(default=None),
    speaker: str = Query(default=DEFAULT_SPK),
    payload: Optional[dict] = Body(default=None),
):
    if not text and isinstance(payload, dict): text = payload.get("text")
    if not text or not text.strip(): raise HTTPException(status_code=400, detail="text is required")
    spk = _norm_speaker(speaker)
    pcm = _pcm_s16le(_synthesize(text.strip(), speaker=spk))
    return StreamingResponse(_stream_wav(pcm), media_type="audio/wav")
