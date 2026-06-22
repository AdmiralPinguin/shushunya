#!/usr/bin/env python3
import json
import os
import struct
import subprocess
import tempfile
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
HOST = os.environ.get("STT_HOST", "127.0.0.1")
PORT = int(os.environ.get("STT_PORT", "8093"))
WHISPER_CLI = Path(os.environ.get("WHISPER_CLI", "/media/shushunya/SHUSHUNYA/shushunya/android-tools/whisper.cpp/build/bin/whisper-cli"))
WHISPER_BUILD_ROOT = WHISPER_CLI.resolve().parents[1]
WHISPER_LIBRARY_PATHS = [
    WHISPER_BUILD_ROOT / "src",
    WHISPER_BUILD_ROOT / "ggml" / "src",
]
MODEL = Path(os.environ.get("STT_MODEL", ROOT / "stt-models" / "ggml-large-v3-turbo-q5_0.bin"))
LANGUAGE_MODELS = {
    "ko": Path(os.environ.get("STT_MODEL_KO", ROOT / "stt-models" / "ggml-large-v3-q5_0.bin")),
    "ru": Path(os.environ.get("STT_MODEL_RU", MODEL)),
    "ar": Path(os.environ.get("STT_MODEL_AR", MODEL)),
    "tr": Path(os.environ.get("STT_MODEL_TR", MODEL)),
}
THREADS = os.environ.get("STT_THREADS", str(max(2, min(16, os.cpu_count() or 4))))
SUPPORTED_LANGUAGES = {"ru", "ko", "ar", "tr"}


def read_json(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def write_json(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def write_wav(path, samples, sample_rate):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for sample in samples:
            value = max(-1.0, min(1.0, float(sample)))
            frames.extend(struct.pack("<h", int(value * 32767)))
        wav.writeframes(bytes(frames))


def write_pcm_wav(path, pcm, sample_rate):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)


def read_chunked_body(handler):
    body = bytearray()
    while True:
        line = handler.rfile.readline()
        if not line:
            break
        size_text = line.split(b";", 1)[0].strip()
        if not size_text:
            continue
        size = int(size_text, 16)
        if size == 0:
            handler.rfile.readline()
            break
        body.extend(handler.rfile.read(size))
        handler.rfile.read(2)
    return bytes(body)


def read_audio_body(handler):
    transfer_encoding = str(handler.headers.get("Transfer-Encoding") or "").lower()
    if "chunked" in transfer_encoding:
        return read_chunked_body(handler)
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return b""
    return handler.rfile.read(length)


def model_for_language(language):
    language_model = LANGUAGE_MODELS.get(language, MODEL)
    if language_model.exists():
        return language_model
    return MODEL


def whisper_prompt(language):
    if language == "ko":
        return "한국어 음성을 정확한 한글 문장으로 받아쓰기."
    return ""


def whisper_env():
    env = os.environ.copy()
    paths = [str(path) for path in WHISPER_LIBRARY_PATHS if path.exists()]
    existing = env.get("LD_LIBRARY_PATH", "")
    if existing:
        paths.append(existing)
    env["LD_LIBRARY_PATH"] = ":".join(paths)
    return env


def transcribe(language, samples, sample_rate):
    with tempfile.TemporaryDirectory(prefix="shushunya-stt-") as tmp:
        wav_path = Path(tmp) / "audio.wav"
        out_prefix = Path(tmp) / "out"
        write_wav(wav_path, samples, sample_rate)
        model_path = model_for_language(language)
        cmd = [
            str(WHISPER_CLI),
            "-m",
            str(model_path),
            "-f",
            str(wav_path),
            "-l",
            language,
            "-t",
            THREADS,
            "-nt",
            "-np",
            "-otxt",
            "-of",
            str(out_prefix),
        ]
        prompt = whisper_prompt(language)
        if prompt:
            cmd.extend(["--prompt", prompt])
        print(f"[stt] language={language} model={model_path.name} samples={len(samples)} sample_rate={sample_rate}", flush=True)
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=240,
            env=whisper_env(),
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or f"whisper-cli failed: {result.returncode}")[-2000:])
        text_path = Path(str(out_prefix) + ".txt")
        if text_path.exists():
            return text_path.read_text(encoding="utf-8").strip()
        return result.stdout.strip()


