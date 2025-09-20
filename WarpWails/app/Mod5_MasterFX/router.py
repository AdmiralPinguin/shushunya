from fastapi import APIRouter, Request, UploadFile, Response
import subprocess, tempfile, os, shutil

router = APIRouter()

# ratios = 2^(semitones/12)
P_MAIN = 1.1892071150   # +3  — основной, высокий, но не «женский»
P_HIGH = 1.3348398542   # +5  — верхний шёпот
P_P2   = 1.1224620483   # +2  — микро-дубль 1
P_P1   = 1.0594630944   # +1  — микро-дубль 2

def filter_graph() -> str:
    return (
        # 4 ветки: основной, высокий шёпот, два почти синхронных дубля
        "asplit=4[main][wh][m1][m2];"
        # Основной: умеренный питч вверх, прибираем «женские» форманты, чуть грязи, лёгкий вибрато
        "[main]rubberband=pitch=%0.6f,"
        "acrusher=bits=10:mix=0.45,"
        "equalizer=f=800:width_type=h:width=250:g=-6,"
        "equalizer=f=2200:width_type=h:width=300:g=-3,"
        "vibrato=f=5.0:d=0.20"
        "[core];"
        # Высокий шёпот: только верх, де-эссер, длинное эхо
        "[wh]rubberband=pitch=%0.6f,highpass=f=1800,deesser=i=0.2:s=0.5,volume=-9dB,"
        "aecho=0.85:0.80:500:0.45"
        "[whp];"
        # Два микро-дубля почти синхронно, чтобы слышалось «несколько ртов»
        "[m1]rubberband=pitch=%0.6f,adelay=12,volume=-6dB[mn1];"
        "[m2]rubberband=pitch=%0.6f,adelay=18,volume=-8dB[mn2];"
        # Сведение в моно + клей-эхо + финал
        "[core][whp][mn1][mn2]amix=inputs=4:normalize=0,"
        "aecho=0.80:0.85:180:0.35,"
        "loudnorm=I=-16:TP=-1.0:LRA=10:print_format=none,"
        "alimiter=limit=0.95[out]"
    ) % (P_MAIN, P_HIGH, P_P2, P_P1)

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
