from fastapi import APIRouter, Request, Response, HTTPException
import tempfile, subprocess, shutil, os, time

router = APIRouter()
FFMPEG = shutil.which("ffmpeg")
if not FFMPEG:
    raise RuntimeError("ffmpeg not found")

LOG_PATH = "/tmp/masterfx_last.log"

def _run(args):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def _have(name: str) -> bool:
    r = _run([FFMPEG, "-hide_banner", "-filters"])
    return r.returncode == 0 and f" {name} " in r.stdout

def _build_chain() -> str:
    want = [
        ("pan",       "pan=stereo|c0=c0|c1=c0"),
        ("bass",      "bass=g=6:f=110:w=0.7"),
        ("equalizer", "equalizer=f=1000:width_type=o:width=2:g=-6"),
        ("aexciter",  "aexciter=amount=2:drive=5"),
        ("acrusher",  "acrusher=bits=8:mix=0.5"),
        ("aecho",     "aecho=0.8:0.8:400|800:0.5|0.3"),
        ("apulsator", "apulsator=hz=1"),
    ]
    chain = []
    for name, expr in want:
        if _have(name):
            chain.append(expr)
    if not chain:
        chain.append("anull")
    return ",".join(chain)

def _master(inp: str, out: str) -> tuple[bool, str]:
    chain = _build_chain()
    cmd = [FFMPEG, "-hide_banner", "-y",
           "-i", inp,
           "-af", chain,
           "-ar", "24000", "-ac", "2", "-sample_fmt", "s16",
           out]
    r = _run(cmd)
    if r.returncode != 0:
        # лог в файл
        with open(LOG_PATH, "w") as f:
            f.write("CMD: " + " ".join(cmd) + "\n\nSTDERR:\n" + (r.stderr or ""))
        return False, (r.stderr or "").strip().splitlines()[-1] if r.stderr else "no stderr"
    return True, "ok"

@router.get("/mod5_capabilities")
def mod5_capabilities():
    r = _run([FFMPEG, "-hide_banner", "-filters"])
    return Response(content=r.stdout, media_type="text/plain")

@router.post("/mod5_masterfx")
async def mod5_masterfx(request: Request):
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="no audio body")
    with tempfile.TemporaryDirectory(prefix="m5_") as td:
        inp = os.path.join(td, "in.wav"); out = os.path.join(td, "out.wav")
        with open(inp, "wb") as f: f.write(data)
        ok, msg = _master(inp, out)
        if ok:
            with open(out, "rb") as f: outb = f.read()
            return Response(content=outb, media_type="audio/wav",
                            headers={"X-MasterFX":"ok"})
        # fallback: вернуть вход как есть, чтобы пайплайн не падал
        with open(inp, "rb") as f: raw = f.read()
        hdr = {"X-MasterFX":"bypass","X-MasterFX-Error":msg[:200]}
        # также сохранить лог с таймстампом
        try:
            ts = time.strftime("%Y%m%d-%H%M%S")
            if os.path.exists("/tmp/masterfx_last.log"):
                os.rename("/tmp/masterfx_last.log", f"/tmp/masterfx_{ts}.log")
        except Exception:
            pass
        return Response(content=raw, media_type="audio/wav", headers=hdr)
