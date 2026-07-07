from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from EyeOfTerror.model_brain import model_contract, request_model_decision
from EyeOfTerror.common_protocol import governor_plan_from_contract, validate_protocol_payload

from ..command_text import task_text_from_commander_order
from ..contracts import build_research_writing_contract, research_writing_worker_plan
from .iskandar import executable_client_action, oversight_plan, payload_with_plan_view, plan_research_writing
from ..pipeline import write_pipeline_run


def required_workers() -> list[str]:
    workers: list[str] = []
    for step in research_writing_worker_plan("capabilities"):
        if step.worker not in workers:
            workers.append(step.worker)
    return workers


def pipeline_summary() -> dict[str, Any]:
    steps = [step.to_dict() for step in research_writing_worker_plan("capabilities")]
    return {
        "kind": "research_writing",
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
    contract = build_research_writing_contract("capabilities", task_id="capabilities")
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


def task_from_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    command = payload.get("commander_order") if isinstance(payload.get("commander_order"), dict) else {}
    if command:
        validate_protocol_payload(command, expected_type="commander_order")
    task = str(payload.get("task") or payload.get("message") or "").strip()
    if not task and command:
        task = task_text_from_commander_order(command)
    return task, command


def protocol_governor_plan(plan_payload: dict[str, Any], command: dict[str, Any]) -> dict[str, Any]:
    contract = plan_payload.get("contract") if isinstance(plan_payload.get("contract"), dict) else {}
    mission_id = str(command.get("mission_id") or f"mission-{contract.get('task_id') or 'unassigned'}")
    payload = governor_plan_from_contract(mission_id, contract, command)
    validate_protocol_payload(payload, expected_type="governor_plan")
    return payload


def service_capabilities() -> dict[str, Any]:
    capability_plan = plan_research_writing("capabilities", task_id="capabilities").to_dict()
    pipeline = pipeline_summary()
    oversight = oversight_template()
    next_action = {
        "kind": "plan_task",
        "method": "POST",
        "endpoint": "POST /plan",
        "body": {"task": "<task>", "task_id": "<optional-task-id>"},
        "reason": "inspect an Iskandar plan for a concrete task",
    }
    return {
        "ok": True,
        "governor": "IskandarKhayon",
        "api_version": 1,
        "task_kinds": ["research", "research_writing", "lore_reconstruction"],
        "required_workers": required_workers(),
        "worker_availability": {
            "ok": not capability_plan.get("missing_workers") and not capability_plan.get("unavailable_workers"),
            "missing_workers": capability_plan.get("missing_workers", []),
            "unavailable_workers": capability_plan.get("unavailable_workers", []),
            "resolved_workers": capability_plan.get("resolved_workers", {}),
        },
        "model_brain": model_contract("IskandarKhayon", "Inner Circle research and writing governor", layer="governor_service"),
        "pipeline": pipeline,
        "oversight": oversight,
        "summary": {
            "pipeline_kind": str(pipeline.get("kind") or ""),
            "step_count": int(pipeline.get("step_count") or 0),
            "required_worker_count": len(required_workers()),
            "quality_gate_count": len(oversight.get("quality_gates") if isinstance(oversight.get("quality_gates"), list) else []),
            "handoff_count": len(oversight.get("handoffs") if isinstance(oversight.get("handoffs"), list) else []),
            "step_quality_matrix_count": len(oversight.get("step_quality_matrix") if isinstance(oversight.get("step_quality_matrix"), list) else []),
            "worker_availability_ok": not capability_plan.get("missing_workers") and not capability_plan.get("unavailable_workers"),
        },
        "display": {
            "headline": "Iskandar Khayon capabilities",
            "detail": f"{int(pipeline.get('step_count') or 0)} steps, {len(required_workers())} required workers",
            "severity": "info" if not capability_plan.get("missing_workers") and not capability_plan.get("unavailable_workers") else "warning",
        },
        "next_action": next_action,
        "client_action": executable_client_action("", next_action),
        "capabilities": [
            "model_backed_governor_planning",
            "research_writing_planning",
            "lore_reconstruction_planning",
            "worker_plan_resolution",
            "dispatch_packet_preparation",
            "oversight_plan",
            "step_quality_matrix",
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
    warmaster_runtime = (REPO_ROOT / "EyeOfTerror" / "Warmaster" / "runtime").resolve()
    candidate = Path(requested) if requested else root / task_id
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    allowed = any(resolved == allowed_root or allowed_root in resolved.parents for allowed_root in (root, warmaster_runtime))
    if not allowed:
        raise ValueError("run_dir must stay inside the default run root or Warmaster runtime")
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
                task, command = task_from_payload(payload)
                if not task:
                    response(self, 400, {"ok": False, "error": "task is required"})
                    return
                task_id = str(payload.get("task_id") or "").strip() or None
                plan = plan_research_writing(task, task_id=task_id)
                model_decision = request_model_decision(
                    "IskandarKhayon",
                    "Inner Circle research and writing governor",
                    payload,
                    layer="governor_service",
                    instructions="Plan a research/reconstruction brigade task, identify source coverage risks, and keep the answer scoped to governor oversight.",
                )
                if not model_decision.get("ok"):
                    response(
                        self,
                        503,
                        {
                            "ok": False,
                            "governor": "IskandarKhayon",
                            "error": "model brain did not answer",
                            "error_code": "model_brain_unavailable",
                            "model_brain": model_decision,
                        },
                    )
                    return
                if self.path == "/plan":
                    plan_payload = payload_with_plan_view(plan.to_dict())
                    plan_payload["governor_plan"] = protocol_governor_plan(plan_payload, command)
                    plan_payload["model_brain"] = model_decision
                    response(self, 200, plan_payload)
                    return
                if self.path == "/prepare_run":
                    run_dir = resolve_run_dir(default_run_root, str(payload.get("run_dir") or ""), plan.contract.task_id)
                    mission_id = str(command.get("mission_id") or f"mission-{plan.contract.task_id}")
                    status = write_pipeline_run(plan.contract, run_dir, oversight=oversight_plan(plan.contract), mission_id=mission_id)
                    plan_payload = payload_with_plan_view(plan.to_dict())
                    governor_plan_payload = protocol_governor_plan(plan_payload, command)
                    (run_dir / "governor_plan.json").write_text(json.dumps(governor_plan_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                    response(
                        self,
                        200,
                        {
                            "ok": status["ok"],
                            "governor": "IskandarKhayon",
                            "model_brain": model_decision,
                            "governor_plan": governor_plan_payload,
                            "status": status,
                            "phase": "run_prepared" if status.get("ok") else "prepare_failed",
                            "decision": {
                                "can_handoff_to_warmaster": bool(status.get("ok")),
                                "recommended_kind": "handoff_run_package" if status.get("ok") else "",
                                "recommended_endpoint": "",
                            },
                            "display": {
                                "headline": "Run package prepared" if status.get("ok") else "Run package preparation failed",
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
