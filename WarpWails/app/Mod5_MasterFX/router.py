from fastapi import APIRouter, Request, HTTPException, Response
import tempfile, subprocess, shutil, os, logging

router = APIRouter()
LOG = logging.getLogger("MasterFX")
FFMPEG = shutil.which("ffmpeg")
if not FFMPEG:
    raise RuntimeError("ffmpeg not found")

def _run(cmd): return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def _goblin_whisper(in_wav: str, out_wav: str):
    chain = ",".join([
        "pan=stereo|c0=c0|c1=c0",              # mono → stereo
        "highpass=f=70",
        "lowpass=f=9000",
        "deesser=f=5500:t=0.5:w=3500",         # правильный де-эссер
        "equalizer=f=3200:t=h:width=200:g=3",
        "aecho=0.35:0.6:18:0.25",
        "chorus=0.4:0.7:15:0.25:0.5:2",
        "acompressor=threshold=-14dB:ratio=3:attack=5:release=120:makeup=2:soft_knee=6",
        "loudnorm=I=-16:TP=-1.2:LRA=10:dual_mono=false:print_format=none",
        "alimiter=limit=-1dB"
    ])
    cmd = [
        FFMPEG, "-hide_banner", "-y",
        "-i", in_wav,
        "-filter_complex", chain,
        "-ar", "24000", "-ac", "2", "-sample_fmt", "s16",
        out_wav
    ]
    res = _run(cmd)
    if res.returncode != 0:
        LOG.error(res.stderr.decode("utf-8", "ignore"))
        raise RuntimeError("ffmpeg masterfx failed")

@router.post("/mod5_masterfx")
async def mod5_masterfx(request: Request, preset: str = "GOBLIN_WHISPER"):
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="no audio body")
    with tempfile.TemporaryDirectory(prefix="m5_") as td:
        inp = os.path.join(td, "in.wav")
        out = os.path.join(td, "out.wav")
        with open(inp, "wb") as f: f.write(data)
        if preset.upper() != "GOBLIN_WHISPER":
            raise HTTPException(status_code=400, detail="only GOBLIN_WHISPER supported now")
        _goblin_whisper(inp, out)
        with open(out, "rb") as f: out_bytes = f.read()
    return Response(content=out_bytes, media_type="audio/wav")
