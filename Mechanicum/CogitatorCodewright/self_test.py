#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from cogitator_codewright import run


def request(step_id: str, artifact: str, *, goal: str = "почини python приложение", target_repo_root: Path | None = None) -> dict:
    payload = {
        "task_id": f"ceraxia-test:{step_id}",
        "goal": goal,
        "step": {"step_id": step_id, "expected_artifacts": [artifact]},
    }
    if target_repo_root is not None:
        payload["target_repo_root"] = str(target_repo_root)
    return payload


def run_pipeline(root: Path, *, goal: str = "почини python приложение", target_repo_root: Path | None = None) -> dict:
    steps = [
        ("repository_survey", "/work/code/repo_survey.json"),
        ("change_planning", "/work/code/change_plan.md"),
        ("implementation", "/work/code/patch_manifest.json"),
        ("verification", "/work/code/verification_report.json"),
        ("code_review", "/work/code/code_review.json"),
        ("finalize", "/work/code/final_manifest.json"),
    ]
    for step_id, artifact in steps:
        result = run(request(step_id, artifact, goal=goal, target_repo_root=target_repo_root), root)
        if not result.get("ok") and result.get("status") not in {"blocked", "needs_revision", "passed_with_warnings"}:
            raise AssertionError(f"{step_id} failed: {result}")
        if not (root / artifact.removeprefix("/work/")).exists():
            raise AssertionError(f"{step_id} did not write {artifact}")
    return json.loads((root / "code" / "final_manifest.json").read_text(encoding="utf-8"))


def explicit_patch_goal() -> str:
    return """почини python приложение

CERAXIA_PATCH:
{
  "operations": [
    {
      "type": "replace",
      "path": "sample.py",
      "old": "return 1",
      "new": "return 2"
    }
  ],
  "verification_commands": ["python -m py_compile sample.py"]
}
"""


def forbidden_verify_goal() -> str:
    return """почини python приложение

CERAXIA_PATCH:
{
  "operations": [
    {
      "type": "replace",
      "path": "sample.py",
      "old": "return 1",
      "new": "return 2"
    }
  ],
  "verification_commands": ["bash -lc echo-nope"]
}
"""


def create_file_goal() -> str:
    return """создай python файл

CERAXIA_CREATE_FILE: generated.py
CERAXIA_FILE_CONTENT:
def generated_value():
    return 42

CERAXIA_VERIFY: python -m py_compile generated.py
"""


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        final = run_pipeline(root)
        if final.get("status") != "blocked" or final.get("next_safe_action") != "handoff_to_patch_worker":
            raise AssertionError(f"final manifest should refuse code completion without source mutation: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("def value():\n    return 1\n", encoding="utf-8")
        final = run_pipeline(root / "work", goal=explicit_patch_goal(), target_repo_root=target_repo)
        if final.get("status") != "ready" or final.get("next_safe_action") != "inspect_final_package":
            raise AssertionError(f"final manifest should be ready after explicit patch verification: {final}")
        if sample.read_text(encoding="utf-8") != "def value():\n    return 2\n":
            raise AssertionError("explicit replace patch did not mutate the target file")
        changed = final.get("changed_files", [])
        if not changed or changed[0].get("path") != "sample.py" or not changed[0].get("changed"):
            raise AssertionError(f"final manifest should preserve changed file metadata: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        sample = target_repo / "sample.py"
        sample.write_text("def value():\n    return 1\n", encoding="utf-8")
        final = run_pipeline(root / "work", goal=forbidden_verify_goal(), target_repo_root=target_repo)
        if final.get("status") != "blocked" or final.get("approved"):
            raise AssertionError(f"forbidden verification command should block final readiness: {final}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        target_repo = root / "repo"
        target_repo.mkdir()
        final = run_pipeline(root / "work", goal=create_file_goal(), target_repo_root=target_repo)
        generated = target_repo / "generated.py"
        if final.get("status") != "ready" or not generated.exists():
            raise AssertionError(f"marker-synthesized create file task should be ready: {final}")
        if "return 42" not in generated.read_text(encoding="utf-8"):
            raise AssertionError("marker-synthesized create file task wrote wrong content")
    print("[ok] CogitatorCodewright code artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
