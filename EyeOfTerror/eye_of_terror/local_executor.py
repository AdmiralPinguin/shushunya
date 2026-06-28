from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ledger import TaskLedger
from .pipeline import write_json_atomic


WORKER_COMMANDS = {
    "Lexmechanic": ("Mechanicum/Lexmechanic", "Mechanicum/Lexmechanic/lexmechanic.py"),
    "AuspexBrowser": ("Mechanicum/AuspexBrowser", "Mechanicum/AuspexBrowser/auspex_browser.py"),
    "NoosphericExtractor": ("Mechanicum/NoosphericExtractor", "Mechanicum/NoosphericExtractor/noospheric_extractor.py"),
    "Chronologis": ("Mechanicum/Chronologis", "Mechanicum/Chronologis/chronologis.py"),
    "ScriptoriumDaemon": ("Mechanicum/ScriptoriumDaemon", "Mechanicum/ScriptoriumDaemon/scriptorium_daemon.py"),
    "ReductorVerifier": ("Mechanicum/ReductorVerifier", "Mechanicum/ReductorVerifier/reductor_verifier.py"),
    "FabricatorFinalis": ("Mechanicum/FabricatorFinalis", "Mechanicum/FabricatorFinalis/fabricator_finalis.py"),
}


@dataclass
class StepResult:
    step_id: str
    worker: str
    returncode: int
    ok: bool
    payload: dict[str, Any]
    stdout: str
    stderr: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "worker": self.worker,
            "returncode": self.returncode,
            "ok": self.ok,
            "payload": self.payload,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return payload


def parse_worker_stdout(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "worker stdout is not JSON"}
    return payload if isinstance(payload, dict) else {"ok": False, "error": "worker stdout JSON is not an object"}


def run_step(repo_root: Path, dispatch_path: Path, workspace_root: Path, timeout_sec: int) -> StepResult:
    packet = load_json(dispatch_path)
    worker = str(packet.get("worker") or "")
    step_id = str(packet.get("step_id") or dispatch_path.stem)
    if worker not in WORKER_COMMANDS:
        payload = {"ok": False, "error": f"no local command registered for worker: {worker}"}
        return StepResult(step_id, worker, 127, False, payload, "", payload["error"])
    pythonpath, script = WORKER_COMMANDS[worker]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / pythonpath)
    completed = subprocess.run(
        [sys.executable, str(repo_root / script), str(dispatch_path), "--workspace-root", str(workspace_root)],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout_sec,
        check=False,
    )
    payload = parse_worker_stdout(completed.stdout)
    ok = completed.returncode == 0 and bool(payload.get("ok"))
    return StepResult(step_id, worker, completed.returncode, ok, payload, completed.stdout, completed.stderr)


def ordered_dispatch_paths(run_dir: Path, step_ids: list[str] | None = None) -> list[Path]:
    status = load_json(run_dir / "status.json")
    dispatch_dir = Path(str(status.get("dispatch_dir") or run_dir / "dispatch"))
    if not dispatch_dir.is_absolute():
        candidates = [dispatch_dir, run_dir / "dispatch", run_dir.parent / dispatch_dir]
        dispatch_dir = next((candidate for candidate in candidates if candidate.exists()), run_dir / "dispatch")
    allowed = set(step_ids or [])
    paths: list[Path] = []
    for step in status.get("steps", []):
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("step_id") or "")
        if step_id and (not allowed or step_id in allowed):
            paths.append(dispatch_dir / f"{step_id}.json")
    return paths


def terminal_payload_allows_completion(payload: dict[str, Any]) -> bool:
    status = str(payload.get("status") or "").strip().lower()
    if status in {"blocked", "needs_revision", "failed", "preflight_failed", "cancelled"}:
        return False
    revision_plan = payload.get("revision_plan")
    if isinstance(revision_plan, dict) and revision_plan.get("required"):
        return False
    return True


def execute_run(
    repo_root: Path,
    run_dir: Path,
    workspace_root: Path,
    timeout_sec: int = 1800,
    step_ids: list[str] | None = None,
) -> dict[str, Any]:
    contract = load_json(run_dir / "contract.json") if (run_dir / "contract.json").exists() else {}
    ledger_path = run_dir / "task_ledger.json"
    ledger = (
        TaskLedger.load(ledger_path)
        if ledger_path.exists()
        else TaskLedger.create(
            ledger_path,
            str(contract.get("task_id") or run_dir.name),
            str(contract.get("goal") or ""),
            str(contract.get("assigned_governor") or ""),
        )
    )
    ledger.set_status("running")
    if step_ids:
        ledger.record_event("revision_execution_started", {"step_ids": step_ids, "mode": "local"})
    results: list[StepResult] = []
    for dispatch_path in ordered_dispatch_paths(run_dir, step_ids=step_ids):
        ledger = TaskLedger.load(ledger_path)
        if ledger.cancel_requested():
            break
        result = run_step(repo_root, dispatch_path, workspace_root, timeout_sec)
        results.append(result)
        ledger.record_step(
            result.step_id,
            result.worker,
            str(result.payload.get("status") or ("completed" if result.ok else "failed")),
            [str(item) for item in result.payload.get("artifacts", [])] if isinstance(result.payload.get("artifacts"), list) else [],
            str(result.payload.get("summary") or result.payload.get("error") or ""),
        )
        if not result.ok:
            break
    cancelled = TaskLedger.load(ledger_path).cancel_requested()
    final_payload = results[-1].payload if results else {}
    terminal_ok = terminal_payload_allows_completion(final_payload) if isinstance(final_payload, dict) else False
    summary = {
        "ok": bool(results) and all(item.ok for item in results) and terminal_ok and not cancelled,
        "run_dir": str(run_dir),
        "workspace_root": str(workspace_root),
        "steps": [item.to_dict() for item in results],
        "cancelled": cancelled,
    }
    if step_ids:
        summary["step_ids"] = step_ids
        summary["revision_execution"] = True
    if isinstance(final_payload, dict) and isinstance(final_payload.get("revision_plan"), dict):
        summary["revision_plan"] = final_payload["revision_plan"]
    report_path = run_dir / "execution_report.json"
    write_json_atomic(report_path, summary)
    if isinstance(final_payload, dict):
        ledger.set_result(
            {
                "ok": summary["ok"],
                "final_step": results[-1].step_id if results else "",
                "artifacts": final_payload.get("artifacts", []),
                "workspace_root": str(workspace_root),
                "status": "cancelled" if cancelled else final_payload.get("status", ""),
                "summary": "Execution cancelled before next step." if cancelled else final_payload.get("summary", ""),
                "revision_plan": final_payload.get("revision_plan", {}),
            }
        )
    ledger.set_status("completed" if summary["ok"] else ("cancelled" if cancelled else "failed"))
    return summary


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Execute a local EyeOfTerror pipeline run package.")
    parser.add_argument("run_dir")
    parser.add_argument("--workspace-root", default="runtime/eye-local-work")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--step-id", action="append", default=[], help="Restrict execution to one or more dispatch step ids")
    args = parser.parse_args()
    summary = execute_run(Path(args.repo_root).resolve(), Path(args.run_dir), Path(args.workspace_root), args.timeout_sec, step_ids=args.step_id or None)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
