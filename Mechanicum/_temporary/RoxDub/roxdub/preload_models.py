from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import snapshot_download


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WHISPER_REPO = "Systran/faster-whisper-large-v3"
DEFAULT_WHISPER_DIR = PROJECT_ROOT / "models" / "faster-whisper-large-v3"


def preload_whisper() -> Path:
    DEFAULT_WHISPER_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=os.getenv("ROXDUB_WHISPER_REPO", DEFAULT_WHISPER_REPO),
        local_dir=str(DEFAULT_WHISPER_DIR),
        token=os.getenv("HF_TOKEN") or None,
    )
    return DEFAULT_WHISPER_DIR


def main() -> int:
    path = preload_whisper()
    print(f"Whisper model ready: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
