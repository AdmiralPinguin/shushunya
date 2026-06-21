from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


SUPPORTED_SOURCE_LANGS = {"auto", "en", "ja"}
DEFAULT_TRANSLATION_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_TRANSLATION_MODEL = "gemma-4-12b-it-UD-Q5_K_XL.gguf"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCAL_WHISPER_MODEL = PROJECT_ROOT / "models" / "faster-whisper-large-v3"
DEFAULT_BS_ROFORMER_MODEL = "roformer-model-bs-roformer-sw-by-jarredou"


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class TranslatedSegment:
    start: float
    end: float
    source_text: str
    translated_text: str
    source_audio_path: str | None = None
    audio_path: str | None = None


@dataclass
class PipelinePaths:
    workdir: Path
    extracted_audio: Path
    separation_audio: Path
    vocals_audio: Path
    transcript_json: Path
    transcript_srt: Path
    translation_txt: Path
    translated_json: Path
    translated_srt: Path
    speech_dir: Path
    source_phrase_dir: Path
    progress_json: Path


def run_command(command: list[str], env: dict[str, str] | None = None) -> None:
    printable = " ".join(command)
    print(f"[run] {printable}")
    try:
        subprocess.run(command, check=True, env=env)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Command failed with exit code {exc.returncode}: {printable}") from exc


