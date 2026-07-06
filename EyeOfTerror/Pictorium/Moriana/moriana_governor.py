from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from EyeOfTerror.Warmaster.eye_of_terror.brigade import contract_required_workers, worker_availability
from EyeOfTerror.Warmaster.eye_of_terror.contracts import TaskContract, build_image_generation_contract, validate_task_contract_payload
from EyeOfTerror.Warmaster.eye_of_terror.pipeline import build_dispatch_packets, pipeline_status, write_pipeline_run
from EyeOfTerror.Warmaster.eye_of_terror.registry import worker_by_name
from EyeOfTerror.Pictorium.Brigades.Image.Workers.ArtifactFinalis.worker import worker_contract as artifact_finalis_contract
from EyeOfTerror.Pictorium.Brigades.Image.Workers.ForgeDispatcher.worker import worker_contract as forge_dispatcher_contract
from EyeOfTerror.Pictorium.Brigades.Image.Workers.ImageVerifier.worker import worker_contract as image_verifier_contract
from EyeOfTerror.Pictorium.Brigades.Image.Workers.ModelQuartermaster.worker import worker_contract as model_quartermaster_contract
from EyeOfTerror.Pictorium.Brigades.Image.Workers.Promptwright.worker import worker_contract as promptwright_contract
from EyeOfTerror.Pictorium.Moriana.moriana_core.asset_catalog import capabilities as forge_capabilities


GOVERNOR = "Moriana"
REQUIRED_WORKERS = ["Promptwright", "ModelQuartermaster", "ForgeDispatcher", "ImageVerifier", "ArtifactFinalis"]


def worker_metadata(path: str) -> dict[str, Any]:
    metadata_path = REPO_ROOT / path / "worker.json"
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def step_quality_checks(step_id: str) -> list[str]:
    checks = {
        "image_planning": [
            "job or project plan is structurally valid",
            "requested dimensions, engine, prompt, and safety fields are preserved",
        ],
        "resource_readiness": [
            "local model readiness and asset approval blockers are explicit",
            "LoRA and unsupported feature constraints are surfaced before dispatch",
        ],
        "forge_dispatch": [
            "ForgeQueue validation result or structured blocker is recorded",
            "queued submission is never treated as completed generation",
        ],
        "image_verification": [
            "artifact metadata and dimensions are checked when an image exists",
            "pending generation is represented as a blocker, not as success",
        ],
        "finalize": [
            "final manifest rolls up blockers and artifacts",
            "delivery readiness is explicit and auditable",
        ],
    }
    return checks.get(step_id, ["expected artifacts exist and satisfy the step purpose"])


def step_role_policy(step_id: str) -> dict[str, Any]:
    policies = {
        "image_planning": "visual_intent_to_forge_plan",
        "resource_readiness": "read_only_runtime_inventory",
        "forge_dispatch": "forge_runtime_validation_and_queued_submit",
        "image_verification": "read_only_artifact_verification",
        "finalize": "read_only_final_manifest_packaging",
    }
    return {
        "role": policies.get(step_id, "image_worker"),
        "authority": policies.get(step_id, "pictorium_artifact_generation"),
        "may_mutate_source": False,
        "required_evidence": ["expected_artifacts", "blockers", "handoff"],
        "forbidden_actions": ["direct_warmaster_to_demonsforge_call", "hiding_runtime_blockers"],
    }


def step_quality_matrix(contract: TaskContract) -> list[dict[str, Any]]:
    artifacts_by_step = {step.step_id: step.expected_artifacts for step in contract.worker_plan}
    matrix = []
    for step in contract.worker_plan:
        required_inputs = [
            artifact
            for dependency in step.depends_on
            for artifact in artifacts_by_step.get(dependency, [])
        ]
        matrix.append(
            {
                "step_id": step.step_id,
                "worker": step.worker,
                "expected_artifacts": step.expected_artifacts,
                "required_inputs": required_inputs,
                "checks": step_quality_checks(step.step_id),
                "blockers": [
                    "required input artifact missing",
                    "worker output is structurally invalid",
                    "worker reports unresolved blockers",
                ],
                "revision_targets": [step.step_id],
                "role_policy": step_role_policy(step.step_id),
            }
        )
    return matrix


