# RoxDub

Pipeline for preparing translated voice tracks from video:

1. Extract audio from a video file.
2. Separate vocals from background audio and effects.
3. Run speech-to-text on the isolated voice.
4. Translate the transcript with an LLM-backed translator.

## Current Shape

This repository contains the orchestration code. The heavy tools are external:

- `ffmpeg` for audio extraction.
- `bs-roformer-infer` for high quality vocal separation.
- `demucs` as a fallback vocal separator.
- `faster-whisper` for speech-to-text.
- `silero-vad` for trimming phrase clips to actual speech.
- local Gemma through the OpenAI-compatible `llama-server` endpoint for translation.

## Setup

The virtual environment already exists at:

```bash
/media/shushunya/SHUSHUNYA/shushunya/LegacyMechanicum/RoxDub/RoxDub
```

`pip` has been bootstrapped into this environment. The Python dependencies are installed.
If the environment is recreated, install them with:

```bash
source /media/shushunya/SHUSHUNYA/shushunya/LegacyMechanicum/RoxDub/RoxDub/bin/activate
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install -r /media/shushunya/SHUSHUNYA/shushunya/LegacyMechanicum/RoxDub/requirements.txt
```

The pipeline can use either system `ffmpeg` or the bundled binary from `imageio-ffmpeg`.
No external OpenAI key is required by default. Translation uses the local Gemma server:

```bash
http://127.0.0.1:8080/v1
```

Preload Whisper before running the pipeline:

```bash
source /media/shushunya/SHUSHUNYA/shushunya/LegacyMechanicum/RoxDub/RoxDub/bin/activate
python -m roxdub.preload_models
```

The default local model path is:

```bash
/media/shushunya/SHUSHUNYA/shushunya/LegacyMechanicum/RoxDub/models/faster-whisper-large-v3
```

If a Hugging Face token is available, put it in `/media/shushunya/SHUSHUNYA/shushunya/LegacyMechanicum/RoxDub/.env`:

```bash
HF_TOKEN=hf_...
```

## Usage

```bash
source /media/shushunya/SHUSHUNYA/shushunya/LegacyMechanicum/RoxDub/RoxDub/bin/activate
python -m roxdub.pipeline /path/to/video.mkv --source-lang auto --target-lang ru
```

Useful options:

```bash
python -m roxdub.pipeline video.mp4 --source-lang en --target-lang ru
python -m roxdub.pipeline video.mp4 --source-lang ja --target-lang ru --workdir ./runs/episode-01
python -m roxdub.pipeline video.mp4 --skip-separation
```

Outputs are written into the work directory:

- `audio/extracted.wav`
- `audio/vocals.wav`
- `phrases/source/0001.wav`, `0002.wav`, ...
- `transcript/source.json`
- `transcript/source.srt`
- `translation/translated.txt`
- `translation/segments.json`
- `translation/translated.srt`
- `speech/0001.mp3`, `0002.mp3`, ...

## Notes

- `--source-lang auto` lets Whisper detect English or Japanese.
- `--skip-separation` is useful for quick testing or videos where the voice is already clean.
- Vocal separation uses the fine-tuned Demucs model `htdemucs_ft` by default.
- CPU mode is used by default.
- Set `ROXDUB_DEMUCS_MODEL=htdemucs`, `htdemucs_ft`, or `htdemucs_6s` to choose another local Demucs model.
- Set `ROXDUB_DEMUCS_SHIFTS=1` for speed or `ROXDUB_DEMUCS_SHIFTS=4` for higher quality.
- Set `ROXDUB_SEPARATION_ENGINE=bs_roformer` to try BS-RoFormer.
- Set `ROXDUB_BS_ROFORMER_MODEL=roformer-model-bs-roformer-sw-by-jarredou` to choose the BS-RoFormer model.
- Set `ROXDUB_VAD_TRIM=0` to disable speech-boundary trimming.
- The translation endpoint can be changed with `ROXDUB_TRANSLATION_BASE_URL`.
- The translation model can be changed with `ROXDUB_TRANSLATION_MODEL`.
- After vocal separation and STT, RoxDub tightens phrase boundaries with VAD and slices the isolated source voice into shorter phrase clips.
- Each phrase is translated separately and receives a separate Russian TTS file.

## Android Controller

The Android app is named `RoxDub`.

APK:

```bash
/media/shushunya/SHUSHUNYA/shushunya/LegacyMechanicum/RoxDub/RoxDub-debug.apk
```

Current temporary internet endpoint:

```bash
https://jackson-geneva-mistakes-neighbors.trycloudflare.com
```

The debug APK already contains this URL and the current access token. The tunnel is temporary:
if `cloudflared` is stopped or restarted, Cloudflare will issue a new URL and the APK must be
rebuilt or the new URL must be entered in the app manually.

Videos shown in the Android app come from:

```bash
/media/shushunya/SHUSHUNYA/shushunya/LegacyMechanicum/RoxDub/videos
```

The app uses tabs:

- `Комп` - videos on the workstation, shown as tiles with previews.
- `Телефон` - videos from the phone media library, also shown as tiles.
- `Фразы` - phrase list after processing, with playback buttons for source and Russian audio.
- `Статус` - current job state and log.
- `Связь` - endpoint and token settings.

The `Статус` tab can start foreground monitoring. Android will show a persistent RoxDub
notification with stage, percent, and a progress bar while the app is in the background.

Current test video:

```bash
/media/shushunya/SHUSHUNYA/shushunya/LegacyMechanicum/RoxDub/videos/Construction Cancellation.mkv
```

Manual server start:

```bash
source /media/shushunya/SHUSHUNYA/shushunya/LegacyMechanicum/RoxDub/RoxDub/bin/activate
python -m roxdub.server
```

Manual tunnel start:

```bash
/media/shushunya/SHUSHUNYA/shushunya/LegacyMechanicum/RoxDub/cloudflared tunnel --url http://127.0.0.1:8765
```
