#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from eye_of_terror.warmaster_gateway import prepare_task, research_loop_run


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        target_repo = temp_root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("def value():\n    return 1\n", encoding="utf-8")
        task = f"""почини python приложение
CERAXIA_TARGET_REPO: {target_repo}
CERAXIA_PATCH:
{{
  "operations": [
    {{"type": "replace", "path": "sample.py", "old": "return 1", "new": "return 2"}}
  ],
  "verification_commands": ["python -m py_compile sample.py"]
}}
"""
        run_root = temp_root / "runs"
        task_id = "ceraxia-explicit-patch-pipeline"
        prepared = prepare_task(task, task_id, run_root, governor_transport="local")
        if not prepared.get("ok") or prepared.get("governor") != "Ceraxia":
            raise AssertionError(f"Ceraxia patch task did not prepare correctly: {prepared}")
        workers = [step.get("worker") for step in prepared.get("status", {}).get("steps", [])]
        if workers != ["LogisRepository", "MagosStrategos", "FerrumPatchwright", "OrdinatusVerifier", "JudicatorCodicis", "SealwrightFinalis"]:
            raise AssertionError(f"Ceraxia pipeline workers drifted: {workers}")
        result = research_loop_run(run_root, task_id, run_mode="local", timeout_sec=120, max_revision_cycles=1)
        if not result.get("ok") or result.get("phase") != "completed":
            raise AssertionError(f"Ceraxia patch pipeline did not complete: {result}")
        manifests = list((run_root / task_id / "work").rglob("final_manifest.json"))
        if len(manifests) != 1:
            raise AssertionError(f"expected one final manifest, found {manifests}")
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        if manifest.get("status") != "ready" or manifest.get("next_safe_action") != "inspect_final_package":
            raise AssertionError(f"Ceraxia patch final manifest should be ready: {manifest}")
        if sample.read_text(encoding="utf-8") != "def value():\n    return 2\n":
            raise AssertionError("Ceraxia patch pipeline did not mutate the target file")
        changed = manifest.get("changed_files", [])
        if not changed or changed[0].get("path") != "sample.py" or not changed[0].get("changed"):
            raise AssertionError(f"Ceraxia final manifest lacks changed file evidence: {manifest}")
    print("[ok] Ceraxia explicit patch pipeline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
