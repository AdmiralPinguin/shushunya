from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .. import config
from ..registries import ENGINE_MODELS, find_lora
from ..schemas import JobSpec
from .base import BaseEngine, EngineError, ProgressCallback


class DiffusersEngine(BaseEngine):
    def __init__(self, engine_name: str):
        if engine_name not in ENGINE_MODELS:
            raise EngineError(f"Unknown engine: {engine_name}")
        self.name = engine_name
        self.meta = ENGINE_MODELS[engine_name]
        self._pipe: Any = None

    def _model_dir(self, spec: JobSpec) -> Path:
        model_name = spec.model or self.meta["default_model"]
        return config.MODELS_DIR / model_name

    def _load_pipeline(self, spec: JobSpec) -> Any:
        if self._pipe is not None:
            return self._pipe
        model_dir = self._model_dir(spec)
        if not (model_dir / "model_index.json").exists():
            raise EngineError(f"Model is not available locally: {model_dir}")

        config.force_cpu_runtime()

        import torch
        from diffusers import FluxPipeline, StableDiffusion3Pipeline, StableDiffusionXLPipeline

        pipeline_cls = {
            "StableDiffusion3Pipeline": StableDiffusion3Pipeline,
            "StableDiffusionXLPipeline": StableDiffusionXLPipeline,
            "FluxPipeline": FluxPipeline,
        }[self.meta["pipeline"]]
        kwargs: dict[str, Any] = {
            "torch_dtype": torch.float32,
            "low_cpu_mem_usage": True,
        }
        if self.name == "sdxl":
            kwargs["use_safetensors"] = True
        self._pipe = pipeline_cls.from_pretrained(model_dir, **kwargs)
        self._pipe.set_progress_bar_config(disable=False)
        return self._pipe

    def _apply_scheduler(self, pipe: Any, scheduler_name: str | None) -> None:
        if not scheduler_name or scheduler_name == "native":
            return
        scheduler_classes = {
            "euler": "EulerDiscreteScheduler",
            "ddim": "DDIMScheduler",
            "dpm_solver": "DPMSolverMultistepScheduler",
        }
        class_name = scheduler_classes.get(scheduler_name)
        if not class_name:
            raise EngineError(f"unsupported scheduler: {scheduler_name}")
        import diffusers

        scheduler_cls = getattr(diffusers, class_name)
        pipe.scheduler = scheduler_cls.from_config(pipe.scheduler.config)

    def _apply_loras(self, pipe: Any, spec: JobSpec) -> None:
        if not spec.loras:
            return
        if self.name != "sdxl":
            raise EngineError(f"{self.name} adapter does not support LoRA loading yet")
        adapter_names = []
        adapter_weights = []
        for item in spec.loras:
            local = find_lora(item.name)
            if not local:
                raise EngineError(f"LoRA is not available locally: {item.name}")
            adapter_name = f"lora_{item.name}".replace(" ", "_")
            pipe.load_lora_weights(local["path"], adapter_name=adapter_name)
            adapter_names.append(adapter_name)
            adapter_weights.append(item.weight)
        if hasattr(pipe, "set_adapters"):
            pipe.set_adapters(adapter_names, adapter_weights=adapter_weights)

    def generate_txt2img(self, spec: JobSpec, progress: ProgressCallback) -> list[object]:
        if spec.type.value != "txt2img":
            raise EngineError(f"{self.name} does not support job type {spec.type.value}")
        if spec.control:
            raise EngineError(f"{self.name} adapter does not support control assets yet")

        progress(0.05, "loading pipeline")
        pipe = self._load_pipeline(spec)
        self._apply_scheduler(pipe, spec.scheduler)
        self._apply_loras(pipe, spec)

        import torch

        seed = spec.seed
        generator = None
        if seed is not None and seed >= 0:
            generator = torch.Generator(device="cpu").manual_seed(seed)

        kwargs: dict[str, Any] = {
            "prompt": (spec.prompt or "").strip(),
            "width": spec.width,
            "height": spec.height,
            "num_inference_steps": spec.steps,
            "generator": generator,
        }
        if self.meta.get("supports_negative_prompt"):
            kwargs["negative_prompt"] = (spec.negative_prompt or "").strip() or None
        if self.name == "flux":
            kwargs["guidance_scale"] = 0.0
        else:
            kwargs["guidance_scale"] = spec.guidance if spec.guidance is not None else spec.cfg
            if kwargs["guidance_scale"] is None:
                kwargs["guidance_scale"] = self.meta["guidance_default"]

        progress(0.15, "generating image")
        result = pipe(**kwargs)
        progress(0.9, "image generated")
        return list(result.images)
