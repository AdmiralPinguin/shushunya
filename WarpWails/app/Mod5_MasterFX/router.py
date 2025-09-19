from fastapi import APIRouter, Request, Response, HTTPException
import tempfile, subprocess, shutil, os, time

router = APIRouter()
FFMPEG = shutil.which("ffmpeg")
if not FFMPEG: raise RuntimeError("ffmpeg not found")
LOG_PATH="/tmp/masterfx_last.log"

def run(a): return subprocess.run(a, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def filter_graph()->str:
    return (
        # CORE: грязь + дрожь + хорус + жир + явное эхо + лёгкий автопан, стерео-моноразвод
        "[0:a]"
        "acrusher=bits=10:mix=0.40,"
        "aphaser=0.6:0.9:60:0.6:0.8:0.5,"
        "vibrato=f=5:d=0.35,"
        "chorus=0.5:0.9:45:0.4:0.25:2,"
        "asubboost=f=150:g=5,"
        "aecho=0.8:0.9:220|440:0.5|0.3,"
        "apulsator=hz=0.6,"
        "pan=stereo|c0=c0|c1=c0[core];"
        # WHISPER: шёпот громкий, жирная задержка, быстрый автопан, стерео
        "[0:a]"
        "highpass=f=2000,volume=-3dB,"
        "aecho=0.9:0.8:600|900:0.6|0.4,"
        "apulsator=hz=1.2,"
        "pan=stereo|c0=c0|c1=c0[wh];"
        # MIX + нормализация
        "[core][wh]amix=inputs=2:normalize=0,"
        "loudnorm=I=-18:TP=-1.2:LRA=11:print_format=none[out]"
    )

def master(inp:str,out:str):
    fc=filter_graph()
    r=run([FFMPEG,"-hide_banner","-y","-i",inp,"-filter_complex",fc,"-map","[out]","-ar","24000","-ac","2","-sample_fmt","s16",out])
    if r.returncode!=0:
        open(LOG_PATH,"w").write("CMD: "+" ".join(r.args)+"\n\nSTDERR:\n"+(r.stderr or ""))
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
                if os.path.exists(LOG_PATH): os.rename(LOG_PATH,f"/tmp/masterfx_{ts}.log")
            except: pass
            return Response(open(inp,"rb").read(), media_type="audio/wav",
                            headers={"X-MasterFX":"bypass","X-MasterFX-Error":str(e)[:200]})
