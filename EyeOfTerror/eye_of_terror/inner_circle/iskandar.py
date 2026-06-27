from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..contracts import TaskContract, build_lore_reconstruction_contract
from ..registry import worker_by_name


@dataclass
class IskandarPlan:
    contract: TaskContract

    def to_dict(self) -> dict[str, Any]:
        contract = self.contract.to_dict()
        missing_workers: list[str] = []
        resolved_workers: dict[str, Any] = {}
        for step in self.contract.worker_plan:
            worker = worker_by_name(step.worker)
            if worker is None:
                missing_workers.append(step.worker)
            else:
                resolved_workers[step.worker] = worker.to_dict()
        return {
            "ok": not missing_workers,
            "governor": "IskandarKhayon",
            "contract": contract,
            "resolved_workers": resolved_workers,
            "missing_workers": missing_workers,
        }


def plan_lore_reconstruction(user_task: str, task_id: str | None = None) -> IskandarPlan:
    return IskandarPlan(contract=build_lore_reconstruction_contract(user_task, task_id=task_id))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build an Iskandar Khayon lore reconstruction plan.")
    parser.add_argument("task", help="User task text")
    parser.add_argument("--task-id", default="", help="Stable task id")
    args = parser.parse_args()
    plan = plan_lore_reconstruction(args.task, task_id=args.task_id or None)
    print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

