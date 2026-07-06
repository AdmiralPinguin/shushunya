from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUN_STATUSES = {
    "created",
    "planning",
    "generating",
    "checking",
    "revising",
    "completed",
    "failed",
}

ARTIFACT_STATUSES = {"draft", "accepted", "rejected", "final"}
ARTIFACT_TYPES = {
    "image",
    "prompt",
    "comic_panel",
    "character_sheet",
    "layout",
    "final",
    "plan",
    "resource_report",
    "dispatch",
    "verification",
    "error",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object expected: {path}")
    return payload


def safe_run_id(value: str | None = None) -> str:
    raw = (value or "").strip()
    if not raw:
        return f"moriana-{uuid.uuid4().hex[:16]}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", raw) or ".." in raw:
        raise ValueError("run_id must match [A-Za-z0-9][A-Za-z0-9_.-]{0,127} and must not contain '..'")
    return raw


def path_inside(root: Path, candidate: Path) -> bool:
    root = root.resolve()
    resolved = candidate.resolve()
    return resolved == root or root in resolved.parents


@dataclass
class RegisteredArtifact:
    artifact_id: str
    run_id: str
    artifact_type: str
    path: str
    created_by: str
    step: str
    attempt: int
    status: str
    rejection_reason: str = ""
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "run_id": self.run_id,
            "type": self.artifact_type,
            "path": self.path,
            "created_by": self.created_by,
            "step": self.step,
            "attempt": self.attempt,
            "status": self.status,
            "rejection_reason": self.rejection_reason,
            "created_at": utc_now(),
            "metadata": self.metadata or {},
        }


