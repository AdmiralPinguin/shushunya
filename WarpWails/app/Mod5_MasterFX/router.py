from fastapi import APIRouter, Request, UploadFile, Response
import subprocess, tempfile, os, shutil

router = APIRouter()

def _fg() -> str:
    # 1) asetrate опускает тон+форманты ~3%
    # 2) atempo компенсирует скорость (чтобы не растягивать)
    # 3) пара узких выемок и подчёркнутый низ для удаления женских резонансов
    # 4) короткие эхо для эффекта множества голосов
    return (
        "[0:a]"
        "asetrate=24000*0.97,aresample=24000,atempo=1.03,"
        "rubberband=pitch=1.00:formant=0.90,"
        "equalizer=f=2800:width_type=h:width=350:g=-6,"
        "equalizer=f=3200:width_type=h:width=300:g=-6,"
        "equalizer=f=850:width_type=h:width=200:g=4,"
        "acrusher=bits=9:mix=0.40,"
        "vibrato=f=5.5:d=0.22,"
        "aecho=0.85:0.80:7|12|15|17:0.6|0.45|0.35|0.25,"
        "loudnorm=I=-16:TP=-1.0:LRA=9:print_format=none,"
        "alimiter=limit=0.95[out]"
    )

@router.post("/mod5_masterfx")
async def mod5_masterfx(request: Request, file: UploadFile | None = None):
    payload = await (file.read() if file is not None else request.body())
    if not payload:
        return Response("empty body", status_code=400)
    tmp = tempfile.mkdtemp(prefix="m5_"); inp = os.path.join(tmp,"in.wav"); out = os.path.join(tmp,"out.wav")
    try:
        open(inp,"wb").write(payload)
        cmd = ["ffmpeg","-hide_banner","-y","-i",inp,"-filter_complex",_fg(),"-map","[out]","-ar","24000","-ac","1","-sample_fmt","s16",out]
        p = subprocess.run(cmd, capture_output=True)
        if p.returncode != 0:
            tail = (p.stderr or b"").decode(errors="ignore").splitlines()[-1:] or ["ffmpeg error"]
            return Response(f"ffmpeg failed: {tail[0]}", status_code=500, headers={"x-masterfx":"error"})
        data = open(out,"rb").read()
        return Response(data, media_type="audio/wav", headers={"x-masterfx":"ok"})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
