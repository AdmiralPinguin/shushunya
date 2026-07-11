#!/usr/bin/env python3
"""Демо эмоций в один wav: каждая фраза называет свою эмоцию. Запускать из WarpWails-F5 venv."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from warpwails_f5 import apply_pronunciation_overrides, ruaccent_f5  # noqa: E402

PREVIEW = ROOT / "preview"
SR = 24000

ALL_PHRASES = [
    ("сарказм", "Это у нас сарказм. Ну надо же, хозяин, какая свежая мысль. Мы восхищены."),
    ("ехидно", "Это у нас ехидно. Кто-то опять уронил сервис, да. А мы всё видели."),
    ("смех", "Это у нас смех. Как же смешно копошатся смертные."),
    ("шепот", "Это у нас шёпот. Тише, хозяин. Варп слушает нас."),
    ("угроза", "Это у нас угроза. Не трогай наши файлы, смертный."),
    ("безумие", "Это у нас безумие. Голоса, голоса, столько голосов, да, прелесть."),
    ("ярость", "Это у нас ярость. Кто посмел трогать наши бэкапы?"),
]
_want = set(sys.argv[1:])
PHRASES = [p for p in ALL_PHRASES if not _want or p[0] in _want]


def main() -> None:
    import soundfile as sf
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

    from warp_sfx import WarpSfxInserter

    sfx = WarpSfxInserter(profile, SR, seed=7)
    combined: list[float] = [0.0] * int(SR * 0.7)  # подушка: синк глотает начало
    for emotion, text in PHRASES:
        entry = refs.get(emotion) or refs["default"]
        gen_text = ruaccent_f5(apply_pronunciation_overrides(text, profile))
        wav, _, _ = f5.infer(
            ref_file=str(ROOT / entry["audio"]),
            ref_text=entry["text"],
            gen_text=gen_text,
            show_info=lambda *a, **k: None,
            progress=None,
            nfe_step=int(f5_cfg.get("nfe_step", 32)),
            cfg_strength=float(f5_cfg.get("cfg_strength", 2.0)),
            speed=float(entry.get("speed") or f5_cfg.get("speed", 0.9)),
            seed=f5_cfg.get("seed"),
        )
        pre = sfx.pre_phrase(emotion)
        if pre:
            combined.extend(s / 32767.0 for s in pre)
        samples = [int(max(-1.0, min(1.0, float(v))) * 32767) for v in wav]
        pitch = float(f5_cfg.get("pitch_semitones", 0.0))
        if pitch:
            from warp_effect import pitch_shift_ffmpeg

            samples = pitch_shift_ffmpeg(samples, SR, pitch)
        combined.extend(s / 32767.0 for s in samples)
        combined.extend([0.0] * int(SR * 0.9))
        print(f"готово: {emotion}", flush=True)

    sf.write(str(PREVIEW / "emotions_demo.wav"), combined, SR)
    print("файл: preview/emotions_demo.wav", flush=True)


if __name__ == "__main__":
    main()
