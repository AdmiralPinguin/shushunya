"""HTTP request/response and upstream-proxy helpers for ArchiveOfHeresy."""
import json
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from archive_config import *  # noqa: F401,F403


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


def authorized(handler, allow_mobile=False):
    if not ARCHIVE_API_KEY and not ARCHIVE_MOBILE_API_KEY:
        return True

    auth = handler.headers.get("Authorization", "").strip()
    if ARCHIVE_API_KEY and auth == f"Bearer {ARCHIVE_API_KEY}":
        return True
    mobile_key = handler.headers.get("X-Shushunya-Mobile-Key", "").strip()
    if allow_mobile and ARCHIVE_MOBILE_API_KEY and (
        auth == f"Bearer {ARCHIVE_MOBILE_API_KEY}" or mobile_key == ARCHIVE_MOBILE_API_KEY
    ):
        return True
    return False


def require_auth(handler, allow_mobile=False):
    if authorized(handler, allow_mobile=allow_mobile):
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


def proxy_json(method, path, payload=None, timeout=180):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(f"{LLM_BASE_URL}{path}", data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body) if body else {}


def open_upstream(method, path, payload=None, timeout=180):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(f"{LLM_BASE_URL}{path}", data=data, headers=headers, method=method)
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
