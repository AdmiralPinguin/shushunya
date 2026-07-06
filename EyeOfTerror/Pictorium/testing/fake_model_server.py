from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator


class FakeModelHandler(BaseHTTPRequestHandler):
    server_version = "PictoriumFakeModel/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            request = {}
        messages = request.get("messages") if isinstance(request, dict) else []
        user_text = ""
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict) and message.get("role") == "user":
                    user_text = str(message.get("content") or "")
        decision = {
            "decision": {
                "action": "proceed",
                "panel_count": 4 if "панел" in user_text.lower() or "panel" in user_text.lower() else 1,
                "title": "Structured Pictorium Test Decision",
                "notes": ["fake OpenAI-compatible model endpoint used for contract testing"],
            },
            "confidence": "high",
            "risks": [],
        }
        response = {
            "choices": [
                {
                    "message": {"content": json.dumps(decision, ensure_ascii=False)},
                    "finish_reason": "stop",
                }
            ]
        }
        body = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def fake_pictorium_model() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    old_env = {
        "EYE_MODEL_BASE_URL": os.environ.get("EYE_MODEL_BASE_URL"),
        "EYE_MODEL_TIMEOUT_SEC": os.environ.get("EYE_MODEL_TIMEOUT_SEC"),
        "EYE_MODEL_MAX_TOKENS": os.environ.get("EYE_MODEL_MAX_TOKENS"),
        "EYE_MODEL_MAX_CONTEXT_CHARS": os.environ.get("EYE_MODEL_MAX_CONTEXT_CHARS"),
    }
    base_url = f"http://127.0.0.1:{server.server_port}/v1"
    os.environ["EYE_MODEL_BASE_URL"] = base_url
    os.environ["EYE_MODEL_TIMEOUT_SEC"] = "5"
    os.environ["EYE_MODEL_MAX_TOKENS"] = "256"
    os.environ["EYE_MODEL_MAX_CONTEXT_CHARS"] = "8000"
    thread.start()
    try:
        yield base_url
    finally:
        server.shutdown()
        thread.join(timeout=5)
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