class MorianaRunStore:
    def __init__(self, run_root: Path):
        self.run_root = run_root

    def run_dir(self, run_id: str) -> Path:
        root = self.run_root.resolve()
        candidate = (root / safe_run_id(run_id)).resolve()
        if not path_inside(root, candidate):
            raise ValueError("run_dir must stay inside run_root")
        return candidate

    def list_runs(self) -> list[dict[str, Any]]:
        if not self.run_root.exists():
            return []
        runs = []
        for path in sorted(self.run_root.iterdir(), key=lambda item: item.name):
            if not path.is_dir():
                continue
            status_path = path / "status.json"
            if status_path.exists():
                runs.append(read_json(status_path))
        return runs

    def create_run(self, run_id: str | None, task: str, task_kind: str, plan: dict[str, Any]) -> dict[str, Any]:
        resolved_id = safe_run_id(run_id)
        run_dir = self.run_dir(resolved_id)
        if run_dir.exists():
            raise FileExistsError(f"run already exists: {resolved_id}")
        return self.ensure_run(resolved_id, task, task_kind, plan, fail_if_exists=True)

    def ensure_run(
        self,
        run_id: str | None,
        task: str,
        task_kind: str,
        plan: dict[str, Any],
        *,
        fail_if_exists: bool = False,
    ) -> dict[str, Any]:
        resolved_id = safe_run_id(run_id)
        run_dir = self.run_dir(resolved_id)
        if fail_if_exists and run_dir.exists():
            raise FileExistsError(f"run already exists: {resolved_id}")
        for subdir in (
            "input",
            "plan",
            "brigade",
            "prompts",
            "parameters",
            "results",
            "artifacts",
            "errors",
            "revisions",
            "final",
        ):
            (run_dir / subdir).mkdir(parents=True, exist_ok=True)
        if not (run_dir / "input" / "task.json").exists():
            write_json_atomic(run_dir / "input" / "task.json", {"run_id": resolved_id, "task": task, "task_kind": task_kind})
        write_json_atomic(run_dir / "plan" / "moriana_plan.json", plan)
        if not (run_dir / "artifact_registry.json").exists():
            write_json_atomic(run_dir / "artifact_registry.json", {"run_id": resolved_id, "artifacts": []})
        if (run_dir / "status.json").exists():
            status = read_json(run_dir / "status.json")
            status.update(
                {
                    "ok": status.get("ok", True),
                    "run_id": resolved_id,
                    "task": task,
                    "task_kind": task_kind,
                    "updated_at": utc_now(),
                    "run_dir": str(run_dir),
                    "pictorium_runtime": True,
                }
            )
            paths = status.get("paths") if isinstance(status.get("paths"), dict) else {}
            paths.update(
                {
                    "input": str(run_dir / "input"),
                    "plan": str(run_dir / "plan"),
                    "brigade": str(run_dir / "brigade"),
                    "prompts": str(run_dir / "prompts"),
                    "parameters": str(run_dir / "parameters"),
                    "results": str(run_dir / "results"),
                    "artifacts": str(run_dir / "artifacts"),
                    "errors": str(run_dir / "errors"),
                    "revisions": str(run_dir / "revisions"),
                    "final": str(run_dir / "final"),
                }
            )
            status["paths"] = paths
            status.setdefault("history", []).append({"status": status.get("status", "created"), "at": utc_now(), "reason": "Moriana runtime workspace ensured"})
            status.setdefault("attempt_count", 0)
            status.setdefault("final_artifact_id", "")
            status.setdefault("error_count", 0)
            write_json_atomic(run_dir / "status.json", status)
            return status
        status = {
            "ok": True,
            "run_id": resolved_id,
            "task": task,
            "task_kind": task_kind,
            "status": "created",
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "run_dir": str(run_dir),
            "paths": {
                "input": str(run_dir / "input"),
                "plan": str(run_dir / "plan"),
                "brigade": str(run_dir / "brigade"),
                "prompts": str(run_dir / "prompts"),
                "parameters": str(run_dir / "parameters"),
                "results": str(run_dir / "results"),
                "artifacts": str(run_dir / "artifacts"),
                "errors": str(run_dir / "errors"),
                "revisions": str(run_dir / "revisions"),
                "final": str(run_dir / "final"),
            },
            "history": [{"status": "created", "at": utc_now(), "reason": "visual run workspace created"}],
            "attempt_count": 0,
            "final_artifact_id": "",
            "error_count": 0,
            "pictorium_runtime": True,
        }
        write_json_atomic(run_dir / "status.json", status)
        return status

    def status(self, run_id: str) -> dict[str, Any]:
        path = self.run_dir(run_id) / "status.json"
        if not path.exists():
            raise FileNotFoundError(f"run not found: {run_id}")
        return read_json(path)

    def set_status(self, run_id: str, status: str, reason: str = "", **extra: Any) -> dict[str, Any]:
        if status not in RUN_STATUSES:
            raise ValueError(f"unknown Moriana run status: {status}")
        path = self.run_dir(run_id) / "status.json"
        if not path.exists():
            raise FileNotFoundError(f"run not found: {run_id}")
        payload = read_json(path)
        payload["status"] = status
        payload["updated_at"] = utc_now()
        payload.update(extra)
        history = payload.get("history") if isinstance(payload.get("history"), list) else []
        history.append({"status": status, "at": utc_now(), "reason": reason})
        payload["history"] = history
        write_json_atomic(path, payload)
        return payload

    def write_step(self, run_id: str, step: str, payload: dict[str, Any], subdir: str = "brigade") -> Path:
        run_dir = self.run_dir(run_id)
        target = run_dir / subdir / f"{step}.json"
        write_json_atomic(target, payload)
        return target

    def registry(self, run_id: str) -> dict[str, Any]:
        path = self.run_dir(run_id) / "artifact_registry.json"
        if not path.exists():
            raise FileNotFoundError(f"run not found: {run_id}")
        return read_json(path, {"run_id": run_id, "artifacts": []})

    def register_artifact(
        self,
        run_id: str,
        *,
        artifact_type: str,
        path: Path,
        created_by: str,
        step: str,
        attempt: int,
        status: str,
        rejection_reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if artifact_type not in ARTIFACT_TYPES:
            raise ValueError(f"unknown artifact type: {artifact_type}")
        if status not in ARTIFACT_STATUSES:
            raise ValueError(f"unknown artifact status: {status}")
        artifact_id = f"{run_id}-{step}-{attempt}-{uuid.uuid4().hex[:8]}"
        record = RegisteredArtifact(
            artifact_id=artifact_id,
            run_id=run_id,
            artifact_type=artifact_type,
            path=str(path),
            created_by=created_by,
            step=step,
            attempt=attempt,
            status=status,
            rejection_reason=rejection_reason,
            metadata=metadata or {},
        ).to_dict()
        registry = self.registry(run_id)
        artifacts = registry.get("artifacts") if isinstance(registry.get("artifacts"), list) else []
        artifacts.append(record)
        registry["artifacts"] = artifacts
        write_json_atomic(self.run_dir(run_id) / "artifact_registry.json", registry)
        return record

    def artifacts(self, run_id: str) -> list[dict[str, Any]]:
        registry = self.registry(run_id)
        return [item for item in registry.get("artifacts", []) if isinstance(item, dict)]

    def write_error(self, run_id: str, step: str, error: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
        status = self.status(run_id)
        error_count = int(status.get("error_count") or 0) + 1
        payload = {
            "run_id": run_id,
            "step": step,
            "error": error,
            "details": details or {},
            "created_at": utc_now(),
        }
        path = self.run_dir(run_id) / "errors" / f"{error_count:03d}_{step}.json"
        write_json_atomic(path, payload)
        self.register_artifact(
            run_id,
            artifact_type="error",
            path=path,
            created_by="Moriana",
            step=step,
            attempt=error_count,
            status="draft",
            metadata=payload,
        )
        self.set_status(run_id, "failed", error, error_count=error_count)
        return payload

    def write_revision(self, run_id: str, attempt: int, blockers: list[dict[str, Any]], action: str) -> dict[str, Any]:
        revision = {
            "run_id": run_id,
            "attempt": attempt,
            "required": True,
            "action": action,
            "blockers": blockers,
            "created_at": utc_now(),
            "steps": [
                {
                    "step_id": "image_planning",
                    "worker": "Promptwright",
                    "reason": "update prompt or generation parameters from verification blockers",
                },
                {
                    "step_id": "forge_dispatch",
                    "worker": "ForgeDispatcher",
                    "reason": "submit revised generation package",
                },
                {
                    "step_id": "image_verification",
                    "worker": "ImageVerifier",
                    "reason": "verify the revised artifact",
                },
                {
                    "step_id": "finalize",
                    "worker": "ArtifactFinalis",
                    "reason": "rebuild final manifest from accepted artifact",
                },
            ],
        }
        path = self.run_dir(run_id) / "revisions" / f"revision_{attempt:02d}.json"
        write_json_atomic(path, revision)
        self.register_artifact(
            run_id,
            artifact_type="plan",
            path=path,
            created_by="Moriana",
            step="revision",
            attempt=attempt,
            status="draft",
            metadata={"blocker_count": len(blockers), "action": action},
        )
        return revision

    def write_final(self, run_id: str, payload: dict[str, Any], final_artifact_id: str = "") -> dict[str, Any]:
        path = self.run_dir(run_id) / "final" / "final_manifest.json"
        write_json_atomic(path, payload)
        artifact = self.register_artifact(
            run_id,
            artifact_type="final",
            path=path,
            created_by="Moriana",
            step="final",
            attempt=int(payload.get("attempt") or 1),
            status="final",
            metadata={"status": payload.get("status"), "final_artifact_id": final_artifact_id},
        )
        self.set_status(
            run_id,
            "completed" if payload.get("status") == "ready" else "failed",
            "final manifest written",
            final_artifact_id=final_artifact_id or artifact["artifact_id"],
        )
        return payload

    def final_result(self, run_id: str) -> dict[str, Any]:
        path = self.run_dir(run_id) / "final" / "final_manifest.json"
        return read_json(path, {"ok": False, "run_id": run_id, "error": "final result is not ready"})

    def request_revision(self, run_id: str, reason: str) -> dict[str, Any]:
        artifacts = self.artifacts(run_id)
        attempt = len([item for item in artifacts if item.get("step") == "revision"]) + 1
        revision = self.write_revision(run_id, attempt, [{"code": "user_requested_revision", "message": reason}], "manual_revision")
        self.set_status(run_id, "revising", reason)
        return revision

    def accept_final(self, run_id: str) -> dict[str, Any]:
        final = self.final_result(run_id)
        if not final or final.get("error"):
            raise FileNotFoundError("final manifest is not ready")
        final["accepted_at"] = utc_now()
        write_json_atomic(self.run_dir(run_id) / "final" / "final_manifest.json", final)
        self.set_status(run_id, "completed", "user accepted final result")
        return final
