from __future__ import annotations

import re

from .registries import ASPECT_PRESETS, capabilities
from .schemas import AssetRequest, JobSpec, JobType, LoraRef, PlanRequest


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
    explicit = re.search(r"(\d{3,4})\s*[xх]\s*(\d{3,4})", text, re.I)
    if explicit:
        width = int(explicit.group(1)) // 8 * 8
        height = int(explicit.group(2)) // 8 * 8
        return width, height, "custom"
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
    match = re.search(r"(lora|лора|модель)\s*[:=]?\s*([A-Za-zА-Яа-я0-9_. -]{3,64})", text, re.I)
    if not match:
        match = re.search(r"персонаж\s+([A-Za-zА-Яа-я0-9_. -]{3,48})", text, re.I)
    if not match:
        return None
    name = match.group(match.lastindex or 1).strip(" .,:;")
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


def _local_loras(text: str) -> list[LoraRef]:
    refs = []
    existing = capabilities()["loras"]
    for match in re.finditer(r"(?:lora|лора)\s*[:=]\s*([A-Za-zА-Яа-я0-9_. -]{3,64})(?:@([0-9.]+))?", text, re.I):
        name = match.group(1).strip(" .,:;")
        local = next((item for item in existing if item["name"].lower() == name.lower()), None)
        if local:
            refs.append(LoraRef(name=local["name"], weight=float(match.group(2) or 1.0)))
    return refs


def _steps(text: str, default: int) -> int:
    match = re.search(r"(?:steps|шаг(?:ов|и|а)?)\s*[:=]?\s*(\d{1,3})", text, re.I)
    if not match:
        return min(default, 20)
    return max(1, min(int(match.group(1)), 60))


def _seed(text: str) -> int | None:
    match = re.search(r"seed\s*[:=]?\s*(\d+)", text, re.I)
    return int(match.group(1)) if match else None


def _negative(text: str, supports_negative: bool) -> str | None:
    if not supports_negative:
        return None
    match = re.search(r"(?:negative|негатив)\s*[:=]\s*([^;]+)", text, re.I)
    if match:
        return match.group(1).strip()
    return "low quality, blurry, distorted"


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
        negative_prompt=_negative(text, engine_caps["supports_negative_prompt"]),
        width=width,
        height=height,
        aspect_preset=preset,
        steps=_steps(text, engine_caps["steps_default"]),
        guidance=engine_caps["guidance_default"],
        sampler="default",
        scheduler="native",
        seed=_seed(text),
        batch_size=1,
        loras=_local_loras(text),
        asset_request=_asset_request_if_needed(text),
    )
    return spec
