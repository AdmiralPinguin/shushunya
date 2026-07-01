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


def executable_client_action(action: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(action, dict) or not action:
        return {}
    method = str(action.get("method") or "").upper()
    endpoint = str(action.get("endpoint") or "")
    endpoint_method = ""
    path = endpoint
    if " " in endpoint:
        endpoint_method, path = endpoint.split(" ", 1)
        endpoint_method = endpoint_method.upper()
    method = method or endpoint_method
    body = action.get("body") if isinstance(action.get("body"), dict) else {}
    return {
        "kind": str(action.get("kind") or ""),
        "method": method,
        "path": path,
        "body": body,
        "reason": str(action.get("reason") or ""),
    }


def worker_display(worker_name: str, status: str, detail: str = "") -> dict[str, Any]:
    severity = "info"
    headline = f"{worker_name} is ready"
    if status == "task_list":
        headline = f"{worker_name} task history"
    if status in {"running", "cancelling"}:
        headline = f"{worker_name} task is running"
    elif status in {"completed", "ready", "passed", "passed_with_warnings"}:
        headline = f"{worker_name} task completed"
    elif status in {"failed", "blocked"}:
        headline = f"{worker_name} task failed"
        severity = "error"
    elif status == "cancelled":
        headline = f"{worker_name} task cancelled"
        severity = "warning"
    elif status == "queued":
        headline = f"{worker_name} task is queued"
    return {"headline": headline, "detail": detail or status, "severity": severity}


def worker_next_action(task_id: str, status: str) -> dict[str, Any]:
    if status == "task_list":
        return {"kind": "inspect_tasks", "method": "GET", "endpoint": "GET /tasks", "body": {}, "reason": "inspect worker task history"}
    if not task_id:
        return {"kind": "inspect_capabilities", "method": "GET", "endpoint": "GET /capabilities", "body": {}, "reason": "inspect worker capabilities"}
    if status in {"running", "queued", "cancelling"}:
        return {"kind": "poll_task", "method": "GET", "endpoint": f"GET /tasks/{task_id}", "body": {}, "reason": "worker task is still active"}
    if status in {"completed", "ready", "passed", "passed_with_warnings", "failed", "blocked", "cancelled"}:
        return {"kind": "inspect_task", "method": "GET", "endpoint": f"GET /tasks/{task_id}", "body": {}, "reason": "inspect recorded worker task"}
    return {"kind": "inspect_task", "method": "GET", "endpoint": f"GET /tasks/{task_id}", "body": {}, "reason": "inspect worker task state"}


def payload_with_worker_view(payload: dict[str, Any], worker_name: str, task_id: str = "", status: str = "") -> dict[str, Any]:
    resolved_task_id = str(payload.get("task_id") or task_id or "")
    resolved_status = str(payload.get("status") or status or "")
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    if task:
        resolved_task_id = str(task.get("task_id") or resolved_task_id)
        resolved_status = str(task.get("status") or resolved_status)
    result = payload.get("result") if isinstance(payload.get("result"), dict) else task.get("result") if isinstance(task.get("result"), dict) else {}
    detail = str(payload.get("error") or result.get("summary") or result.get("error") or resolved_status or "worker service is available")
    next_action = worker_next_action(resolved_task_id, resolved_status)
    return {
        **payload,
        "phase": resolved_status or "available",
        "decision": {
            "can_poll": resolved_status in {"running", "queued", "cancelling"},
            "can_cancel": resolved_status in {"running", "queued"},
            "recommended_kind": str(next_action.get("kind") or ""),
            "recommended_endpoint": str(next_action.get("endpoint") or ""),
        },
        "display": worker_display(worker_name, resolved_status or "available", detail),
        "next_action": next_action,
        "client_action": executable_client_action(next_action),
    }


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
    metadata.setdefault("api_contract", "EyeOfTerror/Warmaster/contracts/worker_api.md")
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


def quality_expectation_errors(request: dict[str, Any], worker_name: str) -> list[dict[str, str]]:
    expectations = request.get("quality_expectations") if isinstance(request.get("quality_expectations"), dict) else {}
    step_quality = expectations.get("step_quality") if isinstance(expectations.get("step_quality"), dict) else {}
    if not step_quality:
        return []
    errors: list[dict[str, str]] = []
    step = request.get("step") if isinstance(request.get("step"), dict) else {}
    step_id = str(step.get("step_id") or "")
    quality_step_id = str(step_quality.get("step_id") or "")
    if step_id and quality_step_id != step_id:
        errors.append({"field": "step_id", "error": f"expected {step_id}, got {quality_step_id or 'missing'}"})
    quality_worker = str(step_quality.get("worker") or "")
    if quality_worker and quality_worker != worker_name:
        errors.append({"field": "worker", "error": f"expected {worker_name}, got {quality_worker}"})
    expected_artifacts = step.get("expected_artifacts") if isinstance(step.get("expected_artifacts"), list) else []
    if step_quality.get("expected_artifacts") != expected_artifacts:
        errors.append({"field": "expected_artifacts", "error": "quality expectations do not match request.step.expected_artifacts"})
    for field_name in ("checks", "blockers", "revision_targets"):
        values = step_quality.get(field_name)
        if not isinstance(values, list) or not values or any(not isinstance(item, str) or not item for item in values):
            errors.append({"field": field_name, "error": "must be a non-empty list of strings"})
    revision_policy = expectations.get("revision_policy") if isinstance(expectations.get("revision_policy"), dict) else {}
    if revision_policy:
        for field_name in ("source_step",):
            if not isinstance(revision_policy.get(field_name), str) or not revision_policy.get(field_name):
                errors.append({"field": f"revision_policy.{field_name}", "error": "must be a non-empty string"})
        for field_name in ("final_steps", "allowed_steps"):
            values = revision_policy.get(field_name)
            if not isinstance(values, list) or not values or any(not isinstance(item, str) or not item for item in values):
                errors.append({"field": f"revision_policy.{field_name}", "error": "must be a non-empty list of strings"})
        allowed_steps = revision_policy.get("allowed_steps") if isinstance(revision_policy.get("allowed_steps"), list) else []
        if step_id and allowed_steps and step_id not in allowed_steps:
            errors.append({"field": "revision_policy.allowed_steps", "error": f"does not include current step {step_id}"})
        for field_name in ("requires_downstream_rerun", "requires_focused_context", "requires_gap_disclosure"):
            if not isinstance(revision_policy.get(field_name), bool):
                errors.append({"field": f"revision_policy.{field_name}", "error": "must be a boolean"})
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
    worker_metadata.setdefault("api_contract", "EyeOfTerror/Warmaster/contracts/worker_api.md")
    tasks: dict[str, dict[str, Any]] = {}
    tasks_lock = threading.RLock()
    terminal_statuses = {"completed", "ready", "passed", "passed_with_warnings", "failed", "blocked", "needs_revision", "cancelled"}

    def ensure_task(task_id: str) -> dict[str, Any]:
        task = tasks.setdefault(task_id, {"task_id": task_id, "worker": worker_name, "created_at": now_iso()})
        task["updated_at"] = now_iso()
        return task

    def service_manifest() -> dict[str, Any]:
        payload = {
            "ok": True,
            "worker": worker_name,
            "workspace_root": str(workspace_root),
            "metadata": worker_metadata,
            "capabilities": worker_metadata.get("capabilities", []),
            "api_contract": worker_metadata.get("api_contract", ""),
            "endpoints": worker_api_endpoints(),
        }
        return payload_with_worker_view(payload, worker_name)

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
                by_status: dict[str, int] = {}
                for task in task_list:
                    status = str(task.get("status") or "unknown")
                    by_status[status] = by_status.get(status, 0) + 1
                payload = payload_with_worker_view(
                    {
                        "ok": True,
                        "worker": worker_name,
                        "summary": {"total": len(task_list), "by_status": by_status},
                        "tasks": task_list,
                    },
                    worker_name,
                    status="task_list",
                )
                response(self, 200, payload)
                return
            if len(parts) == 2 and parts[0] == "tasks":
                task_id = unquote(parts[1])
                with tasks_lock:
                    task = dict(tasks.get(task_id, {}))
                if not task:
                    response(self, 404, payload_with_worker_view({"ok": False, "worker": worker_name, "task_id": task_id, "error": "task not found"}, worker_name, task_id=task_id, status="missing"))
                    return
                response(self, 200, payload_with_worker_view({"ok": True, "worker": worker_name, "task": task}, worker_name))
                return
            response(self, 404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "cancel":
                task_id = unquote(parts[1])
                with tasks_lock:
                    task = ensure_task(task_id)
                    if task.get("status") in terminal_statuses:
                        response(self, 409, payload_with_worker_view({"ok": False, "worker": worker_name, "task": dict(task), "error": "task is already terminal"}, worker_name))
                        return
                    task["cancel_requested"] = True
                    task["cancel_reason"] = "requested through worker API"
                    task["status"] = "cancelling" if task.get("status") == "running" else "cancelled"
                response(self, 200, payload_with_worker_view({"ok": True, "worker": worker_name, "task": dict(task)}, worker_name))
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
                if not task_id:
                    response(self, 400, payload_with_worker_view({"ok": False, "worker": worker_name, "status": "failed", "error": "task_id is required"}, worker_name, status="failed"))
                    return
                packet_worker = str(payload.get("worker") or "").strip()
                if packet_worker and packet_worker != worker_name:
                    result = {
                        "ok": False,
                        "worker": worker_name,
                        "task_id": task_id,
                        "status": "failed",
                        "error": f"worker mismatch: expected {worker_name}, got {packet_worker}",
                    }
                    if task_id:
                        with tasks_lock:
                            task = ensure_task(task_id)
                            task["status"] = "failed"
                            task["result"] = result
                    response(self, 409, payload_with_worker_view(result, worker_name, task_id=task_id, status="failed"))
                    return
                if task_id:
                    with tasks_lock:
                        task = ensure_task(task_id)
                        if task.get("cancel_requested"):
                            task["status"] = "cancelled"
                            task["updated_at"] = now_iso()
                            response(self, 409, payload_with_worker_view({"ok": False, "worker": worker_name, "task_id": task_id, "status": "cancelled", "error": "task cancelled before start"}, worker_name, task_id=task_id, status="cancelled"))
                            return
                        task["status"] = "running"
                artifact_errors = input_artifact_errors(request, workspace_root)
                quality_errors = quality_expectation_errors(request, worker_name)
                if artifact_errors or quality_errors:
                    result = {
                        "ok": False,
                        "worker": worker_name,
                        "task_id": task_id,
                        "status": "failed",
                        "error": "worker request preflight failed" if quality_errors else "input artifact preflight failed",
                        "input_artifact_errors": artifact_errors,
                        "quality_expectation_errors": quality_errors,
                    }
                    if task_id:
                        with tasks_lock:
                            task = ensure_task(task_id)
                            task["status"] = "failed"
                            task["result"] = result
                    response(self, 400, payload_with_worker_view(result, worker_name, task_id=task_id, status="failed"))
                    return
                result = run_worker(request, workspace_root)
                if task_id:
                    with tasks_lock:
                        task = ensure_task(task_id)
                        task["status"] = str(result.get("status") or ("completed" if result.get("ok") else "failed"))
                        task["result"] = result
                response(self, 200 if result.get("ok") else 400, payload_with_worker_view(result, worker_name, task_id=task_id))
            except Exception as exc:  # noqa: BLE001 - server boundary converts exceptions to JSON.
                if task_id:
                    with tasks_lock:
                        task = ensure_task(task_id)
                        task["status"] = "failed"
                        task["error"] = str(exc)
                response(self, 500, payload_with_worker_view({"ok": False, "worker": worker_name, "task_id": task_id, "status": "failed", "error": str(exc)}, worker_name, task_id=task_id, status="failed"))

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
