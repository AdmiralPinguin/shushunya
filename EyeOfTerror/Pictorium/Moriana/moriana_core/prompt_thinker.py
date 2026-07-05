from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

import requests
from pydantic import ValidationError

from EyeOfTerror.Pictorium.Moriana.forge_runtime import config
from EyeOfTerror.Pictorium.Moriana.forge_runtime.schemas import AssetRequest, JobSpec, PlanRequest

from .asset_catalog import SAMPLERS, SCHEDULERS, capabilities


ALLOWED_PATCH_FIELDS = {
    "type",
    "engine",
    "model",
    "prompt",
    "negative_prompt",
    "width",
    "height",
    "aspect_preset",
    "steps",
    "cfg",
    "guidance",
    "sampler",
    "scheduler",
    "seed",
    "strength",
    "upscale_factor",
    "batch_size",
    "loras",
    "embeddings",
    "control",
    "asset_request",
}


class PlannerThinker:
    def __init__(
        self,
        enabled: bool,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float,
    ) -> None:
        self.enabled = enabled
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_config(cls) -> "PlannerThinker":
        return cls(
            enabled=config.PLANNER_THINKER_ENABLED,
            base_url=config.PLANNER_THINKER_BASE_URL,
            api_key=config.PLANNER_THINKER_API_KEY,
            model=config.PLANNER_THINKER_MODEL,
            timeout=config.PLANNER_THINKER_TIMEOUT_SECONDS,
        )

    def status(self) -> dict[str, object]:
        diagnostics = []
        if not self.enabled:
            diagnostics.append("disabled by FORGE_PLANNER_THINKER_ENABLED")
        if self.enabled and not self.base_url:
            diagnostics.append("missing FORGE_PLANNER_THINKER_BASE_URL")
        if self.enabled and not self.model:
            diagnostics.append("missing FORGE_PLANNER_THINKER_MODEL")
        return {
            "enabled": self.enabled,
            "provider": "openai-compatible",
            "base_url_configured": bool(self.base_url),
            "api_key_configured": bool(self.api_key),
            "model": self.model or None,
            "timeout_seconds": self.timeout,
            "ready": self.ready,
            "write_policy": "advisory-json-patch-only",
            "diagnostics": diagnostics,
        }

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.base_url and self.model)

    def improve_plan(self, request: PlanRequest, baseline: JobSpec) -> tuple[JobSpec, dict[str, object]]:
        meta: dict[str, object] = {
            "enabled": self.enabled,
            "used": False,
            "ready": self.ready,
            "provider": "openai-compatible",
            "model": self.model or None,
        }
        if not self.enabled:
            meta["reason"] = "disabled"
            return baseline, meta
        if not self.ready:
            meta["reason"] = "not configured"
            meta["status"] = self.status()
            return baseline, meta

        try:
            patch_payload = self._request_patch(request, baseline)
            patch = self._extract_patch(patch_payload)
            spec, accepted = self._apply_patch(baseline, patch)
        except Exception as exc:
            meta["reason"] = "thinker failed"
            meta["error"] = str(exc)
            return baseline, meta

        meta["used"] = True
        meta["accepted_fields"] = accepted
        meta["rejected_fields"] = sorted(set(patch) - set(accepted))
        return spec, meta

    def _request_patch(self, request: PlanRequest, baseline: JobSpec) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are DemonsForge planner thinker. Return only a compact JSON object. "
                        "Do not use markdown. Do not reveal chain-of-thought. "
                        "You may return advisory overrides for a Stable Diffusion job spec, "
                        "but every field must be compatible with the supplied capabilities. "
                        "If a requested model, LoRA, embedding, ControlNet, IP-Adapter, or reference asset "
                        "is not local, create asset_request with requires_user_approval=true. "
                        "Do not invent successful downloads or local assets."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": request.request,
                            "preferred_engine": request.preferred_engine,
                            "baseline_spec": baseline.model_dump(mode="json"),
                            "capabilities": capabilities(),
                            "allowed_patch_fields": sorted(ALLOWED_PATCH_FIELDS),
                            "engine_policy": {
                                "first_txt2img": "stable_diffusion_or_flux",
                                "existing_image_operations": "sdxl",
                            },
                            "output_contract": {
                                "patch": "object with only allowed_patch_fields",
                                "notes": "short operational notes, no private reasoning",
                            },
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("thinker response content is not text")
        if len(content) > config.PLANNER_THINKER_MAX_PATCH_CHARS:
            raise ValueError("thinker response is too large")
        return content

    def _extract_patch(self, content: str) -> dict[str, Any]:
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("thinker response must be a JSON object")
        patch = data.get("patch", data)
        if not isinstance(patch, dict):
            raise ValueError("thinker patch must be a JSON object")
        return patch

    def _apply_patch(self, baseline: JobSpec, patch: dict[str, Any]) -> tuple[JobSpec, list[str]]:
        accepted: list[str] = []
        payload = baseline.model_dump(mode="json")
        for key, value in patch.items():
            if key not in ALLOWED_PATCH_FIELDS:
                continue
            if value is None and key not in {"cfg", "guidance", "seed", "negative_prompt", "asset_request"}:
                continue
            if key == "asset_request" and value is not None:
                value = self._asset_request_or_none(value)
                if value is None:
                    continue
            payload[key] = deepcopy(value)
            accepted.append(key)
        try:
            spec = JobSpec(**payload)
        except ValidationError as exc:
            raise ValueError(f"thinker patch produced invalid job spec: {exc}") from None
        self._validate_against_capabilities(spec)
        return spec, accepted

    def _asset_request_or_none(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        request = AssetRequest(**value)
        if not request.requires_user_approval:
            request.requires_user_approval = True
        return request.model_dump(mode="json")

    def _validate_against_capabilities(self, spec: JobSpec) -> None:
        caps = capabilities()
        if spec.engine is not None:
            engine = caps["engines"].get(spec.engine)
            if engine is None:
                raise ValueError(f"thinker selected unknown engine: {spec.engine}")
            if spec.type.value not in engine["job_types"]:
                raise ValueError(f"thinker selected engine {spec.engine} for unsupported job type {spec.type.value}")
            if spec.model:
                local_models = {str(item["name"]) for item in caps["models"]}
                if spec.model not in local_models:
                    raise ValueError(f"thinker selected non-local model: {spec.model}")
            if spec.negative_prompt and not engine.get("supports_negative_prompt"):
                raise ValueError(f"thinker selected negative_prompt for engine without support: {spec.engine}")
        if spec.scheduler:
            schedulers = {str(item["name"]) for item in SCHEDULERS if item.get("available")}
            if spec.scheduler not in schedulers:
                raise ValueError(f"thinker selected unsupported scheduler: {spec.scheduler}")
        if spec.sampler and spec.sampler not in SAMPLERS:
            raise ValueError(f"thinker selected unsupported sampler: {spec.sampler}")
        if spec.loras:
            local_loras = {str(item["name"]).lower() for item in caps["loras"]}
            for lora in spec.loras:
                if lora.name.lower() not in local_loras:
                    raise ValueError(f"thinker selected non-local LoRA: {lora.name}")
        if spec.embeddings:
            local_embeddings = {str(item["name"]).lower() for item in caps["embeddings"]}
            for embedding in spec.embeddings:
                if embedding.lower() not in local_embeddings:
                    raise ValueError(f"thinker selected non-local embedding: {embedding}")
