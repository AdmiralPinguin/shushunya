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
from EyeOfTerror.Pictorium.testing.fake_model_server import fake_pictorium_model


def assert_execution_packet(payload: dict[str, object], worker: str) -> None:
    packet = payload.get("execution_packet") if isinstance(payload.get("execution_packet"), dict) else {}
    if packet.get("kind") != "pictorium_worker_execution_packet" or packet.get("worker") != worker:
        raise AssertionError(f"{worker} did not return execution_packet: {payload}")


def assert_revision_packet(payload: dict[str, object], worker: str) -> None:
    packet = payload.get("revision_packet") if isinstance(payload.get("revision_packet"), dict) else {}
    if packet.get("kind") != "pictorium_revision_packet" or packet.get("source_worker") != worker:
        raise AssertionError(f"{worker} did not return revision_packet: {payload}")


def assert_model_guidance(payload: dict[str, object], worker: str) -> None:
    guidance = payload.get("model_guidance") if isinstance(payload.get("model_guidance"), dict) else {}
    if guidance.get("kind") != "pictorium_worker_model_guidance" or guidance.get("worker") != worker or guidance.get("status") != "answered":
        raise AssertionError(f"{worker} did not return answered model_guidance: {payload}")
    if not isinstance(guidance.get("decision"), dict) or not guidance.get("decision"):
        raise AssertionError(f"{worker} model_guidance did not contain structured decision: {payload}")


def _main() -> int:
    plan = prepare_image_plan({"request": "smoke test image 512x512", "use_memory": False, "use_thinker": False})
    if not plan.get("ok") or plan.get("plan_kind") != "job":
        raise AssertionError(f"Promptwright failed: {plan}")
    assert_execution_packet(plan, "Promptwright")
    assert_model_guidance(plan, "Promptwright")
    spec = plan["job_spec"]
    if spec.get("width") != 512 or spec.get("height") != 512:
        raise AssertionError(f"Promptwright dimensions failed: {spec}")
    long_plan = prepare_image_plan(
        {
            "request": (
                "cinematic comic panel, ancient forge, tech-priest, ritual altar, "
                "same character, same style, no text, no speech bubbles, dramatic smoke, "
                "red light, brass machinery, readable composition, detailed background, "
                "continuity reference, establishing shot, 512x512 steps 8"
            ),
            "use_memory": False,
            "use_thinker": False,
        }
    )
    long_spec = long_plan.get("job_spec", {}) if isinstance(long_plan.get("job_spec"), dict) else {}
    compaction = long_spec.get("safety", {}).get("prompt_compaction", {}) if isinstance(long_spec.get("safety"), dict) else {}
    if (
        long_spec.get("width") != 512
        or long_spec.get("height") != 512
        or long_spec.get("steps") != 8
        or "512x512" in str(long_spec.get("prompt") or "")
        or "steps 8" in str(long_spec.get("prompt") or "").lower()
        or compaction.get("kind") != "prompt_compaction"
    ):
        raise AssertionError(f"Promptwright prompt compaction failed: {long_spec}")

    resources = inspect_resources({"job_spec": spec})
    if "capabilities" not in resources or "resource_report" not in resources:
        raise AssertionError(f"ModelQuartermaster failed: {resources}")
    assert_execution_packet(resources, "ModelQuartermaster")
    assert_revision_packet(resources, "ModelQuartermaster")
    assert_model_guidance(resources, "ModelQuartermaster")

    with tempfile.TemporaryDirectory(prefix="pictorium-image-self-test-") as tmp:
        dispatch = prepare_dispatch({"job_spec": spec, "submit": True, "db_path": str(Path(tmp) / "forge.sqlite3")})
        if not dispatch.get("ok") or not dispatch.get("dispatch", {}).get("valid") or not dispatch.get("job_record"):
            raise AssertionError(f"ForgeDispatcher failed: {dispatch}")
        assert_execution_packet(dispatch, "ForgeDispatcher")
        assert_revision_packet(dispatch, "ForgeDispatcher")
        assert_model_guidance(dispatch, "ForgeDispatcher")

        planned_verification = verify_image({"job_spec": spec, "job_record": dispatch["job_record"]})
        if planned_verification.get("ok") or planned_verification.get("blockers", [{}])[0].get("code") != "artifact_not_generated":
            raise AssertionError(f"ImageVerifier planned state failed: {planned_verification}")
        assert_execution_packet(planned_verification, "ImageVerifier")
        assert_revision_packet(planned_verification, "ImageVerifier")
        assert_model_guidance(planned_verification, "ImageVerifier")

        image_path = Path(tmp) / "artifact.png"
        Image.new("RGB", (512, 512), (20, 30, 40)).save(image_path)
        verification = verify_image({"artifact_path": str(image_path), "job_spec": spec, "job_record": dispatch["job_record"]})
        if not verification.get("ok") or not verification.get("verification", {}).get("dimension_match", {}).get("ok"):
            raise AssertionError(f"ImageVerifier concrete artifact failed: {verification}")
        assert_execution_packet(verification, "ImageVerifier")
        assert_revision_packet(verification, "ImageVerifier")
        assert_model_guidance(verification, "ImageVerifier")

        final = build_final_manifest({"plan": plan, "resources": resources, "dispatch": dispatch, "verification": verification})
        if not final.get("ok") or final.get("final_manifest", {}).get("status") != "ready":
            raise AssertionError(f"ArtifactFinalis failed: {final}")
        assert_execution_packet(final, "ArtifactFinalis")
        assert_revision_packet(final, "ArtifactFinalis")
        assert_model_guidance(final, "ArtifactFinalis")

    print("[ok] Pictorium Image Brigade workers")
    return 0


def main() -> int:
    with fake_pictorium_model():
        return _main()


if __name__ == "__main__":
    raise SystemExit(main())
