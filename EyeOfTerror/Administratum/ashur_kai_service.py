from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .schema import DEFAULT_PORT, GOVERNOR
from .storage import (
    create_task,
    create_watch,
    get_task,
    get_watch,
    init_db,
    list_journal,
    list_tasks,
    list_watches,
    set_task_status,
    set_watch_status,
    snooze_task,
)


def response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
    handler.end_headers()
    handler.wfile.write(data)


def payload_from(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def make_handler(db_path: Path) -> type[BaseHTTPRequestHandler]:
    class AshurKaiHandler(BaseHTTPRequestHandler):
        server_version = "AshurKai/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_OPTIONS(self) -> None:  # noqa: N802
            response(self, 200, {"ok": True, "governor": GOVERNOR})

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]
            query = parse_qs(parsed.query)
            try:
                if parsed.path == "/health":
                    response(self, 200, {"ok": True, "department": "Administratum", "governor": GOVERNOR, "port": DEFAULT_PORT})
                    return
                if parsed.path == "/tasks":
                    status = (query.get("status") or [""])[0] or None
                    response(self, 200, {"ok": True, "tasks": list_tasks(status=status, db_path=db_path)})
                    return
                if len(parts) == 2 and parts[0] == "task":
                    task = get_task(parts[1], db_path=db_path)
                    response(self, 200 if task else 404, {"ok": bool(task), "task": task, "error": "" if task else "task not found"})
                    return
                if parsed.path == "/watches":
                    response(self, 200, {"ok": True, "watches": list_watches(db_path=db_path)})
                    return
                if len(parts) == 2 and parts[0] == "watch":
                    watch = get_watch(parts[1], db_path=db_path)
                    response(self, 200 if watch else 404, {"ok": bool(watch), "watch": watch, "error": "" if watch else "watch not found"})
                    return
                if parsed.path == "/journal":
                    limit = int((query.get("limit") or ["100"])[0] or 100)
                    response(self, 200, {"ok": True, "journal": list_journal(limit=limit, db_path=db_path)})
                    return
                response(self, 404, {"ok": False, "error": "not found"})
            except Exception as exc:  # noqa: BLE001
                response(self, 500, {"ok": False, "error": str(exc)})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]
            try:
                payload = payload_from(self)
                if parsed.path == "/task":
                    task = create_task(payload, db_path=db_path)
                    response(self, 201, {"ok": True, "task": task})
                    return
                if len(parts) == 3 and parts[0] == "task":
                    task_id = parts[1]
                    action = parts[2]
                    if action == "done":
                        task = set_task_status(task_id, "done", db_path=db_path)
                    elif action == "cancel":
                        task = set_task_status(task_id, "cancelled", db_path=db_path)
                    elif action == "snooze":
                        next_run = str(payload.get("next_run") or payload.get("due_at") or "").strip()
                        if not next_run:
                            response(self, 400, {"ok": False, "error": "next_run is required"})
                            return
                        task = snooze_task(task_id, next_run, db_path=db_path)
                    else:
                        response(self, 404, {"ok": False, "error": "unknown task action"})
                        return
                    response(self, 200 if task else 404, {"ok": bool(task), "task": task, "error": "" if task else "task not found"})
                    return
                if parsed.path == "/watch":
                    watch = create_watch(payload, db_path=db_path)
                    response(self, 201, {"ok": True, "watch": watch})
                    return
                if len(parts) == 3 and parts[0] == "watch":
                    action_status = {"pause": "paused", "resume": "active", "cancel": "cancelled"}.get(parts[2])
                    if not action_status:
                        response(self, 404, {"ok": False, "error": "unknown watch action"})
                        return
                    watch = set_watch_status(parts[1], action_status, db_path=db_path)
                    response(self, 200 if watch else 404, {"ok": bool(watch), "watch": watch, "error": "" if watch else "watch not found"})
                    return
                response(self, 404, {"ok": False, "error": "not found"})
            except ValueError as exc:
                response(self, 400, {"ok": False, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                response(self, 500, {"ok": False, "error": str(exc)})

    return AshurKaiHandler


def serve(host: str, port: int, db_path: Path) -> None:
    init_db(db_path)
    server = ThreadingHTTPServer((host, port), make_handler(db_path))
    print(f"AshurKai listening on http://{host}:{port}", flush=True)
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve Administratum AshurKai task API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db", default="")
    args = parser.parse_args()
    db_path = Path(args.db) if args.db else Path(__file__).resolve().parent / "runtime" / "administratum.sqlite3"
    serve(args.host, args.port, db_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
