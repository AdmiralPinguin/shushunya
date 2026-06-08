#!/usr/bin/env python3
import os
from pathlib import Path

from huggingface_hub import snapshot_download


ROOT = Path(__file__).resolve().parent
MODEL_ID = "stabilityai/stable-diffusion-xl-base-1.0"
MODEL_DIR = ROOT / "models" / "stable-diffusion-xl-base-1.0"
HF_HOME = ROOT / "hf_home"


def main() -> None:
    os.environ.setdefault("HF_HOME", str(HF_HOME))
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=MODEL_ID,
        local_dir=MODEL_DIR,
        ignore_patterns=["*.onnx", "*.pb", "*.msgpack", "*.ckpt", "*.bin"],
    )
    print(f"Model downloaded to {MODEL_DIR}")


if __name__ == "__main__":
    main()
