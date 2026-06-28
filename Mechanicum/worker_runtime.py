from __future__ import annotations

import argparse
import importlib
import json
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse


WorkerRun = Callable[[dict[str, Any], Path], dict[str, Any]]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def worker_api_endpoints() -> list[str]:
    return [
        "GET /health",
        "GET /capabilities",
        "POST /run",
        "GET /tasks",
        "GET /tasks/{task_id}",
        "POST /tasks/{task_id}/cancel",
    ]


def load_worker(module_path: Path, module_name: str) -> WorkerRun:
    if str(module_path) not in sys.path:
        sys.path.insert(0, str(module_path))
    module = importlib.import_module(module_name)
    run = getattr(module, "run", None)
    if not callable(run):
        raise ValueError(f"worker module has no callable run(): {module_name}")
    return run


def load_worker_metadata(module_path: Path, worker_name: str) -> dict[str, Any]:
    metadata_path = module_path / "worker.json"
    metadata: dict[str, Any] = {}
    if metadata_path.exists():
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"worker metadata must be a JSON object: {metadata_path}")
        metadata = payload
    metadata.setdefault("name", worker_name)
    metadata.setdefault("capabilities", [])
    metadata.setdefault("api_contract", "EyeOfTerror/contracts/worker_api.md")
    return metadata


def response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def artifact_host_path(workspace_root: Path, artifact_path: str) -> Path:
    if not artifact_path.startswith("/work/"):
        raise ValueError(f"artifact path must start with /work/: {artifact_path}")
    root = workspace_root.resolve()
    host_path = (root / artifact_path.removeprefix("/work/")).resolve()
    if not host_path.is_relative_to(root):
        raise ValueError(f"artifact path escapes workspace root: {artifact_path}")
    return host_path


def input_artifact_errors(request: dict[str, Any], workspace_root: Path) -> list[dict[str, str]]:
    input_artifacts = request.get("input_artifacts", [])
    if not isinstance(input_artifacts, list):
        return [{"path": "", "error": "input_artifacts must be a list"}]
    errors: list[dict[str, str]] = []
    for artifact in input_artifacts:
        if not isinstance(artifact, str):
            errors.append({"path": repr(artifact), "error": "input artifact path must be a string"})
            continue
        try:
            host_path = artifact_host_path(workspace_root, artifact)
        except ValueError as exc:
            errors.append({"path": artifact, "error": str(exc)})
            continue
        if not host_path.exists():
            errors.append({"path": artifact, "error": "input artifact does not exist"})
    return errors


def make_handler(
    worker_name: str,
    workspace_root: Path,
    run_worker: WorkerRun,
    metadata: dict[str, Any] | None = None,
) -> type[BaseHTTPRequestHandler]:
    worker_metadata = dict(metadata or {})
    worker_metadata.setdefault("name", worker_name)
    worker_metadata.setdefault("capabilities", [])
    worker_metadata.setdefault("api_contract", "EyeOfTerror/contracts/worker_api.md")
    tasks: dict[str, dict[str, Any]] = {}
    tasks_lock = threading.RLock()

    def ensure_task(task_id: str) -> dict[str, Any]:
        task = tasks.setdefault(task_id, {"task_id": task_id, "worker": worker_name, "created_at": now_iso()})
        task["updated_at"] = now_iso()
        return task

    def service_manifest() -> dict[str, Any]:
        return {
            "ok": True,
            "worker": worker_name,
            "workspace_root": str(workspace_root),
            "metadata": worker_metadata,
            "capabilities": worker_metadata.get("capabilities", []),
            "api_contract": worker_metadata.get("api_contract", ""),
            "endpoints": worker_api_endpoints(),
        }

    class WorkerHandler(BaseHTTPRequestHandler):
        server_version = f"{worker_name}Worker/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
            response(self, 200, {"ok": True, "worker": worker_name})

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                response(self, 200, service_manifest())
                return
            if parsed.path == "/capabilities":
                response(self, 200, service_manifest())
                return
            parts = [part for part in parsed.path.split("/") if part]
            if parts == ["tasks"]:
                with tasks_lock:
                    task_list = sorted((dict(task) for task in tasks.values()), key=lambda item: str(item.get("updated_at") or ""), reverse=True)
                response(self, 200, {"ok": True, "worker": worker_name, "tasks": task_list})
                return
            if len(parts) == 2 and parts[0] == "tasks":
                task_id = unquote(parts[1])
                with tasks_lock:
                    task = dict(tasks.get(task_id, {}))
                if not task:
                    response(self, 404, {"ok": False, "worker": worker_name, "task_id": task_id, "error": "task not found"})
                    return
                response(self, 200, {"ok": True, "worker": worker_name, "task": task})
                return
            response(self, 404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "cancel":
                task_id = unquote(parts[1])
                with tasks_lock:
                    task = ensure_task(task_id)
                    task["cancel_requested"] = True
                    task["cancel_reason"] = "requested through worker API"
                    task["status"] = "cancelling" if task.get("status") == "running" else "cancelled"
                response(self, 200, {"ok": True, "worker": worker_name, "task": dict(task)})
                return
            if parsed.path != "/run":
                response(self, 404, {"ok": False, "error": "not found"})
                return
            task_id = ""
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("request body must be a JSON object")
                request = payload.get("request") if isinstance(payload.get("request"), dict) else payload
                task_id = str(request.get("task_id") or payload.get("task_id") or "")
                if task_id:
                    with tasks_lock:
                        task = ensure_task(task_id)
                        if task.get("cancel_requested"):
                            task["status"] = "cancelled"
                            task["updated_at"] = now_iso()
                            response(self, 409, {"ok": False, "worker": worker_name, "task_id": task_id, "status": "cancelled", "error": "task cancelled before start"})
                            return
                        task["status"] = "running"
                artifact_errors = input_artifact_errors(request, workspace_root)
                if artifact_errors:
                    result = {
                        "ok": False,
                        "worker": worker_name,
                        "task_id": task_id,
                        "status": "failed",
                        "error": "input artifact preflight failed",
                        "input_artifact_errors": artifact_errors,
                    }
                    if task_id:
                        with tasks_lock:
                            task = ensure_task(task_id)
                            task["status"] = "failed"
                            task["result"] = result
                    response(self, 400, result)
                    return
                result = run_worker(request, workspace_root)
                if task_id:
                    with tasks_lock:
                        task = ensure_task(task_id)
                        task["status"] = str(result.get("status") or ("completed" if result.get("ok") else "failed"))
                        task["result"] = result
                response(self, 200 if result.get("ok") else 400, result)
            except Exception as exc:  # noqa: BLE001 - server boundary converts exceptions to JSON.
                if task_id:
                    with tasks_lock:
                        task = ensure_task(task_id)
                        task["status"] = "failed"
                        task["error"] = str(exc)
                response(self, 500, {"ok": False, "worker": worker_name, "error": str(exc)})

    return WorkerHandler


def serve(worker_name: str, module_path: Path, module_name: str, host: str, port: int, workspace_root: Path) -> None:
    run_worker = load_worker(module_path, module_name)
    metadata = load_worker_metadata(module_path, worker_name)
    workspace_root.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), make_handler(worker_name, workspace_root, run_worker, metadata))
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
