#!/usr/bin/env python3
"""Варп-эффект «мелкого ушлого»: эхо уезжает питчем вверх, лёгкий ring-mod, сатурация.

В отличие от старого WarpEffectProcessor тут нет октавы вниз (она давала «Диабло»).
Обрабатывает фразу целиком через numpy — быстро на CPU.
Конфиг — блок "warp_imp" в voice_profile.json.
"""
from __future__ import annotations

import numpy as np

MAX_I16 = 32767.0


def _pitch_shift(x: np.ndarray, semitones: float) -> np.ndarray:
    """Простой ресемплинг: питч вверх, длительность короче — для эха это то, что нужно."""
    if abs(semitones) < 0.01 or len(x) < 4:
        return x
    ratio = 2.0 ** (semitones / 12.0)
    idx = np.arange(0, len(x) - 1, ratio)
    left = idx.astype(np.int64)
    frac = idx - left
    return x[left] * (1.0 - frac) + x[left + 1] * frac


def _lowpass(x: np.ndarray, alpha: float) -> np.ndarray:
    """Однополюсный НЧ-фильтр, чтобы эхо было «шепчущим», а не звонким."""
    try:
        from scipy.signal import lfilter

        return lfilter([alpha], [1.0, -(1.0 - alpha)], x)
    except ImportError:
        out = np.empty_like(x)
        acc = 0.0
        for i in range(len(x)):
            acc += alpha * (x[i] - acc)
            out[i] = acc
        return out


def pitch_shift_ffmpeg(samples: list[int], sample_rate: int, semitones: float) -> list[int]:
    """Сдвиг питча вместе с формантами (asetrate+atempo): «мелкая тварь», длительность сохраняется."""
    import subprocess
    from pathlib import Path

    if abs(semitones) < 0.01 or not samples:
        return samples
    ffmpeg = Path(__file__).resolve().parent / "tools" / "ffmpeg-7.0.2-amd64-static" / "ffmpeg"
    factor = 2.0 ** (semitones / 12.0)
    tempo = 1.0 / factor
    filters = [f"asetrate={int(sample_rate * factor)}", f"aresample={sample_rate}"]
    while tempo < 0.5:
        filters.append("atempo=0.5")
        tempo /= 0.5
    while tempo > 2.0:
        filters.append("atempo=2.0")
        tempo /= 2.0
    filters.append(f"atempo={tempo}")
    pcm = np.asarray(samples, dtype=np.int16).tobytes()
    proc = subprocess.run(
        [str(ffmpeg), "-v", "error", "-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", "-",
         "-af", ",".join(filters), "-f", "s16le", "-"],
        input=pcm,
        capture_output=True,
        check=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.int16).tolist()


class WarpImpEffect:
    def __init__(self, profile: dict, sample_rate: int):
        cfg = profile.get("warp_imp", {})
        self.sample_rate = sample_rate
        self.drive = float(cfg.get("drive", 1.15))
        self.wet = float(cfg.get("wet", 0.3))
        self.ring_hz = float(cfg.get("ring_hz", 31.0))
        self.ring_depth = float(cfg.get("ring_depth", 0.12))
        self.shimmer_hz = float(cfg.get("shimmer_hz", 6.3))
        self.shimmer_depth = float(cfg.get("shimmer_depth", 0.03))
        # эхо: каждый повтор чуть выше питчем и глуше — голос «растворяется в варпе»
        self.echo_taps = cfg.get(
            "echo_taps",
            [
                {"delay_ms": 110, "decay": 0.22, "pitch": 2.0, "lp": 0.35},
                {"delay_ms": 260, "decay": 0.13, "pitch": 4.0, "lp": 0.22},
                {"delay_ms": 430, "decay": 0.07, "pitch": 7.0, "lp": 0.15},
            ],
        )

    def process(self, samples: list[int] | np.ndarray) -> list[int]:
        x = np.asarray(samples, dtype=np.float64) / MAX_I16
        if len(x) == 0:
            return []
        n = len(x)
        t = np.arange(n) / self.sample_rate

        # лёгкая нечеловечность: тремоло + едва слышный ring-mod
        shimmer = 1.0 + self.shimmer_depth * np.sin(2 * np.pi * self.shimmer_hz * t)
        ring = 1.0 - self.ring_depth * 0.5 * (1.0 - np.cos(2 * np.pi * self.ring_hz * t))
        voiced = np.tanh(x * shimmer * ring * self.drive)

        # хвост под эхо
        max_extra = int(self.sample_rate * (max(tap["delay_ms"] for tap in self.echo_taps) / 1000.0 + 0.35)) if self.echo_taps else 0
        out = np.zeros(n + max_extra)
        out[:n] += x * (1.0 - self.wet) + voiced * self.wet

        for tap in self.echo_taps:
            delay = int(self.sample_rate * float(tap["delay_ms"]) / 1000.0)
            echo = _pitch_shift(voiced, float(tap.get("pitch", 0.0)))
            lp = float(tap.get("lp", 0.3))
            if lp < 1.0:
                echo = _lowpass(echo, lp)
            end = min(delay + len(echo), len(out))
            out[delay:end] += echo[: end - delay] * float(tap["decay"]) * self.wet

        np.clip(out, -1.0, 1.0, out=out)
        return (out * MAX_I16).astype(np.int16).tolist()
