from __future__ import annotations

from typing import Any


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit // 2] + "\n...[truncated]...\n" + text[-limit // 2 :]


def compact_json_value(value: Any, string_limit: int = 4000, list_limit: int = 40, depth: int = 0) -> Any:
    if depth > 6:
        return truncate(str(value), string_limit)
    if isinstance(value, str):
        return truncate(value, string_limit)
    if isinstance(value, list):
        compacted = [compact_json_value(item, string_limit, list_limit, depth + 1) for item in value[:list_limit]]
        if len(value) > list_limit:
            compacted.append({"truncated_items": len(value) - list_limit})
        return compacted
    if isinstance(value, dict):
        return {str(key): compact_json_value(item, string_limit, list_limit, depth + 1) for key, item in value.items()}
    return value
