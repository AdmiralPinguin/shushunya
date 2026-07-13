#!/usr/bin/env python3
"""Focused contract for the canonical Warmaster mission-state roster view."""
from __future__ import annotations

import vox_service


def main() -> int:
    original_get_json = vox_service._get_json

    def fake_get_json(_url: str, timeout: float = 15.0) -> dict:  # noqa: ARG001
        return {
            "runs": [
                {
                    "task_id": "task-needs-user",
                    "goal": "Исходный запрос пользователя: Выбери движок для игры",
                    "governor": "Ceraxia",
                    # Deliberately stale compatibility fields: mission_state is
                    # the authoritative typed decision view.
                    "status": "running",
                    "mission_status": "executing",
                    "mission_state": {
                        "status": "blocked",
                        "needs_user": True,
                        "user_visible_state": "needs_user_decision",
                        "next_owner": "user",
                    },
                },
                {
                    "task_id": "task-internal-block",
                    "goal": "Исходный запрос пользователя: Почини приложение",
                    "governor": "Ceraxia",
                    "status": "blocked",
                    "mission_status": "blocked",
                    "mission_state": {
                        "status": "blocked",
                        "needs_user": False,
                        "user_visible_state": "internal_repair_required",
                        "next_owner": "governor",
                    },
                },
            ],
            "process_active_runs": [],
        }

    try:
        vox_service._get_json = fake_get_json
        roster = vox_service.task_roster()
    finally:
        vox_service._get_json = original_get_json

    if roster.get("ok") is not True:
        raise AssertionError(f"roster lookup failed: {roster}")
    tasks = {task.get("task_id"): task for task in roster.get("tasks") or []}

    needs_user = tasks.get("task-needs-user") or {}
    if (
        needs_user.get("state") != "needs_user"
        or needs_user.get("needs_user") is not True
        or needs_user.get("user_visible_state") != "needs_user_decision"
        or needs_user.get("next_owner") != "user"
    ):
        raise AssertionError(f"typed user decision was flattened or lost: {needs_user}")

    internal = tasks.get("task-internal-block") or {}
    if (
        internal.get("state") != "blocked"
        or internal.get("needs_user") is not False
        or internal.get("user_visible_state") != "internal_repair_required"
        or internal.get("next_owner") != "governor"
    ):
        raise AssertionError(f"internal block was misreported as a user decision: {internal}")

    print("vox task roster self-test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
