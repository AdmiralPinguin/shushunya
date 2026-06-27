from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .inner_circle.iskandar import plan_lore_reconstruction
from .ledger import TaskLedger
from .local_executor import execute_run as execute_local_run
from .pipeline import write_pipeline_run


REPO_ROOT = Path(__file__).resolve().parents[2]


def response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def read_payload(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    payload = json.loads(handler.rfile.read(length).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def route_task(message: str) -> str:
    lowered = message.lower()
    if any(term in lowered for term in ("скалатрак", "skalathrax", "лор", "lore", "источник", "source")):
        return "IskandarKhayon"
    return "IskandarKhayon"


def prepare_task(message: str, task_id: str | None, run_root: Path) -> dict[str, Any]:
    governor = route_task(message)
    if governor != "IskandarKhayon":
        raise ValueError(f"unsupported governor route: {governor}")
    plan = plan_lore_reconstruction(message, task_id=task_id)
    run_dir = run_root / plan.contract.task_id
    status = write_pipeline_run(plan.contract, run_dir)
    return {
        "ok": status["ok"],
        "gateway": "WarmasterGateway",
        "governor": governor,
        "task_id": plan.contract.task_id,
        "run_dir": str(run_dir),
        "status": status,
    }


def make_handler(run_root: Path) -> type[BaseHTTPRequestHandler]:
    class WarmasterHandler(BaseHTTPRequestHandler):
        server_version = "WarmasterGateway/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path == "/health":
                response(self, 200, {"ok": True, "gateway": "WarmasterGateway"})
                return
            parts = [part for part in self.path.split("?")[0].split("/") if part]
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
                    response(self, 200, prepare_task(message, task_id, run_root))
                    return
                parts = [part for part in self.path.split("?")[0].split("/") if part]
                if len(parts) == 3 and parts[0] == "runs" and parts[2] == "execute_local":
                    task_id = parts[1]
                    run_dir = run_root / task_id
                    if not run_dir.exists():
                        response(self, 404, {"ok": False, "error": "run not found", "task_id": task_id})
                        return
                    workspace_root = Path(str(payload.get("workspace_root") or run_dir / "work"))
                    timeout_sec = int(payload.get("timeout_sec") or 1800)
                    summary = execute_local_run(REPO_ROOT, run_dir, workspace_root, timeout_sec=timeout_sec)
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
