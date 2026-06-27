from __future__ import annotations

import argparse
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from .inner_circle.iskandar import plan_lore_reconstruction
from doctor import run_doctor
from .http_executor import execute_run as execute_http_run
from .governors import governor_by_name, governor_refs
from .ledger import TaskLedger
from .local_executor import execute_run as execute_local_run, ordered_dispatch_paths
from .pipeline import write_pipeline_run
from .registry import worker_refs
from .routing import route_message


REPO_ROOT = Path(__file__).resolve().parents[2]
ACTIVE_RUNS: set[str] = set()
ACTIVE_RUNS_LOCK = threading.Lock()
MAX_LIST_LIMIT = 200


def parse_limit(raw_value: str, default: int, maximum: int = MAX_LIST_LIMIT) -> int:
    if not raw_value.isdigit():
        return default
    return max(0, min(int(raw_value), maximum))


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


def load_ledger_dict(ledger_path: Path) -> tuple[dict[str, Any], str]:
    if not ledger_path.exists():
        return {}, "ledger not found"
    try:
        return TaskLedger.load(ledger_path).to_dict(), ""
    except Exception as exc:  # noqa: BLE001 - gateway must report corrupt run state instead of crashing.
        return {}, str(exc)


def run_progress(status: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    planned_steps = status.get("steps", [])
    ledger_steps = ledger.get("steps", [])
    if not isinstance(planned_steps, list):
        planned_steps = []
    if not isinstance(ledger_steps, list):
        ledger_steps = []
    by_status: dict[str, int] = {}
    for step in ledger_steps:
        if not isinstance(step, dict):
            continue
        step_status = str(step.get("status") or "unknown")
        by_status[step_status] = by_status.get(step_status, 0) + 1
    completed = by_status.get("completed", 0) + by_status.get("ready", 0)
    failed = by_status.get("failed", 0)
    return {
        "planned_steps": len(planned_steps),
        "recorded_steps": len(ledger_steps),
        "completed_steps": completed,
        "failed_steps": failed,
        "by_status": by_status,
    }


def run_summary(run_dir: Path) -> dict[str, Any]:
    status_path = run_dir / "status.json"
    ledger_path = run_dir / "task_ledger.json"
    try:
        status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
        if not isinstance(status, dict):
            status = {}
    except Exception:
        status = {}
    ledger, ledger_error = load_ledger_dict(ledger_path)
    summary = {
        "task_id": ledger.get("task_id") or status.get("task_id") or run_dir.name,
        "run_dir": str(run_dir),
        "status": "corrupt" if ledger_error and ledger_path.exists() else ledger.get("status") or status.get("status") or "unknown",
        "goal": ledger.get("goal") or "",
        "governor": ledger.get("governor") or status.get("governor") or "",
        "created_at": ledger.get("created_at") or "",
        "updated_at": ledger.get("updated_at") or "",
        "result": ledger.get("result", {}),
        "progress": run_progress(status, ledger),
    }
    if ledger_error and ledger_path.exists():
        summary["ledger_error"] = ledger_error
    return summary


def list_runs(run_root: Path) -> list[dict[str, Any]]:
    if not run_root.exists():
        return []
    runs = [run_summary(path) for path in run_root.iterdir() if path.is_dir()]
    return sorted(runs, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)


def run_status_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for run in runs:
        status = str(run.get("status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
    active = sum(by_status.get(status, 0) for status in ("running", "cancelling", "queued"))
    return {"total": len(runs), "active": active, "by_status": by_status}


def run_contract(run_dir: Path) -> dict[str, Any]:
    contract_path = run_dir / "contract.json"
    if not contract_path.exists():
        return {"ok": False, "error": "contract not found"}
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {"ok": False, "error": "contract is not a JSON object"}
    return {"ok": True, "contract": payload}


def run_dispatch_packets(run_dir: Path) -> dict[str, Any]:
    dispatch_dir = run_dir / "dispatch"
    if not dispatch_dir.exists():
        return {"ok": False, "error": "dispatch directory not found"}
    packets: list[dict[str, Any]] = []
    dispatch_paths = ordered_dispatch_paths(run_dir) if (run_dir / "status.json").exists() else sorted(dispatch_dir.glob("*.json"))
    for dispatch_path in dispatch_paths:
        try:
            packet = json.loads(dispatch_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            packets.append({"path": str(dispatch_path), "ok": False, "error": str(exc)})
            continue
        packets.append({"path": str(dispatch_path), "ok": isinstance(packet, dict), "packet": packet})
    return {"ok": True, "dispatch": packets}


def run_worker_tasks(run_dir: Path, include_health: bool = False, host: str = "127.0.0.1") -> dict[str, Any]:
    dispatch_payload = run_dispatch_packets(run_dir)
    if not dispatch_payload.get("ok"):
        return dispatch_payload
    tasks: list[dict[str, Any]] = []
    for item in dispatch_payload.get("dispatch", []):
        packet = item.get("packet") if isinstance(item, dict) else {}
        if not isinstance(packet, dict):
            continue
        request_payload = packet.get("request") if isinstance(packet.get("request"), dict) else {}
        task_id = str(request_payload.get("task_id") or packet.get("task_id") or "")
        worker = str(packet.get("worker") or "")
        port = int(packet.get("port") or 0)
        task: dict[str, Any] = {
            "step_id": str(packet.get("step_id") or ""),
            "worker": worker,
            "port": port,
            "task_id": task_id,
        }
        if include_health and task_id and port:
            try:
                with urllib.request.urlopen(f"http://{host}:{port}/tasks/{quote(task_id, safe='')}", timeout=1.0) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                task["runtime"] = payload if isinstance(payload, dict) else {"ok": False, "error": "task response is not a JSON object"}
            except Exception as exc:  # noqa: BLE001 - worker task lookup is best-effort.
                task["runtime"] = {"ok": False, "error": str(exc)}
        tasks.append(task)
    return {"ok": True, "worker_tasks": tasks}


def run_events(run_dir: Path, limit: int | None = None) -> dict[str, Any]:
    ledger_path = run_dir / "task_ledger.json"
    ledger, ledger_error = load_ledger_dict(ledger_path)
    if ledger_error:
        return {"ok": False, "error": ledger_error}
    events = ledger.get("events", [])
    if not isinstance(events, list):
        events = []
    if limit is not None and limit >= 0:
        events = events[-limit:]
    return {"ok": True, "task_id": ledger.get("task_id") or run_dir.name, "events": events}


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
        try:
            ledger = TaskLedger.load(ledger_path)
        except Exception:  # noqa: BLE001 - corrupt runs are reported by run_summary and must not block recovery.
            recovered.append(run_summary(run_dir))
            continue
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


def fetch_worker_health(host: str, port: int, timeout_sec: float = 1.0) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=timeout_sec) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            return {"reachable": False, "error": "health response is not a JSON object"}
        return {"reachable": bool(payload.get("ok")), "health": payload}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"reachable": False, "error": str(exc)}


def enrich_worker_metadata(worker: dict[str, Any]) -> dict[str, Any]:
    worker_path = REPO_ROOT / str(worker.get("path") or "") / "worker.json"
    if not worker_path.exists():
        return {**worker, "metadata_available": False}
    try:
        metadata = json.loads(worker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {**worker, "metadata_available": False, "metadata_error": str(exc)}
    if not isinstance(metadata, dict):
        return {**worker, "metadata_available": False, "metadata_error": "worker metadata is not an object"}
    return {
        **worker,
        "metadata_available": True,
        "status": metadata.get("status", ""),
        "capabilities": metadata.get("capabilities", []),
        "api_contract": metadata.get("api_contract", ""),
        "metadata": metadata,
    }


def worker_registry_snapshot(include_health: bool = False, host: str = "127.0.0.1") -> list[dict[str, Any]]:
    workers = [enrich_worker_metadata(worker.to_dict()) for worker in worker_refs()]
    if not include_health:
        return workers
    for worker in workers:
        worker["runtime"] = fetch_worker_health(host, int(worker["port"]))
    return workers


def governor_registry_snapshot(include_health: bool = False, host: str = "127.0.0.1") -> list[dict[str, Any]]:
    governors = [governor.to_dict() for governor in governor_refs()]
    if not include_health:
        return governors
    for governor in governors:
        port = int(governor.get("port") or 0)
        governor["runtime"] = fetch_worker_health(host, port) if port else {"reachable": False, "error": "missing port"}
    return governors


def gateway_capabilities() -> dict[str, Any]:
    return {
        "ok": True,
        "gateway": "WarmasterGateway",
        "api_version": 1,
        "capabilities": [
            "task_routing",
            "run_preparation",
            "run_listing",
            "run_status_summary",
            "ledger_read",
            "artifact_listing",
            "artifact_text_read",
            "run_contract_read",
            "run_dispatch_read",
            "run_worker_task_read",
            "run_events_read",
            "local_execution",
            "http_worker_execution",
            "background_execution",
            "cooperative_cancellation",
            "worker_cancel_fanout",
            "stale_run_recovery",
            "governor_registry",
            "governor_health_snapshot",
            "worker_registry",
            "worker_health_snapshot",
            "state_snapshot",
            "doctor",
        ],
        "endpoints": [
            "GET /health",
            "GET /capabilities",
            "GET /state",
            "GET /doctor",
            "GET /governors",
            "GET /governors?health=1",
            "GET /workers",
            "GET /workers?health=1",
            "POST /task",
            "GET /runs",
            "GET /runs/{task_id}",
            "GET /runs/{task_id}/ledger",
            "GET /runs/{task_id}/contract",
            "GET /runs/{task_id}/dispatch",
            "GET /runs/{task_id}/worker_tasks",
            "GET /runs/{task_id}/events",
            "GET /runs/{task_id}/artifacts",
            "GET /runs/{task_id}/artifact_text?path=/work/...",
            "POST /runs/{task_id}/execute_local",
            "POST /runs/{task_id}/execute_http",
            "POST /runs/{task_id}/start_local",
            "POST /runs/{task_id}/start_http",
            "POST /runs/{task_id}/cancel",
            "POST /recover_stale",
        ],
    }


def gateway_state(run_root: Path, run_limit: int = 20) -> dict[str, Any]:
    all_runs = list_runs(run_root)
    runs = all_runs[: parse_limit(str(run_limit), default=20)]
    return {
        "ok": True,
        "gateway": "WarmasterGateway",
        "capabilities": gateway_capabilities(),
        "governors": governor_registry_snapshot(),
        "workers": worker_registry_snapshot(),
        "run_summary": run_status_summary(all_runs),
        "runs": runs,
    }


def cancel_http_worker_tasks(run_dir: Path, host: str = "127.0.0.1", timeout_sec: float = 1.0) -> list[dict[str, Any]]:
    dispatch_dir = run_dir / "dispatch"
    if not dispatch_dir.exists():
        return []
    results: list[dict[str, Any]] = []
    for dispatch_path in sorted(dispatch_dir.glob("*.json")):
        try:
            packet = json.loads(dispatch_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            results.append({"dispatch": str(dispatch_path), "ok": False, "error": str(exc)})
            continue
        if not isinstance(packet, dict):
            results.append({"dispatch": str(dispatch_path), "ok": False, "error": "dispatch packet is not an object"})
            continue
        request_payload = packet.get("request") if isinstance(packet.get("request"), dict) else {}
        task_id = str(request_payload.get("task_id") or packet.get("task_id") or "")
        worker = str(packet.get("worker") or "")
        port = int(packet.get("port") or 0)
        if not task_id or not port:
            results.append({"worker": worker, "port": port, "task_id": task_id, "ok": False, "error": "missing task_id or port"})
            continue
        url = f"http://{host}:{port}/tasks/{quote(task_id, safe='')}/cancel"
        try:
            data = json.dumps({}).encode("utf-8")
            request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                payload = json.loads(response.read().decode("utf-8"))
            results.append({"worker": worker, "port": port, "task_id": task_id, "ok": bool(isinstance(payload, dict) and payload.get("ok")), "response": payload})
        except Exception as exc:  # noqa: BLE001 - cancellation fan-out is best-effort.
            results.append({"worker": worker, "port": port, "task_id": task_id, "ok": False, "error": str(exc)})
    return results


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
            if parsed.path == "/capabilities":
                response(self, 200, gateway_capabilities())
                return
            if parsed.path == "/state":
                query = parse_qs(parsed.query)
                raw_limit = query.get("run_limit", ["20"])[0]
                run_limit = parse_limit(raw_limit, default=20)
                response(self, 200, gateway_state(run_root, run_limit=run_limit))
                return
            if parsed.path == "/doctor":
                payload = run_doctor()
                response(self, 200 if payload.get("ok") else 500, payload)
                return
            if parsed.path == "/governors":
                query = parse_qs(parsed.query)
                include_health = query.get("health", ["0"])[0] in {"1", "true", "yes"}
                response(
                    self,
                    200,
                    {
                        "ok": True,
                        "health_checked": include_health,
                        "governors": governor_registry_snapshot(include_health=include_health),
                    },
                )
                return
            if parsed.path == "/workers":
                query = parse_qs(parsed.query)
                include_health = query.get("health", ["0"])[0] in {"1", "true", "yes"}
                response(
                    self,
                    200,
                    {
                        "ok": True,
                        "health_checked": include_health,
                        "workers": worker_registry_snapshot(include_health=include_health),
                    },
                )
                return
            parts = [part for part in parsed.path.split("/") if part]
            if parts == ["runs"]:
                runs = list_runs(run_root)
                response(self, 200, {"ok": True, "run_summary": run_status_summary(runs), "runs": runs})
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
                    ledger, ledger_error = load_ledger_dict(ledger_path)
                    if ledger_error:
                        response(self, 500, {"ok": False, "error": ledger_error, "task_id": task_id})
                        return
                    response(self, 200, {"ok": True, "ledger": ledger})
                    return
                if len(parts) == 3 and parts[2] == "contract":
                    payload = run_contract(run_dir)
                    response(self, 200 if payload.get("ok") else 404, payload)
                    return
                if len(parts) == 3 and parts[2] == "dispatch":
                    payload = run_dispatch_packets(run_dir)
                    response(self, 200 if payload.get("ok") else 404, payload)
                    return
                if len(parts) == 3 and parts[2] == "worker_tasks":
                    query = parse_qs(parsed.query)
                    include_live = query.get("live", ["0"])[0] in {"1", "true", "yes"}
                    host = query.get("host", ["127.0.0.1"])[0]
                    payload = run_worker_tasks(run_dir, include_health=include_live, host=host)
                    response(self, 200 if payload.get("ok") else 404, payload)
                    return
                if len(parts) == 3 and parts[2] == "events":
                    query = parse_qs(parsed.query)
                    raw_limit = query.get("limit", [""])[0]
                    limit = parse_limit(raw_limit, default=MAX_LIST_LIMIT) if raw_limit else None
                    payload = run_events(run_dir, limit=limit)
                    response(self, 200 if payload.get("ok") else 404, payload)
                    return
                if len(parts) == 3 and parts[2] == "artifacts":
                    if not ledger_path.exists():
                        response(self, 404, {"ok": False, "error": "ledger not found", "task_id": task_id})
                        return
                    ledger, ledger_error = load_ledger_dict(ledger_path)
                    if ledger_error:
                        response(self, 500, {"ok": False, "error": ledger_error, "task_id": task_id})
                        return
                    response(self, 200, {"ok": True, "task_id": task_id, **artifact_status(ledger)})
                    return
                if len(parts) == 3 and parts[2] == "artifact_text":
                    if not ledger_path.exists():
                        response(self, 404, {"ok": False, "error": "ledger not found", "task_id": task_id})
                        return
                    query = parse_qs(parsed.query)
                    artifact_path = query.get("path", [""])[0]
                    ledger, ledger_error = load_ledger_dict(ledger_path)
                    if ledger_error:
                        response(self, 500, {"ok": False, "error": ledger_error, "task_id": task_id})
                        return
                    try:
                        payload = artifact_text(ledger, artifact_path)
                    except ValueError as exc:
                        response(self, 400, {"ok": False, "error": str(exc)})
                        return
                    response(self, 200 if payload.get("ok") else 404, payload)
                    return
                status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
                ledger, ledger_error = load_ledger_dict(ledger_path)
                if ledger_error and ledger_path.exists():
                    response(self, 200, {"ok": True, "task_id": task_id, "run_dir": str(run_dir), "status": status, "ledger": {}, "ledger_error": ledger_error})
                    return
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
                    host = str(payload.get("host") or "127.0.0.1")
                    worker_cancellations = cancel_http_worker_tasks(run_root / task_id, host=host)
                    response(
                        self,
                        200,
                        {
                            "ok": True,
                            "task_id": task_id,
                            "status": "cancelling",
                            "ledger": ledger.to_dict(),
                            "worker_cancellations": worker_cancellations,
                        },
                    )
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
