#!/usr/bin/env python3
"""WarpWails-демон: модель в памяти, HTTP-ручка /speak, голос сразу в колонки.

Запуск: WarpWails-F5/bin/python warpwails_service.py
Ручки:
  POST /speak  {"text": "[сарказм] Ну надо же.", "opener": true}  -> 202, ставит в очередь
  GET  /health -> статус, длина очереди
  GET  /emotions -> список эмоций-рефов

Порядок на реплику: мгновенная заглушка (смешок/скрип из банка) -> фразы конвейером
(первая играет, следующие считаются). Никаких выходных файлов.
"""
from __future__ import annotations

import json
import queue
import struct
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from warpwails_f5 import (  # noqa: E402
    MAX_I16,
    MIN_I16,
    apply_pronunciation_overrides,
    detect_player,
    parse_lines,
    pick_ref,
    ruaccent_f5,
)

SR = 24000


class Speaker:
    """Одна очередь речи: синтез конвейером, вывод в плеер по мере готовности."""

    def __init__(self) -> None:
        self.profile = json.loads((ROOT / "voice_profile.json").read_text(encoding="utf-8"))
        self.f5_cfg = self.profile.get("f5", {})
        self.service_cfg = self.profile.get("service", {})
        self.jobs: queue.Queue = queue.Queue()
        self.busy = threading.Event()

        import torch
        from f5_tts.api import F5TTS
        from huggingface_hub import hf_hub_download

        from warp_effect import WarpImpEffect
        from warp_sfx import WarpSfxInserter

        device = self.f5_cfg.get("device", "cpu")
        ckpt = hf_hub_download("Misha24-10/F5-TTS_RUSSIAN", "F5TTS_v1_Base_accent_tune/model_last_inference.safetensors")
        vocab = hf_hub_download("Misha24-10/F5-TTS_RUSSIAN", "F5TTS_v1_Base/vocab.txt")
        if device == "cpu":
            torch.set_num_threads(max(1, min(16, torch.get_num_threads())))
        self.f5 = F5TTS(model="F5TTS_v1_Base", ckpt_file=ckpt, vocab_file=vocab, device=device)
        self.effect = WarpImpEffect(self.profile, SR)
        self.sfx = WarpSfxInserter(self.profile, SR)
        threading.Thread(target=self._worker, daemon=True).start()

    def enqueue(self, text: str, opener: bool = True) -> int:
        self.jobs.put((text, opener))
        return self.jobs.qsize()

    def _synth_phrase(self, emotion: str, phrase: str) -> list[int]:
        prepared = apply_pronunciation_overrides(phrase, self.profile)
        gen_text = ruaccent_f5(prepared)
        ref_audio, ref_text, speed_override = pick_ref(self.profile, emotion)
        wav, _, _ = self.f5.infer(
            ref_file=ref_audio,
            ref_text=ref_text,
            gen_text=gen_text,
            show_info=lambda *a, **k: None,
            progress=None,
            nfe_step=int(self.f5_cfg.get("nfe_step", 32)),
            cfg_strength=float(self.f5_cfg.get("cfg_strength", 2.0)),
            speed=speed_override or float(self.f5_cfg.get("speed", 1.45)),
            seed=self.f5_cfg.get("seed"),
        )
        samples = [int(max(-1.0, min(1.0, float(v))) * MAX_I16) for v in wav]
        pitch = float(self.f5_cfg.get("pitch_semitones", 0.0))
        if pitch:
            from warp_effect import pitch_shift_ffmpeg

            samples = pitch_shift_ffmpeg(samples, SR, pitch)
        return self.effect.process(samples)

    def _speak(self, text: str, opener: bool) -> None:
        lines = parse_lines(text, self.profile)
        if not lines:
            return
        _, command, problem = detect_player(SR)
        if not command:
            print(f"[warpwails] нет аудиовывода: {problem}", flush=True)
            return
        with subprocess.Popen(command, stdin=subprocess.PIPE) as player:
            assert player.stdin is not None

            def push(samples: list[int]) -> None:
                if samples:
                    pcm = struct.pack(f"<{len(samples)}h", *[max(MIN_I16, min(MAX_I16, s)) for s in samples])
                    player.stdin.write(pcm)
                    player.stdin.flush()

            # подушки тишины: PipeWire-синк просыпается не мгновенно и глотает начало/хвост
            pad = self.service_cfg.get("pad_ms", {"start": 450, "end": 700})
            push([0] * (SR * int(pad.get("start", 450)) // 1000))
            if opener and self.service_cfg.get("opener", True):
                push(self.sfx.opener(lines[0][0]))
            for index, (emotion, phrase) in enumerate(lines):
                samples = self._synth_phrase(emotion, phrase)
                if index > 0:
                    push(self.sfx.between_phrases(emotion))
                push(self.sfx.pre_phrase(emotion))
                push(self.sfx.process_phrase(samples, emotion))
            push(self.sfx.tail(lines[-1][0]))
            push([0] * (SR * int(pad.get("end", 700)) // 1000))
            player.stdin.close()
            player.wait()

    def _worker(self) -> None:
        while True:
            text, opener = self.jobs.get()
            self.busy.set()
            try:
                self._speak(text, opener)
            except Exception as exc:
                print(f"[warpwails] ошибка речи: {exc}", flush=True)
            finally:
                self.busy.clear()


class Handler(BaseHTTPRequestHandler):
    speaker: Speaker

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {
                "status": "ok",
                "busy": self.speaker.busy.is_set(),
                "queue": self.speaker.jobs.qsize(),
                "device": self.speaker.f5_cfg.get("device", "cpu"),
            })
        elif self.path == "/emotions":
            self._json(200, {"emotions": sorted(self.speaker.f5_cfg.get("refs", {}))})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/speak":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            text = str(payload["text"]).strip()
            if not text:
                raise ValueError("пустой text")
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
            return
        position = self.speaker.enqueue(text, bool(payload.get("opener", True)))
        self._json(202, {"queued": position})

    def log_message(self, fmt: str, *args) -> None:
        print(f"[warpwails] {self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    profile = json.loads((ROOT / "voice_profile.json").read_text(encoding="utf-8"))
    port = int(profile.get("service", {}).get("port", 7500))
    print("[warpwails] грузим модель...", flush=True)
    Handler.speaker = Speaker()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[warpwails] готов на 127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
