from __future__ import annotations

from typing import Any

from EyeOfTerror.Pictorium.Brigades.Image.worker_api import require_payload, response
from EyeOfTerror.Pictorium.Brigades.Image.worker_api import worker_contract as base_contract
from EyeOfTerror.Pictorium.Moriana.moriana_core.asset_catalog import asset_profiles, capabilities


WORKER = "ModelQuartermaster"


def worker_contract() -> dict[str, Any]:
    return base_contract(
        name=WORKER,
        role="model, LoRA, embedding, and asset readiness inspector",
        capabilities=["capability_report", "model_readiness", "asset_profile_report", "structured_blockers"],
        inputs=["job_spec", "project_spec"],
        outputs=["capabilities", "resource_report", "blockers"],
    )


def _engine_report(caps: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    engine = str(spec.get("engine") or caps.get("defaults", {}).get("engine") or "stable_diffusion")
    engine_caps = caps.get("engines", {}).get(engine, {}) if isinstance(caps.get("engines"), dict) else {}
    model_name = str(spec.get("model") or engine_caps.get("default_model") or "")
    local_model = next((item for item in caps.get("models", []) if item.get("name") == model_name), None)
    blockers: list[dict[str, Any]] = []
    if not engine_caps:
        blockers.append({"code": "unknown_engine", "message": f"unknown image engine: {engine}"})
    elif not engine_caps.get("available"):
        blockers.append({"code": "engine_model_missing", "message": f"default model is not available locally for {engine}"})
    if local_model and not local_model.get("available"):
        blockers.append({"code": "model_missing", "message": f"model is not available locally: {model_name}", "model": model_name})
    if spec.get("loras") and not engine_caps.get("supports_lora"):
        blockers.append({"code": "lora_unsupported", "message": f"{engine} does not support LoRA in this runtime"})
    if spec.get("negative_prompt") and not engine_caps.get("supports_negative_prompt"):
        blockers.append({"code": "negative_prompt_unsupported", "message": f"{engine} does not support negative_prompt"})
    asset_request = spec.get("asset_request") if isinstance(spec.get("asset_request"), dict) else None
    if asset_request and asset_request.get("requires_user_approval"):
        blockers.append({"code": "asset_approval_required", "message": "job has unresolved asset_request", "asset_request": asset_request})
    return {
        "engine": engine,
        "model": model_name,
        "engine_available": bool(engine_caps.get("available")),
        "local_model": local_model,
        "blockers": blockers,
    }


def inspect_resources(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = require_payload(payload)
    caps = capabilities()
    spec = data.get("job_spec") if isinstance(data.get("job_spec"), dict) else {}
    if not spec and isinstance(data.get("project_spec"), dict):
        steps = data["project_spec"].get("steps") if isinstance(data["project_spec"].get("steps"), list) else []
        if steps and isinstance(steps[0], dict):
            spec = steps[0].get("spec") if isinstance(steps[0].get("spec"), dict) else {}
    engine_report = _engine_report(caps, spec)
    report = {
        "service": caps.get("service"),
        "version": caps.get("version"),
        "job_types": caps.get("job_types", []),
        "engine": engine_report,
        "model_count": len(caps.get("models", [])),
        "available_models": [item for item in caps.get("models", []) if item.get("available")],
        "lora_count": len(caps.get("loras", [])),
        "embedding_count": len(caps.get("embeddings", [])),
        "asset_profile_count": len(asset_profiles().get("profiles", [])),
    }
    return response(
        WORKER,
        {
            "artifact": "/work/pictorium/resource_report.json",
            "capabilities": caps,
            "resource_report": report,
            "blockers": engine_report["blockers"],
        },
        ok=not engine_report["blockers"],
    )


def handle(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return inspect_resources(payload)
