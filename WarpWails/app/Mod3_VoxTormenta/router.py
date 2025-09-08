from fastapi import APIRouter, UploadFile, File, Query, Response
import subprocess, tempfile, os

router = APIRouter(prefix="/mod3_voicefx", tags=["mod3"])

def _db_to_lin(db: float) -> float: return 10.0 ** (db / 20.0)

PRESETS = {
    "imp_light": dict(pitch_semitones=+3.0, hpf_hz=80,  lpf_hz=6200,
                      comp_thr_db=-18.0, comp_ratio=3.5, comp_atk_ms=6, comp_rel_ms=140,
                      limiter_ceil_db=-3.0, out_sr=24000, out_ch=1),
    "neutral_clean": dict(pitch_semitones=0.0, hpf_hz=70, lpf_hz=6000,
                      comp_thr_db=-18.0, comp_ratio=3.5, comp_atk_ms=6, comp_rel_ms=140,
                      limiter_ceil_db=-3.0, out_sr=24000, out_ch=1),
    "abyss_low": dict(pitch_semitones=-7.0, hpf_hz=50, lpf_hz=3800,
                      comp_thr_db=-18.0, comp_ratio=4.0, comp_atk_ms=6, comp_rel_ms=160,
                      limiter_ceil_db=-3.0, out_sr=24000, out_ch=1),
}

def _run_ffmpeg(src: str, dst: str, af: str, sr: int, ch: int):
    cmd = ["ffmpeg","-y","-hide_banner","-loglevel","error",
           "-i", src, "-af", af,
           "-c:a","pcm_s16le","-ar", str(sr), "-ac", str(ch), dst]
    subprocess.check_call(cmd)

@router.post("")
async def voicefx(
    file: UploadFile = File(...),
    preset: str = Query("imp_light"),
    pitch_semitones: float | None = Query(None),
):
    cfg = dict(PRESETS.get(preset, PRESETS["imp_light"]))
    if pitch_semitones is not None: cfg["pitch_semitones"] = pitch_semitones

    factor = 2.0 ** (cfg["pitch_semitones"]/12.0)
    inv    = 1.0 / factor
    ceil   = _db_to_lin(cfg["limiter_ceil_db"])

    af_rubber = (
      f"rubberband=pitch={factor}:formant=1,"
      f"highpass=f={cfg['hpf_hz']},lowpass=f={cfg['lpf_hz']},"
      f"acompressor=threshold={cfg['comp_thr_db']}:ratio={cfg['comp_ratio']}"
      f":attack={cfg['comp_atk_ms']}:release={cfg['comp_rel_ms']}:makeup=8,"
      f"alimiter=limit={ceil}"
    )
    af_fallback = (
      f"asetrate=48000*{factor},aresample=48000,atempo={inv},"
      f"highpass=f={cfg['hpf_hz']},lowpass=f={cfg['lpf_hz']},"
      f"acompressor=threshold={cfg['comp_thr_db']}:ratio={cfg['comp_ratio']}"
      f":attack={cfg['comp_atk_ms']}:release={cfg['comp_rel_ms']}:makeup=8,"
      f"alimiter=limit={ceil}"
    )

    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "in.wav")
        out = os.path.join(td, "out.wav")
        with open(src, "wb") as f: f.write(await file.read())
        try:
            _run_ffmpeg(src, out, af_rubber, cfg["out_sr"], cfg["out_ch"])
        except subprocess.CalledProcessError:
            try:
                _run_ffmpeg(src, out, af_fallback, cfg["out_sr"], cfg["out_ch"])
            except subprocess.CalledProcessError as e:
                return Response(content=f"voicefx failed: {e}", status_code=500, media_type="text/plain")
        with open(out, "rb") as f: data = f.read()
    return Response(content=data, media_type="audio/wav")
