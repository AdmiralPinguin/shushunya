"""Fail-closed search, pinned HTTP acquisition and bounded normalization.

Search provider output is not allowed to invent a source class. A trusted
classifier must bind the class to each hit, and the fetch result must echo that
binding. HTTP connects to the exact public IP validated immediately before the
request (including every redirect), while preserving the original Host header
and TLS SNI. EPUB extraction validates the complete archive inventory before it
decompresses a byte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import http.client
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import socket
import ssl
import stat
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable
from urllib.parse import urljoin, urlsplit, urlunsplit
import zipfile

from .schema import (
    SOURCE_CLASSES,
    EpubLocator,
    Fb2Locator,
    HtmlLocator,
    Locator,
    PdfLocator,
    TextLocator,
)
from .snapshot_store import RegisteredNormalizer
from .verifier import CANONICAL_EPUB_HREF_PREFIX, CANONICAL_HTML_SELECTOR


MAX_FETCH_BYTES = 1_000_000
MAX_REDIRECTS = 5
EPUB_MAX_MEMBERS = 512
EPUB_MAX_DOCUMENT_MEMBERS = 80
EPUB_MAX_ENTRY_BYTES = 4 * 1024 * 1024
EPUB_MAX_TOTAL_BYTES = 16 * 1024 * 1024
EPUB_MAX_COMPRESSION_RATIO = 100


class ResearchToolError(RuntimeError):
    """Base error for search or acquisition failures."""


class SearchUnavailable(ResearchToolError):
    """Every configured search provider failed."""


class AcquisitionError(ResearchToolError):
    """A source could not be safely fetched and normalized."""


def _source_class(value: Any, context: str, *, allow_unknown: bool = False) -> str:
    if type(value) is not str:
        raise ValueError(f"{context} must be a source-class string")
    selected = value.strip()
    if allow_unknown and selected == "unknown":
        return selected
    if selected not in SOURCE_CLASSES:
        raise ValueError(f"{context} is not a recognized source class")
    return selected


@dataclass(frozen=True, slots=True)
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    source_class: str = "unknown"
    classification_identity: str = "unknown"

    def __post_init__(self) -> None:
        for name in ("title", "url", "snippet"):
            value = getattr(self, name)
            if type(value) is not str:
                raise TypeError(f"SearchHit.{name} must be a string")
        if not self.title.strip() or not self.url.strip():
            raise ValueError("SearchHit title and URL must not be empty")
        _source_class(self.source_class, "SearchHit.source_class", allow_unknown=True)
        if type(self.classification_identity) is not str or not self.classification_identity:
            raise ValueError("SearchHit.classification_identity must not be empty")


@dataclass(frozen=True, slots=True)
class FetchedSource:
    requested_uri: str
    final_uri: str
    raw: bytes
    normalized: str
    medium: str
    fetched_at: str
    normalizer_version: str
    source_class: str = "unknown"
    classification_identity: str = "unknown"
    truncated: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.requested_uri) is not str or not self.requested_uri.strip():
            raise ValueError("FetchedSource.requested_uri must not be empty")
        if type(self.final_uri) is not str or not self.final_uri.strip():
            raise ValueError("FetchedSource.final_uri must not be empty")
        if type(self.raw) is not bytes:
            raise TypeError("FetchedSource.raw must be bytes")
        if type(self.normalized) is not str:
            raise TypeError("FetchedSource.normalized must be a string")
        if not self.raw or not self.normalized.strip():
            raise ValueError("FetchedSource must contain raw and normalized content")
        if self.medium not in {"text", "html", "pdf", "epub", "fb2"}:
            raise ValueError("FetchedSource.medium is unsupported")
        if type(self.fetched_at) is not str or not self.fetched_at.strip():
            raise ValueError("FetchedSource.fetched_at must not be empty")
        if type(self.normalizer_version) is not str or not self.normalizer_version.strip():
            raise ValueError("FetchedSource.normalizer_version must not be empty")
        _source_class(self.source_class, "FetchedSource.source_class", allow_unknown=True)
        if type(self.classification_identity) is not str or not self.classification_identity:
            raise ValueError("FetchedSource.classification_identity must not be empty")
        if type(self.truncated) is not bool:
            raise TypeError("FetchedSource.truncated must be a boolean")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("FetchedSource.metadata must be a mapping")


@runtime_checkable
class SearchAdapter(Protocol):
    def search(self, query: str, limit: int) -> Sequence[SearchHit]:
        """Return bounded candidate sources with trusted class bindings."""


@runtime_checkable
class FetchAdapter(Protocol):
    def fetch(self, hit: SearchHit, max_bytes: int) -> FetchedSource:
        """Fetch one exact classified hit without re-resolving its pinned IP."""


@runtime_checkable
class SourceClassifier(Protocol):
    @property
    def stable_identity(self) -> str:
        """Identity of the application-trusted classification boundary."""

    def classify(self, *, title: str, url: str, snippet: str) -> str:
        """Return one strict source class or fail."""


class ConfiguredDomainSourceClassifier:
    """Mechanical classification from an operator-owned exact/suffix map.

    Anything not explicitly configured is honestly classified as
    ``anonymous_or_unverified_web``. Matching is done on parsed IDNA host labels,
    never raw string suffixes, so ``evilofficial.com`` and
    ``official.com.evil`` cannot inherit ``official.com`` trust.
    """

    def __init__(self, config: Mapping[str, Any]) -> None:
        if not isinstance(config, Mapping) or any(type(key) is not str for key in config):
            raise ValueError("source classifier config must be an object")
        if set(config) != {"version", "exact", "suffix"} or config.get("version") != 1:
            raise ValueError("source classifier config requires version=1, exact, suffix")
        exact = self._rules(config.get("exact"), "exact")
        suffix = self._rules(config.get("suffix"), "suffix")
        canonical = {
            "version": 1,
            "exact": dict(sorted(exact.items())),
            "suffix": dict(sorted(suffix.items())),
            "default": "anonymous_or_unverified_web",
        }
        self._exact = exact
        self._suffix = suffix
        self._canonical = canonical
        encoded = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self._digest = hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _domain(value: Any, context: str) -> str:
        if type(value) is not str or not value.strip():
            raise ValueError(f"{context} domain must be a non-empty string")
        raw = value.strip().rstrip(".")
        if any(token in raw for token in ("://", "/", "\\", "@", "*", ":")):
            raise ValueError(f"{context} domain must contain host labels only")
        try:
            normalized = raw.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError(f"{context} domain is invalid IDNA") from exc
        labels = normalized.split(".")
        if len(labels) < 2 or any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not all(char.isalnum() or char == "-" for char in label)
            for label in labels
        ):
            raise ValueError(f"{context} domain is malformed")
        return normalized

    @classmethod
    def _rules(cls, value: Any, context: str) -> dict[str, str]:
        if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
            raise ValueError(f"source classifier {context} rules must be an object")
        result: dict[str, str] = {}
        for raw_domain, raw_class in value.items():
            domain = cls._domain(raw_domain, context)
            selected = _source_class(raw_class, f"classifier {context}[{domain}]")
            if domain in result:
                raise ValueError(f"duplicate normalized classifier domain: {domain}")
            result[domain] = selected
        return result

    @classmethod
    def from_json_bytes(cls, payload: bytes) -> "ConfiguredDomainSourceClassifier":
        if type(payload) is not bytes or len(payload) > 1_000_000:
            raise ValueError("source classifier JSON must be at most 1 MiB")

        def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate source classifier JSON key: {key}")
                result[key] = value
            return result

        try:
            parsed = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=object_pairs,
                parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("source classifier config is not strict JSON") from exc
        return cls(parsed)

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> "ConfiguredDomainSourceClassifier":
        candidate = Path(path)
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError("source classifier config must be a regular non-symlink file")
        with candidate.open("rb") as stream:
            payload = stream.read(1_000_001)
        return cls.from_json_bytes(payload)

    @classmethod
    def default(cls) -> "ConfiguredDomainSourceClassifier":
        configured = os.environ.get("RESEARCH_SOURCE_CLASSIFIER_JSON", "").strip()
        return cls.from_file(configured) if configured else cls(
            {"version": 1, "exact": {}, "suffix": {}}
        )

    @property
    def stable_identity(self) -> str:
        return "domain-config-" + self._digest[:32]

    def classify(self, *, title: str, url: str, snippet: str) -> str:
        del title, snippet
        parsed, host, _ = _validated_target(url)
        del parsed
        try:
            normalized = host.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("source URL hostname is invalid IDNA") from exc
        if normalized in self._exact:
            return self._exact[normalized]
        matches = [
            (domain.count("."), source_class)
            for domain, source_class in self._suffix.items()
            if normalized == domain or normalized.endswith("." + domain)
        ]
        if matches:
            return max(matches, key=lambda item: item[0])[1]
        return "anonymous_or_unverified_web"


class EyeWebSearchAdapter:
    """Production provider selection plus a mandatory trusted classifier."""

    def __init__(
        self,
        *,
        classifier: SourceClassifier | None = None,
        max_tool_output_chars: int = 120_000,
    ) -> None:
        if type(max_tool_output_chars) is not int or max_tool_output_chars < 1_000:
            raise ValueError("max_tool_output_chars must be at least 1000")
        if classifier is not None and not isinstance(classifier, SourceClassifier):
            raise TypeError("classifier must implement SourceClassifier")
        self.classifier = classifier or ConfiguredDomainSourceClassifier.default()
        self.max_tool_output_chars = max_tool_output_chars

    def search(self, query: str, limit: int) -> Sequence[SearchHit]:
        if type(query) is not str or not query.strip():
            raise ValueError("query must be a non-empty string")
        if type(limit) is not int or limit < 1 or limit > 10:
            raise ValueError("limit must be between 1 and 10")
        try:
            from EyeOfTerror.Services.Search import web_tools
        except ImportError as exc:  # pragma: no cover - deployment boundary
            raise SearchUnavailable("EyeOfTerror Search service is unavailable") from exc
        config = SimpleNamespace(max_tool_output_chars=self.max_tool_output_chars)
        payload = web_tools.web_search(config, query.strip(), limit)
        if not isinstance(payload, Mapping) or payload.get("ok") is not True:
            detail = payload.get("error") if isinstance(payload, Mapping) else "invalid response"
            raise SearchUnavailable(str(detail))
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise SearchUnavailable("search provider returned invalid results")
        results: list[SearchHit] = []
        for item in raw_results[:limit]:
            if not isinstance(item, Mapping):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            snippet = str(item.get("snippet") or "").strip()
            if not title or not url:
                continue
            try:
                classified = _source_class(
                    self.classifier.classify(title=title, url=url, snippet=snippet),
                    "trusted classifier result",
                )
                results.append(
                    SearchHit(
                        title,
                        url,
                        snippet,
                        classified,
                        self.classifier.stable_identity,
                    )
                )
            except (TypeError, ValueError, ResearchToolError):
                continue
        return tuple(results)


Resolver = Callable[..., Sequence[tuple[Any, ...]]]
ConnectionFactory = Callable[[str, str, int, str, float], Any]


def _is_public_ip(raw: str) -> bool:
    try:
        address = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return False
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def resolve_public_addresses(
    host: str, port: int, *, resolver: Resolver = socket.getaddrinfo
) -> tuple[str, ...]:
    """Resolve once, reject mixed/private answers, and return exact connect IPs."""

    try:
        infos = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise AcquisitionError(f"hostname resolution failed: {exc}") from exc
    addresses: list[str] = []
    for info in infos:
        try:
            address = str(info[4][0]).split("%", 1)[0]
        except (IndexError, TypeError):
            raise AcquisitionError("resolver returned a malformed address")
        if not _is_public_ip(address):
            raise AcquisitionError(f"refusing non-public address for {host}: {address}")
        normalized = str(ipaddress.ip_address(address))
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        raise AcquisitionError(f"hostname produced no public addresses: {host}")
    return tuple(addresses)


def _peer_matches(sock: Any, pinned_ip: str) -> None:
    if sock is None or not hasattr(sock, "getpeername"):
        raise AcquisitionError("connected socket has no inspectable peer")
    try:
        peer = str(sock.getpeername()[0]).split("%", 1)[0]
        matches = ipaddress.ip_address(peer) == ipaddress.ip_address(pinned_ip)
    except (OSError, ValueError, TypeError, IndexError) as exc:
        raise AcquisitionError("connected peer address is invalid") from exc
    if not matches:
        raise AcquisitionError(
            f"connected peer {peer} does not match pinned address {pinned_ip}"
        )


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, pinned_ip: str, timeout: float) -> None:
        super().__init__(host, port=port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:  # pragma: no cover - exercised by integration
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )
        _peer_matches(self.sock, self._pinned_ip)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, pinned_ip: str, timeout: float) -> None:
        super().__init__(
            host,
            port=port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._pinned_ip = pinned_ip

    def connect(self) -> None:  # pragma: no cover - exercised by integration
        raw = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )
        try:
            _peer_matches(raw, self._pinned_ip)
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
            _peer_matches(self.sock, self._pinned_ip)
        except Exception:
            raw.close()
            raise


def _default_connection_factory(
    scheme: str, host: str, port: int, pinned_ip: str, timeout: float
) -> Any:
    if scheme == "https":
        return _PinnedHTTPSConnection(host, port, pinned_ip, timeout)
    return _PinnedHTTPConnection(host, port, pinned_ip, timeout)


def _validated_target(url: str) -> tuple[Any, str, int]:
    try:
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise AcquisitionError(f"invalid URL: {exc}") from exc
    if parsed.scheme not in {"http", "https"}:
        raise AcquisitionError("only http and https URLs are allowed")
    if not parsed.hostname:
        raise AcquisitionError("URL hostname is required")
    if parsed.username or parsed.password:
        raise AcquisitionError("URL credentials are forbidden")
    if not 1 <= port <= 65535:
        raise AcquisitionError("URL port is outside the valid range")
    return parsed, parsed.hostname, port


def _host_header(parsed: Any, host: str, port: int) -> str:
    rendered = f"[{host}]" if ":" in host else host
    default = 443 if parsed.scheme == "https" else 80
    return rendered if port == default else f"{rendered}:{port}"


class EyeWebFetchAdapter:
    """IP-pinned HTTP(S) fetcher with manual, independently pinned redirects."""

    NORMALIZER_VERSION = "research-warband-pinned-fetch-v2"

    def __init__(
        self,
        *,
        classifier: SourceClassifier | None = None,
        resolver: Resolver = socket.getaddrinfo,
        connection_factory: ConnectionFactory = _default_connection_factory,
        timeout: float = 30.0,
        max_redirects: int = MAX_REDIRECTS,
    ) -> None:
        if not callable(resolver) or not callable(connection_factory):
            raise TypeError("resolver and connection_factory must be callable")
        if classifier is not None and not isinstance(classifier, SourceClassifier):
            raise TypeError("classifier must implement SourceClassifier")
        if not isinstance(timeout, (int, float)) or not 0.1 <= float(timeout) <= 120:
            raise ValueError("timeout must be between 0.1 and 120 seconds")
        if type(max_redirects) is not int or not 0 <= max_redirects <= 10:
            raise ValueError("max_redirects must be between 0 and 10")
        self.classifier = classifier or ConfiguredDomainSourceClassifier.default()
        self.resolver = resolver
        self.connection_factory = connection_factory
        self.timeout = float(timeout)
        self.max_redirects = max_redirects

    def _open_once(self, url: str, max_bytes: int) -> tuple[int, Mapping[str, str], bytes, str]:
        parsed, host, port = _validated_target(url)
        pinned_ip = resolve_public_addresses(host, port, resolver=self.resolver)[0]
        connection = self.connection_factory(
            parsed.scheme, host, port, pinned_ip, self.timeout
        )
        path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        headers = {
            "Host": _host_header(parsed, host, port),
            "User-Agent": "Shushunya-ResearchWarband/2.0",
            "Accept": (
                "text/html,text/plain,application/json,application/xml,"
                "application/epub+zip;q=0.8,*/*;q=0.1"
            ),
            "Accept-Language": "ru,en;q=0.9",
            "Accept-Encoding": "identity",
            "Connection": "close",
        }
        try:
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            _peer_matches(getattr(connection, "sock", None), pinned_ip)
            status = int(response.status)
            response_headers = {
                str(name).lower(): str(value) for name, value in response.getheaders()
            }
            if status in {301, 302, 303, 307, 308}:
                response.close()
                return status, response_headers, b"", pinned_ip
            length = response_headers.get("content-length", "").strip()
            if length:
                try:
                    if int(length) > max_bytes:
                        raise AcquisitionError("source Content-Length exceeds byte budget")
                except ValueError as exc:
                    raise AcquisitionError("source Content-Length is invalid") from exc
            raw = response.read(max_bytes + 1)
            response.close()
            if len(raw) > max_bytes:
                raise AcquisitionError(
                    "source exceeds the byte budget; partial snapshots are forbidden"
                )
            return status, response_headers, raw, pinned_ip
        except AcquisitionError:
            raise
        except Exception as exc:
            raise AcquisitionError(f"pinned fetch failed: {exc}") from exc
        finally:
            try:
                connection.close()
            except Exception:
                pass

    def fetch(self, hit: SearchHit, max_bytes: int) -> FetchedSource:
        if not isinstance(hit, SearchHit):
            raise TypeError("hit must be a SearchHit")
        source_class = _source_class(hit.source_class, "SearchHit.source_class")
        if hit.classification_identity != self.classifier.stable_identity:
            raise AcquisitionError(
                "SearchHit was not classified by this fetch boundary's trusted config"
            )
        initial_class = _source_class(
            self.classifier.classify(
                title=hit.title, url=hit.url, snippet=hit.snippet
            ),
            "initial fetch classification",
        )
        if initial_class != source_class:
            raise AcquisitionError("SearchHit source class no longer matches trusted config")
        if type(max_bytes) is not int or max_bytes < 1_024:
            raise ValueError("max_bytes must be at least 1024")
        ceiling = min(max_bytes, MAX_FETCH_BYTES)
        requested = hit.url.strip()
        current = requested
        redirects: list[dict[str, Any]] = []
        pinned_peers: list[str] = []
        for redirect_number in range(self.max_redirects + 1):
            status, headers, raw, peer = self._open_once(current, ceiling)
            pinned_peers.append(peer)
            if status not in {301, 302, 303, 307, 308}:
                if not 200 <= status <= 299:
                    raise AcquisitionError(f"source returned HTTP status {status}")
                break
            if redirect_number >= self.max_redirects:
                raise AcquisitionError("source exceeded the redirect limit")
            location = headers.get("location", "").strip()
            if not location:
                raise AcquisitionError("redirect response omitted Location")
            target = urljoin(current, location)
            old_scheme = urlsplit(current).scheme
            new_scheme = urlsplit(target).scheme
            if old_scheme == "https" and new_scheme != "https":
                raise AcquisitionError("HTTPS redirect downgrade is forbidden")
            _validated_target(target)
            redirected_class = _source_class(
                self.classifier.classify(title=hit.title, url=target, snippet=""),
                "redirect target classification",
            )
            if redirected_class != source_class:
                raise AcquisitionError(
                    "redirect crossed the trusted source-class boundary"
                )
            redirects.append({"from": current, "to": target, "status": status})
            current = target
        else:  # pragma: no cover - loop is explicitly bounded
            raise AcquisitionError("redirect loop exhausted")

        if not raw:
            raise AcquisitionError("source returned an empty body")
        content_type = headers.get("content-type", "")
        lowered_type = content_type.lower()
        lowered_url = current.lower()
        try:
            if "pdf" in lowered_type or lowered_url.endswith(".pdf"):
                raise AcquisitionError("PDF source requires a real PDF parser")
            if "epub" in lowered_type or lowered_url.endswith(".epub"):
                medium = "epub"
            else:
                decoded = raw.decode("utf-8", errors="replace")
                stripped = decoded.lstrip()
                if (
                    lowered_url.endswith(".fb2")
                    or "fictionbook" in lowered_type
                    or stripped.startswith("<FictionBook")
                ):
                    medium = "fb2"
                elif "html" in lowered_type or "<html" in decoded[:1000].lower():
                    medium = "html"
                elif _is_textual_content(content_type, raw):
                    medium = "text"
                else:
                    raise AcquisitionError("binary source has no supported parser")
            normalized = normalize_source_bytes(raw, medium)
        except AcquisitionError:
            raise
        except Exception as exc:
            raise AcquisitionError(f"source normalization failed: {exc}") from exc
        if not normalized.strip():
            raise AcquisitionError("source normalization produced no text")
        return FetchedSource(
            requested_uri=requested,
            final_uri=current,
            raw=raw,
            normalized=normalized,
            medium=medium,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            normalizer_version=self.NORMALIZER_VERSION,
            source_class=source_class,
            classification_identity=self.classifier.stable_identity,
            truncated=False,
            metadata={
                "status": status,
                "content_type": content_type,
                "bytes_read": len(raw),
                "configured_byte_ceiling": ceiling,
                "redirects": redirects,
                "pinned_peer_ips": pinned_peers,
                "classification_identity": self.classifier.stable_identity,
            },
        )


def _is_textual_content(content_type: str, data: bytes) -> bool:
    lowered = content_type.lower()
    if any(token in lowered for token in ("text/", "json", "xml", "javascript")):
        return True
    sample = data[:512]
    if b"\x00" in sample:
        return False
    return bool(sample) and sum(byte in b"\t\n\r" or 32 <= byte < 127 for byte in sample) / len(sample) > 0.75


def _safe_epub_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    if not name or "\x00" in name or "\\" in name:
        raise AcquisitionError("EPUB contains an unsafe member name")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} or ":" in part for part in path.parts):
        raise AcquisitionError(f"EPUB member escapes the archive root: {name!r}")
    mode = (info.external_attr >> 16) & 0xFFFF
    if stat.S_ISLNK(mode):
        raise AcquisitionError(f"EPUB symlink member is forbidden: {name!r}")
    if info.flag_bits & 0x1:
        raise AcquisitionError(f"encrypted EPUB member is forbidden: {name!r}")
    if info.file_size < 0 or info.compress_size < 0:
        raise AcquisitionError("EPUB member has invalid size metadata")
    if info.file_size > EPUB_MAX_ENTRY_BYTES:
        raise AcquisitionError(f"EPUB member exceeds per-entry limit: {name!r}")
    ratio = info.file_size / max(1, info.compress_size)
    if ratio > EPUB_MAX_COMPRESSION_RATIO:
        raise AcquisitionError(f"EPUB member compression ratio is unsafe: {name!r}")


def extract_epub_text_bounded(data: bytes) -> str:
    if type(data) is not bytes or not data:
        raise AcquisitionError("EPUB payload must be non-empty bytes")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > EPUB_MAX_MEMBERS:
                raise AcquisitionError("EPUB has too many archive members")
            for info in infos:
                _safe_epub_member(info)
            documents = [
                info
                for info in infos
                if info.filename.lower().endswith((".xhtml", ".html", ".htm"))
                and not info.filename.lower().endswith("toc.ncx")
                and not info.is_dir()
            ]
            if not documents:
                raise AcquisitionError("EPUB contains no readable document members")
            if len(documents) > EPUB_MAX_DOCUMENT_MEMBERS:
                raise AcquisitionError("EPUB has too many document members")
            total = sum(info.file_size for info in documents)
            if total > EPUB_MAX_TOTAL_BYTES:
                raise AcquisitionError("EPUB uncompressed document total exceeds limit")
            try:
                from EyeOfTerror.Services.Search import web_tools
            except ImportError as exc:  # pragma: no cover - deployment boundary
                raise AcquisitionError("EyeOfTerror Search extractors are unavailable") from exc
            texts: list[str] = []
            consumed = 0
            for info in documents:
                with archive.open(info, "r") as stream:
                    raw = stream.read(info.file_size + 1)
                if len(raw) != info.file_size or len(raw) > EPUB_MAX_ENTRY_BYTES:
                    raise AcquisitionError("EPUB member size changed during extraction")
                consumed += len(raw)
                if consumed > EPUB_MAX_TOTAL_BYTES:
                    raise AcquisitionError("EPUB extraction exceeded cumulative limit")
                decoded = raw.decode("utf-8", errors="replace")
                parser = web_tools.WebTextExtractor()
                parser.feed(decoded)
                _, text = parser.result()
                if text:
                    texts.append(text)
    except AcquisitionError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
        raise AcquisitionError(f"invalid EPUB archive: {exc}") from exc
    normalized = " ".join(" ".join(texts).split())
    if not normalized:
        raise AcquisitionError("EPUB extraction produced no text")
    return normalized


def normalize_source_bytes(raw: bytes, medium: str) -> str:
    """Trusted deterministic and resource-bounded raw-byte normalizer."""

    if type(raw) is not bytes:
        raise TypeError("raw must be bytes")
    if medium == "pdf":
        raise AcquisitionError("PDF source requires a real PDF parser")
    if medium == "epub":
        normalized = extract_epub_text_bounded(raw)
    elif medium == "text":
        normalized = raw.decode("utf-8", errors="replace")
    else:
        try:
            from EyeOfTerror.Services.Search import web_tools
        except ImportError as exc:  # pragma: no cover - deployment boundary
            raise AcquisitionError("EyeOfTerror Search extractors are unavailable") from exc
        decoded = raw.decode("utf-8", errors="replace")
        if medium in {"html", "fb2"}:
            parser = web_tools.WebTextExtractor()
            parser.feed(decoded)
            _, normalized = parser.result()
        else:
            raise AcquisitionError(f"unsupported normalizer medium: {medium!r}")
    return normalized.replace("\r\n", "\n").replace("\r", "\n").strip()


def default_registered_normalizer() -> RegisteredNormalizer:
    return RegisteredNormalizer(
        id=EyeWebFetchAdapter.NORMALIZER_VERSION,
        media=frozenset({"text", "html", "epub", "fb2"}),
        callback=normalize_source_bytes,
    )


def exact_locator(medium: str, start: int, end: int) -> Locator:
    if medium == "text":
        return TextLocator(start, end)
    if medium == "html":
        return HtmlLocator(CANONICAL_HTML_SELECTOR, start, end)
    if medium == "pdf":
        return PdfLocator(1, start, end)
    if medium == "epub":
        return EpubLocator(0, f"{CANONICAL_EPUB_HREF_PREFIX}0", start, end)
    if medium == "fb2":
        return Fb2Locator(0, 0, start, end)
    raise ValueError(f"unsupported source medium: {medium!r}")


def locate_exact_excerpt(
    text: str,
    excerpt: str,
    start: int | None = None,
    end: int | None = None,
) -> tuple[int, int]:
    if type(text) is not str or type(excerpt) is not str or not excerpt:
        raise ValueError("text and non-empty excerpt must be strings")
    if start is None and end is None:
        position = text.find(excerpt)
        if position < 0:
            raise ValueError("proposed excerpt does not exist in the source snapshot")
        return position, position + len(excerpt)
    if type(start) is not int or type(end) is not int:
        raise ValueError("excerpt start and end must both be integers")
    if start < 0 or end <= start or end > len(text):
        raise ValueError("proposed excerpt locator is outside the source snapshot")
    if text[start:end] != excerpt:
        raise ValueError("proposed excerpt does not exactly match its locator")
    return start, end


DefaultSearchAdapter = EyeWebSearchAdapter
DefaultFetchAdapter = EyeWebFetchAdapter


__all__ = [
    "AcquisitionError",
    "ConfiguredDomainSourceClassifier",
    "DefaultFetchAdapter",
    "DefaultSearchAdapter",
    "EPUB_MAX_COMPRESSION_RATIO",
    "EPUB_MAX_DOCUMENT_MEMBERS",
    "EPUB_MAX_ENTRY_BYTES",
    "EPUB_MAX_MEMBERS",
    "EPUB_MAX_TOTAL_BYTES",
    "EyeWebFetchAdapter",
    "EyeWebSearchAdapter",
    "FetchAdapter",
    "FetchedSource",
    "ResearchToolError",
    "SearchAdapter",
    "SearchHit",
    "SearchUnavailable",
    "SourceClassifier",
    "default_registered_normalizer",
    "exact_locator",
    "extract_epub_text_bounded",
    "locate_exact_excerpt",
    "normalize_source_bytes",
    "resolve_public_addresses",
]
