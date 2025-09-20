from fastapi import APIRouter, Request, UploadFile, Response
import subprocess, tempfile, os, shutil

router = APIRouter()

def filter_graph() -> str:
    return (
        "[0:a]"
        "pan=stereo|c0=c0|c1=c0,"
        "acrusher=bits=8:mix=0.6,"
        "aphaser=in_gain=0.8:out_gain=0.9:delay=2.0:decay=0.6:speed=1.0,"
        "chorus=0.6:0.8:40:0.4:0.25:2,"
        "aecho=0.8:0.9:250|400:0.6|0.4,"
        "apulsator=hz=0.4,"
        "bass=g=6:f=120:w=0.7"
        "[core];"
        "[0:a]"
        "highpass=f=1800,"
        "volume=-3dB,"
        "pan=stereo|c0=c0|c1=c0,"
        "aecho=0.9:0.8:600|900:0.65|0.50,"
        "apulsator=hz=1.2"
        "[wh];"
        "[core][wh]amix=inputs=2:normalize=0,"
        "equalizer=f=3200:width_type=h:width=200:g=6,"
        "loudnorm=I=-16:TP=-1.0:LRA=10:print_format=none,"
        "alimiter=limit=0.95"
        "[out]"
    )

@router.post("/mod5_masterfx")
async def mod5_masterfx(request: Request, file: UploadFile | None = None):
    # Принимаем либо multipart (file), либо raw body audio/wav
    if file is not None:
        payload = await file.read()
    else:
        payload = await request.body()
        if not payload:
            return Response("empty body", status_code=400)

    tmpdir = tempfile.mkdtemp(prefix="m5_")
    inpath = os.path.join(tmpdir, "in.wav")
    outpath = os.path.join(tmpdir, "out.wav")
    try:
        with open(inpath, "wb") as f:
            f.write(payload)

        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", inpath,
            "-filter_complex", filter_graph(),
            "-map", "[out]", "-ar", "24000", "-ac", "2", "-sample_fmt", "s16",
            outpath
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0:
            err_tail = proc.stderr.decode(errors="ignore").splitlines()[-1] if proc.stderr else "ffmpeg error"
            return Response(f"ffmpeg failed: {err_tail}", status_code=500, headers={"x-masterfx": "error"})

        with open(outpath, "rb") as f:
            data = f.read()
        return Response(data, media_type="audio/wav", headers={"x-masterfx": "ok"})
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
