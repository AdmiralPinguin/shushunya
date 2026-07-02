"""Small HTTP and request/path helpers for the Warmaster gateway."""
from __future__ import annotations

import json
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from .runtime_state import ALLOWED_SERVICE_HOSTS, MAX_LIST_LIMIT, TASK_ID_RE


def parse_limit(raw_value: str, default: int, maximum: int = MAX_LIST_LIMIT) -> int:
    if not raw_value.isdigit():
        return default
    return max(0, min(int(raw_value), maximum))


def parse_nonnegative_int(raw_value: str, default: int) -> int:
    if not raw_value.isdigit():
        return default
    return max(0, int(raw_value))


def requested_step_ids_from_payload(payload: dict[str, Any]) -> list[str]:
    if "step_ids" not in payload:
        return []
    raw_step_ids = payload.get("step_ids")
    if not isinstance(raw_step_ids, list):
        raise ValueError("step_ids must be a list of non-empty strings")
    step_ids: list[str] = []
    for index, item in enumerate(raw_step_ids):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"step_ids[{index}] must be a non-empty string")
        step_id = item.strip()
        if step_id in step_ids:
            raise ValueError(f"step_ids contains duplicate step: {step_id}")
        step_ids.append(step_id)
    return step_ids


def valid_task_id(task_id: str) -> bool:
    return bool(TASK_ID_RE.fullmatch(task_id)) and ".." not in task_id


def resolve_run_child_path(run_dir: Path, requested: str, default_name: str) -> Path:
    root = run_dir.resolve()
    candidate = Path(requested) if requested else root / default_name
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path must stay inside run_dir: {default_name}")
    return resolved


def validate_service_host(host: str) -> str:
    normalized = host.strip().lower()
    if normalized not in ALLOWED_SERVICE_HOSTS:
        raise ValueError("worker service host must be a loopback host")
    return normalized


def response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
    handler.end_headers()
    handler.wfile.write(data)


def read_payload(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    payload = json.loads(handler.rfile.read(length).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def post_json(url: str, payload: dict[str, Any], timeout_sec: float = 10.0) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("service response is not a JSON object")
    return result
