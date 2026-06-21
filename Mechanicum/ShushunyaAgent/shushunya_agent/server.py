#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .agent_runner import AgentConfig, archive_request, run_agent


HOST = os.environ.get("SHUSHUNYA_AGENT_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHUSHUNYA_AGENT_PORT", "8095"))
API_KEY = os.environ.get("SHUSHUNYA_AGENT_API_KEY", "").strip()
ROOT = Path(__file__).resolve().parents[1]
RUN_LOCK = threading.Lock()
RUN_LOCK_FILE = ROOT / "runtime" / "agent-run.lock"


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def write_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def write_ndjson(handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> None:
    line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    handler.wfile.write(line)
    handler.wfile.flush()


def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def authorized(handler: BaseHTTPRequestHandler) -> bool:
    if not API_KEY:
        return True
    return handler.headers.get("Authorization", "").strip() == f"Bearer {API_KEY}"


def bool_field(payload: dict[str, Any], key: str, default: bool = False) -> bool:
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def int_field(payload: dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(payload.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


class AgentHandler(BaseHTTPRequestHandler):
    server_version = "ShushunyaAgent/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f'{self.address_string()} - {fmt % args}', file=os.sys.stderr, flush=True)

    def do_GET(self) -> None:
        if self.path == "/health":
            config = AgentConfig()
            try:
                archive = archive_request(config, "GET", "/health", timeout=5)
                write_json(self, 200, {"status": "ok", "service": "ShushunyaAgent", "archive": archive})
            except Exception as exc:
                write_json(self, 503, {"status": "error", "service": "ShushunyaAgent", "error": str(exc)})
            return
        if self.path == "/tools":
            schema_path = ROOT / "tool_schema.json"
            write_json(self, 200, json.loads(schema_path.read_text(encoding="utf-8")))
            return
        write_json(self, 404, {"error": "not found"})

    def do_POST(self) -> None:
        if not authorized(self):
            write_json(self, 401, {"error": "unauthorized"})
            return

        if self.path == "/run-stream":
            self.run_stream()
            return

        if self.path != "/run":
            write_json(self, 404, {"error": "not found"})
            return

        try:
            payload = read_json(self)
            task = str(payload.get("task", "")).strip()
            if not task:
                write_json(self, 400, {"error": "missing task"})
                return

            config = AgentConfig(
                max_steps=int_field(payload, "max_steps", int(os.environ.get("SHUSHUNYA_AGENT_MAX_STEPS", "12")), 1, 50),
                max_model_tokens=int_field(payload, "max_tokens", int(os.environ.get("SHUSHUNYA_AGENT_MAX_MODEL_TOKENS", "1024")), 128, 4096),
                json_output=True,
                technical_output=bool_field(payload, "technical", True),
                inject_memory=bool_field(payload, "inject_memory", env_bool("SHUSHUNYA_AGENT_INJECT_MEMORY", True)),
                archive_internal_steps=bool_field(
                    payload,
                    "archive_internal_steps",
                    env_bool("SHUSHUNYA_AGENT_ARCHIVE_INTERNAL_STEPS", True),
                ),
                archive_task=bool_field(payload, "archive_task", env_bool("SHUSHUNYA_AGENT_ARCHIVE_TASK", True)),
                task_memory=bool_field(payload, "task_memory", env_bool("SHUSHUNYA_AGENT_TASK_MEMORY", True)),
                archive_user=str(payload.get("archive_user") or os.environ.get("SHUSHUNYA_AGENT_ARCHIVE_USER", "shushunya-agent")),
                memory_namespace=str(payload.get("memory_namespace") or os.environ.get("SHUSHUNYA_AGENT_MEMORY_NAMESPACE", "agent")),
                shell_enabled=bool_field(payload, "shell_enabled", True),
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with RUN_LOCK:
                RUN_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
                with RUN_LOCK_FILE.open("w", encoding="utf-8") as lock_fh:
                    fcntl.flock(lock_fh, fcntl.LOCK_EX)
                    try:
                        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                            code = run_agent(task, config)
                    finally:
                        fcntl.flock(lock_fh, fcntl.LOCK_UN)

            text = stdout.getvalue().strip()
            try:
                result = json.loads(text) if text else {"ok": False, "message": "empty agent output"}
            except json.JSONDecodeError:
                result = {"ok": False, "message": "agent returned non-json output", "raw": text}
            if not bool_field(payload, "include_steps", True):
                result.pop("steps", None)
            result["exit_code"] = code
            if bool_field(payload, "include_stderr", False) or code != 0:
                result["stderr"] = stderr.getvalue()
            write_json(self, 200 if code == 0 else 500, result)
        except Exception as exc:
            write_json(self, 500, {"ok": False, "error": str(exc)})

    def run_stream(self) -> None:
        try:
            payload = read_json(self)
            task = str(payload.get("task", "")).strip()
            if not task:
                write_json(self, 400, {"error": "missing task"})
                return

            config = AgentConfig(
                max_steps=int_field(payload, "max_steps", int(os.environ.get("SHUSHUNYA_AGENT_MAX_STEPS", "12")), 1, 50),
                max_model_tokens=int_field(payload, "max_tokens", int(os.environ.get("SHUSHUNYA_AGENT_MAX_MODEL_TOKENS", "1024")), 128, 4096),
                json_output=True,
                technical_output=bool_field(payload, "technical", True),
                inject_memory=bool_field(payload, "inject_memory", env_bool("SHUSHUNYA_AGENT_INJECT_MEMORY", True)),
                archive_internal_steps=bool_field(
                    payload,
                    "archive_internal_steps",
                    env_bool("SHUSHUNYA_AGENT_ARCHIVE_INTERNAL_STEPS", True),
                ),
                archive_task=bool_field(payload, "archive_task", env_bool("SHUSHUNYA_AGENT_ARCHIVE_TASK", True)),
                task_memory=bool_field(payload, "task_memory", env_bool("SHUSHUNYA_AGENT_TASK_MEMORY", True)),
                archive_user=str(payload.get("archive_user") or os.environ.get("SHUSHUNYA_AGENT_ARCHIVE_USER", "shushunya-agent")),
                memory_namespace=str(payload.get("memory_namespace") or os.environ.get("SHUSHUNYA_AGENT_MEMORY_NAMESPACE", "agent")),
                shell_enabled=bool_field(payload, "shell_enabled", True),
            )

            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            write_ndjson(self, {"type": "start", "message": "агент принят в очередь"})

            stdout = io.StringIO()
            stderr = io.StringIO()
            code = 1
            with RUN_LOCK:
                RUN_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
                with RUN_LOCK_FILE.open("w", encoding="utf-8") as lock_fh:
                    fcntl.flock(lock_fh, fcntl.LOCK_EX)
                    try:
                        write_ndjson(self, {"type": "start", "message": "агент получил слот выполнения"})
                        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                            code = run_agent(task, config, event_sink=lambda event: write_ndjson(self, event))
                    finally:
                        fcntl.flock(lock_fh, fcntl.LOCK_UN)

            if code != 0:
                text = stdout.getvalue().strip()
                try:
                    result = json.loads(text) if text else {"ok": False, "message": "empty agent output"}
                except json.JSONDecodeError:
                    result = {"ok": False, "message": "agent returned non-json output", "raw": text}
                if bool_field(payload, "include_stderr", False):
                    result["stderr"] = stderr.getvalue()
                write_ndjson(self, {"type": "done", "ok": False, "exit_code": code, "result": result})
            else:
                write_ndjson(self, {"type": "done", "ok": True, "exit_code": code})
        except Exception as exc:
            try:
                write_ndjson(self, {"type": "error", "ok": False, "message": str(exc)})
            except Exception:
                pass


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), AgentHandler)
    print(f"ShushunyaAgent server started: http://{HOST}:{PORT}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
