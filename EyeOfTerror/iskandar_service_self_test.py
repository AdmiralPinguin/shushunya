#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from eye_of_terror.inner_circle.iskandar_service import make_handler


def request_json(url: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    method = "POST" if data else "GET"
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def request_options(url: str) -> int:
    req = urllib.request.Request(url, method="OPTIONS")
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(root / "runs"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            if request_options(base + "/plan") != 200:
                raise AssertionError("OPTIONS did not return 200")
            health = request_json(base + "/health")
            if not health.get("ok"):
                raise AssertionError(f"bad health: {health}")
            capabilities = request_json(base + "/capabilities")
            if "dispatch_packet_preparation" not in capabilities.get("capabilities", []):
                raise AssertionError(f"bad capabilities: {capabilities}")
            plan = request_json(base + "/plan", {"task": "Собери события Скалатракса", "task_id": "iskandar-http-test"})
            if not plan.get("ok") or plan["contract"]["assigned_governor"] != "IskandarKhayon":
                raise AssertionError(f"bad plan: {plan}")
            run_dir = root / "custom-run"
            prepared = request_json(
                base + "/prepare_run",
                {"task": "Собери события Скалатракса", "task_id": "iskandar-http-test", "run_dir": str(run_dir)},
            )
            if not prepared.get("ok") or not (run_dir / "dispatch" / "source_discovery.json").exists():
                raise AssertionError(f"bad prepared run: {prepared}")
        finally:
            server.shutdown()
            thread.join(timeout=5)
    print("[ok] Iskandar service")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
