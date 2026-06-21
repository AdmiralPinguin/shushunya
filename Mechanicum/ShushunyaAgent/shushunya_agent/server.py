#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .agent_runner import AgentConfig, archive_request, compact_resume_events, read_task_journal, run_agent, safe_task_id


HOST = os.environ.get("SHUSHUNYA_AGENT_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHUSHUNYA_AGENT_PORT", "8095"))
API_KEY = os.environ.get("SHUSHUNYA_AGENT_API_KEY", "").strip()
ROOT = Path(__file__).resolve().parents[1]
MAX_REQUEST_BYTES = int(os.environ.get("SHUSHUNYA_AGENT_MAX_REQUEST_BYTES", "1048576"))
MAX_TASK_CHARS = int(os.environ.get("SHUSHUNYA_AGENT_MAX_TASK_CHARS", "50000"))
MAX_QUEUE = max(1, int(os.environ.get("SHUSHUNYA_AGENT_MAX_QUEUE", "3")))
STREAM_HEARTBEAT_SEC = max(5.0, float(os.environ.get("SHUSHUNYA_AGENT_STREAM_HEARTBEAT_SEC", "15")))
SERVICE_STARTED_AT = time.time()
RUN_LOCK = threading.Lock()
RUN_LOCK_FILE = ROOT / "runtime" / "agent-run.lock"
STATE_LOCK = threading.Lock()
CANCELLED_TASK_IDS: set[str] = set()
RUN_STATE: dict[str, Any] = {
    "busy": False,
    "current_task_id": "",
    "current_task_started_at": 0.0,
    "queued": 0,
    "completed": 0,
    "last_task_id": "",
    "last_exit_code": None,
    "last_finished_at": 0.0,
    "last_duration_sec": 0.0,
}
RUN_METRICS: dict[str, Any] = {
    "runs_started": 0,
    "runs_completed": 0,
    "runs_failed": 0,
    "runs_cancelled": 0,
    "json_parse_errors": 0,
    "json_repairs": 0,
    "json_repair_failures": 0,
    "validation_rejects": 0,
    "tool_failures": 0,
    "timeouts": 0,
    "web_search_sources": {},
    "total_steps": 0,
}
REVISION_CACHE = ""


class RequestError(Exception):
    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        super().__init__(payload.get("error", "request error"))
        self.status = status
        self.payload = payload


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
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError as exc:
        raise RequestError(400, {"ok": False, "error": "invalid Content-Length"}) from exc
    if length <= 0:
        return {}
    if length > MAX_REQUEST_BYTES:
        raise RequestError(
            413,
            {"ok": False, "error": "request body too large", "max_bytes": MAX_REQUEST_BYTES},
        )
    raw = handler.rfile.read(length)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RequestError(400, {"ok": False, "error": "invalid JSON body", "detail": str(exc)}) from exc
    if not isinstance(payload, dict):
        raise RequestError(400, {"ok": False, "error": "JSON body must be an object"})
    return payload


def authorized(handler: BaseHTTPRequestHandler) -> bool:
    if not API_KEY:
        return True
    return handler.headers.get("Authorization", "").strip() == f"Bearer {API_KEY}"


def health_detail_allowed(handler: BaseHTTPRequestHandler) -> bool:
    return bool(API_KEY) and authorized(handler)


def privileged_api_allowed(handler: BaseHTTPRequestHandler) -> bool:
    return bool(API_KEY) and authorized(handler)


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


def http_shell_enabled(payload: dict[str, Any]) -> bool:
    requested = bool_field(payload, "shell_enabled", env_bool("SHUSHUNYA_AGENT_HTTP_SHELL_ENABLED", False))
    if not requested:
        return False
    if API_KEY or env_bool("SHUSHUNYA_AGENT_HTTP_ALLOW_SHELL_WITHOUT_API_KEY", False):
        return True
    return False


