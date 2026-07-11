#!/usr/bin/env python3
"""Сетка скорость × питч: на каждой скорости фраза в +5 и +6 полутонов. WarpWails-F5 venv."""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from warpwails_f5 import apply_pronunciation_overrides, ruaccent_f5  # noqa: E402

PREVIEW = ROOT / "preview"
FF = ROOT / "tools" / "ffmpeg-7.0.2-amd64-static" / "ffmpeg"
SR = 24000
SPEEDS = [1.15, 1.3, 1.45]
SHIFTS = [5.0, 6.0]
REF = "драма"
NAMES = {1.15: "один пятнадцать", 1.3: "один тридцать", 1.45: "один сорок пять"}


def tone(freq: float, dur: float, gain: float = 0.27) -> list[float]:
    out = []
    n = int(SR * dur)
    for i in range(n):
        env = min(1.0, min(i, n - i) / 300)
        out.append(gain * env * math.sin(2 * math.pi * freq * i / SR))
    return out


def double_beep() -> list[float]:
    return tone(880, 0.12) + [0.0] * int(SR * 0.1) + tone(880, 0.12) + [0.0] * int(SR * 0.4)


def click() -> list[float]:
    return [0.0] * int(SR * 0.25) + tone(1400, 0.05, 0.2) + [0.0] * int(SR * 0.25)


def pitch_up(samples: list[float], semitones: float) -> list[float]:
    import numpy as np

    factor = 2.0 ** (semitones / 12.0)
    tempo = 1.0 / factor
    filters = [f"asetrate={int(SR * factor)}", f"aresample={SR}"]
    while tempo < 0.5:
        filters.append("atempo=0.5")
        tempo /= 0.5
    filters.append(f"atempo={tempo}")
    pcm = b"".join(
        int(max(-1.0, min(1.0, s)) * 32767).to_bytes(2, "little", signed=True) for s in samples
    )
    proc = subprocess.run(
        [str(FF), "-v", "error", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "-",
         "-af", ",".join(filters), "-f", "s16le", "-"],
        input=pcm,
        capture_output=True,
        check=True,
    )
    return (np.frombuffer(proc.stdout, dtype=np.int16).astype(float) / 32767.0).tolist()


def main() -> None:
    import soundfile as sf
    import torch
    from f5_tts.api import F5TTS
    from huggingface_hub import hf_hub_download

    profile = json.loads((ROOT / "voice_profile.json").read_text(encoding="utf-8"))
    f5_cfg = profile.get("f5", {})
    entry = f5_cfg["refs"][REF]

    PREVIEW.mkdir(exist_ok=True)
    ckpt = hf_hub_download("Misha24-10/F5-TTS_RUSSIAN", "F5TTS_v1_Base_accent_tune/model_last_inference.safetensors")
    vocab = hf_hub_download("Misha24-10/F5-TTS_RUSSIAN", "F5TTS_v1_Base/vocab.txt")
    torch.set_num_threads(max(1, min(16, torch.get_num_threads())))
    f5 = F5TTS(model="F5TTS_v1_Base", ckpt_file=ckpt, vocab_file=vocab, device="cpu")

    combined: list[float] = []
    for speed in SPEEDS:
        cache = PREVIEW / f"pitch_base_{REF}_{speed}.wav"
        if cache.exists():
            data, _ = sf.read(str(cache))
            base = [float(v) for v in data]
        else:
            text = f"Скорость {NAMES[speed]}. Ну конечно, хозяин. Смертные вечно всё ломают, а чинить нам."
            gen_text = ruaccent_f5(apply_pronunciation_overrides(text, profile))
            wav, _, _ = f5.infer(
                ref_file=str(ROOT / entry["audio"]),
                ref_text=entry["text"],
                gen_text=gen_text,
                show_info=lambda *a, **k: None,
                progress=None,
                nfe_step=int(f5_cfg.get("nfe_step", 32)),
                cfg_strength=float(f5_cfg.get("cfg_strength", 2.0)),
                speed=speed,
                seed=f5_cfg.get("seed"),
            )
            base = [float(v) for v in wav]
            sf.write(str(cache), base, SR)
        combined.extend(double_beep())
        combined.extend(pitch_up(base, SHIFTS[0]))
        combined.extend(click())
        combined.extend(pitch_up(base, SHIFTS[1]))
        combined.extend([0.0] * int(SR * 0.6))
        print(f"готово: speed={speed} (+{SHIFTS[0]:.0f} и +{SHIFTS[1]:.0f})", flush=True)

    sf.write(str(PREVIEW / "speed_pitch_demo.wav"), combined, SR)
    print("файл: preview/speed_pitch_demo.wav", flush=True)


if __name__ == "__main__":
    main()
