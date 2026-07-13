"""HTTP request/response and upstream-proxy helpers for ArchiveOfHeresy."""
import contextvars
import hmac
import json
import re
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from archive_config import *  # noqa: F401,F403

# Priority carried to the LLM dispatcher (in front of llama.cpp). Set per serving
# path — "chat" for the owner's live answer, "librarian" for memory
# consolidation — and inherited by every upstream call on that thread; defaults
# to "other" for all ancillary/background work.
LLM_PRIORITY = contextvars.ContextVar("llm_priority", default="other")
LLM_ROUTE = contextvars.ContextVar("llm_route", default="")
ALLOWED_LLM_ROUTES = frozenset({"gemma", "qwen"})


def set_llm_route(value):
    """Select a configured dispatcher route without accepting arbitrary URLs."""
    route = str(value or "").strip().lower()
    if route not in ALLOWED_LLM_ROUTES:
        route = ""
    LLM_ROUTE.set(route)
    return route


def _with_priority(headers):
    headers = dict(headers or {})
    headers.setdefault("X-LLM-Priority", LLM_PRIORITY.get())
    route = LLM_ROUTE.get()
    if route:
        headers.setdefault("X-LLM-Route", route)
    return headers


def read_json(handler):
    content_length = int(handler.headers.get("Content-Length", "0"))
    if content_length <= 0:
        return {}
    raw = handler.rfile.read(content_length).decode("utf-8")
    return json.loads(raw)


def write_json(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _matches_secret(presented, expected):
    if not expected:
        return False
    try:
        presented_bytes = str(presented or "").encode("utf-8")
        expected_bytes = str(expected).encode("utf-8")
    except UnicodeError:
        return False
    return hmac.compare_digest(presented_bytes, expected_bytes)


def _authentication_context(handler, allow_mobile=False, allow_client=False):
    if not ARCHIVE_API_KEY and not ARCHIVE_CLIENT_API_KEY and not ARCHIVE_MOBILE_API_KEY:
        default_source = ARCHIVE_CLIENT_AUDIENCE_SOURCE if (allow_client or allow_mobile) else ARCHIVE_API_AUDIENCE_SOURCE
        return True, default_source

    auth = handler.headers.get("Authorization", "").strip()
    bearer = auth[7:] if auth.startswith("Bearer ") else ""
    if _matches_secret(bearer, ARCHIVE_API_KEY):
        return True, ARCHIVE_API_AUDIENCE_SOURCE
    client_key = handler.headers.get("X-Shushunya-Client-Key", "").strip()
    if (allow_client or allow_mobile) and (
        _matches_secret(bearer, ARCHIVE_CLIENT_API_KEY)
        or _matches_secret(client_key, ARCHIVE_CLIENT_API_KEY)
    ):
        return True, ARCHIVE_CLIENT_AUDIENCE_SOURCE
    mobile_key = handler.headers.get("X-Shushunya-Mobile-Key", "").strip()
    if allow_mobile and (
        _matches_secret(bearer, ARCHIVE_MOBILE_API_KEY)
        or _matches_secret(mobile_key, ARCHIVE_MOBILE_API_KEY)
    ):
        return True, ARCHIVE_MOBILE_AUDIENCE_SOURCE
    return False, None


def authorized(handler, allow_mobile=False, allow_client=False):
    ok, audience_source = _authentication_context(
        handler,
        allow_mobile=allow_mobile,
        allow_client=allow_client,
    )
    if ok:
        handler.archive_audience_source = audience_source
    return ok


def authenticated_audience_source(handler, *, fallback="app"):
    return str(getattr(handler, "archive_audience_source", "") or fallback).strip().lower()


def authenticated_artifact_audience(handler, payload=None, *, fallback="app"):
    """Only the privileged generic key may make a scoped transport claim."""
    audience = authenticated_audience_source(handler, fallback=fallback)
    if audience != "*":
        return audience
    payload = payload if isinstance(payload, dict) else {}
    claimed = str(payload.get("client_source") or payload.get("source") or "").strip().lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,127}", claimed):
        return claimed
    return "*"


