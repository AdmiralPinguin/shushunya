from __future__ import annotations

import html
import ipaddress
import json
import os
import socket
from html.parser import HTMLParser
from typing import Any, Callable, Protocol
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .utils import truncate


class WebConfig(Protocol):
    max_tool_output_chars: int


MAX_WEB_BYTES = int(os.environ.get("SHUSHUNYA_AGENT_MAX_WEB_BYTES", "200000"))
BRAVE_SEARCH_API_KEY = os.environ.get("SHUSHUNYA_AGENT_BRAVE_SEARCH_API_KEY", "").strip()
SEARXNG_URL = os.environ.get("SHUSHUNYA_AGENT_SEARXNG_URL", "").strip().rstrip("/")
SEARCH_PROVIDERS = os.environ.get("SHUSHUNYA_AGENT_SEARCH_PROVIDERS", "searxng,marginalia,wikipedia,brave")
WEB_USER_AGENT = os.environ.get(
    "SHUSHUNYA_AGENT_WEB_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
)
WEB_ACCEPT_LANGUAGE = os.environ.get("SHUSHUNYA_AGENT_WEB_ACCEPT_LANGUAGE", "ru,en;q=0.9")


def read_limited_response(response: Any, max_bytes: int) -> tuple[bytes, bool]:
    data = response.read(max_bytes + 1)
    return data[:max_bytes], len(data) > max_bytes


def is_textual_content(content_type: str, data: bytes) -> bool:
    lowered = content_type.lower()
    textual_markers = ("text/", "json", "xml", "html", "javascript", "x-www-form-urlencoded")
    if any(marker in lowered for marker in textual_markers):
        return True
    if b"\x00" in data[:4096]:
        return False
    sample = data[:4096]
    if not sample:
        return True
    control = sum(1 for byte in sample if byte < 32 and byte not in {9, 10, 13})
    return control / max(1, len(sample)) < 0.05


def decode_web_text(data: bytes, charset: str | None) -> tuple[str, str]:
    encoding = charset or "utf-8"
    try:
        return data.decode(encoding, errors="replace"), encoding
    except LookupError:
        return data.decode("utf-8", errors="replace"), "utf-8"


def summarize_json_for_model(value: Any, depth: int = 0) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 40:
                result["_omitted_keys"] = len(value) - 40
                break
            result[str(key)] = summarize_json_for_model(item, depth + 1)
        return result
    if isinstance(value, list):
        count = len(value)
        if count == 0:
            return {"count": 0, "items": []}
        if all(isinstance(item, dict) for item in value):
            if depth <= 1:
                head = value[:4]
                tail = value[-3:] if count > 8 else []
                payload: dict[str, Any] = {
                    "count": count,
                    "items": [summarize_json_for_model(item, depth + 1) for item in head],
                    "truncated": count > len(head) + len(tail),
                }
                if tail:
                    payload["last_items"] = [summarize_json_for_model(item, depth + 1) for item in tail]
                    payload["omitted_middle"] = count - len(head) - len(tail)
                return payload
            return {
                "count": count,
                "first": summarize_json_for_model(value[0], depth + 1),
                "last": summarize_json_for_model(value[-1], depth + 1),
            }
        sample = [summarize_json_for_model(item, depth + 1) for item in value[:20]]
        return {"count": count, "sample": sample, "truncated": count > 20}
    if isinstance(value, str):
        return truncate(value, 300)
    return value


def validate_public_url(raw_url: str) -> str:
    parsed = urlparse(str(raw_url).strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("only http and https URLs are allowed")
    if not parsed.hostname:
        raise ValueError("URL hostname is required")
    if parsed.username or parsed.password:
        raise ValueError("URL credentials are not allowed")
    host = parsed.hostname
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"hostname resolution failed: {exc}") from exc
    for info in infos:
        address = info[4][0]
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise ValueError(f"refusing non-public address for {host}: {address}")
    return raw_url


