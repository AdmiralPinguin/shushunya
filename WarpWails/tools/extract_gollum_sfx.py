#!/usr/bin/env python3
"""Ищет невербальные звуки Горлума: голос есть, слов в транскрипте нет.

Берёт вокальные стемы demucs + word-таймстемпы whisper, вырезает регионы
с голосовой энергией вне слов (смешки, «голлм-голлм», шипение) в sfx/raw_gollum/.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = ROOT / "tools" / "ffmpeg"
SR = 24000

# сцены, где почти весь вокал — Горлум (споры с самим собой, загадки)
CLIPS = ["W128iTO1b10", "glmwf_yY-_U", "8jyYW4h9Xfo", "_yjIIRSFU1g", "sEt-O6Q5EfU"]
MIN_S = 0.35
MAX_S = 3.5


def decode(path: Path) -> np.ndarray:
    proc = subprocess.run(
        [str(FFMPEG), "-v", "error", "-i", str(path), "-ar", str(SR), "-ac", "1", "-f", "s16le", "-"],
        capture_output=True,
        check=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float64) / 32767.0


def word_mask(transcript: list[dict], n: int) -> np.ndarray:
    """True там, где по транскрипту кто-то говорит слова (с запасом 120мс)."""
    mask = np.zeros(n, dtype=bool)
    pad = int(SR * 0.12)
    for seg in transcript:
        words = seg.get("words") or [{"s": seg["start"], "e": seg["end"]}]
        for w in words:
            a = max(0, int(w["s"] * SR) - pad)
            b = min(n, int(w["e"] * SR) + pad)
            mask[a:b] = True
    return mask


def main() -> None:
    out_dir = ROOT / "sfx" / "raw_gollum"
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for clip in CLIPS:
        vocals = ROOT / f"refs/demucs/htdemucs/{clip}_mono/vocals.wav"
        transcript_path = ROOT / f"refs/transcripts/{clip}_mono.json"
        if not vocals.exists() or not transcript_path.exists():
            print(f"skip {clip}: нет стема или транскрипта")
            continue
        x = decode(vocals)
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        speaking = word_mask(transcript, len(x))

        # энергия окнами 20мс
        win = SR // 50
        frames = len(x) // win
        rms = np.sqrt(np.mean(x[: frames * win].reshape(frames, win) ** 2, axis=1))
        floor = np.percentile(rms[rms > 1e-4], 20) if np.any(rms > 1e-4) else 0.0
        voiced = rms > max(0.02, floor * 3.0)

        regions = []
        start = None
        for i in range(frames):
            frame_speaking = speaking[i * win : (i + 1) * win].any()
            if voiced[i] and not frame_speaking:
                if start is None:
                    start = i
            else:
                if start is not None:
                    regions.append((start, i))
                    start = None
        if start is not None:
            regions.append((start, frames))

        # склеить регионы через короткие провалы (<200мс)
        merged = []
        for a, b in regions:
            if merged and (a - merged[-1][1]) * win / SR < 0.2:
                merged[-1] = (merged[-1][0], b)
            else:
                merged.append((a, b))

        count = 0
        for a, b in merged:
            seconds = (b - a) * win / SR
            if not (MIN_S <= seconds <= MAX_S):
                continue
            seg = x[a * win : b * win]
            if np.max(np.abs(seg)) < 0.05:
                continue
            t0 = a * win / SR
            dest = out_dir / f"nonverbal_{clip}_{t0:07.2f}.wav"
            pcm = (np.clip(seg, -1, 1) * 32767).astype("<i2").tobytes()
            subprocess.run(
                [str(FFMPEG), "-v", "error", "-y", "-f", "s16le", "-ar", str(SR), "-ac", "1",
                 "-i", "-", str(dest)],
                input=pcm,
                check=True,
            )
            count += 1
        total += count
        print(f"{clip}: {count} невербальных кусков")
    print(f"итого: {total}")


if __name__ == "__main__":
    main()
