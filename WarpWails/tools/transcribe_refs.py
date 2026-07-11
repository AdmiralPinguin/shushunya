#!/usr/bin/env python3
"""Транскрибация клипов-референсов faster-whisper'ом с таймстемпами сегментов."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from faster_whisper import WhisperModel

ROOT = Path(__file__).resolve().parent.parent
REFS = ROOT / "refs"
OUT = REFS / "transcripts"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    model = WhisperModel("medium", device="cpu", compute_type="int8", cpu_threads=16)
    files = sorted(REFS.glob("*_mono.wav"))
    for wav in files:
        out_path = OUT / (wav.stem + ".json")
        if out_path.exists():
            continue
        segments, info = model.transcribe(
            str(wav), language="ru", vad_filter=True, word_timestamps=True
        )
        data = []
        for seg in segments:
            data.append(
                {
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "text": seg.text.strip(),
                    "words": [
                        {"w": w.word, "s": round(w.start, 2), "e": round(w.end, 2)}
                        for w in (seg.words or [])
                    ],
                }
            )
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"done {wav.name}: {len(data)} segments", flush=True)


if __name__ == "__main__":
    main()