def validate_configured_searxng_url(raw_url: str) -> str:
    parsed = urlparse(str(raw_url).strip())
    configured = urlparse(SEARXNG_URL)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("only http and https URLs are allowed")
    if not parsed.hostname or not configured.hostname:
        raise ValueError("SearXNG hostname is required")
    if parsed.username or parsed.password:
        raise ValueError("SearXNG URL credentials are not allowed")
    if parsed.scheme != configured.scheme:
        raise ValueError("SearXNG request scheme does not match configured scheme")
    if parsed.hostname != configured.hostname:
        raise ValueError("SearXNG request host does not match configured host")
    if (parsed.port or (443 if parsed.scheme == "https" else 80)) != (
        configured.port or (443 if configured.scheme == "https" else 80)
    ):
        raise ValueError("SearXNG request port does not match configured port")
    return raw_url


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Request | None:
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class SearxngRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Request | None:
        validate_configured_searxng_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class WebTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.skip_depth = 0
        self.title_depth = 0
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
        if tag == "title":
            self.title_depth += 1
        if tag in {"p", "br", "div", "section", "article", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth > 0:
            self.skip_depth -= 1
        if tag == "title" and self.title_depth > 0:
            self.title_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = html.unescape(data).strip()
        if not text:
            return
        if self.title_depth:
            self.title_parts.append(text)
        self.text_parts.append(text)

    def result(self) -> tuple[str, str]:
        title = " ".join(" ".join(self.title_parts).split())
        text = "\n".join(line for line in (" ".join(self.text_parts).split("\n")) if line.strip())
        return title, " ".join(text.split())


class DuckDuckGoParser(HTMLParser):
    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit
        self.in_result = False
        self.current_href = ""
        self.current_text: list[str] = []
        self.results: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if len(self.results) >= self.limit or tag.lower() != "a":
            return
        attr_map = {name: value or "" for name, value in attrs}
        classes = attr_map.get("class", "")
        href = attr_map.get("href", "")
        if "result__a" in classes and href:
            self.in_result = True
            self.current_href = href
            self.current_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self.in_result:
            return
        title = " ".join(" ".join(self.current_text).split())
        url = normalize_duckduckgo_url(self.current_href)
        if title and url and all(item["url"] != url for item in self.results):
            self.results.append({"title": title, "url": url})
        self.in_result = False
        self.current_href = ""
        self.current_text = []

    def handle_data(self, data: str) -> None:
        if self.in_result:
            self.current_text.append(html.unescape(data))


def normalize_duckduckgo_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    if parsed.path == "/l/":
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return target
    return raw_url


def clean_search_result(title: Any, url: Any, snippet: Any = "") -> dict[str, str] | None:
    title_text = " ".join(str(title or "").split())
    url_text = str(url or "").strip()
    snippet_text = " ".join(str(snippet or "").split())
    if not title_text or not url_text:
        return None
    try:
        validate_public_url(url_text)
    except Exception:
        return None
    return {"title": title_text, "url": url_text, "snippet": snippet_text}


def dedupe_results(results: list[dict[str, str]], limit: int) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for result in results:
        url = result.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(result)
        if len(deduped) >= limit:
            break
    return deduped


def configured_search_providers() -> list[str]:
    providers: list[str] = []
    for raw in SEARCH_PROVIDERS.split(","):
        name = raw.strip().lower()
        if not name:
            continue
        if name == "brave_api":
            name = "brave"
        if name not in {"searxng", "marginalia", "wikipedia", "brave"}:
            continue
        if name not in providers:
            providers.append(name)
    return providers or ["searxng", "marginalia", "wikipedia", "brave"]


def web_search_brave(query: str, limit: int) -> dict[str, Any]:
    if not BRAVE_SEARCH_API_KEY:
        return {"ok": False, "provider": "brave", "error": "BRAVE_SEARCH_API_KEY is not configured"}
    url = "https://api.search.brave.com/res/v1/web/search?" + urlencode({"q": query, "count": limit})
    validate_public_url(url)
    request = Request(
        url,
        headers={
            "User-Agent": WEB_USER_AGENT,
            "Accept": "application/json",
            "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
        },
    )
    with build_opener(SafeRedirectHandler).open(request, timeout=20) as response:
        data, truncated = read_limited_response(response, 500000)
        payload = json.loads(data.decode("utf-8", errors="replace"))
    raw_results = payload.get("web", {}).get("results", [])
    results = []
    for item in raw_results:
        cleaned = clean_search_result(item.get("title"), item.get("url"), item.get("description", ""))
        if cleaned:
            results.append(cleaned)
    return {"ok": True, "provider": "brave", "results": dedupe_results(results, limit), "truncated": truncated}


def web_search_searxng(query: str, limit: int) -> dict[str, Any]:
    if not SEARXNG_URL:
        return {"ok": False, "provider": "searxng", "error": "SEARXNG_URL is not configured"}
    url = SEARXNG_URL + "/search?" + urlencode({"q": query, "format": "json", "language": "auto"})
    validate_configured_searxng_url(url)
    request = Request(url, headers={"User-Agent": WEB_USER_AGENT, "Accept": "application/json", "X-Real-IP": "127.0.0.1"})
    with build_opener(SearxngRedirectHandler).open(request, timeout=25) as response:
        validate_configured_searxng_url(response.geturl())
        data, truncated = read_limited_response(response, 600000)
        payload = json.loads(data.decode("utf-8", errors="replace"))
    results = []
    for item in payload.get("results", []):
        cleaned = clean_search_result(item.get("title"), item.get("url"), item.get("content", ""))
        if cleaned:
            results.append(cleaned)
    return {"ok": True, "provider": "searxng", "results": dedupe_results(results, limit), "truncated": truncated}


def web_search_marginalia(query: str, limit: int) -> dict[str, Any]:
    url = "https://api.marginalia.nu/public/search/" + quote(query, safe="")
    validate_public_url(url)
    request = Request(url, headers={"User-Agent": WEB_USER_AGENT, "Accept": "application/json"})
    with build_opener(SafeRedirectHandler).open(request, timeout=25) as response:
        data, truncated = read_limited_response(response, 600000)
        payload = json.loads(data.decode("utf-8", errors="replace"))
    results = []
    for item in payload.get("results", []):
        cleaned = clean_search_result(item.get("title"), item.get("url"), item.get("description", ""))
        if cleaned:
            results.append(cleaned)
    return {"ok": True, "provider": "marginalia", "results": dedupe_results(results, limit), "truncated": truncated}


def web_search_wikipedia(query: str, limit: int) -> dict[str, Any]:
    wiki_url = "https://en.wikipedia.org/w/api.php?" + urlencode(
        {
            "action": "opensearch",
            "search": query,
            "limit": limit,
            "namespace": 0,
            "format": "json",
        }
    )
    validate_public_url(wiki_url)
    request = Request(wiki_url, headers={"User-Agent": WEB_USER_AGENT, "Accept": "application/json"})
    with build_opener(SafeRedirectHandler).open(request, timeout=20) as response:
        data, truncated = read_limited_response(response, 200000)
        payload = json.loads(data.decode("utf-8", errors="replace"))
    titles = payload[1] if len(payload) > 1 and isinstance(payload[1], list) else []
    snippets = payload[2] if len(payload) > 2 and isinstance(payload[2], list) else []
    urls = payload[3] if len(payload) > 3 and isinstance(payload[3], list) else []
    results = []
    for index, title in enumerate(titles[:limit]):
        if index >= len(urls):
            continue
        cleaned = clean_search_result(title, urls[index], snippets[index] if index < len(snippets) else "")
        if cleaned:
            results.append(cleaned)
    return {"ok": True, "provider": "wikipedia_opensearch", "results": dedupe_results(results, limit), "truncated": truncated}


def web_fetch(config: WebConfig, url: str, max_bytes: int | None = None) -> dict[str, Any]:
    max_bytes = max(1024, min(int(max_bytes or MAX_WEB_BYTES), 1000000))
    validate_public_url(url)
    opener = build_opener(SafeRedirectHandler)
    request = Request(
        url,
        headers={
            "User-Agent": WEB_USER_AGENT,
            "Accept": "text/html,text/plain,application/json;q=0.8,*/*;q=0.2",
            "Accept-Language": WEB_ACCEPT_LANGUAGE,
        },
    )
    with opener.open(request, timeout=30) as response:
        final_url = response.geturl()
        validate_public_url(final_url)
        data, truncated = read_limited_response(response, max_bytes)
        content_type = response.headers.get("Content-Type", "")
        if not is_textual_content(content_type, data):
            return {
                "ok": True,
                "url": final_url,
                "status": getattr(response, "status", 200),
                "content_type": content_type,
                "truncated": truncated,
                "bytes_read": len(data),
                "is_binary": True,
                "text": "",
                "note": "binary response was not decoded into model context",
            }
        text, charset = decode_web_text(data, response.headers.get_content_charset())
        title = ""
        if "json" in content_type.lower() or text.lstrip().startswith(("{", "[")):
            try:
                return {
                    "ok": True,
                    "url": final_url,
                    "status": getattr(response, "status", 200),
                    "content_type": content_type,
                    "encoding": charset,
                    "title": title,
                    "truncated": truncated,
                    "bytes_read": len(data),
                    "is_binary": False,
                    "json_summary": summarize_json_for_model(json.loads(text)),
                    "text_note": "JSON response compacted for model context; use a targeted API/file action if exact raw JSON is needed",
                }
            except json.JSONDecodeError:
                pass
        if "html" in content_type.lower() or "<html" in text[:500].lower():
            parser = WebTextExtractor()
            parser.feed(text)
            title, text = parser.result()
        return {
            "ok": True,
            "url": final_url,
            "status": getattr(response, "status", 200),
            "content_type": content_type,
            "encoding": charset,
            "title": title,
            "truncated": truncated,
            "bytes_read": len(data),
            "is_binary": False,
            "text": truncate(text.strip(), config.max_tool_output_chars),
        }


def web_search(config: WebConfig, query: str, limit: int | None = None) -> dict[str, Any]:
    query = str(query or "").strip()
    if not query:
        return {"ok": False, "error": "query must not be empty"}
    limit = max(1, min(int(limit or 5), 10))
    provider_errors: list[dict[str, str]] = []
    provider_map: dict[str, Callable[[str, int], dict[str, Any]]] = {
        "searxng": web_search_searxng,
        "marginalia": web_search_marginalia,
        "wikipedia": web_search_wikipedia,
        "brave": web_search_brave,
    }
    for provider_name in configured_search_providers():
        provider = provider_map[provider_name]
        try:
            payload = provider(query, limit)
        except Exception as exc:
            provider_errors.append({"provider": provider.__name__.replace("web_search_", ""), "error": str(exc)})
            continue
        if not payload.get("ok"):
            provider_errors.append({"provider": str(payload.get("provider", "unknown")), "error": str(payload.get("error", "search failed"))})
            continue
        results = payload.get("results", [])
        if results:
            return {
                "ok": True,
                "query": query,
                "source": payload.get("provider", "unknown"),
                "results": results,
                "truncated": bool(payload.get("truncated", False)),
                "provider_errors": provider_errors,
            }
    return {
        "ok": False,
        "query": query,
        "error": "all search providers failed or returned no results",
        "provider_errors": provider_errors,
    }
