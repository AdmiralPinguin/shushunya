#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
WARM_ROOT = ROOT / "Warmaster"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(WARM_ROOT) not in sys.path:
    sys.path.insert(0, str(WARM_ROOT))

from eye_of_terror.run_state import run_summary


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_minimal_run(run_dir: Path, status: str) -> None:
    write_json(run_dir / "status.json", {"task_id": run_dir.name, "status": status, "steps": []})
    write_json(
        run_dir / "task_ledger.json",
        {
            "task_id": run_dir.name,
            "goal": "Проверить lifecycle.",
            "governor": "IskandarKhayon",
            "status": status,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "steps": [],
            "events": [],
        },
    )


def main() -> int:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        legacy_run = root / "legacy-running"
        write_minimal_run(legacy_run, "running")
        legacy_summary = run_summary(legacy_run)
        if legacy_summary.get("lifecycle_status") != "executing":
            raise AssertionError(f"legacy run status was not normalized: {legacy_summary}")

        mission_dir = root / "missions" / "mission-revision"
        write_json(mission_dir / "mission.json", {"mission_id": "mission-revision", "status": "revision"})
        mission_run = root / "mission-run"
        write_minimal_run(mission_run, "failed")
        write_json(
            mission_run / "mission_ref.json",
            {
                "mission_id": "mission-revision",
                "mission_dir": str(mission_dir),
                "assigned_governor": "IskandarKhayon",
            },
        )
        mission_summary = run_summary(mission_run)
        if mission_summary.get("status") != "failed":
            raise AssertionError(f"legacy status should remain unchanged: {mission_summary}")
        if mission_summary.get("lifecycle_status") != "revision":
            raise AssertionError(f"mission lifecycle did not override legacy status: {mission_summary}")
        if mission_summary.get("mission_status") != "revision":
            raise AssertionError(f"mission_status missing: {mission_summary}")
    print("[ok] Warmaster lifecycle status")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
