from __future__ import annotations

import importlib.util
import shutil
import urllib.error
import urllib.request


def status(name: str, ok: bool, detail: str) -> str:
    marker = "OK" if ok else "MISSING"
    return f"{marker:7} {name:16} {detail}"


def has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def has_ffmpeg() -> bool:
    if shutil.which("ffmpeg") is not None:
        return True
    if not has_module("imageio_ffmpeg"):
        return False
    import imageio_ffmpeg

    return bool(imageio_ffmpeg.get_ffmpeg_exe())


def has_local_gemma() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/v1/models", timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def main() -> int:
    checks = [
        ("ffmpeg", has_ffmpeg(), "binary for audio extraction"),
        ("bs_roformer", has_module("bs_roformer"), "strong vocal separation model runner"),
        ("demucs", has_module("demucs"), "Python module for vocal separation"),
        ("faster_whisper", has_module("faster_whisper"), "Python module for STT"),
        ("silero_vad", has_module("silero_vad"), "Python module for phrase boundary trimming"),
        ("openai", has_module("openai"), "OpenAI-compatible client for local Gemma"),
        ("local_gemma", has_local_gemma(), "llama-server at http://127.0.0.1:8080/v1"),
    ]
    for name, ok, detail in checks:
        print(status(name, ok, detail))
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
