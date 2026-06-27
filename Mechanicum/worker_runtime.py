from __future__ import annotations

import argparse
import importlib
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


WorkerRun = Callable[[dict[str, Any], Path], dict[str, Any]]


def load_worker(module_path: Path, module_name: str) -> WorkerRun:
    if str(module_path) not in sys.path:
        sys.path.insert(0, str(module_path))
    module = importlib.import_module(module_name)
    run = getattr(module, "run", None)
    if not callable(run):
        raise ValueError(f"worker module has no callable run(): {module_name}")
    return run


def response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def make_handler(worker_name: str, workspace_root: Path, run_worker: WorkerRun) -> type[BaseHTTPRequestHandler]:
    class WorkerHandler(BaseHTTPRequestHandler):
        server_version = f"{worker_name}Worker/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path != "/health":
                response(self, 404, {"ok": False, "error": "not found"})
                return
            response(self, 200, {"ok": True, "worker": worker_name, "workspace_root": str(workspace_root)})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path != "/run":
                response(self, 404, {"ok": False, "error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("request body must be a JSON object")
                request = payload.get("request") if isinstance(payload.get("request"), dict) else payload
                result = run_worker(request, workspace_root)
                response(self, 200 if result.get("ok") else 400, result)
            except Exception as exc:  # noqa: BLE001 - server boundary converts exceptions to JSON.
                response(self, 500, {"ok": False, "worker": worker_name, "error": str(exc)})

    return WorkerHandler


def serve(worker_name: str, module_path: Path, module_name: str, host: str, port: int, workspace_root: Path) -> None:
    run_worker = load_worker(module_path, module_name)
    workspace_root.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), make_handler(worker_name, workspace_root, run_worker))
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve a Mechanicum worker through the standard Worker API.")
    parser.add_argument("--worker", required=True)
    parser.add_argument("--module-path", required=True)
    parser.add_argument("--module", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--workspace-root", default="runtime/worker-service-work")
    args = parser.parse_args()
    serve(
        worker_name=args.worker,
        module_path=Path(args.module_path).resolve(),
        module_name=args.module,
        host=args.host,
        port=args.port,
        workspace_root=Path(args.workspace_root),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
