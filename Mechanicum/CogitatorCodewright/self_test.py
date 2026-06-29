#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from cogitator_codewright import run


def request(step_id: str, artifact: str) -> dict:
    return {
        "task_id": f"ceraxia-test:{step_id}",
        "goal": "почини python приложение",
        "step": {"step_id": step_id, "expected_artifacts": [artifact]},
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        steps = [
            ("repository_survey", "/work/code/repo_survey.json"),
            ("change_planning", "/work/code/change_plan.md"),
            ("implementation", "/work/code/patch_manifest.json"),
            ("verification", "/work/code/verification_report.json"),
            ("code_review", "/work/code/code_review.json"),
            ("finalize", "/work/code/final_manifest.json"),
        ]
        for step_id, artifact in steps:
            result = run(request(step_id, artifact), root)
            if not result.get("ok") and result.get("status") not in {"blocked", "needs_revision", "passed_with_warnings"}:
                raise AssertionError(f"{step_id} failed: {result}")
            if not (root / artifact.removeprefix("/work/")).exists():
                raise AssertionError(f"{step_id} did not write {artifact}")
        final = json.loads((root / "code" / "final_manifest.json").read_text(encoding="utf-8"))
        if final.get("status") != "ready" or final.get("next_safe_action") != "inspect_final_package":
            raise AssertionError(f"final manifest should expose ready handoff package: {final}")
    print("[ok] CogitatorCodewright code artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
