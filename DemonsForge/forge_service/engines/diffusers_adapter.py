from __future__ import annotations

import os
import gc
import inspect
import time
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
        self._img2img_pipe: Any = None
        self._inpaint_pipe: Any = None
        self.loaded_model: str | None = None
        self.last_used = 0.0
        self._loaded_loras: dict[int, set[str]] = {}

    def _model_dir(self, spec: JobSpec) -> Path:
        model_name = spec.model or self.meta["default_model"]
        return config.MODELS_DIR / model_name

    def _pipeline_kwargs(self) -> dict[str, Any]:
        import torch

        kwargs: dict[str, Any] = {
            "torch_dtype": torch.float32,
            "low_cpu_mem_usage": True,
        }
        if self.name == "sdxl":
            kwargs["use_safetensors"] = True
        return kwargs

    def _load_pipeline(self, spec: JobSpec) -> Any:
        model_dir = self._model_dir(spec)
        if self._pipe is not None and self.loaded_model == str(model_dir):
            self.last_used = time.monotonic()
            return self._pipe
        if not (model_dir / "model_index.json").exists():
            raise EngineError(f"Model is not available locally: {model_dir}")

        config.force_cpu_runtime()

        from diffusers import FluxPipeline, StableDiffusion3Pipeline, StableDiffusionXLPipeline

        pipeline_cls = {
            "StableDiffusion3Pipeline": StableDiffusion3Pipeline,
            "StableDiffusionXLPipeline": StableDiffusionXLPipeline,
            "FluxPipeline": FluxPipeline,
        }[self.meta["pipeline"]]
        self._pipe = pipeline_cls.from_pretrained(model_dir, **self._pipeline_kwargs())
        self._pipe.set_progress_bar_config(disable=False)
        self.loaded_model = str(model_dir)
        self.last_used = time.monotonic()
        return self._pipe

    def unload(self) -> bool:
        if self._pipe is None and self._img2img_pipe is None and self._inpaint_pipe is None:
            return False
        self._pipe = None
        self._img2img_pipe = None
        self._inpaint_pipe = None
        self.loaded_model = None
        self._loaded_loras.clear()
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        return True

    def unload_if_idle(self, idle_seconds: int) -> bool:
        loaded = self._pipe is not None or self._img2img_pipe is not None or self._inpaint_pipe is not None
        if not loaded:
            return False
        if time.monotonic() - self.last_used < idle_seconds:
            return False
        return self.unload()

    def runtime_state(self) -> dict[str, object]:
        loaded = self._pipe is not None or self._img2img_pipe is not None or self._inpaint_pipe is not None
        return {
            "engine": self.name,
            "loaded": loaded,
            "loaded_model": self.loaded_model,
            "loaded_pipelines": {
                "txt2img": self._pipe is not None,
                "img2img": self._img2img_pipe is not None,
                "inpaint": self._inpaint_pipe is not None,
            },
            "loaded_loras": sorted({name for names in self._loaded_loras.values() for name in names}),
            "idle_seconds": round(time.monotonic() - self.last_used, 1) if loaded else None,
        }

    def _load_sdxl_img2img_pipeline(self, spec: JobSpec) -> Any:
        if self.name != "sdxl":
            raise EngineError(f"{self.name} does not support img2img yet")
        model_dir = self._model_dir(spec)
        if self._img2img_pipe is not None and self.loaded_model == str(model_dir):
            self.last_used = time.monotonic()
            return self._img2img_pipe
        if not (model_dir / "model_index.json").exists():
            raise EngineError(f"Model is not available locally: {model_dir}")
        config.force_cpu_runtime()
        from diffusers import StableDiffusionXLImg2ImgPipeline

        self._img2img_pipe = StableDiffusionXLImg2ImgPipeline.from_pretrained(
            model_dir,
            **self._pipeline_kwargs(),
        )
        self._img2img_pipe.set_progress_bar_config(disable=False)
        self.loaded_model = str(model_dir)
        self.last_used = time.monotonic()
        return self._img2img_pipe

    def _load_sdxl_inpaint_pipeline(self, spec: JobSpec) -> Any:
        if self.name != "sdxl":
            raise EngineError(f"{self.name} does not support inpaint yet")
        model_dir = self._model_dir(spec)
        if self._inpaint_pipe is not None and self.loaded_model == str(model_dir):
            self.last_used = time.monotonic()
            return self._inpaint_pipe
        if not (model_dir / "model_index.json").exists():
            raise EngineError(f"Model is not available locally: {model_dir}")
        config.force_cpu_runtime()
        from diffusers import StableDiffusionXLInpaintPipeline

        self._inpaint_pipe = StableDiffusionXLInpaintPipeline.from_pretrained(
            model_dir,
            **self._pipeline_kwargs(),
        )
        self._inpaint_pipe.set_progress_bar_config(disable=False)
        self.loaded_model = str(model_dir)
        self.last_used = time.monotonic()
        return self._inpaint_pipe

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
        loaded_for_pipe = self._loaded_loras.setdefault(id(pipe), set())
        for item in spec.loras:
            local = find_lora(item.name)
            if not local:
                raise EngineError(f"LoRA is not available locally: {item.name}")
            adapter_name = f"lora_{item.name}".replace(" ", "_")
            if adapter_name not in loaded_for_pipe:
                pipe.load_lora_weights(local["path"], adapter_name=adapter_name)
                loaded_for_pipe.add(adapter_name)
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

        signature = inspect.signature(pipe.__call__)
        if "callback_on_step_end" in signature.parameters:
            total_steps = max(spec.steps, 1)

            def on_step_end(_pipeline: Any, step: int, _timestep: Any, callback_kwargs: dict[str, Any]):
                progress_value = 0.15 + (0.7 * min(step + 1, total_steps) / total_steps)
                progress(progress_value, f"generation step {step + 1}/{total_steps}")
                return callback_kwargs

            kwargs["callback_on_step_end"] = on_step_end

        progress(0.15, "generating image")
        result = pipe(**kwargs)
        progress(0.9, "image generated")
        self.last_used = time.monotonic()
        return list(result.images)

    def generate_img2img(self, spec: JobSpec, source_image: Path, progress: ProgressCallback) -> list[object]:
        if spec.type.value != "img2img":
            raise EngineError(f"{self.name} does not support job type {spec.type.value}")
        progress(0.05, "loading img2img pipeline")
        pipe = self._load_sdxl_img2img_pipeline(spec)
        self._apply_scheduler(pipe, spec.scheduler)
        self._apply_loras(pipe, spec)
        image = self._load_input_image(source_image, spec.width, spec.height)
        kwargs = self._image_job_kwargs(spec)
        kwargs["image"] = image
        kwargs["strength"] = spec.strength
        kwargs.update(self._callback_kwargs(pipe, spec.steps, progress))
        progress(0.15, "generating img2img")
        result = pipe(**kwargs)
        progress(0.9, "image generated")
        self.last_used = time.monotonic()
        return list(result.images)

    def generate_inpaint(
        self,
        spec: JobSpec,
        source_image: Path,
        mask_image: Path,
        progress: ProgressCallback,
    ) -> list[object]:
        if spec.type.value != "inpaint":
            raise EngineError(f"{self.name} does not support job type {spec.type.value}")
        progress(0.05, "loading inpaint pipeline")
        pipe = self._load_sdxl_inpaint_pipeline(spec)
        self._apply_scheduler(pipe, spec.scheduler)
        self._apply_loras(pipe, spec)
        image = self._load_input_image(source_image, spec.width, spec.height)
        mask = self._load_input_image(mask_image, spec.width, spec.height)
        kwargs = self._image_job_kwargs(spec)
        kwargs["image"] = image
        kwargs["mask_image"] = mask
        kwargs["strength"] = spec.strength
        kwargs.update(self._callback_kwargs(pipe, spec.steps, progress))
        progress(0.15, "generating inpaint")
        result = pipe(**kwargs)
        progress(0.9, "image generated")
        self.last_used = time.monotonic()
        return list(result.images)

    def _image_job_kwargs(self, spec: JobSpec) -> dict[str, Any]:
        import torch

        generator = None
        if spec.seed is not None and spec.seed >= 0:
            generator = torch.Generator(device="cpu").manual_seed(spec.seed)
        kwargs: dict[str, Any] = {
            "prompt": (spec.prompt or "").strip(),
            "negative_prompt": (spec.negative_prompt or "").strip() or None,
            "width": spec.width,
            "height": spec.height,
            "num_inference_steps": spec.steps,
            "guidance_scale": spec.guidance if spec.guidance is not None else spec.cfg or self.meta["guidance_default"],
            "generator": generator,
        }
        return kwargs

    def _callback_kwargs(self, pipe: Any, steps: int, progress: ProgressCallback) -> dict[str, Any]:
        signature = inspect.signature(pipe.__call__)
        if "callback_on_step_end" not in signature.parameters:
            return {}
        total_steps = max(steps, 1)

        def on_step_end(_pipeline: Any, step: int, _timestep: Any, callback_kwargs: dict[str, Any]):
            progress_value = 0.15 + (0.7 * min(step + 1, total_steps) / total_steps)
            progress(progress_value, f"generation step {step + 1}/{total_steps}")
            return callback_kwargs

        return {"callback_on_step_end": on_step_end}

    def _load_input_image(self, path: Path, width: int, height: int) -> Any:
        from PIL import Image

        with Image.open(path) as image:
            return image.convert("RGB").resize((width, height))
