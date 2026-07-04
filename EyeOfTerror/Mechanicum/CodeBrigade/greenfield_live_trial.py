#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


CODE_BRIGADE_ROOT = Path(__file__).resolve().parent
MECHANICUM_ROOT = CODE_BRIGADE_ROOT.parent
EYE_ROOT = MECHANICUM_ROOT.parent
PROJECT_ROOT = EYE_ROOT.parent
for path in reversed((CODE_BRIGADE_ROOT, MECHANICUM_ROOT / "Ceraxia", MECHANICUM_ROOT / "PlanningBrigade", PROJECT_ROOT)):
    path_text = str(path)
    while path_text in sys.path:
        sys.path.remove(path_text)
    sys.path.insert(0, path_text)

from EyeOfTerror.model_brain import model_settings  # noqa: E402
from ceraxia import run_ceraxia  # noqa: E402
from ceraxia_common import CeraxiaInput  # noqa: E402


DEFAULT_TASK = "Создай новый Python CLI проект `live-model-demo` с командой, которая печатает model-ready."
DEFAULT_RUN_ROOT = EYE_ROOT / "live_runs" / "greenfield_model_trials"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def compact_greenfield_result(result: dict[str, Any], workspace: Path) -> dict[str, Any]:
    run_dir = Path(str(result.get("run_dir") or ""))
    worker_report = load_json(run_dir / "worker_report.json")
    execution_result = worker_report.get("execution_result") if isinstance(worker_report.get("execution_result"), dict) else {}
    greenfield_project = execution_result.get("greenfield_project") if isinstance(execution_result.get("greenfield_project"), dict) else {}
    run_report = greenfield_project.get("greenfield_run_report") if isinstance(greenfield_project.get("greenfield_run_report"), dict) else {}
    file_set = greenfield_project.get("file_set_synthesis_report") if isinstance(greenfield_project.get("file_set_synthesis_report"), dict) else {}
    module_synthesis = greenfield_project.get("implementation_synthesis_report") if isinstance(greenfield_project.get("implementation_synthesis_report"), dict) else {}
    ledger = greenfield_project.get("greenfield_model_guidance_ledger") if isinstance(greenfield_project.get("greenfield_model_guidance_ledger"), dict) else {}
    review = greenfield_project.get("greenfield_review") if isinstance(greenfield_project.get("greenfield_review"), dict) else {}
    verification = greenfield_project.get("verification") if isinstance(greenfield_project.get("verification"), dict) else {}
    return {
        "kind": "code_brigade_greenfield_live_model_trial_result",
        "contract_version": "eye-mechanicum.v1",
        "status": "accepted" if result.get("ok") and module_synthesis.get("status") == "applied" else "blocked",
        "model_settings": model_settings(),
        "task": load_json(run_dir / "task.json").get("task", ""),
        "workspace": str(workspace),
        "run_dir": str(run_dir),
        "ceraxia_ok": bool(result.get("ok")),
        "package_ok": bool(result.get("package_ok")),
        "ready_for_execution": bool(result.get("ready_for_execution")),
        "state": str(result.get("state") or ""),
        "review_decision": str(result.get("review_decision") or ""),
        "worker_status": str(worker_report.get("status") or ""),
        "execution_result_status": str(execution_result.get("status") or ""),
        "execution_blockers": execution_result.get("blockers", []) if isinstance(execution_result.get("blockers"), list) else [],
        "file_set_synthesis_status": str(file_set.get("status") or ""),
        "file_set_synthesis_blockers": file_set.get("blockers", []) if isinstance(file_set.get("blockers"), list) else [],
        "module_synthesis_status": str(module_synthesis.get("status") or ""),
        "module_synthesis_applied_count": int(module_synthesis.get("applied_count") or 0),
        "module_synthesis_model_unavailable_count": int(module_synthesis.get("model_unavailable_count") or 0),
        "module_synthesis_blocked_count": int(module_synthesis.get("blocked_count") or 0),
        "module_synthesis_rows": [
            {
                "module": str(row.get("module") or ""),
                "path": str(row.get("path") or ""),
                "status": str(row.get("status") or ""),
                "model_guidance_status": str(row.get("model_guidance_status") or ""),
                "blockers": row.get("blockers", []) if isinstance(row.get("blockers"), list) else [],
            }
            for row in module_synthesis.get("rows", [])
            if isinstance(row, dict)
        ],
        "verification_status": str(verification.get("status") or ""),
        "verification_commands": [
            str(row.get("command") or "")
            for row in verification.get("results", [])
            if isinstance(row, dict) and row.get("command")
        ],
        "review_status": str(review.get("status") or ""),
        "review_blockers": review.get("blockers", []) if isinstance(review.get("blockers"), list) else [],
        "model_guidance_ledger_status": str(ledger.get("status") or ""),
        "model_guidance_entries": ledger.get("entries", []) if isinstance(ledger.get("entries"), list) else [],
        "run_report": run_report,
    }


def allocate_live_trial_root(run_root: Path) -> Path:
    run_root.mkdir(parents=True, exist_ok=True)
    base = f"greenfield-live-{utc_stamp()}"
    for attempt in range(100):
        suffix = "" if attempt == 0 else f"-{attempt:02d}"
        trial_root = run_root / f"{base}{suffix}"
        try:
            trial_root.mkdir(parents=True, exist_ok=False)
            return trial_root
        except FileExistsError:
            continue
    trial_root = run_root / f"{base}-{uuid4().hex[:8]}"
    trial_root.mkdir(parents=True, exist_ok=False)
    return trial_root


def run_live_trial(task: str, run_root: Path) -> dict[str, Any]:
    trial_root = allocate_live_trial_root(run_root)
    workspace = trial_root / "workspace"
    runs_root = trial_root / "ceraxia_runs"
    workspace.mkdir(parents=True, exist_ok=False)
    result = run_ceraxia(
        CeraxiaInput(
            task=task,
            repo_path=str(workspace),
            execution_mode="project_creation",
            dry_run=False,
            execute_verification=True,
            runs_root=runs_root,
        )
    )
    compact = compact_greenfield_result(result, workspace)
    (trial_root / "live_greenfield_trial_result.json").write_text(json.dumps(compact, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return compact


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a live-model Ceraxia greenfield project trial without replay guidance.")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--require-accepted", action="store_true", help="Exit non-zero unless the live model produced an accepted greenfield run.")
    args = parser.parse_args()
    result = run_live_trial(args.task, args.run_root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if args.require_accepted and result["status"] != "accepted":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
