#!/usr/bin/env python3
"""Полуслучайный генератор варп-вставок: смешки, скрипы, шёпоты поверх и посреди речи.

Банк звуков лежит в sfx/<категория>/*.raw (s16le mono, sample_rate из манифеста).
Манифест sfx/manifest.json описывает файлы и категории.
Конфиг поведения — блок "sfx" в voice_profile.json.
"""
from __future__ import annotations

import json
import math
import random
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SFX_DIR = ROOT / "sfx"
MANIFEST_PATH = SFX_DIR / "manifest.json"
MAX_I16 = 32767
MIN_I16 = -32768


def _clip(value: float) -> int:
    return max(MIN_I16, min(MAX_I16, int(value)))


def _pcm_to_samples(pcm: bytes) -> list[int]:
    usable = len(pcm) - (len(pcm) % 2)
    return list(struct.unpack(f"<{usable // 2}h", pcm[:usable]))


def _samples_to_pcm(samples: list[int]) -> bytes:
    return struct.pack(f"<{len(samples)}h", *[_clip(s) for s in samples])


def _resample(samples: list[int], src_rate: int, dst_rate: int) -> list[int]:
    if src_rate == dst_rate or not samples:
        return samples
    ratio = src_rate / dst_rate
    length = int(len(samples) / ratio)
    out = []
    for i in range(length):
        pos = i * ratio
        left = int(pos)
        right = min(left + 1, len(samples) - 1)
        frac = pos - left
        out.append(int(samples[left] * (1.0 - frac) + samples[right] * frac))
    return out


