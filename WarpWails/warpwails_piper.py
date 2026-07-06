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
MODEL_PATH = ROOT / "models/piper/ru_RU-dmitri-medium/ru_RU-dmitri-medium.onnx"
CONFIG_PATH = ROOT / "models/piper/ru_RU-dmitri-medium/ru_RU-dmitri-medium.onnx.json"
PIPER_BIN = ROOT / "WarpWails-Piper/bin/piper"
SAMPLE_WIDTH = 2
CHANNELS = 1
MAX_I16 = 32767
MIN_I16 = -32768


def load_profile() -> dict:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def sample_rate() -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return int(config.get("audio", {}).get("sample_rate", 22050))


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


def piper_sarcasm_text(text: str, emotion: str, profile: dict) -> str:
    text = apply_pronunciation_overrides(text, profile)
    if emotion in {"сарказм", "ехидно"}:
        text = re.sub(r"([.!?])\s+", r"\1 ... ", text)
        text = text.replace("Ну ", "Нууу ")
    if emotion == "угроза":
        text = re.sub(r"([.!?])\s+", r"\1 ... ", text)
    if emotion == "шепот":
        text = text.replace(".", "...")
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


def command_accepts_audio(command: list[str], sr: int) -> bool:
    try:
        player = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert player.stdin is not None
        player.stdin.write(b"\x00\x00" * int(sr * 0.05))
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


def detect_player(sr: int) -> tuple[str | None, list[str], str | None]:
    candidates: list[tuple[str, list[str]]] = []
    if has_real_pulse_sink():
        if which("pw-play"):
            candidates.append(("pw-play", ["pw-play", "--raw", "--rate", str(sr), "--channels", str(CHANNELS), "--format", "s16", "-"]))
        if which("paplay"):
            candidates.append(("paplay", ["paplay", "--raw", "--rate", str(sr), "--channels", str(CHANNELS), "--format", "s16le"]))
    if has_accessible_alsa_card() and which("aplay"):
        candidates.append(("aplay", ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(sr), "-c", str(CHANNELS)]))
    for name, command in candidates:
        if command_accepts_audio(command, sr):
            return name, command, None
    return None, [], "Не удалось открыть аудиовывод."


def pcm_to_samples(pcm: bytes) -> list[int]:
    usable = len(pcm) - (len(pcm) % SAMPLE_WIDTH)
    return list(struct.unpack(f"<{usable // SAMPLE_WIDTH}h", pcm[:usable]))


def samples_to_pcm(samples: list[int]) -> bytes:
    clipped = [max(MIN_I16, min(MAX_I16, int(sample))) for sample in samples]
    return struct.pack(f"<{len(clipped)}h", *clipped)


def saturate(value: float, drive: float) -> float:
    return math.tanh((value / MAX_I16) * drive) * MAX_I16


class ManualEmotionProcessor:
    def __init__(self, profile: dict, sr: int, emotion: str, dry: bool):
        effect = profile.get("warp_effect", {})
        self.sr = sr
        self.dry = dry
        self.emotion = emotion
        self.drive = float(effect.get("drive", 1.25))
        self.wet = float(effect.get("wet", 0.28))
        self.low_mix = float(effect.get("low_voice_mix", 0.08))
        self.shimmer_depth = float(effect.get("shimmer_depth", 0.025))
        self.shimmer_hz = float(effect.get("shimmer_hz", 5.7))
        self.echoes = [
            (int(float(echo["delay_ms"]) * sr / 1000), float(echo["decay"]))
            for echo in effect.get("echoes", [])
        ]
        if emotion in {"сарказм", "ехидно"}:
            self.drive *= 1.25
            self.shimmer_depth *= 1.4
            self.wet *= 0.7
        if emotion == "угроза":
            self.low_mix *= 2.0
            self.wet *= 1.15
        self.max_delay = max((delay for delay, _ in self.echoes), default=0)
        self.index = 0
        self.history: list[int] = []
        self.echo_buffer: dict[int, float] = {}

    def process_samples(self, samples: list[int]) -> list[int]:
        if self.dry:
            return samples
        out: list[int] = []
        dry_mix = 1.0 - self.wet
        for sample in samples:
            source_index = self.index // 2
            low = self.history[source_index] * self.low_mix if source_index < len(self.history) else 0.0
            shimmer = 1.0 + self.shimmer_depth * math.sin(2.0 * math.pi * self.shimmer_hz * self.index / self.sr)
            base = saturate((sample + low) * shimmer, self.drive)
            echo_value = self.echo_buffer.pop(self.index, 0.0)
            mixed = sample * dry_mix + (base + echo_value) * self.wet
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
        if self.dry or self.max_delay <= 0:
            return b""
        return samples_to_pcm(self.process_samples([0] * self.max_delay))


def stream_to_speakers(chunks, sr: int) -> None:
    name, command, problem = detect_player(sr)
    if not command:
        raise SystemExit(problem or "Аудиовывод недоступен.")
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


def piper_chunks(lines: list[tuple[str, str]], profile: dict, dry: bool):
    sr = sample_rate()
    for emotion, phrase in lines:
        text = piper_sarcasm_text(phrase, emotion, profile)
        command = [
            str(PIPER_BIN),
            "--model", str(MODEL_PATH),
            "--config", str(CONFIG_PATH),
            "--output-raw",
            "--length-scale", "1.12" if emotion in {"сарказм", "ехидно", "угроза"} else "1.0",
            "--noise-scale", "0.72",
            "--noise-w-scale", "0.9",
        ]
        processor = ManualEmotionProcessor(profile, sr, emotion, dry)
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write((text + "\n").encode("utf-8"))
        proc.stdin.close()
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            yield processor.process_pcm(chunk)
        tail = processor.flush()
        if tail:
            yield tail
        stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        rc = proc.wait()
        if rc != 0:
            raise SystemExit(f"piper завершился с ошибкой {rc}: {stderr}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Piper Russian + manual sarcasm + streaming.")
    parser.add_argument("input", nargs="?")
    parser.add_argument("--text")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    profile = load_profile()
    sr = sample_rate()
    if args.check:
        player, _, problem = detect_player(sr)
        print("WarpWails Piper setup")
        print(f"- provider: Piper ru_RU-dmitri-medium")
        print(f"- model: {MODEL_PATH}")
        print(f"- sample_rate: {sr}")
        print(f"- player: {player or 'not found'}")
        if not player:
            raise SystemExit(problem or "Аудиовывод недоступен.")
        return

    lines = parse_lines(read_text(args), profile)
    if args.preview:
        for emotion, phrase in lines:
            print(f"[{emotion}] {piper_sarcasm_text(phrase, emotion, profile)}")
        return
    stream_to_speakers(piper_chunks(lines, profile, args.dry), sr)


if __name__ == "__main__":
    main()