def runtime_state() -> dict[str, Any]:
    with STATE_LOCK:
        payload = dict(RUN_STATE)
        payload["cancelled_task_count"] = len(CANCELLED_TASK_IDS)
        metrics = json.loads(json.dumps(RUN_METRICS))
        finished_runs = int(metrics.get("runs_completed", 0)) + int(metrics.get("runs_failed", 0))
        metrics["average_steps_per_finished_run"] = round(int(metrics.get("total_steps", 0)) / finished_runs, 3) if finished_runs else 0.0
        payload["metrics"] = metrics
    now = time.time()
    if payload.get("busy") and payload.get("current_task_started_at"):
        payload["current_task_duration_sec"] = round(now - float(payload["current_task_started_at"]), 3)
    if payload.get("last_finished_at"):
        payload["last_finished_ago_sec"] = round(now - float(payload["last_finished_at"]), 3)
    payload["max_request_bytes"] = MAX_REQUEST_BYTES
    payload["max_task_chars"] = MAX_TASK_CHARS
    payload["max_queue"] = MAX_QUEUE
    payload["revision"] = service_revision()
    payload["started_at"] = SERVICE_STARTED_AT
    payload["uptime_sec"] = round(now - SERVICE_STARTED_AT, 3)
    return payload


def record_run_started() -> None:
    with STATE_LOCK:
        RUN_METRICS["runs_started"] = int(RUN_METRICS.get("runs_started", 0)) + 1


def record_run_finished(code: int) -> None:
    with STATE_LOCK:
        if code == 0:
            RUN_METRICS["runs_completed"] = int(RUN_METRICS.get("runs_completed", 0)) + 1
        else:
            RUN_METRICS["runs_failed"] = int(RUN_METRICS.get("runs_failed", 0)) + 1
        if code == 2:
            RUN_METRICS["runs_cancelled"] = int(RUN_METRICS.get("runs_cancelled", 0)) + 1


def collect_agent_event(event: dict[str, Any]) -> None:
    event_type = str(event.get("type") or "")
    code = str(event.get("code") or "")
    with STATE_LOCK:
        if event_type == "step":
            RUN_METRICS["total_steps"] = int(RUN_METRICS.get("total_steps", 0)) + 1
        if code == "json_parse_error":
            RUN_METRICS["json_parse_errors"] = int(RUN_METRICS.get("json_parse_errors", 0)) + 1
        elif code == "json_repaired":
            RUN_METRICS["json_repairs"] = int(RUN_METRICS.get("json_repairs", 0)) + 1
        elif code == "json_repair_failed":
            RUN_METRICS["json_repair_failures"] = int(RUN_METRICS.get("json_repair_failures", 0)) + 1
        elif code == "validation_error":
            RUN_METRICS["validation_rejects"] = int(RUN_METRICS.get("validation_rejects", 0)) + 1
        if event_type == "tool_result":
            if event.get("ok") is False:
                RUN_METRICS["tool_failures"] = int(RUN_METRICS.get("tool_failures", 0)) + 1
            if event.get("timeout") is True:
                RUN_METRICS["timeouts"] = int(RUN_METRICS.get("timeouts", 0)) + 1
            if event.get("action") == "web_search" and event.get("source"):
                sources = RUN_METRICS.setdefault("web_search_sources", {})
                source = str(event.get("source"))
                sources[source] = int(sources.get(source, 0)) + 1


def is_task_cancelled(task_id: str) -> bool:
    with STATE_LOCK:
        return safe_task_id(task_id) in CANCELLED_TASK_IDS


def mark_task_cancelled(task_id: str) -> str:
    safe_id = safe_task_id(task_id)
    with STATE_LOCK:
        CANCELLED_TASK_IDS.add(safe_id)
    return safe_id


def clear_task_cancelled(task_id: str) -> None:
    with STATE_LOCK:
        CANCELLED_TASK_IDS.discard(safe_task_id(task_id))


def service_revision() -> str:
    global REVISION_CACHE
    if REVISION_CACHE:
        return REVISION_CACHE
    env_revision = os.environ.get("SHUSHUNYA_AGENT_REVISION", "").strip()
    if env_revision:
        REVISION_CACHE = env_revision
        return REVISION_CACHE
    try:
        completed = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
        REVISION_CACHE = completed.stdout.strip() if completed.returncode == 0 else "unknown"
    except Exception:
        REVISION_CACHE = "unknown"
    return REVISION_CACHE


def runner_is_busy() -> bool:
    with STATE_LOCK:
        return bool(RUN_STATE.get("busy")) or int(RUN_STATE.get("queued", 0)) > 0


def reject_if_busy(payload: dict[str, Any]) -> dict[str, Any] | None:
    if bool_field(payload, "wait_for_slot", True):
        return None
    if not runner_is_busy() and not RUN_LOCK.locked():
        return None
    return {"ok": False, "error": "agent busy", "state": runtime_state()}


