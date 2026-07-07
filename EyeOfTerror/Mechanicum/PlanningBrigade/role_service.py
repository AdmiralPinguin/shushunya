#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import planning_brigade
from EyeOfTerror.common_protocol import validate_protocol_payload, worker_report
from planning_packet_contract import CONTRACT_VERSION, ROLE_ORDER
from roles import design_strategos, repo_surveyor, risk_scribe, task_triage as task_triage_role, verification_architect


ROOT = Path(__file__).resolve().parent

ROLE_MODULES = {
    "TaskTriage": task_triage_role,
    "RepoSurveyor": repo_surveyor,
    "DesignStrategos": design_strategos,
    "VerificationArchitect": verification_architect,
    "RiskScribe": risk_scribe,
}


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def planning_helpers() -> dict[str, Callable[..., Any]]:
    return {
        "task_triage": planning_brigade.task_triage,
        "problem_statement": planning_brigade.problem_statement,
        "repo_survey_request": planning_brigade.repo_survey_request,
        "assumption_register": planning_brigade.assumption_register,
        "investigation_playbook": planning_brigade.investigation_playbook,
        "dependency_map": planning_brigade.dependency_map,
        "work_breakdown": planning_brigade.work_breakdown,
        "impact_analysis": planning_brigade.impact_analysis,
        "execution_forecast": planning_brigade.execution_forecast,
        "expert_quality_plan": planning_brigade.expert_quality_plan,
        "change_control_plan": planning_brigade.change_control_plan,
        "design_options": planning_brigade.design_options,
        "verification_strategy": planning_brigade.verification_strategy,
        "diagnostic_repair_plan": planning_brigade.diagnostic_repair_plan,
        "surface_verification_matrix": planning_brigade.surface_verification_matrix,
        "risk_register": planning_brigade.risk_register,
        "quality_bar": planning_brigade.quality_bar,
        "acceptance_contract": planning_brigade.acceptance_contract,
        "implementation_brief_blueprint": planning_brigade.implementation_brief_blueprint,
        "implementation_work_packages": planning_brigade.implementation_work_packages,
        "surface_package_matrix": planning_brigade.surface_package_matrix,
        "acceptance_trace_matrix": planning_brigade.acceptance_trace_matrix,
        "constraint_trace_matrix": planning_brigade.constraint_trace_matrix,
        "worker_output_contract": planning_brigade.worker_output_contract,
        "planning_review_gate": planning_brigade.planning_review_gate,
        "code_brigade_handoff": planning_brigade.code_brigade_handoff,
    }


def service_contract_for(role_name: str) -> dict[str, Any]:
    contracts = load_json(ROOT / "service_contracts.json")
    for service in contracts.get("services", []):
        if isinstance(service, dict) and service.get("name") == role_name:
            return service
    raise ValueError(f"unknown planning role service: {role_name}")


def role_contract_for(role_name: str) -> dict[str, Any]:
    contracts = load_json(ROOT / "role_contracts.json")
    for role in contracts.get("roles", []):
        if isinstance(role, dict) and role.get("name") == role_name:
            return role
    raise ValueError(f"unknown planning role contract: {role_name}")


def normalize_role_request(role_name: str, request: dict[str, Any]) -> dict[str, Any]:
    order = worker_order_from_request(request)
    context = request.get("context") if isinstance(request.get("context"), dict) else {}
    payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
    if order:
        payload = dict(payload)
        if not str(payload.get("task") or payload.get("goal") or payload.get("message") or "").strip():
            payload["task"] = str(order.get("task") or "")
        revision_context = order.get("revision_context") if isinstance(order.get("revision_context"), dict) else {}
        if revision_context.get("repo_path") and not payload.get("repo_path"):
            payload["repo_path"] = str(revision_context.get("repo_path") or "")
    if role_name == "TaskTriage":
        if not payload and context.get("payload") and isinstance(context.get("payload"), dict):
            payload = context["payload"]
        return {"payload": payload}
    if "payload" not in context:
        context = {**context, "payload": payload}
    return {"context": context}


def worker_order_from_request(request: dict[str, Any]) -> dict[str, Any]:
    order = request.get("worker_order") if isinstance(request.get("worker_order"), dict) else {}
    if order:
        validate_protocol_payload(order, expected_type="worker_order")
    return order


