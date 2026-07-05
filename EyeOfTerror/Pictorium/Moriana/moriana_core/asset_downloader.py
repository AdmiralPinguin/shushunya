from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse

import requests

from EyeOfTerror.Pictorium.Moriana.forge_runtime import config
from EyeOfTerror.Pictorium.Moriana.forge_runtime.schemas import AssetDownloadSpec


APPROVED_HOSTS = {
    "huggingface.co",
    "civitai.com",
    "github.com",
    "raw.githubusercontent.com",
}

ALLOWED_SUFFIXES_BY_TYPE = {
    "model": {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf"},
    "lora": {".safetensors", ".pt", ".bin"},
    "embedding": {".safetensors", ".pt", ".bin"},
    "control_asset": {".safetensors", ".pt", ".pth", ".bin", ".onnx"},
    "ip_adapter": {".safetensors", ".pt", ".pth", ".bin", ".onnx"},
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
    if spec.sha256 and not re.fullmatch(r"[A-Fa-f0-9]{64}", spec.sha256):
        raise DownloadError("sha256 must be a 64-character hexadecimal digest")
    suffix = (Path(parsed.path).suffix or ".bin").lower()
    allowed_suffixes = ALLOWED_SUFFIXES_BY_TYPE[spec.asset_type]
    if suffix not in allowed_suffixes:
        raise DownloadError(f"asset type {spec.asset_type} does not allow {suffix} downloads")
    if suffix == ".bin" and not spec.sha256:
        raise DownloadError("generic .bin asset downloads require sha256")
    target = target_dir_for(spec) / f"{spec.name}{suffix}"
    if target.exists():
        raise DownloadError(f"target asset already exists: {target}")


def download_asset(spec: AssetDownloadSpec) -> dict[str, object]:
    validate_download_spec(spec)
    target_dir = target_dir_for(spec)
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(urlparse(spec.source_url).path).suffix or ".bin"
    target = target_dir / f"{spec.name}{suffix}"
    partial = target.with_suffix(f"{target.suffix}.part")

    digest = hashlib.sha256()
    total_bytes = 0
    try:
        with requests.get(spec.source_url, stream=True, timeout=30) as response:
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > config.MAX_ASSET_DOWNLOAD_BYTES:
                raise DownloadError("asset exceeds configured maximum download size")
            with partial.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total_bytes += len(chunk)
                    if total_bytes > config.MAX_ASSET_DOWNLOAD_BYTES:
                        raise DownloadError("asset exceeds configured maximum download size")
                    digest.update(chunk)
                    handle.write(chunk)
        partial.replace(target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    sha256 = digest.hexdigest()
    if spec.sha256 and spec.sha256.lower() != sha256:
        target.unlink(missing_ok=True)
        raise DownloadError("downloaded file sha256 does not match expected hash")

    return {
        "path": str(target),
        "sha256": sha256,
        "size_bytes": total_bytes,
        "status": "downloaded",
        "license_note": spec.license_note or "",
    }
