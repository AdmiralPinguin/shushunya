from fastapi import APIRouter, Request, Response, HTTPException
import tempfile, subprocess, shutil, os, time

router = APIRouter()
FFMPEG = shutil.which("ffmpeg")
if not FFMPEG: raise RuntimeError("ffmpeg not found")
LOG="/tmp/masterfx_last.log"

def run(a): return subprocess.run(a, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def filter_graph()->str:
    # моно -> стерео, жёсткие эффекты, двойное эхо, саб-буст, эквалайзер, нормализация
    return (
        "[0:a]"
        "pan=stereo|c0=c0|c1=c0,"  # принудительно стерео
        "acrusher=level_in=1:level_out=1:bits=8:mode=log:aa=1,"
        "aphaser=in_gain=0.8:out_gain=1.0:delay=2.0:decay=0.6:speed=2.5,"
        "vibrato=f=6.0:d=0.7,"
        "chorus=0.6:0.9:55:0.4:0.25:2,"
        "apulsator=hz=0.15,"
        "aecho=0.8:0.9:200|200:0.5|0.5,"
        "aecho=0.6:0.6:1200|1200:0.4|0.4,"
        "asubboost=boost=8:cutoff=90:wet=1:dry=1,"
        "equalizer=f=3500:width_type=h:width=200:g=5,"
        "loudnorm=I=-16:TP=-1.0:LRA=11:print_format=none,"
        "alimiter=limit=-1.0[out]"
    )

def master(inp:str,out:str):
    fc=filter_graph()
    r=run([FFMPEG,"-hide_banner","-y","-i",inp,"-filter_complex",fc,"-map","[out]","-ar","24000","-ac","2","-sample_fmt","s16",out])
    if r.returncode!=0:
        open(LOG,"w").write("CMD: "+" ".join(r.args)+"\n\nSTDERR:\n"+(r.stderr or ""))
        raise RuntimeError((r.stderr or "").strip().splitlines()[-1] if r.stderr else "ffmpeg failed")

@router.post("/mod5_masterfx")
async def mod5_masterfx(request: Request):
    data=await request.body()
    if not data: raise HTTPException(status_code=400, detail="no audio body")
    with tempfile.TemporaryDirectory(prefix="m5_") as td:
        inp=os.path.join(td,"in.wav"); out=os.path.join(td,"out.wav")
        open(inp,"wb").write(data)
        try:
            master(inp,out)
            return Response(open(out,"rb").read(), media_type="audio/wav", headers={"X-MasterFX":"ok"})
        except Exception as e:
            try:
                ts=time.strftime("%Y%m%d-%H%M%S")
                if os.path.exists(LOG): os.rename(LOG,f"/tmp/masterfx_{ts}.log")
            except: pass
            return Response(open(inp,"rb").read(), media_type="audio/wav",
                            headers={"X-MasterFX":"bypass","X-MasterFX-Error":str(e)[:200]})
