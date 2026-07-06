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


ROOT = Path(__file__).resolve().parent
PROFILE_PATH = ROOT / "voice_profile.json"
SAMPLE_WIDTH = 2
CHANNELS = 1
MAX_I16 = 32767
MIN_I16 = -32768


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


def apply_pronunciation_overrides(text: str, profile: dict) -> str:
    overrides = profile.get("pronunciation_overrides", {})
    for source, replacement in sorted(overrides.items(), key=lambda item: len(item[0]), reverse=True):
        text = re.sub(rf"(?<![А-Яа-яЁё]){re.escape(source)}(?![А-Яа-яЁё])", replacement, text)
    return text


def prepare_phrase(text: str, emotion: str, profile: dict) -> str:
    phrase = apply_pronunciation_overrides(text, profile)
    if not profile.get("speak_emotion_prompts", False):
        return phrase
    prompt = profile.get("emotion_profiles", {}).get(emotion, {}).get("prompt", "")
    if prompt:
        return f"{prompt}. {phrase}"
    return phrase


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


def command_accepts_audio(command: list[str], sample_rate: int) -> bool:
    try:
        player = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert player.stdin is not None
        player.stdin.write(b"\x00\x00" * int(sample_rate * 0.05))
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
    if real_alsa and which("aplay"):
        candidates.append(("aplay", ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(sample_rate), "-c", str(CHANNELS)]))
    for name, command in candidates:
        if command_accepts_audio(command, sample_rate):
            return name, command, None
    if which("pactl") and not real_pulse and not real_alsa:
        return None, [], "Pulse/PipeWire видит только auto_null, а ALSA-карты недоступны для этой сессии."
    return None, [], "Не удалось открыть ни один raw PCM аудиовывод из этой сессии."


def float_to_pcm(samples) -> bytes:
    pcm = bytearray()
    flat = samples.detach().cpu().flatten().tolist() if hasattr(samples, "detach") else samples
    for value in flat:
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


def stream_pcm_to_speakers(chunks, sample_rate: int) -> None:
    name, command, problem = detect_player(sample_rate)
    if not command:
        raise SystemExit(problem or "Не найден плеер для стриминга в колонки.")
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


def load_model():
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    print(f"torch {torch.__version__}; cuda available: {torch.cuda.is_available()}", file=sys.stderr)
    torch.set_num_threads(max(1, min(4, torch.get_num_threads())))
    return ChatterboxMultilingualTTS.from_pretrained(device=torch.device("cpu"))


def generate_chunks(model, lines: list[tuple[str, str]], profile: dict, dry: bool):
    sample_rate = int(getattr(model, "sr", 24000) or 24000)
    processor = WarpEffectProcessor(profile, sample_rate)
    for emotion, phrase in lines:
        settings = profile.get("emotion_profiles", {}).get(emotion, {})
        prepared = prepare_phrase(phrase, emotion, profile)
        wav = model.generate(
            text=prepared,
            language_id=profile.get("chatterbox_language", "ru"),
            exaggeration=float(settings.get("exaggeration", profile.get("chatterbox_exaggeration", 0.8))),
            cfg_weight=float(settings.get("cfg_weight", profile.get("chatterbox_cfg_weight", 0.5))),
            temperature=float(settings.get("temperature", profile.get("chatterbox_temperature", 0.8))),
        )
        raw_pcm = float_to_pcm(wav)
        pcm = raw_pcm if dry else processor.process_pcm(raw_pcm)
        chunk_size = sample_rate // 5 * SAMPLE_WIDTH
        for offset in range(0, len(pcm), chunk_size):
            yield pcm[offset : offset + chunk_size]
    if not dry:
        yield processor.flush()


def check_setup(profile: dict) -> int:
    import torch

    player, _, audio_problem = detect_player(int(profile.get("sample_rate", 24000)))
    problems = []
    if not player:
        problems.append(audio_problem or "Аудиовывод недоступен.")
    print("WarpWails Chatterbox setup")
    print(f"- project: {ROOT}")
    print(f"- venv: {ROOT / 'WarpWails-Chatterbox'}")
    print("- provider: Chatterbox Multilingual")
    print("- device: CPU")
    print(f"- torch: {torch.__version__}")
    print(f"- cuda available: {torch.cuda.is_available()}")
    print(f"- player: {player or 'not found'}")
    print(f"- language: {profile.get('chatterbox_language', 'ru')}")
    print(f"- emotions: {', '.join(profile.get('emotion_profiles', {}).keys())}")
    if problems:
        print("\nПроблемы:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Chatterbox Multilingual + warp demon streaming.")
    parser.add_argument("input", nargs="?", help="UTF-8 text file with [emotion] phrase lines.")
    parser.add_argument("--text", help="Text to speak directly.")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--dry", action="store_true", help="Play clean TTS without warp effect.")
    args = parser.parse_args()

    profile = load_profile()
    if args.check:
        raise SystemExit(check_setup(profile))
    lines = parse_lines(read_text(args), profile)
    if args.preview:
        for emotion, phrase in lines:
            print(f"[{emotion}] {prepare_phrase(phrase, emotion, profile)}")
        return
    model = load_model()
    stream_pcm_to_speakers(generate_chunks(model, lines, profile, args.dry), int(getattr(model, "sr", 24000) or 24000))


if __name__ == "__main__":
    main()
