from __future__ import annotations

import base64
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

VISION_BASE_URL = (
    os.environ.get("EYE_MODEL_BASE_URL")
    or os.environ.get("ARCHIVE_LLM_BASE_URL")
    or "http://127.0.0.1:8079/v1"
).rstrip("/")
if not VISION_BASE_URL.endswith("/v1"):
    VISION_BASE_URL = f"{VISION_BASE_URL}/v1"
VISION_MODEL = os.environ.get("EYE_MODEL_NAME", "gemma-4-12b-it-UD-Q5_K_XL.gguf")


def vision_review(image_path: Path, intent: str) -> dict[str, Any]:
    """Actually LOOK at the generated image with the multimodal model and judge
    it against the intent — the eyes the pipeline needs to tell a faithful
    render from a two-headed mess. Shared by the ImageVerifier and the studio
    refine loop."""
    try:
        data_uri = "data:image/png;base64," + base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
    except OSError as exc:
        return {"ok": False, "error": f"cannot read artifact: {exc}"}
    system = (
        "You are a strict image art critic for a generation pipeline. You are shown a generated image and the "
        "intended subject/prompt. Judge ONLY what you actually see. Return strict JSON: "
        '{"accept": bool, "quality": 1-10, "matches_intent": bool, '
        '"problems": ["short concrete defects: extra or duplicate parts (e.g. two heads), wrong anatomy, missing '
        'required features, wrong colors, blur, artifacts, off-subject"], '
        '"refine_instructions": "one concrete paragraph telling the next pass exactly what to fix, in English, image-prompt style"}. '
        "accept=true ONLY if the image is genuinely good AND faithfully depicts the intended subject. Be honest and harsh; "
        "a pretty image that shows the wrong thing does NOT pass."
    )
    payload = {
        "model": VISION_MODEL,
        "temperature": 0.2,
        "max_tokens": 500,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Intended subject / prompt:\n{intent[:1500]}\n\nJudge the image below."},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
    }
    try:
        request = urllib.request.Request(
            f"{VISION_BASE_URL}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-LLM-Priority": "other"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            content = str(((json.loads(response.read())["choices"] or [{}])[0].get("message") or {}).get("content") or "")
    except Exception as exc:  # noqa: BLE001 - a blind spot is worse than a soft failure
        return {"ok": False, "error": str(exc)}
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return {"ok": False, "error": "no JSON in vision response", "raw": content[:300]}
    try:
        verdict = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"bad JSON: {exc}", "raw": content[:300]}
    verdict["ok"] = True
    return verdict

try:
    from PIL import Image, ImageChops, ImageStat
except ModuleNotFoundError:  # Pillow lives in the forge venv; planners import this module without it

    class _PillowMissing:
        def __getattr__(self, name):
            raise ModuleNotFoundError("Pillow is required for image evaluation but is not installed in this interpreter")

        def open(self, *args, **kwargs):  # noqa: A003 - mirrors PIL.Image.open
            raise ModuleNotFoundError("Pillow is required for image evaluation but is not installed in this interpreter")

    Image = ImageChops = ImageStat = _PillowMissing()

from EyeOfTerror.Pictorium.Moriana.forge_runtime import config


_STOPWORDS = {
    "the",
    "and",
    "with",
    "into",
    "from",
    "that",
    "this",
    "image",
    "quality",
    "evaluation",
    "сделай",
    "нарисуй",
    "картинку",
    "изображение",
    "качественно",
}


def _image_stats(path: Path) -> dict[str, object]:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        stat = ImageStat.Stat(rgb)
        return {
            "width": rgb.width,
            "height": rgb.height,
            "mode": image.mode,
            "mean": [round(value, 3) for value in stat.mean],
            "stddev": [round(value, 3) for value in stat.stddev],
        }


def _mean_abs_difference(left: Path, right: Path) -> float:
    with Image.open(left) as left_image, Image.open(right) as right_image:
        left_rgb = left_image.convert("RGB")
        right_rgb = right_image.convert("RGB").resize(left_rgb.size)
        diff = ImageChops.difference(left_rgb, right_rgb)
        stat = ImageStat.Stat(diff)
        return round(sum(stat.mean) / len(stat.mean), 3)


