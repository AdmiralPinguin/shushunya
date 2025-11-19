from fastapi import APIRouter, UploadFile, File, Query, Response, Form
import io, os, random
import numpy as np
import soundfile as sf
from scipy.signal import fftconvolve, resample_poly

# импортируем части из модулей 2,3,4
from app.Mod2_TTS_Core.router import tts_mono24k, TARGET_SR as SR_TTS
from app.Mod3_VoxTormenta.router import pitch_shift_resample, shelf_filter, comp_soft, saturate, PRESETS as VT_PRESETS
from app.Mod4_WarpWails.router import synth_wail, mix_at

router = APIRouter(prefix="/mod5_daemon", tags=["mod5_daemon"])

TARGET_SR = 24000
IR_ROOT = os.environ.get("IMPULSE_ASSETS", "assets/impulses")

# ===== io =====
def read_mono24k(bytes_in: bytes):
    x, sr = sf.read(io.BytesIO(bytes_in), always_2d=True)
    x = np.mean(x, axis=1).astype(np.float32)
    if sr != TARGET_SR:
        g = np.gcd(sr, TARGET_SR)
        up = TARGET_SR // g
        down = sr // g
        x = resample_poly(x, up, down).astype(np.float32)
    return x

def write_wav16(x: np.ndarray, sr: int = TARGET_SR) -> bytes:
    x = np.clip(x, -1.0, 1.0)
    buf = io.BytesIO()
    sf.write(buf, x, sr, subtype="PCM_16", format="WAV")
    return buf.getvalue()

# ===== effects =====
def pre_emphasis(x, k=0.0):
    if k <= 0.0:
        return x
    y = np.empty_like(x)
    y[0] = x[0]
    for i in range(1, len(x)):
        y[i] = x[i] - k * x[i-1]
    return y

def waveshaper(x, drive=0.0):
    if drive <= 0.0:
        return x
    k = 1 + 9 * drive
    return np.tanh(k * x)

def bitcrush(x, bits=16, downsample=1):
    bits = int(bits)
    if bits >= 16 and downsample <= 1:
        return x
    q = 2 ** bits
    y = np.round((x * 0.5 + 0.5) * (q - 1)) / (q - 1)
    y = (y - 0.5) * 2
    if downsample > 1:
        y = y[::downsample]
        y = np.repeat(y, downsample)[:len(x)]
    return y.astype(np.float32)

def convolve_ir(x, ir: np.ndarray, wet=0.2):
    if ir is None or len(ir) == 0 or wet <= 0.0:
        return x
    y = fftconvolve(x, ir, mode="full")[:len(x)]
    return (1 - wet) * x + wet * y

def limiter(x, thresh=0.98):
    out = x.copy()
    m = np.max(np.abs(out))
    if m > thresh:
        out = out / (m / thresh)
    return out

# ===== assets =====
def load_ir(relpath: str) -> np.ndarray:
    full = os.path.join(IR_ROOT, relpath)
    if not os.path.isfile(full):
        return np.zeros(0, dtype=np.float32)
    ir, sr = sf.read(full, always_2d=True)
    ir = np.mean(ir, axis=1).astype(np.float32)
    if sr != TARGET_SR:
        g = np.gcd(sr, TARGET_SR)
        up = TARGET_SR // g
        down = sr // g
        ir = resample_poly(ir, up, down).astype(np.float32)
    m = np.max(np.abs(ir))
    if m > 0:
        ir = ir / m
    return ir

# ===== API =====
@router.post("/render")
async def render(
    file: UploadFile = File(...),
    preemp: float = Query(0.0, ge=0.0, le=0.97),
    drive: float = Query(0.35, ge=0.0, le=1.0),
    crush_bits: int = Query(12, ge=4, le=16),
    crush_down: int = Query(1, ge=1, le=8),
    ir: str | None = Query(None, description="relpath в assets/impulses или None"),
    ir_wet: float = Query(0.25, ge=0.0, le=1.0),
    out_gain: float = Query(0.9, ge=0.0, le=2.0),
):
    raw = await file.read()
    x = read_mono24k(raw)

    y = pre_emphasis(x, k=preemp)
    y = waveshaper(y, drive=drive)
    y = bitcrush(y, bits=crush_bits, downsample=crush_down)

    ir_arr = load_ir(ir) if ir else None
    y = convolve_ir(y, ir_arr, wet=ir_wet)

    y *= out_gain
    y = limiter(y, 0.98)

    return Response(content=write_wav16(y), media_type="audio/wav")

