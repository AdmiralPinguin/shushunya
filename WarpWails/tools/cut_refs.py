#!/usr/bin/env python3
"""Режет референсы по эмоциям из вокальных стемов и прописывает их в voice_profile.json."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = ROOT / "tools" / "ffmpeg"
OUT = ROOT / "refs" / "final"
SR = 24000

# (эмоция, клип, старт, конец, текст как слышится)
CUTS = [
    ("default", "glmwf_yY-_U", 38.6, 46.6, "Хозяин мой друг. У тебя нет друзей. Тебя никто не любит."),
    ("сарказм", "8jyYW4h9Xfo", 38.1, 46.4, "Это ветер. Ну, конечно же. Очень умные хоббитцы. Очень умные."),
    ("ехидно", "BrXJvc3jy-o", 32.7, 39.3, "Он делает толстый, глупый хоббит. Он испортил их."),
    ("холодно", "glmwf_yY-_U", 18.1, 25.5, "Гадкие мелкие хоббитцы. Зло, предательство, фальшь."),
    ("угроза", "sEt-O6Q5EfU", 178.6, 188.2, "Тебя это не касается. Шанс потерян. Потерян, потерян."),
    ("шепот", "vAt_i0fyaZc", 1.0, 11.4, "Такое блестящее, такое красивое, моя прелесть."),
    ("драма", "hqUNn3aYhPo", 0.9, 11.3, "Но пришло ко мне: моя собственность, моя любовь, моя, моя прелесть."),
    ("безумие", "QrqlcEgt5tE", 8.5, 16.9, "К воротам, к воротам, к воротам, сказал хозяин, да. Нет, мы не вернёмся. Только не туда, только не к нему."),
    ("смех", "8jyYW4h9Xfo", 72.0, 76.4, "Сдаёшься? Дай нам ответить, моя прелесть. Дай нам шанс."),
    ("ярость", "sEt-O6Q5EfU", 214.6, 219.4, "Полицию-то! Моя прелесть потеряла!"),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    refs = {}
    for emotion, clip, start, end, text in CUTS:
        vocals = ROOT / f"refs/demucs/htdemucs/{clip}_mono/vocals.wav"
        if not vocals.exists():
            print(f"SKIP {emotion}: нет стема {clip}")
            continue
        dest = OUT / f"{emotion}.wav"
        # -ss/-to ДО -i: input-seek, таймлайн фильтров начинается с нуля вырезки
        subprocess.run(
            [str(FFMPEG), "-v", "error", "-y",
             "-ss", str(start), "-to", str(end), "-i", str(vocals),
             "-ar", str(SR), "-ac", "1",
             "-af", "loudnorm=I=-18:TP=-2,afade=t=in:d=0.04,afade=t=out:st={:.2f}:d=0.08".format(end - start - 0.08),
             str(dest)],
            check=True,
        )
        refs[emotion] = {"audio": f"refs/final/{emotion}.wav", "text": text}
        print(f"+ {emotion}: {end - start:.1f}s  «{text[:50]}»")

    profile_path = ROOT / "voice_profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile.setdefault("f5", {})["refs"] = refs
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nвписано в voice_profile.json: {len(refs)} референсов")


if __name__ == "__main__":
    main()
