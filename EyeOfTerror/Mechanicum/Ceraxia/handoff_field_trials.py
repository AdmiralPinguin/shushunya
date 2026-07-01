#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from ceraxia import CeraxiaInput, run_ceraxia


def write_repo(repo: Path, files: dict[str, str]) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    for rel_path, content in files.items():
        path = repo / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def require(condition: bool, message: str, payload: Any = None) -> None:
    if not condition:
        suffix = f": {payload}" if payload is not None else ""
        raise AssertionError(message + suffix)


def load_run_artifacts(run_dir: Path) -> dict[str, Any]:
    return {
        "brief": json.loads((run_dir / "implementation_brief.json").read_text(encoding="utf-8")),
        "worker": json.loads((run_dir / "worker_report.json").read_text(encoding="utf-8")),
        "summary": json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8")),
        "readiness": json.loads((run_dir / "execution_readiness.json").read_text(encoding="utf-8")),
        "review": json.loads((run_dir / "review_gate.json").read_text(encoding="utf-8")),
        "audit": json.loads((run_dir / "run_audit.json").read_text(encoding="utf-8")),
    }


def run_dry_security_trial(root: Path) -> dict[str, Any]:
    repo = root / "dry-security-repo"
    write_repo(
        repo,
        {
            "archive_paths.py": "from pathlib import PurePosixPath\n\ndef normalize(path):\n    return str(PurePosixPath(path))\n",
            "tests/test_archive_paths.py": "from archive_paths import normalize\n\ndef test_normalize():\n    assert normalize('a/b') == 'a/b'\n",
        },
    )
    result = run_ceraxia(
        CeraxiaInput(
            task="почини security path traversal в `archive_paths.py`, сохрани API compatibility и добавь negative tests",
            repo_path=str(repo),
            runs_root=root / "runs",
            dry_run=True,
        )
    )
    artifacts = load_run_artifacts(Path(result["run_dir"]))
    require(result["ok"], "dry security planning handoff should produce an auditable package", result)
    require(not result["ready_for_execution"], "dry security handoff must not claim real execution readiness", result)
    require(artifacts["worker"]["execution_intent"]["mode"] == "planning_handoff_only", "dry security worker must expose planning-only intent", artifacts["worker"])
    require(artifacts["summary"]["code_brigade_execution_intent_mode"] == "planning_handoff_only", "summary must preserve planning-only intent", artifacts["summary"])
    require(artifacts["summary"]["maturity"] == "dry_run_controller_with_code_brigade_handoff_adapter", "dry handoff maturity should remain honest", artifacts["summary"])
    require(artifacts["brief"]["risk_level"] == "high", "security trial should be high risk", artifacts["brief"])
    require(len(artifacts["brief"]["implementation_work_packages"]["packages"]) >= 5, "security trial should create cross-surface work packages", artifacts["brief"])
    require(artifacts["audit"]["decision"] == "passed", "dry security run package audit should pass", artifacts["audit"])
    return {"id": "dry-security-planning-handoff", "result": result, "intent": artifacts["worker"]["execution_intent"]}


def run_explicit_patch_trial(root: Path) -> dict[str, Any]:
    repo = root / "explicit-patch-repo"
    write_repo(repo, {"app.py": "def enabled():\n    return False\n"})
    patch = {
        "operations": [
            {
                "type": "replace_return_expression",
                "path": "app.py",
                "function_name": "enabled",
                "old_expression": "False",
                "new_expression": "True",
            }
        ]
    }
    result = run_ceraxia(
        CeraxiaInput(
            task="почини bug в `app.py`\nCERAXIA_PATCH:\n" + json.dumps(patch),
            repo_path=str(repo),
            runs_root=root / "runs",
            dry_run=False,
            execute_verification=True,
            verification_commands=("python -m py_compile app.py",),
        )
    )
    artifacts = load_run_artifacts(Path(result["run_dir"]))
    require(result["ok"], "explicit patch handoff should complete", result)
    require(result["ready_for_execution"], "explicit patch handoff should be ready after real adapter execution", result)
    require("return True" in (repo / "app.py").read_text(encoding="utf-8"), "explicit patch should mutate app.py")
    require(artifacts["worker"]["execution_intent"]["mode"] == "explicit_patch_execution", "worker must expose explicit patch intent", artifacts["worker"])
    require(artifacts["summary"]["code_brigade_execution_real_supported"] is True, "summary must preserve executable intent", artifacts["summary"])
    require(artifacts["summary"]["maturity"] == "explicit_patch_execution_controller", "explicit patch maturity should show real adapter execution", artifacts["summary"])
    require(artifacts["review"]["decision"] == "ready", "explicit patch review should be ready", artifacts["review"])
    return {"id": "explicit-patch-execution", "result": result, "intent": artifacts["worker"]["execution_intent"]}


def run_missing_path_trial(root: Path) -> dict[str, Any]:
    repo = root / "missing-path-repo"
    write_repo(repo, {"app.py": "def app():\n    return True\n"})
    result = run_ceraxia(
        CeraxiaInput(
            task="почини `missing.py` без изменения public API",
            repo_path=str(repo),
            runs_root=root / "runs",
            dry_run=True,
        )
    )
    artifacts = load_run_artifacts(Path(result["run_dir"]))
    require(not result["ok"], "missing explicit path should block handoff", result)
    require(artifacts["brief"]["blocked"], "missing explicit path should block implementation brief", artifacts["brief"])
    require("missing.py" in artifacts["brief"]["survey_quality_gate"]["missing_path_hints"], "missing path hint must be recorded", artifacts["brief"])
    return {"id": "missing-path-blocker", "result": result, "blockers": artifacts["brief"]["blockers"]}


def run_unshaped_real_execution_trial(root: Path) -> dict[str, Any]:
    repo = root / "unshaped-real-repo"
    write_repo(repo, {"app.py": "def app():\n    return True\n"})
    result = run_ceraxia(
        CeraxiaInput(
            task="добавь API helper в `app.py` без structured patch payload",
            repo_path=str(repo),
            runs_root=root / "runs",
            dry_run=False,
        )
    )
    artifacts = load_run_artifacts(Path(result["run_dir"]))
    require(not result["ok"], "unshaped real execution should block until autonomous adapter exists", result)
    require(artifacts["worker"]["execution_intent"]["mode"] == "planning_handoff_only", "unshaped execution must preserve planning-only intent", artifacts["worker"])
    require(not artifacts["worker"]["execution_intent"]["real_execution_supported"], "unshaped execution must not claim real support", artifacts["worker"])
    require(artifacts["summary"]["maturity"] == "blocked_controller_with_audited_handoff", "blocked unshaped maturity should be explicit", artifacts["summary"])
    require(any("autonomous execution adapter" in item for item in artifacts["worker"]["notes"]), "worker blocker should name missing autonomous adapter", artifacts["worker"])
    return {"id": "unshaped-real-execution-blocker", "result": result, "intent": artifacts["worker"]["execution_intent"]}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        trials = [
            run_dry_security_trial(root),
            run_explicit_patch_trial(root),
            run_missing_path_trial(root),
            run_unshaped_real_execution_trial(root),
        ]
    print(json.dumps({"ok": True, "trial_count": len(trials), "trials": trials}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
