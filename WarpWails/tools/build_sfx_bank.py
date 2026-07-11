#!/usr/bin/env python3
"""Собирает банк варп-звуков: BBC-скрипы + процедурные шёпоты (+ горлум-нарезка, если есть).

Всё приводится к 24кГц моно s16, нормализуется, прогоняется через WarpImpEffect,
складывается в sfx/<категория>/*.raw и описывается в sfx/manifest.json.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from warp_effect import WarpImpEffect  # noqa: E402

SFX = ROOT / "sfx"
RAW_BBC = SFX / "raw_bbc"
RAW_GOLLUM = SFX / "raw_gollum"
FFMPEG = ROOT / "tools" / "ffmpeg"
SR = 24000
MAX_I16 = 32767.0

TAG_TO_CATEGORY = {
    "creak_wood": "скрип",
    "creak_door": "скрип",
    "creak_metal": "скрип",
    "creak_ice": "скрип",
    "creak_ship": "скрип",
    "rattle": "шорох",
}


def decode(path: Path) -> np.ndarray:
    proc = subprocess.run(
        [str(FFMPEG), "-v", "error", "-i", str(path), "-ar", str(SR), "-ac", "1", "-f", "s16le", "-"],
        capture_output=True,
        check=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float64) / MAX_I16


def trim_silence(x: np.ndarray, threshold: float = 0.02) -> np.ndarray:
    hot = np.flatnonzero(np.abs(x) > threshold)
    if len(hot) == 0:
        return x
    return x[max(0, hot[0] - SR // 50) : min(len(x), hot[-1] + SR // 10)]


def normalize(x: np.ndarray, peak: float = 0.7) -> np.ndarray:
    top = np.max(np.abs(x)) or 1.0
    return x * (peak / top)


def fade(x: np.ndarray, ms: float = 15.0) -> np.ndarray:
    n = min(len(x) // 2, int(SR * ms / 1000))
    if n <= 0:
        return x
    env = np.ones(len(x))
    env[:n] = np.linspace(0.0, 1.0, n)
    env[-n:] = np.linspace(1.0, 0.0, n)
    return x * env


def pitch(x: np.ndarray, semitones: float) -> np.ndarray:
    ratio = 2.0 ** (semitones / 12.0)
    idx = np.arange(0, len(x) - 1, ratio)
    left = idx.astype(np.int64)
    frac = idx - left
    return x[left] * (1.0 - frac) + x[left + 1] * frac


def save(entry_list: list, x: np.ndarray, category: str, name: str, gain: float, effect: WarpImpEffect | None) -> None:
    x = fade(normalize(trim_silence(x)))
    if effect is not None:
        processed = np.asarray(effect.process((x * MAX_I16).astype(np.int64).tolist()), dtype=np.float64) / MAX_I16
        x = normalize(processed)
    dest_dir = SFX / category
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{name}.raw"
    dest.write_bytes((np.clip(x, -1, 1) * MAX_I16).astype("<i2").tobytes())
    entry_list.append({"file": f"{category}/{name}.raw", "category": category, "gain": gain})
    print(f"+ {category}/{name}.raw ({len(x)/SR:.1f}s)")


def whisper_texture(seconds: float, seed: int) -> np.ndarray:
    """Процедурный «шёпот из варпа»: шум сквозь плывущий резонанс."""
    rng = np.random.default_rng(seed)
    n = int(SR * seconds)
    noise = rng.normal(0, 1, n)
    t = np.arange(n) / SR
    # плавающая «формантная» огибающая
    center = 900 + 500 * np.sin(2 * np.pi * (0.35 + 0.1 * (seed % 3)) * t + seed)
    out = np.zeros(n)
    acc1 = acc2 = 0.0
    for i in range(n):
        alpha = min(0.9, center[i] / SR * 6.0)
        acc1 += alpha * (noise[i] - acc1)
        acc2 += alpha * (acc1 - acc2)
        out[i] = acc1 - acc2  # полосовой отклик
    syllables = 0.5 * (1 + np.sign(np.sin(2 * np.pi * (2.1 + 0.4 * (seed % 2)) * t + seed))) \
        * (0.4 + 0.6 * rng.random())
    breath = 0.5 + 0.5 * np.sin(2 * np.pi * 0.4 * t + seed * 2)
    return out * syllables * breath


def main() -> None:
    profile = json.loads((ROOT / "voice_profile.json").read_text(encoding="utf-8"))
    effect = WarpImpEffect(profile, SR)
    sounds: list = []

    # 1. BBC скрипы: прямой вариант + замедленно-пониженный «глубокий»
    if RAW_BBC.exists():
        seen = set()
        for mp3 in sorted(RAW_BBC.glob("*.mp3")):
            sfx_id = mp3.stem.split("_")[-1]
            if sfx_id in seen:
                continue
            seen.add(sfx_id)
            tag = mp3.stem.rsplit("_", 1)[0]
            category = TAG_TO_CATEGORY.get(tag, "скрип")
            x = decode(mp3)
            if len(x) < SR // 2:
                continue
            save(sounds, x, category, f"{tag}_{sfx_id}", 0.9, effect)
            save(sounds, pitch(x, -4.0), category, f"{tag}_{sfx_id}_deep", 0.9, effect)

    # 2. Процедурные шёпоты
    for seed in range(1, 6):
        save(sounds, whisper_texture(1.6 + 0.3 * seed, seed), "шёпот", f"warp_whisper_{seed}", 0.8, effect)

    # 3. Горлум-нарезка (смешки, «голлм-голлм»): питч вверх как у основного голоса
    voice_pitch = float(profile.get("f5", {}).get("pitch_semitones", 0.0))
    if RAW_GOLLUM.exists():
        for wav in sorted(RAW_GOLLUM.glob("*.wav")):
            category = "смешок" if wav.stem.startswith("laugh") else "голлм"
            x = decode(wav)
            if voice_pitch:
                x = pitch(x, voice_pitch)
            save(sounds, x, category, wav.stem, 1.0, effect)

    manifest = {"sample_rate": SR, "sounds": sounds}
    (SFX / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nманифест: {len(sounds)} звуков")


if __name__ == "__main__":
    main()
