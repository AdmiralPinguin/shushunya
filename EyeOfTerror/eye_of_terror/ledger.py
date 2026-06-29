from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


TERMINAL_STATUSES = {"completed", "failed", "cancelled", "corrupt", "blocked"}


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
        last_error: json.JSONDecodeError | None = None
        for _ in range(3):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                break
            except json.JSONDecodeError as exc:
                last_error = exc
                time.sleep(0.02)
        else:
            raise last_error or ValueError(f"ledger could not be decoded: {path}")
        if not isinstance(payload, dict):
            raise ValueError(f"ledger must be a JSON object: {path}")
        return cls(path=path, data=payload)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                current = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                current = {}
            if isinstance(current, dict):
                current_status = str(current.get("status") or "")
                new_status = str(self.data.get("status") or "")
                terminal_preserved = (
                    current_status in TERMINAL_STATUSES
                    and current_status != new_status
                    and not (current_status == "failed" and new_status == "blocked")
                )
                if terminal_preserved:
                    self.data["status"] = current_status
                    if isinstance(current.get("result"), dict):
                        self.data["result"] = current["result"]
                    if current.get("cancel_requested"):
                        self.data["cancel_requested"] = True
                        self.data["cancel_reason"] = current.get("cancel_reason", self.data.get("cancel_reason", ""))
                    else:
                        self.data.pop("cancel_requested", None)
                        self.data.pop("cancel_reason", None)
                if current.get("cancel_requested"):
                    self.data["cancel_requested"] = True
                    self.data["cancel_reason"] = current.get("cancel_reason", self.data.get("cancel_reason", ""))
                    if self.data.get("status") == "running":
                        self.data["status"] = "cancelling"
                if "result" not in self.data and isinstance(current.get("result"), dict):
                    self.data["result"] = current["result"]
                current_events = current.get("events", []) if isinstance(current.get("events"), list) else []
                new_events = self.data.get("events", []) if isinstance(self.data.get("events"), list) else []
                seen_events = {json.dumps(event, sort_keys=True, ensure_ascii=False) for event in current_events if isinstance(event, dict)}
                merged_events = list(current_events)
                for event in new_events:
                    marker = json.dumps(event, sort_keys=True, ensure_ascii=False)
                    if marker not in seen_events:
                        merged_events.append(event)
                        seen_events.add(marker)
                self.data["events"] = merged_events
                current_steps = current.get("steps", []) if isinstance(current.get("steps"), list) else []
                new_steps = self.data.get("steps", []) if isinstance(self.data.get("steps"), list) else []
                steps_by_id = {str(step.get("step_id") or ""): step for step in current_steps if isinstance(step, dict)}
                for step in new_steps:
                    if isinstance(step, dict):
                        steps_by_id[str(step.get("step_id") or "")] = step
                self.data["steps"] = [step for key, step in steps_by_id.items() if key]
        self.data["updated_at"] = now_iso()
        tmp_path = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(self.path)

    def record_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        self.data.setdefault("events", []).append({"at": now_iso(), "type": event_type, "payload": payload or {}})
        self.save()

    def set_status(self, status: str) -> None:
        self.data["status"] = status
        self.record_event("status_changed", {"status": status})

    def request_cancel(self, reason: str = "") -> bool:
        if str(self.data.get("status") or "") in TERMINAL_STATUSES:
            self.record_event("cancel_rejected", {"reason": reason, "status": str(self.data.get("status") or "")})
            return False
        self.data["cancel_requested"] = True
        self.data["cancel_reason"] = reason
        self.data["status"] = "cancelling"
        self.record_event("cancel_requested", {"reason": reason})
        return True

    def cancel_requested(self) -> bool:
        return bool(self.data.get("cancel_requested"))

    def set_result(self, result: dict[str, Any]) -> None:
        self.data["result"] = result
        self.record_event("result_recorded", result)

    def record_step(
        self,
        step_id: str,
        worker: str,
        status: str,
        artifacts: list[str] | None = None,
        summary: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
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
        if details:
            payload["details"] = details
        if existing is None:
            payload["created_at"] = payload["updated_at"]
            steps.append(payload)
        else:
            existing.update(payload)
        event_payload = {"step_id": step_id, "worker": worker, "status": status}
        if details:
            event_payload["details"] = details
        self.record_event("step_recorded", event_payload)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.data)
