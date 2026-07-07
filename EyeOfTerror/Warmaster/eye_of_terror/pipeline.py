from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from EyeOfTerror.common_protocol import validate_protocol_payload, worker_order

from .contracts import TaskContract
from .registry import worker_by_name


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


@dataclass
class DispatchPacket:
    task_id: str
    step_id: str
    worker: str
    port: int
    purpose: str
    depends_on: list[str]
    input_artifacts: list[str]
    expected_artifacts: list[str]
    worker_order: dict[str, Any]
    request: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "step_id": self.step_id,
            "worker": self.worker,
            "port": self.port,
            "purpose": self.purpose,
            "depends_on": self.depends_on,
            "input_artifacts": self.input_artifacts,
            "expected_artifacts": self.expected_artifacts,
            "worker_order": self.worker_order,
            "request": self.request,
        }


def quality_expectations_for_step(oversight: dict[str, Any] | None, step_id: str) -> dict[str, Any]:
    if not isinstance(oversight, dict):
        return {}
    matrix = oversight.get("step_quality_matrix") if isinstance(oversight.get("step_quality_matrix"), list) else []
    step_quality = next((item for item in matrix if isinstance(item, dict) and item.get("step_id") == step_id), {})
    expectations: dict[str, Any] = {}
    if step_quality:
        expectations["step_quality"] = step_quality
    final_review = oversight.get("final_review") if isinstance(oversight.get("final_review"), dict) else {}
    if final_review:
        expectations["final_review"] = final_review
    revision_policy = oversight.get("revision_policy") if isinstance(oversight.get("revision_policy"), dict) else {}
    if revision_policy:
        expectations["revision_policy"] = revision_policy
    task_profile = oversight.get("task_profile") if isinstance(oversight.get("task_profile"), dict) else {}
    if task_profile:
        expectations["task_profile"] = task_profile
    research_intent = oversight.get("research_intent") if isinstance(oversight.get("research_intent"), dict) else {}
    if research_intent:
        expectations["research_intent"] = research_intent
    briefs = oversight.get("worker_specialization_briefs") if isinstance(oversight.get("worker_specialization_briefs"), list) else []
    worker_brief = next((item for item in briefs if isinstance(item, dict) and item.get("step_id") == step_id), {})
    if worker_brief:
        expectations["worker_brief"] = worker_brief
    return expectations


def quality_hints_for_request(request: dict[str, Any]) -> dict[str, Any]:
    expectations = request.get("quality_expectations") if isinstance(request.get("quality_expectations"), dict) else {}
    step_quality = expectations.get("step_quality") if isinstance(expectations.get("step_quality"), dict) else {}
    checks = step_quality.get("checks") if isinstance(step_quality.get("checks"), list) else []
    blockers = step_quality.get("blockers") if isinstance(step_quality.get("blockers"), list) else []
    revision_targets = step_quality.get("revision_targets") if isinstance(step_quality.get("revision_targets"), list) else []
    task_profile = expectations.get("task_profile") if isinstance(expectations.get("task_profile"), dict) else {}
    research_intent = expectations.get("research_intent") if isinstance(expectations.get("research_intent"), dict) else {}
    worker_brief = expectations.get("worker_brief") if isinstance(expectations.get("worker_brief"), dict) else {}
    return {
        "check_count": len(checks),
        "blocker_count": len(blockers),
        "revision_targets": revision_targets,
        "task_complexity": str(task_profile.get("complexity") or ""),
        "research_intent": str(research_intent.get("intent") or ""),
        "output_mode": str(research_intent.get("output_mode") or ""),
        "worker_brief": str(worker_brief.get("brief") or ""),
    }


def normalize_request_with_worker_order(request: dict[str, Any], order: dict[str, Any]) -> dict[str, Any]:
    validate_protocol_payload(order, expected_type="worker_order")
    normalized = dict(request)
    normalized["worker_order"] = order
    normalized.setdefault("task", str(order.get("task") or ""))
    normalized.setdefault("expected_output", str(order.get("expected_output") or ""))
    normalized.setdefault("input_artifacts", list(order.get("input_artifacts") if isinstance(order.get("input_artifacts"), list) else []))
    normalized.setdefault("quality_requirements", list(order.get("quality_requirements") if isinstance(order.get("quality_requirements"), list) else []))
    normalized.setdefault("revision_context", dict(order.get("revision_context") if isinstance(order.get("revision_context"), dict) else {}))
    return normalized


def dispatch_packet_with_worker_order(packet: dict[str, Any], revision_context: dict[str, Any] | None = None) -> dict[str, Any]:
    order = packet.get("worker_order") if isinstance(packet.get("worker_order"), dict) else {}
    if not order:
        return dict(packet)
    order = dict(order)
    if revision_context:
        order["revision_context"] = revision_context
    validate_protocol_payload(order, expected_type="worker_order")
    request = dict(packet.get("request") if isinstance(packet.get("request"), dict) else {})
    if revision_context:
        request["revision_context"] = revision_context
    request = normalize_request_with_worker_order(request, order)
    enriched = dict(packet)
    enriched["worker_order"] = order
    enriched["request"] = request
    return enriched


