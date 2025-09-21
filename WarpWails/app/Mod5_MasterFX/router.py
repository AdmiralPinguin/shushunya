import os, tempfile, subprocess, datetime, uuid
from fastapi import APIRouter, Body, Response
from starlette.responses import FileResponse, PlainTextResponse

router = APIRouter()

FFMPEG = "/usr/bin/ffmpeg"
SR = 24000

# Усиленный демонический мастер без невалидных опций
# – моно в стерео (центр)
# – лёгкая дисторсия (acrusher)
# – фазер в допустимых пределах
# – вибрато умеренное
# – 2 слоя echo: очень короткие (псевдо-двойники) и длинная «яма»
# – автопульсатор для «шевеления» тембра
# – саб-буст аккуратный
# – лёгкий хай-шельф в присутствии
# – нормализация по громкости и безопасный лимитер
DEMON_WARP = (
    "[0:a]"
    "pan=stereo|c0=c0|c1=c0,"
    "acrusher=bits=10:mix=0.35,"
    "aphaser=in_gain=0.8:out_gain=0.9:delay=1.5:decay=0.6:speed=1.2,"
    "vibrato=f=5.5:d=0.35,"
    "aecho=0.90:0.85:7|12|16:0.50|0.45|0.40,"
    "aecho=0.70:0.70:850|1150:0.45|0.35,"
    "apulsator=hz=0.35,"
    "asubboost=boost=7:cutoff=110:wet=1:dry=1,"
    "equalizer=f=3200:width_type=h:width=200:g=5,"
    "loudnorm=I=-16:TP=-1.0:LRA=10:print_format=none,"
    "alimiter=limit=0.95"
    "[out]"
)

def _run_ffmpeg(inp: str, out: str, log_path: str) -> None:
    cmd = [
        FFMPEG, "-hide_banner", "-y",
        "-i", inp,
        "-filter_complex", DEMON_WARP,
        "-map", "[out]",
        "-ar", str(SR), "-ac", "2", "-sample_fmt", "s16",
        out,
    ]
    # лог на диск для разборов
    with open(log_path, "w") as lf:
        lf.write("CMD: " + " ".join(cmd) + "\n\n")
    r = subprocess.run(cmd, capture_output=True, text=True)
    with open(log_path, "a") as lf:
        lf.write("STDERR:\n" + (r.stderr or "") + "\n")
    if r.returncode != 0 or not os.path.exists(out):
        tail = (r.stderr or "").splitlines()[-1] if r.stderr else "no stderr"
        raise RuntimeError(f"ffmpeg masterfx failed: {tail}")

@router.post("/mod5_masterfx", responses={
    200: {"content": {"audio/wav": {}}}
})
def mod5_masterfx(raw: bytes = Body(..., media_type="audio/wav")):
    # буферы
    work = tempfile.mkdtemp(prefix="m5_")
    inp = os.path.join(work, "in.wav")
    out = os.path.join(work, "out.wav")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = f"/tmp/masterfx_{stamp}.log"
    open("/tmp/masterfx_last.log", "w").write(f"{log_path}\n")

    # сохранить вход
    with open(inp, "wb") as f:
        f.write(raw)

    # запустить обработку
    _run_ffmpeg(inp, out, log_path)

    # выдать файл
    if not os.path.exists(out):
        return PlainTextResponse("masterfx produced no output", status_code=500)
    headers = {
        "x-masterfx": "ok",
        "x-masterfx-log": log_path,
        "x-masterfx-preset": "DEMON_WARP",
    }
    return FileResponse(out, media_type="audio/wav", filename=f"m5_{uuid.uuid4().hex}.wav", headers=headers)
