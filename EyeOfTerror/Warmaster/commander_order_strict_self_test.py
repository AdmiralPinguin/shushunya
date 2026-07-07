#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
WARM_ROOT = ROOT / "Warmaster"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(WARM_ROOT) not in sys.path:
    sys.path.insert(0, str(WARM_ROOT))

from EyeOfTerror.common_protocol import commander_order
import eye_of_terror.task_prepare as task_prepare_module
from eye_of_terror.mission_control import governor_task_from_order
from eye_of_terror.task_prepare import governor_payload_for, prepare_task, preflight_task


def main() -> int:
    order = commander_order(
        "mission-strict-self-test",
        to="IskandarKhayon",
        user_request="Исследуй Скалатракс и собери источники.",
        commander_intent="Проверить, что боевой путь идет через приказ Вармастера.",
        primary_goal="Подготовить исследовательский план через бригадира.",
        success_conditions=["подготовка использует commander_order", "путь без commander_order не считается strict-протоколом"],
    )
    governor_task = governor_task_from_order(order)
    if governor_task != order.get("primary_goal") or governor_task.startswith("ПРИКАЗ ВАРМАСТЕРА"):
        raise AssertionError(f"governor_task_from_order did not stay compact protocol transport text: {governor_task!r}")
    with TemporaryDirectory() as tmp:
        run_root = Path(tmp)
        missing_preflight = preflight_task(
            "Исследуй Скалатракс.",
            "strict-missing-preflight",
            run_root,
            governor_transport="local",
            forced_governor="IskandarKhayon",
            require_commander_order=True,
        )
        if missing_preflight.get("error_code") != "commander_order_required":
            raise AssertionError(f"strict preflight did not require commander_order: {missing_preflight}")
        missing_prepare = prepare_task(
            "Исследуй Скалатракс.",
            "strict-missing-prepare",
            run_root,
            governor_transport="local",
            forced_governor="IskandarKhayon",
            require_commander_order=True,
        )
        if missing_prepare.get("error_code") != "commander_order_required":
            raise AssertionError(f"strict prepare did not require commander_order: {missing_prepare}")
        governor_payload = governor_payload_for(
            "ПРИКАЗ ВАРМАСТЕРА\nИсходный запрос пользователя:\nсырой текст не должен быть задачей",
            "strict-protocol-test",
            commander_order=order,
        )
        if (
            governor_payload.get("task") != order.get("primary_goal")
            or str(governor_payload.get("task") or "").startswith("ПРИКАЗ ВАРМАСТЕРА")
            or governor_payload.get("commander_order") != order
        ):
            raise AssertionError(f"governor payload did not stay commander_order-first: {governor_payload}")
        original_route_message = task_prepare_module.route_message
        task_prepare_module.route_message = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("forced commander path must not call route_message"))
        try:
            strict_preflight = preflight_task(
                "Исследуй Скалатракс.",
                "strict-with-order-preflight",
                run_root,
                governor_transport="local",
                forced_governor="IskandarKhayon",
                commander_order=order,
                require_commander_order=True,
            )
        finally:
            task_prepare_module.route_message = original_route_message
        if not strict_preflight.get("ok") or strict_preflight.get("protocol_mode") != "commander_order":
            raise AssertionError(f"strict preflight did not use commander_order mode: {strict_preflight}")
        if strict_preflight.get("route", {}).get("source") != "forced_governor":
            raise AssertionError(f"strict preflight did not expose commander route source: {strict_preflight}")
        missing_order_preflight = preflight_task(
            "Исследуй Скалатракс.",
            "strict-missing-order-preflight",
            run_root,
            governor_transport="local",
            forced_governor="IskandarKhayon",
        )
        if missing_order_preflight.get("protocol_mode") != "commander_order_missing":
            raise AssertionError(f"preflight without commander_order was not marked: {missing_order_preflight}")
    print("[ok] Warmaster commander-order strict gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
