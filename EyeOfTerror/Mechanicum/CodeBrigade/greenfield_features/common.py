from __future__ import annotations

import re
from typing import Any


def task_tokens(task: str) -> set[str]:
    return set(re.findall(r"[a-zа-яё0-9/_-]+", task.lower()))

def calculator_requested(task: str) -> bool:
    lowered = task.lower()
    tokens = task_tokens(task)
    english_markers = ("calculator", "calculate", "arithmetic", "add ", "subtract", "multiply", "divide")
    russian_tokens = {
        "калькулятор",
        "арифметика",
        "арифметический",
        "сложение",
        "сложить",
        "вычитание",
        "вычесть",
        "умножение",
        "умножить",
        "деление",
        "делить",
    }
    return any(marker in lowered for marker in english_markers) or bool(tokens & russian_tokens)

def replace_project_file(files: list[Any], rel_path: str, content: str) -> list[Any]:
    replaced = False
    rows: list[Any] = []
    for item in files:
        if isinstance(item, dict) and item.get("path") == rel_path:
            rows.append({"path": rel_path, "content": content})
            replaced = True
        else:
            rows.append(item)
    if not replaced:
        rows.append({"path": rel_path, "content": content})
    return rows
