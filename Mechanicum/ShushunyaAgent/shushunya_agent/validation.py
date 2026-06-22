from __future__ import annotations

from collections.abc import Mapping
from typing import Any


SANDBOX_PATH_ROOTS = ("/work", "/sandbox-tmp", "/artifacts", "/state", "/logs", "/models", "/tools", "/home/agent")
ARCHIVE_SEARCH_KINDS = {"focus", "vector", "graph"}
ARCHIVE_MEMORY_READ_KINDS = {"focus", "wiki"}
ARCHIVE_MEMORY_TARGETS = {"auto", "focus", "wiki", "vector", "graph"}
ARCHIVE_MEMORY_LAYERS = {"focus", "wiki", "vector", "graph"}
ARCHIVE_MEMORY_EVENT_COMPONENTS = {"librarian", "memory_gateway"}
GLOBAL_OPTIONAL_FIELDS = {"reason"}


def _field_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    return type(value).__name__


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _validate_string(action: dict[str, Any], field: str, errors: list[dict[str, Any]], *, min_len: int = 0, max_len: int = 12000) -> None:
    value = action.get(field)
    if not _is_str(value):
        errors.append({"field": field, "error": "expected string", "actual": _field_type(value)})
        return
    if "\x00" in value:
        errors.append({"field": field, "error": "must not contain NUL bytes"})
    if len(value) < min_len:
        errors.append({"field": field, "error": "string is too short", "min_length": min_len})
    if len(value) > max_len:
        errors.append({"field": field, "error": "string is too long", "max_length": max_len, "actual_length": len(value)})


def _validate_int(
    action: dict[str, Any],
    field: str,
    errors: list[dict[str, Any]],
    *,
    minimum: int,
    maximum: int,
    required: bool = False,
) -> None:
    if field not in action:
        if required:
            errors.append({"field": field, "error": "missing required field"})
        return
    value = action.get(field)
    if not _is_int(value):
        errors.append({"field": field, "error": "expected integer", "actual": _field_type(value)})
        return
    if value < minimum or value > maximum:
        errors.append({"field": field, "error": "integer out of range", "minimum": minimum, "maximum": maximum, "actual": value})


def _validate_bool(action: dict[str, Any], field: str, errors: list[dict[str, Any]]) -> None:
    if field in action and not _is_bool(action.get(field)):
        errors.append({"field": field, "error": "expected boolean", "actual": _field_type(action.get(field))})


def _validate_enum(action: dict[str, Any], field: str, allowed: set[str], errors: list[dict[str, Any]], *, required: bool = False) -> None:
    if field not in action:
        if required:
            errors.append({"field": field, "error": "missing required field"})
        return
    value = action.get(field)
    if not _is_str(value):
        errors.append({"field": field, "error": "expected string", "actual": _field_type(value)})
        return
    normalized = value.strip().lower()
    if normalized not in allowed:
        errors.append({"field": field, "error": "unsupported value", "allowed": sorted(allowed), "actual": value})


def _validate_path(action: dict[str, Any], errors: list[dict[str, Any]]) -> None:
    _validate_string(action, "path", errors, min_len=1, max_len=4096)
    path = action.get("path")
    if not isinstance(path, str):
        return
    stripped = path.strip()
    if not stripped:
        errors.append({"field": "path", "error": "path must not be empty"})
        return
    if stripped.startswith("/") and not any(stripped == root or stripped.startswith(root + "/") for root in SANDBOX_PATH_ROOTS):
        errors.append({"field": "path", "error": "absolute path outside sandbox writable roots", "allowed_roots": list(SANDBOX_PATH_ROOTS)})


def _validate_layers(action: dict[str, Any], errors: list[dict[str, Any]]) -> None:
    if "layers" not in action:
        return
    value = action.get("layers")
    if isinstance(value, str):
        layers = [layer.strip().lower() for layer in value.split(",") if layer.strip()]
    elif isinstance(value, list):
        layers = []
        for index, item in enumerate(value):
            if not isinstance(item, str):
                errors.append({"field": "layers", "index": index, "error": "expected string", "actual": _field_type(item)})
                return
            layers.append(item.strip().lower())
    else:
        errors.append({"field": "layers", "error": "expected comma string or string array", "actual": _field_type(value)})
        return
    bad_layers = sorted({layer for layer in layers if layer not in ARCHIVE_MEMORY_LAYERS})
    if bad_layers:
        errors.append({"field": "layers", "error": "unsupported layer", "allowed": sorted(ARCHIVE_MEMORY_LAYERS), "actual": bad_layers})


