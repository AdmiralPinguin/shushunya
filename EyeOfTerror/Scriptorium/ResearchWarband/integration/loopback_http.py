"""Small fail-closed JSON client for the two loopback-only service profiles."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit


class LoopbackHTTPError(RuntimeError):
    """A loopback service returned an HTTP or protocol error."""

    def __init__(self, message: str, *, status: int = 0) -> None:
        super().__init__(message)
        self.status = int(status)


@dataclass(frozen=True, slots=True)
class BoundedHTTPResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def canonical_loopback_base_url(value: str, *, expected_port: int | None = None) -> str:
    """Accept only a literal HTTP loopback origin with no path or credentials."""

    if type(value) is not str or not value.strip():
        raise ValueError("loopback base URL must be a non-empty string")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("loopback base URL is malformed") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or port is None
        or not 1 <= port <= 65535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("base URL must be one literal loopback HTTP origin")
    if expected_port is not None and port != expected_port:
        raise ValueError(f"loopback service must use port {expected_port}")
    host = f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
    return f"http://{host}:{port}"


def _request_target(base_url: str, path: str) -> str:
    if type(path) is not str or not path.startswith("/"):
        raise ValueError("request path must be absolute")
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment or not parsed.path.startswith("/"):
        raise ValueError("request path must stay on the configured loopback origin")
    return base_url + urlunsplit(("", "", parsed.path, parsed.query, ""))


@dataclass(frozen=True, slots=True)
class LoopbackJSONClient:
    """Spawn-pickleable client; openers and sockets never survive a call."""

    base_url: str
    bearer_token: str = ""
    max_response_bytes: int = 66_000_000
    expected_port: int | None = None

    def __post_init__(self) -> None:
        canonical = canonical_loopback_base_url(
            self.base_url, expected_port=self.expected_port
        )
        object.__setattr__(self, "base_url", canonical)
        if type(self.bearer_token) is not str:
            raise TypeError("bearer token must be a string")
        if any(ord(char) < 32 or ord(char) == 127 for char in self.bearer_token):
            raise ValueError("bearer token contains an HTTP control character")
        if (
            type(self.max_response_bytes) is not int
            or not 1_024 <= self.max_response_bytes <= 256 * 1024 * 1024
        ):
            raise ValueError("max_response_bytes is outside the safe range")

    def request_bytes(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout_sec: float,
    ) -> BoundedHTTPResponse:
        if method not in {"GET", "POST"}:
            raise ValueError("loopback client supports only GET and POST")
        if not isinstance(timeout_sec, (int, float)) or not 0.05 <= float(timeout_sec):
            raise ValueError("timeout_sec must be positive")
        target = _request_target(self.base_url, path)
        headers = {
            "Accept": "application/json",
            "Connection": "close",
        }
        body: bytes | None = None
        if payload is not None:
            if method != "POST":
                raise ValueError("only POST requests may contain a JSON body")
            try:
                body = json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError, RecursionError) as exc:
                raise ValueError("request payload must be finite JSON") from exc
            headers["Content-Type"] = "application/json"
        elif method == "POST":
            raise ValueError("POST requires an explicit JSON object")
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        request = urllib.request.Request(
            target, data=body, headers=headers, method=method
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )
        try:
            with opener.open(request, timeout=float(timeout_sec)) as response:
                status = int(response.status)
                final_url = response.geturl()
                response_headers = {
                    str(name).lower(): str(value)
                    for name, value in response.headers.items()
                }
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            raw = exc.read(min(self.max_response_bytes, 65_536) + 1)
            detail = raw[:65_536].decode("utf-8", errors="replace")
            raise LoopbackHTTPError(
                f"loopback service returned HTTP {exc.code}: {detail}",
                status=int(exc.code),
            ) from exc
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise LoopbackHTTPError(f"loopback service request failed: {exc}") from exc
        if final_url != target:
            raise LoopbackHTTPError("loopback service attempted a redirect")
        if len(raw) > self.max_response_bytes:
            raise LoopbackHTTPError("loopback response exceeded the configured byte limit")
        declared = response_headers.get("content-length", "").strip()
        if declared:
            if not declared.isascii() or not declared.isdigit() or int(declared) != len(raw):
                raise LoopbackHTTPError("loopback response Content-Length is inconsistent")
        return BoundedHTTPResponse(status=status, headers=response_headers, body=raw)

    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout_sec: float,
    ) -> dict[str, Any]:
        response = self.request_bytes(
            method, path, payload=payload, timeout_sec=timeout_sec
        )
        media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media != "application/json":
            raise LoopbackHTTPError("loopback service response is not application/json")
        try:
            value = json.loads(
                response.body.decode("utf-8"),
                parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
                object_pairs_hook=_strict_object,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise LoopbackHTTPError("loopback service returned malformed JSON") from exc
        if not isinstance(value, dict):
            raise LoopbackHTTPError("loopback service returned a non-object JSON value")
        return value


__all__ = [
    "BoundedHTTPResponse",
    "LoopbackHTTPError",
    "LoopbackJSONClient",
    "canonical_loopback_base_url",
]
