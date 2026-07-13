"""Small HTTP and request/path helpers for the Warmaster gateway."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from .runtime_state import ALLOWED_SERVICE_HOSTS, MAX_LIST_LIMIT, TASK_ID_RE


MAX_GATEWAY_REQUEST_BYTES = int(os.environ.get("WARMMASTER_MAX_REQUEST_BYTES", "2000000"))
MAX_SERVICE_RESPONSE_BYTES = int(
    os.environ.get("WARMMASTER_MAX_SERVICE_RESPONSE_BYTES", "1000000")
)
_HOST_PATH_KEYS = {"host_path", "artifact_root", "workspace_root", "patch_file"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not forward governor credentials away from their exact URL."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


_PRIVATE_LOOPBACK_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    _NoRedirect(),
)


def _strict_json_object(raw: bytes) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant: {value}")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=invalid_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"service response is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("service response is not a JSON object")
    return value


def _validate_service_response(response: Any, requested_url: str) -> None:
    if response.geturl() != requested_url:
        raise ValueError("service response URL differs from the requested URL")
    media_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0]
    if media_type.strip().lower() != "application/json":
        raise ValueError("service response Content-Type must be application/json")
    length = response.headers.get("Content-Length")
    if length:
        try:
            parsed_length = int(length)
        except ValueError as exc:
            raise ValueError("service returned an invalid Content-Length") from exc
        if parsed_length < 0 or parsed_length > MAX_SERVICE_RESPONSE_BYTES:
            raise ValueError("service response exceeds the byte limit")


def redact_host_paths(value: Any) -> Any:
    """Remove internal filesystem locations from HTTP payloads, recursively."""
    if isinstance(value, dict):
        return {
            key: redact_host_paths(item)
            for key, item in value.items()
            if str(key) not in _HOST_PATH_KEYS
        }
    if isinstance(value, list):
        return [redact_host_paths(item) for item in value]
    return value


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
    data = json.dumps(redact_host_paths(payload), ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    origin = handler.headers.get("Origin", "").strip()
    trusted = {
        item.strip()
        for item in os.environ.get("WARMMASTER_APPLY_TRUSTED_ORIGINS", "").split(",")
        if item.strip()
    }
    if origin and origin in trusted:
        handler.send_header("Access-Control-Allow-Origin", origin)
        handler.send_header("Vary", "Origin")
    handler.end_headers()
    handler.wfile.write(data)


def read_payload(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    raw_length = handler.headers.get("Content-Length", "0")
    try:
        length = int(raw_length)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid Content-Length") from exc
    if length < 0 or length > MAX_GATEWAY_REQUEST_BYTES:
        raise ValueError(f"request body exceeds {MAX_GATEWAY_REQUEST_BYTES} bytes")
    raw = handler.rfile.read(length)
    if len(raw) != length:
        raise ValueError("request body ended before Content-Length")
    try:
        payload = json.loads(raw.decode("utf-8") if raw else "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"request body is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def post_json(
    url: str,
    payload: dict[str, Any],
    timeout_sec: float = 120.0,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    request_headers.update(headers or {})
    request = urllib.request.Request(
        url, data=data, headers=request_headers, method="POST",
    )
    try:
        with _PRIVATE_LOOPBACK_OPENER.open(request, timeout=timeout_sec) as response:
            _validate_service_response(response, url)
            raw = response.read(MAX_SERVICE_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if 300 <= int(exc.code) < 400:
            raise ValueError("service attempted an HTTP redirect") from exc
        _validate_service_response(exc, url)
        raise
    if len(raw) > MAX_SERVICE_RESPONSE_BYTES:
        raise ValueError("service response exceeds the byte limit")
    return _strict_json_object(raw)
