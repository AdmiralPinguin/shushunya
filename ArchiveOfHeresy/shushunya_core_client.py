"""Loopback adapter from Archive's transport/memory facade to ShushunyaCore."""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any
from urllib.parse import urlsplit


CORE_BASE_URL = os.environ.get("SHUSHUNYA_CORE_BASE_URL", "http://127.0.0.1:7600").rstrip("/")
CORE_TIMEOUT_SEC = float(os.environ.get("SHUSHUNYA_CORE_TIMEOUT_SEC", "260"))


def _validate_loopback(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.port != 7600:
        raise RuntimeError("SHUSHUNYA_CORE_BASE_URL must be the loopback Core route on port 7600")


_validate_loopback(CORE_BASE_URL)


def _request(path: str, payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{CORE_BASE_URL}{path}",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout or CORE_TIMEOUT_SEC) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise RuntimeError("ShushunyaCore returned a non-object response")
    return body


def resolve_turn(
    *,
    idempotency_key: str,
    session_id: str,
    memory_namespace: str,
    source: str,
    text: str,
    image_attached: bool,
    model: str,
    recent_history: list[dict[str, Any]],
    capability_manifest: dict[str, Any],
    context: dict[str, Any],
    forced_action: str | None = None,
) -> dict[str, Any]:
    return _request(
        "/v1/turns/resolve",
        {
            "idempotency_key": idempotency_key,
            "session_id": session_id,
            "memory_namespace": memory_namespace,
            "source": source,
            "text": text,
            "image_attached": bool(image_attached),
            "model": model,
            "recent_history": recent_history,
            "capability_manifest": capability_manifest,
            "context": context,
            "forced_action": forced_action,
            "correlation_id": idempotency_key,
        },
    )


def dispatch_effect(effect_id: str) -> dict[str, Any]:
    return _request(f"/v1/effects/{effect_id}/dispatch", {}, timeout=300)
