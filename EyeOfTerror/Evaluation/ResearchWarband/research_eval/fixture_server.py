"""Loopback-only HTTP gateway for deterministic search and source acquisition."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .fixtures import LoadedFixture


class FixtureServer:
    def __init__(self, fixture: LoadedFixture) -> None:
        self.fixture = fixture
        self.access_log: list[dict[str, object]] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("fixture server is not running")
        return f"http://127.0.0.1:{self._server.server_port}"

    def __enter__(self) -> "FixtureServer":
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ResearchFixture/1"

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _record(self, status: int) -> None:
                with outer._lock:
                    outer.access_log.append({"method": self.command, "path": self.path, "status": status})

            def _send(self, status: int, body: bytes, content_type: str, *, sha256: str = "") -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                if sha256:
                    self.send_header("X-Eval-Snapshot-Sha256", sha256)
                    self.send_header("ETag", f'"sha256:{sha256}"')
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
                self._record(status)

            def do_HEAD(self) -> None:  # noqa: N802
                self.do_GET()

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                if parsed.path == "/health":
                    body = json.dumps({"status": "ok", "bundle_id": outer.fixture.data["bundle_id"]}, separators=(",", ":")).encode()
                    self._send(200, body, "application/json")
                    return
                if parsed.path == "/search":
                    query = (parse_qs(parsed.query).get("q") or [""])[0].casefold()
                    source_ids: list[str] = []
                    for rule in outer.fixture.data["search_rules"]:
                        if all(term.casefold() in query for term in rule["query_terms"]):
                            for source_id in rule["source_ids"]:
                                if source_id not in source_ids:
                                    source_ids.append(source_id)
                    results = []
                    for source_id in source_ids:
                        document = outer.fixture.document(source_id)
                        results.append({
                            "source_id": source_id,
                            "url": outer.base_url + document.data["route"],
                            "original_url": document.data["original_url"],
                        })
                    body = json.dumps({"query": query, "closed_world": True, "results": results}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    self._send(200, body, "application/json; charset=utf-8")
                    return
                for document in outer.fixture.documents.values():
                    if parsed.path == document.data["route"]:
                        self._send(200, document.raw, document.data["mime"], sha256=document.data["raw_sha256"])
                        return
                for route in outer.fixture.data["explicit_routes"]:
                    if parsed.path == route["path"]:
                        self._send(int(route["status"]), b"not found\n", "text/plain; charset=utf-8")
                        return
                self._send(404, b"not found\n", "text/plain; charset=utf-8")

            def do_POST(self) -> None:  # noqa: N802
                self._send(405, b"method not allowed\n", "text/plain; charset=utf-8")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, name="research-fixture", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
