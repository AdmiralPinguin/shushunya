#!/usr/bin/env python3
"""Priority dispatcher in front of the single llama.cpp server.

Everything that needs the model — the main chat, the librarian, Vox, the
turn controller, and every EyeOfTerror governor/worker — points at this proxy
instead of llama.cpp directly, so nothing bypasses the queue. One GPU, one
model: requests are admitted by priority, not first-come.

Priority (lower number wins), set by the caller via the X-LLM-Priority header:
  librarian  -> 0   (memory consolidation outranks a fresh answer: the next
                     turn should see up-to-date memory)
  chat       -> 1   (the owner's live answer outranks all brigade work)
  other      -> 2   (governors, workers, Vox, turn controller; FIFO among them)

Non-preemptive: a running request is never interrupted (a llama.cpp generation
can't be paused mid-token), but the moment a slot frees, the highest-priority
waiter goes next — so chat and librarian "jump to the front of the queue".
"""
from __future__ import annotations

import heapq
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ.get("LLM_DISPATCH_UPSTREAM", "http://127.0.0.1:8080").rstrip("/")
HOST = os.environ.get("LLM_DISPATCH_HOST", "127.0.0.1")
PORT = int(os.environ.get("LLM_DISPATCH_PORT", "8079"))
CONCURRENCY = int(os.environ.get("LLM_DISPATCH_CONCURRENCY", "1"))
PRIORITIES = {"librarian": 0, "chat": 1, "other": 2}
DEFAULT_PRIORITY = PRIORITIES["other"]
# Paths that must NOT be gated (cheap, non-generating) so health checks and the
# model list never wait behind a generation.
UNGATED_PREFIXES = ("/health", "/v1/models")


class PriorityGate:
    """Admit up to `concurrency` requests at once; when a slot frees, hand it to
    the lowest-priority-number (= highest priority) waiter, FIFO within a tier."""

    def __init__(self, concurrency: int) -> None:
        self._lock = threading.Lock()
        self._free = max(1, concurrency)
        self._heap: list[tuple[int, int, threading.Event]] = []
        self._seq = 0

    def acquire(self, priority: int) -> None:
        with self._lock:
            if self._free > 0:
                self._free -= 1
                return
            self._seq += 1
            event = threading.Event()
            heapq.heappush(self._heap, (priority, self._seq, event))
        event.wait()  # the slot is handed to us directly on release()

    def release(self) -> None:
        with self._lock:
            if self._heap:
                _, _, event = heapq.heappop(self._heap)
                event.set()  # pass the slot straight to the next waiter
            else:
                self._free += 1


GATE = PriorityGate(CONCURRENCY)


class DispatchHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # noqa: ANN002 - silence default logging
        pass

    def _priority(self) -> int:
        raw = str(self.headers.get("X-LLM-Priority") or "").strip().lower()
        return PRIORITIES.get(raw, DEFAULT_PRIORITY)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length > 0 else b""

    def _forward(self, method: str) -> None:
        body = self._read_body()
        ungated = any(self.path.startswith(prefix) for prefix in UNGATED_PREFIXES)
        if not ungated:
            GATE.acquire(self._priority())
        try:
            headers = {}
            for key in ("Content-Type", "Authorization", "Accept"):
                if self.headers.get(key):
                    headers[key] = self.headers.get(key)
            request = urllib.request.Request(f"{UPSTREAM}{self.path}", data=body if body else None, headers=headers, method=method)
            with urllib.request.urlopen(request, timeout=1800) as upstream:
                self.send_response(upstream.status)
                passthrough = {}
                for key, value in upstream.headers.items():
                    low = key.lower()
                    if low in ("content-length", "transfer-encoding", "connection"):
                        continue
                    passthrough[key] = value
                is_stream = "text/event-stream" in str(upstream.headers.get("Content-Type") or "")
                if is_stream:
                    self.send_header("Transfer-Encoding", "chunked")
                for key, value in passthrough.items():
                    self.send_header(key, value)
                if not is_stream:
                    # Buffer the whole body so we can set Content-Length.
                    payload = upstream.read()
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.end_headers()
                while True:
                    chunk = upstream.read(4096)
                    if not chunk:
                        break
                    size = f"{len(chunk):X}\r\n".encode("ascii")
                    self.wfile.write(size + chunk + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", exc.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:  # noqa: BLE001 - upstream/connectivity failures become 502s
            message = str(exc).encode("utf-8")
            try:
                self.send_response(502)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(message)))
                self.end_headers()
                self.wfile.write(message)
            except Exception:  # noqa: BLE001 - client already gone
                pass
        finally:
            if not ungated:
                GATE.release()

    def do_GET(self) -> None:  # noqa: N802
        self._forward("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._forward("POST")


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), DispatchHandler)
    print(f"LLM dispatcher on {HOST}:{PORT} -> {UPSTREAM} (concurrency={CONCURRENCY})", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