def _fade(samples: list[int], rate: int, ms: float = 12.0) -> list[int]:
    n = min(len(samples) // 2, max(1, int(rate * ms / 1000)))
    out = list(samples)
    for i in range(n):
        gain = i / n
        out[i] = int(out[i] * gain)
        out[-1 - i] = int(out[-1 - i] * gain)
    return out


class SfxBank:
    """Загружает банк звуков и отдаёт полуслучайные сэмплы по категориям."""

    def __init__(self, sample_rate: int, rng: random.Random):
        self.sample_rate = sample_rate
        self.rng = rng
        self.by_category: dict[str, list[dict]] = {}
        self._last_file: dict[str, str] = {}
        if not MANIFEST_PATH.exists():
            return
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        src_rate = int(manifest.get("sample_rate", sample_rate))
        for item in manifest.get("sounds", []):
            path = SFX_DIR / item["file"]
            if not path.exists():
                continue
            samples = _pcm_to_samples(path.read_bytes())
            samples = _fade(_resample(samples, src_rate, sample_rate), sample_rate)
            entry = {
                "name": item["file"],
                "samples": samples,
                "gain": float(item.get("gain", 1.0)),
            }
            self.by_category.setdefault(item["category"], []).append(entry)

    @property
    def empty(self) -> bool:
        return not self.by_category

    def categories(self) -> list[str]:
        return sorted(self.by_category)

    def pick(self, category_weights: dict[str, float]) -> dict | None:
        pool = [
            (category, weight)
            for category, weight in category_weights.items()
            if weight > 0 and self.by_category.get(category)
        ]
        if not pool:
            return None
        total = sum(weight for _, weight in pool)
        roll = self.rng.uniform(0.0, total)
        for category, weight in pool:
            roll -= weight
            if roll <= 0:
                break
        variants = self.by_category[category]
        # не повторять один и тот же файл подряд в категории
        last = self._last_file.get(category)
        candidates = [v for v in variants if v["name"] != last] or variants
        chosen = self.rng.choice(candidates)
        self._last_file[category] = chosen["name"]
        return chosen


class WarpSfxInserter:
    """Мешает звуки банка в речь: паузы между фразами, наложения поверх, подложка.

    Работает с целой фразой (list[int] сэмплов) за раз — F5 и так отдаёт фразу целиком.
    """

    def __init__(self, profile: dict, sample_rate: int, seed: int | None = None):
        config = profile.get("sfx", {})
        self.config = config
        self.sample_rate = sample_rate
        self.enabled = bool(config.get("enabled", True))
        self.rng = random.Random(seed if seed is not None else config.get("seed"))
        self.bank = SfxBank(sample_rate, self.rng)
        self.density = float(config.get("density", 0.5))  # 0..1, общая наглость
        self.overlay_gain = float(config.get("overlay_gain", 0.22))
        self.gap_gain = float(config.get("gap_gain", 0.5))
        self.bed_gain = float(config.get("bed_gain", 0.06))
        self.emotion_weights = config.get(
            "emotion_weights",
            {
                "default": {"смешок": 1.0, "скрип": 1.0, "шёпот": 0.7, "голлм": 0.5},
            },
        )
        self._bed_phase = 0

    def _weights_for(self, emotion: str) -> dict[str, float]:
        weights = self.emotion_weights.get(emotion) or self.emotion_weights.get("default", {})
        return {k: float(v) for k, v in weights.items()}

    def _chance(self, probability: float) -> bool:
        return self.rng.random() < probability * self.density

    def _mix_at(self, base: list[int], sfx: dict, offset: int, gain: float) -> None:
        samples = sfx["samples"]
        total_gain = gain * sfx["gain"]
        room = len(base) - offset
        fit = min(len(samples), room)
        if fit <= 0:
            return
        fade_len = min(fit // 2, int(self.sample_rate * 0.03)) if fit < len(samples) else 0
        for i in range(fit):
            value = samples[i] * total_gain
            if fade_len and i >= fit - fade_len:
                value *= (fit - i) / fade_len
            base[offset + i] = _clip(base[offset + i] + value)

    def _find_pauses(self, samples: list[int], min_ms: float = 220.0) -> list[tuple[int, int]]:
        """Ищет тихие участки внутри фразы, куда можно вставить звук."""
        window = max(1, self.sample_rate // 100)  # 10мс
        threshold = 600
        min_len = int(self.sample_rate * min_ms / 1000)
        pauses = []
        start = None
        for i in range(0, len(samples) - window, window):
            level = max(abs(s) for s in samples[i : i + window])
            if level < threshold:
                if start is None:
                    start = i
            else:
                if start is not None and i - start >= min_len:
                    pauses.append((start, i))
                start = None
        if start is not None and len(samples) - start >= min_len:
            pauses.append((start, len(samples)))
        return pauses

    def _bed_chunk(self, length: int) -> list[int]:
        """Тихая варп-подложка: медленно дышащий детюненный гул."""
        out = []
        rate = self.sample_rate
        for k in range(length):
            t = (self._bed_phase + k) / rate
            breath = 0.6 + 0.4 * math.sin(2 * math.pi * 0.11 * t)
            value = (
                math.sin(2 * math.pi * 52.0 * t)
                + 0.7 * math.sin(2 * math.pi * 52.9 * t + 1.3)
                + 0.5 * math.sin(2 * math.pi * 104.7 * t + 0.4)
            )
            out.append(int(value * breath * self.bed_gain * MAX_I16 / 3))
        self._bed_phase += length
        return out

    def process_phrase(self, samples: list[int], emotion: str) -> list[int]:
        """Наложения посреди/поверх одной фразы + подложка."""
        if not self.enabled or self.bank.empty or not samples:
            return samples
        out = list(samples)
        weights = self._weights_for(emotion)

        # 1. вставки в естественные паузы внутри фразы
        for start, end in self._find_pauses(out):
            if not self._chance(float(self.config.get("pause_prob", 0.45))):
                continue
            sfx = self.bank.pick(weights)
            if sfx is None:
                continue
            room = end - start
            offset = start + self.rng.randint(0, max(0, room // 3))
            self._mix_at(out, sfx, offset, self.gap_gain)

        # 2. наложение поверх речи (тихо, из-за спины)
        if self._chance(float(self.config.get("overlay_prob", 0.35))):
            sfx = self.bank.pick(weights)
            if sfx is not None and len(out) > len(sfx["samples"]):
                offset = self.rng.randint(0, len(out) - len(sfx["samples"]))
                self._mix_at(out, sfx, offset, self.overlay_gain)

        # 3. подложка под всей фразой
        if self.bed_gain > 0:
            bed = self._bed_chunk(len(out))
            for i in range(len(out)):
                out[i] = _clip(out[i] + bed[i])
        return out

    def between_phrases(self, emotion: str) -> list[int]:
        """Звук в паузе между фразами: смешок/скрип + кусочек подложки."""
        if not self.enabled or self.bank.empty:
            return []
        gap_ms = self.rng.uniform(*self.config.get("gap_range_ms", [180, 650]))
        gap = self._bed_chunk(int(self.sample_rate * gap_ms / 1000))
        if self._chance(float(self.config.get("between_prob", 0.6))):
            sfx = self.bank.pick(self._weights_for(emotion))
            if sfx is not None:
                pad = [0] * len(gap)
                base = pad if len(gap) >= len(sfx["samples"]) else [0] * len(sfx["samples"])
                for i in range(min(len(gap), len(base))):
                    base[i] = gap[i]
                self._mix_at(base, sfx, 0, self.gap_gain)
                return base
        return gap

    def pre_phrase(self, emotion: str) -> list[int]:
        """Обязательная вставка перед фразой для эмоций из sfx.pre_phrase (напр. [смех] → живой хихик)."""
        category = self.config.get("pre_phrase", {}).get(emotion)
        if not category or self.bank.empty or not self.bank.by_category.get(category):
            return []
        sfx = self.bank.pick({category: 1.0})
        if sfx is None:
            return []
        out = [0] * (len(sfx["samples"]) + self.sample_rate // 6)
        self._mix_at(out, sfx, 0, self.gap_gain)
        return out

    def opener(self, emotion: str) -> list[int]:
        """Мгновенная заглушка на старт ответа: смешок/скрип, пока считается первая фраза."""
        if self.bank.empty:
            return []
        sfx = self.bank.pick(self._weights_for(emotion))
        if sfx is None:
            return []
        out = [0] * (len(sfx["samples"]) + self.sample_rate // 5)
        self._mix_at(out, sfx, self.sample_rate // 10, self.gap_gain)
        return out

    def tail(self, emotion: str) -> list[int]:
        """Хвост после последней фразы: затухающий скрип/смешок вслед."""
        if not self.enabled or self.bank.empty:
            return []
        if not self._chance(float(self.config.get("tail_prob", 0.5))):
            return []
        sfx = self.bank.pick(self._weights_for(emotion))
        if sfx is None:
            return []
        out = [0] * (len(sfx["samples"]) + self.sample_rate // 4)
        self._mix_at(out, sfx, 0, self.gap_gain)
        bed = self._bed_chunk(len(out))
        fade_span = max(1, len(out))
        for i in range(len(out)):
            fade = 1.0 - i / fade_span
            out[i] = _clip((out[i] + bed[i]) * fade)
        return out
