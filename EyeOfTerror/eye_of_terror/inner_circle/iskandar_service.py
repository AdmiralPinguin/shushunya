from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..contracts import build_lore_reconstruction_contract, lore_worker_plan
from .iskandar import oversight_plan, plan_lore_reconstruction
from ..pipeline import write_pipeline_run


def required_workers() -> list[str]:
    workers: list[str] = []
    for step in lore_worker_plan("capabilities"):
        if step.worker not in workers:
            workers.append(step.worker)
    return workers


def pipeline_summary() -> dict[str, Any]:
    steps = [step.to_dict() for step in lore_worker_plan("capabilities")]
    return {
        "kind": "lore_reconstruction",
        "step_count": len(steps),
        "required_workers": required_workers(),
        "steps": [
            {
                "step_id": step["step_id"],
                "worker": step["worker"],
                "depends_on": step["depends_on"],
                "expected_artifacts": step["expected_artifacts"],
                "expected_artifact_count": len(step["expected_artifacts"]),
            }
            for step in steps
        ],
    }


def oversight_template() -> dict[str, Any]:
    contract = build_lore_reconstruction_contract("capabilities", task_id="capabilities")
    return oversight_plan(contract)


def response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def payload_from(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    payload = json.loads(handler.rfile.read(length).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def service_capabilities() -> dict[str, Any]:
    return {
        "ok": True,
        "governor": "IskandarKhayon",
        "api_version": 1,
        "task_kinds": ["research", "lore_reconstruction"],
        "required_workers": required_workers(),
        "pipeline": pipeline_summary(),
        "oversight": oversight_template(),
        "capabilities": [
            "lore_reconstruction_planning",
            "worker_plan_resolution",
            "dispatch_packet_preparation",
            "oversight_plan",
            "source_research_coordination",
            "timeline_coordination",
            "writer_verifier_finalizer_coordination",
        ],
        "endpoints": [
            "GET /health",
            "GET /capabilities",
            "POST /plan",
            "POST /prepare_run",
        ],
    }


def resolve_run_dir(default_run_root: Path, requested: str, task_id: str) -> Path:
    root = default_run_root.resolve()
    candidate = Path(requested) if requested else root / task_id
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("run_dir must stay inside the default run root")
    return resolved


def make_handler(default_run_root: Path) -> type[BaseHTTPRequestHandler]:
    class IskandarHandler(BaseHTTPRequestHandler):
        server_version = "IskandarKhayon/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
            response(self, 200, {"ok": True, "governor": "IskandarKhayon"})

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path == "/health":
                response(self, 200, {"ok": True, "governor": "IskandarKhayon"})
                return
            if self.path == "/capabilities":
                response(self, 200, service_capabilities())
                return
            response(self, 404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            try:
                payload = payload_from(self)
                task = str(payload.get("task") or payload.get("message") or "").strip()
                if not task:
                    response(self, 400, {"ok": False, "error": "task is required"})
                    return
                task_id = str(payload.get("task_id") or "").strip() or None
                plan = plan_lore_reconstruction(task, task_id=task_id)
                if self.path == "/plan":
                    response(self, 200, plan.to_dict())
                    return
                if self.path == "/prepare_run":
                    run_dir = resolve_run_dir(default_run_root, str(payload.get("run_dir") or ""), plan.contract.task_id)
                    status = write_pipeline_run(plan.contract, run_dir, oversight=oversight_plan(plan.contract))
                    response(self, 200, {"ok": status["ok"], "governor": "IskandarKhayon", "status": status})
                    return
                response(self, 404, {"ok": False, "error": "not found"})
            except ValueError as exc:
                response(self, 400, {"ok": False, "governor": "IskandarKhayon", "error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - service boundary records planning failures.
                response(self, 500, {"ok": False, "governor": "IskandarKhayon", "error": str(exc)})

    return IskandarHandler


def serve(host: str, port: int, default_run_root: Path) -> None:
    default_run_root.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), make_handler(default_run_root))
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve Iskandar Khayon as an Inner Circle governor.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7101)
    parser.add_argument("--default-run-root", default="runtime/iskandar-runs")
    args = parser.parse_args()
    serve(args.host, args.port, Path(args.default_run_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
