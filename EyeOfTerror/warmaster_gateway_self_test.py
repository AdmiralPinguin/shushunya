#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from eye_of_terror.warmaster_gateway import make_handler


def request_json(url: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    method = "POST" if data else "GET"
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=5) as response:
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
            if not run_status.get("ok") or run_status.get("task_id") != "warmaster-test":
                raise AssertionError(f"bad run status: {run_status}")
        finally:
            server.shutdown()
            thread.join(timeout=5)
    print("[ok] Warmaster gateway")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
