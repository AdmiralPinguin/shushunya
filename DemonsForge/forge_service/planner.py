from __future__ import annotations

import re

from .archive_memory import ArchiveMemoryClient
from .registries import ASPECT_PRESETS, SCHEDULERS, capabilities
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
    if "controlnet" in lowered or "контрол" in lowered:
        return AssetRequest(
            name="ControlNet asset requested by prompt",
            asset_type="control_asset",
            candidate_source="user-provided local file or approved model page",
            license_note="Unknown until the user confirms the exact asset.",
            risks=["control models can have incompatible licenses", "unverified weights can be unsafe"],
            requires_user_approval=True,
        )
    if "ip-adapter" in lowered or "ip adapter" in lowered or "айпи адаптер" in lowered:
        return AssetRequest(
            name="IP-Adapter asset requested by prompt",
            asset_type="ip_adapter",
            candidate_source="user-provided local file or approved model page",
            license_note="Unknown until the user confirms the exact asset.",
            risks=["adapter license may restrict usage", "unverified weights can be unsafe"],
            requires_user_approval=True,
        )
    match = re.search(r"(lora|лора|модель)\s*[:=]?\s*([A-Za-zА-Яа-я0-9_. -]{3,64})", text, re.I)
    if not match:
        match = re.search(r"персонаж\s+([A-Za-zА-Яа-я0-9_. -]{3,48})", text, re.I)
    if not match:
        return None
    name = match.group(match.lastindex or 1).strip(" .,:;")
    caps = capabilities()
    asset_type = "lora" if "lora" in lowered or "лора" in lowered else "model"
    local_items = caps["loras"] if asset_type == "lora" else caps["models"]
    if any(item["name"].lower() == name.lower() for item in local_items):
        return None
    return AssetRequest(
        name=name,
        asset_type=asset_type,
        candidate_source="user-provided approved URL, huggingface.co, or civitai.com",
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
        if any(token in text.lower() for token in ["smoke", "быстро", "тест", "test"]):
            return 1
        return min(default, 20)
    return max(1, min(int(match.group(1)), 60))


def _batch_size(text: str) -> int:
    match = re.search(r"(?:batch|батч)\s*[:=]?\s*([1-4])", text, re.I)
    if not match:
        match = re.search(r"([1-4])\s*(?:вариант(?:а|ов)?|images|картин(?:ки|ок)?)", text, re.I)
    return int(match.group(1)) if match else 1


def _seed(text: str) -> int | None:
    match = re.search(r"seed\s*[:=]?\s*(\d+)", text, re.I)
    return int(match.group(1)) if match else None


def _guidance(text: str, default: float) -> tuple[float | None, float | None]:
    match = re.search(r"(guidance|cfg)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)", text, re.I)
    if not match:
        return default, None
    value = max(0.0, min(float(match.group(2)), 30.0))
    if match.group(1).lower() == "cfg":
        return value, value
    return value, None


def _scheduler(text: str) -> str:
    names = {str(item["name"]) for item in SCHEDULERS if item.get("available")}
    match = re.search(r"(?:scheduler|планировщик)\s*[:=]?\s*([A-Za-z0-9_ -]+)", text, re.I)
    if match:
        candidate = match.group(1).strip().lower().replace(" ", "_").replace("-", "_")
        if candidate in names:
            return candidate
    lowered = text.lower()
    for name in names:
        if name != "native" and name in lowered:
            return name
    return "native"


def _upscale_factor(text: str) -> int:
    match = re.search(r"(?:upscale|апскейл|увелич(?:ь|ить)?)\s*(?:x|в)?\s*([234])\s*(?:x|раза?)?", text, re.I)
    if match:
        return int(match.group(1))
    if any(token in text.lower() for token in ["upscale", "апскейл", "увелич"]):
        match = re.search(r"(?:x|в)\s*([234])\s*(?:x|раза?)?", text, re.I)
        if match:
            return int(match.group(1))
    return 2


def _negative(text: str, supports_negative: bool) -> str | None:
    if not supports_negative:
        return None
    match = re.search(r"(?:negative|негатив)\s*[:=]\s*([^;]+)", text, re.I)
    if match:
        return match.group(1).strip()
    return "low quality, blurry, distorted"


def _job_type(text: str) -> JobType:
    lowered = text.lower()
    if any(token in lowered for token in ["inpaint", "инпейнт", "замаж", "маск", "mask"]):
        return JobType.inpaint
    if any(token in lowered for token in ["img2img", "image to image", "по картинке", "из картинки", "вариацию"]):
        return JobType.img2img
    if any(token in lowered for token in ["upscale", "апскейл", "увелич", "увеличь"]):
        return JobType.upscale
    return JobType.txt2img


def _memory_context(text: str, enabled: bool = True) -> dict[str, object]:
    if not enabled:
        return {"enabled": False, "used": False, "reason": "disabled by request"}
    client = ArchiveMemoryClient.from_config()
    status = client.status()
    if not status.get("enabled"):
        return {"enabled": False, "used": False, "reason": "disabled"}
    result = client.search(text, limit=3, layers="focus,wiki,vector,graph", include_content=False, create=True)
    if result.get("ok") is False:
        return {"enabled": True, "used": False, "error": result.get("error"), "status": status}
    excerpts = []
    for item in result.get("focus", [])[:1]:
        if item.get("excerpt"):
            excerpts.append({"layer": "focus", "title": item.get("title"), "excerpt": item.get("excerpt")})
    for item in result.get("wiki", [])[:1]:
        if item.get("excerpt"):
            excerpts.append({"layer": "wiki", "title": item.get("title"), "excerpt": item.get("excerpt")})
    for item in result.get("vector", [])[:2]:
        if item.get("excerpt"):
            excerpts.append({"layer": "vector", "turn_id": item.get("turn_id"), "excerpt": item.get("excerpt")})
    return {
        "enabled": True,
        "used": bool(excerpts),
        "namespace": result.get("memory_namespace"),
        "counts": result.get("counts", {}),
        "excerpts": excerpts,
    }


def plan_txt2img(request: PlanRequest) -> JobSpec:
    text = request.request.strip()
    job_type = _job_type(text)
    engine = _choose_engine(text, request.preferred_engine)
    if job_type in {JobType.img2img, JobType.inpaint}:
        engine = "sdxl"
    caps = capabilities()
    engine_caps = caps["engines"][engine]
    model = engine_caps["default_model"]
    width, height, preset = _aspect(text)
    prompt = text
    additions = [value for key, value in QUALITY_HINTS.items() if key in text.lower()]
    if additions:
        prompt = f"{prompt}, {', '.join(additions)}"
    safety: dict[str, object] = {"memory_context": _memory_context(text, enabled=request.use_memory)}
    if engine in {"flux", "stable_diffusion"}:
        safety["runtime_warning"] = (
            f"{engine} is available but heavy in CPU-only mode; use low steps for smoke runs "
            "or choose sdxl for quicker iteration."
        )
    guidance, cfg = _guidance(text, engine_caps["guidance_default"])
    if engine == "flux" and ((guidance or 0) != 0 or (cfg or 0) != 0):
        guidance = 0.0
        cfg = None
        safety["guidance_warning"] = "Flux adapter currently runs with guidance/cfg fixed at 0.0."

    spec = JobSpec(
        type=job_type,
        engine=engine,
        model=model,
        prompt=prompt,
        negative_prompt=_negative(text, engine_caps["supports_negative_prompt"]),
        width=width,
        height=height,
        aspect_preset=preset,
        steps=_steps(text, engine_caps["steps_default"]),
        cfg=cfg,
        guidance=guidance,
        sampler="default",
        scheduler=_scheduler(text),
        seed=_seed(text),
        upscale_factor=_upscale_factor(text),
        batch_size=_batch_size(text),
        loras=_local_loras(text),
        safety=safety,
        asset_request=_asset_request_if_needed(text),
    )
    if job_type == JobType.upscale:
        spec.engine = None
        spec.model = None
        spec.prompt = None
        spec.negative_prompt = None
        spec.safety["planner_note"] = "upscale requires source_images before execution"
        spec.safety["required_inputs"] = ["source_images"]
    elif job_type == JobType.img2img:
        spec.safety["planner_note"] = "img2img requires source_images before execution"
        spec.safety["required_inputs"] = ["source_images"]
    elif job_type == JobType.inpaint:
        spec.safety["planner_note"] = "inpaint requires source_images and mask_image before execution"
        spec.safety["required_inputs"] = ["source_images", "mask_image"]
    return spec
