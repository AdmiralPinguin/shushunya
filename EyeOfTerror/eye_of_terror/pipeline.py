from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import TaskContract
from .registry import worker_by_name


@dataclass
class DispatchPacket:
    task_id: str
    step_id: str
    worker: str
    port: int
    purpose: str
    depends_on: list[str]
    expected_artifacts: list[str]
    request: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "step_id": self.step_id,
            "worker": self.worker,
            "port": self.port,
            "purpose": self.purpose,
            "depends_on": self.depends_on,
            "expected_artifacts": self.expected_artifacts,
            "request": self.request,
        }


def build_dispatch_packets(contract: TaskContract) -> list[DispatchPacket]:
    packets: list[DispatchPacket] = []
    contract_payload = contract.to_dict()
    for step in contract.worker_plan:
        worker = worker_by_name(step.worker)
        if worker is None:
            raise ValueError(f"worker is not registered: {step.worker}")
        request = {
            "task_id": f"{contract.task_id}:{step.step_id}",
            "contract": contract_payload,
            "step": step.to_dict(),
            "input_artifacts": [],
            "output_schema": {},
            "max_runtime_sec": 1800,
        }
        packets.append(
            DispatchPacket(
                task_id=contract.task_id,
                step_id=step.step_id,
                worker=worker.name,
                port=worker.port,
                purpose=step.purpose,
                depends_on=step.depends_on,
                expected_artifacts=step.expected_artifacts,
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
    return {
        "ok": not missing_dependencies,
        "task_id": contract.task_id,
        "governor": contract.assigned_governor,
        "steps": [
            {
                "step_id": packet.step_id,
                "worker": packet.worker,
                "port": packet.port,
                "depends_on": packet.depends_on,
                "expected_artifacts": packet.expected_artifacts,
            }
            for packet in packets
        ],
        "missing_dependencies": missing_dependencies,
    }


def write_pipeline_run(contract: TaskContract, run_dir: Path) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    dispatch_dir = run_dir / "dispatch"
    dispatch_dir.mkdir(parents=True, exist_ok=True)
    packets = build_dispatch_packets(contract)
    contract_path = run_dir / "contract.json"
    status_path = run_dir / "status.json"
    contract_path.write_text(json.dumps(contract.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for packet in packets:
        packet_path = dispatch_dir / f"{packet.step_id}.json"
        packet_path.write_text(json.dumps(packet.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    status = pipeline_status(contract, packets)
    status["run_dir"] = str(run_dir)
    status["contract_path"] = str(contract_path)
    status["dispatch_dir"] = str(dispatch_dir)
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return status

