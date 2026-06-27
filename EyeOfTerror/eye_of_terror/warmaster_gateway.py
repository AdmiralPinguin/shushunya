from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .inner_circle.iskandar import plan_lore_reconstruction
from .http_executor import execute_run as execute_http_run
from .governors import governor_by_name, governor_refs
from .ledger import TaskLedger
from .local_executor import execute_run as execute_local_run
from .pipeline import write_pipeline_run
from .registry import worker_refs
from .routing import route_message


REPO_ROOT = Path(__file__).resolve().parents[2]
ACTIVE_RUNS: set[str] = set()
ACTIVE_RUNS_LOCK = threading.Lock()


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


def read_payload(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    payload = json.loads(handler.rfile.read(length).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def prepare_task(message: str, task_id: str | None, run_root: Path) -> dict[str, Any]:
    route = route_message(message)
    if not route.ok:
        return {"ok": False, "gateway": "WarmasterGateway", "error": route.reason, "kind": route.kind}
    governor = route.governor
    governor_ref = governor_by_name(governor)
    if governor_ref is None or not governor_ref.active():
        return {"ok": False, "gateway": "WarmasterGateway", "error": f"governor is not active: {governor}", "kind": route.kind}
    plan = plan_lore_reconstruction(message, task_id=task_id)
    run_dir = run_root / plan.contract.task_id
    status = write_pipeline_run(plan.contract, run_dir)
    TaskLedger.create(run_dir / "task_ledger.json", plan.contract.task_id, plan.contract.goal, governor)
    return {
        "ok": status["ok"],
        "gateway": "WarmasterGateway",
        "governor": governor,
        "task_id": plan.contract.task_id,
        "run_dir": str(run_dir),
        "status": status,
    }


def run_summary(run_dir: Path) -> dict[str, Any]:
    status_path = run_dir / "status.json"
    ledger_path = run_dir / "task_ledger.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    ledger = TaskLedger.load(ledger_path).to_dict() if ledger_path.exists() else {}
    return {
        "task_id": ledger.get("task_id") or status.get("task_id") or run_dir.name,
        "run_dir": str(run_dir),
        "status": ledger.get("status") or status.get("status") or "unknown",
        "goal": ledger.get("goal") or "",
        "governor": ledger.get("governor") or status.get("governor") or "",
        "created_at": ledger.get("created_at") or "",
        "updated_at": ledger.get("updated_at") or "",
        "result": ledger.get("result", {}),
    }


def list_runs(run_root: Path) -> list[dict[str, Any]]:
    if not run_root.exists():
        return []
    runs = [run_summary(path) for path in run_root.iterdir() if path.is_dir()]
    return sorted(runs, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)


def recover_stale_runs(run_root: Path) -> list[dict[str, Any]]:
    recovered: list[dict[str, Any]] = []
    if not run_root.exists():
        return recovered
    with ACTIVE_RUNS_LOCK:
        active = set(ACTIVE_RUNS)
    for run_dir in run_root.iterdir():
        if not run_dir.is_dir() or run_dir.name in active:
            continue
        ledger_path = run_dir / "task_ledger.json"
        if not ledger_path.exists():
            continue
        ledger = TaskLedger.load(ledger_path)
        if ledger.data.get("status") in {"running", "cancelling"}:
            ledger.set_status("interrupted")
            ledger.record_event("recovered_stale_run", {"reason": "gateway process has no active worker thread"})
            recovered.append(run_summary(run_dir))
    return recovered


def artifact_status(ledger: dict[str, Any]) -> dict[str, Any]:
    result = ledger.get("result", {}) if isinstance(ledger.get("result"), dict) else {}
    workspace_root = str(result.get("workspace_root") or "")
    artifacts = result.get("artifacts", [])
    if not isinstance(artifacts, list):
        artifacts = []
    items: list[dict[str, Any]] = []
    for artifact in artifacts:
        sandbox_path = str(artifact)
        item: dict[str, Any] = {"path": sandbox_path}
        if workspace_root and sandbox_path.startswith("/work/"):
            host_path = Path(workspace_root) / sandbox_path.removeprefix("/work/")
            item["host_path"] = str(host_path)
            item["exists"] = host_path.exists()
            item["bytes"] = host_path.stat().st_size if host_path.exists() else 0
        else:
            item["exists"] = False
            item["bytes"] = 0
        items.append(item)
    return {"workspace_root": workspace_root, "artifacts": items}


def resolve_artifact(ledger: dict[str, Any], artifact_path: str) -> Path:
    result = ledger.get("result", {}) if isinstance(ledger.get("result"), dict) else {}
    workspace_root = str(result.get("workspace_root") or "")
    if not workspace_root:
        raise ValueError("workspace_root is not recorded for this run")
    if not artifact_path.startswith("/work/"):
        raise ValueError("artifact path must start with /work/")
    root = Path(workspace_root).resolve()
    host_path = (root / artifact_path.removeprefix("/work/")).resolve()
    if root not in host_path.parents and host_path != root:
        raise ValueError("artifact path escapes workspace_root")
    return host_path


def artifact_text(ledger: dict[str, Any], artifact_path: str, max_bytes: int = 500000) -> dict[str, Any]:
    host_path = resolve_artifact(ledger, artifact_path)
    if not host_path.exists():
        return {"ok": False, "error": "artifact not found", "path": artifact_path}
    data = host_path.read_bytes()[: max_bytes + 1]
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    return {
        "ok": True,
        "path": artifact_path,
        "host_path": str(host_path),
        "bytes": host_path.stat().st_size,
        "truncated": truncated,
        "text": data.decode("utf-8", errors="replace"),
    }


def start_background(task_id: str, target: Any) -> bool:
    with ACTIVE_RUNS_LOCK:
        if task_id in ACTIVE_RUNS:
            return False
        ACTIVE_RUNS.add(task_id)

    def wrapped() -> None:
        try:
            target()
        finally:
            with ACTIVE_RUNS_LOCK:
                ACTIVE_RUNS.discard(task_id)

    threading.Thread(target=wrapped, daemon=True).start()
    return True


def make_handler(run_root: Path) -> type[BaseHTTPRequestHandler]:
    class WarmasterHandler(BaseHTTPRequestHandler):
        server_version = "WarmasterGateway/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                response(self, 200, {"ok": True, "gateway": "WarmasterGateway"})
                return
            if parsed.path == "/governors":
                response(self, 200, {"ok": True, "governors": [governor.to_dict() for governor in governor_refs()]})
                return
            if parsed.path == "/workers":
                response(self, 200, {"ok": True, "workers": [worker.to_dict() for worker in worker_refs()]})
                return
            parts = [part for part in parsed.path.split("/") if part]
            if parts == ["runs"]:
                response(self, 200, {"ok": True, "runs": list_runs(run_root)})
                return
            if len(parts) in {2, 3} and parts[0] == "runs":
                task_id = parts[1]
                run_dir = run_root / task_id
                status_path = run_dir / "status.json"
                ledger_path = run_dir / "task_ledger.json"
                if not run_dir.exists():
                    response(self, 404, {"ok": False, "error": "run not found", "task_id": task_id})
                    return
                if len(parts) == 3 and parts[2] == "ledger":
                    if not ledger_path.exists():
                        response(self, 404, {"ok": False, "error": "ledger not found", "task_id": task_id})
                        return
                    response(self, 200, {"ok": True, "ledger": TaskLedger.load(ledger_path).to_dict()})
                    return
                if len(parts) == 3 and parts[2] == "artifacts":
                    if not ledger_path.exists():
                        response(self, 404, {"ok": False, "error": "ledger not found", "task_id": task_id})
                        return
                    response(self, 200, {"ok": True, "task_id": task_id, **artifact_status(TaskLedger.load(ledger_path).to_dict())})
                    return
                if len(parts) == 3 and parts[2] == "artifact_text":
                    if not ledger_path.exists():
                        response(self, 404, {"ok": False, "error": "ledger not found", "task_id": task_id})
                        return
                    query = parse_qs(parsed.query)
                    artifact_path = query.get("path", [""])[0]
                    try:
                        payload = artifact_text(TaskLedger.load(ledger_path).to_dict(), artifact_path)
                    except ValueError as exc:
                        response(self, 400, {"ok": False, "error": str(exc)})
                        return
                    response(self, 200 if payload.get("ok") else 404, payload)
                    return
                status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
                ledger = TaskLedger.load(ledger_path).to_dict() if ledger_path.exists() else {}
                response(self, 200, {"ok": True, "task_id": task_id, "run_dir": str(run_dir), "status": status, "ledger": ledger})
                return
            response(self, 404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            try:
                payload = read_payload(self)
                if self.path == "/task":
                    message = str(payload.get("message") or payload.get("task") or "").strip()
                    if not message:
                        response(self, 400, {"ok": False, "error": "message is required"})
                        return
                    task_id = str(payload.get("task_id") or "").strip() or None
                    prepared = prepare_task(message, task_id, run_root)
                    response(self, 200 if prepared.get("ok") else 400, prepared)
                    return
                if self.path == "/recover_stale":
                    recovered = recover_stale_runs(run_root)
                    response(self, 200, {"ok": True, "recovered": recovered})
                    return
                parts = [part for part in self.path.split("?")[0].split("/") if part]
                if len(parts) == 3 and parts[0] == "runs" and parts[2] == "cancel":
                    task_id = parts[1]
                    ledger_path = run_root / task_id / "task_ledger.json"
                    if not ledger_path.exists():
                        response(self, 404, {"ok": False, "error": "ledger not found", "task_id": task_id})
                        return
                    reason = str(payload.get("reason") or "").strip()
                    ledger = TaskLedger.load(ledger_path)
                    ledger.request_cancel(reason)
                    response(self, 200, {"ok": True, "task_id": task_id, "status": "cancelling", "ledger": ledger.to_dict()})
                    return
                if len(parts) == 3 and parts[0] == "runs" and parts[2] in {"execute_local", "execute_http", "start_local", "start_http"}:
                    task_id = parts[1]
                    run_dir = run_root / task_id
                    if not run_dir.exists():
                        response(self, 404, {"ok": False, "error": "run not found", "task_id": task_id})
                        return
                    ledger_path = run_dir / "task_ledger.json"
                    force = bool(payload.get("force"))
                    if ledger_path.exists() and not force:
                        ledger = TaskLedger.load(ledger_path).to_dict()
                        if ledger.get("status") == "completed":
                            response(
                                self,
                                409,
                                {
                                    "ok": False,
                                    "error": "run already completed; pass force=true to rerun",
                                    "ledger": ledger,
                                },
                            )
                            return
                    workspace_root = Path(str(payload.get("workspace_root") or run_dir / "work"))
                    timeout_sec = max(1, min(int(payload.get("timeout_sec") or 1800), 7200))
                    if parts[2] in {"execute_local", "start_local"}:
                        executor = lambda: execute_local_run(REPO_ROOT, run_dir, workspace_root, timeout_sec=timeout_sec)
                    else:
                        host = str(payload.get("host") or "127.0.0.1")
                        http_workspace_root = Path(str(payload["workspace_root"])) if "workspace_root" in payload else None
                        executor = lambda: execute_http_run(run_dir, host=host, timeout_sec=timeout_sec, workspace_root=http_workspace_root)
                    if parts[2].startswith("start_"):
                        started = start_background(task_id, executor)
                        if not started:
                            response(self, 409, {"ok": False, "error": "run already active", "task_id": task_id})
                            return
                        response(self, 202, {"ok": True, "task_id": task_id, "status": "started"})
                        return
                    if parts[2] == "execute_local":
                        summary = execute_local_run(REPO_ROOT, run_dir, workspace_root, timeout_sec=timeout_sec)
                    else:
                        host = str(payload.get("host") or "127.0.0.1")
                        http_workspace_root = Path(str(payload["workspace_root"])) if "workspace_root" in payload else None
                        summary = execute_http_run(run_dir, host=host, timeout_sec=timeout_sec, workspace_root=http_workspace_root)
                    response(self, 200 if summary.get("ok") else 500, {"ok": bool(summary.get("ok")), "summary": summary})
                    return
                response(self, 404, {"ok": False, "error": "not found"})
            except Exception as exc:  # noqa: BLE001 - gateway boundary records routing failures.
                response(self, 500, {"ok": False, "gateway": "WarmasterGateway", "error": str(exc)})

    return WarmasterHandler


def serve(host: str, port: int, run_root: Path) -> None:
    run_root.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), make_handler(run_root))
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the EyeOfTerror Warmaster Gateway.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7000)
    parser.add_argument("--run-root", default="runtime/warmaster-runs")
    args = parser.parse_args()
    serve(args.host, args.port, Path(args.run_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