def oversight_plan(contract: TaskContract) -> dict[str, Any]:
    planned_step_ids = [step.step_id for step in contract.worker_plan]
    artifact_roles = {
        "plan": [artifact for artifact in contract.required_artifacts if artifact.endswith("/image_plan.json")],
        "resources": [artifact for artifact in contract.required_artifacts if artifact.endswith("/resource_report.json")],
        "dispatch": [artifact for artifact in contract.required_artifacts if artifact.endswith("/forge_jobs.json")],
        "verification": [artifact for artifact in contract.required_artifacts if artifact.endswith("/image_verification.json")],
        "final": [artifact for artifact in contract.required_artifacts if artifact.endswith("/final_manifest.json")],
    }
    return {
        "governor": contract.assigned_governor,
        "kind": "image_generation_oversight",
        "quality_gates": contract.quality_gates,
        "completion_criteria": contract.completion_criteria,
        "non_goals": contract.non_goals,
        "artifact_roles": artifact_roles,
        "handoffs": [
            {
                "from_step": step.step_id,
                "to_steps": [candidate.step_id for candidate in contract.worker_plan if step.step_id in candidate.depends_on],
                "artifacts": step.expected_artifacts,
            }
            for step in contract.worker_plan
        ],
        "step_quality_matrix": step_quality_matrix(contract),
        "final_review": {
            "critic_step": "image_verification",
            "final_step": "finalize",
            "final_artifact": artifact_roles["final"][0] if artifact_roles["final"] else "",
            "deliverable_role": "image_manifest",
            "requires_critic_approval_or_blockers": True,
            "requires_gap_disclosure": True,
            "requires_evidence_trace": True,
        },
        "revision_policy": {
            "source_step": "image_verification",
            "final_steps": ["image_verification", "finalize"],
            "allowed_steps": planned_step_ids,
            "requires_downstream_rerun": True,
            "requires_focused_context": True,
            "requires_gap_disclosure": True,
        },
        "iteration_policy": {
            "controller": "WarmasterGateway",
            "recommended_endpoint": "POST /runs/{task_id}/start_image_pipeline",
            "max_revision_cycles": 3,
            "poll_endpoint": "GET /runs/{task_id}/orchestration?events_after=0",
            "auto_revision_triggers": [
                "resource_report has unresolved blockers",
                "forge validation fails",
                "image_verification reports dimension mismatch or missing artifact",
                "final_manifest status is blocked",
            ],
            "stop_conditions": [
                "final_manifest status is ready",
                "external asset approval is required",
                "runtime model is unavailable",
                "revision plan fingerprint repeats without progress",
            ],
            "final_readiness_checks": [
                "final manifest exists",
                "blockers are explicit",
                "artifact inventory is present",
                "verification is present or pending generation is explicit",
            ],
        },
    }


def plan_actions(contract: dict[str, Any], ok: bool, errors: list[str], availability: dict[str, Any]) -> dict[str, Any]:
    if ok:
        next_action = {
            "kind": "prepare_run",
            "method": "POST",
            "endpoint": "POST /prepare_run",
            "body": {"task": str(contract.get("goal") or ""), "task_id": str(contract.get("task_id") or "")},
            "reason": "Moriana image plan is valid and Image Brigade workers are registered",
        }
    else:
        reason = "Moriana plan failed validation"
        if availability.get("missing_workers") or availability.get("unavailable_workers"):
            reason = "Image Brigade workers are missing or unavailable"
        elif errors:
            reason = "image task contract failed validation"
        next_action = {"kind": "inspect_capabilities", "method": "GET", "endpoint": "GET /capabilities", "body": {}, "reason": reason}
    return {"can_prepare_run": ok, "can_inspect_capabilities": True, "next_action": next_action}


@dataclass
class MorianaPlan:
    contract: TaskContract

    def to_dict(self) -> dict[str, Any]:
        contract = self.contract.to_dict()
        validation_errors = validate_task_contract_payload(contract)
        availability = worker_availability(contract_required_workers(contract))
        resolved_workers: dict[str, Any] = {}
        for step in self.contract.worker_plan:
            worker = worker_by_name(step.worker)
            if worker is None:
                continue
            worker_payload = worker.to_dict()
            metadata = worker_metadata(worker.path)
            if metadata:
                worker_payload["status"] = metadata.get("status", "")
                worker_payload["capabilities"] = metadata.get("capabilities", [])
                worker_payload["callable"] = metadata.get("callable", "")
                worker_payload["model_brain"] = metadata.get("model_brain", {})
            resolved_workers[step.worker] = worker_payload
        ok = not validation_errors and bool(availability.get("ok"))
        pipeline = pipeline_status(self.contract, build_dispatch_packets(self.contract)) if ok else {}
        return {
            "ok": ok,
            "governor": GOVERNOR,
            "contract": contract,
            "validation": {"ok": not validation_errors, "errors": validation_errors},
            "pipeline": pipeline,
            "resolved_workers": resolved_workers,
            "missing_workers": availability.get("missing_workers", []),
            "unavailable_workers": availability.get("unavailable_workers", []),
            "worker_availability": availability,
            "oversight": oversight_plan(self.contract),
            "actions": plan_actions(contract, ok, validation_errors, availability),
        }


