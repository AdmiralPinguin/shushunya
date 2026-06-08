#!/usr/bin/env python3
import os
from pathlib import Path

from huggingface_hub import snapshot_download


ROOT = Path(__file__).resolve().parent
MODEL_ID = "stabilityai/stable-diffusion-3.5-large"
MODEL_DIR = ROOT / "models" / "stable-diffusion-3.5-large"
HF_HOME = ROOT / "hf_home"


def main() -> None:
    os.environ.setdefault("HF_HOME", str(HF_HOME))
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=MODEL_ID,
        local_dir=MODEL_DIR,
    )
    print(f"Model downloaded to {MODEL_DIR}")


if __name__ == "__main__":
    main()