def require_auth(handler, allow_mobile=False, allow_client=False):
    if authorized(handler, allow_mobile=allow_mobile, allow_client=allow_client):
        return True
    write_json(
        handler,
        401,
        {
            "error": {
                "message": "Missing or invalid API key",
                "type": "authentication_error",
            }
        },
    )
    return False


def require_artifact_auth(handler, *, head_only=False):
    """Artifact bytes never inherit Archive's legacy no-key/fail-open mode."""
    configured = bool(ARCHIVE_API_KEY or ARCHIVE_CLIENT_API_KEY or ARCHIVE_MOBILE_API_KEY)
    if configured and authorized(handler, allow_mobile=True, allow_client=True):
        return True
    status = 401 if configured else 503
    if head_only:
        handler.send_response(status)
        handler.send_header("Content-Length", "0")
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
    else:
        write_json(
            handler,
            status,
            {
                "ok": False,
                "error": (
                    "Missing or invalid artifact API key"
                    if configured
                    else "Artifact downloads are disabled until an Archive API key is configured"
                ),
            },
        )
    return False


def require_internal_core_auth(handler):
    """Authenticate the private Core effect adapters, fail closed.

    A peer-address check remains useful as defense in depth, but is not enough:
    Cloudflare terminates locally and public requests can also appear to come
    from loopback.  The separate credential is deliberately not one of the
    public Archive/client keys.
    """
    peer = str((getattr(handler, "client_address", None) or ("",))[0] or "")
    if peer not in {"127.0.0.1", "::1"}:
        write_json(handler, 403, {"ok": False, "error": "internal Core adapter is loopback only"})
        return False
    if not SHUSHUNYA_CORE_ARCHIVE_KEY:
        write_json(
            handler,
            503,
            {"ok": False, "error": "internal Core adapter credential is not configured"},
        )
        return False
    presented = handler.headers.get("X-Shushunya-Core-Key", "").strip()
    if not _matches_secret(presented, SHUSHUNYA_CORE_ARCHIVE_KEY):
        write_json(handler, 401, {"ok": False, "error": "invalid internal Core credential"})
        return False
    return True


def proxy_json(method, path, payload=None, timeout=180):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(f"{LLM_BASE_URL}{path}", data=data, headers=_with_priority(headers), method=method)
    with urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body) if body else {}


def open_upstream(method, path, payload=None, timeout=180):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(f"{LLM_BASE_URL}{path}", data=data, headers=_with_priority(headers), method=method)
    return urlopen(request, timeout=timeout)


def proxy_json_url(method, url, payload=None, timeout=180):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body) if body else {}


def proxy_json_url_raw(method, url, payload=None, timeout=180):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body) if body else {}


def open_json_stream_url(method, url, payload=None, timeout=300, accept="application/x-ndjson"):
    data = None
    headers = {"Accept": accept}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    return urlopen(request, timeout=timeout)


def proxy_binary_url(method, url, body, headers=None, timeout=240):
    request = Request(url, data=body, headers=headers or {}, method=method)
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
        content_type = response.headers.get("Content-Type", "")
        if "json" in content_type:
            return response.status, json.loads(raw.decode("utf-8")) if raw else {}
        return response.status, {"body": raw.decode("utf-8", errors="replace")}


def read_chunked_body(handler):
    body = bytearray()
    while True:
        line = handler.rfile.readline()
        if not line:
            break
        size_text = line.split(b";", 1)[0].strip()
        if not size_text:
            continue
        size = int(size_text, 16)
        if size == 0:
            handler.rfile.readline()
            break
        body.extend(handler.rfile.read(size))
        handler.rfile.read(2)
    return bytes(body)


def read_raw_body(handler):
    transfer_encoding = str(handler.headers.get("Transfer-Encoding") or "").lower()
    if "chunked" in transfer_encoding:
        return read_chunked_body(handler)
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return b""
    return handler.rfile.read(length)


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")
