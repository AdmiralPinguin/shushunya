#!/usr/bin/env python3
"""Собирает кандидатов доп-звуков: качает YouTube-паки, режет по тишине на отдельные звуки.

Результат: sfx/candidates/NNN_<источник>.wav (24кГц моно, нормализовано) + candidates.json.
Запускать из WarpWails-Tools venv.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
FF = ROOT / "tools" / "ffmpeg-7.0.2-amd64-static" / "ffmpeg"
RAW = ROOT / "sfx" / "candidates_raw"
OUT = ROOT / "sfx" / "candidates"
SR = 24000
MIN_S, MAX_S = 0.35, 4.5
MAX_PER_SOURCE = 6

SOURCES = {
    "rwiapxMHF4I": "дьявол1", "X1Kh0yzT1CM": "зловещий", "Pg_Sd8yNTFA": "дьявол2",
    "lB0I3pZde4U": "дьявол3", "WjEvNbEGCJA": "имп", "b7Or4hfVSvA": "гоблины1",
    "PwQICdlTOLQ": "гоблин2", "zUG4ucSUnfU": "гоблин3", "KkJ_lT5MdFc": "смех1",
    "NEWQc32aumI": "смех2", "KDy_NTqphlg": "жуткий", "7JOEKMiHJn0": "шёпот4голоса",
    "mBtP1B657Ro": "демошёпот", "fpalvmhf180": "ведьма",
}


def decode(path: Path) -> np.ndarray:
    r = subprocess.run([str(FF), "-v", "error", "-i", str(path), "-ar", str(SR), "-ac", "1", "-f", "s16le", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float64) / 32767.0


def slice_by_silence(x: np.ndarray) -> list[np.ndarray]:
    win = SR // 50
    frames = len(x) // win
    if frames == 0:
        return []
    rms = np.sqrt(np.mean(x[: frames * win].reshape(frames, win) ** 2, axis=1))
    thr = max(0.015, float(np.percentile(rms, 55)) * 0.25)
    voiced = rms > thr
    chunks, start = [], None
    quiet = 0
    for i, v in enumerate(voiced):
        if v:
            if start is None:
                start = i
            quiet = 0
        elif start is not None:
            quiet += 1
            if quiet >= 12:  # 240мс тишины = граница звука
                chunks.append((start, i - quiet + 2))
                start, quiet = None, 0
    if start is not None:
        chunks.append((start, frames))
    out = []
    for a, b in chunks:
        seg = x[max(0, (a - 2) * win): min(len(x), (b + 2) * win)]
        if MIN_S <= len(seg) / SR <= MAX_S and np.max(np.abs(seg)) > 0.04:
            out.append(seg)
    return out


def save(seg: np.ndarray, path: Path) -> None:
    seg = seg * (0.7 / (np.max(np.abs(seg)) or 1.0))
    n = min(len(seg) // 2, SR * 15 // 1000)
    env = np.ones(len(seg))
    env[:n] = np.linspace(0, 1, n)
    env[-n:] = np.linspace(1, 0, n)
    pcm = (np.clip(seg * env, -1, 1) * 32767).astype("<i2").tobytes()
    subprocess.run([str(FF), "-v", "error", "-y", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "-", str(path)],
                   input=pcm, check=True)


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    idx = 0
    for vid, tag in SOURCES.items():
        wav = RAW / f"{vid}.wav"
        if not wav.exists():
            r = subprocess.run(
                ["yt-dlp", "-f", "bestaudio", "-x", "--audio-format", "wav",
                 "--ffmpeg-location", str(FF.parent), "-o", str(RAW / f"{vid}.%(ext)s"),
                 f"https://www.youtube.com/watch?v={vid}"],
                capture_output=True, text=True)
            if r.returncode != 0 or not wav.exists():
                print(f"FAIL {tag} ({vid})", flush=True)
                continue
        x = decode(wav)
        segs = slice_by_silence(x)[:MAX_PER_SOURCE]
        if not segs:
            # сплошной звук без пауз — берём целиком, обрезав тишину по краям, максимум 8с
            hot = np.flatnonzero(np.abs(x) > 0.03)
            if len(hot):
                seg = x[hot[0]: hot[-1]]
                segs = [seg[: SR * 8]]
        for seg in segs:
            idx += 1
            name = f"{idx:03d}_{tag}.wav"
            save(seg, OUT / name)
            manifest.append({"n": idx, "file": name, "source": tag, "sec": round(len(seg) / SR, 2)})
        print(f"{tag}: {len(segs)} звуков", flush=True)
    (OUT / "candidates.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"итого кандидатов: {idx}", flush=True)


if __name__ == "__main__":
    main()
