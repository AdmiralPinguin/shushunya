#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

try:
    from dotenv import load_dotenv as dotenv_load
except ImportError:  # pragma: no cover - fallback for bare stdlib runs
    dotenv_load = None


ROOT = Path(__file__).resolve().parent
PROFILE_PATH = ROOT / "voice_profile.json"
ENV_PATH = ROOT / ".env"
SAMPLE_RATE = 44100
SAMPLE_WIDTH = 2
CHANNELS = 1
MAX_I16 = 32767
MIN_I16 = -32768


def load_dotenv(path: Path) -> None:
    if dotenv_load is not None:
        dotenv_load(path)
        return
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_profile() -> dict:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def check_setup(profile: dict) -> int:
    load_dotenv(ENV_PATH)
    problems: list[str] = []
    if not ENV_PATH.exists():
        problems.append("Нет .env. Создай его из .env.example.")
    if not os.environ.get("ELEVENLABS_API_KEY"):
        problems.append("Нет ELEVENLABS_API_KEY.")
    if not os.environ.get("ELEVENLABS_VOICE_ID"):
        problems.append("Нет ELEVENLABS_VOICE_ID.")

    print("WarpWails setup")
    print(f"- project: {ROOT}")
    print(f"- venv: {ROOT / 'WarpWails'}")
    print(f"- provider: {profile.get('provider')}")
    print(f"- model: {profile.get('model_id')}")
    print(f"- language: {profile.get('language_code')}")
    print(f"- emotions: {', '.join(profile.get('emotion_tags', {}).keys())}")

    if problems:
        print("\nНужно заполнить:")
        for problem in problems:
            print(f"- {problem}")
        return 1

    print("\nНастройка выглядит готовой.")
    return 0


def list_emotions(profile: dict) -> None:
    for name, tag in profile.get("emotion_tags", {}).items():
        print(f"{name}: {tag}")


