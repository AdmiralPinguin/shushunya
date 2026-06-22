from __future__ import annotations

import re

from .registries import ASPECT_PRESETS, capabilities
from .schemas import AssetRequest, JobSpec, JobType, PlanRequest


QUALITY_HINTS = {
    "кинематограф": "cinematic lighting, high detail",
    "фотореал": "photorealistic, natural light",
    "аниме": "anime style, clean linework",
    "иллюстрац": "digital illustration, polished composition",
    "портрет": "portrait, detailed face",
}


def _choose_engine(text: str, preferred: str | None) -> str:
    caps = capabilities()
    if preferred and preferred in caps["engines"]:
        return preferred
    lowered = text.lower()
    if "flux" in lowered and caps["engines"]["flux"]["available"]:
        return "flux"
    if "sdxl" in lowered and caps["engines"]["sdxl"]["available"]:
        return "sdxl"
    for engine in ["sdxl", "flux", "stable_diffusion"]:
        if caps["engines"][engine]["available"]:
            return engine
    return "sdxl"


def _aspect(text: str) -> tuple[int, int, str | None]:
    lowered = text.lower()
    if "портрет" in lowered or "вертик" in lowered:
        preset = "portrait"
    elif "пейзаж" in lowered or "горизонт" in lowered or "wide" in lowered:
        preset = "landscape"
    else:
        preset = "square"
    dims = ASPECT_PRESETS[preset]
    return dims["width"], dims["height"], preset


def _asset_request_if_needed(text: str) -> AssetRequest | None:
    lowered = text.lower()
    match = re.search(r"(lora|лора|модель|персонаж)\s*[:=]?\s*([A-Za-zА-Яа-я0-9_. -]{3,64})", text, re.I)
    if not match:
        return None
    name = match.group(2).strip(" .,:;")
    existing = capabilities()["loras"]
    if any(item["name"].lower() == name.lower() for item in existing):
        return None
    asset_type = "lora" if "lora" in lowered or "лора" in lowered else "model"
    return AssetRequest(
        name=name,
        asset_type=asset_type,
        candidate_source="huggingface.co or civitai.com",
        license_note="Unknown until the user confirms the exact asset page.",
        risks=["license may restrict commercial or character usage", "unverified weights can be unsafe"],
        requires_user_approval=True,
    )


def plan_txt2img(request: PlanRequest) -> JobSpec:
    text = request.request.strip()
    engine = _choose_engine(text, request.preferred_engine)
    caps = capabilities()
    engine_caps = caps["engines"][engine]
    model = engine_caps["default_model"]
    width, height, preset = _aspect(text)
    prompt = text
    additions = [value for key, value in QUALITY_HINTS.items() if key in text.lower()]
    if additions:
        prompt = f"{prompt}, {', '.join(additions)}"

    spec = JobSpec(
        type=JobType.txt2img,
        engine=engine,
        model=model,
        prompt=prompt,
        negative_prompt="low quality, blurry, distorted" if engine_caps["supports_negative_prompt"] else None,
        width=width,
        height=height,
        aspect_preset=preset,
        steps=min(engine_caps["steps_default"], 20),
        guidance=engine_caps["guidance_default"],
        sampler="default",
        scheduler="native",
        batch_size=1,
        asset_request=_asset_request_if_needed(text),
    )
    return spec
