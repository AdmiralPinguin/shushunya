from fastapi import APIRouter, Body, Query, HTTPException
from fastapi.responses import StreamingResponse
from typing import Optional, Generator
import numpy as np
import torch
import io
import struct
import threading

router = APIRouter()
_MODEL = None
_MODEL_LOCK = threading.Lock()

SR = 24000
CH = 1
BPS = 16  # s16le

def _load_model():
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            _MODEL = torch.hub.load('snakers4/silero-models', 'silero_tts',
                                    language='ru', speaker='v4_ru')  # модуль 2, как задано
    return _MODEL

def _wav_header(num_samples: int, sr: int = SR, ch: int = CH, bps: int = BPS) -> bytes:
    byte_rate = sr * ch * (bps // 8)
    block_align = ch * (bps // 8)
    data_size = num_samples * block_align
    riff_size = 36 + data_size
    return struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF', riff_size, b'WAVE', b'fmt ', 16, 1, ch,
        sr, byte_rate, block_align, bps, b'data', data_size
    )

def _pcm_s16le(x: np.ndarray) -> bytes:
    x = np.clip(x, -1.0, 1.0)
    return (x * 32767.0).astype('<i2').tobytes()

def _synthe_

python3 - <<'PY'
import re, pathlib
p = pathlib.Path("app/Core/main.py")
s = p.read_text()
imp = "from app.Mod2_TTS.stream_router import router as tts_stream_router"
if imp not in s:
    s = s.replace("from fastapi import FastAPI", "from fastapi import FastAPI\n"+imp)
if "app.include_router(tts_stream_router)" not in s:
    s = re.sub(r"(app\s*=\s*FastAPI\(\).*\n)", r"\1app.include_router(tts_stream_router)\n", s, count=1)
p.write_text(s)
print("wired /speak_stream")
