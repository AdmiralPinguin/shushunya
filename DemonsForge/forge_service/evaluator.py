from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat

from . import config


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
