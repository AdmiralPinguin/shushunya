#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from eye_of_terror.http_executor import execute_run
from eye_of_terror.inner_circle.iskandar import plan_lore_reconstruction
from eye_of_terror.pipeline import write_pipeline_run


REPO_ROOT = Path(__file__).resolve().parents[1]
MECHANICUM_ROOT = REPO_ROOT / "Mechanicum"
if str(MECHANICUM_ROOT) not in sys.path:
    sys.path.insert(0, str(MECHANICUM_ROOT))

from start_worker import load_services  # noqa: E402
from worker_runtime import load_worker, make_handler  # noqa: E402


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
    servers: list[ThreadingHTTPServer] = []
    threads: list[threading.Thread] = []
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        run_dir = root / "run"
        work_dir = root / "work"
        plan = plan_lore_reconstruction("Собери все известное о событиях Скалатракса.", task_id="http-pipeline-test")
        write_pipeline_run(plan.contract, run_dir)
        workers = [step.worker for step in plan.contract.worker_plan]
        ports_by_worker: dict[str, int] = {}
        try:
            for worker in workers:
                service = services[worker]
                run_worker = load_worker(REPO_ROOT / service["module_path"], service["module"])
                server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(worker, work_dir, run_worker))
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                servers.append(server)
                threads.append(thread)
                ports_by_worker[worker] = server.server_port
            patch_dispatch_ports(run_dir, ports_by_worker)
            summary = execute_run(run_dir, timeout_sec=30)
            if not summary.get("ok"):
                raise AssertionError(summary)
            manifest = read_json(work_dir / "skalathrax" / "final_manifest.json")
            if manifest.get("status") != "ready":
                raise AssertionError(f"final manifest is not ready: {manifest}")
            ledger = read_json(run_dir / "task_ledger.json")
            if ledger.get("status") != "completed" or len(ledger.get("steps", [])) != len(workers):
                raise AssertionError(f"bad task ledger: {ledger}")
            if ledger.get("result", {}).get("final_step") != "finalize":
                raise AssertionError(f"ledger did not record final result: {ledger}")
        finally:
            for server in servers:
                server.shutdown()
            for thread in threads:
                thread.join(timeout=5)
    print("[ok] http pipeline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