ACTION_SCHEMAS: dict[str, dict[str, Any]] = {
    "final": {"required": {"message"}, "fields": {"action", "message"}},
    "sandbox_status": {"required": set(), "fields": {"action"}},
    "archive_status": {"required": set(), "fields": {"action"}},
    "archive_memory_gateway": {"required": set(), "fields": {"action"}},
    "archive_memory_catalog": {"required": set(), "fields": {"action"}},
    "archive_search": {"required": {"kind", "query"}, "fields": {"action", "kind", "query"}},
    "archive_memory_events": {"required": set(), "fields": {"action", "limit", "component", "event_action", "requester"}},
    "archive_memory_search": {"required": {"query"}, "fields": {"action", "query", "limit", "layers", "include_content"}},
    "archive_memory_read": {"required": {"kind"}, "fields": {"action", "kind", "id", "title", "max_chars"}},
    "archive_memory_propose": {"required": {"proposal"}, "fields": {"action", "target", "importance", "proposal", "evidence"}},
    "shell": {"required": {"cmd"}, "fields": {"action", "cmd", "timeout", "approved"}},
    "python": {"required": {"code"}, "fields": {"action", "code", "timeout"}},
    "web_search": {"required": {"query"}, "fields": {"action", "query", "limit"}},
    "web_fetch": {"required": {"url"}, "fields": {"action", "url", "max_bytes"}},
    "list_files": {"required": {"path"}, "fields": {"action", "path", "max_depth", "limit", "offset"}},
    "read_file": {"required": {"path"}, "fields": {"action", "path", "max_bytes", "offset"}},
    "write_file": {"required": {"path", "content"}, "fields": {"action", "path", "content"}},
    "append_file": {"required": {"path", "content"}, "fields": {"action", "path", "content"}},
    "replace_in_file": {"required": {"path", "old", "new"}, "fields": {"action", "path", "old", "new", "count", "max_file_bytes"}},
    "mkdir": {"required": {"path"}, "fields": {"action", "path"}},
    "remove_file": {"required": {"path"}, "fields": {"action", "path", "recursive"}},
    "file_info": {"required": {"path"}, "fields": {"action", "path", "sha256", "max_hash_bytes"}},
    "find_files": {"required": {"path", "pattern"}, "fields": {"action", "path", "pattern", "max_depth", "limit", "offset"}},
    "search_text": {"required": {"path", "query"}, "fields": {"action", "path", "query", "case_sensitive", "max_matches", "max_bytes_per_file"}},
}


