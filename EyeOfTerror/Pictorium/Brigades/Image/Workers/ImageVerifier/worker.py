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
    it against the intent. This is the eyes the verifier never had — without it
    the brigade cannot tell a faithful render from a two-headed mess."""
    try:
        data_uri = "data:image/png;base64," + base64.b64encode(image_path.read_bytes()).decode("ascii")
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

from EyeOfTerror.Pictorium.Brigades.Image.worker_api import (
    execution_packet,
    guidance_blockers,
    require_payload,
    response,
    revision_packet,
    with_model_guidance,
    worker_model_guidance,
)
from EyeOfTerror.Pictorium.Brigades.Image.worker_api import worker_contract as base_contract
from EyeOfTerror.Pictorium.Moriana.moriana_core.image_evaluator import evaluate_artifact


WORKER = "ImageVerifier"


def worker_contract() -> dict[str, Any]:
    return base_contract(
        name=WORKER,
        role="generated artifact verifier and visual risk reporter",
        capabilities=["image_metadata_checks", "dimension_check", "edit_delta_check", "planned_artifact_manifest"],
        inputs=["artifact_path", "metadata", "job_spec", "job_record"],
        outputs=["verification", "blockers"],
    )


def _metadata_from_payload(data: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(data.get("metadata") or {}) if isinstance(data.get("metadata"), dict) else {}
    spec = data.get("job_spec") if isinstance(data.get("job_spec"), dict) else {}
    if spec:
        metadata.setdefault("prompt", spec.get("prompt"))
        metadata.setdefault("engine", spec.get("engine"))
        metadata.setdefault("model", spec.get("model"))
        metadata.setdefault("quality_preset", spec.get("quality_preset"))
        metadata.setdefault("source_images", spec.get("source_images") or [])
        metadata.setdefault("mask_image", spec.get("mask_image"))
        metadata.setdefault("dimensions", {"width": spec.get("width"), "height": spec.get("height")})
        metadata.setdefault("raw_spec", spec)
    job = data.get("job_record") if isinstance(data.get("job_record"), dict) else {}
    if job:
        metadata.setdefault("job_id", job.get("id"))
    return metadata


def verify_image(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = require_payload(payload)
    guidance = worker_model_guidance(
        WORKER,
        "generated artifact verifier and visual risk reporter",
        data,
        "Verify generated image evidence against the requested visual constraints and return structured JSON with risks and revision targets.",
    )
    model_blockers = guidance_blockers(guidance, worker=WORKER, step="image_verification")
    artifact_path = str(data.get("artifact_path") or "").strip()
    metadata = _metadata_from_payload(data)
    if not artifact_path:
        blockers = [{"code": "artifact_not_generated", "message": "no artifact_path supplied yet", "target_worker": "ForgeDispatcher", "target_step": "forge_dispatch"}, *model_blockers]
        return response(
            WORKER,
            with_model_guidance(
                {
                    "artifact": "/work/pictorium/image_verification.json",
                    "verification": {
                        "status": "planned",
                        "checks": ["dimension_match", "metadata_present", "pixel_statistics_after_generation"],
                        "metadata_preview": metadata,
                    },
                    "blockers": blockers,
                    "execution_packet": execution_packet(
                        worker=WORKER,
                        step="image_verification",
                        produced_artifacts=["/work/pictorium/image_verification.json"],
                        blockers=blockers,
                        handoff={"artifact_present": False},
                    ),
                    "revision_packet": revision_packet(
                        worker=WORKER,
                        source_step="image_verification",
                        blockers=blockers,
                        default_target_worker="ForgeDispatcher",
                        default_target_step="forge_dispatch",
                        action="wait for generated artifact or resubmit the Forge job",
                    ),
                },
                guidance,
            ),
            ok=False,
        )
    path = Path(artifact_path)
    if not path.exists():
        blockers = [{"code": "artifact_missing", "message": f"artifact does not exist: {artifact_path}", "target_worker": "ForgeDispatcher", "target_step": "forge_dispatch"}, *model_blockers]
        return response(
            WORKER,
            with_model_guidance(
                {
                    "artifact": "/work/pictorium/image_verification.json",
                    "verification": {"status": "missing", "artifact_path": artifact_path, "metadata_preview": metadata},
                    "blockers": blockers,
                    "execution_packet": execution_packet(
                        worker=WORKER,
                        step="image_verification",
                        produced_artifacts=["/work/pictorium/image_verification.json"],
                        blockers=blockers,
                        handoff={"artifact_present": False, "artifact_path": artifact_path},
                    ),
                    "revision_packet": revision_packet(
                        worker=WORKER,
                        source_step="image_verification",
                        blockers=blockers,
                        default_target_worker="ForgeDispatcher",
                        default_target_step="forge_dispatch",
                        action="locate generated artifact or resubmit the Forge job",
                    ),
                },
                guidance,
            ),
            ok=False,
        )
    verification = evaluate_artifact(path, metadata)
    blockers = []
    # The eyes: actually look at the pixels and judge them against the intent.
    intent = str(metadata.get("prompt") or "").strip()
    vision = vision_review(path, intent) if intent else {"ok": False, "error": "no prompt to judge against"}
    verification["vision_review"] = vision
    if vision.get("ok"):
        problems = [str(p) for p in (vision.get("problems") or []) if str(p).strip()]
        if not vision.get("accept") or vision.get("matches_intent") is False or int(vision.get("quality") or 0) < 6:
            blockers.append(
                {
                    "code": "vision_review_failed",
                    "message": "image does not faithfully match the intended subject or is low quality: "
                    + ("; ".join(problems[:6]) if problems else "see vision_review"),
                    "details": {"quality": vision.get("quality"), "problems": problems},
                    "target_worker": "Promptwright",
                    "target_step": "image_planning",
                    "requested_change": str(vision.get("refine_instructions") or "refine the image to match the intended subject"),
                }
            )
    dimension_match = verification.get("dimension_match") if isinstance(verification.get("dimension_match"), dict) else {}
    if dimension_match and not dimension_match.get("ok"):
        blockers.append(
            {
                "code": "dimension_mismatch",
                "message": "artifact dimensions do not match requested dimensions",
                "details": dimension_match,
                "target_worker": "Promptwright",
                "target_step": "image_planning",
                "requested_change": "regenerate with dimensions matching the job_spec",
            }
        )
    blockers = [*blockers, *model_blockers]
    return response(
        WORKER,
        with_model_guidance(
            {
                "artifact": "/work/pictorium/image_verification.json",
                "verification": verification,
                "blockers": blockers,
                "execution_packet": execution_packet(
                    worker=WORKER,
                    step="image_verification",
                    produced_artifacts=["/work/pictorium/image_verification.json"],
                    next_steps=[] if blockers else ["finalize"],
                    blockers=blockers,
                    handoff={"artifact_present": True, "artifact_path": artifact_path},
                ),
                "revision_packet": revision_packet(
                    worker=WORKER,
                    source_step="image_verification",
                    blockers=blockers,
                    default_target_worker="Promptwright",
                    default_target_step="image_planning",
                    action="regenerate or adjust prompt/parameters until verification passes",
                ),
            },
            guidance,
        ),
        ok=not blockers,
    )


def handle(payload: dict[str, Any] | None) -> dict[str, Any]:
    return verify_image(payload)

def run(request, workspace_root=None):
    """HTTP worker-launcher entrypoint: the LegacyMechanicum server calls
    run(request, workspace_root); the image brigade's logic lives in handle().
    After handling, materialise declared artifacts so the next step's input
    preflight passes."""
    try:
        from EyeOfTerror.Pictorium.Brigades.Image.worker_api import inject_input_artifacts, persist_expected_artifacts
        inject_input_artifacts(request, workspace_root)
    except Exception as exc:  # noqa: BLE001
        print(f'artifact inject failed: {exc}', flush=True)
    result = handle(request)
    try:
        persist_expected_artifacts(request, workspace_root, result)
    except Exception as exc:  # noqa: BLE001
        print(f'artifact persist failed: {exc}', flush=True)
    return result
