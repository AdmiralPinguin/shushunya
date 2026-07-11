#!/usr/bin/env python3
"""Лесенка темпа речи на базовом референсе: одна фраза на разных speed. WarpWails-F5 venv."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from warpwails_f5 import apply_pronunciation_overrides, ruaccent_f5  # noqa: E402

PREVIEW = ROOT / "preview"
SR = 24000
SPEEDS = [1.0, 1.15, 1.3, 1.45]
TEXT = "Скорость {}. Ну конечно, хозяин. Смертные вечно всё ломают, а чинить нам."


def main() -> None:
    import soundfile as sf
    import torch
    from f5_tts.api import F5TTS
    from huggingface_hub import hf_hub_download

    profile = json.loads((ROOT / "voice_profile.json").read_text(encoding="utf-8"))
    f5_cfg = profile.get("f5", {})
    entry = f5_cfg["refs"]["default"]

    PREVIEW.mkdir(exist_ok=True)
    ckpt = hf_hub_download("Misha24-10/F5-TTS_RUSSIAN", "F5TTS_v1_Base_accent_tune/model_last_inference.safetensors")
    vocab = hf_hub_download("Misha24-10/F5-TTS_RUSSIAN", "F5TTS_v1_Base/vocab.txt")
    torch.set_num_threads(max(1, min(16, torch.get_num_threads())))
    f5 = F5TTS(model="F5TTS_v1_Base", ckpt_file=ckpt, vocab_file=vocab, device="cpu")

    names = {1.0: "один", 1.15: "один пятнадцать", 1.3: "один тридцать", 1.45: "один сорок пять"}
    combined: list[float] = []
    for speed in SPEEDS:
        text = TEXT.format(names[speed])
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
        combined.extend(float(v) for v in wav)
        combined.extend([0.0] * int(SR * 0.8))
        print(f"готово: speed={speed}", flush=True)

    sf.write(str(PREVIEW / "speed_demo.wav"), combined, SR)
    print("файл: preview/speed_demo.wav", flush=True)


if __name__ == "__main__":
    main()
