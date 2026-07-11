#!/usr/bin/env python3
"""Бейк-офф в wav: рендерит кандидатов базового голоса в preview/ для быстрого сравнения.

Запускать из WarpWails-F5 venv. Модель грузится один раз на все варианты.
"""
from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from warpwails_f5 import apply_pronunciation_overrides, ruaccent_f5  # noqa: E402

PREVIEW = ROOT / "preview"
SR = 24000
CANDIDATES = ["default", "холодно", "драма"]
TEXT = "Ну конечно, хозяин. Смертные вечно всё ломают, а чинить нам. Какой жалкий маленький мир."


def beeps(n: int) -> list[float]:
    out: list[float] = []
    for _ in range(n):
        for i in range(int(SR * 0.12)):
            env = min(1.0, min(i, int(SR * 0.12) - i) / 300)
            out.append(0.27 * env * math.sin(2 * math.pi * 880 * i / SR))
        out.extend([0.0] * int(SR * 0.1))
    out.extend([0.0] * int(SR * 0.5))
    return out


def write_wav(path: Path, samples: list[float]) -> None:
    import soundfile as sf

    sf.write(str(path), samples, SR)


def main() -> None:
    import torch
    from f5_tts.api import F5TTS
    from huggingface_hub import hf_hub_download

    profile = json.loads((ROOT / "voice_profile.json").read_text(encoding="utf-8"))
    f5_cfg = profile.get("f5", {})
    refs = f5_cfg.get("refs", {})

    PREVIEW.mkdir(exist_ok=True)
    ckpt = hf_hub_download("Misha24-10/F5-TTS_RUSSIAN", "F5TTS_v1_Base_accent_tune/model_last_inference.safetensors")
    vocab = hf_hub_download("Misha24-10/F5-TTS_RUSSIAN", "F5TTS_v1_Base/vocab.txt")
    torch.set_num_threads(max(1, min(16, torch.get_num_threads())))
    f5 = F5TTS(model="F5TTS_v1_Base", ckpt_file=ckpt, vocab_file=vocab, device="cpu")

    phrase = apply_pronunciation_overrides(TEXT, profile)
    gen_text = ruaccent_f5(phrase)

    combined: list[float] = []
    for index, ref in enumerate(CANDIDATES, 1):
        entry = refs[ref]
        wav, _, _ = f5.infer(
            ref_file=str(ROOT / entry["audio"]),
            ref_text=entry["text"],
            gen_text=gen_text,
            show_info=lambda *a, **k: None,
            progress=None,
            nfe_step=int(f5_cfg.get("nfe_step", 32)),
            cfg_strength=float(f5_cfg.get("cfg_strength", 2.0)),
            speed=float(f5_cfg.get("speed", 0.9)),
            seed=f5_cfg.get("seed"),
        )
        samples = [float(v) for v in wav]
        write_wav(PREVIEW / f"kandidat_{index}_{ref}.wav", samples)
        combined.extend(beeps(index))
        combined.extend(samples)
        combined.extend([0.0] * int(SR * 0.6))
        print(f"готов кандидат {index}: {ref}", flush=True)

    write_wav(PREVIEW / "bakeoff_all.wav", combined)
    print("склейка: preview/bakeoff_all.wav", flush=True)


if __name__ == "__main__":
    main()
