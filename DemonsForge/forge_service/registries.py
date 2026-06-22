from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from datetime import datetime, timezone
from functools import lru_cache

from . import config


ENGINE_MODELS = {
    "stable_diffusion": {
        "default_model": "stable-diffusion-3.5-large",
        "pipeline": "StableDiffusion3Pipeline",
        "job_types": ["txt2img"],
        "supports_negative_prompt": True,
        "supports_lora": False,
        "supports_img2img": False,
        "supports_inpaint": False,
        "supports_control": False,
        "guidance_default": 4.5,
        "steps_default": 28,
    },
    "sdxl": {
        "default_model": "stable-diffusion-xl-base-1.0",
        "pipeline": "StableDiffusionXLPipeline",
        "job_types": ["txt2img", "img2img", "inpaint"],
        "supports_negative_prompt": True,
        "supports_lora": True,
        "supports_img2img": True,
        "supports_inpaint": True,
        "supports_control": False,
        "guidance_default": 7.0,
        "steps_default": 30,
    },
    "flux": {
        "default_model": "FLUX.1-schnell",
        "pipeline": "FluxPipeline",
        "job_types": ["txt2img"],
        "supports_negative_prompt": False,
        "supports_lora": False,
        "supports_img2img": False,
        "supports_inpaint": False,
        "supports_control": False,
        "guidance_default": 0.0,
        "steps_default": 4,
    },
}

SAMPLERS = ["default"]
SCHEDULERS = [
    {"name": "native", "available": True, "class": None},
    {"name": "euler", "available": True, "class": "EulerDiscreteScheduler"},
    {"name": "ddim", "available": True, "class": "DDIMScheduler"},
    {"name": "dpm_solver", "available": True, "class": "DPMSolverMultistepScheduler"},
]
ASPECT_PRESETS = {
    "square": {"width": 1024, "height": 1024},
    "portrait": {"width": 832, "height": 1216},
    "landscape": {"width": 1216, "height": 832},
    "wide": {"width": 1344, "height": 768},
}
SERVICE_JOB_TYPES = ["upscale", "prompt-enhance", "metadata-read", "asset-download"]
UNSUPPORTED_JOB_TYPES = ["outpaint", "variation"]
FUTURE_FEATURES = ["ControlNet", "IP-Adapter", "reference_image"]


@lru_cache(maxsize=256)
def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


@lru_cache(maxsize=512)
def _modified_at(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.is_file():
        mtime = path.stat().st_mtime
    else:
        mtimes = [p.stat().st_mtime for p in path.rglob("*") if p.exists()]
        mtime = max(mtimes, default=path.stat().st_mtime)
    return datetime.fromtimestamp(mtime, timezone.utc).isoformat()


def discover_models() -> list[dict[str, Any]]:
    models = []
    known_names = set()
    for engine, meta in ENGINE_MODELS.items():
        name = meta["default_model"]
        known_names.add(name)
        path = config.MODELS_DIR / name
        models.append(
            {
                "name": name,
                "engine": engine,
                "path": str(path),
                "available": (path / "model_index.json").exists(),
                "size_bytes": _dir_size(path),
                "modified_at": _modified_at(path),
                "pipeline": meta["pipeline"],
            }
        )
    if config.MODELS_DIR.exists():
        for path in sorted(config.MODELS_DIR.iterdir()):
            if not path.is_dir() or path.name in known_names:
                continue
            model_index = path / "model_index.json"
            if not model_index.exists():
                continue
            try:
                payload = json.loads(model_index.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
            pipeline = payload.get("_class_name")
            inferred_engine = {
                "StableDiffusionXLPipeline": "sdxl",
                "StableDiffusion3Pipeline": "stable_diffusion",
                "FluxPipeline": "flux",
            }.get(str(pipeline), "unknown")
            models.append(
                {
                    "name": path.name,
                    "engine": inferred_engine,
                    "path": str(path),
                    "available": True,
                    "size_bytes": _dir_size(path),
                    "modified_at": _modified_at(path),
                    "pipeline": pipeline,
                    "registered": False,
                }
            )
    return models


def discover_loras() -> list[dict[str, Any]]:
    roots = [config.LORAS_DIR, config.MODELS_DIR]
    found = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.safetensors"):
            if "lora" not in path.name.lower() and "lora" not in str(path.parent).lower():
                continue
            found.append(
                {
                    "name": path.stem,
                    "path": str(path),
                    "size_bytes": path.stat().st_size,
                    "modified_at": _modified_at(path),
                    "sha256": None,
                    "license_note": None,
                    "status": "local",
                }
            )
    return found


def find_lora(name: str) -> dict[str, Any] | None:
    for item in discover_loras():
        if item["name"].lower() == name.lower():
            return item
    return None


def discover_embeddings() -> list[dict[str, Any]]:
    if not config.EMBEDDINGS_DIR.exists():
        return []
    return [
        {"name": p.stem, "path": str(p), "size_bytes": p.stat().st_size, "modified_at": _modified_at(p)}
        for p in config.EMBEDDINGS_DIR.rglob("*")
        if p.is_file()
    ]


def capabilities() -> dict[str, Any]:
    models = discover_models()
    model_by_engine = {m["engine"]: m for m in models}
    engines = {}
    for name, meta in ENGINE_MODELS.items():
        model = model_by_engine[name]
        engines[name] = {
            **meta,
            "available": model["available"],
            "models": [model["name"]],
            "job_types": meta["job_types"],
            "implemented": {
                "txt2img": "txt2img" in meta["job_types"],
                "img2img": bool(meta.get("supports_img2img")),
                "inpaint": bool(meta.get("supports_inpaint")),
                "lora": bool(meta.get("supports_lora")),
                "negative_prompt": bool(meta.get("supports_negative_prompt")),
                "control": bool(meta.get("supports_control")),
            },
            "unsupported_job_types": UNSUPPORTED_JOB_TYPES,
            "future_features": FUTURE_FEATURES,
        }
    return {
        "service": "DemonsForge",
        "version": "0.1.0",
        "engines": engines,
        "models": models,
        "loras": discover_loras(),
        "embeddings": discover_embeddings(),
        "samplers": SAMPLERS,
        "schedulers": SCHEDULERS,
        "aspect_presets": ASPECT_PRESETS,
        "limits": {
            "max_width": config.MAX_WIDTH,
            "max_height": config.MAX_HEIGHT,
            "max_steps": config.MAX_STEPS,
            "max_batch": config.MAX_BATCH,
        },
        "implemented_job_types": sorted(
            set(SERVICE_JOB_TYPES)
            | {job_type for meta in ENGINE_MODELS.values() for job_type in meta["job_types"]}
        ),
        "service_job_types": SERVICE_JOB_TYPES,
        "unsupported_job_types": UNSUPPORTED_JOB_TYPES,
        "future_features": FUTURE_FEATURES,
    }


def clear_registry_caches() -> None:
    _dir_size.cache_clear()
    _modified_at.cache_clear()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
