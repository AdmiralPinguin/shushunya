#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from EyeOfTerror.common_protocol import commander_order
from EyeOfTerror.Pictorium.Moriana.moriana_governor import plan_actions as moriana_plan_actions
from EyeOfTerror.Pictorium.Moriana.moriana_governor import task_from_payload as moriana_task_from_payload
from EyeOfTerror.Warmaster.eye_of_terror.inner_circle.ceraxia import plan_actions as ceraxia_plan_actions
from EyeOfTerror.Warmaster.eye_of_terror.inner_circle.ceraxia_service import task_from_payload as ceraxia_task_from_payload
from EyeOfTerror.Warmaster.eye_of_terror.inner_circle.iskandar import plan_actions as iskandar_plan_actions
from EyeOfTerror.Warmaster.eye_of_terror.inner_circle.iskandar_service import task_from_payload as iskandar_task_from_payload


def _order(mission_id: str, to: str, goal: str) -> dict:
    return commander_order(
        mission_id,
        to=to,
        user_request="raw user request",
        commander_intent=goal,
        primary_goal=goal,
        success_conditions=["use the commander order as the task authority"],
    )


def _assert_commander_order_authority(name: str, parser, to: str) -> None:
    goal = f"{name} commander goal"
    task, command = parser(
        {
            "task": "raw override must be ignored",
            "message": "message override must be ignored",
            "request": "request override must be ignored",
            "commander_order": _order(f"mission-{name}", to, goal),
        }
    )
    if command.get("primary_goal") != goal:
        raise AssertionError(f"{name} did not return the commander order: {command}")
    if "raw override" in task or "message override" in task or "request override" in task or goal not in task:
        raise AssertionError(f"{name} used a non-authoritative task field: {task!r}")


def _assert_action_body_has_no_raw_task(name: str, actions: dict) -> None:
    body = actions.get("next_action", {}).get("body", {})
    if "task" in body:
        raise AssertionError(f"{name} advertises raw task body in next_action: {body}")
    if body.get("commander_order") != "<same commander_order used for /plan>":
        raise AssertionError(f"{name} does not advertise commander_order handoff: {body}")


def main() -> int:
    _assert_commander_order_authority("iskandar", iskandar_task_from_payload, "IskandarKhayon")
    _assert_commander_order_authority("ceraxia", ceraxia_task_from_payload, "Ceraxia")
    _assert_commander_order_authority("moriana", moriana_task_from_payload, "Moriana")
    _assert_action_body_has_no_raw_task("iskandar", iskandar_plan_actions({"goal": "x", "task_id": "x"}, True, [], [], []))
    _assert_action_body_has_no_raw_task("ceraxia", ceraxia_plan_actions({"goal": "x", "task_id": "x"}, True, [], [], []))
    _assert_action_body_has_no_raw_task("moriana", moriana_plan_actions({"goal": "x", "task_id": "x"}, True, [], {}))
    print("[ok] Governor commander_order authority")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
