from fastapi import APIRouter, UploadFile, File, Query, Response
import subprocess, tempfile, os

router = APIRouter(prefix="/mod5_masterfx", tags=["mod5"])

FILTERS = {
 "abyss":      "[0:a]asetrate=48000*0.5946035575,aresample=48000,atempo=1.6817928305,highpass=f=40,lowpass=f=3800,acompressor=threshold=-18dB:ratio=3:attack=6:release=120:makeup=6,chorus=0.6:0.9:55:0.4:0.25:2:sin,aecho=0.7:0.5:60|90:0.3|0.2,alimiter=limit=0.97",
 "radio_void": "[0:a]highpass=f=350,lowpass=f=3400,acrusher=bits=8:mode=lin:mix=0.85,tremolo=f=3:d=0.7,aecho=0.3:0.4:40|80:0.2|0.1,alimiter=limit=0.95",
 "ghost":      "[0:a]highpass=f=250,lowpass=f=7500,acompressor=threshold=-30dB:ratio=8:attack=2:release=200:makeup=8,aphaser=type=t:decay=0.6:speed=0.5,aecho=0.6:0.6:6|12:0.25|0.2[vox];anoisesrc=color=pink:amplitude=0.008:d=0,highpass=f=2000,lowpass=f=9000,volume=-20dB[n];[vox][n]amix=inputs=2:weights=1 0.2:normalize=0,alimiter=limit=0.98",
 "swarm":      "[0:a]asplit=4[a0][a1][a2][a3];[a0]anull[a0o];[a1]asetrate=48000*1.014545334,aresample=48000,atempo=0.98565[a1o];[a2]asetrate=48000*0.98765075,aresample=48000,atempo=1.0125[a2o];[a3]asetrate=48000*0.97153194,aresample=48000,atempo=1.02925[a3o];[a0o][a1o][a2o][a3o]amix=inputs=4:weights=1 0.7 0.6 0.5:normalize=0,aecho=0.5:0.5:22|33:0.3|0.2,alimiter=limit=0.97",
 "preverb":    "[0:a]asplit=2[dry][wetpre];[wetpre]areverse,aecho=0.6:0.5:120|180:0.4|0.32,areverse,lowpass=f=6000[wet];[dry][wet]amix=inputs=2:weights=1 0.6:normalize=0,alimiter=limit=0.98"
}

@router.post("")
async def masterfx(preset: str = Query("abyss"), file: UploadFile = File(...)):
    if preset not in FILTERS: return Response(content=f"unknown preset: {preset}", status_code=400)
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "in.wav"); dst = os.path.join(td, "out.wav")
        with open(src, "wb") as f: f.write(await file.read())
        cmd = ["ffmpeg","-y","-hide_banner","-loglevel","error","-i",src,"-filter_complex",FILTERS[preset],"-ar","48000","-ac","2",dst]
        run = subprocess.run(cmd, capture_output=True, text=True)
        if run.returncode != 0: return Response(content=f"ffmpeg failed\n{run.stderr}", status_code=500, media_type="text/plain")
        data = open(dst,"rb").read()
    return Response(content=data, media_type="audio/wav")
