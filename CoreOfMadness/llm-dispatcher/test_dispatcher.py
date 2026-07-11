import json
import socket
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import dispatcher


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


class JsonBackend(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        server = self.server
        with server.state_lock:
            server.request_count += 1
            server.started += 1
            server.active += 1
            server.max_active = max(server.max_active, server.active)
            if server.started >= server.expected:
                server.all_started.set()
        try:
            server.release_responses.wait(timeout=3)
            body = json.dumps({"backend": server.backend_name}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            with server.state_lock:
                server.active -= 1


class StreamingBackend(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _chunk(self, payload):
        self.wfile.write(f"{len(payload):X}\r\n".encode("ascii"))
        self.wfile.write(payload + b"\r\n")
        self.wfile.flush()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self._chunk(b"data: first\n\n")
        self.server.first_event_sent.set()
        if self.server.break_after_first:
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        self.server.release_second_event.wait(timeout=3)
        self._chunk(b"data: second\n\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


class DispatcherTest(unittest.TestCase):
    def setUp(self):
        self.original_routes = dict(dispatcher.ROUTE_UPSTREAMS)
        self.original_gates = dispatcher.GATES
        self.servers = []
        self.server_threads = []
        self.workers = []

    def tearDown(self):
        try:
            for server in self.servers:
                for name in ("release_responses", "release_second_event"):
                    event = getattr(server, name, None)
                    if event is not None:
                        event.set()
            for worker in self.workers:
                worker.join(timeout=4)
            for server in reversed(self.servers):
                server.shutdown()
                server.server_close()
            for thread in self.server_threads:
                thread.join(timeout=2)
        finally:
            dispatcher.GATES = self.original_gates
            dispatcher.ROUTE_UPSTREAMS.clear()
            dispatcher.ROUTE_UPSTREAMS.update(self.original_routes)

    def serve(self, handler, **attrs):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        for name, value in attrs.items():
            setattr(server, name, value)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.servers.append(server)
        self.server_threads.append(thread)
        return server

    def backend(self, name, *, blocked=False, expected=1):
        release = threading.Event()
        if not blocked:
            release.set()
        return self.serve(
            JsonBackend,
            backend_name=name,
            state_lock=threading.Lock(),
            request_count=0,
            started=0,
            active=0,
            max_active=0,
            expected=expected,
            all_started=threading.Event(),
            release_responses=release,
        )

    def stream_backend(self, *, broken=False):
        return self.serve(
            StreamingBackend,
            break_after_first=broken,
            first_event_sent=threading.Event(),
            release_second_event=threading.Event(),
        )

    def proxy(self):
        return self.serve(dispatcher.DispatchHandler)

    def worker(self, target):
        thread = threading.Thread(target=target)
        thread.start()
        self.workers.append(thread)
        return thread

    @staticmethod
    def body(model):
        return json.dumps({"model": model, "messages": []}).encode("utf-8")

    def open_json(self, proxy, model, route, *, priority=None, timeout=3):
        headers = {"Content-Type": "application/json", "X-LLM-Route": route}
        if priority is not None:
            headers["X-LLM-Priority"] = priority
        request = urllib.request.Request(
            f"http://127.0.0.1:{proxy.server_port}/v1/chat/completions",
            data=self.body(model),
            headers=headers,
        )
        return urllib.request.urlopen(request, timeout=timeout)

    def test_four_slots_run_before_a_fifth_waits(self):
        gate = dispatcher.PriorityGate(4, max_queue=4)
        for _ in range(4):
            self.assertEqual(gate.acquire(dispatcher.DEFAULT_PRIORITY), 0.0)
        acquired = threading.Event()

        def fifth():
            gate.acquire(dispatcher.DEFAULT_PRIORITY, timeout=2)
            acquired.set()
            gate.release()

        thread = self.worker(fifth)
        self.assertTrue(wait_until(lambda: gate.snapshot()["queued"] == 1))
        self.assertFalse(acquired.is_set())
        gate.release()
        self.assertTrue(acquired.wait(timeout=1))
        thread.join(timeout=1)
        for _ in range(3):
            gate.release()
        self.assertEqual((gate.snapshot()["active"], gate.snapshot()["free"]), (0, 4))

    def test_bounded_queue_and_timeout_do_not_leak_slots(self):
        gate = dispatcher.PriorityGate(1, max_queue=1)
        gate.acquire(dispatcher.DEFAULT_PRIORITY)

        def waiter():
            gate.acquire(dispatcher.DEFAULT_PRIORITY, timeout=2)
            gate.release()

        thread = self.worker(waiter)
        self.assertTrue(wait_until(lambda: gate.snapshot()["queued"] == 1))
        with self.assertRaises(dispatcher.QueueFullError):
            gate.acquire(dispatcher.DEFAULT_PRIORITY)
        gate.release()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(gate.snapshot()["active"], 0)
        gate.acquire(dispatcher.DEFAULT_PRIORITY)
        with self.assertRaises(dispatcher.QueueWaitTimeoutError):
            gate.acquire(dispatcher.DEFAULT_PRIORITY, timeout=0.02)
        self.assertEqual(gate.snapshot()["timed_out_total"], 1)
        self.assertEqual(gate.snapshot()["queued"], 0)
        self.assertEqual(gate.snapshot()["active"], 1)
        gate.release()
        self.assertEqual(gate.snapshot()["active"], 0)

    def test_aging_promotes_old_background_ahead_of_new_librarian(self):
        gate = dispatcher.PriorityGate(1, max_queue=2, aging_seconds=0.01)
        gate.acquire(dispatcher.DEFAULT_PRIORITY)
        order, errors = [], []

        def wait(priority):
            try:
                gate.acquire(priority, timeout=2)
                order.append(priority)
                gate.release()
            except Exception as exc:
                errors.append(exc)

        old = self.worker(lambda: wait(dispatcher.PRIORITIES["other"]))
        self.assertTrue(wait_until(lambda: gate.snapshot()["queued"] == 1))
        time.sleep(0.025)
        new = self.worker(lambda: wait(dispatcher.PRIORITIES["librarian"]))
        self.assertTrue(wait_until(lambda: gate.snapshot()["queued"] == 2))
        gate.release()
        old.join(timeout=2)
        new.join(timeout=2)
        self.assertEqual(errors, [])
        self.assertEqual(order, [dispatcher.PRIORITIES["other"], dispatcher.PRIORITIES["librarian"]])

    def test_route_model_selection_and_health_caps(self):
        explicit = dispatcher.select_upstream(
            {"X-LLM-Route": "qwen"}, self.body(dispatcher.GEMMA_MODEL)
        )
        self.assertEqual(explicit, (dispatcher.QWEN_UPSTREAM, "qwen"))
        self.assertEqual(
            dispatcher.select_upstream({}, self.body(dispatcher.QWEN_MODEL)),
            (dispatcher.QWEN_UPSTREAM, "qwen"),
        )
        self.assertEqual(
            dispatcher.select_upstream({}, self.body(dispatcher.GEMMA_MODEL)),
            (dispatcher.GEMMA_UPSTREAM, "gemma"),
        )
        self.assertEqual(dispatcher.select_upstream({}, b"bad-json"), (dispatcher.UPSTREAM, "legacy"))
        health = dispatcher.dispatcher_health()
        self.assertEqual(health["routes"]["gemma"]["capacity"], dispatcher.GEMMA_CONCURRENCY)
        self.assertEqual(health["routes"]["qwen"]["capacity"], dispatcher.QWEN_CONCURRENCY)

    def test_proxy_forwards_four_gemma_requests_concurrently(self):
        backend = self.backend("gemma", blocked=True, expected=4)
        proxy = self.proxy()
        gate = dispatcher.PriorityGate(4, max_queue=8)
        dispatcher.ROUTE_UPSTREAMS["gemma"] = f"http://127.0.0.1:{backend.server_port}"
        dispatcher.GATES = {"gemma": gate, "qwen": dispatcher.PriorityGate(1, max_queue=2)}
        errors = []

        def call():
            try:
                with self.open_json(proxy, dispatcher.GEMMA_MODEL, "gemma") as response:
                    json.load(response)
            except Exception as exc:
                errors.append(exc)

        clients = [self.worker(call) for _ in range(4)]
        self.assertTrue(backend.all_started.wait(timeout=2))
        self.assertEqual(backend.max_active, 4)
        backend.release_responses.set()
        for client in clients:
            client.join(timeout=3)
        self.assertEqual(errors, [])
        self.assertEqual(gate.snapshot()["active"], 0)

    def test_blocked_qwen_lane_does_not_delay_gemma(self):
        qwen = self.backend("qwen", blocked=True)
        gemma = self.backend("gemma")
        proxy = self.proxy()
        dispatcher.ROUTE_UPSTREAMS.update(
            qwen=f"http://127.0.0.1:{qwen.server_port}",
            gemma=f"http://127.0.0.1:{gemma.server_port}",
        )
        dispatcher.GATES = {
            "gemma": dispatcher.PriorityGate(4, max_queue=8),
            "qwen": dispatcher.PriorityGate(1, max_queue=2),
        }
        errors = []

        def hold_qwen():
            try:
                with self.open_json(proxy, dispatcher.QWEN_MODEL, "qwen") as response:
                    json.load(response)
            except Exception as exc:
                errors.append(exc)

        background = self.worker(hold_qwen)
        self.assertTrue(qwen.all_started.wait(timeout=1))
        with self.open_json(proxy, dispatcher.GEMMA_MODEL, "gemma", timeout=1) as response:
            self.assertEqual(json.load(response)["backend"], "gemma")
        self.assertTrue(background.is_alive())
        qwen.release_responses.set()
        background.join(timeout=3)
        self.assertEqual(errors, [])

    def test_qwen_rejects_chat_before_gate_but_allows_background(self):
        backend = self.backend("qwen")
        proxy = self.proxy()
        gate = dispatcher.PriorityGate(1, max_queue=2)
        dispatcher.ROUTE_UPSTREAMS["qwen"] = f"http://127.0.0.1:{backend.server_port}"
        dispatcher.GATES = {"gemma": dispatcher.PriorityGate(1, max_queue=2), "qwen": gate}
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.open_json(proxy, dispatcher.QWEN_MODEL, "qwen", priority="chat")
        try:
            self.assertEqual(raised.exception.code, 409)
            self.assertEqual(json.load(raised.exception)["error"], "qwen_background_only")
        finally:
            raised.exception.close()
        self.assertEqual((gate.snapshot()["admitted_total"], backend.request_count), (0, 0))
        with self.open_json(proxy, dispatcher.QWEN_MODEL, "qwen", priority="other") as response:
            self.assertEqual(json.load(response)["backend"], "qwen")
        self.assertEqual((gate.snapshot()["completed_total"], backend.request_count), (1, 1))

    def test_sse_first_event_arrives_before_second_or_eof(self):
        backend = self.stream_backend()
        proxy = self.proxy()
        dispatcher.ROUTE_UPSTREAMS["gemma"] = f"http://127.0.0.1:{backend.server_port}"
        dispatcher.GATES = {
            "gemma": dispatcher.PriorityGate(1, max_queue=2),
            "qwen": dispatcher.PriorityGate(1, max_queue=2),
        }
        received, errors = [], []
        first_received = threading.Event()

        def read_stream():
            try:
                with self.open_json(proxy, dispatcher.GEMMA_MODEL, "gemma") as response:
                    received.append(response.readline())
                    first_received.set()
                    response.read()
            except Exception as exc:
                errors.append(exc)

        client = self.worker(read_stream)
        self.assertTrue(backend.first_event_sent.wait(timeout=2))
        self.assertFalse(backend.release_second_event.is_set())
        self.assertTrue(first_received.wait(timeout=2))
        self.assertEqual(received, [b"data: first\n"])
        backend.release_second_event.set()
        client.join(timeout=3)
        self.assertEqual(errors, [])

    def test_sse_failure_after_commit_never_injects_second_status(self):
        backend = self.stream_backend(broken=True)
        proxy = self.proxy()
        gate = dispatcher.PriorityGate(1, max_queue=2)
        dispatcher.ROUTE_UPSTREAMS["gemma"] = f"http://127.0.0.1:{backend.server_port}"
        dispatcher.GATES = {"gemma": gate, "qwen": dispatcher.PriorityGate(1, max_queue=2)}
        body = self.body(dispatcher.GEMMA_MODEL)
        head = (
            f"POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1:{proxy.server_port}\r\n"
            "Content-Type: application/json\r\nX-LLM-Route: gemma\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        ).encode("ascii")
        with socket.create_connection(("127.0.0.1", proxy.server_port), timeout=2) as client:
            client.settimeout(2)
            client.sendall(head + body)
            parts = []
            while chunk := client.recv(4096):
                parts.append(chunk)
        raw = b"".join(parts)
        self.assertTrue(raw.startswith(b"HTTP/1.1 200"), raw)
        self.assertIn(b"data: first\n\n", raw)
        self.assertNotIn(b"502 Bad Gateway", raw)
        self.assertEqual(raw.count(b"HTTP/1.1 "), 1)
        self.assertTrue(wait_until(lambda: gate.snapshot()["active"] == 0))


if __name__ == "__main__":
    unittest.main()