def transcribe_pcm(language, pcm, sample_rate):
    with tempfile.TemporaryDirectory(prefix="shushunya-stt-") as tmp:
        wav_path = Path(tmp) / "audio.wav"
        out_prefix = Path(tmp) / "out"
        write_pcm_wav(wav_path, pcm, sample_rate)
        model_path = model_for_language(language)
        cmd = [
            str(WHISPER_CLI),
            "-m",
            str(model_path),
            "-f",
            str(wav_path),
            "-l",
            language,
            "-t",
            THREADS,
            "-nt",
            "-np",
            "-otxt",
            "-of",
            str(out_prefix),
        ]
        prompt = whisper_prompt(language)
        if prompt:
            cmd.extend(["--prompt", prompt])
        print(f"[stt] language={language} model={model_path.name} pcm_bytes={len(pcm)} sample_rate={sample_rate}", flush=True)
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=240,
            env=whisper_env(),
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or f"whisper-cli failed: {result.returncode}")[-2000:])
        text_path = Path(str(out_prefix) + ".txt")
        if text_path.exists():
            return text_path.read_text(encoding="utf-8").strip()
        return result.stdout.strip()


class Handler(BaseHTTPRequestHandler):
    server_version = "ShushunyaSTT/0.1"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def do_GET(self):
        if self.path == "/health":
            write_json(
                self,
                200,
                {
                    "status": "ok",
                    "service": "ShushunyaSTT",
                    "model": str(MODEL),
                    "model_exists": MODEL.exists(),
                    "language_models": {language: str(model) for language, model in LANGUAGE_MODELS.items()},
                    "language_model_exists": {language: model.exists() for language, model in LANGUAGE_MODELS.items()},
                    "threads": THREADS,
                    "whisper_cli": str(WHISPER_CLI),
                    "whisper_cli_exists": WHISPER_CLI.exists(),
                    "library_paths": [str(path) for path in WHISPER_LIBRARY_PATHS],
                },
            )
            return
        write_json(self, 404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/stt-live":
            try:
                language = str(self.headers.get("X-Language") or "").strip().lower()
                if language not in SUPPORTED_LANGUAGES:
                    write_json(self, 400, {"error": "X-Language must be one of ru, ko, ar, tr"})
                    return
                sample_rate = int(self.headers.get("X-Sample-Rate") or "16000")
                pcm = read_audio_body(self)
                if len(pcm) < sample_rate:
                    write_json(self, 400, {"error": "audio body is too short"})
                    return
                if len(pcm) % 2:
                    pcm = pcm[:-1]
                write_json(self, 200, {"id": str(uuid.uuid4()), "text": transcribe_pcm(language, pcm, sample_rate)})
            except Exception as exc:
                write_json(self, 500, {"error": str(exc)})
            return

        if self.path == "/stt-pcm":
            try:
                language = str(self.headers.get("X-Language") or "").strip().lower()
                if language not in SUPPORTED_LANGUAGES:
                    write_json(self, 400, {"error": "X-Language must be one of ru, ko, ar, tr"})
                    return
                sample_rate = int(self.headers.get("X-Sample-Rate") or "16000")
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    write_json(self, 400, {"error": "audio body is required"})
                    return
                pcm = self.rfile.read(length)
                write_json(self, 200, {"text": transcribe_pcm(language, pcm, sample_rate)})
            except Exception as exc:
                write_json(self, 500, {"error": str(exc)})
            return

        if self.path != "/stt":
            write_json(self, 404, {"error": "not found"})
            return
        try:
            payload = read_json(self)
            language = str(payload.get("language") or "").strip().lower()
            if language not in SUPPORTED_LANGUAGES:
                write_json(self, 400, {"error": "language must be one of ru, ko, ar, tr"})
                return
            sample_rate = int(payload.get("sample_rate") or 16000)
            samples = payload.get("samples") or []
            if not samples:
                write_json(self, 400, {"error": "samples are required"})
                return
            write_json(self, 200, {"text": transcribe(language, samples, sample_rate)})
        except Exception as exc:
            write_json(self, 500, {"error": str(exc)})


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Shushunya STT started: http://{HOST}:{PORT}", flush=True)
    print(f"Whisper CLI: {WHISPER_CLI}", flush=True)
    print(f"Model: {MODEL}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
