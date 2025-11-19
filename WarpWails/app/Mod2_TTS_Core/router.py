from fastapi import APIRouter, Response, Query, Form, HTTPException
import os, io, importlib, json
import numpy as np, soundfile as sf
from scipy.signal import resample_poly

TARGET_SR = 24000
router = APIRouter(prefix="/mod2_tts", tags=["mod2_tts"])

def _to_mono24k(x: np.ndarray, sr: int) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1).astype(np.float32)
    if sr != TARGET_SR:
        g = np.gcd(sr, TARGET_SR)
        x = resample_poly(x, TARGET_SR // g, sr // g).astype(np.float32)
    return x

def _wav_bytes(x: np.ndarray, sr: int = TARGET_SR) -> bytes:
    x = np.clip(x, -1.0, 1.0).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, x, sr, subtype="PCM_16", format="WAV")
    return buf.getvalue()

# ---------- backends ----------
def _py_spec_func():
    spec = os.environ.get("SHU_TTS_SPEC", "").strip()
    if ":" in spec:
        mod, fn = spec.split(":", 1)
        m = importlib.import_module(mod)
        return getattr(m, fn)
    return None

def _http_call(text: str, speaker: str, base: str) -> np.ndarray:
    import urllib.request, urllib.parse
    for path in ("/speak", "/tts"):
        url = base.rstrip("/") + path
        payload = {"text": text, "speaker": speaker, "sr": TARGET_SR}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                if (r.getheader("Content-Type") or "").lower().startswith("audio/wav"):
                    wav = r.read()
                    x, sr = sf.read(io.BytesIO(wav), always_2d=True)
                    return _to_mono24k(x.mean(axis=1).astype(np.float32), sr)
        except Exception:
            continue
    raise RuntimeError("HTTP TTS: /speak|/tts не отвечает")

_silero_model = None
def _silero(text: str, speaker: str) -> np.ndarray:
    import torch
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    global _silero_model
    if _silero_model is None:
        _silero_model, _ = torch.hub.load(
            repo_or_dir="snakers4/silero-models",
            model="silero_tts",
            language="ru",
            speaker="v3_1_ru",
            trust_repo=True,
        )
        if hasattr(_silero_model, "to"): _silero_model.to(dev)
        if hasattr(_silero_model, "eval"): _silero_model.eval()
    with torch.inference_mode():
        audio = _silero_model.apply_tts(text=text, speaker=speaker, sample_rate=TARGET_SR)
    return _to_mono24k(np.asarray(audio, dtype=np.float32), TARGET_SR)

_BACKEND_KIND = None
_BACKEND_FN = None
def _resolve_backend():
    global _BACKEND_KIND, _BACKEND_FN
    if _BACKEND_KIND is not None:
        return
    fn = _py_spec_func()
    if fn:
        _BACKEND_KIND, _BACKEND_FN = "py", fn
        return
    base = os.environ.get("SHU_TTS_HTTP", "").strip()
    if base:
        _BACKEND_KIND, _BACKEND_FN = "http", base
        return
    _BACKEND_KIND, _BACKEND_FN = "silero", None

def tts_mono24k(text: str, speaker: str = "baya", sr: int = TARGET_SR) -> np.ndarray:
    if not text or not text.strip():
        raise ValueError("empty text")
    _resolve_backend()
    if _BACKEND_KIND == "py":
        y = _BACKEND_FN(text=text, speaker=speaker, sample_rate=sr)
        if not isinstance(y, np.ndarray):
            raise RuntimeError("PY backend должен вернуть np.ndarray")
        return _to_mono24k(y, sr)
    if _BACKEND_KIND == "http":
        return _http_call(text, speaker, _BACKEND_FN)
    return _silero(text, speaker)

@router.get("/voices")
def voices():
    _resolve_backend()
    return {
        "backend": _BACKEND_KIND,
        "sample_rate": TARGET_SR,
        "env": {
            "SHU_TTS_SPEC": os.environ.get("SHU_TTS_SPEC", ""),
            "SHU_TTS_HTTP": os.environ.get("SHU_TTS_HTTP", ""),
        },
    }

@router.post("/speak")
async def speak(text: str = Form(...), speaker: str = Query("baya")):
    try:
        x = tts_mono24k(text=text, speaker=speaker, sr=TARGET_SR)
    except Exception as e:
        raise HTTPException(400, str(e))
    return Response(content=_wav_bytes(x, TARGET_SR), media_type="audio/wav")
