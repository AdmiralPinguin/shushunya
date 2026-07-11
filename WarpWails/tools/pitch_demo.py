#!/usr/bin/env python3
"""Лесенка тембра: реф «драма», одна фраза, питч вверх ступенями (формантный сдвиг «пиздюка»).

Синтез один раз, дальше ffmpeg asetrate+atempo (питч и форманты вместе — тот самый
«мелкий демонский» окрас). Перед вариантом N — N писков. WarpWails-F5 venv.
"""
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
SHIFTS = [float(a) for a in sys.argv[1:]] or [0, 5, 9, 14, 24]  # полутоны вверх
TEXT = "Ну конечно, хозяин. Смертные вечно всё ломают, а чинить нам. Какой жалкий маленький мир."
REF = "драма"
SPEED = 1.2
BASE_CACHE = PREVIEW / f"pitch_base_{REF}_{SPEED}.wav"


def beeps(n: int) -> list[float]:
    out: list[float] = []
    for _ in range(n):
        for i in range(int(SR * 0.12)):
            env = min(1.0, min(i, int(SR * 0.12) - i) / 300)
            out.append(0.27 * env * math.sin(2 * math.pi * 880 * i / SR))
        out.extend([0.0] * int(SR * 0.1))
    out.extend([0.0] * int(SR * 0.4))
    return out


def pitch_up(samples: list[float], semitones: float) -> list[float]:
    if semitones == 0:
        return samples
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
    import numpy as np

    return (np.frombuffer(proc.stdout, dtype=np.int16).astype(float) / 32767.0).tolist()


def main() -> None:
    import soundfile as sf

    PREVIEW.mkdir(exist_ok=True)
    if BASE_CACHE.exists():
        data, _ = sf.read(str(BASE_CACHE))
        base = [float(v) for v in data]
        print("база из кеша", flush=True)
    else:
        import torch
        from f5_tts.api import F5TTS
        from huggingface_hub import hf_hub_download

        profile = json.loads((ROOT / "voice_profile.json").read_text(encoding="utf-8"))
        f5_cfg = profile.get("f5", {})
        entry = f5_cfg["refs"][REF]
        ckpt = hf_hub_download("Misha24-10/F5-TTS_RUSSIAN", "F5TTS_v1_Base_accent_tune/model_last_inference.safetensors")
        vocab = hf_hub_download("Misha24-10/F5-TTS_RUSSIAN", "F5TTS_v1_Base/vocab.txt")
        torch.set_num_threads(max(1, min(16, torch.get_num_threads())))
        f5 = F5TTS(model="F5TTS_v1_Base", ckpt_file=ckpt, vocab_file=vocab, device="cpu")

        gen_text = ruaccent_f5(apply_pronunciation_overrides(TEXT, profile))
        wav, _, _ = f5.infer(
            ref_file=str(ROOT / entry["audio"]),
            ref_text=entry["text"],
            gen_text=gen_text,
            show_info=lambda *a, **k: None,
            progress=None,
            nfe_step=int(f5_cfg.get("nfe_step", 32)),
            cfg_strength=float(f5_cfg.get("cfg_strength", 2.0)),
            speed=SPEED,
            seed=f5_cfg.get("seed"),
        )
        base = [float(v) for v in wav]
        sf.write(str(BASE_CACHE), base, SR)
    print("кручу питч", flush=True)

    combined: list[float] = []
    for index, shift in enumerate(SHIFTS, 1):
        combined.extend(beeps(index))
        combined.extend(pitch_up(base, shift))
        combined.extend([0.0] * int(SR * 0.6))
        print(f"вариант {index}: +{shift} полутонов", flush=True)

    sf.write(str(PREVIEW / "pitch_demo.wav"), combined, SR)
    print("файл: preview/pitch_demo.wav", flush=True)


if __name__ == "__main__":
    main()
