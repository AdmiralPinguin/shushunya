from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import StreamingResponse, Response
import torch, re, io, numpy as np
from typing import List

router = APIRouter()
SR = 24000
SPEAKER_DEFAULT = "kseniya"

_model = None
def _load():
    global _model
    if _model is None:
        pack = torch.hub.load('snakers4/silero-models','silero_tts', language='ru', speaker='v4_ru')
        # pack это (model, example_text)
        _model = pack[0] if isinstance(pack, tuple) else pack
    return _model

def _split_text(t: str, max_len: int = 220) -> List[str]:
    t = re.sub(r'\s+', ' ', t).strip()
    if not t: return []
    parts = re.split(r'([\.!\?\…]+)', t)
    chunks, buf = [], ''
    for i in range(0, len(parts), 2):
        sent = parts[i].strip()
        tail = parts[i+1] if i+1 < len(parts) else ''
        piece = (sent + (tail or '')).strip()
        if not piece: continue
        if len(buf) + 1 + len(piece) <= max_len:
            buf = (buf + ' ' + piece).strip()
        else:
            if buf: chunks.append(buf)
            if len(piece) <= max_len:
                chunks.append(piece)
                buf = ''
            else:
                words = piece.split(' ')
                cur = ''
                for w in words:
                    if len(cur) + 1 + len(w) <= max_len:
                        cur = (cur + ' ' + w).strip()
                    else:
                        if cur: chunks.append(cur)
                        cur = w
                if cur: chunks.append(cur)
                buf = ''
    if buf: chunks.append(buf)
    return chunks

def _pcm_s16le(x: np.ndarray) -> bytes:
    x = np.clip(x, -1.0, 1.0)
    return (x * 32767.0).astype('<i2').tobytes()

def _synthesize(text: str, speaker: str) -> bytes:
    m = _load()
    chunks = _split_text(text)
    if not chunks:
        raise HTTPException(status_code=400, detail="empty text after normalization")
    waves = []
    for ch in chunks:
        wav = m.apply_tts(text=ch, speaker=speaker, sample_rate=SR)
        if isinstance(wav, torch.Tensor):
            wav = wav.detach().cpu().numpy()
        waves.append(wav)
        pad = np.zeros(int(0.12 * SR), dtype=np.float32)
        waves.append(pad)
    audio = np.concatenate(waves) if waves else np.zeros(1, np.float32)
    return _pcm_s16le(audio)

@router.get("/speak_stream")
def speak_stream(text: str = Query(...), speaker: str = Query(SPEAKER_DEFAULT)):
    payload = _synthesize(text.strip(), speaker or SPEAKER_DEFAULT)
    return StreamingResponse(io.BytesIO(payload), media_type="audio/wav")

@router.post("/speak")
def speak(payload: dict):
    text = (payload.get("text") or "").strip()
    speaker = payload.get("speaker") or SPEAKER_DEFAULT
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    wav = _synthesize(text, speaker)
    return Response(content=wav, media_type="audio/wav")
