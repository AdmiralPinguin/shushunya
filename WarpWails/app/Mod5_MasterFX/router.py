from fastapi import APIRouter, Request, UploadFile, Response
import subprocess, tempfile, os, shutil

router = APIRouter()

def _fg() -> str:
    # Форманты вниз ~35% (asetrate=15600), тон возвращаем (rubberband pitch=1.538)
    return (
        "[0:a]"
        "asetrate=15600,aresample=24000,"
        "rubberband=pitch=1.538:formant=1,"
        "deesser=i=0.3:s=0.5,"
        "equalizer=f=2600:width_type=h:width=500:g=-10,"
        "equalizer=f=3400:width_type=h:width=400:g=-7,"
        "equalizer=f=900:width_type=h:width=220:g=0,"
        "acrusher=bits=9:mix=0.45,"
        "vibrato=f=6.5:d=0.28,"
        "aecho=0.85:0.85:7|12|15|17:0.65|0.5|0.4|0.3,"
        "loudnorm=I=-16:TP=-1.0:LRA=10:print_format=none,"
        "alimiter=limit=0.95[out]"
    )

@router.post("/mod5_masterfx")
async def mod5_masterfx(request: Request, file: UploadFile | None = None):
    payload = await (file.read() if file is not None else request.body())
    if not payload:
        return Response("empty body", status_code=400)
    td = tempfile.mkdtemp(prefix="m5_")
    inp, out = f"{td}/in.wav", f"{td}/out.wav"
    try:
        with open(inp,"wb") as f: f.write(payload)
        cmd = ["ffmpeg","-hide_banner","-y","-i",inp,
               "-filter_complex", _fg(),
               "-map","[out]","-ar","24000","-ac","1","-sample_fmt","s16",out]
        p = subprocess.run(cmd, capture_output=True)
        if p.returncode != 0:
            tail = (p.stderr or b"").decode(errors="ignore").splitlines()[-1:] or ["ffmpeg error"]
            return Response(f"ffmpeg failed: {tail[0]}", status_code=500, headers={"x-masterfx":"error"})
        return Response(open(out,"rb").read(), media_type="audio/wav", headers={"x-masterfx":"ok"})
    finally:
        shutil.rmtree(td, ignore_errors=True)