def require_command(name: str, install_hint: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Missing dependency: {name}. {install_hint}")


def build_paths(video_path: Path, workdir: Path | None) -> PipelinePaths:
    base = workdir or Path("runs") / video_path.stem
    return PipelinePaths(
        workdir=base,
        extracted_audio=base / "audio" / "extracted.wav",
        separation_audio=base / "audio" / "separation_input.mp3",
        vocals_audio=base / "audio" / "vocals.wav",
        transcript_json=base / "transcript" / "source.json",
        transcript_srt=base / "transcript" / "source.srt",
        translation_txt=base / "translation" / "translated.txt",
        translated_json=base / "translation" / "segments.json",
        translated_srt=base / "translation" / "translated.srt",
        speech_dir=base / "speech",
        source_phrase_dir=base / "phrases" / "source",
        progress_json=base / "progress.json",
    )


def ensure_dirs(paths: PipelinePaths) -> None:
    for path in (
        paths.extracted_audio.parent,
        paths.separation_audio.parent,
        paths.vocals_audio.parent,
        paths.transcript_json.parent,
        paths.translation_txt.parent,
        paths.speech_dir,
        paths.source_phrase_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)


def write_progress(paths: PipelinePaths, stage: str, percent: int, detail: str = "") -> None:
    payload = {
        "stage": stage,
        "percent": max(0, min(100, percent)),
        "detail": detail,
    }
    paths.progress_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[progress] {payload['percent']}% {stage} {detail}", flush=True)


def ffmpeg_executable() -> str:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise RuntimeError("Missing dependency: ffmpeg. Install imageio-ffmpeg or system ffmpeg.") from exc

    return imageio_ffmpeg.get_ffmpeg_exe()


def ffmpeg_path_dir() -> Path:
    ffmpeg_path = Path(ffmpeg_executable())
    if ffmpeg_path.name == "ffmpeg":
        return ffmpeg_path.parent

    local_bin = Path(__file__).resolve().parents[1] / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    local_ffmpeg = local_bin / "ffmpeg"
    if not local_ffmpeg.exists():
        local_ffmpeg.symlink_to(ffmpeg_path)
    return local_bin


def extract_audio(video_path: Path, output_path: Path) -> None:
    run_command(
        [
            ffmpeg_executable(),
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "2",
            "-ar",
            "48000",
            str(output_path),
        ]
    )


def convert_for_separation(input_path: Path, output_path: Path) -> None:
    run_command(
        [
            ffmpeg_executable(),
            "-y",
            "-i",
            str(input_path),
            "-ac",
            "2",
            "-ar",
            "44100",
            "-b:a",
            "192k",
            str(output_path),
        ]
    )


def _load_audio_for_demucs(audio_path: Path, samplerate: int, channels: int):
    try:
        import numpy as np
        import soundfile as sf
        from scipy.signal import resample_poly
    except ImportError as exc:
        raise RuntimeError("Missing Python dependency for direct Demucs audio IO: soundfile and scipy.") from exc

    audio, source_samplerate = sf.read(str(audio_path), always_2d=True, dtype="float32")
    if source_samplerate != samplerate:
        divisor = math.gcd(source_samplerate, samplerate)
        audio = resample_poly(audio, samplerate // divisor, source_samplerate // divisor, axis=0).astype("float32")

    if audio.shape[1] > channels:
        audio = audio[:, :channels]
    elif audio.shape[1] < channels:
        repeats = [audio]
        while sum(item.shape[1] for item in repeats) < channels:
            repeats.append(audio[:, -1:])
        audio = np.concatenate(repeats, axis=1)[:, :channels]

    return np.ascontiguousarray(audio.T)


def _save_audio_without_torchaudio(audio, output_path: Path, samplerate: int) -> None:
    try:
        import numpy as np
        import soundfile as sf
    except ImportError as exc:
        raise RuntimeError("Missing Python dependency for direct Demucs audio IO: soundfile.") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    audio = audio.detach().cpu().numpy().T
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0.99:
        audio = audio / peak * 0.99
    sf.write(str(output_path), audio, samplerate, subtype="PCM_16")


def _separate_vocals_demucs(audio_path: Path, output_path: Path) -> None:
    try:
        import torch
        from demucs.apply import apply_model
        from demucs.pretrained import get_model
    except ImportError as exc:
        raise RuntimeError("Missing Python dependency: demucs and torch.") from exc

    model_name = os.getenv("ROXDUB_DEMUCS_MODEL", "htdemucs")
    model = get_model(model_name)
    model.to("cpu")
    model.eval()

    samplerate = int(model.samplerate)
    channels = int(model.audio_channels)
    mix = torch.from_numpy(_load_audio_for_demucs(audio_path, samplerate, channels))

    with torch.no_grad():
        sources = apply_model(
            model,
            mix.unsqueeze(0),
            shifts=int(os.getenv("ROXDUB_DEMUCS_SHIFTS", "1")),
            split=True,
            overlap=float(os.getenv("ROXDUB_DEMUCS_OVERLAP", "0.25")),
            progress=True,
            device="cpu",
        )[0]

    if "vocals" not in model.sources:
        raise RuntimeError(f"Demucs model {model_name} has no vocals source: {model.sources}")

    vocals = sources[model.sources.index("vocals")]
    _save_audio_without_torchaudio(vocals, output_path, samplerate)


def _separate_vocals_bs_roformer(audio_path: Path, output_path: Path, workdir: Path) -> None:
    try:
        from bs_roformer.download import download_model_assets
        from bs_roformer.inference import proc_folder
        from bs_roformer.model_registry import MODEL_REGISTRY
    except ImportError as exc:
        raise RuntimeError("Missing Python dependency: bs-roformer-infer.") from exc

    model_slug = os.getenv("ROXDUB_BS_ROFORMER_MODEL", DEFAULT_BS_ROFORMER_MODEL)
    model = MODEL_REGISTRY.get(model_slug)
    models_root = PROJECT_ROOT / "models" / "bs-roformer"
    model_dir = models_root / model.slug
    checkpoint_path = model_dir / model.checkpoint
    config_path = model_dir / model.config

    if not checkpoint_path.exists() or not config_path.exists():
        download_ok = download_model_assets([model], models_root)
        if not download_ok:
            raise RuntimeError(f"Could not download BS-RoFormer model: {model_slug}")

    input_dir = workdir / "audio" / "bs_roformer_input"
    output_dir = workdir / "audio" / "bs_roformer_output"
    if input_dir.exists():
        shutil.rmtree(input_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    model_input = input_dir / "mix.wav"
    run_command(
        [
            ffmpeg_executable(),
            "-y",
            "-i",
            str(audio_path),
            "-ac",
            "2",
            "-ar",
            "44100",
            str(model_input),
        ]
    )

    device = os.getenv("ROXDUB_BS_ROFORMER_DEVICE", "cpu")
    proc_folder(
        [
            "--config_path",
            str(config_path),
            "--model_path",
            str(checkpoint_path),
            "--input_folder",
            str(input_dir),
            "--store_dir",
            str(output_dir),
            "--device",
            device,
        ]
    )

    candidates = sorted(output_dir.glob("*_vocals.wav"))
    if not candidates:
        candidates = sorted(output_dir.glob("*.wav"))
    if not candidates:
        raise RuntimeError("BS-RoFormer did not write a vocals wav file.")

    run_command(
        [
            ffmpeg_executable(),
            "-y",
            "-i",
            str(candidates[0]),
            "-ac",
            "2",
            "-ar",
            "48000",
            str(output_path),
        ]
    )


def separate_vocals(audio_path: Path, output_path: Path, workdir: Path) -> str:
    engine = os.getenv("ROXDUB_SEPARATION_ENGINE", "demucs").strip().lower()
    if engine in {"bs_roformer", "bs-roformer", "roformer"}:
        try:
            _separate_vocals_bs_roformer(audio_path, output_path, workdir)
            return "BS-RoFormer"
        except Exception as exc:
            if os.getenv("ROXDUB_SEPARATION_STRICT", "0") == "1":
                raise RuntimeError(f"BS-RoFormer separation failed: {exc}") from exc
            print(f"[separation] BS-RoFormer failed, falling back to Demucs: {exc}", flush=True)

    _separate_vocals_demucs(audio_path, output_path)
    return "Demucs"


def write_transcript_outputs(
    segments: list[Segment],
    output_json: Path,
    output_srt: Path,
    detected_language: str | None = None,
    language_probability: float | None = None,
) -> None:
    payload = {
        "detected_language": detected_language,
        "language_probability": language_probability,
        "segments": [asdict(segment) for segment in segments],
    }
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    output_srt.write_text(to_srt(segments), encoding="utf-8")


def transcribe(audio_path: Path, source_lang: str, output_json: Path, output_srt: Path) -> list[Segment]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Missing Python dependency: faster-whisper. Install requirements.txt first.") from exc

    language = None if source_lang == "auto" else source_lang
    model_size = os.getenv(
        "ROXDUB_WHISPER_MODEL",
        str(DEFAULT_LOCAL_WHISPER_MODEL) if DEFAULT_LOCAL_WHISPER_MODEL.exists() else "medium",
    )
    device = os.getenv("ROXDUB_WHISPER_DEVICE", "cpu")
    compute_type = os.getenv("ROXDUB_WHISPER_COMPUTE_TYPE", "int8")

    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=True,
        beam_size=5,
    )
    segments = [Segment(start=item.start, end=item.end, text=item.text.strip()) for item in segments_iter]
    segments = trim_segments_to_speech(audio_path, segments)
    write_transcript_outputs(segments, output_json, output_srt, info.language, info.language_probability)
    return segments


def _read_audio_for_vad(audio_path: Path, samplerate: int = 16000):
    try:
        import numpy as np
        import soundfile as sf
        import torch
        from scipy.signal import resample_poly
    except ImportError as exc:
        raise RuntimeError("Missing Python dependency for VAD audio IO: numpy, soundfile, scipy, and torch.") from exc

    audio, source_samplerate = sf.read(str(audio_path), always_2d=True, dtype="float32")
    mono = audio.mean(axis=1)
    if source_samplerate != samplerate:
        divisor = math.gcd(source_samplerate, samplerate)
        mono = resample_poly(mono, samplerate // divisor, source_samplerate // divisor).astype("float32")
    return torch.from_numpy(np.ascontiguousarray(mono))


def detect_speech_intervals(audio_path: Path) -> list[tuple[float, float]]:
    try:
        from silero_vad import get_speech_timestamps, load_silero_vad
    except ImportError as exc:
        raise RuntimeError("Missing Python dependency: silero-vad.") from exc

    samplerate = 16000
    wav = _read_audio_for_vad(audio_path, samplerate)
    model = load_silero_vad()
    timestamps = get_speech_timestamps(
        wav,
        model,
        sampling_rate=samplerate,
        threshold=float(os.getenv("ROXDUB_VAD_THRESHOLD", "0.35")),
        min_speech_duration_ms=int(os.getenv("ROXDUB_VAD_MIN_SPEECH_MS", "180")),
        min_silence_duration_ms=int(os.getenv("ROXDUB_VAD_MIN_SILENCE_MS", "120")),
        speech_pad_ms=int(os.getenv("ROXDUB_VAD_PAD_MS", "80")),
        return_seconds=True,
    )
    return [(float(item["start"]), float(item["end"])) for item in timestamps]


def _speech_bounds_for_segment(
    segment: Segment,
    speech_intervals: list[tuple[float, float]],
    tolerance: float,
) -> tuple[float, float] | None:
    overlaps = [
        (start, end)
        for start, end in speech_intervals
        if end >= segment.start and start <= segment.end
    ]
    if not overlaps and tolerance > 0:
        overlaps = [
            (start, end)
            for start, end in speech_intervals
            if end >= segment.start - tolerance and start <= segment.end + tolerance
        ]
    if not overlaps:
        return None
    edge_pad = float(os.getenv("ROXDUB_VAD_EDGE_PAD_SECONDS", "0.08"))
    start = min(max(item[0], segment.start) for item in overlaps)
    end = max(min(item[1], segment.end) for item in overlaps)
    start = max(segment.start, start - edge_pad)
    end = min(segment.end, end + edge_pad)
    if end <= start:
        return None
    return start, end


def trim_segments_to_speech(audio_path: Path, segments: list[Segment]) -> list[Segment]:
    if os.getenv("ROXDUB_VAD_TRIM", "1") == "0" or not segments:
        return segments

    try:
        speech_intervals = detect_speech_intervals(audio_path)
    except RuntimeError as exc:
        print(f"[vad] speech trimming skipped: {exc}", flush=True)
        return segments

    if not speech_intervals:
        print("[vad] no speech intervals detected; keeping Whisper timings", flush=True)
        return segments

    tolerance = float(os.getenv("ROXDUB_VAD_SEGMENT_TOLERANCE", "0.35"))
    min_duration = float(os.getenv("ROXDUB_VAD_MIN_SEGMENT_SECONDS", "0.25"))
    trimmed: list[Segment] = []
    changed = 0
    for segment in segments:
        if not segment.text.strip() or segment.end <= segment.start:
            continue
        bounds = _speech_bounds_for_segment(segment, speech_intervals, tolerance)
        if bounds is None:
            trimmed.append(segment)
            continue
        start, end = bounds
        if end - start < min_duration:
            midpoint = (start + end) / 2
            start = max(0.0, midpoint - min_duration / 2)
            end = midpoint + min_duration / 2
        start = max(0.0, min(start, segment.end))
        end = max(start + 0.01, end)
        if abs(start - segment.start) > 0.05 or abs(end - segment.end) > 0.05:
            changed += 1
        trimmed.append(Segment(start=start, end=end, text=segment.text))

    print(f"[vad] trimmed {changed}/{len(segments)} segment boundaries using {len(speech_intervals)} speech intervals", flush=True)
    return trimmed


def slice_source_phrases(audio_path: Path, segments: list[Segment], output_dir: Path) -> list[Path | None]:
    output_paths: list[Path | None] = []
    for index, segment in enumerate(segments, start=1):
        if not segment.text.strip() or segment.end <= segment.start:
            output_paths.append(None)
            continue
        output_path = output_dir / f"{index:04}.wav"
        print(f"[slice] phrase {index}/{len(segments)}")
        run_command(
            [
                ffmpeg_executable(),
                "-y",
                "-ss",
                f"{max(segment.start, 0):.3f}",
                "-to",
                f"{max(segment.end, segment.start):.3f}",
                "-i",
                str(audio_path),
                "-ac",
                "1",
                "-ar",
                "24000",
                str(output_path),
            ]
        )
        output_paths.append(output_path)
    return output_paths


def translate_one_segment(client, model: str, source_lang: str, target_lang: str, text: str) -> str:
    response = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[
            {
                "role": "system",
                "content": (
                    "You adapt a single subtitle phrase for Russian dubbing. First silently understand intent, "
                    "tone, implied meaning, and idioms. Then write one natural Russian phrase. "
                    "Do not explain, annotate, give alternatives, or add commentary."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Source language: {source_lang}. Target language: {target_lang}.\n"
                    f"Rewrite this phrase naturally for dubbing:\n{text}"
                ),
            },
        ],
    )
    return (response.choices[0].message.content or "").strip()


def translate_segments(
    segments: Iterable[Segment],
    source_lang: str,
    target_lang: str,
    output_path: Path,
    output_json: Path,
    output_srt: Path,
    source_audio_paths: list[Path | None],
    paths: PipelinePaths,
) -> list[TranslatedSegment]:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Missing Python dependency: openai. Install requirements.txt first.") from exc

    source_segments = [segment for segment in segments if segment.text.strip()]
    if not source_segments:
        raise RuntimeError("Transcript is empty, nothing to translate.")

    model = os.getenv("ROXDUB_TRANSLATION_MODEL", DEFAULT_TRANSLATION_MODEL)
    base_url = os.getenv("ROXDUB_TRANSLATION_BASE_URL", DEFAULT_TRANSLATION_BASE_URL)
    client = OpenAI(base_url=base_url, api_key=os.getenv("ROXDUB_TRANSLATION_API_KEY", "local"))

    translated_segments = []
    for index, segment in enumerate(source_segments, start=1):
        write_progress(paths, "Перевод фраз", 60 + int(index / max(len(source_segments), 1) * 20), f"{index}/{len(source_segments)}")
        print(f"[translate] phrase {index}/{len(source_segments)}")
        translated = translate_one_segment(client, model, source_lang, target_lang, segment.text)
        source_audio_path = source_audio_paths[index - 1] if index - 1 < len(source_audio_paths) else None
        translated_segments.append(
            TranslatedSegment(
                start=segment.start,
                end=segment.end,
                source_text=segment.text,
                translated_text=translated,
                source_audio_path=str(source_audio_path) if source_audio_path else None,
            )
        )

    output_path.write_text("\n".join(item.translated_text for item in translated_segments) + "\n", encoding="utf-8")
    output_json.write_text(
        json.dumps([asdict(item) for item in translated_segments], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    output_srt.write_text(
        to_srt([Segment(item.start, item.end, item.translated_text) for item in translated_segments]),
        encoding="utf-8",
    )
    return translated_segments


async def synthesize_one(text: str, output_path: Path, voice: str) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(output_path))


def synthesize_segments(segments: list[TranslatedSegment], speech_dir: Path, output_json: Path, paths: PipelinePaths) -> None:
    try:
        import edge_tts  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("Missing Python dependency: edge-tts. Install requirements.txt first.") from exc

    voice = os.getenv("ROXDUB_TTS_VOICE", "ru-RU-DmitryNeural")
    for index, segment in enumerate(segments, start=1):
        write_progress(paths, "Озвучка фраз", 82 + int(index / max(len(segments), 1) * 15), f"{index}/{len(segments)}")
        if not segment.translated_text.strip():
            continue
        output_path = speech_dir / f"{index:04}.mp3"
        print(f"[tts] phrase {index}/{len(segments)}")
        asyncio.run(synthesize_one(segment.translated_text, output_path, voice))
        segment.audio_path = str(output_path)

    output_json.write_text(
        json.dumps([asdict(item) for item in segments], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def to_srt(segments: Iterable[Segment]) -> str:
    blocks = []
    for index, segment in enumerate(segments, start=1):
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{format_srt_time(segment.start)} --> {format_srt_time(segment.end)}",
                    segment.text,
                ]
            )
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def format_srt_time(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract voice, transcribe it, and translate the transcript.")
    parser.add_argument("video", type=Path, help="Path to the input video file.")
    parser.add_argument("--source-lang", default="auto", choices=sorted(SUPPORTED_SOURCE_LANGS))
    parser.add_argument("--target-lang", default="ru", help="Target translation language.")
    parser.add_argument("--workdir", type=Path, default=None, help="Directory for intermediate and output files.")
    parser.add_argument("--skip-separation", action="store_true", help="Use extracted audio directly for STT.")
    parser.add_argument("--skip-translation", action="store_true", help="Stop after STT.")
    parser.add_argument("--skip-tts", action="store_true", help="Do not synthesize per-phrase Russian audio.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    video_path = args.video.expanduser().resolve()
    if not video_path.exists():
        print(f"Video file does not exist: {video_path}", file=sys.stderr)
        return 2

    paths = build_paths(video_path, args.workdir)
    ensure_dirs(paths)

    try:
        write_progress(paths, "Извлечение аудио", 5)
        extract_audio(video_path, paths.extracted_audio)
        stt_audio = paths.extracted_audio
        if not args.skip_separation:
            write_progress(paths, "Отделение голоса", 20, os.getenv("ROXDUB_SEPARATION_ENGINE", "Demucs CPU"))
            separation_engine = separate_vocals(paths.extracted_audio, paths.vocals_audio, paths.workdir)
            write_progress(paths, "Отделение голоса", 42, separation_engine)
            stt_audio = paths.vocals_audio

        write_progress(paths, "Распознавание речи", 45, "Whisper CPU")
        segments = transcribe(stt_audio, args.source_lang, paths.transcript_json, paths.transcript_srt)
        write_progress(paths, "Нарезка фраз", 58, f"{len(segments)} фраз")
        source_audio_paths = slice_source_phrases(stt_audio, segments, paths.source_phrase_dir)
        if not args.skip_translation:
            translated_segments = translate_segments(
                segments,
                args.source_lang,
                args.target_lang,
                paths.translation_txt,
                paths.translated_json,
                paths.translated_srt,
                source_audio_paths,
                paths,
            )
            if not args.skip_tts:
                synthesize_segments(translated_segments, paths.speech_dir, paths.translated_json, paths)
    except RuntimeError as exc:
        write_progress(paths, "Ошибка", 100, str(exc))
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1

    write_progress(paths, "Готово", 100)
    print(f"Done. Outputs written to: {paths.workdir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