def plan_image_task(task: str, task_id: str | None = None) -> MorianaPlan:
    return MorianaPlan(build_image_generation_contract(task, task_id=task_id))


def required_workers() -> list[str]:
    return list(REQUIRED_WORKERS)


def service_capabilities() -> dict[str, Any]:
    contracts = [
        promptwright_contract(),
        model_quartermaster_contract(),
        forge_dispatcher_contract(),
        image_verifier_contract(),
        artifact_finalis_contract(),
    ]
    availability = worker_availability(REQUIRED_WORKERS)
    return {
        "ok": True,
        "governor": GOVERNOR,
        "api_version": 1,
        "task_kinds": ["image_generation"],
        "required_workers": REQUIRED_WORKERS,
        "worker_availability": availability,
        "brigades": [
            {"name": "Image", "status": "active", "path": "EyeOfTerror/Pictorium/Brigades/Image"},
            {"name": "Comics", "status": "planned", "path": "EyeOfTerror/Pictorium/Brigades/Comics"},
            {"name": "Video", "status": "planned", "path": "EyeOfTerror/Pictorium/Brigades/Video"},
        ],
        "worker_contracts": contracts,
        "forge_runtime": forge_capabilities(),
        "capabilities": [
            "image_task_planning",
            "resource_readiness",
            "forge_runtime_validation",
            "queued_image_submit",
            "artifact_verification",
            "final_manifest",
        ],
        "endpoints": ["GET /health", "GET /capabilities", "POST /plan", "POST /prepare_run"],
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


def prepare_run(task: str, task_id: str | None, run_dir: Path) -> dict[str, Any]:
    plan = plan_image_task(task, task_id=task_id)
    payload = plan.to_dict()
    if not payload.get("ok"):
        return {"ok": False, "governor": GOVERNOR, "error": "plan is not ready", "plan": payload}
    status = write_pipeline_run(plan.contract, run_dir, oversight=payload["oversight"])
    return {"ok": bool(status.get("ok")), "governor": GOVERNOR, "task_id": plan.contract.task_id, "run_dir": str(run_dir), "status": status}


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
    payload = json.loads(handler.rfile.read(length).decode("utf-8") or "{}")
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def make_handler(default_run_root: Path) -> type[BaseHTTPRequestHandler]:
    class MorianaHandler(BaseHTTPRequestHandler):
        server_version = "Moriana/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_OPTIONS(self) -> None:
            response(self, 200, {"ok": True, "governor": GOVERNOR})

        def do_GET(self) -> None:
            if self.path == "/health":
                response(self, 200, {"ok": True, "governor": GOVERNOR})
                return
            if self.path == "/capabilities":
                response(self, 200, service_capabilities())
                return
            response(self, 404, {"ok": False, "governor": GOVERNOR, "error": "not found"})

        def do_POST(self) -> None:
            try:
                payload = payload_from(self)
                task = str(payload.get("task") or payload.get("request") or "").strip()
                task_id = str(payload.get("task_id") or "").strip() or None
                if self.path == "/plan":
                    response(self, 200, plan_image_task(task, task_id=task_id).to_dict())
                    return
                if self.path == "/prepare_run":
                    if not task:
                        raise ValueError("task is required")
                    planned = plan_image_task(task, task_id=task_id)
                    run_dir = resolve_run_dir(default_run_root, str(payload.get("run_dir") or ""), planned.contract.task_id)
                    response(self, 200, prepare_run(task, planned.contract.task_id, run_dir))
                    return
                response(self, 404, {"ok": False, "governor": GOVERNOR, "error": "not found"})
            except Exception as exc:  # noqa: BLE001 - service boundary must return structured JSON.
                response(self, 400, {"ok": False, "governor": GOVERNOR, "error": str(exc)})

    return MorianaHandler


def serve(host: str, port: int, run_root: Path) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(run_root))
    print(f"Moriana listening on http://{host}:{port}")
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve Moriana as the Pictorium image governor.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7103)
    parser.add_argument("--run-root", default=str(REPO_ROOT / "EyeOfTerror" / "Warmaster" / "runs" / "live_task_runs"))
    args = parser.parse_args()
    serve(args.host, args.port, Path(args.run_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
