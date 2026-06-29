#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from eye_of_terror.inner_circle.iskandar import plan_lore_reconstruction
from eye_of_terror.local_executor import execute_run
from eye_of_terror.pipeline import write_pipeline_run


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        plan = plan_lore_reconstruction(
            "Собери реконструкцию неизвестного конфликта Империума на Армагеддоне",
            task_id="generic-lore-smoke",
        )
        run_dir = root / "run"
        write_pipeline_run(plan.contract, run_dir, oversight=plan.to_dict()["oversight"])
        summary = execute_run(repo_root, run_dir, root / "work", timeout_sec=60)
        steps = summary.get("steps", []) if isinstance(summary.get("steps"), list) else []
        if len(steps) != 2:
            raise AssertionError(f"generic lore task should fail fast at source discovery: {summary}")
        source_step = steps[1]
        payload = source_step.get("payload") if isinstance(source_step.get("payload"), dict) else {}
        if source_step.get("step_id") != "source_discovery" or payload.get("status") != "blocked":
            raise AssertionError(f"generic lore task did not block at source discovery: {summary}")
        source_map = root / "work" / "task" / "source_map.json"
        if not source_map.exists():
            raise AssertionError("blocked generic source discovery did not write source_map diagnostics")

        source_map_payload = json.loads(source_map.read_text(encoding="utf-8"))
        if source_map_payload.get("matched_playbooks"):
            raise AssertionError(f"generic lore task matched an unexpected playbook: {source_map_payload}")
        text = json.dumps(payload, ensure_ascii=False)
        if "moon parley" in text or "Kharn burns shelters" in text:
            raise AssertionError(f"generic lore task leaked Skalathrax playbook findings: {payload}")
    print("[ok] generic lore smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
