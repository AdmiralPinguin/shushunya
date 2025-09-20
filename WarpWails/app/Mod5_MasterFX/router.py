from fastapi import APIRouter, Request, UploadFile, Response
import subprocess, tempfile, os, shutil

router = APIRouter()

def _fg() -> str:
    # агрессивный 'демонический' профиль для kseniya
    return (
        "[0:a]"
        # сильное смещение формант вбок: asetrate вниз -> rubberband сильно выше (форманты меняются)
        "asetrate=15000,aresample=24000,"
        "rubberband=pitch=1.6:formant=0.55,"
        # убрать 'женские' верхние резонансы, подчеркнуть 'неродной' тембр
        "equalizer=f=2700:width_type=h:width=450:g=-12,"
        "equalizer=f=3200:width_type=h:width=400:g=-8,"
        "equalizer=f=800:width_type=h:width=260:g=5,"
        # грязь и цифровые артефакты
        "acrusher=bits=8:mix=0.60,"
        "asubboost=boost=6:cutoff=120:wet=0.6,"
        # металлическая окраска, фазер для 'нереальности'
        "aphaser=in_gain=0.7:out_gain=1.0:delay=2.0:decay=0.6:speed=1.5,"
        # вибрато/дрожь
        "vibrato=f=6.8:d=0.30,"
        # микро-эхо кластер 7/12/15/17 ms (чётко слышны параллельные рты)
        "aecho=0.88:0.84:7|12|15|17:0.70|0.52|0.40|0.28,"
        # финальная чистка и лимит
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