def attach_worker_protocol(role_name: str, request: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    order = worker_order_from_request(request)
    if not order:
        return {**result, "protocol_mode": "legacy_plan"}
    if str(order.get("to") or "").strip() != role_name:
        raise ValueError(f"worker_order.to={order.get('to')!r} cannot be handled by {role_name}")
    output_artifacts = result.get("output_artifacts") if isinstance(result.get("output_artifacts"), list) else []
    missing_outputs = result.get("missing_outputs") if isinstance(result.get("missing_outputs"), list) else []
    status = "done" if result.get("status") == "completed" else "blocked"
    report = worker_report(
        mission_id=str(order.get("mission_id") or ""),
        step_id=str(order.get("step_id") or ""),
        worker=role_name,
        status=status,
        summary=f"{role_name} planning role produced {len(output_artifacts)} artifacts",
        artifacts=[str(item) for item in output_artifacts if str(item).strip()],
        problems=[str(item) for item in missing_outputs if str(item).strip()],
        next_recommended_action=str(result.get("handoff_to") or ""),
    )
    validate_protocol_payload(report, expected_type="worker_report")
    return {
        **result,
        "protocol_mode": "worker_order",
        "worker_order": order,
        "worker_report": report,
    }


def run_role_plan(role_name: str, request: dict[str, Any]) -> dict[str, Any]:
    if role_name not in ROLE_MODULES:
        raise ValueError(f"unsupported PlanningBrigade role: {role_name}")
    normalized = normalize_role_request(role_name, request)
    helpers = planning_helpers()
    module = ROLE_MODULES[role_name]
    if role_name == "TaskTriage":
        result = module.run(normalized["payload"], helpers)
    else:
        result = module.run(normalized["context"], helpers)
        if role_name == "DesignStrategos":
            design_context = {**normalized["context"], **result.get("outputs", {})}
            if "verification_strategy" not in design_context:
                design_context["verification_strategy"] = helpers["verification_strategy"](design_context["task_triage"], design_context["payload"])
            change_control = module.finalize_change_control(design_context, helpers)
            result.setdefault("outputs", {})["change_control_plan"] = change_control
    outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else {}
    expected_outputs = service_contract_for(role_name).get("output_artifacts", [])
    missing_outputs = [name for name in expected_outputs if name not in outputs]
    status = "blocked" if missing_outputs else "completed"
    result = {
        "kind": "planning_brigade_role_service_result",
        "contract_version": CONTRACT_VERSION,
        "role": role_name,
        "status": status,
        "read_only": True,
        "outputs": outputs,
        "output_artifacts": sorted(outputs),
        "missing_outputs": missing_outputs,
        "handoff_to": service_contract_for(role_name).get("handoff_to", ""),
    }
    return attach_worker_protocol(role_name, request, result)


def role_capabilities(role_name: str) -> dict[str, Any]:
    return {
        "kind": "planning_brigade_role_service_capabilities",
        "contract_version": CONTRACT_VERSION,
        "role": role_name,
        "role_order": ROLE_ORDER,
        "endpoints": ["GET /health", "GET /capabilities", "POST /work", "POST /plan"],
        "protocol": {"strict_endpoint": "POST /work", "legacy_endpoint": "POST /plan", "input": "worker_order", "output": "worker_report"},
        "role_contract": role_contract_for(role_name),
        "service_contract": service_contract_for(role_name),
    }


def write_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def make_handler(role_name: str) -> type[BaseHTTPRequestHandler]:
    class PlanningRoleHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def do_OPTIONS(self) -> None:
            write_response(self, 200, {"ok": True})

        def do_GET(self) -> None:
            if self.path == "/health":
                write_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "kind": "planning_brigade_role_service_health",
                        "contract_version": CONTRACT_VERSION,
                        "role": role_name,
                        "read_only": True,
                    },
                )
                return
            if self.path == "/capabilities":
                write_response(self, 200, role_capabilities(role_name))
                return
            write_response(self, 404, {"ok": False, "error": "unknown endpoint"})

        def do_POST(self) -> None:
            if self.path not in {"/plan", "/work"}:
                write_response(self, 404, {"ok": False, "error": "unknown endpoint"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                if not isinstance(request, dict):
                    raise ValueError("request body must be a JSON object")
                if self.path == "/work" and not worker_order_from_request(request):
                    raise ValueError("worker_order is required for POST /work")
                result = run_role_plan(role_name, request)
            except Exception as exc:  # pragma: no cover - exercised through HTTP failure paths manually
                write_response(self, 400, {"ok": False, "error": str(exc), "role": role_name})
                return
            write_response(self, 200 if result["status"] == "completed" else 409, result)

    return PlanningRoleHandler


def serve(role_name: str, host: str, port: int) -> None:
    if role_name not in ROLE_MODULES:
        raise ValueError(f"unsupported PlanningBrigade role: {role_name}")
    server = ThreadingHTTPServer((host, port), make_handler(role_name))
    print(json.dumps({"ok": True, "role": role_name, "host": host, "port": port}, ensure_ascii=False), flush=True)
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a PlanningBrigade role service.")
    parser.add_argument("--role", choices=ROLE_ORDER, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--plan-json", type=Path, help="Run one /plan request from a JSON file instead of serving HTTP.")
    args = parser.parse_args()
    if args.plan_json:
        print(json.dumps(run_role_plan(args.role, load_json(args.plan_json)), ensure_ascii=False, indent=2))
        return 0
    port = args.port or int(service_contract_for(args.role).get("port") or 0)
    if port <= 0:
        raise SystemExit("port must be positive")
    serve(args.role, args.host, port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
