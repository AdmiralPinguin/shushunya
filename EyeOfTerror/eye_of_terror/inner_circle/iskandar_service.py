from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .iskandar import plan_lore_reconstruction
from ..pipeline import write_pipeline_run


def response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def payload_from(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    payload = json.loads(handler.rfile.read(length).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def make_handler(default_run_root: Path) -> type[BaseHTTPRequestHandler]:
    class IskandarHandler(BaseHTTPRequestHandler):
        server_version = "IskandarKhayon/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path != "/health":
                response(self, 404, {"ok": False, "error": "not found"})
                return
            response(self, 200, {"ok": True, "governor": "IskandarKhayon"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            try:
                payload = payload_from(self)
                task = str(payload.get("task") or payload.get("message") or "").strip()
                if not task:
                    response(self, 400, {"ok": False, "error": "task is required"})
                    return
                task_id = str(payload.get("task_id") or "").strip() or None
                plan = plan_lore_reconstruction(task, task_id=task_id)
                if self.path == "/plan":
                    response(self, 200, plan.to_dict())
                    return
                if self.path == "/prepare_run":
                    run_dir = Path(str(payload.get("run_dir") or default_run_root / plan.contract.task_id))
                    status = write_pipeline_run(plan.contract, run_dir)
                    response(self, 200, {"ok": status["ok"], "governor": "IskandarKhayon", "status": status})
                    return
                response(self, 404, {"ok": False, "error": "not found"})
            except Exception as exc:  # noqa: BLE001 - service boundary records planning failures.
                response(self, 500, {"ok": False, "governor": "IskandarKhayon", "error": str(exc)})

    return IskandarHandler


def serve(host: str, port: int, default_run_root: Path) -> None:
    default_run_root.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), make_handler(default_run_root))
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve Iskandar Khayon as an Inner Circle governor.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7101)
    parser.add_argument("--default-run-root", default="runtime/iskandar-runs")
    args = parser.parse_args()
    serve(args.host, args.port, Path(args.default_run_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
