from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import config
from .schemas import AssetDownloadSpec


APPROVED_HOSTS = {
    "huggingface.co",
    "civitai.com",
    "github.com",
    "raw.githubusercontent.com",
}


class DownloadError(RuntimeError):
    pass


def target_dir_for(spec: AssetDownloadSpec) -> Path:
    if spec.target_dir:
        target = Path(spec.target_dir)
        if not target.is_absolute():
            target = config.ROOT / target
        if config.ROOT not in target.resolve().parents and target.resolve() != config.ROOT:
            raise DownloadError("target_dir must stay inside DemonsForge")
        return target
    return {
        "model": config.MODELS_DIR,
        "lora": config.LORAS_DIR,
        "embedding": config.EMBEDDINGS_DIR,
        "control_asset": config.CONTROL_ASSETS_DIR,
        "ip_adapter": config.CONTROL_ASSETS_DIR / "ip_adapter",
    }[spec.asset_type]


def validate_download_spec(spec: AssetDownloadSpec) -> None:
    if not spec.approved:
        raise DownloadError("asset download requires approved=true")
    parsed = urlparse(spec.source_url)
    if parsed.scheme != "https" or parsed.netloc not in APPROVED_HOSTS:
        raise DownloadError(f"unverified URL is not allowed: {spec.source_url}")
    if not re.match(r"^[A-Za-z0-9._ -]+$", spec.name):
        raise DownloadError("asset name contains unsupported characters")


def download_asset(spec: AssetDownloadSpec) -> dict[str, str]:
    validate_download_spec(spec)
    target_dir = target_dir_for(spec)
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(urlparse(spec.source_url).path).suffix or ".bin"
    target = target_dir / f"{spec.name}{suffix}"

    digest = hashlib.sha256()
    with requests.get(spec.source_url, stream=True, timeout=30) as response:
        response.raise_for_status()
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                digest.update(chunk)
                handle.write(chunk)
    sha256 = digest.hexdigest()
    if spec.sha256 and spec.sha256.lower() != sha256:
        target.unlink(missing_ok=True)
        raise DownloadError("downloaded file sha256 does not match expected hash")

    return {
        "path": str(target),
        "sha256": sha256,
        "status": "downloaded",
        "license_note": spec.license_note or "",
    }
