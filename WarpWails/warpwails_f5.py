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
        result = text
    else:
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        result = lines[-1] if lines else text
    # тире-пауза в начале: F5 жрёт первый слог фразы без разгона
    return result if result.startswith("—") else f"— {result}"


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


def detect_player(sr: int, preferred_alsa: str | None = None) -> tuple[str | None, list[str], str | None]:
    candidates = []
    if preferred_alsa and which("aplay"):
        # прибитый гвоздём выход (колонки): двойной PipeWire роняет звук то в null, то в моник
        candidates.append(("aplay", ["aplay", "-q", "-D", preferred_alsa, "-t", "raw", "-f", "S16_LE", "-r", str(sr), "-c", str(CHANNELS)]))
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
    stream_pcm_iter([pcm], sr)


def stream_pcm_iter(chunks, sr: int) -> None:
    """Льёт куски PCM в плеер по мере готовности (конвейер: играем — пока считается следующее)."""
    name, command, problem = detect_player(sr)
    if not command:
        raise SystemExit(problem or "Аудиовывод недоступен.")
    pad_head = b"\x00\x00" * (sr * 45 // 100)  # синк просыпается — глотает начало
    pad_tail = b"\x00\x00" * (sr * 70 // 100)
    with subprocess.Popen(command, stdin=subprocess.PIPE) as player:
        assert player.stdin is not None
        player.stdin.write(pad_head)
        for chunk in chunks:
            if chunk:
                player.stdin.write(chunk)
                player.stdin.flush()
        player.stdin.write(pad_tail)
        player.stdin.close()
        rc = player.wait()
    if rc != 0:
        raise SystemExit(f"{name} завершился с ошибкой {rc}.")


def pick_ref(profile: dict, emotion: str) -> tuple[str, str, float | None]:
    """Референс под эмоцию: свой клип на каждую, default как запасной. Третье — спид-оверрайд эмоции."""
    refs = profile.get("f5", {}).get("refs", {})
    entry = refs.get(emotion) or refs.get("default")
    if entry:
        audio = ROOT / entry["audio"]
        if audio.exists():
            speed = entry.get("speed")
            return str(audio), entry["text"], float(speed) if speed else None
    return (
        str(files("f5_tts").joinpath("infer/examples/basic/basic_ref_en.wav")),
        "Some call me nature, others call me mother nature.",
        None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="F5-TTS Russian + RUAccent + варп-пиздюк + streaming.")
    parser.add_argument("input", nargs="?")
    parser.add_argument("--text")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--no-ruaccent", action="store_true")
    parser.add_argument("--no-sfx", action="store_true", help="Без смешков/скрипов.")
    parser.add_argument("--dry", action="store_true", help="Чистый голос без варп-эффекта.")
    parser.add_argument("--seed", type=int, help="Сид генератора вставок.")
    parser.add_argument("--ref-audio")
    parser.add_argument("--ref-text")
    args = parser.parse_args()

    profile = load_profile()
    if args.check:
        import torch
        player, _, _ = detect_player(24000)
        refs = profile.get("f5", {}).get("refs", {})
        print("WarpWails F5 setup")
        print("- provider: F5-TTS_RUSSIAN accent_tune")
        print("- device: CPU")
        print(f"- torch: {torch.__version__}")
        print(f"- cuda available: {torch.cuda.is_available()}")
        print(f"- player: {player or 'not found'}")
        print(f"- refs: {', '.join(sorted(refs)) or 'нет (fallback на демо-клип)'}")
        return

    lines = parse_lines(read_text(args), profile)
    if args.preview:
        for emotion, phrase in lines:
            phrase = apply_pronunciation_overrides(phrase, profile)
            print(f"[{emotion}] {phrase if args.no_ruaccent else ruaccent_f5(phrase)}")
        return

    import torch
    from f5_tts.api import F5TTS
    from huggingface_hub import hf_hub_download

    from warp_effect import WarpImpEffect
    from warp_sfx import WarpSfxInserter

    ckpt = hf_hub_download("Misha24-10/F5-TTS_RUSSIAN", "F5TTS_v1_Base_accent_tune/model_last_inference.safetensors")
    vocab = hf_hub_download("Misha24-10/F5-TTS_RUSSIAN", "F5TTS_v1_Base/vocab.txt")
    f5_cfg = profile.get("f5", {})

    torch.set_num_threads(max(1, min(16, torch.get_num_threads())))
    f5 = F5TTS(model="F5TTS_v1_Base", ckpt_file=ckpt, vocab_file=vocab, device="cpu")

    sr = 24000
    effect = None if args.dry else WarpImpEffect(profile, sr)
    sfx = None
    if not args.no_sfx and not args.dry:
        sfx = WarpSfxInserter(profile, sr, seed=args.seed)

    import queue
    import threading

    pcm_queue: queue.Queue = queue.Queue(maxsize=4)
    fail: list[BaseException] = []

    def synth_worker() -> None:
        try:
            for index, (emotion, phrase) in enumerate(lines):
                prepared = apply_pronunciation_overrides(phrase, profile)
                gen_text = prepared if args.no_ruaccent else ruaccent_f5(prepared)
                if args.ref_audio:
                    ref_audio, ref_text, speed_override = args.ref_audio, args.ref_text or "", None
                else:
                    ref_audio, ref_text, speed_override = pick_ref(profile, emotion)
                wav, _, _ = f5.infer(
                    ref_file=ref_audio,
                    ref_text=ref_text,
                    gen_text=gen_text,
                    show_info=lambda *_args, **_kwargs: None,
                    progress=None,
                    nfe_step=int(f5_cfg.get("nfe_step", 32)),
                    cfg_strength=float(f5_cfg.get("cfg_strength", 2.0)),
                    speed=speed_override or float(f5_cfg.get("speed", 0.95)),
                    seed=f5_cfg.get("seed"),
                )
                samples = [int(max(-1.0, min(1.0, float(v))) * MAX_I16) for v in wav]
                pitch = float(f5_cfg.get("pitch_semitones", 0.0))
                if pitch:
                    from warp_effect import pitch_shift_ffmpeg

                    samples = pitch_shift_ffmpeg(samples, sr, pitch)
                if effect is not None:
                    samples = effect.process(samples)
                if sfx is not None:
                    if index > 0:
                        gap = sfx.between_phrases(emotion)
                        if gap:
                            pcm_queue.put(struct.pack(f"<{len(gap)}h", *gap))
                    pre = sfx.pre_phrase(emotion)
                    if pre:
                        pcm_queue.put(struct.pack(f"<{len(pre)}h", *pre))
                    samples = sfx.process_phrase(samples, emotion)
                pcm_queue.put(struct.pack(f"<{len(samples)}h", *[max(MIN_I16, min(MAX_I16, s)) for s in samples]))
            if sfx is not None and lines:
                tail = sfx.tail(lines[-1][0])
                if tail:
                    pcm_queue.put(struct.pack(f"<{len(tail)}h", *tail))
        except BaseException as exc:  # пробрасываем в основной поток
            fail.append(exc)
        finally:
            pcm_queue.put(None)

    threading.Thread(target=synth_worker, daemon=True).start()

    def queue_chunks():
        while True:
            chunk = pcm_queue.get()
            if chunk is None:
                break
            yield chunk

    stream_pcm_iter(queue_chunks(), sr)
    if fail:
        raise SystemExit(f"Синтез упал: {fail[0]}")


if __name__ == "__main__":
    main()
