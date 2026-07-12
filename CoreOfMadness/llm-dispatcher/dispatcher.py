#!/usr/bin/env python3
"""Priority dispatcher in front of the configured OpenAI-compatible servers.

Interactive chat, memory, Vox, and Gemma-based governors use this front door.
Skitarii may call its physically separate Qwen backend directly for detached
background missions; Qwen requests that do enter this dispatcher use their own
FIFO lane. Requests are sent only to allow-listed model hosts.

Priority (lower number wins), set by the caller via the X-LLM-Priority header:
  librarian  -> 0   (memory consolidation outranks a fresh answer: the next
                     turn should see up-to-date memory)
  chat       -> 1   (the owner's live answer outranks all brigade work)
  other      -> 2   (governors, workers, Vox, turn controller; FIFO among them)

Gemma/vLLM and Qwen use independent admission lanes.  Gemma is the interactive
lane (four concurrent requests by default); Qwen is a long-running background
lane, so a code generation can never consume an interactive slot.  Admission is
non-preemptive.  The Gemma queue ages waiting requests to prevent a continuous
stream of high-priority work from starving governors forever.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ.get("LLM_DISPATCH_UPSTREAM", "http://127.0.0.1:8080").rstrip("/")
GEMMA_UPSTREAM = os.environ.get("GEMMA_LLM_BASE_URL", UPSTREAM).rstrip("/")
QWEN_UPSTREAM = os.environ.get("QWEN_LLM_BASE_URL", "http://127.0.0.1:8081").rstrip("/")
GEMMA_MODEL = os.environ.get("GEMMA_LLM_MODEL", "gemma-4-12b-it-UD-Q5_K_XL.gguf").strip()
QWEN_MODEL = os.environ.get("QWEN_LLM_MODEL", "Qwen3-Coder-Next-Q6_K-00001-of-00004.gguf").strip()
ROUTE_UPSTREAMS = {
    "gemma": GEMMA_UPSTREAM,
    "qwen": QWEN_UPSTREAM,
}
MODEL_ROUTES = {
    model: route
    for model, route in ((GEMMA_MODEL, "gemma"), (QWEN_MODEL, "qwen"))
    if model
}
HOST = os.environ.get("LLM_DISPATCH_HOST", "127.0.0.1")
PORT = int(os.environ.get("LLM_DISPATCH_PORT", "8079"))
PRIORITIES = {"librarian": 0, "chat": 1, "other": 2}
DEFAULT_PRIORITY = PRIORITIES["other"]


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    try:
        value = default if raw is None or not raw.strip() else int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}")
    return value


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    try:
        value = default if raw is None or not raw.strip() else float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}")
    return value


# LLM_DISPATCH_CONCURRENCY remains a compatibility fallback for old launchers,
# but new deployments should configure each physical backend independently.
LEGACY_CONCURRENCY = _env_int("LLM_DISPATCH_CONCURRENCY", 4, minimum=1)
GEMMA_CONCURRENCY = _env_int(
    "LLM_DISPATCH_GEMMA_CONCURRENCY", LEGACY_CONCURRENCY, minimum=1,
)
QWEN_CONCURRENCY = _env_int("LLM_DISPATCH_QWEN_CONCURRENCY", 1, minimum=1)
GEMMA_MAX_QUEUE = _env_int("LLM_DISPATCH_GEMMA_MAX_QUEUE", 128)
QWEN_MAX_QUEUE = _env_int("LLM_DISPATCH_QWEN_MAX_QUEUE", 32)
GEMMA_PRIORITY_AGING_SEC = _env_float(
    "LLM_DISPATCH_GEMMA_PRIORITY_AGING_SEC", 30.0, minimum=0.0,
)
GEMMA_QUEUE_TIMEOUT_SEC = _env_float(
    "LLM_DISPATCH_GEMMA_QUEUE_TIMEOUT_SEC", 300.0, minimum=0.0,
)
QWEN_QUEUE_TIMEOUT_SEC = _env_float(
    "LLM_DISPATCH_QWEN_QUEUE_TIMEOUT_SEC", 0.0, minimum=0.0,
)
GEMMA_TIMEOUT_SEC = _env_float("LLM_DISPATCH_GEMMA_TIMEOUT_SEC", 600.0, minimum=1.0)
QWEN_TIMEOUT_SEC = _env_float("LLM_DISPATCH_QWEN_TIMEOUT_SEC", 90000.0, minimum=1.0)
LISTEN_BACKLOG = _env_int("LLM_DISPATCH_LISTEN_BACKLOG", 128, minimum=4)

# Cheap, non-generating upstream requests must never wait behind a generation.
UNGATED_PATHS = frozenset(("/health", "/v1/models"))
DISPATCHER_HEALTH_PATH = "/dispatcher/health"


class QueueFullError(RuntimeError):
    """Raised before forwarding when a bounded lane has no queue capacity."""


class QueueWaitTimeoutError(RuntimeError):
    """Raised when a request could not acquire its lane before its deadline."""


class _Waiter:
    __slots__ = ("admitted", "enqueued_at", "event", "priority", "seq")

    def __init__(self, priority: int, seq: int) -> None:
        self.priority = priority
        self.seq = seq
        self.enqueued_at = time.monotonic()
        self.event = threading.Event()
        self.admitted = False


class PriorityGate:
    """A bounded, observable, non-preemptive admission lane.

    Lower priority numbers win.  Waiting requests are promoted one tier per
    ``aging_seconds`` so even ``other`` eventually competes with librarian work.
    With aging disabled, equal priorities are strict FIFO.
    """

    def __init__(
        self,
        concurrency: int,
        *,
        max_queue: int,
        aging_seconds: float = 0.0,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        if max_queue < 0:
            raise ValueError("max_queue must be >= 0")
        if aging_seconds < 0:
            raise ValueError("aging_seconds must be >= 0")
        self._lock = threading.Lock()
        self._capacity = concurrency
        self._free = concurrency
        self._max_queue = max_queue
        self._aging_seconds = aging_seconds
        self._waiters: list[_Waiter] = []
        self._seq = 0
        self._admitted_total = 0
        self._completed_total = 0
        self._rejected_total = 0
        self._timed_out_total = 0

    def acquire(self, priority: int, *, timeout: float | None = None) -> float:
        started = time.monotonic()
        with self._lock:
            if self._free > 0:
                self._free -= 1
                self._admitted_total += 1
                return 0.0
            if len(self._waiters) >= self._max_queue:
                self._rejected_total += 1
                raise QueueFullError("admission queue is full")
            self._seq += 1
            waiter = _Waiter(priority, self._seq)
            self._waiters.append(waiter)
        if waiter.event.wait(timeout=timeout):
            return time.monotonic() - started
        # Resolve the timeout-vs-release race under the same lock that performs
        # the handoff.  A granted waiter owns a slot even if Event.wait happened
        # to report its deadline at the same instant.
        with self._lock:
            if waiter.admitted:
                return time.monotonic() - started
            self._waiters.remove(waiter)
            self._timed_out_total += 1
        raise QueueWaitTimeoutError("admission queue wait timed out")

    def _effective_priority(self, waiter: _Waiter, now: float) -> int:
        if self._aging_seconds <= 0:
            return waiter.priority
        promotions = int((now - waiter.enqueued_at) // self._aging_seconds)
        return max(0, waiter.priority - promotions)

    def release(self) -> None:
        with self._lock:
            if self._free >= self._capacity:
                raise RuntimeError("release without a matching acquire")
            self._completed_total += 1
            if self._waiters:
                now = time.monotonic()
                index = min(
                    range(len(self._waiters)),
                    key=lambda i: (
                        self._effective_priority(self._waiters[i], now),
                        self._waiters[i].seq,
                    ),
                )
                waiter = self._waiters.pop(index)
                waiter.admitted = True
                self._admitted_total += 1
                waiter.event.set()  # Slot stays occupied while ownership changes.
            else:
                self._free += 1

    def snapshot(self) -> dict:
        with self._lock:
            by_priority = {name: 0 for name in PRIORITIES}
            priority_names = {value: name for name, value in PRIORITIES.items()}
            for waiter in self._waiters:
                name = priority_names.get(waiter.priority, "other")
                by_priority[name] += 1
            return {
                "capacity": self._capacity,
                "active": self._capacity - self._free,
                "free": self._free,
                "queued": len(self._waiters),
                "max_queue": self._max_queue,
                "queued_by_priority": by_priority,
                "aging_seconds": self._aging_seconds,
                "admitted_total": self._admitted_total,
                "completed_total": self._completed_total,
                "rejected_total": self._rejected_total,
                "timed_out_total": self._timed_out_total,
            }


GATES = {
    "gemma": PriorityGate(
        GEMMA_CONCURRENCY,
        max_queue=GEMMA_MAX_QUEUE,
        aging_seconds=GEMMA_PRIORITY_AGING_SEC,
    ),
    # All Qwen work is deliberately FIFO/background.  Its long generations do
    # not consume Gemma slots and receive a much longer upstream timeout.
    "qwen": PriorityGate(QWEN_CONCURRENCY, max_queue=QWEN_MAX_QUEUE),
}
ROUTE_TIMEOUTS = {
    "gemma": GEMMA_TIMEOUT_SEC,
    "qwen": QWEN_TIMEOUT_SEC,
}
ROUTE_QUEUE_TIMEOUTS = {
    "gemma": GEMMA_QUEUE_TIMEOUT_SEC,
    # Zero means an intentionally unbounded wait for background code work.
    "qwen": QWEN_QUEUE_TIMEOUT_SEC,
}


def lane_for_route(route: str) -> str:
    """Unknown/legacy traffic uses the interactive default backend and lane."""
    return route if route in GATES else "gemma"


def dispatcher_health() -> dict:
    return {
        "ok": True,
        "service": "llm-priority-dispatcher",
        "version": 2,
        "default_lane": "gemma",
        "routes": {
            lane: {
                "upstream": ROUTE_UPSTREAMS[lane],
                "model": GEMMA_MODEL if lane == "gemma" else QWEN_MODEL,
                "upstream_timeout_sec": ROUTE_TIMEOUTS[lane],
                "queue_timeout_sec": ROUTE_QUEUE_TIMEOUTS[lane],
                **gate.snapshot(),
            }
            for lane, gate in GATES.items()
        },
    }


def select_upstream(headers, body: bytes) -> tuple[str, str]:
    """Resolve only configured routes; unknown input keeps legacy behaviour."""
    route = str(headers.get("X-LLM-Route") or "").strip().lower()
    if route not in ROUTE_UPSTREAMS and body:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            model = str(payload.get("model") or "").strip()
            route = MODEL_ROUTES.get(model, "")
    if route in ROUTE_UPSTREAMS:
        return ROUTE_UPSTREAMS[route], route
    return UPSTREAM, "legacy"


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

    def _send_json(self, status: int, payload: dict, *, retry_after: int | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if retry_after is not None:
            self.send_header("Retry-After", str(retry_after))
        self.end_headers()
        self.wfile.write(body)

    def _forward(self, method: str) -> None:
        body = self._read_body()
        target_upstream, route = select_upstream(self.headers, body)
        lane = lane_for_route(route)
        gate = GATES[lane]
        path_only = self.path.partition("?")[0]
        ungated = path_only in UNGATED_PATHS
        acquired = False
        response_started = False
        wait_seconds = 0.0
        if not ungated:
            if lane == "qwen" and self._priority() == PRIORITIES["chat"]:
                self._send_json(
                    409,
                    {
                        "ok": False,
                        "error": "qwen_background_only",
                        "lane": lane,
                        "message": "Interactive chat must use the Gemma lane.",
                    },
                )
                return
            # Qwen is a FIFO background lane.  Gemma retains owner-facing
            # priorities with bounded aging to prevent starvation.
            priority = DEFAULT_PRIORITY if lane == "qwen" else self._priority()
            try:
                queue_timeout = ROUTE_QUEUE_TIMEOUTS[lane]
                wait_seconds = gate.acquire(
                    priority,
                    timeout=queue_timeout if queue_timeout > 0 else None,
                )
                acquired = True
            except QueueFullError:
                snapshot = gate.snapshot()
                self._send_json(
                    429,
                    {
                        "ok": False,
                        "error": "llm_queue_full",
                        "lane": lane,
                        "capacity": snapshot["capacity"],
                        "queued": snapshot["queued"],
                        "max_queue": snapshot["max_queue"],
                    },
                    retry_after=1,
                )
                return
            except QueueWaitTimeoutError:
                self._send_json(
                    504,
                    {
                        "ok": False,
                        "error": "llm_queue_timeout",
                        "lane": lane,
                        "queue_timeout_sec": ROUTE_QUEUE_TIMEOUTS[lane],
                    },
                    retry_after=1,
                )
                return
        try:
            headers = {}
            for key in ("Content-Type", "Authorization", "Accept"):
                if self.headers.get(key):
                    headers[key] = self.headers.get(key)
            request = urllib.request.Request(
                f"{target_upstream}{self.path}",
                data=body if body else None,
                headers=headers,
                method=method,
            )
            with urllib.request.urlopen(request, timeout=ROUTE_TIMEOUTS[lane]) as upstream:
                passthrough = {}
                for key, value in upstream.headers.items():
                    low = key.lower()
                    if low in ("content-length", "transfer-encoding", "connection"):
                        continue
                    passthrough[key] = value
                is_stream = "text/event-stream" in str(upstream.headers.get("Content-Type") or "")
                payload = None
                if not is_stream:
                    # Read before committing the downstream response.  A failed
                    # upstream body can still become a clean 502 at this point.
                    payload = upstream.read()

                # From here on, never try to write a second status line if the
                # client or upstream disappears: the selected response has begun.
                response_started = True
                self.send_response(upstream.status)
                self.send_header("X-LLM-Route", route)
                self.send_header("X-LLM-Lane", lane)
                self.send_header("X-LLM-Queue-Wait-Ms", str(round(wait_seconds * 1000)))
                if is_stream:
                    self.send_header("Transfer-Encoding", "chunked")
                for key, value in passthrough.items():
                    self.send_header(key, value)
                if not is_stream:
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.end_headers()
                # HTTPResponse.read(n) may wait for all n bytes and therefore
                # buffer a small SSE event until the next event or EOF. read1()
                # returns currently available buffered bytes, preserving
                # first-token latency through the proxy.
                read_stream_chunk = getattr(upstream, "read1", upstream.read)
                while True:
                    chunk = read_stream_chunk(4096)
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
            if response_started:
                # Headers/body may already be on the wire.  A second HTTP status
                # would corrupt the chunked stream; closing signals truncation.
                self.close_connection = True
                return
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
            if acquired:
                gate.release()

    def do_GET(self) -> None:  # noqa: N802
        if self.path.partition("?")[0] == DISPATCHER_HEALTH_PATH:
            self._send_json(200, dispatcher_health())
            return
        self._forward("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._forward("POST")


class DispatchServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = LISTEN_BACKLOG


def main() -> int:
    server = DispatchServer((HOST, PORT), DispatchHandler)
    routes = ", ".join(f"{name}={url}" for name, url in ROUTE_UPSTREAMS.items())
    print(
        f"LLM dispatcher on {HOST}:{PORT} -> {UPSTREAM} "
        f"(routes: {routes}; gemma_concurrency={GEMMA_CONCURRENCY}; "
        f"qwen_concurrency={QWEN_CONCURRENCY})",
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
