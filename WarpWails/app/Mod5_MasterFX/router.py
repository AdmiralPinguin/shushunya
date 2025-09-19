from fastapi import APIRouter, Request, HTTPException, Response
import tempfile, subprocess, shutil, os

router = APIRouter()
FFMPEG = shutil.which("ffmpeg")
if not FFMPEG:
    raise RuntimeError("ffmpeg not found")

def _run(args:list[str]):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def _have(name:str)->bool:
    r=_run([FFMPEG,"-hide_banner","-filters"])
    return r.returncode==0 and f" {name} " in r.stdout

def _build_chain()->str:
    # целевая цепочка: низ + вырез серединки + искажения + эхо + автопан
    wanted = [
        ("bass",       "bass=g=6:f=110:w=0.7"),
        ("equalizer",  "equalizer=f=1000:width_type=o:width=2:g=-6"),
        ("aexciter",   "aexciter=amount=2:drive=5"),
        ("acrusher",   "acrusher=bits=8:mix=0.5"),
        ("aecho",      "aecho=0.8:0.8:400|800:0.5|0.3"),
        ("apulsator",  "apulsator=hz=1"),
    ]
    # всегда: моно->стерео в начале
    chain = []
    if _have("pan"):
        chain.append("pan=stereo|c0=c0|c1=c0")
    for name, expr in wanted:
        if _have(name):
            chain.append(expr)
    # финал: если нет ни одного эффекта — не падать
    if not chain:
        chain.append("anull")
    return ",".join(chain)

def _master(inp:str, out:str):
    chain=_build_chain()
    r=_run([FFMPEG,"-hide_banner","-y","-i",inp,
            "-af",chain,"-ar","24000","-ac","2","-sample_fmt","s16",out])
    if r.returncode!=0:
        msg=(r.stderr or "").strip().splitlines()[-1] if r.stderr else "no stderr"
        raise RuntimeError(f"ffmpeg masterfx failed: {msg}")

@router.post("/mod5_masterfx")
async def mod5_masterfx(request: Request):
    data=await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="no audio body")
    with tempfile.TemporaryDirectory(prefix="m5_") as td:
        inp=os.path.join(td,"in.wav"); out=os.path.join(td,"out.wav")
        with open(inp,"wb") as f: f.write(data)
        _master(inp,out)
        with open(out,"rb") as f: outb=f.read()
    return Response(content=outb, media_type="audio/wav")
