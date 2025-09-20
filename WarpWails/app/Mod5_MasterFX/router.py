from fastapi import APIRouter, Request, UploadFile, Response
import subprocess, tempfile, os, shutil

router = APIRouter()

# semitones -> ratio = 2^(n/12)
P_MAIN = 1.2599210499   # +4
P_HIGH = 1.5874010510   # +8
P_LOW  = 0.5            # -12
P_P2   = 1.1224620483   # +2
P_M2   = 0.8908987181   # -2

def filter_graph() -> str:
    return (
        # сплитим на 5 веток
        "asplit=5[main][hi][low][m1][m2];"
        # основной высокий, не человеческий
        "[main]rubberband=pitch=%0.6f,"
        "acrusher=bits=10:mix=0.40,"
        "equalizer=f=700:width_type=h:width=300:g=-4,"
        "equalizer=f=3300:width_type=h:width=250:g=5"
        "[core];"
        # верхний писклявый шёпот с длинным эхом
        "[hi]rubberband=pitch=%0.6f,highpass=f=2000,volume=-10dB,"
        "aecho=0.90:0.85:700|900:0.55|0.45"
        "[wh];"
        # низкая тень очень тихо
        "[low]rubberband=pitch=%0.6f,lowpass=f=220,volume=-15dB"
        "[shadow];"
        # микро-эхо 1 почти синхронно
        "[m1]rubberband=pitch=%0.6f,adelay=20,volume=-6dB"
        "[mini1];"
        # микро-эхо 2 почти синхронно
        "[m2]rubberband=pitch=%0.6f,adelay=30,volume=-6dB"
        "[mini2];"
        # сводим все 5 голосов
        "[core][wh][shadow][mini1][mini2]amix=inputs=5:normalize=0[mx];"
        # стерео + лёгкий автопан + небольшое эхо для клея
        "[mx]pan=stereo|c0=c0|c1=c0,apulsator=hz=0.30,"
        "aecho=0.80:0.85:220|320:0.45|0.35,"
        # финал: нормализация и лимитер
        "loudnorm=I=-16:TP=-1.0:LRA=10:print_format=none,alimiter=limit=0.95[out]"
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
            "-map","[out]","-ar","24000","-ac","2","-sample_fmt","s16",outpath
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0:
            tail = (proc.stderr or b"").decode(errors="ignore").splitlines()[-1:] or ["ffmpeg error"]
            return Response(f"ffmpeg failed: {tail[0]}", status_code=500, headers={"x-masterfx":"error"})
        with open(outpath,"rb") as f: data = f.read()
        return Response(data, media_type="audio/wav", headers={"x-masterfx":"ok"})
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