@router.post("/render_text")
async def render_text(
    text: str = Form(...),
    speaker: str = Query("xenia"),
    # Mod3 (VoxTormenta) пресет
    vt_preset: str = Query("imp_light"),
    # Mod4 (WarpWails)
    ww_preset: str = Query("squeak_rush", description="squeak_rush | laugh_pulse | whisper_bed | off"),
    ww_count: int = Query(3, ge=0, le=16),
    ww_gain: float = Query(0.35, ge=0.0, le=2.0),
    # Mod5 (демонический финал)
    preemp: float = Query(0.0, ge=0.0, le=0.97),
    drive: float = Query(0.35, ge=0.0, le=1.0),
    crush_bits: int = Query(12, ge=4, le=16),
    crush_down: int = Query(1, ge=1, le=8),
    ir: str | None = Query(None),
    ir_wet: float = Query(0.25, ge=0.0, le=1.0),
    out_gain: float = Query(0.9, ge=0.0, le=2.0),
):
    # 1) TTS (Mod2)
    y = tts_mono24k(text=text, speaker=speaker, sr=TARGET_SR)

    # 2) Mod3 (VoxTormenta)
    cfg = VT_PRESETS.get(vt_preset, VT_PRESETS["imp_light"]).copy()
    y = pitch_shift_resample(y, TARGET_SR, cfg.get("semitones", 0.0))
    y = shelf_filter(y, TARGET_SR, 180.0, cfg.get("low_shelf_db", 0.0), high=False)
    y = shelf_filter(y, TARGET_SR, 3800.0, cfg.get("high_shelf_db", 0.0), high=True)
    y = comp_soft(y, threshold_db=cfg.get("comp_db", -20.0), ratio=cfg.get("comp_ratio", 2.5), sr=TARGET_SR)
    y = saturate(y, drive=cfg.get("drive", 0.1))

    # 3) Mod4 (WarpWails)
    if ww_preset != "off" and ww_count > 0:
        rng = random.Random(0)
        L = len(y)
        pos = 0
        out = y.copy()
        cnt = ww_count
        while pos < L and cnt > 0:
            gap = rng.uniform(0.2, 1.0)
            pos += int(gap * TARGET_SR)
            if pos >= L:
                break
            if ww_preset == "squeak_rush":
                sfx = synth_wail(duration_s=rng.uniform(0.3, 0.9), seed=rng.randint(0, 1_000_000))
            elif ww_preset == "laugh_pulse":
                sfx = synth_wail(duration_s=rng.uniform(0.15, 0.35), seed=rng.randint(0, 1_000_000)) * 0.8
            elif ww_preset == "whisper_bed":
                n = int(rng.uniform(0.5, 1.2) * TARGET_SR)
                sfx = (0.25 * np.random.randn(n)).astype(np.float32)
            else:
                sfx = np.zeros(0, dtype=np.float32)
            out = mix_at(out, sfx, pos, gain=ww_gain)
            cnt -= 1
        y = out

    # 4) Mod5 (финал)
    y = pre_emphasis(y, k=preemp)
    y = waveshaper(y, drive=drive)
    y = bitcrush(y, bits=crush_bits, downsample=crush_down)
    ir_arr = load_ir(ir) if ir else None
    y = convolve_ir(y, ir_arr, wet=ir_wet)
    y *= out_gain
    y = limiter(y, 0.98)

    return Response(content=write_wav16(y), media_type="audio/wav")

@router.get("/ir_examples")
async def ir_examples():
    return {
        "root": IR_ROOT,
        "examples": [
            "halls/long_hall.wav",
            "halls/metal_corridor.wav",
            "cathedrals/imperial_cathedral.wav",
        ]
    }
