from fastapi import APIRouter, UploadFile, File, Body, Response, HTTPException
from typing import Optional
import tempfile
import subprocess
import os
import shutil
import logging

router = APIRouter()

LOG = logging.getLogger("VoxTormenta")
LOG.setLevel(logging.INFO)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
if not FFMPEG:
    raise RuntimeError("ffmpeg not found in PATH")

def _run(cmd: list) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def _has_rubberband() -> bool:
    try:
        out = _run([FFMPEG, "-hide_banner", "-filters"]).stdout.decode("utf-8", "ignore")
        return "rubberband" in out
    except Exception:
        return False

_RUBBERBAND = _has_rubberband()

def _imp_light_chain(use_rubberband: bool) -> str:
    ratio = 1.189207  # +3 semitones
    if use_rubberband:
        pitch = f"rubberband=pitch={ratio}"
    else:
        pitch = f"asetrate=24000*{ratio},aresample=24000,atempo={1/ratio:.6f}"
    hpf = "highpass=f=80"
    lpf = "lowpass=f=6200"
    comp = "acompressor=threshold=-18dB:ratio=3.5:attack=6:release=140:makeup=0"
    lnorm = "loudnorm=I=-18:TP=-3:LRA=11:dual_mono=true:print_format=none"
    limit = "alimiter=limit=-3dB"
    return ",".join([pitch, hpf, lpf, comp, lnorm, limit])

def _process(in_wav: str, out_wav: str, preset: str = "imp_light") -> None:
    if preset != "imp_light":
        raise ValueError("unsupported preset")
    filter_chain = _imp_light_chain(_RUBBERBAND)
    cmd = [
        FFMPEG, "-hide_banner", "-y",
        "-i", in_wav,
        "-ac", "1",
        "-ar", "24000",
        "-filter_complex", filter_chain,
        "-sample_fmt", "s16",
        out_wav,
    ]
    res = _run(cmd)

    if res.returncode != 0 and _RUBBERBAND:
        fb_chain = _imp_light_chain(False)
        cmd_fb = [
            FFMPEG, "-hide_banner", "-y",
            "-i", in_wav,
            "-ac", "1",
            "-ar", "24000",
            "-filter_complex", fb_chain,
            "-sample_fmt", "s16",
            out_wav,
        ]
        res = _run(cmd_fb)

    if res.returncode != 0:
        LOG.error("ffmpeg error:\n%s", res.stderr.decode("utf-8", "ignore"))
        raise RuntimeError("ffmpeg processing failed")

@router.post("/mod3_voicefx")
async def mod3_voicefx(
    file: Optional[UploadFile] = File(None),
    body: Optional[bytes] = Body(default=None),
    preset: str = "imp_light",
):
    if not file and not body:
        raise HTTPException(status_code=400, detail="provide WAV via multipart 'file' or raw body")

    with tempfile.TemporaryDirectory(prefix="vox3_") as td:
        in_wav = os.path.join(td, "in.wav")
        out_wav = os.path.join(td, "out.wav")

        if file:
            data = await file.read()
        else:
            data = body

        with open(in_wav, "wb") as f:
            f.write(data)

        try:
            _process(in_wav, out_wav, preset=preset)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        with open(out_wav, "rb") as f:
            out_bytes = f.read()

    return Response(content=out_bytes, media_type="audio/wav")
