#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from eye_of_terror.warmaster_gateway import cancel_http_worker_tasks, make_handler
from eye_of_terror.ledger import TaskLedger


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def patch_dispatch_ports(run_dir: Path, port: int) -> None:
    for dispatch_path in sorted((run_dir / "dispatch").glob("*.json")):
        packet = json.loads(dispatch_path.read_text(encoding="utf-8"))
        if isinstance(packet, dict):
            packet["port"] = port
            write_json(dispatch_path, packet)


def request_json(url: str, payload: dict | None = None, timeout: int = 5) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    method = "POST" if data else "GET"
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def request_options(url: str) -> int:
    req = urllib.request.Request(url, method="OPTIONS")
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status


def make_cancel_handler(calls: list[str]) -> type[BaseHTTPRequestHandler]:
    class CancelHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            calls.append(self.path)
            body = {"ok": True, "task": {"status": "cancelled"}}
            data = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return CancelHandler


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        run_root = Path(temp_dir) / "runs"
        bad_dispatch = Path(temp_dir) / "bad-dispatch" / "dispatch"
        bad_dispatch.mkdir(parents=True, exist_ok=True)
        (bad_dispatch / "broken.json").write_text("{", encoding="utf-8")
        bad_cancel = cancel_http_worker_tasks(bad_dispatch.parent)
        if not bad_cancel or bad_cancel[0].get("ok"):
            raise AssertionError(f"bad dispatch cancel fan-out should report failure: {bad_cancel}")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(run_root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            if request_options(base + "/task") != 204:
                raise AssertionError("OPTIONS did not return 204")
            health = request_json(base + "/health")
            if not health.get("ok"):
                raise AssertionError(f"bad health: {health}")
            capabilities = request_json(base + "/capabilities")
            required_capabilities = {"background_execution", "worker_registry", "worker_cancel_fanout"}
            if not required_capabilities.issubset(set(capabilities.get("capabilities", []))):
                raise AssertionError(f"bad gateway capabilities response: {capabilities}")
            governors = request_json(base + "/governors")
            if not governors.get("ok") or not any(item.get("name") == "IskandarKhayon" for item in governors.get("governors", [])):
                raise AssertionError(f"bad governors response: {governors}")
            governor_health = request_json(base + "/governors?health=1")
            if not governor_health.get("health_checked") or not all("runtime" in item for item in governor_health.get("governors", [])):
                raise AssertionError(f"bad governor health response: {governor_health}")
            workers = request_json(base + "/workers")
            if not workers.get("ok") or not any(item.get("name") == "Lexmechanic" for item in workers.get("workers", [])):
                raise AssertionError(f"bad workers response: {workers}")
            lexmechanic = next(item for item in workers["workers"] if item.get("name") == "Lexmechanic")
            if not lexmechanic.get("metadata_available") or "web_search" not in lexmechanic.get("capabilities", []):
                raise AssertionError(f"workers response did not expose worker metadata: {workers}")
            shushunya = next(item for item in workers["workers"] if item.get("name") == "ShushunyaAgent")
            if not shushunya.get("metadata_available") or shushunya.get("status") != "active":
                raise AssertionError(f"general worker metadata is missing: {shushunya}")
            worker_health = request_json(base + "/workers?health=1")
            if not worker_health.get("health_checked") or not all("runtime" in item for item in worker_health.get("workers", [])):
                raise AssertionError(f"bad worker health response: {worker_health}")
            try:
                request_json(base + "/task", {"message": "почини python приложение", "task_id": "unsupported-code"})
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
                rejected = json.loads(exc.read().decode("utf-8"))
                if rejected.get("kind") != "code":
                    raise AssertionError(f"bad unsupported route response: {rejected}")
            else:
                raise AssertionError("unsupported code task should be rejected until a code governor exists")
            task = request_json(
                base + "/task",
                {"message": "Собери все известное о событиях Скалатракса.", "task_id": "warmaster-test"},
            )
            if not task.get("ok") or task.get("governor") != "IskandarKhayon":
                raise AssertionError(f"bad task response: {task}")
            run_dir = Path(task["run_dir"])
            if not (run_dir / "dispatch" / "source_discovery.json").exists():
                raise AssertionError(f"gateway did not prepare run package: {task}")
            run_status = request_json(base + "/runs/warmaster-test")
            if not run_status.get("ok") or run_status.get("task_id") != "warmaster-test" or not run_status.get("ledger"):
                raise AssertionError(f"bad run status: {run_status}")
            contract = request_json(base + "/runs/warmaster-test/contract")
            if not contract.get("ok") or contract["contract"].get("task_id") != "warmaster-test":
                raise AssertionError(f"bad run contract: {contract}")
            dispatch = request_json(base + "/runs/warmaster-test/dispatch")
            if not dispatch.get("ok") or not any(item.get("packet", {}).get("worker") == "Lexmechanic" for item in dispatch.get("dispatch", [])):
                raise AssertionError(f"bad run dispatch: {dispatch}")
            events = request_json(base + "/runs/warmaster-test/events?limit=1")
            if not events.get("ok") or len(events.get("events", [])) != 1:
                raise AssertionError(f"bad run events: {events}")
            run_list = request_json(base + "/runs")
            if not run_list.get("ok") or not any(item.get("task_id") == "warmaster-test" for item in run_list.get("runs", [])):
                raise AssertionError(f"bad run list: {run_list}")
            executed = request_json(base + "/runs/warmaster-test/execute_local", {"timeout_sec": 30}, timeout=60)
            if not executed.get("ok"):
                raise AssertionError(f"bad local execution: {executed}")
            ledger = request_json(base + "/runs/warmaster-test/ledger")
            if not ledger.get("ok") or ledger["ledger"].get("status") != "completed":
                raise AssertionError(f"bad ledger after execution: {ledger}")
            artifacts = request_json(base + "/runs/warmaster-test/artifacts")
            if not artifacts.get("ok") or not artifacts.get("artifacts") or not artifacts["artifacts"][0].get("exists"):
                raise AssertionError(f"bad artifacts response: {artifacts}")
            artifact_path = artifacts["artifacts"][0]["path"]
            text_artifact = request_json(base + f"/runs/warmaster-test/artifact_text?path={artifact_path}")
            if not text_artifact.get("ok") or "ready" not in text_artifact.get("text", ""):
                raise AssertionError(f"bad artifact text response: {text_artifact}")
            event_types = [event.get("type") for event in ledger["ledger"].get("events", [])]
            if event_types.count("task_created") != 1:
                raise AssertionError(f"ledger should preserve original task_created event: {ledger}")
            try:
                request_json(base + "/runs/warmaster-test/execute_local", {"timeout_sec": 30}, timeout=60)
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                blocked = json.loads(exc.read().decode("utf-8"))
                if "already completed" not in blocked.get("error", ""):
                    raise AssertionError(f"bad rerun block response: {blocked}")
            else:
                raise AssertionError("completed run should not execute again without force=true")
            forced = request_json(base + "/runs/warmaster-test/execute_local", {"timeout_sec": 30, "force": True}, timeout=60)
            if not forced.get("ok"):
                raise AssertionError(f"forced rerun failed: {forced}")
            background_task = request_json(
                base + "/task",
                {"message": "Собери все известное о событиях Скалатракса.", "task_id": "warmaster-background-test"},
            )
            if not background_task.get("ok"):
                raise AssertionError(f"bad background task response: {background_task}")
            started = request_json(base + "/runs/warmaster-background-test/start_local", {"timeout_sec": 30})
            if started.get("status") != "started":
                raise AssertionError(f"background start failed: {started}")
            for _ in range(60):
                background_ledger = request_json(base + "/runs/warmaster-background-test/ledger")
                if background_ledger["ledger"].get("status") == "completed":
                    break
                time.sleep(0.2)
            else:
                raise AssertionError(f"background run did not complete: {background_ledger}")
            cancel_task = request_json(
                base + "/task",
                {"message": "Собери все известное о событиях Скалатракса.", "task_id": "warmaster-cancel-test"},
            )
            if not cancel_task.get("ok"):
                raise AssertionError(f"bad cancel task response: {cancel_task}")
            cancel_calls: list[str] = []
            worker_server = ThreadingHTTPServer(("127.0.0.1", 0), make_cancel_handler(cancel_calls))
            worker_thread = threading.Thread(target=worker_server.serve_forever, daemon=True)
            worker_thread.start()
            try:
                patch_dispatch_ports(Path(cancel_task["run_dir"]), worker_server.server_port)
                cancelled = request_json(base + "/runs/warmaster-cancel-test/cancel", {"reason": "test"})
            finally:
                worker_server.shutdown()
                worker_thread.join(timeout=5)
            if not cancelled.get("ok") or not cancelled["ledger"].get("cancel_requested"):
                raise AssertionError(f"bad cancel response: {cancelled}")
            if not cancel_calls or not any(item.get("ok") for item in cancelled.get("worker_cancellations", [])):
                raise AssertionError(f"cancel was not propagated to worker tasks: {cancelled}")
            try:
                request_json(base + "/runs/warmaster-cancel-test/execute_local", {"timeout_sec": 30}, timeout=60)
            except urllib.error.HTTPError as exc:
                if exc.code != 500:
                    raise
                cancelled_execution = json.loads(exc.read().decode("utf-8"))
                if not cancelled_execution.get("summary", {}).get("cancelled"):
                    raise AssertionError(f"bad cancelled execution response: {cancelled_execution}")
            else:
                raise AssertionError("cancelled run should not execute successfully")
            stale_dir = run_root / "stale-test"
            stale_dir.mkdir(parents=True, exist_ok=True)
            stale = TaskLedger.create(stale_dir / "task_ledger.json", "stale-test", "goal", "IskandarKhayon")
            stale.set_status("running")
            recovered = request_json(base + "/recover_stale", {}, timeout=10)
            if not recovered.get("ok") or recovered["recovered"][0].get("status") != "interrupted":
                raise AssertionError(f"bad stale recovery: {recovered}")
        finally:
            server.shutdown()
            thread.join(timeout=5)
    print("[ok] Warmaster gateway")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
