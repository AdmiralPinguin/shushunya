from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from roxdub.pipeline import ffmpeg_executable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIDEO_DIR = PROJECT_ROOT / "videos"
THUMB_DIR = PROJECT_ROOT / "videos" / ".thumbnails"
RUNS_DIR = PROJECT_ROOT / "runs"
TOKEN_FILE = PROJECT_ROOT / ".roxdub-server-token"
ENV_FILE = PROJECT_ROOT / ".env"
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}

JobState = Literal["idle", "running", "done", "failed"]


class RunRequest(BaseModel):
    video_path: str
    source_lang: str = "auto"
    target_lang: str = "ru"
    skip_separation: bool = False
    skip_translation: bool = False


class VideoRunRequest(BaseModel):
    name: str
    source_lang: str = "auto"
    target_lang: str = "ru"
    skip_separation: bool = False
    skip_translation: bool = False


@dataclass
class Job:
    state: JobState = "idle"
    started_at: float | None = None
    finished_at: float | None = None
    return_code: int | None = None
    video_path: str | None = None
    workdir: str | None = None
    log_path: str | None = None
    error: str | None = None
    command: list[str] = field(default_factory=list)


app = FastAPI(title="RoxDub Controller")
job = Job()
worker: asyncio.Task | None = None


def load_env_file() -> None:
    if not ENV_FILE.exists():
        return
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()


def get_token() -> str:
    env_token = os.getenv("ROXDUB_SERVER_TOKEN")
    if env_token:
        return env_token
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(24)
    TOKEN_FILE.write_text(token + "\n", encoding="utf-8")
    TOKEN_FILE.chmod(0o600)
    return token


def check_auth(authorization: str | None) -> None:
    expected = f"Bearer {get_token()}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def check_token_param(token: str | None) -> None:
    if token != get_token():
        raise HTTPException(status_code=401, detail="Unauthorized")


def safe_upload_name(filename: str) -> str:
    cleaned = Path(filename).name.replace(" ", "_")
    return cleaned or f"upload-{int(time.time())}.mp4"


def video_path_by_name(name: str) -> Path:
    clean = Path(name).name
    path = (VIDEO_DIR / clean).resolve()
    if path.parent != VIDEO_DIR.resolve():
        raise HTTPException(status_code=400, detail="Invalid video name")
    return path


