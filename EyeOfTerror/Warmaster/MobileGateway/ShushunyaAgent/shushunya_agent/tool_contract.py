from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from .validation import ACTION_SCHEMAS, GLOBAL_OPTIONAL_FIELDS, SANDBOX_PATH_ROOTS


FIELD_DESCRIPTIONS: dict[str, str] = {
    "action": "action name",
    "approved": "boolean",
    "caption": "optional Telegram caption, max 1024 chars",
    "case_sensitive": "boolean, default true",
    "chat_id": "optional allowed chat id; default is configured server chat",
    "cmd": "string",
    "code": "string",
    "component": "string enum",
    "content": "string, max 12000 chars",
    "count": "integer",
    "cwd": "optional sandbox working directory; also added to PYTHONPATH",
    "dedupe": "boolean dedupe identical file contents",
    "event_action": "string",
    "evidence": "string",
    "exclude_glob": "optional comma-separated glob list",
    "files": "array of 1..20 objects with sandbox_path path and string content, max 12000 chars per content",
    "id": "string",
    "importance": "integer 1..5",
    "include_content": "boolean",
    "include_glob": "glob for input files, default *.txt",
    "include_title": "boolean",
    "kind": "string enum",
    "layers": "comma string or string array: focus,wiki,vector,graph",
    "limit": "integer",
    "max_bytes": "integer",
    "max_bytes_per_file": "integer",
    "max_chars": "integer",
    "max_depth": "integer",
    "max_file_bytes": "integer",
    "max_hash_bytes": "integer",
    "max_matches": "integer",
    "min_bytes": "integer minimum file size",
    "min_chars": "integer",
    "mode": "string enum",
    "must_contain": "string or string array of required markers/patterns",
    "must_not_contain": "string or string array of forbidden markers/patterns",
    "new": "string",
    "offset": "integer",
    "old": "string",
    "ordered_patterns": "string or string array that must appear in this order",
    "output_fb2": "sandbox_path for generated FB2",
    "output_txt": "sandbox_path for combined UTF-8 text",
    "path": "sandbox_path",
    "path_template": "sandbox_path template, supports {index}, {seq}, {slug}, {vol}, {chapter}",
    "pattern": "optional regex or plain text filter",
    "proposal": "string",
    "query": "string",
    "reason": "string",
    "recursive": "boolean",
    "regex": "boolean; when true patterns are regular expressions",
    "requester": "string",
    "sha256": "boolean",
    "start_url": "optional public_http_url; skip links before this URL",
    "end_url": "optional public_http_url; stop after this URL",
    "target": "string enum",
    "timeout": "integer",
    "title": "string",
    "url": "public_http_url",
    "workdir": "alias for cwd",
}

ENUM_VALUES: dict[str, dict[str, list[str]]] = {
    "archive_search": {"kind": ["focus", "vector", "graph"]},
    "archive_memory_events": {"component": ["librarian", "memory_gateway"]},
    "archive_memory_search": {"layers": ["focus", "wiki", "vector", "graph"]},
    "archive_memory_read": {"kind": ["focus", "wiki"]},
    "archive_memory_propose": {"target": ["auto", "focus", "wiki", "vector", "graph"]},
    "web_extract_to_file": {"mode": ["write", "append"]},
    "ranobehub_chapter": {"mode": ["write", "append"]},
}


def _ordered_fields(action_type: str, fields: set[str], required: set[str]) -> list[str]:
    ordered = ["action"]
    ordered.extend(field for field in sorted(required) if field != "action")
    ordered.extend(field for field in sorted(fields) if field not in set(ordered))
    return ordered


def _field_description(action_type: str, field: str) -> Any:
    enum_values = ENUM_VALUES.get(action_type, {}).get(field)
    if enum_values:
        return enum_values
    if field == "action":
        return action_type
    return FIELD_DESCRIPTIONS.get(field, "string")


def build_actions_contract(previous_actions: Mapping[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    actions: dict[str, dict[str, Any]] = {}
    for action_type, runtime_schema in ACTION_SCHEMAS.items():
        required = set(runtime_schema["required"])
        fields = set(runtime_schema["fields"])
        previous_action = previous_actions.get(action_type, {}) if previous_actions else {}
        previous_properties = previous_action.get("properties") if isinstance(previous_action, Mapping) else {}
        if not isinstance(previous_properties, Mapping):
            previous_properties = {}
        properties = {field: _field_description(action_type, field) for field in _ordered_fields(action_type, fields, required)}
        action_contract: dict[str, Any] = {
            "required": _ordered_fields(action_type, {"action"} | required, required),
            "properties": properties,
        }
        previous_returns = previous_action.get("returns") if isinstance(previous_action, Mapping) else None
        previous_property_returns = previous_properties.get("returns")
        returns = previous_returns or previous_property_returns
        if returns:
            action_contract["returns"] = returns
        actions[action_type] = action_contract
    return actions


def build_tool_schema(base_schema: Mapping[str, Any] | None = None) -> dict[str, Any]:
    schema = dict(base_schema or {})
    previous_actions = schema.get("actions")
    if not isinstance(previous_actions, Mapping):
        previous_actions = {}
    schema["version"] = schema.get("version", 1)
    schema["response_contract"] = schema.get("response_contract", "exactly_one_json_object_per_step")
    schema["global_optional_properties"] = {
        field: FIELD_DESCRIPTIONS.get(field, "string")
        for field in sorted(GLOBAL_OPTIONAL_FIELDS)
    }
    schema["actions"] = build_actions_contract(previous_actions)
    schema["sandbox_path_roots"] = list(SANDBOX_PATH_ROOTS)
    return schema


def schema_contract_mismatches(schema: Mapping[str, Any]) -> list[str]:
    mismatches: list[str] = []
    actions = schema.get("actions")
    if not isinstance(actions, Mapping):
        return ["actions is missing or not an object"]
    expected = build_actions_contract(actions)
    if set(actions) != set(expected):
        mismatches.append(f"action set mismatch: missing={sorted(set(expected) - set(actions))}, extra={sorted(set(actions) - set(expected))}")
    for action_type, expected_contract in expected.items():
        actual_contract = actions.get(action_type)
        if not isinstance(actual_contract, Mapping):
            mismatches.append(f"{action_type}: action contract is missing or not an object")
            continue
        actual_required = list(actual_contract.get("required") or [])
        if actual_required != expected_contract["required"]:
            mismatches.append(f"{action_type}: required mismatch expected={expected_contract['required']} actual={actual_required}")
        actual_properties = actual_contract.get("properties")
        if not isinstance(actual_properties, Mapping):
            mismatches.append(f"{action_type}: properties is missing or not an object")
            continue
        actual_fields = sorted(actual_properties)
        expected_fields = sorted(expected_contract["properties"])
        if actual_fields != expected_fields:
            mismatches.append(f"{action_type}: property fields mismatch expected={expected_fields} actual={actual_fields}")
    return mismatches


def load_tool_schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_tool_schema(path: Path) -> None:
    current = load_tool_schema(path) if path.exists() else {}
    generated = build_tool_schema(current)
    path.write_text(json.dumps(generated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.write:
        write_tool_schema(args.path)
        return 0
    print(json.dumps(build_tool_schema(load_tool_schema(args.path)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
