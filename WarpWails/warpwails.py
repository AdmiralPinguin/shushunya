#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import struct
import subprocess
import sys
import time
from pathlib import Path
from shutil import which
from typing import Iterable


ROOT = Path(__file__).resolve().parent
PROFILE_PATH = ROOT / "voice_profile.json"
MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"
SAMPLE_WIDTH = 2
CHANNELS = 1
MAX_I16 = 32767
MIN_I16 = -32768
ACUTE = "\u0301"
VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"


def load_profile() -> dict:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def read_text(args: argparse.Namespace) -> str:
    if args.text:
        return args.text
    if args.input:
        return Path(args.input).read_text(encoding="utf-8")
    data = sys.stdin.read()
    if data.strip():
        return data
    raise SystemExit("Нужен текст: файл, --text или stdin.")


def parse_lines(text: str, profile: dict) -> list[tuple[str, str]]:
    known = profile.get("emotion_profiles", {})
    pattern = re.compile(r"^\s*\[([^\]]+)\]\s*(.+?)\s*$")
    lines: list[tuple[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = pattern.match(stripped)
        if match:
            emotion, phrase = match.groups()
            emotion = emotion.strip().lower()
            lines.append((emotion if emotion in known else "default", phrase.strip()))
        else:
            lines.append(("default", stripped))
    return lines


def plus_stress_to_acute(text: str) -> str:
    out: list[str] = []
    mark_next_vowel = False
    for char in text:
        if char == "+":
            mark_next_vowel = True
            continue
        out.append(char)
        if mark_next_vowel and char in VOWELS:
            out.append(ACUTE)
            mark_next_vowel = False
    return "".join(out)


def add_russian_stress(text: str) -> str:
    try:
        from silero_stress import load_accentor
    except ImportError:
        return text
    accentor = add_russian_stress.__dict__.get("_accentor")
    if accentor is None:
        accentor = load_accentor("ru")
        add_russian_stress.__dict__["_accentor"] = accentor
    return plus_stress_to_acute(accentor(text))


def prepare_phrase(text: str, emotion: str, profile: dict, auto_stress: bool) -> str:
    phrase = add_russian_stress(text) if auto_stress and ACUTE not in text else text
    phrase = apply_pronunciation_overrides(phrase, profile)
    if not profile.get("speak_emotion_prompts", False):
        return phrase
    prompt = profile.get("emotion_profiles", {}).get(emotion, {}).get("prompt", "")
    if prompt:
        return f"{prompt}. {phrase}"
    return phrase


def apply_pronunciation_overrides(text: str, profile: dict) -> str:
    overrides = profile.get("pronunciation_overrides", {})
    for source, replacement in sorted(overrides.items(), key=lambda item: len(item[0]), reverse=True):
        text = re.sub(rf"(?<![А-Яа-яЁё]){re.escape(source)}(?![А-Яа-яЁё])", replacement, text)
    return text


def has_real_pulse_sink() -> bool:
    if not which("pactl"):
        return False
    try:
        result = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    return any(line.strip() and "auto_null" not in line for line in result.stdout.splitlines())


def has_accessible_alsa_card() -> bool:
    if not which("aplay"):
        return False
    try:
        result = subprocess.run(["aplay", "-l"], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and "card " in result.stdout


def raw_silence(sample_rate: int, seconds: float = 0.05) -> bytes:
    return b"\x00\x00" * max(1, int(sample_rate * seconds))


def command_accepts_audio(command: list[str], sample_rate: int) -> bool:
    try:
        player = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert player.stdin is not None
        player.stdin.write(raw_silence(sample_rate))
        player.stdin.close()
        deadline = time.time() + 2.0
        while player.poll() is None and time.time() < deadline:
            time.sleep(0.02)
        if player.poll() is None:
            player.terminate()
            player.wait(timeout=1)
        return player.returncode == 0
    except (OSError, BrokenPipeError, subprocess.SubprocessError):
        return False


def detect_player(sample_rate: int) -> tuple[str | None, list[str], str | None]:
    real_pulse = has_real_pulse_sink()
    real_alsa = has_accessible_alsa_card()
    candidates: list[tuple[str, list[str]]] = []
    if real_pulse:
        if which("pw-play"):
            candidates.append(("pw-play", ["pw-play", "--raw", "--rate", str(sample_rate), "--channels", str(CHANNELS), "--format", "s16", "-"]))
        if which("paplay"):
            candidates.append(("paplay", ["paplay", "--raw", "--rate", str(sample_rate), "--channels", str(CHANNELS), "--format", "s16le"]))
    if real_alsa:
        candidates.append(("aplay", ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(sample_rate), "-c", str(CHANNELS)]))

    for name, command in candidates:
        if command_accepts_audio(command, sample_rate):
            return name, command, None

    if which("pactl") and not real_pulse and not real_alsa:
        return None, [], "Pulse/PipeWire видит только auto_null, а ALSA-карты недоступны для этой сессии."
    return None, [], "Не удалось открыть ни один raw PCM аудиовывод из этой сессии."


def float_to_pcm(samples) -> bytes:
    pcm = bytearray()
    for value in samples:
        sample = int(max(-1.0, min(1.0, float(value))) * MAX_I16)
        pcm.extend(struct.pack("<h", max(MIN_I16, min(MAX_I16, sample))))
    return bytes(pcm)


def pcm_to_samples(pcm: bytes) -> list[int]:
    usable = len(pcm) - (len(pcm) % SAMPLE_WIDTH)
    return list(struct.unpack(f"<{usable // SAMPLE_WIDTH}h", pcm[:usable]))


def samples_to_pcm(samples: list[int]) -> bytes:
    clipped = [max(MIN_I16, min(MAX_I16, int(sample))) for sample in samples]
    return struct.pack(f"<{len(clipped)}h", *clipped)


def saturate(value: float, drive: float) -> float:
    return math.tanh((value / MAX_I16) * drive) * MAX_I16


class WarpEffectProcessor:
    def __init__(self, profile: dict, sample_rate: int):
        effect = profile.get("warp_effect", {})
        self.sample_rate = sample_rate
        self.drive = float(effect.get("drive", 1.8))
        self.wet = float(effect.get("wet", 0.8))
        self.shimmer_depth = float(effect.get("shimmer_depth", 0.08))
        self.shimmer_hz = float(effect.get("shimmer_hz", 5.5))
        self.low_mix = float(effect.get("low_voice_mix", 0.3))
        self.echoes = [
            (int(float(echo["delay_ms"]) * sample_rate / 1000), float(echo["decay"]))
            for echo in effect.get("echoes", [])
        ]
        self.max_delay = max((delay for delay, _ in self.echoes), default=0)
        self.index = 0
        self.history: list[int] = []
        self.echo_buffer: dict[int, float] = {}

    def process_samples(self, samples: list[int]) -> list[int]:
        out: list[int] = []
        dry = 1.0 - self.wet
        for sample in samples:
            source_index = self.index // 2
            low = self.history[source_index] * self.low_mix if source_index < len(self.history) else 0.0
            shimmer = 1.0 + self.shimmer_depth * math.sin(2.0 * math.pi * self.shimmer_hz * self.index / self.sample_rate)
            base = saturate((sample + low) * shimmer, self.drive)
            echo_value = self.echo_buffer.pop(self.index, 0.0)
            mixed = sample * dry + (base + echo_value) * self.wet
            out.append(int(mixed))
            for delay, decay in self.echoes:
                target = self.index + delay
                self.echo_buffer[target] = self.echo_buffer.get(target, 0.0) + base * decay
            self.history.append(sample)
            self.index += 1
        return out

    def process_pcm(self, pcm: bytes) -> bytes:
        return samples_to_pcm(self.process_samples(pcm_to_samples(pcm)))

    def flush(self) -> bytes:
        if self.max_delay <= 0:
            return b""
        return samples_to_pcm(self.process_samples([0] * self.max_delay))


def stream_pcm_to_speakers(chunks: Iterable[bytes], sample_rate: int) -> None:
    name, command, problem = detect_player(sample_rate)
    if not command:
        raise SystemExit(problem or "Не найден плеер для стриминга в колонки: нужен aplay, pw-play или paplay.")
    with subprocess.Popen(command, stdin=subprocess.PIPE) as player:
        assert player.stdin is not None
        try:
            for chunk in chunks:
                if chunk:
                    player.stdin.write(chunk)
                    player.stdin.flush()
        finally:
            player.stdin.close()
        rc = player.wait()
    if rc != 0:
        raise SystemExit(f"{name} завершился с ошибкой {rc}.")


def cpu_test_chunks(profile: dict, sample_rate: int, seconds: float = 1.0) -> Iterable[bytes]:
    processor = WarpEffectProcessor(profile, sample_rate)
    chunk_size = 1024
    total = int(sample_rate * seconds)
    for start in range(0, total, chunk_size):
        samples = [
            int(9000 * math.sin(2.0 * math.pi * 155.0 * index / sample_rate))
            for index in range(start, min(start + chunk_size, total))
        ]
        yield samples_to_pcm(processor.process_samples(samples))
    yield processor.flush()


def load_xtts():
    import torch
    from TTS.api import TTS

    if torch.cuda.is_available():
        print("CUDA найдена, но не используется: запуск строго на CPU.", file=sys.stderr)
    torch.set_num_threads(max(1, min(4, torch.get_num_threads())))
    return TTS(MODEL_NAME, gpu=False)


def xtts_stream_chunks(tts, lines: list[tuple[str, str]], profile: dict, auto_stress: bool) -> Iterable[bytes]:
    sample_rate = int(getattr(tts.synthesizer, "output_sample_rate", 24000) or 24000)
    processor = WarpEffectProcessor(profile, sample_rate)
    default_speaker = profile.get("speaker")
    if not default_speaker and getattr(tts, "speakers", None):
        default_speaker = tts.speakers[0]
    for emotion, phrase in lines:
        settings = profile.get("emotion_profiles", {}).get(emotion, {})
        prepared = prepare_phrase(phrase, emotion, profile, auto_stress)
        wav = tts.tts(
            text=prepared,
            language=profile.get("language", "ru"),
            speaker=settings.get("speaker") or default_speaker,
            speaker_wav=profile.get("speaker_wav") or None,
            split_sentences=True,
        )
        pcm = processor.process_pcm(float_to_pcm(wav))
        chunk_size = sample_rate // 5 * SAMPLE_WIDTH
        for offset in range(0, len(pcm), chunk_size):
            yield pcm[offset : offset + chunk_size]
    yield processor.flush()


def check_setup(profile: dict) -> int:
    sample_rate = int(profile.get("sample_rate", 24000))
    player, _, audio_problem = detect_player(sample_rate)
    problems: list[str] = []
    if not player:
        problems.append(audio_problem or "Нет аудиоплеера для raw PCM: нужен aplay, pw-play или paplay.")
    try:
        import torch
        from TTS.api import TTS  # noqa: F401
        from silero_stress import load_accentor  # noqa: F401
    except ImportError as exc:
        problems.append(f"Не хватает Python-модуля: {exc.name}")
        torch = None

    print("WarpWails setup")
    print(f"- project: {ROOT}")
    print(f"- venv: {ROOT / 'WarpWails-XTTS'}")
    print("- provider: local XTTS-v2")
    print("- device: CPU")
    if "torch" in locals() and torch is not None:
        print(f"- torch: {torch.__version__}")
        print(f"- cuda available: {torch.cuda.is_available()}")
    print(f"- player: {player or 'not found'}")
    print(f"- model: {MODEL_NAME}")
    print(f"- language: {profile.get('language', 'ru')}")
    print(f"- auto stress: {profile.get('auto_stress', True)}")
    print(f"- emotions: {', '.join(profile.get('emotion_profiles', {}).keys())}")
    if problems:
        print("\nПроблемы:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("\nЛокальная CPU-настройка готова. Модель XTTS скачивается при первом запуске после принятия CPML.")
    return 0


def list_emotions(profile: dict) -> None:
    for name, data in profile.get("emotion_profiles", {}).items():
        print(f"{name}: speed={data.get('speed', profile.get('speed', 1.0))}; {data.get('prompt', '')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local CPU XTTS-v2 + warp demon streaming to speakers.")
    parser.add_argument("input", nargs="?", help="UTF-8 text file with [emotion] phrase lines.")
    parser.add_argument("--text", help="Text to speak directly.")
    parser.add_argument("--preview", action="store_true", help="Print prepared text and exit.")
    parser.add_argument("--check", action="store_true", help="Check local CPU setup.")
    parser.add_argument("--cpu-test", action="store_true", help="Stream a short CPU-only processed test tone to speakers.")
    parser.add_argument("--emotions", action="store_true", help="Print configured emotion aliases.")
    parser.add_argument("--no-auto-stress", action="store_true", help="Disable automatic Russian stress marks.")
    args = parser.parse_args()

    profile = load_profile()
    auto_stress = bool(profile.get("auto_stress", True)) and not args.no_auto_stress

    if args.check:
        raise SystemExit(check_setup(profile))
    if args.emotions:
        list_emotions(profile)
        return
    if args.cpu_test:
        stream_pcm_to_speakers(cpu_test_chunks(profile, int(profile.get("sample_rate", 24000))), int(profile.get("sample_rate", 24000)))
        return

    lines = parse_lines(read_text(args), profile)
    if args.preview:
        for emotion, phrase in lines:
            print(f"[{emotion}] {prepare_phrase(phrase, emotion, profile, auto_stress)}")
        return

    tts = load_xtts()
    sample_rate = int(getattr(tts.synthesizer, "output_sample_rate", 24000) or 24000)
    stream_pcm_to_speakers(xtts_stream_chunks(tts, lines, profile, auto_stress), sample_rate)


if __name__ == "__main__":
    main()
