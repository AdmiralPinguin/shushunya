#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import sys
import time
from importlib.resources import files
from pathlib import Path
from shutil import which


ROOT = Path(__file__).resolve().parent
PROFILE_PATH = ROOT / "voice_profile.json"
RUACCENT_PY = ROOT / "WarpWails-RUAccent/bin/python"
RUACCENT_CLI = ROOT / "ruaccent_cli.py"
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
    lines = []
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


def ruaccent_f5(text: str) -> str:
    proc = subprocess.run(
        [str(RUACCENT_PY), str(RUACCENT_CLI), "--f5", text],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        return text
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return lines[-1] if lines else text


def prepare_text(lines: list[tuple[str, str]], profile: dict, no_ruaccent: bool) -> str:
    phrases = []
    for _, phrase in lines:
        phrase = apply_pronunciation_overrides(phrase, profile)
        phrases.append(phrase if no_ruaccent else ruaccent_f5(phrase))
    return "\n".join(phrases)


def has_real_pulse_sink() -> bool:
    if not which("pactl"):
        return False
    try:
        result = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and any(line.strip() and "auto_null" not in line for line in result.stdout.splitlines())


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
    candidates = []
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


def float_to_pcm(samples) -> bytes:
    pcm = bytearray()
    for value in samples:
        sample = int(max(-1.0, min(1.0, float(value))) * MAX_I16)
        pcm.extend(struct.pack("<h", max(MIN_I16, min(MAX_I16, sample))))
    return bytes(pcm)


def stream_pcm(pcm: bytes, sr: int) -> None:
    name, command, problem = detect_player(sr)
    if not command:
        raise SystemExit(problem or "Аудиовывод недоступен.")
    with subprocess.Popen(command, stdin=subprocess.PIPE) as player:
        assert player.stdin is not None
        player.stdin.write(pcm)
        player.stdin.close()
        rc = player.wait()
    if rc != 0:
        raise SystemExit(f"{name} завершился с ошибкой {rc}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="F5-TTS Russian + RUAccent + streaming.")
    parser.add_argument("input", nargs="?")
    parser.add_argument("--text")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--no-ruaccent", action="store_true")
    parser.add_argument("--ref-audio")
    parser.add_argument("--ref-text")
    args = parser.parse_args()

    profile = load_profile()
    if args.check:
        import torch
        player, _, _ = detect_player(24000)
        print("WarpWails F5 setup")
        print("- provider: F5-TTS_RUSSIAN accent_tune")
        print("- device: CPU")
        print(f"- torch: {torch.__version__}")
        print(f"- cuda available: {torch.cuda.is_available()}")
        print(f"- player: {player or 'not found'}")
        return

    lines = parse_lines(read_text(args), profile)
    gen_text = prepare_text(lines, profile, args.no_ruaccent)
    if args.preview:
        print(gen_text)
        return

    import torch
    from f5_tts.api import F5TTS
    from huggingface_hub import hf_hub_download

    ckpt = hf_hub_download("Misha24-10/F5-TTS_RUSSIAN", "F5TTS_v1_Base_accent_tune/model_last_inference.safetensors")
    vocab = hf_hub_download("Misha24-10/F5-TTS_RUSSIAN", "F5TTS_v1_Base/vocab.txt")
    ref_audio = args.ref_audio or str(files("f5_tts").joinpath("infer/examples/basic/basic_ref_en.wav"))
    ref_text = args.ref_text or "Some call me nature, others call me mother nature."

    torch.set_num_threads(max(1, min(4, torch.get_num_threads())))
    f5 = F5TTS(model="F5TTS_v1_Base", ckpt_file=ckpt, vocab_file=vocab, device="cpu")
    wav, sr, _ = f5.infer(
        ref_file=ref_audio,
        ref_text=ref_text,
        gen_text=gen_text,
        show_info=lambda *_args, **_kwargs: None,
        progress=None,
        nfe_step=16,
        cfg_strength=2.0,
        speed=0.95,
    )
    stream_pcm(float_to_pcm(wav), sr)


if __name__ == "__main__":
    main()
