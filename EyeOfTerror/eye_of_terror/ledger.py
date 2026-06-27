from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TaskLedger:
    path: Path
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, path: Path, task_id: str, goal: str, governor: str) -> "TaskLedger":
        ledger = cls(
            path=path,
            data={
                "task_id": task_id,
                "goal": goal,
                "governor": governor,
                "status": "created",
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "steps": [],
                "events": [],
            },
        )
        ledger.record_event("task_created", {"governor": governor})
        return ledger

    @classmethod
    def load(cls, path: Path) -> "TaskLedger":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"ledger must be a JSON object: {path}")
        return cls(path=path, data=payload)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated_at"] = now_iso()
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def record_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        self.data.setdefault("events", []).append({"at": now_iso(), "type": event_type, "payload": payload or {}})
        self.save()

    def set_status(self, status: str) -> None:
        self.data["status"] = status
        self.record_event("status_changed", {"status": status})

    def set_result(self, result: dict[str, Any]) -> None:
        self.data["result"] = result
        self.record_event("result_recorded", result)

    def record_step(self, step_id: str, worker: str, status: str, artifacts: list[str] | None = None, summary: str = "") -> None:
        steps = self.data.setdefault("steps", [])
        existing = next((step for step in steps if step.get("step_id") == step_id), None)
        payload = {
            "step_id": step_id,
            "worker": worker,
            "status": status,
            "artifacts": artifacts or [],
            "summary": summary,
            "updated_at": now_iso(),
        }
        if existing is None:
            payload["created_at"] = payload["updated_at"]
            steps.append(payload)
        else:
            existing.update(payload)
        self.record_event("step_recorded", {"step_id": step_id, "worker": worker, "status": status})

    def to_dict(self) -> dict[str, Any]:
        return dict(self.data)
