#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from eye_of_terror.inner_circle.iskandar_service import make_handler, resolve_run_dir


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
        if resolve_run_dir(root / "runs", "child", "task").resolve() != (root / "runs" / "child").resolve():
            raise AssertionError("relative run_dir did not resolve under default root")
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
            if capabilities.get("required_workers", [])[0] != "Lexmechanic" or "FabricatorFinalis" not in capabilities.get("required_workers", []):
                raise AssertionError(f"capabilities did not expose required workers: {capabilities}")
            plan = request_json(base + "/plan", {"task": "Собери события Скалатракса", "task_id": "iskandar-http-test"})
            if not plan.get("ok") or plan["contract"]["assigned_governor"] != "IskandarKhayon":
                raise AssertionError(f"bad plan: {plan}")
            run_dir = root / "runs" / "custom-run"
            prepared = request_json(
                base + "/prepare_run",
                {"task": "Собери события Скалатракса", "task_id": "iskandar-http-test", "run_dir": str(run_dir)},
            )
            if not prepared.get("ok") or not (run_dir / "dispatch" / "source_discovery.json").exists():
                raise AssertionError(f"bad prepared run: {prepared}")
            try:
                request_json(
                    base + "/prepare_run",
                    {"task": "Собери события Скалатракса", "task_id": "iskandar-escape-test", "run_dir": str(root / "escape")},
            )
            except urllib.error.HTTPError as exc:
                if exc.code != 400:
                    raise
                rejected = json.loads(exc.read().decode("utf-8"))
                if "run_dir must stay inside" not in rejected.get("error", ""):
                    raise AssertionError(f"bad run_dir rejection: {rejected}")
            else:
                raise AssertionError("prepare_run should reject run_dir outside default root")
        finally:
            server.shutdown()
            thread.join(timeout=5)
    print("[ok] Iskandar service")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
