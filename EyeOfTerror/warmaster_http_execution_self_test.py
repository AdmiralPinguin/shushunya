#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from eye_of_terror.warmaster_gateway import make_handler


REPO_ROOT = Path(__file__).resolve().parents[1]
MECHANICUM_ROOT = REPO_ROOT / "Mechanicum"
if str(MECHANICUM_ROOT) not in sys.path:
    sys.path.insert(0, str(MECHANICUM_ROOT))

from start_worker import load_services  # noqa: E402
from worker_runtime import load_worker, make_handler as make_worker_handler  # noqa: E402


def request_json(url: str, payload: dict | None = None, timeout: int = 5) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    method = "POST" if data else "GET"
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def patch_dispatch_ports(run_dir: Path, ports_by_worker: dict[str, int]) -> None:
    status_path = run_dir / "status.json"
    status = read_json(status_path)
    for step in status["steps"]:
        step["port"] = ports_by_worker[step["worker"]]
        dispatch_path = run_dir / "dispatch" / f"{step['step_id']}.json"
        packet = read_json(dispatch_path)
        packet["port"] = step["port"]
        write_json(dispatch_path, packet)
    write_json(status_path, status)


def main() -> int:
    services = load_services(REPO_ROOT)
    worker_servers: list[ThreadingHTTPServer] = []
    worker_threads: list[threading.Thread] = []
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        run_root = root / "runs"
        work_root = root / "work"
        gateway = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(run_root))
        gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        gateway_thread.start()
        try:
            base = f"http://127.0.0.1:{gateway.server_port}"
            task = request_json(
                base + "/task",
                {"message": "Собери все известное о событиях Скалатракса.", "task_id": "warmaster-http-test"},
            )
            if not task.get("ok"):
                raise AssertionError(f"gateway task failed: {task}")
            run_dir = Path(task["run_dir"])
            status = read_json(run_dir / "status.json")
            ports_by_worker: dict[str, int] = {}
            for step in status["steps"]:
                worker = step["worker"]
                service = services[worker]
                run_worker = load_worker(REPO_ROOT / service["module_path"], service["module"])
                server = ThreadingHTTPServer(("127.0.0.1", 0), make_worker_handler(worker, work_root, run_worker))
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                worker_servers.append(server)
                worker_threads.append(thread)
                ports_by_worker[worker] = server.server_port
            patch_dispatch_ports(run_dir, ports_by_worker)
            executed = request_json(base + "/runs/warmaster-http-test/execute_http", {"timeout_sec": 30}, timeout=60)
            if not executed.get("ok"):
                raise AssertionError(f"gateway HTTP execution failed: {executed}")
            ledger = request_json(base + "/runs/warmaster-http-test/ledger")
            if ledger["ledger"].get("status") != "completed":
                raise AssertionError(f"ledger did not complete: {ledger}")
            manifest = read_json(work_root / "skalathrax" / "final_manifest.json")
            if manifest.get("status") != "ready":
                raise AssertionError(f"final manifest is not ready: {manifest}")
        finally:
            gateway.shutdown()
            gateway_thread.join(timeout=5)
            for server in worker_servers:
                server.shutdown()
            for thread in worker_threads:
                thread.join(timeout=5)
    print("[ok] Warmaster HTTP execution")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
