#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WARM_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(WARM_ROOT) not in sys.path:
    sys.path.insert(0, str(WARM_ROOT))

from EyeOfTerror.common_protocol import validate_protocol_payload
from eye_of_terror.mission_control import build_commander_order, governor_task_from_order


FORBIDDEN_PLAN_KEYS = {
    "work_plan",
    "worker_plan",
    "steps",
    "quality_gates",
    "expected_deliverables",
}


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    elif "{" in stripped and "}" in stripped:
        stripped = stripped[stripped.find("{") : stripped.rfind("}") + 1]
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise AssertionError(f"model content is not a JSON object: {text!r}")
    return parsed


def require_live_model_brain(payload: dict[str, Any], owner: str) -> None:
    model_brain = payload.get("model_brain") if isinstance(payload.get("model_brain"), dict) else {}
    if model_brain.get("owner") != owner or model_brain.get("status") != "answered":
        raise AssertionError(f"{owner} did not answer through live model brain: {payload}")


def main() -> int:
    mission_id = "mission-live-commander-self-test"
    message = (
        "Создай новый python CLI проект с тестами и документацией. "
        "Вармастер должен только оформить приказ бригадиру, без детального плана реализации."
    )
    result = build_commander_order(message, mission_id)
    if not result.get("ok"):
        raise AssertionError(f"WarmasterCommander failed to build commander_order: {result}")

    route = result.get("route") if isinstance(result.get("route"), dict) else {}
    require_live_model_brain(route, "WarmasterRouter")
    if route.get("governor") != "Ceraxia" or route.get("kind") != "code":
        raise AssertionError(f"live router did not select Ceraxia/code for a code project task: {route}")

    require_live_model_brain(result, "WarmasterCommander")
    model_brain = result["model_brain"]
    model_payload = extract_json_object(str(model_brain.get("content") or ""))
    forbidden_model_keys = sorted(FORBIDDEN_PLAN_KEYS.intersection(model_payload))
    if forbidden_model_keys:
        raise AssertionError(f"WarmasterCommander returned governor planning keys: {forbidden_model_keys}")

    order = result.get("commander_order") if isinstance(result.get("commander_order"), dict) else {}
    validate_protocol_payload(order, expected_type="commander_order")
    if order.get("mission_id") != mission_id:
        raise AssertionError(f"commander_order mission_id drifted: {order}")
    if order.get("from") != "Warmaster" or order.get("to") != "Ceraxia":
        raise AssertionError(f"commander_order authority boundary drifted: {order}")
    for field in ("commander_intent", "primary_goal"):
        if not str(order.get(field) or "").strip():
            raise AssertionError(f"commander_order missing {field}: {order}")
    if not order.get("success_conditions"):
        raise AssertionError(f"commander_order missing success_conditions: {order}")
    if order.get("reporting_policy", {}).get("revision_is_internal") is not True:
        raise AssertionError(f"commander_order does not keep revision internal: {order}")
    forbidden_order_keys = sorted(FORBIDDEN_PLAN_KEYS.intersection(order))
    if forbidden_order_keys:
        raise AssertionError(f"commander_order leaked governor planning keys: {forbidden_order_keys}")

    governor_task = governor_task_from_order(order)
    if not governor_task.strip():
        raise AssertionError(f"governor transport task is empty for order: {order}")
    if governor_task.startswith("ПРИКАЗ ВАРМАСТЕРА") or "Исходный запрос пользователя:" in governor_task:
        raise AssertionError(f"governor transport task leaked raw command wrapper: {governor_task!r}")
    if str(order.get("primary_goal") or "").strip() not in governor_task:
        raise AssertionError(f"governor transport task does not carry primary_goal: {governor_task!r}")

    print("[ok] Warmaster live commander order")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