def visible_videos() -> list[dict]:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(VIDEO_DIR.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        stat = path.stat()
        items.append(
            {
                "name": path.name,
                "path": str(path),
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
                "thumbnail_url": f"/thumbnail/{path.name}",
            }
        )
    return items


def ensure_thumbnail(video_path: Path) -> Path:
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    thumb_path = THUMB_DIR / f"{video_path.stem}.jpg"
    if thumb_path.exists() and thumb_path.stat().st_mtime >= video_path.stat().st_mtime:
        return thumb_path
    subprocess.run(
        [
            ffmpeg_executable(),
            "-y",
            "-ss",
            "00:00:08",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            "scale=480:-1",
            str(thumb_path),
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return thumb_path


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": "RoxDub"}


@app.get("/status")
def status(authorization: str | None = Header(default=None)) -> dict:
    check_auth(authorization)
    payload = asdict(job)
    payload["progress"] = read_progress()
    return payload


def read_progress() -> dict:
    if not job.workdir:
        return {"stage": "Ожидание", "percent": 0, "detail": ""}
    path = Path(job.workdir) / "progress.json"
    if not path.exists():
        if job.state == "running":
            return {"stage": "Запуск", "percent": 1, "detail": ""}
        if job.state == "failed":
            return {"stage": "Ошибка", "percent": 100, "detail": job.error or ""}
        if job.state == "done":
            return {"stage": "Готово", "percent": 100, "detail": ""}
        return {"stage": "Ожидание", "percent": 0, "detail": ""}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"stage": "Чтение прогресса", "percent": 0, "detail": "progress.json повреждён"}


@app.get("/videos")
def list_videos(authorization: str | None = Header(default=None)) -> dict[str, list[dict]]:
    check_auth(authorization)
    return {"videos": visible_videos()}


@app.get("/thumbnail/{name}")
def thumbnail(name: str, token: str | None = None) -> FileResponse:
    check_token_param(token)
    video_path = video_path_by_name(name)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    thumb_path = ensure_thumbnail(video_path)
    if not thumb_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(thumb_path, media_type="image/jpeg")


@app.post("/upload")
async def upload_video(file: UploadFile = File(...), authorization: str | None = Header(default=None)) -> dict[str, str]:
    check_auth(authorization)
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    target = VIDEO_DIR / f"{int(time.time())}-{safe_upload_name(file.filename or 'video.mp4')}"
    with target.open("wb") as output:
        while chunk := await file.read(1024 * 1024):
            output.write(chunk)
    return {"video_path": str(target)}


@app.post("/run")
async def run_pipeline(request: RunRequest, authorization: str | None = Header(default=None)) -> dict:
    global worker
    check_auth(authorization)
    if job.state == "running":
        raise HTTPException(status_code=409, detail="Pipeline is already running")

    video_path = Path(request.video_path).expanduser().resolve()
    if not video_path.exists():
        raise HTTPException(status_code=404, detail=f"Video not found: {video_path}")

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    workdir = RUNS_DIR / f"{video_path.stem}-{int(time.time())}"
    log_path = workdir / "server.log"
    workdir.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "roxdub.pipeline",
        str(video_path),
        "--source-lang",
        request.source_lang,
        "--target-lang",
        request.target_lang,
        "--workdir",
        str(workdir),
    ]
    if request.skip_separation:
        command.append("--skip-separation")
    if request.skip_translation:
        command.append("--skip-translation")

    job.state = "running"
    job.started_at = time.time()
    job.finished_at = None
    job.return_code = None
    job.video_path = str(video_path)
    job.workdir = str(workdir)
    job.log_path = str(log_path)
    job.error = None
    job.command = command

    worker = asyncio.create_task(run_job(command, log_path))
    return asdict(job)


@app.post("/run-video")
async def run_video(request: VideoRunRequest, authorization: str | None = Header(default=None)) -> dict:
    path = video_path_by_name(request.name)
    return await run_pipeline(
        RunRequest(
            video_path=str(path),
            source_lang=request.source_lang,
            target_lang=request.target_lang,
            skip_separation=request.skip_separation,
            skip_translation=request.skip_translation,
        ),
        authorization=authorization,
    )


async def run_job(command: list[str], log_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    with log_path.open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        return_code = await process.wait()

    job.return_code = return_code
    job.finished_at = time.time()
    if return_code == 0:
        job.state = "done"
    else:
        job.state = "failed"
        job.error = f"Pipeline exited with code {return_code}"


@app.get("/log", response_class=PlainTextResponse)
def read_log(authorization: str | None = Header(default=None)) -> str:
    check_auth(authorization)
    if not job.log_path:
        return ""
    path = Path(job.log_path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[-20000:]


@app.get("/phrases")
def phrases(authorization: str | None = Header(default=None)) -> dict[str, list[dict]]:
    check_auth(authorization)
    if not job.workdir:
        return {"phrases": []}
    path = Path(job.workdir) / "translation" / "segments.json"
    if not path.exists():
        return {"phrases": []}
    items = json.loads(path.read_text(encoding="utf-8"))
    token = get_token()
    phrases_payload = []
    for index, item in enumerate(items, start=1):
        phrases_payload.append(
            {
                "index": index,
                "start": item.get("start"),
                "end": item.get("end"),
                "source_text": item.get("source_text", ""),
                "translated_text": item.get("translated_text", ""),
                "source_audio_url": f"/phrase-audio/source/{index}?token={token}",
                "translated_audio_url": f"/phrase-audio/translated/{index}?token={token}",
            }
        )
    return {"phrases": phrases_payload}


@app.get("/phrase-audio/{kind}/{index}")
def phrase_audio(kind: str, index: int, token: str | None = None) -> FileResponse:
    check_token_param(token)
    if not job.workdir:
        raise HTTPException(status_code=404, detail="No job")
    if kind == "source":
        path = Path(job.workdir) / "phrases" / "source" / f"{index:04}.wav"
    elif kind == "translated":
        path = Path(job.workdir) / "speech" / f"{index:04}.mp3"
    else:
        raise HTTPException(status_code=404, detail="Unknown phrase audio kind")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Phrase audio not found")
    return FileResponse(path)


@app.get("/result/{name}")
def result_file(name: str, authorization: str | None = Header(default=None)) -> FileResponse:
    check_auth(authorization)
    if not job.workdir:
        raise HTTPException(status_code=404, detail="No completed job")
    allowed = {
        "translation": Path(job.workdir) / "translation" / "translated.txt",
        "transcript_json": Path(job.workdir) / "transcript" / "source.json",
        "transcript_srt": Path(job.workdir) / "transcript" / "source.srt",
        "vocals": Path(job.workdir) / "audio" / "vocals.wav",
        "audio": Path(job.workdir) / "audio" / "extracted.wav",
    }
    path = allowed.get(name)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Result file not found")
    return FileResponse(path)


@app.post("/stop")
def stop(authorization: str | None = Header(default=None)) -> dict:
    check_auth(authorization)
    if job.state != "running":
        return asdict(job)
    for process in subprocess.run(
        ["pgrep", "-f", "roxdub.pipeline"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.splitlines():
        subprocess.run(["kill", process], check=False)
    job.state = "failed"
    job.finished_at = time.time()
    job.error = "Stopped by user"
    return asdict(job)


def main() -> None:
    import uvicorn

    host = os.getenv("ROXDUB_SERVER_HOST", "127.0.0.1")
    port = int(os.getenv("ROXDUB_SERVER_PORT", "8765"))
    print(f"RoxDub token: {get_token()}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
