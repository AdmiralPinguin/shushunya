from fastapi import APIRouter, Request, Response, HTTPException
import tempfile, subprocess, shutil, os, time

router = APIRouter()
FFMPEG = shutil.which("ffmpeg")
if not FFMPEG: raise RuntimeError("ffmpeg not found")
LOG_PATH="/tmp/masterfx_last.log"

def run(a): return subprocess.run(a, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def filter_graph()->str:
    # Все параметры в допустимых диапазонах ffmpeg
    return (
        # CORE: стерео-центр + грязь + дрожь + хорус + эхо + лёгкий автопан
        "[0:a]"
        "pan=stereo|c0=c0|c1=c0,"
        "acrusher=bits=10:mix=0.50,"
        "aphaser=in_gain=0.8:out_gain=0.9:delay=2.0:decay=0.6:speed=1.2,"
        "vibrato=f=5.0:d=0.35,"
        "chorus=0.6:0.8:30:0.4:0.25:2|45:0.3:0.3:1.6:0.25,"
        "aecho=0.85:0.90:220|440:0.55|0.40,"
        "apulsator=hz=0.4,"
        "asubboost=boost=8:cutoff=110:wet=1[out_core];"
        # WHISPER: громкий шёпот + жирная задержка + быстрый автопан
        "[0:a]"
        "highpass=f=1800,volume=-3dB,"
        "pan=stereo|c0=c0|c1=c0,"
        "aecho=0.95:0.90:600|900:0.60|0.45,"
        "apulsator=hz=1.0[out_wh];"
        # MIX + нормализация + лимитер (валидное значение limit)
        "[out_core][out_wh]amix=inputs=2:normalize=0,"
        "equalizer=f=3200:width_type=h:width=200:g=6,"
        "loudnorm=I=-16:TP=-1.0:LRA=10:print_format=none,"
        "alimiter=limit=0.95[out]"
    )

def master(inp:str,out:str):
    fc=filter_graph()
    r=run([FFMPEG,"-hide_banner","-y","-i",inp,
           "-filter_complex",fc,"-map","[out]",
           "-ar","24000","-ac","2","-sample_fmt","s16",out])
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
            return Response(open(out,"rb").read(), media_type="audio/wav",
                            headers={"X-MasterFX":"ok"})
        except Exception as e:
            try:
                ts=time.strftime("%Y%m%d-%H%M%S")
                if os.path.exists(LOG_PATH): os.rename(LOG_PATH,f"/tmp/masterfx_{ts}.log")
            except: pass
            return Response(open(inp,"rb").read(), media_type="audio/wav",
                            headers={"X-MasterFX":"bypass","X-MasterFX-Error":str(e)[:200]})