def try_enqueue_run() -> dict[str, Any] | None:
    with STATE_LOCK:
        if int(RUN_STATE.get("queued", 0)) >= MAX_QUEUE:
            return {"ok": False, "error": "agent queue full"}
        RUN_STATE["queued"] = int(RUN_STATE.get("queued", 0)) + 1
    return None


def validate_task_text(task: str) -> dict[str, Any] | None:
    if not task:
        return {"status": 400, "payload": {"error": "missing task"}}
    if len(task) > MAX_TASK_CHARS:
        return {
            "status": 413,
            "payload": {"ok": False, "error": "task is too large", "max_chars": MAX_TASK_CHARS},
        }
    return None


def config_from_payload(payload: dict[str, Any]) -> AgentConfig:
    task_id = str(payload.get("task_id") or payload.get("resume_task_id") or "").strip()
    return AgentConfig(
        max_steps=int_field(payload, "max_steps", int(os.environ.get("SHUSHUNYA_AGENT_MAX_STEPS", "12")), 1, 50),
        max_runtime_sec=int_field(payload, "max_runtime_sec", int(os.environ.get("SHUSHUNYA_AGENT_MAX_RUNTIME_SEC", "1800")), 30, 7200),
        max_model_tokens=int_field(payload, "max_tokens", int(os.environ.get("SHUSHUNYA_AGENT_MAX_MODEL_TOKENS", "1024")), 128, 4096),
        llm_retries=int_field(payload, "llm_retries", int(os.environ.get("SHUSHUNYA_AGENT_LLM_RETRIES", "3")), 1, 5),
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
        task_id=safe_task_id(task_id),
        shell_enabled=http_shell_enabled(payload),
    )


def attach_cancel_check(config: AgentConfig) -> AgentConfig:
    config.cancel_check = lambda task_id=config.task_id: is_task_cancelled(task_id)
    return config


def apply_resume_context(task: str, config: AgentConfig, payload: dict[str, Any]) -> str:
    resume_task_id = str(payload.get("resume_task_id") or "").strip()
    if not resume_task_id:
        return task
    journal = read_task_journal(resume_task_id, limit=80)
    if not journal.get("ok"):
        return task + "\n\nResume note: requested previous task journal was not found."
    compact_events = compact_resume_events(journal.get("events", [])[-80:])
    return (
        task
        + "\n\nResume context from previous agent task journal "
        + journal.get("task_id", resume_task_id)
        + ":\n"
        + json.dumps(compact_events, ensure_ascii=False, indent=2)
    )


def public_task_journal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    public_payload = dict(payload)
    public_payload.pop("path", None)
    return public_payload


