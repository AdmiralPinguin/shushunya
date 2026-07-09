"""Moriana studio: the multi-stage 'make a good image' loop.

understand -> FLUX draft -> the model LOOKS and judges -> if not good, SDXL
img2img refine using the judge's concrete instructions -> look again -> keep
the best. This turns the three proven capabilities (art-directed prompt, SDXL
img2img refine, vision verification) into one closed loop, independent of the
fragile mission machinery so it can be tested and driven directly.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from EyeOfTerror.Pictorium.Moriana.forge_runtime.queue import ForgeQueue
from EyeOfTerror.Pictorium.Moriana.forge_runtime.schemas import JobSpec, PlanRequest
from EyeOfTerror.Pictorium.Moriana.forge_runtime.storage import ForgeStore
from EyeOfTerror.Pictorium.Moriana.moriana_core.image_evaluator import vision_review
from EyeOfTerror.Pictorium.Moriana.moriana_core.promptwright import plan_txt2img

FORGE_DB = "DemonsForge/runtime/forge.sqlite3"


def _wait_for_image(store: ForgeStore, job_id: str, timeout_sec: int = 1500) -> str | None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        time.sleep(5)
        job = store.get_job(job_id)
        status = str(getattr(job, "status", "") or "")
        if "succeeded" in status or "completed" in status:
            conn = sqlite3.connect(FORGE_DB)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT path FROM artifacts WHERE job_id=? AND kind='image'", (job_id,)).fetchone()
            conn.close()
            return row["path"] if row else None
        if "failed" in status:
            return None
    return None


def produce_refined_image(
    intent: str,
    max_refine: int = 1,
    loras: list[dict[str, Any]] | None = None,
    lora_style: str | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Draft in FLUX, judge with vision, refine in SDXL img2img on the judge's
    notes, judge again, keep the best. Returns the best image path and the trail.

    If lora_style is given, autonomously fetch a matching SDXL LoRA and apply it
    in the refine pass (full autonomy: HuggingFace, SDXL-only, size-capped)."""
    store = ForgeStore()
    queue = ForgeQueue(store, start_worker=True)

    loras = list(loras or [])
    if lora_style:
        try:
            from EyeOfTerror.Pictorium.Moriana.moriana_core.lora_scout import acquire_lora  # noqa: PLC0415

            got = acquire_lora(lora_style)
            if got:
                loras.append({"name": got["name"], "weight": 0.7})
                log(f"[studio] LoRA acquired for '{lora_style}': {got['repo']}")
            else:
                log(f"[studio] no SDXL LoRA found for '{lora_style}', continuing without")
        except Exception as exc:  # noqa: BLE001 - a LoRA miss must not sink the render
            log(f"[studio] LoRA scout failed: {exc}")

    # 1. understand + art-directed draft prompt (Promptwright thinker), FLUX draft
    plan = plan_txt2img(PlanRequest(request=intent, use_thinker=True, use_memory=False))
    prompt = plan.prompt or intent
    draft_spec = JobSpec(engine="flux", model="FLUX.1-schnell", type="txt2img", prompt=prompt, width=832, height=832, steps=4)
    log(f"[studio] FLUX draft: {prompt[:120]}...")
    draft_path = _wait_for_image(store, queue.submit(draft_spec).id)
    if not draft_path:
        return {"ok": False, "error": "draft generation failed"}

    best = {"path": draft_path, "verdict": vision_review(Path(draft_path), intent), "stage": "flux_draft"}
    trail = [{"stage": "flux_draft", "path": draft_path, "verdict": best["verdict"]}]
    log(f"[studio] draft verdict: accept={best['verdict'].get('accept')} quality={best['verdict'].get('quality')}")

    # 2. improve loop. Route by the KIND of problem the judge saw:
    #  - structural (two heads, duplicate/extra parts, wrong count): img2img
    #    preserves the flawed composition, so REGENERATE a fresh FLUX draft with
    #    a hard anti-duplication boost.
    #  - detail/quality: SDXL img2img refine on the judge's notes.
    current = draft_path
    for i in range(max_refine):
        verdict = trail[-1]["verdict"]
        if verdict.get("ok") and verdict.get("accept") and int(verdict.get("quality") or 0) >= 8:
            break  # already good, no point spending a pass
        problems_text = " ".join(str(p) for p in (verdict.get("problems") or [])).lower()
        fixes = str(verdict.get("refine_instructions") or "").strip()
        structural = any(
            token in problems_text
            for token in ("two head", "second head", "extra head", "duplicate", "two face", "two creature", "second face", "extra limb", "extra eye")
        )
        if structural:
            boosted = (
                f"{prompt} ABSOLUTELY ONE single head and one face only, a single fused creature, "
                "no second head, no duplicate head on the neck or back, one body."
            )
            spec = JobSpec(engine="flux", model="FLUX.1-schnell", type="txt2img", prompt=boosted, width=832, height=832, steps=4)
            stage = f"flux_regen_{i + 1}"
            log(f"[studio] structural defect ({problems_text[:60]}...) -> fresh FLUX regen with anti-duplication")
        else:
            spec = JobSpec(
                engine="sdxl", model="stable-diffusion-xl-base-1.0", type="img2img",
                prompt=f"{prompt} {fixes}".strip(), source_images=[current],
                strength=0.42, width=1024, height=1024, steps=16, loras=loras,
            )
            stage = f"sdxl_refine_{i + 1}"
            log(f"[studio] SDXL refine {i + 1} on fixes: {fixes[:100]}...")
        new_path = _wait_for_image(store, queue.submit(spec).id)
        if not new_path:
            log("[studio] pass failed, keeping best so far")
            break
        new_verdict = vision_review(Path(new_path), intent)
        trail.append({"stage": stage, "path": new_path, "verdict": new_verdict})
        log(f"[studio] {stage} verdict: accept={new_verdict.get('accept')} quality={new_verdict.get('quality')}")
        current = new_path
        # Prefer accepted images; among equals, higher quality wins.
        best_score = (bool((best["verdict"] or {}).get("accept")), int((best["verdict"] or {}).get("quality") or 0))
        new_score = (bool(new_verdict.get("accept")), int(new_verdict.get("quality") or 0))
        if new_score >= best_score:
            best = {"path": new_path, "verdict": new_verdict, "stage": stage}

    return {"ok": True, "best_path": best["path"], "best_stage": best["stage"], "best_verdict": best["verdict"], "trail": trail}
