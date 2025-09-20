from fastapi import APIRouter, Request, UploadFile, Response
import subprocess, tempfile, os, shutil

router = APIRouter()

# ratio = 2^(n/12)
P_MAIN = 1.1892071150   # +3 semitones (было +4)
P_HIGH = 1.3348398542   # +5 semitones (было +8 → ближе)
P_LOW  = 0.5946035575   # -8 semitones (было -12 → ближе)
P_P2   = 1.1224620483   # +2 semitones
P_M2   = 0.9438743127   # -1 semitone (было -2 → ближе)

def filter_graph() -> str:
    return (
        "asplit=5[main][hi][low][m1][m2];"
        "[main]rubberband=pitch=%0.6f,"
        "acrusher=bits=10:mix=0.40,"
        "equalizer=f=600:width_type=h:width=200:g=-5,"
        "equalizer=f=3000:width_type=h:width=300:g=6"
        "[core];"
        "[hi]rubberband=pitch=%0.6f,highpass=f=2000,volume=-10dB,"
        "aecho=0.90:0.85:500|700:0.55|0.40"
        "[wh];"
        "[low]rubberband=pitch=%0.6f,lowpass=f=250,volume=-15dB"
        "[shadow];"
        "[m1]rubberband=pitch=%0.6f,adelay=15,volume=-6dB"
        "[mini1];"
        "[m2]rubberband=pitch=%0.6f,adelay=20,volume=-6dB"
        "[mini2];"
        "[core][wh][shadow][mini1][mini2]amix=inputs=5:normalize=0[mx];"
        "[mx]aecho=0.80:0.85:180|260:0.45|0.35,"
        "loudnorm=I=-16:TP=-1.0:LRA=10:print_format=none,"
        "alimiter=limit=0.95[out]"
    ) % (P_MAIN, P_HIGH, P_LOW, P_P2, P_M2)

@router.post("/mod5_masterfx")
async def mod5_masterfx(request: Request, file: UploadFile | None = None):
    payload = await (file.read() if file is not None else request.body())
    if not payload:
        return Response("empty body", status_code=400)
    tmpdir = tempfile.mkdtemp(prefix="m5_")
    inpath, outpath = os.path.join(tmpdir,"in.wav"), os.path.join(tmpdir,"out.wav")
    try:
        with open(inpath,"wb") as f: f.write(payload)
        cmd = [
            "ffmpeg","-hide_banner","-y","-i",inpath,
            "-filter_complex", filter_graph(),
            "-map","[out]","-ar","24000","-ac","1","-sample_fmt","s16",outpath
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0:
            tail = (proc.stderr or b"").decode(errors="ignore").splitlines()[-1:] or ["ffmpeg error"]
            return Response(f"ffmpeg failed: {tail[0]}", status_code=500, headers={"x-masterfx":"error"})
        with open(outpath,"rb") as f: data = f.read()
        return Response(data, media_type="audio/wav", headers={"x-masterfx":"ok"})
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
