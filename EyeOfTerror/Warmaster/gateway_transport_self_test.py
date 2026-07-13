from __future__ import annotations

import io
import os
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from eye_of_terror import gateway_util


class _Handler(BaseHTTPRequestHandler):
    paths: list[str] = []

    def log_message(self, _format: str, *_args) -> None:
        return None

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        type(self).paths.append(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if self.path == "/redirect":
            self.send_response(307)
            self.send_header("Location", "/ok")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
            return
        if self.path == "/text":
            body = b'{"ok":true}'
            content_type = "text/plain"
        elif self.path == "/duplicate":
            body = b'{"ok":true,"ok":false}'
            content_type = "application/json"
        else:
            body = b'{"ok":true}'
            content_type = "application/json; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class GatewayTransportTest(unittest.TestCase):
    def test_loopback_post_is_proxyless_and_refuses_redirect_or_non_json(self) -> None:
        _Handler.paths = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with patch.dict(
                os.environ,
                {
                    "HTTP_PROXY": "http://127.0.0.1:1",
                    "HTTPS_PROXY": "http://127.0.0.1:1",
                    "NO_PROXY": "",
                },
            ):
                self.assertEqual(
                    gateway_util.post_json(
                        base + "/ok",
                        {"request": True},
                        headers={"Authorization": "Bearer secret"},
                    ),
                    {"ok": True},
                )
                for path in ("/redirect", "/text", "/duplicate"):
                    with self.subTest(path=path):
                        with self.assertRaises(ValueError):
                            gateway_util.post_json(
                                base + path,
                                {"request": True},
                                headers={"Authorization": "Bearer secret"},
                            )
            self.assertNotIn("/ok", _Handler.paths[1:])
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def test_governor_error_payload_is_strict_and_bounded(self) -> None:
        from eye_of_terror import task_prepare

        for body in (
            b'{"error":"one","error":"two"}',
            b'{"error":NaN}',
            b"{" + (b"x" * 1_000_001),
        ):
            error = urllib.error.HTTPError(
                "http://127.0.0.1:7101/prepare_run",
                409,
                "conflict",
                {"Content-Type": "application/json"},
                io.BytesIO(body),
            )
            self.assertEqual(task_prepare._bounded_http_error_payload(error), {})

if __name__ == "__main__":
    unittest.main()
