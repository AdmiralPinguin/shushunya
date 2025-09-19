from fastapi import APIRouter, Request, HTTPException, Response
import tempfile, subprocess, shutil, os

router = APIRouter()
FFMPEG = shutil.which("ffmpeg")
if not FFMPEG:
    raise RuntimeError("ffmpeg not found")

def _run(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def _have(name: str) -> bool:
    r = _run([FFMPEG, "-hide_banner", "-filters"])
    return r.returncode == 0 and f" {name} " in r.stdout

def _build_chain() -> str:
    want = ["pan","highpass","lowpass","equalizer","aecho","chorus","acompressor","loudnorm","alimiter"]
    have = {k: _have(k) for k in want}
    chain = []
    if have["pan"]:         chain.append("pan=stereo|c0=c0|c1=c0")
    if have["highpass"]:    chain.append("highpass=f=70")
    if have["lowpass"]:     chain.append("lowpass=f=9000")
    if have["equalizer"]:   chain.append("equalizer=f=3200:t=h:w=200:g=3")
    if have["aecho"]:       chain.append("aecho=0.35:0.6:18:0.25")
    if have["chorus"]:      chain.append("chorus=0.4:0.7:15:0.25:0.5:2")
    if have["acompressor"]: chain.append("acompressor=threshold=-14dB:ratio=3:attack=5:release=120:makeup=2:soft_knee=6")
    if have["loudnorm"]:    chain.append("loudnorm=I=-16:TP=-1.2:LRA=10:print_format=none")
    if have["alimiter"]:    chain.append("alimiter=limit=-1dB")
    if not chain:           chain.append("anull")
    return ",".join(chain)

def _master(inp: str, out: str):
    chain = _build_chain()
    r = _run([FFMPEG, "-hide_banner", "-y",
              "-i", inp,
              "-af", chain,            # линейная цепь
              "-ar", "24000", "-ac", "2", "-sample_fmt", "s16",
              out])
    if r.returncode != 0:
        msg = (r.stderr or "").strip().splitlines()[-1] if r.stderr else "no stderr"
        raise RuntimeError(f"ffmpeg masterfx failed: {msg}")

@router.post("/mod5_masterfx")
async def mod5_masterfx(request: Request):
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="no audio body")
    with tempfile.TemporaryDirectory(prefix="m5_") as td:
        inp = os.path.join(td, "in.wav"); out = os.path.join(td, "out.wav")
        with open(inp, "wb") as f: f.write(data)
        _master(inp, out)
        with open(out, "rb") as f: out_bytes = f.read()
    return Response(content=out_bytes, media_type="audio/wav")
