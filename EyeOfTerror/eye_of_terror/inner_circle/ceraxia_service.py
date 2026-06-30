from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..contracts import build_code_task_contract, code_worker_plan
from ..pipeline import write_pipeline_run
from .ceraxia import executable_client_action, oversight_plan, patch_contract_capabilities, payload_with_plan_view, plan_code_task


def required_workers() -> list[str]:
    workers: list[str] = []
    for step in code_worker_plan("capabilities"):
        if step.worker not in workers:
            workers.append(step.worker)
    return workers


def pipeline_summary() -> dict[str, Any]:
    steps = [step.to_dict() for step in code_worker_plan("capabilities")]
    return {
        "kind": "code_task",
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
    contract = build_code_task_contract("capabilities", task_id="capabilities")
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
    capability_plan = plan_code_task("capabilities", task_id="capabilities").to_dict()
    pipeline = pipeline_summary()
    oversight = oversight_template()
    next_action = {
        "kind": "plan_task",
        "method": "POST",
        "endpoint": "POST /plan",
        "body": {"task": "<task>", "task_id": "<optional-task-id>"},
        "reason": "inspect a Ceraxia code plan for a concrete task",
    }
    return {
        "ok": True,
        "governor": "Ceraxia",
        "api_version": 1,
        "task_kinds": ["code"],
        "required_workers": required_workers(),
        "worker_availability": {
            "ok": not capability_plan.get("missing_workers") and not capability_plan.get("unavailable_workers"),
            "missing_workers": capability_plan.get("missing_workers", []),
            "unavailable_workers": capability_plan.get("unavailable_workers", []),
            "resolved_workers": capability_plan.get("resolved_workers", {}),
        },
        "pipeline": pipeline,
        "patch_contract": patch_contract_capabilities(),
        "oversight": oversight,
        "task_profile": capability_plan.get("task_profile", {}),
        "worker_specialization_briefs": capability_plan.get("worker_specialization_briefs", []),
        "summary": {
            "pipeline_kind": str(pipeline.get("kind") or ""),
            "step_count": int(pipeline.get("step_count") or 0),
            "required_worker_count": len(required_workers()),
            "quality_gate_count": len(oversight.get("quality_gates") if isinstance(oversight.get("quality_gates"), list) else []),
            "handoff_count": len(oversight.get("handoffs") if isinstance(oversight.get("handoffs"), list) else []),
            "step_quality_matrix_count": len(oversight.get("step_quality_matrix") if isinstance(oversight.get("step_quality_matrix"), list) else []),
            "worker_availability_ok": not capability_plan.get("missing_workers") and not capability_plan.get("unavailable_workers"),
            "task_profile_complexity": str(capability_plan.get("task_profile", {}).get("complexity", "")) if isinstance(capability_plan.get("task_profile"), dict) else "",
        },
        "display": {
            "headline": "Ceraxia capabilities",
            "detail": f"{int(pipeline.get('step_count') or 0)} code steps, {len(required_workers())} required workers",
            "severity": "info" if not capability_plan.get("missing_workers") and not capability_plan.get("unavailable_workers") else "warning",
        },
        "next_action": next_action,
        "client_action": executable_client_action("", next_action),
        "capabilities": [
            "code_task_planning",
            "repository_survey",
            "patch_manifest_preparation",
            "verification_planning",
            "code_review_coordination",
            "safe_final_handoff",
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
    class CeraxiaHandler(BaseHTTPRequestHandler):
        server_version = "Ceraxia/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_OPTIONS(self) -> None:  # noqa: N802
            response(self, 200, {"ok": True, "governor": "Ceraxia"})

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                response(self, 200, {"ok": True, "governor": "Ceraxia"})
                return
            if self.path == "/capabilities":
                response(self, 200, service_capabilities())
                return
            response(self, 404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            try:
                payload = payload_from(self)
                task = str(payload.get("task") or payload.get("message") or "").strip()
                if not task:
                    response(self, 400, {"ok": False, "error": "task is required"})
                    return
                task_id = str(payload.get("task_id") or "").strip() or None
                plan = plan_code_task(task, task_id=task_id)
                if self.path == "/plan":
                    response(self, 200, payload_with_plan_view(plan.to_dict()))
                    return
                if self.path == "/prepare_run":
                    run_dir = resolve_run_dir(default_run_root, str(payload.get("run_dir") or ""), plan.contract.task_id)
                    status = write_pipeline_run(plan.contract, run_dir, oversight=oversight_plan(plan.contract))
                    response(
                        self,
                        200,
                        {
                            "ok": status["ok"],
                            "governor": "Ceraxia",
                            "status": status,
                            "phase": "run_prepared" if status.get("ok") else "prepare_failed",
                            "decision": {
                                "can_handoff_to_warmaster": bool(status.get("ok")),
                                "recommended_kind": "handoff_run_package" if status.get("ok") else "",
                                "recommended_endpoint": "",
                            },
                            "display": {
                                "headline": "Code run package prepared" if status.get("ok") else "Code run package preparation failed",
                                "detail": str(status.get("error") or "Run package was written for Warmaster verification"),
                                "severity": "info" if status.get("ok") else "error",
                                "task_id": plan.contract.task_id,
                            },
                            "next_action": {},
                            "client_action": {},
                        },
                    )
                    return
                response(self, 404, {"ok": False, "error": "not found"})
            except ValueError as exc:
                response(self, 400, {"ok": False, "governor": "Ceraxia", "error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                response(self, 500, {"ok": False, "governor": "Ceraxia", "error": str(exc)})

    return CeraxiaHandler


def serve(host: str, port: int, default_run_root: Path) -> None:
    default_run_root.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), make_handler(default_run_root))
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve Ceraxia as an Inner Circle code governor.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7104)
    parser.add_argument("--default-run-root", default="runtime/ceraxia-runs")
    args = parser.parse_args()
    serve(args.host, args.port, Path(args.default_run_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
