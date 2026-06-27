from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PORT_REGISTRY_PATH = ROOT / "registry" / "ports.json"


@dataclass(frozen=True)
class WorkerRef:
    name: str
    port: int
    role: str
    path: str
    backend: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "port": self.port,
            "role": self.role,
            "path": self.path,
        }
        if self.backend:
            payload["backend"] = self.backend
        return payload


def load_port_registry(path: Path = PORT_REGISTRY_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def worker_refs(section: str = "mechanicum", path: Path = PORT_REGISTRY_PATH) -> list[WorkerRef]:
    registry = load_port_registry(path)
    raw_workers = registry.get(section)
    if not isinstance(raw_workers, dict):
        return []
    refs: list[WorkerRef] = []
    for raw_port, data in sorted(raw_workers.items(), key=lambda item: int(item[0])):
        if not isinstance(data, dict):
            continue
        refs.append(
            WorkerRef(
                name=str(data.get("name") or ""),
                port=int(raw_port),
                role=str(data.get("role") or ""),
                path=str(data.get("path") or ""),
                backend=str(data.get("backend") or ""),
            )
        )
    return refs


def worker_by_name(name: str, section: str = "mechanicum", path: Path = PORT_REGISTRY_PATH) -> WorkerRef | None:
    target = name.strip().lower()
    for worker in worker_refs(section=section, path=path):
        if worker.name.lower() == target:
            return worker
    return None