def _masked_difference(left: Path, right: Path, mask: Path) -> dict[str, float]:
    with Image.open(left) as left_image, Image.open(right) as right_image, Image.open(mask) as mask_image:
        left_rgb = left_image.convert("RGB")
        right_rgb = right_image.convert("RGB").resize(left_rgb.size)
        mask_l = mask_image.convert("L").resize(left_rgb.size)
        diff = ImageChops.difference(left_rgb, right_rgb)
        masked = ImageStat.Stat(diff, mask_l)
        unmasked = ImageStat.Stat(diff, ImageChops.invert(mask_l))
        return {
            "masked": round(sum(masked.mean) / len(masked.mean), 3),
            "unmasked": round(sum(unmasked.mean) / len(unmasked.mean), 3),
        }


def _prompt_terms(prompt: str | None) -> dict[str, object]:
    if not prompt:
        return {"terms": [], "count": 0}
    terms = []
    for item in re.findall(r"[\wА-Яа-яЁё-]{4,}", prompt.lower()):
        if item not in _STOPWORDS and item not in terms:
            terms.append(item)
    return {"terms": terms[:24], "count": len(terms)}


def _edit_delta_hint(diff: float) -> dict[str, object]:
    if diff < 2.0:
        label = "very_low"
    elif diff < 8.0:
        label = "low"
    elif diff < 24.0:
        label = "moderate"
    elif diff < 48.0:
        label = "high"
    else:
        label = "very_high"
    return {
        "class": label,
        "too_low_for_visible_edit": diff < 2.0,
        "identity_loss_risk": diff > 35.0,
    }


def _inpaint_risk(region_diff: dict[str, float]) -> dict[str, object]:
    masked = region_diff["masked"]
    unmasked = region_diff["unmasked"]
    ratio = round(masked / max(unmasked, 0.001), 3)
    return {
        "masked_gt_unmasked": masked > unmasked,
        "ratio": ratio,
        "underpaint_risk": masked < 2.0,
        "overpaint_risk": unmasked > 8.0 or (masked > 0 and unmasked / max(masked, 0.001) > 0.75),
        "notes": [
            "underpaint_risk means the masked area barely changed",
            "overpaint_risk means unmasked pixels changed too much for a localized inpaint",
        ],
    }


def _local_path(value: object) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    return config.ROOT / path


def evaluate_artifact(path: Path, metadata: dict[str, Any]) -> dict[str, object]:
    prompt = metadata.get("prompt")
    source_images = [_local_path(item) for item in metadata.get("source_images") or []]
    mask_image = metadata.get("mask_image")
    dimensions = metadata.get("dimensions") or {}
    raw_spec = metadata.get("raw_spec") or {}
    expected_dimensions = dict(dimensions) if isinstance(dimensions, dict) else {}
    if raw_spec.get("type") == "upscale" and source_images and source_images[0].exists():
        with Image.open(source_images[0]) as source_image:
            factor = int(metadata.get("upscale_factor") or raw_spec.get("upscale_factor") or 1)
            expected_dimensions = {"width": source_image.width * factor, "height": source_image.height * factor}
    result: dict[str, object] = {
        "artifact_path": str(path),
        "job_id": metadata.get("job_id"),
        "job_type": raw_spec.get("type"),
        "engine": metadata.get("engine"),
        "model": metadata.get("model"),
        "quality_preset": metadata.get("quality_preset") or (metadata.get("raw_spec") or {}).get("quality_preset"),
        "requested_dimensions": dimensions,
        "expected_dimensions": expected_dimensions,
        "actual_image": _image_stats(path),
        "prompt_terms": _prompt_terms(str(prompt) if prompt else None),
        "limited_checks": [
            "No semantic vision model is used.",
            "Prompt adherence is not scored numerically.",
            "Image/edit checks are deterministic metadata and pixel statistics only.",
        ],
    }
    actual = result["actual_image"]
    if isinstance(actual, dict):
        result["dimension_match"] = {
            "ok": actual.get("width") == expected_dimensions.get("width")
            and actual.get("height") == expected_dimensions.get("height"),
            "expected": expected_dimensions,
            "actual": {"width": actual.get("width"), "height": actual.get("height")},
        }
    if source_images and source_images[0].exists():
        source_diff = _mean_abs_difference(source_images[0], path)
        result["diff_from_first_source"] = source_diff
        result["edit_delta_hint"] = _edit_delta_hint(source_diff)
    elif source_images:
        result["source_warning"] = f"source image missing: {source_images[0]}"
    if mask_image and source_images and source_images[0].exists() and _local_path(mask_image).exists():
        region_diff = _masked_difference(source_images[0], path, _local_path(mask_image))
        result["inpaint_region_diff"] = region_diff
        result["inpaint_localization_hint"] = _inpaint_risk(region_diff)
    return result
