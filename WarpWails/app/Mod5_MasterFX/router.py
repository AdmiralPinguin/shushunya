from fastapi import APIRouter, Request, UploadFile, Response
import subprocess, tempfile, os, shutil

router = APIRouter()

def _fg() -> str:
    # 1) asetrate=0.75 (24000->18000) опускает тон+форманты
    # 2) rubberband=pitch=1.333 возвращает ТОН в норму, форманты остаются «толще»
    # затем анти-женские полосы, лёгкая грязь, дрожь и 4 микро-эхо 7/12/15/17 мс
    return (
        "[0:a]asetrate=18000,aresample=24000,"
        "rubberband=pitch=1.333:formant=1,"
        "deesser=i=0.25:s=0.5,"
        "equalizer=f=2800:width_type=h:width=400:g=-8,"
        "equalizer=f=900:width_type=h:width=220:g=3,"
        "acrusher=bits=10:mix=0.35,"
        "vibrato=f=5.5:d=0.22,"
        "aecho=0.85:0.85:7|12|15|17:0.65|0.5|0.4|0.3,"
        "loudnorm=I=-16:TP=-1.0:LRA=10:print_format=none,"
        "alimiter=limit=0.98[out]"
    )

@router.post("/mod5_masterfx")
async def mod5_masterfx(request: Request, file: UploadFile | None = None):
    payload = await (file.read() if file is not None else request.body())
    if not payload:
        return Response("empty body", status_code=400)
    tmp = tempfile.mkdtemp(prefix="m5_")
    inp, out = f"{tmp}/in.wav", f"{tmp}/out.wav"
    try:
        with open(inp, "wb") as f: f.write(payload)
        cmd = ["ffmpeg","-hide_banner","-y","-i",inp,
               "-filter_complex", _fg(),
               "-map","[out]","-ar","24000","-ac","1","-sample_fmt","s16",out]
        p = subprocess.run(cmd, capture_output=True)
        if p.returncode != 0:
            tail = (p.stderr or b"").decode(errors="ignore").splitlines()[-1:] or ["ffmpeg error"]
            return Response(f"ffmpeg failed: {tail[0]}", status_code=500, headers={"x-masterfx":"error"})
        return Response(open(out,"rb").read(), media_type="audio/wav", headers={"x-masterfx":"ok"})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