def validate_action(action: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(action, Mapping):
        return {"ok": False, "error": "action must be a JSON object"}
    action_type_value = action.get("action")
    if not isinstance(action_type_value, str) or not action_type_value.strip():
        return {"ok": False, "error": "missing action"}
    action_type = action_type_value.strip().lower()
    schema = ACTION_SCHEMAS.get(action_type)
    if schema is None:
        return {"ok": False, "error": "unsupported action", "action": action_type, "allowed_actions": sorted(ACTION_SCHEMAS)}

    action_dict = dict(action)
    errors: list[dict[str, Any]] = []
    missing = sorted(field for field in schema["required"] if field not in action_dict)
    for field in missing:
        errors.append({"field": field, "error": "missing required field"})

    allowed_fields = set(schema["fields"]) | GLOBAL_OPTIONAL_FIELDS
    unknown = sorted(field for field in action_dict if field not in allowed_fields)
    if unknown:
        errors.append({"error": "unknown fields", "fields": unknown})

    if "reason" in action_dict:
        _validate_string(action_dict, "reason", errors, max_len=2000)

    if action_type == "final":
        _validate_string(action_dict, "message", errors, min_len=1, max_len=20000)
    elif action_type == "archive_search":
        _validate_enum(action_dict, "kind", ARCHIVE_SEARCH_KINDS, errors, required=True)
        _validate_string(action_dict, "query", errors, min_len=1, max_len=10000)
    elif action_type == "archive_memory_events":
        _validate_int(action_dict, "limit", errors, minimum=1, maximum=100)
        _validate_enum(action_dict, "component", ARCHIVE_MEMORY_EVENT_COMPONENTS, errors)
        for field in ("event_action", "requester"):
            if field in action_dict:
                _validate_string(action_dict, field, errors, min_len=1, max_len=200)
    elif action_type == "archive_memory_search":
        _validate_string(action_dict, "query", errors, min_len=1, max_len=10000)
        _validate_int(action_dict, "limit", errors, minimum=1, maximum=20)
        _validate_bool(action_dict, "include_content", errors)
        _validate_layers(action_dict, errors)
    elif action_type == "archive_memory_read":
        _validate_enum(action_dict, "kind", ARCHIVE_MEMORY_READ_KINDS, errors, required=True)
        for field in ("id", "title"):
            if field in action_dict:
                _validate_string(action_dict, field, errors, min_len=1, max_len=500)
        _validate_int(action_dict, "max_chars", errors, minimum=1000, maximum=50000)
    elif action_type == "archive_memory_propose":
        _validate_string(action_dict, "proposal", errors, min_len=1, max_len=20000)
        _validate_enum(action_dict, "target", ARCHIVE_MEMORY_TARGETS, errors)
        _validate_int(action_dict, "importance", errors, minimum=1, maximum=5)
        if "evidence" in action_dict:
            _validate_string(action_dict, "evidence", errors, max_len=20000)
    elif action_type == "shell":
        _validate_string(action_dict, "cmd", errors, min_len=1, max_len=20000)
        _validate_int(action_dict, "timeout", errors, minimum=1, maximum=300)
        _validate_bool(action_dict, "approved", errors)
    elif action_type == "python":
        _validate_string(action_dict, "code", errors, min_len=1, max_len=50000)
        _validate_int(action_dict, "timeout", errors, minimum=1, maximum=300)
    elif action_type == "web_search":
        _validate_string(action_dict, "query", errors, min_len=1, max_len=10000)
        _validate_int(action_dict, "limit", errors, minimum=1, maximum=10)
    elif action_type == "web_fetch":
        _validate_string(action_dict, "url", errors, min_len=1, max_len=4096)
        _validate_int(action_dict, "max_bytes", errors, minimum=1024, maximum=1000000)
    elif action_type in {"list_files", "read_file", "write_file", "append_file", "replace_in_file", "mkdir", "remove_file", "file_info", "find_files", "search_text"}:
        _validate_path(action_dict, errors)
        if action_type in {"write_file", "append_file"}:
            _validate_string(action_dict, "content", errors, max_len=12000)
        if action_type == "replace_in_file":
            _validate_string(action_dict, "old", errors, min_len=1, max_len=200000)
            _validate_string(action_dict, "new", errors, max_len=200000)
            _validate_int(action_dict, "count", errors, minimum=-1, maximum=100000)
            _validate_int(action_dict, "max_file_bytes", errors, minimum=1, maximum=20000000)
        if action_type == "remove_file":
            _validate_bool(action_dict, "recursive", errors)
        if action_type == "file_info":
            _validate_bool(action_dict, "sha256", errors)
            _validate_int(action_dict, "max_hash_bytes", errors, minimum=1, maximum=200000000)
        if action_type == "find_files":
            _validate_string(action_dict, "pattern", errors, min_len=1, max_len=1024)
        if action_type == "search_text":
            _validate_string(action_dict, "query", errors, min_len=1, max_len=10000)
            _validate_bool(action_dict, "case_sensitive", errors)
            _validate_int(action_dict, "max_matches", errors, minimum=1, maximum=500)
            _validate_int(action_dict, "max_bytes_per_file", errors, minimum=1024, maximum=1000000)
        if action_type in {"list_files", "find_files"}:
            max_depth_max = 8 if action_type == "list_files" else 12
            _validate_int(action_dict, "max_depth", errors, minimum=0, maximum=max_depth_max)
            _validate_int(action_dict, "limit", errors, minimum=1, maximum=1000)
            _validate_int(action_dict, "offset", errors, minimum=0, maximum=1000000000)
        if action_type == "read_file":
            _validate_int(action_dict, "max_bytes", errors, minimum=1, maximum=200000)
            _validate_int(action_dict, "offset", errors, minimum=0, maximum=1000000000)

    if errors:
        return {"ok": False, "error": "invalid action schema", "action": action_type, "errors": errors}
    return {"ok": True, "action": action_type}
