#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fabricator_finalis import run


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> int:
    request = {
        "task_id": "test-skalathrax:finalize",
        "step": {"expected_artifacts": ["/work/skalathrax/final_manifest.json"]},
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        base = root / "skalathrax"
        for filename in [
            "source_map.json",
            "source_snapshots.json",
            "direct_event_notes.json",
            "timeline.json",
            "critic_report.json",
        ]:
            write(base / filename, json.dumps({"approved": True, "status": "passed_with_warnings"}))
        write(base / "reconstruction_ru.md", "# draft\n")
        write(base / "coverage_report.md", "# coverage\n")
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"FabricatorFinalis failed: {result}")
        manifest = json.loads((base / "final_manifest.json").read_text(encoding="utf-8"))
        if manifest["status"] != "ready" or not manifest["approved"]:
            raise AssertionError(f"expected ready manifest: {manifest}")
        if manifest.get("revision_plan", {}).get("required"):
            raise AssertionError(f"ready manifest should not require revision: {manifest}")
        (base / "timeline.json").unlink()
        write(
            base / "critic_report.json",
            json.dumps(
                {
                    "approved": False,
                    "status": "needs_revision",
                    "findings": [{"severity": "blocker", "message": "Draft does not visibly cover required event: test"}],
                    "revision_plan": {
                        "required": True,
                        "steps": [
                            {
                                "step_id": "draft_reconstruction",
                                "worker": "ScriptoriumDaemon",
                                "reason": "Draft does not visibly cover required event: test",
                                "source": "critic_finding",
                                "priority": "blocker",
                            }
                        ],
                    },
                }
            ),
        )
        result = run(request, root)
        if not result.get("ok"):
            raise AssertionError(f"FabricatorFinalis failed on missing file: {result}")
        manifest = json.loads((base / "final_manifest.json").read_text(encoding="utf-8"))
        if manifest["status"] != "blocked" or not manifest["missing"]:
            raise AssertionError(f"expected blocked manifest: {manifest}")
        revision_steps = manifest.get("revision_plan", {}).get("steps", [])
        revision_workers = {step.get("worker") for step in revision_steps}
        if not manifest.get("revision_plan", {}).get("required") or not {"ScriptoriumDaemon", "Chronologis"}.issubset(revision_workers):
            raise AssertionError(f"blocked manifest did not expose merged revision plan: {manifest}")
    print("[ok] FabricatorFinalis manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
