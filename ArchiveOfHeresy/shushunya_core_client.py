"""Loopback adapter from Archive's transport/memory facade to ShushunyaCore."""
from __future__ import annotations

import json
import os
import urllib.error
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


class CoreRequestError(RuntimeError):
    """A bounded, user-safe description of a failed Core call."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        retryable: bool = False,
        error_code: str = "core_request_failed",
    ) -> None:
        super().__init__(message)
        self.status = int(status or 0)
        self.retryable = bool(retryable)
        self.error_code = str(error_code or "core_request_failed")


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read(65_537)
    except Exception:
        return ""
    if len(raw) > 65_536:
        return "response body exceeds the diagnostic limit"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, list):
        findings = []
        for item in detail[:3]:
            if not isinstance(item, dict):
                continue
            location = ".".join(str(part) for part in item.get("loc", []) if part is not None)
            message = " ".join(str(item.get("msg") or "").split())
            finding = ": ".join(part for part in (location, message) if part)
            if finding:
                findings.append(finding)
        return "; ".join(findings)[:500]
    if isinstance(detail, str):
        return " ".join(detail.split())[:500]
    return ""


def _request(path: str, payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{CORE_BASE_URL}{path}",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout or CORE_TIMEOUT_SEC) as response:
            raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise CoreRequestError(
                    "ShushunyaCore response exceeds the 1 MiB contract limit",
                    retryable=True,
                    error_code="core_response_invalid",
                )
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CoreRequestError(
                    "ShushunyaCore returned invalid JSON",
                    retryable=True,
                    error_code="core_response_invalid",
                ) from exc
    except urllib.error.HTTPError as exc:
        status = int(exc.code or 0)
        detail = _http_error_detail(exc)
        error_code = "core_contract_rejected" if status == 422 else "core_http_error"
        explanation = f"ShushunyaCore returned HTTP {status or 'error'}"
        if detail:
            explanation += f": {detail}"
        raise CoreRequestError(
            explanation,
            status=status,
            retryable=status >= 500 or status in {408, 425, 429},
            error_code=error_code,
        ) from exc
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        raise CoreRequestError(
            f"ShushunyaCore transport failed: {type(exc).__name__}",
            retryable=True,
            error_code="core_transport_unavailable",
        ) from exc
    if not isinstance(body, dict):
        raise CoreRequestError(
            "ShushunyaCore returned a non-object response",
            retryable=True,
            error_code="core_response_invalid",
        )
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
