from fastapi import APIRouter, UploadFile, File, Query, Response, HTTPException
import io
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly, butter, sosfilt

router = APIRouter(prefix="/mod3_voxtormenta", tags=["mod3_voxtormenta"])

TARGET_SR = 24000
TARGET_CH = 1

# ===== Helpers =====

def read_audio_to_mono24k(file_bytes: bytes):
    data, sr = sf.read(io.BytesIO(file_bytes), always_2d=True)
    # to mono
    x = np.mean(data, axis=1).astype(np.float32)
    if sr != TARGET_SR:
        # high-quality polyphase resample
        g = np.gcd(sr, TARGET_SR)
        up = TARGET_SR // g
        down = sr // g
        x = resample_poly(x, up, down).astype(np.float32)
    return x, TARGET_SR


def write_wav_pcm16(x: np.ndarray, sr: int) -> bytes:
    x = np.clip(x, -1.0, 1.0)
    buf = io.BytesIO()
    sf.write(buf, x, sr, subtype='PCM_16', format='WAV')
    return buf.getvalue()

# simple compressor (RMS-based soft knee)

def comp_soft(x, threshold_db=-18.0, ratio=3.0, attack_ms=5.0, release_ms=50.0, sr=TARGET_SR):
    thr = 10 ** (threshold_db / 20.0)
    atk = np.exp(-1.0 / (sr * attack_ms / 1000.0))
    rel = np.exp(-1.0 / (sr * release_ms / 1000.0))
    env = 0.0
    gain = np.zeros_like(x)
    eps = 1e-9
    for i, s in enumerate(np.abs(x) + eps):
        if s > env:
            env = atk * env + (1 - atk) * s
        else:
            env = rel * env + (1 - rel) * s
        over = max(env / thr, 1.0)
        g = over ** (-(1 - 1/ratio))
        gain[i] = g
    return x * gain

# EQ shelves via biquad

def shelf_filter(x, sr, freq=200.0, gain_db=0.0, high=False):
    if abs(gain_db) < 0.1:
        return x
    # simple 1st order shelf using butter as approximation with small Q tweak
    order = 2
    if high:
        sos = butter(order, freq, btype='high', fs=sr, output='sos')
    else:
        sos = butter(order, freq, btype='low', fs=sr, output='sos')
    y = sosfilt(sos, x)
    g = 10 ** (gain_db / 20.0)
    return y * g

# naive pitch shift by resampling (formants move with it)

def pitch_shift_resample(x, sr, semitones=0.0):
    if abs(semitones) < 0.01:
        return x
    ratio = 2 ** (semitones / 12.0)
    # resample to shift pitch, then back to original length
    y = resample_poly(x, int(1000*ratio), 1000)
    # time-stretch back to original length
    y2 = resample_poly(y, len(x), len(y))
    return y2.astype(np.float32)

# Simple saturation

def saturate(x, drive=0.0):
    if drive <= 0.0:
        return x
    k = 1 + drive * 9.0  # drive 0..1 => k 1..10
    return np.tanh(k * x)

# ===== Presets =====

PRESETS = {
    "imp_light": dict(  # чуть выше и чище
        semitones=+3.0,
        low_shelf_db=-2.0,
        high_shelf_db=+3.0,
        comp_db=-20.0,
        comp_ratio=2.5,
        drive=0.1,
    ),
    "daemon_low": dict(  # ниже и мрачнее
        semitones=-4.0,
        low_shelf_db=+2.5,
        high_shelf_db=-2.0,
        comp_db=-22.0,
        comp_ratio=3.0,
        drive=0.2,
    ),
    "radio_clean": dict(  # звонкость и лёгкая компрессия
        semitones=0.0,
        low_shelf_db=-3.0,
        high_shelf_db=+4.0,
        comp_db=-18.0,
        comp_ratio=2.0,
        drive=0.05,
    ),
}

# ===== API =====

@router.get("/presets")
def list_presets():
    return {"presets": list(PRESETS.keys())}


@router.post("/process")
async def process(
    file: UploadFile = File(...),
    preset: str = Query("imp_light"),
    semitones: float | None = Query(None),
    low_shelf_db: float | None = Query(None),
    high_shelf_db: float | None = Query(None),
    comp_db: float | None = Query(None),
    comp_ratio: float | None = Query(None),
    drive: float | None = Query(None),
):
    if preset not in PRESETS:
        raise HTTPException(400, f"unknown preset '{preset}'")
    cfg = PRESETS[preset].copy()
    # override with explicit params
    for k, v in dict(semitones=semitones, low_shelf_db=low_shelf_db, high_shelf_db=high_shelf_db,
                     comp_db=comp_db, comp_ratio=comp_ratio, drive=drive).items():
        if v is not None:
            cfg[k] = v

    raw = await file.read()
    x, sr = read_audio_to_mono24k(raw)

    # chain
    y = pitch_shift_resample(x, sr, cfg.get('semitones', 0.0))
    y = shelf_filter(y, sr, 180.0, cfg.get('low_shelf_db', 0.0), high=False)
    y = shelf_filter(y, sr, 3800.0, cfg.get('high_shelf_db', 0.0), high=True)
    y = comp_soft(y, threshold_db=cfg.get('comp_db', -20.0), ratio=cfg.get('comp_ratio', 2.5), sr=sr)
    y = saturate(y, drive=cfg.get('drive', 0.1))

    out = write_wav_pcm16(y, sr)
    return Response(content=out, media_type="audio/wav")