class AgentHandler(BaseHTTPRequestHandler):
    server_version = "ShushunyaAgent/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f'{self.address_string()} - {fmt % args}', file=os.sys.stderr, flush=True)

    def do_GET(self) -> None:
        parsed_path = urlparse(self.path)
        if parsed_path.path == "/health":
            config = AgentConfig()
            try:
                archive = archive_request(config, "GET", "/health", timeout=5)
                detail = (parse_qs(parsed_path.query).get("detail") or ["0"])[0] in {"1", "true", "yes"}
                if detail and not health_detail_allowed(self):
                    write_json(self, 401, {"error": "unauthorized"})
                    return
                payload = {
                    "status": "ok",
                    "service": "ShushunyaAgent",
                    "revision": service_revision(),
                    "uptime_sec": round(time.time() - SERVICE_STARTED_AT, 3),
                    "archive_status": archive.get("status", "unknown"),
                }
                if detail:
                    payload["archive"] = archive
                write_json(self, 200, payload)
            except Exception as exc:
                write_json(self, 503, {"status": "error", "service": "ShushunyaAgent", "error": str(exc)})
            return
        if parsed_path.path == "/tools":
            schema_path = ROOT / "tool_schema.json"
            write_json(self, 200, json.loads(schema_path.read_text(encoding="utf-8")))
            return
        if parsed_path.path == "/state":
            if not authorized(self):
                write_json(self, 401, {"error": "unauthorized"})
                return
            write_json(self, 200, {"ok": True, "service": "ShushunyaAgent", "state": runtime_state()})
            return
        if parsed_path.path == "/task-journal":
            if not privileged_api_allowed(self):
                write_json(self, 401, {"error": "unauthorized"})
                return
            params = parse_qs(parsed_path.query)
            task_id = (params.get("task_id") or [""])[0].strip() or None
            limit = int_field({"limit": (params.get("limit") or [80])[0]}, "limit", 80, 1, 500)
            payload = read_task_journal(task_id, limit=limit)
            write_json(self, 200 if payload.get("ok") else 404, public_task_journal_payload(payload))
            return
        write_json(self, 404, {"error": "not found"})

    def do_POST(self) -> None:
        if not authorized(self):
            write_json(self, 401, {"error": "unauthorized"})
            return

        if self.path == "/run-stream":
            self.run_stream()
            return

        if self.path == "/cancel":
            self.cancel_run()
            return

        if self.path != "/run":
            write_json(self, 404, {"error": "not found"})
            return

        try:
            payload = read_json(self)
            task = str(payload.get("task", "")).strip()
            task_error = validate_task_text(task)
            if task_error is not None:
                write_json(self, int(task_error["status"]), task_error["payload"])
                return
            if str(payload.get("resume_task_id") or "").strip() and not privileged_api_allowed(self):
                write_json(self, 401, {"error": "resume_task_id requires API key"})
                return
            busy = reject_if_busy(payload)
            if busy is not None:
                write_json(self, 409, busy)
                return

            config = attach_cancel_check(config_from_payload(payload))
            task = apply_resume_context(task, config, payload)

            stdout = io.StringIO()
            stderr = io.StringIO()
            code = 1
            queue_error = try_enqueue_run()
            if queue_error is not None:
                queue_error["state"] = runtime_state()
                write_json(self, 429, queue_error)
                return
            with RUN_LOCK:
                with STATE_LOCK:
                    RUN_STATE["queued"] = max(0, int(RUN_STATE["queued"]) - 1)
                    RUN_STATE["busy"] = True
                    RUN_STATE["current_task_id"] = config.task_id
                    RUN_STATE["current_task_started_at"] = time.time()
                record_run_started()
                RUN_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
                with RUN_LOCK_FILE.open("w", encoding="utf-8") as lock_fh:
                    fcntl.flock(lock_fh, fcntl.LOCK_EX)
                    try:
                        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                            code = run_agent(task, config, event_sink=collect_agent_event)
                    finally:
                        fcntl.flock(lock_fh, fcntl.LOCK_UN)
                        with STATE_LOCK:
                            started_at = float(RUN_STATE["current_task_started_at"] or 0.0)
                            finished_at = time.time()
                            RUN_STATE["busy"] = False
                            RUN_STATE["completed"] = int(RUN_STATE["completed"]) + 1
                            RUN_STATE["last_task_id"] = config.task_id
                            RUN_STATE["last_exit_code"] = code
                            RUN_STATE["last_finished_at"] = finished_at
                            RUN_STATE["last_duration_sec"] = round(finished_at - started_at, 3) if started_at else 0.0
                            RUN_STATE["current_task_id"] = ""
                            RUN_STATE["current_task_started_at"] = 0.0
                        record_run_finished(code)
                        clear_task_cancelled(config.task_id)

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
        except RequestError as exc:
            write_json(self, exc.status, exc.payload)
        except Exception as exc:
            write_json(self, 500, {"ok": False, "error": str(exc)})

    def run_stream(self) -> None:
        try:
            payload = read_json(self)
            task = str(payload.get("task", "")).strip()
            task_error = validate_task_text(task)
            if task_error is not None:
                write_json(self, int(task_error["status"]), task_error["payload"])
                return
            if str(payload.get("resume_task_id") or "").strip() and not privileged_api_allowed(self):
                write_json(self, 401, {"error": "resume_task_id requires API key"})
                return
            busy = reject_if_busy(payload)
            if busy is not None:
                write_json(self, 409, busy)
                return

            config = attach_cancel_check(config_from_payload(payload))
            task = apply_resume_context(task, config, payload)
            queue_error = try_enqueue_run()
            if queue_error is not None:
                queue_error["state"] = runtime_state()
                write_json(self, 429, queue_error)
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            write_ndjson(self, {"type": "start", "message": "агент принят в очередь"})

            stdout = io.StringIO()
            stderr = io.StringIO()
            code = 1
            with RUN_LOCK:
                with STATE_LOCK:
                    RUN_STATE["queued"] = max(0, int(RUN_STATE["queued"]) - 1)
                    RUN_STATE["busy"] = True
                    RUN_STATE["current_task_id"] = config.task_id
                    RUN_STATE["current_task_started_at"] = time.time()
                record_run_started()
                RUN_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
                with RUN_LOCK_FILE.open("w", encoding="utf-8") as lock_fh:
                    fcntl.flock(lock_fh, fcntl.LOCK_EX)
                    try:
                        write_ndjson(self, {"type": "start", "message": "агент получил слот выполнения"})
                        events: queue.Queue[dict[str, Any]] = queue.Queue()
                        code_box = {"code": 1}

                        def run_worker() -> None:
                            try:
                                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                                    def stream_event_sink(event: dict[str, Any]) -> None:
                                        collect_agent_event(event)
                                        events.put(event)

                                    code_box["code"] = run_agent(task, config, event_sink=stream_event_sink)
                            except Exception as exc:
                                code_box["code"] = 1
                                events.put({"type": "error", "ok": False, "message": str(exc)})
                            finally:
                                events.put({"type": "_runner_done"})

                        worker = threading.Thread(target=run_worker, name=f"agent-run-{config.task_id}", daemon=True)
                        worker.start()
                        client_connected = True
                        while True:
                            try:
                                event = events.get(timeout=STREAM_HEARTBEAT_SEC)
                            except queue.Empty:
                                state = runtime_state()
                                if client_connected:
                                    try:
                                        write_ndjson(
                                            self,
                                            {
                                                "type": "heartbeat",
                                                "task_id": config.task_id,
                                                "busy": state.get("busy", False),
                                                "current_task_duration_sec": state.get("current_task_duration_sec", 0.0),
                                            },
                                        )
                                    except OSError:
                                        client_connected = False
                                        mark_task_cancelled(config.task_id)
                                continue
                            if event.get("type") == "_runner_done":
                                break
                            if client_connected:
                                try:
                                    write_ndjson(self, event)
                                except OSError:
                                    client_connected = False
                                    mark_task_cancelled(config.task_id)
                        worker.join(timeout=1)
                        code = int(code_box["code"])
                    finally:
                        fcntl.flock(lock_fh, fcntl.LOCK_UN)
                        with STATE_LOCK:
                            started_at = float(RUN_STATE["current_task_started_at"] or 0.0)
                            finished_at = time.time()
                            RUN_STATE["busy"] = False
                            RUN_STATE["completed"] = int(RUN_STATE["completed"]) + 1
                            RUN_STATE["last_task_id"] = config.task_id
                            RUN_STATE["last_exit_code"] = code
                            RUN_STATE["last_finished_at"] = finished_at
                            RUN_STATE["last_duration_sec"] = round(finished_at - started_at, 3) if started_at else 0.0
                            RUN_STATE["current_task_id"] = ""
                            RUN_STATE["current_task_started_at"] = 0.0
                        record_run_finished(code)
                        clear_task_cancelled(config.task_id)

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
        except RequestError as exc:
            write_json(self, exc.status, exc.payload)
        except Exception as exc:
            try:
                write_ndjson(self, {"type": "error", "ok": False, "message": str(exc)})
                write_ndjson(self, {"type": "done", "ok": False, "exit_code": 1})
            except Exception:
                pass

    def cancel_run(self) -> None:
        try:
            payload = read_json(self)
            task_id = str(payload.get("task_id") or "").strip()
            if not task_id:
                if not privileged_api_allowed(self):
                    write_json(self, 401, {"ok": False, "error": "cancel without task_id requires API key"})
                    return
                with STATE_LOCK:
                    task_id = str(RUN_STATE.get("current_task_id") or "").strip()
            if not task_id:
                write_json(self, 400, {"ok": False, "error": "missing task_id and no current task"})
                return
            safe_id = mark_task_cancelled(task_id)
            write_json(self, 200, {"ok": True, "task_id": safe_id, "message": "cancel requested"})
        except RequestError as exc:
            write_json(self, exc.status, exc.payload)
        except Exception as exc:
            write_json(self, 500, {"ok": False, "error": str(exc)})


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), AgentHandler)
    print(f"ShushunyaAgent server started: http://{HOST}:{PORT}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
