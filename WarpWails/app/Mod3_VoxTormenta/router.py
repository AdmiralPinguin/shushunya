from fastapi import APIRouter, UploadFile, File, Query, Response
import subprocess, tempfile, os

router = APIRouter(prefix="/mod3_voicefx", tags=["mod3"])

PRESETS = {
    "imp_light": dict(semitones=+3.0, hpf=80,  lpf=6200, thr=-18.0, ratio=3.5, atk=6,  rel=140, ceil=-3.0, sr=24000, ch=1),
    "neutral":   dict(semitones=+0.0, hpf=70,  lpf=6000, thr=-18.0, ratio=3.0, atk=6,  rel=120, ceil=-3.0, sr=24000, ch=1),
}

def _filter(cfg: dict) -> str:
    f = 2.0 ** (cfg["semitones"] / 12.0)
    atempo = 1.0 / f
    chain = [
        f"asetrate=48000*{f:.9f},aresample=48000,atempo={atempo:.9f}",
        f"highpass=f={cfg['hpf']}",
        f"lowpass=f={cfg['lpf']}",
        f"acompressor=threshold={cfg['thr']}dB:ratio={cfg['ratio']}:attack={cfg['atk']}:release={cfg['rel']}:makeup=8",
        f"alimiter=limit={(10**(cfg['ceil']/20.0)):.4f}",
    ]
    return ",".join(chain)

@router.post("")
async def voicefx(preset: str = Query("imp_light"), file: UploadFile = File(...)):
    cfg = PRESETS.get(preset, PRESETS["imp_light"])
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "in.wav"); dst = os.path.join(td, "out.wav")
        with open(src, "wb") as f: f.write(await file.read())
        cmd = ["ffmpeg","-y","-hide_banner","-loglevel","error","-i",src,"-af",_filter(cfg),
               "-c:a","pcm_s16le","-ar",str(cfg["sr"]),"-ac",str(cfg["ch"]),dst]
        run = subprocess.run(cmd, capture_output=True)
        if run.returncode != 0: return Response(content=run.stderr or b"ffmpeg failed", status_code=500, media_type="text/plain")
        data = open(dst,"rb").read()
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE": return Response(content=b"bad wav", status_code=500, media_type="text/plain")
    return Response(content=data, media_type="audio/wav")
