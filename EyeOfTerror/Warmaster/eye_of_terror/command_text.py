from __future__ import annotations

from typing import Any


def task_text_from_commander_order(commander_order: dict[str, Any]) -> str:
    """Compact transport task for governors.

    The embedded commander_order is the authority. This string exists only for
    legacy governor code that still requires a task field.
    """
    task = str(commander_order.get("primary_goal") or commander_order.get("commander_intent") or "").strip()
    constraints = [
        item.strip()
        for item in commander_order.get("constraints", [])
        if isinstance(item, str) and item.strip()
    ]
    if constraints:
        constraint_text = "Ограничения приказа:\n" + "\n".join(f"- {item}" for item in constraints)
        return f"{task}\n\n{constraint_text}" if task else constraint_text
    return task