def list_elevenlabs_voices() -> None:
    load_dotenv(ENV_PATH)
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise SystemExit("Нет ELEVENLABS_API_KEY. Заполни /media/shushunya/SHUSHUNYA/shushunya/WarpWails/.env")

    request = urllib.request.Request(
        "https://api.elevenlabs.io/v1/voices",
        headers={"xi-api-key": api_key, "accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"ElevenLabs вернул ошибку {exc.code}: {details}") from exc

    for voice in payload.get("voices", []):
        name = voice.get("name", "unknown")
        voice_id = voice.get("voice_id", "")
        category = voice.get("category", "")
        labels = voice.get("labels", {})
        label_text = ", ".join(f"{key}={value}" for key, value in labels.items())
        suffix = f" ({category}; {label_text})" if category or label_text else ""
        print(f"{name}: {voice_id}{suffix}")


def read_text(args: argparse.Namespace) -> str:
    if args.text:
        return args.text
    if args.input:
        return Path(args.input).read_text(encoding="utf-8")
    data = sys.stdin.read()
    if data.strip():
        return data
    raise SystemExit("Нужен текст: файл, --text или stdin.")


def apply_emotion_tags(text: str, profile: dict) -> str:
    tags = profile.get("emotion_tags", {})
    default_direction = profile.get("default_direction", "")
    out_lines: list[str] = []
    pattern = re.compile(r"^\s*\[([^\]]+)\]\s*(.+?)\s*$")

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = pattern.match(stripped)
        if not match:
            out_lines.append(f"{default_direction} {stripped}".strip())
            continue

        emotion, phrase = match.groups()
        normalized = emotion.strip().lower()
        direction = tags.get(normalized, f"[{emotion}]")
        out_lines.append(f"{default_direction}{direction} {phrase}".strip())

    return "\n".join(out_lines)


def elevenlabs_tts_pcm(text: str, profile: dict) -> bytes:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID")
    if not api_key:
        raise SystemExit("Нет ELEVENLABS_API_KEY. Заполни /media/shushunya/SHUSHUNYA/shushunya/WarpWails/.env")
    if not voice_id:
        raise SystemExit("Нет ELEVENLABS_VOICE_ID. Заполни /media/shushunya/SHUSHUNYA/shushunya/WarpWails/.env")

    query = urllib.parse.urlencode({"output_format": profile.get("output_format", "pcm_44100")})
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?{query}"
    payload = {
        "text": text,
        "model_id": profile.get("model_id", "eleven_v3"),
        "language_code": profile.get("language_code", "ru"),
        "voice_settings": profile.get("voice_settings", {}),
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "xi-api-key": api_key,
            "content-type": "application/json",
            "accept": "audio/pcm",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"ElevenLabs вернул ошибку {exc.code}: {details}") from exc


def pcm_to_samples(pcm: bytes) -> list[int]:
    usable = len(pcm) - (len(pcm) % SAMPLE_WIDTH)
    return list(struct.unpack(f"<{usable // SAMPLE_WIDTH}h", pcm[:usable]))


def samples_to_pcm(samples: list[int]) -> bytes:
    clipped = [max(MIN_I16, min(MAX_I16, int(sample))) for sample in samples]
    return struct.pack(f"<{len(clipped)}h", *clipped)


def low_voice_layer(samples: list[int], mix: float) -> list[float]:
    lowered: list[float] = []
    for index in range(len(samples)):
        source_index = index // 2
        if source_index < len(samples):
            lowered.append(samples[source_index] * mix)
        else:
            lowered.append(0.0)
    return lowered


def saturate(value: float, drive: float) -> float:
    return math.tanh((value / MAX_I16) * drive) * MAX_I16


def apply_warp_effect(samples: list[int], profile: dict) -> list[int]:
    effect = profile.get("warp_effect", {})
    drive = float(effect.get("drive", 1.8))
    wet = float(effect.get("wet", 0.8))
    shimmer_depth = float(effect.get("shimmer_depth", 0.08))
    shimmer_hz = float(effect.get("shimmer_hz", 5.5))
    low_mix = float(effect.get("low_voice_mix", 0.3))
    echoes = effect.get("echoes", [])

    low = low_voice_layer(samples, low_mix)
    output = [0.0] * (len(samples) + max((int(e["delay_ms"] * SAMPLE_RATE / 1000) for e in echoes), default=0))

    for index, sample in enumerate(samples):
        shimmer = 1.0 + shimmer_depth * math.sin(2.0 * math.pi * shimmer_hz * index / SAMPLE_RATE)
        base = saturate((sample + low[index]) * shimmer, drive)
        output[index] += base
        for echo in echoes:
            delay = int(float(echo["delay_ms"]) * SAMPLE_RATE / 1000)
            decay = float(echo["decay"])
            target = index + delay
            if target < len(output):
                output[target] += base * decay

    dry = 1.0 - wet
    final: list[int] = []
    for index, value in enumerate(output):
        original = samples[index] if index < len(samples) else 0
        final.append(int(original * dry + value * wet))
    return final


def write_wav(path: Path, samples: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(samples_to_pcm(samples))


def main() -> None:
    parser = argparse.ArgumentParser(description="ElevenLabs TTS + warp demon voice processing.")
    parser.add_argument("input", nargs="?", help="UTF-8 text file with [emotion] phrase lines.")
    parser.add_argument("--text", help="Text to speak directly.")
    parser.add_argument("--out", default="out/warp.wav", help="Output WAV path.")
    parser.add_argument("--dry-out", help="Optional unprocessed ElevenLabs WAV path.")
    parser.add_argument("--preview", action="store_true", help="Print the final tagged prompt and exit.")
    parser.add_argument("--check", action="store_true", help="Check local setup and required .env values.")
    parser.add_argument("--emotions", action="store_true", help="Print configured emotion aliases.")
    parser.add_argument("--list-voices", action="store_true", help="Print available ElevenLabs voices for the API key.")
    args = parser.parse_args()

    load_dotenv(ENV_PATH)
    profile = load_profile()

    if args.check:
        raise SystemExit(check_setup(profile))
    if args.emotions:
        list_emotions(profile)
        return
    if args.list_voices:
        list_elevenlabs_voices()
        return

    raw_text = read_text(args)
    tagged_text = apply_emotion_tags(raw_text, profile)

    if args.preview:
        print(tagged_text)
        return

    pcm = elevenlabs_tts_pcm(tagged_text, profile)
    dry_samples = pcm_to_samples(pcm)
    if args.dry_out:
        write_wav(Path(args.dry_out), dry_samples)

    wet_samples = apply_warp_effect(dry_samples, profile)
    write_wav(Path(args.out), wet_samples)
    print(Path(args.out).resolve())


if __name__ == "__main__":
    main()
