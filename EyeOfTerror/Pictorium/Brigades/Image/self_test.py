#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image

from EyeOfTerror.Pictorium.Brigades.Image.Workers.ArtifactFinalis.worker import build_final_manifest
from EyeOfTerror.Pictorium.Brigades.Image.Workers.ForgeDispatcher.worker import prepare_dispatch
from EyeOfTerror.Pictorium.Brigades.Image.Workers.ImageVerifier.worker import verify_image
from EyeOfTerror.Pictorium.Brigades.Image.Workers.ModelQuartermaster.worker import inspect_resources
from EyeOfTerror.Pictorium.Brigades.Image.Workers.Promptwright.worker import prepare_image_plan


def main() -> int:
    plan = prepare_image_plan({"request": "smoke test image 512x512", "use_memory": False, "use_thinker": False})
    if not plan.get("ok") or plan.get("plan_kind") != "job":
        raise AssertionError(f"Promptwright failed: {plan}")
    spec = plan["job_spec"]
    if spec.get("width") != 512 or spec.get("height") != 512:
        raise AssertionError(f"Promptwright dimensions failed: {spec}")

    resources = inspect_resources({"job_spec": spec})
    if "capabilities" not in resources or "resource_report" not in resources:
        raise AssertionError(f"ModelQuartermaster failed: {resources}")

    with tempfile.TemporaryDirectory(prefix="pictorium-image-self-test-") as tmp:
        dispatch = prepare_dispatch({"job_spec": spec, "submit": True, "db_path": str(Path(tmp) / "forge.sqlite3")})
        if not dispatch.get("ok") or not dispatch.get("dispatch", {}).get("valid") or not dispatch.get("job_record"):
            raise AssertionError(f"ForgeDispatcher failed: {dispatch}")

        planned_verification = verify_image({"job_spec": spec, "job_record": dispatch["job_record"]})
        if planned_verification.get("ok") or planned_verification.get("blockers", [{}])[0].get("code") != "artifact_not_generated":
            raise AssertionError(f"ImageVerifier planned state failed: {planned_verification}")

        image_path = Path(tmp) / "artifact.png"
        Image.new("RGB", (512, 512), (20, 30, 40)).save(image_path)
        verification = verify_image({"artifact_path": str(image_path), "job_spec": spec, "job_record": dispatch["job_record"]})
        if not verification.get("ok") or not verification.get("verification", {}).get("dimension_match", {}).get("ok"):
            raise AssertionError(f"ImageVerifier concrete artifact failed: {verification}")

        final = build_final_manifest({"plan": plan, "resources": resources, "dispatch": dispatch, "verification": verification})
        if not final.get("ok") or final.get("final_manifest", {}).get("status") != "ready":
            raise AssertionError(f"ArtifactFinalis failed: {final}")

    print("[ok] Pictorium Image Brigade workers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
