from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REGISTRY_PATH = Path(__file__).resolve().parents[1] / "registry" / "governors.json"


@dataclass(frozen=True)
class GovernorRef:
    name: str
    status: str
    port: int
    task_kinds: list[str]
    route_terms: list[str]
    service: str

    def active(self) -> bool:
        return self.status == "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "port": self.port,
            "task_kinds": self.task_kinds,
            "route_terms": self.route_terms,
            "service": self.service,
        }


def governor_refs() -> list[GovernorRef]:
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("governors registry must be an object")
    refs: list[GovernorRef] = []
    for name, item in payload.items():
        if not isinstance(item, dict):
            continue
        refs.append(
            GovernorRef(
                name=str(name),
                status=str(item.get("status") or "planned"),
                port=int(item.get("port") or 0),
                task_kinds=[str(kind) for kind in item.get("task_kinds", [])],
                route_terms=[str(term) for term in item.get("route_terms", [])],
                service=str(item.get("service") or ""),
            )
        )
    return refs


def governor_by_name(name: str) -> GovernorRef | None:
    return next((governor for governor in governor_refs() if governor.name == name), None)
