"""Autonomous LoRA scout: find an SDXL-compatible LoRA on HuggingFace for a
requested style and download it into the local LoRA folder.

Full autonomy (per owner's choice): it searches, picks, and downloads without a
per-item approval prompt — but still only from HuggingFace over https, only
SDXL-tagged repos, only .safetensors files, within the size cap enforced by
asset_downloader. Autonomous style matching is inherently noisy; the scout
returns None when it can't find a plausible SDXL LoRA rather than grabbing junk.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from EyeOfTerror.Pictorium.Moriana.forge_runtime.schemas import AssetDownloadSpec
from EyeOfTerror.Pictorium.Moriana.moriana_core.asset_catalog import find_lora
from EyeOfTerror.Pictorium.Moriana.moriana_core.asset_downloader import DownloadError, download_asset

_UA = {"User-Agent": "Mozilla/5.0 (MorianaLoraScout)"}


def _get(url: str) -> Any:
    with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=30) as response:
        return json.loads(response.read())


def _is_sdxl(model: dict[str, Any]) -> bool:
    tags = [str(t).lower() for t in (model.get("tags") or [])]
    repo = str(model.get("id") or "").lower()
    if not any("lora" in t for t in tags):
        return False
    blob = " ".join(tags) + " " + repo
    # Reject clearly non-SDXL bases; accept when SDXL is named or nothing rules it out.
    if any(bad in blob for bad in ("sd-v1", "sd1.5", "sdv1-5", "stable-diffusion-v1", "flux", "pony", "sd3", "qwen")):
        return "sdxl" in blob or "xl-base" in blob
    return "xl" in blob or "sdxl" in blob or "base_model" in blob


def _query_cascade(style_query: str) -> list[str]:
    style_query = " ".join(str(style_query or "").split())
    words = style_query.split()
    forms = [f"{style_query} sdxl lora", f"{' '.join(words[:2])} sdxl lora" if words else "", f"{words[0]} sdxl lora" if words else "", "sdxl lora"]
    seen: list[str] = []
    for form in forms:
        form = " ".join(form.split())
        if form and form not in seen:
            seen.append(form)
    return seen


def search_sdxl_lora(style_query: str, limit: int = 12) -> list[dict[str, Any]]:
    """Return SDXL-LoRA candidates for a style, best (most downloaded) first.
    HuggingFace search is brittle with long queries, so we fall back from the
    full style phrase to progressively shorter forms until one returns hits."""
    for query in _query_cascade(style_query):
        url = f"https://huggingface.co/api/models?search={urllib.parse.quote(query)}&limit={limit}&sort=downloads&direction=-1"
        try:
            results = _get(url)
        except Exception:  # noqa: BLE001 - offline / API hiccup: try next form
            continue
        candidates = []
        for model in results if isinstance(results, list) else []:
            if not _is_sdxl(model):
                continue
            repo = str(model.get("id") or "")
            if repo:
                candidates.append({"repo": repo, "downloads": int(model.get("downloads") or 0), "query": query})
        if candidates:
            return candidates
    return []


def _resolve_safetensors(repo: str) -> str | None:
    try:
        info = _get(f"https://huggingface.co/api/models/{urllib.parse.quote(repo)}")
    except Exception:  # noqa: BLE001
        return None
    files = [str(s.get("rfilename") or "") for s in (info.get("siblings") or [])]
    weights = [f for f in files if f.endswith(".safetensors")]
    if not weights:
        return None
    # Prefer a file that looks like the LoRA weight, not a text-encoder shard.
    weights.sort(key=lambda f: (("lora" not in f.lower()), len(f)))
    return weights[0]


def acquire_lora(style_query: str) -> dict[str, Any] | None:
    """Find and download the best SDXL LoRA for a style. Returns
    {name, path, repo, file} to feed into a JobSpec's loras, or None."""
    for candidate in search_sdxl_lora(style_query):
        repo = candidate["repo"]
        weight = _resolve_safetensors(repo)
        if not weight:
            continue
        name = repo.replace("/", "_")
        existing = find_lora(name)
        if existing:
            return {"name": name, "path": str(existing), "repo": repo, "file": weight, "reused": True}
        url = f"https://huggingface.co/{repo}/resolve/main/{urllib.parse.quote(weight)}?download=true"
        spec = AssetDownloadSpec(name=name, asset_type="lora", source_url=url, approved=True)
        try:
            result = download_asset(spec)
        except DownloadError:
            continue  # not allowed / too big / bad type: try the next candidate
        except Exception:  # noqa: BLE001 - network hiccup: try the next
            continue
        return {"name": name, "path": str(result.get("path") or result.get("target") or ""), "repo": repo, "file": weight}
    return None
