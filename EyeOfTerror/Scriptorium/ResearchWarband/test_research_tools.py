from __future__ import annotations

import http.client
import socket
import threading
import unittest
from unittest.mock import patch

from ResearchWarband.research_tools import (
    AcquisitionError,
    ConfiguredDomainSourceClassifier,
    EyeWebFetchAdapter,
    EyeWebSearchAdapter,
    _PinnedHTTPSConnection,
    _attest_response_peer,
)


class _PlaintextTLSContext:
    """Exercise HTTPSConnection's stdlib response ownership without test PKI."""

    def __init__(self) -> None:
        self.server_names: list[str] = []

    def wrap_socket(self, sock: socket.socket, *, server_hostname: str) -> socket.socket:
        self.server_names.append(server_hostname)
        return sock


class _OneShotHTTPServer:
    def __init__(self, body: bytes = b"ok") -> None:
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = int(self.listener.getsockname()[1])
        self.body = body
        self.request = b""
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "_OneShotHTTPServer":
        self.thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.thread.join(timeout=3)
        self.listener.close()
        if self.thread.is_alive():
            raise AssertionError("test HTTP server did not stop")
        if self.error is not None:
            raise self.error

    def _serve(self) -> None:
        try:
            client, _ = self.listener.accept()
            with client:
                while b"\r\n\r\n" not in self.request:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    self.request += chunk
                client.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    + f"Content-Length: {len(self.body)}\r\n".encode("ascii")
                    + b"Connection: close\r\n\r\n"
                    + self.body
                )
        except BaseException as exc:  # pragma: no cover - surfaced by __exit__
            self.error = exc


class PinnedResponsePeerTests(unittest.TestCase):
    def test_exact_caller_source_uses_trusted_classifier_without_search_provider(self) -> None:
        classifier = ConfiguredDomainSourceClassifier(
            {
                "version": 1,
                "exact": {"www.rfc-editor.org": "standards_specification"},
                "suffix": {},
            }
        )
        adapter = EyeWebSearchAdapter(classifier=classifier)
        url = "https://www.rfc-editor.org/rfc/rfc1149.txt"

        hit = adapter.classify_exact_source(url)

        self.assertEqual(url, hit.url)
        self.assertEqual("standards_specification", hit.source_class)
        self.assertEqual(classifier.stable_identity, hit.classification_identity)

    def test_exact_caller_source_rejects_credentials_and_non_http_scheme(self) -> None:
        adapter = EyeWebSearchAdapter()
        for url in ("https://user:secret@example.test/x", "file:///etc/passwd"):
            with self.subTest(url=url), self.assertRaises(AcquisitionError):
                adapter.classify_exact_source(url)

    def test_https_response_socket_is_attested_after_connection_releases_it(self) -> None:
        context = _PlaintextTLSContext()
        with _OneShotHTTPServer() as server, patch(
            "ResearchWarband.research_tools.ssl.create_default_context",
            return_value=context,
        ):
            connection = _PinnedHTTPSConnection(
                "official.example", server.port, "127.0.0.1", 2.0
            )
            connection.request(
                "GET", "/evidence", headers={"Connection": "close"}
            )
            response = connection.getresponse()

            # This is the Python 3.12 behavior that caused the live failure:
            # getresponse() moved ownership into HTTPResponse.fp.raw._sock.
            self.assertIsNone(connection.sock)
            self.assertIsInstance(response, http.client.HTTPResponse)
            self.assertTrue(hasattr(response.fp.raw._sock, "getpeername"))
            _attest_response_peer(connection, response, "127.0.0.1")
            self.assertEqual(response.read(), b"ok")
            response.close()
            connection.close()

        self.assertEqual(context.server_names, ["official.example"])

    def test_open_once_uses_response_socket_and_preserves_host_and_sni(self) -> None:
        context = _PlaintextTLSContext()
        with _OneShotHTTPServer(b"official evidence") as server, patch(
            "ResearchWarband.research_tools.ssl.create_default_context",
            return_value=context,
        ), patch(
            "ResearchWarband.research_tools.resolve_public_addresses",
            return_value=("127.0.0.1",),
        ):
            status, _, raw, peer = EyeWebFetchAdapter()._open_once(
                f"https://official.example:{server.port}/pep", 4096
            )

        self.assertEqual(status, 200)
        self.assertEqual(raw, b"official evidence")
        self.assertEqual(peer, "127.0.0.1")
        self.assertIn(
            f"Host: official.example:{server.port}\r\n".encode("ascii"),
            server.request,
        )
        self.assertEqual(context.server_names, ["official.example"])

    def test_released_connection_cannot_hide_wrong_actual_peer(self) -> None:
        class _MisdirectedConnection(http.client.HTTPConnection):
            def __init__(self, server_port: int) -> None:
                super().__init__("official.example", port=443, timeout=2.0)
                self.server_port = server_port

            def connect(self) -> None:
                self.sock = socket.create_connection(
                    ("127.0.0.1", self.server_port), self.timeout
                )

        with _OneShotHTTPServer() as server, patch(
            "ResearchWarband.research_tools.resolve_public_addresses",
            return_value=("93.184.216.34",),
        ):
            adapter = EyeWebFetchAdapter(
                connection_factory=lambda *_: _MisdirectedConnection(server.port)
            )
            with self.assertRaisesRegex(
                AcquisitionError,
                "connected peer 127.0.0.1 does not match pinned address 93.184.216.34",
            ):
                adapter._open_once("https://official.example/evidence", 4096)


if __name__ == "__main__":
    unittest.main()
