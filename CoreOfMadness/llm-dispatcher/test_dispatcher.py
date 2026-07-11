import json
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import dispatcher


class BackendStub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        body = json.dumps({"backend": self.server.backend_name}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ModelRoutingTest(unittest.TestCase):
    @staticmethod
    def body(model):
        return json.dumps({"model": model, "messages": []}).encode("utf-8")

    def test_explicit_allowlisted_route_wins(self):
        upstream, route = dispatcher.select_upstream(
            {"X-LLM-Route": "qwen"},
            self.body(dispatcher.GEMMA_MODEL),
        )
        self.assertEqual((upstream, route), (dispatcher.QWEN_UPSTREAM, "qwen"))

    def test_model_id_routes_without_header_for_legacy_clients(self):
        qwen = dispatcher.select_upstream({}, self.body(dispatcher.QWEN_MODEL))
        gemma = dispatcher.select_upstream({}, self.body(dispatcher.GEMMA_MODEL))
        self.assertEqual(qwen, (dispatcher.QWEN_UPSTREAM, "qwen"))
        self.assertEqual(gemma, (dispatcher.GEMMA_UPSTREAM, "gemma"))

    def test_unknown_or_malformed_input_uses_legacy_upstream(self):
        unknown_header = dispatcher.select_upstream(
            {"X-LLM-Route": "http://attacker.invalid"},
            self.body("unconfigured-model"),
        )
        malformed = dispatcher.select_upstream({}, b"not-json")
        self.assertEqual(unknown_header, (dispatcher.UPSTREAM, "legacy"))
        self.assertEqual(malformed, (dispatcher.UPSTREAM, "legacy"))

    def test_http_proxy_reaches_the_selected_backend(self):
        servers = []
        threads = []
        original_routes = dict(dispatcher.ROUTE_UPSTREAMS)
        try:
            for name in ("gemma", "qwen"):
                server = ThreadingHTTPServer(("127.0.0.1", 0), BackendStub)
                server.backend_name = name
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                servers.append(server)
                threads.append(thread)
                dispatcher.ROUTE_UPSTREAMS[name] = f"http://127.0.0.1:{server.server_port}"

            proxy = ThreadingHTTPServer(("127.0.0.1", 0), dispatcher.DispatchHandler)
            proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
            proxy_thread.start()
            servers.append(proxy)
            threads.append(proxy_thread)

            for name in ("gemma", "qwen"):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{proxy.server_port}/v1/chat/completions",
                    data=self.body("ignored-because-route-is-explicit"),
                    headers={
                        "Content-Type": "application/json",
                        "X-LLM-Route": name,
                    },
                )
                with urllib.request.urlopen(request, timeout=3) as response:
                    payload = json.load(response)
                self.assertEqual(payload["backend"], name)
        finally:
            dispatcher.ROUTE_UPSTREAMS.clear()
            dispatcher.ROUTE_UPSTREAMS.update(original_routes)
            for server in reversed(servers):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
