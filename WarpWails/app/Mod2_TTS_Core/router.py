from fastapi import APIRouter, Response, Query, Form, HTTPException
import io, numpy as np, soundfile as sf
from scipy.signal import resample_poly

# Silero TTS (PyTorch)
import torch

router = APIRouter(prefix="/mod2_tts", tags=["mod2_tts"])
TARGET_SR = 24000

_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
_TTS = None
_LANG = "ru"  # основной сценарий — русский

# известные русские спикеры silero
RU_SPEAKERS = ["aidar", "baya", "kseniya", "xenia", "eugene", "random"]

def _get_model():
    global _TTS
    if _TTS is None:
        # скачает веса при первом запуске
        _TTS, _ = torch.hub.load(
            repo_or_dir="snakers4/silero-models",
            model="silero_tts",
            language=_LANG,
            speaker="v3_1_ru",
            trust_repo=True,
        )
        _TTS.to(_DEVICE)
        _TTS.eval()
    return _TTS

def _write_wav16(x: np.ndarray, sr: int = TARGET_SR) -> bytes:
    x = np.clip(x, -1.0, 1.0).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, x, sr, subtype="PCM_16", format="WAV")
    return buf.getvalue()

def tts_mono24k(text: str, speaker: str = "xenia", sr: int = TARGET_SR) -> np.ndarray:
    if not text or text.strip() == "":
        raise ValueError("empty text")
    if speaker not in RU_SPEAKERS:
        speaker = "xenia"
    model = _get_model()
    with torch.inference_mode():
        audio = model.apply_tts(text=text, speaker=speaker, sample_rate=sr)
    # Silero возвращает python list/torch; приводим к np.float32 mono
    x = np.asarray(audio, dtype=np.float32)
    if sr != TARGET_SR:
        g = np.gcd(sr, TARGET_SR)
        x = resample_poly(x, TARGET_SR // g, sr // g).astype(np.float32)
    return x

@router.get("/voices")
def voices():
    return {"language": _LANG, "speakers": RU_SPEAKERS, "sample_rate": TARGET_SR, "device": _DEVICE}

@router.post("/speak")
async def speak(text: str = Form(...), speaker: str = Query("xenia")):
    try:
        x = tts_mono24k(text=text, speaker=speaker, sr=TARGET_SR)
    except Exception as e:
        raise HTTPException(400, str(e))
    return Response(content=_write_wav16(x, TARGET_SR), media_type="audio/wav")
