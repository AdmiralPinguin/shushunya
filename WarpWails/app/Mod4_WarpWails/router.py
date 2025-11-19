from fastapi import APIRouter, UploadFile, File, Query, Response, HTTPException
import io, os, glob, random
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

router = APIRouter(prefix="/mod4_warpwails", tags=["mod4_warpwails"])

TARGET_SR = 24000
ASSET_ROOT = os.environ.get("WARPWAILS_ASSETS", "assets/warpwails")

# ===== basic i/o =====

def read_audio_mono24k(bytes_in: bytes):
    x, sr = sf.read(io.BytesIO(bytes_in), always_2d=True)
    x = np.mean(x, axis=1).astype(np.float32)
    if sr != TARGET_SR:
        g = np.gcd(sr, TARGET_SR)
        up = TARGET_SR // g
        down = sr // g
        x = resample_poly(x, up, down).astype(np.float32)
    return x


def write_wav_pcm16(x: np.ndarray, sr: int = TARGET_SR) -> bytes:
    x = np.clip(x, -1.0, 1.0)
    buf = io.BytesIO()
    sf.write(buf, x, sr, subtype='PCM_16', format='WAV')
    return buf.getvalue()

# ===== asset helpers =====
def list_assets():
    cats = {}
    for cat in ("laughs", "creaks", "whispers"):
        path = os.path.join(ASSET_ROOT, cat)
        files = sorted(glob.glob(os.path.join(path, "*.wav")))
        cats[cat] = [os.path.relpath(f, ASSET_ROOT) for f in files]
    return cats


def load_asset(relpath: str) -> np.ndarray:
    full = os.path.join(ASSET_ROOT, relpath)
    if not os.path.isfile(full):
        raise FileNotFoundError(relpath)
    x, sr = sf.read(full, always_2d=True)
    x = np.mean(x, axis=1).astype(np.float32)
    if sr != TARGET_SR:
        g = np.gcd(sr, TARGET_SR)
        up = TARGET_SR // g
        down = sr // g
        x = resample_poly(x, up, down).astype(np.float32)
    return x

# ===== synthesis =====
def env_adsr(n, sr, a=0.02, d=0.05, s=0.6, r=0.1):
    a_n = int(a * sr)
    d_n = int(d * sr)
    r_n = int(r * sr)
    s_n = max(0, n - a_n - d_n - r_n)
    env = np.zeros(n, dtype=np.float32)
    if a_n > 0:
        env[:a_n] = np.linspace(0, 1, a_n)
    if d_n > 0:
        env[a_n:a_n + d_n] = np.linspace(1, s, d_n)
    if s_n > 0:
        env[a_n + d_n:a_n + d_n + s_n] = s
    if r_n > 0:
        env[a_n + d_n + s_n:] = np.linspace(s, 0, r_n)
    return env


def synth_wail(duration_s=0.8, seed=0):
    random.seed(seed)
    np.random.seed(seed)
    sr = TARGET_SR
    n = int(duration_s * sr)
    t = np.arange(n) / sr
    f0 = random.uniform(120.0, 420.0)
    f1 = f0 * random.uniform(1.5, 3.0)
    # glissando
    f = np.linspace(f0, f1, n)
    phase = 2 * np.pi * np.cumsum(f) / sr
    tone = 0.6 * np.sin(phase)
    noise = 0.3 * np.random.randn(n)
    y = tone + noise
    y *= env_adsr(n, sr, a=0.01, d=0.1, s=0.4, r=0.2)
    # light nonlinearity
    y = np.tanh(2.5 * y)
    return y.astype(np.float32)

# ===== mixing =====
def mix_at(voice: np.ndarray, sfx: np.ndarray, where: int, gain: float = 0.5):
    out = voice.copy()
    L = len(out)
    r = min(len(sfx), L - where)
    if r <= 0:
        return out
    out[where:where + r] += gain * sfx[:r]
    return np.clip(out, -1.2, 1.2)

# ===== API =====
@router.get("/assets")
def assets():
    return {"root": ASSET_ROOT, "categories": list_assets()}


@router.post("/synthesize")
async def synth(
    duration_s: float = Query(0.8, ge=0.1, le=5.0),
    seed: int = Query(0),
):
    y = synth_wail(duration_s=duration_s, seed=seed)
    return Response(content=write_wav_pcm16(y), media_type="audio/wav")


@router.post("/mix")
async def mix(
    voice: UploadFile = File(...),
    preset: str = Query("squeak_rush", description="squeak_rush | laugh_pulse | whisper_bed | assets:<relpath>"),
    seed: int = Query(0),
    count: int = Query(3, ge=1, le=16),
    min_gap_s: float = Query(0.2, ge=0.0),
    max_gap_s: float = Query(1.0, ge=0.0),
    sfx_gain: float = Query(0.35, ge=0.0, le=2.0),
):
    raw = await voice.read()
    v = read_audio_mono24k(raw)
    L = len(v)

    rng = random.Random(seed)

    if preset.startswith("assets:"):
        rel = preset.split(":", 1)[1]
        sfx = load_asset(rel)
        # sprinkle at intervals
        pos = 0
        out = v.copy()
        while pos < L:
            gap = rng.uniform(min_gap_s, max_gap_s)
            pos += int(gap * TARGET_SR)
            if pos >= L:
                break
            out = mix_at(out, sfx, pos, gain=sfx_gain)
        return Response(content=write_wav_pcm16(out), media_type="audio/wav")

    # procedural presets
    out = v.copy()
    pos = 0
    while pos < L and count > 0:
        gap = rng.uniform(min_gap_s, max_gap_s)
        pos += int(gap * TARGET_SR)
        if pos >= L:
            break
        if preset == "squeak_rush":
            y = synth_wail(duration_s=rng.uniform(0.3, 0.9), seed=rng.randint(0, 1_000_000))
        elif preset == "laugh_pulse":
            # short bursts
            y = synth_wail(duration_s=rng.uniform(0.15, 0.35), seed=rng.randint(0, 1_000_000)) * 0.8
        elif preset == "whisper_bed":
            n = int(rng.uniform(0.5, 1.2) * TARGET_SR)
            y = (0.25 * np.random.randn(n)).astype(np.float32)
        else:
            raise HTTPException(400, f"unknown preset '{preset}'")
        out = mix_at(out, y, pos, gain=sfx_gain)
        count -= 1

    return Response(content=write_wav_pcm16(out), media_type="audio/wav")
