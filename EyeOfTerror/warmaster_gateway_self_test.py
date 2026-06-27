#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from eye_of_terror.warmaster_gateway import make_handler


def request_json(url: str, payload: dict | None = None, timeout: int = 5) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    method = "POST" if data else "GET"
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        run_root = Path(temp_dir) / "runs"
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(run_root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            health = request_json(base + "/health")
            if not health.get("ok"):
                raise AssertionError(f"bad health: {health}")
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
            executed = request_json(base + "/runs/warmaster-test/execute_local", {"timeout_sec": 30}, timeout=60)
            if not executed.get("ok"):
                raise AssertionError(f"bad local execution: {executed}")
            ledger = request_json(base + "/runs/warmaster-test/ledger")
            if not ledger.get("ok") or ledger["ledger"].get("status") != "completed":
                raise AssertionError(f"bad ledger after execution: {ledger}")
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
        finally:
            server.shutdown()
            thread.join(timeout=5)
    print("[ok] Warmaster gateway")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
