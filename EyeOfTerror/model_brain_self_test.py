#!/usr/bin/env python3
from __future__ import annotations

import os

from model_brain import model_contract, model_settings, request_model_decision


def main() -> int:
    original_enabled = os.environ.get("EYE_MODEL_ENABLED")
    try:
        os.environ["EYE_MODEL_ENABLED"] = "0"
        settings = model_settings()
        if settings["enabled"]:
            raise AssertionError(f"model settings should be disabled by explicit env: {settings}")
        contract = model_contract("FixtureWorker", "fixture role")
        if contract.get("kind") != "eye_of_terror_model_brain" or contract.get("required_for_autonomous_mode") is not True:
            raise AssertionError(f"bad model contract: {contract}")
        decision = request_model_decision("FixtureWorker", "fixture role", {"task_id": "fixture", "task": "do the thing"})
        if decision.get("status") != "disabled" or "EYE_MODEL_ENABLED=1" not in decision.get("error", ""):
            raise AssertionError(f"disabled model decision should be explicit: {decision}")
        os.environ["EYE_MODEL_ENABLED"] = "1"
        os.environ["EYE_MODEL_BASE_URL"] = "http://127.0.0.1:9/v1"
        os.environ["EYE_MODEL_TIMEOUT_SEC"] = "1"
        unavailable = request_model_decision("FixtureWorker", "fixture role", {"task_id": "fixture", "task": "do the thing"})
        if unavailable.get("status") not in {"unavailable", "error"} or not unavailable.get("error"):
            raise AssertionError(f"unavailable model decision should fail soft: {unavailable}")
    finally:
        if original_enabled is None:
            os.environ.pop("EYE_MODEL_ENABLED", None)
        else:
            os.environ["EYE_MODEL_ENABLED"] = original_enabled
        os.environ.pop("EYE_MODEL_BASE_URL", None)
        os.environ.pop("EYE_MODEL_TIMEOUT_SEC", None)
    print("[ok] EyeOfTerror model brain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