def build_dispatch_packets(contract: TaskContract, oversight: dict[str, Any] | None = None, mission_id: str | None = None) -> list[DispatchPacket]:
    packets: list[DispatchPacket] = []
    contract_payload = contract.to_dict()
    resolved_mission_id = str(mission_id or f"mission-{contract.task_id}").strip()
    artifacts_by_step = {step.step_id: step.expected_artifacts for step in contract.worker_plan}
    for step in contract.worker_plan:
        worker = worker_by_name(step.worker)
        if worker is None:
            raise ValueError(f"worker is not registered: {step.worker}")
        input_artifacts = [
            artifact
            for dependency in step.depends_on
            for artifact in artifacts_by_step.get(dependency, [])
        ]
        request = {
            "task_id": f"{contract.task_id}:{step.step_id}",
            "contract": contract_payload,
            "step": step.to_dict(),
            "input_artifacts": input_artifacts,
            "output_schema": {},
            "max_runtime_sec": 1800,
        }
        quality_expectations = quality_expectations_for_step(oversight, step.step_id)
        if quality_expectations:
            request["quality_expectations"] = quality_expectations
        quality_requirements: list[str] = []
        step_quality = quality_expectations.get("step_quality") if isinstance(quality_expectations.get("step_quality"), dict) else {}
        for item in step_quality.get("checks", []) if isinstance(step_quality.get("checks"), list) else []:
            if isinstance(item, str) and item.strip():
                quality_requirements.append(item.strip())
        order = worker_order(
            resolved_mission_id,
            step_id=step.step_id,
            sender=contract.assigned_governor,
            to=worker.name,
            task=step.purpose,
            input_artifacts=input_artifacts,
            expected_output=", ".join(step.expected_artifacts) if step.expected_artifacts else step.purpose,
            quality_requirements=quality_requirements,
        )
        validate_protocol_payload(order, expected_type="worker_order")
        request = normalize_request_with_worker_order(request, order)
        packets.append(
            DispatchPacket(
                task_id=contract.task_id,
                step_id=step.step_id,
                worker=worker.name,
                port=worker.port,
                purpose=step.purpose,
                depends_on=step.depends_on,
                input_artifacts=input_artifacts,
                expected_artifacts=step.expected_artifacts,
                worker_order=order,
                request=request,
            )
        )
    return packets


def pipeline_status(contract: TaskContract, packets: list[DispatchPacket]) -> dict[str, Any]:
    steps_by_id = {packet.step_id: packet for packet in packets}
    missing_dependencies: dict[str, list[str]] = {}
    for packet in packets:
        missing = [step_id for step_id in packet.depends_on if step_id not in steps_by_id]
        if missing:
            missing_dependencies[packet.step_id] = missing
    required_workers: list[str] = []
    for packet in packets:
        if packet.worker not in required_workers:
            required_workers.append(packet.worker)
    return {
        "ok": not missing_dependencies,
        "task_id": contract.task_id,
        "governor": contract.assigned_governor,
        "step_count": len(packets),
        "required_workers": required_workers,
        "steps": [
            {
                "step_id": packet.step_id,
                "worker": packet.worker,
                "port": packet.port,
                "depends_on": packet.depends_on,
                "input_artifacts": packet.input_artifacts,
                "expected_artifacts": packet.expected_artifacts,
                "quality_hints": quality_hints_for_request(packet.request),
            }
            for packet in packets
        ],
        "missing_dependencies": missing_dependencies,
    }


def write_pipeline_run(
    contract: TaskContract,
    run_dir: Path,
    oversight: dict[str, Any] | None = None,
    mission_id: str | None = None,
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    dispatch_dir = run_dir / "dispatch"
    dispatch_dir.mkdir(parents=True, exist_ok=True)
    packets = build_dispatch_packets(contract, oversight=oversight, mission_id=mission_id)
    current_packet_names = {f"{packet.step_id}.json" for packet in packets}
    for stale_packet in dispatch_dir.glob("*.json"):
        if stale_packet.name not in current_packet_names:
            stale_packet.unlink()
    contract_path = run_dir / "contract.json"
    oversight_path = run_dir / "oversight.json"
    status_path = run_dir / "status.json"
    write_json_atomic(contract_path, contract.to_dict())
    if oversight is not None:
        write_json_atomic(oversight_path, oversight)
    for packet in packets:
        packet_path = dispatch_dir / f"{packet.step_id}.json"
        write_json_atomic(packet_path, packet.to_dict())
    status = pipeline_status(contract, packets)
    status["run_dir"] = str(run_dir)
    status["contract_path"] = str(contract_path)
    if oversight is not None:
        status["oversight_path"] = str(oversight_path)
    status["dispatch_dir"] = str(dispatch_dir)
    write_json_atomic(status_path, status)
    return status
